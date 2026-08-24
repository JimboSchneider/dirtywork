# #60 Harness follow-ups after tool results — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every harness-authored follow-up (verify feedback, timeout/stall/malformed nudges, the nudge after an empty reply) lands in a history position that strict chat templates (Mistral/Devstral on LM Studio) render, the `finish` tool result is honest on every exit path, and the transcript records what the model was sent.

**Architecture:** Runner-level carriers (spec §2-§5): a follow-up on a tool-call turn rides on that turn's last tool result (`follow_up`), verify feedback becomes the `finish` call's own result, an empty reply is stored with a placeholder so no template drops it. The transcript gains a turn-scoped write buffer (`Transcript.turn()`) so a turn's events are written once its wire messages are final, plus sparse fields `follow_up`, `placeholder`, `via`. Providers are untouched; legality is enforced by a strict-template oracle inside every test double that drives the runner.

**Tech Stack:** Python ≥ 3.9, stdlib only (`threading`, `contextlib`, `json`, `re`, `html`). Dev-only dependency: pytest. Test command: `/usr/bin/python3 -m pytest -q` (the only interpreter on this machine with pytest; it is the 3.9 floor). Live tests need LM Studio at `http://localhost:1234/v1` with `mistralai/devstral-small-2-2512` loaded; soak reruns need Docker.

**Spec:** `docs/superpowers/specs/2026-08-23-harness-followups-after-tool-results-design.md` (v4, owner-approved). Read it before any task; each task cites the sections it implements.

## Global Constraints

- Python 3.9 floor; stdlib only; no new runtime dependencies. No `match`, no `X | Y` in runtime annotations without `from __future__ import annotations` (every touched module already has it).
- `schema_version` stays `2`. Every transcript change is a new sparse field (`follow_up`, `placeholder`, `via`), a new documented enum value (`nudge.kind: malformed_entry`) or a new documented string value of an existing field (`finish` results). Never remove or rename a field.
- Baseline: `1237 passed, 1 skipped, 20 deselected` at `b94dec9`. **Every existing test stays green after every task**; the count only rises. Update existing tests only where the spec's §9.13 says their assertions change; never delete one.
- Exact strings (copy verbatim): `EMPTY_REPLY_PLACEHOLDER = "[empty reply]"`; `FINISH_DONE = "run finished"`; `FINISH_PROVISIONAL = "run not finished: verify did not run"`; terminal finish strings `run not finished: verify failed (exit <code>); no fix rounds remain`, `run not finished: verify could not run (<reason>)`, `run not finished: <status>`; nudge kind `malformed_entry`; `via` values `tool_result` / `user` (nudge) and `finish_result` / `user` (verify); wire join `result + "\n\n" + follow_up`.
- DRY: one `deliver()`, one `append_assistant()`, one `resolve_finish()` in `Runner.run()`; one `assert_strict_template_legal()` in `tests/provider_doubles.py`; the Markdown export reuses `_md_block`/`_md_fence`/`_md_inline`.
- Commit after every task with the message given; do not squash tasks together. Never push, merge or release — the owner does that.
- Work on branch `issue-60-followup-carriers` (already exists, spec commits on it).

---

## File Structure

| File | Responsibility after this plan |
|---|---|
| `dirtywork/transcript.py` | Append-only JSONL writer **plus** the turn-scoped buffer: `turn()`, `flush()`, `close()`, one lock (spec §6.1). |
| `dirtywork/runner.py` | Owns history shape: `append_assistant()` (R2 placeholder), `deliver()` (R1 carriers), `resolve_finish()` (§4), per-turn lists `turn_tool_msgs`/`turn_terminal`, the loop body as `one_turn()` inside `transcript.turn()`. |
| `dirtywork/builtin_tools.py` | `FINISH_SPEC` declares `transcript="full"`. |
| `dirtywork/bench.py` | `NUDGE_KINDS` gains `malformed_entry`. |
| `dirtywork/runs.py` | Both renderers show `follow_up`, `placeholder`, `[not finished]`; `finish` exempt from the Markdown trim. |
| `tests/provider_doubles.py` | Shared doubles: `assert_strict_template_legal()`, `DictProvider` calling it, `TimeoutThenFailingVerifySandbox`. |
| `tests/test_transcript.py`, `tests/test_runner.py`, `tests/test_runs.py`, `tests/test_transcript_schema.py`, `tests/test_provider_openai.py`, `tests/test_provider_ollama.py`, `tests/test_provider_anthropic.py`, `tests/test_bench.py`, `tests/test_live.py` | Tests per task. |
| `docs/transcript-schema.md`, `docs/machine-contract.md`, `docs/operating.md` | Contract text (spec §8). |
| `docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md` | §10 rerun rows appended. |

---

### Task 1: `Transcript.turn()` — turn-scoped buffer with atomic close

**Files:**
- Modify: `dirtywork/transcript.py` (whole class, 44 lines today)
- Test: `tests/test_transcript.py` (append after `test_flushes_each_line_before_close`)
- Modify: `docs/transcript-schema.md:3-5`, `docs/machine-contract.md:326-327`

**Interfaces:**
- Produces: `Transcript.turn()` context manager; `Transcript.write(event, **fields) -> dict | None` (the buffered record while a turn is open, else `None`); `Transcript.flush() -> None`; `Transcript.close() -> None` (flushes first). Later tasks amend the returned dict inside the turn.

Spec: §6.1 (including the *(v3)* lock scope and *(v4)* atomic-close invariant), §9.11.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transcript.py`:

```python
def _lines(path: Path) -> list:
    return [json.loads(l) for l in path.read_text().splitlines()]


def test_turn_buffers_writes_until_exit_and_keeps_order(tmp_path: Path):
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    t.write("run_start", task="x")
    with t.turn():
        rec = t.write("guardrail_block", tool="bash", reason="no")
        t.write("tool_result", tool="bash", result="r")
        t.write("nudge", kind="timeout", turn=1)
        assert isinstance(rec, dict) and rec["event"] == "guardrail_block"
        assert path.read_text().count("\n") == 1          # nothing of the turn is on disk yet
    events = [e["event"] for e in _lines(path)]
    assert events == ["run_start", "guardrail_block", "tool_result", "nudge"]
    t.close()


def test_turn_write_returns_none_outside_a_turn(tmp_path: Path):
    t = Transcript(tmp_path / "t.jsonl")
    assert t.write("run_start", task="x") is None
    t.close()


def test_record_amended_inside_the_turn_is_written_amended(tmp_path: Path):
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    with t.turn():
        rec = t.write("tool_result", tool="finish", result="provisional")
        rec["result"] = "resolved"
        rec["follow_up"] = "extra"
    ev = _lines(path)[0]
    assert ev["result"] == "resolved" and ev["follow_up"] == "extra"
    t.close()


def test_amendment_after_flush_does_not_reach_disk(tmp_path: Path):
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    with t.turn():
        rec = t.write("tool_result", tool="bash", result="a")
    rec["result"] = "late"
    assert _lines(path)[0]["result"] == "a"
    t.close()


def test_ts_is_stamped_at_write_time_and_non_decreasing(tmp_path: Path):
    import time
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    with t.turn():
        r1 = t.write("tool_result", tool="bash", result="a")
        time.sleep(0.01)
        r2 = t.write("tool_result", tool="bash", result="b")
        assert r1["ts"] < r2["ts"]
    a, b = _lines(path)
    assert a["ts"] == r1["ts"] and b["ts"] == r2["ts"]
    t.close()


def test_turn_flushes_on_keyboard_interrupt(tmp_path: Path):
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    with pytest.raises(KeyboardInterrupt):
        with t.turn():
            t.write("tool_result", tool="bash", result="a")
            raise KeyboardInterrupt
    assert [e["event"] for e in _lines(path)] == ["tool_result"]
    t.close()


def test_record_resolved_in_an_except_handler_inside_the_turn_is_written_resolved(tmp_path: Path):
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    with t.turn():
        rec = t.write("tool_result", tool="finish", result="provisional")
        try:
            raise KeyboardInterrupt
        except KeyboardInterrupt:
            rec["result"] = "run not finished: interrupted"
    assert _lines(path)[0]["result"] == "run not finished: interrupted"
    t.close()


def test_flush_mid_turn_writes_and_keeps_the_turn_open(tmp_path: Path):
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    with t.turn():
        t.write("tool_result", tool="bash", result="a")
        t.flush()
        assert [e["event"] for e in _lines(path)] == ["tool_result"]
        rec = t.write("run_end", status="completed")
        assert rec is not None                           # still buffering
        assert path.read_text().count("\n") == 1
    assert [e["event"] for e in _lines(path)] == ["tool_result", "run_end"]
    t.close()


def test_turn_is_not_reentrant(tmp_path: Path):
    t = Transcript(tmp_path / "t.jsonl")
    with t.turn():
        with pytest.raises(RuntimeError):
            with t.turn():
                pass
    t.close()


def test_close_flushes_an_open_turn(tmp_path: Path):
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    cm = t.turn()
    cm.__enter__()
    t.write("tool_result", tool="bash", result="a")
    t.close()
    assert [e["event"] for e in _lines(path)] == ["tool_result"]


def test_write_from_another_thread_during_a_turn_lands_with_the_turn(tmp_path: Path):
    import threading
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    started = threading.Event()
    with t.turn():
        t.write("tool_result", tool="bash", result="a")

        def other():
            t.write("sandbox_reset", reason="stray")
            started.set()
        th = threading.Thread(target=other)
        th.start()
        started.wait(5)
        th.join(5)
        assert path.read_text() == ""                     # buffered, not written directly
    assert [e["event"] for e in _lines(path)] == ["tool_result", "sandbox_reset"]
    t.close()


