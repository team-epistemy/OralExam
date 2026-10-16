"""Unit tests for the Google STT adapter (subject-exam answer dictation).

Fully mocked — no network or credentials: the auth token and urlopen are stubbed,
so we assert request construction (URL, body, auth header, base64 payload) and
transcript parsing. Mirrors test_azure_speech's urlopen-monkeypatch style.
"""
import base64
import json

from backend import google_speech
from backend.google_speech import GoogleSpeech


def _install(monkeypatch, capture, response_obj):
    """Stub _token + urlopen; capture the outgoing request, return response_obj."""
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(response_obj).encode()

    def _fake_urlopen(req, timeout=None):
        capture["url"] = req.full_url
        capture["headers"] = {k.lower(): v for k, v in req.headers.items()}
        capture["body"] = json.loads(req.data.decode())
        capture["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(GoogleSpeech, "_token", lambda self: "dummy-token")
    monkeypatch.setattr(google_speech.urllib.request, "urlopen", _fake_urlopen)
    return GoogleSpeech("proj-x", "us-central1", "chirp_2", "en-US")


def test_builds_request_and_joins_transcript(monkeypatch):
    cap = {}
    stt = _install(monkeypatch, cap, {"results": [
        {"alternatives": [{"transcript": "Hello world"}]},
        {"alternatives": [{"transcript": "second part"}]},
    ]})
    out = stt.transcribe(b"\x01\x02\x03audio")

    assert out == "Hello world second part"
    # URL targets the implicit recognizer for the project/location.
    assert cap["url"] == ("https://us-central1-speech.googleapis.com/v2/projects/"
                          "proj-x/locations/us-central1/recognizers/_:recognize")
    # Auth header comes from _token.
    assert cap["headers"]["authorization"] == "Bearer dummy-token"
    assert cap["headers"]["content-type"] == "application/json"
    # Config: model / language / auto-decoding + punctuation.
    cfg = cap["body"]["config"]
    assert cfg["model"] == "chirp_2"
    assert cfg["languageCodes"] == ["en-US"]
    assert "autoDecodingConfig" in cfg
    assert cfg["features"]["enableAutomaticPunctuation"] is True
    # Audio is sent as base64 of the exact bytes.
    assert base64.b64decode(cap["body"]["content"]) == b"\x01\x02\x03audio"


def test_empty_results_returns_empty_string(monkeypatch):
    cap = {}
    stt = _install(monkeypatch, cap, {"results": []})
    assert stt.transcribe(b"x") == ""


def test_missing_results_key_returns_empty_string(monkeypatch):
    cap = {}
    stt = _install(monkeypatch, cap, {})
    assert stt.transcribe(b"x") == ""


def test_skips_missing_or_empty_alternatives(monkeypatch):
    cap = {}
    stt = _install(monkeypatch, cap, {"results": [
        {},                                       # no alternatives key
        {"alternatives": []},                     # empty alternatives
        {"alternatives": [{"transcript": ""}]},   # empty transcript
        {"alternatives": [{"transcript": "  kept  "}]},  # whitespace-trimmed
    ]})
    assert stt.transcribe(b"x") == "kept"


def test_language_and_model_are_configurable(monkeypatch):
    cap = {}
    monkeypatch.setattr(GoogleSpeech, "_token", lambda self: "t")
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"results": []}'
    def _fake(req, timeout=None):
        cap["url"] = req.full_url
        cap["body"] = json.loads(req.data.decode())
        return _Resp()
    monkeypatch.setattr(google_speech.urllib.request, "urlopen", _fake)
    GoogleSpeech("p", "europe-west4", "latest_long", "fr-FR").transcribe(b"a")
    assert cap["url"].startswith("https://europe-west4-speech.googleapis.com/")
    assert cap["body"]["config"]["model"] == "latest_long"
    assert cap["body"]["config"]["languageCodes"] == ["fr-FR"]
