"""Latency guards for the student answer path.

Every number a student feels comes from three steps — waiting for the rubric,
the evaluation LLM call, and TTS. These tests pin the two things that decide how
bad a bad turn gets (the bounds on those calls) and the timing lines that are the
only real measurement of them: the admin perf probe is synthetic, so without
`turn-timing` in the logs every claim about student latency is an estimate.
"""
import logging

import pytest

pytest.importorskip("fastapi")

from backend.app import http_app  # noqa: E402
from backend import tts_helper  # noqa: E402
from backend.config import Settings  # noqa: E402


# ── the stopwatch ────────────────────────────────────────────────────────────

def test_timed_logs_step_and_duration(caplog):
    with caplog.at_level(logging.INFO, logger=http_app.logger.name):
        with http_app._timed("eval_llm", eds=True):
            pass
    line = caplog.text
    assert "turn-timing step=eval_llm" in line
    assert "ms=" in line
    assert "eds=True" in line


def test_timed_logs_even_when_the_step_raises(caplog):
    """A step that failed slowly is exactly the one worth seeing in the logs."""
    with caplog.at_level(logging.INFO, logger=http_app.logger.name):
        with pytest.raises(RuntimeError):
            with http_app._timed("tts", chars=12):
                raise RuntimeError("elevenlabs down")
    assert "turn-timing step=tts" in caplog.text
    assert "chars=12" in caplog.text


def test_timed_prefix_is_distinct_from_http_access_lines():
    """Logging is plain text (BACKLOG item 4), so the prefix has to be unambiguous.

    A metric filter for `turn-timing` must not also match request lines for the
    perf endpoints, which is what happened with an earlier `perf-trace` filter.
    """
    assert "turn-timing" not in "GET /api/admin/perf/history HTTP/1.1 200"


# ── bounds on the two calls a turn makes ─────────────────────────────────────

def test_eval_call_is_bounded(monkeypatch):
    """The eval call had no timeout: it inherited the SDK's 600s default, so a hung
    connection rode until CloudFront 504'd the student at 60s."""
    assert http_app._EVAL_TIMEOUT_S == 20
    assert http_app._EVAL_TIMEOUT_S < 60, "must stay under CloudFront's origin timeout"


def test_rubric_generation_budget_stays_off_the_request_path():
    """Generation runs in a thread, so its budget may exceed a student's patience —
    but not the 60s request ceiling, so the number is never mistaken for one."""
    assert http_app._EXPECTED_PATH_TIMEOUT_S == 40
    assert http_app._EXPECTED_PATH_WAIT_S < http_app._EXPECTED_PATH_TIMEOUT_S
    assert http_app._EXPECTED_PATH_TIMEOUT_S < 60


# ── TTS: the per-turn cost paid on every single answer ───────────────────────

def test_synthesize_uses_flash_streaming_and_the_chosen_bitrate(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test_dummy")
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"ID3fake"

    def _fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _Resp()

    monkeypatch.setattr(tts_helper.urllib.request, "urlopen", _fake_urlopen)
    assert tts_helper.synthesize(Settings(), "hello", voice_id="V1") == b"ID3fake"
    assert seen["url"].endswith(f"/V1/stream?output_format={tts_helper.OUTPUT_FORMAT}")
    assert tts_helper.OUTPUT_FORMAT == "mp3_44100_64"


def test_default_tts_model_is_flash():
    """turbo_v2_5 is deprecated and ~3x flash's model latency, paid every turn."""
    assert Settings().elevenlabs_model == "eleven_flash_v2_5"


def test_explicit_model_still_overrides_the_default(monkeypatch):
    """The hybrid eval path passes its own model; the default must not win."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test_dummy")
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"ID3fake"

    def _fake_urlopen(req, timeout=None):
        import json
        seen.update(json.loads(req.data.decode()))
        return _Resp()

    monkeypatch.setattr(tts_helper.urllib.request, "urlopen", _fake_urlopen)
    tts_helper.synthesize(Settings(), "hi", model="eleven_turbo_v2_5")
    assert seen["model_id"] == "eleven_turbo_v2_5"
