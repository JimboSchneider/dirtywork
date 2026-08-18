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
