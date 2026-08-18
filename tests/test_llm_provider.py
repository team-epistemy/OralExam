"""Offline tests for LLM provider routing (mocked client — no network/keys).

The final test is an opt-in live check (EPISTEMY_LIVE_TESTS=1) that actually
calls the Anthropic API to confirm the direct-SDK path works end to end.
"""
import os
from dataclasses import replace

import pytest

from backend.config import Settings
from backend import bedrock_helper


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Message:
    def __init__(self, text):
        self.content = [_Block(text)]


class _FakeMessages:
    def __init__(self, recorder, text):
        self._recorder = recorder
        self._text = text

    def create(self, **kwargs):
        self._recorder.update(kwargs)
        return _Message(self._text)


class _FakeAnthropic:
    def __init__(self, recorder, text):
        self.messages = _FakeMessages(recorder, text)


def _patch_anthropic(monkeypatch, recorder, text):
    monkeypatch.setattr(
        bedrock_helper, "_get_anthropic_client",
        lambda settings: _FakeAnthropic(recorder, text),
    )


def test_anthropic_route_sends_claude_request_and_parses_json(monkeypatch):
    recorder = {}
    _patch_anthropic(monkeypatch, recorder, '```json\n{"concepts": [{"label": "X"}]}\n```')
    settings = Settings(llm_provider="anthropic", anthropic_model="claude-opus-4-8")

    result = bedrock_helper.call_bedrock(settings, "SYS", "USER", max_tokens=1234, temperature=0.1)

    assert result == {"concepts": [{"label": "X"}]}       # fences stripped, JSON parsed
    assert recorder["model"] == "claude-opus-4-8"
    assert recorder["max_tokens"] == 1234
    assert recorder["system"] == "SYS"
    assert recorder["messages"] == [{"role": "user", "content": "USER"}]
    assert "temperature" not in recorder                   # removed on Opus 4.8 (would 400)


def test_default_provider_is_anthropic(monkeypatch):
    recorder = {}
    _patch_anthropic(monkeypatch, recorder, '{"ok": true}')
    # A default Settings() should route to Claude, not Bedrock.
    result = bedrock_helper.call_bedrock(Settings(), "s", "u")
    assert result == {"ok": True}
    assert recorder["model"] == "claude-sonnet-4-6"


def test_bedrock_route_still_available(monkeypatch):
    captured = {}

    class _FakeBedrock:
        def converse(self, **kwargs):
            captured.update(kwargs)
            return {"output": {"message": {"content": [{"text": '{"from": "bedrock"}'}]}}}

    monkeypatch.setattr(bedrock_helper, "_get_bedrock_client", lambda region: _FakeBedrock())
    settings = Settings(llm_provider="bedrock")

    result = bedrock_helper.call_bedrock(settings, "s", "u", temperature=0.2)
    assert result == {"from": "bedrock"}
    assert captured["inferenceConfig"]["temperature"] == 0.2  # bedrock still uses temperature


@pytest.mark.skipif(
    os.getenv("EPISTEMY_LIVE_TESTS") != "1",
    reason="set EPISTEMY_LIVE_TESTS=1 (with ANTHROPIC_API_KEY or AWS creds + "
           "EPISTEMY_ANTHROPIC_SECRET) to run the live Anthropic API test",
)
def test_live_anthropic_direct_api():
    """Real Claude call via the direct Anthropic SDK (not Bedrock); opt-in only."""
    settings = Settings()
    # Confirms the app defaults to the direct Anthropic API path, not Bedrock.
    assert settings.llm_provider == "anthropic"
    # When no ANTHROPIC_API_KEY env is set, fetch the key from Secrets Manager by
    # its computed name (mirrors what the ECS task env does at deploy time).
    if not os.getenv("ANTHROPIC_API_KEY") and not settings.anthropic_secret:
        settings = replace(settings, anthropic_secret=settings.anthropic_secret_name)

    result = bedrock_helper.call_bedrock(
        settings,
        "You are a JSON API. Respond with ONLY a JSON object, no prose.",
        'Return exactly {"ok": true}.',
        max_tokens=50,
    )
    assert isinstance(result, dict) and result.get("ok") is True
