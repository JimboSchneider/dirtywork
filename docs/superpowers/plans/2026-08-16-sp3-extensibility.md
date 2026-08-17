# SP3 Extensibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make dirtywork extensible without surgery on `tools.py`/`runner.py`: a new tool becomes one `ToolSpec`, a new LLM provider becomes one adapter class passing a shared contract suite, and the operator gets `dirtywork runs …` (list/show/export/clean/verdict) and `dirtywork bench` (per-model completion/acceptance/token/latency numbers) on top of the sandboxed, hardened runtime that sub-projects 1, 2 and 2.5 already shipped (dirtywork **v0.5.1** on `main`).

**Architecture:** A generic `ToolRegistry` (`dirtywork/toolspec.py`) validates arguments and enforces per-tool `Caps` (input/output size, timeout, transcript verbosity) against hand-rolled `ToolSpec` declarations; the **seven** tools that ship today (`read_file`, `write_file`, `edit_file`, `list_dir`, `grep`, `bash`, and the terminal `finish`) become `ToolSpec`s in `dirtywork/builtin_tools.py` whose `fn` calls the `Sandbox` protocol SP2 introduced. The registry also owns `canonical_args(name, args)` — the effective-argument normalizer SP2.5's `ProgressTracker` depends on — so stall detection keeps working unchanged. A `Provider` protocol (`dirtywork/providers/`) gives the runner a provider-neutral chat history and `ChatResponse`; `OpenAICompatClient` (moved out of `llm.py`, which keeps only the bounded `http_json` transport) and a new `AnthropicClient` both pass one shared `tests/provider_contract.py` suite against recorded wire fixtures. `dirtywork/runner.py` is **refactored in place** (not rewritten) to drive `provider.chat(...)` and `registry.execute(...)`; every SP2.5 behaviour — `FailureTracker` (per-kind and total consecutive-failure thresholds), `strip_think`/`classify_text_reply`/`NUDGES`/`_join_nudges`, `ProgressTracker`/`stalled`, `finish` interception, `trim_messages`, `resolve_context_window` — is preserved, with `resolve_context_window` extended to consult the provider. `dirtywork/runs.py` and `dirtywork/bench.py` are new CLI-facing modules built on SP1's `rundir.py`, SP2's `docker_cli.py`/`export.py`/labels, and SP2.5's `resume.py`.

**Tech Stack:** Python ≥3.9, stdlib only (`dataclasses`, `typing`, `argparse`, `json`, `subprocess`, `urllib`, `tarfile`, `hashlib`, `tempfile`). Dev-only dependency: pytest.

**Spec:** `docs/superpowers/specs/2026-08-15-review-response-design.md` — "Sub-project 3: Extensibility" (§1–§5 + Sequencing), plus §SP2.3 (name-collision rule, consumed by `runs clean`) and §SP2.7 (export flow, reused by `runs export`).

---

## Revision 2 (2026-08-17) — re-baselined on v0.5.1