def test_atomic_close_never_loses_a_racing_write(tmp_path: Path):
    """Spec §6.1 (v4): a writer racing the turn's exit either lands in the
    buffer (and is flushed with it) or writes directly after the close. Every
    write is on disk exactly once."""
    import threading
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    total = 0
    for _ in range(200):
        stop = threading.Event()
        wrote = [0]

        def hammer():
            while not stop.is_set():
                t.write("sandbox_reset", reason="r")
                wrote[0] += 1
        th = threading.Thread(target=hammer)
        with t.turn():
            t.write("tool_result", tool="bash", result="a")
            th.start()
        stop.set()
        th.join(5)
        total += wrote[0] + 1
    t.close()
    lines = _lines(path)
    assert len(lines) == total
    # every turn's tool_result precedes the resets written after its close
    assert lines[0]["event"] == "tool_result"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/usr/bin/python3 -m pytest -q tests/test_transcript.py`
Expected: the new tests FAIL with `AttributeError: 'Transcript' object has no attribute 'turn'` (and `assert None is not None` shapes); the six pre-existing tests still pass.

- [ ] **Step 3: Implement the buffer**

Replace the whole of `dirtywork/transcript.py` with:

```python
from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class Transcript:
    """Append-only JSONL event log.

    Outside a turn every `write` reaches disk immediately (`tail -f` sees it
    at once). Inside `turn()` writes are buffered and land together, in write
    order, when the turn exits -- normally or by ANY exception, KeyboardInterrupt
    included -- so the runner can amend a turn's records (the `finish` result,
    a `follow_up`) until the turn's wire messages are final (spec #60 §6.1).
    `tail -f` therefore shows a run one turn at a time; a hard kill loses at
    most the current turn's events.

    One lock covers the buffer AND the physical write, so a write from another
    thread (the docker watchdog's `sandbox_reset`) can neither interleave a
    partial line nor slip between the closing flush and the close itself:
    leaving a turn is a single critical section.

    The parent directory must already exist (created by
    `dirtywork.rundir.create_run_dir` before this is constructed) — this
    class no longer creates it. Opened with O_EXCL so a slug collision (or a
    symlink planted at the transcript path) is a loud failure instead of a
    silent append/overwrite, and O_NOFOLLOW so a symlink at the exact path
    is refused rather than followed.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | os.O_NOFOLLOW
        fd = os.open(str(self.path), flags, 0o600)
        self._fh = os.fdopen(fd, "a", encoding="utf-8")
        self._lock = threading.Lock()
        self._buffer = None      # a list while a turn is open, else None

    def write(self, event: str, **fields):
        """Record the event. Returns the record (a dict the caller may amend
        until its turn flushes) while a turn is open; None otherwise, when the
        line is already on disk. `ts` is stamped here, never at flush time."""
        record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event}
        record.update(fields)
        with self._lock:
            if self._buffer is not None:
                self._buffer.append(record)
                return record
            self._write_line(record)
            return None

    def _write_line(self, record: dict) -> None:
        # Caller holds self._lock.
        # allow_nan=False keeps the JSONL strictly valid; a NaN/Infinity that
        # reached here (e.g. from a hostile server response) is re-dumped with
        # those constants coerced to null rather than emitting invalid JSON.
        try:
            line = json.dumps(record, ensure_ascii=False, allow_nan=False)
        except ValueError:
            scrubbed = json.loads(
                json.dumps(record, ensure_ascii=False),
                parse_constant=lambda _c: None,  # NaN/Infinity -> null
            )
            line = json.dumps(scrubbed, ensure_ascii=False, allow_nan=False)
        self._fh.write(line + "\n")
        self._fh.flush()

    def _flush_locked(self, close_turn: bool) -> None:
        # Caller holds self._lock. Swap the list out, write it, and -- when
        # closing -- mark the turn closed in the SAME critical section, so no
        # racing write can enter a buffer that is then cleared unflushed.
        pending = self._buffer or []
        self._buffer = None if close_turn else ([] if self._buffer is not None else None)
        for record in pending:
            self._write_line(record)

    def flush(self) -> None:
        """Write whatever is buffered now, in order, leaving the turn open."""
        with self._lock:
            self._flush_locked(close_turn=False)

    @contextmanager
    def turn(self):
        """Buffer every write until the block exits (see the class docstring)."""
        with self._lock:
            if self._buffer is not None:
                raise RuntimeError("Transcript.turn() is not reentrant")
            self._buffer = []
        try:
            yield
        finally:
            with self._lock:
                self._flush_locked(close_turn=True)

    def close(self) -> None:
        with self._lock:
            self._flush_locked(close_turn=True)
            self._fh.close()
```

- [ ] **Step 4: Run the transcript tests**

Run: `/usr/bin/python3 -m pytest -q tests/test_transcript.py`
Expected: all pass (6 old + 12 new).

- [ ] **Step 5: Update the two doc passages that promise per-line flushing**

In `docs/transcript-schema.md:3-5` replace

```
`~/.dirtywork/runs/<slug>/transcript.jsonl` (`tail -f` friendly — each line is
flushed immediately). Every line has at least `ts` (UTC ISO-8601) and `event`
```

with

```
`~/.dirtywork/runs/<slug>/transcript.jsonl`. Since 1.0 the events of one model
turn are flushed together when the turn ends (`run_start`, and a `run_end`
written by the CLI's own failure path, are flushed immediately), so `tail -f`
shows a run one turn at a time and a hard kill loses at most the current turn.
Every line has at least `ts` (UTC ISO-8601, stamped when the event happened,
not when it was flushed) and `event`
```

In `docs/machine-contract.md:326-327` replace `watch a live run with `tail -f` on the transcript path.` with `watch a live run with `tail -f` on the transcript path (events land one turn at a time since 1.0).`

- [ ] **Step 6: Run the full suite and commit**

Run: `/usr/bin/python3 -m pytest -q`
Expected: `1249 passed, 1 skipped, 20 deselected` (baseline + 12).

```bash
git add dirtywork/transcript.py tests/test_transcript.py docs/transcript-schema.md docs/machine-contract.md
git commit -m "transcript: turn-scoped write buffer with atomic close (#60 spec §6.1)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: strict-template oracle (pure helper + its own tests)

**Files:**
- Modify: `tests/provider_doubles.py` (add one function; do **not** hook it into `DictProvider` yet — that is Task 5)
- Test: `tests/test_providers.py` (append)

**Interfaces:**
- Produces: `assert_strict_template_legal(history: list) -> None` — raises `AssertionError` with a readable message when the OpenAI-serialized history would be rejected by the Mistral template rule (spec §1.1, §7).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_providers.py`:

```python
from dirtywork.providers import ToolCall, assistant_message, tool_message
from .provider_doubles import assert_strict_template_legal


def _tc(cid, name="bash"):
    return ToolCall(id=cid, name=name, arguments={"command": "ls"}, error=None,
                    raw_arguments='{"command": "ls"}')


def _sys_user():
    return [{"role": "system", "content": "s"}, {"role": "user", "content": "task"}]


@pytest.mark.parametrize("tail", [
    # S1: tool -> user (verify feedback after finish)
    [assistant_message("", [_tc("abc123def", "finish")]), tool_message("abc123def", "run finished"),
     {"role": "user", "content": "VERIFY FAILED"}],
    # S7: assistant(text + tool_call) -> tool -> user
    [assistant_message("let me finish", [_tc("abc123def", "finish")]),
     tool_message("abc123def", "run finished"), {"role": "user", "content": "x"}],
    # S11: two tool results then user
    [assistant_message("", [_tc("a1"), _tc("b1")]), tool_message("a1", "x"), tool_message("b1", "y"),
     {"role": "user", "content": "nudge"}],
    # S16: tool_call -> tool -> empty assistant -> user (the F5 truncation shape)
    [assistant_message("", [_tc("b1")]), tool_message("b1", "ok"), assistant_message("", None),
     {"role": "user", "content": "cut off"}],
    # whitespace-only assistant reply followed by user (stricter than the template, by design)
    [assistant_message("   ", None), {"role": "user", "content": "x"}],
    # two consecutive user messages
    [{"role": "user", "content": "again"}],
    # empty assistant reply with no tool calls, even without a following user
    [assistant_message("", None)],
])
def test_oracle_rejects_illegal_shapes(tail):
    with pytest.raises(AssertionError):
        assert_strict_template_legal(_sys_user() + tail)


@pytest.mark.parametrize("tail", [
    # S4: feedback inside the tool result
    [assistant_message("", [_tc("abc123def", "finish")]),
     tool_message("abc123def", "run finished\n\nVERIFY FAILED ...")],
    # S17: tool -> assistant(placeholder) -> user
    [assistant_message("", [_tc("b1")]), tool_message("b1", "ok"),
     assistant_message("[empty reply]", None), {"role": "user", "content": "cut off"}],
    # S6: normal tool turn
    [assistant_message("", [_tc("b1")]), tool_message("b1", "ok")],
    # plain answer then user feedback
    [assistant_message("done", None), {"role": "user", "content": "VERIFY FAILED"}],
    # a longer legal run: tool turn, text turn, user, tool turn
    [assistant_message("", [_tc("b1")]), tool_message("b1", "ok"), assistant_message("hm", None),
     {"role": "user", "content": "go on"}, assistant_message("", [_tc("b2")]), tool_message("b2", "ok")],
])
def test_oracle_accepts_legal_shapes(tail):
    assert_strict_template_legal(_sys_user() + tail)


def test_oracle_skips_a_leading_system_message_only():
    assert_strict_template_legal([{"role": "user", "content": "no system prompt"}])
    with pytest.raises(AssertionError):
        # a history that STARTS with an assistant turn is illegal
        assert_strict_template_legal([{"role": "system", "content": "s"},
                                      assistant_message("hi", None)])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/usr/bin/python3 -m pytest -q tests/test_providers.py`
Expected: the module fails to collect (`ImportError: cannot import name 'assert_strict_template_legal'`), so every test in the file is reported as an error until Step 3.

- [ ] **Step 3: Implement the helper**

Append to `tests/provider_doubles.py`:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `/usr/bin/python3 -m pytest -q tests/test_providers.py`
Expected: all pass.

- [ ] **Step 5: Full suite and commit**

Run: `/usr/bin/python3 -m pytest -q`
Expected: `1262 passed` (…+13: 7 + 5 parametrized cases + 1), 1 skipped, 20 deselected.

```bash
git add tests/provider_doubles.py tests/test_providers.py
git commit -m "tests: strict-template oracle for runner histories (#60 spec §7)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `append_assistant()` and the empty-reply placeholder (R2)

**Files:**
- Modify: `dirtywork/runner.py` — constants near `NUDGES` (`:81`), the assistant-append block (`:696-741` at `b94dec9`)
- Modify: `docs/transcript-schema.md` (`assistant` table, `:60-64`)
- Test: `tests/test_runner.py` (update `test_empty_reply_is_nudged_not_completed` at `:763`; add tests), `tests/test_transcript_schema.py` (`ASSISTANT_FIELDS`)

**Interfaces:**
- Consumes: nothing new.
- Produces: `EMPTY_REPLY_PLACEHOLDER` (module constant, importable); inside `Runner.run()` the closure `append_assistant(text: str, tool_calls: list, finish_reason) -> None` — the only place that appends the model's turn to history and writes the `assistant` event; `assistant.placeholder` sparse transcript field.

Spec: §5, §6.2 (`placeholder`), §9.4/§9.5/§9.7, §9.13 (`:774`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_runner.py`, change the import block to also import `EMPTY_REPLY_PLACEHOLDER` from `dirtywork.runner`, and edit `test_empty_reply_is_nudged_not_completed` (`:763`): replace

```python
    assert second[-2] == {"role": "assistant", "content": ""}
    events = _events(tmp)
    nudges = [e for e in events if e["event"] == "nudge"]
    assert len(nudges) == 1
    assert nudges[0]["kind"] == "empty" and nudges[0]["turn"] == 1
```

with

```python
    assert second[-2] == {"role": "assistant", "content": EMPTY_REPLY_PLACEHOLDER}
    events = _events(tmp)
    nudges = [e for e in events if e["event"] == "nudge"]
    assert len(nudges) == 1
    assert nudges[0]["kind"] == "empty" and nudges[0]["turn"] == 1
    assistant = next(e for e in events if e["event"] == "assistant")
    assert assistant["text"] == "" and assistant["placeholder"] == EMPTY_REPLY_PLACEHOLDER
```

Then append these tests (after `test_malformed_tool_call_entry_recovers`, `:271`):

```python
def test_empty_reply_after_a_tool_turn_gets_the_placeholder(parts):
    # Spec §5 / probe S16: the F5 truncation shape. LM Studio drops an empty
    # assistant message, which leaves `tool -> user` and a 400 from Mistral.
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("c1", "read_file", {"path": "f.txt"})]),
        _resp(content="", finish_reason="length"),
        _resp(content="done"),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    third = provider.requests[2]
    assert third[-3]["role"] == "tool"
    assert third[-2] == {"role": "assistant", "content": EMPTY_REPLY_PLACEHOLDER}
    assert third[-1]["role"] == "user" and third[-1]["content"] == NUDGES["truncated"]
    events = _events(tmp)
    assistants = [e for e in events if e["event"] == "assistant"]
    assert "placeholder" not in assistants[0]                # a tool-call turn: never a placeholder
    assert assistants[1]["placeholder"] == EMPTY_REPLY_PLACEHOLDER and assistants[1]["text"] == ""
    assert "placeholder" not in assistants[2]


def test_think_only_and_text_tool_call_replies_get_no_placeholder(parts):
    wt, registry, sandbox, transcript, tmp = parts
    think = "<" + "think>hmm</" + "think>"
    provider = FakeProvider([_resp(content=think),
                             _resp(content="<tool_call>{}</tool_call>"),
                             _resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    r.run("s", "t")
    transcript.close()
    assistants = [e for e in _events(tmp) if e["event"] == "assistant"]
    assert all("placeholder" not in e for e in assistants)
    last = provider.requests[2]                              # [system, user(task), assistant, ...]
    assert last[2]["content"] == think                       # the model's own text is what is sent


def test_malformed_only_turn_gets_placeholder_and_a_user_nudge_without_touching_prior_turns(parts):
    # Spec §5 + §9.4: no addressable call and no text -> placeholder; the
    # malformed nudge is a user message; the previous turn's tool message is
    # byte-equal before and after, and its event never grows a follow_up.
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("c1", "read_file", {"path": "f.txt"})]),
        _resp(tool_calls=[_bad_entry()]),
        _resp(content="done"),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    second, third = provider.requests[1], provider.requests[2]
    prior_tool_before = next(m for m in second if m["role"] == "tool")
    prior_tool_after = next(m for m in third if m["role"] == "tool")
    assert prior_tool_before == prior_tool_after
    assert third[-2] == {"role": "assistant", "content": EMPTY_REPLY_PLACEHOLDER}
    assert third[-1]["role"] == "user" and "were malformed" in third[-1]["content"]
    events = _events(tmp)
    prior_event = next(e for e in events if e["event"] == "tool_result" and e["tool"] == "read_file")
    assert "follow_up" not in prior_event
    assert [e["event"] for e in events if e["event"] == "nudge"] == []   # (Task 5 adds malformed_entry)


def test_malformed_only_turn_with_text_gets_no_placeholder(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="I'll call a tool", tool_calls=[_bad_entry()]),
                             _resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    r.run("s", "t")
    transcript.close()
    second = provider.requests[1]
    assert second[-2] == {"role": "assistant", "content": "I'll call a tool"}
    assert second[-1]["role"] == "user"
    assert "placeholder" not in next(e for e in _events(tmp) if e["event"] == "assistant")


def test_malformed_only_length_turn_gets_placeholder_and_no_truncated_nudge(parts):
    # The turn takes the tool path (resp.tool_calls is non-empty) -> malformed
    # nudge only, never `truncated`.
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="", tool_calls=[_bad_entry()], finish_reason="length"),
                             _resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    r.run("s", "t")
    transcript.close()
    second = provider.requests[1]
    assert second[-2] == {"role": "assistant", "content": EMPTY_REPLY_PLACEHOLDER}
    assert second[-1]["role"] == "user" and "were malformed" in second[-1]["content"]
    assert NUDGES["truncated"] not in second[-1]["content"]


def test_third_malformed_entry_strike_ends_after_recording_the_placeholder(parts):
    wt, registry, sandbox, transcript, tmp = parts
    bad = _resp(tool_calls=[_bad_entry()])
    provider = FakeProvider([bad, bad, bad])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"
    events = _events(tmp)
    assistants = [e for e in events if e["event"] == "assistant"]
    assert len(assistants) == 3 and all(e["placeholder"] == EMPTY_REPLY_PLACEHOLDER for e in assistants)
    assert events[-1]["event"] == "run_end"
    assert events[-2]["event"] == "tool_result"          # the strike itself; no nudge, no user message after it
```

And in `tests/test_transcript_schema.py:24` change `ASSISTANT_FIELDS = ["text", "tool_calls", "finish_reason"]` to `ASSISTANT_FIELDS = ["text", "tool_calls", "finish_reason", "placeholder"]` (the existing `test_doc_documents_every_assistant_field` at `:184` already loops over it — add nothing else).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/usr/bin/python3 -m pytest -q tests/test_runner.py tests/test_transcript_schema.py`
Expected: ImportError on `EMPTY_REPLY_PLACEHOLDER`; after adding only the constant, the six new tests and the edited one FAIL on `{"role": "assistant", "content": ""}` / missing `placeholder`.

- [ ] **Step 3: Implement**

In `dirtywork/runner.py`, directly after the `NUDGES = {...}` dict (`:81-90`) add:

```python
# Spec #60 §5 (R2): an assistant history entry with no addressable tool call
# and no non-whitespace text is stored with this content, so no chat template
# or server preprocessing (LM Studio drops empty assistant messages) can delete
# it and pull the following user message up against a tool result. The
# transcript keeps the model's real text ("") and records the substitution in
# `assistant.placeholder`. One constant: the wording can change without a
# schema change.
EMPTY_REPLY_PLACEHOLDER = "[empty reply]"
```

Inside `Runner.run()`, after the `note_last_tool_result` closure (`:542-551`) add:

```python
        def append_assistant(text, tool_calls, finish_reason) -> None:
            """Spec #60 §5: the ONE place the model's turn enters history and
            the transcript. `tool_calls` are the addressable calls (id-bearing);
            with none of them and no non-whitespace text the history entry
            carries EMPTY_REPLY_PLACEHOLDER (R2) and the event says so."""
            nonlocal last_assistant_text
            transcript_text = text
            if isinstance(transcript_text, str) and len(transcript_text) > MAX_ASSISTANT_TEXT_CHARS:
                transcript_text = (
                    transcript_text[:MAX_ASSISTANT_TEXT_CHARS]
                    + f"\n[truncated at {MAX_ASSISTANT_TEXT_CHARS} chars in the transcript "
                      f"only — the full text was sent to the model]"
                )
            has_text = isinstance(text, str) and bool(text.strip())
            fields = {}
            if not tool_calls and not has_text:
                fields["placeholder"] = EMPTY_REPLY_PLACEHOLDER
            self.transcript.write(
                "assistant", text=transcript_text,
                tool_calls=[{"name": tc.name, "arguments": (tc.raw_arguments or "")[:2000]}
                            for tc in tool_calls],
                # Spec §1.5: an OPEN enum. Adapters do not guarantee a
                # string (Anthropic passes an unknown stop reason through
                # raw), so anything else is recorded as null rather than
                # emitted as some other JSON type.
                finish_reason=finish_reason if isinstance(finish_reason, str) else None,
                **fields)
            if has_text:
                last_assistant_text = transcript_text[:LAST_TEXT_CHARS]
            messages.append(assistant_message(fields.get("placeholder", text), tool_calls))
```

Then in the loop body (`:696-741`): delete the block from `transcript_text = resp.text` through the `if isinstance(transcript_text, str) and transcript_text.strip(): last_assistant_text = ...` lines, and put `append_assistant(resp.text, tool_calls, finish_reason)` in its place (right after `tool_calls = [tc for tc in resp.tool_calls if tc.id]`). Then delete the three `messages.append(assistant_message(...))` lines: the one under `if resp.tool_calls:` (leave the `if resp.tool_calls:` branch with just a `pass`-free structure — see below), the one under `if kind == "answer":`, and the one before `self.transcript.write("nudge", kind=kind, turn=turns)`. The resulting block reads:

```python
                tool_calls = [tc for tc in resp.tool_calls if tc.id]
                append_assistant(resp.text, tool_calls, finish_reason)
                if not resp.tool_calls:
                    content = resp.text
                    kind = classify_text_reply(content, finish_reason)
                    if kind == "answer":
                        ended, feedback = check_verify(content)
                        if ended is not None:
                            return ended
                        messages.append({"role": "user", "content": feedback})
                        continue
                    self.transcript.write("nudge", kind=kind, turn=turns)
                    abort_reason = failures.record("empty_reply")
                    if abort_reason is not None:
                        return finish("model_error", abort_reason)
                    stalled, stall_text = check_progress()
                    if stalled is not None:
                        return stalled
                    messages.append({"role": "user", "content": _join_nudges(NUDGES[kind], stall_text)})
                    continue
```

(`if resp.tool_calls: ... else:` becomes `if not resp.tool_calls:` with the former else-body; the former if-body's only statement was the append.)

- [ ] **Step 4: Document `placeholder`**

In `docs/transcript-schema.md`, in the `assistant` table after the `finish_reason` row add:

```
| `placeholder` | | ✓ | string | 1.0 (#60): **sparse** — present only when the reply had no addressable tool call and no non-whitespace text. The history entry sent to the model on later turns carries this value (currently `[empty reply]`) instead of the empty text, so a strict chat template cannot drop the turn; `text` stays the model's real reply (`""`) |
```

- [ ] **Step 5: Run the runner and schema tests, then the full suite**

Run: `/usr/bin/python3 -m pytest -q tests/test_runner.py tests/test_transcript_schema.py`
Expected: all pass (including `test_a_real_run_emits_the_documented_events`, whose empty-reply turn now emits `placeholder`).
Run: `/usr/bin/python3 -m pytest -q`
Expected: `1268 passed` (…+6), 1 skipped, 20 deselected.

- [ ] **Step 6: Commit**

```bash
git add dirtywork/runner.py docs/transcript-schema.md tests/test_runner.py tests/test_transcript_schema.py
git commit -m "runner: append_assistant() with the [empty reply] placeholder (#60 spec §5, R2)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: turn wiring, `resolve_finish()` and the honest `finish` result

**Files:**
- Modify: `dirtywork/runner.py` — constants, `Runner.run()` closures, the terminal-tool branch, the loop wrapper
- Modify: `dirtywork/builtin_tools.py:256` (`FINISH_SPEC` caps)
- Modify: `docs/transcript-schema.md` (`finish` paragraph `:77-80`, `tool_result.result` row `:74`, `verify` table)
- Test: `tests/test_runner.py` (update `:1321` and `:1589`; add tests), `tests/test_builtin_tools.py` (append)

**Interfaces:**
- Consumes: `Transcript.turn()`/`flush()` (Task 1), `append_assistant` (Task 3).
- Produces: constants `FINISH_DONE`, `FINISH_PROVISIONAL`; closures `resolve_finish(text: str) -> None`, `one_turn() -> RunResult | None`; per-turn lists `turn_tool_msgs: list[tuple[dict, dict]]` (history message, buffered `tool_result` record — every addressable call, in order) and `turn_terminal` (the subset that were terminal calls); `run_verify() -> tuple[str | None, dict]`; `check_verify(final: str, via: str) -> tuple[RunResult | None, str | None]`; `verify.via` transcript field. Task 5 consumes `turn_tool_msgs`.

Spec: §4 (all), §6.1 (*(v2)* "`finish()` flushes before the export", *(v3)* "Resolution must precede the flush"), §6.2 (`verify.via`, `finish` result values), §9.1, §9.3, §9.8, §9.9, §9.13 (`:1321`, `:1589`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_runner.py` import `FINISH_DONE, FINISH_PROVISIONAL, VERIFY_FEEDBACK` from `dirtywork.runner` alongside the others. Edit `test_verify_failure_with_a_round_left_feeds_back_and_retries` (`:1321`): replace

```python
    # the failed round was fed back as a user message naming the command
    feedback = [m for m in provider.requests[-1] if m["role"] == "user"]
    assert any("VERIFY FAILED (round 1 of 2)" in m["content"] for m in feedback)
    assert any("test -e fixed" in m["content"] for m in feedback)
```

with

```python
    # Spec #60 §4: the failed round IS the finish call's result -- no user message
    last = provider.requests[-1]
    f1 = next(m for m in last if m["role"] == "tool" and m["tool_call_id"] == "f1")
    assert f1["content"].startswith("VERIFY FAILED (round 1 of 2)")
    assert "test -e fixed" in f1["content"]
    assert not any(m["role"] == "user" and "VERIFY FAILED" in m["content"] for m in last)
    # the last request was captured BEFORE f2 (called in that very reply) existed at all
    assert not any(m["role"] == "tool" and m["tool_call_id"] == "f2" for m in last)
    events = _events(tmp)
    finish_events = [e for e in events if e["event"] == "tool_result" and e["tool"] == "finish"]
    assert finish_events[0]["result"] == f1["content"]
    assert finish_events[1]["result"] == FINISH_DONE
    verify_events = [e for e in events if e["event"] == "verify"]
    assert verify_events[0]["via"] == "finish_result" and "via" not in verify_events[1]
    # the transcript shows the resolved finish result BEFORE its verify event
    assert events.index(finish_events[0]) < events.index(verify_events[0])
```

Edit `test_verify_feedback_carries_the_timeout_nudge_from_the_same_turn` (`:1589`): replace the last block

```python
    # the next user message carries BOTH texts, merged into one message
    second_request = provider.requests[1]
    assert second_request[-1]["role"] == "user"
    content = second_request[-1]["content"]
    assert "VERIFY FAILED (round 1 of 2)" in content
    assert "A command timed out and did not finish" in content
```

with

```python
    # Spec #60 §4: the feedback is the finish result; the timeout nudge is (Task 5)
    # the follow_up on the turn's last tool result, which here is finish itself.
    second_request = provider.requests[1]
    f1 = next(m for m in second_request if m["role"] == "tool" and m["tool_call_id"] == "f1")
    assert f1["content"].startswith("VERIFY FAILED (round 1 of 2)")
    assert "A command timed out and did not finish" in second_request[-1]["content"]
```

Append new tests at the end of the verify section (after `test_a_verify_timeout_is_not_counted`):

```python
def _finish_results(events):
    return [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "finish"]


def test_finish_result_is_the_full_verify_feedback_even_past_the_preview_cap(parts):
    # Spec #60 §4 "Transcript cap": FINISH_SPEC is transcript="full", so a
    # 3000-char verify tail is recorded byte-for-byte.
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "first"})]),
        _resp(content="ok"),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m",
               verify="python3 -c \"print('x' * 3000)\"; exit 1", verify_rounds=1)
    r.run("s", "t")
    transcript.close()
    f1 = next(m for m in provider.requests[1] if m["role"] == "tool")
    assert len(f1["content"]) > 3000
    assert _finish_results(_events(tmp)) == [f1["content"]]
    assert provider.requests[1][-1]["role"] == "tool"          # no user message follows


def test_verify_rounds_zero_leaves_an_honest_finish_result(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "done"})])])
    r = Runner(provider, registry, sandbox, transcript, model="m",
               verify="echo boom; exit 3", verify_rounds=0)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "verify_failed"
    events = _events(tmp)
    assert _finish_results(events) == ["run not finished: verify failed (exit 3); no fix rounds remain"]
    assert "via" not in next(e for e in events if e["event"] == "verify")


def test_last_round_failure_leaves_an_honest_finish_result(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "a"})]),
                             _resp(tool_calls=[_call("f2", "finish", {"summary": "b"})])])
    r = Runner(provider, registry, sandbox, transcript, model="m", verify="exit 2", verify_rounds=1)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "verify_failed"
    results = _finish_results(_events(tmp))
    assert results[0].startswith("VERIFY FAILED (round 1 of 2)")
    assert results[1] == "run not finished: verify failed (exit 2); no fix rounds remain"


def test_verify_that_cannot_run_leaves_an_honest_finish_result(parts):
    from dirtywork.budget import BudgetExceeded
    from dirtywork.sandbox import SandboxError          # the same import runner.py uses

    class Raising:
        def __init__(self, exc):
            self.exc = exc

        def bash(self, command, timeout=120):
            raise self.exc

    for exc, status, reason in ((BudgetExceeded("worktree exceeds 2048 MB"), "budget_exceeded",
                                 "worktree exceeds 2048 MB"),
                                (SandboxError("container gone"), "sandbox_error", "container gone")):
        wt, registry, sandbox, transcript, tmp = parts
        transcript_i = Transcript(tmp / f"t-{status}.jsonl")
        provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "done"})])])
        r = Runner(provider, registry, Raising(exc), transcript_i, model="m", verify="true")
        result = r.run("s", "t")
        transcript_i.close()
        assert result.status == status
        events = [json.loads(l) for l in (tmp / f"t-{status}.jsonl").read_text().splitlines()]
        assert _finish_results(events) == [f"run not finished: verify could not run ({reason})"]


def test_terminal_exits_before_verify_never_leave_run_finished(parts):
    # Spec #60 §4 row 5 + §9.9: a later call ends the run before check_verify;
    # finish() resolves the still-provisional record from the status.
    from dirtywork.budget import BudgetExceeded

    class BudgetBustingSandbox:
        def write_file(self, path, content):
            raise BudgetExceeded("worktree exceeds 2048 MB")

    class InterruptingSandbox:
        def bash(self, command, timeout=120):
            raise KeyboardInterrupt

    cases = [
        ([_call("f1", "finish", {"summary": "s"}), _call("w1", "write_file", {"path": "x", "content": "y"})],
         BudgetBustingSandbox(), "budget_exceeded"),
        ([_call("f1", "finish", {"summary": "s"}), _bash_call("b1")],
         InterruptingSandbox(), "interrupted"),
        ([_call("f1", "finish", {"summary": "s"})] + [_call(f"u{i}", "no_such_tool", {}) for i in range(3)],
         None, "model_error"),
    ]
    for calls, box, status in cases:
        wt, registry, sandbox, transcript, tmp = parts
        transcript_i = Transcript(tmp / f"t-{status}.jsonl")
        provider = FakeProvider([_resp(tool_calls=calls)])
        r = Runner(provider, registry, box or sandbox, transcript_i, model="m")
        result = r.run("s", "t")
        transcript_i.close()
        assert result.status == status
        events = [json.loads(l) for l in (tmp / f"t-{status}.jsonl").read_text().splitlines()]
        assert _finish_results(events) == [f"run not finished: {status}"]
        assert not any(e.get("result") == FINISH_DONE for e in events)
        assert events[-1]["event"] == "run_end" and events[-1]["status"] == status


def test_interrupt_inside_verify_resolves_the_finish_result_before_the_flush(parts):
    # Spec #60 §4 (v3): KeyboardInterrupt is caught INSIDE the turn block.
    wt, registry, sandbox, transcript, tmp = parts

    class InterruptingVerify:
        def bash(self, command, timeout=120):
            raise KeyboardInterrupt

    provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "s"})])])
    r = Runner(provider, registry, InterruptingVerify(), transcript, model="m", verify="npm test")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "interrupted"
    events = _events(tmp)
    assert _finish_results(events) == ["run not finished: interrupted"]
    assert events[-1]["event"] == "run_end"


def test_multiple_finish_calls_in_one_turn_resolve_to_the_same_string(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "a"}), _call("f2", "finish", {"summary": "b"})]),
        _resp(content="ok"),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m", verify="exit 1", verify_rounds=1)
    result = r.run("s", "t")
    transcript.close()
    assert result.final_message == "ok"
    second = provider.requests[1]
    tools = [m for m in second if m["role"] == "tool"]
    assert len(tools) == 2 and tools[0]["content"] == tools[1]["content"]
    assert tools[0]["content"].startswith("VERIFY FAILED (round 1 of 2)")
    assert _finish_results(_events(tmp)) == [tools[0]["content"], tools[1]["content"]]
    # (Task 5 adds the [finish, bash(timeout), finish] variant: follow_up on f2 only)


def test_a_malformed_finish_is_not_terminal_and_is_never_rewritten(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(tool_calls=[_bad_args(call_id="f1", name="finish")]),
                             _resp(tool_calls=[_call("f2", "finish", {"summary": "ok"})])])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    results = _finish_results(_events(tmp))
    assert results[0].startswith("ERROR:") and results[1] == FINISH_DONE


def test_finish_flushes_the_turn_before_finalize(parts):
    # Spec #60 §6.1 (v2): the turn's evidence is on disk before the export runs.
    wt, registry, sandbox, transcript, tmp = parts
    seen = {}

    def finalize():
        seen["events"] = [e["event"] for e in _events(tmp)]
        return {}

    provider = FakeProvider([_resp(tool_calls=[_call("c1", "read_file", {"path": "f.txt"}),
                                               _call("f1", "finish", {"summary": "s"})])])
    r = Runner(provider, registry, sandbox, transcript, model="m", finalize=finalize)
    r.run("s", "t")
    transcript.close()
    assert seen["events"] == ["run_start", "assistant", "tool_result", "tool_result"]
    assert _events(tmp)[-1]["event"] == "run_end"
```

Append to `tests/test_builtin_tools.py`:

```python
def test_finish_result_reaches_the_transcript_in_full():
    from dirtywork.builtin_tools import default_registry
    registry = default_registry()
    long = "x" * 5000
    assert registry.transcript_preview("finish", long) == long
    assert len(registry.transcript_preview("bash", long)) == 2000
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/usr/bin/python3 -m pytest -q tests/test_runner.py tests/test_builtin_tools.py`
Expected: ImportError on `FINISH_DONE`; after adding the constants only, the new and edited tests FAIL (`"run finished"` where feedback/terminal strings are expected; `verify` event has no `via`; preview capped at 2000).

- [ ] **Step 3: `FINISH_SPEC` → full transcript**

In `dirtywork/builtin_tools.py:256` change `caps=Caps(fs="none", max_output_chars=TOOL_OUTPUT_CAP, transcript="preview"),` to:

```python
    # Spec #60 §4: the finish result is harness-authored and bounded (the verify
    # feedback carries a VERIFY_OUTPUT_CHARS tail), so the transcript keeps it
    # byte-for-byte instead of the 2000-char preview every model/tool-authored
    # result gets.
    caps=Caps(fs="none", max_output_chars=TOOL_OUTPUT_CAP, transcript="full"),
```

- [ ] **Step 4: Constants and closures in `runner.py`**

After `FINISH_TOOL = "finish"` (`:27`) add:

```python
# Spec #60 §4. `finish` is executed by verifying, so its result is resolved
# when the turn's verify outcome is known. Until then the history message and
# the buffered tool_result record hold FINISH_PROVISIONAL -- which reaches disk
# only when an exception the runner does not handle leaves the turn (true, and
# the only case it is written). FINISH_DONE is the only value on a run that
# actually finished; every other exit resolves to "run not finished: <why>".
FINISH_DONE = "run finished"
FINISH_PROVISIONAL = "run not finished: verify did not run"
```

Inside `Runner.run()`, after `deadline = start + self.timeout` (`:540`) add:

```python
        # Spec #60 §3/§4: the current turn's tool messages, in call order, each
        # paired with its buffered transcript record so a follow-up or the
        # finish resolution rewrites BOTH (transcript == wire). Cleared at the
        # start of every turn; a carrier is only ever chosen from here.
        turn_tool_msgs = []     # [(history message, tool_result record)]
        turn_terminal = []      # the subset that were terminal (finish) calls

        def resolve_finish(text: str) -> None:
            """Spec #60 §4: rewrite EVERY terminal record and history message of
            the current turn to `text`."""
            for msg, record in turn_terminal:
                msg["content"] = text
                record["result"] = text
```

Modify the `finish()` closure (`:554`): make its first statements

```python
        def finish(status: str, final: str) -> RunResult:
            # Spec #60 §4(c): the single exit point resolves any terminal record
            # the verify path never reached (a later call raised, the failure
            # tracker aborted, Ctrl-C) -- then flushes the turn so its evidence
            # is on disk BEFORE finalize() (docker export) runs (§6.1).
            if any(record.get("result") == FINISH_PROVISIONAL for _, record in turn_terminal):
                resolve_finish(FINISH_DONE if status == "completed"
                               else f"run not finished: {status}")
            self.transcript.flush()
            # This evidence rides on EVERY result (null when there is none), so
            ...
```

(the rest of `finish()` unchanged).

Replace `run_verify` and `check_verify` (`:595-647`) with:

```python
        def run_verify():
            """One execution of the operator's gate (spec §4.2). Runs through
            the same sandbox.bash the tool uses — same guardrails, same budget
            watchdog, same reaper, same environment the worker's bash had — and
            happens BEFORE finalize(), so in docker mode the container is still
            alive and nothing has been exported yet. Returns (feedback text for
            another round or None when the run may end now, the buffered
            `verify` record) -- verify_state says whether it passed."""
            nonlocal verify_state, verify_rounds_used
            verify_rounds_used += 1
            result = self.sandbox.bash(self.verify, self.verify_timeout)
            exit_code = parse_exit_code(result)
            passed = exit_code == 0
            tail = result[-VERIFY_OUTPUT_CHARS:] if isinstance(result, str) else ""
            verify_state = {"command": self.verify, "exit_code": exit_code,
                            "output_tail": tail, "rounds": verify_rounds_used,
                            "passed": passed}
            record = self.transcript.write("verify", round=verify_rounds_used,
                                           exit_code=exit_code, passed=passed)
            if passed or verify_rounds_used > self.verify_rounds:
                return None, record
            return VERIFY_FEEDBACK.format(round=verify_rounds_used,
                                          rounds=self.verify_rounds + 1,
                                          command=self.verify,
                                          exit_code=exit_code, output=tail), record

        def check_verify(final: str, via: str):
            """(RunResult to return, or None; feedback to deliver, or None) for a
            completion path. Both completion paths — the finish tool and a plain
            answer — go through this one function, so they can never disagree
            about what verifying means. `via` names the carrier the caller will
            use for feedback ("finish_result" / "user") and is stamped on the
            verify event only when feedback is delivered (spec #60 §6.2). Every
            branch resolves the turn's terminal records (§4) -- a no-op on the
            plain-answer path, which has none. BudgetExceeded/SandboxError end
            the run with the same statuses a tool call would."""
            nonlocal stuck
            if not self.verify:
                resolve_finish(FINISH_DONE)
                return finish("completed", final), None
            try:
                feedback, record = run_verify()
            except BudgetExceeded as e:
                resolve_finish(f"run not finished: verify could not run ({e.reason})")
                return finish("budget_exceeded", e.reason), None
            except SandboxError as e:
                resolve_finish(f"run not finished: verify could not run ({e})")
                return finish("sandbox_error", str(e)), None
            if feedback is not None:
                # A feedback round is a fresh episode: the worker is retrying
                # against new instructions, so whatever bash streak was latched
                # (possibly in THIS SAME turn, before it called finish) must not
                # end the next turn as "stuck" for a check that no longer
                # reflects what the worker is doing.
                stuck = None
                repeats.reset()
                if record is not None:
                    record["via"] = via
                resolve_finish(feedback)
                return None, feedback
            if verify_state["passed"]:
                resolve_finish(FINISH_DONE)
                return finish("completed", final), None
            code = verify_state["exit_code"]
            resolve_finish("run not finished: verify failed "
                           f"(exit {code if code is not None else 'unknown'}); no fix rounds remain")
            return finish("verify_failed", final), None
```

- [ ] **Step 5: The terminal branch and the per-turn lists**

In the tool loop, change the terminal branch (`:779-782`) to:

```python
                            if spec is not None and spec.terminal:
                                summary = args.get("summary")
                                pending_finish = summary if isinstance(summary, str) else ""
                                result = FINISH_PROVISIONAL
                                terminal = True
```

and add `terminal = False` right after `abort_reason = None` at the top of the `for tc in tool_calls:` body (`:758`). Then replace the write/append pair (`:809-815`):

```python
                    self.transcript.write("tool_result", tool=name,
                                          args=raw_args[:500],
                                          result=self.registry.transcript_preview(name, result),
                                          **timed_out_fields)
                    if name != FINISH_TOOL:
                        note_last_tool_result(name, raw_args, result)
                    messages.append(tool_message(tc.id, result))
```

with

```python
                    record = self.transcript.write("tool_result", tool=name,
                                                   args=raw_args[:500],
                                                   result=self.registry.transcript_preview(name, result),
                                                   **timed_out_fields)
                    if name != FINISH_TOOL:
                        note_last_tool_result(name, raw_args, result)
                    msg = tool_message(tc.id, result)
                    messages.append(msg)
                    turn_tool_msgs.append((msg, record))
                    if terminal:
                        turn_terminal.append((msg, record))
```

Update the two `check_verify` call sites: the plain-answer path (Task 3's block) becomes `ended, feedback = check_verify(content, via="user")`; the finish path (`:831`) becomes `ended, feedback = check_verify(pending_finish, via="finish_result")`, and that path no longer appends the feedback — replace

```python
                    if timed_out_this_turn:
                        self.transcript.write("nudge", kind="timeout", turn=turns)
                        feedback = _join_nudges(feedback, timeout_text)
                    messages.append({"role": "user", "content": feedback})
                    continue
```

with

```python
                    # The feedback is already the finish result (resolve_finish);
                    # only the timeout nudge still needs delivering.
                    if timed_out_this_turn:
                        self.transcript.write("nudge", kind="timeout", turn=turns)
                        messages.append({"role": "user", "content": timeout_text})
                    continue
```

(Task 5 replaces that `messages.append` with `deliver`.)

- [ ] **Step 6: Wrap the loop body in `one_turn()` inside `transcript.turn()`**

Today (`:649-865`) the body sits directly under `try: while True:`. Turn it into a closure:

1. Insert, immediately before `try:` at `:649`:

```python
        def one_turn():
            """One model turn (spec #60 §6.1: runs inside transcript.turn()).
            Returns the RunResult that ends the run, or None to continue."""
            nonlocal turns, trimmed_turns, timeouts, stuck
            turn_tool_msgs.clear()
            turn_terminal.clear()
```

2. Move every line that was under `while True:` (from `if turns >= self.max_turns:` through the final `messages.append({"role": "user", "content": nudge_text})`) into `one_turn()`, dedented by one level (they were 4 deeper under `while`; now 4 deeper under `def`) — a pure re-indent of the block, no other change, **except**: replace each bare `continue` (there are three: after the plain-answer feedback append, after the text-reply nudge append, after the finish-path timeout append) with `return None`. Every `return finish(...)` / `return stalled` / `return ended` stays as it is.

3. Replace the old loop with:

```python
        try:
            while True:
                with self.transcript.turn():
                    try:
                        ended = one_turn()
                    except KeyboardInterrupt:
                        # Spec #60 §4 (v3): caught INSIDE the turn so finish()
                        # resolves the finish record and flushes before the
                        # turn's buffer is written.
                        ended = finish("interrupted", "")
                if ended is not None:
                    return ended
        except KeyboardInterrupt:
            # Between turns only: nothing is buffered and no finish record can
            # be pending here.
            return finish("interrupted", "")
```

Check with `grep -n "^\s*continue$" dirtywork/runner.py` that no `continue` remains inside `one_turn()`, and `/usr/bin/python3 -m pyflakes dirtywork/runner.py` (if pyflakes is available; otherwise `python3 -m py_compile`) is clean.

- [ ] **Step 7: Documentation rows**

In `docs/transcript-schema.md`:
- `tool_result` table `:74`, change `All built-in tools declare `preview`, which caps the record at 2000 chars;` to `All built-in tools but `finish` declare `preview`, which caps the record at 2000 chars (`finish` declares `full` since 1.0 — its result is harness-authored and bounded);`.
- Replace the `finish` paragraph (`:77-80`) with:

```
A `finish(summary=…)` call is an ordinary tool call: it appears in the
`assistant` event's `tool_calls` and produces a `tool_result`. Since 1.0 (#60)
its `result` is honest about what happened: `run finished` only when the run
ends `completed`; the full `VERIFY FAILED (round r of R) …` feedback text when
`--verify` failed and a fix round remains (the run continues — that text is what
the model receives as the call's result); `run not finished: verify failed (exit
N); no fix rounds remain` (run ends `verify_failed`); `run not finished: verify
could not run (<reason>)` (`budget_exceeded`/`sandbox_error` raised by the verify
command); `run not finished: <status>` when the turn ended before verify ran (a
later call in the same turn raised, three consecutive failures, or an
interrupt); and `run not finished: verify did not run` only if an unhandled
exception left the turn. The summary becomes the run's `final_message`.
```

- `verify` table: add the row `| `via` | | ✓ | string | 1.0 (#60): **sparse** — present only when feedback for another round was delivered: `finish_result` (the feedback became the `finish` call's result) or `user` (the worker answered in prose, so the feedback is the next user message) |`.

- [ ] **Step 8: Run the suite**

Run: `/usr/bin/python3 -m pytest -q tests/test_runner.py tests/test_builtin_tools.py tests/test_transcript_schema.py`
Expected: pass. Then `/usr/bin/python3 -m pytest -q` → `1278 passed` (…+10: 9 runner + 1 builtin_tools), 1 skipped, 20 deselected.

- [ ] **Step 9: Commit**

```bash
git add dirtywork/runner.py dirtywork/builtin_tools.py docs/transcript-schema.md tests/test_runner.py tests/test_builtin_tools.py
git commit -m "runner: one_turn() inside transcript.turn(); finish result resolved on every exit (#60 spec §4, §6.1)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `deliver()` — carriers, `follow_up`, `nudge.via`, `malformed_entry`, oracle hook

**Files:**
- Modify: `dirtywork/runner.py` (`check_progress`, the four delivery sites, the malformed-nudge write), `dirtywork/bench.py:48`
- Modify: `tests/provider_doubles.py` (`DictProvider.chat`), `tests/test_runner.py` (`FakeProvider.chat`; update `:929`, `:1053`, `:1518`, `:1540`, `:1589`; new tests), `tests/test_transcript_schema.py:20`, `tests/test_bench.py:486`, `tests/test_provider_anthropic.py`, `tests/test_provider_openai.py`, `tests/test_provider_ollama.py`
- Modify: `docs/transcript-schema.md` (`tool_result.follow_up` row; `nudge` section `:82-93`)

**Interfaces:**
- Consumes: `turn_tool_msgs` (Task 4), `assert_strict_template_legal` (Task 2).
- Produces: closure `deliver(text: str | None, nudge_records: list) -> None`; `check_progress() -> tuple[RunResult | None, str | None, dict | None]`; transcript fields `tool_result.follow_up`, `nudge.via`; nudge kind `malformed_entry`; `bench.NUDGE_KINDS` with six kinds; scenario builders `_scenario_*()` in `tests/test_runner.py`.

Spec: §3 (all, incl. the *(v3)* four callers), §6.2 (`follow_up`, `nudge.via`, `malformed_entry`), §6.3 (`last_tool_result` unchanged), §7 (oracle in both doubles), §9.2, §9.6, §9.10, §9.12, §9.13.

- [ ] **Step 1: Hook the oracle into both doubles (these assertions are what fails first)**

In `tests/test_runner.py` `FakeProvider.chat` (`:76-80`) add the check before recording:

```python
    def chat(self, model, history, tools, *, temperature=None, max_tokens=4096, timeout=None):
        # Spec #60 §7: every request the runner makes is legal for strict templates.
        assert_strict_template_legal(history)
        # Deep-copy the history the way the old FakeProvider did, so later
        # mutation (trim_messages) cannot rewrite what a test already saw.
        self.requests.append([dict(m) for m in history])
        self.timeouts.append(timeout)
        return self.responses.pop(0)
```

with `from .provider_doubles import assert_strict_template_legal` added to the imports. In `tests/provider_doubles.py` `DictProvider.chat`:

```python
    def chat(self, model, history, tools, *, temperature=None, max_tokens=4096, timeout=None):
        assert_strict_template_legal(history)      # spec #60 §7
        self.calls += 1
        return parse_chat_response(self.reply(model, history, tools))
```

Run: `/usr/bin/python3 -m pytest -q tests/test_runner.py tests/test_main.py tests/test_transcript_schema.py`
Expected: the tests whose scenarios produce `tool -> user` FAIL with the oracle's "user message directly after a tool result" message (at least `test_runner_stalled_status_after_idle_turns`, `test_one_timeout_nudge_per_turn_even_with_two_timeouts`, `test_timeout_nudge_merges_with_the_stall_nudge`, `test_verify_feedback_carries_the_timeout_nudge_from_the_same_turn`; `test_malformed_entries_on_stall_nudge_turn_send_one_merged_user_message` stays green — its nudge turn is malformed-only, so the carrier is already a `user` after the placeholder). Note the list; every one must pass by Step 6.

- [ ] **Step 2: Write the new and updated runner tests**

Update `test_runner_stalled_status_after_idle_turns` (`:929`): replace

```python
    fourth = provider.requests[3]
    assert fourth[-1]["role"] == "user" and fourth[-1]["content"] == STALL_NUDGE.format(n=2)
    nudges = [e for e in _events(tmp) if e["event"] == "nudge"]
    assert len(nudges) == 1 and nudges[0]["kind"] == "stall" and nudges[0]["turn"] == 3
```

with

```python
    # Spec #60 §3: on a tool turn the nudge rides on the turn's last tool result
    fourth = provider.requests[3]
    assert fourth[-1]["role"] == "tool"
    assert fourth[-1]["content"].endswith("\n\n" + STALL_NUDGE.format(n=2))
    events = _events(tmp)
    nudges = [e for e in events if e["event"] == "nudge"]
    assert len(nudges) == 1 and nudges[0]["kind"] == "stall" and nudges[0]["turn"] == 3
    assert nudges[0]["via"] == "tool_result"
    carrier = [e for e in events if e["event"] == "tool_result"][2]     # turn 3's read_file
    assert carrier["follow_up"] == STALL_NUDGE.format(n=2)
    assert fourth[-1]["content"] == carrier["result"] + "\n\n" + carrier["follow_up"]
```

Update `test_malformed_entries_on_stall_nudge_turn_send_one_merged_user_message` (`:1053`): after the existing assertions add

```python
    kinds = [(e["kind"], e["via"]) for e in _events(tmp) if e["event"] == "nudge"]
    assert kinds == [("stall", "user"), ("malformed_entry", "user")]
```

Update Task 3's `test_malformed_only_turn_gets_placeholder_and_a_user_nudge_without_touching_prior_turns`: replace its last line (`assert [e["event"] for e in events if e["event"] == "nudge"] == []   # (Task 5 adds malformed_entry)`) with

```python
    assert [(e["kind"], e["via"]) for e in events if e["event"] == "nudge"] == [("malformed_entry", "user")]
```

Update `test_one_timeout_nudge_per_turn_even_with_two_timeouts` (`:1518`): replace from `# the nudge text reached the model as the next user message` to the end with

```python
    # Spec #60 §3: the nudge rides on the turn's LAST tool result (b2), not b1
    second_request = provider.requests[1]
    assert second_request[-1]["role"] == "tool" and second_request[-1]["tool_call_id"] == "b2"
    # both calls got the default 120 s timeout, so both results are the same text;
    # only the LAST one carries the nudge
    assert second_request[-1]["content"] == second_request[-2]["content"] + "\n\n" + TIMEOUT_NUDGE
    events = [e for e in _events(tmp) if e["event"] == "tool_result"]
    assert "follow_up" not in events[0] and events[1]["follow_up"] == TIMEOUT_NUDGE
    assert nudges[0]["via"] == "tool_result"
```

Import `TIMEOUT_NUDGE` from `dirtywork.runner`.

Update `test_timeout_nudge_merges_with_the_stall_nudge` (`:1540`): replace `text = provider.requests[1][-1]["content"]` with

```python
    last = provider.requests[1][-1]
    assert last["role"] == "tool"
    carrier = [e for e in _events(tmp) if e["event"] == "tool_result"][-1]
    text = carrier["follow_up"]
    assert last["content"] == carrier["result"] + "\n\n" + text
```

Update `test_verify_feedback_carries_the_timeout_nudge_from_the_same_turn` (`:1589`, edited in Task 4): replace `assert "A command timed out and did not finish" in second_request[-1]["content"]` with

```python
    assert second_request[-1]["role"] == "tool"                     # no user message follows
    assert f1["content"].endswith("\n\n" + TIMEOUT_NUDGE)            # finish is the turn's last call
    events = _events(tmp)
    finish_event = next(e for e in events if e["event"] == "tool_result" and e["tool"] == "finish")
    assert finish_event["result"].startswith("VERIFY FAILED (round 1 of 2)")
    assert finish_event["follow_up"] == TIMEOUT_NUDGE
    assert nudges[0]["via"] == "tool_result"
```

Append the shared scenario builders and new tests (end of file):

```python
# ---- Spec #60 shared scenarios: (provider, sandbox, runner kwargs). Test 12 iterates them.

def _scenario_verify_feedback_on_finish():
    provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "first"})]),
                             _resp(content="ok")])
    return provider, None, {"verify": "exit 1", "verify_rounds": 1}


def _scenario_finish_first_then_timeout():
    provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "s"}), _bash_call("b1")]),
                             _resp(content="ok")])
    return provider, _TimeoutThenFailingVerifySandbox("npm test"), {"verify": "npm test", "verify_rounds": 1}


def _scenario_malformed_only_turn():
    provider = FakeProvider([_resp(tool_calls=[_call("c1", "read_file", {"path": "f.txt"})]),
                             _resp(tool_calls=[_bad_entry()]),
                             _resp(content="done")])
    return provider, None, {}


def _scenario_empty_reply_after_tool_turn():
    provider = FakeProvider([_resp(tool_calls=[_call("c1", "read_file", {"path": "f.txt"})]),
                             _resp(content="", finish_reason="length"),
                             _resp(content="done")])
    return provider, None, {}


SCENARIOS = [_scenario_verify_feedback_on_finish, _scenario_finish_first_then_timeout,
             _scenario_malformed_only_turn, _scenario_empty_reply_after_tool_turn]


def _run_scenario(parts, build):
    wt, registry, sandbox, transcript, tmp = parts
    provider, box, kwargs = build()
    r = Runner(provider, registry, box or sandbox, transcript, model="m", **kwargs)
    result = r.run("s", "t")
    transcript.close()
    return provider, result, _events(tmp)


def _tool_events(events):
    return [e for e in events if e["event"] == "tool_result"]


def test_mixed_turn_finish_first_then_timeout(parts):
    # Spec §3 example table row 3: feedback on finish, TIMEOUT_NUDGE on bash.
    provider, result, events = _run_scenario(parts, _scenario_finish_first_then_timeout)
    second = provider.requests[1]
    tools = [m for m in second if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tools] == ["f1", "b1"]           # wire order == call order
    assert tools[0]["content"].startswith("VERIFY FAILED (round 1 of 2)")
    assert TIMEOUT_NUDGE not in tools[0]["content"]
    assert tools[1]["content"].endswith("\n\n" + TIMEOUT_NUDGE)
    f1, b1 = _tool_events(events)
    assert "follow_up" not in f1 and b1["follow_up"] == TIMEOUT_NUDGE
    # the verify command fails on the plain-answer round too and no round is left
    assert result.status == "verify_failed"


def test_two_finish_calls_around_a_timeout_put_the_follow_up_on_the_last_call_only(parts):
    # Spec §9.3: both terminal results resolve to the same string; the follow_up
    # attaches only to the turn's last addressable call, which is f2 here.
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "a"}), _bash_call("b1"),
                          _call("f2", "finish", {"summary": "b"})]),
        _resp(content="ok"),
    ])
    r = Runner(provider, registry, _TimeoutThenFailingVerifySandbox("npm test"), transcript,
               model="m", verify="npm test", verify_rounds=1)
    r.run("s", "t")
    transcript.close()
    f1, b1, f2 = _tool_events(_events(tmp))
    assert f1["result"] == f2["result"] and f1["result"].startswith("VERIFY FAILED")
    assert "follow_up" not in f1 and "follow_up" not in b1 and f2["follow_up"] == TIMEOUT_NUDGE
    tools = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert tools[2]["content"] == f2["result"] + "\n\n" + TIMEOUT_NUDGE


def test_mixed_turn_timeout_finish_timeout_carrier_is_the_last_call(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_bash_call("b1"), _call("f1", "finish", {"summary": "s"}), _bash_call("b2")]),
        _resp(content="ok"),
    ])
    r = Runner(provider, registry, _TimeoutThenFailingVerifySandbox("npm test"), transcript,
               model="m", verify="npm test", verify_rounds=1)
    r.run("s", "t")
    transcript.close()
    tools = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tools] == ["b1", "f1", "b2"]
    assert tools[1]["content"].startswith("VERIFY FAILED") and TIMEOUT_NUDGE not in tools[1]["content"]
    assert tools[2]["content"].endswith("\n\n" + TIMEOUT_NUDGE) and TIMEOUT_NUDGE not in tools[0]["content"]
    b1, f1, b2 = _tool_events(_events(tmp))
    assert "follow_up" not in b1 and "follow_up" not in f1 and b2["follow_up"] == TIMEOUT_NUDGE
    assert [e["kind"] for e in _events(tmp) if e["event"] == "nudge"] == ["timeout"]


class _TimeoutThenPassingVerifySandbox(_TimeoutThenFailingVerifySandbox):
    """Worker bash calls time out; the --verify command passes."""

    def bash(self, command, timeout=120):
        if command == self.verify_command:
            return "exit code: 0\n"
        return super().bash(command, timeout)


def test_mixed_turn_finish_first_then_timeout_with_passing_verify_ends_clean(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "s"}), _bash_call("b1")])])
    r = Runner(provider, registry, _TimeoutThenPassingVerifySandbox("npm test"), transcript,
               model="m", verify="npm test")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    events = _events(tmp)
    assert all("follow_up" not in e for e in _tool_events(events))
    assert _tool_events(events)[0]["result"] == FINISH_DONE
    assert [e for e in events if e["event"] == "nudge"] == []


def test_stall_and_malformed_nudges_share_one_follow_up_on_a_tool_turn(parts):
    # stall_turns=2 -> stall nudge at idle 1 (turn 2); that turn also carries a
    # malformed entry alongside an addressable read_file -> one follow_up
    # holding both texts, in the documented order (malformed, timeout, stall).
    wt, registry, sandbox, transcript, tmp = parts
    idle = _resp(tool_calls=[_call("c", "read_file", {"path": "f.txt"})])
    mixed = _resp(tool_calls=[_bad_entry(), _call("c2", "read_file", {"path": "f.txt"})])
    provider = FakeProvider([idle, mixed, _resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m", stall_turns=2)
    r.run("s", "t")
    transcript.close()
    events = _events(tmp)
    carrier = [e for e in _tool_events(events) if e["tool"] == "read_file"][-1]
    assert carrier["follow_up"].startswith("1 of your tool calls were malformed")
    assert carrier["follow_up"].endswith(STALL_NUDGE.format(n=1))
    assert "\n\n" in carrier["follow_up"]
    nudges = [(e["kind"], e["via"]) for e in events if e["event"] == "nudge"]
    assert sorted(nudges) == [("malformed_entry", "tool_result"), ("stall", "tool_result")]
    third = provider.requests[2]
    assert third[-1]["role"] == "tool" and third[-1]["content"] == carrier["result"] + "\n\n" + carrier["follow_up"]


def test_last_tool_result_excludes_the_follow_up(parts):
    provider, result, events = _run_scenario(parts, _scenario_finish_first_then_timeout)
    last = result.extra["last_tool_result"]
    assert last["tool"] == "bash" and TIMEOUT_NUDGE not in last["result"]


def test_transcript_equals_wire_for_every_tool_and_assistant_message(parts):
    # Spec §9.10. Results kept under the preview cap except the finish result,
    # which is recorded in full (a 3000-char verify tail).
    wt, registry, sandbox, transcript, tmp = parts

    class Box(_TimeoutThenFailingVerifySandbox):
        def bash(self, command, timeout=120):
            if command == self.verify_command:
                return "exit code: 1\n" + "y" * 3000
            return super().bash(command, timeout)

        def read_file(self, path, offset=0, limit=400):   # turn 1 reads f.txt; keep it under the preview cap
            return "data\n"

    provider = FakeProvider([
        _resp(tool_calls=[_bash_call("b1"), _call("c1", "read_file", {"path": "f.txt"})]),
        _resp(content="", finish_reason="length"),
        _resp(tool_calls=[_call("f1", "finish", {"summary": "s"})]),
        _resp(content="ok"),
    ])
    r = Runner(provider, registry, Box("npm test"), transcript, model="m",
               verify="npm test", verify_rounds=1, stall_turns=0)
    r.run("s", "t")
    transcript.close()
    events = _events(tmp)
    final = provider.requests[-1]
    wire_tools = [m for m in final if m["role"] == "tool"]
    for msg, ev in zip(wire_tools, _tool_events(events)):
        expected = ev["result"] + ("\n\n" + ev["follow_up"] if "follow_up" in ev else "")
        assert msg["content"] == expected, (msg, ev)
    wire_assistants = [m for m in final if m["role"] == "assistant"]
    for msg, ev in zip(wire_assistants, [e for e in events if e["event"] == "assistant"]):
        assert msg["content"] == ev.get("placeholder", ev["text"])
    assert len(wire_tools) == 3 and len(wire_assistants) == 3
    assert len(next(e for e in _tool_events(events) if e["tool"] == "finish")["result"]) > 3000


@pytest.mark.parametrize("build", SCENARIOS)
def test_scenarios_are_legal_for_every_provider(parts, build):
    # Spec §9.12: the last request of each scenario through both serializers.
    from dirtywork.providers.anthropic import _to_anthropic_messages
    from dirtywork.providers.openai_compat import _to_openai_messages
    from .provider_doubles import assert_strict_template_legal
    from .test_provider_anthropic import _assert_alternating
    provider, _result, _events_ = _run_scenario(parts, build)
    for history in provider.requests:
        assert_strict_template_legal(history)                       # OpenAI and (same serializer) Ollama
        assert _to_openai_messages(history)                          # serializes without error
        _system, messages = _to_anthropic_messages(history)
        _assert_alternating(messages)


def test_anthropic_serializes_a_mixed_turn_with_tool_result_blocks_in_call_order(parts):
    from dirtywork.providers.anthropic import _to_anthropic_messages
    provider, _r, _e = _run_scenario(parts, _scenario_finish_first_then_timeout)
    _system, messages = _to_anthropic_messages(provider.requests[1])
    last_user = [m for m in messages if m["role"] == "user"][-1]
    assert [b["tool_use_id"] for b in last_user["content"] if b.get("type") == "tool_result"] == ["f1", "b1"]
```

Update `tests/test_transcript_schema.py:20` to `NUDGE_KINDS = ["truncated", "empty", "text_tool_call", "stall", "timeout", "malformed_entry"]` and `tests/test_bench.py:486` `"2/1/0/0/0"` → `"2/1/0/0/0/0"` (comment: `# 1.0 added the malformed_entry kind`).

Add to `tests/test_provider_openai.py` and `tests/test_provider_ollama.py` (each, at the end):

```python
def test_runner_shaped_history_is_legal_for_strict_templates():
    from dirtywork.providers import ToolCall, assistant_message, tool_message
    from .provider_doubles import assert_strict_template_legal
    tc = ToolCall(id="abc123def", name="finish", arguments={"summary": "s"}, error=None,
                  raw_arguments='{"summary": "s"}')
    history = [{"role": "system", "content": "s"}, {"role": "user", "content": "task"},
               assistant_message("", [tc]),
               tool_message("abc123def", "VERIFY FAILED (round 1 of 2) ...\n\n" + "timeout nudge")]
    assert_strict_template_legal(history)
```

- [ ] **Step 3: Implement `deliver()` and `check_progress`**

In `Runner.run()`, after `resolve_finish` add:

```python
        def deliver(text, nudge_records) -> None:
            """Spec #60 §3 (R1): the ONE place harness text enters history. On a
            turn with addressable tool calls it rides on the LAST tool result of
            this turn (wire = result + "\\n\\n" + text; the transcript record
            gets `follow_up`); otherwise it is the next user message, legal
            because the preceding assistant entry is counted and non-empty (R2).
            Every nudge record handed in is stamped with the carrier (`via`)."""
            if not text:
                return
            if turn_tool_msgs:
                msg, record = turn_tool_msgs[-1]
                msg["content"] = f"{msg['content']}\n\n{text}"
                record["follow_up"] = text
                via = "tool_result"
            else:
                messages.append({"role": "user", "content": text})
                via = "user"
            for record in nudge_records:
                if record is not None:
                    record["via"] = via
```

Replace `check_progress` (`:581-593`) with:

```python
        def check_progress():
            """(RunResult to end the run with, or None; stall-nudge text, or
            None; the buffered stall-nudge record, or None). The caller hands
            the text and record to deliver(), which picks the carrier (spec #60
            §3) -- history never carries a harness message after a tool result
            nor two consecutive user messages."""
            verdict = progress.end_turn()
            if verdict == "stalled":
                return finish("stalled", f"no progress in {self.stall_turns} consecutive turns"), None, None
            if verdict == "nudge":
                record = self.transcript.write("nudge", kind="stall", turn=turns)
                return None, STALL_NUDGE.format(n=self.stall_turns // 2), record
            return None, None, None
```

Then the four delivery sites inside `one_turn()`:

1. Plain-answer feedback: `messages.append({"role": "user", "content": feedback})` → `deliver(feedback, [])`.
2. Text-reply nudges:

```python
                    kind_record = self.transcript.write("nudge", kind=kind, turn=turns)
                    abort_reason = failures.record("empty_reply")
                    if abort_reason is not None:
                        return finish("model_error", abort_reason)
                    stalled, stall_text, stall_record = check_progress()
                    if stalled is not None:
                        return stalled
                    deliver(_join_nudges(NUDGES[kind], stall_text), [kind_record, stall_record])
                    return None
```

3. Finish-path timeout:

```python
                    if timed_out_this_turn:
                        timeout_record = self.transcript.write("nudge", kind="timeout", turn=turns)
                        deliver(timeout_text, [timeout_record])
                    return None
```

4. Ordinary path (`:851-863`):

```python
                stalled, stall_text, stall_record = check_progress()
                if stalled is not None:
                    return stalled

                malformed_text = malformed_record = None
                if malformed_count > 0:
                    malformed_text = (f"{malformed_count} of your tool calls were malformed "
                                      "(unaddressable: no usable id/name) and were "
                                      "discarded. Re-issue them as valid tool calls.")
                    # Spec #60 §6.2: delivered since 0.5, transcribed since 1.0.
                    malformed_record = self.transcript.write("nudge", kind="malformed_entry", turn=turns)
                timeout_record = None
                if timed_out_this_turn:
                    # Once per turn, however many commands timed out in it.
                    timeout_record = self.transcript.write("nudge", kind="timeout", turn=turns)
                deliver(_join_nudges(malformed_text, timeout_text, stall_text),
                        [malformed_record, timeout_record, stall_record])
                return None
```

`dirtywork/bench.py:48`: `NUDGE_KINDS = ("stall", "empty", "truncated", "text_tool_call", "timeout", "malformed_entry")`, and the legend at `dirtywork/bench.py:775` becomes `print("nudges: " + "/".join(NUDGE_KINDS))` so it can never drift again; update `tests/test_bench.py:793` to assert `"nudges: stall/empty/truncated/text_tool_call/timeout/malformed_entry" in out`.

- [ ] **Step 4: Documentation rows**

`docs/transcript-schema.md`, `tool_result` table: add after `timed_out`:

```
| `follow_up` | | ✓ | string | 1.0 (#60): **sparse** — present when this result carried harness text on the wire. The exact text appended after the tool's own result, uncapped (harness-authored and bounded): the model received `result + "\n\n" + follow_up` as this call's result. Only the **last** tool result of a turn can carry one; it merges every nudge of that turn in the order `malformed_entry`, `timeout`, `stall` (or `timeout` alone on a verify-feedback turn) |
```

Replace the `nudge` section (`:82-93`) with:

```
### `nudge`

**v2 only.** One per corrective text the harness injected on a turn. Where the
text lands (since 1.0, #60): on a turn that made at least one addressable tool
call it is appended to that turn's **last** tool result (`via: "tool_result"`;
the exact text is in that `tool_result` event's `follow_up`); on a text-only
turn it is the next `user` message (`via: "user"`). Several nudges on one turn
are merged into a single follow-up (in the order `malformed_entry`, `timeout`,
`stall`; `truncated`/`empty`/`text_tool_call` then `stall` on a text turn), but
each is recorded here separately. The history never carries a `user` message
directly after a tool result, nor two consecutive `user` messages — the shapes
strict chat templates (Mistral/Devstral) reject.

| Field | v1 | v2 | Type | Notes |
|---|---|---|---|---|
| `kind` | | ✓ | string | `truncated` (the reply hit the token limit), `empty` (no tool call and no answer), `text_tool_call` (a tool call written as prose instead of through the tools API), `stall` (no progress for `--stall-turns // 2` turns), `timeout` (0.9: at least one `bash` command timed out on this turn — exactly one per turn however many timed out, and only on a turn that continues; a timeout is not a `FailureTracker` event), `malformed_entry` (1.0: N tool-call entries had no usable id/name and were discarded — delivered since 0.5, recorded since 1.0) |
| `turn` | | ✓ | integer | 1-based turn number the nudge was issued on |
| `via` | | ✓ | string | 1.0: `tool_result` or `user` — the carrier this nudge rode on (see above) |
```

- [ ] **Step 5: Run the suite**

Run: `/usr/bin/python3 -m pytest -q tests/test_runner.py tests/test_main.py tests/test_transcript_schema.py tests/test_bench.py tests/test_provider_openai.py tests/test_provider_ollama.py tests/test_soak_tools.py`
Expected: pass — including every test from the Step 1 failure list. Then `/usr/bin/python3 -m pytest -q` → `1292 passed` (…+14: 8 plain + 4 parametrized scenario cases + 1 anthropic-order + 1 openai + 1 ollama, minus nothing), 1 skipped, 20 deselected. If your count differs by one or two, recount the tests you added rather than hunting for phantom ones — "all green and the count rose by what you added" is the gate.

- [ ] **Step 6: Commit**

```bash
git add dirtywork/runner.py dirtywork/bench.py docs/transcript-schema.md tests/
git commit -m "runner: deliver() carriers, follow_up/via, malformed_entry nudge; strict-template oracle in every runner double (#60 spec §3, §6.2, §7)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: `runs show` renders `follow_up`, `placeholder`, `[not finished]`

**Files:**
- Modify: `dirtywork/runs.py` (`_tool_result_outcome` `:266-282`, `_timeline_line` `:285-306`, `MD_RESULT_CHARS` comment `:319`, `_md_event_lines` `:354-375`, `_md_timeline` `:395-430`)
- Test: `tests/test_runs.py` (append)
- Modify: `docs/transcript-schema.md:263-273`, `docs/operating.md:179-183`

**Interfaces:**
- Consumes: transcript fields from Tasks 3-5.
- Produces: `_tool_result_outcome(result_text, tool=None) -> str` (new optional parameter; returns `"not finished"` for a `finish` result other than `run finished`).

Spec: §6.3 (with *(v3)* finish exempt from `_md_trim`, *(v4)* fenced rendering), §9.14.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runs.py`:

```python
ADVERSARIAL_FOLLOW_UP = "```\n> quoted\n# heading\n</details>\r\nlast line"


def _followup_run(tmp_path):
    # _write_run creates the run dir itself (tests/test_runs.py:33-37)
    run_dir = _write_run(tmp_path / "runs", "fu1", {
        "slug": "fu1", "task": "t", "status": "verify_failed", "repo": "/r", "worktree": "/w",
        "model": "m", "provider": "openai", "turns": 2, "started": "2026-08-23T00:00:00+00:00",
        "sandbox": "none",
    })
    events = [
        {"ts": "t1", "event": "run_start", "model": "m", "task": "t"},
        {"ts": "t2", "event": "assistant", "text": "", "tool_calls": [{"name": "bash", "arguments": "{}"}]},
        {"ts": "t3", "event": "tool_result", "tool": "bash", "args": "{}", "result": "exit code: 0",
         "follow_up": ADVERSARIAL_FOLLOW_UP},
        {"ts": "t4", "event": "nudge", "kind": "timeout", "turn": 1, "via": "tool_result"},
        {"ts": "t5", "event": "assistant", "text": "", "placeholder": "[empty reply]"},
        {"ts": "t6", "event": "nudge", "kind": "empty", "turn": 2, "via": "user"},
        {"ts": "t7", "event": "assistant", "text": "", "tool_calls": [{"name": "finish", "arguments": "{}"}]},
        {"ts": "t8", "event": "tool_result", "tool": "finish", "args": "{\"summary\": \"s\"}",
         "result": "run not finished: verify failed (exit 1); no fix rounds remain"},
        {"ts": "t9", "event": "tool_result", "tool": "finish", "args": "{}", "result": "x" * 2500},
        {"ts": "t10", "event": "run_end", "status": "verify_failed", "turns": 3},
    ]
    (run_dir / "transcript.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return run_dir


def test_cmd_show_timeline_marks_follow_up_placeholder_and_not_finished(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _followup_run(tmp_path)
    assert runs.cmd_show(argparse.Namespace(slug="fu1", diff=False)) == 0
    out = capsys.readouterr().out
    bash_line = next(l for l in out.splitlines() if "tool_result" in l and "bash" in l)
    assert bash_line.rstrip().endswith("[ok] +follow_up")
    assert ADVERSARIAL_FOLLOW_UP.splitlines()[1] not in out          # the text itself is not dumped in the timeline
    assert "(sent as: [empty reply])" in out
    assert "[not finished]" in out
    assert out.count("[not finished]") == 2


def test_cmd_show_markdown_fences_follow_up_and_shows_finish_in_full(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _followup_run(tmp_path)
    assert runs.cmd_show(argparse.Namespace(slug="fu1", diff=False, markdown=True, out=None)) == 0
    out = capsys.readouterr().out
    # spec §6.3 (v4): a callout line, then the text verbatim inside a fence
    # longer than any backtick run it contains -- never a raw blockquote.
    assert "> **harness → model:**\n\n````\n" + ADVERSARIAL_FOLLOW_UP + "\n````\n" in out
    assert "\n# heading\n" not in out.replace("````\n" + ADVERSARIAL_FOLLOW_UP + "\n````", "")
    assert "> > quoted" not in out and "> # heading" not in out
    assert "_[fence auto-closed by the exporter]_" not in out
    assert "_(sent as: [empty reply])_" in out
    assert "[not finished]" in out
    assert "x" * 2500 in out                                           # finish exempt from the 2000 trim
    assert "... [truncated]" not in out


def test_runs_show_tolerates_old_transcripts_without_the_new_keys(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _markdown_run(tmp_path)
    assert runs.cmd_show(argparse.Namespace(slug="md1", diff=False, markdown=True, out=None)) == 0
    out = capsys.readouterr().out
    assert "harness → model" not in out and "sent as" not in out and "[not finished]" not in out
```

- [ ] **Step 2: Run to verify they fail**

Run: `/usr/bin/python3 -m pytest -q tests/test_runs.py -k "follow_up or not_finished or tolerates"`
Expected: the first two FAIL (no `+follow_up`, no `[not finished]`, no fence); the third passes already.

- [ ] **Step 3: Implement**

`_tool_result_outcome`:

```python
def _tool_result_outcome(result_text, tool=None) -> str:
    """'timed out' / ERROR / BLOCKED / 'not finished' / ok, from the tool
    result's leading token ... (keep the existing docstring) ... Since 1.0 a
    `finish` result other than `run finished` is `not finished` (#60 §4)."""
    text = str(result_text or "")
    if tool == "finish" and text != "run finished":
        return "not finished"
    if is_timeout_result(text):
        return "timed out"
    if text.startswith("ERROR"):
        return "ERROR"
    if text.startswith("BLOCKED"):
        return "BLOCKED"
    return "ok"
```

`_timeline_line`: pass `tool` into the outcome and add the suffixes:

```python
    if name == "tool_result":
        result = str(event.get("result", ""))
        tool = event.get("tool") or "(malformed call)"
        outcome = _tool_result_outcome(result, event.get("tool"))
        suffix = " +follow_up" if "follow_up" in event else ""
        return (f"{ts}  {name:<15} {tool:<12} {str(event.get('args', ''))[:80]:<80} "
                f"[{outcome}]{suffix}")
    if name == "assistant":
        tools = ",".join(str(tc.get("name")) for tc in (event.get("tool_calls") or [])
                         if isinstance(tc, dict))
        sent_as = f" (sent as: {event['placeholder']})" if event.get("placeholder") else ""
        return f"{ts}  {name:<15} " + (f"tools: {tools}" if tools else "text reply") + sent_as
```

`MD_RESULT_CHARS` comment: `MD_RESULT_CHARS = 2000   # the transcript's preview cap for model/tool-authored results; finish is exempt (#60)`.

`_md_event_lines` tool_result branch:

```python
    if name == "tool_result":
        tool = event.get("tool") or "(malformed call)"
        result = str(event.get("result", ""))
        outcome = _tool_result_outcome(result, event.get("tool"))
        summary = (f"{html.escape(str(tool), quote=False)}"
                   f"({_md_inline(event.get('args', ''), MD_ARGS_CHARS)}) [{outcome}]")
        lines = ["<details>", f"<summary>{summary}</summary>", ""]
        # Spec #60 §4/§6.3: the finish result is harness-authored, bounded and
        # recorded in full; trimming it here would reproduce the truncation the
        # transcript no longer has.
        lines += _md_block(result if event.get("tool") == "finish" else _md_trim(result, MD_RESULT_CHARS))
        lines += ["</details>", ""]
        if "follow_up" in event:
            # Spec #60 §6.3 (v4): verify output and the operator's command can
            # hold fences, `>`, headings or HTML -- a fence longer than any
            # backtick run inside is the only rendering that survives them.
            lines += ["> **harness → model:**", ""]
            lines += _md_block(str(event["follow_up"]))
        return lines
```

`_md_timeline` assistant branch, after the `tool_calls`/`_text reply_` lines and before `continue`:

```python
            if event.get("placeholder"):
                lines += [f"_(sent as: {_md_inline(event['placeholder'], MD_ARGS_CHARS)})_", ""]
```

- [ ] **Step 4: Docs**

`docs/transcript-schema.md:263-273`: change `its `tool_result`s as `<details>` blocks (capped at the same 2000-char preview the transcript itself applies) and its `nudge`/`guardrail_block`/`sandbox_reset` events as blockquote callouts.` to `its `tool_result`s as `<details>` blocks (capped at the transcript's 2000-char preview; `finish` results are shown in full), a `> **harness → model:**` callout with the fenced `follow_up` text under a result that carried one, `_(sent as: [empty reply])_` under an assistant turn stored with a placeholder, `[not finished]` on a `finish` that did not finish the run, and its `nudge`/`guardrail_block`/`sandbox_reset` events as blockquote callouts.`

`docs/operating.md:179-183`: after `blockquote callouts for nudges/guardrail blocks/sandbox resets,` insert `the harness text a tool result carried to the model (1.0: a fenced "harness → model" block), `(sent as: [empty reply])` on a turn the harness had to pad,`.

- [ ] **Step 5: Run and commit**

Run: `/usr/bin/python3 -m pytest -q tests/test_runs.py` then the full suite → `1295 passed` (…+3).

```bash
git add dirtywork/runs.py tests/test_runs.py docs/transcript-schema.md docs/operating.md
git commit -m "runs show: render follow_up (fenced), placeholder, [not finished]; finish shown in full (#60 spec §6.3)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: contract prose, schema coverage scenario, shared sandbox double

**Files:**
- Modify: `docs/operating.md:74-89`, `:100-106`; `docs/machine-contract.md:90-96`, `:332-345`, `:356`; `docs/transcript-schema.md` (new **Wire shape** subsection before `### run_end`)
- Modify: `tests/provider_doubles.py` (add `TimeoutThenFailingVerifySandbox`), `tests/test_runner.py` (import it instead of the local class), `tests/test_transcript_schema.py` (second scenario)

**Interfaces:**
- Produces: `tests.provider_doubles.TimeoutThenFailingVerifySandbox(verify_command)` (the class from `tests/test_runner.py:1574`, moved verbatim, public name).

Spec: §6.4, §8, §9.15.

- [ ] **Step 1: Move the sandbox double**

Cut the `_TimeoutThenFailingVerifySandbox` class from `tests/test_runner.py:1574-1586` and paste it into `tests/provider_doubles.py` (end of file) renamed `TimeoutThenFailingVerifySandbox`, docstring prefixed with `Shared with test_transcript_schema (spec #60 §9.15).`. In `tests/test_runner.py` add `from .provider_doubles import TimeoutThenFailingVerifySandbox as _TimeoutThenFailingVerifySandbox` to the imports so every existing use keeps its name.

Run: `/usr/bin/python3 -m pytest -q tests/test_runner.py` → pass.

- [ ] **Step 2: Write the failing schema-coverage test**

Append to `tests/test_transcript_schema.py`:

```python
class _FollowUpProvider(DictProvider):
    """Turn 1: a wire body whose tool_calls entry has no id (-> malformed_entry)
    beside a bash call that times out; turn 2: finish into a failing verify with
    one round left; turn 3: a plain answer (verify fails again; run ends
    verify_failed). Emits follow_up, nudge.via, verify.via, malformed_entry."""

    def reply(self, model, history, tools):
        if self.calls == 1:
            body = tool_call_body("bash", {"command": "sleep 999"}, call_id="b1")
            body["choices"][0]["message"]["tool_calls"].insert(
                0, {"type": "function", "function": {"name": "bash", "arguments": "{}"}})
            return body
        if self.calls == 2:
            return tool_call_body("finish", {"summary": "s"}, call_id="f1")
        return text_body("done")


def test_a_verify_run_emits_the_documented_follow_up_fields(tmp_path):
    from dirtywork.sandbox.host import HostSandbox   # noqa: F401  (kept for parity with the sibling test)
    from .provider_doubles import TimeoutThenFailingVerifySandbox
    transcript = Transcript(tmp_path / "t.jsonl")
    registry = default_registry(transcript=transcript)
    r = Runner(_FollowUpProvider(), registry, TimeoutThenFailingVerifySandbox("npm test"), transcript,
               model="m", verify="npm test", verify_rounds=1)
    result = r.run("s", "t")
    transcript.close()
    events = [json.loads(l) for l in (tmp_path / "t.jsonl").read_text().splitlines()]
    assert result.status == "verify_failed"
    tool_events = [e for e in events if e["event"] == "tool_result"]
    assert any("follow_up" in e for e in tool_events)
    kinds = {(e["kind"], e["via"]) for e in events if e["event"] == "nudge"}
    assert {("malformed_entry", "tool_result"), ("timeout", "tool_result")} <= kinds
    verify_events = [e for e in events if e["event"] == "verify"]
    assert verify_events[0]["via"] == "finish_result" and "via" not in verify_events[1]
    finish = next(e for e in tool_events if e["tool"] == "finish")
    assert finish["result"].startswith("VERIFY FAILED")
    documented = _doc_tokens()
    for e in events:
        for key in e:
            if key in ("ts", "event"):
                continue
            assert key in documented, f"{e['event']}.{key} is not documented in {DOC.name}"
    text = DOC.read_text(encoding="utf-8")
    for phrase in ("run not finished", "Wire shape", "malformed_entry", "finish_result"):
        assert phrase in text
```

Run: `/usr/bin/python3 -m pytest -q tests/test_transcript_schema.py -k follow_up`
Expected: FAIL on `"Wire shape" in text` (fields themselves are documented by Tasks 3-5).

- [ ] **Step 3: Docs — Wire shape subsection**

In `docs/transcript-schema.md`, before `### `run_end`` insert:

```
### Wire shape (1.0, #60)

Two rules the runner enforces on the history it sends every provider:

- **R1 — a harness follow-up never directly follows a tool result.** On a turn
  with at least one addressable tool call, verify feedback becomes the
  `finish` call's own `result` and every nudge is appended to that turn's
  last tool result (`follow_up`); on a turn with none, the follow-up is the
  next `user` message.
- **R2 — an assistant history entry is never droppable.** A reply with no
  addressable tool call and no non-whitespace text is stored as
  `[empty reply]` (`assistant.placeholder`).

Mistral-family templates count only `user` messages and tool-call-free
assistant messages and require them to alternate; a `user` after a `tool`,
or after an assistant message the server dropped as empty, is an HTTP 400.

**Reconstructing what the model was sent.** A tool message is
`result + "\n\n" + follow_up` (`result` alone without a `follow_up`); an
assistant message is `placeholder` when present, else `text`. Three limits:
a non-`finish` `result` is the 2000-char preview (exact only under the cap;
`follow_up` and `finish` results are always exact); on later turns
`trim_messages` replaces the oldest tool results in *history* with
`[result trimmed — re-run the tool if needed]` (their `follow_up` included)
and the transcript records only `run_end.trimmed_turns`, not which results —
so the transcript is exact for what the model saw *when the result was
produced*, not for what a later request re-sent; user-carried nudges are
recorded by `kind` and `via` only (the stall count and the malformed count are
not transcribed), and plain-answer verify feedback is summarized by `verify`
and `run_end.verify` (last round's tail).
```

- [ ] **Step 4: Docs — operating.md and machine-contract.md**

`docs/operating.md:81-84`: replace `the default hands the first failure back to the worker as a message naming the command, the exit code and the output tail, and lets it try once more` with `the default hands the first failure back to the worker — as the `finish` call's own result when it finished through `finish(summary=…)`, or as the next message when it answered in prose — naming the command, the exit code and the output tail, and lets it try once more`.

`docs/operating.md:104-106`: replace `The same turn also gets a one-line nudge telling the worker in words that the result is unknown and must not be reported as a pass,` with `The same turn also carries a one-line nudge — appended to the turn's last tool result, so no chat template sees a user message after a tool result (1.0, #60) — telling the worker in words that the result is unknown and must not be reported as a pass,`.

`docs/machine-contract.md:90-96` (`--verify` bullet): append the sentence `Feedback for a fix round is delivered as the `finish` call's tool result (or as the next user message after a prose answer); the `verify` event records which (`via`).`

`docs/machine-contract.md:339-340`: change `` `nudge` (`{"event": "nudge", "kind": "truncated|empty|text_tool_call|stall", "turn": N}`) `` to `` `nudge` (`{"event": "nudge", "kind": "truncated|empty|text_tool_call|stall|timeout|malformed_entry", "turn": N, "via": "tool_result|user"}` — since 1.0 a nudge on a tool-call turn rides on the turn's last `tool_result` (its `follow_up` field) and never as a user message after a tool result; the history never carries two consecutive user messages) ``.

`docs/machine-contract.md:356`: replace `followed by a `tool_result` event whose `result` is `run finished`;` with `followed by a `tool_result` event whose `result` is `run finished` only when the run ends `completed` — otherwise the verify feedback text (a fix round follows) or a `run not finished: …` reason (see `transcript-schema.md`);`. Then append, as its own paragraph right after that sentence's paragraph: `Since 1.0 the history sent to the model obeys two rules (#60): a harness follow-up never directly follows a tool result — it rides on the turn's last `tool_result` as `follow_up`, or is the `finish` result — and an assistant reply with no tool call and no text is stored as `[empty reply]` (`assistant.placeholder`), so strict chat templates never see a dropped turn or a user message after a tool result.`

- [ ] **Step 5: Run and commit**

Run: `/usr/bin/python3 -m pytest -q tests/test_transcript_schema.py tests/test_runner.py` then the full suite → `1296 passed` (…+1). (Task 8 adds four `live`-marked cases, so the default run then reports `24 deselected`.)
Also run `grep -rn "next user message\|as a user message\|flushed per line\|flushed immediately" docs/*.md dirtywork/*.py` and confirm every hit is either historical (`docs/2026-08-14-*.md`), already rewritten, or one of the two correct descriptions of the plain-answer carrier this plan itself adds (the `verify.via` row in `transcript-schema.md` and `deliver()`'s docstring in `runner.py`).

```bash
git add docs/ tests/provider_doubles.py tests/test_runner.py tests/test_transcript_schema.py
git commit -m "docs: wire-shape contract, finish result values, nudge carriers (#60 spec §6.4, §8); schema coverage scenario

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: live replay against Devstral and the soak reruns (§7 live, §10)

**Files:**
- Test: `tests/test_live.py` (append; `live` marker)
- Create: scratchpad `plans/issue60-rerun.jsonl`, staged `f3-run` repo
- Modify: `docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md` (append a `## #60 reruns` section)

**Interfaces:** consumes the shared scenarios of Task 5.

Preconditions: LM Studio serving `mistralai/devstral-small-2-2512` and `qwen/qwen3-coder-next` (`curl -s http://localhost:1234/v1/models`); Docker running; `dirtywork` importable from this checkout.

- [ ] **Step 1: Write the live test**

Append to `tests/test_live.py` (look at the file's existing `live` fixtures/skips and reuse its base-URL/model discovery helpers; if it has none, use the inline discovery below):

```python
from .test_runner import SCENARIOS, _run_scenario, parts  # noqa: F401  (fixture re-exported)


@pytest.mark.live
@pytest.mark.parametrize("build", SCENARIOS)
def test_devstral_accepts_runner_histories(parts, build):
    """Spec #60 §7: replay the runner-produced histories for the #60 shapes
    against the loaded Devstral; a strict template renders every request."""
    import json
    import urllib.request
    from dirtywork.providers.openai_compat import _to_openai_messages
    base = "http://localhost:1234/v1"
    model = "mistralai/devstral-small-2-2512"
    try:
        with urllib.request.urlopen(f"{base}/models", timeout=5) as r:
            ids = [m["id"] for m in json.load(r)["data"]]
    except Exception as e:                              # noqa: BLE001
        pytest.skip(f"LM Studio not reachable: {e}")
    if model not in ids:
        pytest.skip(f"{model} not loaded")
    provider, _r, _e = _run_scenario(parts, build)
    tools = [{"type": "function", "function": {"name": n, "description": "x",
              "parameters": {"type": "object", "properties": {"summary": {"type": "string"},
                                                              "command": {"type": "string"},
                                                              "path": {"type": "string"}}}}}
             for n in ("finish", "bash", "read_file", "write_file")]
    for history in provider.requests:
        body = json.dumps({"model": model, "messages": _to_openai_messages(history),
                           "tools": tools, "max_tokens": 1, "temperature": 0}).encode()
        req = urllib.request.Request(f"{base}/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            assert r.status == 200
```

(`parts` is `tests/test_runner.py`'s fixture, re-exported by the import above; if `test_live.py` already defines a fixture of that name, rename the import.) Mistral's template requires tool-call ids of exactly 9 alphanumeric characters; the scenarios use `f1`/`b1`/`c1`. If LM Studio rejects those ids (a 400 naming the id), map ids in the serialized messages to 9-char forms (`f"{id:0<9}"` with letters) inside the test before posting — the shape under test is unaffected.

Run: `/usr/bin/python3 -m pytest -q -m live tests/test_live.py -k devstral`
Expected: 4 passed (or skipped when LM Studio is down — then say so in the ledger and stop this task here; the owner reruns).

- [ ] **Step 2: Stage the reruns**

```bash
S=/private/tmp/claude-501/-Users-jimschneider-repos-dirtywork/fa6cf3d7-ce30-4690-93c3-5563eedc75b8/scratchpad
mkdir -p $S/plans $S/f3-run && cp -R bench/repos/sh-wait-for-service/. $S/f3-run/ && (cd $S/f3-run && rm -f bench.json && git init -q && git add -A && git commit -qm "soak fixture")
cat > $S/plans/issue60-rerun.jsonl <<'EOF'
{"label": "F4b-round2-dev-60", "task": "py-fix-off-by-one", "model": "mistralai/devstral-small-2-2512", "provider": "openai", "base_url": "http://localhost:1234/v1", "flags": ["--verify", "test -f .verify-round2 || { touch .verify-round2; echo 'verify: first round fails by design (F4 retry provoker); fix nothing, just finish again'; exit 1; }; python3 acceptance/check_sum_range.py", "--verify-rounds", "2"]}
{"label": "F4b-round2-qwen-60", "task": "py-fix-off-by-one", "model": "qwen/qwen3-coder-next", "provider": "openai", "base_url": "http://localhost:1234/v1", "flags": ["--verify", "test -f .verify-round2 || { touch .verify-round2; echo 'verify: first round fails by design (F4 retry provoker); fix nothing, just finish again'; exit 1; }; python3 acceptance/check_sum_range.py", "--verify-rounds", "2"]}
{"label": "F5-trunc2048-dev-60", "task": "py-big-fixture", "model": "mistralai/devstral-small-2-2512", "flags": ["--max-tokens", "2048"], "provider": "openai", "base_url": "http://localhost:1234/v1"}
{"label": "F5-trunc2048-qwen-60", "task": "py-big-fixture", "model": "qwen/qwen3-coder-next", "flags": ["--max-tokens", "2048"], "provider": "openai", "base_url": "http://localhost:1234/v1"}
{"label": "F3v2-run-dev-60", "repo": "__F3__", "task_text": "Run `bash build.sh` from the repo root and report exactly what it printed and its exit code. It is expected to write out.txt with a build confirmation. If it does not finish, find out why and fix it so `bash acceptance/check.sh` passes.", "model": "mistralai/devstral-small-2-2512", "flags": [], "provider": "openai", "base_url": "http://localhost:1234/v1"}
{"label": "F3v2-run-qwen-60", "repo": "__F3__", "task_text": "Run `bash build.sh` from the repo root and report exactly what it printed and its exit code. It is expected to write out.txt with a build confirmation. If it does not finish, find out why and fix it so `bash acceptance/check.sh` passes.", "model": "qwen/qwen3-coder-next", "flags": [], "provider": "openai", "base_url": "http://localhost:1234/v1"}
EOF
sed -i '' "s|__F3__|$S/f3-run|g" $S/plans/issue60-rerun.jsonl
/usr/bin/python3 tools/soak_driver.py $S/plans/issue60-rerun.jsonl --out ~/.dirtywork/bench/soak-60.jsonl --dry-run
```

Confirm the dry run prints six commands, then run it for real (each row can take up to 15 min; run in the background and poll `~/.dirtywork/bench/soak-60.jsonl`):

```bash
/usr/bin/python3 tools/soak_driver.py $S/plans/issue60-rerun.jsonl --out ~/.dirtywork/bench/soak-60.jsonl
/usr/bin/python3 tools/soak_harvest.py ~/.dirtywork/bench/soak-60.jsonl
```

- [ ] **Step 3: Judge and record**

For each row read `status`, `error`, the harvest `features` column, and grep the run's transcript:

```bash
for d in $(python3 -c "import json;[print(json.loads(l)['run_dir']) for l in open('$HOME/.dirtywork/bench/soak-60.jsonl')]"); do
  echo "== $d"; grep -c '"event": "nudge"' $d/transcript.jsonl; grep -o '"via": "[a-z_]*"' $d/transcript.jsonl | sort | uniq -c
  grep -o '"follow_up"' $d/transcript.jsonl | wc -l; grep -o 'jinja\|400' $d/transcript.jsonl | head -2
done
```

Pass criteria (spec §10): the three `-dev-60` rows are not `model_error` and `run_end.error` is empty; `F4b-round2-qwen-60` shows `F4(passed=True,rounds=2)`; every `nudge` event carries `via`; no transcript contains `Error rendering prompt`. Also note (spec §5) whether any assistant turn *after* a `placeholder` turn contains the literal `[empty reply]` — that is the imitation signal.

Append to `docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md`:

```
## #60 reruns (branch issue-60-followup-carriers, <commit>)

| label | model | status | wall_s | turns | nudges (via) | follow_up events | F-detectors | note |
|---|---|---|---|---|---|---|---|---|
| F4b-round2-dev-60 | devstral | … | … | … | … | … | … | was model_error (jinja 400) at b94dec9 |
| F4b-round2-qwen-60 | qwen | … | … | … | … | … | … | control: F4(passed=True,rounds=2) expected |
| F5-trunc2048-dev-60 | … | … | … | … | … | … | … | was model_error (empty reply after 38 append_file) |
| F5-trunc2048-qwen-60 | … | … | … | … | … | … | … | control |
| F3v2-run-dev-60 | … | … | … | … | … | … | … | was model_error (timeout nudge after tool result) |
| F3v2-run-qwen-60 | … | … | … | … | … | … | … | control |

Placeholder imitation: <none observed | seen in run <slug> turn N>.
```

(fill every cell from `soak-60.jsonl` / the harvest output; a row that still fails is recorded as such with its `error` — do not omit it).

- [ ] **Step 4: Commit**

```bash
git add tests/test_live.py docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md
git commit -m "live: Devstral accepts every #60 history shape; soak reruns recorded (#60 spec §7, §10)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-review (done while writing; kept for the executor)

- **Spec coverage:** §1-§2 facts → Task 2 oracle + docs; §3 → Task 5; §4 → Task 4; §5 → Task 3; §6.1 → Tasks 1, 4; §6.2 → Tasks 3, 4, 5; §6.3 → Task 6; §6.4 → Task 7; §7 → Tasks 2, 5, 8; §8 → Tasks 1, 3, 4, 5, 6, 7 (release notes are cut at release time by the owner — not a task here); §9.1-9.16 → Tasks 4, 5, 3, 3, 3, 5, 3, 4, 4, 5, 1, 5, 4+5, 6, 7, 8 respectively; §10 → Task 8; §11 out of scope honoured (no adapter change, no SIGTERM handler, no `user` event).
- **Type consistency:** `check_verify(final, via)` returns `(RunResult | None, str | None)` everywhere; `check_progress()` returns a 3-tuple in Task 5 and both callers unpack three; `deliver(text, records)`; `turn_tool_msgs`/`turn_terminal` hold `(msg, record)` pairs; `_tool_result_outcome(result_text, tool=None)`.
- **Known interim state:** after Task 4 the finish-path timeout nudge is still a user message after a tool result; the oracle is hooked only in Task 5, which fixes it — Task 4's suite is green because no double asserts the oracle yet.
- **Expected counts** are the arithmetic of the tests each task adds; if a count differs, the number that matters is "everything green and the count rose by the tests you added".
