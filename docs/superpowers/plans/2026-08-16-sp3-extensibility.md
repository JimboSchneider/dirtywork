# SP3 Extensibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make dirtywork extensible without surgery on `tools.py`/`runner.py`: a new tool becomes one `ToolSpec`, a new LLM provider becomes one adapter class passing a shared contract suite, and the operator gets `dirtywork runs …` (list/show/export/clean/verdict) and `dirtywork bench` (per-model completion/acceptance/token/latency numbers) on top of the sandboxed runtime sub-projects 1 and 2 deliver.

**Architecture:** A generic `ToolRegistry` (`dirtywork/toolspec.py`) validates arguments and enforces per-tool `Caps` (input/output size, timeout, transcript verbosity) against hand-rolled `ToolSpec` declarations; the six existing tools become `ToolSpec`s in `dirtywork/builtin_tools.py` whose `fn` calls the `Sandbox` protocol SP2 introduced. A `Provider` protocol (`dirtywork/providers/`) gives the runner a provider-neutral chat history and `ChatResponse`; `OpenAICompatClient` (moved out of `llm.py`) and a new `AnthropicClient` both pass one shared `tests/provider_contract.py` suite against recorded wire fixtures. `dirtywork/runner.py` is rewritten to drive `provider.chat(...)` and `registry.execute(...)` instead of the old OpenAI-dict-shaped client and `ToolExecutor`. `dirtywork/runs.py` and `dirtywork/bench.py` are new CLI-facing modules built on SP1's `rundir.py` and SP2's `docker_cli.py`/`export.py`/labels.

**Tech Stack:** Python ≥3.9, stdlib only (`dataclasses`, `typing`, `argparse`, `json`, `subprocess`, `urllib`, `tarfile`, `hashlib`, `tempfile`). Dev-only dependency: pytest.

**Spec:** `docs/superpowers/specs/2026-08-15-review-response-design.md` — "Sub-project 3: Extensibility" (§1–§5 + Sequencing), plus §SP2.3 (name-collision rule, consumed by `runs clean`) and §SP2.7 (export flow, reused by `runs export`).

## Global Constraints

