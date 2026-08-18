"""Provider-neutral chat surface.

The runner keeps a neutral history of plain dicts and never sees a wire shape:
serialization and deserialization live entirely in the adapters
(`openai_compat.py`, `anthropic.py`). `get_provider` is the only place that
knows which adapter a provider name maps to.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Protocol

PROVIDER_NAMES = ("openai", "anthropic")

DEFAULT_BASE_URLS = {
    "openai": "http://localhost:1234/v1",
    "anthropic": "https://api.anthropic.com",
}


@dataclass
class ToolCall:
    """One tool call the model asked for.

    `arguments` is the decoded object, or None when the provider could not
    decode it -- in which case `error` says why. A call the provider could not
    address at all (no usable id) is reported with `id=""`; the runner treats
    that as a malformed *entry* (nothing to answer) rather than a malformed
    *argument* (answerable with an error tool result).

    `raw_arguments` is the original argument payload as the provider received
    it, kept so the transcript records the model's own bytes and so a call can
    be resent verbatim.
    """

    id: str
    name: str
    arguments: dict | None
    error: str | None
    raw_arguments: str = ""


@dataclass
class ChatResponse:
    text: str
    tool_calls: list = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict = field(default_factory=dict)


class Provider(Protocol):
    name: str

    def list_models(self) -> list:
        ...

    def context_window(self, model: str):
        ...

    def chat(self, model, history, tools, *, temperature, max_tokens, timeout) -> ChatResponse:
        ...


def assistant_message(text, tool_calls=None) -> dict:
    msg = {"role": "assistant", "content": text or ""}
    if tool_calls:
        msg["tool_calls"] = list(tool_calls)
    return msg


def tool_message(call_id: str, text: str) -> dict:
    return {"role": "tool", "content": text, "tool_call_id": call_id}


def get_provider(name: str, base_url: str | None = None, timeout: int = 600) -> Provider:
    """The adapters are imported lazily, inside the branches, so this module
    imports cleanly before either concrete adapter exists (Task 5/7) and so a
    missing optional dependency in one adapter can never break the other."""
    url = base_url or DEFAULT_BASE_URLS.get(name)
    if name == "openai":
        from .openai_compat import OpenAICompatClient
        return OpenAICompatClient(base_url=url, timeout=timeout)
    if name == "anthropic":
        from .anthropic import AnthropicClient
        return AnthropicClient(base_url=url, timeout=timeout)
    raise ValueError(f"unknown provider '{name}'. Available: {', '.join(PROVIDER_NAMES)}.")


def sanitize_usage(raw, *, prompt_key: str = "prompt_tokens",
                   completion_key: str = "completion_tokens") -> dict:
    """Normalise a server-reported usage object to {prompt_tokens, completion_tokens}
    ints. usage is server-controlled: NaN/Infinity survive json.loads and would later
    emit invalid JSON on our stdout/transcript contract, so only finite, non-negative
    numbers are accepted; anything else counts as 0. Adapters pass their wire key names."""
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    raw = raw if isinstance(raw, dict) else {}
    for key, wire_key in (("prompt_tokens", prompt_key), ("completion_tokens", completion_key)):
        v = raw.get(wire_key, 0)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0:
            usage[key] = int(v)
    return usage
