from __future__ import annotations

import json
from pathlib import Path

import pytest

from dirtywork.llm import LLMError, LLMTimeout, MalformedResponse
from dirtywork.providers.openai_compat import OpenAICompatClient, parse_chat_response

from .provider_contract import ProviderContract, RecordingTransport

FIXTURES = Path(__file__).parent / "fixtures" / "providers" / "openai"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


def _client(transport):
    return OpenAICompatClient(base_url="http://fake/v1", http_json=transport)


class TestOpenAIProviderContract(ProviderContract):
    fixtures_dir = FIXTURES

    def make_client(self, transport):
        return _client(transport)

    def _system_text(self, payload):
        for m in payload["messages"]:
            if m["role"] == "system":
                return m["content"]
        return None

    def _tool_result_entries(self, payload):
        return [(m["tool_call_id"], m["content"])
                for m in payload["messages"] if m["role"] == "tool"]


def test_chat_omits_tools_when_empty():
    transport = RecordingTransport([_fixture("simple_ok.json")])
    _client(transport).chat("model-x", [{"role": "user", "content": "hi"}], [],
                            temperature=None, max_tokens=100, timeout=30)
    assert "tools" not in transport.calls[0]["payload"]


def test_chat_includes_tools_when_nonempty():
    transport = RecordingTransport([_fixture("simple_ok.json")])
    tools = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object", "properties": {}}}}]
    _client(transport).chat("model-x", [{"role": "user", "content": "hi"}], tools,
                            temperature=None, max_tokens=100, timeout=30)
    assert transport.calls[0]["payload"]["tools"] == tools


def test_chat_temperature_omitted_when_none_included_when_set():
    transport = RecordingTransport([_fixture("simple_ok.json"), _fixture("simple_ok.json")])
    client = _client(transport)
    client.chat("model-x", [{"role": "user", "content": "hi"}], [],
                temperature=None, max_tokens=100, timeout=30)
    assert "temperature" not in transport.calls[0]["payload"]
    client.chat("model-x", [{"role": "user", "content": "hi"}], [],
                temperature=0.2, max_tokens=100, timeout=30)
    assert transport.calls[1]["payload"]["temperature"] == 0.2


def test_list_models_returns_ids():
    transport = RecordingTransport([{"data": [{"id": "m1"}, {"id": "m2"}]}])
    client = _client(transport)
    assert client.list_models() == ["m1", "m2"]
    assert transport.calls[0]["method"] == "GET"
    assert transport.calls[0]["url"] == "http://fake/v1/models"


@pytest.mark.parametrize("bad_body", [
    {},
    {"data": "nope"},
    {"data": [{"nope": 1}]},
])
def test_list_models_unexpected_shape_raises_llmerror(bad_body):
    with pytest.raises(LLMError):
        _client(RecordingTransport([bad_body])).list_models()


def test_context_window_known_and_unknown_model():
    client = _client(RecordingTransport([]))
    assert client.context_window("qwen/qwen3-coder-next") == 65536
    assert client.context_window("mistralai/devstral-small-2-2512") == 32768
    assert client.context_window("nonexistent/model") is None


def test_provider_name_is_openai():
    assert _client(RecordingTransport([])).name == "openai"


# --- moved here from tests/test_runner.py: these assert how the OPENAI WIRE
# --- FORMAT is deserialized, which is now the adapter's job, not the runner's.

def test_arguments_null_treated_as_empty():
    body = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "list_dir", "arguments": None}}]},
        "finish_reason": "tool_calls"}]}
    resp = parse_chat_response(body)
    assert resp.tool_calls[0].arguments == {}
    assert resp.tool_calls[0].error is None
    assert resp.tool_calls[0].raw_arguments == "{}"


def test_valid_call_missing_type_field_is_accepted_and_canonicalized_on_resend():
    body = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "function": {"name": "list_dir", "arguments": "{}"}}]},
        "finish_reason": "tool_calls"}]}
    resp = parse_chat_response(body)
    assert resp.tool_calls[0].name == "list_dir"
    transport = RecordingTransport([_fixture("simple_ok.json")])
    from dirtywork.providers import assistant_message
    _client(transport).chat("m", [assistant_message(None, resp.tool_calls)], [],
                            temperature=None, max_tokens=10, timeout=5)
    sent = transport.calls[0]["payload"]["messages"][0]["tool_calls"][0]
    assert sent == {"id": "c1", "type": "function",
                    "function": {"name": "list_dir", "arguments": "{}"}}


def test_malformed_response_raises_malformed_response():
    for body in ({"choices": []}, {}, {"choices": [{"message": None}]},
                 {"choices": [{"message": "not an object"}]}):
        with pytest.raises(MalformedResponse):
            parse_chat_response(body)
    assert issubclass(MalformedResponse, LLMError)


def test_null_usage_tolerated():
    body = {"choices": [{"message": {"role": "assistant", "content": "hi"}}], "usage": None}
    assert parse_chat_response(body).usage == {"prompt_tokens": 0, "completion_tokens": 0}


def test_usage_ignores_non_finite_and_negative_from_server():
    body = json.loads('{"choices": [{"message": {"role": "assistant", "content": "hi"}}],'
                      ' "usage": {"prompt_tokens": Infinity, "completion_tokens": -3}}')
    assert parse_chat_response(body).usage == {"prompt_tokens": 0, "completion_tokens": 0}


