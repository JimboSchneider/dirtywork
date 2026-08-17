from __future__ import annotations

import json
import math
import os

from . import ChatResponse, ToolCall
from ..llm import LLMError, MalformedResponse, http_json

DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

_STOP_REASON_MAP = {
    "end_turn": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "refusal": "stop",
    "pause_turn": "stop",
}


def _parse_tool_use_block(b: dict) -> ToolCall:
    call_id = b.get("id")
    name = b.get("name")
    if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
        # Unaddressable: the runner counts a malformed_entry and answers nothing.
        return ToolCall(id="", name="", arguments=None,
                        error="malformed tool call entry (missing or invalid id/name fields)")
    input_ = b.get("input")
    if not isinstance(input_, dict):
        return ToolCall(id=call_id, name=name, arguments=None,
                        error="malformed tool arguments: missing or non-object input "
                              "(likely truncated by max_tokens)")
    return ToolCall(id=call_id, name=name, arguments=input_, error=None,
                    raw_arguments=json.dumps(input_))


def _to_anthropic_tool(t: dict) -> dict:
    fn = t["function"]
    return {"name": fn["name"], "description": fn.get("description", ""),
            "input_schema": fn["parameters"]}


def _to_anthropic_messages(history: list):
    """Returns (system: str | None, messages: list). Consecutive `tool` entries
    merge into one `user` message with multiple tool_result blocks, per the
    Anthropic wire contract (tool results ride in a single user turn)."""
    system_parts = []
    messages = []
    for m in history:
        role = m["role"]
        if role == "system":
            if m.get("content"):
                system_parts.append(m["content"])
            continue
        if role == "assistant":
            blocks = []
            text = m.get("content")
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in m.get("tool_calls") or []:
                if not tc.id:
                    continue     # unaddressable: never resent
                blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name,
                               "input": tc.arguments or {}})
            messages.append({"role": "assistant", "content": blocks if blocks else (text or "")})
            continue
        if role == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"],
                     "content": m.get("content") or ""}
            if (messages and messages[-1]["role"] == "user"
                    and isinstance(messages[-1]["content"], list)
                    and messages[-1]["content"]
                    and messages[-1]["content"][-1].get("type") == "tool_result"):
                messages[-1]["content"].append(block)
            else:
                messages.append({"role": "user", "content": [block]})
            continue
        messages.append({"role": role, "content": m.get("content") or ""})
    system = "\n\n".join(system_parts) if system_parts else None
    return system, messages


def _sanitize_usage(raw) -> dict:
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    raw = raw if isinstance(raw, dict) else {}
    for key, wire_key in (("prompt_tokens", "input_tokens"),
                          ("completion_tokens", "output_tokens")):
        v = raw.get(wire_key, 0)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0:
            usage[key] = int(v)
    return usage


class AnthropicClient:
    name = "anthropic"

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = 600, *,
                 http_json=http_json, api_key: str | None = None):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._http_json = http_json
        # Read host-side, at construction. Never forwarded into the sandbox --
        # the sandbox never sees provider credentials.
        self.api_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")

    def _headers(self) -> dict:
        return {"x-api-key": self.api_key, "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json"}

    def _require_key(self) -> None:
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")

    def list_models(self) -> list:
        self._require_key()
        # NOTE: the exact /v1/models response envelope was NOT verified against
        # a live wire example while writing this plan; it follows the
        # {"data": [...]} shape Anthropic documents for its other list
        # endpoints. Verify against current Anthropic docs before relying on
        # this in production.
        body = self._http_json(f"{self.base_url}/v1/models", None, self._headers(),
                               self.timeout, method="GET")
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise LLMError("unexpected /v1/models response shape from server")
        ids = []
        for m in body["data"]:
            if not isinstance(m, dict) or not isinstance(m.get("id"), str):
                raise LLMError("unexpected /v1/models entry shape from server")
            ids.append(m["id"])
        return ids

    def context_window(self, model: str):
        # Deliberately conservative and static: this only feeds the runner's
        # trim budget, never API correctness, and --context-window overrides it
        # per run. Verify against current Anthropic docs if precision matters.
        if model.startswith("claude-"):
            return 200000
        return None

    def chat(self, model, history, tools, *, temperature=None, max_tokens=4096,
             timeout=None) -> ChatResponse:
        self._require_key()
        system, messages = _to_anthropic_messages(history)
        payload = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [_to_anthropic_tool(t) for t in tools]
        if temperature is not None:
            payload["temperature"] = temperature
        effective_timeout = timeout if timeout is not None else self.timeout
        body = self._http_json(f"{self.base_url}/v1/messages", payload, self._headers(),
                               effective_timeout)
        if not isinstance(body, dict) or not isinstance(body.get("content"), list):
            raise MalformedResponse("malformed response from server (no content blocks)")
        content = body["content"]
        text_parts = [b["text"] for b in content
                      if isinstance(b, dict) and b.get("type") == "text"
                      and isinstance(b.get("text"), str)]
        tool_calls = [_parse_tool_use_block(b) for b in content
                      if isinstance(b, dict) and b.get("type") == "tool_use"]
        raw_stop = body.get("stop_reason")
        return ChatResponse(text="".join(text_parts), tool_calls=tool_calls,
                            finish_reason=_STOP_REASON_MAP.get(raw_stop, raw_stop),
                            usage=_sanitize_usage(body.get("usage")))
