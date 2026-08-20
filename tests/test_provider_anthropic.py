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


def _assert_alternating(messages):
    """Every wire message alternates user/assistant strictly -- what the
    Anthropic Messages API requires and what A1/A2's merges exist to
    guarantee."""
    assert messages, "expected at least one message"
    for i in range(1, len(messages)):
        assert messages[i]["role"] != messages[i - 1]["role"], (
            f"messages[{i - 1}] and messages[{i}] are both '{messages[i]['role']}': {messages}")


def test_tool_result_then_user_nudge_merges_into_one_user_turn():
    from dirtywork.providers import ToolCall, assistant_message, tool_message
    transport = RecordingTransport([_fixture("simple_ok.json")])
    calls = [ToolCall(id="c1", name="list_dir", arguments={}, error=None)]
    history = [{"role": "user", "content": "go"}, assistant_message(None, calls),
               tool_message("c1", "a"), {"role": "user", "content": "please continue"}]
    _client(transport).chat("claude-x", history, [], temperature=None,
                            max_tokens=10, timeout=5)
    messages = transport.calls[0]["payload"]["messages"]
    _assert_alternating(messages)
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    last_content = messages[-1]["content"]
    assert last_content[-1] == {"type": "text", "text": "please continue"}


def test_empty_assistant_reply_dropped_and_user_texts_merge():
    transport = RecordingTransport([_fixture("simple_ok.json")])
    history = [{"role": "user", "content": "task"},
               {"role": "assistant", "content": ""},
               {"role": "user", "content": "nudge"}]
    _client(transport).chat("claude-x", history, [], temperature=None,
                            max_tokens=10, timeout=5)
    messages = transport.calls[0]["payload"]["messages"]
    _assert_alternating(messages)
    assert messages == [{"role": "user", "content": [
        {"type": "text", "text": "task"}, {"type": "text", "text": "nudge"}]}]


def test_assistant_with_tool_use_and_empty_text_is_kept():
    from dirtywork.providers import ToolCall, assistant_message, tool_message
    transport = RecordingTransport([_fixture("simple_ok.json")])
    calls = [ToolCall(id="c1", name="list_dir", arguments={}, error=None)]
    history = [{"role": "user", "content": "go"}, assistant_message("", calls),
               tool_message("c1", "a")]
    _client(transport).chat("claude-x", history, [], temperature=None,
                            max_tokens=10, timeout=5)
    messages = transport.calls[0]["payload"]["messages"]
    _assert_alternating(messages)
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assistant_msg = messages[1]
    assert assistant_msg["content"] == [
        {"type": "tool_use", "id": "c1", "name": "list_dir", "input": {}}]


def test_nested_parameters_reach_input_schema_unchanged():
    # Spec §1.3: a ParamSpec.schema renders into `function.parameters`, and the
    # Anthropic adapter forwards `parameters` verbatim as `input_schema` -- so
    # both wire renderings carry the nested schema without adapter-side work.
    from dirtywork.providers.anthropic import _to_anthropic_tool
    from dirtywork.toolspec import Caps, ParamSpec, ToolRegistry, ToolSpec

    nested = {"type": "array", "minItems": 1,
              "items": {"type": "object",
                        "properties": {"old": {"type": "string"},
                                       "new": {"type": "string"}},
                        "required": ["old", "new"], "additionalProperties": False}}
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="apply_edits", description="batch edits",
        params={"path": ParamSpec(type="string"),
                "edits": ParamSpec(type="array", schema=nested)},
        required=("path", "edits"), fn=lambda sandbox, path, edits: "",
        caps=Caps(fs="write")))
    tool = _to_anthropic_tool(registry.schemas()[0])
    assert tool["name"] == "apply_edits"
    assert tool["input_schema"]["properties"]["edits"] == nested
    assert tool["input_schema"]["required"] == ["path", "edits"]


def test_loaded_context_window_is_none():
    # Spec §3.1: implemented explicitly, so resolve_context_window's optional
    # hook has a visible answer on both shipped providers.
    assert _client(RecordingTransport([])).loaded_context_window("claude-opus-5") is None
