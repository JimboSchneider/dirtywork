from __future__ import annotations

import json
import urllib.parse

from . import ChatResponse, ToolCall, sanitize_usage
from ..llm import LLMError, MalformedResponse, http_json

DEFAULT_BASE_URL = "http://localhost:1234/v1"

# Moved here from runner.py: a context window is a property of the model as the
# provider serves it, and resolve_context_window now asks the provider.
CONTEXT_WINDOWS = {
    "qwen/qwen3-coder-next": 65536,
    "mistralai/devstral-small-2-2512": 32768,
}

# Spec §3.2: the loaded-context probe is a side query, not part of a turn, so it
# gets its own short deadline rather than the client's (600 s) chat timeout -- a
# server that does not implement the endpoint must cost a run essentially nothing.
LOADED_CONTEXT_PROBE_TIMEOUT = 2

MALFORMED_ENTRY = "malformed tool call entry (missing or invalid id/function fields)"


def _origin(url: str) -> str:
    """scheme://netloc of `url`. LM Studio's native API lives at /api/v0 on the
    SERVER, not under the OpenAI-compatible /v1 prefix, and a proxy path prefix
    ("http://h:1/prefix/v1") is not part of it either -- so the whole path is
    dropped. That can produce a 404 against a proxy that only forwards /v1,
    which is a normal None (spec §3.2), not an error."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _valid_tool_call(tc) -> bool:
    """Structurally valid OpenAI tool call: non-empty string id, function object
    with non-empty string name, arguments absent/None or a string."""
    if not isinstance(tc, dict):
        return False
    if not isinstance(tc.get("id"), str) or not tc["id"]:
        return False
    fn = tc.get("function")
    if not isinstance(fn, dict):
        return False
    if not isinstance(fn.get("name"), str) or not fn["name"]:
        return False
    args = fn.get("arguments")
    return args is None or isinstance(args, str)


def _parse_tool_calls(raw_list: list) -> list:
    out = []
    for tc in raw_list:
        if not _valid_tool_call(tc):
            # No usable id: the runner cannot answer this with a tool result.
            out.append(ToolCall(id="", name="", arguments=None, error=MALFORMED_ENTRY))
            continue
        fn = tc["function"]
        raw_args = fn.get("arguments") or "{}"
        try:
            parsed = json.loads(raw_args)
            if not isinstance(parsed, dict):
                raise ValueError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            out.append(ToolCall(id=tc["id"], name=fn["name"], arguments=None,
                                error=f"malformed tool arguments: {e}",
                                raw_arguments=raw_args))
            continue
        out.append(ToolCall(id=tc["id"], name=fn["name"], arguments=parsed, error=None,
                            raw_arguments=raw_args))
    return out


def _to_wire_tool_call(tc) -> dict:
    """Canonical OpenAI wire shape (id, type, function.arguments as a string) so
    the history we resend stays protocol-valid for strict servers — on every
    path that resends tool calls, not just when something was malformed. A call
    whose arguments failed to parse is resent with the model's own bytes."""
    if tc.raw_arguments:
        arguments = tc.raw_arguments
    else:
        arguments = json.dumps(tc.arguments or {})
    return {"id": tc.id, "type": "function",
            "function": {"name": tc.name, "arguments": arguments}}


def _to_openai_messages(history: list) -> list:
    messages = []
    for m in history:
        role = m["role"]
        if role == "assistant":
            msg = {"role": "assistant", "content": m.get("content") or ""}
            tool_calls = [tc for tc in (m.get("tool_calls") or []) if tc.id]
            if tool_calls:
                msg["tool_calls"] = [_to_wire_tool_call(tc) for tc in tool_calls]
            messages.append(msg)
        elif role == "tool":
            messages.append({"role": "tool", "tool_call_id": m["tool_call_id"],
                             "content": m.get("content") or ""})
        else:
            messages.append({"role": role, "content": m.get("content") or ""})
    return messages