- Python 3.9 floor: no `match`, no `X | Y` unions at runtime (only under `from __future__ import annotations`), no `tarfile.data_filter`, no `typing.Literal` misuse beyond 3.9 (`Literal` exists in 3.9's `typing` — fine), `dataclass(slots=)` not available.
- Stdlib only. No new dependencies.
- The stdout JSON contract may gain fields but must not lose or rename any (`status, worktree, branch, transcript, turns, usage, final_message`).
- Every existing test stays green after every task. Run `python -m pytest -q` at the end of each task.
- Commit after each task with a conventional message (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`).
- New tests go in the existing module test file for the file touched where one exists; new modules get `tests/test_<module>.py`.
- Tests that need Docker use marker `docker` (added by sub-project 2's plan to `pyproject.toml`'s `markers` and to `addopts` — `-m 'not live and not docker'`) and skip when Docker is not available.
- Never leave placeholders in a plan step: every code step shows the actual code; every test step shows the actual test.

## Precondition

Sub-project 1 (hardening) and sub-project 2 (Docker sandbox) are implemented and merged before this plan starts. Every name below already exists exactly as written: `Sandbox` protocol and `SandboxError`/`RunArtifacts` in `dirtywork/sandbox/__init__.py`; `HostSandbox`/`DockerSandbox`; `dirtywork/sandbox/export.py:export_run`; `dirtywork/sandbox/docker_cli.py` (`run`, `resolve_image`, `validate_objects_dir`, `T_QUERY`/`T_LIFECYCLE`/`T_PULL`/`T_EXPORT_STEP`); `dirtywork/sandbox/docker_args.py` (`DockerConfig`, `container_name`, `volume_name`, `repo_label`, `DEFAULT_IMAGE`); `dirtywork/rundir.py` (`RUNS_DIR`, `ensure_runs_dir`, `create_run_dir`, `write_run_json`, `read_run_json`, `RunDirError`); `dirtywork/workspace.py` (`load_repo_context(repo, base_commit)`, `worktree_base_commit`, `create_worktree(..., no_checkout=False)`, `ensure_worktrees_excluded`, `host_diff_stat`); `dirtywork/budget.py` (`BudgetExceeded`, `BudgetReport`, `measure_worktree`); `dirtywork/tools.py`'s `ToolExecutor.__init__(self, sandbox: Sandbox, transcript=None)` dispatching to sandbox methods; the runner's `finalize: Callable[[], dict] | None` hook and its `SandboxError` → `sandbox_error` / `BudgetExceeded` → `budget_exceeded` mapping; `run.json` fields as specified in SP2 §2 steps 4/11; Docker labels `dirtywork.run`/`dirtywork.repo`. `dirtywork/llm.py` was touched by SP1 only at row 5 (`detail = e.read(500)` in place of `e.read()[:500]`); otherwise it is the 121-line file shipped on `main` today — this plan's Task 5 moves its client into `providers/openai_compat.py` and must preserve that `e.read(500)` bound.

## File Structure

```
dirtywork/
  toolspec.py                    # NEW — Task 1, 2
  builtin_tools.py                # NEW — Task 3
  tools.py                        # MODIFIED — Task 3 (TOOL_SCHEMAS, ToolExecutor removed)
  providers/
    __init__.py                   # NEW — Task 4
    openai_compat.py              # NEW — Task 5
    anthropic.py                  # NEW — Task 7
  llm.py                          # MODIFIED — Task 5 (http_json extracted; LMStudioClient alias)
  runner.py                       # MODIFIED — Task 3 (registry+sandbox), Task 6 (provider-neutral)
  __main__.py                     # MODIFIED — Task 3, 6 (full rewrites), 9/14/15 (targeted edits)
  runs.py                         # NEW — Task 9, 10, 11, 12
  bench.py                        # NEW — Task 13, 14, 15
docs/
  transcript-schema.md            # NEW — Task 8
bench/
  repos/
    py-fix-off-by-one/            # NEW — Task 13
    node-add-cli-flag/            # NEW — Task 13
    sh-fix-script/                 # NEW — Task 13
tests/
  test_toolspec.py                # NEW — Task 1, 2
  test_builtin_tools.py           # NEW — Task 3
  test_tools_bash.py              # MODIFIED — Task 3 (TOOL_SCHEMAS/ToolExecutor tests removed)
  test_providers.py               # NEW — Task 4
  provider_contract.py            # NEW — Task 5 (no test_ prefix; not collected directly)
  fixtures/providers/openai/*.json      # NEW — Task 5
  fixtures/providers/anthropic/*.json   # NEW — Task 7
  test_provider_openai.py         # NEW — Task 5
  test_provider_anthropic.py      # NEW — Task 7
  test_llm.py                     # MODIFIED — Task 5 (http_json-level tests)
  test_runner.py                  # MODIFIED — Task 3, 6
  test_main.py                    # MODIFIED — Task 6, 9
  test_transcript_schema.py       # NEW — Task 8
  test_runs.py                    # NEW — Task 9, 10, 11, 12
  test_bench.py                   # NEW — Task 13, 14, 15
```

---

### Task 1: `toolspec.py` — dataclasses, `register`, `schemas`

**Files:**
- Create: `dirtywork/toolspec.py`
- Create: `tests/test_toolspec.py`

**Interfaces:**
- Consumes: nothing (stdlib `dataclasses`/`typing` only).
- Produces: `MISSING` sentinel; `ParamSpec(type: str, description: str = "", default: Any = MISSING)`; `Caps(fs: str, network: bool = False, max_input_bytes: int | None = None, max_output_chars: int = 8000, timeout_default: int | None = None, timeout_max: int | None = None, transcript: str = "preview")`; `ToolSpec(name: str, description: str, params: dict[str, ParamSpec], required: tuple[str, ...], fn: Callable, caps: Caps)`; `ToolResult(text: str, kind: str)`; `ToolValidationError(Exception)`; `ToolRegistry(transcript=None)` with `.register(spec: ToolSpec) -> None` and `.schemas() -> list[dict]` (OpenAI wire shape).

- [ ] **Step 1: Write the failing test**

`tests/test_toolspec.py`:

```python
from __future__ import annotations

from dirtywork.toolspec import Caps, MISSING, ParamSpec, ToolRegistry, ToolSpec


def _fn_ping(sandbox, **kwargs):
    return "pong"


def _fn_echo(sandbox, text):
    return f"echo:{text}"


PING_SPEC = ToolSpec(
    name="ping",
    description="Reply pong.",
    params={},
    required=(),
    fn=_fn_ping,
    caps=Caps(fs="none"),
)

ECHO_SPEC = ToolSpec(
    name="echo",
    description="Echo the given text back.",
    params={"text": ParamSpec(type="string", description="Text to echo")},
    required=("text",),
    fn=_fn_echo,
    caps=Caps(fs="none"),
)


def test_register_and_schemas_wire_shape():
    registry = ToolRegistry()
    registry.register(PING_SPEC)
    registry.register(ECHO_SPEC)
    assert registry.schemas() == [
        {"type": "function", "function": {
            "name": "ping", "description": "Reply pong.",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "echo", "description": "Echo the given text back.",
            "parameters": {"type": "object", "properties": {
                "text": {"type": "string", "description": "Text to echo"}},
                "required": ["text"]}}},
    ]


def test_missing_sentinel_is_falsy_and_distinct_from_none():
    assert not MISSING
    assert MISSING is not None
    assert repr(MISSING) == "MISSING"


def test_param_without_description_omits_key_from_schema():
    registry = ToolRegistry()
    spec = ToolSpec(
        name="bare", description="No param descriptions.",
        params={"n": ParamSpec(type="integer")}, required=(),
        fn=lambda sandbox, n=0: str(n), caps=Caps(fs="none"),
    )
    registry.register(spec)
    props = registry.schemas()[0]["function"]["parameters"]["properties"]
    assert props == {"n": {"type": "integer"}}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_toolspec.py -q`
Expected: `ModuleNotFoundError: No module named 'dirtywork.toolspec'`

- [ ] **Step 3: Write the minimal implementation**

`dirtywork/toolspec.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


class _MissingType:
    """Sentinel distinguishing 'no default provided' from an explicit ``None``
    default. Falsy, so ``if param.default:`` degrades safely for callers that
    forget to check identity, but code that needs correctness uses ``is``."""

    def __repr__(self) -> str:
        return "MISSING"

    def __bool__(self) -> bool:
        return False


MISSING = _MissingType()


@dataclass(frozen=True)
class ParamSpec:
    type: str
    description: str = ""
    default: Any = MISSING


@dataclass(frozen=True)
class Caps:
    """Enforced generically by ToolRegistry.execute (Task 2). ``network``/``fs``
    are declared for docs/schema purposes only in this release: every tool runs
    inside the sandbox passed to ``execute``, so there is no separate host/sandbox
    dispatch flag here to gate on."""

    fs: str  # "none" | "read" | "write"
    network: bool = False
    max_input_bytes: int | None = None
    max_output_chars: int = 8000
    timeout_default: int | None = None
    timeout_max: int | None = None
    transcript: str = "preview"  # "full" | "preview" | "none"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    params: dict
    required: tuple
    fn: Callable
    caps: Caps


@dataclass(frozen=True)
class ToolResult:
    text: str
    kind: str  # "ok" | "error" | "blocked"


class ToolValidationError(Exception):
    """Raised by the internal argument validator; ToolRegistry.execute (Task 2)
    catches it and converts it to an error ToolResult. Never escapes execute()."""


class ToolRegistry:
    def __init__(self, transcript=None):
        self.transcript = transcript
        self._table: dict = {}

    def register(self, spec: ToolSpec) -> None:
        self._table[spec.name] = spec

    def spec(self, name: str):
        return self._table.get(name)

    def schemas(self) -> list:
        out = []
        for spec in self._table.values():
            properties = {}
            for pname, pspec in spec.params.items():
                prop = {"type": pspec.type}
                if pspec.description:
                    prop["description"] = pspec.description
                properties[pname] = prop
            out.append({
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": list(spec.required),
                    },
                },
            })
        return out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_toolspec.py -q`
Expected: 3 passed

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: all green (this module is not imported by anything yet, so nothing else can break)

- [ ] **Step 6: Commit**

```bash
git add dirtywork/toolspec.py tests/test_toolspec.py
git commit -m "feat: add ToolSpec/ToolRegistry dataclasses with register/schemas"
```

---

### Task 2: `ToolRegistry.execute` — validation, caps, blocked, transcript

**Files:**
- Modify: `dirtywork/toolspec.py`
- Modify: `tests/test_toolspec.py`

**Interfaces:**
- Consumes: `time.monotonic` (stdlib).
- Produces: `ToolRegistry.execute(self, name: str, args: dict, *, sandbox, deadline: float | None) -> ToolResult`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_toolspec.py`:

```python
import time

import pytest

from dirtywork.toolspec import ToolResult


class _RecordingTranscript:
    def __init__(self):
        self.events = []

    def write(self, event, **fields):
        self.events.append((event, fields))


def _fn_blocked(sandbox, **kwargs):
    return "BLOCKED: not allowed here"


def _fn_long(sandbox, **kwargs):
    return "x" * 50


def _fn_needs_timeout(sandbox, timeout=10):
    return f"ran with timeout={timeout}"


BLOCKED_SPEC = ToolSpec(
    name="blockme", description="Always blocked.", params={}, required=(),
    fn=_fn_blocked, caps=Caps(fs="none"))

CAPPED_SPEC = ToolSpec(
    name="longtext", description="Returns 50 chars.", params={}, required=(),
    fn=_fn_long, caps=Caps(fs="none", max_output_chars=10))

TIMEOUT_SPEC = ToolSpec(
    name="waitfor", description="Echoes its timeout.",
    params={"timeout": ParamSpec(type="integer", default=10)}, required=(),
    fn=_fn_needs_timeout, caps=Caps(fs="none", timeout_default=10, timeout_max=20))

BYTES_SPEC = ToolSpec(
    name="bytesin", description="Takes a string.",
    params={"text": ParamSpec(type="string")}, required=("text",),
    fn=_fn_echo, caps=Caps(fs="none", max_input_bytes=5))


def _fn_needs_timeout_hidden(sandbox, path=".", timeout=30):
    return f"listed {path} with timeout={timeout}"


HIDDEN_TIMEOUT_SPEC = ToolSpec(
    name="listwait",
    description="grep-shaped: caps.timeout_default with no exposed timeout param.",
    params={"path": ParamSpec(type="string", default=".")}, required=(),
    fn=_fn_needs_timeout_hidden, caps=Caps(fs="read", timeout_default=30))


def _registry():
    r = ToolRegistry()
    for spec in (PING_SPEC, ECHO_SPEC, BLOCKED_SPEC, CAPPED_SPEC, TIMEOUT_SPEC,
                 BYTES_SPEC, HIDDEN_TIMEOUT_SPEC):
        r.register(spec)
    return r


def test_execute_unknown_tool():
    r = _registry()
    result = r.execute("nope", {}, sandbox=None, deadline=None)
    assert result.kind == "error"
    assert result.text.startswith("ERROR: unknown tool 'nope'. Available:")
    assert "ping" in result.text and "echo" in result.text


def test_execute_dispatches_and_fills_defaults():
    r = _registry()
    result = r.execute("ping", {}, sandbox=object(), deadline=None)
    assert result == ToolResult(text="pong", kind="ok")


def test_execute_unknown_parameter_is_error():
    r = _registry()
    result = r.execute("ping", {"bogus": 1}, sandbox=object(), deadline=None)
    assert result.kind == "error"
    assert "unknown parameter" in result.text.lower()
    assert "bogus" in result.text


def test_execute_missing_required_is_error():
    r = _registry()
    result = r.execute("echo", {}, sandbox=object(), deadline=None)
    assert result.kind == "error"
    assert "missing required" in result.text.lower()
    assert "text" in result.text


def test_execute_type_mismatch_is_error():
    r = _registry()
    result = r.execute("echo", {"text": 123}, sandbox=object(), deadline=None)
    assert result.kind == "error"
    assert "must be string" in result.text


def test_execute_bool_is_not_integer():
    r = ToolRegistry()
    spec = ToolSpec(name="takesint", description="d",
                     params={"n": ParamSpec(type="integer")}, required=("n",),
                     fn=lambda sandbox, n: f"n={n}", caps=Caps(fs="none"))
    r.register(spec)
    result = r.execute("takesint", {"n": True}, sandbox=object(), deadline=None)
    assert result.kind == "error"
    assert "must be integer" in result.text


def test_execute_int_accepted_for_number_param():
    r = ToolRegistry()
    spec = ToolSpec(name="takesnum", description="d",
                     params={"n": ParamSpec(type="number")}, required=("n",),
                     fn=lambda sandbox, n: f"n={n}", caps=Caps(fs="none"))
    r.register(spec)
    result = r.execute("takesnum", {"n": 3}, sandbox=object(), deadline=None)
    assert result.kind == "ok" and result.text == "n=3"


def test_execute_caps_max_output_chars_truncates():
    r = _registry()
    result = r.execute("longtext", {}, sandbox=object(), deadline=None)
    assert result.kind == "ok"
    assert result.text.startswith("x" * 10)
    assert "truncated at 10 chars" in result.text


def test_execute_caps_max_input_bytes_rejects_oversized():
    r = _registry()
    result = r.execute("bytesin", {"text": "toolong"}, sandbox=object(), deadline=None)
    assert result.kind == "error"
    assert "byte limit" in result.text


def test_execute_timeout_clamped_to_timeout_max():
    r = _registry()
    result = r.execute("waitfor", {"timeout": 999}, sandbox=object(), deadline=None)
    assert result.text == "ran with timeout=20"


def test_execute_timeout_clamped_to_remaining_deadline():
    r = _registry()
    deadline = time.monotonic() + 3
    result = r.execute("waitfor", {"timeout": 999}, sandbox=object(), deadline=deadline)
    assert result.text in ("ran with timeout=1", "ran with timeout=2", "ran with timeout=3")


def test_execute_injects_timeout_even_when_not_a_schema_param():
    r = _registry()
    deadline = time.monotonic() + 2
    result = r.execute("listwait", {}, sandbox=object(), deadline=deadline)
    assert result.text in ("listed . with timeout=1", "listed . with timeout=2")


def test_execute_deadline_exceeded_short_circuits():
    r = _registry()
    deadline = time.monotonic() - 1
    result = r.execute("ping", {}, sandbox=object(), deadline=deadline)
    assert result.kind == "error"
    assert "deadline exceeded" in result.text.lower()


def test_execute_blocked_writes_guardrail_block_and_kind():
    transcript = _RecordingTranscript()
    r = ToolRegistry(transcript=transcript)
    r.register(BLOCKED_SPEC)
    result = r.execute("blockme", {}, sandbox=object(), deadline=None)
    assert result.kind == "blocked"
    assert result.text.startswith("BLOCKED:")
    assert transcript.events and transcript.events[0][0] == "guardrail_block"
    assert transcript.events[0][1]["tool"] == "blockme"


def test_execute_fn_exception_propagates():
    r = ToolRegistry()
    def _boom(sandbox, **kwargs):
        raise RuntimeError("kaboom")
    r.register(ToolSpec(name="boom", description="d", params={}, required=(),
                         fn=_boom, caps=Caps(fs="none")))
    with pytest.raises(RuntimeError, match="kaboom"):
        r.execute("boom", {}, sandbox=object(), deadline=None)


def test_registry_spec_lookup():
    r = _registry()
    assert r.spec("echo") is ECHO_SPEC
    assert r.spec("nonexistent") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_toolspec.py -q`
Expected: `AttributeError: 'ToolRegistry' object has no attribute 'execute'`

- [ ] **Step 3: Write the minimal implementation**

In `dirtywork/toolspec.py`, add `import time` to the top imports, then add these module-level pieces above `class ToolRegistry` (after `ToolValidationError`):

```python
_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
}


def _validate_args(spec: ToolSpec, args: dict) -> dict:
    unknown = sorted(set(args) - set(spec.params))
    if unknown:
        raise ToolValidationError(
            f"unknown parameter(s) for {spec.name}: {', '.join(unknown)}"
        )
    missing = [p for p in spec.required if p not in args]
    if missing:
        raise ToolValidationError(
            f"missing required parameter(s) for {spec.name}: {', '.join(missing)}"
        )
    call_args = {}
    for pname, pspec in spec.params.items():
        if pname in args:
            value = args[pname]
            check = _TYPE_CHECKS.get(pspec.type)
            if check is not None and not check(value):
                raise ToolValidationError(
                    f"parameter '{pname}' for {spec.name} must be {pspec.type}, "
                    f"got {type(value).__name__}"
                )
            call_args[pname] = value
        elif pspec.default is not MISSING:
            call_args[pname] = pspec.default
    return call_args
```

Then add `execute` to `ToolRegistry`, right after `schemas`:

```python
    def execute(self, name: str, args: dict, *, sandbox, deadline) -> ToolResult:
        spec = self._table.get(name)
        if spec is None:
            available = ", ".join(sorted(self._table))
            return ToolResult(
                text=f"ERROR: unknown tool '{name}'. Available: {available}.",
                kind="error",
            )
        try:
            call_args = _validate_args(spec, args)
        except ToolValidationError as e:
            return ToolResult(text=f"ERROR: {e}", kind="error")

        caps = spec.caps
        if caps.max_input_bytes is not None:
            total = sum(
                len(v.encode("utf-8")) for v in call_args.values() if isinstance(v, str)
            )
            if total > caps.max_input_bytes:
                return ToolResult(
                    text=(f"ERROR: input for {name} is {total} bytes, over the "
                          f"{caps.max_input_bytes}-byte limit."),
                    kind="error",
                )

        remaining = None
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return ToolResult(
                    text=("ERROR: run deadline exceeded; stop calling tools and "
                          "summarize what you have done."),
                    kind="error",
                )

        if caps.timeout_default is not None:
            # Deadline-based clamping applies whenever a tool declares a
            # timeout_default, even for tools (like grep) that never expose
            # "timeout" as a model-settable schema parameter — the registry
            # injects/overwrites args["timeout"] either way, and spec.fn's own
            # keyword default absorbs it since it isn't in the JSON schema.
            requested = call_args.get("timeout")
            base = requested if requested is not None else caps.timeout_default
            bound = base
            if caps.timeout_max is not None:
                bound = min(bound, caps.timeout_max)
            if remaining is not None:
                bound = min(bound, max(1, int(remaining)))
            call_args["timeout"] = int(bound)

        result_text = spec.fn(sandbox, **call_args)

        if len(result_text) > caps.max_output_chars:
            result_text = (
                result_text[: caps.max_output_chars]
                + f"\n[output truncated at {caps.max_output_chars} chars]"
            )

        if result_text.startswith("BLOCKED:"):
            if self.transcript is not None:
                self.transcript.write(
                    "guardrail_block", tool=name, args=call_args, reason=result_text
                )
            return ToolResult(text=result_text, kind="blocked")
        return ToolResult(text=result_text, kind="ok")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_toolspec.py -q`
Expected: 19 passed

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add dirtywork/toolspec.py tests/test_toolspec.py
git commit -m "feat: implement ToolRegistry.execute (validation, caps, blocked, deadline)"
```

---

### Task 3: `builtin_tools.py` — six `ToolSpec`s; runner and CLI switch to the registry

**Files:**
- Create: `dirtywork/builtin_tools.py`
- Create: `tests/test_builtin_tools.py`
- Modify: `dirtywork/tools.py` (delete `TOOL_SCHEMAS`, `ToolExecutor`)
- Modify: `tests/test_tools_bash.py` (drop the `TOOL_SCHEMAS`/`ToolExecutor` tests; keep the host-function tests)
- Modify: `dirtywork/runner.py` (full rewrite: `executor` → `registry` + `sandbox`)
- Modify: `dirtywork/__main__.py` (targeted patch: wire `default_registry` in place of `ToolExecutor`)
- Modify: `tests/test_runner.py` (targeted patch: `parts` fixture and every `Runner(...)` call site)

**Interfaces:**
- Consumes: `Sandbox` protocol methods (`read_file(path, offset, limit)`, `write_file(path, content)`, `edit_file(path, old, new)`, `list_dir(path)`, `grep(pattern, path, glob, timeout)`, `bash(command, timeout)`) from SP2; `dirtywork.toolspec.{Caps, ParamSpec, ToolRegistry, ToolSpec}`; `dirtywork.tools.MAX_RESULT_CHARS`.
- Produces: `default_registry(transcript=None) -> ToolRegistry` with the six specs registered; `Runner(client, registry, sandbox, transcript, model, max_turns=40, timeout=1800, temperature=None, run_info=None, finalize=None)`.

- [ ] **Step 1: Write the failing test**

`tests/test_builtin_tools.py`:

```python
from __future__ import annotations

from dirtywork.builtin_tools import default_registry


class FakeSandbox:
    def __init__(self):
        self.calls = []

    def read_file(self, path, offset, limit):
        self.calls.append(("read_file", path, offset, limit))
        return f"read:{path}:{offset}:{limit}"

    def write_file(self, path, content):
        self.calls.append(("write_file", path, content))
        return f"wrote:{path}:{len(content)}"

    def edit_file(self, path, old, new):
        self.calls.append(("edit_file", path, old, new))
        return f"edited:{path}"

    def list_dir(self, path):
        self.calls.append(("list_dir", path))
        return f"listing:{path}"

    def grep(self, pattern, path, glob, timeout):
        self.calls.append(("grep", pattern, path, glob, timeout))
        return f"grepped:{pattern}"

    def bash(self, command, timeout):
        self.calls.append(("bash", command, timeout))
        return f"exit code: 0\n{command}"


def _param(props, required):
    return {"type": "object", "properties": props, "required": required}


# Frozen copy of today's dirtywork.tools.TOOL_SCHEMAS -- this equality is the
# regression test for the model-facing wire contract; it must never drift
# without a deliberate, matching change to builtin_tools.py.
EXPECTED_SCHEMAS = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a file, returning numbered lines. Use offset/limit to "
                       "page through; files over ~5 MB or non-regular files are refused.",
        "parameters": _param({
            "path": {"type": "string", "description": "Path relative to worktree root"},
            "offset": {"type": "integer", "description": "0-based first line, default 0"},
            "limit": {"type": "integer", "description": "Max lines, default 400"},
        }, ["path"])}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Create or overwrite a file. Parent directories are created.",
        "parameters": _param({
            "path": {"type": "string"},
            "content": {"type": "string"},
        }, ["path", "content"])}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Replace old_string with new_string in a file. old_string "
                       "must occur exactly once — include surrounding context to "
                       "make it unique.",
        "parameters": _param({
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        }, ["path", "old_string", "new_string"])}},
    {"type": "function", "function": {
        "name": "list_dir",
        "description": "List a directory's entries (dirs end with /).",
        "parameters": _param({"path": {"type": "string", "description": "Default '.'"}}, [])}},
    {"type": "function", "function": {
        "name": "grep",
        "description": "Search file contents with a regex. Optional glob filter "
                       "like '*.cs' or '*.tsx'.",
        "parameters": _param({
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "Default '.'"},
            "glob": {"type": "string"},
        }, ["pattern"])}},
    {"type": "function", "function": {
        "name": "bash",
        "description": "Run a shell command in the worktree (cwd is the worktree "
                       "root). Use for builds/tests/git-status, NEVER for editing "
                       "files. 120s default timeout, 600s max. Backgrounded "
                       "processes are terminated when the command returns.",
        "parameters": _param({
            "command": {"type": "string"},
            "timeout": {"type": "integer", "description": "Seconds, default 120, max 600"},
        }, ["command"])}},
]


def test_schemas_match_frozen_wire_contract():
    registry = default_registry()
    assert registry.schemas() == EXPECTED_SCHEMAS


def test_read_file_dispatches_positionally():
    sandbox = FakeSandbox()
    registry = default_registry()
    result = registry.execute("read_file", {"path": "a.txt"}, sandbox=sandbox, deadline=None)
    assert result.kind == "ok"
    assert sandbox.calls == [("read_file", "a.txt", 0, 400)]


def test_write_file_dispatches():
    sandbox = FakeSandbox()
    registry = default_registry()
    result = registry.execute("write_file", {"path": "a.txt", "content": "hi"},
                              sandbox=sandbox, deadline=None)
    assert result.kind == "ok"
    assert sandbox.calls == [("write_file", "a.txt", "hi")]


def test_edit_file_dispatches_positionally_old_new():
    sandbox = FakeSandbox()
    registry = default_registry()
    registry.execute("edit_file", {"path": "a.txt", "old_string": "x", "new_string": "y"},
                     sandbox=sandbox, deadline=None)
    assert sandbox.calls == [("edit_file", "a.txt", "x", "y")]


def test_list_dir_default_path():
    sandbox = FakeSandbox()
    registry = default_registry()
    registry.execute("list_dir", {}, sandbox=sandbox, deadline=None)
    assert sandbox.calls == [("list_dir", ".")]


def test_grep_dispatches_with_hidden_timeout_default():
    sandbox = FakeSandbox()
    registry = default_registry()
    registry.execute("grep", {"pattern": "foo"}, sandbox=sandbox, deadline=None)
    assert sandbox.calls == [("grep", "foo", ".", None, 30)]


def test_bash_dispatches_with_timeout_default():
    sandbox = FakeSandbox()
    registry = default_registry()
    registry.execute("bash", {"command": "ls"}, sandbox=sandbox, deadline=None)
    assert sandbox.calls == [("bash", "ls", 120)]


def test_bash_timeout_clamped_to_600():
    sandbox = FakeSandbox()
    registry = default_registry()
    registry.execute("bash", {"command": "ls", "timeout": 9999}, sandbox=sandbox, deadline=None)
    assert sandbox.calls == [("bash", "ls", 600)]


def test_blocked_result_from_sandbox_marks_kind_blocked():
    class BlockingSandbox(FakeSandbox):
        def bash(self, command, timeout):
            return "BLOCKED: sudo is not allowed."
    sandbox = BlockingSandbox()
    registry = default_registry()
    result = registry.execute("bash", {"command": "sudo ls"}, sandbox=sandbox, deadline=None)
    assert result.kind == "blocked"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_builtin_tools.py -q`
Expected: `ModuleNotFoundError: No module named 'dirtywork.builtin_tools'`

- [ ] **Step 3: Write the minimal implementation**

`dirtywork/builtin_tools.py`:

```python
from __future__ import annotations

from .toolspec import Caps, ParamSpec, ToolRegistry, ToolSpec
from .tools import MAX_RESULT_CHARS


def _read_file(sandbox, path, offset=0, limit=400):
    return sandbox.read_file(path, offset, limit)


def _write_file(sandbox, path, content):
    return sandbox.write_file(path, content)


def _edit_file(sandbox, path, old_string, new_string):
    return sandbox.edit_file(path, old_string, new_string)


def _list_dir(sandbox, path="."):
    return sandbox.list_dir(path)


def _grep(sandbox, pattern, path=".", glob=None, timeout=30):
    return sandbox.grep(pattern, path, glob, timeout)


def _bash(sandbox, command, timeout=120):
    return sandbox.bash(command, timeout)


READ_FILE_SPEC = ToolSpec(
    name="read_file",
    description=("Read a file, returning numbered lines. Use offset/limit to "
                 "page through; files over ~5 MB or non-regular files are refused."),
    params={
        "path": ParamSpec(type="string", description="Path relative to worktree root"),
        "offset": ParamSpec(type="integer", description="0-based first line, default 0", default=0),
        "limit": ParamSpec(type="integer", description="Max lines, default 400", default=400),
    },
    required=("path",),
    fn=_read_file,
    caps=Caps(fs="read", max_output_chars=MAX_RESULT_CHARS, transcript="preview"),
)

WRITE_FILE_SPEC = ToolSpec(
    name="write_file",
    description="Create or overwrite a file. Parent directories are created.",
    params={
        "path": ParamSpec(type="string"),
        "content": ParamSpec(type="string"),
    },
    required=("path", "content"),
    fn=_write_file,
    caps=Caps(fs="write", max_output_chars=MAX_RESULT_CHARS, transcript="preview"),
)

EDIT_FILE_SPEC = ToolSpec(
    name="edit_file",
    description=("Replace old_string with new_string in a file. old_string "
                 "must occur exactly once — include surrounding context to "
                 "make it unique."),
    params={
        "path": ParamSpec(type="string"),
        "old_string": ParamSpec(type="string"),
        "new_string": ParamSpec(type="string"),
    },
    required=("path", "old_string", "new_string"),
    fn=_edit_file,
    caps=Caps(fs="write", max_output_chars=MAX_RESULT_CHARS, transcript="preview"),
)

LIST_DIR_SPEC = ToolSpec(
    name="list_dir",
    description="List a directory's entries (dirs end with /).",
    params={"path": ParamSpec(type="string", description="Default '.'", default=".")},
    required=(),
    fn=_list_dir,
    caps=Caps(fs="read", max_output_chars=MAX_RESULT_CHARS, transcript="preview"),
)

GREP_SPEC = ToolSpec(
    name="grep",
    description="Search file contents with a regex. Optional glob filter like '*.cs' or '*.tsx'.",
    params={
        "pattern": ParamSpec(type="string"),
        "path": ParamSpec(type="string", description="Default '.'", default="."),
        "glob": ParamSpec(type="string", default=None),
    },
    required=("pattern",),
    fn=_grep,
    caps=Caps(fs="read", max_output_chars=MAX_RESULT_CHARS, timeout_default=30, transcript="preview"),
)

BASH_SPEC = ToolSpec(
    name="bash",
    description=("Run a shell command in the worktree (cwd is the worktree "
                 "root). Use for builds/tests/git-status, NEVER for editing "
                 "files. 120s default timeout, 600s max. Backgrounded "
                 "processes are terminated when the command returns."),
    params={
        "command": ParamSpec(type="string"),
        "timeout": ParamSpec(type="integer", description="Seconds, default 120, max 600", default=120),
    },
    required=("command",),
    fn=_bash,
    caps=Caps(fs="write", network=True, max_output_chars=MAX_RESULT_CHARS,
              timeout_default=120, timeout_max=600, transcript="preview"),
)


def default_registry(transcript=None) -> ToolRegistry:
    registry = ToolRegistry(transcript=transcript)
    for spec in (READ_FILE_SPEC, WRITE_FILE_SPEC, EDIT_FILE_SPEC, LIST_DIR_SPEC, GREP_SPEC, BASH_SPEC):
        registry.register(spec)
    return registry
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_builtin_tools.py -q`
Expected: 9 passed

- [ ] **Step 5: Remove `TOOL_SCHEMAS`/`ToolExecutor` from `tools.py`**

Neither symbol is referenced anywhere else inside `tools.py` (both live in one trailing block: `_param`, `TOOL_SCHEMAS`, `class ToolExecutor`, in that order, ending at end-of-file), so this mechanical removal is safe regardless of exactly how SP1/SP2 changed the function bodies above it:

```bash
python3 - <<'PY'
from pathlib import Path
p = Path("dirtywork/tools.py")
text = p.read_text()
marker = "\ndef _param(props: dict, required: list) -> dict:"
assert marker in text, "tools.py no longer has the expected _param marker -- inspect the file by hand"
idx = text.index(marker)
p.write_text(text[:idx].rstrip() + "\n")
PY
```

Then rewrite `tests/test_tools_bash.py` to drop the `TOOL_SCHEMAS`/`ToolExecutor`-specific tests (their coverage now lives in `test_toolspec.py` and `test_builtin_tools.py`) and keep only the tests exercising `bash`/`grep` as bare host functions:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from dirtywork.tools import bash, grep


@pytest.fixture()
def wt(tmp_path: Path) -> Path:
    (tmp_path / "hello.txt").write_text("hi\n")
    return tmp_path


def test_bash_runs_in_worktree_cwd(wt: Path):
    out = bash(wt, "pwd && cat hello.txt")
    assert "exit code: 0" in out
    assert str(wt.resolve()) in out
    assert "hi" in out


def test_bash_nonzero_exit_reported(wt: Path):
    out = bash(wt, "exit 3")
    assert "exit code: 3" in out


def test_bash_blocked_command(wt: Path):
    out = bash(wt, "sudo ls")
    assert out.startswith("BLOCKED:")


def test_bash_timeout(wt: Path):
    out = bash(wt, "sleep 5", timeout=1)
    assert "timed out" in out.lower()


def test_bash_env_is_minimal(wt: Path, monkeypatch):
    monkeypatch.setenv("MY_SECRET", "sekrit")
    out = bash(wt, "env")
    assert "PATH=" in out
    assert "MY_SECRET" not in out  # parent env not inherited wholesale


def test_grep_timeout_kwarg_works(wt: Path):
    out = grep(wt, "hi", timeout=5)
    assert "hello.txt" in out


def test_bash_output_is_capped(wt: Path):
    # 2 MB of output must not blow up; it is capped and noted.
    out = bash(wt, "python3 -c \"import sys; sys.stdout.write('A'*2000000)\"")
    assert len(out) < 20000
    assert "capped" in out


def test_bash_runaway_output_times_out_without_ooming(wt: Path):
    # cat /dev/zero would OOM under unbounded capture; here it is drained and killed.
    out = bash(wt, "cat /dev/zero", timeout=1)
    assert "timed out" in out.lower()


def test_bash_backgrounded_child_does_not_stall(wt: Path):
    import time
    start = time.monotonic()
    out = bash(wt, "sleep 30 & echo hi", timeout=10)
    assert "hi" in out
    assert time.monotonic() - start < 3.0


def test_bash_timeout_reaps_process_tree(wt: Path):
    import time
    out = bash(wt, "(sleep 2 && touch survived.txt) & wait", timeout=1)
    assert "timed out" in out.lower()
    time.sleep(2.5)
    assert not (wt / "survived.txt").exists()
```

Note this drops the `import json`/`import time`/`import pytest`/`from dirtywork.transcript import Transcript` header lines the old file had for the removed tests; `pytest` is still needed for the `@pytest.fixture` decorator, so the final import block is `from __future__ import annotations`, `from pathlib import Path`, `import pytest`, `from dirtywork.tools import bash, grep` (with `time` imported locally inside the two tests that need it, as shown above, to keep the top-level import list minimal since only those two tests use it).

- [ ] **Step 6: Rewrite `dirtywork/runner.py`**

`ToolExecutor` and `TOOL_SCHEMAS` are gone, so `Runner` can no longer import them. Overwrite the whole file — this also carries forward every SP1/SP2 addition already documented for this file (`MAX_ASSISTANT_TEXT_CHARS`, the `finalize` hook merged into `run_end`/`RunResult.extra`, `BudgetExceeded` → `budget_exceeded`, `SandboxError` → `sandbox_error`, `schema_version: 2` on `run_start`) on top of this task's registry/sandbox switch:

```python
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Callable

from .budget import BudgetExceeded
from .llm import LLMTimeout
from .sandbox import SandboxError

CONTEXT_WINDOWS = {
    "qwen/qwen3-coder-next": 65536,
    "mistralai/devstral-small-2-2512": 32768,
}
DEFAULT_WINDOW = 32768
TRIM_MARKER = "[result trimmed — re-run the tool if needed]"
CHARS_PER_TOKEN = 4
BUDGET_FRACTION = 0.75
MAX_CONSECUTIVE_FAILURES = 3
MAX_ASSISTANT_TEXT_CHARS = 64_000


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
    def __init__(self, client, registry, sandbox, transcript, model,
                 max_turns: int = 40, timeout: int = 1800,
                 temperature: float | None = None,
                 run_info: dict | None = None,
                 finalize: "Callable[[], dict] | None" = None):
        self.client = client
        self.registry = registry
        self.sandbox = sandbox
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

        def finish(status: str, final: str) -> RunResult:
            extra = {}
            if self.finalize is not None:
                try:
                    extra = self.finalize() or {}
                except Exception as e:
                    extra = {"finalize_error": repr(e)}
            self.transcript.write("run_end", status=status, turns=turns,
                                  duration_s=round(time.monotonic() - start, 1),
                                  usage=usage, **extra)
            return RunResult(status, turns, final, usage, extra)

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
                    resp = self.client.chat(self.model, messages, tools=self.registry.schemas(),
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
                    v = resp_usage.get(k, 0)
                    if isinstance(v, (int, float)) and not isinstance(v, bool) \
                            and math.isfinite(v) and v >= 0:
                        usage[k] += int(v)
                raw = msg.get("tool_calls") or []
                if not isinstance(raw, list):
                    raw = []
                tool_calls = [_canonical_tool_call(tc) for tc in raw if _valid_tool_call(tc)]
                malformed_count = len(raw) - len(tool_calls)
                assistant_text = msg.get("content")
                self.transcript.write(
                    "assistant",
                    text=(assistant_text[:MAX_ASSISTANT_TEXT_CHARS]
                          if assistant_text else assistant_text),
                    tool_calls=[{"name": (tc.get("function") or {}).get("name"),
                                 "arguments": ((tc.get("function") or {}).get("arguments") or "")[:2000]}
                                for tc in tool_calls])
                if raw:
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

                try:
                    for tc in tool_calls:
                        fn_info = tc.get("function") or {}
                        name = fn_info.get("name") or ""
                        raw_args = fn_info.get("arguments") or "{}"
                        call_id = tc.get("id", "")
                        try:
                            args = json.loads(raw_args)
                            if not isinstance(args, dict):
                                raise ValueError("arguments must be a JSON object")
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
                        else:
                            tool_result = self.registry.execute(
                                name, args, sandbox=self.sandbox, deadline=deadline)
                            result = tool_result.text
                            failures = 0 if tool_result.kind != "error" else failures + 1
                        self.transcript.write("tool_result", tool=name,
                                              args=raw_args[:500],
                                              result=result[:2000])
                        messages.append({"role": "tool", "tool_call_id": call_id,
                                         "content": result})
                        if failures >= MAX_CONSECUTIVE_FAILURES:
                            return finish("model_error",
                                          "aborted after repeated malformed tool calls")
                except BudgetExceeded as e:
                    return finish("budget_exceeded", e.reason)
                except SandboxError as e:
                    return finish("sandbox_error", str(e))

                if malformed_count > 0:
                    messages.append({
                        "role": "user",
                        "content": (f"{malformed_count} of your tool calls were malformed "
                                    "(missing or invalid id/function fields) and were "
                                    "discarded. Re-issue them as valid tool calls."),
                    })
        except KeyboardInterrupt:
            return finish("interrupted", "")
```

- [ ] **Step 7: Patch `dirtywork/__main__.py`**

By this point (SP1+SP2 landed), `main()` already constructs a `sandbox` object (`HostSandbox()` or `DockerSandbox(...)` depending on `--sandbox`) and builds `executor = ToolExecutor(sandbox, transcript=transcript)`, then `Runner(client, executor, transcript, model=..., ...)`. This step only swaps that dispatch object — every CLI flag, preflight check, worktree/run-dir/sandbox construction from SP1/SP2 is untouched:

```bash
python3 - <<'PY'
from pathlib import Path
import re

p = Path("dirtywork/__main__.py")
text = p.read_text()

assert "from .tools import ToolExecutor" in text, (
    "__main__.py does not import ToolExecutor as expected post-SP1/SP2 -- "
    "inspect the file and apply this substitution by hand."
)
text = text.replace(
    "from .tools import ToolExecutor",
    "from .builtin_tools import default_registry",
)

assert re.search(r"executor\s*=\s*ToolExecutor\(", text), (
    "no 'executor = ToolExecutor(...)' call found -- inspect the file by hand."
)
text = re.sub(
    r"executor\s*=\s*ToolExecutor\([^)]*\)",
    "registry = default_registry(transcript)",
    text,
)

count = text.count("Runner(client, executor, transcript")
assert count >= 1, "no 'Runner(client, executor, transcript' call site found."
text = text.replace(
    "Runner(client, executor, transcript",
    "Runner(client, registry, sandbox, transcript",
)

p.write_text(text)
print(f"patched {count} Runner(...) call site(s)")
PY
```

- [ ] **Step 8: Patch `tests/test_runner.py`**

Same situation: SP1/SP2 already updated the `parts` fixture to build a `sandbox` and pass it into `ToolExecutor(sandbox, transcript=transcript)`, and every test still destructures `wt, executor, transcript, tmp = parts` (or `tmp_path` in the one test that names it that way) before calling `Runner(client, executor, transcript, model=...)`. This task replaces the fixture with a `registry` + a small in-file `_WorktreeSandbox` double (dispatching straight to the already-tested host functions in `dirtywork.tools`, independent of `HostSandbox`'s real start/finalize/budget lifecycle, which is irrelevant to these tool-loop tests) and repoints every call site:

```bash
python3 - <<'PY'
from pathlib import Path
import re

p = Path("tests/test_runner.py")
text = p.read_text()

assert "from dirtywork.tools import ToolExecutor" in text, (
    "test_runner.py does not import ToolExecutor as expected post-SP1/SP2 -- "
    "inspect the file and apply this patch by hand."
)
text = text.replace(
    "from dirtywork.tools import ToolExecutor",
    "from dirtywork import tools\nfrom dirtywork.builtin_tools import default_registry",
)

sandbox_double = '''

class _WorktreeSandbox:
    """Minimal Sandbox double for runner unit tests: dispatches straight to the
    host tool functions against a fixed worktree, independent of HostSandbox's
    real start/finalize/budget lifecycle (irrelevant to these tool-loop tests)."""

    def __init__(self, worktree):
        self.worktree = worktree

    def read_file(self, path, offset, limit):
        return tools.read_file(self.worktree, path, offset, limit)

    def write_file(self, path, content):
        return tools.write_file(self.worktree, path, content)

    def edit_file(self, path, old, new):
        return tools.edit_file(self.worktree, path, old, new)

    def list_dir(self, path):
        return tools.list_dir(self.worktree, path)

    def grep(self, pattern, path, glob, timeout):
        return tools.grep(self.worktree, pattern, path, glob, timeout)

    def bash(self, command, timeout):
        return tools.bash(self.worktree, command, timeout)

'''

marker = "\n@pytest.fixture()\ndef parts(tmp_path: Path):"
assert marker in text, "no 'parts' fixture found at the expected marker -- inspect by hand."
text = text.replace(marker, sandbox_double + marker.lstrip("\n"), 1)

# Rewrite the fixture body (ends at "return wt, executor, transcript, tmp_path")
old_fixture_tail = '''    transcript = Transcript(tmp_path / "t.jsonl")
    executor = ToolExecutor(wt, transcript=transcript)
    return wt, executor, transcript, tmp_path'''
new_fixture_tail = '''    transcript = Transcript(tmp_path / "t.jsonl")
    registry = default_registry(transcript)
    sandbox = _WorktreeSandbox(wt)
    return wt, registry, sandbox, transcript, tmp_path'''
if old_fixture_tail in text:
    text = text.replace(old_fixture_tail, new_fixture_tail, 1)
else:
    print("NOTE: fixture tail did not match the expected pre-SP1/SP2 text verbatim "
          "(SP2 likely changed the ToolExecutor(...) call to pass a sandbox) -- "
          "edit the 'parts' fixture by hand so it ends with:\\n" + new_fixture_tail)

n1 = len(re.findall(r"wt, executor, transcript, (tmp\w*) = parts", text))
text = re.sub(r"wt, executor, transcript, (tmp\w*) = parts",
              r"wt, registry, sandbox, transcript, \1 = parts", text)
n2 = text.count("Runner(client, executor, transcript")
text = text.replace("Runner(client, executor, transcript",
                    "Runner(client, registry, sandbox, transcript")

p.write_text(text)
print(f"rewrote {n1} destructuring site(s), {n2} Runner(...) call site(s)")
PY
```

Run the printed patch script and inspect its output; if it printed the `NOTE:` fallback message, open `tests/test_runner.py`, find the `parts` fixture by hand, and edit its body to exactly:

```python
@pytest.fixture()
def parts(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "f.txt").write_text("data\n")
    transcript = Transcript(tmp_path / "t.jsonl")
    registry = default_registry(transcript)
    sandbox = _WorktreeSandbox(wt)
    return wt, registry, sandbox, transcript, tmp_path
```

- [ ] **Step 9: Run the tool-facing tests**

Run: `python -m pytest tests/test_toolspec.py tests/test_builtin_tools.py tests/test_tools_bash.py tests/test_tools_files.py tests/test_runner.py -q`
Expected: all pass. If `test_runner.py` fails on the fixture, re-check Step 8's fallback instructions.

- [ ] **Step 10: Run the full suite**

Run: `python -m pytest -q`
Expected: all green (host-mode `-m 'not live and not docker'` tests only; Docker/live tests are skipped in this environment as before)

- [ ] **Step 11: Commit**

```bash
git add dirtywork/builtin_tools.py dirtywork/tools.py dirtywork/runner.py dirtywork/__main__.py \
        tests/test_builtin_tools.py tests/test_tools_bash.py tests/test_runner.py
git commit -m "refactor: replace ToolExecutor/TOOL_SCHEMAS with ToolRegistry/builtin_tools"
```

---

### Task 4: `providers/__init__.py` — `ToolCall`, `ChatResponse`, `Provider`, `get_provider`

**Files:**
- Create: `dirtywork/providers/__init__.py`
- Create: `tests/test_providers.py`

**Interfaces:**
- Consumes: nothing yet (`get_provider` lazily imports the concrete adapters created in Task 5/7, so this task never imports them).
- Produces: `ToolCall(id: str, name: str, arguments: dict | None, error: str | None)`; `ChatResponse(text: str, tool_calls: list[ToolCall], finish_reason: str | None, usage: dict)`; `Provider` Protocol (`name: str`; `list_models(self) -> list[str]`; `context_window(self, model: str) -> int | None`; `chat(self, model, history, tools, *, temperature, max_tokens, timeout) -> ChatResponse`); `DEFAULT_BASE_URLS = {"openai": "http://localhost:1234/v1", "anthropic": "https://api.anthropic.com"}`; `get_provider(name: str, base_url: str | None = None, timeout: int = 600) -> Provider`; `assistant_message(text: str | None, tool_calls: list[ToolCall] | None = None) -> dict`; `tool_message(call_id: str, text: str) -> dict`. Neutral history entries are dicts `{"role": "system"|"user"|"assistant"|"tool", "content": str, "tool_calls"?: [ToolCall], "tool_call_id"?: str}`.

- [ ] **Step 1: Write the failing test**

`tests/test_providers.py`:

```python
from __future__ import annotations

import pytest

from dirtywork.providers import (
    DEFAULT_BASE_URLS,
    ChatResponse,
    ToolCall,
    assistant_message,
    get_provider,
    tool_message,
)


def test_default_base_urls():
    assert DEFAULT_BASE_URLS == {
        "openai": "http://localhost:1234/v1",
        "anthropic": "https://api.anthropic.com",
    }


def test_tool_call_dataclass_fields():
    tc = ToolCall(id="c1", name="read_file", arguments={"path": "a.txt"}, error=None)
    assert tc.id == "c1"
    assert tc.name == "read_file"
    assert tc.arguments == {"path": "a.txt"}
    assert tc.error is None


def test_chat_response_dataclass_fields():
    tc = ToolCall(id="c1", name="read_file", arguments={}, error=None)
    resp = ChatResponse(text="hi", tool_calls=[tc], finish_reason="stop",
                        usage={"prompt_tokens": 1, "completion_tokens": 1})
    assert resp.text == "hi"
    assert resp.tool_calls == [tc]
    assert resp.finish_reason == "stop"
    assert resp.usage == {"prompt_tokens": 1, "completion_tokens": 1}


def test_assistant_message_without_tool_calls():
    msg = assistant_message("hello", None)
    assert msg == {"role": "assistant", "content": "hello"}


def test_assistant_message_with_tool_calls():
    tc = ToolCall(id="c1", name="read_file", arguments={"path": "a.txt"}, error=None)
    msg = assistant_message(None, [tc])
    assert msg["role"] == "assistant"
    assert msg["content"] == ""
    assert msg["tool_calls"] == [tc]


def test_tool_message():
    msg = tool_message("c1", "file contents")
    assert msg == {"role": "tool", "content": "file contents", "tool_call_id": "c1"}


def test_get_provider_unknown_raises():
    with pytest.raises(ValueError, match="unknown provider 'bogus'"):
        get_provider("bogus")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_providers.py -q`
Expected: `ModuleNotFoundError: No module named 'dirtywork.providers'`

- [ ] **Step 3: Write the minimal implementation**

`dirtywork/providers/__init__.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

DEFAULT_BASE_URLS = {
    "openai": "http://localhost:1234/v1",
    "anthropic": "https://api.anthropic.com",
}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict | None
    error: str | None


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


def assistant_message(text: str | None, tool_calls: list | None = None) -> dict:
    msg = {"role": "assistant", "content": text or ""}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def tool_message(call_id: str, text: str) -> dict:
    return {"role": "tool", "content": text, "tool_call_id": call_id}


def get_provider(name: str, base_url: str | None = None, timeout: int = 600) -> Provider:
    url = base_url or DEFAULT_BASE_URLS.get(name)
    if name == "openai":
        from .openai_compat import OpenAICompatClient
        return OpenAICompatClient(base_url=url, timeout=timeout)
    if name == "anthropic":
        from .anthropic import AnthropicClient
        return AnthropicClient(base_url=url, timeout=timeout)
    raise ValueError(f"unknown provider '{name}'. Available: openai, anthropic.")
```

`dirtywork/providers` needs no separate `__init__.py`-adjacent files yet — `openai_compat.py`/`anthropic.py` are created in Task 5/7; `get_provider`'s imports of them are deferred (inside the `if` branches) precisely so this module imports cleanly before either exists.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_providers.py -q`
Expected: 7 passed

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add dirtywork/providers/__init__.py tests/test_providers.py
git commit -m "feat: add Provider protocol, ToolCall/ChatResponse, get_provider"
```

---

### Task 5: Provider contract suite + OpenAI fixtures + `OpenAICompatClient`

**Files:**
- Create: `tests/provider_contract.py`
- Create: `tests/fixtures/providers/openai/*.json` (8 files)
- Create: `tests/test_provider_openai.py`
- Create: `dirtywork/providers/openai_compat.py`
- Modify: `dirtywork/llm.py` (extract `http_json`; `LMStudioClient = OpenAICompatClient` alias)
- Modify: `tests/test_llm.py` (retarget at `http_json` directly; the old `LMStudioClient.chat()`-returns-a-dict tests move to `test_provider_openai.py`, since `chat()` now returns `ChatResponse`)

**Interfaces:**
- Consumes: `dirtywork.llm.{LLMError, LLMTimeout, http_json}`; `dirtywork.providers.{ChatResponse, ToolCall}`.
- Produces: `OpenAICompatClient(base_url=..., timeout=600, *, http_json=http_json)` implementing `Provider` (`name = "openai"`); `dirtywork.llm.http_json(url, payload, headers, timeout, *, method="POST") -> dict`; `tests.provider_contract.ProviderContract` (mixin: `fixtures_dir`, `make_client(transport)`, `_system_text(payload)`, `_tool_result_entries(payload)` hooks; seven `test_*` methods every adapter inherits) and `RecordingTransport` (fake `http_json` callable recording every call).

- [ ] **Step 1: Write the failing tests and fixtures**

Create the eight OpenAI wire-shape fixtures.

`tests/fixtures/providers/openai/simple_ok.json`:

```json
{"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}
```

`tests/fixtures/providers/openai/parallel_tool_calls.json`:

```json
{
  "choices": [{
    "message": {
      "role": "assistant", "content": null,
      "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"a.txt\"}"}},
        {"id": "call_2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"b.txt\"}"}}
      ]
    },
    "finish_reason": "tool_calls"
  }],
  "usage": {"prompt_tokens": 12, "completion_tokens": 8}
}
```

`tests/fixtures/providers/openai/malformed_tool_call.json`:

```json
{
  "choices": [{
    "message": {
      "role": "assistant", "content": null,
      "tool_calls": [
        {"id": "call_ok", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}},
        {"id": "call_bad", "type": "function"}
      ]
    },
    "finish_reason": "tool_calls"
  }],
  "usage": {"prompt_tokens": 10, "completion_tokens": 6}
}
```

`tests/fixtures/providers/openai/bad_json_arguments.json`:

```json
{
  "choices": [{
    "message": {
      "role": "assistant", "content": null,
      "tool_calls": [
        {"id": "call_badargs", "type": "function", "function": {"name": "write_file", "arguments": "{\"path\": \"x\", \"content\": \"abc"}}
      ]
    },
    "finish_reason": "length"
  }],
  "usage": {"prompt_tokens": 4, "completion_tokens": 2}
}
```

`tests/fixtures/providers/openai/finish_reason_stop.json`:

```json
{"choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
```

`tests/fixtures/providers/openai/finish_reason_length_text.json`:

```json
{"choices": [{"message": {"role": "assistant", "content": "cut off mid"}, "finish_reason": "length"}], "usage": {"prompt_tokens": 2, "completion_tokens": 2}}
```

`tests/fixtures/providers/openai/usage_missing.json`:

```json
{"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
```

`tests/fixtures/providers/openai/usage_nan_negative.json`:

```json
{"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": NaN, "completion_tokens": -5}}
```

`tests/provider_contract.py` (no `test_` prefix — imported by adapter test files, never collected on its own):

```python
from __future__ import annotations

import json
from pathlib import Path

from dirtywork.providers import ToolCall, assistant_message, tool_message


class RecordingTransport:
    """Fake http_json: returns canned fixture bodies in call order and records
    every (url, payload, headers, timeout, method) it was called with."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, payload, headers, timeout, *, method="POST"):
        self.calls.append({"url": url, "payload": payload, "headers": headers,
                            "timeout": timeout, "method": method})
        return self.responses.pop(0)


class ProviderContract:
    """Shared behavioral contract every Provider adapter must satisfy. Subclass,
    set ``fixtures_dir`` to the adapter's own fixture directory, and implement
    ``make_client``/``_system_text``/``_tool_result_entries``."""

    fixtures_dir: Path

    def make_client(self, transport):
        raise NotImplementedError

    def _system_text(self, payload: dict):
        raise NotImplementedError

    def _tool_result_entries(self, payload: dict) -> list:
        raise NotImplementedError

    def _load(self, name: str) -> dict:
        return json.loads((self.fixtures_dir / name).read_text())

    def test_system_prompt_lands_where_wire_expects(self):
        transport = RecordingTransport([self._load("simple_ok.json")])
        client = self.make_client(transport)
        history = [
            {"role": "system", "content": "SYS PROMPT TEXT"},
            {"role": "user", "content": "hi"},
        ]
        client.chat("model-x", history, [], temperature=None, max_tokens=100, timeout=30)
        assert self._system_text(transport.calls[0]["payload"]) == "SYS PROMPT TEXT"

    def test_parallel_tool_calls_parse_in_order(self):
        transport = RecordingTransport([self._load("parallel_tool_calls.json")])
        client = self.make_client(transport)
        history = [{"role": "user", "content": "read two files"}]
        resp = client.chat("model-x", history, [], temperature=None, max_tokens=100, timeout=30)
        assert len(resp.tool_calls) == 2
        assert resp.tool_calls[0].name == "read_file"
        assert resp.tool_calls[0].arguments == {"path": "a.txt"}
        assert resp.tool_calls[1].name == "read_file"
        assert resp.tool_calls[1].arguments == {"path": "b.txt"}
        assert resp.tool_calls[0].id != resp.tool_calls[1].id
        assert resp.finish_reason == "tool_calls"

    def test_malformed_tool_call_sets_error_others_intact(self):
        transport = RecordingTransport([self._load("malformed_tool_call.json")])
        client = self.make_client(transport)
        history = [{"role": "user", "content": "do two things"}]
        resp = client.chat("model-x", history, [], temperature=None, max_tokens=100, timeout=30)
        assert len(resp.tool_calls) == 2
        ok_calls = [tc for tc in resp.tool_calls if tc.error is None]
        bad_calls = [tc for tc in resp.tool_calls if tc.error is not None]
        assert len(ok_calls) == 1 and ok_calls[0].name == "list_dir"
        assert len(bad_calls) == 1
        assert "malformed" in bad_calls[0].error.lower()

    def test_tool_results_serialize_in_order_with_ids(self):
        transport = RecordingTransport([self._load("simple_ok.json")])
        client = self.make_client(transport)
        tool_calls = [
            ToolCall(id="call_1", name="read_file", arguments={"path": "a.txt"}, error=None),
            ToolCall(id="call_2", name="read_file", arguments={"path": "b.txt"}, error=None),
        ]
        history = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "read two files"},
            assistant_message(None, tool_calls),
            tool_message("call_1", "contents of a"),
            tool_message("call_2", "contents of b"),
        ]
        client.chat("model-x", history, [], temperature=None, max_tokens=100, timeout=30)
        entries = self._tool_result_entries(transport.calls[0]["payload"])
        assert entries == [("call_1", "contents of a"), ("call_2", "contents of b")]

    def test_finish_reason_mapping(self):
        cases = [
            ("finish_reason_stop.json", "stop"),
            ("parallel_tool_calls.json", "tool_calls"),
            ("finish_reason_length_text.json", "length"),
        ]
        for fixture, expected in cases:
            transport = RecordingTransport([self._load(fixture)])
            client = self.make_client(transport)
            resp = client.chat("model-x", [{"role": "user", "content": "hi"}], [],
                               temperature=None, max_tokens=100, timeout=30)
            assert resp.finish_reason == expected, f"{fixture}: expected {expected}, got {resp.finish_reason}"

    def test_usage_normalization(self):
        transport = RecordingTransport([self._load("usage_missing.json")])
        client = self.make_client(transport)
        resp = client.chat("model-x", [{"role": "user", "content": "hi"}], [],
                           temperature=None, max_tokens=100, timeout=30)
        assert resp.usage == {"prompt_tokens": 0, "completion_tokens": 0}

        transport = RecordingTransport([self._load("usage_nan_negative.json")])
        client = self.make_client(transport)
        resp = client.chat("model-x", [{"role": "user", "content": "hi"}], [],
                           temperature=None, max_tokens=100, timeout=30)
        assert resp.usage == {"prompt_tokens": 0, "completion_tokens": 0}

    def test_max_tokens_cutoff_mid_call(self):
        transport = RecordingTransport([self._load("bad_json_arguments.json")])
        client = self.make_client(transport)
        resp = client.chat("model-x", [{"role": "user", "content": "write a big file"}], [],
                           temperature=None, max_tokens=10, timeout=30)
        assert resp.finish_reason == "length"
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].error is not None
```

`tests/test_provider_openai.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from dirtywork.providers.openai_compat import OpenAICompatClient

from .provider_contract import ProviderContract, RecordingTransport

FIXTURES = Path(__file__).parent / "fixtures" / "providers" / "openai"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


class TestOpenAIProviderContract(ProviderContract):
    fixtures_dir = FIXTURES

    def make_client(self, transport):
        return OpenAICompatClient(base_url="http://fake/v1", http_json=transport)

    def _system_text(self, payload):
        for m in payload["messages"]:
            if m["role"] == "system":
                return m["content"]
        return None

    def _tool_result_entries(self, payload):
        return [(m["tool_call_id"], m["content"])
                for m in payload["messages"] if m["role"] == "tool"]


def test_chat_omits_tools_when_empty():
    transport = RecordingTransport([_fixture("simple_ok.json")])
    client = OpenAICompatClient(base_url="http://fake/v1", http_json=transport)
    client.chat("model-x", [{"role": "user", "content": "hi"}], [],
               temperature=None, max_tokens=100, timeout=30)
    assert "tools" not in transport.calls[0]["payload"]


def test_chat_includes_tools_when_nonempty():
    transport = RecordingTransport([_fixture("simple_ok.json")])
    client = OpenAICompatClient(base_url="http://fake/v1", http_json=transport)
    tools = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object", "properties": {}}}}]
    client.chat("model-x", [{"role": "user", "content": "hi"}], tools,
               temperature=None, max_tokens=100, timeout=30)
    assert transport.calls[0]["payload"]["tools"] == tools


def test_chat_temperature_omitted_when_none():
    transport = RecordingTransport([_fixture("simple_ok.json")])
    client = OpenAICompatClient(base_url="http://fake/v1", http_json=transport)
    client.chat("model-x", [{"role": "user", "content": "hi"}], [],
               temperature=None, max_tokens=100, timeout=30)
    assert "temperature" not in transport.calls[0]["payload"]


def test_chat_temperature_included_when_set():
    transport = RecordingTransport([_fixture("simple_ok.json")])
    client = OpenAICompatClient(base_url="http://fake/v1", http_json=transport)
    client.chat("model-x", [{"role": "user", "content": "hi"}], [],
               temperature=0.2, max_tokens=100, timeout=30)
    assert transport.calls[0]["payload"]["temperature"] == 0.2


def test_list_models_returns_ids():
    transport = RecordingTransport([{"data": [{"id": "m1"}, {"id": "m2"}]}])
    client = OpenAICompatClient(base_url="http://fake/v1", http_json=transport)
    assert client.list_models() == ["m1", "m2"]
    assert transport.calls[0]["method"] == "GET"


def test_context_window_known_and_unknown_model():
    client = OpenAICompatClient(base_url="http://fake/v1", http_json=RecordingTransport([]))
    assert client.context_window("qwen/qwen3-coder-next") == 65536
    assert client.context_window("nonexistent/model") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_provider_openai.py -q`
Expected: `ModuleNotFoundError: No module named 'dirtywork.providers.openai_compat'`

- [ ] **Step 3: Extract `http_json` in `dirtywork/llm.py`**

Overwrite `dirtywork/llm.py` in full:

```python
from __future__ import annotations

import http.client
import json
import socket
import time
import urllib.error
import urllib.request

# urllib's timeout is per-socket-op, not a whole-transfer deadline, and resp.read()
# is unbounded — so a hostile/buggy endpoint could drip-feed a response for far
# longer than the run's timeout, or return a giant body that exhausts memory.
MAX_RESPONSE_BYTES = 64 * 1024 * 1024


def _underlying_socket(resp):
    """Best-effort access to a urlopen response's raw socket (CPython) so its
    timeout can be tightened per read; None if the internals differ."""
    raw = getattr(getattr(resp, "fp", None), "raw", None)
    return getattr(raw, "_sock", None)


class LLMError(Exception):
    """Raised when a model-serving endpoint is unreachable or returns garbage."""


class LLMTimeout(LLMError):
    """Raised when a request to a model-serving endpoint times out."""


def http_json(url: str, payload, headers: dict, timeout: float, *, method: str = "POST") -> dict:
    """Bounded stdlib HTTP JSON request shared by every Provider adapter:
    whole-transfer wall-clock deadline (not urllib's per-socket-op timeout),
    a MAX_RESPONSE_BYTES cap, and every failure mode raised as LLMError or
    LLMTimeout. ``payload=None`` sends no request body (for GET); ``method``
    overrides the HTTP verb (default POST)."""
    try:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
    except (ValueError, TypeError) as e:
        raise LLMError(f"invalid request for {url!r}: {e}")
    deadline = time.monotonic() + timeout
    resp = None
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        # urllib's timeout is per-socket-op and resp.read() refills across many
        # recvs, so a drip-fed body could outlast the deadline. Read one recv at
        # a time (read1 returns available bytes) with the socket timeout tightened
        # to the REMAINING wall-clock budget before each read — a hard bound.
        sock = _underlying_socket(resp)
        body = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LLMTimeout(f"request to {url} exceeded {timeout}s")
            if sock is not None:
                sock.settimeout(remaining)
            try:
                chunk = resp.read1(65536)
            except socket.timeout:
                raise LLMTimeout(f"request to {url} exceeded {timeout}s")
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise LLMError(f"response from {url} exceeds {MAX_RESPONSE_BYTES} bytes")
        body = bytes(body)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read(500)
        except Exception:
            detail = b"<unreadable error body>"
        raise LLMError(f"HTTP {e.code} on {url}: {detail!r}")
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
        if isinstance(e, socket.timeout) or isinstance(getattr(e, "reason", None), socket.timeout):
            raise LLMTimeout(f"request to {url} timed out after {timeout}s")
        raise LLMError(f"cannot reach {url}: {e}")
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
    try:
        return json.loads(body.decode())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise LLMError(f"invalid JSON from {url}: {e}")


# LMStudioClient moved to dirtywork.providers.openai_compat.OpenAICompatClient in
# 0.3 (SP3 extensibility). This alias is kept for one release as a deprecation
# bridge -- new code should import OpenAICompatClient directly, or call
# dirtywork.providers.get_provider("openai", ...). The import sits at the
# BOTTOM of this module, not the top: providers.openai_compat imports
# LLMError/http_json back from here, and by the time this line executes those
# names are already bound on this module's namespace, so the circular import
# resolves cleanly. Do not move this import above them.
from .providers.openai_compat import OpenAICompatClient  # noqa: E402

LMStudioClient = OpenAICompatClient
```

- [ ] **Step 4: Implement `dirtywork/providers/openai_compat.py`**

```python
from __future__ import annotations

import json
import math

from . import ChatResponse, ToolCall
from ..llm import LLMError, http_json

DEFAULT_BASE_URL = "http://localhost:1234/v1"

CONTEXT_WINDOWS = {
    "qwen/qwen3-coder-next": 65536,
    "mistralai/devstral-small-2-2512": 32768,
}


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
            out.append(ToolCall(id="", name="", arguments=None,
                                 error="malformed tool call entry (missing or invalid id/function fields)"))
            continue
        fn = tc["function"]
        raw_args = fn.get("arguments") or "{}"
        try:
            parsed = json.loads(raw_args)
            if not isinstance(parsed, dict):
                raise ValueError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            out.append(ToolCall(id=tc["id"], name=fn["name"], arguments=None,
                                 error=f"malformed tool arguments: {e}"))
            continue
        out.append(ToolCall(id=tc["id"], name=fn["name"], arguments=parsed, error=None))
    return out


def _to_wire_tool_call(tc) -> dict:
    return {"id": tc.id, "type": "function",
            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments or {})}}


def _to_openai_messages(history: list) -> list:
    messages = []
    for m in history:
        role = m["role"]
        if role == "assistant":
            msg = {"role": "assistant", "content": m.get("content") or ""}
            tool_calls = m.get("tool_calls")
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
    raw = raw or {}
    for k in usage:
        # usage is server-controlled: NaN/Infinity would survive json.loads and
        # later emit invalid JSON on our stdout/transcript contract. Accept only
        # finite, non-negative numbers.
        v = raw.get(k, 0)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0:
            usage[k] = int(v)
    return usage


class OpenAICompatClient:
    name = "openai"

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = 600, *, http_json=http_json):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._http_json = http_json

    def list_models(self) -> list:
        body = self._http_json(f"{self.base_url}/models", None,
                               {"Content-Type": "application/json"}, self.timeout, method="GET")
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

    def chat(self, model, history, tools, *, temperature=None, max_tokens=4096, timeout=None) -> ChatResponse:
        payload = {"model": model, "messages": _to_openai_messages(history), "max_tokens": max_tokens}
        if tools:
            payload["tools"] = tools
        if temperature is not None:
            payload["temperature"] = temperature
        effective_timeout = timeout if timeout is not None else self.timeout
        body = self._http_json(f"{self.base_url}/chat/completions", payload,
                               {"Content-Type": "application/json"}, effective_timeout)
        try:
            msg = body["choices"][0]["message"]
            if not isinstance(msg, dict):
                raise TypeError("message is not an object")
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"malformed response from server (no choices[0].message): {e}")
        finish_reason = body["choices"][0].get("finish_reason")
        raw_calls = msg.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raw_calls = []
        tool_calls = _parse_tool_calls(raw_calls)
        usage = _sanitize_usage(body.get("usage"))
        return ChatResponse(text=msg.get("content") or "", tool_calls=tool_calls,
                            finish_reason=finish_reason, usage=usage)
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `python -m pytest tests/test_provider_openai.py -q`
Expected: 13 passed (6 explicit + 7 inherited from `ProviderContract`)

- [ ] **Step 6: Confirm the old `test_llm.py` now fails, then rewrite it**

Run: `python -m pytest tests/test_llm.py -q`
Expected: FAIL — `LMStudioClient.chat()` now returns a `ChatResponse`, not a dict, so tests like `test_chat_payload_and_response` (`resp["choices"][0]...`) raise `TypeError: 'ChatResponse' object is not subscriptable`.

Overwrite `tests/test_llm.py` in full — it now tests `http_json` directly (the payload/temperature/tools-shape assertions that used to go through `LMStudioClient` now live in `test_provider_openai.py`, Step 1 above):

```python
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from dirtywork.llm import LLMError, LLMTimeout, LMStudioClient, http_json
from dirtywork.providers.openai_compat import OpenAICompatClient


class _FakeServer(BaseHTTPRequestHandler):
    last_payload: dict = {}
    get_body: object = {"ok": True}

    def do_GET(self):
        body = json.dumps(_FakeServer.get_body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        _FakeServer.last_payload = json.loads(self.rfile.read(length))
        body = json.dumps({"echo": _FakeServer.last_payload}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silence test output
        pass


@pytest.fixture()
def server():
    _FakeServer.get_body = {"ok": True}
    srv = HTTPServer(("127.0.0.1", 0), _FakeServer)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


class _SlowServer(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.rfile.read(length)
        time.sleep(2)
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture()
def slow_server():
    srv = HTTPServer(("127.0.0.1", 0), _SlowServer)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


class _DripServer(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        # Valid body delivered one byte at a time — each write lands within the
        # per-socket timeout, so only a whole-transfer deadline can stop it.
        body = json.dumps({"ok": True}).encode()
        try:
            for b in body:
                self.wfile.write(bytes([b]))
                self.wfile.flush()
                time.sleep(0.05)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def log_message(self, *a):
        pass


@pytest.fixture()
def drip_server():
    srv = HTTPServer(("127.0.0.1", 0), _DripServer)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_http_json_get(server: str):
    body = http_json(f"{server}/models", None, {"Content-Type": "application/json"}, 5, method="GET")
    assert body == {"ok": True}


def test_http_json_post_roundtrip(server: str):
    body = http_json(f"{server}/x", {"a": 1}, {"Content-Type": "application/json"}, 5)
    assert body == {"echo": {"a": 1}}


def test_http_json_connection_error_raises_llmerror():
    with pytest.raises(LLMError):
        http_json("http://127.0.0.1:1/x", {}, {"Content-Type": "application/json"}, 2)


def test_http_json_empty_url_raises_llmerror():
    # urllib.request.Request('') raises ValueError ("unknown url type") outside
    # any try in the pre-fix code, escaping the LLMError-only contract.
    with pytest.raises(LLMError):
        http_json("", {}, {"Content-Type": "application/json"}, 2)


def test_http_json_unparseable_url_raises_llmerror():
    with pytest.raises(LLMError):
        http_json("not-a-url", {}, {"Content-Type": "application/json"}, 2)


def test_http_json_timeout_raises_llmtimeout(slow_server: str):
    with pytest.raises(LLMTimeout) as exc_info:
        http_json(f"{slow_server}/x", {}, {"Content-Type": "application/json"}, 0.5)
    assert isinstance(exc_info.value, LLMError)


def test_http_json_drip_feed_hits_wallclock_deadline(drip_server: str):
    start = time.monotonic()
    with pytest.raises(LLMTimeout):
        http_json(f"{drip_server}/x", {}, {"Content-Type": "application/json"}, 0.5)
    assert time.monotonic() - start < 2.0  # hard deadline ~0.5s, not the full ~2s drip


def test_http_json_oversized_response_raises_llmerror(server: str, monkeypatch):
    import dirtywork.llm as llm_mod
    monkeypatch.setattr(llm_mod, "MAX_RESPONSE_BYTES", 10)
    with pytest.raises(LLMError):
        http_json(f"{server}/x", {"a": "b" * 100}, {"Content-Type": "application/json"}, 5)


def test_lmstudio_client_is_openai_compat_client_alias():
    assert LMStudioClient is OpenAICompatClient
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_llm.py tests/test_provider_openai.py tests/test_providers.py -q`
Expected: all pass

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest -q`
Expected: all green

- [ ] **Step 9: Commit**

```bash
git add dirtywork/llm.py dirtywork/providers/openai_compat.py tests/provider_contract.py \
        tests/fixtures/providers/openai tests/test_provider_openai.py tests/test_llm.py
git commit -m "feat: extract http_json, add provider contract suite and OpenAICompatClient"
```

---

### Task 6: Runner on neutral history + `Provider`/`ChatResponse`; CLI `--provider`/`--base-url`/`--context-window`

**Files:**
- Modify: `dirtywork/runner.py` (full rewrite: provider-neutral history, `ChatResponse`-driven loop, per-provider context window)
- Modify: `dirtywork/__main__.py` (targeted patch: `client`/`LMStudioClient` → `provider`/`get_provider`; new flags)
- Modify: `tests/test_runner.py` (full rewrite: `FakeClient` → `FakeProvider` returning `ChatResponse`)
- Modify: `tests/test_main.py` (targeted patch: provider-aware preflight test)

**Interfaces:**
- Consumes: `dirtywork.providers.{Provider, ChatResponse, ToolCall, DEFAULT_BASE_URLS, get_provider, assistant_message, tool_message}`; `dirtywork.llm.{LLMError, LLMTimeout}`.
- Produces: `Runner(provider, registry, sandbox, transcript, model, max_turns=40, timeout=1800, temperature=None, run_info=None, finalize=None, context_window=None)`; `Runner.char_budget` derived from `context_window or provider.context_window(model) or DEFAULT_WINDOW`.

- [ ] **Step 1: Write the failing tests**

Overwrite `tests/test_runner.py` in full. The `FakeClient`/raw-OpenAI-dict helpers are replaced by a `FakeProvider` returning `ChatResponse` directly; the `_WorktreeSandbox` double from Task 3 is unchanged and kept. Tests that exercised wire-level malformed-tool-call *parsing* (structurally invalid OpenAI dicts, JSON-undecodable arguments) move to `tests/provider_contract.py`/`tests/test_provider_openai.py` (Task 5) since that parsing now lives in the provider adapter, not the runner — the runner's job is now just to branch on `ToolCall.error`:

```python
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from dirtywork import tools
from dirtywork.budget import BudgetExceeded
from dirtywork.builtin_tools import default_registry
from dirtywork.llm import LLMError, LLMTimeout
from dirtywork.providers import ChatResponse, ToolCall
from dirtywork.runner import DEFAULT_WINDOW, TRIM_MARKER, RunResult, Runner, trim_messages
from dirtywork.sandbox import SandboxError
from dirtywork.transcript import Transcript


def _resp(content=None, tool_calls=None, finish_reason=None, usage=None):
    return ChatResponse(
        text=content or "",
        tool_calls=tool_calls or [],
        finish_reason=finish_reason,
        usage=usage or {"prompt_tokens": 10, "completion_tokens": 5},
    )


def _call(call_id, name, args: dict):
    return ToolCall(id=call_id, name=name, arguments=args, error=None)


class FakeProvider:
    name = "fake"

    def __init__(self, responses, context_window=None):
        self.responses = list(responses)
        self.requests = []
        self.timeouts = []
        self._context_window = context_window

    def list_models(self):
        return ["m"]

    def context_window(self, model):
        return self._context_window

    def chat(self, model, history, tools, *, temperature=None, max_tokens=4096, timeout=None):
        self.requests.append([dict(m) for m in history])
        self.timeouts.append(timeout)
        return self.responses.pop(0)


class ExplodingProvider:
    """Raises on chat() -- used for LLMError/LLMTimeout status-mapping tests."""

    name = "fake"

    def __init__(self, exc):
        self.exc = exc

    def list_models(self):
        return ["m"]

    def context_window(self, model):
        return None

    def chat(self, *a, **k):
        raise self.exc


class _WorktreeSandbox:
    """Minimal Sandbox double: dispatches straight to the host tool functions
    against a fixed worktree, independent of HostSandbox's real lifecycle."""

    def __init__(self, worktree):
        self.worktree = worktree

    def read_file(self, path, offset, limit):
        return tools.read_file(self.worktree, path, offset, limit)

    def write_file(self, path, content):
        return tools.write_file(self.worktree, path, content)

    def edit_file(self, path, old, new):
        return tools.edit_file(self.worktree, path, old, new)

    def list_dir(self, path):
        return tools.list_dir(self.worktree, path)

    def grep(self, pattern, path, glob, timeout):
        return tools.grep(self.worktree, pattern, path, glob, timeout)

    def bash(self, command, timeout):
        return tools.bash(self.worktree, command, timeout)


class _ExplodingSandbox(_WorktreeSandbox):
    """Raises a fixed exception from every tool call -- for BudgetExceeded/
    SandboxError status-mapping tests."""

    def __init__(self, worktree, exc):
        super().__init__(worktree)
        self.exc = exc

    def bash(self, command, timeout):
        raise self.exc


@pytest.fixture()
def parts(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "f.txt").write_text("data\n")
    transcript = Transcript(tmp_path / "t.jsonl")
    registry = default_registry(transcript)
    sandbox = _WorktreeSandbox(wt)
    return wt, registry, sandbox, transcript, tmp_path


def _events(tmp_path: Path):
    return [json.loads(l) for l in (tmp_path / "t.jsonl").read_text().splitlines()]


def test_two_turn_run(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("c1", "read_file", {"path": "f.txt"})]),
        _resp(content="Done: file says data"),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="qwen/qwen3-coder-next")
    result = r.run("sysprompt", "read the file")
    transcript.close()

    assert result.status == "completed"
    assert result.turns == 2
    assert "Done" in result.final_message
    assert result.usage == {"prompt_tokens": 20, "completion_tokens": 10}

    second = provider.requests[1]
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["tool_call_id"] == "c1"
    assert "data" in tool_msgs[0]["content"]

    kinds = [e["event"] for e in _events(tmp)]
    assert kinds[0] == "run_start" and kinds[-1] == "run_end"
    assert "assistant" in kinds and "tool_result" in kinds
    run_start = next(e for e in _events(tmp) if e["event"] == "run_start")
    assert run_start["schema_version"] == 2


def test_max_turns(parts):
    wt, registry, sandbox, transcript, tmp = parts
    loop_resp = _resp(tool_calls=[_call("c", "list_dir", {"path": "."})])
    provider = FakeProvider([loop_resp] * 3)
    r = Runner(provider, registry, sandbox, transcript, model="m", max_turns=3)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "max_turns"
    assert result.turns == 3


def test_unknown_tool_counts_as_strike_but_recovers(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("c1", "no_such_tool", {})]),
        _resp(content="ok done"),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    second = provider.requests[1]
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert "unknown tool" in tool_msgs[0]["content"].lower()


def test_tool_call_error_counts_as_strike_and_recovers(parts):
    wt, registry, sandbox, transcript, tmp = parts
    bad = ToolCall(id="c1", name="read_file", arguments=None, error="malformed tool arguments: bad json")
    provider = FakeProvider([
        _resp(tool_calls=[bad]),
        _resp(content="ok done"),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    second = provider.requests[1]
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert "malformed tool arguments" in tool_msgs[0]["content"]


def test_three_consecutive_tool_call_errors_aborts(parts):
    wt, registry, sandbox, transcript, tmp = parts
    bad = ToolCall(id="c", name="read_file", arguments=None, error="bad")
    provider = FakeProvider([_resp(tool_calls=[bad]), _resp(tool_calls=[bad]), _resp(tool_calls=[bad])])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"


def test_length_finish_reason_gives_helpful_hint(parts):
    wt, registry, sandbox, transcript, tmp = parts
    bad = ToolCall(id="c", name="write_file", arguments=None, error="malformed tool arguments: unterminated string")
    truncated = _resp(tool_calls=[bad], finish_reason="length",
                      usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([truncated, _resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    tool_msgs = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert "cut off at the token limit" in tool_msgs[0]["content"]


def test_trim_messages():
    msgs = [
        {"role": "system", "content": "s" * 100},
        {"role": "tool", "tool_call_id": "1", "content": "x" * 1000},
        {"role": "assistant", "content": "a" * 100},
        {"role": "tool", "tool_call_id": "2", "content": "y" * 1000},
    ]
    fits = trim_messages(msgs, char_budget=1300)
    assert fits
    assert msgs[1]["content"] == TRIM_MARKER      # oldest trimmed first
    assert msgs[3]["content"] == "y" * 1000        # newer kept
    assert msgs[0]["content"] == "s" * 100         # system never trimmed


def test_trim_cannot_fit():
    msgs = [{"role": "system", "content": "s" * 5000}]
    assert trim_messages(msgs, char_budget=100) is False


def test_trim_counts_tool_call_arguments():
    tc = ToolCall(id="1", name="write_file", arguments={"content": "a" * 1000}, error=None)
    msgs = [{"role": "assistant", "content": "", "tool_calls": [tc]}]
    # No role=="tool" messages exist to trim, so this only passes if the
    # ToolCall arguments are counted toward the budget in the first place.
    assert trim_messages(msgs, char_budget=500) is False


def test_context_exhausted_status(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    r.char_budget = 10
    result = r.run("s" * 100, "t")
    transcript.close()
    assert result.status == "context_exhausted"


def test_run_start_includes_run_info(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m", run_info={"repo": "/r"})
    result = r.run("s", "t")
    transcript.close()
    events = _events(tmp)
    run_start = next(e for e in events if e["event"] == "run_start")
    assert run_start["repo"] == "/r"


def test_chat_receives_bounded_timeout(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m", timeout=30)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert provider.timeouts and provider.timeouts[0] is not None
    assert 0 < provider.timeouts[0] <= 30


def test_llm_timeout_near_deadline_gives_timeout_status(parts):
    wt, registry, sandbox, transcript, tmp = parts

    class SlowTimeoutProvider(ExplodingProvider):
        def chat(self, *a, **k):
            time.sleep(0.3)
            raise LLMTimeout("request timed out")

    r = Runner(SlowTimeoutProvider(None), registry, sandbox, transcript, model="m", timeout=0.2)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "timeout"


def test_llm_timeout_far_from_deadline_gives_model_error(parts):
    wt, registry, sandbox, transcript, tmp = parts
    r = Runner(ExplodingProvider(LLMTimeout("timed out")), registry, sandbox, transcript,
              model="m", timeout=1800)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"


def test_llm_error_from_provider_is_model_error(parts):
    wt, registry, sandbox, transcript, tmp = parts
    r = Runner(ExplodingProvider(LLMError("malformed response from server")),
              registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"
    assert "malformed response" in result.final_message


def test_strike_counter_resets_on_success(parts):
    wt, registry, sandbox, transcript, tmp = parts
    bad = ToolCall(id="x", name="read_file", arguments=None, error="bad")
    good = _call("g", "list_dir", {"path": "."})
    provider = FakeProvider([
        _resp(tool_calls=[bad]), _resp(tool_calls=[bad]),
        _resp(tool_calls=[good]),
        _resp(tool_calls=[bad]), _resp(tool_calls=[bad]),
        _resp(content="done"),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"


def test_timeout_status(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="never reached")])
    r = Runner(provider, registry, sandbox, transcript, model="m", timeout=-1)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "timeout"
    assert result.turns == 0


def test_interrupted_status(parts):
    wt, registry, sandbox, transcript, tmp = parts
    class InterruptingProvider(ExplodingProvider):
        def chat(self, *a, **k):
            raise KeyboardInterrupt
    r = Runner(InterruptingProvider(None), registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "interrupted"
    events = _events(tmp)
    assert events[-1]["event"] == "run_end"


def test_usage_accumulates_across_turns(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("c1", "list_dir", {"path": "."})],
             usage={"prompt_tokens": 5, "completion_tokens": 2}),
        _resp(content="done", usage={"prompt_tokens": 7, "completion_tokens": 3}),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.usage == {"prompt_tokens": 12, "completion_tokens": 5}


def test_context_window_override(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="done")], context_window=65536)
    r = Runner(provider, registry, sandbox, transcript, model="m", context_window=1000)
    assert r.char_budget == int(1000 * 0.75 * 4)


def test_context_window_falls_back_to_provider_then_default(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="done")], context_window=65536)
    r = Runner(provider, registry, sandbox, transcript, model="m")
    assert r.char_budget == int(65536 * 0.75 * 4)

    provider2 = FakeProvider([_resp(content="done")], context_window=None)
    r2 = Runner(provider2, registry, sandbox, transcript, model="m")
    assert r2.char_budget == int(DEFAULT_WINDOW * 0.75 * 4)


def test_finalize_hook_merges_into_run_end_and_extra(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m",
              finalize=lambda: {"diff_stat": "1 file changed"})
    result = r.run("s", "t")
    transcript.close()
    assert result.extra == {"diff_stat": "1 file changed"}
    run_end = next(e for e in _events(tmp) if e["event"] == "run_end")
    assert run_end["diff_stat"] == "1 file changed"


def test_finalize_exception_recorded_not_raised(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="done")])
    def boom():
        raise RuntimeError("disk full")
    r = Runner(provider, registry, sandbox, transcript, model="m", finalize=boom)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert "disk full" in result.extra["finalize_error"]


def test_budget_exceeded_status(parts):
    wt, registry, sandbox, transcript, tmp = parts
    exploding = _ExplodingSandbox(wt, BudgetExceeded("worktree exceeds 2048 MB"))
    exploding.exc.reason = "worktree exceeds 2048 MB"
    provider = FakeProvider([_resp(tool_calls=[_call("c1", "bash", {"command": "true"})])])
    r = Runner(provider, registry, exploding, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "budget_exceeded"
    assert result.final_message == "worktree exceeds 2048 MB"


def test_sandbox_error_status(parts):
    wt, registry, sandbox, transcript, tmp = parts
    exploding = _ExplodingSandbox(wt, SandboxError("docker exec timed out"))
    provider = FakeProvider([_resp(tool_calls=[_call("c1", "bash", {"command": "true"})])])
    r = Runner(provider, registry, exploding, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "sandbox_error"
    assert "docker exec timed out" in result.final_message
```

Note `BudgetExceeded` per SP1's shared brief has a `.reason: str` attribute — the exact constructor isn't specified beyond that, so `test_budget_exceeded_status` sets `.reason` explicitly after construction to stay correct regardless of whether `BudgetExceeded.__init__` takes `reason` positionally or requires it be set as an attribute; if SP1's actual `BudgetExceeded(reason)` constructor already accepts `reason` as its first positional argument (the natural reading), the explicit `exploding.exc.reason = ...` line is redundant but harmless.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_runner.py -q`
Expected: `TypeError: Runner.__init__() got an unexpected keyword argument 'context_window'` (or similar — the old registry+sandbox-only constructor from Task 3 doesn't accept `context_window`/take a `provider` as first positional yet in the ChatResponse sense)

- [ ] **Step 3: Rewrite `dirtywork/runner.py`**

```python
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable

from .budget import BudgetExceeded
from .llm import LLMError, LLMTimeout
from .providers import assistant_message, tool_message
from .sandbox import SandboxError

DEFAULT_WINDOW = 32768
TRIM_MARKER = "[result trimmed — re-run the tool if needed]"
CHARS_PER_TOKEN = 4
BUDGET_FRACTION = 0.75
MAX_CONSECUTIVE_FAILURES = 3
MAX_ASSISTANT_TEXT_CHARS = 64_000


def _tool_call_arg_chars(tc) -> int:
    if tc.arguments is None:
        return 0
    try:
        return len(json.dumps(tc.arguments))
    except (TypeError, ValueError):
        return 0


def _total_chars(messages: list) -> int:
    total = 0
    for m in messages:
        total += len(m.get("content") or "")
        for tc in m.get("tool_calls") or []:
            total += _tool_call_arg_chars(tc)
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
    def __init__(self, provider, registry, sandbox, transcript, model,
                 max_turns: int = 40, timeout: int = 1800,
                 temperature: float | None = None,
                 run_info: dict | None = None,
                 finalize: "Callable[[], dict] | None" = None,
                 context_window: int | None = None):
        self.provider = provider
        self.registry = registry
        self.sandbox = sandbox
        self.transcript = transcript
        self.model = model
        self.max_turns = max_turns
        self.timeout = timeout
        self.temperature = temperature
        self.run_info = run_info
        self.finalize = finalize
        window = context_window or provider.context_window(model) or DEFAULT_WINDOW
        self.char_budget = int(window * BUDGET_FRACTION * CHARS_PER_TOKEN)

    def run(self, system_prompt: str, task: str) -> RunResult:
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

        def finish(status: str, final: str) -> RunResult:
            extra = {}
            if self.finalize is not None:
                try:
                    extra = self.finalize() or {}
                except Exception as e:
                    extra = {"finalize_error": repr(e)}
            self.transcript.write("run_end", status=status, turns=turns,
                                  duration_s=round(time.monotonic() - start, 1),
                                  usage=usage, **extra)
            return RunResult(status, turns, final, usage, extra)

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
                    resp = self.provider.chat(self.model, messages, self.registry.schemas(),
                                              temperature=self.temperature,
                                              timeout=max(1.0, remaining))
                except LLMTimeout:
                    if time.monotonic() >= deadline - 0.5:
                        return finish("timeout", "")
                    else:
                        return finish("model_error",
                                      "model request timed out before the run deadline")
                except LLMError as e:
                    return finish("model_error", str(e))
                turns += 1

                for k in usage:
                    usage[k] += resp.usage.get(k, 0)

                assistant_text = resp.text
                self.transcript.write(
                    "assistant",
                    text=(assistant_text[:MAX_ASSISTANT_TEXT_CHARS] if assistant_text else assistant_text),
                    tool_calls=[{"name": tc.name, "arguments": tc.arguments} for tc in resp.tool_calls])

                if not resp.tool_calls:
                    messages.append(assistant_message(assistant_text, None))
                    return finish("completed", assistant_text or "")

                messages.append(assistant_message(assistant_text, resp.tool_calls))

                try:
                    for tc in resp.tool_calls:
                        if tc.error is not None:
                            failures += 1
                            if resp.finish_reason == "length":
                                result = (
                                    "ERROR: your reply was cut off at the token limit before "
                                    "the tool call completed. Emit smaller tool calls — e.g. "
                                    "write the file in pieces using multiple write_file/"
                                    "edit_file calls."
                                )
                            else:
                                result = f"ERROR: {tc.error}"
                        else:
                            tool_result = self.registry.execute(
                                tc.name, tc.arguments, sandbox=self.sandbox, deadline=deadline)
                            result = tool_result.text
                            failures = 0 if tool_result.kind != "error" else failures + 1
                        self.transcript.write("tool_result", tool=tc.name,
                                              args=str(tc.arguments)[:500],
                                              result=result[:2000])
                        messages.append(tool_message(tc.id, result))
                        if failures >= MAX_CONSECUTIVE_FAILURES:
                            return finish("model_error",
                                          "aborted after repeated malformed tool calls")
                except BudgetExceeded as e:
                    return finish("budget_exceeded", e.reason)
                except SandboxError as e:
                    return finish("sandbox_error", str(e))
        except KeyboardInterrupt:
            return finish("interrupted", "")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_runner.py -q`
Expected: 26 passed

- [ ] **Step 5: Patch `dirtywork/__main__.py`**

```bash
python3 - <<'PY'
from pathlib import Path

p = Path("dirtywork/__main__.py")
text = p.read_text()

assert "from .llm import LLMError, LMStudioClient" in text
text = text.replace(
    "from .llm import LLMError, LMStudioClient",
    "from .llm import LLMError\nfrom .providers import get_provider",
)

old_flag = '    run_p.add_argument("--base-url", default="http://localhost:1234/v1")'
assert old_flag in text, "the --base-url argparse line moved -- inspect the file by hand"
new_flags = (
    '    run_p.add_argument("--provider", default="openai", choices=["openai", "anthropic"])\n'
    '    run_p.add_argument("--base-url", default=None)\n'
    '    run_p.add_argument("--context-window", type=int, default=None)'
)
text = text.replace(old_flag, new_flags)

assert "client = LMStudioClient(base_url=args.base_url)" in text
text = text.replace(
    "client = LMStudioClient(base_url=args.base_url)",
    "provider = get_provider(args.provider, args.base_url, timeout=600)",
)

assert "models = client.list_models()" in text
text = text.replace("models = client.list_models()", "models = provider.list_models()")

old_hint = 'f"{e}\\nIs LM Studio running? Try: lms ps"'
if old_hint in text:
    text = text.replace(
        old_hint,
        'f"{e}\\nIs the {args.provider} endpoint reachable? "\n'
        '             f"(LM Studio: lms ps; Anthropic: check ANTHROPIC_API_KEY)"',
    )
else:
    print("NOTE: the LM-Studio-specific preflight hint string did not match verbatim -- "
          "leave it as-is or generalize it by hand; it is not required for tests to pass.")

count = text.count("Runner(client, registry, sandbox, transcript")
assert count >= 1, "no 'Runner(client, registry, sandbox, transcript' call site found (Task 3's output)"
text = text.replace(
    "Runner(client, registry, sandbox, transcript",
    "Runner(provider, registry, sandbox, transcript, context_window=args.context_window",
)

if '"provider": "openai"' in text:
    text = text.replace('"provider": "openai"', '"provider": args.provider')
else:
    print("NOTE: no literal '\"provider\": \"openai\"' run_info entry found -- add "
          "'provider': args.provider to the run_info dict passed to Runner() by hand "
          "if SP1 wired it up differently.")

p.write_text(text)
print(f"patched {count} Runner(...) call site(s)")
PY
```

- [ ] **Step 6: Patch `tests/test_main.py` for provider-aware preflight**

The existing `test_main_lmstudio_down_exits_2` and the tests that `monkeypatch.setattr(m.LMStudioClient, "list_models", ...)` need to target `get_provider`'s return value instead, since `m.LMStudioClient` is no longer imported into `__main__`'s namespace. Patch:

```bash
python3 - <<'PY'
from pathlib import Path

p = Path("tests/test_main.py")
text = p.read_text()

old = 'monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])'
new = 'monkeypatch.setattr(m.get_provider("openai").__class__, "list_models", lambda self: [m.DEFAULT_MODEL])'
count = text.count(old)
if count:
    text = text.replace(old, new)
    print(f"patched {count} preflight monkeypatch site(s)")
else:
    print("NOTE: the LMStudioClient monkeypatch pattern did not match verbatim -- "
          "update each 'monkeypatch.setattr(m.LMStudioClient, \"list_models\", ...)' "
          "call by hand to patch the OpenAICompatClient class returned by "
          "dirtywork.providers.get_provider(\"openai\") instead, since m.LMStudioClient "
          "is no longer imported into dirtywork.__main__'s namespace.")

p.write_text(text)
PY
```

Add one new test to `tests/test_main.py` confirming the `--provider` flag is wired through:

```python
def test_main_unknown_provider_rejected_by_argparse(tmp_path: Path, capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["run", "--repo", str(tmp_path), "--provider", "bogus", "do things"])
    assert exc_info.value.code == 2
```

- [ ] **Step 7: Run the CLI-facing tests**

Run: `python -m pytest tests/test_main.py tests/test_runner.py -q`
Expected: all pass. If Step 5 or 6 printed a `NOTE:`, resolve it by hand (inspect `dirtywork/__main__.py` / `tests/test_main.py` at the described spot) before continuing.

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest -q`
Expected: all green

- [ ] **Step 9: Commit**

```bash
git add dirtywork/runner.py dirtywork/__main__.py tests/test_runner.py tests/test_main.py
git commit -m "feat: runner drives provider-neutral history and ChatResponse; add --provider/--base-url/--context-window"
```

---

### Task 7: `AnthropicClient` + fixtures passing the contract suite

**Files:**
- Create: `tests/fixtures/providers/anthropic/*.json` (8 files)
- Create: `tests/test_provider_anthropic.py`
- Create: `dirtywork/providers/anthropic.py`

**Interfaces:**
- Consumes: `dirtywork.llm.{LLMError, http_json}`; `dirtywork.providers.{ChatResponse, ToolCall}`; `os.environ["ANTHROPIC_API_KEY"]` (read host-side in the constructor; never passed to a sandbox).
- Produces: `AnthropicClient(base_url="https://api.anthropic.com", timeout=600, *, http_json=http_json, api_key=None)` implementing `Provider` (`name = "anthropic"`).

- [ ] **Step 1: Write the failing tests and fixtures**

Create the eight Anthropic wire-shape fixtures (same logical names as the OpenAI set, Anthropic-shaped bodies).

`tests/fixtures/providers/anthropic/simple_ok.json`:

```json
{"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {"input_tokens": 3, "output_tokens": 2}}
```

`tests/fixtures/providers/anthropic/parallel_tool_calls.json`:

```json
{
  "content": [
    {"type": "tool_use", "id": "call_1", "name": "read_file", "input": {"path": "a.txt"}},
    {"type": "tool_use", "id": "call_2", "name": "read_file", "input": {"path": "b.txt"}}
  ],
  "stop_reason": "tool_use",
  "usage": {"input_tokens": 12, "output_tokens": 8}
}
```

`tests/fixtures/providers/anthropic/malformed_tool_call.json` (the second block is missing both `id` and `name` — Anthropic never actually emits this in practice, but the contract suite needs a way to exercise the malformed path uniformly across adapters):

```json
{
  "content": [
    {"type": "tool_use", "id": "call_ok", "name": "list_dir", "input": {}},
    {"type": "tool_use", "input": {}}
  ],
  "stop_reason": "tool_use",
  "usage": {"input_tokens": 10, "output_tokens": 6}
}
```

`tests/fixtures/providers/anthropic/bad_json_arguments.json` (Anthropic's `input` is already structured JSON, so there is no string-decode failure mode the way OpenAI has one — this fixture instead models a `max_tokens` cutoff truncating the block before `input` was ever emitted):

```json
{
  "content": [
    {"type": "tool_use", "id": "call_badargs", "name": "write_file"}
  ],
  "stop_reason": "max_tokens",
  "usage": {"input_tokens": 4, "output_tokens": 2}
}
```

`tests/fixtures/providers/anthropic/finish_reason_stop.json`:

```json
{"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}
```

`tests/fixtures/providers/anthropic/finish_reason_length_text.json`:

```json
{"content": [{"type": "text", "text": "cut off mid"}], "stop_reason": "max_tokens", "usage": {"input_tokens": 2, "output_tokens": 2}}
```

`tests/fixtures/providers/anthropic/usage_missing.json`:

```json
{"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
```

`tests/fixtures/providers/anthropic/usage_nan_negative.json`:

```json
{"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {"input_tokens": NaN, "output_tokens": -5}}
```

`tests/test_provider_anthropic.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dirtywork.llm import LLMError
from dirtywork.providers.anthropic import AnthropicClient

from .provider_contract import ProviderContract, RecordingTransport

FIXTURES = Path(__file__).parent / "fixtures" / "providers" / "anthropic"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


class TestAnthropicProviderContract(ProviderContract):
    fixtures_dir = FIXTURES

    def make_client(self, transport):
        return AnthropicClient(base_url="http://fake", http_json=transport, api_key="sk-ant-test")

    def _system_text(self, payload):
        return payload.get("system")

    def _tool_result_entries(self, payload):
        for m in reversed(payload["messages"]):
            if m["role"] == "user" and isinstance(m["content"], list):
                blocks = [b for b in m["content"] if b.get("type") == "tool_result"]
                if blocks:
                    return [(b["tool_use_id"], b["content"]) for b in blocks]
        return []


def test_missing_api_key_raises_llmerror_on_list_models(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = AnthropicClient(base_url="http://fake", http_json=RecordingTransport([]), api_key=None)
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        client.list_models()


def test_missing_api_key_raises_llmerror_on_chat(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = AnthropicClient(base_url="http://fake", http_json=RecordingTransport([]), api_key=None)
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        client.chat("claude-x", [{"role": "user", "content": "hi"}], [],
                    temperature=None, max_tokens=100, timeout=30)


def test_api_key_read_from_env_when_not_passed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    client = AnthropicClient(base_url="http://fake", http_json=RecordingTransport([]))
    assert client.api_key == "sk-ant-from-env"


def test_chat_sends_required_headers():
    transport = RecordingTransport([_fixture("simple_ok.json")])
    client = AnthropicClient(base_url="http://fake", http_json=transport, api_key="sk-ant-test")
    client.chat("claude-x", [{"role": "user", "content": "hi"}], [],
               temperature=None, max_tokens=100, timeout=30)
    headers = transport.calls[0]["headers"]
    assert headers["x-api-key"] == "sk-ant-test"
    assert headers["anthropic-version"] == "2023-06-01"
    assert headers["content-type"] == "application/json"


def test_chat_url_is_v1_messages():
    transport = RecordingTransport([_fixture("simple_ok.json")])
    client = AnthropicClient(base_url="http://fake", http_json=transport, api_key="sk-ant-test")
    client.chat("claude-x", [{"role": "user", "content": "hi"}], [],
               temperature=None, max_tokens=100, timeout=30)
    assert transport.calls[0]["url"] == "http://fake/v1/messages"


def test_tools_converted_to_input_schema_shape():
    transport = RecordingTransport([_fixture("simple_ok.json")])
    client = AnthropicClient(base_url="http://fake", http_json=transport, api_key="sk-ant-test")
    openai_tools = [{"type": "function", "function": {
        "name": "read_file", "description": "Read a file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]
    client.chat("claude-x", [{"role": "user", "content": "hi"}], openai_tools,
               temperature=None, max_tokens=100, timeout=30)
    sent = transport.calls[0]["payload"]["tools"]
    assert sent == [{"name": "read_file", "description": "Read a file.",
                     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                                      "required": ["path"]}}]


def test_list_models_returns_ids():
    transport = RecordingTransport([{"data": [{"id": "claude-opus-5"}, {"id": "claude-sonnet-5"}]}])
    client = AnthropicClient(base_url="http://fake", http_json=transport, api_key="sk-ant-test")
    assert client.list_models() == ["claude-opus-5", "claude-sonnet-5"]


def test_context_window_claude_prefix_and_unknown():
    client = AnthropicClient(base_url="http://fake", http_json=RecordingTransport([]), api_key="k")
    assert client.context_window("claude-opus-5") == 200000
    assert client.context_window("nonexistent/model") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_provider_anthropic.py -q`
Expected: `ModuleNotFoundError: No module named 'dirtywork.providers.anthropic'`

- [ ] **Step 3: Write the minimal implementation**

`dirtywork/providers/anthropic.py`:

```python
from __future__ import annotations

import math
import os

from . import ChatResponse, ToolCall
from ..llm import LLMError, http_json

DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

_STOP_REASON_MAP = {
    "end_turn": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "stop_sequence": "stop",
    # newer stop reasons; unknown values pass through unchanged (see chat())
    "refusal": "stop",
    "pause_turn": "stop",
}


def _parse_tool_use_block(b: dict) -> ToolCall:
    call_id = b.get("id")
    name = b.get("name")
    if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
        return ToolCall(id="", name="", arguments=None,
                        error="malformed tool call entry (missing or invalid id/name fields)")
    input_ = b.get("input")
    if not isinstance(input_, dict):
        return ToolCall(id=call_id, name=name, arguments=None,
                        error="malformed tool arguments: missing or non-object input "
                              "(likely truncated by max_tokens)")
    return ToolCall(id=call_id, name=name, arguments=input_, error=None)


def _to_anthropic_tool(t: dict) -> dict:
    fn = t["function"]
    return {"name": fn["name"], "description": fn.get("description", ""),
            "input_schema": fn["parameters"]}


def _to_anthropic_messages(history: list):
    """Returns (system: str | None, messages: list). Consecutive `tool` role
    entries merge into one `user` message with multiple tool_result blocks, per
    the Anthropic wire contract (tool results ride in a single user turn)."""
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
    raw = raw or {}
    for key, wire_key in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
        v = raw.get(wire_key, 0)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0:
            usage[key] = int(v)
    return usage


class AnthropicClient:
    name = "anthropic"

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = 600, *,
                 http_json=http_json, api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._http_json = http_json
        # Read host-side, at construction. Never forwarded into the sandbox --
        # the sandbox never sees provider credentials.
        self.api_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")

    def _headers(self) -> dict:
        return {"x-api-key": self.api_key, "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json"}

    def list_models(self) -> list:
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        # NOTE: the exact /v1/models response envelope was not directly
        # verifiable against a raw wire example in this plan's reference
        # material; this follows the {"data": [...]} shape documented for
        # Anthropic's other list endpoints (Batches, Files). Verify against
        # current Anthropic docs before relying on this in production.
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
        # Deliberately conservative and static (used only for the runner's
        # trim-budget heuristic, not for API correctness); verify against
        # current Anthropic docs / GET /v1/models for a model's actual
        # advertised context window if precision matters here.
        # --context-window overrides this per run regardless.
        if model.startswith("claude-"):
            return 200000
        return None

    def chat(self, model, history, tools, *, temperature=None, max_tokens=4096, timeout=None) -> ChatResponse:
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
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
        content = body.get("content") or []
        text_parts = [b["text"] for b in content if b.get("type") == "text"]
        tool_calls = [_parse_tool_use_block(b) for b in content if b.get("type") == "tool_use"]
        raw_stop = body.get("stop_reason")
        finish_reason = _STOP_REASON_MAP.get(raw_stop, raw_stop)
        usage = _sanitize_usage(body.get("usage"))
        return ChatResponse(text="".join(text_parts), tool_calls=tool_calls,
                            finish_reason=finish_reason, usage=usage)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_provider_anthropic.py -q`
Expected: 14 passed (7 explicit + 7 inherited from `ProviderContract`)

- [ ] **Step 5: Confirm `get_provider("anthropic", ...)` now works end to end**

Add to `tests/test_providers.py`:

```python
def test_get_provider_anthropic_returns_anthropic_client(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    provider = get_provider("anthropic", "http://fake", timeout=10)
    assert provider.name == "anthropic"
    assert provider.base_url == "http://fake"


def test_get_provider_openai_returns_openai_client():
    provider = get_provider("openai")
    assert provider.name == "openai"
    assert provider.base_url == DEFAULT_BASE_URLS["openai"]
```

Run: `python -m pytest tests/test_providers.py -q`
Expected: 9 passed

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add dirtywork/providers/anthropic.py tests/fixtures/providers/anthropic \
        tests/test_provider_anthropic.py tests/test_providers.py
git commit -m "feat: add AnthropicClient passing the provider contract suite"
```

---

### Task 8: `docs/transcript-schema.md` + `schema_version` regression tests

**Files:**
- Create: `docs/transcript-schema.md`
- Create: `tests/test_transcript_schema.py`
- Modify: `README.md` (Machine contract section — pointer to the new doc)

**Interfaces:**
- Consumes: nothing new — this task documents and regression-tests behavior SP1/SP2 already shipped (`schema_version: 2` on `run_start` and in the stdout JSON, `run_dir` in the stdout JSON).
- Produces: `docs/transcript-schema.md` (v1/v2 tables for every event and field).

- [ ] **Step 1: Write the failing tests**

`tests/test_transcript_schema.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from dirtywork.builtin_tools import default_registry
from dirtywork.providers import ChatResponse
from dirtywork.runner import Runner
from dirtywork.transcript import Transcript

DOC = Path(__file__).parent.parent / "docs" / "transcript-schema.md"

EVENT_NAMES = ["run_start", "assistant", "tool_result", "guardrail_block", "sandbox_reset", "run_end"]
V2_STATUSES = ["budget_exceeded", "sandbox_error", "export_failed"]
V2_RUN_END_FIELDS = ["diff_stat", "escaping_symlinks", "dropped_git_entries", "worktree_bytes", "worktree_files"]


def test_doc_exists_and_documents_every_event_name():
    assert DOC.exists(), f"{DOC} does not exist"
    text = DOC.read_text()
    for name in EVENT_NAMES:
        assert name in text, f"event '{name}' is not documented in {DOC.name}"


def test_doc_documents_schema_version_and_v2_statuses():
    text = DOC.read_text()
    assert "schema_version" in text
    assert "v1" in text and "v2" in text
    for status in V2_STATUSES:
        assert status in text, f"status '{status}' is not documented in {DOC.name}"
    for field in V2_RUN_END_FIELDS:
        assert field in text, f"run_end field '{field}' is not documented in {DOC.name}"


class _NoOpProvider:
    name = "fake"

    def list_models(self):
        return ["m"]

    def context_window(self, model):
        return None

    def chat(self, model, history, tools, *, temperature=None, max_tokens=4096, timeout=None):
        return ChatResponse(text="done", tool_calls=[], finish_reason="stop",
                            usage={"prompt_tokens": 1, "completion_tokens": 1})


def test_real_run_start_has_schema_version_2(tmp_path):
    transcript = Transcript(tmp_path / "t.jsonl")
    registry = default_registry(transcript)
    r = Runner(_NoOpProvider(), registry, object(), transcript, model="m")
    r.run("s", "t")
    transcript.close()
    events = [json.loads(l) for l in (tmp_path / "t.jsonl").read_text().splitlines()]
    run_start = next(e for e in events if e["event"] == "run_start")
    assert run_start["schema_version"] == 2


def test_stdout_json_has_schema_version_2_and_run_dir(tmp_path, monkeypatch, capsys):
    import subprocess
    import dirtywork.__main__ as m
    from dirtywork.runner import RunResult
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(m.get_provider("openai").__class__, "list_models", lambda self: [m.DEFAULT_MODEL])
    monkeypatch.setattr(m.Runner, "run", lambda self, sp, task: RunResult("completed", 1, "ok", {}))

    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "some task"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 2
    assert "run_dir" in payload
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_transcript_schema.py -q`
Expected: 2 failed (`test_doc_exists_and_documents_every_event_name`, `test_doc_documents_schema_version_and_v2_statuses` — `docs/transcript-schema.md` does not exist yet), 2 passed (the two behavioral regression checks, since SP1/SP2 already ship `schema_version`/`run_dir`)

- [ ] **Step 3: Write `docs/transcript-schema.md`**

```markdown
# Transcript schema

`dirtywork` writes one JSON object per line to `~/.dirtywork/runs/<slug>/transcript.jsonl` (`tail -f` friendly — each line is flushed immediately). Every line has at least `ts` (UTC ISO-8601) and `event` (one of the six event names below); `schema_version` marks the overall version and appears once, on `run_start`, and again in the CLI's stdout JSON — not on every line.

**v1** is the pre-hardening shape (dirtywork ≤ 0.2.0, host-only execution, no `schema_version` field at all — its absence *is* the v1 marker). **v2** (0.3.0+) adds Docker-sandbox provenance, provider identity, three new terminal statuses, and richer `run_end` fields from the export validator. A v1 transcript reader that ignores unknown fields keeps working unmodified against v2 output — every v2 addition is a new field or a new enum value, never a removed or renamed one (the same compatibility rule the stdout JSON contract follows, and for the same reason).

## Events

### `run_start`

One per run, first line.

| Field | v1 | v2 | Type | Notes |
|---|---|---|---|---|
| `ts` | ✓ | ✓ | string | UTC ISO-8601 |
| `event` | ✓ | ✓ | `"run_start"` | |
| `task` | ✓ | ✓ | string | the task text |
| `model` | ✓ | ✓ | string | |
| `max_turns` | ✓ | ✓ | integer | |
| `timeout` | ✓ | ✓ | integer | seconds |
| `repo` | ✓ | ✓ | string | absolute path |
| `worktree` | ✓ | ✓ | string | absolute path |
| `schema_version` | | ✓ | `2` | present from v2 onward; its absence marks v1 |
| `base_commit` | | ✓ | string | resolved ref the worktree branched from |
| `branch` | | ✓ | string | `dirtywork/<slug>` |
| `branch_from` | | ✓ | string \| null | `--branch-from` as given, or null for repo HEAD |
| `base_url` | | ✓ | string | the provider endpoint in use |
| `dirtywork_version` | | ✓ | string | `dirtywork.__version__` |
| `temperature` | | ✓ | number \| null | |
| `provider` | | ✓ | `"openai"` \| `"anthropic"` | |
| `sandbox` | | ✓ | `"none"` \| object | `"none"` in host mode; `{backend, image, image_digest, network, memory, cpus, pids_limit, tmp_size, gitdir_size, max_worktree_mb, max_worktree_files, user}` in Docker mode |

### `assistant`

One per model turn that produced a reply (with or without tool calls).

| Field | v1 | v2 | Type | Notes |
|---|---|---|---|---|
| `text` | ✓ | ✓ | string \| null | capped at `MAX_ASSISTANT_TEXT_CHARS` (64 000 chars); the full text still goes to the model, only the transcript copy is capped |
| `tool_calls` | ✓ | ✓ | list | `[{name, arguments}, ...]` — v1 carried an OpenAI-shaped `{name, arguments: <raw JSON string, capped at 2000 chars>}`; v2 carries `{name, arguments: <parsed object, from the provider-neutral ToolCall>}` since the runner no longer touches wire-shaped strings — that parsing now lives entirely in the provider adapter |

### `tool_result`

One per tool call executed (or per malformed entry, in v1).

| Field | v1 | v2 | Type | Notes |
|---|---|---|---|---|
| `tool` | ✓ | ✓ | string | tool name (`""` for a v1 malformed-entry record) |
| `args` | ✓ | ✓ | string | v1: raw JSON arguments string, capped at 500 chars; v2: `str(ToolCall.arguments)`, capped at 500 chars |
| `result` | ✓ | ✓ | string | capped at 2000 chars per `Caps.transcript` (`"preview"` for all six built-in tools in this release; the registry also supports `"full"`/`"none"`, unused by any shipped tool) |

### `guardrail_block`

One per `BLOCKED:`-prefixed tool result (a bash denylist hit, for example). Written by the registry itself (`ToolRegistry(transcript=...)`), not by the runner — this moved from `ToolExecutor` in sub-project 3.

| Field | v1 | v2 | Type | Notes |
|---|---|---|---|---|
| `tool` | ✓ | ✓ | string | |
| `args` | ✓ | ✓ | object | the validated argument dict passed to the tool |
| `reason` | ✓ | ✓ | string | the full `BLOCKED: ...` text |

### `sandbox_reset`

v2 only (Docker sandbox mode; sub-project 2). Emitted on a container reset (timeout, OOM, a stuck `docker exec`).

| Field | v1 | v2 | Type | Notes |
|---|---|---|---|---|
| `reason` | | ✓ | string | why the reset happened |

### `run_end`

One per run, last line.

| Field | v1 | v2 | Type | Notes |
|---|---|---|---|---|
| `status` | ✓ | ✓ | string | v1: `completed`, `max_turns`, `timeout`, `context_exhausted`, `model_error`, `interrupted`. v2 adds `budget_exceeded`, `sandbox_error`, `export_failed`. (An intermediate v2 sub-project-2 design draft also had a `tampered` status; it was removed before ship — once the worktree lives on a Docker volume with a validated export, there is no host-side surface left for the worker to tamper with, so the status was never needed.) |
| `turns` | ✓ | ✓ | integer | |
| `duration_s` | ✓ | ✓ | number | wall-clock seconds, rounded to 1 decimal |
| `usage` | ✓ | ✓ | object | `{prompt_tokens, completion_tokens}`, cumulative across turns |
| `diff_stat` | | ✓ | string | capped `git diff --stat` output (host mode: `workspace.host_diff_stat`; Docker mode: the container-computed patch stat from the export flow), merged in via the runner's `finalize` hook |
| `patch_path` | | ✓ | string \| null | path to `diff.patch` when the export flow ran (Docker mode only) |
| `worktree_bytes` | | ✓ | integer \| null | sampled worktree size from `budget.measure_worktree` |
| `worktree_files` | | ✓ | integer \| null | sampled worktree entry count |
| `escaping_symlinks` | | ✓ | list | symlinks whose target is absolute or escapes the worktree (created by the worker; never followed, always reported) |
| `dropped_git_entries` | | ✓ | list | Docker mode only: any `.git`-named entry the export step found and refused to add |
| `finalize_error` | | ✓ | string | present only if the runner's `finalize` hook itself raised; the run's `status` is unaffected |

## `schema_version` and the stdout JSON contract

`schema_version: 2` also appears in the CLI's single stdout JSON object (`dirtywork run`'s machine contract — see the README's "Machine contract" section), alongside a new `run_dir` field (`~/.dirtywork/runs/<slug>`). Per this project's global compatibility rule, the stdout JSON may only gain fields, never lose or rename `status, worktree, branch, transcript, turns, usage, final_message`.

## `run.json`

Separate from the transcript: `~/.dirtywork/runs/<slug>/run.json` is a single JSON object (not JSONL), written at run start and updated at run end. See sub-project 2 §2 steps 4/11 for its exact field list; `dirtywork runs show <slug>` (sub-project 3 §4, this plan's Task 9) prints it pretty-formatted alongside a tool-call timeline reconstructed from the transcript.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_transcript_schema.py -q`
Expected: 4 passed

- [ ] **Step 5: Point the README at the new doc**

```bash
python3 - <<'PY'
from pathlib import Path

p = Path("README.md")
text = p.read_text()

old = ('**Transcript events** (JSONL, one per line): `run_start` (task, repo, model,\n'
       'config), `assistant` (text + tool calls), `tool_result` (truncated),\n'
       '`guardrail_block`, `run_end` (status, turns, duration, cumulative usage).')
new = ('**Transcript events** (JSONL, one per line): `run_start` (task, repo, model,\n'
       'config), `assistant` (text + tool calls), `tool_result` (truncated),\n'
       '`guardrail_block`, `sandbox_reset` (Docker mode), `run_end` (status, turns,\n'
       'duration, cumulative usage). Full schema, including every v1→v2 field: see\n'
       '[`docs/transcript-schema.md`](docs/transcript-schema.md).')
if old in text:
    text = text.replace(old, new)
    p.write_text(text)
    print("patched README.md Transcript events bullet")
else:
    print("NOTE: the exact 'Transcript events' paragraph did not match verbatim "
          "(SP2 likely reworded the Machine contract section) -- add a pointer to "
          "docs/transcript-schema.md near wherever that section now lists transcript "
          "events by hand.")
PY
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add docs/transcript-schema.md tests/test_transcript_schema.py README.md
git commit -m "docs: document the v1/v2 transcript schema"
```

---

### Task 9: `runs list` + `runs show [--diff]`

**Files:**
- Create: `dirtywork/runs.py`
- Create: `tests/test_runs.py`
- Modify: `dirtywork/__main__.py` (extract `cmd_run(args)`; add `runs` subparser and dispatch)
- Modify: `tests/test_main.py` (two small end-to-end dispatch tests)

**Interfaces:**
- Consumes: `dirtywork.rundir.{RUNS_DIR, read_run_json}`; `dirtywork.sandbox.docker_cli.{run, T_QUERY}`.
- Produces: `dirtywork.runs.cmd_list(args) -> int`, `dirtywork.runs.cmd_show(args) -> int`; `dirtywork.__main__.cmd_run(args) -> int` (the former inline body of `main()`, now a standalone function); CLI: `dirtywork runs list [--json]`, `dirtywork runs show <slug> [--diff]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_runs.py`:

```python
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from dirtywork import rundir, runs


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "--allow-empty", "-m", "i")
    return r


def _write_run(runs_dir: Path, slug: str, data: dict):
    run_dir = runs_dir / slug
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps(data))
    return run_dir


def test_cmd_list_prints_table(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "fix-bug-0101", {
        "status": "completed", "started": "2026-08-16T00:00:00Z",
        "branch": "dirtywork/fix-bug-0101", "repo": str(repo),
        "worktree": str(repo / ".worktrees" / "dw-fix-bug-0101"),
        "container": None, "volume": None,
    })
    rc = runs.cmd_list(argparse.Namespace(json=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "fix-bug-0101" in out
    assert "completed" in out


def test_cmd_list_json_output(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "running", "started": "t", "branch": "b", "repo": str(repo),
        "worktree": str(repo), "container": None, "volume": None,
    })
    rc = runs.cmd_list(argparse.Namespace(json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["slug"] == "slug1"
    assert payload[0]["status"] == "running"


def test_cmd_list_no_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    rc = runs.cmd_list(argparse.Namespace(json=False))
    assert rc == 0
    assert "no runs found" in capsys.readouterr().out


def test_cmd_list_worktree_present_detection(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = repo / ".worktrees" / "dw-present"
    _git(repo, "worktree", "add", "-b", "dirtywork/present", str(wt), "HEAD")
    _write_run(tmp_path / "runs", "present", {
        "status": "completed", "started": "t", "branch": "dirtywork/present", "repo": str(repo),
        "worktree": str(wt), "container": None, "volume": None,
    })
    rc = runs.cmd_list(argparse.Namespace(json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["worktree"] == "yes"


def test_cmd_list_docker_query_failure_is_non_fatal(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")

    def fake_run(*a, **k):
        raise RuntimeError("docker not installed")
    monkeypatch.setattr(runs.docker_cli, "run", fake_run)
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "started": "t", "branch": "b", "repo": str(repo),
        "worktree": str(repo), "container": "dw-slug1", "volume": "dw-slug1-work",
    })
    rc = runs.cmd_list(argparse.Namespace(json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["container"] == "-"  # docker query failed -> best-effort "-", never fatal


def test_cmd_show_prints_run_json_and_timeline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _write_run(tmp_path / "runs", "slug1", {"status": "completed", "slug": "slug1"})
    (run_dir / "transcript.jsonl").write_text(
        json.dumps({"ts": "t1", "event": "run_start"}) + "\n"
        + json.dumps({"ts": "t2", "event": "tool_result", "tool": "bash",
                     "args": "ls", "result": "exit code: 0"}) + "\n"
    )
    rc = runs.cmd_show(argparse.Namespace(slug="slug1", diff=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert '"status": "completed"' in out
    assert "timeline:" in out
    assert "run_start" in out
    assert "tool_result" in out
    assert "bash" in out


def test_cmd_show_unknown_slug_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    rc = runs.cmd_show(argparse.Namespace(slug="nope", diff=False))
    assert rc == 2
    assert "no such run" in capsys.readouterr().err


def test_cmd_show_diff_prints_patch(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _write_run(tmp_path / "runs", "slug1", {"status": "completed"})
    (run_dir / "diff.patch").write_text("--- a/x\n+++ b/x\n")
    rc = runs.cmd_show(argparse.Namespace(slug="slug1", diff=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "diff:" in out
    assert "--- a/x" in out


def test_cmd_show_diff_missing_patch_notes_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "slug1", {"status": "completed"})
    rc = runs.cmd_show(argparse.Namespace(slug="slug1", diff=True))
    assert rc == 0
    assert "no diff.patch" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_runs.py -q`
Expected: `ModuleNotFoundError: No module named 'dirtywork.runs'`

- [ ] **Step 3: Write the minimal implementation**

`dirtywork/runs.py`:

```python
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import rundir
from .sandbox import docker_cli


def _iter_run_dirs(runs_dir: Path):
    if not runs_dir.is_dir():
        return
    for d in sorted(runs_dir.iterdir()):
        if d.is_dir() and (d / "run.json").exists():
            yield d


def _docker_state():
    """Returns (container_states: dict[name, str], volume_names: set[str]).
    Docker failures never raise -- an empty/best-effort result just means every
    row's docker column renders '-'/'?' instead of failing the whole command."""
    containers = {}
    volumes = set()
    try:
        cp = docker_cli.run(["ps", "-a", "--format", "{{.Names}}\t{{.State}}",
                             "--filter", "label=dirtywork.run"], timeout=docker_cli.T_QUERY)
        if cp.returncode == 0:
            for line in cp.output.decode("utf-8", errors="replace").splitlines():
                if "\t" in line:
                    name, state = line.split("\t", 1)
                    containers[name] = state
    except Exception:
        pass
    try:
        cp = docker_cli.run(["volume", "ls", "--format", "{{.Name}}",
                             "--filter", "label=dirtywork.run"], timeout=docker_cli.T_QUERY)
        if cp.returncode == 0:
            volumes = set(cp.output.decode("utf-8", errors="replace").splitlines())
    except Exception:
        pass
    return containers, volumes


def _worktree_present(repo: str, worktree: str):
    """True/False if determinable, None if git itself could not be asked."""
    if not repo or not worktree:
        return None
    try:
        cp = subprocess.run(["git", "-C", repo, "worktree", "list", "--porcelain"],
                            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if cp.returncode != 0:
        return None
    paths = [ln.split(" ", 1)[1] for ln in cp.stdout.splitlines() if ln.startswith("worktree ")]
    resolved = {str(Path(p).resolve()) for p in paths}
    return str(Path(worktree).resolve()) in resolved


def _print_table(rows: list) -> None:
    columns = ["slug", "status", "started", "branch", "worktree", "container", "volume"]
    if not rows:
        print("no runs found")
        return
    widths = {c: max(len(c), max(len(str(r.get(c, ""))) for r in rows)) for c in columns}
    print("  ".join(c.upper().ljust(widths[c]) for c in columns))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns))


def cmd_list(args) -> int:
    containers, volumes = _docker_state()
    rows = []
    for run_dir in _iter_run_dirs(rundir.RUNS_DIR):
        slug = run_dir.name
        try:
            data = rundir.read_run_json(run_dir)
        except Exception as e:
            rows.append({"slug": slug, "status": "?", "started": "?", "branch": "?",
                        "worktree": "?", "container": "?", "volume": "?",
                        "error": f"unreadable run.json: {e}"})
            continue
        wt_present = _worktree_present(data.get("repo", ""), data.get("worktree", ""))
        container_name = data.get("container")
        volume_name = data.get("volume")
        rows.append({
            "slug": slug,
            "status": data.get("status", "?"),
            "started": data.get("started", "?"),
            "branch": data.get("branch", "?"),
            "worktree": "?" if wt_present is None else ("yes" if wt_present else "no"),
            "container": containers.get(container_name, "-") if container_name else "-",
            "volume": ("present" if volume_name in volumes else "absent") if volume_name else "-",
        })
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2))
        return 0
    _print_table(rows)
    return 0


def cmd_show(args) -> int:
    run_dir = rundir.RUNS_DIR / args.slug
    if not run_dir.is_dir():
        print(f"error: no such run '{args.slug}'", file=sys.stderr)
        return 2
    try:
        data = rundir.read_run_json(run_dir)
    except Exception as e:
        print(f"error: cannot read run.json for '{args.slug}': {e}", file=sys.stderr)
        return 2
    print(json.dumps(data, indent=2))

    transcript_path = run_dir / "transcript.jsonl"
    if transcript_path.exists():
        print("\ntimeline:")
        for line in transcript_path.read_text().splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = event.get("ts", "")
            name = event.get("event", "")
            if name == "tool_result":
                tool = event.get("tool", "")
                args_preview = str(event.get("args", ""))[:80]
                status = "ERROR" if str(event.get("result", "")).startswith("ERROR") else "ok"
                print(f"{ts}  {name:<15} {tool:<12} {args_preview:<80} {status}")
            else:
                print(f"{ts}  {name}")

    if getattr(args, "diff", False):
        patch_path = run_dir / "diff.patch"
        if patch_path.exists():
            print("\ndiff:")
            print(patch_path.read_text())
        else:
            print("\nno diff.patch for this run (host mode, or export never ran)")
    return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_runs.py -q`
Expected: 9 passed

- [ ] **Step 5: Extract `cmd_run(args)` and wire the `runs` subparser into `dirtywork/__main__.py`**

`main()` currently has exactly one subcommand ("run") and its whole body inline after `args = parser.parse_args(argv)`. This step (a) inserts the `runs` subparser's `add_parser`/`add_argument` calls right before `parser.parse_args(argv)` is called, then (b) extracts everything from `repo = args.repo.expanduser().resolve()` through `return 0 if result.status == "completed" else 1` (the entire "run" command body, untouched) into a new top-level `cmd_run(args) -> int` function, and (c) replaces that span inside `main()` with a dispatch on `args.cmd`:

```bash
python3 - <<'PY'
from pathlib import Path

p = Path("dirtywork/__main__.py")
text = p.read_text()

parse_anchor = '    args = parser.parse_args(argv)\n'
assert parse_anchor in text, "parse_args anchor not found -- inspect dirtywork/__main__.py by hand"

runs_subparser_block = (
    '\n'
    '    runs_p = sub.add_parser("runs", help="inspect and manage dirtywork runs")\n'
    '    runs_sub = runs_p.add_subparsers(dest="runs_cmd", required=True)\n'
    '\n'
    '    runs_list_p = runs_sub.add_parser("list", help="list all runs")\n'
    '    runs_list_p.add_argument("--json", action="store_true")\n'
    '\n'
    '    runs_show_p = runs_sub.add_parser("show", help="show one run\'s details")\n'
    '    runs_show_p.add_argument("slug")\n'
    '    runs_show_p.add_argument("--diff", action="store_true")\n'
)
text = text.replace(parse_anchor, runs_subparser_block + parse_anchor, 1)

lines = text.splitlines(keepends=True)

start_anchor = '    repo = args.repo.expanduser().resolve()\n'
end_anchor = '    return 0 if result.status == "completed" else 1\n'
assert start_anchor in lines, "start anchor line not found -- inspect the file by hand"
assert end_anchor in lines, "end anchor line not found -- inspect the file by hand"

start_idx = lines.index(start_anchor)
end_idx = lines.index(end_anchor)
assert start_idx < end_idx

body = lines[start_idx:end_idx + 1]
main_def_idx = next(i for i, l in enumerate(lines) if l.startswith("def main("))
assert main_def_idx < start_idx

cmd_run_func = ["def cmd_run(args) -> int:\n"] + body + ["\n\n"]

dispatch = [
    '    if args.cmd == "run":\n',
    '        return cmd_run(args)\n',
    '    if args.cmd == "runs":\n',
    '        from . import runs as runs_mod\n',
    '        return {"list": runs_mod.cmd_list, "show": runs_mod.cmd_show}[args.runs_cmd](args)\n',
    '    raise AssertionError(f"unhandled subcommand {args.cmd!r}")\n',
]

new_lines = (
    lines[:main_def_idx]
    + cmd_run_func
    + lines[main_def_idx:start_idx]
    + dispatch
    + lines[end_idx + 1:]
)
p.write_text("".join(new_lines))
print(f"extracted cmd_run() ({len(body)} lines); main() now dispatches on args.cmd; "
      f"added 'runs list'/'runs show' subparsers")
PY
```

Open `dirtywork/__main__.py` and confirm by eye that `cmd_run` reads correctly (its body should be byte-identical to the old inline code, just under a new `def cmd_run(args) -> int:` header) and that `main()` now ends with the `args.cmd` dispatch shown above. If either assertion in the script fired, resolve it by hand: define `cmd_run(args) -> int` containing exactly what `main()` used to do after argument parsing, add the `runs` subparser block shown above before `parser.parse_args(argv)`, and make `main()` dispatch on `args.cmd` as shown.

- [ ] **Step 6: Add end-to-end dispatch tests to `tests/test_main.py`**

```python
def test_runs_list_dispatches_to_cmd_list(tmp_path, monkeypatch, capsys):
    import dirtywork.__main__ as m
    from dirtywork import rundir
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    rc = m.main(["runs", "list", "--json"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "[]"


def test_runs_show_unknown_slug_exits_2(tmp_path, monkeypatch, capsys):
    import dirtywork.__main__ as m
    from dirtywork import rundir
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    rc = m.main(["runs", "show", "nope"])
    assert rc == 2
```

- [ ] **Step 7: Run the CLI-facing tests**

Run: `python -m pytest tests/test_runs.py tests/test_main.py -q`
Expected: all pass

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest -q`
Expected: all green

- [ ] **Step 9: Commit**

```bash
git add dirtywork/runs.py dirtywork/__main__.py tests/test_runs.py tests/test_main.py
git commit -m "feat: add 'dirtywork runs list' and 'dirtywork runs show'"
```

---

### Task 10: `runs export`

**Files:**
- Modify: `dirtywork/runs.py` (add `cmd_export`)
- Modify: `dirtywork/__main__.py` (add the `runs export` subparser + dispatch entry)
- Modify: `tests/test_runs.py` (add `cmd_export` tests)

**Interfaces:**
- Consumes: `dirtywork.sandbox.{RunArtifacts}`; `dirtywork.sandbox.docker_cli.{run, validate_objects_dir, resolve_image, T_QUERY}`; `dirtywork.sandbox.docker_args.{DockerConfig, DEFAULT_IMAGE}`; `dirtywork.sandbox.export.export_run`.
- Produces: `dirtywork.runs.cmd_export(args) -> int`; CLI: `dirtywork runs export <slug> [--max-patch-mb 10] [--keep-volume]`.

- [ ] **Step 1: Write the failing tests**

Add to the top of `tests/test_runs.py`, alongside the existing imports:

```python
from dirtywork.sandbox import RunArtifacts
```

Append to `tests/test_runs.py`:

```python
def _write_docker_run(runs_dir: Path, slug: str, repo: Path, worktree: Path, volume="dw-slug1-work"):
    return _write_run(runs_dir, slug, {
        "status": "running", "sandbox": "docker", "slug": slug,
        "repo": str(repo), "worktree": str(worktree),
        "base_commit": "abc123", "volume": volume, "container": f"dw-{slug}",
        "image": "dirtywork/worker:0.3",
    })


def test_cmd_export_not_docker_sandbox_rejected(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "hostrun", {"status": "completed", "sandbox": "none"})
    rc = runs.cmd_export(argparse.Namespace(slug="hostrun", max_patch_mb=10, keep_volume=False))
    assert rc == 2
    assert "not a docker-sandbox run" in capsys.readouterr().err


def test_cmd_export_missing_volume_exits_2(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = repo / ".worktrees" / "dw-slug1"
    wt.mkdir(parents=True)
    _write_docker_run(tmp_path / "runs", "slug1", repo, wt)

    class FakeCP:
        returncode = 1
    monkeypatch.setattr(runs.docker_cli, "run", lambda *a, **k: FakeCP())
    rc = runs.cmd_export(argparse.Namespace(slug="slug1", max_patch_mb=10, keep_volume=False))
    assert rc == 2
    assert "does not exist" in capsys.readouterr().err


def test_cmd_export_success_updates_run_json(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = repo / ".worktrees" / "dw-slug1"
    wt.mkdir(parents=True)
    run_dir = _write_docker_run(tmp_path / "runs", "slug1", repo, wt)

    class FakeCP:
        returncode = 0
    monkeypatch.setattr(runs.docker_cli, "run", lambda *a, **k: FakeCP())
    monkeypatch.setattr(runs.docker_cli, "validate_objects_dir", lambda repo: repo / ".git" / "objects")
    monkeypatch.setattr(runs.docker_cli, "resolve_image", lambda image: f"{image}@sha256:deadbeef")
    monkeypatch.setattr(runs.export, "export_run", lambda cfg, **kw: RunArtifacts(
        diff_stat=" 1 file changed", patch_path=str(run_dir / "diff.patch"),
        worktree_bytes=100, worktree_files=1, export_status="ok"))

    rc = runs.cmd_export(argparse.Namespace(slug="slug1", max_patch_mb=10, keep_volume=False))
    assert rc == 0
    assert "exported 'slug1'" in capsys.readouterr().out

    data = json.loads((run_dir / "run.json").read_text())
    assert data["export_status"] == "ok"
    assert data["diff_stat"] == " 1 file changed"


def test_cmd_export_failure_reports_and_returns_1(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = repo / ".worktrees" / "dw-slug1"
    wt.mkdir(parents=True)
    run_dir = _write_docker_run(tmp_path / "runs", "slug1", repo, wt)

    class FakeCP:
        returncode = 0
    monkeypatch.setattr(runs.docker_cli, "run", lambda *a, **k: FakeCP())
    monkeypatch.setattr(runs.docker_cli, "validate_objects_dir", lambda repo: repo / ".git" / "objects")
    monkeypatch.setattr(runs.docker_cli, "resolve_image", lambda image: f"{image}@sha256:deadbeef")
    monkeypatch.setattr(runs.export, "export_run", lambda cfg, **kw: RunArtifacts(
        export_status="export_failed: worktree not empty"))

    rc = runs.cmd_export(argparse.Namespace(slug="slug1", max_patch_mb=10, keep_volume=False))
    assert rc == 1
    assert "export failed" in capsys.readouterr().err
    data = json.loads((run_dir / "run.json").read_text())
    assert data["status"] == "export_failed"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_runs.py -q -k export`
Expected: `AttributeError: module 'dirtywork.runs' has no attribute 'cmd_export'`

- [ ] **Step 3: Write the minimal implementation**

Add these imports to the top of `dirtywork/runs.py` (alongside the existing `from . import rundir` / `from .sandbox import docker_cli`):

```python
import os
from pathlib import Path

from .sandbox import docker_args, export
```

Append `cmd_export` to `dirtywork/runs.py`:

```python
def cmd_export(args) -> int:
    run_dir = rundir.RUNS_DIR / args.slug
    if not run_dir.is_dir():
        print(f"error: no such run '{args.slug}'", file=sys.stderr)
        return 2
    try:
        data = rundir.read_run_json(run_dir)
    except Exception as e:
        print(f"error: cannot read run.json for '{args.slug}': {e}", file=sys.stderr)
        return 2
    if data.get("sandbox") != "docker":
        print(f"error: run '{args.slug}' was not a docker-sandbox run; nothing to export",
             file=sys.stderr)
        return 2
    volume = data.get("volume")
    if not volume:
        print(f"error: run.json for '{args.slug}' has no volume recorded", file=sys.stderr)
        return 2

    try:
        cp = docker_cli.run(["volume", "inspect", volume], timeout=docker_cli.T_QUERY)
    except Exception as e:
        print(f"error: cannot query docker: {e}", file=sys.stderr)
        return 2
    if cp.returncode != 0:
        print(f"error: volume '{volume}' does not exist -- nothing to export "
             f"(it may already have been removed by 'runs clean')", file=sys.stderr)
        return 2

    repo = Path(data["repo"])
    worktree = Path(data["worktree"])
    try:
        objects_dir = docker_cli.validate_objects_dir(repo)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    image = data.get("image") or docker_args.DEFAULT_IMAGE
    try:
        image_ref = docker_cli.resolve_image(image)
    except Exception as e:
        print(f"error: cannot resolve image '{image}': {e}", file=sys.stderr)
        return 2

    cfg = docker_args.DockerConfig(image=image, max_patch_mb=args.max_patch_mb,
                                   keep_volume=args.keep_volume)
    uid, gid = (os.getuid(), os.getgid()) if hasattr(os, "getuid") else (1000, 1000)

    artifacts = export.export_run(
        cfg, slug=args.slug, base_commit=data["base_commit"], worktree=worktree,
        run_dir=run_dir, objects_dir=objects_dir, image_ref=image_ref, uid=uid, gid=gid,
        repo_label=docker_args.repo_label(Path(data["repo"])),
    )

    data["status"] = "completed" if artifacts.export_status == "ok" else "export_failed"
    data["export_status"] = artifacts.export_status
    data["diff_stat"] = artifacts.diff_stat
    data["patch_path"] = artifacts.patch_path
    rundir.write_run_json(run_dir, data)

    if artifacts.export_status != "ok":
        print(f"error: export failed: {artifacts.export_status}", file=sys.stderr)
        return 1
    print(f"exported '{args.slug}' into {worktree}")
    print(artifacts.diff_stat)
    return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_runs.py -q`
Expected: 13 passed

- [ ] **Step 5: Wire the `runs export` subparser into `dirtywork/__main__.py`**

```bash
python3 - <<'PY'
from pathlib import Path

p = Path("dirtywork/__main__.py")
text = p.read_text()

anchor = '    runs_show_p.add_argument("--diff", action="store_true")\n'
assert anchor in text, "runs_show_p anchor not found -- inspect the file by hand"
addition = (
    '\n'
    '    runs_export_p = runs_sub.add_parser("export", help="re-run the export flow for a run")\n'
    '    runs_export_p.add_argument("slug")\n'
    '    runs_export_p.add_argument("--max-patch-mb", type=int, default=10)\n'
    '    runs_export_p.add_argument("--keep-volume", action="store_true")\n'
)
text = text.replace(anchor, anchor + addition, 1)

old_dispatch = '{"list": runs_mod.cmd_list, "show": runs_mod.cmd_show}[args.runs_cmd](args)'
new_dispatch = ('{"list": runs_mod.cmd_list, "show": runs_mod.cmd_show, '
                '"export": runs_mod.cmd_export}[args.runs_cmd](args)')
assert old_dispatch in text, "runs dispatch dict not found -- inspect the file by hand"
text = text.replace(old_dispatch, new_dispatch, 1)

p.write_text(text)
print("added 'runs export' subparser and dispatch entry")
PY
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add dirtywork/runs.py dirtywork/__main__.py tests/test_runs.py
git commit -m "feat: add 'dirtywork runs export'"
```

---

### Task 11: `runs clean`

**Files:**
- Modify: `dirtywork/runs.py` (add `cmd_clean`)
- Modify: `dirtywork/__main__.py` (add the `runs clean` subparser + dispatch entry)
- Modify: `tests/test_runs.py` (add `cmd_clean` tests, with fake `docker` and real tmp repos/worktrees)

**Interfaces:**
- Consumes: `dirtywork.sandbox.docker_args.repo_label`; `dirtywork.sandbox.docker_cli.{run, T_QUERY, T_LIFECYCLE}`; `os.kill(pid, 0)`.
- Produces: `dirtywork.runs.cmd_clean(args) -> int`; CLI: `dirtywork runs clean <slug> | --all [--keep-transcript] [--force]`.

- [ ] **Step 1: Write the failing tests**

Add to the top of `tests/test_runs.py`:

```python
import shutil

from dirtywork.sandbox import docker_args
```

Append to `tests/test_runs.py`:

```python
class _FakeCompleted:
    def __init__(self, returncode, output=b""):
        self.returncode = returncode
        self.output = output


def _fake_docker_run(container_label=None, volume_label=None, rm_ok=True):
    def _run(argv, timeout=None):
        if argv and argv[0] == "inspect":
            return _FakeCompleted(1) if container_label is None else _FakeCompleted(0, container_label.encode())
        if argv[:2] == ["volume", "inspect"]:
            return _FakeCompleted(1) if volume_label is None else _FakeCompleted(0, volume_label.encode())
        if argv[0] == "rm" or argv[:2] == ["volume", "rm"]:
            return _FakeCompleted(0 if rm_ok else 1)
        return _FakeCompleted(1)
    return _run


def test_clean_skips_unlabeled_container(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": None,
        "container": "dw-slug1", "volume": None, "branch": None,
    })
    monkeypatch.setattr(runs.docker_cli, "run",
                        _fake_docker_run(container_label="other-slug\twrong-repo-label"))
    rc = runs.cmd_clean(argparse.Namespace(slug="slug1", all=False, keep_transcript=True, force=False))
    out = capsys.readouterr().out
    assert "labels do not match" in out
    assert rc == 1


def test_clean_skips_not_owned_by_current_user(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": None, "container": None, "volume": None,
    })
    real_stat = Path.stat

    def fake_stat(self, *a, **k):
        result = real_stat(self, *a, **k)
        if self == run_dir / "run.json":
            class _Fake:
                st_uid = result.st_uid + 1
            return _Fake()
        return result
    monkeypatch.setattr(Path, "stat", fake_stat)
    rc = runs.cmd_clean(argparse.Namespace(slug="slug1", all=False, keep_transcript=True, force=False))
    assert "not owned by the current user" in capsys.readouterr().out
    assert rc == 1


def test_clean_skips_running_with_alive_pid(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "running", "host_pid": os.getpid(), "repo": str(repo),
        "worktree": None, "container": None, "volume": None,
    })
    rc = runs.cmd_clean(argparse.Namespace(slug="slug1", all=False, keep_transcript=True, force=False))
    assert "host process is alive" in capsys.readouterr().out
    assert rc == 1


def test_clean_refuses_dead_pid_without_force(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "running", "host_pid": 999999, "repo": str(repo),
        "worktree": None, "container": None, "volume": None,
    })
    rc = runs.cmd_clean(argparse.Namespace(slug="slug1", all=False, keep_transcript=True, force=False))
    assert "dead host process" in capsys.readouterr().out
    assert rc == 1


def test_clean_removes_dead_pid_with_force(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _write_run(tmp_path / "runs", "slug1", {
        "status": "running", "host_pid": 999999, "repo": str(repo),
        "worktree": None, "container": None, "volume": None, "branch": None,
    })
    rc = runs.cmd_clean(argparse.Namespace(slug="slug1", all=False, keep_transcript=False, force=True))
    out = capsys.readouterr().out
    assert "removed-rundir" in out
    assert not run_dir.exists()
    assert rc == 0


def test_clean_removes_matching_container_and_volume(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    label = docker_args.repo_label(repo)
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": None,
        "container": "dw-slug1", "volume": "dw-slug1-work", "branch": None,
    })
    monkeypatch.setattr(runs.docker_cli, "run", _fake_docker_run(
        container_label=f"slug1\t{label}", volume_label=f"slug1\t{label}", rm_ok=True))
    rc = runs.cmd_clean(argparse.Namespace(slug="slug1", all=False, keep_transcript=False, force=False))
    out = capsys.readouterr().out
    assert "removed-container: dw-slug1" in out
    assert "removed-volume: dw-slug1-work" in out
    assert rc == 0


def test_clean_refuses_dirty_worktree_without_force(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = repo / ".worktrees" / "dw-slug1"
    _git(repo, "worktree", "add", "-b", "dirtywork/slug1", str(wt), "HEAD")
    (wt / "dirty.txt").write_text("uncommitted")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": str(wt),
        "container": None, "volume": None, "branch": "dirtywork/slug1",
    })
    rc = runs.cmd_clean(argparse.Namespace(slug="slug1", all=False, keep_transcript=True, force=False))
    out = capsys.readouterr().out
    assert "has uncommitted changes" in out
    assert wt.exists()
    assert rc == 1


def test_clean_force_removes_dirty_worktree_and_branch(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = repo / ".worktrees" / "dw-slug1"
    _git(repo, "worktree", "add", "-b", "dirtywork/slug1", str(wt), "HEAD")
    (wt / "dirty.txt").write_text("uncommitted")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": str(wt),
        "container": None, "volume": None, "branch": "dirtywork/slug1",
    })
    rc = runs.cmd_clean(argparse.Namespace(slug="slug1", all=False, keep_transcript=False, force=True))
    out = capsys.readouterr().out
    assert "removed-worktree" in out
    assert "removed-branch" in out
    assert not wt.exists()
    assert rc == 0
    branches = _git(repo, "branch", "--list", "dirtywork/slug1").stdout
    assert "dirtywork/slug1" not in branches


def test_clean_keep_transcript_preserves_transcript_and_run_json(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": None, "container": None, "volume": None,
    })
    (run_dir / "transcript.jsonl").write_text('{"event": "run_start"}\n')
    (run_dir / "diff.patch").write_text("stuff")
    rc = runs.cmd_clean(argparse.Namespace(slug="slug1", all=False, keep_transcript=True, force=False))
    assert rc == 0
    assert (run_dir / "transcript.jsonl").exists()
    assert (run_dir / "run.json").exists()
    assert not (run_dir / "diff.patch").exists()


def test_clean_all_processes_every_run_dir(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    for slug in ("a", "b"):
        _write_run(tmp_path / "runs", slug, {
            "status": "completed", "repo": str(repo), "worktree": None,
            "container": None, "volume": None,
        })
    rc = runs.cmd_clean(argparse.Namespace(slug=None, all=True, keep_transcript=False, force=False))
    assert rc == 0
    assert not (tmp_path / "runs" / "a").exists()
    assert not (tmp_path / "runs" / "b").exists()


def test_clean_unknown_slug_reports_skip(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    rc = runs.cmd_clean(argparse.Namespace(slug="nope", all=False, keep_transcript=True, force=False))
    assert "no such run" in capsys.readouterr().out
    assert rc == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_runs.py -q -k clean`
Expected: `AttributeError: module 'dirtywork.runs' has no attribute 'cmd_clean'`

- [ ] **Step 3: Write the minimal implementation**

Add `import shutil` to the top of `dirtywork/runs.py`. Append `cmd_clean` and its helpers:

```python
def _staleness(data: dict, force: bool):
    """Returns (is_stale: bool, why_not: str | None) per the SP2 §3 collision
    rule: not-running is always stale; running is stale only with a confirmed-
    dead host_pid AND --force."""
    status = data.get("status")
    if status != "running":
        return True, None
    host_pid = data.get("host_pid")
    if host_pid is None:
        return False, "status is 'running' and no host_pid is recorded to check"
    try:
        os.kill(host_pid, 0)
    except ProcessLookupError:
        if force:
            return True, None
        return False, "status is 'running' with a dead host process -- pass --force to confirm cleanup"
    except PermissionError:
        return False, "status is 'running' and the host process is alive (owned by another user)"
    return False, "status is 'running' and the host process is alive"


def _run_json_owned_by_current_user(run_dir: Path) -> bool:
    try:
        st = (run_dir / "run.json").stat()
    except OSError:
        return False
    # Windows has no uid ownership; Windows is unsupported until its integration
    # suite exists (spec §SP2.8), so failing closed here is the honest choice.
    return hasattr(os, "getuid") and st.st_uid == os.getuid()


def _clean_docker_resource(kind: str, name: str, repo: str, slug: str, log: list) -> None:
    """kind is 'container' or 'volume'. Only ever removes a resource whose
    dirtywork.run/dirtywork.repo labels match this exact run -- everything else
    (missing, unlabeled, or belonging to a different repo/run) is skipped and
    reported, never touched."""
    inspect_argv = (["inspect", "--format",
                     '{{index .Config.Labels "dirtywork.run"}}\t{{index .Config.Labels "dirtywork.repo"}}',
                     name] if kind == "container" else
                    ["volume", "inspect", "--format",
                     '{{index .Labels "dirtywork.run"}}\t{{index .Labels "dirtywork.repo"}}', name])
    try:
        cp = docker_cli.run(inspect_argv, timeout=docker_cli.T_QUERY)
    except Exception as e:
        log.append((f"skip-{kind}", f"'{name}': cannot inspect: {e}"))
        return
    if cp.returncode != 0:
        log.append((f"skip-{kind}", f"'{name}': not found (already removed?)"))
        return
    out = cp.output.decode("utf-8", errors="replace").strip()
    run_label, _, repo_label = out.partition("\t")
    if run_label != slug or repo_label != docker_args.repo_label(Path(repo)):
        log.append((f"skip-{kind}", f"'{name}': labels do not match this run -- never touching it"))
        return
    rm_argv = ["rm", "-f", name] if kind == "container" else ["volume", "rm", name]
    rm = docker_cli.run(rm_argv, timeout=docker_cli.T_LIFECYCLE)
    log.append((f"removed-{kind}" if rm.returncode == 0 else f"skip-{kind}", name))


def _clean_one(slug: str, *, keep_transcript: bool, force: bool) -> list:
    """Returns a list of (action, detail) tuples describing what happened.
    Any action starting with 'skip' means something was deliberately left
    alone -- never a silent no-op."""
    log = []
    run_dir = rundir.RUNS_DIR / slug
    if not run_dir.is_dir():
        log.append(("skip", f"'{slug}': no such run"))
        return log
    try:
        data = rundir.read_run_json(run_dir)
    except Exception as e:
        log.append(("skip", f"'{slug}': cannot read run.json: {e}"))
        return log

    if not _run_json_owned_by_current_user(run_dir):
        log.append(("skip", f"'{slug}': run.json not owned by the current user"))
        return log

    is_stale, why_not = _staleness(data, force)
    if not is_stale:
        log.append(("skip", f"'{slug}': {why_not}"))
        return log

    repo = data.get("repo", "")
    if data.get("container"):
        _clean_docker_resource("container", data["container"], repo, slug, log)
    if data.get("volume"):
        _clean_docker_resource("volume", data["volume"], repo, slug, log)

    worktree = data.get("worktree")
    if worktree and repo:
        dirty = False
        try:
            status_cp = subprocess.run(["git", "-C", worktree, "status", "--porcelain"],
                                       capture_output=True, text=True, timeout=10)
            dirty = status_cp.returncode != 0 or bool(status_cp.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            dirty = True  # fail closed: cannot confirm clean, treat as dirty
        if dirty and not force:
            log.append(("skip-worktree",
                       f"'{worktree}': has uncommitted changes (pass --force to remove anyway)"))
        else:
            rm = subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", worktree],
                                capture_output=True, text=True, timeout=30)
            log.append(("removed-worktree" if rm.returncode == 0 else "skip-worktree", worktree))
            branch = data.get("branch")
            if branch:
                br = subprocess.run(["git", "-C", repo, "branch", "-D", branch],
                                    capture_output=True, text=True, timeout=10)
                log.append(("removed-branch" if br.returncode == 0 else "skip-branch", branch))

    if keep_transcript:
        for child in run_dir.iterdir():
            if child.name not in ("transcript.jsonl", "run.json"):
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
        log.append(("kept-transcript", str(run_dir)))
    else:
        shutil.rmtree(run_dir, ignore_errors=True)
        log.append(("removed-rundir", str(run_dir)))

    return log


def cmd_clean(args) -> int:
    slugs = ([d.name for d in _iter_run_dirs(rundir.RUNS_DIR)] if args.all else [args.slug])
    any_skipped = False
    for slug in slugs:
        for action, detail in _clean_one(slug, keep_transcript=args.keep_transcript, force=args.force):
            print(f"{action}: {detail}")
            if action.startswith("skip"):
                any_skipped = True
    return 1 if any_skipped else 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_runs.py -q`
Expected: 22 passed

- [ ] **Step 5: Wire the `runs clean` subparser into `dirtywork/__main__.py`**

```bash
python3 - <<'PY'
from pathlib import Path

p = Path("dirtywork/__main__.py")
text = p.read_text()

anchor = '    runs_export_p.add_argument("--keep-volume", action="store_true")\n'
assert anchor in text, "runs_export_p anchor not found -- inspect the file by hand"
addition = (
    '\n'
    '    runs_clean_p = runs_sub.add_parser("clean", help="remove a run\'s container/volume/worktree")\n'
    '    runs_clean_p.add_argument("slug", nargs="?", default=None)\n'
    '    runs_clean_p.add_argument("--all", action="store_true")\n'
    '    runs_clean_p.add_argument("--keep-transcript", action="store_true")\n'
    '    runs_clean_p.add_argument("--force", action="store_true")\n'
)
text = text.replace(anchor, anchor + addition, 1)

old_dispatch = ('{"list": runs_mod.cmd_list, "show": runs_mod.cmd_show, '
                '"export": runs_mod.cmd_export}[args.runs_cmd](args)')
new_dispatch_block = (
    '        if args.runs_cmd == "clean" and not args.all and not args.slug:\n'
    '            _err("\'runs clean\' needs a slug or --all")\n'
    '            return 2\n'
    '        if args.runs_cmd == "clean" and args.all and args.slug:\n'
    '            _err("\'runs clean\' takes a slug or --all, not both")\n'
    '            return 2\n'
    '        return {"list": runs_mod.cmd_list, "show": runs_mod.cmd_show, '
    '"export": runs_mod.cmd_export, "clean": runs_mod.cmd_clean}[args.runs_cmd](args)'
)
assert old_dispatch in text, "runs dispatch dict not found -- inspect the file by hand"
text = text.replace(old_dispatch, new_dispatch_block, 1)

p.write_text(text)
print("added 'runs clean' subparser, dispatch entry, and slug/--all validation")
PY
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add dirtywork/runs.py dirtywork/__main__.py tests/test_runs.py
git commit -m "feat: add 'dirtywork runs clean' with the SP2 collision rule"
```

---

### Task 12: `runs verdict`

**Files:**
- Modify: `dirtywork/runs.py` (add `cmd_verdict`)
- Modify: `dirtywork/__main__.py` (add the `runs verdict` subparser + dispatch entry)
- Modify: `tests/test_runs.py` (add `cmd_verdict` tests)

**Interfaces:**
- Consumes: `datetime.{datetime, timezone}` (stdlib).
- Produces: `dirtywork.runs.cmd_verdict(args) -> int`; CLI: `dirtywork runs verdict <slug> accept|reject|cleanup [--note TEXT] [--review-seconds N]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runs.py`:

```python
def test_cmd_verdict_records_fields(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "ended": "2026-08-16T00:00:00+00:00",
    })
    rc = runs.cmd_verdict(argparse.Namespace(slug="slug1", verdict="accept",
                                             note="looks good", review_seconds=42))
    assert rc == 0
    data = json.loads((run_dir / "run.json").read_text())
    assert data["verdict"] == "accept"
    assert data["note"] == "looks good"
    assert data["review_seconds"] == 42
    assert "verdict_at" in data
    assert data["time_to_verdict_s"] >= 0


def test_cmd_verdict_missing_ended_leaves_time_to_verdict_none(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _write_run(tmp_path / "runs", "slug1", {"status": "running"})
    rc = runs.cmd_verdict(argparse.Namespace(slug="slug1", verdict="cleanup",
                                             note=None, review_seconds=None))
    assert rc == 0
    data = json.loads((run_dir / "run.json").read_text())
    assert data["time_to_verdict_s"] is None


def test_cmd_verdict_unknown_slug_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    rc = runs.cmd_verdict(argparse.Namespace(slug="nope", verdict="reject",
                                             note=None, review_seconds=None))
    assert rc == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_runs.py -q -k verdict`
Expected: `AttributeError: module 'dirtywork.runs' has no attribute 'cmd_verdict'`

- [ ] **Step 3: Write the minimal implementation**

Add `from datetime import datetime, timezone` to the top of `dirtywork/runs.py`. Append `cmd_verdict`:

```python
def cmd_verdict(args) -> int:
    run_dir = rundir.RUNS_DIR / args.slug
    if not run_dir.is_dir():
        print(f"error: no such run '{args.slug}'", file=sys.stderr)
        return 2
    try:
        data = rundir.read_run_json(run_dir)
    except Exception as e:
        print(f"error: cannot read run.json for '{args.slug}': {e}", file=sys.stderr)
        return 2

    verdict_at = datetime.now(timezone.utc).isoformat()
    data["verdict"] = args.verdict
    data["note"] = args.note
    data["verdict_at"] = verdict_at
    data["review_seconds"] = args.review_seconds

    ended = data.get("ended")
    data["time_to_verdict_s"] = None
    if ended:
        try:
            ended_dt = datetime.fromisoformat(str(ended).replace("Z", "+00:00"))
            verdict_dt = datetime.fromisoformat(verdict_at)
            data["time_to_verdict_s"] = (verdict_dt - ended_dt).total_seconds()
        except ValueError:
            pass

    rundir.write_run_json(run_dir, data)
    print(f"recorded verdict '{args.verdict}' for '{args.slug}'")
    return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_runs.py -q`
Expected: 25 passed

- [ ] **Step 5: Wire the `runs verdict` subparser into `dirtywork/__main__.py`**

```bash
python3 - <<'PY'
from pathlib import Path

p = Path("dirtywork/__main__.py")
text = p.read_text()

anchor = '    runs_clean_p.add_argument("--force", action="store_true")\n'
assert anchor in text, "runs_clean_p anchor not found -- inspect the file by hand"
addition = (
    '\n'
    '    runs_verdict_p = runs_sub.add_parser("verdict", help="record accept/reject/cleanup for a run")\n'
    '    runs_verdict_p.add_argument("slug")\n'
    '    runs_verdict_p.add_argument("verdict", choices=["accept", "reject", "cleanup"])\n'
    '    runs_verdict_p.add_argument("--note", default=None)\n'
    '    runs_verdict_p.add_argument("--review-seconds", type=float, default=None)\n'
)
text = text.replace(anchor, anchor + addition, 1)

old_entry = '"export": runs_mod.cmd_export, "clean": runs_mod.cmd_clean}[args.runs_cmd](args)'
new_entry = ('"export": runs_mod.cmd_export, "clean": runs_mod.cmd_clean, '
            '"verdict": runs_mod.cmd_verdict}[args.runs_cmd](args)')
assert old_entry in text, "runs dispatch dict not found -- inspect the file by hand"
text = text.replace(old_entry, new_entry, 1)

p.write_text(text)
print("added 'runs verdict' subparser and dispatch entry")
PY
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add dirtywork/runs.py dirtywork/__main__.py tests/test_runs.py
git commit -m "feat: add 'dirtywork runs verdict'"
```

---

### Task 13: Bench fixture repos (3) + `bench.json` schema

**Files:**
- Create: `bench/repos/py-fix-off-by-one/{sum_range.py, bench.json, acceptance/test_sum_range.py}`
- Create: `bench/repos/node-add-cli-flag/{greet.js, bench.json, acceptance/greet.test.js}`
- Create: `bench/repos/sh-fix-script/{report.sh, bench.json, acceptance/expected_output.txt, acceptance/check.sh}`
- Create: `tests/test_bench.py`

**Interfaces:**
- Consumes: `hashlib.sha256` (stdlib).
- Produces: three fixture repos, each `bench.json` = `{"task": str, "acceptance": {"command": str, "hashes": {"acceptance/<relpath>": "<sha256 hex>"}}}`.

Each fixture ships in its **unsolved** (buggy) state — the model's job in a bench run is to fix it so the acceptance command passes. `acceptance/` is git-tracked along with the source, so it travels into the worker's worktree; `bench.json`'s `hashes` let `bench summarize`/`bench run` (Task 14) detect a worker that tampered with its own copy of the acceptance harness instead of fixing the source — the actual acceptance *command*, per the spec, is always run against a separate, freshly-mounted read-only copy of `acceptance/`, never the worker's own.

- [ ] **Step 1: Write the failing tests**

`tests/test_bench.py`:

```python
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

BENCH_REPOS = Path(__file__).parent.parent / "bench" / "repos"
TASK_NAMES = ["py-fix-off-by-one", "node-add-cli-flag", "sh-fix-script"]


def _bench_json(task_name: str) -> dict:
    return json.loads((BENCH_REPOS / task_name / "bench.json").read_text())


def test_every_task_dir_exists_and_is_tiny():
    assert BENCH_REPOS.is_dir()
    for name in TASK_NAMES:
        task_dir = BENCH_REPOS / name
        assert task_dir.is_dir(), f"missing bench fixture: {name}"
        files = [p for p in task_dir.rglob("*") if p.is_file()]
        assert len(files) <= 5, f"{name} has {len(files)} files, expected <= 5"


def test_bench_json_schema():
    for name in TASK_NAMES:
        data = _bench_json(name)
        assert isinstance(data.get("task"), str) and data["task"]
        acceptance = data.get("acceptance")
        assert isinstance(acceptance, dict)
        assert isinstance(acceptance.get("command"), str) and acceptance["command"]
        assert isinstance(acceptance.get("hashes"), dict) and acceptance["hashes"]


def test_bench_json_hashes_match_files_on_disk():
    for name in TASK_NAMES:
        task_dir = BENCH_REPOS / name
        data = _bench_json(name)
        for rel_path, expected_hash in data["acceptance"]["hashes"].items():
            assert rel_path.startswith("acceptance/"), (
                f"{name}: hashed path '{rel_path}' is not under acceptance/")
            p = task_dir / rel_path
            assert p.is_file(), f"{name}: hashed path '{rel_path}' does not exist"
            actual = hashlib.sha256(p.read_bytes()).hexdigest()
            assert actual == expected_hash, (
                f"{name}: {rel_path} hash mismatch (fixture file changed since bench.json "
                f"was written -- recompute with hashlib.sha256(path.read_bytes()).hexdigest())")


def test_task_source_files_are_unsolved():
    # Fixtures ship the BUGGY state -- if the acceptance check already passes
    # against the fixture as committed, the task gives the model nothing to do.
    cases = [
        ("py-fix-off-by-one", ["python3", "-m", "pytest", "acceptance/test_sum_range.py", "-q"], None),
        ("sh-fix-script", ["bash", "acceptance/check.sh"], None),
        ("node-add-cli-flag", ["node", "--test", "acceptance/greet.test.js"], "node"),
    ]
    for name, command, needs in cases:
        if needs and shutil.which(needs) is None:
            continue  # optional runtime not installed in this environment
        task_dir = BENCH_REPOS / name
        result = subprocess.run(command, cwd=task_dir, capture_output=True, text=True)
        assert result.returncode != 0, f"{name}: acceptance check already passes on the unsolved fixture"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_bench.py -q`
Expected: `test_every_task_dir_exists_and_is_tiny` fails (`bench/repos` does not exist); the rest error alongside it.

- [ ] **Step 3: Create the `py-fix-off-by-one` fixture**

`bench/repos/py-fix-off-by-one/sum_range.py`:

```python
def sum_range(low, high):
    """Return the sum of integers from low to high, INCLUSIVE."""
    total = 0
    for i in range(low, high):  # BUG: excludes `high`; should be range(low, high + 1)
        total += i
    return total
```

`bench/repos/py-fix-off-by-one/acceptance/test_sum_range.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sum_range import sum_range


def test_sum_range_inclusive():
    assert sum_range(1, 5) == 15  # 1+2+3+4+5
    assert sum_range(3, 3) == 3
    assert sum_range(0, 10) == 55
```

`bench/repos/py-fix-off-by-one/bench.json` (the hash below is the real `sha256` of the exact `acceptance/test_sum_range.py` content above — recompute with `python3 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" acceptance/test_sum_range.py` if you change so much as a byte of that file):

```json
{
  "task": "sum_range(low, high) in sum_range.py should be inclusive of `high` but currently excludes it. Fix the bug so acceptance/test_sum_range.py passes.",
  "acceptance": {
    "command": "python3 -m pytest acceptance/test_sum_range.py -q",
    "hashes": {
      "acceptance/test_sum_range.py": "b23cfbfbc6794380d8fb2bdb88ba5c09e6e286ae657a1848dc184da983766aa0"
    }
  }
}
```

- [ ] **Step 4: Create the `node-add-cli-flag` fixture**

`bench/repos/node-add-cli-flag/greet.js`:

```javascript
#!/usr/bin/env node
// Prints a greeting. Missing: a --loud flag that should uppercase the output.
const args = process.argv.slice(2);
const name = args.find((a) => !a.startsWith("--")) || "world";
console.log(`Hello, ${name}!`);
```

`bench/repos/node-add-cli-flag/acceptance/greet.test.js`:

```javascript
const { test } = require("node:test");
const assert = require("node:assert");
const { execFileSync } = require("node:child_process");
const path = require("node:path");

const GREET = path.join(__dirname, "..", "greet.js");

test("prints a plain greeting", () => {
  const out = execFileSync("node", [GREET, "Ada"]).toString().trim();
  assert.strictEqual(out, "Hello, Ada!");
});

test("--loud uppercases the greeting", () => {
  const out = execFileSync("node", [GREET, "Ada", "--loud"]).toString().trim();
  assert.strictEqual(out, "HELLO, ADA!");
});
```

`bench/repos/node-add-cli-flag/bench.json`:

```json
{
  "task": "Add a --loud flag to greet.js: when passed, the printed greeting must be uppercased. See acceptance/greet.test.js for the exact expected behavior.",
  "acceptance": {
    "command": "node --test acceptance/greet.test.js",
    "hashes": {
      "acceptance/greet.test.js": "76db5cc077bbf45c6aabebfaccfaa9a54e772ef9867c6edadd577d72563336d1"
    }
  }
}
```

- [ ] **Step 5: Create the `sh-fix-script` fixture**

`bench/repos/sh-fix-script/report.sh`:

```bash
#!/usr/bin/env bash
# Prints a count report for the files given as arguments. BUG: drops the
# trailing newline the expected output requires.
set -euo pipefail
count=0
for f in "$@"; do
  count=$((count + 1))
done
printf 'files: %d' "$count"
```

`bench/repos/sh-fix-script/acceptance/expected_output.txt` (note the required trailing newline):

```
files: 3
```

`bench/repos/sh-fix-script/acceptance/check.sh`:

```bash
#!/usr/bin/env bash
# Acceptance check: report.sh a b c must print exactly the expected output,
# including the trailing newline this script's own diff requires.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
actual="$(bash "$here/../report.sh" a b c)"
expected="$(cat "$here/expected_output.txt")"
if [ "$actual" != "$expected" ]; then
  echo "FAIL: got $(printf '%q' "$actual"), want $(printf '%q' "$expected")" >&2
  exit 1
fi
echo "PASS"
```

`bench/repos/sh-fix-script/bench.json`:

```json
{
  "task": "report.sh a b c should print 'files: 3' followed by a trailing newline, matching acceptance/expected_output.txt. Fix report.sh so acceptance/check.sh passes.",
  "acceptance": {
    "command": "bash acceptance/check.sh",
    "hashes": {
      "acceptance/expected_output.txt": "c79ef4d283db0e99f72006e91c5e75804a69b342234958a7bd02afef01e90ea4",
      "acceptance/check.sh": "26f7c5820e019aab12b4f1332ebbc0de7275b3d75bf88a99d9fff4b174529b25"
    }
  }
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_bench.py -q`
Expected: 4 passed (`test_task_source_files_are_unsolved` skips the `node-add-cli-flag` case automatically if `node` is not installed in this environment, but still checks the other two)

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -q`
Expected: all green

- [ ] **Step 8: Commit**

```bash
git add bench/repos tests/test_bench.py
git commit -m "test: add three tiny bench fixture repos with bench.json"
```

---

### Task 14: `dirtywork bench` run command

**Files:**
- Create: `dirtywork/bench.py`
- Modify: `dirtywork/__main__.py` (add `run_once(argv) -> dict`; add the `bench` subparser + dispatch entry)
- Create: `tests/test_bench.py` additions (argv-exact tests, fake sandbox/provider via monkeypatched `run_once`/`docker_cli.run`)

**Interfaces:**
- Consumes: `dirtywork.rundir.{RUNS_DIR, read_run_json}`; `dirtywork.sandbox.docker_cli.{run, resolve_image, T_LIFECYCLE, T_EXPORT_STEP}`; `dirtywork.sandbox.docker_args.DEFAULT_IMAGE`.
- Produces: `dirtywork.__main__.run_once(argv: list) -> dict`; `dirtywork.bench.cmd_bench(args) -> int`; `dirtywork.bench.run_one_bench_case(model, task, repeat, provider, stamp, run=docker_cli.run) -> dict`; CLI: `dirtywork bench --models m1,m2 [--provider openai|anthropic] [--repeats N] [--tasks a,b] [--out FILE]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bench.py`:

```python
import argparse

from dirtywork import bench
from dirtywork.sandbox import docker_args


def test_hash_check_argv_exact():
    argv = bench._hash_check_argv("dw-slug-work", "img@sha256:deadbeef", 501, 20,
                                  ["/work/acceptance/test_x.py"])
    assert argv == [
        "run", "--rm", "--network", "none",
        "--user", "501:20", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--memory", "2g", "--pids-limit", "256",
        "--mount", "type=volume,src=dw-slug-work,dst=/work",
        "-e", f"PATH={docker_args.PATH_ENV}",
        "--entrypoint", "/usr/bin/sha256sum", "img@sha256:deadbeef",
        "/work/acceptance/test_x.py",
    ]


def test_acceptance_run_argv_exact(tmp_path):
    acceptance_dir = tmp_path / "acceptance"
    acceptance_dir.mkdir()
    argv = bench._acceptance_run_argv("dw-slug-work", "img@sha256:deadbeef", 501, 20,
                                      acceptance_dir, "bash acceptance/check.sh")
    assert argv[:11] == [
        "run", "--rm", "--network", "none",
        "--user", "501:20", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--memory", "2g", "--pids-limit", "256",
    ]
    assert "--mount" in argv
    assert f"type=volume,src=dw-slug-work,dst=/work" in argv
    assert f"type=bind,src={acceptance_dir.resolve()},dst=/acceptance,readonly" in argv
    assert f"PATH={docker_args.PATH_ENV}" in argv
    assert argv[-5:] == ["--entrypoint", "/bin/sh", "img@sha256:deadbeef", "-c",
                         "cd /work && bash acceptance/check.sh"]
    # export steps never get network, regardless of the run's --allow-network
    assert "none" in argv[argv.index("--network") + 1]


def test_stage_repo_creates_unique_committed_git_repo():
    d1 = bench._stage_repo("sh-fix-script")
    d2 = bench._stage_repo("sh-fix-script")
    try:
        assert d1 != d2
        for d in (d1, d2):
            assert (d / "report.sh").exists()
            log = subprocess.run(["git", "-C", str(d), "log", "--oneline"],
                                 capture_output=True, text=True)
            assert log.returncode == 0 and log.stdout.strip()
    finally:
        shutil.rmtree(d1, ignore_errors=True)
        shutil.rmtree(d2, ignore_errors=True)


class _FakeCompleted:
    def __init__(self, returncode, output=b""):
        self.returncode = returncode
        self.output = output


def test_run_acceptance_pass(monkeypatch, tmp_path):
    bench_data = bench._bench_json("sh-fix-script")
    expected_hashes = bench_data["acceptance"]["hashes"]

    def fake_run(argv, timeout=None):
        if "sha256sum" in argv:
            lines = [f"{h}  /work/{p}" for p, h in expected_hashes.items()]
            return _FakeCompleted(0, ("\n".join(lines) + "\n").encode())
        return _FakeCompleted(0)  # the acceptance command itself succeeds

    monkeypatch.setattr(bench.docker_cli, "resolve_image", lambda image: f"{image}@sha256:deadbeef")
    result = bench._run_acceptance("sh-fix-script", bench_data, "dw-x-work", run=fake_run)
    assert result == "pass"


def test_run_acceptance_fail(monkeypatch):
    bench_data = bench._bench_json("sh-fix-script")
    expected_hashes = bench_data["acceptance"]["hashes"]

    def fake_run(argv, timeout=None):
        if "sha256sum" in argv:
            lines = [f"{h}  /work/{p}" for p, h in expected_hashes.items()]
            return _FakeCompleted(0, ("\n".join(lines) + "\n").encode())
        return _FakeCompleted(1)  # the acceptance command fails

    monkeypatch.setattr(bench.docker_cli, "resolve_image", lambda image: f"{image}@sha256:deadbeef")
    result = bench._run_acceptance("sh-fix-script", bench_data, "dw-x-work", run=fake_run)
    assert result == "fail"


def test_run_acceptance_gamed_on_hash_mismatch(monkeypatch):
    bench_data = bench._bench_json("sh-fix-script")

    def fake_run(argv, timeout=None):
        if "sha256sum" in argv:
            # every hash wrong -- worker tampered with its own acceptance/ copy
            lines = [f"0000000000000000000000000000000000000000000000000000000000000000  /work/{p}"
                    for p in bench_data["acceptance"]["hashes"]]
            return _FakeCompleted(0, ("\n".join(lines) + "\n").encode())
        return _FakeCompleted(0)

    monkeypatch.setattr(bench.docker_cli, "resolve_image", lambda image: f"{image}@sha256:deadbeef")
    result = bench._run_acceptance("sh-fix-script", bench_data, "dw-x-work", run=fake_run)
    assert result == "gamed"


def test_run_one_bench_case_calls_run_once_and_removes_volume(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "BENCH_REPOS", bench.BENCH_REPOS)  # sanity: attribute exists
    monkeypatch.setattr(bench.rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = tmp_path / "runs" / "fixtask-0101-abcd"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"volume": "dw-fixtask-work", "diff_stat": "1 file changed"}))
    (run_dir / "transcript.jsonl").write_text(
        json.dumps({"event": "guardrail_block"}) + "\n" + json.dumps({"event": "run_start"}) + "\n")

    def fake_run_once(argv):
        return {"status": "completed", "turns": 3, "usage": {"prompt_tokens": 1, "completion_tokens": 1},
               "branch": "dirtywork/fixtask-0101-abcd"}
    monkeypatch.setattr(bench, "run_once", fake_run_once)
    monkeypatch.setattr(bench, "_run_acceptance", lambda *a, **k: "pass")
    removed = []
    monkeypatch.setattr(bench.docker_cli, "run",
                        lambda argv, timeout=None: removed.append(argv) or _FakeCompleted(0))
    monkeypatch.setattr(bench, "_stage_repo", lambda task: tmp_path / "staged")
    (tmp_path / "staged").mkdir()

    row = bench.run_one_bench_case("m1", "sh-fix-script", 0, "openai", "20260816T000000Z")
    assert row["status"] == "completed"
    assert row["acceptance"] == "pass"
    assert row["guardrail_blocks"] == 1
    assert row["slug"] == "fixtask-0101-abcd"
    assert any(argv[:2] == ["volume", "rm"] for argv in removed)


def test_cmd_bench_requires_models(capsys):
    rc = bench.cmd_bench(argparse.Namespace(bench_cmd=None, models=None, provider="openai",
                                            repeats=1, tasks=None, out=None))
    assert rc == 2
    assert "models is required" in capsys.readouterr().err


def test_cmd_bench_writes_jsonl_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "run_one_bench_case",
                        lambda model, task, repeat, provider, stamp: {
                            "stamp": stamp, "model": model, "task": task, "repeat": repeat,
                            "status": "completed", "acceptance": "pass"})
    out_file = tmp_path / "results.jsonl"
    rc = bench.cmd_bench(argparse.Namespace(bench_cmd=None, models="m1,m2", provider="openai",
                                            repeats=1, tasks="sh-fix-script", out=str(out_file)))
    assert rc == 0
    rows = [json.loads(l) for l in out_file.read_text().splitlines()]
    assert len(rows) == 2
    assert {r["model"] for r in rows} == {"m1", "m2"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_bench.py -q -k "hash_check_argv or acceptance_run_argv or stage_repo or run_acceptance or bench_case or cmd_bench"`
Expected: `ModuleNotFoundError: No module named 'dirtywork.bench'`

- [ ] **Step 3: Write `dirtywork/bench.py`**

```python
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import rundir
from .__main__ import run_once
from .sandbox import docker_args, docker_cli

BENCH_REPOS = Path(__file__).parent.parent / "bench" / "repos"
BENCH_HOME = Path.home() / ".dirtywork" / "bench"


def _bench_json(task: str) -> dict:
    return json.loads((BENCH_REPOS / task / "bench.json").read_text())


def _stage_repo(task: str) -> Path:
    """Copies bench/repos/<task> into a uniquely-named temp dir and commits it.
    Docker Desktop caches deleted bind-mount source paths (see the design
    spec's residual exposures), so bench must never reuse a path across runs
    -- tempfile.mkdtemp's random suffix guarantees a fresh path every call."""
    src = BENCH_REPOS / task
    dest = Path(tempfile.mkdtemp(prefix=f"dwbench-{task}-"))
    shutil.rmtree(dest)  # mkdtemp already created it; copytree needs it absent
    shutil.copytree(src, dest)
    subprocess.run(["git", "-C", str(dest), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(dest), "-c", "user.email=bench@dirtywork.local",
                    "-c", "user.name=dirtywork-bench", "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(dest), "-c", "user.email=bench@dirtywork.local",
                    "-c", "user.name=dirtywork-bench", "commit", "-q", "-m", "bench fixture"],
                   check=True)
    return dest


def _base_acceptance_argv(volume: str, image_ref: str, uid: int, gid: int, extra_mounts=()) -> list:
    argv = [
        "run", "--rm", "--network", "none",
        "--user", f"{uid}:{gid}", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--memory", "2g", "--pids-limit", "256",
        "--mount", f"type=volume,src={volume},dst=/work",
        "-e", f"PATH={docker_args.PATH_ENV}",   # spec: every docker run passes an explicit PATH + --entrypoint
    ]
    for mount in extra_mounts:
        argv += ["--mount", mount]
    return argv


def _hash_check_argv(volume: str, image_ref: str, uid: int, gid: int, paths: list) -> list:
    return (_base_acceptance_argv(volume, image_ref, uid, gid)
            + ["--entrypoint", "/usr/bin/sha256sum", image_ref] + paths)


def _acceptance_run_argv(volume: str, image_ref: str, uid: int, gid: int,
                         acceptance_dir: Path, command: str) -> list:
    extra = [f"type=bind,src={acceptance_dir.resolve()},dst=/acceptance,readonly"]
    return (_base_acceptance_argv(volume, image_ref, uid, gid, extra_mounts=extra)
            + ["--entrypoint", "/bin/sh", image_ref, "-c", f"cd /work && {command}"])


def _run_acceptance(task: str, bench_data: dict, volume: str, *, run=docker_cli.run) -> str:
    """Returns 'pass' | 'fail' | 'gamed' | 'skipped'. Never raises -- a docker
    failure here degrades to 'skipped' rather than aborting the whole bench run."""
    task_dir = BENCH_REPOS / task
    image = docker_args.DEFAULT_IMAGE
    try:
        image_ref = docker_cli.resolve_image(image)
    except Exception:
        return "skipped"
    uid, gid = (os.getuid(), os.getgid()) if hasattr(os, "getuid") else (1000, 1000)

    hashes = bench_data["acceptance"]["hashes"]
    paths = [f"/work/{p}" for p in hashes]
    try:
        cp = run(_hash_check_argv(volume, image_ref, uid, gid, paths), timeout=docker_cli.T_EXPORT_STEP)
    except Exception:
        return "skipped"
    if cp.returncode not in (0, 1):
        return "skipped"   # docker itself failed (125/126/127); not the worker's doing
    # rc 1 = sha256sum could not read at least one harness file (deleted/moved by
    # the worker) — that is a mismatch, and falls through to "gamed" below.
    actual = {}
    for line in cp.output.decode("utf-8", errors="replace").splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            digest, path = parts
            actual[path.lstrip("*").removeprefix("/work/")] = digest
    for rel_path, expected in hashes.items():
        if actual.get(rel_path) != expected:
            return "gamed"

    try:
        cp = run(_acceptance_run_argv(volume, image_ref, uid, gid,
                                      task_dir / "acceptance", bench_data["acceptance"]["command"]),
                timeout=docker_cli.T_EXPORT_STEP)
    except Exception:
        return "skipped"
    return "pass" if cp.returncode == 0 else "fail"


def _count_events(run_dir: Path, event_name: str) -> int:
    transcript_path = run_dir / "transcript.jsonl"
    if not transcript_path.is_file():
        return 0
    count = 0
    for line in transcript_path.read_text().splitlines():
        try:
            if json.loads(line).get("event") == event_name:
                count += 1
        except json.JSONDecodeError:
            continue
    return count


def run_one_bench_case(model: str, task: str, repeat: int, provider: str, stamp: str) -> dict:
    bench_data = _bench_json(task)
    repo_dir = _stage_repo(task)
    wall_start = time.monotonic()
    try:
        payload = run_once(["run", bench_data["task"], "--repo", str(repo_dir),
                            "--model", model, "--provider", provider,
                            "--sandbox", "docker", "--keep-volume"])
    except Exception as e:
        return {"stamp": stamp, "model": model, "task": task, "repeat": repeat,
               "status": "bench_error", "error": str(e),
               "wall_s": round(time.monotonic() - wall_start, 1), "acceptance": "skipped", "slug": None}
    wall_s = round(time.monotonic() - wall_start, 1)

    branch = payload.get("branch") or ""
    slug = branch.split("/", 1)[1] if "/" in branch else branch or None
    run_dir = rundir.RUNS_DIR / slug if slug else None
    run_json = {}
    if run_dir is not None and run_dir.is_dir():
        try:
            run_json = rundir.read_run_json(run_dir)
        except Exception:
            run_json = {}
    volume = run_json.get("volume")

    acceptance = "skipped"
    if volume and payload.get("status") == "completed":
        acceptance = _run_acceptance(task, bench_data, volume)

    if volume:
        try:
            docker_cli.run(["volume", "rm", volume], timeout=docker_cli.T_LIFECYCLE)
        except Exception:
            pass

    return {
        "stamp": stamp, "model": model, "task": task, "repeat": repeat,
        "status": payload.get("status"), "turns": payload.get("turns"),
        "usage": payload.get("usage"), "wall_s": wall_s,
        "guardrail_blocks": _count_events(run_dir, "guardrail_block") if run_dir else 0,
        "sandbox_resets": _count_events(run_dir, "sandbox_reset") if run_dir else 0,
        "diff_stat": run_json.get("diff_stat"),
        "acceptance": acceptance, "slug": slug,
    }


def cmd_bench(args) -> int:
    if getattr(args, "bench_cmd", None) == "summarize":
        return cmd_summarize(args)
    if not args.models:
        print("error: --models is required", file=sys.stderr)
        return 2
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    tasks = ([t.strip() for t in args.tasks.split(",") if t.strip()] if args.tasks
            else sorted(d.name for d in BENCH_REPOS.iterdir() if d.is_dir()))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out) if args.out else (BENCH_HOME / f"{stamp}.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "a", encoding="utf-8") as fh:
        for model in models:
            for task in tasks:
                for repeat in range(args.repeats):
                    row = run_one_bench_case(model, task, repeat, args.provider, stamp)
                    fh.write(json.dumps(row) + "\n")
                    fh.flush()
    print(f"results: {out_path}")
    return 0
```

`cmd_summarize` is referenced but not yet defined — that reference is inside the `if getattr(args, "bench_cmd", None) == "summarize":` branch, which cannot be reached yet (no plan step has added `"summarize"` as a valid `bench_cmd` choice), so this is not a `NameError` at import time, only at that (currently unreachable) call time. Task 15 defines it in this same module.

- [ ] **Step 4: Add `run_once` and the `bench` subparser to `dirtywork/__main__.py`**

```bash
python3 - <<'PY'
from pathlib import Path

p = Path("dirtywork/__main__.py")
text = p.read_text()

old_imports = "import argparse\nimport json\nimport sys\n"
assert old_imports in text, "top-of-file import block changed shape -- inspect the file by hand"
new_imports = "import argparse\nimport contextlib\nimport io\nimport json\nimport sys\n"
text = text.replace(old_imports, new_imports, 1)

parse_anchor = '    args = parser.parse_args(argv)\n'
assert parse_anchor in text
bench_block = (
    '\n'
    '    bench_p = sub.add_parser("bench", help="benchmark models against fixture tasks")\n'
    '    bench_p.add_argument("--models", default=None)\n'
    '    bench_p.add_argument("--provider", default="openai", choices=["openai", "anthropic"])\n'
    '    bench_p.add_argument("--repeats", type=int, default=1)\n'
    '    bench_p.add_argument("--tasks", default=None)\n'
    '    bench_p.add_argument("--out", default=None)\n'
)
text = text.replace(parse_anchor, bench_block + parse_anchor, 1)

main_def_marker = "def main("
assert text.count(main_def_marker) == 1, "'def main(' is not unique -- inspect the file by hand"
run_once_func = (
    'def run_once(argv: list) -> dict:\n'
    '    """Run one dirtywork invocation in-process (no subprocess) and return the\n'
    '    parsed stdout JSON payload. Relies on the machine contract: exactly one\n'
    '    JSON object on stdout after preflight -- bench needs many of these per\n'
    '    invocation and a subprocess per run would be far slower."""\n'
    '    buf = io.StringIO()\n'
    '    err_buf = io.StringIO()\n'
    '    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err_buf):\n'
    '        rc = main(argv)\n'
    '    text = buf.getvalue()\n'
    '    if not text.strip():\n'
    '        raise RuntimeError(f"dirtywork produced no stdout JSON (exit {rc}): '
    '{err_buf.getvalue().strip()}")\n'
    '    return json.loads(text)\n'
    '\n'
    '\n'
)
text = text.replace(main_def_marker, run_once_func + main_def_marker, 1)

old_entry = '    raise AssertionError(f"unhandled subcommand {args.cmd!r}")\n'
new_entry = (
    '    if args.cmd == "bench":\n'
    '        from . import bench as bench_mod\n'
    '        return bench_mod.cmd_bench(args)\n'
    '    raise AssertionError(f"unhandled subcommand {args.cmd!r}")\n'
)
assert old_entry in text, "dispatch fallthrough line not found -- inspect the file by hand"
text = text.replace(old_entry, new_entry, 1)

p.write_text(text)
print("added run_once(), 'bench' subparser, and bench dispatch")
PY
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_bench.py -q`
Expected: all pass

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add dirtywork/bench.py dirtywork/__main__.py tests/test_bench.py
git commit -m "feat: add 'dirtywork bench' run command with hash-based gamed detection"
```

---

### Task 15: `bench summarize`

**Files:**
- Modify: `dirtywork/bench.py` (add `cmd_summarize`)
- Modify: `dirtywork/__main__.py` (add the `bench summarize` sub-subparser)
- Modify: `tests/test_bench.py` (add `cmd_summarize` tests)

**Interfaces:**
- Consumes: `statistics.median` (stdlib); `dirtywork.rundir.{RUNS_DIR, read_run_json}` (to join each row's `slug` against its run's recorded verdict).
- Produces: `dirtywork.bench.cmd_summarize(args) -> int`; CLI: `dirtywork bench summarize <file>`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bench.py`:

```python
def test_summarize_prints_per_model_stats(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bench.rundir, "RUNS_DIR", tmp_path / "runs")
    results = tmp_path / "results.jsonl"
    rows = [
        {"model": "m1", "task": "t", "repeat": 0, "status": "completed", "acceptance": "pass",
         "usage": {"prompt_tokens": 10, "completion_tokens": 5}, "wall_s": 2.0, "slug": "s1"},
        {"model": "m1", "task": "t", "repeat": 1, "status": "completed", "acceptance": "fail",
         "usage": {"prompt_tokens": 20, "completion_tokens": 10}, "wall_s": 4.0, "slug": "s2"},
        {"model": "m2", "task": "t", "repeat": 0, "status": "max_turns", "acceptance": "skipped",
         "usage": {"prompt_tokens": 5, "completion_tokens": 1}, "wall_s": 1.0, "slug": "s3"},
    ]
    results.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    for slug, verdict, review in [("s1", "accept", 30), ("s2", "reject", 90)]:
        run_dir = tmp_path / "runs" / slug
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({"verdict": verdict, "review_seconds": review}))

    rc = bench.cmd_summarize(argparse.Namespace(file=str(results)))
    assert rc == 0
    out = capsys.readouterr().out
    assert "model: m1" in out
    assert "runs: 2" in out
    assert "completion rate: 100%" in out
    assert "acceptance rate: 50%" in out
    assert "verdict rate: 50%" in out
    assert "median review_seconds: 60" in out
    assert "model: m2" in out


def test_summarize_missing_file_exits_2(tmp_path, capsys):
    rc = bench.cmd_summarize(argparse.Namespace(file=str(tmp_path / "nope.jsonl")))
    assert rc == 2


def test_cmd_bench_dispatches_to_summarize(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bench.rundir, "RUNS_DIR", tmp_path / "runs")
    results = tmp_path / "r.jsonl"
    results.write_text(json.dumps({"model": "m1", "status": "completed", "acceptance": "pass",
                                   "usage": {}, "wall_s": 1.0, "slug": None}) + "\n")
    rc = bench.cmd_bench(argparse.Namespace(bench_cmd="summarize", file=str(results),
                                            models=None, provider="openai", repeats=1,
                                            tasks=None, out=None))
    assert rc == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_bench.py -q -k summarize`
Expected: `AttributeError: module 'dirtywork.bench' has no attribute 'cmd_summarize'`

- [ ] **Step 3: Write the minimal implementation**

Add `import statistics` to the top of `dirtywork/bench.py`. Append `_summarize_model` and `cmd_summarize`:

```python
def _summarize_model(rows: list) -> dict:
    n = len(rows)
    completed = sum(1 for r in rows if r.get("status") == "completed")
    accepted = sum(1 for r in rows if r.get("acceptance") == "pass")
    token_totals = [
        (r.get("usage") or {}).get("prompt_tokens", 0) + (r.get("usage") or {}).get("completion_tokens", 0)
        for r in rows if r.get("usage")
    ]
    wall_times = [r["wall_s"] for r in rows if isinstance(r.get("wall_s"), (int, float))]

    verdicts = []
    review_seconds = []
    for r in rows:
        slug = r.get("slug")
        if not slug:
            continue
        run_dir = rundir.RUNS_DIR / slug
        if not run_dir.is_dir():
            continue
        try:
            run_data = rundir.read_run_json(run_dir)
        except Exception:
            continue
        if "verdict" in run_data:
            verdicts.append(run_data["verdict"])
        if isinstance(run_data.get("review_seconds"), (int, float)):
            review_seconds.append(run_data["review_seconds"])

    return {
        "runs": n,
        "completion_rate": completed / n if n else 0.0,
        "acceptance_rate": accepted / n if n else 0.0,
        "mean_tokens": (sum(token_totals) / len(token_totals)) if token_totals else None,
        "mean_wall_s": (sum(wall_times) / len(wall_times)) if wall_times else None,
        "verdict_rate": (verdicts.count("accept") / len(verdicts)) if verdicts else None,
        "median_review_seconds": statistics.median(review_seconds) if review_seconds else None,
    }


def cmd_summarize(args) -> int:
    path = Path(args.file)
    if not path.is_file():
        print(f"error: no such file '{path}'", file=sys.stderr)
        return 2
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    by_model = {}
    for row in rows:
        by_model.setdefault(row.get("model", "?"), []).append(row)

    for model in sorted(by_model):
        summary = _summarize_model(by_model[model])
        print(f"model: {model}")
        print(f"  runs: {summary['runs']}")
        print(f"  completion rate: {summary['completion_rate']:.0%}")
        print(f"  acceptance rate: {summary['acceptance_rate']:.0%}")
        print(f"  mean tokens: {summary['mean_tokens']:.1f}"
             if summary["mean_tokens"] is not None else "  mean tokens: n/a")
        print(f"  mean wall_s: {summary['mean_wall_s']:.1f}"
             if summary["mean_wall_s"] is not None else "  mean wall_s: n/a")
        if summary["verdict_rate"] is not None:
            print(f"  verdict rate: {summary['verdict_rate']:.0%}")
            print(f"  median review_seconds: {summary['median_review_seconds']}")
        print()
    return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_bench.py -q`
Expected: all pass

- [ ] **Step 5: Wire the `bench summarize` sub-subparser into `dirtywork/__main__.py`**

```bash
python3 - <<'PY'
from pathlib import Path

p = Path("dirtywork/__main__.py")
text = p.read_text()

anchor = '    bench_p.add_argument("--out", default=None)\n'
assert anchor in text, "bench_p anchor not found -- inspect the file by hand"
addition = (
    '\n'
    '    bench_sub = bench_p.add_subparsers(dest="bench_cmd")\n'
    '    bench_summarize_p = bench_sub.add_parser("summarize", help="summarize a bench results file")\n'
    '    bench_summarize_p.add_argument("file")\n'
)
text = text.replace(anchor, anchor + addition, 1)

p.write_text(text)
print("added 'bench summarize' sub-subparser")
PY
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: all green — this is the last task; the full suite now includes every test file from Tasks 1–15 plus everything SP1/SP2 already shipped.

- [ ] **Step 7: Commit**

```bash
git add dirtywork/bench.py dirtywork/__main__.py tests/test_bench.py
git commit -m "feat: add 'dirtywork bench summarize'"
```

---

## Self-review: spec coverage

Maps every bullet of spec §"Sub-project 3: Extensibility" (§1–§5 + Sequencing), plus the SP2 §3/§7 pieces this plan reuses, to the task(s) that implement it.

| Spec item | Task(s) |
|---|---|
| §1 `MISSING`, `ParamSpec`, `Caps`, `ToolSpec`, `ToolResult`, `ToolRegistry.register`/`.schemas` | Task 1 |
| §1 `ToolRegistry.execute`: required/type validation, unknown-parameter rejection, `Caps` enforcement (input size, output cap, timeout clamp to deadline/`timeout_max`) | Task 2 |
| §1 `kind="blocked"` for `BLOCKED:` results + `guardrail_block` transcript event | Task 2 |
| §1 six tools become `ToolSpec`s; `TOOL_SCHEMAS`, `ToolExecutor`, and the runner's ad-hoc `except TypeError` go away | Task 3 |
| §2 `ToolCall`, `ChatResponse`, `Provider` Protocol dataclasses | Task 4 |
| §2 Contract suite: system prompt handling; parallel tool calls; malformed tool call; tool results in order; `finish_reason` mapping; usage normalization; `max_tokens` cutoff mid-call — against recorded fixtures for both wire formats | Task 5 (suite + OpenAI fixtures), Task 7 (Anthropic fixtures) |
| §2 Runner keeps a provider-neutral history and never sees wire shapes | Task 6 |
| §2 `OpenAICompatClient` (rename of `LMStudioClient`, alias kept); `_valid_tool_call`/`_canonical_tool_call`/usage sanitizing absorbed as deserialization | Task 5 |
| §2 `AnthropicClient`: urllib, `ANTHROPIC_API_KEY` read host-side, top-level `system`, `tool_use`/`tool_result` blocks, `/v1/models` preflight, `input_tokens`/`output_tokens` → prompt/completion | Task 7 |
| §2 Anthropic adapter written after the contract suite passes for OpenAI | Task 7 (sequenced after Task 5) |
| §2 `trim_messages` operates on the neutral history | Task 6 |
| §2 `CONTEXT_WINDOWS` → per-provider defaults with `--context-window` override | Task 6 (CLI flag + `Runner.context_window` param), Task 5/7 (`context_window(model)` per adapter) |
| §2 CLI `--provider openai\|anthropic` (default `openai`), `--base-url` (default per provider) | Task 6 |
| §2 `run_start` records `provider` | Task 6 |
| §3 `schema_version: 2` on `run_start` and in the stdout JSON | Task 8 (regression-tested; shipped by SP2) |
| §3 `docs/transcript-schema.md` documents every event and field, v1 vs v2 | Task 8 |
| §3 `run.json` written at start, updated at end | Task 8 doc references it; Task 10 (`runs export` updates it), Task 12 (`runs verdict` updates it) |
| §4 `runs list` | Task 9 |
| §4 `runs show <slug> [--diff]` | Task 9 |
| §4 `runs export <slug>` | Task 10 |
| §4 `runs clean <slug> \| --all [--keep-transcript] [--force]` | Task 11 |
| §4 `runs verdict <slug> accept\|reject\|cleanup [--note] [--review-seconds N]` | Task 12 |
| §5 `bench/repos/<name>/` fixture repos with `bench.json` (`task`, `acceptance`) and an `acceptance/` harness dir, ≤ 5 files each, unsolved state | Task 13 |
| §5 `dirtywork bench --models ... [--provider] [--repeats N] [--tasks ...]` runs each (model × task × repeat) through the normal `run` path with `--keep-volume` | Task 14 |
| §5 Acceptance in a fresh container, `acceptance/` mounted read-only, hash check against recorded hashes marks a run `gamed` | Task 14 |
| §5 Volume removed afterwards; results appended to `~/.dirtywork/bench/<stamp>.jsonl` | Task 14 |
| §5 `bench summarize <file>` prints completion/acceptance rate, mean tokens/latency, verdict rate / median review seconds where verdicts exist | Task 15 |
| Sequencing: Registry → providers (contract suite, OpenAI, then Anthropic) → schema/`run.json` docs → `runs` commands → bench | Task order 1–3 → 4–7 → 8 → 9–12 → 13–15 (this plan's task order matches exactly) |
| SP2 §3 collision rule (labels match, `run.json` owned by current user, run definitively stale or `--force`) — consumed by `runs clean` | Task 11 |
| SP2 §7 export flow (`export_run`) — reused by `runs export` | Task 10 |

**Spec items not mapped to any task:** none.

## Type consistency checklist

Every name below is used exactly as declared in the shared brief's "SP3 introduces" section; each row cites the task that defines it and the task(s) that consume it.

| Name | Defined in | Signature as used | Consumed by |
|---|---|---|---|
| `MISSING` | Task 1 (`toolspec.py`) | sentinel object, falsy, `repr() == "MISSING"` | Tasks 2, 3 |
| `ParamSpec` | Task 1 | `ParamSpec(type: str, description: str = "", default: Any = MISSING)` | Tasks 2, 3 |
| `Caps` | Task 1 | `Caps(fs, network=False, max_input_bytes=None, max_output_chars=8000, timeout_default=None, timeout_max=None, transcript="preview")` | Tasks 2, 3 |
| `ToolSpec` | Task 1 | `ToolSpec(name, description, params, required, fn, caps)` | Tasks 2, 3 |
| `ToolResult` | Task 1 | `ToolResult(text: str, kind: str)` | Tasks 2, 3, 6 |
| `ToolValidationError` | Task 1 | `Exception` subclass, internal to `toolspec.py` | Task 2 |
| `ToolRegistry` | Task 1 (register/schemas), Task 2 (execute) | `ToolRegistry(transcript=None)`; `.register(spec)`; `.schemas() -> list[dict]`; `.execute(name, args, *, sandbox, deadline) -> ToolResult`; `.spec(name)` | Tasks 3, 6, 9 |
| `default_registry` | Task 3 (`builtin_tools.py`) | `default_registry(transcript=None) -> ToolRegistry` | Task 6 (`__main__.py`), Task 9 (test scaffolding) |
| `ToolCall` | Task 4 (`providers/__init__.py`) | `ToolCall(id: str, name: str, arguments: dict \| None, error: str \| None)` | Tasks 5, 6, 7 |
| `ChatResponse` | Task 4 | `ChatResponse(text, tool_calls=[], finish_reason=None, usage={})` | Tasks 5, 6, 7 |
| `Provider` | Task 4 | Protocol: `name`, `list_models()`, `context_window(model)`, `chat(model, history, tools, *, temperature, max_tokens, timeout)` | Tasks 5, 6, 7 |
| `DEFAULT_BASE_URLS` | Task 4 | `{"openai": "http://localhost:1234/v1", "anthropic": "https://api.anthropic.com"}` | Tasks 5, 6, 7 |
| `get_provider` | Task 4 | `get_provider(name, base_url=None, timeout=600) -> Provider` | Task 6 (`__main__.py`) |
| `assistant_message` / `tool_message` | Task 4 | `assistant_message(text, tool_calls=None) -> dict`; `tool_message(call_id, text) -> dict` | Tasks 5, 6, 7 |
| `OpenAICompatClient` | Task 5 (`providers/openai_compat.py`) | `OpenAICompatClient(base_url=..., timeout=600, *, http_json=http_json)`, `name = "openai"` | Task 6 (via `get_provider`), `llm.LMStudioClient` alias |
| `http_json` | Task 5 (moved from the old `LMStudioClient._request`, now in `llm.py`) | `http_json(url, payload, headers, timeout, *, method="POST") -> dict` | Tasks 5, 7 |
| `LMStudioClient` | Task 5 (`llm.py`, now an alias) | `LMStudioClient = OpenAICompatClient` | unchanged external import surface |
| `ProviderContract` | Task 5 (`tests/provider_contract.py`) | mixin: `fixtures_dir`, `make_client(transport)`, seven `test_*` methods | Tasks 5, 7 (subclassed) |
| `AnthropicClient` | Task 7 (`providers/anthropic.py`) | `AnthropicClient(base_url=..., timeout=600, *, http_json=http_json, api_key=None)`, `name = "anthropic"` | Task 6 (via `get_provider`) |
| `Runner` | Task 3 (interim: registry+sandbox), Task 6 (final: provider-neutral) | `Runner(provider, registry, sandbox, transcript, model, max_turns=40, timeout=1800, temperature=None, run_info=None, finalize=None, context_window=None)` | `__main__.cmd_run` |
| `cmd_run` | Task 9 (extracted from `main()`) | `cmd_run(args) -> int` | `main()` dispatch |
| `run_once` | Task 14 (`__main__.py`) | `run_once(argv: list) -> dict` | `bench.run_one_bench_case` |
| `cmd_list` / `cmd_show` / `cmd_export` / `cmd_clean` / `cmd_verdict` | Tasks 9, 10, 11, 12 (`runs.py`) | each `(args) -> int` | `main()`'s `runs` dispatch dict |
| `cmd_bench` / `cmd_summarize` | Tasks 14, 15 (`bench.py`) | each `(args) -> int` | `main()`'s `bench` dispatch |
| `run_one_bench_case` | Task 14 (`bench.py`) | `run_one_bench_case(model, task, repeat, provider, stamp) -> dict` | `cmd_bench` |

No name in the shared brief's "SP3 introduces" list is redefined with a different shape anywhere in this plan.
