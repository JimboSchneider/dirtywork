from __future__ import annotations

import json
from pathlib import Path

import pytest

from dirtywork.llm import LLMError, MalformedResponse
from dirtywork.providers.anthropic import AnthropicClient

from .provider_contract import ProviderContract, RecordingTransport

FIXTURES = Path(__file__).parent / "fixtures" / "providers" / "anthropic"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


def _client(transport, api_key="sk-ant-test"):
    return AnthropicClient(base_url="http://fake", http_json=transport, api_key=api_key)


class TestAnthropicProviderContract(ProviderContract):
    fixtures_dir = FIXTURES

    def make_client(self, transport):
        return _client(transport)

    def _system_text(self, payload):
        return payload.get("system")

    def _tool_result_entries(self, payload):
        for m in reversed(payload["messages"]):
            if m["role"] == "user" and isinstance(m["content"], list):
                blocks = [b for b in m["content"] if b.get("type") == "tool_result"]
                if blocks:
                    return [(b["tool_use_id"], b["content"]) for b in blocks]
        return []


def test_provider_name_is_anthropic():
    assert _client(RecordingTransport([])).name == "anthropic"


def test_missing_api_key_raises_llmerror_on_list_models(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        _client(RecordingTransport([]), api_key=None).list_models()


def test_missing_api_key_raises_llmerror_on_chat(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        _client(RecordingTransport([]), api_key=None).chat(
            "claude-x", [{"role": "user", "content": "hi"}], [],
            temperature=None, max_tokens=100, timeout=30)


def test_api_key_read_from_env_when_not_passed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    client = AnthropicClient(base_url="http://fake", http_json=RecordingTransport([]))
    assert client.api_key == "sk-ant-from-env"


def test_chat_sends_required_headers_and_url():
    transport = RecordingTransport([_fixture("simple_ok.json")])
    _client(transport).chat("claude-x", [{"role": "user", "content": "hi"}], [],
                            temperature=None, max_tokens=100, timeout=30)
    headers = transport.calls[0]["headers"]
    assert headers["x-api-key"] == "sk-ant-test"
    assert headers["anthropic-version"] == "2023-06-01"
    assert headers["content-type"] == "application/json"
    assert transport.calls[0]["url"] == "http://fake/v1/messages"


def test_tools_converted_to_input_schema_shape():
    transport = RecordingTransport([_fixture("simple_ok.json")])
    openai_tools = [{"type": "function", "function": {
        "name": "read_file", "description": "Read a file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}}]
    _client(transport).chat("claude-x", [{"role": "user", "content": "hi"}], openai_tools,
                            temperature=None, max_tokens=100, timeout=30)
    assert transport.calls[0]["payload"]["tools"] == [{
        "name": "read_file", "description": "Read a file.",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                         "required": ["path"]}}]


def test_consecutive_tool_results_merge_into_one_user_turn():
    from dirtywork.providers import ToolCall, assistant_message, tool_message
    transport = RecordingTransport([_fixture("simple_ok.json")])
    calls = [ToolCall(id="c1", name="list_dir", arguments={}, error=None),
             ToolCall(id="c2", name="list_dir", arguments={}, error=None)]
    history = [{"role": "user", "content": "go"}, assistant_message(None, calls),
               tool_message("c1", "a"), tool_message("c2", "b")]
    _client(transport).chat("claude-x", history, [], temperature=None,
                            max_tokens=10, timeout=5)
    messages = transport.calls[0]["payload"]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert len(messages[-1]["content"]) == 2


def test_tool_use_blocks_carry_raw_arguments_as_json():
    resp = _client(RecordingTransport([_fixture("parallel_tool_calls.json")])).chat(
        "claude-x", [{"role": "user", "content": "hi"}], [],
        temperature=None, max_tokens=10, timeout=5)
    assert resp.tool_calls[0].raw_arguments == '{"path": "a.txt"}'


def test_unreadable_body_raises_malformed_response():
    with pytest.raises(MalformedResponse):
        _client(RecordingTransport([{"content": "not a list"}])).chat(
            "claude-x", [{"role": "user", "content": "hi"}], [],
            temperature=None, max_tokens=10, timeout=5)


def test_unknown_stop_reason_passes_through():
    body = {"content": [{"type": "text", "text": "x"}], "stop_reason": "brand_new_reason"}
    resp = _client(RecordingTransport([body])).chat(
        "claude-x", [{"role": "user", "content": "hi"}], [],
        temperature=None, max_tokens=10, timeout=5)
    assert resp.finish_reason == "brand_new_reason"


def test_list_models_returns_ids():
    transport = RecordingTransport([{"data": [{"id": "claude-opus-5"}, {"id": "claude-sonnet-5"}]}])
    client = _client(transport)
    assert client.list_models() == ["claude-opus-5", "claude-sonnet-5"]
    assert transport.calls[0]["method"] == "GET"


def test_context_window_claude_prefix_and_unknown():
    client = _client(RecordingTransport([]))
    assert client.context_window("claude-opus-5") == 200000
    assert client.context_window("nonexistent/model") is None
