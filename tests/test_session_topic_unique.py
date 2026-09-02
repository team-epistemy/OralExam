"""Session topic uniqueness guard (_require_unique_session_topic)."""
import pytest

pytest.importorskip("fastapi")  # http_app imports fastapi at module load
from fastapi import HTTPException

from backend.app.http_app import _require_unique_session_topic


class FakeCursor:
    """Records the last query and returns a preset fetchone result."""

    def __init__(self, hit):
        self._hit = hit
        self.sql = None
        self.params = None
        self.executed = False

    def execute(self, sql, params=None):
        self.executed = True
        self.sql = sql
        self.params = params

    def fetchone(self):
        return (1,) if self._hit else None


def test_blank_topic_is_exempt():
    cur = FakeCursor(hit=True)
    _require_unique_session_topic(cur, "c1", "o1", "   ")
    assert cur.executed is False  # no query for blank/whitespace topics


def test_duplicate_topic_raises_409():
    cur = FakeCursor(hit=True)
    with pytest.raises(HTTPException) as exc:
        _require_unique_session_topic(cur, "c1", "o1", "Week 3")
    assert exc.value.status_code == 409


def test_unique_topic_passes():
    cur = FakeCursor(hit=False)
    _require_unique_session_topic(cur, "c1", "o1", "Week 3")  # no raise


def test_match_is_case_and_space_insensitive():
    cur = FakeCursor(hit=False)
    _require_unique_session_topic(cur, "c1", "o1", "  Week 3  ")
    # topic is trimmed before binding; SQL lowercases both sides.
    assert cur.params[-1] == "Week 3"
    assert "lower(" in cur.sql


def test_self_exclusion_on_update():
    cur = FakeCursor(hit=False)
    _require_unique_session_topic(cur, "c1", "o1", "Week 3",
                                  exclude_session_id="sess-9")
    assert "session_id <>" in cur.sql
    assert "sess-9" in cur.params
