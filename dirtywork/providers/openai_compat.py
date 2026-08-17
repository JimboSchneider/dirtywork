from __future__ import annotations

import json
import math

from . import ChatResponse, ToolCall
from ..llm import LLMError, MalformedResponse, http_json

DEFAULT_BASE_URL = "http://localhost:1234/v1"

# Moved here from runner.py: a context window is a property of the model as the
# provider serves it, and resolve_context_window now asks the provider.
CONTEXT_WINDOWS = {
    "qwen/qwen3-coder-next": 65536,
    "mistralai/devstral-small-2-2512": 32768,
}

MALFORMED_ENTRY = "malformed tool call entry (missing or invalid id/function fields)"


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


def _sanitize_usage(raw) -> dict:
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    raw = raw if isinstance(raw, dict) else {}
    for k in usage:
        # usage is server-controlled: NaN/Infinity would survive json.loads and
        # later emit invalid JSON on our stdout/transcript contract. Accept only
        # finite, non-negative numbers.
        v = raw.get(k, 0)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0:
            usage[k] = int(v)
    return usage


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
                        finish_reason=finish_reason, usage=_sanitize_usage(body.get("usage")))


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
