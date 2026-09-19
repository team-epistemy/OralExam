"""Expected-path generation must never run inside a request.

Covers the pieces that keep the ~10s Claude call off the answer path: the
in-flight guard (so concurrent sub-turns don't fire duplicate calls), the
per-question commit, and the "one question failing isn't the batch" contract.
"""
import pytest

pytest.importorskip("fastapi")

from backend.app import http_app  # noqa: E402


class FakeCursor:
    """Records executed SQL; context-manager shaped like psycopg's cursor."""

    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))


class FakeConn:
    def __init__(self):
        self.log = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self.log)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _clear_inflight():
    http_app._INFLIGHT_PATHS.clear()
    yield
    http_app._INFLIGHT_PATHS.clear()


def test_store_expected_path_commits_the_update(monkeypatch):
    monkeypatch.setattr(http_app, "_generate_expected_path",
                        lambda *a, **k: {"nodes": [{"label": "n"}], "edges": []})
    conn = FakeConn()
    http_app._store_expected_path(conn, object(), "q" * 36, "text", ["c"])
    assert conn.commits == 1
    sql, params = conn.log[0]
    assert sql.startswith("UPDATE question SET expected_path")
    assert params[1] == "q" * 36


def test_store_expected_path_skips_the_write_when_generation_is_empty(monkeypatch):
    """An empty path must not be persisted — the no-path rubric handles it."""
    monkeypatch.setattr(http_app, "_generate_expected_path",
                        lambda *a, **k: {"nodes": [], "edges": []})
    conn = FakeConn()
    http_app._store_expected_path(conn, object(), "q" * 36, "text", ["c"])
    assert conn.log == []
    assert conn.commits == 0


def test_fill_bg_continues_after_one_question_fails(monkeypatch):
    calls = []

    def flaky(settings, text, concepts):
        calls.append(text)
        if text == "boom":
            raise RuntimeError("model said no")
        return {"nodes": [{"label": "n"}], "edges": []}

    conn = FakeConn()
    monkeypatch.setattr(http_app, "_generate_expected_path", flaky)
    monkeypatch.setattr(http_app.factory, "db_connection", lambda s: conn)

    http_app._fill_expected_paths_bg(object(), "org-1", [
        ("a" * 36, "first", ["c"]),
        ("b" * 36, "boom", ["c"]),
        ("c" * 36, "third", ["c"]),
    ])

    assert calls == ["first", "boom", "third"]
    assert conn.rollbacks == 1
    assert conn.closed is True
    # set_config + the two successful UPDATEs
    assert sum(1 for sql, _ in conn.log if sql.startswith("UPDATE question")) == 2


def test_fill_bg_always_releases_the_inflight_ids(monkeypatch):
    """Even when the connection itself fails, ids must not stay wedged in-flight."""
    def boom(_settings):
        raise RuntimeError("no db")

    monkeypatch.setattr(http_app.factory, "db_connection", boom)
    http_app._INFLIGHT_PATHS.add("a" * 36)
    http_app._fill_expected_paths_bg(object(), "org-1", [("a" * 36, "t", ["c"])])
    assert "a" * 36 not in http_app._INFLIGHT_PATHS


def test_async_dedups_in_flight_questions(monkeypatch):
    started = []
    monkeypatch.setattr(http_app.threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda s: started.append(kw["args"][2])})())

    http_app._fill_expected_paths_async(object(), "org-1", [("q" * 36, "t", ["c"])])
    # Second call for the same question while the first is in flight: no new thread.
    http_app._fill_expected_paths_async(object(), "org-1", [("q" * 36, "t", ["c"])])

    assert len(started) == 1
    assert started[0] == [("q" * 36, "t", ["c"])]


def test_async_starts_nothing_when_every_id_is_in_flight(monkeypatch):
    monkeypatch.setattr(http_app.threading, "Thread",
                        lambda **kw: pytest.fail("should not spawn a thread"))
    http_app._INFLIGHT_PATHS.add("q" * 36)
    http_app._fill_expected_paths_async(object(), "org-1", [("q" * 36, "t", ["c"])])


def test_generator_is_bounded(monkeypatch):
    """The generator must cap retries and wall clock, or a bad sample stacks calls."""
    seen = {}

    def fake_call(settings, system, user, **kw):
        seen.update(kw)
        return {"nodes": []}

    monkeypatch.setattr(http_app, "call_bedrock", fake_call)
    http_app._generate_expected_path(object(), "q text", ["c"])
    assert seen["retries"] == 1
    assert seen["timeout"] == http_app._EXPECTED_PATH_TIMEOUT_S
