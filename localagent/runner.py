from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .llm import LLMTimeout

CONTEXT_WINDOWS = {
    "qwen/qwen3-coder-next": 65536,
    "mistralai/devstral-small-2-2512": 32768,
}
DEFAULT_WINDOW = 32768
TRIM_MARKER = "[result trimmed — re-run the tool if needed]"
CHARS_PER_TOKEN = 4
BUDGET_FRACTION = 0.75
MAX_CONSECUTIVE_FAILURES = 3


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


class Runner:
    def __init__(self, client, executor, transcript, model,
                 max_turns: int = 40, timeout: int = 1800,
                 temperature: float | None = None,
                 run_info: dict | None = None):
        self.client = client
        self.executor = executor
        self.transcript = transcript
        self.model = model
        self.max_turns = max_turns
        self.timeout = timeout
        self.temperature = temperature
        self.run_info = run_info
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
                              **(self.run_info or {}))
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        turns = 0
        failures = 0
        start = time.monotonic()
        deadline = start + self.timeout
        self.executor.deadline = deadline

        def finish(status: str, final: str) -> RunResult:
            self.transcript.write("run_end", status=status, turns=turns,
                                  duration_s=round(time.monotonic() - start, 1),
                                  usage=usage)
            return RunResult(status, turns, final, usage)

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
                    usage[k] += resp_usage.get(k, 0) or 0
                raw = msg.get("tool_calls") or []
                if not isinstance(raw, list):
                    raw = []
                tool_calls = [tc for tc in raw if isinstance(tc, dict)]
                malformed_count = len(raw) - len(tool_calls)
                self.transcript.write(
                    "assistant", text=msg.get("content"),
                    tool_calls=[{"name": (tc.get("function") or {}).get("name"),
                                 "arguments": ((tc.get("function") or {}).get("arguments") or "")[:2000]}
                                for tc in tool_calls])
                if malformed_count > 0:
                    # Sanitize: drop malformed entries (e.g. None) so the history we
                    # send back stays protocol-valid for strict OpenAI-compatible servers.
                    clean_msg = {"role": "assistant", "content": msg.get("content") or ""}
                    if tool_calls:
                        clean_msg["tool_calls"] = tool_calls
                    messages.append(clean_msg)
                else:
                    messages.append(msg)

                if not raw:
                    return finish("completed", msg.get("content") or "")

                for _ in range(malformed_count):
                    failures += 1
                    result = "ERROR: malformed tool call entry (not an object)"
                    self.transcript.write("tool_result", tool="", args="", result=result)
                if malformed_count > 0 and failures >= MAX_CONSECUTIVE_FAILURES:
                    return finish("model_error",
                                  "aborted after repeated malformed tool calls")

                for tc in tool_calls:
                    fn_info = tc.get("function") or {}
                    name = fn_info.get("name") or ""
                    raw_args = fn_info.get("arguments") or "{}"
                    call_id = tc.get("id", "")
                    try:
                        args = json.loads(raw_args)
                        if not isinstance(args, dict):
                            raise ValueError("arguments must be a JSON object")
                        result = self.executor.execute(name, args)
                        failures = 0
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
                        result = (f"ERROR: unknown tool '{name}'. Available: read_file, "
                                  f"write_file, edit_file, list_dir, grep, bash.")
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

                if malformed_count > 0:
                    messages.append({
                        "role": "user",
                        "content": (f"{malformed_count} of your tool calls were malformed "
                                    "(not an object) and were discarded. Re-issue them as "
                                    "valid tool calls."),
                    })
        except KeyboardInterrupt:
            return finish("interrupted", "")
