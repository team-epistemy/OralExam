"""Tests for the Azure Speech token helper (CEFR speaking exam).

Mocked tests run everywhere (no network/keys). Mirrors test_tts_helper: the key
is resolved from AZURE_SPEECH_KEY env or Secrets Manager, and issue_token POSTs
to the Azure issueToken endpoint, returning None (never raising) on any failure.
"""
import io
import urllib.error

from backend.config import Settings
from backend import azure_speech


def _reset_cache():
    azure_speech._token_cache.clear()
    azure_speech._token_cache.update({"token": None, "at": 0.0})
    azure_speech._key_cache.clear()


def test_issue_token_none_without_key(monkeypatch):
    """No key resolvable -> None (graceful fallback to text capture)."""
    _reset_cache()
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    monkeypatch.setattr(azure_speech, "_resolve_key", lambda settings: None)
    assert azure_speech.issue_token(Settings()) is None


def test_issue_token_posts_and_caches(monkeypatch):
    """With a key it POSTs to the region issueToken endpoint, returns token+region,
    and caches within the TTL (no second POST)."""
    _reset_cache()
    monkeypatch.setenv("AZURE_SPEECH_KEY", "azkey-dummy")
    monkeypatch.setenv("EPISTEMY_AZURE_SPEECH_REGION", "eastus")
    calls = {"n": 0, "url": None, "headers": None}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"eyJhbGcidummy.token"

    def _fake_urlopen(req, timeout=None):
        calls["n"] += 1
        calls["url"] = req.full_url
        calls["headers"] = {k.lower(): v for k, v in req.headers.items()}
        return _Resp()

    monkeypatch.setattr(azure_speech.urllib.request, "urlopen", _fake_urlopen)
    clock = [1_000_000.0]
    out = azure_speech.issue_token(Settings(), now=lambda: clock[0])
    assert out["token"] == "eyJhbGcidummy.token"
    assert out["region"] == "eastus"
    assert calls["url"] == "https://eastus.api.cognitive.microsoft.com/sts/v1.0/issueToken"
    assert calls["headers"]["ocp-apim-subscription-key"] == "azkey-dummy"

    # Within TTL -> served from cache, no new POST.
    clock[0] += 60
    azure_speech.issue_token(Settings(), now=lambda: clock[0])
    assert calls["n"] == 1
    # Past TTL -> refreshed.
    clock[0] += azure_speech._TOKEN_TTL_S + 1
    azure_speech.issue_token(Settings(), now=lambda: clock[0])
    assert calls["n"] == 2


def test_issue_token_handles_http_error(monkeypatch):
    """A non-2xx from Azure -> None, not an exception."""
    _reset_cache()
    monkeypatch.setenv("AZURE_SPEECH_KEY", "azkey-dummy")

    def _raise(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"bad key"))

    monkeypatch.setattr(azure_speech.urllib.request, "urlopen", _raise)
    assert azure_speech.issue_token(Settings()) is None
