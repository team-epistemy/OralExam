"""Offline tests for LLM provider routing (mocked client — no network/keys)."""
from dataclasses import replace

from epistemy_m3.config import Settings
from epistemy_m3 import bedrock_helper


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
