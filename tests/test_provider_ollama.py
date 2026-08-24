from __future__ import annotations

import json
from pathlib import Path

from dirtywork.llm import LLMError, LLMTimeout
from dirtywork.providers import DEFAULT_BASE_URLS, PROVIDER_NAMES, get_provider
from dirtywork.providers.ollama import OLLAMA_DEFAULT_BASE_URL, OllamaClient
from dirtywork.providers.openai_compat import CONTEXT_WINDOWS, LOADED_CONTEXT_PROBE_TIMEOUT

from .provider_contract import ProviderContract, RecordingTransport

FIXTURES = Path(__file__).parent / "fixtures" / "providers" / "ollama"


def _client(transport, base_url=OLLAMA_DEFAULT_BASE_URL):
    return OllamaClient(base_url=base_url, http_json=transport)


class TestOllamaProviderContract(ProviderContract):
    """Ollama speaks the same wire format as the parent adapter; running the
    shared contract against ITS OWN fixtures is what keeps that claim honest
    instead of assumed."""

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


def test_name_and_default_base_url():
    assert OllamaClient.name == "ollama"
    assert OLLAMA_DEFAULT_BASE_URL == "http://localhost:11434/v1"
    assert "ollama" in PROVIDER_NAMES
    assert DEFAULT_BASE_URLS["ollama"] == OLLAMA_DEFAULT_BASE_URL
    assert get_provider("ollama").base_url == OLLAMA_DEFAULT_BASE_URL


def test_explicit_empty_base_url_is_not_replaced_by_the_ollama_default():
    # The subclass keeps the parent's None-only defaulting: an explicit "" is a
    # caller choice, not "unset".
    assert OllamaClient(base_url="").base_url == ""
    assert get_provider("ollama", "").base_url == ""


def test_context_window_is_always_none_not_lm_studios_table():
    # Spec §3.1: CONTEXT_WINDOWS is LM STUDIO's table. An Ollama user whose
    # model happens to share a name must not silently inherit its number under
    # the source `provider:ollama`.
    known = next(iter(CONTEXT_WINDOWS))
    assert CONTEXT_WINDOWS[known] > 0
    assert OllamaClient().context_window(known) is None
    assert OllamaClient().context_window("gemma4:latest") is None


def _ps_body(models):
    return {"models": models}


def test_loaded_context_window_returns_the_loaded_num_ctx():
    transport = RecordingTransport([_ps_body([
        {"name": "gemma4:latest", "model": "gemma4:latest", "size": 1, "context_length": 16384},
    ])])
    assert _client(transport).loaded_context_window("gemma4:latest") == 16384


def test_loaded_context_window_probes_api_ps_on_the_origin_with_the_short_timeout():
    transport = RecordingTransport([_ps_body([])])
    _client(transport, base_url="http://h:11434/prefix/v1").loaded_context_window("m")
    call = transport.calls[0]
    # /api/ps lives on the SERVER, not under the OpenAI-compatible /v1 prefix.
    assert call["url"] == "http://h:11434/api/ps"
    assert call["method"] == "GET"
    assert call["payload"] is None
    assert call["timeout"] == LOADED_CONTEXT_PROBE_TIMEOUT


def test_loaded_context_window_matches_the_model_key_only():
    # Ollama sets `model` and `name` to the same tagged id; matching both would
    # make one entry matchable twice (spec §3.1).
    transport = RecordingTransport([_ps_body([
        {"name": "gemma4:latest", "model": "other:latest", "context_length": 16384},
    ])])
    assert _client(transport).loaded_context_window("gemma4:latest") is None


def test_loaded_context_window_returns_none_on_the_first_match_without_scanning_on():
    # First match wins: a later, better-looking entry is NOT consulted.
    transport = RecordingTransport([_ps_body([
        {"model": "gemma4:latest", "context_length": None},
        {"model": "gemma4:latest", "context_length": 32768},
    ])])
    assert _client(transport).loaded_context_window("gemma4:latest") is None


def test_loaded_context_window_none_for_every_unusable_shape():
    cases = [
        _ps_body([]),                                                   # nothing resident
        _ps_body([{"model": "gemma4:latest"}]),                         # no context_length
        _ps_body([{"model": "gemma4:latest", "context_length": None}]),
        _ps_body([{"model": "gemma4:latest", "context_length": 0}]),
        _ps_body([{"model": "gemma4:latest", "context_length": -1}]),
        _ps_body([{"model": "gemma4:latest", "context_length": True}]),  # bool is not an int here
        _ps_body([{"model": "gemma4:latest", "context_length": "16384"}]),
        _ps_body([["gemma4:latest", 16384]]),                            # entry not an object
        {"models": "gemma4:latest"},                                     # models not a list
        {},                                                              # no models key
        [],                                                              # body not an object
    ]
    for body in cases:
        transport = RecordingTransport([body])
        assert _client(transport).loaded_context_window("gemma4:latest") is None, body


def test_loaded_context_window_none_when_unreachable():
    class _Boom:
        def __call__(self, url, payload, headers, timeout, *, method="POST"):
            raise LLMError("connection refused")

    class _Slow:
        def __call__(self, url, payload, headers, timeout, *, method="POST"):
            raise LLMTimeout("timed out")

    assert _client(_Boom()).loaded_context_window("gemma4:latest") is None
    assert _client(_Slow()).loaded_context_window("gemma4:latest") is None


def test_chat_payload_is_the_parents_shape():
    transport = RecordingTransport([json.loads((FIXTURES / "simple_ok.json").read_text())])
    _client(transport).chat("gemma4:latest", [{"role": "user", "content": "hi"}], [],
                            temperature=None, max_tokens=256, timeout=30)
    call = transport.calls[0]
    assert call["url"] == "http://localhost:11434/v1/chat/completions"
    assert call["payload"] == {"model": "gemma4:latest",
                               "messages": [{"role": "user", "content": "hi"}],
                               "max_tokens": 256}


def test_resolve_context_window_prefers_the_server_then_falls_to_default():
    from dirtywork.runner import DEFAULT_WINDOW, resolve_context_window
    loaded = _client(RecordingTransport([_ps_body([
        {"model": "gemma4:latest", "context_length": 16384}])]))
    assert resolve_context_window("gemma4:latest", None, None, loaded) == (
        16384, "provider:ollama:server")
    # Cold start (spec §3.1): /v1/models lists PULLED models, so preflight
    # passes for a model that is not resident; /api/ps then has no entry and
    # the window falls all the way to the default.
    cold = _client(RecordingTransport([_ps_body([])]))
    assert resolve_context_window("gemma4:latest", None, None, cold) == (
        DEFAULT_WINDOW, "default")


def test_runner_shaped_history_is_legal_for_strict_templates():
    from dirtywork.providers import ToolCall, assistant_message, tool_message
    from .provider_doubles import assert_strict_template_legal
    tc = ToolCall(id="abc123def", name="finish", arguments={"summary": "s"}, error=None,
                  raw_arguments='{"summary": "s"}')
    history = [{"role": "system", "content": "s"}, {"role": "user", "content": "task"},
               assistant_message("", [tc]),
               tool_message("abc123def", "VERIFY FAILED (round 1 of 2) ...\n\n" + "timeout nudge")]
    assert_strict_template_legal(history)
