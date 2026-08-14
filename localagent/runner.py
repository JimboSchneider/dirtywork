from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

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
    return sum(len(m.get("content") or "") for m in messages)


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
                 temperature: float | None = None):
        self.client = client
        self.executor = executor
        self.transcript = transcript
        self.model = model
        self.max_turns = max_turns
        self.timeout = timeout
        self.temperature = temperature
        window = CONTEXT_WINDOWS.get(model, DEFAULT_WINDOW)
        self.char_budget = int(window * BUDGET_FRACTION * CHARS_PER_TOKEN)

    def run(self, system_prompt: str, task: str) -> RunResult:
        from .tools import TOOL_SCHEMAS

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]
        self.transcript.write("run_start", task=task, model=self.model,
                              max_turns=self.max_turns, timeout=self.timeout)
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        turns = 0
        failures = 0
        start = time.monotonic()

        def finish(status: str, final: str) -> RunResult:
            self.transcript.write("run_end", status=status, turns=turns,
                                  duration_s=round(time.monotonic() - start, 1),
                                  usage=usage)
            return RunResult(status, turns, final, usage)

        try:
            while True:
                if turns >= self.max_turns:
                    return finish("max_turns", "")
                if time.monotonic() - start > self.timeout:
                    return finish("timeout", "")
                if not trim_messages(messages, self.char_budget):
                    return finish("context_exhausted", "")

                resp = self.client.chat(self.model, messages, tools=TOOL_SCHEMAS,
                                        temperature=self.temperature)
                turns += 1
                for k in usage:
                    usage[k] += resp.get("usage", {}).get(k, 0)
                msg = resp["choices"][0]["message"]
                tool_calls = msg.get("tool_calls") or []
                self.transcript.write(
                    "assistant", text=msg.get("content"),
                    tool_calls=[{"name": tc["function"]["name"],
                                 "arguments": tc["function"]["arguments"]}
                                for tc in tool_calls])
                messages.append(msg)

                if not tool_calls:
                    return finish("completed", msg.get("content") or "")

                for tc in tool_calls:
                    name = tc["function"]["name"]
                    call_id = tc.get("id", "")
                    try:
                        args = json.loads(tc["function"]["arguments"] or "{}")
                        if not isinstance(args, dict):
                            raise ValueError("arguments must be a JSON object")
                        result = self.executor.execute(name, args)
                        failures = 0
                    except (json.JSONDecodeError, ValueError) as e:
                        failures += 1
                        result = f"ERROR: malformed tool arguments: {e}"
                    except KeyError:
                        failures += 1
                        result = (f"ERROR: unknown tool '{name}'. Available: read_file, "
                                  f"write_file, edit_file, list_dir, grep, bash.")
                    except TypeError as e:
                        failures += 1
                        result = f"ERROR: bad arguments for {name}: {e}"
                    self.transcript.write("tool_result", tool=name,
                                          args=tc["function"]["arguments"][:500],
                                          result=result[:2000])
                    messages.append({"role": "tool", "tool_call_id": call_id,
                                     "content": result})
                    if failures >= MAX_CONSECUTIVE_FAILURES:
                        return finish("model_error",
                                      "aborted after repeated malformed tool calls")
        except KeyboardInterrupt:
            return finish("interrupted", "")