def test_tool_calls_non_list_treated_as_absent():
    body = {"choices": [{"message": {"role": "assistant", "content": "hi",
                                     "tool_calls": "nope"}}]}
    assert parse_chat_response(body).tool_calls == []


@pytest.mark.parametrize("entry", [
    None,
    {},
    {"id": "", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}},
    {"id": "c1", "type": "function", "function": {"name": "", "arguments": "{}"}},
    {"id": "c1", "type": "function", "function": {"name": "list_dir", "arguments": 5}},
    {"id": "c1", "type": "function"},
])
def test_structurally_invalid_entries_become_id_less_error_tool_calls(entry):
    body = {"choices": [{"message": {"role": "assistant", "content": None,
                                     "tool_calls": [entry]}}]}
    tc = parse_chat_response(body).tool_calls[0]
    assert tc.id == ""
    assert tc.error == "malformed tool call entry (missing or invalid id/function fields)"


def test_mixed_invalid_and_valid_entries_keep_order():
    body = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        None,
        {"id": "c2", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}]}}]}
    calls = parse_chat_response(body).tool_calls
    assert [c.id for c in calls] == ["", "c2"]
    assert calls[0].error is not None and calls[1].error is None


def test_unparseable_arguments_keep_id_and_raw_text():
    resp = parse_chat_response(_fixture("bad_json_arguments.json"))
    tc = resp.tool_calls[0]
    assert tc.id == "call_badargs" and tc.name == "write_file"
    assert tc.arguments is None
    assert tc.error.startswith("malformed tool arguments:")
    assert tc.raw_arguments.startswith('{"path": "x"')


def test_non_object_arguments_are_a_malformed_args_error():
    body = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "list_dir", "arguments": "[1,2]"}}]}}]}
    tc = parse_chat_response(body).tool_calls[0]
    assert tc.id == "c1" and tc.arguments is None
    assert "must be a JSON object" in tc.error


_LOADED_BODY = {"data": [
    {"id": "other/model", "state": "loaded", "loaded_context_length": 4096},
    {"id": "qwen/qwen3-coder-next", "state": "loaded",
     "max_context_length": 262144, "loaded_context_length": 131072},
]}


def test_loaded_context_window_probes_the_origin_with_its_own_timeout():
    transport = RecordingTransport([_LOADED_BODY])
    client = _client(transport)
    assert client.loaded_context_window("qwen/qwen3-coder-next") == 131072
    call = transport.calls[0]
    assert call["url"] == "http://fake/api/v0/models"    # NOT under /v1
    assert call["method"] == "GET"
    assert call["payload"] is None
    assert call["timeout"] == 2


def test_loaded_context_window_drops_a_proxy_path_prefix():
    transport = RecordingTransport([_LOADED_BODY])
    client = OpenAICompatClient(base_url="http://h:1/prefix/v1", http_json=transport)
    assert client.loaded_context_window("qwen/qwen3-coder-next") == 131072
    assert transport.calls[0]["url"] == "http://h:1/api/v0/models"


@pytest.mark.parametrize("body", [
    {},                                                       # not the expected shape
    {"data": "nope"},                                         # data is not a list
    {"data": []},                                             # model absent
    {"data": [{"id": "other", "state": "loaded", "loaded_context_length": 4096}]},
    {"data": [{"id": "m", "state": "loading", "loaded_context_length": 4096}]},
    {"data": [{"id": "m", "state": None, "loaded_context_length": 4096}]},
    {"data": [{"id": "m", "state": "loaded"}]},                # field missing
    {"data": [{"id": "m", "state": "loaded", "loaded_context_length": None}]},
    {"data": [{"id": "m", "state": "loaded", "loaded_context_length": 0}]},
    {"data": [{"id": "m", "state": "loaded", "loaded_context_length": -1}]},
    {"data": [{"id": "m", "state": "loaded", "loaded_context_length": True}]},
    {"data": [{"id": "m", "state": "loaded", "loaded_context_length": "4096"}]},
])
def test_loaded_context_window_rejects_anything_else(body):
    assert _client(RecordingTransport([body])).loaded_context_window("m") is None


def test_loaded_context_window_accepts_an_entry_with_no_state_field():
    # The `state` KEY being absent is not the same as `"state": null` (rejected
    # above): a compatible server that reports the loaded length without a
    # state field at all is still answering the question.
    body = {"data": [{"id": "m", "loaded_context_length": 8192}]}
    assert _client(RecordingTransport([body])).loaded_context_window("m") == 8192


def test_loaded_context_window_is_none_when_the_endpoint_is_unreachable():
    def boom(url, payload, headers, timeout, *, method="POST"):
        raise LLMError(f"cannot reach {url}")

    client = OpenAICompatClient(base_url="http://fake/v1", http_json=boom)
    assert client.loaded_context_window("m") is None


def test_loaded_context_window_is_none_when_the_probe_times_out():
    # LLMTimeout subclasses LLMError, so one `except LLMError` covers both --
    # this test is what proves the probe's 2-second budget cannot fail a run.
    def slow(url, payload, headers, timeout, *, method="POST"):
        raise LLMTimeout(f"request to {url} exceeded {timeout}s")

    client = OpenAICompatClient(base_url="http://fake/v1", http_json=slow)
    assert client.loaded_context_window("m") is None
