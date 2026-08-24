"""Shared provider doubles for the CLI tests (no test_ prefix: imported, never
collected). A double writes OpenAI wire bodies and this base turns them into a
ChatResponse with the real adapter's parser, so a CLI test exercises the same
deserialization production does."""
from __future__ import annotations

from dirtywork.providers.openai_compat import parse_chat_response

DEFAULT_MODEL = "qwen/qwen3-coder-next"


class DictProvider:
    """Base for a test double driven by OpenAI chat-completions bodies.

    Subclass and implement ``reply(model, history, tools) -> dict``. Raising
    LLMError from ``reply`` is how a test simulates a dropped connection."""

    name = "openai"
    default_models = ()

    def __init__(self, base_url=None):
        self.base_url = base_url
        self.calls = 0

    def list_models(self):
        return list(self.default_models) or [DEFAULT_MODEL]

    def context_window(self, model):
        return None

    def reply(self, model, history, tools) -> dict:
        raise NotImplementedError

    def chat(self, model, history, tools, *, temperature=None, max_tokens=4096, timeout=None):
        self.calls += 1
        return parse_chat_response(self.reply(model, history, tools))


def text_body(text="done", prompt_tokens=1, completion_tokens=1) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}}


def tool_call_body(name, arguments, call_id="c1", prompt_tokens=1, completion_tokens=1) -> dict:
    import json
    return {"choices": [{"message": {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": call_id, "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)}}],
    }}], "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}}


class PreflightProvider(DictProvider):
    """Passes preflight and nothing else: tests using it patch Runner.run or
    fail before the first turn."""

    def reply(self, model, history, tools):
        return text_body()


def patch_provider(monkeypatch, module, factory):
    """Point a CLI module's get_provider at `factory(base_url)`, ignoring the
    provider name — the CLI test is exercising the run path, not the registry."""
    monkeypatch.setattr(module, "get_provider",
                        lambda name, base_url=None, timeout=600: factory(base_url))


def assert_strict_template_legal(history: list) -> None:
    """The Mistral/Devstral chat-template rule as a pure check on the runner's
    neutral history, serialized the way the OpenAI-compatible adapter sends it
    (spec #60 §1.1, §7). The template (lines 44-46 of the Devstral Small 2
    template) counts only `user` messages and assistant messages WITHOUT tool
    calls, and requires them to alternate starting with `user`; a leading
    `system` message is sliced off first (lines 9-25). Line 82 rejects an
    assistant message with empty content and no tool calls; this check is
    stricter on purpose and rejects whitespace-only content too (spec R2).
    A `user` directly after a `tool` is implied by the parity rule and is
    named separately so the failure reads as the #60 shape."""
    from dirtywork.providers.openai_compat import _to_openai_messages
    messages = _to_openai_messages(history)
    if messages and messages[0]["role"] == "system":
        messages = messages[1:]
    index = 0
    for i, m in enumerate(messages):
        role = m["role"]
        prev_role = messages[i - 1]["role"] if i else None
        has_calls = bool(m.get("tool_calls"))
        if role == "assistant" and not has_calls and not (m.get("content") or "").strip():
            raise AssertionError(f"messages[{i}]: empty assistant reply with no tool calls "
                                 f"(a strict template drops or rejects it): {messages}")
        if role == "user" and prev_role == "tool":
            raise AssertionError(f"messages[{i}]: user message directly after a tool result "
                                 f"(#60 shape; strict templates return 400): {messages}")
        counted = role == "user" or (role == "assistant" and not has_calls)
        if not counted:
            continue
        if (role == "user") != (index % 2 == 0):
            raise AssertionError(f"messages[{i}]: roles must alternate user/assistant among "
                                 f"counted messages (got {role} at counted index {index}): "
                                 f"{messages}")
        index += 1
