"""Tests for the ElevenLabs TTS helper.

The mocked tests run everywhere (no network/keys). The live test is opt-in via
EPISTEMY_LIVE_TESTS=1 and actually calls ElevenLabs, resolving the key from
ANTHROPIC-style Secrets Manager or the ELEVENLABS_API_KEY env var.
"""
import io
import json
import os
import urllib.error

import pytest

from backend.config import Settings
from backend import tts_helper


def test_synthesize_returns_none_without_key(monkeypatch):
    """No key resolvable -> None (graceful text-only degradation, never raises)."""
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr(tts_helper, "_resolve_key", lambda settings: None)
    assert tts_helper.synthesize(Settings(), "hello") is None


def test_synthesize_posts_expected_request(monkeypatch):
    """With a key, it POSTs to the voice endpoint with the right headers/body."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test_dummy")
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"ID3fake-mp3-bytes"

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        captured["body"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setattr(tts_helper.urllib.request, "urlopen", _fake_urlopen)
    settings = Settings()
    out = tts_helper.synthesize(settings, "Explain Little's Law.", voice_id="VOICE123")

    assert out == b"ID3fake-mp3-bytes"
    assert "VOICE123" in captured["url"]                      # requested voice used
    assert captured["headers"]["xi-api-key"] == "sk_test_dummy"
    assert captured["headers"]["accept"] == "audio/mpeg"
    assert captured["body"]["text"] == "Explain Little's Law."
    assert captured["body"]["model_id"] == settings.elevenlabs_model


def test_synthesize_handles_http_error(monkeypatch):
    """A non-2xx from ElevenLabs -> None, not an exception."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test_dummy")

    def _raise(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"bad key"))

    monkeypatch.setattr(tts_helper.urllib.request, "urlopen", _raise)
    assert tts_helper.synthesize(Settings(), "hi") is None


@pytest.mark.skipif(
    os.getenv("EPISTEMY_LIVE_TESTS") != "1",
    reason="set EPISTEMY_LIVE_TESTS=1 (with ELEVENLABS_API_KEY or AWS creds + "
           "EPISTEMY_ENV) to run the live ElevenLabs API test",
)
def test_synthesize_live_elevenlabs():
    """Real ElevenLabs call -> valid MP3 bytes (opt-in; not run in CI)."""
    audio = tts_helper.synthesize(Settings(), "This is a live ElevenLabs test.")
    assert audio, "ElevenLabs returned no audio (key/secret/API problem)"
    assert len(audio) > 1000
    assert audio[:3] == b"ID3" or audio[:2] in (b"\xff\xfb", b"\xff\xf3")
