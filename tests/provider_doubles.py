"""Shared provider doubles for the CLI tests (no test_ prefix: imported, never
collected). A double writes OpenAI wire bodies and this base turns them into a
ChatResponse with the real adapter's parser, so a CLI test exercises the same
deserialization production does."""
from __future__ import annotations

from dirtywork.changes import FINGERPRINT_SCRIPT
from dirtywork.providers.openai_compat import parse_chat_response
from dirtywork.sandbox.host import HostSandbox

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
        assert_strict_template_legal(history)      # spec #60 §7
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


class TimeoutThenFailingVerifySandbox:
    """Shared with test_transcript_schema (spec #60 §9.15).
    Worker bash calls time out; the --verify command runs "for real" and
    fails with a plain nonzero exit -- distinguished by command so the
    verify-failure text stays clean instead of itself reading as a timeout."""

    def __init__(self, verify_command):
        self.verify_command = verify_command

    def bash(self, command, timeout=120):
        if command == self.verify_command:
            return "exit code: 1\nboom"
        from dirtywork.tools import timeout_result
        return timeout_result(timeout)


class FingerprintSandbox(HostSandbox):
    """A HostSandbox whose `bash` answers FINGERPRINT_SCRIPT from a scripted
    list and delegates everything else (verify commands and the file tools
    stay real). Entries: a str hash -> "exit code: 0\n<hash>\n<40 zeros>" (two
    lines, as the real script prints a tree and HEAD) -- str entries MUST be
    40 lowercase hex chars (e.g. "a" * 40): they go through
    parse_fingerprint, which drops anything else; None -> "exit code:
    1\nerror: boom"; an exception instance -> raised. The last entry repeats.
    hashes=None -> the real script runs for the fingerprint too (a recording
    HostSandbox). `commands` records every (command, timeout) the runner sends."""
    def __init__(self, worktree, hashes=None, **kwargs):
        super().__init__(worktree, **kwargs)
        self.hashes = None if hashes is None else list(hashes)
        for h in self.hashes or []:
            assert h is None or isinstance(h, BaseException) or (len(h) == 40 and all(c in "0123456789abcdef" for c in h)), h
        self.commands = []

    def bash(self, command, timeout=120):
        self.commands.append((command, timeout))
        if command != FINGERPRINT_SCRIPT or self.hashes is None:
            return super().bash(command, timeout)
        entry = self.hashes.pop(0) if len(self.hashes) > 1 else self.hashes[0]
        if isinstance(entry, BaseException):
            raise entry
        if entry is None:
            return "exit code: 1\nerror: boom"
        return f"exit code: 0\n{entry}\n{'0' * 40}"