Revision 1 of this plan was written against pre-SP2.5 code. SP2.5 shipped as v0.5.0/v0.5.1 (PRs #13/#14/#15, `main` = `d9533c8`) and changed nearly every file this plan touches. This revision re-bases Tasks 1–8 on what actually ships. Binding rulings R1–R9 come from `plan-rev-brief.md`; each row names the ruling and/or the shipped code that forced the change.

| Task | What changed in the plan | Why |
|---|---|---|
| **All** | Test commands are `python3 -m pytest -q`; the green baseline is **585 unit tests** (plus 12 `-m docker` live tests). Line-number references replaced by function names. | R9 |
| **All** | "Six tools" → **seven** everywhere (`finish` shipped in SP2.5, `dirtywork/tools.py:TOOL_SCHEMAS`). | R2 / shipped `tools.py` |
| **1** | `ToolSpec` gains `terminal: bool = False`. `ToolResult` gains `failure: str \| None = None` (carries the `FailureTracker` kind: `"unknown_tool"`/`"bad_args"`, `None` otherwise) so SP2.5's per-kind abort messages survive the move off `except KeyError`/`except TypeError`. `ToolRegistry` gains `spec(name)`, `canonical_args(name, args)`, `transcript_preview(name, text)`. | R2, R3; shipped `runner.FailureTracker`/`FAILURE_KINDS`, `ProgressTracker.note_call` |
| **2** | `execute()` **drops** unknown parameters instead of rejecting them (deliberate SP1 fix, commit `23a9c22`, regression-tested by `test_executor_drops_unknown_tool_args`) — a documented, deliberate deviation from spec §1's "rejects unknown parameters". Unknown-tool text keeps the shipped wording incl. `To end the run call finish(summary=...)`. A `TypeError` raised by `spec.fn` maps to `failure="bad_args"` with today's message. Deadline-exceeded and `BLOCKED:` results carry `failure=None` (they reset the strike counter today). Per-tool `max_output_chars` is sized **above** each tool's own `_cap` plus its note text so the registry never re-truncates a shipped message. `Caps.transcript` is genuinely enforced, via `ToolRegistry.transcript_preview` (`preview` = today's 2000-char cap). `canonical_args` tests move here from `tests/test_runner.py`. | R1, R3; shipped `tools.ToolExecutor`, `tools._cap`, `MAX_BASH_CHARS` |
| **3** | `builtin_tools.py` declares seven specs, `finish` with `terminal=True`. `runner.py` is **edited, not rewritten** (constructor signature, `TOOL_SCHEMAS` → `registry.schemas()`, executor→registry dispatch, `spec.terminal` in place of the `FINISH_TOOL` name check, `registry.canonical_args` for `ProgressTracker`). `__main__.py` gets a 3-line targeted patch inside `_execute`. `tests/test_runner.py` keeps every SP2.5 test — only the `parts` fixture and the `Runner(...)`/`executor.` call sites change. The `TOOL_SCHEMAS`/`ToolExecutor` tests in `tests/test_tools_bash.py` move to `tests/test_builtin_tools.py` (named individually in Step 6) rather than being deleted. | R1, R2, R3 |
| **4** | `dirtywork.providers` must be added to `pyproject.toml`'s `[tool.setuptools] packages` (currently `["dirtywork", "dirtywork.sandbox"]`) or an installed dirtywork loses the new package. `ToolCall` gains `raw_arguments: str = ""` so the transcript's `tool_result.args` stays the raw JSON string it is today (and so a malformed-args call is resent verbatim). `PROVIDER_NAMES` added for argparse choices. | shipped `runner.run()` transcript write; `pyproject.toml` |
| **5** | Reconciled with the real 124-line `dirtywork/llm.py`: `_underlying_socket`, `MAX_RESPONSE_BYTES`, the `sock.settimeout` `OSError` guard, and the `e.read(500)` bound are preserved verbatim in `http_json`. The `LMStudioClient` alias is exposed through a module-level `__getattr__` instead of a bottom-of-file import — the revision-1 form was a genuine circular-import bug (`import dirtywork.providers.openai_compat` first ⇒ `ImportError` from a partially-initialized module). New `MalformedResponse(LLMError)` lets the runner keep mapping a garbage response body to `status="model_error"` **through `finish()`** while a plain `LLMError` still escapes `Runner.run()` (which `test_main_docker_llm_error_after_start_finalizes_before_stop` depends on). `CONTEXT_WINDOWS` moves here from `runner.py`. | R1; shipped `llm.py`, `tests/test_llm.py`, `tests/test_main.py` |
| **6** | Targeted refactor, not a rewrite. Preserves `FailureTracker`, `ProgressTracker`/`check_progress`/`STALL_NUDGE`, `strip_think`/`classify_text_reply`/`NUDGES`/`_join_nudges`, `finish` interception, `trim_messages`, `MAX_ASSISTANT_TEXT_CHARS`, `RunResult.extra`, `finalize`, and every transcript event including `nudge`. `resolve_context_window(model, flag_value, env_value, provider=None)` extends the precedence to flag > env > `provider.context_window(model)` > `DEFAULT_WINDOW` (source `flag`/`env`/`provider:<name>`/`default`); `--context-window`/`DIRTYWORK_CONTEXT_WINDOW` already exist and are **not** re-added. `--provider` goes into `_add_run_flags` (so `resume` inherits it) and `--base-url`'s default becomes provider-dependent via `DEFAULT_BASE_URLS`. `run.json` and the stdout JSON gain `provider`; `resume` refuses a provider switch the way `_load_resume_target` already pins `sandbox`. 13 wire-parsing tests move by name to the provider suites; every other `test_runner.py` test stays. | R1, R4 |
| **7** | Conformed to Task 4/5's revised interfaces (`raw_arguments`, `MalformedResponse`, `PROVIDER_NAMES`, `name = "anthropic"`); fixtures approach unchanged. | R1 |
| **8** | Documents the schema **as shipped**: the `nudge` event and its four kinds, the `finish` tool call, `stalled` in the status list, `context_window`/`provider`/`resumed_from` on `run_start`, `untracked`/`watchdog_violation`/`watchdog_violation_kind` on `run_end`, and the full `run.json` field list (`task`, `model`, `context_window`, `turns`, `resumed_from`, `resumed_by`, `provider`, …). Regression tests assert the shipped shapes rather than a designed-but-unshipped one. | R5 |
| 9 | The `cmd_run` extraction is gone: `main()` already dispatches `run`/`resume` through `_parse_args`/`_execute`, so the task adds `_add_runs_parsers(sub)`, one call in `_parse_args`, one `args.cmd == "runs"` branch, and `runs.dispatch`. All the `python3 - <<PY` in-place patch scripts are replaced by explicit before/after code. `runs list` gains a `RESUMED` column (`from <slug>` / `by <slug>`) and renders `stalled` like any other status; `runs show` prints a task/model/provider/turns/resumed_from/resumed_by summary before the run.json dump and renders `nudge`/`guardrail_block`/`sandbox_reset` events in the timeline. Added `format_table`, `RunsError`, `_open_run` as the shared spine for Tasks 10-12. | R6; shipped `__main__` (`_parse_args`, `_execute`, `_write_run_json_start`, `_update_run_json`), `runner` (`nudge` events, `stalled`), `rundir.read_run_json` |
| 10 | Reconciled with `sandbox/export.py` as shipped: refuses a non-empty worktree up front (export_run's own precondition), refuses a run whose `host_pid` is still alive (`resume.pid_alive`), resolves the image with `docker_args.pin_for(image)` like `_docker_preflight` does, records `worktree_bytes`/`worktree_files`/`escaping_symlinks`/`dropped_git_entries` from `RunArtifacts`, and only rewrites `status` when the old status was about the export (`_export_status_update`) instead of stamping `completed` over `budget_exceeded`. Test fakes now match the real `Captured`/`resolve_image` signatures. | Spec §SP2.7 + `export.export_run`, `docker_cli.resolve_image`, `RunArtifacts`, `_final_status`'s only-replaces-completed rule |
| 11 | Removes the run's `<worktree>.pre-resume-<slug>` stash (`resume.stash_dir_for`) and, once a worktree is removed, every orphaned stash beside it (`resume.find_stashes`); a run with `resumed_by` set keeps its worktree **and branch** (the resume reuses both) with a `kept-worktree` note naming the newer run, while its own run dir and its own label-checked container/volume are still cleaned. `_staleness` now uses `resume.pid_alive`; the ownership test patches `os.getuid` instead of `Path.stat`; label names re-verified against `docker_args._label_args`. | R6; `resume.py`, `_workspace_resume` (branch/worktree reuse), §SP2.3 |
| 12 | Uses the shared `_open_run`, and reads the **`ended`** key (the name `_update_run_json` actually writes) rather than `ended_at`; asserts the run's own fields survive the merge. | Shipped `__main__._update_run_json` |
| 13 | Fixtures rebuilt so they actually work: acceptance harnesses use plain asserts and no pytest (the worker image ships `python3` without pytest and has no network) and no `node --test` file argument (Node 18 CLI variance); `sh-fix-script`'s check now compares bytes with `cmp` (the old `"$(...)"` comparison stripped the trailing newline the task is about, so the unsolved fixture passed); every `acceptance.command` names `/acceptance/...` so the command comes from the read-only mount, never the worktree; each harness resolves its subject from the cwd so it runs identically on the host and at `/work`. All three verified failing unsolved / passing solved, and the recorded sha256 hashes are the real hashes of the files as written, plus a repair step. | Spec §SP3.5 ("acceptance commands never come from the worktree"), `docker/Dockerfile` package list, measured behaviour |
| 14 | Row schema now carries turns, wall seconds, prompt/completion tokens, status, provider, verdict/review seconds and the harness-failure classes: nudge counts by kind (`stall`/`empty`/`truncated`/`text_tool_call`), `empty_reply` (= non-stall nudges, one `FailureTracker.record("empty_reply")` each), `stalled`/`max_turns`/`sandbox_error` flags, and `abort_kind` parsed from the runner's abort message against `runner.FAILURE_KINDS`. Per-model-entry provider/base URL via `model[@provider][=base_url]`; when neither is given the flag is omitted so `run`'s own default applies (no provider name is hardcoded here). Acceptance argv rewritten: `--read-only` + `/tmp` tmpfs, no `-w` (the verified workdir-over-volume ownership bug), image resolved with the digest pin, slug taken from the payload's `run_dir`, staged repo cleaned up. | R7; `runner.py` (NUDGES/FAILURE_KINDS/nudge events), `docker_args`, `docker/Dockerfile` comment, stdout JSON contract |
| 15 | `bench summarize` prints two blocks: a per model x task x repeat detail table (turns, wall seconds, prompt/completion tokens, status, acceptance, verdict, review seconds, nudge counts, failure classes) and the per-model rates the spec asks for (completion, acceptance, gamed, mean tokens, mean wall, verdict rate, median review seconds). Verdicts are re-joined from `run.json` at summarize time, since operators record them after the sweep. Reuses `runs.format_table` rather than a second renderer. | R7 + spec §SP3.5; DRY |
| 16 | New task. `--allow-commit` is a **prompt-only** switch: `grep` confirms no guardrail ever blocked `git commit` (only `git push`, plus host-scope ref/config rules), so no denylist change — one regression test pins that. Preflight refuses `--sandbox docker` because the export archive can never contain a `.git` member; flag lives in `_add_run_flags` (so `resume` inherits it) with `default=None` to distinguish "not given" from "off", `_load_resume_target` fills it from the prior run, `_write_run_json_start` records `allow_commit`, `_execute` passes it to `build_system_prompt`. README flag line + bullet, one line in the transcript-schema `run.json` section. The refusal message says `--sandbox none (host mode)`, not `--sandbox host`: the shipped CLI has no `host` choice, so the literal wording would print an argparse error. | R8; `guardrails.py` (verified), `sandbox/export.py` validator, `_add_run_flags`/`_load_resume_target` |

---

## Global Constraints

- Python 3.9 floor: no `match`, no `X | Y` unions at runtime (only under `from __future__ import annotations`), no `tarfile.data_filter`, no `typing.Literal` misuse beyond 3.9 (`Literal` exists in 3.9's `typing` — fine), `dataclass(slots=)` not available.
- Stdlib only. No new dependencies.
- The stdout JSON contract may gain fields but must not lose or rename any (`status, worktree, branch, transcript, turns, usage, final_message`).
- Every existing test stays green after every task. Run `python3 -m pytest -q` at the end of each task. The baseline on `main` (v0.5.1) is **585 passed**; a task may only raise that number, never lower it except where this plan names the moved test and its new home.
- Commit after each task with a conventional message (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`).
- New tests go in the existing module test file for the file touched where one exists; new modules get `tests/test_<module>.py`.
- Tests that need Docker use marker `docker` (already in `pyproject.toml`'s `markers`, with `addopts = "-m 'not live and not docker'"`) and skip when Docker is not available.
- Never leave placeholders in a plan step: every code step shows the actual code; every test step shows the actual test.

## Precondition

Sub-projects 1 (hardening), 2 (Docker sandbox) and 2.5 (harness robustness) are implemented and merged; this plan starts from `main` at dirtywork **v0.5.1**. Every name below already exists exactly as written:

- **Sandbox (SP2):** `Sandbox` protocol, `SandboxError`, `RunArtifacts` in `dirtywork/sandbox/__init__.py`; `HostSandbox`/`DockerSandbox`; `dirtywork/sandbox/export.py:export_run`; `dirtywork/sandbox/docker_cli.py` (`run`, `docker_version`, `resolve_image`, `image_repo_digest`, `validate_objects_dir`, `DockerError`); `dirtywork/sandbox/docker_args.py` (`DockerConfig`, `container_name`, `volume_name`, `repo_label`, `DEFAULT_IMAGE`, `pin_for`).
- **Run dirs (SP1):** `dirtywork/rundir.py` (`RUNS_DIR`, `ensure_runs_dir`, `create_run_dir`, `write_run_json`, `read_run_json`, `RunDirError`); `dirtywork/workspace.py` (`load_repo_context`, `worktree_base_commit`, `create_worktree`, `ensure_worktrees_excluded`, `host_diff_stat`, `make_slug`, `preflight_repo`, `remove_worktree`, `commit_exists`, `WorkspaceError`); `dirtywork/budget.py` (`BudgetExceeded` with `.reason`, `BudgetReport`, `measure_worktree`).
- **Tools (SP2/SP2.5):** `dirtywork/tools.py` has the plain host functions (`read_file`, `write_file`, `edit_file`, `list_dir`, `grep`, `bash`) plus `MAX_RESULT_CHARS = 8000`, `MAX_BASH_CHARS = 10000`, `MAX_READ_BYTES`, `MAX_WRITE_BYTES`, `MAX_LIST_ENTRIES`, `_cap`; `TOOL_SCHEMAS` with **seven** entries (the six tools plus `finish`); `_TOOL_PARAMS`; `ToolExecutor(sandbox, transcript=None)` with `.deadline`, `.execute(name, args) -> str` and `.canonical_args(name, args) -> dict`.
- **Runner (SP2.5):** `dirtywork/runner.py` has `FINISH_TOOL`, `FAILURE_KINDS`, `MAX_CONSECUTIVE_FAILURES = 3`, `MAX_TOTAL_CONSECUTIVE_FAILURES = 6`, `FailureTracker`, `strip_think`, `classify_text_reply`, `NUDGES`, `_join_nudges`, `_looks_like_tool_json`, `DEFAULT_STALL_TURNS = 12`, `STALL_NUDGE`, `_MUTATING_TOOLS`, `_VOLATILE_RE`, `_bash_fingerprint`, `ProgressTracker`, `CONTEXT_WINDOWS`, `DEFAULT_WINDOW = 32768`, `resolve_context_window(model, flag_value, env_value) -> (int, str)`, `MAX_ASSISTANT_TEXT_CHARS = 64_000`, `TRIM_MARKER`, `trim_messages`, `RunResult(status, turns, final_message, usage, extra)`, and `Runner(client, executor, transcript, model, max_turns=40, timeout=1800, temperature=None, run_info=None, finalize=None, stall_turns=DEFAULT_STALL_TURNS, context_window=None)` whose `run()` writes `run_start`/`assistant`/`tool_result`/`nudge`/`run_end`.
- **CLI (SP2.5):** `dirtywork/__main__.py` has `build_system_prompt`, `PreflightFailure`, `RunContext`, `_preflight_llm`, `_resolve_context_window`, `_docker_preflight`/`_docker_preflight_or_fail`, `_workspace_new`, `_workspace_resume`, `_build_sandbox`, `_write_run_json_start`, `_update_run_json`, `_emit_result`, `_final_status`, `_fail_setup`, `_fail_run`, `_load_resume_target`, `_execute`, `_add_run_flags(p, *, resume)`, `_parse_args`, `main`; subcommands `run` and `resume`; flags including `--base-url`, `--context-window`, `--stall-turns`.
- **Resume (SP2.5):** `dirtywork/resume.py` (`RESUME_MARKER`, `PRE_RESUME_SUFFIX`, `stash_dir_for`, `find_stashes`, `resolve_run_dir`, `load_prior_run`, `check_resumable`, `render_transcript_tail`, `build_resume_task`, `ResumeError`).
- **LLM (SP1):** `dirtywork/llm.py` is the 124-line module holding `MAX_RESPONSE_BYTES`, `_underlying_socket`, `LLMError`, `LLMTimeout` and `LMStudioClient` (`_request`, `list_models`, `chat`). Task 5 moves the client into `providers/openai_compat.py` and must preserve `_underlying_socket`, the per-read `sock.settimeout(remaining)` with its `except OSError` guard, the `MAX_RESPONSE_BYTES` cap and the `e.read(500)` HTTPError-body bound exactly.
- **run.json** is written at start by `_write_run_json_start` and merge-updated by `_update_run_json`; it already records `schema_version`, `status`, `slug`, `repo`, `worktree`, `branch`, `base_commit`, `task`, `model`, `context_window`, `resumed_from`, `container`, `volume`, `image`, `image_digest`, `image_pinned`, `host_pid`, `started`, `sandbox`, and at the end `ended`, `turns`, `diff_stat`, `export_status`, `patch_path`, `finalize_error`, `watchdog_violation`, `watchdog_violation_kind` (plus `resumed_by` on the *prior* run's run.json).

## File Structure

```
dirtywork/
  toolspec.py                     # NEW — Task 1, 2
  builtin_tools.py                # NEW — Task 3
  tools.py                        # MODIFIED — Task 3 (TOOL_SCHEMAS, _TOOL_PARAMS, ToolExecutor removed)
  providers/
    __init__.py                   # NEW — Task 4
    openai_compat.py              # NEW — Task 5
    anthropic.py                  # NEW — Task 7
  llm.py                          # MODIFIED — Task 5 (http_json + MalformedResponse; LMStudioClient alias via __getattr__)
  runner.py                       # MODIFIED — Task 3 (registry+sandbox), Task 6 (provider-neutral, context window)
  __main__.py                     # MODIFIED — Task 3, 6 (targeted patches), 9/10/11/12/14/15 (subcommand wiring), 16 (--allow-commit)
  guardrails.py                   # MODIFIED — Task 16 only, and only if a `git commit` guardrail exists
  runs.py                         # NEW — Task 9, 10, 11, 12
  bench.py                        # NEW — Task 13, 14, 15
docs/
  transcript-schema.md            # NEW — Task 8
bench/
  repos/
    py-fix-off-by-one/            # NEW — Task 13
    node-add-cli-flag/            # NEW — Task 13
    sh-fix-script/                # NEW — Task 13
pyproject.toml                    # MODIFIED — Task 4 (packages += "dirtywork.providers")
README.md                         # MODIFIED — Task 8 (schema pointer), 9–15 (runs/bench), 16 (--allow-commit)
tests/
  test_toolspec.py                # NEW — Task 1, 2
  test_builtin_tools.py           # NEW — Task 3
  test_tools_bash.py              # MODIFIED — Task 3 (TOOL_SCHEMAS/ToolExecutor tests move out)
  test_providers.py               # NEW — Task 4
  provider_contract.py            # NEW — Task 5 (no test_ prefix; not collected directly)
  fixtures/providers/openai/*.json      # NEW — Task 5
  fixtures/providers/anthropic/*.json   # NEW — Task 7
  test_provider_openai.py         # NEW — Task 5
  test_provider_anthropic.py      # NEW — Task 7
  provider_doubles.py             # NEW — Task 6 (shared CLI-test provider doubles; no test_ prefix)
  test_llm.py                     # MODIFIED — Task 5 (http_json-level tests)
  test_runner.py                  # MODIFIED — Task 3, 6
  test_main.py                    # MODIFIED — Task 6, 9, 16
  test_docker_live.py             # MODIFIED — Task 6 (provider double; `-m docker`)
  test_transcript_schema.py       # NEW — Task 8
  test_runs.py                    # NEW — Task 9, 10, 11, 12
  test_bench.py                   # NEW — Task 13, 14, 15
  test_guardrails_bash.py         # MODIFIED — Task 16 only, and only if the guardrail changes
```

---

### Task 1: `toolspec.py` — dataclasses, `register`, `schemas`

**Files:**
- Create: `dirtywork/toolspec.py`
- Create: `tests/test_toolspec.py`

**Interfaces:**
- Consumes: nothing (stdlib `dataclasses`/`typing` only).
- Produces: `MISSING` sentinel; `ParamSpec(type: str, description: str = "", default: Any = MISSING)`; `Caps(fs: str, network: bool = False, max_input_bytes: int | None = None, max_output_chars: int = 8000, timeout_default: int | None = None, timeout_max: int | None = None, transcript: str = "preview")`; `ToolSpec(name: str, description: str, params: dict, required: tuple, fn: Callable, caps: Caps, terminal: bool = False)`; `ToolResult(text: str, kind: str, failure: str | None = None)`; `ToolValidationError(Exception)`; `ToolRegistry(transcript=None)` with `.register(spec) -> None`, `.spec(name) -> ToolSpec | None` and `.schemas() -> list[dict]` (OpenAI wire shape).

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


def test_schemas_preserve_registration_order():
    registry = ToolRegistry()
    registry.register(ECHO_SPEC)
    registry.register(PING_SPEC)
    assert [s["function"]["name"] for s in registry.schemas()] == ["echo", "ping"]


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


def test_spec_lookup_returns_the_registered_object_or_none():
    registry = ToolRegistry()
    registry.register(ECHO_SPEC)
    assert registry.spec("echo") is ECHO_SPEC
    assert registry.spec("nonexistent") is None


def test_terminal_defaults_false_and_can_be_declared():
    assert PING_SPEC.terminal is False
    end = ToolSpec(name="end", description="Ends the run.", params={}, required=(),
                   fn=_fn_ping, caps=Caps(fs="none"), terminal=True)
    assert end.terminal is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_toolspec.py -q`
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
    """Enforced generically by ToolRegistry (Task 2). ``fs``/``network`` are
    declared for documentation and schema purposes only: every tool runs inside
    the Sandbox passed to ``execute``, so there is no host/sandbox dispatch flag
    left to gate on. ``max_output_chars`` is an OUTER safety net -- every
    built-in tool already truncates its own result and appends an explanatory
    note, so a tool's cap here must sit above its own cap plus that note or the
    registry would chop the note off (see builtin_tools.TOOL_OUTPUT_CAP).
    ``transcript`` picks how much of a result reaches the transcript:
    "full" (uncapped), "preview" (TRANSCRIPT_PREVIEW_CHARS), "none" (nothing)."""

    fs: str  # "none" | "read" | "write"
    network: bool = False
    max_input_bytes: int | None = None
    max_output_chars: int = 8000
    timeout_default: int | None = None
    timeout_max: int | None = None
    transcript: str = "preview"  # "full" | "preview" | "none"


@dataclass(frozen=True)
class ToolSpec:
    """``terminal=True`` marks a tool the RUNNER handles itself and never
    dispatches to ``execute`` -- ``finish`` ends the run, so its arguments are
    read straight off the ToolCall (a missing ``summary`` completes the run with
    an empty final message rather than becoming a validation strike)."""

    name: str
    description: str
    params: dict
    required: tuple
    fn: Callable
    caps: Caps
    terminal: bool = False


@dataclass(frozen=True)
class ToolResult:
    """``failure`` is the runner's FailureTracker kind for this result --
    "unknown_tool" or "bad_args" -- or None when nothing should be counted
    against the model. A blocked result and a deadline-exceeded refusal both
    carry ``failure=None`` because today's executor lets them RESET the strike
    counter (``execute`` returned normally), and that behaviour is preserved."""

    text: str
    kind: str  # "ok" | "error" | "blocked"
    failure: str | None = None


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

    def names(self) -> list:
        """Tool names in registration order -- the order they are advertised to
        the model, and the order the unknown-tool error lists them in."""
        return list(self._table)

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

Run: `python3 -m pytest tests/test_toolspec.py -q`
Expected: 6 passed

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest -q`
Expected: 591 passed (585 baseline + 6 new; this module is not imported by anything yet, so nothing else can break)

- [ ] **Step 6: Commit**

```bash
git add dirtywork/toolspec.py tests/test_toolspec.py
git commit -m "feat: add ToolSpec/ToolRegistry dataclasses with register/schemas"
```

---

### Task 2: `ToolRegistry.execute` — validation, caps, blocked, transcript, `canonical_args`

**Files:**
- Modify: `dirtywork/toolspec.py`
- Modify: `tests/test_toolspec.py`

**Interfaces:**
- Consumes: `time.monotonic`, `posixpath` (stdlib).
- Produces: `TRANSCRIPT_PREVIEW_CHARS = 2000`; `ToolRegistry.execute(self, name: str, args: dict, *, sandbox, deadline) -> ToolResult`; `ToolRegistry.canonical_args(self, name: str, args) -> dict`; `ToolRegistry.transcript_preview(self, name: str, text: str) -> str`.

**Behaviour that must match `dirtywork/tools.py:ToolExecutor` exactly (it is being replaced):**
1. Unknown parameters are **dropped**, not rejected — local models routinely attach another harness's parameters (e.g. Claude Code's `description` on `bash`); commit `23a9c22` made that deliberate and `test_executor_drops_unknown_tool_args` guards it. (Spec §1 says "rejects unknown parameters"; the shipped behaviour wins, and this deviation is deliberate.)
2. Unknown tool name → `ERROR: unknown tool '<name>'. Available: <names in registration order>. To end the run call finish(summary=...).` with `failure="unknown_tool"`.
3. Missing/invalid required arguments → `ERROR: bad arguments for <name>: <detail>` with `failure="bad_args"`.
4. `deadline` already passed → `ERROR: run deadline exceeded; stop calling tools and summarize what you have done.`, `failure=None`, and the tool is **not** run.
5. A tool declaring `caps.timeout_default` gets `timeout` injected/clamped to `min(requested-or-default, timeout_max, max(1, int(remaining)))`.
6. A result starting with `BLOCKED:` writes a `guardrail_block` transcript event (`tool`, `args`, `reason`) and returns `kind="blocked"`, `failure=None`.
7. `BudgetExceeded`/`SandboxError` raised by a sandbox method propagate untouched (the runner maps them to statuses).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_toolspec.py`:

```python
import time

import pytest

from dirtywork.toolspec import TRANSCRIPT_PREVIEW_CHARS, ToolResult


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


def _fn_paths(sandbox, path=".", offset=0, limit=400):
    return f"paths:{path}:{offset}:{limit}"


def _fn_cmd(sandbox, command, timeout=120):
    return f"exit code: 0\n{command}"


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

PATHS_SPEC = ToolSpec(
    name="paths", description="read_file-shaped.",
    params={"path": ParamSpec(type="string"),
            "offset": ParamSpec(type="integer", default=0),
            "limit": ParamSpec(type="integer", default=400)},
    required=("path",), fn=_fn_paths, caps=Caps(fs="read"))

CMD_SPEC = ToolSpec(
    name="cmd", description="bash-shaped.",
    params={"command": ParamSpec(type="string"),
            "timeout": ParamSpec(type="integer", default=120)},
    required=("command",), fn=_fn_cmd,
    caps=Caps(fs="write", timeout_default=120, timeout_max=600))

FULL_SPEC = ToolSpec(
    name="verbose", description="Transcribed in full.", params={}, required=(),
    fn=lambda sandbox: "y" * 5000, caps=Caps(fs="none", max_output_chars=100000,
                                             transcript="full"))

SILENT_SPEC = ToolSpec(
    name="silent", description="Never transcribed.", params={}, required=(),
    fn=lambda sandbox: "secret", caps=Caps(fs="none", transcript="none"))


def _registry():
    r = ToolRegistry()
    for spec in (PING_SPEC, ECHO_SPEC, BLOCKED_SPEC, CAPPED_SPEC, TIMEOUT_SPEC,
                 BYTES_SPEC, HIDDEN_TIMEOUT_SPEC, PATHS_SPEC, CMD_SPEC,
                 FULL_SPEC, SILENT_SPEC):
        r.register(spec)
    return r


def test_execute_unknown_tool():
    r = _registry()
    result = r.execute("nope", {}, sandbox=None, deadline=None)
    assert result.kind == "error"
    assert result.failure == "unknown_tool"
    assert result.text.startswith("ERROR: unknown tool 'nope'. Available:")
    assert "ping" in result.text and "echo" in result.text
    assert "To end the run call finish(summary=...)." in result.text


def test_execute_dispatches_and_fills_defaults():
    r = _registry()
    result = r.execute("ping", {}, sandbox=object(), deadline=None)
    assert result == ToolResult(text="pong", kind="ok", failure=None)


def test_execute_drops_unknown_parameters():
    # qwen and friends attach another harness's parameters (Claude Code's
    # `description` on bash). Dropping them keeps a habit from becoming three
    # bad_args strikes and an aborted run (SP1, commit 23a9c22).
    r = _registry()
    result = r.execute("paths", {"path": "a.txt", "description": "look"},
                       sandbox=object(), deadline=None)
    assert result.kind == "ok"
    assert result.text == "paths:a.txt:0:400"


def test_execute_missing_required_is_bad_args():
    r = _registry()
    result = r.execute("echo", {}, sandbox=object(), deadline=None)
    assert result.kind == "error"
    assert result.failure == "bad_args"
    assert result.text.startswith("ERROR: bad arguments for echo:")
    assert "text" in result.text


def test_execute_type_mismatch_is_bad_args():
    r = _registry()
    result = r.execute("echo", {"text": 123}, sandbox=object(), deadline=None)
    assert result.kind == "error"
    assert result.failure == "bad_args"
    assert "must be string" in result.text


def test_execute_bool_is_not_integer():
    r = ToolRegistry()
    spec = ToolSpec(name="takesint", description="d",
                    params={"n": ParamSpec(type="integer")}, required=("n",),
                    fn=lambda sandbox, n: f"n={n}", caps=Caps(fs="none"))
    r.register(spec)
    result = r.execute("takesint", {"n": True}, sandbox=object(), deadline=None)
    assert result.kind == "error" and result.failure == "bad_args"
    assert "must be integer" in result.text


def test_execute_int_accepted_for_number_param():
    r = ToolRegistry()
    spec = ToolSpec(name="takesnum", description="d",
                    params={"n": ParamSpec(type="number")}, required=("n",),
                    fn=lambda sandbox, n: f"n={n}", caps=Caps(fs="none"))
    r.register(spec)
    result = r.execute("takesnum", {"n": 3}, sandbox=object(), deadline=None)
    assert result.kind == "ok" and result.text == "n=3"


def test_execute_explicit_null_allowed_for_none_defaulted_param():
    # `grep(glob=None)`: a model that spells the default out explicitly must not
    # take a bad_args strike for it.
    r = ToolRegistry()
    spec = ToolSpec(name="g", description="d",
                    params={"pattern": ParamSpec(type="string"),
                            "glob": ParamSpec(type="string", default=None)},
                    required=("pattern",),
                    fn=lambda sandbox, pattern, glob=None: f"{pattern}:{glob}",
                    caps=Caps(fs="read"))
    r.register(spec)
    result = r.execute("g", {"pattern": "x", "glob": None}, sandbox=object(), deadline=None)
    assert result.kind == "ok" and result.text == "x:None"


def test_execute_fn_type_error_is_bad_args():
    r = ToolRegistry()
    def _picky(sandbox, n):
        raise TypeError("n must be positive")
    r.register(ToolSpec(name="picky", description="d",
                        params={"n": ParamSpec(type="integer")}, required=("n",),
                        fn=_picky, caps=Caps(fs="none")))
    result = r.execute("picky", {"n": 1}, sandbox=object(), deadline=None)
    assert result.kind == "error" and result.failure == "bad_args"
    assert result.text == "ERROR: bad arguments for picky: n must be positive"


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
    assert result.failure == "bad_args"
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


def test_execute_deadline_exceeded_short_circuits_without_running_the_tool():
    r = _registry()
    deadline = time.monotonic() - 1
    result = r.execute("cmd", {"command": "touch created.txt"}, sandbox=object(),
                       deadline=deadline)
    assert result.kind == "error"
    assert result.failure is None          # today's executor resets the strike counter here
    assert "deadline exceeded" in result.text.lower()


def test_execute_blocked_writes_guardrail_block_and_kind():
    transcript = _RecordingTranscript()
    r = ToolRegistry(transcript=transcript)
    r.register(BLOCKED_SPEC)
    result = r.execute("blockme", {}, sandbox=object(), deadline=None)
    assert result.kind == "blocked"
    assert result.failure is None
    assert result.text.startswith("BLOCKED:")
    assert transcript.events and transcript.events[0][0] == "guardrail_block"
    assert transcript.events[0][1]["tool"] == "blockme"
    assert transcript.events[0][1]["reason"].startswith("BLOCKED:")


def test_execute_fn_exception_propagates():
    r = ToolRegistry()
    def _boom(sandbox, **kwargs):
        raise RuntimeError("kaboom")
    r.register(ToolSpec(name="boom", description="d", params={}, required=(),
                        fn=_boom, caps=Caps(fs="none")))
    with pytest.raises(RuntimeError, match="kaboom"):
        r.execute("boom", {}, sandbox=object(), deadline=None)


def test_transcript_preview_modes():
    r = _registry()
    assert r.transcript_preview("paths", "z" * 5000) == "z" * TRANSCRIPT_PREVIEW_CHARS
    assert r.transcript_preview("verbose", "y" * 5000) == "y" * 5000
    assert r.transcript_preview("silent", "secret") == ""
    assert r.transcript_preview("nonexistent", "z" * 5000) == "z" * TRANSCRIPT_PREVIEW_CHARS


# --- canonical_args: moved here verbatim in intent from tests/test_runner.py's
# --- test_canonical_args_normalizes_effective_arguments (R3). ProgressTracker
# --- depends on these exact semantics; two calls that do the same thing must
# --- look the same or a stuck model could dodge `stalled` by varying noise.

def test_canonical_args_normalizes_effective_arguments():
    r = _registry()
    a = r.canonical_args("paths", {"path": "./f.txt", "description": "x"})
    b = r.canonical_args("paths", {"path": "f.txt", "offset": 0, "limit": 400})
    assert a == b == {"path": "f.txt", "offset": 0, "limit": 400}
    assert r.canonical_args("cmd", {"command": " ls \n", "timeout": 5}) == {"command": "ls"}
    assert r.canonical_args("cmd", {"command": "ls"}) == {"command": "ls"}
    assert r.canonical_args("listwait", {}) == {"path": "."}
    assert r.canonical_args("no_such_tool", {"x": 1}) == {"x": 1}
    assert r.canonical_args("paths", "not a dict") == {}


def test_canonical_args_normalizes_trailing_slash_and_empty_path():
    r = _registry()
    assert r.canonical_args("paths", {"path": "f.txt/"})["path"] == "f.txt"
    assert r.canonical_args("paths", {"path": "  "})["path"] == "."


def test_canonical_args_omits_params_without_defaults():
    r = _registry()
    assert r.canonical_args("paths", {}) == {"offset": 0, "limit": 400}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_toolspec.py -q`
Expected: `ImportError: cannot import name 'TRANSCRIPT_PREVIEW_CHARS'` (collection error for the whole file)

- [ ] **Step 3: Write the minimal implementation**

In `dirtywork/toolspec.py`, add `import posixpath` and `import time` to the top imports, then add these module-level pieces immediately after `class ToolValidationError`:

```python
TRANSCRIPT_PREVIEW_CHARS = 2000

_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
}


def _validate_args(spec: ToolSpec, args: dict) -> dict:
    """Effective keyword arguments for spec.fn. Unknown keys are DROPPED, not
    rejected (SP1, commit 23a9c22): local models routinely attach parameters
    from other harnesses' tool schemas, and turning that habit into three
    consecutive bad_args strikes aborted real runs. Missing/invalid REQUIRED
    arguments still fail loudly."""
    missing = [p for p in spec.required if p not in args]
    if missing:
        raise ToolValidationError(f"missing required parameter(s): {', '.join(missing)}")
    call_args = {}
    for pname, pspec in spec.params.items():
        if pname in args:
            value = args[pname]
            if value is None and pspec.default is None:
                call_args[pname] = None          # the model spelled out the default
                continue
            check = _TYPE_CHECKS.get(pspec.type)
            if check is not None and not check(value):
                raise ToolValidationError(
                    f"parameter '{pname}' must be {pspec.type}, got {type(value).__name__}")
            call_args[pname] = value
        elif pspec.default is not MISSING:
            call_args[pname] = pspec.default
    return call_args
```

Then add these three methods to `ToolRegistry`, right after `schemas`:

```python
    def canonical_args(self, name: str, args) -> dict:
        """The call's *effective* arguments, for the runner's stall detector:
        unknown keys dropped (execute() ignores them), defaults filled in from
        the spec, `timeout` dropped (execute() clamps it per call anyway),
        path-like strings normalized (`foo` == `./foo` == `foo/`), and
        `command` stripped. Two calls that do the same thing must look the
        same, or a stuck model could dodge `stalled` by varying noise."""
        if not isinstance(args, dict):
            return {}
        spec = self._table.get(name)
        if spec is None:
            return dict(args)
        out = {}
        for pname, pspec in spec.params.items():
            if pname == "timeout":
                continue
            if pname in args:
                out[pname] = args[pname]
            elif pspec.default is not MISSING:
                out[pname] = pspec.default
        if isinstance(out.get("path"), str):
            stripped = out["path"].strip()
            out["path"] = posixpath.normpath(stripped) if stripped else "."
        if isinstance(out.get("command"), str):
            out["command"] = out["command"].strip()
        return out

    def transcript_preview(self, name: str, text: str) -> str:
        """How much of a tool result reaches the transcript, per Caps.transcript.
        Unknown names (an unknown-tool error) get the default preview cap."""
        spec = self._table.get(name)
        mode = spec.caps.transcript if spec is not None else "preview"
        if mode == "none":
            return ""
        if mode == "full":
            return text
        return text[:TRANSCRIPT_PREVIEW_CHARS]

    def execute(self, name: str, args: dict, *, sandbox, deadline) -> ToolResult:
        spec = self._table.get(name)
        if spec is None:
            available = ", ".join(self._table)
            return ToolResult(
                text=(f"ERROR: unknown tool '{name}'. Available: {available}. "
                      f"To end the run call finish(summary=...)."),
                kind="error", failure="unknown_tool")
        try:
            call_args = _validate_args(spec, args)
        except ToolValidationError as e:
            return ToolResult(text=f"ERROR: bad arguments for {name}: {e}",
                              kind="error", failure="bad_args")

        caps = spec.caps
        if caps.max_input_bytes is not None:
            total = sum(len(v.encode("utf-8")) for v in call_args.values()
                        if isinstance(v, str))
            if total > caps.max_input_bytes:
                return ToolResult(
                    text=(f"ERROR: bad arguments for {name}: input is {total} bytes, "
                          f"over the {caps.max_input_bytes}-byte limit."),
                    kind="error", failure="bad_args")

        remaining = None
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Matches ToolExecutor.execute: the run is over, the tool never
                # runs, and this does NOT count as a model failure.
                return ToolResult(
                    text=("ERROR: run deadline exceeded; stop calling tools and "
                          "summarize what you have done."),
                    kind="error", failure=None)

        if caps.timeout_default is not None:
            # Deadline-based clamping applies whenever a tool declares a
            # timeout_default, even for tools (like grep) that never expose
            # "timeout" as a model-settable schema parameter -- the registry
            # injects/overwrites call_args["timeout"] either way, and spec.fn's
            # own keyword default absorbs it since it isn't in the JSON schema.
            requested = call_args.get("timeout")
            bound = requested if requested is not None else caps.timeout_default
            if caps.timeout_max is not None:
                bound = min(bound, caps.timeout_max)
            if remaining is not None:
                bound = min(bound, max(1, int(remaining)))
            call_args["timeout"] = int(bound)

        try:
            result_text = spec.fn(sandbox, **call_args)
        except TypeError as e:
            # ToolExecutor turned a TypeError out of the tool function into a
            # bad_args strike; keep that (validation above catches the common
            # cases, this catches the rest) rather than aborting the run.
            return ToolResult(text=f"ERROR: bad arguments for {name}: {e}",
                              kind="error", failure="bad_args")

        if len(result_text) > caps.max_output_chars:
            result_text = (result_text[: caps.max_output_chars]
                           + f"\n[output truncated at {caps.max_output_chars} chars]")

        if result_text.startswith("BLOCKED:"):
            if self.transcript is not None:
                self.transcript.write("guardrail_block", tool=name, args=call_args,
                                      reason=result_text)
            return ToolResult(text=result_text, kind="blocked", failure=None)
        return ToolResult(text=result_text, kind="ok", failure=None)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_toolspec.py -q`
Expected: 27 passed

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest -q`
Expected: 612 passed (585 baseline + 27 new)

- [ ] **Step 6: Commit**

```bash
git add dirtywork/toolspec.py tests/test_toolspec.py
git commit -m "feat: implement ToolRegistry.execute/canonical_args (validation, caps, blocked, deadline)"
```

---

### Task 3: `builtin_tools.py` — seven `ToolSpec`s; runner and CLI switch to the registry

This task changes **how** tools are declared and dispatched. It changes **no** model-visible behaviour: `default_registry().schemas()` must equal today's `dirtywork.tools.TOOL_SCHEMAS` byte for byte, and every SP2.5 runner test must still pass.

**Files:**
- Create: `dirtywork/builtin_tools.py`
- Create: `tests/test_builtin_tools.py`
- Create: `tests/fixtures/tool_schemas_v051.json` (generated from the live `TOOL_SCHEMAS` in Step 1)
- Modify: `dirtywork/tools.py` (delete `_param`, `TOOL_SCHEMAS`, `_TOOL_PARAMS`, `ToolExecutor`)
- Modify: `tests/test_tools_bash.py` (the `TOOL_SCHEMAS`/`ToolExecutor` tests move to `tests/test_builtin_tools.py`)
- Modify: `dirtywork/runner.py` (targeted edits: `executor` → `registry` + `sandbox`)
- Modify: `dirtywork/__main__.py` (targeted patch inside `_execute`)
- Modify: `tests/test_runner.py` (targeted patch: `parts` fixture + call sites)

**Interfaces:**
- Consumes: `Sandbox` protocol methods (`read_file(path, offset, limit)`, `write_file(path, content)`, `edit_file(path, old_string, new_string)`, `list_dir(path)`, `grep(pattern, path, glob, timeout)`, `bash(command, timeout)`) from SP2; `dirtywork.toolspec.{Caps, ParamSpec, ToolRegistry, ToolSpec}`; `dirtywork.tools.{MAX_RESULT_CHARS, MAX_BASH_CHARS}`.
- Produces: `dirtywork.builtin_tools.{TOOL_OUTPUT_CAP, BASH_OUTPUT_CAP, READ_FILE_SPEC, WRITE_FILE_SPEC, EDIT_FILE_SPEC, LIST_DIR_SPEC, GREP_SPEC, BASH_SPEC, FINISH_SPEC, BUILTIN_SPECS, default_registry(transcript=None) -> ToolRegistry}`; `Runner(client, registry, sandbox, transcript, model, max_turns=40, timeout=1800, temperature=None, run_info=None, finalize=None, stall_turns=DEFAULT_STALL_TURNS, context_window=None)`.

- [ ] **Step 1: Freeze today's wire contract as a fixture**

Run this from the repo root, **before** touching `tools.py`. It captures the exact seven schemas the model sees today, so the new registry is compared against the real thing rather than a hand-typed copy:

```bash
mkdir -p tests/fixtures
python3 - <<'PY'
import json
from pathlib import Path
from dirtywork.tools import TOOL_SCHEMAS
Path("tests/fixtures/tool_schemas_v051.json").write_text(
    json.dumps(TOOL_SCHEMAS, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
names = [s["function"]["name"] for s in TOOL_SCHEMAS]
print(names)
assert names == ["read_file", "write_file", "edit_file", "list_dir", "grep", "bash", "finish"], names
PY
```
Expected output: `['read_file', 'write_file', 'edit_file', 'list_dir', 'grep', 'bash', 'finish']`

- [ ] **Step 2: Write the failing test**

`tests/test_builtin_tools.py`:

```python
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from dirtywork.budget import BudgetExceeded
from dirtywork.builtin_tools import default_registry
from dirtywork.sandbox.host import HostSandbox
from dirtywork.transcript import Transcript

FROZEN_SCHEMAS = Path(__file__).parent / "fixtures" / "tool_schemas_v051.json"


class FakeSandbox:
    def __init__(self):
        self.calls = []

    def read_file(self, path, offset, limit):
        self.calls.append(("read_file", path, offset, limit))
        return f"read:{path}:{offset}:{limit}"

    def write_file(self, path, content):
        self.calls.append(("write_file", path, content))
        return f"wrote:{path}:{len(content)}"

    def edit_file(self, path, old_string, new_string):
        self.calls.append(("edit_file", path, old_string, new_string))
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


@pytest.fixture()
def wt(tmp_path: Path) -> Path:
    (tmp_path / "hello.txt").write_text("hi\n")
    return tmp_path


def test_schemas_match_the_frozen_v051_wire_contract():
    # The model-facing contract must not drift without a deliberate, matching
    # change to builtin_tools.py AND to this fixture.
    expected = json.loads(FROZEN_SCHEMAS.read_text(encoding="utf-8"))
    assert default_registry().schemas() == expected


def test_schemas_shape():
    schemas = default_registry().schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {"read_file", "write_file", "edit_file", "list_dir", "grep", "bash", "finish"}
    for s in schemas:
        assert s["type"] == "function"
        assert "parameters" in s["function"]


def test_bash_schema_mentions_reset_behavior():
    schema = next(s for s in default_registry().schemas() if s["function"]["name"] == "bash")
    description = schema["function"]["description"]
    assert "reset" in description.lower()
    assert "index" in description.lower() or "git state" in description.lower()


def test_finish_is_the_only_terminal_spec():
    registry = default_registry()
    terminal = [name for name in registry.names() if registry.spec(name).terminal]
    assert terminal == ["finish"]


def test_read_file_dispatches_positionally():
    sandbox = FakeSandbox()
    result = default_registry().execute("read_file", {"path": "a.txt"},
                                        sandbox=sandbox, deadline=None)
    assert result.kind == "ok"
    assert sandbox.calls == [("read_file", "a.txt", 0, 400)]


def test_write_file_dispatches():
    sandbox = FakeSandbox()
    result = default_registry().execute("write_file", {"path": "a.txt", "content": "hi"},
                                        sandbox=sandbox, deadline=None)
    assert result.kind == "ok"
    assert sandbox.calls == [("write_file", "a.txt", "hi")]


def test_edit_file_dispatches_old_new():
    sandbox = FakeSandbox()
    default_registry().execute("edit_file", {"path": "a.txt", "old_string": "x",
                                             "new_string": "y"},
                               sandbox=sandbox, deadline=None)
    assert sandbox.calls == [("edit_file", "a.txt", "x", "y")]


def test_list_dir_default_path():
    sandbox = FakeSandbox()
    default_registry().execute("list_dir", {}, sandbox=sandbox, deadline=None)
    assert sandbox.calls == [("list_dir", ".")]


def test_grep_dispatches_with_hidden_timeout_default():
    sandbox = FakeSandbox()
    default_registry().execute("grep", {"pattern": "foo"}, sandbox=sandbox, deadline=None)
    assert sandbox.calls == [("grep", "foo", ".", None, 30)]


def test_bash_dispatches_with_timeout_default():
    sandbox = FakeSandbox()
    default_registry().execute("bash", {"command": "ls"}, sandbox=sandbox, deadline=None)
    assert sandbox.calls == [("bash", "ls", 120)]


def test_bash_timeout_clamped_to_600():
    sandbox = FakeSandbox()
    default_registry().execute("bash", {"command": "ls", "timeout": 9999},
                               sandbox=sandbox, deadline=None)
    assert sandbox.calls == [("bash", "ls", 600)]


def test_registry_output_cap_never_re_truncates_a_tool_s_own_capped_result():
    # tools._cap already truncates at MAX_RESULT_CHARS and appends a note; the
    # registry cap sits above that, so the note survives intact.
    class CappedSandbox(FakeSandbox):
        def read_file(self, path, offset, limit):
            from dirtywork.tools import MAX_RESULT_CHARS
            return "z" * MAX_RESULT_CHARS + "\n[output truncated at 8000 chars — re-run with offset/limit to see more]"

    result = default_registry().execute("read_file", {"path": "a.txt"},
                                        sandbox=CappedSandbox(), deadline=None)
    assert result.text.endswith("re-run with offset/limit to see more]")


# --- moved here from tests/test_tools_bash.py (the ToolExecutor tests): the
# --- subject moved from ToolExecutor to ToolRegistry + builtin specs.

def test_dispatch_and_unknown_tool(wt: Path):
    registry = default_registry()
    sandbox = HostSandbox(wt)
    assert "hi" in registry.execute("read_file", {"path": "hello.txt"},
                                    sandbox=sandbox, deadline=None).text
    unknown = registry.execute("format_disk", {}, sandbox=sandbox, deadline=None)
    assert unknown.failure == "unknown_tool"
    assert "unknown tool 'format_disk'" in unknown.text


def test_drops_unknown_tool_args(wt: Path):
    # qwen/other local models attach e.g. Claude Code's `description` to bash
    # calls; that must not become a bad_args strike (3 in a row aborts the run).
    registry = default_registry()
    out = registry.execute("bash", {"command": "echo hi", "description": "say hi"},
                           sandbox=HostSandbox(wt), deadline=None)
    assert out.kind == "ok"
    assert "hi" in out.text


def test_missing_required_arg_is_bad_args(wt: Path):
    out = default_registry().execute("bash", {"description": "no command"},
                                     sandbox=HostSandbox(wt), deadline=None)
    assert out.kind == "error" and out.failure == "bad_args"
    assert "command" in out.text


def test_deadline_exceeded_blocks_execution(wt: Path):
    out = default_registry().execute("bash", {"command": "touch created.txt"},
                                     sandbox=HostSandbox(wt),
                                     deadline=time.monotonic() - 1)
    assert "deadline exceeded" in out.text.lower()
    assert not (wt / "created.txt").exists()


def test_clamps_bash_timeout_to_remaining_deadline(wt: Path):
    captured = {}

    class CapturingSandbox(FakeSandbox):
        def bash(self, command, timeout):
            captured["timeout"] = timeout
            return "exit code: 0\n"

    default_registry().execute("bash", {"command": "true", "timeout": 600},
                               sandbox=CapturingSandbox(),
                               deadline=time.monotonic() + 3)
    assert 1 <= captured["timeout"] <= 3


def test_clamps_grep_timeout_to_remaining_deadline():
    captured = {}

    class CapturingSandbox(FakeSandbox):
        def grep(self, pattern, path, glob, timeout):
            captured["timeout"] = timeout
            return "No matches found."

    default_registry().execute("grep", {"pattern": "hi"}, sandbox=CapturingSandbox(),
                               deadline=time.monotonic() + 3)
    assert 1 <= captured["timeout"] <= 3


def test_logs_guardrail_block(wt: Path, tmp_path: Path):
    t = Transcript(tmp_path / "log.jsonl")
    registry = default_registry(transcript=t)
    out = registry.execute("bash", {"command": "git push"}, sandbox=HostSandbox(wt),
                           deadline=None)
    t.close()
    assert out.kind == "blocked"
    assert out.text.startswith("BLOCKED:")
    events = [json.loads(l) for l in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert any(e["event"] == "guardrail_block" for e in events)


def test_blocked_result_from_sandbox_marks_kind_blocked():
    class BlockingSandbox(FakeSandbox):
        def bash(self, command, timeout):
            return "BLOCKED: sudo is not allowed."

    result = default_registry().execute("bash", {"command": "sudo ls"},
                                        sandbox=BlockingSandbox(), deadline=None)
    assert result.kind == "blocked"
    assert result.failure is None


def test_budget_exceeded_propagates_over_file_limit(wt: Path):
    registry = default_registry()
    sb = HostSandbox(wt, max_worktree_files=3)
    # wt already has 1 entry (hello.txt from the fixture). Each write adds one
    # more; the check runs AFTER the write, so it must succeed through exactly
    # 3 total entries and only raise once a 4th is created.
    registry.execute("write_file", {"path": "a.txt", "content": "x"}, sandbox=sb, deadline=None)
    registry.execute("write_file", {"path": "b.txt", "content": "x"}, sandbox=sb, deadline=None)
    with pytest.raises(BudgetExceeded):
        registry.execute("write_file", {"path": "c.txt", "content": "x"}, sandbox=sb, deadline=None)
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_builtin_tools.py -q`
Expected: `ModuleNotFoundError: No module named 'dirtywork.builtin_tools'`

- [ ] **Step 4: Write `dirtywork/builtin_tools.py`**

Every `description` string below is copied verbatim from today's `dirtywork/tools.py:TOOL_SCHEMAS` — including the em dashes. If `test_schemas_match_the_frozen_v051_wire_contract` fails, diff the failing string against `tests/fixtures/tool_schemas_v051.json` and fix **this file**, never the fixture.

```python
"""The seven tools dirtywork ships, declared as ToolSpecs.

Each `fn` receives the Sandbox as its first argument and forwards to the
matching Sandbox method, so a tool never knows whether it is running on the
host or inside a container. Adding a tool means adding one ToolSpec here and
one method to the Sandbox protocol -- nothing in runner.py or __main__.py
changes.
"""
from __future__ import annotations

from .toolspec import Caps, ParamSpec, ToolRegistry, ToolSpec
from .tools import MAX_BASH_CHARS, MAX_RESULT_CHARS

# Caps.max_output_chars is an OUTER safety net. Every tool already truncates
# its own result (tools._cap) at MAX_RESULT_CHARS / MAX_BASH_CHARS and appends
# an explanatory note ("... — re-run with offset/limit to see more"). If the
# registry's cap were equal to the tool's own cap it would chop that note off
# and change shipped output, so it sits one note-length above it.
TOOL_OUTPUT_CAP = MAX_RESULT_CHARS + 512
BASH_OUTPUT_CAP = MAX_BASH_CHARS + 512


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


def _finish(sandbox, summary=""):
    """Never executed: the runner sees ToolSpec.terminal and ends the run
    itself, reading `summary` straight off the tool call (so a `finish` with no
    summary completes the run with an empty final message instead of taking a
    validation strike). Present only so the spec is a complete ToolSpec."""
    return "run finished"


READ_FILE_SPEC = ToolSpec(
    name="read_file",
    description="Read a file, returning numbered lines. Use offset/limit to "
                "page through; files over ~5 MB or non-regular files are refused.",
    params={
        "path": ParamSpec(type="string", description="Path relative to worktree root"),
        "offset": ParamSpec(type="integer", description="0-based first line, default 0", default=0),
        "limit": ParamSpec(type="integer", description="Max lines, default 400", default=400),
    },
    required=("path",),
    fn=_read_file,
    caps=Caps(fs="read", max_output_chars=TOOL_OUTPUT_CAP, transcript="preview"),
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
    caps=Caps(fs="write", max_output_chars=TOOL_OUTPUT_CAP, transcript="preview"),
)

EDIT_FILE_SPEC = ToolSpec(
    name="edit_file",
    description="Replace old_string with new_string in a file. old_string "
                "must occur exactly once — include surrounding context.",
    params={
        "path": ParamSpec(type="string"),
        "old_string": ParamSpec(type="string"),
        "new_string": ParamSpec(type="string"),
    },
    required=("path", "old_string", "new_string"),
    fn=_edit_file,
    caps=Caps(fs="write", max_output_chars=TOOL_OUTPUT_CAP, transcript="preview"),
)

LIST_DIR_SPEC = ToolSpec(
    name="list_dir",
    description="List a directory's entries (dirs end with /).",
    params={"path": ParamSpec(type="string", description="Default '.'", default=".")},
    required=(),
    fn=_list_dir,
    caps=Caps(fs="read", max_output_chars=TOOL_OUTPUT_CAP, transcript="preview"),
)

GREP_SPEC = ToolSpec(
    name="grep",
    description="Search file contents with a regex. Optional glob filter "
                "like '*.cs' or '*.tsx'.",
    params={
        "pattern": ParamSpec(type="string"),
        "path": ParamSpec(type="string", description="Default '.'", default="."),
        "glob": ParamSpec(type="string", default=None),
    },
    required=("pattern",),
    fn=_grep,
    caps=Caps(fs="read", max_output_chars=TOOL_OUTPUT_CAP, timeout_default=30,
              transcript="preview"),
)

BASH_SPEC = ToolSpec(
    name="bash",
    description="Run a shell command in the worktree (cwd is the worktree "
                "root). Use for builds/tests/git-status, NEVER for editing "
                "files. 120s default timeout, 600s max. Backgrounded "
                "processes are terminated when the command returns. In "
                "docker mode, a stray background process or an "
                "out-of-memory container triggers an automatic reset: the "
                "working tree survives, but any git state you created "
                "inside the sandbox (index changes, stashes, local "
                "commits) does not — write_file/edit_file changes and "
                "anything already written to disk are unaffected.",
    params={
        "command": ParamSpec(type="string"),
        "timeout": ParamSpec(type="integer", description="Seconds, default 120, max 600", default=120),
    },
    required=("command",),
    fn=_bash,
    caps=Caps(fs="write", network=True, max_output_chars=BASH_OUTPUT_CAP,
              timeout_default=120, timeout_max=600, transcript="preview"),
)

FINISH_SPEC = ToolSpec(
    name="finish",
    description=("End the run. Call this once the task is complete and verified "
                 "(tests/build run, changes committed if the task asked for commits). "
                 "summary: 2-6 sentences on what you did and anything left undone."),
    params={"summary": ParamSpec(type="string")},
    required=("summary",),
    fn=_finish,
    caps=Caps(fs="none", max_output_chars=TOOL_OUTPUT_CAP, transcript="preview"),
    terminal=True,
)

# Registration order is the order the tools are advertised to the model and the
# order the unknown-tool error lists them in. Do not reorder.
BUILTIN_SPECS = (READ_FILE_SPEC, WRITE_FILE_SPEC, EDIT_FILE_SPEC, LIST_DIR_SPEC,
                 GREP_SPEC, BASH_SPEC, FINISH_SPEC)


def default_registry(transcript=None) -> ToolRegistry:
    registry = ToolRegistry(transcript=transcript)
    for spec in BUILTIN_SPECS:
        registry.register(spec)
    return registry
```

- [ ] **Step 5: Run the new test to verify it passes**

Run: `python3 -m pytest tests/test_builtin_tools.py -q`
Expected: 20 passed. If `test_schemas_match_the_frozen_v051_wire_contract` fails, the failure output shows the exact differing string — fix `builtin_tools.py`.

- [ ] **Step 6: Remove `TOOL_SCHEMAS`/`_TOOL_PARAMS`/`ToolExecutor` from `tools.py`**

They live in one trailing block (`_param`, `TOOL_SCHEMAS`, `_TOOL_PARAMS`, `class ToolExecutor`, in that order, ending at end-of-file):

```bash
python3 - <<'PY'
from pathlib import Path
p = Path("dirtywork/tools.py")
text = p.read_text(encoding="utf-8")
marker = "\ndef _param(props: dict, required: list) -> dict:"
assert marker in text, "tools.py no longer has the expected _param marker -- inspect by hand"
p.write_text(text[:text.index(marker)].rstrip() + "\n", encoding="utf-8")
PY
python3 - <<'PY'
# `inspect` and `posixpath` were only used by ToolExecutor.canonical_args, and
# `time` only by ToolExecutor.execute's deadline math. Drop those three imports.
from pathlib import Path
p = Path("dirtywork/tools.py")
lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
body = "".join(lines)
for name in ("inspect", "posixpath", "time"):
    line = f"import {name}\n"
    assert line in body, f"import {name} not found -- inspect by hand"
    rest = body.replace(line, "", 1)
    assert f"{name}." not in rest, f"{name} is still used in tools.py -- keep its import"
    body = rest
p.write_text(body, encoding="utf-8")
print("trimmed unused imports")
PY
```

Then remove the moved tests from `tests/test_tools_bash.py`. These tests move to `tests/test_builtin_tools.py` (Step 2 above already contains their adapted bodies) — **delete exactly these ten and nothing else**:
`test_schemas_shape`, `test_executor_dispatch_and_unknown`, `test_executor_drops_unknown_tool_args`, `test_executor_still_raises_on_missing_required_arg`, `test_executor_deadline_exceeded_blocks_execution`, `test_executor_clamps_bash_timeout_to_remaining_deadline`, `test_executor_clamps_grep_timeout_to_remaining_deadline`, `test_executor_logs_guardrail_block`, `test_executor_raises_budget_exceeded_over_file_limit`, `test_bash_schema_mentions_reset_behavior`.

Everything else in the file (the bare `bash`/`grep` host-function tests: `test_bash_runs_in_worktree_cwd`, `test_bash_nonzero_exit_reported`, `test_bash_blocked_command`, `test_bash_timeout`, `test_bash_cd_into_worktree_by_absolute_path_allowed`, `test_bash_env_is_minimal`, `test_grep_timeout_kwarg_works`, `test_bash_output_is_capped`, `test_bash_runaway_output_times_out_without_ooming`, `test_bash_backgrounded_child_does_not_stall`, `test_bash_timeout_reaps_process_tree`, `test_bash_popen_failure_returns_error_prefix`) stays exactly as it is.

After deleting them, fix the imports at the top of `tests/test_tools_bash.py`:

```python
from dirtywork.tools import TOOL_SCHEMAS, ToolExecutor, bash, grep
```
becomes
```python
from dirtywork.tools import bash, grep
```

Then run `python3 -m pytest tests/test_tools_bash.py -q` and delete any import (`json`, `time`, `HostSandbox`, `Transcript`) the remaining tests no longer use, guided by the errors — do not delete `pytest` (the `wt` fixture needs it) or `Path`.

- [ ] **Step 7: Edit `dirtywork/runner.py` (six targeted edits)**

Do **not** rewrite this file. Apply these six edits exactly; everything else — `FailureTracker`, `strip_think`, `classify_text_reply`, `NUDGES`, `ProgressTracker`, `resolve_context_window`, `trim_messages`, `check_progress`, the `nudge` events — is untouched.

Edit 7a — the constructor. Replace:
```python
    def __init__(self, client, executor, transcript, model,
```
with:
```python
    def __init__(self, client, registry, sandbox, transcript, model,
```
and replace:
```python
        self.client = client
        self.executor = executor
        self.transcript = transcript
```
with:
```python
        self.client = client
        self.registry = registry
        self.sandbox = sandbox
        self.transcript = transcript
```

Edit 7b — drop the deferred `TOOL_SCHEMAS` import. Replace:
```python
    def run(self, system_prompt: str, task: str) -> RunResult:
        from .tools import TOOL_SCHEMAS

        messages = [
```
with:
```python
    def run(self, system_prompt: str, task: str) -> RunResult:
        messages = [
```

Edit 7c — the executor deadline attribute is gone (the registry takes `deadline` per call). Replace:
```python
        deadline = start + self.timeout
        self.executor.deadline = deadline
```
with:
```python
        deadline = start + self.timeout
```

Edit 7d — advertise the registry's schemas. Replace:
```python
                    resp = self.client.chat(self.model, messages, tools=TOOL_SCHEMAS,
```
with:
```python
                    resp = self.client.chat(self.model, messages, tools=self.registry.schemas(),
```

Edit 7e — terminal-tool interception and registry dispatch. Replace:
```python
                        if name == FINISH_TOOL:
                            summary = args.get("summary")
                            pending_finish = summary if isinstance(summary, str) else ""
                            result = "run finished"
                        else:
                            result = self.executor.execute(name, args)
                            failures.reset()
```
with:
```python
                        spec = self.registry.spec(name)
                        if spec is not None and spec.terminal:
                            summary = args.get("summary")
                            pending_finish = summary if isinstance(summary, str) else ""
                            result = "run finished"
                        else:
                            tool_result = self.registry.execute(
                                name, args, sandbox=self.sandbox, deadline=deadline)
                            result = tool_result.text
                            if tool_result.failure is not None:
                                abort_reason = failures.record(tool_result.failure)
                            else:
                                # A blocked command and a deadline refusal both
                                # reset the counter, exactly as ToolExecutor's
                                # non-raising return did.
                                failures.reset()
```

Edit 7f — the registry reports unknown tools and bad arguments as results, so the two `except` clauses that used to catch them go away, and the transcript preview now comes from the spec's `Caps`. Replace:
```python
                    except KeyError:
                        abort_reason = failures.record("unknown_tool")
                        available_tools = ', '.join(s['function']['name'] for s in TOOL_SCHEMAS)
                        result = (f"ERROR: unknown tool '{name}'. Available: {available_tools}. "
                                  f"To end the run call finish(summary=...).")
                    except TypeError as e:
                        abort_reason = failures.record("bad_args")
                        result = f"ERROR: bad arguments for {name}: {e}"
                    progress.note_call(name, self.executor.canonical_args(name, args), result)
                    self.transcript.write("tool_result", tool=name,
                                          args=raw_args[:500],
                                          result=result[:2000])
```
with:
```python
                    progress.note_call(name, self.registry.canonical_args(name, args), result)
                    self.transcript.write("tool_result", tool=name,
                                          args=raw_args[:500],
                                          result=self.registry.transcript_preview(name, result))
```

Finally, add a comment above the `FINISH_TOOL` constant (which stays — `runs`/`bench` and the system prompt still name the tool):
```python
# The terminal tool's NAME. The runner branches on ToolSpec.terminal, not on
# this constant; it is kept because the system prompt, the docs and the bench
# scoreboard all refer to the tool by name.
FINISH_TOOL = "finish"
```

- [ ] **Step 8: Patch `dirtywork/__main__.py` (three lines inside `_execute`)**

```bash
python3 - <<'PY'
from pathlib import Path
p = Path("dirtywork/__main__.py")
text = p.read_text(encoding="utf-8")

pairs = [
    ("from .tools import ToolExecutor",
     "from .builtin_tools import default_registry"),
    ("        executor = ToolExecutor(sandbox, transcript=transcript)",
     "        registry = default_registry(transcript=transcript)"),
    ("            client, executor, transcript, model=args.model,",
     "            client, registry, sandbox, transcript, model=args.model,"),
]
for old, new in pairs:
    assert text.count(old) == 1, f"expected exactly one occurrence of: {old!r}"
    text = text.replace(old, new)
p.write_text(text, encoding="utf-8")
print("patched __main__.py")
PY
```

- [ ] **Step 9: Patch `tests/test_runner.py` (fixture + call sites only)**

Every SP2.5 behaviour test stays. Only the `parts` fixture's shape and the `Runner(...)` call sites change; the fixture keeps using the real `HostSandbox` it already builds today.

```bash
python3 - <<'PY'
import re
from pathlib import Path
p = Path("tests/test_runner.py")
text = p.read_text(encoding="utf-8")

old_import = "from dirtywork.tools import ToolExecutor\n"
assert old_import in text
text = text.replace(old_import, "from dirtywork.builtin_tools import default_registry\n", 1)

old_fixture = '''    transcript = Transcript(tmp_path / "t.jsonl")
    executor = ToolExecutor(HostSandbox(wt), transcript=transcript)
    return wt, executor, transcript, tmp_path'''
new_fixture = '''    transcript = Transcript(tmp_path / "t.jsonl")
    registry = default_registry(transcript=transcript)
    sandbox = HostSandbox(wt)
    return wt, registry, sandbox, transcript, tmp_path'''
assert old_fixture in text, "the parts fixture body changed -- edit it by hand"
text = text.replace(old_fixture, new_fixture, 1)

n1 = len(re.findall(r"wt, executor, transcript, (tmp\w*) = parts", text))
text = re.sub(r"wt, executor, transcript, (tmp\w*) = parts",
              r"wt, registry, sandbox, transcript, \1 = parts", text)
n2 = len(re.findall(r"Runner\((\w+(?:\(\[\]\)|\(\))?), executor, transcript", text))
text = re.sub(r"Runner\((\w+(?:\(\[\]\)|\(\))?), executor, transcript",
              r"Runner(\1, registry, sandbox, transcript", text)
p.write_text(text, encoding="utf-8")
print(f"rewrote {n1} destructuring site(s), {n2} Runner(...) call site(s)")
PY
```
Expected output: `rewrote 56 destructuring site(s), 54 Runner(...) call site(s)` (the two-site gap is `test_budget_exceeded_from_executor_ends_run`, whose `Runner(client, BudgetBustingExecutor(), transcript, ...)` is rewritten by hand in 9a, and `test_canonical_args_normalizes_effective_arguments`, deleted in 9b). Anything materially lower means the regex missed sites — inspect before continuing.

Then make these three edits by hand, because their bodies (not just their call shape) change:

9a — `test_budget_exceeded_from_executor_ends_run` drove a fake **executor**; the budget now comes out of the sandbox. Replace the whole test with:
```python
def test_budget_exceeded_from_sandbox_ends_run(parts):
    wt, registry, sandbox, transcript, tmp = parts
    from dirtywork.budget import BudgetExceeded

    class BudgetBustingSandbox:
        def write_file(self, path, content):
            raise BudgetExceeded("worktree exceeds 2048 MB")

    client = FakeClient([_resp(tool_calls=[_call("c1", "write_file", {"path": "x", "content": "y"})])])
    r = Runner(client, registry, BudgetBustingSandbox(), transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "budget_exceeded"
    assert "2048 MB" in result.final_message
    events = _events(tmp)
    run_end = next(e for e in events if e["event"] == "run_end")
    assert run_end["status"] == "budget_exceeded"
```

9b — delete `test_canonical_args_normalizes_effective_arguments` (the last test in the file). It moved to `tests/test_toolspec.py` in Task 2, where it is asserted against `ToolRegistry.canonical_args` with registry-shaped specs.

9c — `test_mixed_failure_kinds_do_not_abort_at_three` relies on `read_file` with no `path` producing a **bad_args** strike. That still holds (the registry's `_validate_args` raises for the missing required parameter), but update its inline comment from `# missing required arg → TypeError → bad_args` to `# missing required arg → registry validation → bad_args`.

- [ ] **Step 10: Run the tool- and runner-facing tests**

Run: `python3 -m pytest tests/test_toolspec.py tests/test_builtin_tools.py tests/test_tools_bash.py tests/test_tools_files.py tests/test_runner.py -q`
Expected: all pass, with the same number of `test_runner.py` tests as before minus the one moved (`test_canonical_args_normalizes_effective_arguments`).

- [ ] **Step 11: Run the full suite**

Run: `python3 -m pytest -q`
Expected: 621 passed (612 after Task 2, +20 new in `test_builtin_tools.py`, −10 moved out of `test_tools_bash.py`, −1 moved out of `test_runner.py`)

- [ ] **Step 12: Commit**

```bash
git add dirtywork/builtin_tools.py dirtywork/tools.py dirtywork/runner.py dirtywork/__main__.py \
        tests/fixtures/tool_schemas_v051.json tests/test_builtin_tools.py \
        tests/test_tools_bash.py tests/test_runner.py tests/test_toolspec.py
git commit -m "refactor: replace ToolExecutor/TOOL_SCHEMAS with ToolRegistry and builtin ToolSpecs"
```

---

### Task 4: `providers/__init__.py` — `ToolCall`, `ChatResponse`, `Provider`, `get_provider`

**Files:**
- Create: `dirtywork/providers/__init__.py`
- Create: `tests/test_providers.py`
- Modify: `pyproject.toml` (`[tool.setuptools] packages`)

**Interfaces:**
- Consumes: nothing yet (`get_provider` lazily imports the concrete adapters created in Task 5/7, so this module never imports them at import time).
- Produces: `ToolCall(id: str, name: str, arguments: dict | None, error: str | None, raw_arguments: str = "")`; `ChatResponse(text: str, tool_calls: list, finish_reason: str | None, usage: dict)`; `Provider` Protocol (`name: str`; `list_models() -> list`; `context_window(model) -> int | None`; `chat(model, history, tools, *, temperature, max_tokens, timeout) -> ChatResponse`); `PROVIDER_NAMES = ("openai", "anthropic")`; `DEFAULT_BASE_URLS = {"openai": "http://localhost:1234/v1", "anthropic": "https://api.anthropic.com"}`; `get_provider(name: str, base_url: str | None = None, timeout: int = 600) -> Provider`; `assistant_message(text, tool_calls=None) -> dict`; `tool_message(call_id, text) -> dict`. Neutral history entries are dicts `{"role": "system"|"user"|"assistant"|"tool", "content": str, "tool_calls"?: [ToolCall], "tool_call_id"?: str}`.

`raw_arguments` is the provider's original argument payload as a string. The runner writes it to the transcript's `tool_result.args` (which is a raw JSON string today and must stay one), and the OpenAI adapter resends it verbatim so a call whose arguments failed to parse goes back to the model exactly as the model emitted it.

- [ ] **Step 1: Write the failing test**

`tests/test_providers.py`:

```python
from __future__ import annotations

import pytest

from dirtywork.providers import (
    DEFAULT_BASE_URLS,
    PROVIDER_NAMES,
    ChatResponse,
    ToolCall,
    assistant_message,
    get_provider,
    tool_message,
)


def test_provider_names_and_default_base_urls_agree():
    assert PROVIDER_NAMES == ("openai", "anthropic")
    assert DEFAULT_BASE_URLS == {
        "openai": "http://localhost:1234/v1",
        "anthropic": "https://api.anthropic.com",
    }
    assert set(DEFAULT_BASE_URLS) == set(PROVIDER_NAMES)


def test_tool_call_dataclass_fields():
    tc = ToolCall(id="c1", name="read_file", arguments={"path": "a.txt"}, error=None,
                  raw_arguments='{"path": "a.txt"}')
    assert (tc.id, tc.name, tc.arguments, tc.error) == (
        "c1", "read_file", {"path": "a.txt"}, None)
    assert tc.raw_arguments == '{"path": "a.txt"}'


def test_tool_call_raw_arguments_defaults_to_empty_string():
    tc = ToolCall(id="c1", name="read_file", arguments={}, error=None)
    assert tc.raw_arguments == ""


def test_chat_response_dataclass_fields():
    tc = ToolCall(id="c1", name="read_file", arguments={}, error=None)
    resp = ChatResponse(text="hi", tool_calls=[tc], finish_reason="stop",
                        usage={"prompt_tokens": 1, "completion_tokens": 1})
    assert resp.text == "hi"
    assert resp.tool_calls == [tc]
    assert resp.finish_reason == "stop"
    assert resp.usage == {"prompt_tokens": 1, "completion_tokens": 1}


def test_chat_response_defaults():
    resp = ChatResponse(text="hi")
    assert resp.tool_calls == [] and resp.finish_reason is None and resp.usage == {}


def test_assistant_message_without_tool_calls():
    assert assistant_message("hello", None) == {"role": "assistant", "content": "hello"}


def test_assistant_message_with_tool_calls():
    tc = ToolCall(id="c1", name="read_file", arguments={"path": "a.txt"}, error=None)
    msg = assistant_message(None, [tc])
    assert msg["role"] == "assistant"
    assert msg["content"] == ""
    assert msg["tool_calls"] == [tc]


def test_tool_message():
    assert tool_message("c1", "file contents") == {
        "role": "tool", "content": "file contents", "tool_call_id": "c1"}


def test_get_provider_unknown_raises():
    with pytest.raises(ValueError, match="unknown provider 'bogus'"):
        get_provider("bogus")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_providers.py -q`
Expected: `ModuleNotFoundError: No module named 'dirtywork.providers'`

- [ ] **Step 3: Write the minimal implementation**

`dirtywork/providers/__init__.py`:

```python
"""Provider-neutral chat surface.

The runner keeps a neutral history of plain dicts and never sees a wire shape:
serialization and deserialization live entirely in the adapters
(`openai_compat.py`, `anthropic.py`). `get_provider` is the only place that
knows which adapter a provider name maps to.
"""
from __future__ import annotations

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
```

- [ ] **Step 4: Add the package to `pyproject.toml`**

`[tool.setuptools] packages` is an explicit list; without this, an installed dirtywork ships no `dirtywork.providers` and `dirtywork run` fails at import in any non-editable install.

```bash
python3 - <<'PY'
from pathlib import Path
p = Path("pyproject.toml")
text = p.read_text(encoding="utf-8")
old = 'packages = ["dirtywork", "dirtywork.sandbox"]'
new = 'packages = ["dirtywork", "dirtywork.providers", "dirtywork.sandbox"]'
assert text.count(old) == 1, "packages line changed -- edit pyproject.toml by hand"
p.write_text(text.replace(old, new), encoding="utf-8")
print("patched pyproject.toml")
PY
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_providers.py -q`
Expected: 9 passed

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest -q`
Expected: 630 passed

- [ ] **Step 7: Commit**

```bash
git add dirtywork/providers/__init__.py tests/test_providers.py pyproject.toml
git commit -m "feat: add Provider protocol, ToolCall/ChatResponse, get_provider"
```

---

### Task 5: Provider contract suite + OpenAI fixtures + `OpenAICompatClient`

`dirtywork/llm.py` keeps exactly one job — the bounded stdlib HTTP transport (`http_json`) and the error types — and hands the OpenAI wire format to `providers/openai_compat.py`. Every hardening detail SP1 put into `LMStudioClient._request` must survive the move verbatim: `_underlying_socket`, the per-read `sock.settimeout(remaining)` guarded by `except OSError` (CI regression: `[Errno 9] Bad file descriptor` once http.client has already closed a fully-buffered body), the `MAX_RESPONSE_BYTES` cap, and the `e.read(500)` bound on an `HTTPError` body.

**Files:**
- Create: `tests/provider_contract.py`
- Create: `tests/fixtures/providers/openai/*.json` (8 files)
- Create: `tests/test_provider_openai.py`
- Create: `dirtywork/providers/openai_compat.py`
- Modify: `dirtywork/llm.py` (extract `http_json`; add `MalformedResponse`; `LMStudioClient` alias via module `__getattr__`)
- Modify: `tests/test_llm.py` (targeted: three return-shape assertions + new `http_json`-level tests)

**Interfaces:**
- Consumes: `dirtywork.providers.{ChatResponse, ToolCall, assistant_message, tool_message}`.
- Produces: `dirtywork.llm.http_json(url, payload, headers, timeout, *, method="POST") -> dict`; `dirtywork.llm.MalformedResponse(LLMError)`; `dirtywork.llm.LMStudioClient` (lazy alias of `OpenAICompatClient`); `dirtywork.providers.openai_compat.{CONTEXT_WINDOWS, parse_chat_response(body) -> ChatResponse, OpenAICompatClient(base_url=..., timeout=600, *, http_json=http_json)}` implementing `Provider` with `name = "openai"`; `tests.provider_contract.{ProviderContract, RecordingTransport}`.

**Why `MalformedResponse` exists:** today a response body without `choices[0].message` makes `Runner.run()` return `finish("model_error", ...)` — through `finish()`, so `finalize()` runs and a `run_end` event is written — while a transport-level `LLMError` *escapes* `Runner.run()` and is handled by `__main__._fail_run` (which keeps the docker volume for recovery; `test_main_docker_llm_error_after_start_finalizes_before_stop` pins this). Both behaviours must survive, so the adapter raises the narrower `MalformedResponse` for a body it cannot read, and the runner (Task 6) catches only that.

- [ ] **Step 1: Create the eight OpenAI wire-shape fixtures**

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

- [ ] **Step 2: Write the shared contract suite**

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
    """Shared behavioural contract every Provider adapter must satisfy. Subclass,
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
        resp = client.chat("model-x", [{"role": "user", "content": "read two files"}], [],
                           temperature=None, max_tokens=100, timeout=30)
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
        resp = client.chat("model-x", [{"role": "user", "content": "do two things"}], [],
                           temperature=None, max_tokens=100, timeout=30)
        assert len(resp.tool_calls) == 2
        ok_calls = [tc for tc in resp.tool_calls if tc.error is None]
        bad_calls = [tc for tc in resp.tool_calls if tc.error is not None]
        assert len(ok_calls) == 1 and ok_calls[0].name == "list_dir"
        assert len(bad_calls) == 1
        assert "malformed" in bad_calls[0].error.lower()
        # An unaddressable entry carries no id: the runner cannot answer it with
        # a tool result, and must count it as a malformed *entry*.
        assert bad_calls[0].id == ""

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
        # NaN/-5 are server-controlled and would emit invalid JSON downstream.
        assert resp.usage == {"prompt_tokens": 0, "completion_tokens": 0}

    def test_max_tokens_cutoff_mid_call(self):
        transport = RecordingTransport([self._load("bad_json_arguments.json")])
        client = self.make_client(transport)
        resp = client.chat("model-x", [{"role": "user", "content": "write a big file"}], [],
                           temperature=None, max_tokens=10, timeout=30)
        assert resp.finish_reason == "length"
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].error is not None
        # Addressable (it has an id), so the runner answers it with an error
        # tool result rather than dropping it.
        assert resp.tool_calls[0].id == "call_badargs"
```

- [ ] **Step 3: Write `tests/test_provider_openai.py`**

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dirtywork.llm import LLMError, MalformedResponse
from dirtywork.providers.openai_compat import OpenAICompatClient, parse_chat_response

from .provider_contract import ProviderContract, RecordingTransport

FIXTURES = Path(__file__).parent / "fixtures" / "providers" / "openai"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


def _client(transport):
    return OpenAICompatClient(base_url="http://fake/v1", http_json=transport)


class TestOpenAIProviderContract(ProviderContract):
    fixtures_dir = FIXTURES

    def make_client(self, transport):
        return _client(transport)

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
    _client(transport).chat("model-x", [{"role": "user", "content": "hi"}], [],
                            temperature=None, max_tokens=100, timeout=30)
    assert "tools" not in transport.calls[0]["payload"]


def test_chat_includes_tools_when_nonempty():
    transport = RecordingTransport([_fixture("simple_ok.json")])
    tools = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object", "properties": {}}}}]
    _client(transport).chat("model-x", [{"role": "user", "content": "hi"}], tools,
                            temperature=None, max_tokens=100, timeout=30)
    assert transport.calls[0]["payload"]["tools"] == tools


def test_chat_temperature_omitted_when_none_included_when_set():
    transport = RecordingTransport([_fixture("simple_ok.json"), _fixture("simple_ok.json")])
    client = _client(transport)
    client.chat("model-x", [{"role": "user", "content": "hi"}], [],
                temperature=None, max_tokens=100, timeout=30)
    assert "temperature" not in transport.calls[0]["payload"]
    client.chat("model-x", [{"role": "user", "content": "hi"}], [],
                temperature=0.2, max_tokens=100, timeout=30)
    assert transport.calls[1]["payload"]["temperature"] == 0.2


def test_list_models_returns_ids():
    transport = RecordingTransport([{"data": [{"id": "m1"}, {"id": "m2"}]}])
    client = _client(transport)
    assert client.list_models() == ["m1", "m2"]
    assert transport.calls[0]["method"] == "GET"
    assert transport.calls[0]["url"] == "http://fake/v1/models"


@pytest.mark.parametrize("bad_body", [
    {},
    {"data": "nope"},
    {"data": [{"nope": 1}]},
])
def test_list_models_unexpected_shape_raises_llmerror(bad_body):
    with pytest.raises(LLMError):
        _client(RecordingTransport([bad_body])).list_models()


def test_context_window_known_and_unknown_model():
    client = _client(RecordingTransport([]))
    assert client.context_window("qwen/qwen3-coder-next") == 65536
    assert client.context_window("mistralai/devstral-small-2-2512") == 32768
    assert client.context_window("nonexistent/model") is None


def test_provider_name_is_openai():
    assert _client(RecordingTransport([])).name == "openai"


# --- moved here from tests/test_runner.py: these assert how the OPENAI WIRE
# --- FORMAT is deserialized, which is now the adapter's job, not the runner's.

def test_arguments_null_treated_as_empty():
    body = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "list_dir", "arguments": None}}]},
        "finish_reason": "tool_calls"}]}
    resp = parse_chat_response(body)
    assert resp.tool_calls[0].arguments == {}
    assert resp.tool_calls[0].error is None
    assert resp.tool_calls[0].raw_arguments == "{}"


def test_valid_call_missing_type_field_is_accepted_and_canonicalized_on_resend():
    body = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "function": {"name": "list_dir", "arguments": "{}"}}]},
        "finish_reason": "tool_calls"}]}
    resp = parse_chat_response(body)
    assert resp.tool_calls[0].name == "list_dir"
    transport = RecordingTransport([_fixture("simple_ok.json")])
    from dirtywork.providers import assistant_message
    _client(transport).chat("m", [assistant_message(None, resp.tool_calls)], [],
                            temperature=None, max_tokens=10, timeout=5)
    sent = transport.calls[0]["payload"]["messages"][0]["tool_calls"][0]
    assert sent == {"id": "c1", "type": "function",
                    "function": {"name": "list_dir", "arguments": "{}"}}


def test_malformed_response_raises_malformed_response():
    for body in ({"choices": []}, {}, {"choices": [{"message": None}]},
                 {"choices": [{"message": "not an object"}]}):
        with pytest.raises(MalformedResponse):
            parse_chat_response(body)
    assert issubclass(MalformedResponse, LLMError)


def test_null_usage_tolerated():
    body = {"choices": [{"message": {"role": "assistant", "content": "hi"}}], "usage": None}
    assert parse_chat_response(body).usage == {"prompt_tokens": 0, "completion_tokens": 0}


def test_usage_ignores_non_finite_and_negative_from_server():
    body = json.loads('{"choices": [{"message": {"role": "assistant", "content": "hi"}}],'
                      ' "usage": {"prompt_tokens": Infinity, "completion_tokens": -3}}')
    assert parse_chat_response(body).usage == {"prompt_tokens": 0, "completion_tokens": 0}


def test_tool_calls_non_list_treated_as_absent():
    body = {"choices": [{"message": {"role": "assistant", "content": "hi",
                                     "tool_calls": "nope"}}]}
    assert parse_chat_response(body).tool_calls == []


@pytest.mark.parametrize("entry", [
    None,
    {},
    {"id": "", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}},
    {"id": "c1", "type": "function", "function": {"name": "", "arguments": "{}"}},
    {"id": "c1", "type": "function", "function": {"name": "list_dir", "arguments": 5}},
    {"id": "c1", "type": "function"},
])
def test_structurally_invalid_entries_become_id_less_error_tool_calls(entry):
    body = {"choices": [{"message": {"role": "assistant", "content": None,
                                     "tool_calls": [entry]}}]}
    tc = parse_chat_response(body).tool_calls[0]
    assert tc.id == ""
    assert tc.error == "malformed tool call entry (missing or invalid id/function fields)"


def test_mixed_invalid_and_valid_entries_keep_order():
    body = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        None,
        {"id": "c2", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}]}}]}
    calls = parse_chat_response(body).tool_calls
    assert [c.id for c in calls] == ["", "c2"]
    assert calls[0].error is not None and calls[1].error is None


def test_unparseable_arguments_keep_id_and_raw_text():
    resp = parse_chat_response(_fixture("bad_json_arguments.json"))
    tc = resp.tool_calls[0]
    assert tc.id == "call_badargs" and tc.name == "write_file"
    assert tc.arguments is None
    assert tc.error.startswith("malformed tool arguments:")
    assert tc.raw_arguments.startswith('{"path": "x"')


def test_non_object_arguments_are_a_malformed_args_error():
    body = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "list_dir", "arguments": "[1,2]"}}]}}]}
    tc = parse_chat_response(body).tool_calls[0]
    assert tc.id == "c1" and tc.arguments is None
    assert "must be a JSON object" in tc.error
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_provider_openai.py -q`
Expected: `ModuleNotFoundError: No module named 'dirtywork.providers.openai_compat'`

- [ ] **Step 5: Rewrite `dirtywork/llm.py`**

Overwrite the file in full. `MAX_RESPONSE_BYTES`, `_underlying_socket`, `LLMError`, `LLMTimeout` keep their names and semantics; `LMStudioClient._request` becomes the module-level `http_json`; the client itself moves to Task 5's adapter and is re-exposed here as a lazy alias.

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


class MalformedResponse(LLMError):
    """The endpoint answered, but the body is not a response we can read.

    Narrower than LLMError on purpose: Runner.run() converts this to
    status='model_error' through its own finish() (so finalize() runs and a
    run_end event is written), while a plain LLMError still escapes the runner
    to __main__._fail_run, which keeps a docker volume for recovery."""


def http_json(url: str, payload, headers: dict, timeout: float, *, method: str = "POST") -> dict:
    """Bounded stdlib HTTP JSON request shared by every Provider adapter:
    a whole-transfer wall-clock deadline (not urllib's per-socket-op timeout),
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
                try:
                    sock.settimeout(remaining)
                except OSError:  # http.client already closed the socket (body fully buffered)
                    sock = None
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


def __getattr__(name):
    """`LMStudioClient` moved to providers.openai_compat.OpenAICompatClient in
    0.6 (SP3). The alias is kept for one release, resolved LAZILY through PEP
    562 rather than an import at the bottom of this module: openai_compat
    imports http_json/LLMError from here, so an eager import here would make
    `import dirtywork.providers.openai_compat` (with llm not yet imported) fail
    against a partially-initialized module. New code should use
    dirtywork.providers.get_provider('openai', ...)."""
    if name == "LMStudioClient":
        from .providers.openai_compat import OpenAICompatClient
        return OpenAICompatClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

- [ ] **Step 6: Write `dirtywork/providers/openai_compat.py`**

```python
from __future__ import annotations

import json
import math

from . import ChatResponse, ToolCall
from ..llm import LLMError, MalformedResponse, http_json

DEFAULT_BASE_URL = "http://localhost:1234/v1"

# Moved here from runner.py: a context window is a property of the model as the
# provider serves it, and resolve_context_window now asks the provider.
CONTEXT_WINDOWS = {
    "qwen/qwen3-coder-next": 65536,
    "mistralai/devstral-small-2-2512": 32768,
}

MALFORMED_ENTRY = "malformed tool call entry (missing or invalid id/function fields)"


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
            # No usable id: the runner cannot answer this with a tool result.
            out.append(ToolCall(id="", name="", arguments=None, error=MALFORMED_ENTRY))
            continue
        fn = tc["function"]
        raw_args = fn.get("arguments") or "{}"
        try:
            parsed = json.loads(raw_args)
            if not isinstance(parsed, dict):
                raise ValueError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            out.append(ToolCall(id=tc["id"], name=fn["name"], arguments=None,
                                error=f"malformed tool arguments: {e}",
                                raw_arguments=raw_args))
            continue
        out.append(ToolCall(id=tc["id"], name=fn["name"], arguments=parsed, error=None,
                            raw_arguments=raw_args))
    return out


def _to_wire_tool_call(tc) -> dict:
    """Canonical OpenAI wire shape (id, type, function.arguments as a string) so
    the history we resend stays protocol-valid for strict servers — on every
    path that resends tool calls, not just when something was malformed. A call
    whose arguments failed to parse is resent with the model's own bytes."""
    if tc.raw_arguments:
        arguments = tc.raw_arguments
    else:
        arguments = json.dumps(tc.arguments or {})
    return {"id": tc.id, "type": "function",
            "function": {"name": tc.name, "arguments": arguments}}


def _to_openai_messages(history: list) -> list:
    messages = []
    for m in history:
        role = m["role"]
        if role == "assistant":
            msg = {"role": "assistant", "content": m.get("content") or ""}
            tool_calls = [tc for tc in (m.get("tool_calls") or []) if tc.id]
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
    raw = raw if isinstance(raw, dict) else {}
    for k in usage:
        # usage is server-controlled: NaN/Infinity would survive json.loads and
        # later emit invalid JSON on our stdout/transcript contract. Accept only
        # finite, non-negative numbers.
        v = raw.get(k, 0)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0:
            usage[k] = int(v)
    return usage


def parse_chat_response(body) -> ChatResponse:
    """Deserialize one OpenAI chat-completions body. Public because the CLI
    tests drive the runner with recorded wire bodies and must go through the
    same code path the real adapter uses."""
    try:
        msg = body["choices"][0]["message"]
        if not isinstance(msg, dict):
            raise TypeError("message is not an object")
    except (KeyError, IndexError, TypeError):
        raise MalformedResponse("malformed response from server (no choices[0].message)")
    finish_reason = body["choices"][0].get("finish_reason")
    raw_calls = msg.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        raw_calls = []
    text = msg.get("content") if isinstance(msg.get("content"), str) else ""
    return ChatResponse(text=text, tool_calls=_parse_tool_calls(raw_calls),
                        finish_reason=finish_reason, usage=_sanitize_usage(body.get("usage")))


class OpenAICompatClient:
    """Any OpenAI-compatible /v1 endpoint: LM Studio, vLLM, llama.cpp, Ollama's
    compat shim. `dirtywork.llm.LMStudioClient` is a deprecated alias."""

    name = "openai"

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = 600, *,
                 http_json=http_json):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._http_json = http_json

    def list_models(self) -> list:
        body = self._http_json(f"{self.base_url}/models", None,
                               {"Content-Type": "application/json"}, self.timeout,
                               method="GET")
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

    def chat(self, model, history, tools, *, temperature=None, max_tokens=4096,
             timeout=None) -> ChatResponse:
        payload = {"model": model, "messages": _to_openai_messages(history),
                   "max_tokens": max_tokens}
        if tools:
            payload["tools"] = tools
        if temperature is not None:
            payload["temperature"] = temperature
        effective_timeout = timeout if timeout is not None else self.timeout
        body = self._http_json(f"{self.base_url}/chat/completions", payload,
                               {"Content-Type": "application/json"}, effective_timeout)
        return parse_chat_response(body)
```

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `python3 -m pytest tests/test_provider_openai.py -q`
Expected: 30 passed (7 inherited from `ProviderContract` + 23 explicit, counting the two parametrized sets)

- [ ] **Step 8: Patch `tests/test_llm.py`**

Keep every existing test — including the `_RealLMStudio`/`real_server` CI regression test and `test_http_error_body_read_is_bounded`, which pin the two hardening details the move must not lose. Only three assertions change (`chat()` now returns a `ChatResponse`), plus new `http_json`-level tests and an alias test.

```bash
python3 - <<'PY'
from pathlib import Path
p = Path("tests/test_llm.py")
text = p.read_text(encoding="utf-8")

pairs = [
    # chat() returns ChatResponse, not the raw body
    ('    resp = client.chat("m1", [{"role": "user", "content": "x"}], tools=[])\n'
     '    assert resp["choices"][0]["message"]["content"] == "hi"\n'
     '    payload = _FakeLMStudio.last_payload',
     '    resp = client.chat("m1", [{"role": "user", "content": "x"}], tools=[])\n'
     '    assert resp.text == "hi"\n'
     '    payload = _FakeLMStudio.last_payload'),
    ('    resp = client.chat("m1", [{"role": "user", "content": "x"}], tools=[], timeout=5)\n'
     '    assert resp["choices"][0]["message"]["content"] == "hi"',
     '    resp = client.chat("m1", [{"role": "user", "content": "x"}], tools=[], timeout=5)\n'
     '    assert resp.text == "hi"'),
    ('    resp = client.chat("m", [{"role": "user", "content": "x"}], tools=[])\n'
     '    assert resp["choices"][0]["message"]["content"] == "hi"',
     '    resp = client.chat("m", [{"role": "user", "content": "x"}], tools=[])\n'
     '    assert resp.text == "hi"'),
    ('from dirtywork.llm import LLMError, LLMTimeout, LMStudioClient',
     'from dirtywork.llm import LLMError, LLMTimeout, LMStudioClient, http_json\n'
     'from dirtywork.providers.openai_compat import OpenAICompatClient'),
]
for old, new in pairs:
    assert text.count(old) == 1, f"expected exactly one occurrence of:\n{old}"
    text = text.replace(old, new)
p.write_text(text, encoding="utf-8")
print("patched test_llm.py")
PY
```

Then append these tests to `tests/test_llm.py` — the transport now has its own public entry point and deserves direct coverage:

```python
def test_http_json_get(server: str):
    body = http_json(f"{server}/models", None, {"Content-Type": "application/json"},
                     5, method="GET")
    assert body == {"data": [{"id": "m1"}, {"id": "m2"}]}


def test_http_json_post_roundtrip(server: str):
    body = http_json(f"{server}/chat/completions", {"a": 1},
                     {"Content-Type": "application/json"}, 5)
    assert body["choices"][0]["message"]["content"] == "hi"
    assert _FakeLMStudio.last_payload == {"a": 1}


def test_http_json_custom_headers_are_sent(server: str, monkeypatch):
    seen = {}
    import urllib.request
    real = urllib.request.urlopen

    def spy(req, timeout=None):
        seen.update(req.headers)
        return real(req, timeout=timeout)

    monkeypatch.setattr(urllib.request, "urlopen", spy)
    http_json(f"{server}/x", {"a": 1}, {"Content-Type": "application/json",
                                        "X-Api-Key": "secret"}, 5)
    assert seen.get("X-api-key") == "secret"


def test_http_json_connection_error_raises_llmerror():
    with pytest.raises(LLMError):
        http_json("http://127.0.0.1:1/x", {}, {"Content-Type": "application/json"}, 2)


def test_http_json_unparseable_url_raises_llmerror():
    with pytest.raises(LLMError):
        http_json("not-a-url", {}, {"Content-Type": "application/json"}, 2)


def test_lmstudio_client_alias_resolves_to_openai_compat_client():
    assert LMStudioClient is OpenAICompatClient


def test_importing_openai_compat_first_does_not_break_the_alias():
    # Regression: an eager `from .providers.openai_compat import ...` at the
    # bottom of llm.py made this ImportError when openai_compat was imported
    # before llm. PEP 562 __getattr__ keeps it lazy.
    import subprocess
    import sys
    rc = subprocess.run(
        [sys.executable, "-c",
         "import dirtywork.providers.openai_compat as oc;"
         " import dirtywork.llm as llm;"
         " assert llm.LMStudioClient is oc.OpenAICompatClient"],
        capture_output=True)
    assert rc.returncode == 0, rc.stderr.decode()
```

- [ ] **Step 9: Run the affected tests**

Run: `python3 -m pytest tests/test_llm.py tests/test_provider_openai.py tests/test_providers.py -q`
Expected: all pass.

- [ ] **Step 10: Run the full suite**

Run: `python3 -m pytest -q`
Expected: 667 passed (630 after Task 4, +30 provider-openai, +7 new llm tests). `tests/test_runner.py` and `tests/test_main.py` are untouched by this task: `Runner` still calls `client.chat(...)` on whatever object it was given, and `__main__` still imports `LMStudioClient` — which now resolves through the alias. (Every `test_main.py` test that patches `m.LMStudioClient.list_models` also patches `m.Runner.run` or fails before the run, so nothing drives a real `OpenAICompatClient` through the runner. `tests/test_live.py` and `tests/test_docker_live.py` are excluded by `addopts` here and are updated in Task 6.)

- [ ] **Step 11: Commit**

```bash
git add dirtywork/llm.py dirtywork/providers/openai_compat.py tests/provider_contract.py \
        tests/fixtures/providers/openai tests/test_provider_openai.py tests/test_llm.py
git commit -m "feat: extract http_json, add provider contract suite and OpenAICompatClient"
```

---

### Task 6: Runner on neutral history + `Provider`/`ChatResponse`; CLI `--provider`, provider-aware context window

A **targeted refactor**, not a rewrite. `dirtywork/runner.py` keeps every SP2.5 mechanism — `FailureTracker` and its per-kind/total thresholds, `strip_think`/`classify_text_reply`/`NUDGES`/`_join_nudges`, `ProgressTracker`/`check_progress`/`STALL_NUDGE`/`stalled`, terminal-tool interception, `trim_messages`, `MAX_ASSISTANT_TEXT_CHARS`, `RunResult.extra`, the `finalize` hook, and all five transcript events. What changes is only where wire shapes are parsed: the provider hands the runner a `ChatResponse` of `ToolCall`s.

**Files:**
- Modify: `dirtywork/runner.py`
- Modify: `dirtywork/__main__.py`
- Modify: `tests/test_runner.py`
- Create: `tests/provider_doubles.py`
- Modify: `tests/test_main.py`
- Modify: `tests/test_live.py`, `tests/test_docker_live.py` (marked `live`/`docker`; not run by the default suite, but they must still be correct)

**Interfaces:**
- Consumes: `dirtywork.providers.{ChatResponse, ToolCall, DEFAULT_BASE_URLS, PROVIDER_NAMES, get_provider, assistant_message, tool_message}`; `dirtywork.llm.{LLMError, LLMTimeout, MalformedResponse}`.
- Produces: `Runner(provider, registry, sandbox, transcript, model, max_turns=40, timeout=1800, temperature=None, run_info=None, finalize=None, stall_turns=DEFAULT_STALL_TURNS, context_window=None)`; `resolve_context_window(model, flag_value, env_value, provider=None) -> (int, source)` with `source` in `flag` | `env` | `provider:<name>` | `default`; CLI flag `--provider {openai,anthropic}` on both `run` and `resume`; `--base-url` defaulting to `DEFAULT_BASE_URLS[provider]`; `provider` in `run.json` and in the stdout JSON; `RunContext.provider`.

**What must NOT change:**
- A plain `LLMError` raised by `provider.chat()` still **escapes** `Runner.run()`. `__main__._fail_run` handles it — attempting `sandbox.finalize()` so a docker volume's work is exported before `finally: sandbox.stop()`. `test_main_docker_llm_error_after_start_finalizes_before_stop` pins this exact path, including `payload["export_status"] == "ok"`. Only the narrower `MalformedResponse` is caught inside the runner.
- `--context-window` and `DIRTYWORK_CONTEXT_WINDOW` already exist. Do not re-add them, and do not change their precedence over everything else.
- `run_start` already carries `context_window` and `provider`; `provider` simply stops being hard-coded to `"openai"`.

**Tests that move (R1).** These thirteen assert how the **OpenAI wire format** is deserialized — the adapter's job now, not the runner's. Task 5 already added their replacements to `tests/test_provider_openai.py`; this task deletes them from `tests/test_runner.py`:

| Deleted from `tests/test_runner.py` | Replacement in `tests/test_provider_openai.py` |
|---|---|
| `test_arguments_null_treated_as_empty` | `test_arguments_null_treated_as_empty` |
| `test_valid_call_missing_type_field_canonicalized_on_resend` | `test_valid_call_missing_type_field_is_accepted_and_canonicalized_on_resend` |
| `test_null_message_is_model_error` | `test_malformed_response_raises_malformed_response` |
| `test_null_usage_tolerated` | `test_null_usage_tolerated` |
| `test_usage_ignores_non_finite_from_server` | `test_usage_ignores_non_finite_and_negative_from_server` |
| `test_valid_tool_call_predicate` | `test_structurally_invalid_entries_become_id_less_error_tool_calls` |
| `test_tool_calls_non_list_treated_as_absent` | `test_tool_calls_non_list_treated_as_absent` |
| `test_malformed_tool_call_null_entry_recovers` | `test_structurally_invalid_entries_become_id_less_error_tool_calls[None]` |
| `test_empty_object_tool_call_recovers` | `test_structurally_invalid_entries_become_id_less_error_tool_calls[entry1]` |
| `test_empty_id_tool_call_recovers` | `test_structurally_invalid_entries_become_id_less_error_tool_calls[entry2]` |
| `test_missing_function_name_tool_call_recovers` | `test_structurally_invalid_entries_become_id_less_error_tool_calls[entry3]` |
| `test_mixed_null_and_valid_tool_call_recovers` | `test_mixed_invalid_and_valid_entries_keep_order` |
| (the argument-decode half of) `test_malformed_args_three_strikes` | `test_unparseable_arguments_keep_id_and_raw_text`, `test_non_object_arguments_are_a_malformed_args_error` |

`test_malformed_args_three_strikes` itself **stays** — its subject (three consecutive `malformed_args` strikes abort the run) is the runner's — it is just rebuilt on a `ToolCall` carrying an error. Every other test in `tests/test_runner.py` stays.

- [ ] **Step 1: Patch the helpers and doubles at the top of `tests/test_runner.py`**

Replace the import block and the `_resp`/`_call`/`FakeClient` helpers (lines from `from dirtywork.llm import LLMTimeout` through the end of `class FakeClient`) with:

```python
from dirtywork.llm import LLMError, LLMTimeout, MalformedResponse
from dirtywork.providers import ChatResponse, ToolCall
from dirtywork.providers.openai_compat import CONTEXT_WINDOWS
from dirtywork.runner import (
    DEFAULT_STALL_TURNS,
    DEFAULT_WINDOW,
    FailureTracker,
    MAX_TOTAL_CONSECUTIVE_FAILURES,
    NUDGES,
    ProgressTracker,
    RunResult,
    Runner,
    STALL_NUDGE,
    TRIM_MARKER,
    _bash_fingerprint,
    classify_text_reply,
    resolve_context_window,
    strip_think,
    trim_messages,
)
from dirtywork.sandbox.host import HostSandbox
from dirtywork.builtin_tools import default_registry
from dirtywork.transcript import Transcript


def _resp(content=None, tool_calls=None, usage=None, finish_reason=None):
    return ChatResponse(text=content or "",
                        tool_calls=list(tool_calls or []),
                        finish_reason=finish_reason,
                        usage=usage or {"prompt_tokens": 10, "completion_tokens": 5})


def _call(call_id, name, args: dict):
    return ToolCall(id=call_id, name=name, arguments=args, error=None,
                    raw_arguments=json.dumps(args))


def _bad_args(call_id="x", name="read_file", raw="{not json"):
    """A tool call the provider could parse structurally but whose arguments it
    could not decode: addressable (has an id), so the runner answers it with an
    error tool result and counts a `malformed_args` strike."""
    return ToolCall(id=call_id, name=name, arguments=None,
                    error="malformed tool arguments: bad JSON", raw_arguments=raw)


def _bad_entry():
    """A structurally invalid wire entry: no usable id, so the runner cannot
    answer it and counts a `malformed_entry` strike."""
    return ToolCall(id="", name="", arguments=None,
                    error="malformed tool call entry (missing or invalid id/function fields)")


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
        # Deep-copy the history the way the old FakeClient did, so later
        # mutation (trim_messages) cannot rewrite what a test already saw.
        self.requests.append([dict(m) for m in history])
        self.timeouts.append(timeout)
        return self.responses.pop(0)
```

Note the removed imports: `_valid_tool_call` (moved to the adapter) and `from dirtywork.tools import ToolExecutor` (removed in Task 3). `CONTEXT_WINDOWS` now comes from the adapter.

Then rename the double throughout the file:

```bash
python3 - <<'PY'
from pathlib import Path
p = Path("tests/test_runner.py")
t = p.read_text(encoding="utf-8")
n = t.count("FakeClient")
t = t.replace("FakeClient", "FakeProvider")
t = t.replace("client = FakeProvider", "provider = FakeProvider")
t = t.replace("Runner(client, registry", "Runner(provider, registry")
t = t.replace("client.requests", "provider.requests")
t = t.replace("client.timeouts", "provider.timeouts")
p.write_text(t, encoding="utf-8")
print(f"renamed {n} FakeClient reference(s)")
PY
```

Finally, three tests define their own bare doubles that only implement `chat`
(`SlowTimeoutClient`, `ImmediateTimeoutClient`, `InterruptingClient`). `Runner.__init__`
now asks the provider for a context window, so each must inherit the rest of the
protocol — change each `class XClient:` to `class XClient(FakeProvider):` and pass an
empty response list at its construction site:

```python
    class SlowTimeoutClient(FakeProvider):
        def chat(self, *a, **k):
            time.sleep(0.3)
            raise LLMTimeout("request timed out")

    r = Runner(SlowTimeoutClient([]), registry, sandbox, transcript, model="m", timeout=0.2)
```

```python
    class ImmediateTimeoutClient(FakeProvider):
        def chat(self, *a, **k):
            raise LLMTimeout("request timed out")

    r = Runner(ImmediateTimeoutClient([]), registry, sandbox, transcript, model="m", timeout=1800)
```

```python
    class InterruptingClient(FakeProvider):
        def chat(self, *a, **k):
            raise KeyboardInterrupt

    r = Runner(InterruptingClient([]), registry, sandbox, transcript, model="m")
```

- [ ] **Step 2: Delete the thirteen moved tests and rebuild the six wire-dict tests**

Delete these test functions from `tests/test_runner.py` (their replacements already live in `tests/test_provider_openai.py`, per the table above): `test_arguments_null_treated_as_empty`, `test_valid_call_missing_type_field_canonicalized_on_resend`, `test_null_message_is_model_error`, `test_null_usage_tolerated`, `test_usage_ignores_non_finite_from_server`, `test_valid_tool_call_predicate`, `test_tool_calls_non_list_treated_as_absent`, `test_malformed_tool_call_null_entry_recovers`, `test_empty_object_tool_call_recovers`, `test_empty_id_tool_call_recovers`, `test_missing_function_name_tool_call_recovers`, `test_mixed_null_and_valid_tool_call_recovers`.

Then replace these six tests, whose bodies built raw OpenAI dicts, with the versions below (same subject, same assertions, `ToolCall`-shaped inputs):

```python
def test_malformed_args_three_strikes(parts):
    wt, registry, sandbox, transcript, tmp = parts
    bad = _resp(tool_calls=[_bad_args()])
    provider = FakeProvider([bad, bad, bad])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"


def test_malformed_tool_call_entry_recovers(parts):
    # An entry the provider could not address at all (no id) routes through the
    # malformed-tool-call recovery path: no tool message, one user nudge.
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(tool_calls=[_bad_entry()]), _resp(content="ok done")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    second = provider.requests[1]
    assert not [m for m in second if m["role"] == "tool"]
    user_msgs = [m for m in second if m["role"] == "user"]
    assert any("malformed" in (m.get("content") or "").lower() for m in user_msgs)


def test_malformed_response_is_model_error(parts):
    # The adapter raises MalformedResponse for a body it cannot read; the runner
    # converts it through finish(), so finalize() runs and run_end is written.
    wt, registry, sandbox, transcript, tmp = parts

    class BadBodyProvider(FakeProvider):
        def chat(self, *a, **k):
            raise MalformedResponse("malformed response from server (no choices[0].message)")

    r = Runner(BadBodyProvider([]), registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"
    assert "malformed response from server" in result.final_message
    assert _events(tmp)[-1]["event"] == "run_end"


def test_plain_llm_error_escapes_the_runner(parts):
    # A transport-level LLMError is NOT caught here: __main__._fail_run handles
    # it so a docker volume's work is exported before the sandbox is stopped.
    wt, registry, sandbox, transcript, tmp = parts

    class DeadProvider(FakeProvider):
        def chat(self, *a, **k):
            raise LLMError("connection dropped")

    r = Runner(DeadProvider([]), registry, sandbox, transcript, model="m")
    with pytest.raises(LLMError):
        r.run("s", "t")
    transcript.close()


def test_strike_counter_resets_on_success(parts):
    wt, registry, sandbox, transcript, tmp = parts
    bad = _resp(tool_calls=[_bad_args()])
    good = _resp(tool_calls=[_call("g", "list_dir", {"path": "."})])
    provider = FakeProvider([bad, bad, good, bad, bad, _resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"


def test_length_finish_reason_gives_helpful_hint(parts):
    wt, registry, sandbox, transcript, tmp = parts
    truncated = _resp(tool_calls=[_bad_args("c", "write_file", '{"path": "x", "content": "abc')],
                      finish_reason="length",
                      usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([truncated, _resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    tool_msgs = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert "cut off at the token limit" in tool_msgs[0]["content"]


def test_three_consecutive_malformed_tool_calls_aborts(parts):
    wt, registry, sandbox, transcript, tmp = parts
    bad = _resp(tool_calls=[_bad_entry()])
    provider = FakeProvider([bad, bad, bad])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"


def test_malformed_entries_on_stall_nudge_turn_send_one_merged_user_message(parts):
    wt, registry, sandbox, transcript, tmp = parts
    idle = _resp(tool_calls=[_call("c", "read_file", {"path": "f.txt"})])
    bad_entry = _resp(tool_calls=[_bad_entry()],
                      usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([idle, idle, bad_entry, _resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m", stall_turns=4)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    fourth = provider.requests[3]
    assert fourth[-1]["role"] == "user"
    assert "were malformed" in fourth[-1]["content"]
    assert STALL_NUDGE.format(n=2) in fourth[-1]["content"]
    _no_consecutive_user_messages(provider.requests)


def test_malformed_entry_abort_reports_first_threshold(parts):
    wt, registry, sandbox, transcript, tmp = parts
    six_bad = _resp(tool_calls=[_bad_entry() for _ in range(6)],
                    usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([six_bad])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"
    assert result.final_message == "aborted after 3 consecutive malformed_entry failures"


def test_mixed_failure_kinds_do_not_abort_at_three(parts):
    wt, registry, sandbox, transcript, tmp = parts
    bad_args = _resp(tool_calls=[_bad_args()])
    unknown = _resp(tool_calls=[_call("u", "no_such_tool", {})])
    wrong_type = _resp(tool_calls=[_call("t", "read_file", {})])  # missing required arg → registry validation → bad_args
    provider = FakeProvider([bad_args, unknown, wrong_type, _resp(content="ok done")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert result.turns == 4
```

- [ ] **Step 3: Rewrite the four context-window tests in `tests/test_runner.py`**

The table moved into the provider, so the runner and `resolve_context_window` ask the provider for it.

```python
@pytest.mark.parametrize("model,flag,env,window,expected", [
    ("qwen/qwen3-coder-next", None, None, 65536, (65536, "provider:fake")),
    ("unknown/model", None, None, None, (DEFAULT_WINDOW, "default")),
    ("qwen/qwen3-coder-next", 8000, None, 65536, (8000, "flag")),
    ("qwen/qwen3-coder-next", None, "9000", 65536, (9000, "env")),
    ("unknown/model", 8000, "9000", None, (8000, "flag")),
    ("unknown/model", None, "", None, (DEFAULT_WINDOW, "default")),
])
def test_resolve_context_window(model, flag, env, window, expected):
    provider = FakeProvider([], context_window=window)
    assert resolve_context_window(model, flag, env, provider) == expected


def test_resolve_context_window_without_a_provider_falls_back_to_default():
    assert resolve_context_window("qwen/qwen3-coder-next", None, None) == (DEFAULT_WINDOW, "default")


def test_resolve_context_window_uses_the_real_openai_table():
    from dirtywork.providers.openai_compat import OpenAICompatClient
    provider = OpenAICompatClient(base_url="http://fake/v1")
    assert resolve_context_window("qwen/qwen3-coder-next", None, None, provider) == \
        (CONTEXT_WINDOWS["qwen/qwen3-coder-next"], "provider:openai")


@pytest.mark.parametrize("env", ["abc", "0", "-5", "1.5"])
def test_resolve_context_window_rejects_bad_env(env):
    with pytest.raises(ValueError):
        resolve_context_window("m", None, env, FakeProvider([]))


def test_runner_context_window_defaults_from_the_provider(parts):
    wt, registry, sandbox, transcript, tmp = parts
    r = Runner(FakeProvider([], context_window=65536), registry, sandbox, transcript,
               model="qwen/qwen3-coder-next")
    assert r.context_window == 65536
    r2 = Runner(FakeProvider([], context_window=None), registry, sandbox, transcript, model="m")
    assert r2.context_window == DEFAULT_WINDOW


def test_runner_context_window_zero_is_not_replaced_by_the_provider(parts):
    wt, registry, sandbox, transcript, tmp = parts
    r = Runner(FakeProvider([], context_window=65536), registry, sandbox, transcript,
               model="qwen/qwen3-coder-next", context_window=0)
    assert r.context_window == 0
```

(`test_runner_context_window_param_sets_budget_and_run_start` is unchanged apart from the `FakeClient`→`FakeProvider` rename Step 1 already applied.)

- [ ] **Step 4: Run the runner tests to verify they fail**

Run: `python3 -m pytest tests/test_runner.py -q`
Expected: failures such as `AttributeError: 'ChatResponse' object has no attribute 'get'` — the runner still expects an OpenAI dict.

- [ ] **Step 5: Edit `dirtywork/runner.py`**

Do **not** rewrite the file. Apply these eight edits.

5a — imports and the moved table. Replace:
```python
from .budget import BudgetExceeded
from .llm import LLMTimeout
from .sandbox import SandboxError

MAX_ASSISTANT_TEXT_CHARS = 64_000

CONTEXT_WINDOWS = {
    "qwen/qwen3-coder-next": 65536,
    "mistralai/devstral-small-2-2512": 32768,
}
DEFAULT_WINDOW = 32768
```
with:
```python
from .budget import BudgetExceeded
from .llm import LLMTimeout, MalformedResponse
from .providers import assistant_message, tool_message
from .sandbox import SandboxError

MAX_ASSISTANT_TEXT_CHARS = 64_000

# The per-model context-window table moved to the provider that serves those
# models (providers/openai_compat.py): resolve_context_window asks the provider.
DEFAULT_WINDOW = 32768
```

Also delete the now-unused `import math` (usage sanitizing lives in the adapter) and the whole `_valid_tool_call`/`_canonical_tool_call` pair (moved to `providers/openai_compat.py`). Keep `import hashlib`, `import json`, `import re`, `import time`.

5b — `resolve_context_window` gains the provider step. Replace the whole function with:
```python
def resolve_context_window(model: str, flag_value, env_value, provider=None) -> tuple:
    """Precedence: --context-window > DIRTYWORK_CONTEXT_WINDOW > the provider's
    own table for this model > DEFAULT_WINDOW. Returns (tokens, source) with
    source in flag|env|provider:<name>|default. Raises ValueError for an env
    value that is not a positive integer."""
    if flag_value is not None:
        return int(flag_value), "flag"
    if env_value not in (None, ""):
        try:
            value = int(env_value)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            raise ValueError(
                f"DIRTYWORK_CONTEXT_WINDOW must be a positive integer, got {env_value!r}")
        return value, "env"
    if provider is not None:
        window = provider.context_window(model)
        if window:
            return int(window), f"provider:{getattr(provider, 'name', 'provider')}"
    return DEFAULT_WINDOW, "default"
```

5c — `_total_chars` counts `ToolCall` arguments instead of wire strings. Replace:
```python
def _total_chars(messages: list) -> int:
    total = 0
    for m in messages:
        total += len(m.get("content") or "")
        for tc in m.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            total += len((tc.get("function") or {}).get("arguments") or "")
    return total
```
with:
```python
def _tool_call_arg_chars(tc) -> int:
    if tc.raw_arguments:
        return len(tc.raw_arguments)
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
```

5d — the constructor takes a provider and asks it for the window. Replace:
```python
    def __init__(self, client, registry, sandbox, transcript, model,
```
with:
```python
    def __init__(self, provider, registry, sandbox, transcript, model,
```
replace:
```python
        self.client = client
        self.registry = registry
```
with:
```python
        self.provider = provider
        self.registry = registry
```
and replace:
```python
        self.context_window = context_window if context_window is not None else CONTEXT_WINDOWS.get(model, DEFAULT_WINDOW)
```
with:
```python
        # An explicit 0 is honoured (it is how a test forces context_exhausted);
        # only None means "ask the provider".
        self.context_window = (context_window if context_window is not None
                               else (provider.context_window(model) or DEFAULT_WINDOW))
```

5e — the chat call and its error mapping. Replace:
```python
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
```
with:
```python
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
                except MalformedResponse as e:
                    # A body we cannot read is a model failure, not a transport
                    # failure: end through finish() so finalize() runs and a
                    # run_end event is written. A plain LLMError deliberately
                    # escapes to __main__._fail_run instead.
                    return finish("model_error", str(e))
```

5f — response unpacking. Replace everything from `turns += 1` down to (and including) the `self.transcript.write("assistant", ...)` call — that is:
```python
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
```
with:
```python
                turns += 1
                finish_reason = resp.finish_reason
                # The adapter already sanitized usage (finite, non-negative).
                for k in usage:
                    usage[k] += resp.usage.get(k, 0)
                # An entry the provider could not address (no id) cannot be
                # answered with a tool result: that is a malformed *entry*. One
                # with an id but undecodable arguments is answerable.
                malformed_count = sum(1 for tc in resp.tool_calls
                                      if tc.error is not None and not tc.id)
                tool_calls = [tc for tc in resp.tool_calls if tc.id]
                transcript_text = resp.text
                if isinstance(transcript_text, str) and len(transcript_text) > MAX_ASSISTANT_TEXT_CHARS:
                    transcript_text = (
                        transcript_text[:MAX_ASSISTANT_TEXT_CHARS]
                        + f"\n[truncated at {MAX_ASSISTANT_TEXT_CHARS} chars in the transcript "
                          f"only — the full text was sent to the model]"
                    )
                self.transcript.write(
                    "assistant", text=transcript_text,
                    tool_calls=[{"name": tc.name, "arguments": (tc.raw_arguments or "")[:2000]}
                                for tc in tool_calls])
```

5g — history append. Replace:
```python
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
                    content = msg.get("content") if isinstance(msg.get("content"), str) else ""
                    kind = classify_text_reply(msg.get("content"), finish_reason)
                    if kind == "answer":
                        messages.append(msg)
                        return finish("completed", content)
                    messages.append({"role": "assistant", "content": content})
```
with:
```python
                if resp.tool_calls:
                    # The adapter re-serializes these into whatever wire shape
                    # its protocol needs; the runner keeps neutral objects.
                    messages.append(assistant_message(resp.text, tool_calls))
                else:
                    content = resp.text
                    kind = classify_text_reply(content, finish_reason)
                    if kind == "answer":
                        messages.append(assistant_message(content, None))
                        return finish("completed", content)
                    messages.append(assistant_message(content, None))
```

5h — the tool-call loop. Replace the whole block from `pending_finish = None` down to (but not including) `if pending_finish is not None:` with:
```python
                pending_finish = None
                for tc in tool_calls:
                    name = tc.name
                    raw_args = tc.raw_arguments or "{}"
                    args = tc.arguments
                    abort_reason = None
                    if tc.error is not None:
                        abort_reason = failures.record("malformed_args")
                        if finish_reason == "length":
                            result = (
                                "ERROR: your reply was cut off at the token limit before "
                                "the tool call completed. Emit smaller tool calls — e.g. "
                                "write the file in pieces using multiple write_file/"
                                "edit_file calls."
                            )
                        else:
                            result = f"ERROR: {tc.error}"
                    else:
                        try:
                            spec = self.registry.spec(name)
                            if spec is not None and spec.terminal:
                                summary = args.get("summary")
                                pending_finish = summary if isinstance(summary, str) else ""
                                result = "run finished"
                            else:
                                tool_result = self.registry.execute(
                                    name, args, sandbox=self.sandbox, deadline=deadline)
                                result = tool_result.text
                                if tool_result.failure is not None:
                                    abort_reason = failures.record(tool_result.failure)
                                else:
                                    failures.reset()
                        except BudgetExceeded as e:
                            return finish("budget_exceeded", e.reason)
                        except SandboxError as e:
                            return finish("sandbox_error", str(e))
                    progress.note_call(name, self.registry.canonical_args(name, args), result)
                    self.transcript.write("tool_result", tool=name,
                                          args=raw_args[:500],
                                          result=self.registry.transcript_preview(name, result))
                    messages.append(tool_message(tc.id, result))
                    if abort_reason is not None:
                        return finish("model_error", abort_reason)
```

- [ ] **Step 6: Run the runner tests to verify they pass**

Run: `python3 -m pytest tests/test_runner.py -q`
Expected: all pass (the file has 13 fewer tests than before this task, and 1 more: `test_plain_llm_error_escapes_the_runner`, plus the two extra `resolve_context_window` cases).

- [ ] **Step 7: Patch `dirtywork/__main__.py`**

7a — imports. Replace:
```python
from .llm import LLMError, LMStudioClient
```
with:
```python
from .llm import LLMError
from .providers import DEFAULT_BASE_URLS, PROVIDER_NAMES, get_provider
```

7b — `RunContext` gains the provider (right below `sandbox_mode`):
```python
    sandbox_mode: str
    provider: str
```

7c — replace `_preflight_llm` in full:
```python
_ENDPOINT_HINTS = {
    "openai": "Is the OpenAI-compatible server running? Try: lms ps",
    "anthropic": "Check ANTHROPIC_API_KEY and that api.anthropic.com is reachable.",
}


def _preflight_llm(args):
    """Resolve --base-url against the chosen provider's default (recorded on
    args so run_start/run.json report the endpoint actually used), then prove
    the endpoint is reachable and the model is available."""
    if args.base_url is None:
        args.base_url = DEFAULT_BASE_URLS[args.provider]
    provider = get_provider(args.provider, args.base_url)
    try:
        models = provider.list_models()
    except LLMError as e:
        raise PreflightFailure(f"{e}\n{_ENDPOINT_HINTS.get(args.provider, '')}")
    if args.model not in models:
        hint = (f"Load it with: lms load {args.model}" if args.provider == "openai"
                else f"Pick one of the models listed above with --model.")
        raise PreflightFailure(
            f"model '{args.model}' not loaded (loaded: {', '.join(models) or 'none'}). {hint}")
    return provider
```

7d — `_resolve_context_window` takes the provider. Replace:
```python
def _resolve_context_window(args) -> int:
    try:
        window, source = resolve_context_window(
            args.model, args.context_window, os.environ.get("DIRTYWORK_CONTEXT_WINDOW"))
```
with:
```python
def _resolve_context_window(args, provider=None) -> int:
    try:
        window, source = resolve_context_window(
            args.model, args.context_window, os.environ.get("DIRTYWORK_CONTEXT_WINDOW"),
            provider)
```
and in `main()`, replace `context_window = _resolve_context_window(args)` with `context_window = _resolve_context_window(args, client)`.

7e — both `RunContext(...)` constructions record the provider. In `_workspace_new` replace `sandbox_mode=args.sandbox, image_ref=image_ref,` with `sandbox_mode=args.sandbox, provider=args.provider, image_ref=image_ref,`; in `_workspace_resume` replace `base_commit=prior["base_commit"], task=task, sandbox_mode=args.sandbox,` with `base_commit=prior["base_commit"], task=task, sandbox_mode=args.sandbox, provider=args.provider,`.

7f — `run.json` records the provider. In `_write_run_json_start`, add after `"model": args.model,`:
```python
        "provider": ctx.provider,
```

7g — the stdout JSON records the provider. In `_emit_result`, add `provider: str` to the signature (after `run_dir: Path`) and `"provider": provider,` to the payload dict, then pass `provider=ctx.provider` at all three call sites (`_fail_setup`, `_fail_run`, and the success path in `_execute`).

7h — `run_info` stops hard-coding the provider. Replace `"sandbox": sandbox_info, "provider": "openai",` with `"sandbox": sandbox_info, "provider": ctx.provider,`.

7i — the flags. In `_add_run_flags`, replace:
```python
    p.add_argument("--base-url", default="http://localhost:1234/v1")
```
with:
```python
    p.add_argument("--provider", choices=list(PROVIDER_NAMES),
                   default=None if resume else "openai",
                   help="model provider (default: openai — any OpenAI-compatible endpoint)")
    p.add_argument("--base-url", default=None,
                   help="provider endpoint (default: the provider's own default)")
```

7j — `resume` inherits the provider and refuses a switch, the same way it pins `sandbox`. In `_load_resume_target`, replace:
```python
    args.sandbox = prior["sandbox"]
    if args.model is None:
        args.model = prior["model"]
```
with:
```python
    args.sandbox = prior["sandbox"]
    prior_provider = prior.get("provider") or "openai"
    if args.provider is None:
        args.provider = prior_provider
    elif args.provider != prior_provider:
        # Same rule as --sandbox (which resume does not expose at all): the
        # prior run's history was shaped by that provider's wire format.
        raise PreflightFailure(
            f"run {prior['slug']} used provider '{prior_provider}'; resume it with that "
            f"provider (drop --provider {args.provider}) or start a new run")
    if args.model is None:
        args.model = prior["model"]
```

- [ ] **Step 8: Write `tests/provider_doubles.py`**

The CLI tests were written against a client that returned raw OpenAI bodies. Rather than hand-translating a dozen fakes into `ChatResponse` builders, they keep writing wire bodies and go through the real adapter's deserializer — the same code path production uses.

```python
"""Shared provider doubles for the CLI tests (no test_ prefix: imported, never
collected). A double writes OpenAI wire bodies and this base turns them into a
ChatResponse with the real adapter's parser, so a CLI test exercises the same
deserialization production does."""
from __future__ import annotations

from dirtywork.providers.openai_compat import parse_chat_response


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


DEFAULT_MODEL = "qwen/qwen3-coder-next"
```

Move the `DEFAULT_MODEL = "qwen/qwen3-coder-next"` assignment to the top of the module (above `DictProvider`) so `list_models` can see it; it must equal `dirtywork.__main__.DEFAULT_MODEL`, and `tests/test_main.py` already asserts through `m.DEFAULT_MODEL`, so add this test to `tests/test_providers.py`:

```python
def test_provider_double_default_model_matches_the_cli():
    import dirtywork.__main__ as m
    from tests.provider_doubles import DEFAULT_MODEL
    assert DEFAULT_MODEL == m.DEFAULT_MODEL
```

- [ ] **Step 9: Patch `tests/test_main.py`**

Two mechanical rewrites plus one new test.

9a — the ten preflight-only patch sites and the sixteen fake-client installation sites:
```bash
python3 - <<'PY'
from pathlib import Path
p = Path("tests/test_main.py")
t = p.read_text(encoding="utf-8")
old = 'monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])'
new = 'patch_provider(monkeypatch, m, PreflightProvider)'
n = t.count(old)
t = t.replace(old, new)
# Every remaining installation site has the form
#   monkeypatch.setattr(m, "LMStudioClient", <factory>)
# (sometimes with the factory on the next line); only the prefix changes, so
# the closing paren stays balanced either way.
old_install = 'monkeypatch.setattr(m, "LMStudioClient",'
new_install = 'patch_provider(monkeypatch, m,'
k = t.count(old_install)
t = t.replace(old_install, new_install)
old_import = "from dirtywork.sandbox.docker_cli import DockerError\n"
assert old_import in t
t = t.replace(old_import, old_import +
              "\nfrom .provider_doubles import (DictProvider, PreflightProvider, patch_provider,\n"
              "                               text_body, tool_call_body)\n", 1)
p.write_text(t, encoding="utf-8")
print(f"patched {n} preflight site(s), {k} installation site(s)")
PY
```
Expected output: `patched 10 preflight site(s), 16 installation site(s)`

9b — the fourteen in-test fake clients. Each has the same shape: a `__init__(self, base_url=None)`, a `list_models`, and a `chat(self, model, messages, tools, temperature=None, max_tokens=4096, timeout=None)` returning an OpenAI body. Convert each one by hand with these four mechanical edits, in this order:

1. `class X:` → `class X(DictProvider):`
2. delete its `def list_models(self): return [m.DEFAULT_MODEL]` (the base provides it)
3. `def __init__(self, base_url=None): pass` → delete it entirely; `def __init__(self, base_url=None): self.calls = 0` → delete it too (the base already sets `self.calls = 0`)
4. `def chat(self, model, messages, tools, temperature=None, max_tokens=4096, timeout=None):` → `def reply(self, model, messages, tools):`, keeping the body byte for byte (it already returns a wire dict; `DictProvider.chat` parses it and counts the call)

Its installation line was already rewritten by the script in 9a.

The fourteen classes, in file order: `WritingFakeClient` (docker end-to-end), `ImmediateDoneClient` ×2 (docker preflight image-ref, docker sandbox_info), `OneBashCallClient`, `FlakyClient`, `ImmediateDoneClient` ×4 (run.json start/end, keep-volume, watchdog), `BashCallingClient`, `ImmediateDoneClient` (run_start provenance), `WritingFakeClient` ×2 (host-mode diff_stat/untracked), `_ScriptedClient` (module-level, used by `_install_host_harness` and the resume tests).

`_ScriptedClient` additionally keeps its `instances` list, its `responses` constructor argument and its "last response repeats" behaviour; convert it to:

```python
class _ScriptedClient(DictProvider):
    """Provider stand-in driven by a list of OpenAI chat bodies; the last
    response repeats so a run can never underflow."""
    instances = []

    def __init__(self, base_url=None, responses=None):
        super().__init__(base_url)
        self.responses = list(responses or [text_body()])
        _ScriptedClient.instances.append(self)

    def list_models(self):
        import dirtywork.__main__ as m
        return [m.DEFAULT_MODEL, "other/model"]

    def reply(self, model, messages, tools):
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]
```
`_install_host_harness` and the two resume tests install it through a `lambda base_url=None: _ScriptedClient(base_url, …)` factory; the script in 9a already rewrote those three lines to `patch_provider(monkeypatch, m, lambda base_url=None: _ScriptedClient(...))`.

`FlakyClient` raises `LLMError` from what is now `reply`; that still propagates out of `DictProvider.chat` unchanged, which is the point of `test_main_docker_llm_error_after_start_finalizes_before_stop`.

9c — new tests for the flag surface. Append to `tests/test_main.py`:

```python
def test_main_unknown_provider_rejected_by_argparse(tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        main(["run", "--repo", str(tmp_path), "--provider", "bogus", "do things"])
    assert exc_info.value.code == 2


def test_base_url_defaults_per_provider(tmp_path, monkeypatch, capsys):
    import dirtywork.__main__ as m
    from dirtywork.runner import RunResult
    repo = _host_repo(tmp_path)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    seen = {}

    def factory(base_url=None):
        seen["base_url"] = base_url
        return PreflightProvider(base_url)

    patch_provider(monkeypatch, m, factory)
    monkeypatch.setattr(m.Runner, "run", lambda self, sp, task: RunResult("completed", 1, "ok", {}))
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "task"])
    assert rc == 0
    assert seen["base_url"] == "http://localhost:1234/v1"


def test_run_json_and_stdout_record_the_provider(tmp_path, monkeypatch, capsys):
    import dirtywork.__main__ as m
    m2 = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    rc = m2.main(["run", "--repo", str(repo), "--sandbox", "none", "task"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["provider"] == "openai"
    run_json = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert run_json["provider"] == "openai"
    transcript = (Path(payload["run_dir"]) / "transcript.jsonl").read_text().splitlines()
    run_start = next(json.loads(l) for l in transcript if json.loads(l)["event"] == "run_start")
    assert run_start["provider"] == "openai"
    assert run_start["base_url"] == "http://localhost:1234/v1"


def test_resume_refuses_a_provider_switch(tmp_path, monkeypatch, capsys):
    import dirtywork.__main__ as m
    m2 = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    assert m2.main(["run", "--repo", str(repo), "--sandbox", "none", "task"]) == 0
    slug = json.loads(capsys.readouterr().out)["run_dir"].rsplit("/", 1)[-1]
    rc = m2.main(["resume", str(tmp_path / "runs" / slug), "--provider", "anthropic"])
    assert rc == 2
    assert "provider 'openai'" in capsys.readouterr().err


def test_resume_inherits_the_prior_provider(tmp_path, monkeypatch, capsys):
    import dirtywork.__main__ as m
    m2 = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    assert m2.main(["run", "--repo", str(repo), "--sandbox", "none", "task"]) == 0
    slug = json.loads(capsys.readouterr().out)["run_dir"].rsplit("/", 1)[-1]
    assert m2.main(["resume", str(tmp_path / "runs" / slug)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["provider"] == "openai"
```

- [ ] **Step 10: Patch the live suites (`-m live` / `-m docker`)**

`tests/test_live.py`: replace `from dirtywork.llm import LLMError, LMStudioClient` with `from dirtywork.llm import LLMError` + `from dirtywork.providers import get_provider`, replace both `LMStudioClient(...)` constructions with `get_provider("openai", timeout=5)` / `get_provider("openai")`, and in `test_model_emits_tool_calls` replace
```python
    msg = resp["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    assert calls, f"{model} returned no tool_calls: {msg.get('content')!r:.200}"
    assert calls[0]["function"]["name"] == "list_dir"
```
with
```python
    calls = resp.tool_calls
    assert calls, f"{model} returned no tool_calls: {resp.text!r:.200}"
    assert calls[0].name == "list_dir"
```
(the `chat(...)` call itself already passes `tools=`/`max_tokens=`/`temperature=` as keywords, so it needs no change).

`tests/test_docker_live.py`: its single fake client is installed with `monkeypatch.setattr(m, "LMStudioClient", lambda base_url=None: client)` — replace that line with `patch_provider(monkeypatch, m, lambda base_url=None: client)` (importing `patch_provider` from `.provider_doubles`) and convert the fake itself with the same four mechanical edits as Step 9b.

Verify: `python3 -m pytest tests/test_docker_live.py -q -m docker` (requires Docker; skip if unavailable) and `python3 -m pytest tests/test_live.py -q -m live` (requires LM Studio).

- [ ] **Step 11: Run the CLI-facing tests**

Run: `python3 -m pytest tests/test_main.py tests/test_runner.py tests/test_resume.py -q`
Expected: all pass.

- [ ] **Step 12: Run the full suite**

Run: `python3 -m pytest -q`
Expected: 660 passed (667 after Task 5, −13 moved out of `test_runner.py`, +6 new: 1 runner, 1 providers, 5 CLI... adjust the number to what the suite actually reports and record it here — it must never be lower than 585 + everything added since).

- [ ] **Step 13: Commit**

```bash
git add dirtywork/runner.py dirtywork/__main__.py tests/test_runner.py tests/test_main.py \
        tests/provider_doubles.py tests/test_providers.py tests/test_live.py tests/test_docker_live.py
git commit -m "feat: runner drives a provider-neutral history; add --provider and provider-aware context windows"
```

---

### Task 7: `AnthropicClient` + fixtures passing the contract suite

Written **after** the OpenAI adapter passes the contract suite, so the neutral history is shaped by two real wire formats rather than one.

**Files:**
- Create: `tests/fixtures/providers/anthropic/*.json` (8 files)
- Create: `tests/test_provider_anthropic.py`
- Create: `dirtywork/providers/anthropic.py`
- Modify: `tests/test_providers.py` (two `get_provider` end-to-end tests)

**Interfaces:**
- Consumes: `dirtywork.llm.{LLMError, MalformedResponse, http_json}`; `dirtywork.providers.{ChatResponse, ToolCall}`; `os.environ["ANTHROPIC_API_KEY"]` (read host-side in the constructor; never passed into a sandbox).
- Produces: `AnthropicClient(base_url="https://api.anthropic.com", timeout=600, *, http_json=http_json, api_key=None)` implementing `Provider` with `name = "anthropic"`.

**Contract details this adapter must honour (set by Tasks 4–6):**
- A block it cannot address (no `id`/`name`) becomes `ToolCall(id="", …)` — the runner counts it as a `malformed_entry`.
- A block with an id but no usable `input` becomes `ToolCall(id=<id>, arguments=None, error=…)` — a `malformed_args` strike the runner answers with an error `tool_result`.
- `raw_arguments` is `json.dumps(input)` so the transcript's `tool_result.args` stays a JSON string across providers.
- A body it cannot read at all raises `MalformedResponse`, not `LLMError`.

- [ ] **Step 1: Create the eight Anthropic wire-shape fixtures**

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

`tests/fixtures/providers/anthropic/malformed_tool_call.json` (the second block is missing both `id` and `name` — Anthropic does not emit this in practice, but the contract suite needs the malformed path exercised uniformly across adapters):

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

`tests/fixtures/providers/anthropic/bad_json_arguments.json` (Anthropic's `input` is already structured JSON, so there is no string-decode failure mode; this models a `max_tokens` cutoff that truncated the block before `input` was emitted):

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

- [ ] **Step 2: Write `tests/test_provider_anthropic.py`**

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dirtywork.llm import LLMError, MalformedResponse
from dirtywork.providers.anthropic import AnthropicClient

from .provider_contract import ProviderContract, RecordingTransport

FIXTURES = Path(__file__).parent / "fixtures" / "providers" / "anthropic"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


def _client(transport, api_key="sk-ant-test"):
    return AnthropicClient(base_url="http://fake", http_json=transport, api_key=api_key)


class TestAnthropicProviderContract(ProviderContract):
    fixtures_dir = FIXTURES

    def make_client(self, transport):
        return _client(transport)

    def _system_text(self, payload):
        return payload.get("system")

    def _tool_result_entries(self, payload):
        for m in reversed(payload["messages"]):
            if m["role"] == "user" and isinstance(m["content"], list):
                blocks = [b for b in m["content"] if b.get("type") == "tool_result"]
                if blocks:
                    return [(b["tool_use_id"], b["content"]) for b in blocks]
        return []


def test_provider_name_is_anthropic():
    assert _client(RecordingTransport([])).name == "anthropic"


def test_missing_api_key_raises_llmerror_on_list_models(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        _client(RecordingTransport([]), api_key=None).list_models()


def test_missing_api_key_raises_llmerror_on_chat(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        _client(RecordingTransport([]), api_key=None).chat(
            "claude-x", [{"role": "user", "content": "hi"}], [],
            temperature=None, max_tokens=100, timeout=30)


def test_api_key_read_from_env_when_not_passed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    client = AnthropicClient(base_url="http://fake", http_json=RecordingTransport([]))
    assert client.api_key == "sk-ant-from-env"


def test_chat_sends_required_headers_and_url():
    transport = RecordingTransport([_fixture("simple_ok.json")])
    _client(transport).chat("claude-x", [{"role": "user", "content": "hi"}], [],
                            temperature=None, max_tokens=100, timeout=30)
    headers = transport.calls[0]["headers"]
    assert headers["x-api-key"] == "sk-ant-test"
    assert headers["anthropic-version"] == "2023-06-01"
    assert headers["content-type"] == "application/json"
    assert transport.calls[0]["url"] == "http://fake/v1/messages"


def test_tools_converted_to_input_schema_shape():
    transport = RecordingTransport([_fixture("simple_ok.json")])
    openai_tools = [{"type": "function", "function": {
        "name": "read_file", "description": "Read a file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}}]
    _client(transport).chat("claude-x", [{"role": "user", "content": "hi"}], openai_tools,
                            temperature=None, max_tokens=100, timeout=30)
    assert transport.calls[0]["payload"]["tools"] == [{
        "name": "read_file", "description": "Read a file.",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                         "required": ["path"]}}]


def test_consecutive_tool_results_merge_into_one_user_turn():
    from dirtywork.providers import ToolCall, assistant_message, tool_message
    transport = RecordingTransport([_fixture("simple_ok.json")])
    calls = [ToolCall(id="c1", name="list_dir", arguments={}, error=None),
             ToolCall(id="c2", name="list_dir", arguments={}, error=None)]
    history = [{"role": "user", "content": "go"}, assistant_message(None, calls),
               tool_message("c1", "a"), tool_message("c2", "b")]
    _client(transport).chat("claude-x", history, [], temperature=None,
                            max_tokens=10, timeout=5)
    messages = transport.calls[0]["payload"]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert len(messages[-1]["content"]) == 2


def test_tool_use_blocks_carry_raw_arguments_as_json():
    resp = _client(RecordingTransport([_fixture("parallel_tool_calls.json")])).chat(
        "claude-x", [{"role": "user", "content": "hi"}], [],
        temperature=None, max_tokens=10, timeout=5)
    assert resp.tool_calls[0].raw_arguments == '{"path": "a.txt"}'


def test_unreadable_body_raises_malformed_response():
    with pytest.raises(MalformedResponse):
        _client(RecordingTransport([{"content": "not a list"}])).chat(
            "claude-x", [{"role": "user", "content": "hi"}], [],
            temperature=None, max_tokens=10, timeout=5)


def test_unknown_stop_reason_passes_through():
    body = {"content": [{"type": "text", "text": "x"}], "stop_reason": "brand_new_reason"}
    resp = _client(RecordingTransport([body])).chat(
        "claude-x", [{"role": "user", "content": "hi"}], [],
        temperature=None, max_tokens=10, timeout=5)
    assert resp.finish_reason == "brand_new_reason"


def test_list_models_returns_ids():
    transport = RecordingTransport([{"data": [{"id": "claude-opus-5"}, {"id": "claude-sonnet-5"}]}])
    client = _client(transport)
    assert client.list_models() == ["claude-opus-5", "claude-sonnet-5"]
    assert transport.calls[0]["method"] == "GET"


def test_context_window_claude_prefix_and_unknown():
    client = _client(RecordingTransport([]))
    assert client.context_window("claude-opus-5") == 200000
    assert client.context_window("nonexistent/model") is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_provider_anthropic.py -q`
Expected: `ModuleNotFoundError: No module named 'dirtywork.providers.anthropic'`

- [ ] **Step 4: Write `dirtywork/providers/anthropic.py`**

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_provider_anthropic.py -q`
Expected: 19 passed (7 inherited from `ProviderContract` + 12 explicit)

- [ ] **Step 6: Confirm `get_provider` works end to end**

Append to `tests/test_providers.py`:

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


def test_get_provider_defaults_base_url_per_provider(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert get_provider("anthropic").base_url == DEFAULT_BASE_URLS["anthropic"]


def test_every_provider_name_is_constructible(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    for name in PROVIDER_NAMES:
        assert get_provider(name).name == name
```

Run: `python3 -m pytest tests/test_providers.py -q`
Expected: 14 passed

- [ ] **Step 7: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all green, 23 more tests than after Task 6.

- [ ] **Step 8: Commit**

```bash
git add dirtywork/providers/anthropic.py tests/fixtures/providers/anthropic \
        tests/test_provider_anthropic.py tests/test_providers.py
git commit -m "feat: add AnthropicClient passing the provider contract suite"
```

---

### Task 8: `docs/transcript-schema.md` + shipped-shape regression tests

Spec §3. This task documents **what ships on this branch** — not a design. The regression tests derive their expectations from a real run and from the code, so the doc cannot silently rot: if a new transcript event, status, `run.json` key or stdout key appears and is not documented, the suite fails.

**Files:**
- Create: `docs/transcript-schema.md`
- Create: `tests/test_transcript_schema.py`
- Modify: `README.md` (Machine contract — pointer to the new doc)

**Interfaces:**
- Consumes: `dirtywork.runner.{Runner, FINISH_TOOL}`; `dirtywork.builtin_tools.default_registry`; `dirtywork.transcript.Transcript`; `dirtywork.__main__` (for an end-to-end host-mode run); `tests.provider_doubles`.
- Produces: `docs/transcript-schema.md` — v1/v2 tables covering every event, every field, every status, and the `run.json`/stdout field lists.

**The complete inventory the doc must cover** (verified against the code on this branch — re-derive it with the greps in Step 1 rather than trusting this list):
- Transcript events: `run_start` (`dirtywork/runner.py`), `assistant`, `tool_result`, `nudge` (runner), `guardrail_block` (`dirtywork/toolspec.py` after Task 2 — it was `dirtywork/tools.py` before), `sandbox_reset` (`dirtywork/sandbox/docker.py`), `run_end` (runner **and** `dirtywork/__main__.py`'s failure paths, which write `status` + `error`).
- `nudge` kinds: `truncated`, `empty`, `text_tool_call` (reply classification) and `stall` (progress tracker).
- Statuses: `completed`, `max_turns`, `timeout`, `context_exhausted`, `model_error`, `interrupted`, `stalled`, `budget_exceeded`, `sandbox_error`, `export_failed`.
- `run.json` keys: `schema_version`, `status`, `slug`, `repo`, `worktree`, `branch`, `base_commit`, `task`, `model`, `provider`, `context_window`, `resumed_from`, `resumed_by`, `container`, `volume`, `image`, `image_digest`, `image_pinned`, `host_pid`, `started`, `sandbox`, `ended`, `turns`, `diff_stat`, `export_status`, `patch_path`, `finalize_error`, `watchdog_violation`, `watchdog_violation_kind`.
- stdout JSON keys: `schema_version`, `status`, `worktree`, `branch`, `transcript`, `turns`, `usage`, `final_message`, `run_dir`, `provider`, `base_commit`, `resumed_from`, `finalize_error`, `watchdog_violation`, `watchdog_violation_kind`, and `export_status` on the exception-recovery path.

- [ ] **Step 1: Re-derive the inventory from the code**

Run these and reconcile the output with the list above before writing anything. If they disagree, the code wins.

```bash
grep -rn 'transcript.write("' dirtywork/ | sed 's/.*write("/event: /; s/".*//' | sort -u
grep -rn 'self.transcript.write("nudge", kind=' dirtywork/runner.py
grep -n 'NUDGES = {' -A 12 dirtywork/runner.py
grep -n 'return finish("' dirtywork/runner.py | sed 's/.*finish("/status: /; s/".*//' | sort -u
grep -n 'status=' dirtywork/__main__.py | grep -o '"[a-z_]*"' | sort -u
python3 - <<'PY'
import ast, pathlib
src = pathlib.Path("dirtywork/__main__.py").read_text()
tree = ast.parse(src)
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "write_run_json":
        for arg in node.args:
            if isinstance(arg, ast.Dict):
                print("run.json start keys:", [k.value for k in arg.keys])
PY
```

- [ ] **Step 2: Write the failing tests**

`tests/test_transcript_schema.py`:

```python
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from dirtywork.builtin_tools import default_registry
from dirtywork.runner import Runner
from dirtywork.transcript import Transcript

from .provider_doubles import DictProvider, patch_provider, text_body, tool_call_body

DOC = Path(__file__).parent.parent / "docs" / "transcript-schema.md"

EVENT_NAMES = ["run_start", "assistant", "tool_result", "guardrail_block", "nudge",
               "sandbox_reset", "run_end"]
NUDGE_KINDS = ["truncated", "empty", "text_tool_call", "stall"]
STATUSES = ["completed", "max_turns", "timeout", "context_exhausted", "model_error",
            "interrupted", "stalled", "budget_exceeded", "sandbox_error", "export_failed"]
RUN_END_FIELDS = ["diff_stat", "untracked", "patch_path", "escaping_symlinks",
                  "dropped_git_entries", "worktree_bytes", "worktree_files",
                  "export_status", "watchdog_violation", "watchdog_violation_kind",
                  "finalize_error"]


def _doc_tokens():
    return set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", DOC.read_text(encoding="utf-8")))


def test_doc_exists_and_documents_every_event_name():
    assert DOC.exists(), f"{DOC} does not exist"
    tokens = _doc_tokens()
    for name in EVENT_NAMES:
        assert name in tokens, f"event '{name}' is not documented in {DOC.name}"


def test_doc_documents_schema_version_v1_v2_statuses_and_nudge_kinds():
    text = DOC.read_text(encoding="utf-8")
    tokens = _doc_tokens()
    assert "schema_version" in tokens
    assert "v1" in text and "v2" in text
    for status in STATUSES:
        assert status in tokens, f"status '{status}' is not documented"
    for kind in NUDGE_KINDS:
        assert kind in tokens, f"nudge kind '{kind}' is not documented"
    for field in RUN_END_FIELDS:
        assert field in tokens, f"run_end field '{field}' is not documented"


def test_doc_documents_the_finish_tool_and_the_seven_tools():
    tokens = _doc_tokens()
    for name in ("read_file", "write_file", "edit_file", "list_dir", "grep", "bash", "finish"):
        assert name in tokens, f"tool '{name}' is not documented"


class _NudgingProvider(DictProvider):
    """Turn 1 calls a tool, turn 2 replies with nothing (→ `empty` nudge),
    turn 3 answers."""

    def reply(self, model, history, tools):
        if self.calls == 1:
            return tool_call_body("read_file", {"path": "f.txt"})
        if self.calls == 2:
            return text_body("")
        return text_body("done")


def test_a_real_run_emits_the_documented_events(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "f.txt").write_text("data\n")
    from dirtywork.sandbox.host import HostSandbox
    transcript = Transcript(tmp_path / "t.jsonl")
    registry = default_registry(transcript=transcript)
    r = Runner(_NudgingProvider(), registry, HostSandbox(wt), transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    events = [json.loads(l) for l in (tmp_path / "t.jsonl").read_text().splitlines()]
    kinds = [e["event"] for e in events]
    assert kinds[0] == "run_start" and kinds[-1] == "run_end"
    assert set(kinds) == {"run_start", "assistant", "tool_result", "nudge", "run_end"}
    run_start = events[0]
    assert run_start["schema_version"] == 2
    assert run_start["context_window"]
    nudge = next(e for e in events if e["event"] == "nudge")
    assert nudge["kind"] in NUDGE_KINDS and isinstance(nudge["turn"], int)
    assert result.status == "completed"
    # every field emitted by a real run must be documented
    documented = _doc_tokens()
    for e in events:
        for key in e:
            if key in ("ts", "event"):
                continue
            assert key in documented, f"{e['event']}.{key} is not documented in {DOC.name}"


class _FinishingProvider(DictProvider):
    def reply(self, model, history, tools):
        return tool_call_body("finish", {"summary": "all done"}, call_id="f1")


def test_finish_appears_as_an_ordinary_tool_call(tmp_path):
    from dirtywork.sandbox.host import HostSandbox
    wt = tmp_path / "wt"
    wt.mkdir()
    transcript = Transcript(tmp_path / "t.jsonl")
    r = Runner(_FinishingProvider(), default_registry(transcript=transcript),
               HostSandbox(wt), transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    events = [json.loads(l) for l in (tmp_path / "t.jsonl").read_text().splitlines()]
    assistant = next(e for e in events if e["event"] == "assistant")
    assert assistant["tool_calls"][0]["name"] == "finish"
    tool_result = next(e for e in events if e["event"] == "tool_result")
    assert tool_result["result"] == "run finished"
    assert result.final_message == "all done"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "--allow-empty", "-m", "i"], capture_output=True)
    return repo


class _DoneProvider(DictProvider):
    def reply(self, model, history, tools):
        return text_body("done")


def test_stdout_and_run_json_fields_are_all_documented(tmp_path, monkeypatch, capsys):
    import dirtywork.__main__ as m
    repo = _repo(tmp_path)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    patch_provider(monkeypatch, m, lambda base_url=None: _DoneProvider(base_url))

    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "some task"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 2
    assert payload["provider"] == "openai"
    assert "run_dir" in payload
    run_json = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert run_json["provider"] == "openai"

    documented = _doc_tokens()
    for key in payload:
        assert key in documented, f"stdout JSON key '{key}' is not documented in {DOC.name}"
    for key in run_json:
        assert key in documented, f"run.json key '{key}' is not documented in {DOC.name}"


def test_stdout_contract_fields_never_disappear(tmp_path, monkeypatch, capsys):
    import dirtywork.__main__ as m
    repo = _repo(tmp_path)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    patch_provider(monkeypatch, m, lambda base_url=None: _DoneProvider(base_url))
    assert m.main(["run", "--repo", str(repo), "--sandbox", "none", "some task"]) == 0
    payload = json.loads(capsys.readouterr().out)
    for key in ("status", "worktree", "branch", "transcript", "turns", "usage",
                "final_message"):
        assert key in payload
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_transcript_schema.py -q`
Expected: the three doc tests and the two "all documented" assertions fail (`docs/transcript-schema.md` does not exist); the behavioural checks pass.

- [ ] **Step 4: Write `docs/transcript-schema.md`**

````markdown
# Transcript schema

`dirtywork` writes one JSON object per line to
`~/.dirtywork/runs/<slug>/transcript.jsonl` (`tail -f` friendly — each line is
flushed immediately). Every line has at least `ts` (UTC ISO-8601) and `event`
(one of the seven event names below). `schema_version` marks the overall
version and appears once, on `run_start`, and again in the CLI's stdout JSON
and in `run.json` — not on every line.

**v1** is the pre-hardening shape (dirtywork ≤ 0.2.0, host-only execution, no
`schema_version` field at all — its absence *is* the v1 marker). **v2** (0.3.0
and later, including the 0.4.x Docker sandbox and the 0.5.x harness-robustness
releases) adds Docker-sandbox provenance, provider identity, resume lineage,
four new terminal statuses, the `nudge` and `sandbox_reset` events, and richer
`run_end` fields from the export validator. A v1 reader that ignores unknown
fields keeps working unmodified against v2 output: every v2 addition is a new
field, a new event, or a new enum value — never a removed or renamed one. That
is the same compatibility rule the stdout JSON contract follows, for the same
reason.

## Events

### `run_start`

One per run, always the first line.

| Field | v1 | v2 | Type | Notes |
|---|---|---|---|---|
| `ts` | ✓ | ✓ | string | UTC ISO-8601 |
| `event` | ✓ | ✓ | `"run_start"` | |
| `task` | ✓ | ✓ | string | the task text; on a resumed run it also carries the `--- RESUMED RUN ---` block |
| `model` | ✓ | ✓ | string | |
| `max_turns` | ✓ | ✓ | integer | |
| `timeout` | ✓ | ✓ | integer | seconds, whole-run wall clock |
| `repo` | ✓ | ✓ | string | absolute path |
| `worktree` | ✓ | ✓ | string | absolute path |
| `schema_version` | | ✓ | `2` | present from v2 onward; its absence marks v1 |
| `context_window` | | ✓ | integer | tokens; the resolved value (`--context-window` > `DIRTYWORK_CONTEXT_WINDOW` > the provider's table > 32768) |
| `base_commit` | | ✓ | string | resolved commit the worktree branched from |
| `branch` | | ✓ | string | `dirtywork/<slug>` |
| `branch_from` | | ✓ | string \| null | `--branch-from` as given, or null for repo HEAD |
| `base_url` | | ✓ | string | the provider endpoint actually used (after the per-provider default is applied) |
| `dirtywork_version` | | ✓ | string | `dirtywork.__version__` |
| `temperature` | | ✓ | number \| null | omitted from the request when null |
| `provider` | | ✓ | `"openai"` \| `"anthropic"` | |
| `resumed_from` | | ✓ | string \| null | slug of the run this one continues |
| `sandbox` | | ✓ | `"none"` \| object | `"none"` in host mode; in Docker mode `{backend, image, image_digest, image_pinned, network, memory, cpus, pids_limit, tmp_size, gitdir_size, max_worktree_mb, max_worktree_files, user}` |

### `assistant`

One per model turn that produced a reply, with or without tool calls.

| Field | v1 | v2 | Type | Notes |
|---|---|---|---|---|
| `text` | ✓ | ✓ | string | the reply text; capped at `MAX_ASSISTANT_TEXT_CHARS` (64 000) **in the transcript only** — the full text is still sent to the model, and the cap is marked inline |
| `tool_calls` | ✓ | ✓ | list | `[{name, arguments}, …]` — `arguments` is the model's own raw JSON argument string, capped at 2000 chars. Structurally invalid entries the provider could not address are **not** listed here; they appear as `tool_result` records with an empty `tool` |

### `tool_result`

One per tool call executed, plus one per malformed tool-call entry discarded.

| Field | v1 | v2 | Type | Notes |
|---|---|---|---|---|
| `tool` | ✓ | ✓ | string | tool name — one of `read_file`, `write_file`, `edit_file`, `list_dir`, `grep`, `bash`, `finish`; `""` for a discarded malformed entry |
| `args` | ✓ | ✓ | string | the raw JSON argument string, capped at 500 chars; `""` for a discarded malformed entry |
| `result` | ✓ | ✓ | string | the tool's result, trimmed per the tool's `Caps.transcript` setting. All seven built-in tools declare `preview`, which caps the record at 2000 chars; the registry also supports `full` and `none`, unused by any shipped tool |

A `finish(summary=…)` call is an ordinary tool call: it appears in the
`assistant` event's `tool_calls` and produces a `tool_result` whose `result` is
`run finished`. The summary becomes the run's `final_message` and the run ends
`completed`.

### `nudge`

**v2 only.** One per turn in which the harness injected corrective guidance
into the next user message. Several nudges in one turn are merged into a
single user message (the chat history must never carry two consecutive user
messages), but each is recorded here separately.

| Field | v1 | v2 | Type | Notes |
|---|---|---|---|---|
| `kind` | | ✓ | string | `truncated` (the reply hit the token limit), `empty` (no tool call and no answer), `text_tool_call` (a tool call written as prose instead of through the tools API), `stall` (no progress for `--stall-turns // 2` turns) |
| `turn` | | ✓ | integer | 1-based turn number the nudge was issued on |

### `guardrail_block`

One per `BLOCKED:`-prefixed tool result (a bash denylist hit, for example).
Written by the tool registry itself (`ToolRegistry(transcript=…)`), not by the
runner — this moved from `ToolExecutor` in sub-project 3.

| Field | v1 | v2 | Type | Notes |
|---|---|---|---|---|
| `tool` | ✓ | ✓ | string | |
| `args` | ✓ | ✓ | object | the validated argument dict actually passed to the tool (unknown keys already dropped, `timeout` already clamped) |
| `reason` | ✓ | ✓ | string | the full `BLOCKED: …` text |

### `sandbox_reset`

**v2 only**, Docker sandbox mode. Emitted when the container is reset (a stuck
`docker exec`, a stray background process, an out-of-memory kill).

| Field | v1 | v2 | Type | Notes |
|---|---|---|---|---|
| `reason` | | ✓ | string | why the reset happened |

### `run_end`

One per run, always the last line. Written by the runner on every terminal
status, and by the CLI's failure paths when the runner never returned (in that
case it carries `status` and `error` only).

| Field | v1 | v2 | Type | Notes |
|---|---|---|---|---|
| `status` | ✓ | ✓ | string | see the status table below |
| `turns` | ✓ | ✓ | integer | |
| `duration_s` | ✓ | ✓ | number | wall-clock seconds, one decimal |
| `usage` | ✓ | ✓ | object | `{prompt_tokens, completion_tokens}`, cumulative across turns, sanitized (finite, non-negative) |
| `error` | | ✓ | string | only on the CLI failure paths, where `turns`/`duration_s`/`usage` are absent |
| `diff_stat` | | ✓ | string | capped `git diff --stat` against the base commit. Host mode: tracked changes only. Docker mode: already includes new files, since the export stages everything first |
| `untracked` | | ✓ | string | host mode: `git status --porcelain` `??` entries, capped at 64 000 chars. Docker mode: always `""` |
| `patch_path` | | ✓ | string \| null | path to `diff.patch` (Docker mode's container-computed patch) |
| `worktree_bytes` | | ✓ | integer \| null | sampled worktree size from `budget.measure_worktree` |
| `worktree_files` | | ✓ | integer \| null | sampled worktree entry count |
| `escaping_symlinks` | | ✓ | list | symlinks whose target is absolute or escapes the worktree — never followed, always reported |
| `dropped_git_entries` | | ✓ | list | Docker mode: `.git`-named entries the export refused to add |
| `export_status` | | ✓ | string | `"ok"`, `"export_failed: <reason>"`, or `"n/a"` (host mode never exports) |
| `watchdog_violation` | | ✓ | string \| null | Docker mode: the reason the watchdog killed the container, when that happened after the last tool call returned |
| `watchdog_violation_kind` | | ✓ | string \| null | `"budget"` (worktree-size or host-disk-floor breach) or `"sandbox_error"` (the watchdog's own sampling exec failed twice); meaningful only alongside `watchdog_violation` |
| `finalize_error` | | ✓ | string \| null | set when the finalize/export step itself raised after the agent loop finished; the run's own status is unaffected except that `completed` becomes `export_failed` |

## Statuses

| Status | v1 | v2 | Meaning |
|---|---|---|---|
| `completed` | ✓ | ✓ | the model called `finish(summary=…)` or replied with a plain answer |
| `max_turns` | ✓ | ✓ | `--max-turns` reached |
| `timeout` | ✓ | ✓ | `--timeout` wall clock reached |
| `context_exhausted` | ✓ | ✓ | history could not be trimmed under the char budget |
| `model_error` | ✓ | ✓ | repeated malformed replies/tool calls, an unreadable response body, or any exception the CLI caught |
| `interrupted` | ✓ | ✓ | Ctrl-C during the loop |
| `stalled` | | ✓ | `--stall-turns` consecutive turns with no progress (no new tool call, no successful write, no new command output) |
| `budget_exceeded` | | ✓ | worktree size/file budget or host disk floor breached |
| `sandbox_error` | | ✓ | the sandbox backend failed in a way the run cannot continue past |
| `export_failed` | | ✓ | the run itself completed, but the validated export of the worker's files did not |

## `schema_version` and the stdout JSON contract

`schema_version: 2` also appears in the CLI's single stdout JSON object
(`dirtywork run`'s machine contract — see the README's "Machine contract"
section). Its fields: `schema_version`, `status`, `worktree`, `branch`,
`transcript`, `turns`, `usage`, `final_message`, `run_dir`, `provider`,
`base_commit`, `resumed_from`, `finalize_error`, `watchdog_violation`,
`watchdog_violation_kind`, and `export_status` on the exception-recovery path.
Per this project's compatibility rule the stdout JSON may only gain fields,
never lose or rename `status`, `worktree`, `branch`, `transcript`, `turns`,
`usage`, `final_message`.

## `run.json`

Separate from the transcript: `~/.dirtywork/runs/<slug>/run.json` is a single
JSON object (not JSONL), written at run start and merge-updated at run end.

| Field | Written | Notes |
|---|---|---|
| `schema_version` | start | `2` |
| `status` | start, end | `"running"` at start, then the terminal status |
| `slug` | start | run slug; the run directory's name |
| `repo` | start | absolute path |
| `worktree` | start | absolute path |
| `branch` | start | `dirtywork/<slug>` |
| `base_commit` | start | |
| `task` | start | |
| `model` | start | |
| `provider` | start | `"openai"` \| `"anthropic"` |
| `context_window` | start | resolved tokens |
| `resumed_from` | start | slug of the run this one continues, or null |
| `resumed_by` | — | written onto the **prior** run's `run.json` when a resume starts |
| `container` | start | Docker mode container name, else null |
| `volume` | start | Docker mode volume name, else null |
| `image` | start | `--image` as given (Docker mode), else null |
| `image_digest` | start | registry digest from `RepoDigests`, or null for a locally built image — provenance only |
| `image_pinned` | start | true only when the maintained default image was pinned and enforced |
| `host_pid` | start | the CLI's pid; `resume` uses it to refuse a run still in progress |
| `started` | start | UTC ISO-8601 |
| `sandbox` | start | `"docker"` \| `"none"` |
| `ended` | end | UTC ISO-8601 |
| `turns` | end | |
| `diff_stat` | end | |
| `export_status` | end | `"ok"` \| `"export_failed: …"` \| `"n/a"` |
| `patch_path` | end | |
| `finalize_error` | end | |
| `watchdog_violation` | end | |
| `watchdog_violation_kind` | end | |

`dirtywork runs show <slug>` prints this file alongside a tool-call timeline
reconstructed from the transcript.
````

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_transcript_schema.py -q`
Expected: 7 passed. A failure naming an undocumented key means the doc is missing a row — add it; never weaken the assertion.

- [ ] **Step 6: Point the README at the new doc**

```bash
python3 - <<'PY'
from pathlib import Path
p = Path("README.md")
text = p.read_text(encoding="utf-8")
anchor = "**Transcript events** (JSONL, one per line):"
assert anchor in text, "the 'Transcript events' paragraph moved -- add the pointer by hand"
pointer = ("Full field-by-field schema, including every v1→v2 addition and the\n"
           "`run.json` field list: [`docs/transcript-schema.md`](docs/transcript-schema.md).\n\n")
text = text.replace(anchor, pointer + anchor, 1)
p.write_text(text, encoding="utf-8")
print("patched README.md")
PY
```

- [ ] **Step 7: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all green, 7 more tests than after Task 7.

- [ ] **Step 8: Commit**

```bash
git add docs/transcript-schema.md tests/test_transcript_schema.py README.md
git commit -m "docs: document the v1/v2 transcript, run.json and stdout schema as shipped"
```

---

### Task 9: `runs list` + `runs show [--diff]`

**Files:**
- Create: `dirtywork/runs.py`
- Create: `tests/test_runs.py`
- Modify: `dirtywork/__main__.py` (add `_add_runs_parsers(sub)`, call it from `_parse_args`, route `runs` in `main()`)
- Modify: `tests/test_main.py` (two end-to-end dispatch tests)

**Interfaces:**
- Consumes: `dirtywork.rundir.{RUNS_DIR, read_run_json}`; `dirtywork.sandbox.docker_cli.{run, T_QUERY}` (returns a `dirtywork.procs.Captured` with `.returncode` and merged-bytes `.output`); the shipped `run.json` keys (`schema_version`, `status`, `slug`, `repo`, `worktree`, `branch`, `base_commit`, `task`, `model`, `provider`, `context_window`, `turns`, `resumed_from`, `resumed_by`, `container`, `volume`, `image`, `image_digest`, `image_pinned`, `host_pid`, `started`, `ended`, `sandbox`, `diff_stat`, `export_status`, `patch_path`, `finalize_error`, `watchdog_violation`, `watchdog_violation_kind`) written by `__main__._write_run_json_start` / `__main__._update_run_json`.
- Produces: `dirtywork.runs.{RunsError, format_table, cmd_list, cmd_show, dispatch}`; `dirtywork.__main__._add_runs_parsers(sub)`; CLI: `dirtywork runs list [--json]`, `dirtywork runs show <slug> [--diff]`.

Notes that shape this task (all verified against the code on this branch):

- `main()` already dispatches subcommands (`run`, `resume`) through `_parse_args` / `_execute`; there is **no** inline body left in `main()` to extract. This task adds one parser-builder function and one dispatch branch — nothing is refactored.
- `run.json`'s end-of-run timestamp key is **`ended`** (written by `_update_run_json`), not `ended_at`.
- `usage` and `final_message` live in the **stdout JSON only**; they are not in `run.json`. `runs show` therefore reads what is actually recorded.
- Statuses include `stalled` (SP2.5). A resumed run records `resumed_from`; the run it continued gets `resumed_by` written back into *its* `run.json`.
- `provider` is written to `run.json` by Task 6; `runs show` prints `-` for older runs that predate it.

- [ ] **Step 1: Write the failing tests**

`tests/test_runs.py`:

```python
from __future__ import annotations

import argparse
import json
import os
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


def _write_run(runs_dir: Path, slug: str, data: dict) -> Path:
    run_dir = runs_dir / slug
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps(data))
    return run_dir


class _FakeCaptured:
    """Stand-in for dirtywork.procs.Captured: only returncode/output are read."""

    def __init__(self, returncode, output=b""):
        self.returncode = returncode
        self.output = output


def test_cmd_list_prints_table_with_status_and_started(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "fix-bug-0101", {
        "status": "stalled", "started": "2026-08-16T00:00:00+00:00",
        "branch": "dirtywork/fix-bug-0101", "repo": str(repo),
        "worktree": str(repo / ".worktrees" / "dw-fix-bug-0101"),
        "container": None, "volume": None,
    })
    rc = runs.cmd_list(argparse.Namespace(json=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "SLUG" in out and "STATUS" in out and "RESUMED" in out
    assert "fix-bug-0101" in out
    assert "stalled" in out           # SP2.5 status must render like any other


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


def test_cmd_list_marks_resumed_runs(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "aaa-first", {
        "status": "max_turns", "started": "t", "branch": "dirtywork/aaa-first",
        "repo": str(repo), "worktree": str(repo), "container": None, "volume": None,
        "resumed_from": None, "resumed_by": "bbb-second",
    })
    _write_run(tmp_path / "runs", "bbb-second", {
        "status": "completed", "started": "t", "branch": "dirtywork/aaa-first",
        "repo": str(repo), "worktree": str(repo), "container": None, "volume": None,
        "resumed_from": "aaa-first", "resumed_by": None,
    })
    assert runs.cmd_list(argparse.Namespace(json=False)) == 0
    table = capsys.readouterr().out
    assert "by bbb-second" in table
    assert "from aaa-first" in table

    assert runs.cmd_list(argparse.Namespace(json=True)) == 0
    payload = {row["slug"]: row for row in json.loads(capsys.readouterr().out)}
    assert payload["aaa-first"]["resumed_by"] == "bbb-second"
    assert payload["bbb-second"]["resumed_from"] == "aaa-first"
    assert payload["bbb-second"]["resumed"] == "from aaa-first"


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


def test_cmd_list_docker_state_columns(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")

    def fake_run(argv, timeout=None):
        if argv[:2] == ["ps", "-a"]:
            return _FakeCaptured(0, b"dw-slug1\texited\n")
        if argv[:2] == ["volume", "ls"]:
            return _FakeCaptured(0, b"dw-slug1-work\n")
        return _FakeCaptured(1)

    monkeypatch.setattr(runs.docker_cli, "run", fake_run)
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "started": "t", "branch": "b", "repo": str(repo),
        "worktree": str(repo), "container": "dw-slug1", "volume": "dw-slug1-work",
    })
    assert runs.cmd_list(argparse.Namespace(json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["container"] == "exited"
    assert payload[0]["volume"] == "present"


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
    assert payload[0]["container"] == "-"     # best effort, never fatal
    assert payload[0]["volume"] == "absent"


def test_cmd_list_unreadable_run_json_is_a_row_not_a_crash(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = (tmp_path / "runs" / "broken")
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text("{not json")
    rc = runs.cmd_list(argparse.Namespace(json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["slug"] == "broken"
    assert payload[0]["status"] == "?"
    assert "error" in payload[0]


def test_cmd_show_prints_summary_run_json_and_timeline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _write_run(tmp_path / "runs", "slug1", {
        "status": "stalled", "slug": "slug1", "task": "fix the bug",
        "model": "qwen/qwen3-coder-next", "provider": "openai", "turns": 12,
        "resumed_from": "older-run", "resumed_by": None, "sandbox": "docker",
    })
    (run_dir / "transcript.jsonl").write_text(
        json.dumps({"ts": "t1", "event": "run_start", "model": "qwen/qwen3-coder-next"}) + "\n"
        + json.dumps({"ts": "t2", "event": "tool_result", "tool": "bash",
                      "args": "{\"command\": \"ls\"}", "result": "exit code: 0"}) + "\n"
        + json.dumps({"ts": "t3", "event": "nudge", "kind": "stall", "turn": 6}) + "\n"
        + json.dumps({"ts": "t4", "event": "run_end", "status": "stalled", "turns": 12}) + "\n"
    )
    rc = runs.cmd_show(argparse.Namespace(slug="slug1", diff=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "task: fix the bug" in out
    assert "model: qwen/qwen3-coder-next" in out
    assert "provider: openai" in out
    assert "turns: 12" in out
    assert "resumed_from: older-run" in out
    assert "resumed_by: -" in out
    assert '"status": "stalled"' in out          # the full run.json is still printed
    assert "timeline:" in out
    assert "kind=stall" in out                   # nudge events are visible in the timeline
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

Run: `python3 -m pytest tests/test_runs.py -q`
Expected: `ModuleNotFoundError: No module named 'dirtywork.runs'`

- [ ] **Step 3: Write the implementation**

`dirtywork/runs.py`:

```python
"""`dirtywork runs ...` — inspect and clean up finished runs (spec SP3 section 4).

Everything here reads what a run left behind (`~/.dirtywork/runs/<slug>/`:
`run.json`, `transcript.jsonl`, `diff.patch`) plus, best effort, the docker and
git state around it. Nothing in this module ever starts a model run, and no
docker/git failure here is fatal to the command as a whole: a run directory is
the source of truth, the rest is decoration.

RUNS_DIR is read through the `rundir` module (`rundir.RUNS_DIR`) rather than
imported by value, so tests can point it at a tmp_path.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import rundir
from .sandbox import docker_cli

COLUMN_GAP = "  "
LIST_COLUMNS = ("slug", "status", "started", "resumed", "branch", "worktree",
                "container", "volume")
SHOW_FIELDS = ("slug", "status", "sandbox", "task", "model", "provider", "turns",
               "resumed_from", "resumed_by", "branch", "worktree", "started", "ended")
TASK_PREVIEW_CHARS = 200


class RunsError(Exception):
    """A `runs` subcommand refusal that maps to exit 2 (bad slug, unreadable
    run.json, a run this command cannot act on)."""


def format_table(columns, rows) -> str:
    """Fixed-width table: upper-case header, one line per row, every column
    padded to its widest cell. Shared with `dirtywork bench summarize` so both
    CLIs render identically."""
    widths = {c: max([len(c)] + [len(str(r.get(c, ""))) for r in rows]) for c in columns}
    lines = [COLUMN_GAP.join(str(c).upper().ljust(widths[c]) for c in columns).rstrip()]
    for row in rows:
        lines.append(COLUMN_GAP.join(str(row.get(c, "")).ljust(widths[c]) for c in columns).rstrip())
    return "\n".join(lines)


def _iter_run_dirs(runs_dir: Path):
    runs_dir = Path(runs_dir)
    if not runs_dir.is_dir():
        return
    for d in sorted(runs_dir.iterdir()):
        if d.is_dir() and (d / "run.json").exists():
            yield d


def _open_run(slug: str):
    """(run_dir, run.json dict) or RunsError — the one lookup every single-run
    subcommand uses, so 'no such run' reads identically everywhere."""
    run_dir = Path(rundir.RUNS_DIR) / slug
    if not run_dir.is_dir():
        raise RunsError(f"no such run '{slug}' under {rundir.RUNS_DIR}")
    try:
        data = rundir.read_run_json(run_dir)
    except (OSError, ValueError) as e:
        raise RunsError(f"cannot read run.json for '{slug}': {e}")
    if not isinstance(data, dict):
        raise RunsError(f"run.json for '{slug}' is not a JSON object")
    return run_dir, data


def _docker_state():
    """(container_states: dict[name, state], volume_names: set[str]), both
    best effort: any docker failure yields empty results so the command still
    prints every run instead of dying on a missing daemon."""
    containers, volumes = {}, set()
    try:
        cp = docker_cli.run(["ps", "-a", "--format", "{{.Names}}\t{{.State}}",
                             "--filter", "label=dirtywork.run"], timeout=docker_cli.T_QUERY)
        if cp.returncode == 0:
            for line in cp.output.decode("utf-8", errors="replace").splitlines():
                if "\t" in line:
                    name, state = line.split("\t", 1)
                    containers[name.strip()] = state.strip()
    except Exception:
        pass
    try:
        cp = docker_cli.run(["volume", "ls", "--format", "{{.Name}}",
                             "--filter", "label=dirtywork.run"], timeout=docker_cli.T_QUERY)
        if cp.returncode == 0:
            volumes = {ln.strip() for ln in cp.output.decode("utf-8", errors="replace").splitlines()
                       if ln.strip()}
    except Exception:
        pass
    return containers, volumes


def _worktree_present(repo, worktree):
    """True/False if git could be asked, None if it could not."""
    if not repo or not worktree:
        return None
    try:
        cp = subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"],
                            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if cp.returncode != 0:
        return None
    paths = [ln.split(" ", 1)[1] for ln in cp.stdout.splitlines() if ln.startswith("worktree ")]
    try:
        resolved = {str(Path(p).resolve()) for p in paths}
        return str(Path(worktree).resolve()) in resolved
    except OSError:
        return None


def _resumed_mark(data: dict) -> str:
    """How `runs list` marks a run that is part of a resume chain: `from <slug>`
    for a resumed run, `by <slug>` for one that was later resumed, both when a
    run sits in the middle of a chain."""
    marks = []
    if data.get("resumed_from"):
        marks.append(f"from {data['resumed_from']}")
    if data.get("resumed_by"):
        marks.append(f"by {data['resumed_by']}")
    return ", ".join(marks) if marks else "-"


def cmd_list(args) -> int:
    containers, volumes = _docker_state()
    rows = []
    for run_dir in _iter_run_dirs(rundir.RUNS_DIR):
        slug = run_dir.name
        try:
            data = rundir.read_run_json(run_dir)
            if not isinstance(data, dict):
                raise ValueError("run.json is not a JSON object")
        except (OSError, ValueError) as e:
            rows.append({"slug": slug, "status": "?", "started": "?", "resumed": "?",
                         "branch": "?", "worktree": "?", "container": "?", "volume": "?",
                         "error": f"unreadable run.json: {e}"})
            continue
        present = _worktree_present(data.get("repo", ""), data.get("worktree", ""))
        container_name = data.get("container")
        volume_name = data.get("volume")
        rows.append({
            "slug": slug,
            "status": data.get("status", "?"),
            "started": data.get("started", "?"),
            "resumed": _resumed_mark(data),
            "resumed_from": data.get("resumed_from"),
            "resumed_by": data.get("resumed_by"),
            "branch": data.get("branch", "?"),
            "worktree": "?" if present is None else ("yes" if present else "no"),
            "container": containers.get(container_name, "-") if container_name else "-",
            "volume": ("present" if volume_name in volumes else "absent") if volume_name else "-",
        })
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("no runs found")
        return 0
    print(format_table(LIST_COLUMNS, rows))
    return 0


def _summary_value(key: str, data: dict) -> str:
    value = data.get(key)
    if value is None or value == "":
        return "-"
    text = str(value)
    if key == "task" and len(text) > TASK_PREVIEW_CHARS:
        text = text[:TASK_PREVIEW_CHARS].replace("\n", " ") + " ... (full text below)"
    return text.replace("\n", " ") if key == "task" else text


def _timeline_line(event: dict) -> str:
    ts = event.get("ts", "")
    name = str(event.get("event", ""))
    if name == "tool_result":
        result = str(event.get("result", ""))
        outcome = ("ERROR" if result.startswith("ERROR")
                   else "BLOCKED" if result.startswith("BLOCKED") else "ok")
        tool = event.get("tool") or "(malformed call)"
        return f"{ts}  {name:<15} {tool:<12} {str(event.get('args', ''))[:80]:<80} [{outcome}]"
    if name == "assistant":
        tools = ",".join(str(tc.get("name")) for tc in (event.get("tool_calls") or [])
                         if isinstance(tc, dict))
        return f"{ts}  {name:<15} " + (f"tools: {tools}" if tools else "text reply")
    if name == "nudge":
        return f"{ts}  {name:<15} kind={event.get('kind', '')} turn={event.get('turn', '')}"
    if name == "guardrail_block":
        return f"{ts}  {name:<15} {event.get('tool', '')}: {str(event.get('reason', ''))[:120]}"
    if name == "sandbox_reset":
        return f"{ts}  {name:<15} {str(event.get('reason', ''))[:120]}"
    if name == "run_end":
        return f"{ts}  {name:<15} status={event.get('status', '')} turns={event.get('turns', '')}"
    return f"{ts}  {name}"


def cmd_show(args) -> int:
    try:
        run_dir, data = _open_run(args.slug)
    except RunsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    for key in SHOW_FIELDS:
        print(f"{key}: {_summary_value(key, data)}")
    print()
    print(json.dumps(data, indent=2, sort_keys=True))

    transcript_path = run_dir / "transcript.jsonl"
    if transcript_path.is_file():
        print("\ntimeline:")
        try:
            lines = transcript_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            lines = []
            print(f"  (cannot read transcript: {e})")
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                print(_timeline_line(event))

    if getattr(args, "diff", False):
        patch_path = run_dir / "diff.patch"
        if patch_path.is_file():
            print("\ndiff:")
            print(patch_path.read_text(encoding="utf-8", errors="replace"))
        else:
            print("\nno diff.patch for this run (host mode, or the export never ran)")
    return 0


def dispatch(args) -> int:
    """`main()` routes `dirtywork runs <sub>` here. Each later task adds one
    entry to this table and one parser block in `__main__._add_runs_parsers`."""
    handlers = {
        "list": cmd_list,
        "show": cmd_show,
    }
    return handlers[args.runs_cmd](args)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_runs.py -q`
Expected: 12 passed

- [ ] **Step 5: Wire the `runs` subparsers into `dirtywork/__main__.py`**

`main()` already dispatches on `args.cmd` (`run` / `resume`) and `_parse_args` already builds both subparsers — nothing has to be extracted. Make exactly three edits.

(a) Add this function immediately **above** `def _parse_args(argv):`:

```python
def _add_runs_parsers(sub) -> None:
    """`dirtywork runs ...` (spec SP3 section 4). Every subcommand is
    implemented in dirtywork/runs.py and routed by `runs.dispatch()`."""
    runs_p = sub.add_parser("runs", help="inspect and manage dirtywork runs")
    runs_sub = runs_p.add_subparsers(dest="runs_cmd", required=True)

    list_p = runs_sub.add_parser("list", help="list every run under ~/.dirtywork/runs")
    list_p.add_argument("--json", action="store_true", help="machine-readable output")

    show_p = runs_sub.add_parser("show", help="show one run's summary, run.json and timeline")
    show_p.add_argument("slug")
    show_p.add_argument("--diff", action="store_true", help="also print the run's diff.patch")
```

(b) In `_parse_args`, insert the call before the `return`:

```python
    resume_p = sub.add_parser("resume", help="continue an earlier run on its worktree")
    resume_p.add_argument("run", help="run slug (under ~/.dirtywork/runs) or a run directory path")
    _add_run_flags(resume_p, resume=True)
    _add_runs_parsers(sub)
    return parser.parse_args(argv)
```

(c) In `main()`, route `runs` before any preflight work happens:

```python
def main(argv: list | None = None) -> int:
    args = _parse_args(argv)
    if args.cmd == "runs":
        from . import runs as runs_mod
        return runs_mod.dispatch(args)
    try:
```

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
    assert "no such run" in capsys.readouterr().err
```

- [ ] **Step 7: Run the CLI-facing tests**

Run: `python3 -m pytest tests/test_runs.py tests/test_main.py -q`
Expected: all pass

- [ ] **Step 8: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all green (585 baseline plus every test added by this plan so far)

- [ ] **Step 9: Commit**

```bash
git add dirtywork/runs.py dirtywork/__main__.py tests/test_runs.py tests/test_main.py
git commit -m "feat: add 'dirtywork runs list' and 'dirtywork runs show'"
```

---

### Task 10: `runs export`

**Files:**
- Modify: `dirtywork/runs.py` (add `cmd_export` and helpers)
- Modify: `dirtywork/__main__.py` (add the `runs export` parser block)
- Modify: `tests/test_runs.py` (add `cmd_export` tests)

**Interfaces:**
- Consumes: `dirtywork.sandbox.export.export_run(cfg, *, slug, base_commit, worktree, run_dir, objects_dir, image_ref, uid, gid, repo_label, run=docker_cli.run, popen=subprocess.Popen) -> RunArtifacts`; `dirtywork.sandbox.docker_cli.{run, validate_objects_dir, resolve_image, T_QUERY}`; `dirtywork.sandbox.docker_args.{DockerConfig, DEFAULT_IMAGE, repo_label, pin_for, volume_name}`; `dirtywork.resume.pid_alive`; `dirtywork.rundir.write_run_json`.
- Produces: `dirtywork.runs.cmd_export(args) -> int`; CLI: `dirtywork runs export <slug> [--max-patch-mb 10] [--keep-volume]`.

Facts this task is built on (verified in `dirtywork/sandbox/export.py` and `dirtywork/sandbox/docker.py`):

- `export_run` refuses a worktree that holds anything other than the single `.git` **file** (`export_status="export_failed: worktree not empty"`). `runs export` checks that first and refuses with exit 2, so a re-export after a successful one never spins up a container.
- On `export_failed`, `export_run` always keeps the volume so a retry is possible; on success it removes the volume unless `cfg.keep_volume`.
- `resolve_image` takes `pinned_digest`; `docker_args.pin_for(image)` returns the pin for the maintained default image only. `runs export` must use the same rule as `_docker_preflight`, or a re-export of a default-image run would silently skip the pin.
- uid/gid follow `DockerSandbox.start`: `os.getuid()/os.getgid()` on POSIX, `1000/1000` elsewhere.
- The archive can never contain `.git` entries — that is the export validator's rule, and the reason a docker run's commits cannot reach the host (Task 16 depends on this).

- [ ] **Step 1: Write the failing tests**

Add to the imports at the top of `tests/test_runs.py`:

```python
from dirtywork.sandbox import RunArtifacts
```

Append to `tests/test_runs.py`:

```python
def _docker_run_json(runs_dir: Path, slug: str, repo: Path, worktree: Path, **over):
    data = {
        "status": "export_failed", "sandbox": "docker", "slug": slug,
        "repo": str(repo), "worktree": str(worktree), "base_commit": "abc123",
        "volume": f"dw-{slug}-work", "container": f"dw-{slug}",
        "image": "ghcr.io/jimboschneider/dirtywork-worker:0.5", "host_pid": 999999,
    }
    data.update(over)
    return _write_run(runs_dir, slug, data)


def _empty_worktree(repo: Path, slug: str) -> Path:
    """A worktree in the state export_run requires: the .git file and nothing else."""
    wt = repo / ".worktrees" / f"dw-{slug}"
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: /nowhere\n")
    return wt


def _export_ok(monkeypatch, artifacts):
    monkeypatch.setattr(runs.docker_cli, "run", lambda *a, **k: _FakeCaptured(0))
    monkeypatch.setattr(runs.docker_cli, "validate_objects_dir",
                        lambda repo: Path(repo) / ".git" / "objects")
    monkeypatch.setattr(runs.docker_cli, "resolve_image",
                        lambda image, **kw: f"sha256:{'a' * 64}")
    monkeypatch.setattr(runs.export, "export_run", lambda cfg, **kw: artifacts)


def test_cmd_export_not_docker_sandbox_rejected(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "hostrun", {"status": "completed", "sandbox": "none"})
    rc = runs.cmd_export(argparse.Namespace(slug="hostrun", max_patch_mb=10, keep_volume=False))
    assert rc == 2
    assert "not a docker-sandbox run" in capsys.readouterr().err


def test_cmd_export_live_run_rejected(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = _empty_worktree(repo, "slug1")
    _docker_run_json(tmp_path / "runs", "slug1", repo, wt,
                     status="running", host_pid=os.getpid())
    rc = runs.cmd_export(argparse.Namespace(slug="slug1", max_patch_mb=10, keep_volume=False))
    assert rc == 2
    assert "still running" in capsys.readouterr().err


def test_cmd_export_non_empty_worktree_rejected(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = _empty_worktree(repo, "slug1")
    (wt / "already-exported.txt").write_text("x")
    _docker_run_json(tmp_path / "runs", "slug1", repo, wt)
    rc = runs.cmd_export(argparse.Namespace(slug="slug1", max_patch_mb=10, keep_volume=False))
    assert rc == 2
    assert "not empty" in capsys.readouterr().err


def test_cmd_export_missing_volume_exits_2(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = _empty_worktree(repo, "slug1")
    _docker_run_json(tmp_path / "runs", "slug1", repo, wt)
    monkeypatch.setattr(runs.docker_cli, "run", lambda *a, **k: _FakeCaptured(1))
    rc = runs.cmd_export(argparse.Namespace(slug="slug1", max_patch_mb=10, keep_volume=False))
    assert rc == 2
    assert "does not exist" in capsys.readouterr().err


def test_cmd_export_success_updates_run_json(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = _empty_worktree(repo, "slug1")
    run_dir = _docker_run_json(tmp_path / "runs", "slug1", repo, wt)
    _export_ok(monkeypatch, RunArtifacts(diff_stat=" 1 file changed",
                                         patch_path=str(run_dir / "diff.patch"),
                                         worktree_bytes=100, worktree_files=1,
                                         export_status="ok"))
    rc = runs.cmd_export(argparse.Namespace(slug="slug1", max_patch_mb=10, keep_volume=False))
    assert rc == 0
    assert "exported 'slug1'" in capsys.readouterr().out
    data = json.loads((run_dir / "run.json").read_text())
    assert data["export_status"] == "ok"
    assert data["diff_stat"] == " 1 file changed"
    assert data["status"] == "completed"       # export_failed -> completed


def test_cmd_export_success_keeps_a_non_export_status(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = _empty_worktree(repo, "slug1")
    run_dir = _docker_run_json(tmp_path / "runs", "slug1", repo, wt, status="budget_exceeded")
    _export_ok(monkeypatch, RunArtifacts(export_status="ok"))
    rc = runs.cmd_export(argparse.Namespace(slug="slug1", max_patch_mb=10, keep_volume=False))
    assert rc == 0
    data = json.loads((run_dir / "run.json").read_text())
    assert data["export_status"] == "ok"
    assert data["status"] == "budget_exceeded"   # why the run ended is not rewritten


def test_cmd_export_failure_reports_and_returns_1(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = _empty_worktree(repo, "slug1")
    run_dir = _docker_run_json(tmp_path / "runs", "slug1", repo, wt, status="completed")
    _export_ok(monkeypatch, RunArtifacts(export_status="export_failed: archive too large"))
    rc = runs.cmd_export(argparse.Namespace(slug="slug1", max_patch_mb=10, keep_volume=False))
    assert rc == 1
    assert "export failed" in capsys.readouterr().err
    data = json.loads((run_dir / "run.json").read_text())
    assert data["status"] == "export_failed"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_runs.py -q -k export`
Expected: `AttributeError: module 'dirtywork.runs' has no attribute 'cmd_export'`

- [ ] **Step 3: Write the implementation**

Extend the imports at the top of `dirtywork/runs.py`:

```python
import os
from .resume import pid_alive
from .sandbox import docker_args, docker_cli, export
```

(i.e. the existing `from .sandbox import docker_cli` line becomes the three-name import above; `import os` joins the stdlib block.)

Append to `dirtywork/runs.py`:

```python
def _uid_gid():
    """Same rule DockerSandbox.start uses: the invoking user on POSIX, the
    image's baked-in worker uid elsewhere."""
    return (os.getuid(), os.getgid()) if os.name == "posix" else (1000, 1000)


def _export_status_update(previous: str, export_status: str) -> str:
    """What `status` becomes after a re-export, mirroring `__main__._final_status`:
    an export result only ever replaces a status that was ABOUT the export (or a
    run left marked 'running' by a crash). A run that ended `budget_exceeded` or
    `timeout` keeps that status — the export is not why it ended."""
    if export_status == "ok":
        return "completed" if previous in (None, "", "running", "export_failed") else previous
    return "export_failed" if previous in (None, "", "running", "completed") else previous


def cmd_export(args) -> int:
    """Spec SP3 section 4: re-run the SP2 section 7 export for a run whose volume
    still exists (a crash, or `export_failed` after the operator raised a limit).
    Refuses a non-empty worktree, a still-running run, and anything that is not a
    docker-sandbox run."""
    try:
        run_dir, data = _open_run(args.slug)
    except RunsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if data.get("sandbox") != "docker":
        print(f"error: run '{args.slug}' is not a docker-sandbox run; nothing to export",
              file=sys.stderr)
        return 2
    if data.get("status") == "running" and pid_alive(data.get("host_pid")):
        print(f"error: run '{args.slug}' is still running (pid {data.get('host_pid')}); "
              f"wait for it to finish before exporting", file=sys.stderr)
        return 2

    volume = data.get("volume") or ""
    if not volume:
        print(f"error: run.json for '{args.slug}' records no volume", file=sys.stderr)
        return 2
    worktree = Path(data.get("worktree", ""))
    if not worktree.is_dir():
        print(f"error: worktree {worktree} is missing; nothing to export into", file=sys.stderr)
        return 2
    existing = list(worktree.iterdir())
    if len(existing) != 1 or existing[0].name != ".git" or not existing[0].is_file():
        print(f"error: worktree {worktree} is not empty (it holds more than the .git file); "
              f"the export refuses to overwrite work already on disk", file=sys.stderr)
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
    try:
        objects_dir = docker_cli.validate_objects_dir(repo)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    image = data.get("image") or docker_args.DEFAULT_IMAGE
    try:
        image_ref = docker_cli.resolve_image(image, pinned_digest=docker_args.pin_for(image))
    except Exception as e:
        print(f"error: cannot resolve image '{image}': {e}", file=sys.stderr)
        return 2

    cfg = docker_args.DockerConfig(image=image, max_patch_mb=args.max_patch_mb,
                                   keep_volume=args.keep_volume)
    uid, gid = _uid_gid()
    artifacts = export.export_run(
        cfg, slug=args.slug, base_commit=data["base_commit"], worktree=worktree,
        run_dir=run_dir, objects_dir=objects_dir, image_ref=image_ref, uid=uid, gid=gid,
        repo_label=docker_args.repo_label(repo),
    )

    data["status"] = _export_status_update(data.get("status"), artifacts.export_status)
    data["export_status"] = artifacts.export_status
    data["diff_stat"] = artifacts.diff_stat
    data["patch_path"] = artifacts.patch_path
    data["worktree_bytes"] = artifacts.worktree_bytes
    data["worktree_files"] = artifacts.worktree_files
    data["escaping_symlinks"] = artifacts.escaping_symlinks
    data["dropped_git_entries"] = artifacts.dropped_git_entries
    rundir.write_run_json(run_dir, data)

    if artifacts.export_status != "ok":
        print(f"error: export failed: {artifacts.export_status}\n"
              f"the volume was kept, so this command can be retried after raising a limit",
              file=sys.stderr)
        return 1
    print(f"exported '{args.slug}' into {worktree}")
    if artifacts.diff_stat:
        print(artifacts.diff_stat)
    return 0
```

Add `"export": cmd_export,` to the `handlers` dict inside `dispatch`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_runs.py -q`
Expected: 19 passed

- [ ] **Step 5: Add the `runs export` parser block**

In `dirtywork/__main__.py`, append to `_add_runs_parsers`:

```python
    export_p = runs_sub.add_parser("export", help="re-run the export flow for a run")
    export_p.add_argument("slug")
    export_p.add_argument("--max-patch-mb", type=int, default=10)
    export_p.add_argument("--keep-volume", action="store_true", default=False)
```

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add dirtywork/runs.py dirtywork/__main__.py tests/test_runs.py
git commit -m "feat: add 'dirtywork runs export'"
```

---

### Task 11: `runs clean`

**Files:**
- Modify: `dirtywork/runs.py` (add `cmd_clean` and helpers)
- Modify: `dirtywork/__main__.py` (add the `runs clean` parser block)
- Modify: `tests/test_runs.py` (add `cmd_clean` tests)

**Interfaces:**
- Consumes: `dirtywork.sandbox.docker_args.repo_label`; `dirtywork.sandbox.docker_cli.{run, T_QUERY, T_LIFECYCLE}`; `dirtywork.resume.{pid_alive, stash_dir_for, find_stashes}`; `git worktree remove --force` / `git branch -D`.
- Produces: `dirtywork.runs.cmd_clean(args) -> int`; CLI: `dirtywork runs clean <slug> | --all [--keep-transcript] [--force]`.

The rules this implements, each traceable to a source:

1. **SP2 section 3 collision rule** — a container/volume is removed only when the `dirtywork.run` label equals this run's slug **and** the `dirtywork.repo` label equals `docker_args.repo_label(repo)` (verified names in `dirtywork/sandbox/docker_args.py::_label_args`), the run's `run.json` is owned by the current user, and the run is definitively stale (status other than `running`, or `running` with a dead `host_pid` **and** `--force`).
2. **Pre-resume stashes** — a docker resume parks the prior worktree content in `<worktree>.pre-resume-<slug>` (`dirtywork.resume.stash_dir_for`). Cleaning a run removes the stash *that run created*; when the worktree itself is removed, every remaining stash beside it (`find_stashes`) goes too, because nothing owns them any more.
3. **Shared worktree** — a run with `resumed_by` set had its worktree **and branch** taken over by the later resume run (`_workspace_resume` reuses `prior["branch"]` and `prior["worktree"]`). Cleaning the older run therefore leaves worktree and branch alone and prints a note naming the newer run; its own run dir and its own per-slug container/volume are still cleaned, since those are named `dw-<slug>`/`dw-<slug>-work` and label-checked against this exact slug.

- [ ] **Step 1: Write the failing tests**

Add to the imports at the top of `tests/test_runs.py`:

```python
import shutil

from dirtywork.resume import stash_dir_for
from dirtywork.sandbox import docker_args
```

Append to `tests/test_runs.py`:

```python
def _fake_docker_run(container_label=None, volume_label=None, rm_ok=True):
    """container_label/volume_label are the `<run>\\t<repo>` label pair the fake
    `docker inspect --format` prints; None means 'no such object'."""
    def _run(argv, timeout=None):
        if argv[:1] == ["inspect"]:
            return _FakeCaptured(1) if container_label is None else _FakeCaptured(
                0, container_label.encode())
        if argv[:2] == ["volume", "inspect"]:
            return _FakeCaptured(1) if volume_label is None else _FakeCaptured(
                0, volume_label.encode())
        if argv[:1] == ["rm"] or argv[:2] == ["volume", "rm"]:
            return _FakeCaptured(0 if rm_ok else 1)
        return _FakeCaptured(1)
    return _run


def _clean_args(slug=None, all=False, keep_transcript=False, force=False):
    return argparse.Namespace(slug=slug, all=all, keep_transcript=keep_transcript, force=force)


def test_clean_skips_unlabeled_container(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": None,
        "container": "dw-slug1", "volume": None, "branch": None,
    })
    monkeypatch.setattr(runs.docker_cli, "run",
                        _fake_docker_run(container_label="other-slug\twrong-repo-label"))
    rc = runs.cmd_clean(_clean_args("slug1", keep_transcript=True))
    out = capsys.readouterr().out
    assert "labels do not match" in out
    assert rc == 1


def test_clean_skips_not_owned_by_current_user(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": None,
        "container": None, "volume": None,
    })
    real_uid = os.getuid()
    # capture the real uid FIRST: the lambda must not call the patched getuid
    monkeypatch.setattr(runs.os, "getuid", lambda: real_uid + 1)
    rc = runs.cmd_clean(_clean_args("slug1", keep_transcript=True))
    assert "not owned by the current user" in capsys.readouterr().out
    assert rc == 1


def test_clean_skips_running_with_alive_pid(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "running", "host_pid": os.getpid(), "repo": str(repo),
        "worktree": None, "container": None, "volume": None,
    })
    rc = runs.cmd_clean(_clean_args("slug1", keep_transcript=True))
    assert "host process" in capsys.readouterr().out
    assert rc == 1


def test_clean_refuses_dead_pid_without_force(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "running", "host_pid": 999999, "repo": str(repo),
        "worktree": None, "container": None, "volume": None,
    })
    rc = runs.cmd_clean(_clean_args("slug1", keep_transcript=True))
    assert "dead host process" in capsys.readouterr().out
    assert rc == 1


def test_clean_removes_dead_pid_with_force(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _write_run(tmp_path / "runs", "slug1", {
        "status": "running", "host_pid": 999999, "repo": str(repo),
        "worktree": None, "container": None, "volume": None, "branch": None,
    })
    rc = runs.cmd_clean(_clean_args("slug1", force=True))
    assert "removed-rundir" in capsys.readouterr().out
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
        container_label=f"slug1\t{label}", volume_label=f"slug1\t{label}"))
    rc = runs.cmd_clean(_clean_args("slug1"))
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
    rc = runs.cmd_clean(_clean_args("slug1", keep_transcript=True))
    out = capsys.readouterr().out
    assert "uncommitted changes" in out
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
    rc = runs.cmd_clean(_clean_args("slug1", force=True))
    out = capsys.readouterr().out
    assert "removed-worktree" in out
    assert "removed-branch" in out
    assert not wt.exists()
    assert rc == 0
    assert "dirtywork/slug1" not in _git(repo, "branch", "--list", "dirtywork/slug1").stdout


def test_clean_removes_the_runs_pre_resume_stash(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = repo / ".worktrees" / "dw-prior"
    _git(repo, "worktree", "add", "-b", "dirtywork/prior", str(wt), "HEAD")
    stash = stash_dir_for(wt, "slug1")
    stash.mkdir()
    (stash / "kept.txt").write_text("prior content")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": str(wt),
        "container": None, "volume": None, "branch": "dirtywork/prior",
    })
    rc = runs.cmd_clean(_clean_args("slug1", force=True))
    out = capsys.readouterr().out
    assert f"removed-stash: {stash}" in out
    assert not stash.exists()
    assert rc == 0


def test_clean_keeps_worktree_shared_with_a_later_resume(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = repo / ".worktrees" / "dw-first"
    _git(repo, "worktree", "add", "-b", "dirtywork/first", str(wt), "HEAD")
    run_dir = _write_run(tmp_path / "runs", "first", {
        "status": "max_turns", "repo": str(repo), "worktree": str(wt),
        "container": None, "volume": None, "branch": "dirtywork/first",
        "resumed_by": "second",
    })
    rc = runs.cmd_clean(_clean_args("first"))
    out = capsys.readouterr().out
    assert "kept-worktree" in out
    assert "second" in out
    assert wt.exists()                                   # the newer run still owns it
    assert "dirtywork/first" in _git(repo, "branch", "--list", "dirtywork/first").stdout
    assert not run_dir.exists()                          # but this run's own dir is gone
    assert rc == 0


def test_clean_keep_transcript_preserves_transcript_and_run_json(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": None,
        "container": None, "volume": None,
    })
    (run_dir / "transcript.jsonl").write_text('{"event": "run_start"}\n')
    (run_dir / "diff.patch").write_text("stuff")
    rc = runs.cmd_clean(_clean_args("slug1", keep_transcript=True))
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
    rc = runs.cmd_clean(_clean_args(all=True))
    assert rc == 0
    assert not (tmp_path / "runs" / "a").exists()
    assert not (tmp_path / "runs" / "b").exists()


def test_clean_unknown_slug_reports_skip(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    rc = runs.cmd_clean(_clean_args("nope", keep_transcript=True))
    assert "no such run" in capsys.readouterr().out
    assert rc == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_runs.py -q -k clean`
Expected: `AttributeError: module 'dirtywork.runs' has no attribute 'cmd_clean'`

- [ ] **Step 3: Write the implementation**

Extend the imports at the top of `dirtywork/runs.py`: add `import shutil` to the stdlib block and widen the resume import to `from .resume import find_stashes, pid_alive, stash_dir_for`.

Append to `dirtywork/runs.py`:

```python
def _staleness(data: dict, force: bool):
    """(is_stale, why_not) per SP2 section 3: any status other than 'running' is
    stale; 'running' is stale only with a confirmed-dead host_pid AND --force."""
    if data.get("status") != "running":
        return True, None
    host_pid = data.get("host_pid")
    if not isinstance(host_pid, int) or isinstance(host_pid, bool):
        return False, "status is 'running' and no host_pid is recorded to check"
    if pid_alive(host_pid):
        return False, f"status is 'running' and its host process ({host_pid}) is alive"
    if force:
        return True, None
    return False, ("status is 'running' with a dead host process -- pass --force to "
                   "confirm cleanup")


def _run_json_owned_by_current_user(run_dir: Path) -> bool:
    """SP2 section 3's ownership condition. Windows has no uid ownership and no
    integration suite yet, so this fails closed there."""
    if not hasattr(os, "getuid"):
        return False
    try:
        return (run_dir / "run.json").stat().st_uid == os.getuid()
    except OSError:
        return False


def _clean_docker_resource(kind: str, name: str, repo: str, slug: str, log: list) -> None:
    """kind is 'container' or 'volume'. Removes ONLY a resource whose
    dirtywork.run/dirtywork.repo labels match this exact run; anything missing,
    unlabeled, or belonging to another run/repo is reported and left alone."""
    if kind == "container":
        inspect_argv = ["inspect", "--format",
                        '{{index .Config.Labels "dirtywork.run"}}\t'
                        '{{index .Config.Labels "dirtywork.repo"}}', name]
        rm_argv = ["rm", "-f", name]
    else:
        inspect_argv = ["volume", "inspect", "--format",
                        '{{index .Labels "dirtywork.run"}}\t'
                        '{{index .Labels "dirtywork.repo"}}', name]
        rm_argv = ["volume", "rm", name]
    try:
        cp = docker_cli.run(inspect_argv, timeout=docker_cli.T_QUERY)
    except Exception as e:
        log.append((f"skip-{kind}", f"'{name}': cannot inspect: {e}"))
        return
    if cp.returncode != 0:
        log.append((f"skip-{kind}", f"'{name}': not found (already removed?)"))
        return
    run_label, _, repo_label_value = cp.output.decode("utf-8", errors="replace").strip().partition("\t")
    if run_label != slug or repo_label_value != docker_args.repo_label(Path(repo)):
        log.append((f"skip-{kind}", f"'{name}': labels do not match this run -- never touching it"))
        return
    try:
        rm = docker_cli.run(rm_argv, timeout=docker_cli.T_LIFECYCLE)
    except Exception as e:
        log.append((f"skip-{kind}", f"'{name}': removal failed: {e}"))
        return
    log.append((f"removed-{kind}" if rm.returncode == 0 else f"skip-{kind}", name))


def _worktree_is_dirty(worktree: str) -> bool:
    """Fail closed: if git cannot be asked, treat the worktree as dirty."""
    try:
        cp = subprocess.run(["git", "-C", str(worktree), "status", "--porcelain"],
                            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return True
    return cp.returncode != 0 or bool(cp.stdout.strip())


def _clean_worktree_and_branch(data: dict, slug: str, force: bool, log: list) -> bool:
    """Returns True when the worktree was actually removed. A run whose worktree
    was taken over by a later resume (resumed_by set) keeps both the worktree and
    the branch -- they belong to the newest run in the chain."""
    worktree = data.get("worktree")
    repo = data.get("repo", "")
    if not worktree or not repo:
        return False
    resumed_by = data.get("resumed_by")
    if resumed_by:
        log.append(("kept-worktree",
                    f"'{worktree}': shared with the later resume run '{resumed_by}' -- "
                    f"the worktree and branch belong to the newest run in the chain; "
                    f"run `dirtywork runs clean {resumed_by}` to remove them"))
        return False
    if _worktree_is_dirty(worktree) and not force:
        log.append(("skip-worktree",
                    f"'{worktree}': has uncommitted changes (pass --force to remove anyway)"))
        return False
    try:
        rm = subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
                            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        log.append(("skip-worktree", f"'{worktree}': {e}"))
        return False
    if rm.returncode != 0:
        log.append(("skip-worktree", f"'{worktree}': {rm.stderr.strip() or 'git worktree remove failed'}"))
        return False
    log.append(("removed-worktree", str(worktree)))
    branch = data.get("branch")
    if branch:
        try:
            br = subprocess.run(["git", "-C", str(repo), "branch", "-D", str(branch)],
                                capture_output=True, text=True, timeout=10)
            log.append(("removed-branch" if br.returncode == 0 else "skip-branch", str(branch)))
        except (OSError, subprocess.SubprocessError) as e:
            log.append(("skip-branch", f"'{branch}': {e}"))
    return True


def _clean_stashes(data: dict, slug: str, worktree_removed: bool, log: list) -> None:
    """A docker resume parks the pre-resume worktree content in
    `<worktree>.pre-resume-<slug>` (resume.stash_dir_for). Cleaning a run removes
    the stash that run created; once the worktree itself is gone, every remaining
    stash beside it is orphaned and goes too."""
    worktree = data.get("worktree")
    if not worktree:
        return
    worktree = Path(worktree)
    targets = [stash_dir_for(worktree, slug)]
    if worktree_removed:
        targets += [p for p in find_stashes(worktree) if p not in targets]
    for stash in targets:
        if stash.is_dir():
            shutil.rmtree(stash, ignore_errors=True)
            log.append(("removed-stash", str(stash)))


def _clean_run_dir(run_dir: Path, keep_transcript: bool, log: list) -> None:
    if keep_transcript:
        for child in run_dir.iterdir():
            if child.name in ("transcript.jsonl", "run.json"):
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except OSError:
                    pass
        log.append(("kept-transcript", str(run_dir)))
    else:
        shutil.rmtree(run_dir, ignore_errors=True)
        log.append(("removed-rundir", str(run_dir)))


def _clean_one(slug: str, *, keep_transcript: bool, force: bool) -> list:
    """(action, detail) pairs describing what happened. Any action starting with
    'skip' means something was deliberately left alone -- never a silent no-op."""
    log: list = []
    try:
        run_dir, data = _open_run(slug)
    except RunsError as e:
        log.append(("skip", str(e)))
        return log
    if not _run_json_owned_by_current_user(run_dir):
        log.append(("skip", f"'{slug}': run.json is not owned by the current user"))
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

    worktree_removed = _clean_worktree_and_branch(data, slug, force, log)
    _clean_stashes(data, slug, worktree_removed, log)
    _clean_run_dir(run_dir, keep_transcript, log)
    return log


def cmd_clean(args) -> int:
    slugs = ([d.name for d in _iter_run_dirs(rundir.RUNS_DIR)] if args.all else [args.slug])
    any_skipped = False
    for slug in slugs:
        for action, detail in _clean_one(slug, keep_transcript=args.keep_transcript,
                                         force=args.force):
            print(f"{action}: {detail}")
            if action.startswith("skip"):
                any_skipped = True
    return 1 if any_skipped else 0
```

Add `"clean": cmd_clean,` to the `handlers` dict inside `dispatch`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_runs.py -q`
Expected: 32 passed

- [ ] **Step 5: Add the `runs clean` parser block and its argument validation**

In `dirtywork/__main__.py`, append to `_add_runs_parsers`:

```python
    clean_p = runs_sub.add_parser("clean", help="remove a run's container/volume/worktree/run dir")
    clean_p.add_argument("slug", nargs="?", default=None)
    clean_p.add_argument("--all", action="store_true", default=False)
    clean_p.add_argument("--keep-transcript", action="store_true", default=False)
    clean_p.add_argument("--force", action="store_true", default=False)
```

and add the slug/`--all` validation to `runs.dispatch` (not to `main()`, so the whole `runs` CLI keeps its rules in one module) — insert at the top of `dispatch`:

```python
    if args.runs_cmd == "clean":
        if not args.all and not args.slug:
            print("error: 'runs clean' needs a slug or --all", file=sys.stderr)
            return 2
        if args.all and args.slug:
            print("error: 'runs clean' takes a slug or --all, not both", file=sys.stderr)
            return 2
```

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add dirtywork/runs.py dirtywork/__main__.py tests/test_runs.py
git commit -m "feat: add 'dirtywork runs clean' with the SP2 collision rule and stash cleanup"
```

---

### Task 12: `runs verdict`

**Files:**
- Modify: `dirtywork/runs.py` (add `cmd_verdict`)
- Modify: `dirtywork/__main__.py` (add the `runs verdict` parser block)
- Modify: `tests/test_runs.py` (add `cmd_verdict` tests)

**Interfaces:**
- Consumes: `datetime.{datetime, timezone}`; `dirtywork.rundir.write_run_json`; the `ended` key `__main__._update_run_json` writes at end of run.
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
    assert data["status"] == "completed"        # the run's own fields are untouched


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
    assert "no such run" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_runs.py -q -k verdict`
Expected: `AttributeError: module 'dirtywork.runs' has no attribute 'cmd_verdict'`

- [ ] **Step 3: Write the implementation**

Add `from datetime import datetime, timezone` to the stdlib imports at the top of `dirtywork/runs.py`, then append:

```python
def cmd_verdict(args) -> int:
    """Spec SP3 section 4: append the operator's verdict to run.json.
    `time_to_verdict_s` is measured from the run's `ended` timestamp (the key
    `__main__._update_run_json` writes) and is deliberately noisy -- it includes
    idle time. `--review-seconds` is the operator's explicit measure."""
    try:
        run_dir, data = _open_run(args.slug)
    except RunsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    verdict_at = datetime.now(timezone.utc).isoformat()
    data["verdict"] = args.verdict
    data["note"] = args.note
    data["verdict_at"] = verdict_at
    data["review_seconds"] = args.review_seconds
    data["time_to_verdict_s"] = None
    ended = data.get("ended")
    if ended:
        try:
            ended_dt = datetime.fromisoformat(str(ended).replace("Z", "+00:00"))
            data["time_to_verdict_s"] = (
                datetime.fromisoformat(verdict_at) - ended_dt).total_seconds()
        except ValueError:
            pass

    rundir.write_run_json(run_dir, data)
    print(f"recorded verdict '{args.verdict}' for '{args.slug}'")
    return 0
```

Add `"verdict": cmd_verdict,` to the `handlers` dict inside `dispatch`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_runs.py -q`
Expected: 35 passed

- [ ] **Step 5: Add the `runs verdict` parser block**

In `dirtywork/__main__.py`, append to `_add_runs_parsers`:

```python
    verdict_p = runs_sub.add_parser("verdict", help="record accept/reject/cleanup for a run")
    verdict_p.add_argument("slug")
    verdict_p.add_argument("verdict", choices=["accept", "reject", "cleanup"])
    verdict_p.add_argument("--note", default=None)
    verdict_p.add_argument("--review-seconds", type=float, default=None)
```

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add dirtywork/runs.py dirtywork/__main__.py tests/test_runs.py
git commit -m "feat: add 'dirtywork runs verdict'"
```

---
### Task 13: Bench fixture repos (3) + `bench.json` schema

**Files:**
- Create: `bench/repos/py-fix-off-by-one/{sum_range.py, bench.json, acceptance/check_sum_range.py}`
- Create: `bench/repos/node-add-cli-flag/{greet.js, bench.json, acceptance/greet.test.js}`
- Create: `bench/repos/sh-fix-script/{report.sh, bench.json, acceptance/expected_output.txt, acceptance/check.sh}`
- Create: `tests/test_bench.py`

**Interfaces:**
- Consumes: `hashlib.sha256` (stdlib); the runtimes the worker image actually ships (`docker/Dockerfile`: `python3` **without pytest**, `nodejs`, `bash`, `coreutils`, `git`, `ripgrep`, `dotnet`).
- Produces: three fixture repos, each with `bench.json` = `{"task": str, "acceptance": {"command": str, "hashes": {"acceptance/<relpath>": "<sha256 hex>"}}}`.

Design rules for every fixture, each forced by something real:

- **The acceptance command runs the copy under `/acceptance`, never the worktree's copy.** Spec SP3 section 5: "Acceptance commands never come from the worktree." Task 14 mounts `bench/repos/<task>/acceptance/` read-only at `/acceptance` and runs the command with `/work` as the working directory, so every `command` names an absolute `/acceptance/...` path. The hashes are still recorded and checked against the worker's own `/work/acceptance/` copy — that check is what marks a run `gamed`.
- **Each harness resolves the subject from the current working directory**, so the identical file works from the fixture directory on the host (`cwd = bench/repos/<task>`) and from `/acceptance` in the container (`cwd = /work`).
- **No pytest, no test-runner CLI flags.** The worker image has `python3` but no pytest and no network to install it, and `node --test <file>` argument handling varies across Node 18.x. Plain asserts plus a nonzero exit are portable to both.
- **Byte-exact comparison where the bug is about bytes.** `"$(...)"` strips trailing newlines, so a shell harness that compares command substitutions cannot see a missing trailing newline — `sh-fix-script`'s check writes to a temp file and uses `cmp`.
- Each fixture ships in its **unsolved** state; the model's job is to make the acceptance command pass.

- [ ] **Step 1: Write the failing tests**

`tests/test_bench.py`:

```python
from __future__ import annotations

import hashlib
import json
import shlex
import shutil
import subprocess
from pathlib import Path

BENCH_REPOS = Path(__file__).resolve().parent.parent / "bench" / "repos"
TASK_NAMES = ["py-fix-off-by-one", "node-add-cli-flag", "sh-fix-script"]


def _bench_json(task_name: str) -> dict:
    return json.loads((BENCH_REPOS / task_name / "bench.json").read_text())


def _local_command(task_dir: Path, command: str) -> str:
    """The container command with /acceptance rewritten to this checkout's copy,
    so the same string can be exercised on the host."""
    return command.replace("/acceptance/", str(task_dir / "acceptance") + "/")


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
                f"{name}: {rel_path} hash mismatch -- recompute the hashes map with "
                f"hashlib.sha256(path.read_bytes()).hexdigest()")


def test_acceptance_commands_use_the_mounted_copy():
    # Spec SP3 section 5: the acceptance command never comes from the worktree.
    # Task 14 mounts acceptance/ read-only at /acceptance, so every command must
    # name that absolute path and never a /work-relative one.
    for name in TASK_NAMES:
        command = _bench_json(name)["acceptance"]["command"]
        assert "/acceptance/" in command, f"{name}: command does not use /acceptance"
        assert not command.startswith("acceptance/"), f"{name}: command is worktree-relative"


def test_task_source_files_are_unsolved():
    # Fixtures ship the BUGGY state -- if the acceptance check already passes
    # against the fixture as committed, the task gives the model nothing to do.
    runtimes = {"py-fix-off-by-one": "python3", "sh-fix-script": "bash",
                "node-add-cli-flag": "node"}
    for name in TASK_NAMES:
        if shutil.which(runtimes[name]) is None:
            continue  # optional runtime not installed in this environment
        task_dir = BENCH_REPOS / name
        command = _local_command(task_dir, _bench_json(name)["acceptance"]["command"])
        result = subprocess.run(shlex.split(command), cwd=str(task_dir),
                                capture_output=True, text=True)
        assert result.returncode != 0, (
            f"{name}: the acceptance check already passes on the unsolved fixture")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_bench.py -q`
Expected: `test_every_task_dir_exists_and_is_tiny` fails (`bench/repos` does not exist); the rest fail alongside it.

- [ ] **Step 3: Create the `py-fix-off-by-one` fixture**

`bench/repos/py-fix-off-by-one/sum_range.py`:

```python
def sum_range(low, high):
    """Return the sum of the integers from low to high, INCLUSIVE."""
    total = 0
    for i in range(low, high):  # BUG: excludes `high`
        total += i
    return total
```

`bench/repos/py-fix-off-by-one/acceptance/check_sum_range.py`:

```python
"""Acceptance check for py-fix-off-by-one.

Run from the repo root (`cd <repo> && python3 <this file>`): the subject is
imported from the CURRENT WORKING DIRECTORY, never from this script's own
directory, so the same file works both from the fixture dir on the host and
mounted read-only at /acceptance with /work as the cwd. Plain asserts and a
nonzero exit on failure -- the worker image ships python3 with no pytest.
"""
import os
import sys

sys.path.insert(0, os.getcwd())

from sum_range import sum_range  # noqa: E402

assert sum_range(1, 5) == 15, f"sum_range(1, 5) == {sum_range(1, 5)}, want 15"
assert sum_range(3, 3) == 3, f"sum_range(3, 3) == {sum_range(3, 3)}, want 3"
assert sum_range(0, 10) == 55, f"sum_range(0, 10) == {sum_range(0, 10)}, want 55"
print("PASS")
```

`bench/repos/py-fix-off-by-one/bench.json`:

```json
{
  "task": "sum_range(low, high) in sum_range.py must be inclusive of `high` but currently excludes it. Fix the bug so `python3 acceptance/check_sum_range.py` passes when run from the repo root.",
  "acceptance": {
    "command": "python3 /acceptance/check_sum_range.py",
    "hashes": {
      "acceptance/check_sum_range.py": "3135ce45c45c647f078869a55bbf6133b97f97d0dbf8f885fd99c7e04368e0aa"
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
// Acceptance check for node-add-cli-flag. Run from the repo root
// (`cd <repo> && node <this file>`): the subject is resolved from the CURRENT
// WORKING DIRECTORY, so this file works both from the fixture dir on the host
// and mounted read-only at /acceptance with /work as the cwd. Plain asserts --
// no test-runner CLI flags, so it behaves identically on any Node >= 18.
const assert = require("node:assert");
const { execFileSync } = require("node:child_process");
const path = require("node:path");

const GREET = path.join(process.cwd(), "greet.js");

const plain = execFileSync("node", [GREET, "Ada"]).toString().trim();
assert.strictEqual(plain, "Hello, Ada!");

const loud = execFileSync("node", [GREET, "Ada", "--loud"]).toString().trim();
assert.strictEqual(loud, "HELLO, ADA!");

console.log("PASS");
```

`bench/repos/node-add-cli-flag/bench.json`:

```json
{
  "task": "Add a --loud flag to greet.js: when passed, the printed greeting must be uppercased. `node acceptance/greet.test.js`, run from the repo root, must pass.",
  "acceptance": {
    "command": "node /acceptance/greet.test.js",
    "hashes": {
      "acceptance/greet.test.js": "cd0d5706648ecdd0fc1c87c86de2261e4decac195302712ea20b256690c1c9a0"
    }
  }
}
```

- [ ] **Step 5: Create the `sh-fix-script` fixture**

`bench/repos/sh-fix-script/report.sh`:

```bash
#!/usr/bin/env bash
# Prints a count report for the arguments it is given. BUG: no trailing
# newline, so the output does not match acceptance/expected_output.txt.
set -euo pipefail
count=0
for _ in "$@"; do
  count=$((count + 1))
done
printf 'files: %d' "$count"
```

`bench/repos/sh-fix-script/acceptance/expected_output.txt` — exactly `files: 3` followed by a newline; create it with a command rather than an editor so the trailing byte is unambiguous:

```bash
printf 'files: 3\n' > bench/repos/sh-fix-script/acceptance/expected_output.txt
```

`bench/repos/sh-fix-script/acceptance/check.sh`:

```bash
#!/usr/bin/env bash
# Acceptance check for sh-fix-script. Run from the repo root
# (`cd <repo> && bash <this file>`): the subject is ./report.sh in the CURRENT
# WORKING DIRECTORY, the expectation is read from this script's own directory,
# so the same file works from the fixture dir on the host and mounted
# read-only at /acceptance with /work as the cwd.
# cmp compares raw bytes: "$(...)" strips trailing newlines and would hide the
# exact bug this task is about.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
actual="$(mktemp)"
trap 'rm -f "$actual"' EXIT
bash ./report.sh a b c > "$actual"
if ! cmp -s "$actual" "$here/expected_output.txt"; then
  echo "FAIL: report.sh output does not match expected_output.txt byte for byte" >&2
  od -c "$actual" >&2
  exit 1
fi
echo "PASS"
```

`bench/repos/sh-fix-script/bench.json`:

```json
{
  "task": "report.sh a b c must print 'files: 3' followed by a trailing newline, matching acceptance/expected_output.txt byte for byte. Fix report.sh so `bash acceptance/check.sh`, run from the repo root, passes.",
  "acceptance": {
    "command": "bash /acceptance/check.sh",
    "hashes": {
      "acceptance/check.sh": "6509c6f08e1833ba9a912756c6ab0015b6c04102770fb703857e84aed528df29",
      "acceptance/expected_output.txt": "c79ef4d283db0e99f72006e91c5e75804a69b342234958a7bd02afef01e90ea4"
    }
  }
}
```

- [ ] **Step 6: Verify (and, if a byte drifted, repair) the recorded hashes**

The hashes above are the real `sha256` of the exact file contents in steps 3-5. If transcription changed so much as a trailing space, this rewrites each `bench.json`'s `hashes` map from what is actually on disk — the map must describe the committed files, not the other way round.

```bash
python3 - <<'PY'
import hashlib, json
from pathlib import Path

for bench_file in sorted(Path("bench/repos").glob("*/bench.json")):
    data = json.loads(bench_file.read_text())
    task_dir = bench_file.parent
    fresh = {rel: hashlib.sha256((task_dir / rel).read_bytes()).hexdigest()
             for rel in sorted(data["acceptance"]["hashes"])}
    if fresh != data["acceptance"]["hashes"]:
        data["acceptance"]["hashes"] = fresh
        bench_file.write_text(json.dumps(data, indent=2) + "\n")
        print(f"repaired hashes in {bench_file}")
    else:
        print(f"hashes already correct in {bench_file}")
PY
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_bench.py -q`
Expected: 5 passed (`test_task_source_files_are_unsolved` silently skips any fixture whose runtime is not installed here, and still checks the others)

- [ ] **Step 8: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all green

- [ ] **Step 9: Commit**

```bash
git add bench/repos tests/test_bench.py
git commit -m "test: add three tiny bench fixture repos with bench.json"
```

---

### Task 14: `dirtywork bench` run command

**Files:**
- Create: `dirtywork/bench.py`
- Modify: `dirtywork/__main__.py` (add `run_once(argv) -> dict`, `_add_bench_parsers(sub)`, the `bench` dispatch branch)
- Modify: `tests/test_bench.py` (add the bench-module tests)

**Interfaces:**
- Consumes: `dirtywork.__main__.main` (through the new `run_once`); `dirtywork.rundir.{RUNS_DIR, read_run_json}`; `dirtywork.runner.FAILURE_KINDS`; `dirtywork.sandbox.docker_cli.{run, resolve_image, T_LIFECYCLE, T_EXPORT_STEP}`; `dirtywork.sandbox.docker_args.{DEFAULT_IMAGE, PATH_ENV, pin_for}`; the shipped `run` flags in `_add_run_flags` (`--model`, `--provider`, `--base-url`, `--sandbox`, `--keep-volume`, `--max-turns`, `--timeout`).
- Produces: `dirtywork.__main__.run_once(argv: list) -> dict`; `dirtywork.bench.{BENCH_REPOS, BENCH_HOME, NUDGE_KINDS, available_tasks, parse_model_spec, run_one_bench_case, cmd_bench, dispatch}`; CLI: `dirtywork bench --models <spec>[,<spec>...] [--provider P] [--base-url URL] [--repeats N] [--tasks a,b] [--out FILE] [--max-turns N] [--timeout N]`.

What each results row carries (ruling R7) and where every number comes from:

| Field | Source |
|---|---|
| `stamp`, `model`, `task`, `repeat`, `provider`, `base_url` | the bench invocation itself |
| `slug`, `run_dir` | `Path(payload["run_dir"]).name` from the run's stdout JSON |
| `status` | stdout JSON `status` (includes `stalled`, `max_turns`, `sandbox_error`, `export_failed`) |
| `turns`, `prompt_tokens`, `completion_tokens` | stdout JSON `turns` / `usage` |
| `wall_s` | `time.monotonic()` around the run |
| `acceptance` | `pass` / `fail` / `gamed` / `skipped` from the fresh acceptance container |
| `guardrail_blocks`, `sandbox_resets` | counted `guardrail_block` / `sandbox_reset` transcript events |
| `harness.nudge_{stall,empty,truncated,text_tool_call}` | counted `nudge` events by `kind` (`runner.check_progress` writes `kind="stall"`; the reply-classification path writes the three `NUDGES` keys) |
| `harness.empty_reply` | the sum of the three non-stall nudge counts — the runner records exactly one `empty_reply` failure per such nudge (`failures.record("empty_reply")`) |
| `harness.stalled` / `max_turns` / `sandbox_error` | 1/0 from the final status |
| `harness.abort_kind` | parsed out of `final_message` for a `model_error` abort (`"aborted after N consecutive <kind> failures"`, with `<kind>` in `runner.FAILURE_KINDS`, or `mixed` for the total-failure abort) |
| `diff_stat` | the run's `run.json` |
| `verdict`, `review_seconds` | the run's `run.json` if a verdict already exists; `bench summarize` re-joins them later, which is the normal case |

`bench/` is not part of the installed package (`pyproject.toml`'s `[tool.setuptools] packages` lists only `dirtywork` and `dirtywork.sandbox`), so `dirtywork bench` runs from a source checkout. That is stated in the module docstring rather than worked around.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bench.py`:

```python
import argparse

from dirtywork import bench
from dirtywork.sandbox import docker_args


class _FakeCaptured:
    def __init__(self, returncode, output=b""):
        self.returncode = returncode
        self.output = output


def test_parse_model_spec_variants():
    assert bench.parse_model_spec("qwen/qwen3-coder-next") == (
        "qwen/qwen3-coder-next", None, None)
    assert bench.parse_model_spec("qwen/qwen3-coder-next", "openai", "http://localhost:1234/v1") == (
        "qwen/qwen3-coder-next", "openai", "http://localhost:1234/v1")
    assert bench.parse_model_spec("some-model@anthropic", "openai") == (
        "some-model", "anthropic", None)
    assert bench.parse_model_spec("m@openai=http://127.0.0.1:9/v1", "anthropic", "http://x") == (
        "m", "openai", "http://127.0.0.1:9/v1")


def test_hash_check_argv_exact():
    argv = bench._hash_check_argv("dw-slug-work", "sha256:deadbeef", 501, 20,
                                  ["/work/acceptance/check.sh"])
    assert argv == [
        "run", "--rm",
        "--network", "none",
        "--user", "501:20",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--memory", "2g", "--memory-swap", "2g",
        "--cpus", "2",
        "--pids-limit", "256",
        "--tmpfs", "/tmp:rw,exec,size=256m,mode=1777",
        "--mount", "type=volume,src=dw-slug-work,dst=/work",
        "-e", f"PATH={docker_args.PATH_ENV}",
        "-e", "HOME=/tmp",
        "-e", "TMPDIR=/tmp",
        "-e", "LANG=C.UTF-8",
        "--entrypoint", "/usr/bin/sha256sum", "sha256:deadbeef",
        "/work/acceptance/check.sh",
    ]


def test_acceptance_run_argv_exact(tmp_path):
    acceptance_dir = tmp_path / "acceptance"
    acceptance_dir.mkdir()
    argv = bench._acceptance_run_argv("dw-slug-work", "sha256:deadbeef", 501, 20,
                                      acceptance_dir, "bash /acceptance/check.sh")
    assert argv[:2] == ["run", "--rm"]
    # the acceptance container never gets network, whatever the run used
    assert argv[argv.index("--network") + 1] == "none"
    assert "type=volume,src=dw-slug-work,dst=/work" in argv
    assert f"type=bind,src={acceptance_dir.resolve()},dst=/acceptance,readonly" in argv
    assert f"PATH={docker_args.PATH_ENV}" in argv
    assert argv[-5:] == ["--entrypoint", "/bin/sh", "sha256:deadbeef", "-c",
                         "cd /work && bash /acceptance/check.sh"]


def test_stage_repo_creates_unique_committed_git_repos():
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


def _hash_lines(hashes, digest=None):
    return ("\n".join(f"{digest or h}  /work/{p}" for p, h in hashes.items()) + "\n").encode()


def _acceptance_fake(hashes, digest=None, command_rc=0, hash_rc=0):
    def _run(argv, timeout=None):
        if "/usr/bin/sha256sum" in argv:
            return _FakeCaptured(hash_rc, _hash_lines(hashes, digest))
        return _FakeCaptured(command_rc)
    return _run


def _patch_resolve_image(monkeypatch):
    monkeypatch.setattr(bench.docker_cli, "resolve_image",
                        lambda image, **kw: f"sha256:{'a' * 64}")


def test_run_acceptance_pass(monkeypatch):
    data = bench._bench_json("sh-fix-script")
    _patch_resolve_image(monkeypatch)
    assert bench._run_acceptance("sh-fix-script", data, "dw-x-work",
                                 run=_acceptance_fake(data["acceptance"]["hashes"])) == "pass"


def test_run_acceptance_fail(monkeypatch):
    data = bench._bench_json("sh-fix-script")
    _patch_resolve_image(monkeypatch)
    assert bench._run_acceptance("sh-fix-script", data, "dw-x-work",
                                 run=_acceptance_fake(data["acceptance"]["hashes"],
                                                      command_rc=1)) == "fail"


def test_run_acceptance_gamed_on_hash_mismatch(monkeypatch):
    data = bench._bench_json("sh-fix-script")
    _patch_resolve_image(monkeypatch)
    assert bench._run_acceptance("sh-fix-script", data, "dw-x-work",
                                 run=_acceptance_fake(data["acceptance"]["hashes"],
                                                      digest="0" * 64)) == "gamed"


def test_run_acceptance_gamed_when_a_harness_file_is_missing(monkeypatch):
    data = bench._bench_json("sh-fix-script")
    _patch_resolve_image(monkeypatch)
    # sha256sum exits 1 and prints nothing for a file the worker deleted
    assert bench._run_acceptance("sh-fix-script", data, "dw-x-work",
                                 run=lambda argv, timeout=None: _FakeCaptured(1, b"")) == "gamed"


def test_run_acceptance_skipped_when_docker_is_unavailable(monkeypatch):
    data = bench._bench_json("sh-fix-script")

    def boom(*a, **k):
        raise RuntimeError("no docker here")

    monkeypatch.setattr(bench.docker_cli, "resolve_image", boom)
    assert bench._run_acceptance("sh-fix-script", data, "dw-x-work", run=boom) == "skipped"


def _fake_run_environment(tmp_path, monkeypatch, *, payload, transcript_events=(), run_json=None):
    """Wires run_once/_stage_repo/_run_acceptance/docker_cli.run and lays down the
    run dir the real CLI would have produced. Returns the list argv is recorded into."""
    runs_dir = tmp_path / "runs"
    slug = "fixtask-0101-abcd"
    run_dir = runs_dir / slug
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps(run_json if run_json is not None
                                                 else {"volume": "dw-fixtask-work",
                                                       "diff_stat": " 1 file changed"}))
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in transcript_events))
    payload = dict(payload)
    payload.setdefault("run_dir", str(run_dir))
    monkeypatch.setattr(bench.rundir, "RUNS_DIR", runs_dir)

    seen = []
    monkeypatch.setattr(bench, "run_once", lambda argv: seen.append(argv) or payload)
    staged = tmp_path / "staged"
    staged.mkdir()
    monkeypatch.setattr(bench, "_stage_repo", lambda task: staged)
    monkeypatch.setattr(bench, "_run_acceptance", lambda *a, **k: "pass")
    monkeypatch.setattr(bench.docker_cli, "run",
                        lambda argv, timeout=None: seen.append(argv) or _FakeCaptured(0))
    return seen


def test_run_one_bench_case_argv_and_row(tmp_path, monkeypatch):
    seen = _fake_run_environment(tmp_path, monkeypatch, payload={
        "status": "completed", "turns": 3,
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        "provider": "openai"})
    row = bench.run_one_bench_case("m1", "sh-fix-script", 0, provider="openai",
                                   base_url="http://127.0.0.1:9/v1", stamp="20260816T000000Z",
                                   max_turns=40, timeout=1800)
    run_argv = seen[0]
    assert run_argv[0] == "run"
    assert run_argv[1] == bench._bench_json("sh-fix-script")["task"]
    assert "--sandbox" in run_argv and run_argv[run_argv.index("--sandbox") + 1] == "docker"
    assert "--keep-volume" in run_argv
    assert run_argv[run_argv.index("--model") + 1] == "m1"
    assert run_argv[run_argv.index("--provider") + 1] == "openai"
    assert run_argv[run_argv.index("--base-url") + 1] == "http://127.0.0.1:9/v1"
    assert run_argv[run_argv.index("--max-turns") + 1] == "40"

    assert row["status"] == "completed"
    assert row["turns"] == 3
    assert row["prompt_tokens"] == 11 and row["completion_tokens"] == 7
    assert row["acceptance"] == "pass"
    assert row["slug"] == "fixtask-0101-abcd"
    assert row["provider"] == "openai"
    assert isinstance(row["wall_s"], float)
    assert any(argv[:2] == ["volume", "rm"] for argv in seen)     # volume removed afterwards


def test_run_one_bench_case_counts_harness_failures(tmp_path, monkeypatch):
    events = [
        {"event": "nudge", "kind": "stall", "turn": 6},
        {"event": "nudge", "kind": "empty", "turn": 7},
        {"event": "nudge", "kind": "truncated", "turn": 8},
        {"event": "nudge", "kind": "text_tool_call", "turn": 9},
        {"event": "guardrail_block", "tool": "bash", "reason": "BLOCKED: nope"},
        {"event": "sandbox_reset", "reason": "timeout"},
        {"event": "run_end", "status": "stalled"},
    ]
    _fake_run_environment(tmp_path, monkeypatch, transcript_events=events, payload={
        "status": "stalled", "turns": 12, "usage": {}, "final_message": ""})
    row = bench.run_one_bench_case("m1", "sh-fix-script", 0, provider=None, base_url=None,
                                   stamp="s", max_turns=40, timeout=1800)
    harness = row["harness"]
    assert harness["nudge_stall"] == 1
    assert harness["nudge_empty"] == 1
    assert harness["nudge_truncated"] == 1
    assert harness["nudge_text_tool_call"] == 1
    assert harness["empty_reply"] == 3       # every non-stall nudge is one empty_reply failure
    assert harness["stalled"] == 1
    assert harness["max_turns"] == 0
    assert harness["sandbox_error"] == 0
    assert row["guardrail_blocks"] == 1
    assert row["sandbox_resets"] == 1
    assert row["acceptance"] == "skipped"    # a non-completed run is never scored


def test_abort_kind_is_parsed_from_the_final_message():
    assert bench._abort_kind("aborted after 3 consecutive bad_args failures") == "bad_args"
    assert bench._abort_kind("aborted after 6 consecutive tool failures") == "mixed"
    assert bench._abort_kind("all done") is None
    assert bench._abort_kind(None) is None


def test_cmd_bench_requires_models(capsys):
    rc = bench.cmd_bench(argparse.Namespace(models=None, provider=None, base_url=None,
                                            repeats=1, tasks=None, out=None,
                                            max_turns=40, timeout=1800))
    assert rc == 2
    assert "--models is required" in capsys.readouterr().err


def test_cmd_bench_writes_one_row_per_model_spec(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_case(model, task, repeat, *, provider, base_url, stamp, max_turns, timeout):
        calls.append((model, task, repeat, provider, base_url))
        return {"stamp": stamp, "model": model, "task": task, "repeat": repeat,
                "provider": provider, "status": "completed", "acceptance": "pass"}

    monkeypatch.setattr(bench, "run_one_bench_case", fake_case)
    out_file = tmp_path / "results.jsonl"
    rc = bench.cmd_bench(argparse.Namespace(models="m1,m2@anthropic", provider=None,
                                            base_url=None, repeats=1, tasks="sh-fix-script",
                                            out=str(out_file), max_turns=40, timeout=1800))
    assert rc == 0
    rows = [json.loads(l) for l in out_file.read_text().splitlines()]
    assert len(rows) == 2
    assert {r["model"] for r in rows} == {"m1", "m2"}
    assert ("m2", "sh-fix-script", 0, "anthropic", None) in calls


def test_cmd_bench_rejects_an_unknown_task(tmp_path, capsys):
    rc = bench.cmd_bench(argparse.Namespace(models="m1", provider=None, base_url=None,
                                            repeats=1, tasks="no-such-task",
                                            out=str(tmp_path / "r.jsonl"),
                                            max_turns=40, timeout=1800))
    assert rc == 2
    assert "no-such-task" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_bench.py -q`
Expected: `ModuleNotFoundError: No module named 'dirtywork.bench'`

- [ ] **Step 3: Write `dirtywork/bench.py`**

```python
"""`dirtywork bench` -- run every (model x task x repeat) through the normal
`dirtywork run` path and score the result (spec SP3 section 5).

Runs from a source checkout: `bench/` is not part of the installed package.

Two containers are involved per case and neither is the worker's own:
the run itself (created by `dirtywork run --sandbox docker --keep-volume`) and,
afterwards, a fresh acceptance container with the run's volume at /work and this
checkout's `bench/repos/<task>/acceptance/` mounted read-only at /acceptance.
The acceptance COMMAND always comes from /acceptance; the worker's own copy under
/work/acceptance is only ever hashed, so tampering with it marks the run `gamed`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import rundir
from .__main__ import run_once
from .runner import FAILURE_KINDS
from .sandbox import docker_args, docker_cli

BENCH_REPOS = Path(__file__).resolve().parent.parent / "bench" / "repos"
BENCH_HOME = Path.home() / ".dirtywork" / "bench"
NUDGE_KINDS = ("stall", "empty", "truncated", "text_tool_call")
ACCEPTANCE_MEMORY = "2g"
ACCEPTANCE_CPUS = "2"
ACCEPTANCE_PIDS = 256
_ABORT_RE = re.compile(r"aborted after \d+ consecutive (\S+) (?:tool )?failures")


def _bench_json(task: str) -> dict:
    return json.loads((BENCH_REPOS / task / "bench.json").read_text())


def available_tasks() -> list:
    if not BENCH_REPOS.is_dir():
        return []
    return sorted(d.name for d in BENCH_REPOS.iterdir() if (d / "bench.json").is_file())


def parse_model_spec(spec: str, default_provider=None, default_base_url=None):
    """`model[@provider][=base_url]` -> (model, provider|None, base_url|None).

    A model name may contain `/`, `-`, `.` and `:` (`qwen/qwen3-coder-next`,
    `mistralai/devstral-small-2-2512`) but never `@` or `=`, so splitting on
    those two characters is unambiguous. Anything omitted falls back to the
    bench-wide `--provider`/`--base-url`, and if those are unset the flag is not
    passed to `dirtywork run` at all -- `run`'s own defaults then apply, so bench
    never has to hardcode a provider name."""
    rest, sep, base_url = spec.partition("=")
    base_url = base_url.strip() if sep else (default_base_url or "")
    head, at, tail = rest.rpartition("@")
    if at:
        model, provider = head.strip(), tail.strip()
    else:
        model, provider = rest.strip(), (default_provider or "")
    return model, (provider or None), (base_url or None)


def _stage_repo(task: str) -> Path:
    """Copy bench/repos/<task> into a uniquely named temp dir and commit it.
    Docker Desktop caches deleted bind-mount source paths (spec SP2 section 8),
    so bench must never reuse a path -- mkdtemp's random suffix guarantees a
    fresh one every call."""
    src = BENCH_REPOS / task
    dest = Path(tempfile.mkdtemp(prefix=f"dwbench-{task}-"))
    shutil.rmtree(dest)                     # mkdtemp created it; copytree needs it absent
    shutil.copytree(src, dest)
    git_id = ["-c", "user.email=bench@dirtywork.local", "-c", "user.name=dirtywork-bench"]
    subprocess.run(["git", "-C", str(dest), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(dest), *git_id, "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(dest), *git_id, "commit", "-q", "-m", "bench fixture"],
                   check=True)
    return dest


def _uid_gid():
    return (os.getuid(), os.getgid()) if os.name == "posix" else (1000, 1000)


def _acceptance_base_argv(volume: str, uid: int, gid: int, extra_mounts=()) -> list:
    """Shared shape of both post-run containers: no network, no capabilities,
    read-only rootfs with a single writable /tmp tmpfs, the run's volume at /work,
    and an explicit PATH (nothing in the image's own launch config is trusted).
    No `-w`: a container-level workdir over the volume is the verified ownership
    bug called out in docker/Dockerfile, so the command cd's instead."""
    argv = [
        "run", "--rm",
        "--network", "none",
        "--user", f"{uid}:{gid}",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--memory", ACCEPTANCE_MEMORY, "--memory-swap", ACCEPTANCE_MEMORY,
        "--cpus", ACCEPTANCE_CPUS,
        "--pids-limit", str(ACCEPTANCE_PIDS),
        "--tmpfs", "/tmp:rw,exec,size=256m,mode=1777",
        "--mount", f"type=volume,src={volume},dst=/work",
        "-e", f"PATH={docker_args.PATH_ENV}",
        "-e", "HOME=/tmp",
        "-e", "TMPDIR=/tmp",
        "-e", "LANG=C.UTF-8",
    ]
    for mount in extra_mounts:
        argv += ["--mount", mount]
    return argv


def _hash_check_argv(volume: str, image_ref: str, uid: int, gid: int, paths) -> list:
    return (_acceptance_base_argv(volume, uid, gid)
            + ["--entrypoint", "/usr/bin/sha256sum", image_ref] + list(paths))


def _acceptance_run_argv(volume: str, image_ref: str, uid: int, gid: int,
                         acceptance_dir: Path, command: str) -> list:
    mounts = [f"type=bind,src={Path(acceptance_dir).resolve()},dst=/acceptance,readonly"]
    return (_acceptance_base_argv(volume, uid, gid, extra_mounts=mounts)
            + ["--entrypoint", "/bin/sh", image_ref, "-c", f"cd /work && {command}"])


def _run_acceptance(task: str, bench_data: dict, volume: str, *, run=docker_cli.run) -> str:
    """'pass' | 'fail' | 'gamed' | 'skipped'. Never raises: a docker failure
    degrades to 'skipped' rather than aborting the whole bench sweep."""
    image = docker_args.DEFAULT_IMAGE
    try:
        image_ref = docker_cli.resolve_image(image, pinned_digest=docker_args.pin_for(image))
    except Exception:
        return "skipped"
    uid, gid = _uid_gid()
    hashes = bench_data["acceptance"]["hashes"]

    try:
        cp = run(_hash_check_argv(volume, image_ref, uid, gid,
                                  [f"/work/{p}" for p in hashes]),
                 timeout=docker_cli.T_EXPORT_STEP)
    except Exception:
        return "skipped"
    if cp.returncode not in (0, 1):
        return "skipped"      # docker itself failed (125/126/127), not the worker's doing
    # rc 1 = sha256sum could not read a harness file (the worker deleted or moved
    # it) -- that is a mismatch and falls through to "gamed" below.
    actual = {}
    for line in cp.output.decode("utf-8", errors="replace").splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            digest, path = parts[0], parts[1].strip().lstrip("*")
            if path.startswith("/work/"):
                path = path[len("/work/"):]
            actual[path] = digest
    for rel_path, expected in hashes.items():
        if actual.get(rel_path) != expected:
            return "gamed"

    try:
        cp = run(_acceptance_run_argv(volume, image_ref, uid, gid,
                                      BENCH_REPOS / task / "acceptance",
                                      bench_data["acceptance"]["command"]),
                 timeout=docker_cli.T_EXPORT_STEP)
    except Exception:
        return "skipped"
    return "pass" if cp.returncode == 0 else "fail"


def _event_counts(run_dir) -> dict:
    """One pass over the transcript: nudges by kind plus the two whole-run
    event counters."""
    counts = {"guardrail_block": 0, "sandbox_reset": 0, "nudge_other": 0}
    counts.update({f"nudge_{kind}": 0 for kind in NUDGE_KINDS})
    if run_dir is None:
        return counts
    transcript_path = Path(run_dir) / "transcript.jsonl"
    if not transcript_path.is_file():
        return counts
    for line in transcript_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        name = event.get("event")
        if name in ("guardrail_block", "sandbox_reset"):
            counts[name] += 1
        elif name == "nudge":
            key = f"nudge_{event.get('kind')}"
            counts[key if key in counts else "nudge_other"] += 1
    return counts


def _abort_kind(final_message):
    """Which FailureTracker abort ended a `model_error` run, read back out of the
    final message the runner produced (`FailureTracker.record`)."""
    if not isinstance(final_message, str):
        return None
    match = _ABORT_RE.search(final_message)
    if match is None:
        return None
    kind = match.group(1)
    if kind in FAILURE_KINDS:
        return kind
    return "mixed" if kind == "tool" else None


def _harness_failures(counts: dict, status, final_message) -> dict:
    """The harness-failure classes the scoreboard reports. `empty_reply` is the
    FailureTracker kind: the runner records exactly one per non-stall nudge."""
    non_stall = sum(counts[f"nudge_{kind}"] for kind in NUDGE_KINDS if kind != "stall")
    failures = {f"nudge_{kind}": counts[f"nudge_{kind}"] for kind in NUDGE_KINDS}
    failures["nudge_other"] = counts["nudge_other"]
    failures["empty_reply"] = non_stall
    for name in ("stalled", "max_turns", "sandbox_error"):
        failures[name] = 1 if status == name else 0
    failures["abort_kind"] = _abort_kind(final_message)
    return failures


def run_one_bench_case(model: str, task: str, repeat: int, *, provider, base_url, stamp,
                       max_turns: int, timeout: int) -> dict:
    bench_data = _bench_json(task)
    repo_dir = _stage_repo(task)
    argv = ["run", bench_data["task"], "--repo", str(repo_dir), "--model", model,
            "--sandbox", "docker", "--keep-volume",
            "--max-turns", str(max_turns), "--timeout", str(timeout)]
    if provider:
        argv += ["--provider", provider]
    if base_url:
        argv += ["--base-url", base_url]

    wall_start = time.monotonic()
    try:
        payload = run_once(argv)
    except Exception as e:
        shutil.rmtree(repo_dir, ignore_errors=True)
        return {"stamp": stamp, "model": model, "task": task, "repeat": repeat,
                "provider": provider, "base_url": base_url, "slug": None, "run_dir": None,
                "status": "bench_error", "error": str(e), "turns": None,
                "prompt_tokens": None, "completion_tokens": None,
                "wall_s": round(time.monotonic() - wall_start, 1),
                "acceptance": "skipped", "guardrail_blocks": 0, "sandbox_resets": 0,
                "diff_stat": None, "harness": {}, "verdict": None, "review_seconds": None}
    wall_s = round(time.monotonic() - wall_start, 1)

    run_dir = Path(payload["run_dir"]) if payload.get("run_dir") else None
    slug = run_dir.name if run_dir is not None else None
    run_json = {}
    if run_dir is not None and run_dir.is_dir():
        try:
            run_json = rundir.read_run_json(run_dir)
        except (OSError, ValueError):
            run_json = {}

    status = payload.get("status")
    volume = run_json.get("volume")
    acceptance = "skipped"
    try:
        if volume and status == "completed":
            acceptance = _run_acceptance(task, bench_data, volume)
        if volume:
            try:
                docker_cli.run(["volume", "rm", volume], timeout=docker_cli.T_LIFECYCLE)
            except Exception:
                pass
    finally:
        shutil.rmtree(repo_dir, ignore_errors=True)

    usage = payload.get("usage") or {}
    counts = _event_counts(run_dir)
    return {
        "stamp": stamp, "model": model, "task": task, "repeat": repeat,
        "provider": payload.get("provider", provider), "base_url": base_url,
        "slug": slug, "run_dir": str(run_dir) if run_dir else None,
        "status": status, "turns": payload.get("turns"), "wall_s": wall_s,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "acceptance": acceptance,
        "guardrail_blocks": counts["guardrail_block"],
        "sandbox_resets": counts["sandbox_reset"],
        "diff_stat": run_json.get("diff_stat"),
        "harness": _harness_failures(counts, status, payload.get("final_message")),
        "verdict": run_json.get("verdict"),
        "review_seconds": run_json.get("review_seconds"),
    }


def cmd_bench(args) -> int:
    if not args.models:
        print("error: --models is required (e.g. --models qwen/qwen3-coder-next,"
              "other-model@anthropic)", file=sys.stderr)
        return 2
    specs = [s.strip() for s in args.models.split(",") if s.strip()]
    tasks = ([t.strip() for t in args.tasks.split(",") if t.strip()] if args.tasks
             else available_tasks())
    if not tasks:
        print(f"error: no bench fixtures found under {BENCH_REPOS}", file=sys.stderr)
        return 2
    unknown = [t for t in tasks if not (BENCH_REPOS / t / "bench.json").is_file()]
    if unknown:
        print(f"error: unknown bench task(s): {', '.join(unknown)}; available: "
              f"{', '.join(available_tasks())}", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out) if args.out else (BENCH_HOME / f"{stamp}.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a", encoding="utf-8") as fh:
        for spec in specs:
            model, provider, base_url = parse_model_spec(spec, args.provider, args.base_url)
            for task in tasks:
                for repeat in range(args.repeats):
                    row = run_one_bench_case(model, task, repeat, provider=provider,
                                             base_url=base_url, stamp=stamp,
                                             max_turns=args.max_turns, timeout=args.timeout)
                    fh.write(json.dumps(row) + "\n")
                    fh.flush()
                    print(f"{model}  {task}  repeat {repeat}: {row.get('status')} / "
                          f"acceptance {row.get('acceptance')}", file=sys.stderr)
    print(f"results: {out_path}")
    return 0


def dispatch(args) -> int:
    """`main()` routes `dirtywork bench ...` here."""
    return cmd_bench(args)
```

- [ ] **Step 4: Add `run_once` and the `bench` parsers to `dirtywork/__main__.py`**

(a) Extend the stdlib import block at the top of the file:

```python
import argparse
import contextlib
import io
import json
import os
import sys
```

(b) Add `run_once` immediately **above** `def main(`:

```python
def run_once(argv: list) -> dict:
    """Run one dirtywork invocation in-process and return its stdout JSON.
    Relies on the machine contract -- exactly one JSON object on stdout after
    preflight -- so `dirtywork bench` can drive many runs without paying for a
    subprocess (and a fresh interpreter) per run. stderr is captured too, so a
    preflight refusal shows up in the raised error rather than vanishing."""
    out_buf, err_buf = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
        rc = main(argv)
    text = out_buf.getvalue()
    if not text.strip():
        raise RuntimeError(f"dirtywork produced no stdout JSON (exit {rc}): "
                           f"{err_buf.getvalue().strip()}")
    return json.loads(text)
```

Import note: `bench.py` binds `run_once` at module level (`from .__main__ import run_once`) so tests can monkeypatch `bench.run_once`. Under `python3 -m dirtywork bench` that makes Python hold two copies of the module (`__main__` and `dirtywork.__main__`); both are stateless apart from constants, so the behaviour is identical, and the installed console script (`dirtywork = "dirtywork.__main__:main"`) imports it once. Do not "fix" this by importing inside the function — that would break the tests' patch point.

(c) Add the parser builder next to `_add_runs_parsers`:

```python
def _add_bench_parsers(sub) -> None:
    """`dirtywork bench ...` (spec SP3 section 5). --provider/--base-url are
    passed straight through to `dirtywork run`; leaving them unset means `run`'s
    own defaults apply, and a per-model override uses the
    `model[@provider][=base_url]` spec syntax."""
    bench_p = sub.add_parser("bench", help="benchmark models against the fixture tasks")
    bench_p.add_argument("--models", default=None,
                         help="comma-separated model[@provider][=base_url] specs")
    bench_p.add_argument("--provider", default=None, help="default provider for every model")
    bench_p.add_argument("--base-url", default=None, help="default base URL for every model")
    bench_p.add_argument("--repeats", type=_positive_int, default=1)
    bench_p.add_argument("--tasks", default=None,
                         help="comma-separated fixture names (default: all of bench/repos)")
    bench_p.add_argument("--out", default=None,
                         help="results JSONL path (default: ~/.dirtywork/bench/<stamp>.jsonl)")
    bench_p.add_argument("--max-turns", type=_positive_int, default=40)
    bench_p.add_argument("--timeout", type=_positive_int, default=1800)
```

(d) Call it in `_parse_args`, next to the `runs` call:

```python
    _add_runs_parsers(sub)
    _add_bench_parsers(sub)
    return parser.parse_args(argv)
```

(e) Add the dispatch branch in `main()`, beside the `runs` one:

```python
    if args.cmd == "bench":
        from . import bench as bench_mod
        return bench_mod.dispatch(args)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_bench.py -q`
Expected: 17 passed

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add dirtywork/bench.py dirtywork/__main__.py tests/test_bench.py
git commit -m "feat: add 'dirtywork bench' with acceptance, gamed detection, and harness-failure counts"
```

---

### Task 15: `bench summarize`

**Files:**
- Modify: `dirtywork/bench.py` (add `cmd_summarize` and its helpers; extend `dispatch`)
- Modify: `dirtywork/__main__.py` (add the `bench summarize` sub-subparser)
- Modify: `tests/test_bench.py` (add `cmd_summarize` tests)

**Interfaces:**
- Consumes: `statistics.median`; `dirtywork.runs.format_table` (the table renderer Task 9 introduced — one renderer for both CLIs); `dirtywork.rundir.{RUNS_DIR, read_run_json}` to join each row's `slug` against the verdict recorded later by `runs verdict`.
- Produces: `dirtywork.bench.cmd_summarize(args) -> int`; CLI: `dirtywork bench summarize <file>`.

The output is two blocks, together covering both spec SP3 section 5 (rates and means per model) and ruling R7 (per model x task detail):

1. a detail table, one line per model x task x repeat, with columns `model, task, rep, status, turns, wall_s, prompt, completion, accept, verdict, review_s, nudges, failures` (`nudges` renders `stall/empty/truncated/text_tool_call`; `failures` lists the classes that fired);
2. a per-model block: runs, completion rate, acceptance rate, gamed count, mean tokens, mean wall seconds, verdict rate and median review seconds where verdicts exist.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bench.py`:

```python
def _result_row(**over):
    row = {"model": "m1", "task": "t", "repeat": 0, "status": "completed",
           "acceptance": "pass", "turns": 4, "wall_s": 2.0,
           "prompt_tokens": 10, "completion_tokens": 5, "slug": "s1",
           "harness": {"nudge_stall": 0, "nudge_empty": 0, "nudge_truncated": 0,
                       "nudge_text_tool_call": 0, "nudge_other": 0, "empty_reply": 0,
                       "stalled": 0, "max_turns": 0, "sandbox_error": 0, "abort_kind": None}}
    row.update(over)
    return row


def test_summarize_prints_detail_table_and_per_model_stats(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bench.rundir, "RUNS_DIR", tmp_path / "runs")
    results = tmp_path / "results.jsonl"
    rows = [
        _result_row(),
        _result_row(repeat=1, acceptance="fail", prompt_tokens=20, completion_tokens=10,
                    wall_s=4.0, slug="s2"),
        _result_row(model="m2", status="stalled", acceptance="skipped", turns=12,
                    wall_s=1.0, prompt_tokens=5, completion_tokens=1, slug="s3",
                    harness={"nudge_stall": 2, "nudge_empty": 1, "nudge_truncated": 0,
                             "nudge_text_tool_call": 0, "nudge_other": 0, "empty_reply": 1,
                             "stalled": 1, "max_turns": 0, "sandbox_error": 0,
                             "abort_kind": None}),
    ]
    results.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    for slug, verdict, review in [("s1", "accept", 30), ("s2", "reject", 90)]:
        run_dir = tmp_path / "runs" / slug
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({"verdict": verdict,
                                                      "review_seconds": review}))

    rc = bench.cmd_summarize(argparse.Namespace(file=str(results)))
    assert rc == 0
    out = capsys.readouterr().out
    # detail table
    assert "MODEL" in out and "NUDGES" in out and "FAILURES" in out
    assert "2/1/0/0" in out            # m2's nudge counts
    assert "stalled" in out
    assert "accept" in out and "reject" in out
    # per-model block
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
    assert "no such file" in capsys.readouterr().err


def test_summarize_ignores_blank_and_malformed_lines(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bench.rundir, "RUNS_DIR", tmp_path / "runs")
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps(_result_row()) + "\n\nnot json\n")
    assert bench.cmd_summarize(argparse.Namespace(file=str(results))) == 0
    assert "runs: 1" in capsys.readouterr().out


def test_dispatch_routes_summarize_and_bench(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bench.rundir, "RUNS_DIR", tmp_path / "runs")
    results = tmp_path / "r.jsonl"
    results.write_text(json.dumps(_result_row(slug=None)) + "\n")
    rc = bench.dispatch(argparse.Namespace(bench_cmd="summarize", file=str(results)))
    assert rc == 0
    monkeypatch.setattr(bench, "cmd_bench", lambda args: 7)
    assert bench.dispatch(argparse.Namespace(bench_cmd=None, models="m1")) == 7
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_bench.py -q -k "summarize or dispatch"`
Expected: `AttributeError: module 'dirtywork.bench' has no attribute 'cmd_summarize'`

- [ ] **Step 3: Write the implementation**

Add `import statistics` to the stdlib block and `from .runs import format_table` to the local imports at the top of `dirtywork/bench.py`, then append:

```python
DETAIL_COLUMNS = ("model", "task", "rep", "status", "turns", "wall_s", "prompt",
                  "completion", "accept", "verdict", "review_s", "nudges", "failures")


def _verdict_for(row: dict) -> tuple:
    """(verdict, review_seconds) for a result row, re-read from run.json at
    summarize time -- the operator usually records a verdict long after the bench
    sweep, so the value stored in the row itself is only a fallback."""
    slug = row.get("slug")
    if slug:
        run_dir = Path(rundir.RUNS_DIR) / slug
        if run_dir.is_dir():
            try:
                data = rundir.read_run_json(run_dir)
                return data.get("verdict"), data.get("review_seconds")
            except (OSError, ValueError):
                pass
    return row.get("verdict"), row.get("review_seconds")


def _failure_cell(harness: dict) -> str:
    parts = [name for name in ("stalled", "max_turns", "sandbox_error") if harness.get(name)]
    if harness.get("empty_reply"):
        parts.append(f"empty_reply={harness['empty_reply']}")
    if harness.get("abort_kind"):
        parts.append(f"abort={harness['abort_kind']}")
    return ",".join(parts) if parts else "-"


def _detail_row(row: dict, verdict, review_seconds) -> dict:
    harness = row.get("harness") or {}
    nudges = "/".join(str(harness.get(f"nudge_{kind}", 0)) for kind in NUDGE_KINDS)
    return {
        "model": row.get("model", "?"), "task": row.get("task", "?"),
        "rep": row.get("repeat", 0), "status": row.get("status", "?"),
        "turns": "-" if row.get("turns") is None else row["turns"],
        "wall_s": "-" if row.get("wall_s") is None else row["wall_s"],
        "prompt": "-" if row.get("prompt_tokens") is None else row["prompt_tokens"],
        "completion": "-" if row.get("completion_tokens") is None else row["completion_tokens"],
        "accept": row.get("acceptance", "-"),
        "verdict": verdict or "-",
        "review_s": "-" if review_seconds is None else review_seconds,
        "nudges": nudges, "failures": _failure_cell(harness),
    }


def _summarize_model(rows: list, verdicts: list, review_seconds: list) -> dict:
    n = len(rows)
    completed = sum(1 for r in rows if r.get("status") == "completed")
    accepted = sum(1 for r in rows if r.get("acceptance") == "pass")
    gamed = sum(1 for r in rows if r.get("acceptance") == "gamed")
    tokens = [(r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0)
              for r in rows if r.get("prompt_tokens") is not None
              or r.get("completion_tokens") is not None]
    walls = [r["wall_s"] for r in rows if isinstance(r.get("wall_s"), (int, float))]
    return {
        "runs": n,
        "completion_rate": completed / n if n else 0.0,
        "acceptance_rate": accepted / n if n else 0.0,
        "gamed": gamed,
        "mean_tokens": (sum(tokens) / len(tokens)) if tokens else None,
        "mean_wall_s": (sum(walls) / len(walls)) if walls else None,
        "verdict_rate": (verdicts.count("accept") / len(verdicts)) if verdicts else None,
        "median_review_seconds": statistics.median(review_seconds) if review_seconds else None,
    }


def cmd_summarize(args) -> int:
    path = Path(args.file)
    if not path.is_file():
        print(f"error: no such file '{path}'", file=sys.stderr)
        return 2
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)

    detail, by_model, verdicts, reviews = [], {}, {}, {}
    for row in rows:
        model = row.get("model", "?")
        verdict, review = _verdict_for(row)
        detail.append(_detail_row(row, verdict, review))
        by_model.setdefault(model, []).append(row)
        if verdict:
            verdicts.setdefault(model, []).append(verdict)
        if isinstance(review, (int, float)):
            reviews.setdefault(model, []).append(review)

    if detail:
        print("nudges: stall/empty/truncated/text_tool_call")
        print(format_table(DETAIL_COLUMNS, detail))
        print()

    for model in sorted(by_model):
        summary = _summarize_model(by_model[model], verdicts.get(model, []),
                                   reviews.get(model, []))
        print(f"model: {model}")
        print(f"  runs: {summary['runs']}")
        print(f"  completion rate: {summary['completion_rate']:.0%}")
        print(f"  acceptance rate: {summary['acceptance_rate']:.0%}")
        print(f"  gamed: {summary['gamed']}")
        print(f"  mean tokens: {summary['mean_tokens']:.1f}" if summary["mean_tokens"] is not None
              else "  mean tokens: n/a")
        print(f"  mean wall_s: {summary['mean_wall_s']:.1f}" if summary["mean_wall_s"] is not None
              else "  mean wall_s: n/a")
        if summary["verdict_rate"] is not None:
            print(f"  verdict rate: {summary['verdict_rate']:.0%}")
            print(f"  median review_seconds: {summary['median_review_seconds']:g}")
        print()
    return 0
```

Replace `dispatch` with the summarize-aware version:

```python
def dispatch(args) -> int:
    """`main()` routes `dirtywork bench ...` here."""
    if getattr(args, "bench_cmd", None) == "summarize":
        return cmd_summarize(args)
    return cmd_bench(args)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_bench.py -q`
Expected: 21 passed

- [ ] **Step 5: Add the `bench summarize` sub-subparser**

In `dirtywork/__main__.py`, append to `_add_bench_parsers`:

```python
    bench_sub = bench_p.add_subparsers(dest="bench_cmd")
    summarize_p = bench_sub.add_parser("summarize", help="summarize a bench results file")
    summarize_p.add_argument("file")
```

(`dest="bench_cmd"` without `required=True` is deliberate: `dirtywork bench --models ...` must stay valid with no sub-subcommand.)

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add dirtywork/bench.py dirtywork/__main__.py tests/test_bench.py
git commit -m "feat: add 'dirtywork bench summarize' with the per model x task scoreboard"
```

---
### Task 16: `--allow-commit` (host mode only)

**Files:**
- Modify: `dirtywork/__main__.py` (`build_system_prompt`, `_add_run_flags`, `_resolve_allow_commit`, `_load_resume_target`, `_write_run_json_start`, `_execute`, `main`)
- Modify: `README.md` (flags list + one bullet)
- Modify: `docs/transcript-schema.md` (one line in the `run.json` section Task 8 created)
- Modify: `tests/test_main.py` (five tests)
- Modify: `tests/test_guardrails_bash.py` (one regression test pinning the assumption this flag rests on)

**Interfaces:**
- Consumes: `dirtywork.__main__.{build_system_prompt, RunContext, PreflightFailure, _add_run_flags, _load_resume_target, _write_run_json_start, _execute}`; `dirtywork.guardrails.check_bash_command`.
- Produces: CLI flag `--allow-commit` on `run` and `resume` (via `_add_run_flags`); `dirtywork.__main__._resolve_allow_commit(args) -> None`; `run.json` key `allow_commit`; `build_system_prompt(display_root, repo_context, *, allow_commit=False)`.

Verified facts this task rests on — check them before writing code, and do not "also fix" anything they contradict:

- **No guardrail blocks `git commit` or `git branch <name>`.** `dirtywork/guardrails.py`'s `_RULES` blocks `git push` (scope `always`, both modes), and — host scope only — `git config`/`remote`/`update-ref`/`gc`/`filter-branch`/`reflog expire|delete`/`worktree add|remove|prune|move`/`branch -d|-D|-m|-M`/`tag -d`. Committing is only ever discouraged by the **system prompt**, so `--allow-commit` is a prompt switch and nothing else. Confirm with:
  `grep -n "commit" dirtywork/guardrails.py` (only the `git push` rule's message text mentions commits).
- **The docker export carries files, not commits.** `dirtywork/sandbox/export.py` builds a tree with `git add -A` + `git write-tree` inside a throwaway `/gitdir` and streams `git archive` through a validator that **refuses any member whose name contains a `.git` component (case-insensitively)** — the worker's commits and refs physically cannot reach the host. README already states this under "Resuming a run". Hence the preflight refusal in docker mode: the flag would change the prompt and then silently throw the resulting history away.
- `_add_run_flags(p, *, resume: bool)` is shared by `run` and `resume`, so a flag added there is inherited by `resume` automatically.
- `_write_run_json_start(run_dir, ctx, args)` already reads run-shaped values straight off `args` (`args.model`, `args.image`), so `allow_commit` needs no `RunContext` field.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_main.py` (it already provides `_host_repo`, `_install_host_harness`, `_read_only_run_json` and `_first_run`; use them so this task stays correct whichever client/provider the harness installs after Task 6):

```python
def test_build_system_prompt_allow_commit_replaces_the_no_commit_rule(tmp_path: Path):
    default = build_system_prompt(tmp_path, None)
    assert "Do not run git commit" in default
    assert "leave all changes uncommitted for review" in default

    allowed = build_system_prompt(tmp_path, None, allow_commit=True)
    assert "Do not run git commit" not in allowed
    assert "small conventional commits" in allowed
    assert "git push" in allowed          # pushing stays forbidden, guardrail and prompt alike
    assert "finish(summary=...)" in allowed


def test_allow_commit_with_docker_sandbox_exits_2_and_creates_nothing(tmp_path, monkeypatch, capsys):
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "docker", "--allow-commit", "t"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--allow-commit requires --sandbox none" in err
    assert "docker export carries files, not commits" in err
    assert not (tmp_path / "runs").exists()
    assert not (repo / ".worktrees").exists()


def test_allow_commit_records_the_flag_and_switches_the_prompt(tmp_path, monkeypatch, capsys):
    from dirtywork.runner import RunResult
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    captured = {}

    def fake_run(self, system_prompt, task):
        captured["prompt"] = system_prompt
        return RunResult("completed", 1, "done", {"prompt_tokens": 0, "completion_tokens": 0})

    monkeypatch.setattr(m.Runner, "run", fake_run)
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "--allow-commit", "t"])
    assert rc == 0
    assert _read_only_run_json(tmp_path)["allow_commit"] is True
    assert "small conventional commits" in captured["prompt"]


def test_without_allow_commit_run_json_records_false(tmp_path, monkeypatch, capsys):
    from dirtywork.runner import RunResult
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    captured = {}

    def fake_run(self, system_prompt, task):
        captured["prompt"] = system_prompt
        return RunResult("completed", 1, "done", {"prompt_tokens": 0, "completion_tokens": 0})

    monkeypatch.setattr(m.Runner, "run", fake_run)
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "t"])
    assert rc == 0
    assert _read_only_run_json(tmp_path)["allow_commit"] is False
    assert "Do not run git commit" in captured["prompt"]


def test_resume_inherits_allow_commit_from_the_prior_run(tmp_path, monkeypatch, capsys):
    # One tool-call response (which repeats) so the first run ends `max_turns`
    # instead of completing on turn 1 -- the shape the shipped resume tests use.
    write_once = [
        {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "w1", "type": "function", "function": {"name": "write_file",
             "arguments": json.dumps({"path": "new.txt", "content": "from run 1\n"})}}]}}],
         "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
    ]
    m = _install_host_harness(monkeypatch, tmp_path, write_once)
    repo = _host_repo(tmp_path)
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "--max-turns", "1",
                 "--allow-commit", "add a file"])
    assert rc == 1                      # max_turns
    first = json.loads(capsys.readouterr().out)
    assert json.loads((Path(first["run_dir"]) / "run.json").read_text())["allow_commit"] is True

    captured = {}
    real_build = m.build_system_prompt

    def spy_build(display_root, repo_context, **kwargs):
        captured.update(kwargs)
        return real_build(display_root, repo_context, **kwargs)

    monkeypatch.setattr(m, "build_system_prompt", spy_build)
    rc = m.main(["resume", Path(first["run_dir"]).name, "--max-turns", "1"])  # no --allow-commit
    second = json.loads(capsys.readouterr().out)
    assert captured["allow_commit"] is True
    assert json.loads((Path(second["run_dir"]) / "run.json").read_text())["allow_commit"] is True
```

Append to `tests/test_guardrails_bash.py` — a regression pin, not a behaviour change: `--allow-commit` is a prompt-only switch **because** no denylist rule stands in the way. If a future rule blocks committing, this test fails loudly instead of the flag silently becoming a lie.

```python
def test_git_commit_is_not_denylisted_in_either_mode(tmp_path):
    # --allow-commit (SP3) is a system-prompt switch only: nothing in the
    # denylist blocks committing, in host or docker mode. Pushing still is.
    for sandboxed in (False, True):
        assert check_bash_command("git add -A && git commit -m 'feat: x'",
                                  tmp_path, sandboxed=sandboxed) is None
        assert check_bash_command("git commit --amend --no-edit",
                                  tmp_path, sandboxed=sandboxed) is None
        assert check_bash_command("git push origin HEAD",
                                  tmp_path, sandboxed=sandboxed) is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_main.py -q -k allow_commit`
Expected: failures — `build_system_prompt() got an unexpected keyword argument 'allow_commit'` and `unrecognized arguments: --allow-commit`. (`tests/test_guardrails_bash.py::test_git_commit_is_not_denylisted_in_either_mode` passes immediately; it documents an existing property.)

- [ ] **Step 3: Switch the system prompt rule**

In `dirtywork/__main__.py`, change `build_system_prompt` to take the flag and swap exactly one rule line. Replace the existing signature and the `prompt = f"""..."""` assignment with:

```python
NO_COMMIT_RULE = "- Do not run git commit or git branch commands; leave all changes uncommitted for review."
COMMIT_RULE = ("- Commit your work in small conventional commits as you go (git add + git commit); "
               "stay on the current branch -- do not create branches and never run git push.")


def build_system_prompt(display_root, repo_context: str | None, *, allow_commit: bool = False) -> str:
    """`display_root` is what the model is told its files live at: the host
    worktree path in host mode, or the fixed in-container mount point
    (DOCKER_WORKDIR) in docker mode -- the model never sees a host path it
    cannot `cd` to (docker.py's `_rel()` rejects absolute paths outside the
    container's own tree).

    `allow_commit` (host mode only, enforced in `_resolve_allow_commit`) swaps
    the leave-it-uncommitted rule for a commit-as-you-go rule. Nothing else
    changes: no guardrail ever blocked `git commit`, and `git push` stays
    blocked by the denylist in both modes."""
    commit_rule = COMMIT_RULE if allow_commit else NO_COMMIT_RULE
    prompt = f"""You are a coding agent. Your files live at {display_root} -- treat it as your working directory for every tool call.
Complete the task, then reply with a plain-text summary of what you changed and what commands you ran.

Rules:
- Use edit_file or write_file for ALL file changes. Never modify files via bash (no sed -i, no echo redirects, no heredocs).
- Paths are relative to {display_root}.
- Explore before editing: use list_dir, grep, and read_file to understand the code first.
- Verify your work: run the repo's tests or build via bash before declaring the task complete.
{commit_rule}
- When the task is complete, call finish(summary=...) with a short summary of what you did and anything left undone. A plain reply with no tool calls also ends the run."""
    if repo_context:
        prompt += f"\n\nRepository conventions (from the repo's own docs):\n\n{repo_context}"
    return prompt
```

- [ ] **Step 4: Add the flag, the preflight refusal, and the resume inheritance**

(a) In `_add_run_flags`, next to the other run-shaping flags:

```python
    p.add_argument("--allow-commit", action="store_true", default=None,
                   help="host mode only: tell the worker to commit its work as it goes "
                        "(resume inherits this from the run it continues)")
```

`default=None` (not `False`) is what makes "not given" distinguishable from "given as off", which is what lets `resume` inherit the prior run's setting.

(b) Add this function next to `_resolve_context_window`:

```python
def _resolve_allow_commit(args) -> None:
    """Normalize `--allow-commit` to a real bool on args and refuse the
    combination that cannot work. The docker export builds a tree with
    `git add -A` and streams `git archive` through a validator that refuses
    every `.git` member (sandbox/export.py) -- a container's commits can never
    reach the host, so honouring the flag there would change the prompt and
    then silently discard the history it produced."""
    if args.allow_commit and args.sandbox == "docker":
        raise PreflightFailure(
            "--allow-commit requires --sandbox none (host mode): docker export "
            "carries files, not commits")
    args.allow_commit = bool(args.allow_commit)
```

(c) In `_load_resume_target`, alongside the existing prior-run defaults (`args.sandbox`, `args.model`, `args.image`):

```python
    if args.allow_commit is None:
        args.allow_commit = bool(prior.get("allow_commit", False))
```

(d) In `main()`, call it right after the repo preflight, before the LLM preflight — so the refusal costs nothing and creates nothing:

```python
        preflight_repo(repo)
        _resolve_allow_commit(args)
        client = _preflight_llm(args)
```

(e) In `_write_run_json_start`, add the field to the dict:

```python
        "allow_commit": bool(args.allow_commit),
```

(f) In `_execute`, pass it to the prompt builder:

```python
        system_prompt = build_system_prompt(display_root,
                                            load_repo_context(ctx.repo, ctx.base_commit),
                                            allow_commit=bool(args.allow_commit))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_main.py tests/test_guardrails_bash.py tests/test_resume.py -q`
Expected: all pass

- [ ] **Step 6: Document the flag**

(a) In `README.md`'s "Machine contract" flags block, add the line directly after the `--sandbox` line:

```
    [--sandbox docker|none]           # default: docker
    [--allow-commit]                  # host mode only; worker commits its own work
```

(b) In the same section, insert this bullet immediately **before** the `- \`--stall-turns N\` (default 12) ...` bullet:

```markdown
- `--allow-commit` (host mode only) — replaces the prompt's "leave all changes
  uncommitted for review" rule with "commit your work in small conventional
  commits as you go", so the run's branch comes back as real history instead of
  a dirty worktree. Rejected in preflight with `--sandbox docker`: the export
  carries files, not commits (its archive can never contain a `.git` entry), so
  a container's commits could not reach the host anyway. `dirtywork resume`
  inherits the setting from the run it continues.
```

(c) In `docs/transcript-schema.md` (created in Task 8), add one line at the end of the `## \`run.json\`` section:

```markdown
`allow_commit` (bool) records whether the run's system prompt told the worker to
commit as it went (`--allow-commit`, host mode only — see the README). A run
that predates the flag has no such key.
```

- [ ] **Step 7: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all green — this is the last task, so the suite is the 585-test baseline plus everything Tasks 1-16 added.

- [ ] **Step 8: Commit**

```bash
git add dirtywork/__main__.py README.md docs/transcript-schema.md tests/test_main.py tests/test_guardrails_bash.py
git commit -m "feat: add --allow-commit (host mode only) so the worker can commit its own work"
```

---

## Self-review: spec coverage (Tasks 9-16)

Tasks 1-8 are covered by part A's table. This one maps every bullet of spec §"Sub-project 3" sections 4 and 5, plus the SP2 pieces these tasks reuse and the one ruling-driven addition, to the task that implements it.

| Spec item | Task(s) |
|---|---|
| §4 `runs list` — slug, status, started, branch, worktree present, container and volume state (from `run.json`, `git worktree list --porcelain`, `docker ps -a --filter label=dirtywork.run`, `docker volume ls --filter label=dirtywork.run`) | Task 9 |
| §4 `runs list` also renders SP2.5's `stalled` status and marks resume chains (`from <slug>` / `by <slug>`) — ruling R6 | Task 9 |
| §4 `runs show <slug> [--diff]` — `run.json` plus a tool-call timeline from the transcript; `--diff` prints `diff.patch` (the container-computed patch; no host git touches worker content) | Task 9 |
| §4 `runs show` prints task/model/provider/turns/resumed_from/resumed_by up front — ruling R6 | Task 9 |
| §4 `runs export <slug>` — re-runs §SP2.7 for a run whose volume still exists; refuses a non-empty worktree | Task 10 |
| §4 `runs clean <slug> \| --all [--keep-transcript] [--force]` — labeled container `docker rm -f`, labeled volume `docker volume rm`, `git worktree remove --force`, `git branch -D`, run dir; refuses a dirty worktree without `--force` | Task 11 |
| §4 `runs clean` obeys the §SP2.3 collision rule (labels `dirtywork.run`/`dirtywork.repo` match, `run.json` owned by the current user, run definitively stale or `--force`) | Task 11 |
| §4 `runs clean` also removes the run's `<worktree>.pre-resume-<slug>` stash, and leaves a worktree/branch shared with a later resume run alone with a note — ruling R6 | Task 11 |
| §4 `runs verdict <slug> accept\|reject\|cleanup [--note] [--review-seconds N]` appending `{verdict, note, verdict_at, review_seconds}` plus the automatic `time_to_verdict_s` | Task 12 |
| §5 `bench/repos/<name>/` — tiny fixture repos, each with `bench.json` (`task`, `acceptance`) and an `acceptance/` harness dir including hashes of the harness files | Task 13 |
| §5 fixtures are `git init`ed into a **uniquely named** temp copy at bench time (Docker Desktop stale-path cache, §SP2.8) | Task 14 (`_stage_repo`) |
| §5 `dirtywork bench --models <m>[,<m>…] [--provider …] [--repeats N] [--tasks …]` runs each (model × task × repeat) through the normal `run` path with `--keep-volume` | Task 14 |
| §5 `--provider`/`--base-url` are passed through per model entry — ruling R7 | Task 14 (`parse_model_spec`, `model[@provider][=base_url]`) |
| §5 acceptance runs in a **fresh** container with the run's volume at `/work` and `acceptance/` mounted read-only at `/acceptance`; acceptance commands never come from the worktree | Task 14 (`_acceptance_run_argv`) + Task 13 (every `command` names `/acceptance/...`) |
| §5 harness files inside `/work` are compared to the recorded hashes; any mismatch marks the run `gamed` | Task 14 (`_hash_check_argv`, `_run_acceptance`) |
| §5 the volume is removed afterwards | Task 14 |
| §5 results append to `~/.dirtywork/bench/<stamp>.jsonl`: model, task, repeat, status, turns, tokens, wall seconds, guardrail blocks, sandbox resets, diff stat, acceptance pass/fail/gamed, run slug, and the verdict/review seconds when the operator later records one | Task 14 (row), Task 15 (verdict join at summarize time) |
| §5 scoreboard also carries the harness-failure classes: nudge counts by kind plus `stalled`, `empty_reply`, `max_turns`, `sandbox_error` — ruling R7 | Task 14 (`_harness_failures`), Task 15 (`nudges`/`failures` columns) |
| §5 `bench summarize <file>` prints completion rate, acceptance rate, mean tokens and latency per model, and verdict rate / median review seconds where verdicts exist | Task 15 |
| SP2 §7 export flow (`export.export_run`) reused, including its keep-the-volume-on-failure retry contract | Task 10 |
| SP2 §3 name-collision rule as actually implemented (label names verified in `docker_args._label_args`) | Task 11 |
| Ruling R8: `--allow-commit`, host-mode-only prompt switch, docker refusal, `run.json` record, resume inheritance, README/schema docs | Task 16 |

**Spec items in sections 4-5 not mapped to any task:** none.

**Deliberate deviations, and why:**

1. *`runs clean` on a run whose worktree was taken over by a resume.* R6 says such a run "removes only the run dir and prints a note". This plan additionally removes that run's **own** container and volume, which are named `dw-<slug>`/`dw-<slug>-work` and pass the same label check as any other cleanup. They can never be the newer run's resources (a resume creates a new slug and a new volume), and leaving them behind would give the operator no CLI path to them at all. Worktree and branch — the actually shared resources — are left untouched, which is what the ruling protects.
2. *The refusal message names `--sandbox none`, not `--sandbox host`.* The shipped CLI has `--sandbox docker|none`; a message telling the operator to pass `--sandbox host` would produce an argparse "invalid choice" error. The wording is otherwise the ruling's: "--allow-commit requires --sandbox none (host mode): docker export carries files, not commits".
3. *R7's "bench.json schema" is implemented as the results-row schema.* A fixture's `bench.json` holds only `task` and `acceptance` (spec §5); turns, tokens, status, verdict and failure classes belong to the per-run rows in `~/.dirtywork/bench/<stamp>.jsonl`, which is where they are recorded.

## Type consistency checklist (Tasks 9-16)

Every name below is used exactly as declared, with the task that defines it and the task(s) that consume it. Names owned by Tasks 1-8 (registry, providers, `Runner`) are in part A's checklist.

| Name | Defined in | Signature as used | Consumed by |
|---|---|---|---|
| `RunsError` | Task 9 (`runs.py`) | `Exception` subclass; every single-run subcommand turns it into exit 2 | Tasks 10, 11, 12 |
| `format_table` | Task 9 (`runs.py`) | `format_table(columns, rows) -> str` (rows are dicts keyed by column name) | Task 9 (`cmd_list`), Task 15 (`cmd_summarize`) |
| `_open_run` | Task 9 (`runs.py`) | `_open_run(slug) -> (Path, dict)`; raises `RunsError` | Tasks 10, 11, 12 |
| `_iter_run_dirs` | Task 9 (`runs.py`) | `_iter_run_dirs(runs_dir) -> Iterator[Path]` | Tasks 9, 11 |
| `cmd_list` / `cmd_show` | Task 9 (`runs.py`) | each `(args) -> int` | `runs.dispatch` |
| `cmd_export` | Task 10 (`runs.py`) | `(args) -> int`; `args` has `slug`, `max_patch_mb`, `keep_volume` | `runs.dispatch` |
| `_export_status_update` | Task 10 (`runs.py`) | `(previous: str, export_status: str) -> str` | `cmd_export` |
| `_uid_gid` | Task 10 (`runs.py`), Task 14 (`bench.py`) | `() -> (int, int)`; POSIX uid/gid, else `(1000, 1000)` | `cmd_export`, `_run_acceptance` |
| `cmd_clean` | Task 11 (`runs.py`) | `(args) -> int`; `args` has `slug`, `all`, `keep_transcript`, `force` | `runs.dispatch` |
| `_clean_one` | Task 11 (`runs.py`) | `(slug, *, keep_transcript, force) -> list[(action, detail)]` | `cmd_clean` |
| `cmd_verdict` | Task 12 (`runs.py`) | `(args) -> int`; `args` has `slug`, `verdict`, `note`, `review_seconds` | `runs.dispatch` |
| `runs.dispatch` | Task 9 (extended in 10, 11, 12) | `dispatch(args) -> int`, keyed on `args.runs_cmd` | `__main__.main` |
| `_add_runs_parsers` | Task 9 (extended in 10, 11, 12) | `_add_runs_parsers(sub) -> None` | `__main__._parse_args` |
| `run_once` | Task 14 (`__main__.py`) | `run_once(argv: list) -> dict` (the stdout JSON payload) | `bench.run_one_bench_case` |
| `BENCH_REPOS` / `BENCH_HOME` | Task 14 (`bench.py`) | `Path`; `<checkout>/bench/repos`, `~/.dirtywork/bench` | Tasks 14, 15 |
| `NUDGE_KINDS` | Task 14 (`bench.py`) | `("stall", "empty", "truncated", "text_tool_call")` — `runner`'s `NUDGES` keys plus the stall nudge | Tasks 14, 15 |
| `parse_model_spec` | Task 14 (`bench.py`) | `parse_model_spec(spec, default_provider=None, default_base_url=None) -> (model, provider\|None, base_url\|None)` | `cmd_bench` |
| `available_tasks` | Task 14 (`bench.py`) | `() -> list[str]` | `cmd_bench` |
| `_stage_repo` | Task 14 (`bench.py`) | `_stage_repo(task) -> Path` (unique temp git repo) | `run_one_bench_case` |
| `_hash_check_argv` / `_acceptance_run_argv` | Task 14 (`bench.py`) | `(volume, image_ref, uid, gid, ...) -> list[str]` (docker argv, no leading "docker") | `_run_acceptance` |
| `_run_acceptance` | Task 14 (`bench.py`) | `_run_acceptance(task, bench_data, volume, *, run=docker_cli.run) -> "pass"\|"fail"\|"gamed"\|"skipped"` | `run_one_bench_case` |
| `_event_counts` / `_harness_failures` / `_abort_kind` | Task 14 (`bench.py`) | `_event_counts(run_dir) -> dict`; `_harness_failures(counts, status, final_message) -> dict`; `_abort_kind(final_message) -> str\|None` | `run_one_bench_case`, Task 15 |
| `run_one_bench_case` | Task 14 (`bench.py`) | `run_one_bench_case(model, task, repeat, *, provider, base_url, stamp, max_turns, timeout) -> dict` | `cmd_bench` |
| `cmd_bench` / `cmd_summarize` | Tasks 14, 15 (`bench.py`) | each `(args) -> int` | `bench.dispatch` |
| `bench.dispatch` | Task 14 (extended in 15) | `dispatch(args) -> int`, keyed on `args.bench_cmd` | `__main__.main` |
| `_add_bench_parsers` | Task 14 (extended in 15) | `_add_bench_parsers(sub) -> None` | `__main__._parse_args` |
| `build_system_prompt` | shipped; extended in Task 16 | `build_system_prompt(display_root, repo_context, *, allow_commit=False) -> str` | `__main__._execute`, `tests/test_main.py` |
| `_resolve_allow_commit` | Task 16 (`__main__.py`) | `_resolve_allow_commit(args) -> None`; raises `PreflightFailure` | `__main__.main` |

Names read from shipped modules and never redefined here: `rundir.{RUNS_DIR, read_run_json, write_run_json}`, `resume.{pid_alive, stash_dir_for, find_stashes}`, `runner.FAILURE_KINDS`, `sandbox.RunArtifacts`, `sandbox.export.export_run`, `sandbox.docker_cli.{run, resolve_image, validate_objects_dir, T_QUERY, T_LIFECYCLE, T_EXPORT_STEP}`, `sandbox.docker_args.{DockerConfig, DEFAULT_IMAGE, PATH_ENV, container_name, volume_name, repo_label, pin_for}`.
