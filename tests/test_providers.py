from __future__ import annotations

import pytest

from dirtywork.providers import (
    DEFAULT_BASE_URLS,
    PROVIDER_NAMES,
    ChatResponse,
    ToolCall,
    assistant_message,
    get_provider,
    tool_message,
)


def test_provider_names_and_default_base_urls_agree():
    assert PROVIDER_NAMES == ("openai", "anthropic")
    assert DEFAULT_BASE_URLS == {
        "openai": "http://localhost:1234/v1",
        "anthropic": "https://api.anthropic.com",
    }
    assert set(DEFAULT_BASE_URLS) == set(PROVIDER_NAMES)


def test_tool_call_dataclass_fields():
    tc = ToolCall(id="c1", name="read_file", arguments={"path": "a.txt"}, error=None,
                  raw_arguments='{"path": "a.txt"}')
    assert (tc.id, tc.name, tc.arguments, tc.error) == (
        "c1", "read_file", {"path": "a.txt"}, None)
    assert tc.raw_arguments == '{"path": "a.txt"}'


def test_tool_call_raw_arguments_defaults_to_empty_string():
    tc = ToolCall(id="c1", name="read_file", arguments={}, error=None)
    assert tc.raw_arguments == ""


def test_chat_response_dataclass_fields():
    tc = ToolCall(id="c1", name="read_file", arguments={}, error=None)
    resp = ChatResponse(text="hi", tool_calls=[tc], finish_reason="stop",
                        usage={"prompt_tokens": 1, "completion_tokens": 1})
    assert resp.text == "hi"
    assert resp.tool_calls == [tc]
    assert resp.finish_reason == "stop"
    assert resp.usage == {"prompt_tokens": 1, "completion_tokens": 1}


def test_chat_response_defaults():
    resp = ChatResponse(text="hi")
    assert resp.tool_calls == [] and resp.finish_reason is None and resp.usage == {}


def test_assistant_message_without_tool_calls():
    assert assistant_message("hello", None) == {"role": "assistant", "content": "hello"}


def test_assistant_message_with_tool_calls():
    tc = ToolCall(id="c1", name="read_file", arguments={"path": "a.txt"}, error=None)
    msg = assistant_message(None, [tc])
    assert msg["role"] == "assistant"
    assert msg["content"] == ""
    assert msg["tool_calls"] == [tc]


def test_tool_message():
    assert tool_message("c1", "file contents") == {
        "role": "tool", "content": "file contents", "tool_call_id": "c1"}


def test_get_provider_unknown_raises():
    with pytest.raises(ValueError, match="unknown provider 'bogus'"):
        get_provider("bogus")


def test_get_provider_anthropic_returns_anthropic_client(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    provider = get_provider("anthropic", "http://fake", timeout=10)
    assert provider.name == "anthropic"
    assert provider.base_url == "http://fake"


def test_get_provider_openai_returns_openai_client():
    provider = get_provider("openai")
    assert provider.name == "openai"
    assert provider.base_url == DEFAULT_BASE_URLS["openai"]


def test_get_provider_defaults_base_url_per_provider(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert get_provider("anthropic").base_url == DEFAULT_BASE_URLS["anthropic"]


def test_every_provider_name_is_constructible(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    for name in PROVIDER_NAMES:
        assert get_provider(name).name == name