def parse_chat_response(body) -> ChatResponse:
    """Deserialize one OpenAI chat-completions body. Public because the CLI
    tests drive the runner with recorded wire bodies and must go through the
    same code path the real adapter uses."""
    try:
        msg = body["choices"][0]["message"]
        if not isinstance(msg, dict):
            raise TypeError("message is not an object")
    except (KeyError, IndexError, TypeError):
        raise MalformedResponse("malformed response from server (no choices[0].message)")
    finish_reason = body["choices"][0].get("finish_reason")
    raw_calls = msg.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        raw_calls = []
    text = msg.get("content") if isinstance(msg.get("content"), str) else ""
    return ChatResponse(text=text, tool_calls=_parse_tool_calls(raw_calls),
                        finish_reason=finish_reason, usage=sanitize_usage(body.get("usage")))


class OpenAICompatClient:
    """Any OpenAI-compatible /v1 endpoint: LM Studio, vLLM, llama.cpp, Ollama's
    compat shim. `dirtywork.llm.LMStudioClient` is a deprecated alias."""

    name = "openai"

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = 600, *,
                 http_json=http_json):
        self.base_url = (DEFAULT_BASE_URL if base_url is None else base_url).rstrip("/")
        self.timeout = timeout
        self._http_json = http_json

    def list_models(self) -> list:
        body = self._http_json(f"{self.base_url}/models", None,
                               {"Content-Type": "application/json"}, self.timeout,
                               method="GET")
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise LLMError("unexpected /models response shape from server")
        ids = []
        for m in body["data"]:
            if not isinstance(m, dict) or not isinstance(m.get("id"), str):
                raise LLMError("unexpected /models entry shape from server")
            ids.append(m["id"])
        return ids

    def context_window(self, model: str):
        return CONTEXT_WINDOWS.get(model)

    def loaded_context_window(self, model: str):
        """Spec §3.2: the context length the server currently has this model
        LOADED with, or None. Verified live 2026-08-19 against LM Studio at
        localhost:1234 -- GET /api/v0/models returns
        {"data":[{"id":"qwen/qwen3-coder-next","state":"loaded",
        "max_context_length":262144,"loaded_context_length":65536, …}]} -- while
        /v1/models carries no context field at all.

        `max_context_length` is deliberately NOT used: it is what the model
        could do, not what the server allocated, and budgeting against a window
        the server does not have is worse than budgeting conservatively.

        Every other outcome -- connection error, timeout, non-2xx, non-JSON,
        missing/None/non-int/<=0 field, a model that is not loaded, a model that
        is absent -- returns None, so resolve_context_window falls through to the
        static table exactly as it did before 0.9."""
        url = f"{_origin(self.base_url)}/api/v0/models"
        try:
            body = self._http_json(url, None, {"Content-Type": "application/json"},
                                   LOADED_CONTEXT_PROBE_TIMEOUT, method="GET")
        except LLMError:
            return None      # LLMTimeout is an LLMError: both mean "no answer"
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            return None
        for entry in body["data"]:
            if not isinstance(entry, dict) or entry.get("id") != model:
                continue
            # Key PRESENCE, not truthiness: a server that reports no `state`
            # at all is still answering the question, but an explicit
            # `"state": null` is a present-and-not-"loaded" state and is
            # rejected like `"loading"` (spec §3.2).
            if "state" in entry and entry.get("state") != "loaded":
                return None
            loaded = entry.get("loaded_context_length")
            if isinstance(loaded, int) and not isinstance(loaded, bool) and loaded > 0:
                return loaded
            return None
        return None

    def chat(self, model, history, tools, *, temperature=None, max_tokens=4096,
             timeout=None) -> ChatResponse:
        payload = {"model": model, "messages": _to_openai_messages(history),
                   "max_tokens": max_tokens}
        if tools:
            payload["tools"] = tools
        if temperature is not None:
            payload["temperature"] = temperature
        effective_timeout = timeout if timeout is not None else self.timeout
        body = self._http_json(f"{self.base_url}/chat/completions", payload,
                               {"Content-Type": "application/json"}, effective_timeout)
        return parse_chat_response(body)
