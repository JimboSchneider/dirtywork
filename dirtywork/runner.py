from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Callable

from .budget import BudgetExceeded
from .llm import LLMTimeout
from .sandbox import SandboxError

MAX_ASSISTANT_TEXT_CHARS = 64_000

CONTEXT_WINDOWS = {
    "qwen/qwen3-coder-next": 65536,
    "mistralai/devstral-small-2-2512": 32768,
}
DEFAULT_WINDOW = 32768
TRIM_MARKER = "[result trimmed — re-run the tool if needed]"
CHARS_PER_TOKEN = 4
BUDGET_FRACTION = 0.75
MAX_CONSECUTIVE_FAILURES = 3
FINISH_TOOL = "finish"


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


def _canonical_tool_call(tc: dict) -> dict:
    """Rebuild an accepted call in canonical OpenAI wire shape."""
    fn = tc["function"]
    return {"id": tc["id"], "type": "function",
            "function": {"name": fn["name"], "arguments": fn.get("arguments") or "{}"}}


def _total_chars(messages: list) -> int:
    total = 0
    for m in messages:
        total += len(m.get("content") or "")
        for tc in m.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            total += len((tc.get("function") or {}).get("arguments") or "")
    return total


def trim_messages(messages: list, char_budget: int) -> bool:
    """Replace oldest tool results with TRIM_MARKER until under budget."""
    for m in messages:
        if _total_chars(messages) <= char_budget:
            return True
        if m.get("role") == "tool" and m.get("content") != TRIM_MARKER:
            m["content"] = TRIM_MARKER
    return _total_chars(messages) <= char_budget


@dataclass
class RunResult:
    status: str
    turns: int
    final_message: str
    usage: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)


class Runner:
    def __init__(self, client, executor, transcript, model,
                 max_turns: int = 40, timeout: int = 1800,
                 temperature: float | None = None,
                 run_info: dict | None = None,
                 finalize: Callable[[], dict] | None = None):
        self.client = client
        self.executor = executor
        self.transcript = transcript
        self.model = model
        self.max_turns = max_turns
        self.timeout = timeout
        self.temperature = temperature
        self.run_info = run_info
        self.finalize = finalize
        window = CONTEXT_WINDOWS.get(model, DEFAULT_WINDOW)
        self.char_budget = int(window * BUDGET_FRACTION * CHARS_PER_TOKEN)

    def run(self, system_prompt: str, task: str) -> RunResult:
        from .tools import TOOL_SCHEMAS

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]
        self.transcript.write("run_start", task=task, model=self.model,
                              max_turns=self.max_turns, timeout=self.timeout,
                              schema_version=2, **(self.run_info or {}))
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        turns = 0
        failures = 0
        start = time.monotonic()
        deadline = start + self.timeout
        self.executor.deadline = deadline

        def finish(status: str, final: str) -> RunResult:
            extra: dict = {}
            finalize_error = None
            if self.finalize is not None:
                try:
                    finalize_result = self.finalize()
                    if isinstance(finalize_result, dict):
                        extra.update(finalize_result)
                except Exception as e:
                    finalize_error = f"{type(e).__name__}: {e}"
            if finalize_error is not None:
                extra["finalize_error"] = finalize_error
            self.transcript.write("run_end", status=status, turns=turns,
                                  duration_s=round(time.monotonic() - start, 1),
                                  usage=usage, **extra)
            return RunResult(status, turns, final, usage, extra=extra)

        try:
            while True:
                if turns >= self.max_turns:
                    return finish("max_turns", "")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return finish("timeout", "")
                if not trim_messages(messages, self.char_budget):
                    return finish("context_exhausted", "")

                try:
                    resp = self.client.chat(self.model, messages, tools=TOOL_SCHEMAS,
                                            temperature=self.temperature,
                                            timeout=max(1.0, remaining))
                except LLMTimeout:
                    if time.monotonic() >= deadline - 0.5:
                        return finish("timeout", "")
                    else:
                        return finish("model_error",
                                      "model request timed out before the run deadline")
                turns += 1
                try:
                    msg = resp["choices"][0]["message"]
                    if not isinstance(msg, dict):
                        raise TypeError("message is not an object")
                except (KeyError, IndexError, TypeError):
                    return finish("model_error",
                                  "malformed response from server (no choices[0].message)")
                finish_reason = resp["choices"][0].get("finish_reason")
                resp_usage = resp.get("usage") or {}
                for k in usage:
                    # usage is server-controlled: NaN/Infinity would survive
                    # json.loads and later emit invalid JSON on our stdout/transcript
                    # contract. Accept only finite, non-negative numbers.
                    v = resp_usage.get(k, 0)
                    if isinstance(v, (int, float)) and not isinstance(v, bool) \
                            and math.isfinite(v) and v >= 0:
                        usage[k] += int(v)
                raw = msg.get("tool_calls") or []
                if not isinstance(raw, list):
                    raw = []
                tool_calls = [_canonical_tool_call(tc) for tc in raw if _valid_tool_call(tc)]
                malformed_count = len(raw) - len(tool_calls)
                transcript_text = msg.get("content")
                if isinstance(transcript_text, str) and len(transcript_text) > MAX_ASSISTANT_TEXT_CHARS:
                    transcript_text = (
                        transcript_text[:MAX_ASSISTANT_TEXT_CHARS]
                        + f"\n[truncated at {MAX_ASSISTANT_TEXT_CHARS} chars in the transcript "
                          f"only — the full text was sent to the model]"
                    )
                self.transcript.write(
                    "assistant", text=transcript_text,
                    tool_calls=[{"name": (tc.get("function") or {}).get("name"),
                                 "arguments": ((tc.get("function") or {}).get("arguments") or "")[:2000]}
                                for tc in tool_calls])
                if raw:
                    # Rebuild in canonical wire shape (id, type: "function", function
                    # with a string arguments) so the history we send back stays
                    # protocol-valid for strict OpenAI-compatible servers, on every
                    # path that resends tool calls -- not just when malformed.
                    clean_msg = {"role": "assistant", "content": msg.get("content") or ""}
                    if tool_calls:
                        clean_msg["tool_calls"] = tool_calls
                    messages.append(clean_msg)
                else:
                    messages.append(msg)
                    return finish("completed", msg.get("content") or "")

                for _ in range(malformed_count):
                    failures += 1
                    result = "ERROR: malformed tool call entry (missing or invalid id/function fields)"
                    self.transcript.write("tool_result", tool="", args="", result=result)
                if malformed_count > 0 and failures >= MAX_CONSECUTIVE_FAILURES:
                    return finish("model_error",
                                  "aborted after repeated malformed tool calls")

                pending_finish = None
                for tc in tool_calls:
                    fn_info = tc.get("function") or {}
                    name = fn_info.get("name") or ""
                    raw_args = fn_info.get("arguments") or "{}"
                    call_id = tc.get("id", "")
                    try:
                        args = json.loads(raw_args)
                        if not isinstance(args, dict):
                            raise ValueError("arguments must be a JSON object")
                        if name == FINISH_TOOL:
                            summary = args.get("summary")
                            pending_finish = summary if isinstance(summary, str) else ""
                            result = "run finished"
                        else:
                            result = self.executor.execute(name, args)
                            failures = 0
                    except BudgetExceeded as e:
                        return finish("budget_exceeded", e.reason)
                    except SandboxError as e:
                        return finish("sandbox_error", str(e))
                    except (json.JSONDecodeError, ValueError) as e:
                        failures += 1
                        if finish_reason == "length":
                            result = (
                                "ERROR: your reply was cut off at the token limit before "
                                "the tool call completed. Emit smaller tool calls — e.g. "
                                "write the file in pieces using multiple write_file/"
                                "edit_file calls."
                            )
                        else:
                            result = f"ERROR: malformed tool arguments: {e}"
                    except KeyError:
                        failures += 1
                        available_tools = ', '.join(s['function']['name'] for s in TOOL_SCHEMAS)
                        result = (f"ERROR: unknown tool '{name}'. Available: {available_tools}. "
                                  f"To end the run call finish(summary=...).")
                    except TypeError as e:
                        failures += 1
                        result = f"ERROR: bad arguments for {name}: {e}"
                    self.transcript.write("tool_result", tool=name,
                                          args=raw_args[:500],
                                          result=result[:2000])
                    messages.append({"role": "tool", "tool_call_id": call_id,
                                     "content": result})
                    if failures >= MAX_CONSECUTIVE_FAILURES:
                        return finish("model_error",
                                      "aborted after repeated malformed tool calls")

                if pending_finish is not None:
                    return finish("completed", pending_finish)

                if malformed_count > 0:
                    messages.append({
                        "role": "user",
                        "content": (f"{malformed_count} of your tool calls were malformed "
                                    "(missing or invalid id/function fields) and were "
                                    "discarded. Re-issue them as valid tool calls."),
                    })
        except KeyboardInterrupt:
            return finish("interrupted", "")
