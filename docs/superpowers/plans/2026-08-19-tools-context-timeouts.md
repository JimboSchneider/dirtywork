# 0.9 — `apply_edits`, Context Sizing, Louder Timeouts, Windows Advisory CI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Every code block below is the literal code to write — transcribe it, do not paraphrase it. Where a step shows a **before** block, that text exists verbatim on this branch; match it exactly and replace it with the **after** block. A later task's **before** block is the earlier task's **after** block wherever they touch the same lines, so tasks must be done in order.

**Goal:** Ship dirtywork **0.9.0** — the five harness issues (#20–#24) the SP3 build record produced. Give a small model one call for a brief's numbered edit list (`apply_edits`), make the context window honest (server-reported window, a task-size warning, trimming visible as `trimmed_turns`), make a timed-out command impossible to skate past (one canonical text, an event flag, a nudge, a counter, a scoreboard class), and learn what Windows actually breaks (an advisory CI leg plus an automated per-file table) without claiming support.

**Architecture:** Everything is additive over shipped structure; no new production module, no new dependency, `schema_version` stays 2. Task 1 teaches the existing registry two things it already had the shape for — a `ParamSpec.schema` that `ToolRegistry.schemas()` emits verbatim (the Anthropic adapter already forwards `parameters` as `input_schema`, so both wire renderings come free) and a minimal recursive validator so the nested shape is proved *before* any tool function runs. Task 2 is then one more transform over the read→transform→write path Task 2 of the 0.8 plan already built: `_apply_edits_once` sits beside `_replace_once`/`_insert_once` in `dirtywork/tools.py` and both backends call it through their own `_transform_file`, so the host and the container cannot disagree about ordering, uniqueness, or an error string; the same step puts the write cap in that shared path (one helper, two call sites) so all four in-place tools refuse an oversized result identically. Task 3 turns `trim_messages` into a `(fits, newly_trimmed)` tuple — the only way the runner can report *turns on which trimming happened* — and rides the count out on `RunResult.extra` through the plumbing `stuck_on` already established. Task 4 adds one optional provider hook (`loaded_context_window`), obtained with `getattr` so every existing double and third-party provider stays valid, and finally records the precedence *source* that `_resolve_context_window` has been discarding since SP2.5. Task 5 gives the timeout one canonical string and one predicate (`tools.is_timeout_result`) that the transcript flag, the nudge, the counter and the scoreboard all read, and gives `DockerError` the `timed_out` flag that lets both `bash` and `grep` tell a real timeout from an ordinary docker failure. Task 6 adds a committed stdlib script plus an advisory CI job that can never gate. Task 7 is the release wrap-up.

**Tech Stack:** Python ≥3.9, stdlib only (`json`, `math`, `os`, `sys`, `urllib.parse`, `xml.etree.ElementTree`). Dev-only dependency: pytest. CI: GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-19-tools-context-timeouts-design.md` (v3.1, owner-approved 2026-08-19 12:37 CDT, binding).

---

## Design

Restated from the spec named above. Section numbers below are the spec's.

### §1 — `apply_edits` (issue #20)

**§1.1/§1.2** `apply_edits(path, edits: list[{old, new}]) -> str` applies a batch of exact `old → new` replacements to ONE file, **in order on the running text**: edit *i* sees the text after edits 1…*i−1*. Matching is `str.count(old)` — the same non-overlapping count `edit_file` uses — and each `old` must occur exactly once at its turn; the replacement is `text.replace(old, new, 1)`. **All-or-nothing before the write:** any validation or matching failure writes nothing and returns an `ERROR:` naming the first failure. Success returns `describe_change(path, old_text, new_text, verb=f"Applied {n} edit{'' if n == 1 else 's'} to")`. The empty list is a wire/runtime `bad_args`. Error texts are fixed, 1-based, and reproduced verbatim in Task 2.

**§1.3** `ParamSpec` gains an optional `schema: dict | None`. When set, `ToolRegistry.schemas()` emits it in place of the flat `{"type": …}`, with `ParamSpec.description` merged in as `"description"` exactly as for flat params. `_validate_args` validates such a parameter **recursively** with a minimal built-in validator (`type`, `minItems`, `maxItems`, `items`, `properties`, `required`, `additionalProperties`) whose messages are path-qualified; `additionalProperties: false` is enforced at runtime too, so an edit object with a stray key is rejected rather than dropped (the registry's *top-level* drop-unknown-keys policy is unchanged — it exists because local models attach stray top-level parameters, and nested objects are authored per call). `_coerce_numeric_string` applies at nested scalar leaves as it does at the top level.

**§1.4** `ToolRegistry.execute`'s `max_input_bytes` check becomes a recursive walk over `call_args` counting the UTF-8 length of every `str` **value** — top-level strings (so `path` counts) and strings nested in lists/dicts; dict **keys** and non-string scalars do not count. `APPLY_EDITS_SPEC` sets `max_input_bytes=MAX_APPLY_EDITS_INPUT_BYTES` (2 MiB). No existing built-in sets a cap, so nothing else changes.

**§1.5** The write cap moves into the shared transform path: one helper in `tools.py`, called by BOTH `_transform_file`s, returning `ERROR: result is <n> bytes, over the <MAX_WRITE_BYTES>-byte write limit; nothing was written` — identical for `edit_file`, `insert_before`, `insert_after` and `apply_edits` on both backends. `write_file` keeps its own existing (backend-specific) oversized wording.

**§1.6** Write semantics are **stated, not changed**: "nothing written" covers every failure *before* the write begins. A failure *during* the write can still leave a truncated file — the property `edit_file`/`insert_*`/`write_file` already have. A temp-file/`os.replace` primitive is deferred to its own spec (it re-opens the final-component TOCTOU that `O_NOFOLLOW` closes) and filed as a follow-up.

**§1.7** One transform factory shared by both backends; `Sandbox` Protocol, `HostSandbox` (with `_check_budget()`) and `DockerSandbox` gain `apply_edits`; `APPLY_EDITS_SPEC` goes immediately after `EDIT_FILE_SPEC` in `BUILTIN_SPECS`; `runner._MUTATING_TOOLS` gains it; the system-prompt rule names it; docs enumerate ten tools; `docs/machine-contract.md` gains a Tools subsection; `docs/operating.md` gains a paragraph plus the §1.6 caveat.

### §2 — Context sizing and trimming (issue #21)

**§2.1** After `ctx` is built (one call site valid for both `run` and `resume`), `task_tokens = ceil(len(ctx.task) / CHARS_PER_TOKEN)`; if that exceeds `TASK_WARN_FRACTION` (0.20) of `ctx.context_window`, one advisory line goes to stderr. Nothing is recorded.

**§2.2** `trim_messages(messages, char_budget)` returns `(fits, newly_trimmed)` — results replaced with `TRIM_MARKER` **on this call** only. Its single call site counts a turn when `newly_trimmed > 0`, including the final call that then gives up. `trimmed_turns` rides `RunResult.extra` → every payload, `run_end`, `run.json` (default `0` on the failure paths); `runs show` shows it plain and in Markdown; `bench summarize` reports its mean and pairs it in `--compare`.

**§2.3** `docs/operating.md` gains `## Sizing the context window` with the SP3 numbers, cited; README *Requirements* links to it.

### §3 — Server-reported context window (issue #22)

**§3.1** The `Provider` protocol documents an **optional** `loaded_context_window(model) -> int | None`. `resolve_context_window` obtains it with `getattr(provider, "loaded_context_window", None)`; absent → `None`, raising → `None`. `AnthropicClient` implements it explicitly and returns `None`.

**§3.2** `OpenAICompatClient.loaded_context_window` GETs `<origin of base_url>/api/v0/models` (LM Studio's native endpoint; `/v1/models` carries no context field) with `LOADED_CONTEXT_PROBE_TIMEOUT = 2` through the same `http_json` transport, and accepts only a `data` entry whose `id` equals the model, whose `state` (when present) is `"loaded"`, and whose `loaded_context_length` is a non-bool `int > 0`. Everything else is `None`. Ollama is not probed in 0.9 (documented as a follow-up).

**§3.3/§3.4** Precedence becomes flag > env > **server** (`provider:<name>:server`) > table (`provider:<name>`) > default. The source string — today discarded — is carried on `RunContext.context_window_source` (a required field placed before the first defaulted one), written to `run_start` and `run.json` at start, echoed on every payload and `run_end`, and shown by `runs show`.

### §4 — Louder timeouts (issue #23)

**§4.1** One canonical timeout result on both backends, with **no partial output**; `TIMEOUT_PREFIX` and `is_timeout_result(text)` live in `tools.py` and are the one predicate everything else uses.

**§4.2** `DockerError` gains a keyword-only `timed_out: bool = False` that only `docker_cli.run`'s expired-timeout path sets. `DockerSandbox.bash` renders the canonical text only when `e.timed_out`, otherwise `ERROR: bash failed: {message}`; `DockerSandbox.grep` keeps its own (unchanged) timeout wording for `e.timed_out` and otherwise `ERROR: grep failed: {message}`. Those are the only two methods that turn a `DockerError` into a tool result.

**§4.3** `tool_result` events for `bash` carry `timed_out: true` when the result is a timeout (sparse, additive). A turn with ≥1 timed-out bash result writes exactly ONE `nudge` event of kind `timeout` and merges its text into the next user message via `_join_nudges` — only on turns that continue, exactly like the `stall` nudge. It is **not** a `FailureTracker` event (a timeout carries `failure=None` and resets the streak — unchanged and intended). `timeouts` rides `RunResult.extra` → every payload, `run_end`, `run.json`. `runs show` gets a `"timed out"` outcome class rendered as `[timed out]`; `bench` gains the `timeout` nudge kind, takes `timeouts` from the payload, excludes it from `empty_reply`, and widens the harness cell to a 4-tuple.

**§4.4/§4.5** Docs and tests as enumerated in Task 5.

### §5 — Windows advisory CI leg (issue #24)

A **separate** `windows-unit` job (`windows-latest`, Python 3.13, `continue-on-error: true`) that `gate` does not need, so it can never block. It runs `python -m pytest --junitxml=junit-windows.xml`, then the committed stdlib `tools/junit_summary.py` (per-file pass/fail/error/skip table to stdout and, when `GITHUB_STEP_SUMMARY` is set, appended there), and uploads the XML with `actions/upload-artifact` SHA-pinned to the same commit `publish.yml` already pins. `tests/test_budget.py`'s collection-time `os.getuid()` becomes Windows-safe. Docs say plainly that this is advisory and Windows stays unsupported. **No production code is changed for Windows in 0.9** — the deliverable is the measured failure table.

### §6 — Cross-cutting

Every payload gains `trimmed_turns` (int, default 0), `timeouts` (int, default 0) and `context_window_source` (string); `_emit_result` seeds all three before `payload.update(extra)`; the two manual `run_end` writes and the two failure-path `_update_run_json` calls carry the same three. Transcript gains `tool_result.timed_out` (sparse), nudge kind `timeout`, `run_start.context_window_source`. Ten tools; the wire fixture is regenerated and renamed. `schema_version` stays 2. `DEFAULT_IMAGE` and every doc mention move to `:0.9` with `PINNED_DIGEST = None`; version `0.9.0` in both files.

## Global Constraints

- **Python 3.9 floor.** No `match`, no runtime `X | Y` unions. `X | None` **in annotations** is fine only in modules that already carry `from __future__ import annotations` — every production module this plan touches (`dirtywork/toolspec.py`, `tools.py`, `builtin_tools.py`, `runner.py`, `runs.py`, `bench.py`, `__main__.py`, `sandbox/__init__.py`, `sandbox/host.py`, `sandbox/docker.py`, `sandbox/docker_cli.py`, `sandbox/docker_args.py`, `providers/__init__.py`, `providers/openai_compat.py`, `providers/anthropic.py`) already has it, and so does every test module this plan touches. The one NEW file with source code, `tools/junit_summary.py`, gets the import too.
- **Stdlib only. No new dependencies.** The only new imports in this whole plan are `math` (Task 3, `dirtywork/__main__.py`), `urllib.parse` (Task 4, `dirtywork/providers/openai_compat.py`), `sys` (Task 6, `tests/test_budget.py`), and `os`/`sys`/`xml.etree.ElementTree` in the new `tools/junit_summary.py`.
- **This plan adds no new CLI flag.** The project rule stands regardless: any flag a reviewer adds later must be read with `getattr(args, "<name>", <default>)`, never `args.<name>`, because existing tests build `argparse.Namespace` without new attributes (`tests/test_runs.py` calls `runs.cmd_show(argparse.Namespace(slug="slug1", diff=False))`). The equivalent discipline for the values this plan *does* add is `extra.get("<name>", <default>)` on the CLI side and `data.get("<name>")` in `runs.py`/`bench.py`, used everywhere below.
- **Additive only.** stdout JSON, transcript and `run.json` changes are additive; `schema_version` stays **2**; no existing key is renamed or removed; no existing CLI stdout line is lost. The one existing *string* that changes is the bash timeout result (spec §4.1, a deliberate contract change recorded in the docs).
- **Every existing test stays green after every task.** Run `/usr/bin/python3 -m pytest -q` at the end of each task; the number may only rise from the baseline in *Precondition*. `/usr/bin/python3` is the only interpreter on this machine with pytest installed and is the 3.9 floor — use it for every command in this plan.
- **The expected pass counts below assume exactly the tests this plan writes.** If you write one more assertion inside an existing test the number does not change; if you split a test in two it rises by one. The binding invariant is that the count never falls and nothing fails.
- **New tests go into the existing test modules**, with exactly one exception: Task 6 creates `tests/test_junit_summary.py`, because nothing under `tools/` has a test module today (`tools/ci_sandbox_smoke.py` has none either) and wedging it into an unrelated module would be worse.
- **DRY/SOLID.** Where two call sites need the same block, this plan extracts a helper first and calls it twice — `tools._check_write_size` (both `_transform_file`s), `tools._apply_edits_once` (both backends), `tools.is_timeout_result` (transcript, nudge, counter, `runs`), `__main__._contract_fields` (the normal path and both failure paths).
- **Commit after each task** with the exact message given in the task's final step.
- **Never leave a placeholder.** Every code step below is the actual code; every test step the actual test.

## Precondition

Branch `tools-context-0.9`, dirtywork **0.8.1**, working tree clean, off `main` = `5e41a78`.

**Baseline (measured on this branch with `/usr/bin/python3 -m pytest -q` from the repo root): `926 passed, 1 skipped, 18 deselected in ~37s`.** The 18 deselected are the `docker`/`live` markers excluded by `pyproject.toml`'s default `addopts = "-m 'not live and not docker'"`; the 1 skip is `tests/test_workspace.py:600`, which needs a filesystem that accepts an undecodable filename. Both are normal. A task may only raise 926.

Every name below already exists exactly as written:

- `dirtywork/toolspec.py`: `MISSING` (`:21`), `ParamSpec` (`:24-28`), `Caps` (`:31-49`), `ToolSpec` (`:52-65`), `ToolResult`, `ToolValidationError` (`:81`), `TRANSCRIPT_PREVIEW_CHARS`, `_TYPE_CHECKS` (`:88-93`), `_coerce_numeric_string` (`:96`), `_validate_args` (`:114-140`), `ToolRegistry` (`:143`) with `register`, `spec`, `names`, `schemas` (`:161-182`), `canonical_args`, `transcript_preview`, `execute` (`:227-294`) whose `max_input_bytes` check is at `:242-249`.
- `dirtywork/tools.py`: `MAX_RESULT_CHARS` (`:14`), `MAX_DIFF_LINES`, `MAX_DIFF_CHARS`, `DESCRIBE_DIFF_MAX_LINES`, `MAX_READ_BYTES` (`:27`), `MAX_WRITE_BYTES` (`:28`), `MAX_LIST_ENTRIES`, `_cap` (`:32`), `_open_regular` (`:39`), `_worktree_candidate` (`:82`), `_number_lines`, `_lines_keep_newlines`, `_line_counts`, `describe_change` (`:149`), `describe_write` (`:203`), `insert_text` (`:217`), `_read_text_for_diff`, `read_file`, `write_file` (`:278-312`), `_transform_file` (`:315-357`), `_replace_once` (`:360-372`), `_insert_once` (`:375-388`), `edit_file` (`:391`), `insert_before` (`:396`), `insert_after` (`:401`), `list_dir`, `grep`, `MAX_BASH_CHARS` (`:464`), `MAX_BASH_CAPTURE_BYTES`, `bash` (`:472-494`).
- `dirtywork/builtin_tools.py`: module docstring (`:1`), `TOOL_OUTPUT_CAP` (`:19`), `BASH_OUTPUT_CAP` (`:20`), the nine `_`-prefixed dispatchers (`:23-60`), `EDIT_FILE_SPEC` (`:89-101`), `INSERT_BEFORE_SPEC` (`:103`), `BUILTIN_SPECS` (`:197-198`), `default_registry` (`:201`).
- `dirtywork/runner.py`: `MAX_ASSISTANT_TEXT_CHARS`, `LAST_ARGS_CHARS`, `FINISH_TOOL`, `DEFAULT_WINDOW` (`:30`), `TRIM_MARKER` (`:31`), `CHARS_PER_TOKEN` (`:32`), `BUDGET_FRACTION`, `FailureTracker`, `NUDGES` (`:75-84`), `_join_nudges` (`:86-88`), `classify_text_reply`, `DEFAULT_STALL_TURNS` (`:128`), `DEFAULT_STUCK_REPEATS`, `STUCK_OUTPUT_CHARS`, `DEFAULT_VERIFY_ROUNDS`, `DEFAULT_VERIFY_TIMEOUT`, `VERIFY_OUTPUT_CHARS`, `VERIFY_FEEDBACK`, `STALL_NUDGE` (`:150`), `_MUTATING_TOOLS` (`:153`), `_VOLATILE_RE`, `_bash_fingerprint` (`:167`), `parse_exit_code` (`:179`), `ProgressTracker` (`:196`), `RepeatTracker` (`:246`), `resolve_context_window` (`:306-327`), `_tool_call_arg_chars`, `_total_chars` (`:340`), `trim_messages` (`:349-357`), `RunResult` (`:359-366`), `Runner.__init__` (`:368-401`), `Runner.run` (`:403-700`) with its nested `note_last_tool_result`, `finish` (`:437-458`), `check_progress` (`:460-473`), `run_verify` (`:475-501`), `check_verify` (`:503-529`) and the turn loop at `:531-699`.
- `dirtywork/__main__.py`: `DEFAULT_MODEL` (`:60`), `DOCKER_WORKDIR` (`:61`), `build_system_prompt` (`:64-92`, the file-change rule at `:84`), `_err`, `PreflightFailure`, `RunContext` (`:104-125`), `_positive_int`, `_non_negative_int`, `_preflight_llm`, `_resolve_context_window` (`:166-176`), `_resolve_allow_commit`, `_resolve_branch_from`, `_workspace_new` (`:237-266`), `_docker_preflight`, `_build_sandbox`, `_write_run_json_start` (`:382-411`), `_update_run_json` (`:414-426`), `_emit_result` (`:429-460`), `_final_status`, `_fail_setup` (`:508-528`), `_fail_run` (`:531-573`), `_load_feedback`, `_load_resume_target`, `_workspace_resume` (`:650-674`), `_execute` (`:677-805`), `_add_run_flags` (`:808`), `_parse_args`, `run_once`, `main` (`:974-996`).
- `dirtywork/runs.py`: `SHOW_FIELDS` (`:33-35`), `TASK_PREVIEW_CHARS`, `RunsError`, `format_table`, `_open_run`, `_summary_value` (`:222-240`), `read_transcript_events`, `_tool_result_outcome` (`:267-275`), `_timeline_line`, `MD_HEADER_FIELDS` (`:301-302`), `MD_VERDICT_FIELDS`, `MD_RESULT_FIELDS` (`:304`), `MD_ARGS_CHARS`, `MD_RESULT_CHARS`, `_md_block`, `_md_inline`, `_md_event_lines`, `_md_result` (`:448-490`), `render_markdown`, `cmd_show` (`:544-599`), `dispatch`.
- `dirtywork/bench.py`: `NUDGE_KINDS` (`:46`), `_event_counts` (`:187`), `_abort_kind`, `_harness_failures` (`:227-238`), `run_one_bench_case` (`:240-317`), `DETAIL_COLUMNS` (`:360-361`), `_failure_cell` (`:381-388`), `_detail_row` (`:390-405`), `_mean`, `_numbers`, `_summarize_model` (`:417-456`), `COMPARE_COLUMNS` (`:459-460`), `COMPARE_MODEL_COLUMNS` (`:461-462`), `_aggregate`, `_compare_cell`, `_stat`, `_harness_cell` (`:557-568`), `_harness_counts` (`:570-571`), `_harness_known`, `_paired_counts_cell`, `_compare_rows`, `_compare_model_rows` (`:655-676`), `_print_comparison` (`:678-698`), `cmd_summarize` (`:700-756`).
- `dirtywork/sandbox/__init__.py`: `SandboxError`, `RunArtifacts` (`:15-42`), the `Sandbox` Protocol (`:45-73`) with `edit_file` at `:59`.
- `dirtywork/sandbox/host.py`: `HostSandbox` (`:16`) with `_check_budget` (`:39-42`), `edit_file` (`:52-55`), `insert_before` (`:57-60`), `bash` (`:74-77`).
- `dirtywork/sandbox/docker.py`: the `..tools` import block (`:15-25`), `_rel` (`:45`), `_oversized` (`:72-83`), `DockerSandbox` (`:85`), `_read_raw` (`:388-413`), `write_file` (`:445-455`), `_transform_file` (`:457-473`), `edit_file` (`:475-476`), `insert_before` (`:478-479`), `insert_after` (`:481-482`), `grep` (`:550-576`), `bash` (`:757-784`).
- `dirtywork/sandbox/docker_cli.py`: `T_QUERY`, `DockerError` (`:20-23`), `run` (`:32-41`).
- `dirtywork/sandbox/docker_args.py`: `DEFAULT_IMAGE` (`:8`), `PINNED_DIGEST` (`:22`), `PATH_ENV`.
- `dirtywork/providers/__init__.py`: `PROVIDER_NAMES`, `DEFAULT_BASE_URLS`, `ToolCall`, `ChatResponse`, `Provider` (`:52-62`), `get_provider`, `sanitize_usage`.
- `dirtywork/providers/openai_compat.py`: `DEFAULT_BASE_URL` (`:8`), `CONTEXT_WINDOWS` (`:12-15`), `parse_chat_response`, `OpenAICompatClient` (`:109`) with `list_models` (`:121-132`), `context_window` (`:134-135`), `chat` (`:137-148`).
- `dirtywork/providers/anthropic.py`: `_to_anthropic_tool` (`:38-41`), `AnthropicClient` (`:109`) with `context_window` (`:147-153`).
- `dirtywork/llm.py`: `LLMError`, `LLMTimeout`, `MalformedResponse`, `http_json` (`:40-99`, `payload=None` sends no body and `method` overrides the verb).
- Test helpers: `tests/docker_fakes.py`'s `FakeDocker`/`FakePopen`/`_ok`/`_fail`; `tests/test_docker_sandbox.py`'s `docker`/`started` fixtures and `_TOP_HEADER`; `tests/test_runner.py`'s `FakeProvider`, `parts` fixture, `_resp`, `_call`, `_events`; `tests/test_main.py`'s `_host_repo`, `_install_host_harness`, `_ScriptedClient`, `_read_only_run_json`, `_first_run`, `_resume_responses`, `_DEFAULT_EVIDENCE`; `tests/provider_doubles.py`'s `DictProvider`, `PreflightProvider`, `patch_provider`, `text_body`, `tool_call_body`; `tests/provider_contract.py`'s `RecordingTransport`; `tests/test_provider_openai.py`'s `_client`; `tests/test_provider_anthropic.py`'s `_client`; `tests/test_runs.py`'s `_write_run` and `repo` fixture; `tests/test_bench.py`'s `_result_row` and `_fake_run_environment`; `tests/test_tools_files.py`'s `wt` fixture; `tests/test_sandbox_host.py`'s `wt` fixture.
- The frozen wire fixture `tests/fixtures/tool_schemas_v051.json` is exactly `json.dumps(default_registry().schemas(), indent=2, ensure_ascii=False) + "\n"`.
- `.github/workflows/publish.yml:22` pins `actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7` — Task 6 copies that SHA and comment verbatim.

## File Structure

```
dirtywork/
  toolspec.py              # MODIFIED — Task 1 (ParamSpec.schema, schemas(), _validate_against_schema, _input_bytes)
  tools.py                 # MODIFIED — Task 2 (_apply_edits_once, _check_write_size, apply_edits), Task 5 (TIMEOUT_PREFIX, timeout_result, is_timeout_result, bash)
  builtin_tools.py         # MODIFIED — Task 2 (APPLY_EDITS_SPEC, caps, docstring, BUILTIN_SPECS)
  runner.py                # MODIFIED — Task 3 (trim_messages tuple, trimmed_turns), Task 4 (context_window_source), Task 5 (TIMEOUT_NUDGE, timeouts, timed_out)
  runs.py                  # MODIFIED — Task 3 (SHOW_FIELDS/MD_RESULT_FIELDS), Task 4 (context_window in both views), Task 5 (_tool_result_outcome)
  bench.py                 # MODIFIED — Task 3 (trimmed_turns), Task 5 (NUDGE_KINDS, timeouts, 4-tuple, legends)
  __main__.py              # MODIFIED — Task 2 (system prompt), Task 3 (_warn_task_size, _contract_fields), Task 4 (context_window_source), Task 5 (timeouts)
  sandbox/
    __init__.py            # MODIFIED — Task 2 (Protocol.apply_edits)
    host.py                # MODIFIED — Task 2 (apply_edits)
    docker.py              # MODIFIED — Task 2 (apply_edits, write cap), Task 5 (bash/grep timeout discrimination)
    docker_cli.py          # MODIFIED — Task 5 (DockerError.timed_out)
    docker_args.py         # MODIFIED — Task 7 (:0.9, PINNED_DIGEST)
  providers/
    __init__.py            # MODIFIED — Task 4 (Provider docstring)
    openai_compat.py       # MODIFIED — Task 4 (loaded_context_window)
    anthropic.py           # MODIFIED — Task 4 (loaded_context_window)
  __init__.py              # MODIFIED — Task 7 (0.9.0)
tools/
  junit_summary.py         # NEW — Task 6
.github/workflows/ci.yml   # MODIFIED — Task 6 (windows-unit), Task 7 (:0.9)
pyproject.toml             # MODIFIED — Task 7 (0.9.0)
README.md                  # MODIFIED — Tasks 2, 3, 6, 7
docker/README.md           # MODIFIED — Task 7 (:0.9)
docs/operating.md          # MODIFIED — Tasks 2, 3, 5
docs/security.md           # MODIFIED — Tasks 2, 6, 7
docs/machine-contract.md   # MODIFIED — Tasks 2, 4, 5, 7
docs/transcript-schema.md  # MODIFIED — Tasks 2, 3, 4, 5, 7
tests/
  test_toolspec.py         # MODIFIED — Task 1
  test_builtin_tools.py    # MODIFIED — Tasks 1, 2
  test_tools_files.py      # MODIFIED — Task 2
  test_sandbox_host.py     # MODIFIED — Task 2
  test_docker_sandbox.py   # MODIFIED — Tasks 2, 5
  test_docker_cli.py       # MODIFIED — Task 5
  test_runner.py           # MODIFIED — Tasks 3, 4, 5
  test_main.py             # MODIFIED — Tasks 3, 4, 5
  test_runs.py             # MODIFIED — Tasks 3, 4, 5
  test_bench.py            # MODIFIED — Tasks 3, 5
  test_provider_openai.py  # MODIFIED — Task 4
  test_provider_anthropic.py # MODIFIED — Tasks 1, 4
  test_transcript_schema.py# MODIFIED — Tasks 2, 3, 4, 5
  test_budget.py           # MODIFIED — Task 6
  test_docker_args.py      # MODIFIED — Task 7
  test_junit_summary.py    # NEW — Task 6
  fixtures/tool_schemas_v051.json -> fixtures/tool_schemas.json   # RENAMED — Task 1; REGENERATED — Task 2
```

---

### Task 1: registry foundations — `ParamSpec.schema`, a recursive validator, recursive input accounting (spec §1.3, §1.4)

**Files:**
- Modify: `dirtywork/toolspec.py` (`:24-28` `ParamSpec`; `:114-140` `_validate_args`; `:161-182` `schemas`; `:242-249` the `max_input_bytes` check; new `_validate_against_schema`, `_input_bytes`)
- Modify: `tests/test_toolspec.py` (12 new tests)
- Modify: `tests/test_provider_anthropic.py` (1 new test)
- Rename: `tests/fixtures/tool_schemas_v051.json` → `tests/fixtures/tool_schemas.json` (content unchanged in this task)
- Modify: `tests/test_builtin_tools.py` (`:14` fixture path, `:60-65` test name/comment)

**Interfaces:**
- Consumes: `toolspec._TYPE_CHECKS` (`toolspec.py:88`), `toolspec._coerce_numeric_string` (`toolspec.py:96`), `toolspec.ToolValidationError` (`toolspec.py:81`); `dirtywork.providers.anthropic._to_anthropic_tool` (`anthropic.py:38`).
- Produces:
  - `toolspec.ParamSpec.schema: dict | None = None`
  - `toolspec._validate_against_schema(value, schema: dict, path: str)` — returns the validated (possibly coerced) value; raises `ToolValidationError`
  - `toolspec._input_bytes(value) -> int`
- No tool gains a `schema` in this task, so `default_registry().schemas()` is byte-identical before and after — the fixture is renamed, not regenerated. Task 2 is the first task whose fixture content changes.

- [ ] **Step 1: Write the failing registry tests**

Append to `tests/test_toolspec.py`:

```python
# --- spec §1.3/§1.4: nested parameter schemas, recursive validation, and
# --- recursive input-size accounting. No shipped tool uses these until Task 2.

_EDITS_SCHEMA = {
    "type": "array", "minItems": 1, "maxItems": 100,
    "items": {"type": "object",
              "properties": {"old": {"type": "string"}, "new": {"type": "string"}},
              "required": ["old", "new"], "additionalProperties": False},
}


def _nested_spec(**caps_kwargs):
    """A ToolSpec shaped exactly like Task 2's apply_edits, so these tests pin
    the registry behaviour the real tool will depend on."""
    def _fn(sandbox, path, edits):
        return f"{path}:{len(edits)}"

    return ToolSpec(
        name="apply_edits",
        description="batch edits",
        params={
            "path": ParamSpec(type="string"),
            "edits": ParamSpec(type="array", description="Replacements in order.",
                               schema=_EDITS_SCHEMA),
        },
        required=("path", "edits"),
        fn=_fn,
        caps=Caps(fs="write", **caps_kwargs),
    )


def test_schema_param_renders_the_nested_schema_with_the_description_merged():
    registry = ToolRegistry()
    registry.register(_nested_spec())
    params = registry.schemas()[0]["function"]["parameters"]
    assert params["properties"]["path"] == {"type": "string"}     # flat rendering unchanged
    assert params["properties"]["edits"] == {
        "type": "array", "minItems": 1, "maxItems": 100,
        "items": {"type": "object",
                  "properties": {"old": {"type": "string"}, "new": {"type": "string"}},
                  "required": ["old", "new"], "additionalProperties": False},
        "description": "Replacements in order.",
    }
    # the ParamSpec's own schema dict is never mutated by the merge
    assert "description" not in _EDITS_SCHEMA


@pytest.mark.parametrize("edits,message", [
    ("not-a-list", "edits must be array, got str"),
    ([], "edits must have at least 1 item(s)"),
    ([{"old": "a", "new": "b"}] * 101, "edits must have at most 100 item(s)"),
    (["nope"], "edits[0] must be an object"),
    ([{"old": "a", "new": "b"}, {"new": "b"}], "edits[1] is missing required property 'old'"),
    ([{"old": "a", "new": "b"}, {"old": "a", "new": "b", "note": "x"}],
     "edits[1] has unexpected property 'note'"),
    ([{"old": "a", "new": "b"}, {"old": "a", "new": "b"}, {"old": "a", "new": 3}],
     "edits[2].new must be string, got int"),
])
def test_nested_validation_messages_are_path_qualified_bad_args(edits, message):
    registry = ToolRegistry()
    registry.register(_nested_spec())
    result = registry.execute("apply_edits", {"path": "a.py", "edits": edits},
                              sandbox=None, deadline=None)
    assert result.failure == "bad_args"
    assert result.text == f"ERROR: bad arguments for apply_edits: {message}"


def test_nested_validation_coerces_a_numeric_string_at_a_nested_leaf():
    # Same rule as the top level (_coerce_numeric_string): local models send
    # "5" where the schema says integer, at every depth.
    spec = ToolSpec(
        name="t", description="", params={
            "rows": ParamSpec(type="array", schema={
                "type": "array",
                "items": {"type": "object", "properties": {"n": {"type": "integer"}},
                          "required": ["n"], "additionalProperties": False}}),
        },
        required=("rows",), fn=lambda sandbox, rows: repr(rows),
        caps=Caps(fs="none"))
    registry = ToolRegistry()
    registry.register(spec)
    result = registry.execute("t", {"rows": [{"n": "5"}]}, sandbox=None, deadline=None)
    assert result.kind == "ok"
    assert result.text == "[{'n': 5}]"


def test_nested_validation_passes_a_valid_batch_through_unchanged():
    registry = ToolRegistry()
    registry.register(_nested_spec())
    result = registry.execute("apply_edits",
                              {"path": "a.py", "edits": [{"old": "a", "new": "b"}]},
                              sandbox=None, deadline=None)
    assert result.kind == "ok" and result.text == "a.py:1"


def test_max_input_bytes_counts_nested_strings_and_the_path_but_not_keys():
    registry = ToolRegistry()
    registry.register(_nested_spec(max_input_bytes=20))
    # path "a.py" (4) + old "x"*10 (10) + new "y"*10 (10) = 24 > 20.
    # The keys "old"/"new" (6 more bytes) are deliberately NOT counted.
    result = registry.execute(
        "apply_edits", {"path": "a.py", "edits": [{"old": "x" * 10, "new": "y" * 10}]},
        sandbox=None, deadline=None)
    assert result.failure == "bad_args"
    assert result.text == ("ERROR: bad arguments for apply_edits: input is 24 bytes, "
                           "over the 20-byte limit.")


def test_max_input_bytes_under_the_cap_runs_the_tool():
    registry = ToolRegistry()
    registry.register(_nested_spec(max_input_bytes=20))
    result = registry.execute("apply_edits",
                              {"path": "a.py", "edits": [{"old": "x", "new": "y"}]},
                              sandbox=None, deadline=None)
    assert result.kind == "ok" and result.text == "a.py:1"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_toolspec.py -q -k "nested or max_input_bytes_counts or max_input_bytes_under"`
Expected: 12 failed — every one with `TypeError: __init__() got an unexpected keyword argument 'schema'` raised while building `_nested_spec()` (`ParamSpec` has no `schema` field yet).

- [ ] **Step 3: Give `ParamSpec` an optional schema**

In `dirtywork/toolspec.py`.

Before:

```python
@dataclass(frozen=True)
class ParamSpec:
    type: str
    description: str = ""
    default: Any = MISSING
```

After:

```python
@dataclass(frozen=True)
class ParamSpec:
    """``schema`` (spec §1.3) is the parameter's full JSON schema for a
    parameter whose shape is not a flat scalar -- an array of objects, say.
    When set, ToolRegistry.schemas() emits it INSTEAD of ``{"type": type}``
    (with ``description`` merged in exactly as for a flat param) and
    _validate_args validates against it recursively. ``type`` stays set to the
    schema's top-level type so canonical_args and any other reader that only
    knows about flat types keeps working. Leave it None for a flat param and
    nothing about that param changes."""

    type: str
    description: str = ""
    default: Any = MISSING
    schema: dict | None = None
```

- [ ] **Step 4: Emit the nested schema from `schemas()`**

In `dirtywork/toolspec.py`, `ToolRegistry.schemas`.

Before:

```python
            for pname, pspec in spec.params.items():
                prop = {"type": pspec.type}
                if pspec.description:
                    prop["description"] = pspec.description
                properties[pname] = prop
```

After:

```python
            for pname, pspec in spec.params.items():
                # A schema-bearing param renders its own schema verbatim; the
                # copy matters because `description` is merged in below and the
                # ParamSpec's dict is shared with every future call. The merge
                # runs last for both kinds of param, so `description` is always
                # the final key -- flat and nested render the same way.
                prop = dict(pspec.schema) if pspec.schema is not None else {"type": pspec.type}
                if pspec.description:
                    prop["description"] = pspec.description
                properties[pname] = prop
```

- [ ] **Step 5: Add the recursive validator**

In `dirtywork/toolspec.py`, insert immediately **after** `_coerce_numeric_string` (which ends with `    return None`) and immediately **before** `def _validate_args(spec: ToolSpec, args: dict) -> dict:`.

```python
def _validate_against_schema(value, schema: dict, path: str):
    """Validate `value` against the minimal JSON-Schema subset a ParamSpec.schema
    may use (spec §1.3): type, minItems, maxItems, items, properties, required,
    additionalProperties. Deliberately NOT a JSON-Schema library -- anything else
    in the schema dict is ignored, and the only schemas that reach here are the
    ones this repo authors.

    Returns the validated value, with numeric strings coerced at every scalar
    leaf exactly as the top level coerces them, so a tool function may rely on
    the shape its schema declares. Raises ToolValidationError with a
    path-qualified message ("edits[2].new must be string, got int") that
    ToolRegistry.execute turns into a `bad_args` result.

    additionalProperties: false is enforced HERE too, not just on the wire:
    the registry's drop-unknown-keys policy is a TOP-LEVEL concession to local
    models that attach stray parameters from other harnesses' schemas, and a
    nested object is authored per call, so it gets the strict rule."""
    ptype = schema.get("type")
    if ptype == "array":
        if not isinstance(value, list):
            raise ToolValidationError(f"{path} must be array, got {type(value).__name__}")
        minimum = schema.get("minItems")
        if isinstance(minimum, int) and len(value) < minimum:
            raise ToolValidationError(f"{path} must have at least {minimum} item(s)")
        maximum = schema.get("maxItems")
        if isinstance(maximum, int) and len(value) > maximum:
            raise ToolValidationError(f"{path} must have at most {maximum} item(s)")
        item_schema = schema.get("items")
        if not isinstance(item_schema, dict):
            return value
        return [_validate_against_schema(item, item_schema, f"{path}[{i}]")
                for i, item in enumerate(value)]
    if ptype == "object":
        if not isinstance(value, dict):
            raise ToolValidationError(f"{path} must be an object")
        properties = schema.get("properties") or {}
        for name in schema.get("required") or ():
            if name not in value:
                raise ToolValidationError(f"{path} is missing required property {name!r}")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    raise ToolValidationError(f"{path} has unexpected property {name!r}")
        out = {}
        for name, item in value.items():
            sub = properties.get(name)
            out[name] = (_validate_against_schema(item, sub, f"{path}.{name}")
                         if isinstance(sub, dict) else item)
        return out
    check = _TYPE_CHECKS.get(ptype)
    if check is not None and not check(value):
        coerced = _coerce_numeric_string(ptype, value)
        if coerced is None:
            raise ToolValidationError(f"{path} must be {ptype}, got {type(value).__name__}")
        return coerced
    return value


def _input_bytes(value) -> int:
    """UTF-8 length of every str VALUE inside `value`, recursively (spec §1.4).
    Dict KEYS and non-string scalars do not count: the cap exists to bound the
    text a model sent, and a key is this repo's schema, not the model's."""
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, dict):
        return sum(_input_bytes(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_input_bytes(item) for item in value)
    return 0
```

- [ ] **Step 6: Route schema-bearing parameters through it**

In `dirtywork/toolspec.py`, `_validate_args`.

Before:

```python
            if value is None and pspec.default is None:
                call_args[pname] = None          # the model spelled out the default
                continue
            check = _TYPE_CHECKS.get(pspec.type)
```

After:

```python
            if value is None and pspec.default is None:
                call_args[pname] = None          # the model spelled out the default
                continue
            if pspec.schema is not None:
                # Spec §1.3: the whole shape is proved here, before spec.fn runs,
                # so a tool function never has to re-check nested item shapes.
                call_args[pname] = _validate_against_schema(value, pspec.schema, pname)
                continue
            check = _TYPE_CHECKS.get(pspec.type)
```

- [ ] **Step 7: Make the input-size check recursive**

In `dirtywork/toolspec.py`, `ToolRegistry.execute`.

Before:

```python
        if caps.max_input_bytes is not None:
            total = sum(len(v.encode("utf-8")) for v in call_args.values()
                        if isinstance(v, str))
```

After:

```python
        if caps.max_input_bytes is not None:
            # Spec §1.4: recursive, so a batch tool's nested strings count too.
            # Top-level strings (e.g. `path`) still count exactly as before.
            total = _input_bytes(call_args)
```

- [ ] **Step 8: Run the registry tests**

Run: `/usr/bin/python3 -m pytest tests/test_toolspec.py -q`
Expected: `45 passed` (33 today + 12).

- [ ] **Step 9: Write the failing Anthropic passthrough test**

Spec §1.3: "The Anthropic adapter passes `parameters` through as `input_schema` unchanged, so both wire renderings get it for free." That is a claim about `_to_anthropic_tool`, and it needs a test that would fail if anyone ever started rewriting `parameters`.

Append to `tests/test_provider_anthropic.py`:

```python
def test_nested_parameters_reach_input_schema_unchanged():
    # Spec §1.3: a ParamSpec.schema renders into `function.parameters`, and the
    # Anthropic adapter forwards `parameters` verbatim as `input_schema` -- so
    # both wire renderings carry the nested schema without adapter-side work.
    from dirtywork.providers.anthropic import _to_anthropic_tool
    from dirtywork.toolspec import Caps, ParamSpec, ToolRegistry, ToolSpec

    nested = {"type": "array", "minItems": 1,
              "items": {"type": "object",
                        "properties": {"old": {"type": "string"},
                                       "new": {"type": "string"}},
                        "required": ["old", "new"], "additionalProperties": False}}
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="apply_edits", description="batch edits",
        params={"path": ParamSpec(type="string"),
                "edits": ParamSpec(type="array", schema=nested)},
        required=("path", "edits"), fn=lambda sandbox, path, edits: "",
        caps=Caps(fs="write")))
    tool = _to_anthropic_tool(registry.schemas()[0])
    assert tool["name"] == "apply_edits"
    assert tool["input_schema"]["properties"]["edits"] == nested
    assert tool["input_schema"]["required"] == ["path", "edits"]
```

- [ ] **Step 10: Run it**

Run: `/usr/bin/python3 -m pytest tests/test_provider_anthropic.py -q -k nested_parameters`
Expected: `1 passed` — the adapter already forwards `parameters`; this test exists so a future refactor cannot silently break the nested rendering. (If it fails, `_to_anthropic_tool` was changed and must be restored to `"input_schema": fn["parameters"]`.)

- [ ] **Step 11: Rename the frozen wire fixture**

The fixture has tracked HEAD, not 0.5.1, since 0.8 regenerated it; the name is now a lie (spec §1.3).

```bash
git mv tests/fixtures/tool_schemas_v051.json tests/fixtures/tool_schemas.json
```

In `tests/test_builtin_tools.py`.

Before:

```python
FROZEN_SCHEMAS = Path(__file__).parent / "fixtures" / "tool_schemas_v051.json"
```

After:

```python
FROZEN_SCHEMAS = Path(__file__).parent / "fixtures" / "tool_schemas.json"
```

And the test that reads it.

Before:

```python
def test_schemas_match_the_frozen_v051_wire_contract():
    # The model-facing contract must not drift without a deliberate, matching
    # change to builtin_tools.py AND to this fixture.
    expected = json.loads(FROZEN_SCHEMAS.read_text(encoding="utf-8"))
    assert default_registry().schemas() == expected
```

After:

```python
def test_schemas_match_the_frozen_wire_fixture():
    # The model-facing contract must not drift without a deliberate, matching
    # change to builtin_tools.py AND to this fixture. The fixture tracks HEAD
    # (it was regenerated in 0.8 and again in 0.9), which is why it is no
    # longer named after 0.5.1: regenerate it with
    #   python3 -c "import json; from dirtywork.builtin_tools import default_registry; \
    #     open('tests/fixtures/tool_schemas.json','w').write(\
    #     json.dumps(default_registry().schemas(), indent=2, ensure_ascii=False) + '\n')"
    # and read the diff before committing it.
    expected = json.loads(FROZEN_SCHEMAS.read_text(encoding="utf-8"))
    assert default_registry().schemas() == expected
```

- [ ] **Step 12: Prove the fixture content is untouched**

Run:

```bash
git status --porcelain tests/fixtures/
git diff --cached --stat tests/fixtures/
```
Expected: exactly one line `R  tests/fixtures/tool_schemas_v051.json -> tests/fixtures/tool_schemas.json` with `0 insertions(+), 0 deletions(-)`. No tool declares a `schema` yet, so the wire contract is byte-identical; Task 2 is the first task that changes its content.

- [ ] **Step 13: Run the full suite**

Run: `/usr/bin/python3 -m pytest -q`
Expected: `939 passed, 1 skipped, 18 deselected` (926 + 13).

- [ ] **Step 14: Commit**

```bash
git add dirtywork/toolspec.py tests/test_toolspec.py tests/test_provider_anthropic.py tests/test_builtin_tools.py tests/fixtures/tool_schemas.json
git commit -m "feat(registry): nested parameter schemas, recursive validation and input accounting"
```

---

### Task 2: `apply_edits` — one call for a brief's numbered edit list (spec §1.1, §1.2, §1.5, §1.7, §1.8)

**Files:**
- Modify: `dirtywork/tools.py` (new `_check_write_size`, `_apply_edits_once`, `apply_edits`; `:315-357` `_transform_file`)
- Modify: `dirtywork/sandbox/__init__.py` (`:45-73` Protocol)
- Modify: `dirtywork/sandbox/host.py` (`:52-55` region)
- Modify: `dirtywork/sandbox/docker.py` (`:15-25` import block; `:457-473` `_transform_file`; `:475-482` region)
- Modify: `dirtywork/builtin_tools.py` (`:1` docstring; `:11-12` imports; new `MAX_APPLY_EDITS`/`MAX_APPLY_EDITS_INPUT_BYTES`; new `_apply_edits`; new `APPLY_EDITS_SPEC` after `EDIT_FILE_SPEC`; `:197-198` `BUILTIN_SPECS`)
- Modify: `dirtywork/runner.py` (`:153` `_MUTATING_TOOLS`)
- Modify: `dirtywork/__main__.py` (`:81` system-prompt rule)
- Regenerate: `tests/fixtures/tool_schemas.json`
- Modify: `tests/test_tools_files.py`, `tests/test_sandbox_host.py`, `tests/test_docker_sandbox.py`, `tests/test_builtin_tools.py`, `tests/test_transcript_schema.py`
- Modify: `README.md`, `docs/security.md`, `docs/transcript-schema.md`, `docs/machine-contract.md`, `docs/operating.md`

**Interfaces:**
- Consumes: `tools.describe_change(path, old_text, new_text, *, verb) -> str` (`tools.py:149`); `tools.MAX_WRITE_BYTES` (`tools.py:28`); `tools._transform_file(worktree, path, transform, *, tool)` (`tools.py:315`); `DockerSandbox._transform_file(path, transform)` (`docker.py:453`); `HostSandbox._check_budget()` (`host.py:39`); `toolspec.ParamSpec.schema` (Task 1).
- Produces:
  - `tools._check_write_size(new_text: str) -> str | None`
  - `tools._apply_edits_once(path: str, edits: list) -> Callable[[str], tuple]`
  - `tools.apply_edits(worktree: Path, path: str, edits: list) -> str`
  - `Sandbox.apply_edits(path: str, edits: list) -> str` on the Protocol, `HostSandbox` and `DockerSandbox`
  - `builtin_tools.MAX_APPLY_EDITS = 100`, `builtin_tools.MAX_APPLY_EDITS_INPUT_BYTES = 2 * 1024 * 1024`, `builtin_tools.APPLY_EDITS_SPEC`
- Decision (recorded, spec is silent): both new constants live in `builtin_tools.py` next to `TOOL_OUTPUT_CAP`/`BASH_OUTPUT_CAP`, not in `tools.py`. They are wire/registry caps (`maxItems` and `Caps.max_input_bytes`), not filesystem limits, and `tools.py` never reads them — `MAX_APPLY_EDITS` is enforced entirely by the schema's `maxItems`, which Task 1's validator already honours.
- Decision (spec-exact, looks odd on purpose): the 0-match message renders `(after edits 1..0 are applied)` for the first edit. §1.2 mandates the literal `1..i-1`; do not "fix" it.

- [ ] **Step 1: Write the failing host tool tests**

Append to `tests/test_tools_files.py`:

```python
# --- spec §1.1/§1.2/§1.5: apply_edits and the shared write cap.

def test_apply_edits_applies_in_order(wt: Path):
    (wt / "seq.txt").write_text("alpha\nbeta\n")
    out = tools.apply_edits(wt, "seq.txt", [
        {"old": "alpha", "new": "gamma"},
        {"old": "gamma\nbeta", "new": "gamma\ndelta"},   # only matches after edit 1
    ])
    assert out.startswith("Applied 2 edits to seq.txt: ")
    assert (wt / "seq.txt").read_text() == "gamma\ndelta\n"


def test_apply_edits_singular_verb_for_one_edit(wt: Path):
    out = tools.apply_edits(wt, "src/app.py", [{"old": "return 42", "new": "return 43"}])
    assert out.startswith("Applied 1 edit to src/app.py: ")
    assert "return 43" in (wt / "src" / "app.py").read_text()


def test_apply_edits_rolls_back_when_a_later_edit_does_not_match(wt: Path):
    before = (wt / "src" / "app.py").read_text()
    out = tools.apply_edits(wt, "src/app.py", [
        {"old": "return 42", "new": "return 43"},
        {"old": "not here", "new": "x"},
    ])
    assert out == ("ERROR: edit 2 of 2: old text occurs 0 times in src/app.py; it must "
                   "occur exactly once (after edits 1..1 are applied); no edits applied")
    assert (wt / "src" / "app.py").read_text() == before   # byte-identical: nothing written


def test_apply_edits_rejects_an_empty_old(wt: Path):
    before = (wt / "src" / "app.py").read_text()
    out = tools.apply_edits(wt, "src/app.py", [{"old": "", "new": "x"}])
    assert out == "ERROR: edit 1 of 1: old text is empty; no edits applied"
    assert (wt / "src" / "app.py").read_text() == before


def test_apply_edits_rejects_a_repeated_old(wt: Path):
    (wt / "dup.txt").write_text("aa\naa\n")
    out = tools.apply_edits(wt, "dup.txt", [{"old": "aa", "new": "bb"}])
    assert out == ("ERROR: edit 1 of 1: old text occurs 2 times in dup.txt; it must occur "
                   "exactly once. Include more surrounding context to make it unique; "
                   "no edits applied")
    assert (wt / "dup.txt").read_text() == "aa\naa\n"


def test_apply_edits_result_carries_the_unified_diff(wt: Path):
    out = tools.apply_edits(wt, "src/app.py", [{"old": "return 42", "new": "return 43"}])
    lines = out.splitlines()
    assert lines[0] == "Applied 1 edit to src/app.py: +1 -1 (removed 1 non-blank line)"
    assert "--- a/src/app.py" in out and "+++ b/src/app.py" in out
    assert "-    return 42" in out and "+    return 43" in out


def test_apply_edits_result_over_the_write_cap_is_refused(wt: Path):
    # The file holds "seed\n"; replacing "seed" leaves the trailing newline, so
    # the result is exactly MAX_WRITE_BYTES + 2 bytes.
    (wt / "grow.txt").write_text("seed\n")
    huge = "x" * (tools.MAX_WRITE_BYTES + 1)
    expected = tools.MAX_WRITE_BYTES + 2
    out = tools.apply_edits(wt, "grow.txt", [{"old": "seed", "new": huge}])
    assert out == (f"ERROR: result is {expected} bytes, over the "
                   f"{tools.MAX_WRITE_BYTES}-byte write limit; nothing was written")
    assert (wt / "grow.txt").read_text() == "seed\n"


def test_edit_file_result_over_the_write_cap_is_refused(wt: Path):
    # Spec §1.5: the cap lives in the SHARED transform path, so edit_file gets
    # the identical refusal -- it had none at all before 0.9.
    (wt / "grow.txt").write_text("seed\n")
    huge = "x" * (tools.MAX_WRITE_BYTES + 1)
    expected = tools.MAX_WRITE_BYTES + 2
    out = tools.edit_file(wt, "grow.txt", "seed", huge)
    assert out == (f"ERROR: result is {expected} bytes, over the "
                   f"{tools.MAX_WRITE_BYTES}-byte write limit; nothing was written")
    assert (wt / "grow.txt").read_text() == "seed\n"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_tools_files.py -q -k "apply_edits or over_the_write_cap"`
Expected: 8 failed — the six `apply_edits` tests with `AttributeError: module 'dirtywork.tools' has no attribute 'apply_edits'`, and `test_edit_file_result_over_the_write_cap_is_refused` with an `AssertionError` showing a successful `Edited grow.txt: …` result (today's host transform path has no write cap at all).

- [ ] **Step 3: Add the shared write-cap helper and the transform factory**

In `dirtywork/tools.py`, insert immediately **after** `_insert_once` (which ends with `    return transform`) and immediately **before** `def edit_file(worktree: Path, path: str, old_string: str, new_string: str) -> str:`.

```python
def _check_write_size(new_text: str):
    """Spec §1.5: the one over-the-limit refusal for the shared transform path,
    or None. Both backends' _transform_file call it immediately before the
    write, so edit_file, insert_before, insert_after and apply_edits refuse an
    oversized RESULT with the identical string on the host and in the container.

    write_file keeps its own (backend-specific) oversized wording: it refuses
    the model's own `content` before any read, which is a different event with a
    different fix ("write the file in smaller pieces")."""
    size = len(new_text.encode("utf-8"))
    if size > MAX_WRITE_BYTES:
        return (f"ERROR: result is {size} bytes, over the {MAX_WRITE_BYTES}-byte "
                f"write limit; nothing was written")
    return None


def _apply_edits_once(path: str, edits: list):
    """apply_edits' transform (spec §1.1). Defined here, not in a backend, so
    the host and the container share one ordering rule, one uniqueness rule and
    one set of error strings.

    Every edit is applied IN ORDER on the RUNNING text -- edit i sees the text
    after edits 1..i-1 -- because that is what a brief's numbered list means:
    edit 3 may legitimately depend on what edit 1 produced. Each `old` must
    occur exactly once in the text as it stands at its turn, counted with
    str.count (the same non-overlapping count edit_file uses).

    The first failure refuses the WHOLE batch: the transform returns None as
    its new text, which both _transform_file implementations treat as "refused,
    do not write". Registry validation (spec §1.3) has already proved every
    item is exactly {"old": str, "new": str}, so this never re-checks shapes."""
    total = len(edits)

    def transform(text: str):
        new_text = text
        for index, edit in enumerate(edits, 1):
            old = edit["old"]
            if not old:
                return None, (f"ERROR: edit {index} of {total}: old text is empty; "
                              f"no edits applied")
            count = new_text.count(old)
            if count == 0:
                return None, (
                    f"ERROR: edit {index} of {total}: old text occurs 0 times in {path}; "
                    f"it must occur exactly once (after edits 1..{index - 1} are "
                    f"applied); no edits applied"
                )
            if count > 1:
                return None, (
                    f"ERROR: edit {index} of {total}: old text occurs {count} times in "
                    f"{path}; it must occur exactly once. Include more surrounding "
                    f"context to make it unique; no edits applied"
                )
            new_text = new_text.replace(old, edit["new"], 1)
        verb = f"Applied {total} edit{'' if total == 1 else 's'} to"
        return new_text, describe_change(path, text, new_text, verb=verb)
    return transform
```

- [ ] **Step 4: Enforce the cap in the host transform path**

In `dirtywork/tools.py`, `_transform_file`.

Before:

```python
    new_text, result = transform(text)
    if new_text is None:
        return result
    write_target = _worktree_candidate(path, worktree)
```

After:

```python
    new_text, result = transform(text)
    if new_text is None:
        return result
    too_big = _check_write_size(new_text)
    if too_big:
        return too_big
    write_target = _worktree_candidate(path, worktree)
```

Also update the docstring's list of checks.

Before:

```python
    `transform(text) -> (new_text_or_None, result)`: a None new_text means the
    transform refused and `result` (an 'ERROR: …' string) is returned without
    writing anything. Every check edit_file used to perform itself lives here,
    unchanged: worktree containment, the regular-file/symlink refusals, the
    5 MB read limit, UTF-8 validation, and the O_NOFOLLOW write."""
```

After:

```python
    `transform(text) -> (new_text_or_None, result)`: a None new_text means the
    transform refused and `result` (an 'ERROR: …' string) is returned without
    writing anything. Every check edit_file used to perform itself lives here,
    unchanged: worktree containment, the regular-file/symlink refusals, the
    5 MB read limit, UTF-8 validation, and the O_NOFOLLOW write -- plus, since
    0.9, the shared output cap (_check_write_size, spec §1.5)."""
```

- [ ] **Step 5: Add the host `apply_edits`**

In `dirtywork/tools.py`, after `insert_after`.

Before:

```python
def insert_after(worktree: Path, path: str, anchor: str, text: str) -> str:
    return _transform_file(worktree, path, _insert_once(path, anchor, text, "after"),
                           tool="insert_after")
```

After:

```python
def insert_after(worktree: Path, path: str, anchor: str, text: str) -> str:
    return _transform_file(worktree, path, _insert_once(path, anchor, text, "after"),
                           tool="insert_after")


def apply_edits(worktree: Path, path: str, edits: list) -> str:
    return _transform_file(worktree, path, _apply_edits_once(path, edits),
                           tool="apply_edits")
```

- [ ] **Step 6: Run the host tool tests**

Run: `/usr/bin/python3 -m pytest tests/test_tools_files.py -q`
Expected: `48 passed` (40 today + 8).

- [ ] **Step 7: Write the failing HostSandbox test**

Append to `tests/test_sandbox_host.py`:

```python
def test_host_sandbox_apply_edits(wt: Path):
    sb = HostSandbox(wt)
    sb.start(wt, wt, "slug", "deadbeef")
    (wt / "batch.txt").write_text("one\ntwo\n")
    out = sb.apply_edits("batch.txt", [{"old": "one", "new": "1"},
                                       {"old": "two", "new": "2"}])
    assert out.startswith("Applied 2 edits to batch.txt: ")
    assert (wt / "batch.txt").read_text() == "1\n2\n"
```

- [ ] **Step 8: Run it to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_sandbox_host.py -q -k apply_edits`
Expected: 1 failed — `AttributeError: 'HostSandbox' object has no attribute 'apply_edits'`.

- [ ] **Step 9: Add `apply_edits` to the Protocol and `HostSandbox`**

In `dirtywork/sandbox/__init__.py`, the Protocol's docstring.

Before:

```python
    Tool methods (read_file/write_file/edit_file/insert_before/insert_after/list_dir/grep/bash) may raise BudgetExceeded (worktree over budget) or SandboxError (backend failure); the runner catches both."""
```

After:

```python
    Tool methods (read_file/write_file/edit_file/apply_edits/insert_before/insert_after/list_dir/grep/bash) may raise BudgetExceeded (worktree over budget) or SandboxError (backend failure); the runner catches both."""
```

And the method list.

Before:

```python
    def edit_file(self, path: str, old_string: str, new_string: str) -> str: ...

    def insert_before(self, path: str, anchor: str, text: str) -> str: ...
```

After:

```python
    def edit_file(self, path: str, old_string: str, new_string: str) -> str: ...

    def apply_edits(self, path: str, edits: list) -> str: ...

    def insert_before(self, path: str, anchor: str, text: str) -> str: ...
```

In `dirtywork/sandbox/host.py`.

Before:

```python
    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        result = tools.edit_file(self.worktree, path, old_string, new_string)
        self._check_budget()
        return result
```

After:

```python
    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        result = tools.edit_file(self.worktree, path, old_string, new_string)
        self._check_budget()
        return result

    def apply_edits(self, path: str, edits: list) -> str:
        result = tools.apply_edits(self.worktree, path, edits)
        self._check_budget()
        return result
```

- [ ] **Step 10: Run the host sandbox tests**

Run: `/usr/bin/python3 -m pytest tests/test_sandbox_host.py -q`
Expected: `10 passed` (9 today + 1).

- [ ] **Step 11: Write the failing docker tests**

Append to `tests/test_docker_sandbox.py`:

```python
def test_apply_edits_reads_then_writes(started):
    sb, fake, run_dir = started
    fake.script(["exec"], [_ok(b"one\ntwo\n"), _ok()])
    out = sb.apply_edits("batch.txt", [{"old": "one", "new": "1"},
                                       {"old": "two", "new": "2"}])
    assert out.startswith("Applied 2 edits to batch.txt: ")
    heads = [c for c in fake.calls if "/usr/bin/head" in c[0]]
    writes = [c for c in fake.calls if "cat > \"$1\"" in " ".join(c[0])]
    assert len(heads) == 1 and len(writes) == 1        # one read, one write, per batch
    assert writes[0][2] == b"1\n2\n"                    # the batch's final text


def test_apply_edits_rollback_never_writes(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b"one\ntwo\n"))
    out = sb.apply_edits("batch.txt", [{"old": "one", "new": "1"},
                                       {"old": "nope", "new": "x"}])
    # Byte-identical to the host's text (spec §1.8 parity), and no write exec.
    assert out == ("ERROR: edit 2 of 2: old text occurs 0 times in batch.txt; it must "
                   "occur exactly once (after edits 1..1 are applied); no edits applied")
    assert not [c for c in fake.calls if "cat > \"$1\"" in " ".join(c[0])]


def test_apply_edits_matches_the_host_text_for_success_and_every_refusal(started, tmp_path):
    """Spec §1.8: host/docker parity scoped to matching, success and rollback."""
    from dirtywork import tools
    sb, fake, run_dir = started
    wt = tmp_path / "parity"
    wt.mkdir()
    cases = [
        ("one\ntwo\n", [{"old": "one", "new": "1"}, {"old": "two", "new": "2"}]),
        ("one\ntwo\n", [{"old": "one", "new": "1"}, {"old": "nope", "new": "x"}]),
        ("one\ntwo\n", [{"old": "", "new": "x"}]),
        ("aa\naa\n", [{"old": "aa", "new": "b"}]),
    ]
    for content, edits in cases:
        (wt / "f.txt").write_text(content)
        host_out = tools.apply_edits(wt, "f.txt", edits)
        fake.script(["exec"], [_ok(content.encode("utf-8")), _ok()])
        docker_out = sb.apply_edits("f.txt", edits)
        assert docker_out == host_out


def test_transform_result_over_the_write_cap_is_refused(started):
    from dirtywork.tools import MAX_WRITE_BYTES
    sb, fake, run_dir = started
    huge = "x" * (MAX_WRITE_BYTES + 1)
    expected = (f"ERROR: result is {MAX_WRITE_BYTES + 2} bytes, over the "
                f"{MAX_WRITE_BYTES}-byte write limit; nothing was written")
    fake.script(["exec"], _ok(b"seed\n"))
    assert sb.edit_file("grow.txt", "seed", huge) == expected
    fake.script(["exec"], _ok(b"seed\n"))
    assert sb.apply_edits("grow.txt", [{"old": "seed", "new": huge}]) == expected
    assert not [c for c in fake.calls if "cat > \"$1\"" in " ".join(c[0])]
```

- [ ] **Step 12: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_docker_sandbox.py -q -k "apply_edits or over_the_write_cap"`
Expected: 4 failed — three with `AttributeError: 'DockerSandbox' object has no attribute 'apply_edits'`, and `test_transform_result_over_the_write_cap_is_refused` with an `AssertionError` comparing against docker's current `_write_raw` wording (`ERROR: content is N bytes, over the M-byte write limit`).

- [ ] **Step 13: Add `apply_edits` and the cap to `DockerSandbox`**

In `dirtywork/sandbox/docker.py`, the `..tools` import block.

Before:

```python
from ..tools import (
    MAX_BASH_CHARS,
    MAX_LIST_ENTRIES,
    MAX_READ_BYTES,
    MAX_WRITE_BYTES,
    _cap,
    _insert_once,
    _number_lines,
    _replace_once,
    describe_write,
)
```

After:

```python
from ..tools import (
    MAX_BASH_CHARS,
    MAX_LIST_ENTRIES,
    MAX_READ_BYTES,
    MAX_WRITE_BYTES,
    _apply_edits_once,
    _cap,
    _check_write_size,
    _insert_once,
    _number_lines,
    _replace_once,
    describe_write,
)
```

Then `_transform_file`.

Before:

```python
        text, err = self._read_raw(path, strict=True)
        if err:
            return err
        new_text, result = transform(text)
        if new_text is None:
            return result
        err = self._write_raw(path, new_text.encode("utf-8"))
        if err:
            return err
        return result
```

After:

```python
        text, err = self._read_raw(path, strict=True)
        if err:
            return err
        new_text, result = transform(text)
        if new_text is None:
            return result
        # Spec §1.5: the shared cap fires BEFORE _write_raw's own _oversized
        # check, so all four in-place tools refuse an oversized result with the
        # same string here as on the host. write_file still reaches _oversized
        # with its own wording, which is why that check stays where it is.
        too_big = _check_write_size(new_text)
        if too_big:
            return too_big
        err = self._write_raw(path, new_text.encode("utf-8"))
        if err:
            return err
        return result
```

Then the tool methods.

Before:

```python
    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        return self._transform_file(path, _replace_once(path, old_string, new_string))
```

After:

```python
    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        return self._transform_file(path, _replace_once(path, old_string, new_string))

    def apply_edits(self, path: str, edits: list) -> str:
        return self._transform_file(path, _apply_edits_once(path, edits))
```

- [ ] **Step 14: Run the docker sandbox tests**

Run: `/usr/bin/python3 -m pytest tests/test_docker_sandbox.py -q`
Expected: `86 passed` (82 today + 4).

- [ ] **Step 15: Write the failing registry/dispatch tests**

In `tests/test_builtin_tools.py`, extend `FakeSandbox`.

Before:

```python
    def edit_file(self, path, old_string, new_string):
        self.calls.append(("edit_file", path, old_string, new_string))
        return f"edited:{path}"
```

After:

```python
    def edit_file(self, path, old_string, new_string):
        self.calls.append(("edit_file", path, old_string, new_string))
        return f"edited:{path}"

    def apply_edits(self, path, edits):
        self.calls.append(("apply_edits", path, edits))
        return f"applied:{path}:{len(edits)}"
```

And the name set.

Before:

```python
def test_schemas_shape():
    schemas = default_registry().schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {"read_file", "write_file", "edit_file", "insert_before", "insert_after",
                     "list_dir", "grep", "bash", "finish"}
```

After:

```python
def test_schemas_shape():
    schemas = default_registry().schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {"read_file", "write_file", "edit_file", "apply_edits", "insert_before",
                     "insert_after", "list_dir", "grep", "bash", "finish"}
```

And append:

```python
def test_apply_edits_dispatches_and_declares_its_caps():
    sandbox = FakeSandbox()
    registry = default_registry()
    result = registry.execute(
        "apply_edits", {"path": "a.py", "edits": [{"old": "x", "new": "y"}]},
        sandbox=sandbox, deadline=None)
    assert result.kind == "ok" and result.text == "applied:a.py:1"
    assert sandbox.calls == [("apply_edits", "a.py", [{"old": "x", "new": "y"}])]
    caps = registry.spec("apply_edits").caps
    assert caps.fs == "write"
    assert caps.max_input_bytes == 2 * 1024 * 1024
    assert caps.transcript == "preview"
    # order is significant and documented in BUILTIN_SPECS
    names = registry.names()
    assert names[names.index("edit_file") + 1] == "apply_edits"
```

- [ ] **Step 16: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_builtin_tools.py -q -k "schemas_shape or apply_edits"`
Expected: 2 failed — `test_schemas_shape` with an `AssertionError` on the missing name, and `test_apply_edits_dispatches_and_declares_its_caps` with `AssertionError` on `result.text` (`ERROR: unknown tool 'apply_edits'. Available: …`).

- [ ] **Step 17: Register the tool**

In `dirtywork/builtin_tools.py`, the module docstring.

Before:

```python
"""The nine tools dirtywork ships, declared as ToolSpecs.
```

After:

```python
"""The ten tools dirtywork ships, declared as ToolSpecs.
```

Then the caps block.

Before:

```python
TOOL_OUTPUT_CAP = MAX_RESULT_CHARS + 512
BASH_OUTPUT_CAP = MAX_BASH_CHARS + 512
```

After:

```python
TOOL_OUTPUT_CAP = MAX_RESULT_CHARS + 512
BASH_OUTPUT_CAP = MAX_BASH_CHARS + 512
# Spec §1.1/§1.4: apply_edits' own limits. MAX_APPLY_EDITS is enforced entirely
# by the wire schema's `maxItems` -- the registry's recursive validator honours
# it, so there is no second runtime check to keep in step. The input cap bounds
# `path` plus every `old`/`new` the model sent (the FILE is separately capped at
# tools.MAX_READ_BYTES, 5 MB); it is the only Caps.max_input_bytes any built-in
# sets, so no other tool's behaviour changes.
MAX_APPLY_EDITS = 100
MAX_APPLY_EDITS_INPUT_BYTES = 2 * 1024 * 1024
```

Then the dispatcher.

Before:

```python
def _edit_file(sandbox, path, old_string, new_string):
    return sandbox.edit_file(path, old_string, new_string)
```

After:

```python
def _edit_file(sandbox, path, old_string, new_string):
    return sandbox.edit_file(path, old_string, new_string)


def _apply_edits(sandbox, path, edits):
    return sandbox.apply_edits(path, edits)
```

Then the spec itself, immediately after `EDIT_FILE_SPEC`.

Before:

```python
INSERT_BEFORE_SPEC = ToolSpec(
    name="insert_before",
```

After:

```python
APPLY_EDITS_SPEC = ToolSpec(
    name="apply_edits",
    description="Apply several exact old→new replacements to one file in one "
                "call, in order: every `old` must occur exactly once (in the "
                "file as it stands after the edits before it); if any does "
                "not, nothing is written and the result names the first "
                "failure. Prefer this over a run of edit_file calls when a "
                "brief lists several edits to the same file.",
    params={
        "path": ParamSpec(type="string"),
        "edits": ParamSpec(
            type="array",
            description="Replacements in order; each old must occur exactly once in "
                        "the file as it stands after the previous edits.",
            schema={
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_APPLY_EDITS,
                "items": {
                    "type": "object",
                    "properties": {"old": {"type": "string"},
                                   "new": {"type": "string"}},
                    "required": ["old", "new"],
                    "additionalProperties": False,
                },
            }),
    },
    required=("path", "edits"),
    fn=_apply_edits,
    caps=Caps(fs="write", max_input_bytes=MAX_APPLY_EDITS_INPUT_BYTES,
              max_output_chars=TOOL_OUTPUT_CAP, transcript="preview"),
)

INSERT_BEFORE_SPEC = ToolSpec(
    name="insert_before",
```

Then the registration tuple.

Before:

```python
BUILTIN_SPECS = (READ_FILE_SPEC, WRITE_FILE_SPEC, EDIT_FILE_SPEC, INSERT_BEFORE_SPEC,
                 INSERT_AFTER_SPEC, LIST_DIR_SPEC, GREP_SPEC, BASH_SPEC, FINISH_SPEC)
```

After:

```python
BUILTIN_SPECS = (READ_FILE_SPEC, WRITE_FILE_SPEC, EDIT_FILE_SPEC, APPLY_EDITS_SPEC,
                 INSERT_BEFORE_SPEC, INSERT_AFTER_SPEC, LIST_DIR_SPEC, GREP_SPEC,
                 BASH_SPEC, FINISH_SPEC)
```

- [ ] **Step 18: Regenerate the frozen wire fixture**

Run:

```bash
/usr/bin/python3 -c "import json; from dirtywork.builtin_tools import default_registry; open('tests/fixtures/tool_schemas.json','w').write(json.dumps(default_registry().schemas(), indent=2, ensure_ascii=False) + '\n')"
git diff --stat tests/fixtures/tool_schemas.json
git diff tests/fixtures/tool_schemas.json | grep '^-' | grep -v '^---'
```
Expected: the stat shows insertions only; the third command prints **nothing**. A removed line means an existing tool's schema changed, which this task must not do — investigate before continuing.

- [ ] **Step 19: Run the registry tests**

Run: `/usr/bin/python3 -m pytest tests/test_builtin_tools.py -q`
Expected: `24 passed` (23 today + 1).

- [ ] **Step 20: Teach the runner and the system prompt about the tool**

In `dirtywork/runner.py`.

Before:

```python
_MUTATING_TOOLS = ("write_file", "edit_file", "insert_before", "insert_after")
```

After:

```python
_MUTATING_TOOLS = ("write_file", "edit_file", "apply_edits", "insert_before", "insert_after")
```

In `dirtywork/__main__.py`, `build_system_prompt`.

Before:

```
- Use edit_file, insert_before, insert_after or write_file for ALL file changes. Never modify files via bash (no sed -i, no echo redirects, no heredocs).
```

After:

```
- Use edit_file, apply_edits (several exact replacements in one file at once), insert_before, insert_after or write_file for ALL file changes. Never modify files via bash (no sed -i, no echo redirects, no heredocs).
```

- [ ] **Step 21: Teach the doc test about the tenth tool**

In `tests/test_transcript_schema.py`.

Before:

```python
def test_doc_documents_the_finish_tool_and_the_nine_tools():
    tokens = _doc_tokens()
    for name in ("read_file", "write_file", "edit_file", "insert_before", "insert_after",
                 "list_dir", "grep", "bash", "finish"):
        assert name in tokens, f"tool '{name}' is not documented"
```

After:

```python
def test_doc_documents_the_finish_tool_and_the_ten_tools():
    tokens = _doc_tokens()
    for name in ("read_file", "write_file", "edit_file", "apply_edits", "insert_before",
                 "insert_after", "list_dir", "grep", "bash", "finish"):
        assert name in tokens, f"tool '{name}' is not documented"
```

- [ ] **Step 22: Document the tool — `docs/transcript-schema.md`**

The `tool_result` table's `tool` row.

Before:

```
| `tool` | ✓ | ✓ | string | tool name — one of `read_file`, `write_file`, `edit_file`, `insert_before`, `insert_after`, `list_dir`, `grep`, `bash`, `finish` (`insert_before`/`insert_after` are v2, added in 0.8); `""` for a discarded malformed entry |
```

After:

```
| `tool` | ✓ | ✓ | string | tool name — one of `read_file`, `write_file`, `edit_file`, `apply_edits`, `insert_before`, `insert_after`, `list_dir`, `grep`, `bash`, `finish` (`insert_before`/`insert_after` are v2, added in 0.8; `apply_edits` in 0.9); `""` for a discarded malformed entry |
```

And the `result` row (append one sentence to the existing cell; the rest of the row is unchanged).

Before:

```
| `result` | ✓ | ✓ | string | the tool's result, trimmed per the tool's `Caps.transcript` setting. All built-in tools declare `preview`, which caps the record at 2000 chars; the registry also supports `full` and `none`, unused by any shipped tool. Since 0.8 a successful `edit_file`/`write_file` result is `<Verb> <path>: +A -D [(removed N non-blank lines)]` followed by a unified diff (capped at 40 lines / 3000 chars, then `[diff truncated: N more lines]`); `write_file` on a new file returns `Wrote N bytes to <path> (new file, M lines)` with no diff. When either side of the edit exceeds 20000 lines, the diff itself is never computed (it is quadratic-ish on files with popular repeated lines) — the result is just `<Verb> <path>: <N> lines (diff omitted: file too large)` |
```

After:

```
| `result` | ✓ | ✓ | string | the tool's result, trimmed per the tool's `Caps.transcript` setting. All built-in tools declare `preview`, which caps the record at 2000 chars; the registry also supports `full` and `none`, unused by any shipped tool. Since 0.8 a successful `edit_file`/`write_file` result is `<Verb> <path>: +A -D [(removed N non-blank lines)]` followed by a unified diff (capped at 40 lines / 3000 chars, then `[diff truncated: N more lines]`); `write_file` on a new file returns `Wrote N bytes to <path> (new file, M lines)` with no diff. 0.9's `apply_edits` uses the same shape with the verb `Applied N edits to` (`Applied 1 edit to` for a single edit). When either side of the edit exceeds 20000 lines, the diff itself is never computed (it is quadratic-ish on files with popular repeated lines) — the result is just `<Verb> <path>: <N> lines (diff omitted: file too large)`. An in-place tool whose RESULT would exceed the 5 MB write limit returns `ERROR: result is <n> bytes, over the <limit>-byte write limit; nothing was written` on both backends (0.9) |
```

- [ ] **Step 23: Document the tool — `README.md`**

The Security & trust enumeration.

Before:

```
Every tool call (`read_file`/`write_file`/`edit_file`/`insert_before`/
`insert_after`/`list_dir`/`grep`/`bash`) runs inside a locked-down
container: `--network none` by default,
```

After:

```
Every tool call (`read_file`/`write_file`/`edit_file`/`apply_edits`/
`insert_before`/`insert_after`/`list_dir`/`grep`/`bash`) runs inside a
locked-down container: `--network none` by default,
```

And the "How a run works" step 3.

Before:

```
3. **The loop** — the model gets nine tools (`read_file`, `write_file`,
   `edit_file`, `insert_before`, `insert_after`, `list_dir`, `grep`, `bash`,
   `finish`) via OpenAI function-calling. `insert_before`/`insert_after` add
   whole lines around a unique anchor without touching the anchor's own line
   — the primitive for "add a line here", which `edit_file` could only express
   as a replace. Every successful `edit_file`/`write_file`/`insert_*` result
   echoes a capped unified diff of what actually changed, so a replace that
   silently deleted a line is visible to the worker in the same turn.
```

After:

```
3. **The loop** — the model gets ten tools (`read_file`, `write_file`,
   `edit_file`, `apply_edits`, `insert_before`, `insert_after`, `list_dir`,
   `grep`, `bash`, `finish`) via OpenAI function-calling.
   `insert_before`/`insert_after` add
   whole lines around a unique anchor without touching the anchor's own line
   — the primitive for "add a line here", which `edit_file` could only express
   as a replace. `apply_edits` takes a brief's whole numbered list of exact
   replacements to one file in a single call, applied in order, all-or-nothing:
   if any `old` does not match exactly once at its turn, nothing is written and
   the result names the first failure. Every successful
   `edit_file`/`apply_edits`/`write_file`/`insert_*` result
   echoes a capped unified diff of what actually changed, so a replace that
   silently deleted a line is visible to the worker in the same turn.
```

- [ ] **Step 24: Document the tool — `docs/security.md`**

Before:

```
Every tool call (`read_file`/`write_file`/`edit_file`/`insert_before`/
`insert_after`/`list_dir`/`grep`/`bash`) runs inside a locked-down
container: `--network none` by default,
```

After:

```
Every tool call (`read_file`/`write_file`/`edit_file`/`apply_edits`/
`insert_before`/`insert_after`/`list_dir`/`grep`/`bash`) runs inside a
locked-down container: `--network none` by default,
```

- [ ] **Step 25: Add the Tools subsection to `docs/machine-contract.md`**

Spec §1.7: the machine contract enumerates the flags, the payload and the events but has never listed the tools. Insert the new subsection between the `--allow-commit` bullet and the `**stdout:**` paragraph.

Before:

```
- `--allow-commit` (host mode only) — replaces the prompt's "leave all changes
  uncommitted for review" rule with "commit your work in small conventional
  commits as you go", so the run's branch comes back as real history instead of
  a dirty worktree. Rejected in preflight with `--sandbox docker`: the export
  carries files, not commits (its archive can never contain a `.git` entry), so
  a container's commits could not reach the host anyway. `dirtywork resume`
  inherits the setting from the run it continues.

**stdout:** on any run that gets past preflight, exactly one JSON object is
printed to stdout (nothing else goes to stdout):
```

After:

```
- `--allow-commit` (host mode only) — replaces the prompt's "leave all changes
  uncommitted for review" rule with "commit your work in small conventional
  commits as you go", so the run's branch comes back as real history instead of
  a dirty worktree. Rejected in preflight with `--sandbox docker`: the export
  carries files, not commits (its archive can never contain a `.git` entry), so
  a container's commits could not reach the host anyway. `dirtywork resume`
  inherits the setting from the run it continues.

**Tools:** the worker is advertised exactly ten tools, in this order. They are
not configurable; a run's tool surface is the same in host and docker mode.

- `read_file(path, offset=0, limit=400)` — numbered lines; files over ~5 MB and
  non-regular files are refused.
- `write_file(path, content)` — create or overwrite; parent directories are
  created. The result echoes a capped unified diff (a new file reports its byte
  and line count instead).
- `edit_file(path, old_string, new_string)` — one exact replacement;
  `old_string` must occur exactly once.
- `apply_edits(path, edits)` — several exact replacements to ONE file in one
  call, applied **in order on the running text** (edit *i* sees the text after
  edits 1…*i−1*), each `old` matching exactly once at its turn. All-or-nothing
  before the write: the first failure writes nothing and the result names it
  (`ERROR: edit i of N: …`). At most 100 edits and 2 MiB of argument text per
  call. This is the tool for a brief's numbered edit list.
- `insert_before(path, anchor, text)` / `insert_after(path, anchor, text)` —
  insert whole line(s) around the line holding a unique `anchor`, never
  modifying the anchor's own line.
- `list_dir(path=".")` — entries, directories suffixed `/`.
- `grep(pattern, path=".", glob=None)` — regex search (ripgrep when the image
  has it, `grep -rn` otherwise).
- `bash(command, timeout=120)` — a shell command in the worktree; 600 s
  maximum. A command that hits its timeout returns
  `ERROR: command timed out after <n>s — it did not finish and its result is
  unknown. …` with **no partial output**, the `tool_result` event carries
  `timed_out: true`, and the run's `timeouts` counter rises.
- `finish(summary)` — ends the run.

The four in-place tools (`edit_file`, `apply_edits`, `insert_before`,
`insert_after`) share one read→transform→write path per backend, so they refuse
an oversized result with the same string
(`ERROR: result is <n> bytes, over the <limit>-byte write limit; nothing was
written`) and produce byte-identical success text on the host and in the
container. "Nothing was written" covers every failure **before** the write
begins; a failure *during* the write (I/O error, kill) can still leave a
truncated file — see `docs/operating.md`.

**stdout:** on any run that gets past preflight, exactly one JSON object is
printed to stdout (nothing else goes to stdout):
```

- [ ] **Step 26: Add the operator guidance — `docs/operating.md`**

Insert a new subsection immediately before `#### Verifying a run`.

Before:

```
#### Verifying a run

    dirtywork run --repo ~/repos/someproject --verify 'npm test' "Add a unit test for X"
```

After:

```
#### Editing files

The worker changes files only through tools — `write_file`, `edit_file`,
`apply_edits`, `insert_before`, `insert_after` — never through `bash`. When a
brief lists several exact replacements in one file, say so and expect one
`apply_edits` call rather than a run of `edit_file` calls: the edits are applied
**in order on the running text** (edit 3 may depend on what edit 1 produced),
each `old` must match exactly once at its turn, and the whole batch is
all-or-nothing before the write — the first failure writes nothing and the
result names it. That is one turn instead of five, and one prompt-cache hit
instead of five, which is most of the difference on a small local model.

> **In-place edits are atomic *before* the write, not *through* it.** Every
> refusal — validation, a non-matching `old`, an unreadable or non-UTF-8 file,
> a result over the 5 MB write limit — happens before the file is opened, so
> the file is untouched. Once the write starts, an I/O error or a kill can
> still leave the file truncated; that is true of `edit_file`, `insert_*` and
> `write_file` too and is unchanged in 0.9. The worktree is a scratch branch,
> so the recovery is `git -C <worktree> checkout -- <path>`. A temp-file/rename
> primitive was considered and deferred: done naively it re-opens the
> final-component symlink race that the current `O_NOFOLLOW` write closes.

#### Verifying a run

    dirtywork run --repo ~/repos/someproject --verify 'npm test' "Add a unit test for X"
```

- [ ] **Step 27: Run the full suite**

Run: `/usr/bin/python3 -m pytest -q`
Expected: `953 passed, 1 skipped, 18 deselected` (939 + 14).

- [ ] **Step 28: Commit**

```bash
git add dirtywork/tools.py dirtywork/builtin_tools.py dirtywork/runner.py dirtywork/__main__.py dirtywork/sandbox/__init__.py dirtywork/sandbox/host.py dirtywork/sandbox/docker.py tests/fixtures/tool_schemas.json tests/test_tools_files.py tests/test_sandbox_host.py tests/test_docker_sandbox.py tests/test_builtin_tools.py tests/test_transcript_schema.py README.md docs/security.md docs/machine-contract.md docs/operating.md docs/transcript-schema.md
git commit -m "feat: apply_edits — a brief's whole edit list in one all-or-nothing call"
```

---

### Task 3: context sizing — `trimmed_turns` end to end, plus the task-size warning (spec §2)

**Files:**
- Modify: `dirtywork/runner.py` (`:349-357` `trim_messages`; `:403-421` `run()`'s locals; `:437-443` `finish`; the trim call site at `:540-541`)
- Modify: `dirtywork/__main__.py` (`:1-9` imports; `:60-61` constants; `:429-460` `_emit_result` + new `_contract_fields`; `:508-528` `_fail_setup`; `:531-573` `_fail_run`; `:772-803` the two end-of-run emitters; `:974-996` `main`)
- Modify: `dirtywork/runs.py` (`:33-35` `SHOW_FIELDS`; `:304` `MD_RESULT_FIELDS`)
- Modify: `dirtywork/bench.py` (`:300-317` the row; `:417-456` `_summarize_model`; `:461-462` `COMPARE_MODEL_COLUMNS`; `:655-676` `_compare_model_rows`; `:700-756` `cmd_summarize`)
- Modify: `tests/test_runner.py`, `tests/test_main.py`, `tests/test_runs.py`, `tests/test_bench.py`, `tests/test_transcript_schema.py`
- Modify: `README.md`, `docs/operating.md`, `docs/transcript-schema.md`

**Interfaces:**
- Consumes: `runner.TRIM_MARKER` (`runner.py:31`), `runner._total_chars` (`runner.py:340`), `runner.CHARS_PER_TOKEN` (`runner.py:32`), `__main__.RunContext.task`/`.context_window` (`__main__.py:104-125`), `bench._mean`/`_numbers` (`bench.py:409-415`), `bench._compare_cell` (`bench.py:544`), `runs._summary_value` (`runs.py:222`).
- Produces:
  - `runner.trim_messages(messages, char_budget) -> tuple` — `(fits: bool, newly_trimmed: int)` (**signature change**; one production call site, three unit tests)
  - `RunResult.extra["trimmed_turns"]` — `int`, present on every runner-returned result
  - `__main__.TASK_WARN_FRACTION = 0.20`, `__main__._warn_task_size(ctx) -> None`
  - `__main__._contract_fields(extra: dict, ctx: RunContext) -> dict` — the 0.9 contract fields for every payload/`run_end`/`run.json` writer (Tasks 4 and 5 add their keys to this one dict; it is written once, here)
  - `bench` row key `trimmed_turns`; `_summarize_model` key `mean_trimmed_turns`
- Task 4 and Task 5 both edit `_contract_fields`, `_emit_result`'s seed dict and `Runner.run`'s `finish`/locals; the **after** blocks below are their **before** blocks.

- [ ] **Step 1: Rewrite the three `trim_messages` tests and add the counting tests**

The three existing tests are rewritten in place because the return type changes: `assert fits` would pass vacuously against a tuple and `is False` would fail outright.

In `tests/test_runner.py`.

Before:

```python
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
    msgs = [
        {"role": "assistant", "content": None,
         "tool_calls": [ToolCall(id="1", name="write_file", arguments=None,
                                 error=None, raw_arguments="a" * 1000)]},
    ]
    # No role=="tool" messages exist to trim, so this only passes if the
    # tool_call arguments are counted toward the budget in the first place.
    assert trim_messages(msgs, char_budget=500) is False
```

After:

```python
def test_trim_messages():
    msgs = [
        {"role": "system", "content": "s" * 100},
        {"role": "tool", "tool_call_id": "1", "content": "x" * 1000},
        {"role": "assistant", "content": "a" * 100},
        {"role": "tool", "tool_call_id": "2", "content": "y" * 1000},
    ]
    fits, newly_trimmed = trim_messages(msgs, char_budget=1300)
    assert fits is True
    assert newly_trimmed == 1                      # spec §2.2: replaced ON THIS CALL
    assert msgs[1]["content"] == TRIM_MARKER      # oldest trimmed first
    assert msgs[3]["content"] == "y" * 1000        # newer kept
    assert msgs[0]["content"] == "s" * 100         # system never trimmed


def test_trim_does_not_recount_a_result_it_already_trimmed():
    # The count is what makes `trimmed_turns` mean "turns on which trimming
    # happened" rather than "markers currently in the history".
    msgs = [
        {"role": "system", "content": "s" * 100},
        {"role": "tool", "tool_call_id": "1", "content": "x" * 1000},
        {"role": "assistant", "content": "a" * 100},
        {"role": "tool", "tool_call_id": "2", "content": "y" * 1000},
    ]
    assert trim_messages(msgs, char_budget=1300) == (True, 1)
    assert trim_messages(msgs, char_budget=1300) == (True, 0)


def test_trim_cannot_fit():
    msgs = [{"role": "system", "content": "s" * 5000}]
    assert trim_messages(msgs, char_budget=100) == (False, 0)


def test_trim_counts_tool_call_arguments():
    msgs = [
        {"role": "assistant", "content": None,
         "tool_calls": [ToolCall(id="1", name="write_file", arguments=None,
                                 error=None, raw_arguments="a" * 1000)]},
    ]
    # No role=="tool" messages exist to trim, so this only passes if the
    # tool_call arguments are counted toward the budget in the first place.
    assert trim_messages(msgs, char_budget=500) == (False, 0)
```

Then append the two runner-level counting tests:

```python
def _scripted_trim(monkeypatch, script):
    """Drive Runner.run's trim bookkeeping with a scripted trim_messages, so
    the counting rule is tested without also re-testing the trim arithmetic
    (which the four unit tests above already pin). The runner looks the name up
    on the module at call time, so patching the module attribute is enough."""
    import dirtywork.runner as runner_mod
    steps = iter(script)
    monkeypatch.setattr(runner_mod, "trim_messages",
                        lambda messages, char_budget: next(steps))


def test_trimmed_turns_counts_the_final_failing_call_when_it_trimmed(parts, monkeypatch):
    wt, registry, sandbox, transcript, tmp = parts
    _scripted_trim(monkeypatch, [(True, 0), (True, 2), (True, 1), (False, 3)])
    provider = FakeProvider([_resp(tool_calls=[_call(f"c{i}", "read_file", {"path": "f.txt"})])
                             for i in range(3)])
    r = Runner(provider, registry, sandbox, transcript, model="m", max_turns=10)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "context_exhausted"
    # turns 2 and 3 trimmed, and so did the call that then gave up
    assert result.extra["trimmed_turns"] == 3
    end = [e for e in _events(tmp) if e["event"] == "run_end"][-1]
    assert end["trimmed_turns"] == 3


def test_trimmed_turns_ignores_a_final_failing_call_that_trimmed_nothing(parts, monkeypatch):
    wt, registry, sandbox, transcript, tmp = parts
    _scripted_trim(monkeypatch, [(True, 0), (True, 1), (True, 1), (False, 0)])
    provider = FakeProvider([_resp(tool_calls=[_call(f"c{i}", "read_file", {"path": "f.txt"})])
                             for i in range(3)])
    r = Runner(provider, registry, sandbox, transcript, model="m", max_turns=10)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "context_exhausted"
    assert result.extra["trimmed_turns"] == 2


def test_trimmed_turns_is_zero_on_an_ordinary_run(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="all done")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert result.extra["trimmed_turns"] == 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_runner.py -q -k "trim"`
Expected: 6 failed — `test_trim_messages` with `TypeError: cannot unpack non-sequence bool`, `test_trim_does_not_recount…`/`test_trim_cannot_fit`/`test_trim_counts_tool_call_arguments` with `AssertionError` comparing a bool to a tuple, and the three `trimmed_turns` tests with `KeyError: 'trimmed_turns'`.

- [ ] **Step 3: Make `trim_messages` report what it trimmed**

In `dirtywork/runner.py`.

Before:

```python
def trim_messages(messages: list, char_budget: int) -> bool:
    """Replace oldest tool results with TRIM_MARKER until under budget."""
    for m in messages:
        if _total_chars(messages) <= char_budget:
            return True
        if m.get("role") == "tool" and m.get("content") != TRIM_MARKER:
            m["content"] = TRIM_MARKER
    return _total_chars(messages) <= char_budget
```

After:

```python
def trim_messages(messages: list, char_budget: int) -> tuple:
    """Replace oldest tool results with TRIM_MARKER until under budget.

    Returns (fits, newly_trimmed) (spec §2.2). `newly_trimmed` counts only the
    results replaced ON THIS CALL -- a result already holding the marker is
    never counted twice -- which is what lets the runner report `trimmed_turns`
    as "turns on which trimming happened" instead of "markers in the history".
    Trimming is destructive and cumulative, so a running total of markers would
    say the same thing on every later turn and mean nothing."""
    newly_trimmed = 0
    for m in messages:
        if _total_chars(messages) <= char_budget:
            return True, newly_trimmed
        if m.get("role") == "tool" and m.get("content") != TRIM_MARKER:
            m["content"] = TRIM_MARKER
            newly_trimmed += 1
    return _total_chars(messages) <= char_budget, newly_trimmed
```

- [ ] **Step 4: Count the turns and carry the number out**

In `dirtywork/runner.py`, `Runner.run`'s locals.

Before:

```python
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        turns = 0
        failures = FailureTracker()
```

After:

```python
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        turns = 0
        trimmed_turns = 0       # spec §2.2: turns on which trimming happened
        failures = FailureTracker()
```

Then `finish`'s evidence dict.

Before:

```python
            extra: dict = {"stuck_on": stuck,
                           "last_tool_result": last_tool_result,
                           "last_assistant_text": last_assistant_text,
                           "verify": verify_state}
            finalize_error = None
```

After:

```python
            extra: dict = {"stuck_on": stuck,
                           "last_tool_result": last_tool_result,
                           "last_assistant_text": last_assistant_text,
                           "verify": verify_state,
                           "trimmed_turns": trimmed_turns}
            finalize_error = None
```

Then the trim call site.

Before:

```python
                if not trim_messages(messages, self.char_budget):
                    return finish("context_exhausted", "")
```

After:

```python
                fits, newly_trimmed = trim_messages(messages, self.char_budget)
                if newly_trimmed > 0:
                    # Counted BEFORE the fits check, so the final call that
                    # trimmed something and still could not fit counts too
                    # (spec §2.2).
                    trimmed_turns += 1
                if not fits:
                    return finish("context_exhausted", "")
```

- [ ] **Step 5: Run the runner tests**

Run: `/usr/bin/python3 -m pytest tests/test_runner.py -q`
Expected: `104 passed` (100 today + 4 new: `test_trim_does_not_recount…`, the two counting tests and `test_trimmed_turns_is_zero_on_an_ordinary_run`).

- [ ] **Step 6: Write the failing CLI test**

Append to `tests/test_main.py`:

```python
def test_trimmed_turns_lands_on_the_payload_run_end_and_run_json(tmp_path, monkeypatch, capsys):
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    assert m.main(["run", "--repo", str(repo), "--sandbox", "none", "t"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["trimmed_turns"] == 0
    run_dir = Path(payload["run_dir"])
    assert json.loads((run_dir / "run.json").read_text())["trimmed_turns"] == 0
    events = [json.loads(line) for line in
              (run_dir / "transcript.jsonl").read_text().splitlines()]
    end = [e for e in events if e["event"] == "run_end"][-1]
    assert end["trimmed_turns"] == 0
```

And extend the shared default-evidence expectation so every failure-path test checks it too.

Before:

```python
_DEFAULT_EVIDENCE = {
    "stuck_on": None,
    "files_changed": [],
    "files_changed_truncated": False,
    "last_tool_result": None,
    "last_assistant_text": None,
    "verify": None,
}
```

After:

```python
_DEFAULT_EVIDENCE = {
    "stuck_on": None,
    "files_changed": [],
    "files_changed_truncated": False,
    "last_tool_result": None,
    "last_assistant_text": None,
    "verify": None,
    "trimmed_turns": 0,
}
```

- [ ] **Step 7: Run it to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_main.py -q -k "trimmed_turns or evidence"`
Expected: 3 failed — `test_trimmed_turns_lands_on_the_payload_run_end_and_run_json` with `KeyError: 'trimmed_turns'` on `run.json`, and the two `_DEFAULT_EVIDENCE` tests with `AssertionError: 'trimmed_turns' missing from payload`.

- [ ] **Step 8: Add `_contract_fields` and seed the payload**

In `dirtywork/__main__.py`, right after `_emit_result` returns.

Before:

```python
    payload.update(extra)
    return payload


def _final_status(result) -> str:
```

After:

```python
    payload.update(extra)
    return payload


def _contract_fields(extra: dict, ctx: RunContext) -> dict:
    """The 0.9 contract fields that ride on EVERY payload, `run_end` event and
    `run.json` write (spec §6). One dict feeds the stdout payload and the
    `_update_run_json` call on every path, so those two can never disagree.

    The two failure paths call it with `extra={}`: `runner.run()` never
    returned there, so the documented defaults are exactly what `.get` yields.
    `ctx` is taken even though this first version does not read it -- the
    context-window source (Task 4) comes from the RunContext, not from
    `extra`."""
    return {"trimmed_turns": extra.get("trimmed_turns", 0)}


def _final_status(result) -> str:
```

And the payload seed.

Before:

```python
        "last_assistant_text": None,
        "verify": None,
    }
    payload.update(extra)
```

After:

```python
        "last_assistant_text": None,
        "verify": None,
        "trimmed_turns": 0,
    }
    payload.update(extra)
```

- [ ] **Step 9: Carry it on both failure paths**

In `dirtywork/__main__.py`, `_fail_setup`.

Before:

```python
    message = str(e)
    if transcript is not None:
        try:
            transcript.write("run_end", status="sandbox_error", error=message)
        except Exception:
            pass
    if ctx.owns_worktree:
        remove_worktree(ctx.repo, ctx.slug)
    _err(message)
    _update_run_json(run_dir, status="sandbox_error")
    print(json.dumps(_emit_result(
        status="sandbox_error", worktree=ctx.worktree, branch=ctx.branch, transcript_path=transcript_path,
        run_dir=run_dir, turns=None, usage={}, final_message=message, base_commit=ctx.base_commit,
        resumed_from=ctx.resumed_from, provider=ctx.provider,
    ), indent=2))
    return 1
```

After:

```python
    message = str(e)
    contract = _contract_fields({}, ctx)
    if transcript is not None:
        try:
            transcript.write("run_end", status="sandbox_error", error=message, **contract)
        except Exception:
            pass
    if ctx.owns_worktree:
        remove_worktree(ctx.repo, ctx.slug)
    _err(message)
    _update_run_json(run_dir, status="sandbox_error", **contract)
    print(json.dumps(_emit_result(
        status="sandbox_error", worktree=ctx.worktree, branch=ctx.branch, transcript_path=transcript_path,
        run_dir=run_dir, turns=None, usage={}, final_message=message, base_commit=ctx.base_commit,
        resumed_from=ctx.resumed_from, provider=ctx.provider, **contract,
    ), indent=2))
    return 1
```

And `_fail_run`.

Before:

```python
    if transcript is not None:
        try:
            transcript.write("run_end", status=fail_status, error=message)
        except Exception:
            pass
    _err(message)

    run_json_fields = {"status": fail_status}
    extra_fields = {"base_commit": ctx.base_commit, "resumed_from": ctx.resumed_from}
```

After:

```python
    contract = _contract_fields({}, ctx)
    if transcript is not None:
        try:
            transcript.write("run_end", status=fail_status, error=message, **contract)
        except Exception:
            pass
    _err(message)

    run_json_fields = dict(contract, status=fail_status)
    extra_fields = dict(contract, base_commit=ctx.base_commit,
                        resumed_from=ctx.resumed_from)
```

- [ ] **Step 10: Carry it on the normal path**

In `dirtywork/__main__.py`, `_execute`'s `_update_run_json` call.

Before:

```python
        last_assistant_text=extra.get("last_assistant_text"),
        verify=extra.get("verify"),
        turns=result.turns,
    )
```

After:

```python
        last_assistant_text=extra.get("last_assistant_text"),
        verify=extra.get("verify"),
        turns=result.turns,
        **_contract_fields(extra, ctx),
    )
```

And the stdout emitter.

Before:

```python
        last_assistant_text=extra.get("last_assistant_text"),
        verify=extra.get("verify"),
        resumed_from=ctx.resumed_from, provider=ctx.provider,
    ), indent=2))
```

After:

```python
        last_assistant_text=extra.get("last_assistant_text"),
        verify=extra.get("verify"),
        resumed_from=ctx.resumed_from, provider=ctx.provider,
        **_contract_fields(extra, ctx),
    ), indent=2))
```

- [ ] **Step 11: Run the CLI tests**

Run: `/usr/bin/python3 -m pytest tests/test_main.py -q`
Expected: `70 passed` (69 today + 1).

- [ ] **Step 12: Write the failing task-size-warning tests**

Append to `tests/test_main.py`:

```python
def test_task_size_warning_fires_for_a_long_brief(tmp_path, monkeypatch, capsys):
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    task = "x" * 4000                     # ~1000 tokens at 4 chars/token
    assert m.main(["run", "--repo", str(repo), "--sandbox", "none",
                   "--context-window", "2000", task]) == 0
    err = capsys.readouterr().err
    assert "warning: the task text is ~1000 tokens, 50% of the 2000-token context window" in err
    assert "docs/operating.md#sizing-the-context-window" in err


def test_task_size_warning_is_silent_under_the_threshold(tmp_path, monkeypatch, capsys):
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    task = "x" * 400                      # ~100 tokens = 5% of 2000
    assert m.main(["run", "--repo", str(repo), "--sandbox", "none",
                   "--context-window", "2000", task]) == 0
    assert "the task text is" not in capsys.readouterr().err


def test_task_size_warning_fires_on_resume(tmp_path, monkeypatch, capsys):
    # resume has no args.task: the check runs against ctx.task, which
    # build_resume_task filled with the prior task plus the transcript tail.
    # The scripted client repeats its one tool call, so both runs end
    # `max_turns` after a single turn -- resumable, and deterministic.
    loop = [tool_call_body("read_file", {"path": "README.md"})]
    m = _install_host_harness(monkeypatch, tmp_path, loop)
    repo = _host_repo(tmp_path)
    assert m.main(["run", "--repo", str(repo), "--sandbox", "none", "--max-turns", "1",
                   "x" * 4000]) == 1
    prior = json.loads(capsys.readouterr().out)
    assert m.main(["resume", Path(prior["run_dir"]).name, "--context-window", "2000",
                   "--max-turns", "1"]) == 1
    err = capsys.readouterr().err
    assert "warning: the task text is ~" in err
    assert "% of the 2000-token context window" in err
```

- [ ] **Step 13: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_main.py -q -k task_size_warning`
Expected: 3 failed — the two "fires" tests with `AssertionError` on a stderr that carries no such line; `test_task_size_warning_is_silent_under_the_threshold` passes already (it asserts absence) — treat its pass as expected and confirm the other two fail.

- [ ] **Step 14: Add the warning**

In `dirtywork/__main__.py`, the import block.

Before:

```python
import argparse
import contextlib
import io
import json
import os
import sys
```

After:

```python
import argparse
import contextlib
import io
import json
import math
import os
import sys
```

The runner import.

Before:

```python
from .runner import (
    DEFAULT_STALL_TURNS,
    DEFAULT_STUCK_REPEATS,
    DEFAULT_VERIFY_ROUNDS,
    DEFAULT_VERIFY_TIMEOUT,
    Runner,
    resolve_context_window,
)
```

After:

```python
from .runner import (
    CHARS_PER_TOKEN,
    DEFAULT_STALL_TURNS,
    DEFAULT_STUCK_REPEATS,
    DEFAULT_VERIFY_ROUNDS,
    DEFAULT_VERIFY_TIMEOUT,
    Runner,
    resolve_context_window,
)
```

The constants.

Before:

```python
DEFAULT_MODEL = "qwen/qwen3-coder-next"
DOCKER_WORKDIR = "/work"
```

After:

```python
DEFAULT_MODEL = "qwen/qwen3-coder-next"
DOCKER_WORKDIR = "/work"
# Spec §2.1: a brief past this fraction of the window earns one stderr line.
# 20% is where SP3's 1,084-line brief started thrashing the prompt cache on a
# 65k window: every turn re-sent a task that large, the per-turn trim
# invalidated the cache, and two runs died `context_exhausted`.
TASK_WARN_FRACTION = 0.20
```

And the function, immediately after `_err`.

Before:

```python
def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


class PreflightFailure(Exception):
```

After:

```python
def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def _warn_task_size(ctx) -> None:
    """Spec §2.1: one advisory stderr line when the brief itself eats too much
    of the window. Called once, after the RunContext exists, so it covers both
    `run` (args.task) and `resume` (the marker block build_resume_task made,
    which is what actually gets sent). Nothing is recorded anywhere: this is
    advice, and a run that ignores it is not a different kind of run."""
    task_tokens = math.ceil(len(ctx.task) / CHARS_PER_TOKEN)
    if task_tokens <= TASK_WARN_FRACTION * ctx.context_window:
        return
    pct = round(100 * task_tokens / ctx.context_window)
    print(f"warning: the task text is ~{task_tokens} tokens, {pct}% of the "
          f"{ctx.context_window}-token context window; long briefs thrash the prompt "
          f"cache and risk context_exhausted — split the task or load the model with "
          f"a larger context (docs/operating.md#sizing-the-context-window)",
          file=sys.stderr)


class PreflightFailure(Exception):
```

And the call site in `main`.

Before:

```python
        ctx = (_workspace_resume(args, prior, context_window) if prior
               else _workspace_new(args, repo, context_window))
    except (PreflightFailure, WorkspaceError) as e:
        _err(str(e))
        return 2
    return _execute(ctx, args, client)
```

After:

```python
        ctx = (_workspace_resume(args, prior, context_window) if prior
               else _workspace_new(args, repo, context_window))
    except (PreflightFailure, WorkspaceError) as e:
        _err(str(e))
        return 2
    _warn_task_size(ctx)
    return _execute(ctx, args, client)
```

- [ ] **Step 15: Run the CLI tests**

Run: `/usr/bin/python3 -m pytest tests/test_main.py -q`
Expected: `73 passed` (70 after Step 11 + 3).

- [ ] **Step 16: Write the failing `runs show` test**

Append to `tests/test_runs.py`:

```python
def test_show_renders_trimmed_turns_plain_and_markdown(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "trim1", {
        "slug": "trim1", "status": "context_exhausted", "task": "big brief",
        "trimmed_turns": 7,
    })
    assert runs.cmd_show(argparse.Namespace(slug="trim1", diff=False)) == 0
    assert "trimmed_turns: 7" in capsys.readouterr().out

    assert runs.cmd_show(argparse.Namespace(slug="trim1", diff=False, markdown=True)) == 0
    assert "- **trimmed_turns:** 7" in capsys.readouterr().out
```

- [ ] **Step 17: Run it to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_runs.py -q -k trimmed_turns`
Expected: 1 failed — `AssertionError: assert 'trimmed_turns: 7' in …` (`trimmed_turns` is in neither `SHOW_FIELDS` nor `MD_RESULT_FIELDS`).

- [ ] **Step 18: Show it in both `runs show` renderers**

In `dirtywork/runs.py`.

Before:

```python
SHOW_FIELDS = ("slug", "status", "sandbox", "task", "model", "provider", "turns",
               "resumed_from", "resumed_by", "branch", "worktree", "started", "ended",
               "stuck_on", "files_changed", "verify")
```

After:

```python
SHOW_FIELDS = ("slug", "status", "sandbox", "task", "model", "provider", "turns",
               "resumed_from", "resumed_by", "branch", "worktree", "started", "ended",
               "stuck_on", "files_changed", "verify", "trimmed_turns")
```

And the Markdown result fields.

Before:

```python
MD_RESULT_FIELDS = ("status", "error", "export_status", "finalize_error", "watchdog_violation")
```

After:

```python
# `trimmed_turns` (0.9) is an int that is meaningful at 0, and _md_result's loop
# prints anything not None/"" -- so it renders "0" rather than disappearing,
# which is the point: "nothing was trimmed" is a fact worth reading.
MD_RESULT_FIELDS = ("status", "error", "export_status", "finalize_error",
                    "watchdog_violation", "trimmed_turns")
```

- [ ] **Step 19: Run the runs tests**

Run: `/usr/bin/python3 -m pytest tests/test_runs.py -q`
Expected: `86 passed` (85 today + 1).

- [ ] **Step 20: Write the failing bench test**

Append to `tests/test_bench.py`:

```python
def test_bench_row_records_trimmed_turns_and_summarize_reports_its_mean(
        tmp_path, monkeypatch, capsys):
    _fake_run_environment(tmp_path, monkeypatch, payload={
        "status": "completed", "turns": 3, "trimmed_turns": 4,
        "usage": {"prompt_tokens": 1, "completion_tokens": 1}, "provider": "openai"})
    row = bench.run_one_bench_case("m1", "sh-fix-script", 0, provider="openai",
                                   base_url=None, stamp="s", max_turns=40, timeout=1800)
    assert row["trimmed_turns"] == 4

    monkeypatch.setattr(bench.rundir, "RUNS_DIR", tmp_path / "runs2")
    results = tmp_path / "r.jsonl"
    results.write_text("\n".join(json.dumps(r) for r in [
        _result_row(slug=None, trimmed_turns=2),
        _result_row(slug=None, repeat=1, trimmed_turns=4),
    ]) + "\n")
    assert bench.cmd_summarize(argparse.Namespace(file=str(results))) == 0
    assert "mean trimmed_turns: 3.0" in capsys.readouterr().out


def test_summarize_compare_pairs_trimmed_turns(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bench.rundir, "RUNS_DIR", tmp_path / "runs")
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_text(json.dumps(_result_row(slug=None, trimmed_turns=6)) + "\n")
    b.write_text(json.dumps(_result_row(slug=None, trimmed_turns=2)) + "\n")
    assert bench.cmd_summarize(argparse.Namespace(file=str(a), compare=str(b))) == 0
    out = capsys.readouterr().out
    assert "TRIMMED" in out
    assert "6.0 -> 2.0 (-4.0)" in out
```

- [ ] **Step 21: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_bench.py -q -k trimmed`
Expected: 2 failed — the first with `KeyError: 'trimmed_turns'` on the row, the second with `AssertionError: assert 'TRIMMED' in …`.

- [ ] **Step 22: Record and summarize it in `bench`**

In `dirtywork/bench.py`, the row `run_one_bench_case` returns.

Before:

```python
        "status": status, "turns": payload.get("turns"), "wall_s": wall_s,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
```

After:

```python
        "status": status, "turns": payload.get("turns"), "wall_s": wall_s,
        # Spec §2.2: the runner's own count, straight off the payload -- the
        # per-run signal for "this model needed a bigger window for this task".
        "trimmed_turns": payload.get("trimmed_turns"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
```

Then `_summarize_model`'s returned stats.

Before:

```python
        # added for `--compare`; the per-model block above ignores them
        "mean_turns": _mean(_numbers(rows, "turns")),
```

After:

```python
        "mean_trimmed_turns": _mean(_numbers(rows, "trimmed_turns")),
        # added for `--compare`; the per-model block above ignores them
        "mean_turns": _mean(_numbers(rows, "turns")),
```

Then the per-model plain block.

Before:

```python
        print(f"  mean wall_s: {summary['mean_wall_s']:.1f}" if summary["mean_wall_s"] is not None
              else "  mean wall_s: n/a")
```

After:

```python
        print(f"  mean wall_s: {summary['mean_wall_s']:.1f}" if summary["mean_wall_s"] is not None
              else "  mean wall_s: n/a")
        print(f"  mean trimmed_turns: {summary['mean_trimmed_turns']:.1f}"
              if summary["mean_trimmed_turns"] is not None
              else "  mean trimmed_turns: n/a")
```

Then the paired per-model columns.

Before:

```python
COMPARE_MODEL_COLUMNS = ("model", "runs", "completion", "accept", "gamed", "tokens",
                         "wall_s", "verdict", "review_s")
```

After:

```python
COMPARE_MODEL_COLUMNS = ("model", "runs", "completion", "accept", "gamed", "tokens",
                         "wall_s", "trimmed", "verdict", "review_s")
```

And `_compare_model_rows`.

Before:

```python
            "wall_s": _compare_cell(_stat(a, "mean_wall_s"), _stat(b, "mean_wall_s")),
            "verdict": _compare_cell(_stat(a, "verdict_rate"),
                                     _stat(b, "verdict_rate"), "pct"),
```

After:

```python
            "wall_s": _compare_cell(_stat(a, "mean_wall_s"), _stat(b, "mean_wall_s")),
            "trimmed": _compare_cell(_stat(a, "mean_trimmed_turns"),
                                     _stat(b, "mean_trimmed_turns")),
            "verdict": _compare_cell(_stat(a, "verdict_rate"),
                                     _stat(b, "verdict_rate"), "pct"),
```

- [ ] **Step 23: Run the bench tests**

Run: `/usr/bin/python3 -m pytest tests/test_bench.py -q`
Expected: `41 passed` (39 today + 2).

- [ ] **Step 24: Document the field — `docs/transcript-schema.md`**

First the sentence that introduces the `run_end` table, which has said "`status`
and `error` only" for the CLI's failure paths since 0.4 and stopped being true
the moment `_contract_fields` started riding on them.

Before:

```
One per run, always the last line. Written by the runner on every terminal
status, and by the CLI's failure paths when the runner never returned (in that
case it carries `status` and `error` only).
```

After:

```
One per run, always the last line. Written by the runner on every terminal
status, and by the CLI's failure paths when the runner never returned (in that
case it carries `status`, `error` and the rows marked **always** below —
run-level fields that are known even when the agent loop never started).
```

Then the `run_end` table, after the `verify` row.

Before:

```
| `verify` | | ✓ | object \| null | 0.8: `{command, exit_code, output_tail, rounds, passed}` for the LAST `--verify` execution (`output_tail` capped at 4000 chars); `null` when `--verify` was not given |

## Statuses
```

After:

```
| `verify` | | ✓ | object \| null | 0.8: `{command, exit_code, output_tail, rounds, passed}` for the LAST `--verify` execution (`output_tail` capped at 4000 chars); `null` when `--verify` was not given |
| `trimmed_turns` | | ✓ | integer | **always** — 0.9: the number of turns on which the runner had to replace at least one tool result with `[result trimmed — re-run the tool if needed]` to fit the char budget. A result already trimmed is never recounted, and the final failing trim (the one that ends the run `context_exhausted`) counts if it trimmed anything. `0` on a run that never trimmed, and on the two failure paths where the runner never returned |

## Statuses
```

And the `run.json` table, after the `verify` row.

Before:

```
| `verify` | end | 0.8: `{command, exit_code, output_tail, rounds, passed}` for the last `--verify` execution, or null (null whenever verify never ran, even if `--verify` was given — see `verify_command` above, which `dirtywork resume` reads from instead) |
```

After:

```
| `verify` | end | 0.8: `{command, exit_code, output_tail, rounds, passed}` for the last `--verify` execution, or null (null whenever verify never ran, even if `--verify` was given — see `verify_command` above, which `dirtywork resume` reads from instead) |
| `trimmed_turns` | end | 0.9: turns on which at least one tool result was trimmed to fit the context budget; `0` when nothing was trimmed |
```

- [ ] **Step 25: Teach the doc test about the field**

In `tests/test_transcript_schema.py`.

Before:

```python
RUN_END_FIELDS = ["diff_stat", "untracked", "patch_path", "escaping_symlinks",
                  "dropped_git_entries", "worktree_bytes", "worktree_files",
                  "export_status", "watchdog_violation", "watchdog_violation_kind",
                  "finalize_error", "stuck_on", "files_changed",
                  "files_changed_truncated", "last_tool_result", "last_assistant_text",
                  "verify"]
```

After:

```python
RUN_END_FIELDS = ["diff_stat", "untracked", "patch_path", "escaping_symlinks",
                  "dropped_git_entries", "worktree_bytes", "worktree_files",
                  "export_status", "watchdog_violation", "watchdog_violation_kind",
                  "finalize_error", "stuck_on", "files_changed",
                  "files_changed_truncated", "last_tool_result", "last_assistant_text",
                  "verify", "trimmed_turns"]
```

- [ ] **Step 26: Write the sizing guide — `docs/operating.md`**

Insert a new section immediately before `## Benchmarking`.

Before:

```
## Benchmarking

    dirtywork bench --models 'model[@provider][=base_url],...' \
```

After:

```
## Sizing the context window

**One slot loaded with the largest context your machine holds beats more slots
with smaller ones.** These numbers are from the SP3 build record
(`docs/superpowers/bench/2026-08-17-sp3-worker-scoreboard.md` and
`-run-split.md`), measured on a 128 GB Apple Silicon machine with LM Studio
serving `qwen/qwen3-coder-next`:

| Loaded context | Per turn | Prompt throughput | Outcome on a 1,084-line brief |
|---|---|---|---|
| 65k | 15–17 s | ~3k tok/s | `context_exhausted` twice — the per-turn trim invalidated the prompt cache, so every turn re-read the whole history from scratch |
| 131k | 2.6–5 s | ~13k tok/s | no exhaustion |

The 65k number is not a slow model; it is a *cache-miss* number. Once the
history stops fitting, dirtywork trims the oldest tool results every turn, the
prompt prefix changes every turn, and the server re-processes it every turn.
The fix is a bigger window, not a faster machine.

Two 131k slots do **not** fit on 128 GB: loading the second one crashed LM
Studio (55.9 GB wired, 1.2 GB free just before the crash), while a single 131k
slot peaks around 66 GB wired. Load one:

    lms load qwen/qwen3-coder-next -c 131072

dirtywork asks the server what it actually loaded (LM Studio's
`GET /api/v0/models` reports `loaded_context_length`) and uses that, so you do
not have to repeat the number as `--context-window`. The run records where the
value came from in `context_window_source` — `provider:openai:server` when the
server answered, `provider:openai` when the built-in table did, `flag`/`env`
when you said so, `default` when nothing knew. Ollama is not probed in 0.9: its
`/api/show` reports the model's architectural maximum rather than the loaded
`num_ctx`, so pass `--context-window` there.

**Rules of thumb**

- Keep a dispatched brief under ~450 lines. Past roughly 20% of the window
  dirtywork prints a `warning: the task text is ~N tokens, P% of the …` line on
  stderr; that is the same signal, earlier.
- Bias briefs toward whole-file writes and `apply_edits` batches rather than
  long prose: the model re-reads the task every turn, so a compact brief is
  cheaper on every turn, not just the first.
- Watch `trimmed_turns` on the run's stdout JSON (and in `dirtywork runs show`).
  A run with a non-zero count paid the cache-miss tax on that many turns; a run
  with a large one wanted a bigger window or a smaller brief.

## Benchmarking

    dirtywork bench --models 'model[@provider][=base_url],...' \
```

- [ ] **Step 27: Link it from the README**

In `README.md`, *Requirements*.

Before:

```
- [LM Studio](https://lmstudio.ai) serving its OpenAI-compatible API at
  `localhost:1234` with a tool-calling-capable model loaded. Verified
  working: `qwen/qwen3-coder-next` (65k context, default) and
  `mistralai/devstral-small-2-2512` (32k context)
```

After:

```
- [LM Studio](https://lmstudio.ai) serving its OpenAI-compatible API at
  `localhost:1234` with a tool-calling-capable model loaded. Verified
  working: `qwen/qwen3-coder-next` (65k context, default) and
  `mistralai/devstral-small-2-2512` (32k context). One slot loaded with the
  largest context your machine holds beats several smaller ones — see
  [Sizing the context window](https://github.com/JimboSchneider/dirtywork/blob/main/docs/operating.md#sizing-the-context-window)
  for the measured numbers
```

- [ ] **Step 28: Run the full suite**

Run: `/usr/bin/python3 -m pytest -q`
Expected: `964 passed, 1 skipped, 18 deselected` (953 + 11).

- [ ] **Step 29: Commit**

```bash
git add dirtywork/runner.py dirtywork/__main__.py dirtywork/runs.py dirtywork/bench.py tests/test_runner.py tests/test_main.py tests/test_runs.py tests/test_bench.py tests/test_transcript_schema.py README.md docs/operating.md docs/transcript-schema.md
git commit -m "feat: report trimmed_turns and warn when the brief eats the context window"
```

---

### Task 4: server-reported context window and a recorded source (spec §3)

**Files:**
- Modify: `dirtywork/providers/__init__.py` (`:52-62` the `Provider` Protocol)
- Modify: `dirtywork/providers/openai_compat.py` (`:1-8` imports/constants; new `_origin`; new `loaded_context_window` after `context_window` at `:134-135`)
- Modify: `dirtywork/providers/anthropic.py` (`:147-153` region)
- Modify: `dirtywork/runner.py` (`:306-327` `resolve_context_window`; `:368-401` `Runner.__init__`; `:404-409` `run_start`; `finish`'s extra dict)
- Modify: `dirtywork/__main__.py` (`:104-125` `RunContext`; `:166-176` `_resolve_context_window`; `:237-266` `_workspace_new`; `:382-411` `_write_run_json_start`; `_contract_fields`; `_emit_result`'s seed; `:650-674` `_workspace_resume`; the `Runner(...)` construction; `:974-996` `main`)
- Modify: `dirtywork/runs.py` (`:33-35` `SHOW_FIELDS`; `:222-240` `_summary_value`; `:301-302` `MD_HEADER_FIELDS`)
- Modify: `tests/test_runner.py`, `tests/test_provider_openai.py`, `tests/test_provider_anthropic.py`, `tests/test_main.py`, `tests/test_runs.py`, `tests/test_transcript_schema.py`
- Modify: `docs/transcript-schema.md`, `docs/machine-contract.md`

**Interfaces:**
- Consumes: `llm.http_json(url, payload, headers, timeout, *, method)` (`llm.py:40`; `payload=None` sends no body), `llm.LLMError`/`LLMTimeout` (`llm.py:23-28`), `runner.DEFAULT_WINDOW` (`runner.py:30`), `__main__._contract_fields` (Task 3).
- Produces:
  - `providers.openai_compat.LOADED_CONTEXT_PROBE_TIMEOUT = 2`, `providers.openai_compat._origin(url) -> str`
  - `OpenAICompatClient.loaded_context_window(model: str) -> int | None`
  - `AnthropicClient.loaded_context_window(model: str) -> int | None` (always `None`)
  - `resolve_context_window(...) -> (tokens, source)` with the new `provider:<name>:server` source
  - `RunContext.context_window_source: str` — **required**, declared immediately after `context_window` and therefore before the first defaulted field (`branch_from`)
  - `__main__._resolve_context_window(args, provider=None) -> tuple` (**signature change**; one call site)
  - `__main__._workspace_new(args, repo, context_window, context_window_source)` and `_workspace_resume(args, prior, context_window, context_window_source)` (**signature changes**; one call site each, both inside `main`)
  - `Runner.__init__(..., context_window_source: str | None = None)`
- Decision (recorded): the optional hook is **documented in the `Provider` Protocol's docstring, not declared as a method stub**. Declaring it would tell a type checker that every provider must implement it, which is exactly what spec §3.1 says it must not mean — the protocol is not `runtime_checkable` and nothing does `isinstance`, so the docstring is the whole contract.
- Decision (recorded): spec §3.5 puts the probe tests in "`tests/test_providers.py` / `provider_contract.py`". `tests/test_providers.py` holds only provider-registry tests and imports no transport double; `RecordingTransport` lives in `provider_contract.py` and is already used through `tests/test_provider_openai.py`'s `_client`. The probe tests therefore go in `tests/test_provider_openai.py` and `tests/test_provider_anthropic.py`, next to every other adapter test.

- [ ] **Step 1: Write the failing precedence tests**

Append to `tests/test_runner.py`:

```python
class _ServerProvider(FakeProvider):
    """A provider that also implements the optional loaded_context_window hook
    (spec §3.1). `loaded` may be an int, None, or an Exception to raise."""

    def __init__(self, loaded, context_window=65536):
        super().__init__([], context_window=context_window)
        self._loaded = loaded

    def loaded_context_window(self, model):
        if isinstance(self._loaded, Exception):
            raise self._loaded
        return self._loaded


def test_resolve_context_window_prefers_what_the_server_loaded():
    provider = _ServerProvider(131072)
    assert resolve_context_window("qwen/qwen3-coder-next", None, None, provider) == \
        (131072, "provider:fake:server")


@pytest.mark.parametrize("loaded", [None, 0, -1, True, "65536", RuntimeError("boom")])
def test_resolve_context_window_falls_back_to_the_table_when_the_probe_says_nothing(loaded):
    provider = _ServerProvider(loaded)
    assert resolve_context_window("qwen/qwen3-coder-next", None, None, provider) == \
        (65536, "provider:fake")


def test_resolve_context_window_without_the_hook_uses_the_table():
    # Every existing double and every third-party provider is this case.
    provider = FakeProvider([], context_window=65536)
    assert not hasattr(provider, "loaded_context_window")
    assert resolve_context_window("qwen/qwen3-coder-next", None, None, provider) == \
        (65536, "provider:fake")


def test_flag_and_env_still_beat_the_server_report():
    provider = _ServerProvider(131072)
    assert resolve_context_window("m", 8000, None, provider) == (8000, "flag")
    assert resolve_context_window("m", None, "9000", provider) == (9000, "env")
```

And fix the test that would otherwise attempt a real GET.

Before:

```python
def test_resolve_context_window_uses_the_real_openai_table():
    from dirtywork.providers.openai_compat import OpenAICompatClient
    provider = OpenAICompatClient(base_url="http://fake/v1")
    assert resolve_context_window("qwen/qwen3-coder-next", None, None, provider) == \
        (CONTEXT_WINDOWS["qwen/qwen3-coder-next"], "provider:openai")
```

After:

```python
def test_resolve_context_window_uses_the_real_openai_table():
    # The stub transport keeps this a pure unit test: with 0.9's server probe
    # in front of the table, a real client would otherwise try to GET
    # http://fake/api/v0/models before falling back.
    from dirtywork.llm import LLMError
    from dirtywork.providers.openai_compat import OpenAICompatClient

    def no_server(url, payload, headers, timeout, *, method="POST"):
        raise LLMError(f"cannot reach {url}")

    provider = OpenAICompatClient(base_url="http://fake/v1", http_json=no_server)
    assert resolve_context_window("qwen/qwen3-coder-next", None, None, provider) == \
        (CONTEXT_WINDOWS["qwen/qwen3-coder-next"], "provider:openai")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_runner.py -q -k resolve_context_window`
Expected: `1 failed, 19 passed` — only `test_resolve_context_window_prefers_what_the_server_loaded` fails, with `AssertionError: (65536, 'provider:fake') == (131072, 'provider:fake:server')`. That is the point of the new step: every other new case asserts the FALLBACK, which today's code already produces by ignoring the hook, so those pass before and after. (`test_flag_and_env_still_beat_the_server_report` does not match this `-k` expression; the full-module run in Step 4 covers it.)

- [ ] **Step 3: Add the precedence step**

In `dirtywork/runner.py`, `resolve_context_window`'s docstring.

Before:

```python
def resolve_context_window(model: str, flag_value, env_value, provider=None) -> tuple:
    """Precedence: --context-window > DIRTYWORK_CONTEXT_WINDOW > the provider's
    own table for this model > DEFAULT_WINDOW. Returns (tokens, source) with
    source in flag|env|provider:<name>|default. Raises ValueError for an env
    value that is not a positive integer."""
```

After:

```python
def resolve_context_window(model: str, flag_value, env_value, provider=None) -> tuple:
    """Precedence: --context-window > DIRTYWORK_CONTEXT_WINDOW > what the SERVER
    reports it actually loaded the model with > the provider's own static table
    for this model > DEFAULT_WINDOW. Returns (tokens, source) with source in
    flag|env|provider:<name>:server|provider:<name>|default -- the two provider
    sources are deliberately distinct strings, so a run record says which one
    answered. Raises ValueError for an env value that is not a positive integer."""
```

And its provider branch.

Before:

```python
    if provider is not None:
        window = provider.context_window(model)
        if window:
            return int(window), f"provider:{getattr(provider, 'name', 'provider')}"
    return DEFAULT_WINDOW, "default"
```

After:

```python
    if provider is not None:
        name = getattr(provider, "name", "provider")
        # Spec §3.1: loaded_context_window is OPTIONAL. Third-party providers and
        # every existing test double simply do not have it; one that raises (an
        # endpoint behaving unexpectedly, a transport bug) must never fail a run.
        # Both cases fall through to the static table, exactly as before 0.9.
        probe = getattr(provider, "loaded_context_window", None)
        if probe is not None:
            try:
                loaded = probe(model)
            except Exception:
                loaded = None
            if isinstance(loaded, int) and not isinstance(loaded, bool) and loaded > 0:
                return loaded, f"provider:{name}:server"
        window = provider.context_window(model)
        if window:
            return int(window), f"provider:{name}"
    return DEFAULT_WINDOW, "default"
```

- [ ] **Step 4: Run the precedence tests**

Run: `/usr/bin/python3 -m pytest tests/test_runner.py -q`
Expected: `113 passed` (104 after Task 3 + 9).

- [ ] **Step 5: Write the failing provider tests**

Append to `tests/test_provider_openai.py`:

```python
_LOADED_BODY = {"data": [
    {"id": "other/model", "state": "loaded", "loaded_context_length": 4096},
    {"id": "qwen/qwen3-coder-next", "state": "loaded",
     "max_context_length": 262144, "loaded_context_length": 131072},
]}


def test_loaded_context_window_probes_the_origin_with_its_own_timeout():
    transport = RecordingTransport([_LOADED_BODY])
    client = _client(transport)
    assert client.loaded_context_window("qwen/qwen3-coder-next") == 131072
    call = transport.calls[0]
    assert call["url"] == "http://fake/api/v0/models"    # NOT under /v1
    assert call["method"] == "GET"
    assert call["payload"] is None
    assert call["timeout"] == 2


def test_loaded_context_window_drops_a_proxy_path_prefix():
    transport = RecordingTransport([_LOADED_BODY])
    client = OpenAICompatClient(base_url="http://h:1/prefix/v1", http_json=transport)
    assert client.loaded_context_window("qwen/qwen3-coder-next") == 131072
    assert transport.calls[0]["url"] == "http://h:1/api/v0/models"


@pytest.mark.parametrize("body", [
    {},                                                       # not the expected shape
    {"data": "nope"},                                         # data is not a list
    {"data": []},                                             # model absent
    {"data": [{"id": "other", "state": "loaded", "loaded_context_length": 4096}]},
    {"data": [{"id": "m", "state": "loading", "loaded_context_length": 4096}]},
    {"data": [{"id": "m", "state": "loaded"}]},                # field missing
    {"data": [{"id": "m", "state": "loaded", "loaded_context_length": None}]},
    {"data": [{"id": "m", "state": "loaded", "loaded_context_length": 0}]},
    {"data": [{"id": "m", "state": "loaded", "loaded_context_length": -1}]},
    {"data": [{"id": "m", "state": "loaded", "loaded_context_length": True}]},
    {"data": [{"id": "m", "state": "loaded", "loaded_context_length": "4096"}]},
])
def test_loaded_context_window_rejects_anything_else(body):
    assert _client(RecordingTransport([body])).loaded_context_window("m") is None


def test_loaded_context_window_accepts_an_entry_with_no_state_field():
    # `state` is only checked WHEN PRESENT: a compatible server that reports the
    # loaded length without a state field is still answering the question.
    body = {"data": [{"id": "m", "loaded_context_length": 8192}]}
    assert _client(RecordingTransport([body])).loaded_context_window("m") == 8192


def test_loaded_context_window_is_none_when_the_endpoint_is_unreachable():
    def boom(url, payload, headers, timeout, *, method="POST"):
        raise LLMError(f"cannot reach {url}")

    client = OpenAICompatClient(base_url="http://fake/v1", http_json=boom)
    assert client.loaded_context_window("m") is None
```

Append to `tests/test_provider_anthropic.py`:

```python
def test_loaded_context_window_is_none():
    # Spec §3.1: implemented explicitly, so resolve_context_window's optional
    # hook has a visible answer on both shipped providers.
    assert _client(RecordingTransport([])).loaded_context_window("claude-opus-5") is None
```

- [ ] **Step 6: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_provider_openai.py tests/test_provider_anthropic.py -q -k loaded_context_window`
Expected: 16 failed — every one with `AttributeError: 'OpenAICompatClient' object has no attribute 'loaded_context_window'` / `'AnthropicClient' object has no attribute 'loaded_context_window'`.

- [ ] **Step 7: Implement the probe**

In `dirtywork/providers/openai_compat.py`, the imports.

Before:

```python
from __future__ import annotations

import json

from . import ChatResponse, ToolCall, sanitize_usage
from ..llm import LLMError, MalformedResponse, http_json
```

After:

```python
from __future__ import annotations

import json
import urllib.parse

from . import ChatResponse, ToolCall, sanitize_usage
from ..llm import LLMError, MalformedResponse, http_json
```

And the constants.

Before:

```python
MALFORMED_ENTRY = "malformed tool call entry (missing or invalid id/function fields)"
```

After:

```python
# Spec §3.2: the loaded-context probe is a side query, not part of a turn, so it
# gets its own short deadline rather than the client's (600 s) chat timeout -- a
# server that does not implement the endpoint must cost a run essentially nothing.
LOADED_CONTEXT_PROBE_TIMEOUT = 2

MALFORMED_ENTRY = "malformed tool call entry (missing or invalid id/function fields)"


def _origin(url: str) -> str:
    """scheme://netloc of `url`. LM Studio's native API lives at /api/v0 on the
    SERVER, not under the OpenAI-compatible /v1 prefix, and a proxy path prefix
    ("http://h:1/prefix/v1") is not part of it either -- so the whole path is
    dropped. That can produce a 404 against a proxy that only forwards /v1,
    which is a normal None (spec §3.2), not an error."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"
```

And the method, immediately after `context_window`.

Before:

```python
    def context_window(self, model: str):
        return CONTEXT_WINDOWS.get(model)
```

After:

```python
    def context_window(self, model: str):
        return CONTEXT_WINDOWS.get(model)

    def loaded_context_window(self, model: str):
        """Spec §3.2: the context length the server currently has this model
        LOADED with, or None. Verified live 2026-08-19 against LM Studio at
        localhost:1234 -- GET /api/v0/models returns
        {"data":[{"id":"qwen/qwen3-coder-next","state":"loaded",
        "max_context_length":262144,"loaded_context_length":65536, …}]} -- while
        /v1/models carries no context field at all.

        `max_context_length` is deliberately NOT used: it is what the model
        could do, not what the server allocated, and budgeting against a window
        the server does not have is worse than budgeting conservatively.

        Every other outcome -- connection error, timeout, non-2xx, non-JSON,
        missing/None/non-int/<=0 field, a model that is not loaded, a model that
        is absent -- returns None, so resolve_context_window falls through to the
        static table exactly as it did before 0.9."""
        url = f"{_origin(self.base_url)}/api/v0/models"
        try:
            body = self._http_json(url, None, {"Content-Type": "application/json"},
                                   LOADED_CONTEXT_PROBE_TIMEOUT, method="GET")
        except LLMError:
            return None      # LLMTimeout is an LLMError: both mean "no answer"
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            return None
        for entry in body["data"]:
            if not isinstance(entry, dict) or entry.get("id") != model:
                continue
            state = entry.get("state")
            if state is not None and state != "loaded":
                return None
            loaded = entry.get("loaded_context_length")
            if isinstance(loaded, int) and not isinstance(loaded, bool) and loaded > 0:
                return loaded
            return None
        return None
```

In `dirtywork/providers/anthropic.py`.

Before:

```python
    def context_window(self, model: str):
        # Deliberately conservative and static: this only feeds the runner's
        # trim budget, never API correctness, and --context-window overrides it
        # per run. Verify against current Anthropic docs if precision matters.
        if model.startswith("claude-"):
            return 200000
        return None
```

After:

```python
    def context_window(self, model: str):
        # Deliberately conservative and static: this only feeds the runner's
        # trim budget, never API correctness, and --context-window overrides it
        # per run. Verify against current Anthropic docs if precision matters.
        if model.startswith("claude-"):
            return 200000
        return None

    def loaded_context_window(self, model: str):
        """Spec §3.1: the Anthropic API reports no per-connection "loaded"
        context length -- the window is a property of the model, which
        context_window() above already covers. Implemented explicitly rather
        than left absent so the optional hook's contract is visible on both
        shipped providers."""
        return None
```

And document the hook on the Protocol, in `dirtywork/providers/__init__.py`.

Before:

```python
class Provider(Protocol):
    name: str

    def list_models(self) -> list:
        ...
```

After:

```python
class Provider(Protocol):
    """The four members every adapter must have, plus one OPTIONAL method that
    is deliberately not declared here:

        loaded_context_window(model: str) -> int | None

    the context length the server currently has `model` loaded with, or None
    when it cannot say. `runner.resolve_context_window` reaches for it with
    `getattr(provider, "loaded_context_window", None)` and treats a missing
    method, a None, and any raised exception identically -- as "no answer" --
    so a third-party provider or a test double that does not implement it stays
    a valid Provider. Declaring it below would say the opposite (spec §3.1)."""

    name: str

    def list_models(self) -> list:
        ...
```

- [ ] **Step 8: Run the provider tests**

Run: `/usr/bin/python3 -m pytest tests/test_provider_openai.py tests/test_provider_anthropic.py -q`
Expected: `69 passed` (31 + 22 today, + 15 + 1).

- [ ] **Step 9: Write the failing CLI/runs tests**

Append to `tests/test_main.py`:

```python
def test_context_window_source_lands_in_run_json_run_start_and_stdout(
        tmp_path, monkeypatch, capsys):
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    assert m.main(["run", "--repo", str(repo), "--sandbox", "none",
                   "--context-window", "5000", "t"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["context_window_source"] == "flag"
    run_dir = Path(payload["run_dir"])
    data = json.loads((run_dir / "run.json").read_text())
    assert data["context_window"] == 5000
    assert data["context_window_source"] == "flag"
    events = [json.loads(line) for line in
              (run_dir / "transcript.jsonl").read_text().splitlines()]
    assert events[0]["event"] == "run_start"
    assert events[0]["context_window_source"] == "flag"
    end = [e for e in events if e["event"] == "run_end"][-1]
    assert end["context_window_source"] == "flag"


def test_resume_records_its_own_context_window_source(tmp_path, monkeypatch, capsys):
    # The window is re-resolved on resume exactly as on a fresh run, so the
    # source is the resuming invocation's, not the prior run's. Both runs end
    # `max_turns` after one turn: the scripted client repeats its tool call.
    loop = [tool_call_body("read_file", {"path": "README.md"})]
    m = _install_host_harness(monkeypatch, tmp_path, loop)
    repo = _host_repo(tmp_path)
    assert m.main(["run", "--repo", str(repo), "--sandbox", "none", "--max-turns", "1",
                   "t"]) == 1
    prior = json.loads(capsys.readouterr().out)
    assert json.loads((Path(prior["run_dir"]) / "run.json").read_text())[
        "context_window_source"] == "default"
    assert m.main(["resume", Path(prior["run_dir"]).name, "--context-window", "7000",
                   "--max-turns", "1"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["context_window_source"] == "flag"
    assert json.loads((Path(payload["run_dir"]) / "run.json").read_text())[
        "context_window_source"] == "flag"
```

And extend the default-evidence map (a failure-path payload must carry a real source, so it is asserted separately rather than added to `_DEFAULT_EVIDENCE`):

```python
def test_emit_result_seeds_context_window_source():
    import dirtywork.__main__ as m
    payload = m._emit_result(
        status="sandbox_error", worktree=Path("/wt"), branch="b",
        transcript_path=Path("/t.jsonl"), run_dir=Path("/rd"), turns=None,
        usage={}, final_message="boom", provider="openai")
    assert payload["context_window_source"] is None       # seeded, then overridden
    payload = m._emit_result(
        status="sandbox_error", worktree=Path("/wt"), branch="b",
        transcript_path=Path("/t.jsonl"), run_dir=Path("/rd"), turns=None,
        usage={}, final_message="boom", provider="openai",
        context_window_source="provider:openai:server")
    assert payload["context_window_source"] == "provider:openai:server"
```

Append to `tests/test_runs.py`:

```python
def test_show_renders_context_window_with_its_source(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "ctx1", {
        "slug": "ctx1", "status": "completed", "task": "t",
        "context_window": 65536, "context_window_source": "provider:openai:server",
    })
    assert runs.cmd_show(argparse.Namespace(slug="ctx1", diff=False)) == 0
    assert "context_window: 65536 (provider:openai:server)" in capsys.readouterr().out

    assert runs.cmd_show(argparse.Namespace(slug="ctx1", diff=False, markdown=True)) == 0
    md = capsys.readouterr().out
    assert "- **context_window:** 65536 (provider:openai:server)" in md
```

- [ ] **Step 10: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_main.py tests/test_runs.py -q -k "context_window_source or context_window_with_its_source"`
Expected: 4 failed — the three `test_main.py` tests with `KeyError: 'context_window_source'`, and the `runs` test with `AssertionError` (the field is in neither `SHOW_FIELDS` nor `MD_HEADER_FIELDS`).

- [ ] **Step 11: Carry the source on the RunContext**

In `dirtywork/__main__.py`, `RunContext`.

Before:

```python
    image_pinned: bool
    context_window: int
    branch_from: str | None = None
```

After:

```python
    image_pinned: bool
    context_window: int
    # Spec §3.4: which precedence step produced `context_window` --
    # flag|env|provider:<name>:server|provider:<name>|default. REQUIRED (no
    # default), so it must stay above the first defaulted field below; a run
    # record that reports 32768 without saying whether anybody chose it is not
    # a record.
    context_window_source: str
    branch_from: str | None = None
```

And `_resolve_context_window`.

Before:

```python
def _resolve_context_window(args, provider=None) -> int:
    try:
        window, source = resolve_context_window(
            args.model, args.context_window, os.environ.get("DIRTYWORK_CONTEXT_WINDOW"),
            provider)
    except ValueError as e:
        raise PreflightFailure(str(e))
    if source == "default":
        print(f"warning: no known context window for '{args.model}'; assuming {window} tokens "
              f"(set --context-window or DIRTYWORK_CONTEXT_WINDOW)", file=sys.stderr)
    return window
```

After:

```python
def _resolve_context_window(args, provider=None) -> tuple:
    """(tokens, source). The source was discarded before 0.9; it is now recorded
    on the run. The "assuming …" warning still fires only for "default" --
    "provider:openai:server" and "provider:openai" are both known values."""
    try:
        window, source = resolve_context_window(
            args.model, args.context_window, os.environ.get("DIRTYWORK_CONTEXT_WINDOW"),
            provider)
    except ValueError as e:
        raise PreflightFailure(str(e))
    if source == "default":
        print(f"warning: no known context window for '{args.model}'; assuming {window} tokens "
              f"(set --context-window or DIRTYWORK_CONTEXT_WINDOW)", file=sys.stderr)
    return window, source
```

And the two workspace builders.

Before:

```python
def _workspace_new(args, repo: Path, context_window: int) -> RunContext:
```

After:

```python
def _workspace_new(args, repo: Path, context_window: int,
                   context_window_source: str) -> RunContext:
```

Before:

```python
        base_commit=worktree_base_commit(worktree), task=args.task,
        sandbox_mode=args.sandbox, provider=args.provider, image_ref=image_ref, image_digest=image_digest,
        image_pinned=image_pinned, context_window=context_window, branch_from=branch_from,
        branch_from_run=branch_from_run,
    )
```

After:

```python
        base_commit=worktree_base_commit(worktree), task=args.task,
        sandbox_mode=args.sandbox, provider=args.provider, image_ref=image_ref, image_digest=image_digest,
        image_pinned=image_pinned, context_window=context_window,
        context_window_source=context_window_source, branch_from=branch_from,
        branch_from_run=branch_from_run,
    )
```

Before:

```python
def _workspace_resume(args, prior: dict, context_window: int) -> RunContext:
```

After:

```python
def _workspace_resume(args, prior: dict, context_window: int,
                      context_window_source: str) -> RunContext:
```

Before:

```python
        base_commit=prior["base_commit"], task=task, sandbox_mode=args.sandbox,
        provider=args.provider, image_ref=image_ref, image_digest=image_digest, image_pinned=image_pinned,
        context_window=context_window, resumed_from=prior["slug"], feedback=feedback,
```

After:

```python
        base_commit=prior["base_commit"], task=task, sandbox_mode=args.sandbox,
        provider=args.provider, image_ref=image_ref, image_digest=image_digest, image_pinned=image_pinned,
        context_window=context_window, context_window_source=context_window_source,
        resumed_from=prior["slug"], feedback=feedback,
```

And `main`.

Before:

```python
        client = _preflight_llm(args)
        context_window = _resolve_context_window(args, client)
        ctx = (_workspace_resume(args, prior, context_window) if prior
               else _workspace_new(args, repo, context_window))
    except (PreflightFailure, WorkspaceError) as e:
```

After:

```python
        client = _preflight_llm(args)
        context_window, window_source = _resolve_context_window(args, client)
        ctx = (_workspace_resume(args, prior, context_window, window_source) if prior
               else _workspace_new(args, repo, context_window, window_source))
    except (PreflightFailure, WorkspaceError) as e:
```

- [ ] **Step 12: Write it to `run.json`, the payload and every `run_end`**

In `dirtywork/__main__.py`, `_write_run_json_start`.

Before:

```python
        "context_window": ctx.context_window,
        "branch_from_run": ctx.branch_from_run,
```

After:

```python
        "context_window": ctx.context_window,
        "context_window_source": ctx.context_window_source,
        "branch_from_run": ctx.branch_from_run,
```

Then `_emit_result`'s seed.

Before:

```python
        "verify": None,
        "trimmed_turns": 0,
    }
    payload.update(extra)
```

After:

```python
        "verify": None,
        "trimmed_turns": 0,
        "context_window_source": None,
    }
    payload.update(extra)
```

Then `_contract_fields`.

Before:

```python
    The two failure paths call it with `extra={}`: `runner.run()` never
    returned there, so the documented defaults are exactly what `.get` yields.
    `ctx` is taken even though this first version does not read it -- the
    context-window source (Task 4) comes from the RunContext, not from
    `extra`."""
    return {"trimmed_turns": extra.get("trimmed_turns", 0)}
```

After:

```python
    The two failure paths call it with `extra={}`: `runner.run()` never
    returned there, so the documented defaults are exactly what `.get` yields.
    `context_window_source` comes from the RunContext instead, because it is
    resolved in preflight and is therefore known on every path a payload can
    exist on -- a context-window preflight failure exits 2 with no payload at
    all, as it did before 0.9."""
    return {"trimmed_turns": extra.get("trimmed_turns", 0),
            "context_window_source": ctx.context_window_source}
```

And the `Runner(...)` construction in `_execute`.

Before:

```python
            stall_turns=args.stall_turns, context_window=ctx.context_window,
            stuck_repeats=getattr(args, "stuck_repeats", DEFAULT_STUCK_REPEATS),
```

After:

```python
            stall_turns=args.stall_turns, context_window=ctx.context_window,
            context_window_source=ctx.context_window_source,
            stuck_repeats=getattr(args, "stuck_repeats", DEFAULT_STUCK_REPEATS),
```

- [ ] **Step 13: Record it on `run_start` and `run_end` from the runner**

In `dirtywork/runner.py`, `Runner.__init__`'s signature.

Before:

```python
                 stall_turns: int = DEFAULT_STALL_TURNS,
                 context_window: int | None = None,
                 stuck_repeats: int = DEFAULT_STUCK_REPEATS,
```

After:

```python
                 stall_turns: int = DEFAULT_STALL_TURNS,
                 context_window: int | None = None,
                 context_window_source: str | None = None,
                 stuck_repeats: int = DEFAULT_STUCK_REPEATS,
```

And its body.

Before:

```python
        self.stall_turns = stall_turns
        self.stuck_repeats = stuck_repeats
```

After:

```python
        self.stall_turns = stall_turns
        # Spec §3.4: recorded, never used for a decision. The runner already has
        # the NUMBER; this only says where it came from, so a run record can be
        # read without guessing whether anybody chose it. None for a Runner
        # built directly (tests, embedders) that never resolved a source.
        self.context_window_source = context_window_source
        self.stuck_repeats = stuck_repeats
```

Then the `run_start` write.

Before:

```python
        self.transcript.write("run_start", task=task, model=self.model,
                              max_turns=self.max_turns, timeout=self.timeout,
                              context_window=self.context_window,
                              schema_version=2, **(self.run_info or {}))
```

After:

```python
        self.transcript.write("run_start", task=task, model=self.model,
                              max_turns=self.max_turns, timeout=self.timeout,
                              context_window=self.context_window,
                              context_window_source=self.context_window_source,
                              schema_version=2, **(self.run_info or {}))
```

Then `finish`'s evidence dict.

Before:

```python
                           "verify": verify_state,
                           "trimmed_turns": trimmed_turns}
            finalize_error = None
```

After:

```python
                           "verify": verify_state,
                           "trimmed_turns": trimmed_turns,
                           "context_window_source": self.context_window_source}
            finalize_error = None
```

- [ ] **Step 14: Show it in `runs show`**

In `dirtywork/runs.py`.

Before:

```python
SHOW_FIELDS = ("slug", "status", "sandbox", "task", "model", "provider", "turns",
               "resumed_from", "resumed_by", "branch", "worktree", "started", "ended",
               "stuck_on", "files_changed", "verify", "trimmed_turns")
```

After:

```python
SHOW_FIELDS = ("slug", "status", "sandbox", "task", "model", "provider",
               "context_window", "turns",
               "resumed_from", "resumed_by", "branch", "worktree", "started", "ended",
               "stuck_on", "files_changed", "verify", "trimmed_turns")
```

And `_summary_value`.

Before:

```python
    if key == "verify" and isinstance(value, dict):
        state = "passed" if value.get("passed") else "failed"
        return f"{state} (exit {value.get('exit_code')})"
    text = str(value)
```

After:

```python
    if key == "verify" and isinstance(value, dict):
        state = "passed" if value.get("passed") else "failed"
        return f"{state} (exit {value.get('exit_code')})"
    if key == "context_window":
        # 0.9: the number alone cannot be read -- 32768 may be the model's real
        # window or the fallback nobody chose. The source says which. A run.json
        # written before 0.9 has no source and renders the bare number.
        source = data.get("context_window_source")
        return f"{value} ({source})" if source else str(value)
    text = str(value)
```

And the Markdown header fields.

Before:

```python
MD_HEADER_FIELDS = ("status", "task", "model", "provider", "sandbox", "turns",
                    "base_commit", "branch", "worktree", "resumed_from", "resumed_by")
```

After:

```python
MD_HEADER_FIELDS = ("status", "task", "model", "provider", "context_window", "sandbox",
                    "turns", "base_commit", "branch", "worktree", "resumed_from",
                    "resumed_by")
```

- [ ] **Step 15: Run the CLI and runs tests**

Run: `/usr/bin/python3 -m pytest tests/test_main.py tests/test_runs.py -q`
Expected: `163 passed` (73 + 86 after Task 3, + 3 + 1).

- [ ] **Step 16: Document the field — `docs/transcript-schema.md`**

The `run_start` table, after the `context_window` row.

Before:

```
| `context_window` | | ✓ | integer | tokens; the resolved value (`--context-window` > `DIRTYWORK_CONTEXT_WINDOW` > the provider's table > 32768) |
```

After:

```
| `context_window` | | ✓ | integer | tokens; the resolved value (`--context-window` > `DIRTYWORK_CONTEXT_WINDOW` > what the server reports it loaded the model with > the provider's static table > 32768) |
| `context_window_source` | | ✓ | string \| null | 0.9: which of those steps answered — `flag`, `env`, `provider:<name>:server` (the server's own report, e.g. LM Studio's `loaded_context_length`), `provider:<name>` (the built-in table), or `default` (nothing knew; the "assuming 32768 tokens" warning fires only for this one). `null` only for a `Runner` constructed directly without a source |
```

The `run_end` table, after the `trimmed_turns` row.

Before:

```
| `trimmed_turns` | | ✓ | integer | **always** — 0.9: the number of turns on which the runner had to replace at least one tool result with `[result trimmed — re-run the tool if needed]` to fit the char budget. A result already trimmed is never recounted, and the final failing trim (the one that ends the run `context_exhausted`) counts if it trimmed anything. `0` on a run that never trimmed, and on the two failure paths where the runner never returned |
```

After:

```
| `trimmed_turns` | | ✓ | integer | **always** — 0.9: the number of turns on which the runner had to replace at least one tool result with `[result trimmed — re-run the tool if needed]` to fit the char budget. A result already trimmed is never recounted, and the final failing trim (the one that ends the run `context_exhausted`) counts if it trimmed anything. `0` on a run that never trimmed, and on the two failure paths where the runner never returned |
| `context_window_source` | | ✓ | string | **always** — 0.9: the same value as `run_start.context_window_source`, repeated at the end so a consumer that reads only the last line still knows where the window came from |
```

The `run.json` table, after the `context_window` row.

Before:

```
| `context_window` | start | resolved tokens |
```

After:

```
| `context_window` | start | resolved tokens |
| `context_window_source` | start, end | 0.9: `flag` \| `env` \| `provider:<name>:server` \| `provider:<name>` \| `default` — which precedence step produced `context_window`. Written at start and repeated at end (including on the two failure paths) so the plain `dirtywork runs show`, which reads only `run.json`, never shows `-` |
```

- [ ] **Step 17: Document the precedence — `docs/machine-contract.md`**

Before:

```
- `--context-window TOKENS` — the model's context window, used to size the
  transcript trimming budget. Precedence: flag, then `DIRTYWORK_CONTEXT_WINDOW`,
  then a built-in table for the known LM Studio models, then 32768 (with a
  warning on stderr).
```

After:

```
- `--context-window TOKENS` — the model's context window, used to size the
  transcript trimming budget. Precedence: flag, then `DIRTYWORK_CONTEXT_WINDOW`,
  then **what the server reports it actually loaded the model with** (LM Studio's
  `GET /api/v0/models` → `loaded_context_length`, probed once at preflight with
  a 2-second timeout; any failure is silently no answer), then a built-in table
  for the known LM Studio models, then 32768 (with a warning on stderr — only
  this last step warns). Which step answered is recorded as
  `context_window_source` on `run_start`, `run.json`, every payload and
  `run_end`: `flag`, `env`, `provider:<name>:server`, `provider:<name>`, or
  `default`. Ollama is not probed in 0.9 — its `/api/show` reports the model's
  architectural maximum rather than the loaded `num_ctx` — so pass
  `--context-window` there. See
  [Sizing the context window](operating.md#sizing-the-context-window).
```

- [ ] **Step 18: Run the full suite**

Run: `/usr/bin/python3 -m pytest -q`
Expected: `993 passed, 1 skipped, 18 deselected` (964 + 29).

Note that `tests/test_transcript_schema.py` needed no edit for this field: its
`test_a_real_run_emits_the_documented_events` walks every key a real run emits
and asserts it is a backticked token in `docs/transcript-schema.md`, which
Step 16 satisfied — that test is the gate, and it must be green here.

- [ ] **Step 19: Commit**

```bash
git add dirtywork/providers/__init__.py dirtywork/providers/openai_compat.py dirtywork/providers/anthropic.py dirtywork/runner.py dirtywork/__main__.py dirtywork/runs.py tests/test_runner.py tests/test_provider_openai.py tests/test_provider_anthropic.py tests/test_main.py tests/test_runs.py docs/transcript-schema.md docs/machine-contract.md
git commit -m "feat: use the server's loaded context length and record where the window came from"
```

---

### Task 5: louder timeouts — one canonical text, one predicate, a nudge and a counter (spec §4)

**Files:**
- Modify: `dirtywork/tools.py` (`:464-469` constants region; `:472-494` `bash`; new `TIMEOUT_PREFIX`, `TIMEOUT_TEXT`, `timeout_result`, `is_timeout_result`)
- Modify: `dirtywork/sandbox/docker_cli.py` (`:20-23` `DockerError`; `:32-41` `run`)
- Modify: `dirtywork/sandbox/docker.py` (the `..tools` import block; `grep`'s `DockerError` catch; `bash`'s `DockerError` catch)
- Modify: `dirtywork/runner.py` (`:10-13` imports; the nudge constants; `run()`'s locals; the tool loop; the end-of-turn nudge composition; `finish`'s extra)
- Modify: `dirtywork/__main__.py` (`_emit_result`'s docstring and seed; `_contract_fields`)
- Modify: `dirtywork/runs.py` (`:24-28` imports; `:33-35` `SHOW_FIELDS`; `:267-275` `_tool_result_outcome`; `:304` `MD_RESULT_FIELDS`)
- Modify: `dirtywork/bench.py` (`:46` `NUDGE_KINDS`; `:227-238` `_harness_failures`; the row's `harness` value; `:381-388` `_failure_cell`; `:417-456` `_summarize_model`; `:557-571` `_harness_cell`/`_harness_counts`; `:678-698` `_print_comparison`; `:700-756` `cmd_summarize`)
- Modify: `tests/test_tools_bash.py`, `tests/test_docker_cli.py`, `tests/test_docker_sandbox.py`, `tests/test_runner.py`, `tests/test_main.py`, `tests/test_runs.py`, `tests/test_bench.py`, `tests/test_transcript_schema.py`
- Modify: `docs/operating.md`, `docs/transcript-schema.md`

**Interfaces:**
- Consumes: `runner._join_nudges(*parts) -> str` (`runner.py:86`), `runner.STALL_NUDGE` (`runner.py:150`), `procs.Captured.timed_out`, `docker_cli.run` (`docker_cli.py:32`), `bench._event_counts` (`bench.py:187`), `bench._mean` (`bench.py:409`).
- Produces:
  - `tools.TIMEOUT_PREFIX`, `tools.TIMEOUT_TEXT`, `tools.timeout_result(timeout: int) -> str`, `tools.is_timeout_result(text) -> bool`
  - `docker_cli.DockerError(*args, timed_out: bool = False)` with `.timed_out`
  - `runner.TIMEOUT_NUDGE`; `RunResult.extra["timeouts"]` — `int`, on every runner-returned result
  - transcript `tool_result.timed_out: true` (sparse, `bash` only) and nudge kind `timeout`
  - `runs._tool_result_outcome` returns the new class `"timed out"`
  - `bench._harness_failures(counts, status, final_message, timeouts=0)` (**signature change**; one call site), `harness["timeouts"]`, `_summarize_model` key `timeouts`, 4-tuple `_harness_counts`
- Decision (recorded deviation): spec §4.3 says "`_failure_cell` renders `n/s/m/t`, and the legend text becomes `harness: nudges/stalled/max_turns/timeouts` (plain and `--compare`)". `_failure_cell` today renders a comma-joined list of NAMES (`stalled,empty_reply=3,abort=bad_args`) for the plain detail table's FAILURES column; the `n/s/m/t` cell is `_harness_cell`, which only `--compare` prints. Rendering `_failure_cell` as `n/s/m/t` literally would delete shipped output (`abort=`, `sandbox_error`), which the additive-contract rule forbids. So: `_harness_counts`/`_harness_cell` become the 4-tuple and the `--compare` legend becomes `harness: nudges/stalled/max_turns/timeouts` exactly as written; `_failure_cell` gains an additive `timeouts=N` token alongside `empty_reply=N`; and the plain summary's legend block gains the matching lines (its `nudges:` legend must gain `/timeout` regardless, because the NUDGES column joins over `NUDGE_KINDS`).

- [ ] **Step 1: Write the failing host-bash test**

In `tests/test_tools_bash.py`.

Before:

```python
def test_bash_timeout(wt: Path):
    out = bash(wt, "sleep 5", timeout=1)
    assert "timed out" in out.lower()
```

After:

```python
def test_bash_timeout(wt: Path):
    # Spec §4.1: the exact canonical text, so a wording drift fails here rather
    # than in a worker's head.
    out = bash(wt, "sleep 5", timeout=1)
    assert out == (
        "ERROR: command timed out after 1s — it did not finish and its result is "
        "unknown. Re-run it with a larger timeout (up to 600) or split it into "
        "smaller commands; do not report it as passed.")


def test_bash_timeout_appends_no_partial_output(wt: Path):
    # Spec §4.1: the host CAN produce a tail (it captured one) and docker cannot.
    # Parity wins: a tail is what a small model misreads as the command's result.
    out = bash(wt, "echo PARTIAL_MARKER; sleep 5", timeout=1)
    assert "PARTIAL_MARKER" not in out
    assert out == (
        "ERROR: command timed out after 1s — it did not finish and its result is "
        "unknown. Re-run it with a larger timeout (up to 600) or split it into "
        "smaller commands; do not report it as passed.")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_tools_bash.py -q -k timeout`
Expected: 2 failed of 4 — both new assertions fail against today's `ERROR: command timed out after 1s.` (the second also shows `PARTIAL_MARKER` in the tail). `test_bash_timeout_reaps_process_tree` and `test_bash_runaway_output_times_out_without_ooming` keep passing (they assert `"timed out" in out.lower()`, which the new text still satisfies).

- [ ] **Step 3: Add the canonical text and the predicate**

In `dirtywork/tools.py`, above `bash`.

Before:

```python
MAX_BASH_CHARS = 10000
# Hard cap on child output buffered in memory. subprocess.run(capture_output=True)
# buffers the whole stream before we can truncate it, so `cat /dev/zero` would OOM
# the process. We drain the pipe on a thread (so the child never blocks on a full
# pipe) but keep only the first MAX_BASH_CAPTURE_BYTES.
MAX_BASH_CAPTURE_BYTES = 1024 * 1024
```

After:

```python
MAX_BASH_CHARS = 10000
# Hard cap on child output buffered in memory. subprocess.run(capture_output=True)
# buffers the whole stream before we can truncate it, so `cat /dev/zero` would OOM
# the process. We drain the pipe on a thread (so the child never blocks on a full
# pipe) but keep only the first MAX_BASH_CAPTURE_BYTES.
MAX_BASH_CAPTURE_BYTES = 1024 * 1024

# Spec §4.1: ONE canonical timed-out-command result, identical on both backends.
# TIMEOUT_PREFIX is the predicate everything downstream keys on -- the
# tool_result flag, the `timeout` nudge, the run's `timeouts` counter,
# `runs show`'s outcome class and the bench scoreboard -- so there is exactly
# one string in this codebase to keep in step.
TIMEOUT_PREFIX = "ERROR: command timed out after "
TIMEOUT_TEXT = (TIMEOUT_PREFIX + "{timeout}s — it did not finish and its result is "
                "unknown. Re-run it with a larger timeout (up to 600) or split it "
                "into smaller commands; do not report it as passed.")


def timeout_result(timeout: int) -> str:
    """The canonical result for a command that hit its timeout. No partial
    output is appended: the host CAN produce a tail (it captured one) and docker
    cannot, and parity wins -- a tail is exactly what a small model reads as
    "the command's result" when the truth is that the command never finished."""
    return TIMEOUT_TEXT.format(timeout=timeout)


def is_timeout_result(text) -> bool:
    """True for a bash result produced by timeout_result(). The ONE predicate --
    never re-derive this from a substring search somewhere else, or the two
    will drift the first time the wording changes."""
    return isinstance(text, str) and text.startswith(TIMEOUT_PREFIX)
```

And `bash`'s timeout branch.

Before:

```python
    if captured.timed_out:
        tail = f"\n{out}" if out else ""
        return _cap(f"ERROR: command timed out after {timeout}s.{tail}",
                    cap=MAX_BASH_CHARS, note=note)
```

After:

```python
    if captured.timed_out:
        # Spec §4.1: no partial output, and no cap -- the canonical text is a
        # couple of hundred characters, far under MAX_BASH_CHARS.
        return timeout_result(timeout)
```

- [ ] **Step 4: Run the host-bash tests**

Run: `/usr/bin/python3 -m pytest tests/test_tools_bash.py -q`
Expected: `13 passed` (12 today + 1).

- [ ] **Step 5: Write the failing `DockerError` test**

Append to `tests/test_docker_cli.py`:

```python
def test_docker_error_timed_out_defaults_false_and_run_sets_it(monkeypatch):
    # Every existing construction is positional and must keep working -- the
    # new flag is keyword-only with a default precisely so they do.
    plain = DockerError("docker exec failed: no such container")
    assert plain.timed_out is False
    assert str(plain) == "docker exec failed: no such container"
    assert DockerError("x", timed_out=True).timed_out is True

    import dirtywork.sandbox.docker_cli as mod

    def fake_run_capped(argv, *, timeout=None, stdin=None):
        return Captured(returncode=None, output=b"", truncated=False, timed_out=True)

    monkeypatch.setattr(mod, "run_capped", fake_run_capped)
    with pytest.raises(DockerError) as excinfo:
        run(["exec", "c", "true"], timeout=5)
    assert excinfo.value.timed_out is True
    assert "timed out after 5s" in str(excinfo.value)
```

- [ ] **Step 6: Run it to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_docker_cli.py -q -k timed_out`
Expected: 1 failed — `AttributeError: 'DockerError' object has no attribute 'timed_out'`.

- [ ] **Step 7: Give `DockerError` the flag**

In `dirtywork/sandbox/docker_cli.py`.

Before:

```python
class DockerError(SandboxError):
    """Raised on a nonzero docker CLI exit or an expired timeout. Callers
    turn this into status sandbox_error (via the runner catching
    SandboxError) or, at preflight, into an exit-2 hint."""
```

After:

```python
class DockerError(SandboxError):
    """Raised on a nonzero docker CLI exit or an expired timeout. Callers
    turn this into status sandbox_error (via the runner catching
    SandboxError) or, at preflight, into an exit-2 hint.

    `timed_out` (spec §4.2) is True ONLY on run()'s expired-timeout path below --
    the one place a DockerError means "the command may still be running". Every
    other raise leaves it False, so DockerSandbox.bash and .grep can tell a real
    timeout from an ordinary docker failure instead of reporting both as a
    timeout. Keyword-only with a default, so every existing positional
    `DockerError("...")` construction and every `except DockerError` in the tree
    keeps working untouched."""

    def __init__(self, *args, timed_out: bool = False):
        super().__init__(*args)
        self.timed_out = timed_out
```

And `run`.

Before:

```python
    if captured.timed_out:
        raise DockerError(f"docker {' '.join(str(a) for a in argv)} timed out after {timeout}s")
```

After:

```python
    if captured.timed_out:
        raise DockerError(
            f"docker {' '.join(str(a) for a in argv)} timed out after {timeout}s",
            timed_out=True)
```

- [ ] **Step 8: Run the docker CLI tests**

Run: `/usr/bin/python3 -m pytest tests/test_docker_cli.py -q`
Expected: `27 passed` (26 today + 1).

- [ ] **Step 9: Rewrite the docker bash/grep timeout tests and add the regressions**

In `tests/test_docker_sandbox.py`.

Before:

```python
def test_grep_timeout_returns_error_text(started):
    sb, fake, run_dir = started

    def raise_timeout(argv, *, timeout, stdin=None):
        fake.calls.append((list(argv), timeout, stdin))
        raise DockerError("docker exec ... timed out after 40s")

    sb._run = raise_timeout
    out = sb.grep("foo", timeout=30)
    assert "timed out" in out.lower()
```

After:

```python
def test_grep_timeout_returns_error_text(started):
    sb, fake, run_dir = started

    def raise_timeout(argv, *, timeout, stdin=None):
        fake.calls.append((list(argv), timeout, stdin))
        raise DockerError("docker exec ... timed out after 40s", timed_out=True)

    sb._run = raise_timeout
    out = sb.grep("foo", timeout=30)
    # Unchanged wording (spec §4.2): a grep timeout is not a bash timeout.
    assert out == "ERROR: grep timed out after 30s — narrow the pattern or path."


def test_grep_generic_docker_error_is_not_reported_as_a_timeout(started):
    # Spec §4.2: before 0.9 EVERY DockerError out of grep rendered as "timed
    # out", so a killed container read as a slow search.
    sb, fake, run_dir = started

    def raise_failure(argv, *, timeout, stdin=None):
        fake.calls.append((list(argv), timeout, stdin))
        raise DockerError("No such container: dw-abc123")

    sb._run = raise_failure
    out = sb.grep("foo", timeout=30)
    assert out == "ERROR: grep failed: No such container: dw-abc123"
    assert "timed out" not in out
```

Before:

```python
def test_bash_timeout_returns_text_not_raise(started):
    sb, fake, run_dir = started
    real_run = sb._run
    def run_with_timeout(argv, *, timeout, stdin=None):
        # Only the model's own bash exec times out; docker top/inspect keep working.
        if "sleep 600" in " ".join(argv):
            fake.calls.append((list(argv), timeout, stdin))
            raise DockerError("docker exec ... timed out after 1s")
        return real_run(argv, timeout=timeout, stdin=stdin)
    sb._run = run_with_timeout
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    out = sb.bash("sleep 600", timeout=1)
    assert "timed out after 1s" in out
    assert not any(c[0][:1] == ["kill"] for c in fake.calls)  # healthy container: no reset
```

After:

```python
def test_bash_timeout_returns_text_not_raise(started):
    sb, fake, run_dir = started
    real_run = sb._run
    def run_with_timeout(argv, *, timeout, stdin=None):
        # Only the model's own bash exec times out; docker top/inspect keep working.
        if "sleep 600" in " ".join(argv):
            fake.calls.append((list(argv), timeout, stdin))
            raise DockerError("docker exec ... timed out after 1s", timed_out=True)
        return real_run(argv, timeout=timeout, stdin=stdin)
    sb._run = run_with_timeout
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    out = sb.bash("sleep 600", timeout=1)
    # Spec §4.2: the FULL canonical text -- a substring check would pass on the
    # non-timeout branch too, which is the bug this test now guards.
    assert out == (
        "ERROR: command timed out after 1s — it did not finish and its result is "
        "unknown. Re-run it with a larger timeout (up to 600) or split it into "
        "smaller commands; do not report it as passed.")
    assert not any(c[0][:1] == ["kill"] for c in fake.calls)  # healthy container: no reset


def test_bash_generic_docker_error_is_not_reported_as_a_timeout(started):
    sb, fake, run_dir = started
    real_run = sb._run

    def run_with_failure(argv, *, timeout, stdin=None):
        if "sleep 600" in " ".join(argv):
            fake.calls.append((list(argv), timeout, stdin))
            raise DockerError("No such container: dw-abc123")
        return real_run(argv, timeout=timeout, stdin=stdin)

    sb._run = run_with_failure
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    out = sb.bash("sleep 600", timeout=1)
    assert out == "ERROR: bash failed: No such container: dw-abc123"
    assert "timed out" not in out
```

- [ ] **Step 10: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_docker_sandbox.py -q -k "timeout or not_reported_as_a_timeout"`
Expected: 3 failed — `test_bash_timeout_returns_text_not_raise` (today's `ERROR: command timed out after 1s.`), `test_bash_generic_docker_error_is_not_reported_as_a_timeout` (today renders a timeout for any `DockerError`), and `test_grep_generic_docker_error_is_not_reported_as_a_timeout` (same for grep). `test_grep_timeout_returns_error_text` passes both before and after — its wording is unchanged by design.

- [ ] **Step 11: Discriminate real timeouts in `DockerSandbox`**

In `dirtywork/sandbox/docker.py`, the `..tools` import block (already touched in Task 2 — this is that block's current state).

Before:

```python
from ..tools import (
    MAX_BASH_CHARS,
    MAX_LIST_ENTRIES,
    MAX_READ_BYTES,
    MAX_WRITE_BYTES,
    _apply_edits_once,
    _cap,
    _check_write_size,
    _insert_once,
    _number_lines,
    _replace_once,
    describe_write,
)
```

After:

```python
from ..tools import (
    MAX_BASH_CHARS,
    MAX_LIST_ENTRIES,
    MAX_READ_BYTES,
    MAX_WRITE_BYTES,
    _apply_edits_once,
    _cap,
    _check_write_size,
    _insert_once,
    _number_lines,
    _replace_once,
    describe_write,
    timeout_result,
)
```

Then `grep`'s catch.

Before:

```python
        try:
            captured = self._run(argv, timeout=timeout + 10)
        except docker_cli.DockerError:
            return f"ERROR: grep timed out after {timeout}s — narrow the pattern or path."
```

After:

```python
        try:
            captured = self._run(argv, timeout=timeout + 10)
        except docker_cli.DockerError as e:
            # Spec §4.2: the same discrimination as bash below. A grep timeout
            # keeps its own (unchanged) wording and does NOT count toward
            # `timeouts` or the `timeout` nudge -- those are about commands the
            # WORKER ran, and grep is the harness searching on its behalf.
            if e.timed_out:
                return f"ERROR: grep timed out after {timeout}s — narrow the pattern or path."
            return f"ERROR: grep failed: {e}"
```

Then `bash`'s catch.

Before:

```python
        try:
            captured = self._run(argv, timeout=timeout + 10)
        except docker_cli.DockerError:
            if self.watchdog is not None:
                self.watchdog.note_bash_end()
            result = _cap(f"ERROR: command timed out after {timeout}s.", cap=MAX_BASH_CHARS)
            self._after_bash()
            return result
```

After:

```python
        try:
            captured = self._run(argv, timeout=timeout + 10)
        except docker_cli.DockerError as e:
            if self.watchdog is not None:
                self.watchdog.note_bash_end()
            # Spec §4.2: only a REAL expired timeout renders as the canonical
            # timeout text. Any other DockerError (a killed container, an exec
            # that could not start) gets the host's own non-timeout wording, so
            # an ordinary failure is never read as "it might still be running"
            # -- and never counts as a timeout downstream.
            result = (timeout_result(timeout) if e.timed_out
                      else f"ERROR: bash failed: {e}")
            self._after_bash()
            return result
```

- [ ] **Step 12: Run the docker sandbox tests**

Run: `/usr/bin/python3 -m pytest tests/test_docker_sandbox.py -q`
Expected: `88 passed` (86 after Task 2 + 2).

- [ ] **Step 13: Write the failing runner tests**

Append to `tests/test_runner.py`:

```python
class _TimeoutSandbox:
    """A sandbox whose bash always times out (or never does), with the canonical
    text the real backends produce. Only `bash` is needed: the registry calls
    exactly the method the tool dispatches to."""

    def __init__(self, timing_out=True):
        self.timing_out = timing_out
        self.commands = []

    def bash(self, command, timeout=120):
        self.commands.append(command)
        if self.timing_out:
            from dirtywork.tools import timeout_result
            return timeout_result(timeout)
        return "exit code: 0\nfine"


def _bash_call(call_id, command="sleep 999"):
    return _call(call_id, "bash", {"command": command})


def test_timed_out_is_flagged_on_the_event_and_absent_otherwise(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_bash_call("b1")]),
        _resp(content="done"),
    ])
    r = Runner(provider, registry, _TimeoutSandbox(), transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    events = [e for e in _events(tmp) if e["event"] == "tool_result"]
    assert events[0]["timed_out"] is True
    assert result.extra["timeouts"] == 1

    # and a normal bash result carries no such key at all (sparse, additive)
    transcript2 = Transcript(tmp / "t2.jsonl")
    registry2 = default_registry(transcript=transcript2)
    provider2 = FakeProvider([_resp(tool_calls=[_bash_call("b2")]), _resp(content="ok")])
    r2 = Runner(provider2, registry2, _TimeoutSandbox(timing_out=False), transcript2,
                model="m")
    result2 = r2.run("s", "t")
    transcript2.close()
    events2 = [json.loads(l) for l in (tmp / "t2.jsonl").read_text().splitlines()]
    tool_events = [e for e in events2 if e["event"] == "tool_result"]
    assert "timed_out" not in tool_events[0]
    assert result2.extra["timeouts"] == 0


def test_one_timeout_nudge_per_turn_even_with_two_timeouts(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_bash_call("b1", "sleep 1"), _bash_call("b2", "sleep 2")]),
        _resp(content="done"),
    ])
    r = Runner(provider, registry, _TimeoutSandbox(), transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    nudges = [e for e in _events(tmp) if e["event"] == "nudge"]
    assert [n["kind"] for n in nudges] == ["timeout"]
    assert nudges[0]["turn"] == 1
    assert result.extra["timeouts"] == 2        # the COUNT is per call, not per turn
    # the nudge text reached the model as the next user message
    second_request = provider.requests[1]
    assert second_request[-1]["role"] == "user"
    assert second_request[-1]["content"] == (
        "A command timed out and did not finish; its result is unknown. Re-run it "
        "with a larger timeout (up to 600 seconds) or split it into smaller "
        "commands. Do not report it as passed.")


def test_timeout_nudge_merges_with_the_stall_nudge(parts):
    wt, registry, sandbox, transcript, tmp = parts
    # stall_turns=2 nudges at turn 1 (2 // 2); the same turn also timed out.
    provider = FakeProvider([_resp(tool_calls=[_bash_call("b1")]),
                             _resp(content="done")])
    r = Runner(provider, registry, _TimeoutSandbox(), transcript, model="m",
               stall_turns=2)
    r.run("s", "t")
    transcript.close()
    kinds = [e["kind"] for e in _events(tmp) if e["event"] == "nudge"]
    # Both events are written; their ORDER follows the code path (check_progress
    # runs first, because it may end the run), while the merged MESSAGE leads
    # with the timeout, which is the more actionable of the two.
    assert sorted(kinds) == ["stall", "timeout"]
    text = provider.requests[1][-1]["content"]
    assert text.startswith("A command timed out and did not finish;")
    assert "No progress in the last 1 turns" in text
    assert "\n\n" in text                      # merged through _join_nudges


def test_no_timeout_nudge_when_the_turn_ends_the_run(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_bash_call("b1"),
                          _call("f1", "finish", {"summary": "done anyway"})]),
    ])
    r = Runner(provider, registry, _TimeoutSandbox(), transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert [e for e in _events(tmp) if e["event"] == "nudge"] == []
    assert result.extra["timeouts"] == 1       # the COUNT is unaffected by finishing


def test_a_verify_timeout_is_not_counted(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="all done")])
    box = _TimeoutSandbox()
    r = Runner(provider, registry, box, transcript, model="m",
               verify="npm test", verify_rounds=0)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "verify_failed"
    assert box.commands == ["npm test"]        # it DID run, and it DID time out
    assert result.extra["timeouts"] == 0       # spec §4.3: worker tool calls only
    assert [e for e in _events(tmp) if e["event"] == "nudge"] == []
```

- [ ] **Step 14: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_runner.py -q -k "timed_out or timeout_nudge or timeouts or timeout_is_not_counted"`
Expected: 5 failed — every one with `KeyError: 'timeouts'` or `KeyError: 'timed_out'`, plus `assert [] == ['timeout']` in the two nudge tests.

- [ ] **Step 15: Count, flag and nudge in the runner**

In `dirtywork/runner.py`, the imports.

Before:

```python
from .budget import BudgetExceeded
from .llm import LLMTimeout, MalformedResponse
from .providers import assistant_message, tool_message
from .sandbox import SandboxError
```

After:

```python
from .budget import BudgetExceeded
from .llm import LLMTimeout, MalformedResponse
from .providers import assistant_message, tool_message
from .sandbox import SandboxError
from .tools import is_timeout_result
```

Then the nudge constants.

Before:

```python
STALL_NUDGE = ("No progress in the last {n} turns: no file changed and no command produced "
               "new output. If the task is complete, commit (if asked) and call "
               "finish(summary=...); otherwise change your approach.")
```

After:

```python
STALL_NUDGE = ("No progress in the last {n} turns: no file changed and no command produced "
               "new output. If the task is complete, commit (if asked) and call "
               "finish(summary=...); otherwise change your approach.")
# Spec §4.3: a timed-out command is not a model mistake -- it never reaches
# FailureTracker and it RESETS the consecutive-failure streak like any other
# non-failing execution. This exists only so the model is told, in words, that
# the result it just got is not a result.
TIMEOUT_NUDGE = ("A command timed out and did not finish; its result is unknown. Re-run it "
                 "with a larger timeout (up to 600 seconds) or split it into smaller "
                 "commands. Do not report it as passed.")
```

Then `run()`'s locals.

Before:

```python
        turns = 0
        trimmed_turns = 0       # spec §2.2: turns on which trimming happened
        failures = FailureTracker()
```

After:

```python
        turns = 0
        trimmed_turns = 0       # spec §2.2: turns on which trimming happened
        timeouts = 0            # spec §4.3: worker bash calls that timed out
        failures = FailureTracker()
```

Then `finish`'s evidence dict.

Before:

```python
                           "trimmed_turns": trimmed_turns,
                           "context_window_source": self.context_window_source}
            finalize_error = None
```

After:

```python
                           "trimmed_turns": trimmed_turns,
                           "timeouts": timeouts,
                           "context_window_source": self.context_window_source}
            finalize_error = None
```

Then the per-turn flag.

Before:

```python
                pending_finish = None
                for tc in tool_calls:
```

After:

```python
                pending_finish = None
                timed_out_this_turn = False   # spec §4.3: at most ONE nudge per turn
                for tc in tool_calls:
```

Then the tool loop's bookkeeping.

Before:

```python
                    progress.note_call(name, self.registry.canonical_args(name, args), result)
                    if name == "bash":
                        command = args.get("command") if isinstance(args, dict) else None
                        if repeats.note_bash(command, result) == "stuck":
                            stuck = repeats.stuck_on()
                    self.transcript.write("tool_result", tool=name,
                                          args=raw_args[:500],
                                          result=self.registry.transcript_preview(name, result))
```

After:

```python
                    progress.note_call(name, self.registry.canonical_args(name, args), result)
                    timed_out_fields = {}
                    if name == "bash":
                        command = args.get("command") if isinstance(args, dict) else None
                        if repeats.note_bash(command, result) == "stuck":
                            stuck = repeats.stuck_on()
                        if is_timeout_result(result):
                            # Spec §4.3: worker bash TOOL CALLS only. The
                            # --verify command goes through sandbox.bash
                            # directly, is never a tool call and is never
                            # transcribed here, so it can never reach this.
                            timeouts += 1
                            timed_out_this_turn = True
                            timed_out_fields["timed_out"] = True
                    self.transcript.write("tool_result", tool=name,
                                          args=raw_args[:500],
                                          result=self.registry.transcript_preview(name, result),
                                          **timed_out_fields)
```

Then the end-of-turn nudge composition.

Before:

```python
                malformed_text = None
                if malformed_count > 0:
                    malformed_text = (f"{malformed_count} of your tool calls were malformed "
                                      "(unaddressable: no usable id/name) and were "
                                      "discarded. Re-issue them as valid tool calls.")
                nudge_text = _join_nudges(malformed_text, stall_text)
                if nudge_text:
                    messages.append({"role": "user", "content": nudge_text})
```

After:

```python
                malformed_text = None
                if malformed_count > 0:
                    malformed_text = (f"{malformed_count} of your tool calls were malformed "
                                      "(unaddressable: no usable id/name) and were "
                                      "discarded. Re-issue them as valid tool calls.")
                timeout_text = None
                if timed_out_this_turn:
                    # Emitted HERE, past the finish/stuck/abort exits above, so a
                    # turn that ENDS the run never writes a nudge -- the same
                    # place and the same reason as the stall nudge. Once per
                    # turn, however many commands timed out in it.
                    self.transcript.write("nudge", kind="timeout", turn=turns)
                    timeout_text = TIMEOUT_NUDGE
                nudge_text = _join_nudges(malformed_text, timeout_text, stall_text)
                if nudge_text:
                    messages.append({"role": "user", "content": nudge_text})
```

- [ ] **Step 16: Run the runner tests**

Run: `/usr/bin/python3 -m pytest tests/test_runner.py -q`
Expected: `118 passed` (113 after Task 4 + 5).

- [ ] **Step 17: Write the failing CLI test**

Append to `tests/test_main.py`:

```python
def test_timeouts_land_on_the_payload_run_end_and_run_json(tmp_path, monkeypatch, capsys):
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    assert m.main(["run", "--repo", str(repo), "--sandbox", "none", "t"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["timeouts"] == 0
    run_dir = Path(payload["run_dir"])
    assert json.loads((run_dir / "run.json").read_text())["timeouts"] == 0
    events = [json.loads(line) for line in
              (run_dir / "transcript.jsonl").read_text().splitlines()]
    assert [e for e in events if e["event"] == "run_end"][-1]["timeouts"] == 0
```

And extend the default-evidence map.

Before:

```python
    "verify": None,
    "trimmed_turns": 0,
}
```

After:

```python
    "verify": None,
    "trimmed_turns": 0,
    "timeouts": 0,
}
```

- [ ] **Step 18: Run it to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_main.py -q -k "timeouts or evidence"`
Expected: 3 failed — the new test with `KeyError: 'timeouts'`, and the two `_DEFAULT_EVIDENCE` tests with `AssertionError: 'timeouts' missing from payload`.

- [ ] **Step 19: Carry `timeouts` through the CLI contract**

In `dirtywork/__main__.py`, `_emit_result`'s docstring.

Before:

```python
    """The one place that shapes the stdout JSON contract — both the success
    path and every failure path funnel through here so the field set can
    never drift between them. Fix item 3: the six 0.8 evidence keys
    (`stuck_on`, `files_changed`, `files_changed_truncated`,
    `last_tool_result`, `last_assistant_text`, `verify`) are seeded with
    their null/empty defaults BEFORE `extra` is applied, so every payload —
    including `_fail_setup`'s and `_fail_run`'s, where `runner.run()` never
    returned and so never had real values for them — carries the full key
    set; the normal end-of-run path's real values (passed via `extra`) still
    override these defaults."""
```

After:

```python
    """The one place that shapes the stdout JSON contract — both the success
    path and every failure path funnel through here so the field set can
    never drift between them. Fix item 3: the six 0.8 evidence keys
    (`stuck_on`, `files_changed`, `files_changed_truncated`,
    `last_tool_result`, `last_assistant_text`, `verify`) are seeded with
    their null/empty defaults BEFORE `extra` is applied, so every payload —
    including `_fail_setup`'s and `_fail_run`'s, where `runner.run()` never
    returned and so never had real values for them — carries the full key
    set; the normal end-of-run path's real values (passed via `extra`) still
    override these defaults.

    0.9 seeds three more the same way (spec §6): `trimmed_turns` and `timeouts`
    (ints, default 0) and `context_window_source` (string). Both failure paths
    pass real values for all three through `_contract_fields`, so the seeds are
    only a backstop against a future caller that forgets."""
```

Then the seed dict.

Before:

```python
        "trimmed_turns": 0,
        "context_window_source": None,
    }
    payload.update(extra)
```

After:

```python
        "trimmed_turns": 0,
        "timeouts": 0,
        "context_window_source": None,
    }
    payload.update(extra)
```

Then `_contract_fields`.

Before:

```python
    return {"trimmed_turns": extra.get("trimmed_turns", 0),
            "context_window_source": ctx.context_window_source}
```

After:

```python
    return {"trimmed_turns": extra.get("trimmed_turns", 0),
            "timeouts": extra.get("timeouts", 0),
            "context_window_source": ctx.context_window_source}
```

- [ ] **Step 20: Run the CLI tests**

Run: `/usr/bin/python3 -m pytest tests/test_main.py -q`
Expected: `77 passed` (76 after Task 4 + 1).

- [ ] **Step 21: Write the failing `runs show` test**

Append to `tests/test_runs.py`:

```python
def test_timed_out_is_its_own_outcome_class_in_both_views(tmp_path, monkeypatch, capsys):
    from dirtywork.tools import timeout_result
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _write_run(tmp_path / "runs", "to1", {
        "slug": "to1", "status": "completed", "task": "t", "timeouts": 2,
    })
    (run_dir / "transcript.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"ts": "T", "event": "tool_result", "tool": "bash",
         "args": '{"command": "sleep 999"}', "result": timeout_result(120),
         "timed_out": True},
        {"ts": "T", "event": "tool_result", "tool": "bash",
         "args": '{"command": "false"}', "result": "ERROR: bash failed: boom"},
    ]) + "\n")

    assert runs.cmd_show(argparse.Namespace(slug="to1", diff=False)) == 0
    plain = capsys.readouterr().out
    assert "[timed out]" in plain
    assert "[ERROR]" in plain             # the ordinary failure keeps its class
    assert "timeouts: 2" in plain

    assert runs.cmd_show(argparse.Namespace(slug="to1", diff=False, markdown=True)) == 0
    md = capsys.readouterr().out
    assert "[timed out]" in md
    assert "- **timeouts:** 2" in md
```

- [ ] **Step 22: Run it to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_runs.py -q -k timed_out`
Expected: 1 failed — `AssertionError: assert '[timed out]' in …` (a timeout result classifies as `ERROR` today).

- [ ] **Step 23: Add the outcome class and the counter to `runs`**

In `dirtywork/runs.py`, the imports.

Before:

```python
from .sandbox import docker_args, docker_cli, export
from .workspace import WorkspaceError, host_worktree_dirty, snapshot_worktree
```

After:

```python
from .sandbox import docker_args, docker_cli, export
from .tools import is_timeout_result
from .workspace import WorkspaceError, host_worktree_dirty, snapshot_worktree
```

Then the field lists.

Before:

```python
SHOW_FIELDS = ("slug", "status", "sandbox", "task", "model", "provider",
               "context_window", "turns",
               "resumed_from", "resumed_by", "branch", "worktree", "started", "ended",
               "stuck_on", "files_changed", "verify", "trimmed_turns")
```

After:

```python
SHOW_FIELDS = ("slug", "status", "sandbox", "task", "model", "provider",
               "context_window", "turns",
               "resumed_from", "resumed_by", "branch", "worktree", "started", "ended",
               "stuck_on", "files_changed", "verify", "trimmed_turns", "timeouts")
```

Before:

```python
# `trimmed_turns` (0.9) is an int that is meaningful at 0, and _md_result's loop
# prints anything not None/"" -- so it renders "0" rather than disappearing,
# which is the point: "nothing was trimmed" is a fact worth reading.
MD_RESULT_FIELDS = ("status", "error", "export_status", "finalize_error",
                    "watchdog_violation", "trimmed_turns")
```

After:

```python
# `trimmed_turns` and `timeouts` (0.9) are ints that are meaningful at 0, and
# _md_result's loop prints anything not None/"" -- so they render "0" rather
# than disappearing, which is the point: "nothing was trimmed" and "nothing
# timed out" are facts worth reading.
MD_RESULT_FIELDS = ("status", "error", "export_status", "finalize_error",
                    "watchdog_violation", "trimmed_turns", "timeouts")
```

Then the outcome classifier.

Before:

```python
def _tool_result_outcome(result_text) -> str:
    """ERROR / BLOCKED / ok, from the tool result's leading token -- the one
    classification both the text timeline and the Markdown export use."""
    text = str(result_text or "")
    if text.startswith("ERROR"):
        return "ERROR"
    if text.startswith("BLOCKED"):
        return "BLOCKED"
    return "ok"
```

After:

```python
def _tool_result_outcome(result_text) -> str:
    """'timed out' / ERROR / BLOCKED / ok, from the tool result's leading token
    -- the one classification both the text timeline and the Markdown export
    use, composed into `[{outcome}]` by each. The timeout class is checked FIRST
    because a timed-out result also starts with ERROR, and "the command never
    finished, so its result is unknown" is a different thing to an operator than
    "the command failed" (spec §4.3). No emoji, one rule, both views."""
    text = str(result_text or "")
    if is_timeout_result(text):
        return "timed out"
    if text.startswith("ERROR"):
        return "ERROR"
    if text.startswith("BLOCKED"):
        return "BLOCKED"
    return "ok"
```

- [ ] **Step 24: Run the runs tests**

Run: `/usr/bin/python3 -m pytest tests/test_runs.py -q`
Expected: `88 passed` (87 after Task 4 + 1).

- [ ] **Step 25: Write the failing bench tests and update the two 3-tuple assertions**

In `tests/test_bench.py`, the existing detail-table assertion.

Before:

```python
    assert "2/1/0/0" in out            # m2's nudge counts
```

After:

```python
    assert "2/1/0/0/0" in out          # m2's nudge counts (0.9 added the timeout kind)
```

The compare test's missing-side assertion.

Before:

```python
    assert "- -> 0/0/0" in out and "- -> 0/0/0 (" not in out   # no delta against an unknown side
```

After:

```python
    assert "- -> 0/0/0/0" in out and "- -> 0/0/0/0 (" not in out  # no delta against an unknown side
```

The compare test's partial-side assertion.

Before:

```python
    assert "1/0/0* -> 0/0/0 (-1/0/0)" in out   # A partial (1 of 2 rows), B full; delta on the known counts
```

After:

```python
    assert "1/0/0/0* -> 0/0/0/0 (-1/0/0/0)" in out   # A partial (1 of 2 rows), B full; delta on the known counts
```

Then append:

```python
def test_harness_timeouts_come_from_the_payload_and_are_excluded_from_empty_reply(
        tmp_path, monkeypatch):
    events = [
        {"event": "nudge", "kind": "timeout", "turn": 3},
        {"event": "nudge", "kind": "empty", "turn": 4},
        {"event": "run_end", "status": "completed"},
    ]
    _fake_run_environment(tmp_path, monkeypatch, transcript_events=events, payload={
        "status": "completed", "turns": 5, "timeouts": 4, "usage": {},
        "final_message": "done"})
    row = bench.run_one_bench_case("m1", "sh-fix-script", 0, provider=None, base_url=None,
                                   stamp="s", max_turns=40, timeout=1800)
    harness = row["harness"]
    assert harness["nudge_timeout"] == 1        # one nudge event on that turn
    assert harness["timeouts"] == 4             # the RUNNER's count, off the payload
    assert harness["empty_reply"] == 1          # the `empty` nudge only, not the timeout


def test_summarize_reports_timeouts_in_the_failures_cell_and_the_legends(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bench.rundir, "RUNS_DIR", tmp_path / "runs")
    results = tmp_path / "r.jsonl"
    harness = {"nudge_stall": 0, "nudge_empty": 0, "nudge_truncated": 0,
               "nudge_text_tool_call": 0, "nudge_timeout": 1, "nudge_other": 0,
               "empty_reply": 0, "timeouts": 3, "stalled": 0, "max_turns": 0,
               "sandbox_error": 0, "abort_kind": None}
    results.write_text(json.dumps(_result_row(slug=None, harness=harness)) + "\n")
    assert bench.cmd_summarize(argparse.Namespace(file=str(results))) == 0
    out = capsys.readouterr().out
    assert "nudges: stall/empty/truncated/text_tool_call/timeout" in out
    assert "timeouts=3" in out

    other = tmp_path / "b.jsonl"
    other.write_text(json.dumps(_result_row(slug=None, harness=harness)) + "\n")
    assert bench.cmd_summarize(argparse.Namespace(file=str(results),
                                                  compare=str(other))) == 0
    compare_out = capsys.readouterr().out
    assert "harness: nudges/stalled/max_turns/timeouts" in compare_out
    assert "1/0/0/3 -> 1/0/0/3 (0/0/0/0)" in compare_out
```

- [ ] **Step 26: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_bench.py -q`
Expected: 5 failed — the two new tests (`KeyError: 'nudge_timeout'` and `AssertionError` on the legend), plus the three edited assertions (`2/1/0/0/0`, `- -> 0/0/0/0`, `1/0/0/0* -> …`), which still render 3-tuples.

- [ ] **Step 27: Add the timeout class to `bench`**

In `dirtywork/bench.py`, the nudge kinds.

Before:

```python
NUDGE_KINDS = ("stall", "empty", "truncated", "text_tool_call")
```

After:

```python
# Order is the order the NUDGES column prints in; the plain summary's legend
# line spells it out and must stay in step.
NUDGE_KINDS = ("stall", "empty", "truncated", "text_tool_call", "timeout")
```

Then `_harness_failures`.

Before:

```python
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
```

After:

```python
def _harness_failures(counts: dict, status, final_message, timeouts=0) -> dict:
    """The harness-failure classes the scoreboard reports. `empty_reply` is the
    FailureTracker kind: the runner records exactly one per non-stall nudge --
    EXCEPT 0.9's `timeout` nudge, which is not a FailureTracker event at all
    (a timed-out command is not a model mistake), so it is excluded here too and
    gets its own class.

    `timeouts` is the RUNNER's own count, taken from the payload by the caller
    and never re-derived from the nudge events: a turn with two timed-out
    commands emits ONE nudge and counts TWO timeouts."""
    non_stall = sum(counts[f"nudge_{kind}"] for kind in NUDGE_KINDS
                    if kind not in ("stall", "timeout"))
    failures = {f"nudge_{kind}": counts[f"nudge_{kind}"] for kind in NUDGE_KINDS}
    failures["nudge_other"] = counts["nudge_other"]
    failures["empty_reply"] = non_stall
    failures["timeouts"] = int(timeouts or 0)
    for name in ("stalled", "max_turns", "sandbox_error"):
        failures[name] = 1 if status == name else 0
    failures["abort_kind"] = _abort_kind(final_message)
    return failures
```

Then the row.

Before:

```python
        "harness": _harness_failures(counts, status, payload.get("final_message")),
```

After:

```python
        "harness": _harness_failures(counts, status, payload.get("final_message"),
                                     payload.get("timeouts", 0)),
```

Then the failures cell.

Before:

```python
    if harness.get("empty_reply"):
        parts.append(f"empty_reply={harness['empty_reply']}")
    if harness.get("abort_kind"):
```

After:

```python
    if harness.get("empty_reply"):
        parts.append(f"empty_reply={harness['empty_reply']}")
    if harness.get("timeouts"):
        parts.append(f"timeouts={harness['timeouts']}")
    if harness.get("abort_kind"):
```

Then the aggregate.

Before:

```python
        "stalled": sum(1 for h in harness_dicts if h.get("stalled")),
        "max_turns": sum(1 for h in harness_dicts if h.get("max_turns")),
    }
```

After:

```python
        "stalled": sum(1 for h in harness_dicts if h.get("stalled")),
        "max_turns": sum(1 for h in harness_dicts if h.get("max_turns")),
        # A COUNT, not a run tally like stalled/max_turns: one run can time out
        # many commands, and that is the number worth comparing between sweeps.
        "timeouts": sum((h.get("timeouts") or 0) for h in harness_dicts),
    }
```

Then the harness cell and its tuple.

Before:

```python
def _harness_cell(summary) -> str:
    """nudges/stalled/max_turns for one side, compact. MISSING when none of
    this side's rows carried a harness dict (bench_error rows only -- P2-2);
    suffixed with `*` when only SOME of them did, so partial knowledge is
    visible instead of silently reading like a clean zero."""
```

After:

```python
def _harness_cell(summary) -> str:
    """nudges/stalled/max_turns/timeouts for one side, compact. MISSING when
    none of this side's rows carried a harness dict (bench_error rows only --
    P2-2); suffixed with `*` when only SOME of them did, so partial knowledge is
    visible instead of silently reading like a clean zero."""
```

Before:

```python
def _harness_counts(summary) -> tuple:
    return (summary["nudges"], summary["stalled"], summary["max_turns"])
```

After:

```python
def _harness_counts(summary) -> tuple:
    return (summary["nudges"], summary["stalled"], summary["max_turns"],
            summary["timeouts"])
```

Then the `--compare` legend.

Before:

```python
    print("harness: nudges/stalled/max_turns")
```

After:

```python
    print("harness: nudges/stalled/max_turns/timeouts")
```

And the plain legend block.

Before:

```python
    if detail:
        print("nudges: stall/empty/truncated/text_tool_call")
        print(format_table(DETAIL_COLUMNS, detail))
        print()
```

After:

```python
    if detail:
        print("nudges: stall/empty/truncated/text_tool_call/timeout")
        print("failures: harness classes for the run; timeouts=N counts timed-out bash "
              "calls (excluded from empty_reply, which is the FailureTracker kind)")
        print(format_table(DETAIL_COLUMNS, detail))
        print()
```

- [ ] **Step 28: Run the bench tests**

Run: `/usr/bin/python3 -m pytest tests/test_bench.py -q`
Expected: `43 passed` (41 after Task 3 + 2).

- [ ] **Step 29: Document the timeout — `docs/operating.md`**

Insert a new subsection immediately before `### Resuming a run`.

Before:

```
### Resuming a run

A run that ended early (`max_turns`, `stalled`, `timeout`, `interrupted`, a
crash) keeps its worktree. Continue it on the same worktree and branch:
```

After:

```
#### The bash tool

`bash(command, timeout=120)` runs one shell command in the worktree. The
per-call `timeout` is the model's to set (default 120 s, maximum 600 s, clamped;
`--timeout` is the separate whole-run wall clock). A command that hits its
timeout is killed with its whole process group and returns exactly:

    ERROR: command timed out after 120s — it did not finish and its result is unknown. Re-run it with a larger timeout (up to 600) or split it into smaller commands; do not report it as passed.

**No partial output is appended**, deliberately: the host backend could produce
a tail and the container backend cannot, and a tail is exactly what a small
model reads as "the command's result" when the command never finished. The same
turn also gets a one-line nudge telling the worker in words that the result is
unknown and must not be reported as a pass, the `tool_result` transcript event
carries `timed_out: true`, and the run's `timeouts` counter rises — visible on
the stdout JSON, in `run_end`, in `run.json`, in `dirtywork runs show`
(where the call renders `[timed out]` rather than `[ERROR]`), and as its own
column in `dirtywork bench summarize`.

A timeout is **not** counted as a model failure: it does not feed the
consecutive-failure abort, and it resets that streak like any other tool call
that executed. It does count toward `--stuck-repeats` — the same command timing
out four times in a row ends the run `stuck`, which is the honest outcome.

In docker mode, only a real expired timeout renders that way; any other docker
failure (a killed container, an exec that could not start) returns
`ERROR: bash failed: …` instead, so an ordinary failure is never read as "it
might still be running".

### Resuming a run

A run that ended early (`max_turns`, `stalled`, `timeout`, `interrupted`, a
crash) keeps its worktree. Continue it on the same worktree and branch:
```

- [ ] **Step 30: Document the fields — `docs/transcript-schema.md`**

The `tool_result` table, after the `result` row.

Before:

```
A `finish(summary=…)` call is an ordinary tool call: it appears in the
`assistant` event's `tool_calls` and produces a `tool_result` whose `result` is
```

After:

```
| `timed_out` | | ✓ | boolean | 0.9: `true` on a `bash` tool result whose command hit its timeout. **Sparse** — the key is absent, not `false`, on every other result, including a `grep` timeout (a different wording and a different meaning: the harness's search, not the worker's command) and the `--verify` command (not a tool call, so it produces no `tool_result` at all; its outcome is in `verify`) |

A `finish(summary=…)` call is an ordinary tool call: it appears in the
`assistant` event's `tool_calls` and produces a `tool_result` whose `result` is
```

The `nudge` table's `kind` row.

Before:

```
| `kind` | | ✓ | string | `truncated` (the reply hit the token limit), `empty` (no tool call and no answer), `text_tool_call` (a tool call written as prose instead of through the tools API), `stall` (no progress for `--stall-turns // 2` turns) |
```

After:

```
| `kind` | | ✓ | string | `truncated` (the reply hit the token limit), `empty` (no tool call and no answer), `text_tool_call` (a tool call written as prose instead of through the tools API), `stall` (no progress for `--stall-turns // 2` turns), `timeout` (0.9: at least one `bash` command timed out on this turn — exactly one per turn however many timed out, and only on a turn that continues; a timeout is not a `FailureTracker` event) |
```

The `run_end` table, after the `context_window_source` row.

Before:

```
| `context_window_source` | | ✓ | string | **always** — 0.9: the same value as `run_start.context_window_source`, repeated at the end so a consumer that reads only the last line still knows where the window came from |
```

After:

```
| `context_window_source` | | ✓ | string | **always** — 0.9: the same value as `run_start.context_window_source`, repeated at the end so a consumer that reads only the last line still knows where the window came from |
| `timeouts` | | ✓ | integer | **always** — 0.9: how many `bash` TOOL CALLS timed out during the run (per call, not per turn). `grep` timeouts and the `--verify` command are excluded. `0` on a run where nothing timed out, and on the two failure paths where the runner never returned |
```

The `run.json` table, after the `trimmed_turns` row.

Before:

```
| `trimmed_turns` | end | 0.9: turns on which at least one tool result was trimmed to fit the context budget; `0` when nothing was trimmed |
```

After:

```
| `trimmed_turns` | end | 0.9: turns on which at least one tool result was trimmed to fit the context budget; `0` when nothing was trimmed |
| `timeouts` | end | 0.9: how many `bash` tool calls timed out; `0` when none did |
```

- [ ] **Step 31: Teach the doc test about the kind and the field**

In `tests/test_transcript_schema.py`.

Before:

```python
NUDGE_KINDS = ["truncated", "empty", "text_tool_call", "stall"]
```

After:

```python
NUDGE_KINDS = ["truncated", "empty", "text_tool_call", "stall", "timeout"]
```

Before:

```python
                  "files_changed_truncated", "last_tool_result", "last_assistant_text",
                  "verify", "trimmed_turns"]
```

After:

```python
                  "files_changed_truncated", "last_tool_result", "last_assistant_text",
                  "verify", "trimmed_turns", "timeouts"]
```

- [ ] **Step 32: Run the full suite**

Run: `/usr/bin/python3 -m pytest -q`
Expected: `1006 passed, 1 skipped, 18 deselected` (993 + 13).

- [ ] **Step 33: Commit**

```bash
git add dirtywork/tools.py dirtywork/sandbox/docker_cli.py dirtywork/sandbox/docker.py dirtywork/runner.py dirtywork/__main__.py dirtywork/runs.py dirtywork/bench.py tests/test_tools_bash.py tests/test_docker_cli.py tests/test_docker_sandbox.py tests/test_runner.py tests/test_main.py tests/test_runs.py tests/test_bench.py tests/test_transcript_schema.py docs/operating.md docs/transcript-schema.md
git commit -m "feat: a timed-out command says so — canonical text, nudge, counter, scoreboard class"
```

---

### Task 6: Windows advisory CI leg — measure what breaks, claim nothing (spec §5)

**Files:**
- Create: `tools/junit_summary.py`
- Create: `tests/test_junit_summary.py`
- Modify: `.github/workflows/ci.yml` (new `windows-unit` job between `test` and `docker-live`; `gate` untouched)
- Modify: `tests/test_budget.py` (`:1-8` imports; `:73` the skipif)
- Modify: `README.md` (the Platform support table's Windows row)
- Modify: `docs/security.md` (the Windows callout)

**Interfaces:**
- Consumes: `xml.etree.ElementTree.fromstring` / `ParseError`, `os.environ`, `sys.argv` (stdlib only — the script must run on a bare `windows-latest` runner with nothing but Python installed, and it must never import `dirtywork` or `pytest`).
- Produces:
  - `tools/junit_summary.py` with `OUTCOMES`, `summarize(xml_text) -> dict`, `render(table) -> str`, `main(argv=None) -> int`
  - CI job `windows-unit` (advisory: `continue-on-error: true`, absent from `gate`'s `needs`)
- Decision (recorded deviation from the "no new test files" convention): `tests/test_junit_summary.py` is a new module because nothing under `tools/` has one (`tools/ci_sandbox_smoke.py` has none either) and every existing module is about `dirtywork/`, not about a CI helper.
- Decision (recorded): the test loads the script through `importlib.util.spec_from_file_location` rather than importing it as a package member — `tools/` is deliberately not a package (no `__init__.py`), and adding one would put it in the installable surface.
- **No production code is changed for Windows in 0.9.** `os.O_NOFOLLOW`, the process-group kills, the docker paths — all untouched. The deliverable is the table.

- [ ] **Step 1: Write the failing summary tests**

Create `tests/test_junit_summary.py`:

```python
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "junit_summary.py"


def _load():
    """tools/ is deliberately not a package (adding __init__.py would put a CI
    helper into the installable surface), so the script is loaded by path."""
    spec = importlib.util.spec_from_file_location("junit_summary", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="1" failures="1" skipped="1" tests="5">
    <testcase classname="tests.test_a" name="test_ok" file="tests/test_a.py"/>
    <testcase classname="tests.test_a" name="test_bad" file="tests/test_a.py">
      <failure message="boom">trace</failure>
    </testcase>
    <testcase classname="tests.test_b" name="test_err" file="tests\\\\test_b.py">
      <error message="kaboom">trace</error>
    </testcase>
    <testcase classname="tests.test_b" name="test_skip" file="tests\\\\test_b.py">
      <skipped message="no fifo"/>
    </testcase>
    <testcase classname="tests.test_c" name="test_ok2"/>
  </testsuite>
</testsuites>
"""


def test_summarize_counts_outcomes_per_file():
    module = _load()
    table = module.summarize(SAMPLE)
    assert table["tests/test_a.py"] == {"passed": 1, "failed": 1, "error": 0, "skipped": 0}
    # Windows backslashes are normalized so the table sorts and reads like the repo
    assert table["tests/test_b.py"] == {"passed": 0, "failed": 0, "error": 1, "skipped": 1}
    # a writer that emits only classname still gets a row
    assert table["tests.py"] == {"passed": 1, "failed": 0, "error": 0, "skipped": 0}


def test_render_is_a_sorted_markdown_table_with_a_total_row():
    module = _load()
    text = module.render(module.summarize(SAMPLE))
    lines = text.splitlines()
    assert lines[0] == "| file | passed | failed | error | skipped |"
    assert lines[1] == "|---|---:|---:|---:|---:|"
    assert lines[2].startswith("| tests.py |")
    assert lines[3].startswith("| tests/test_a.py |")
    assert lines[4].startswith("| tests/test_b.py |")
    assert lines[-1] == "| **total** | 2 | 1 | 1 | 1 |"


def test_main_prints_the_table_and_appends_to_the_step_summary(tmp_path, monkeypatch, capsys):
    module = _load()
    xml = tmp_path / "junit.xml"
    xml.write_text(SAMPLE, encoding="utf-8")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert module.main([str(xml)]) == 0
    out = capsys.readouterr().out
    assert "| **total** | 2 | 1 | 1 | 1 |" in out
    written = summary.read_text(encoding="utf-8")
    assert written.startswith("## Windows unit suite (advisory)")
    assert "| **total** | 2 | 1 | 1 | 1 |" in written


def test_main_without_a_step_summary_still_prints(tmp_path, monkeypatch, capsys):
    module = _load()
    xml = tmp_path / "junit.xml"
    xml.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert module.main([str(xml)]) == 0
    assert "| **total** | 2 | 1 | 1 | 1 |" in capsys.readouterr().out


@pytest.mark.parametrize("argv,message", [
    ([], "usage:"),
    (["a.xml", "b.xml"], "usage:"),
    (["/nonexistent/junit.xml"], "cannot read"),
])
def test_main_refuses_bad_input_with_exit_2(argv, message, capsys):
    module = _load()
    assert module.main(argv) == 2
    assert message in capsys.readouterr().err


def test_main_reports_unparseable_xml(tmp_path, capsys):
    module = _load()
    xml = tmp_path / "junit.xml"
    xml.write_text("<not-xml", encoding="utf-8")
    assert module.main([str(xml)]) == 2
    assert "not valid JUnit XML" in capsys.readouterr().err
```

- [ ] **Step 2: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_junit_summary.py -q`
Expected: 8 failed — every one with `FileNotFoundError`/`AttributeError` out of `_load()` (`tools/junit_summary.py` does not exist).

- [ ] **Step 3: Write the script**

Create `tools/junit_summary.py`:

```python
#!/usr/bin/env python3
"""Summarize a JUnit XML report as a per-file pass/fail/error/skip table.

Written for the advisory Windows CI leg (spec §5, issue #24). The point of that
job is the TABLE -- which test files actually break on Windows -- not a verdict,
so this script never exits non-zero because tests failed; it exits 2 only when
it cannot do its own job (bad arguments, unreadable file, unparseable XML).

Stdlib only, and it imports neither `dirtywork` nor `pytest`: it runs on a bare
runner against the XML pytest already wrote, so a collection error that stops
pytest from importing the package cannot also stop the report.
"""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET

OUTCOMES = ("passed", "failed", "error", "skipped")
HEADING = "## Windows unit suite (advisory)"


def _outcome(case) -> str:
    """One testcase's outcome, from the child element pytest writes for it.
    A case with none of them passed."""
    if case.find("failure") is not None:
        return "failed"
    if case.find("error") is not None:
        return "error"
    if case.find("skipped") is not None:
        return "skipped"
    return "passed"


def _file_of(case) -> str:
    """The test file a case belongs to. pytest sets `file` on every testcase;
    a writer that only sets `classname` (a dotted module path) still gets a
    row. Backslashes are normalized so a Windows run's table reads and sorts
    like the repository's own paths."""
    path = case.get("file")
    if path:
        return path.replace("\\", "/")
    classname = case.get("classname") or ""
    module = classname.split(".")[0] if classname else "unknown"
    return module + ".py"


def summarize(xml_text: str) -> dict:
    """{file: {passed, failed, error, skipped}} for one JUnit XML document.
    Accepts either a <testsuites> wrapper or a bare <testsuite> root."""
    root = ET.fromstring(xml_text)
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    table = {}
    for suite in suites:
        for case in suite.iter("testcase"):
            counts = table.setdefault(_file_of(case), dict.fromkeys(OUTCOMES, 0))
            counts[_outcome(case)] += 1
    return table


def render(table: dict) -> str:
    """A Markdown table sorted by file, with a total row. Markdown because
    GitHub renders the step summary as Markdown, and it is still perfectly
    readable as plain text in the job log."""
    lines = ["| file | passed | failed | error | skipped |",
             "|---|---:|---:|---:|---:|"]
    totals = dict.fromkeys(OUTCOMES, 0)
    for path in sorted(table):
        counts = table[path]
        for name in OUTCOMES:
            totals[name] += counts[name]
        lines.append("| {} | {} | {} | {} | {} |".format(
            path, counts["passed"], counts["failed"], counts["error"],
            counts["skipped"]))
    lines.append("| **total** | {} | {} | {} | {} |".format(
        totals["passed"], totals["failed"], totals["error"], totals["skipped"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: junit_summary.py <junit.xml>", file=sys.stderr)
        return 2
    try:
        with open(args[0], encoding="utf-8") as fh:
            xml_text = fh.read()
    except OSError as e:
        print(f"error: cannot read '{args[0]}': {e}", file=sys.stderr)
        return 2
    try:
        table = summarize(xml_text)
    except ET.ParseError as e:
        print(f"error: '{args[0]}' is not valid JUnit XML: {e}", file=sys.stderr)
        return 2
    text = render(table)
    print(text)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(HEADING + "\n\n" + text + "\n")
        except OSError as e:
            # The table already went to stdout; failing the step over the
            # decoration would be worse than losing it.
            print(f"warning: cannot append to GITHUB_STEP_SUMMARY: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the summary tests**

Run: `/usr/bin/python3 -m pytest tests/test_junit_summary.py -q`
Expected: `8 passed`.

- [ ] **Step 5: Make `tests/test_budget.py` importable on Windows**

The `skipif` calls `os.getuid()` at **collection** time, and `os.getuid` does not
exist on Windows — so today the whole module fails to collect there, which would
poison the very table this job exists to produce.

In `tests/test_budget.py`, the imports.

Before:

```python
from __future__ import annotations

import os
from pathlib import Path
```

After:

```python
from __future__ import annotations

import os
import sys
from pathlib import Path
```

And the marker.

Before:

```python
@pytest.mark.skipif(os.getuid() == 0, reason="root ignores directory permissions")
```

After:

```python
# `os.getuid` does not exist on Windows, and this decorator is evaluated at
# COLLECTION time -- calling it unguarded makes the whole module fail to import
# there, which would hide every other budget test from the advisory Windows run
# (spec §5). `chmod 000` does not deny access on Windows either, so the test is
# skipped rather than fixed.
@pytest.mark.skipif(getattr(os, "getuid", lambda: -1)() == 0 or sys.platform == "win32",
                    reason="root (or Windows, where chmod 000 does not deny access) "
                           "ignores directory permissions")
```

- [ ] **Step 6: Run the budget tests**

Run: `/usr/bin/python3 -m pytest tests/test_budget.py -q`
Expected: `9 passed` — unchanged on macOS/Linux (`os.getuid` exists and is not 0, `sys.platform` is not `win32`), which is the point: the guard changes nothing off Windows.

- [ ] **Step 7: Add the advisory CI job**

In `.github/workflows/ci.yml`, between the `test` job and the `docker-live` job.

Before:

```yaml
      - run: python -m pytest -v

  docker-live:
```

After:

```yaml
      - run: python -m pytest -v

  windows-unit:
    # ADVISORY ONLY (spec §5, issue #24). Windows is NOT supported; this leg
    # exists to MEASURE what breaks there. It is a separate job, not a matrix
    # entry of `test`, and `gate` does not list it in `needs`, so it can never
    # block a merge or a release. `continue-on-error` at the job level keeps a
    # red result from failing the workflow; the same flag on the pytest step
    # (plus `if: always()` below) is what lets the report and the artifact
    # still be produced when tests fail -- which is the expected outcome.
    name: pytest (3.13, windows-latest) — advisory
    runs-on: windows-latest
    continue-on-error: true
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7
        with:
          python-version: "3.13"
      - run: python -m pip install pytest
      # pyproject.toml's addopts deselect the live/docker markers here exactly as
      # they do everywhere else -- pytest reads them itself, so no shell quoting
      # is involved and PowerShell cannot mangle them.
      - run: python -m pytest --junitxml=junit-windows.xml
        continue-on-error: true
      - name: Per-file summary
        if: always()
        run: python tools/junit_summary.py junit-windows.xml
      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7
        if: always()
        with:
          name: junit-windows
          path: junit-windows.xml

  docker-live:
```

- [ ] **Step 8: Confirm the gate is untouched**

Run:

```bash
grep -n "needs:" .github/workflows/ci.yml
/usr/bin/python3 -c "import re,sys; t=open('.github/workflows/ci.yml').read(); assert 'needs: [test, docker-live]' in t; assert 'windows-unit' not in t.split('gate:')[1]; print('gate unchanged')"
```
Expected: the grep prints exactly `needs: [test, docker-live]` on the `gate` job, and the assertion prints `gate unchanged`. The advisory job must not appear anywhere under `gate:`.

- [ ] **Step 9: Say plainly that it is advisory — `README.md`**

The Platform support table's Windows row.

Before:

```
| Unsupported | Windows | until a Windows integration suite passes (see the note in [Security & trust](https://github.com/JimboSchneider/dirtywork/blob/main/docs/security.md#security--trust)) |
```

After:

```
| Unsupported | Windows | the unit suite also runs on `windows-latest` in CI as an advisory (allowed-to-fail) job that publishes a per-file pass/fail/error/skip table; Windows remains unsupported until an integration suite passes (see the note in [Security & trust](https://github.com/JimboSchneider/dirtywork/blob/main/docs/security.md#security--trust)) |
```

- [ ] **Step 10: Say the same in `docs/security.md`**

Before:

```
> **Windows: designed for Docker Desktop on Windows; not supported until a
> Windows integration suite passes.** Items that need real Windows testing:
> Git for Windows paths and `\\?\` handling, `docker` CLI behavior, uid
> `1000:1000`, symlink-as-file export, case-insensitivity, long paths,
> `core.symlinks=false`, `core.longpaths`.
```

After:

```
> **Windows: designed for Docker Desktop on Windows; not supported until a
> Windows integration suite passes.** Since 0.9 the unit suite also runs on
> `windows-latest` in CI as an **advisory (allowed-to-fail) job**: it is a
> separate job that the release gate does not require, and it publishes a
> per-file pass/fail/error/skip table (`tools/junit_summary.py`) plus the raw
> JUnit XML as an artifact. That measures the gap; it does not close it, and no
> production code is changed for Windows. Items that still need real Windows
> testing: Git for Windows paths and `\\?\` handling, `docker` CLI behavior, uid
> `1000:1000`, symlink-as-file export, case-insensitivity, long paths,
> `core.symlinks=false`, `core.longpaths`.
```

- [ ] **Step 11: Run the full suite**

Run: `/usr/bin/python3 -m pytest -q`
Expected: `1014 passed, 1 skipped, 18 deselected` (1006 + 8).

- [ ] **Step 12: Commit**

```bash
git add tools/junit_summary.py tests/test_junit_summary.py tests/test_budget.py .github/workflows/ci.yml README.md docs/security.md
git commit -m "ci: advisory windows-latest unit leg with a per-file JUnit summary"
```

After the first run of this job on the branch, post the per-file table on issue
#24 (a manual step for the PR author — the step summary and the uploaded
artifact make it reproducible).

---

### Task 7: 0.9.0 wrap-up — image tag, version, consolidated contract, final gate (spec §6)

**Files:**
- Modify: `dirtywork/sandbox/docker_args.py` (`:8-22` `DEFAULT_IMAGE`/`PINNED_DIGEST`)
- Modify: `.github/workflows/ci.yml` (`:58` docker-live tag)
- Modify: `docker/README.md` (every `:0.8` mention)
- Modify: `docs/machine-contract.md` (`--image` mentions; the consolidated example JSON and the prose under it)
- Modify: `docs/transcript-schema.md` (the stdout field list)
- Modify: `README.md` (Machine contract pointer prose)
- Modify: `tests/test_docker_args.py` (`:21-26`)
- Modify: `pyproject.toml` (`:7`), `dirtywork/__init__.py` (`:1`)

**Interfaces:**
- Consumes: `docker_args.DEFAULT_IMAGE`, `docker_args.PINNED_DIGEST`, `docker_args.pin_for` (all `docker_args.py:8-22`); `tests/test_transcript_schema.py::test_version_is_in_step_with_pyproject` (asserts the two version strings agree — the gate for Step 8).
- Produces: no new symbol. `DEFAULT_IMAGE == "ghcr.io/jimboschneider/dirtywork-worker:0.9"`, `PINNED_DIGEST is None`, `dirtywork.__version__ == "0.9.0"`.
- Note (spec §6 names `tests/test_docker_image.py`): that module asserts nothing about the default tag — its own build tag is `…:0.5-test` and its only `0.8` mention is a historical comment about the package additions issue #30 brought in. Step 3 proves this with a grep and leaves the file untouched.

- [ ] **Step 1: Find every occurrence of the tag**

Run:

```bash
grep -rn "dirtywork-worker:0\.8\|my-worker:0\.8" --include="*.py" --include="*.md" --include="*.yml" . | grep -v docs/superpowers
```
Expected: exactly 22 lines — `dirtywork/sandbox/docker_args.py` × 2 (the constant and the `docker pull` line in its comment), `.github/workflows/ci.yml` × 1, `tests/test_docker_args.py` × 1, `docker/README.md` × 14, `docs/machine-contract.md` × 4. The `grep -v` excludes this plan and the specs, whose **before** blocks quote the old tag on purpose. (`docker/README.md`'s `0.8.0`/`0.8.1` mentions are release numbers in the pinning-policy prose, not image tags; Step 4 edits them as policy text, not as a tag rename.)

- [ ] **Step 2: Move the default image and unpin**

In `dirtywork/sandbox/docker_args.py`.

Before:

```python
DEFAULT_IMAGE = "ghcr.io/jimboschneider/dirtywork-worker:0.8"
# Pinned for 0.8.1: the multi-arch index digest of the :0.8 image published by
# the v0.8.0 release (publish-image.yml run 32268219134), resolved with
# `docker pull ghcr.io/jimboschneider/dirtywork-worker:0.8` and cross-checked
# against `docker image inspect --format '{{json .RepoDigests}}'`,
# `docker buildx imagetools inspect` and the publish job log (all four agree);
# docker/README.md documents the procedure. This only pins a REGISTRY digest --
# resolve_image() enforces it against a *pulled* DEFAULT_IMAGE only; a locally
# built/loaded image warns instead of refusing, and a user-supplied --image is
# never checked. MUST be re-resolved whenever the :0.8 tag is re-pushed.
# (0.7.x shipped unpinned; 0.6.x pinned :0.6 at
# sha256:1f7b98898001b7064d8db396a8a5a1a324df4ce48692597fcd4381ea90e4354a;
# 0.5.x pinned :0.5 at
# sha256:3b8d019a2f20a9df55a72ed51139076f02f2feb597243a69519bc41db1029648.)
PINNED_DIGEST: str | None = "sha256:d8ca51c169cd93b53120485cbcf3c092363587285a06b43ca97df8bd625495d8"
```

After:

```python
DEFAULT_IMAGE = "ghcr.io/jimboschneider/dirtywork-worker:0.9"
# Unset for 0.9.0, as for every first release of a minor: the :0.9 image is
# first published BY the v0.9.0 release, so there is no prior publish to pin
# against and resolve_image() performs no pin check. The Dockerfile is unchanged
# in 0.9 -- :0.9 is a rebuild of :0.8 under the tag-tracks-the-minor policy
# (docker/README.md) -- but the tag is still new, so it must be resolved fresh.
# Pin in 0.9.1 with the digest publish-image.yml reports:
# `docker pull ghcr.io/jimboschneider/dirtywork-worker:0.9`
# `docker image inspect --format '{{json .RepoDigests}}' ghcr.io/jimboschneider/dirtywork-worker:0.9`
# and set the `sha256:<...>` portion here. This only ever pins a REGISTRY digest
# -- resolve_image() enforces it against a *pulled* DEFAULT_IMAGE only; a
# locally built/loaded image warns instead of refusing, and a user-supplied
# --image is never checked. MUST be re-resolved whenever the :0.9 tag is
# re-pushed. (0.8.x pinned :0.8 at
# sha256:d8ca51c169cd93b53120485cbcf3c092363587285a06b43ca97df8bd625495d8;
# 0.7.x shipped unpinned; 0.6.x pinned :0.6 at
# sha256:1f7b98898001b7064d8db396a8a5a1a324df4ce48692597fcd4381ea90e4354a;
# 0.5.x pinned :0.5 at
# sha256:3b8d019a2f20a9df55a72ed51139076f02f2feb597243a69519bc41db1029648.)
PINNED_DIGEST: str | None = None
```

And its test, in `tests/test_docker_args.py`.

Before:

```python
def test_default_image_and_pinned_digest():
    assert DEFAULT_IMAGE == "ghcr.io/jimboschneider/dirtywork-worker:0.8"
    # Pinned in 0.8.1 to the :0.8 multi-arch index digest published by the
    # v0.8.0 release (docker/README.md documents how to re-resolve it
    # whenever :0.8 is re-pushed).
    assert PINNED_DIGEST == "sha256:d8ca51c169cd93b53120485cbcf3c092363587285a06b43ca97df8bd625495d8"
```

After:

```python
def test_default_image_and_pinned_digest():
    assert DEFAULT_IMAGE == "ghcr.io/jimboschneider/dirtywork-worker:0.9"
    # Unset for 0.9.0: the :0.9 image is first published by the v0.9.0 release,
    # so there is nothing to pin against yet. 0.9.1 pins it (docker/README.md
    # documents how to resolve and re-pin the digest whenever :0.9 is re-pushed).
    assert PINNED_DIGEST is None
```

- [ ] **Step 3: Move the CI docker-live tag, and confirm `test_docker_image.py` needs nothing**

In `.github/workflows/ci.yml`.

Before:

```yaml
          tags: ghcr.io/jimboschneider/dirtywork-worker:0.8
```

After:

```yaml
          tags: ghcr.io/jimboschneider/dirtywork-worker:0.9
```

Then confirm the other test module the spec names has no tag to move:

```bash
grep -n "0\.8\|0\.9" tests/test_docker_image.py
```
Expected: exactly one line — the docstring's `0.8 additions come from a real run whose bash suite needed them (issue #30)`, which is a historical statement about when `jq`/`uuid-runtime`/`shellcheck`/`curl` were added and stays true. The module's own build tag is `…:0.5-test` and is unrelated to `DEFAULT_IMAGE`. Leave the file unchanged.

- [ ] **Step 4: Move every `docker/README.md` mention**

Run, then read the diff:

```bash
/usr/bin/python3 - <<'PY'
from pathlib import Path
p = Path("docker/README.md")
text = p.read_text(encoding="utf-8")
text = text.replace("dirtywork-worker:0.8", "dirtywork-worker:0.9")
text = text.replace("my-worker:0.8", "my-worker:0.9")
p.write_text(text, encoding="utf-8")
PY
git diff --stat docker/README.md
```
Expected: 14 changed lines, all of them tag references.

Then update the two prose lines that name releases rather than tags. Both
**before** blocks below show the file **after** the substitution above (the
pinning-policy paragraph already reads `:0.9` by this point), so apply them in
this order.

Before:

```
and (since 0.8) jq, uuid-runtime, shellcheck and curl.
```

After:

```
and (since 0.8) jq, uuid-runtime, shellcheck and curl. The 0.9 image is a
rebuild of the 0.8 one — the Dockerfile is unchanged; the tag tracks the minor.
```

Before:

```
The first release of a minor (0.4.0, 0.5.0, 0.6.0, 0.7.0, 0.8.0) ships with `PINNED_DIGEST = None`: there is no prior publish to pin
against on the very first release, so `resolve_image()` performs no pin
check and trusts whatever `docker image inspect` currently reports for
`ghcr.io/jimboschneider/dirtywork-worker:0.9`. The next patch release
(0.4.1 for 0.4; 0.5.1 for 0.5; 0.6.1 for 0.6; 0.8.1 for 0.8 — 0.7.x shipped unpinned) pins — once `publish-image.yml` has run, take the
digest from its job summary (or resolve it yourself below) and commit it
as `PINNED_DIGEST` ahead of the next release.
```

After:

```
The first release of a minor (0.4.0, 0.5.0, 0.6.0, 0.7.0, 0.8.0, 0.9.0) ships with `PINNED_DIGEST = None`: there is no prior publish to pin
against on the very first release, so `resolve_image()` performs no pin
check and trusts whatever `docker image inspect` currently reports for
`ghcr.io/jimboschneider/dirtywork-worker:0.9`. The next patch release
(0.4.1 for 0.4; 0.5.1 for 0.5; 0.6.1 for 0.6; 0.8.1 for 0.8; 0.9.1 for 0.9 — 0.7.x shipped unpinned) pins — once `publish-image.yml` has run, take the
digest from its job summary (or resolve it yourself below) and commit it
as `PINNED_DIGEST` ahead of the next release.
```

- [ ] **Step 5: Move every `docs/machine-contract.md` mention**

Before:

```
    [--image ghcr.io/jimboschneider/dirtywork-worker:0.8]  # docker mode only
```

After:

```
    [--image ghcr.io/jimboschneider/dirtywork-worker:0.9]  # docker mode only
```

Before (quoted in a four-backtick fence, because the block contains a fence of
its own):

````
- `--image REF` (docker mode) — the worker image, default
  `ghcr.io/jimboschneider/dirtywork-worker:0.8`. The image is the worker's
  whole toolchain: with `--network none` and no host mounts, nothing can be
  installed during a run. To add a tool, derive an image once:

  ```Dockerfile
  FROM ghcr.io/jimboschneider/dirtywork-worker:0.8
  USER root
  RUN apt-get update && apt-get install -y --no-install-recommends <packages> \
      && rm -rf /var/lib/apt/lists/*
  USER worker
  ```

  then `docker build -t my-worker:0.8 .` and `--image my-worker:0.8`. A custom
  `--image` is never digest-pinned — `PINNED_DIGEST` protects the maintained
  default image only.
````

After:

````
- `--image REF` (docker mode) — the worker image, default
  `ghcr.io/jimboschneider/dirtywork-worker:0.9`. The image is the worker's
  whole toolchain: with `--network none` and no host mounts, nothing can be
  installed during a run. To add a tool, derive an image once:

  ```Dockerfile
  FROM ghcr.io/jimboschneider/dirtywork-worker:0.9
  USER root
  RUN apt-get update && apt-get install -y --no-install-recommends <packages> \
      && rm -rf /var/lib/apt/lists/*
  USER worker
  ```

  then `docker build -t my-worker:0.9 .` and `--image my-worker:0.9`. A custom
  `--image` is never digest-pinned — `PINNED_DIGEST` protects the maintained
  default image only.
````

- [ ] **Step 6: Consolidate the contract — `docs/machine-contract.md`**

Two edits: the example payload gains the three 0.9 keys, then the prose under it
stops calling six keys "the last six". They are separate so neither quoted block
has to contain the document's own closing fence.

First the tail of the example JSON.

Before:

```
  "last_assistant_text": "Added the retry and a test for it.",
  "verify": {
    "command": "npm test",
    "exit_code": 0,
    "output_tail": "exit code: 0\n12 passing",
    "rounds": 1,
    "passed": true
  }
}
```

After:

```
  "last_assistant_text": "Added the retry and a test for it.",
  "verify": {
    "command": "npm test",
    "exit_code": 0,
    "output_tail": "exit code: 0\n12 passing",
    "rounds": 1,
    "passed": true
  },
  "trimmed_turns": 0,
  "timeouts": 0,
  "context_window_source": "provider:openai:server"
}
```

Then the paragraph immediately below that JSON block.

Before:

```
The last six keys are 0.8 additions (`stuck_on`, `files_changed`,
`files_changed_truncated`, `last_tool_result`, `last_assistant_text`,
`verify`). Every one of them is present on every payload — `null` when it
does not apply, `[]`/`false` for the list and its flag — including the two
paths where `runner.run()` never returns (see below), where they carry those
same null/empty defaults rather than being omitted.
```

After:

```
Six of those keys are 0.8 additions (`stuck_on`, `files_changed`,
`files_changed_truncated`, `last_tool_result`, `last_assistant_text`,
`verify`) and the last three are 0.9's (`trimmed_turns`, `timeouts`,
`context_window_source`). Every one of them is present on every payload —
`null` when it does not apply, `[]`/`false` for the list and its flag, `0` for
the two counters — including the two paths where `runner.run()` never returns
(see below), where they carry those same defaults rather than being omitted.
`trimmed_turns` is how many turns had to drop tool results to fit the context
budget, `timeouts` how many `bash` calls never finished, and
`context_window_source` which precedence step produced `context_window`
(`flag` | `env` | `provider:<name>:server` | `provider:<name>` | `default`).
```

And the paragraph that enumerates them further down.

Before:

```
`finalize_error`, `watchdog_violation` and `watchdog_violation_kind` are
added on the normal end-of-run path — i.e. whenever `runner.run()` returns a
result, `completed` or not — normally `null`; see `run_end` below for what
each means. `stuck_on`, `files_changed`, `files_changed_truncated`,
`last_tool_result`, `last_assistant_text` and `verify` are present on
**every** payload (`null`/`[]`/`false` when they do not apply) — including
the two paths below where `runner.run()` never returns, where they carry
those same defaults rather than being omitted.
```

After:

```
`finalize_error`, `watchdog_violation` and `watchdog_violation_kind` are
added on the normal end-of-run path — i.e. whenever `runner.run()` returns a
result, `completed` or not — normally `null`; see `run_end` below for what
each means. `stuck_on`, `files_changed`, `files_changed_truncated`,
`last_tool_result`, `last_assistant_text`, `verify`, `trimmed_turns`,
`timeouts` and `context_window_source` are present on
**every** payload (`null`/`[]`/`false`/`0` when they do not apply) — including
the two paths below where `runner.run()` never returns, where they carry
those same defaults rather than being omitted.
```

- [ ] **Step 7: Consolidate the field list — `docs/transcript-schema.md` and `README.md`**

In `docs/transcript-schema.md`, the stdout field list.

Before:

```
[docs/machine-contract.md](machine-contract.md)). Its fields: `schema_version`, `status`, `worktree`, `branch`,
`transcript`, `turns`, `usage`, `final_message`, `run_dir`, `provider`,
`base_commit`, `resumed_from`, `finalize_error`, `watchdog_violation`,
`watchdog_violation_kind`, `stuck_on`, `files_changed`,
`files_changed_truncated`, `last_tool_result`, `last_assistant_text`, `verify`,
and `export_status` on the exception-recovery path.
```

After:

```
[docs/machine-contract.md](machine-contract.md)). Its fields: `schema_version`, `status`, `worktree`, `branch`,
`transcript`, `turns`, `usage`, `final_message`, `run_dir`, `provider`,
`base_commit`, `resumed_from`, `finalize_error`, `watchdog_violation`,
`watchdog_violation_kind`, `stuck_on`, `files_changed`,
`files_changed_truncated`, `last_tool_result`, `last_assistant_text`, `verify`,
`trimmed_turns`, `timeouts`, `context_window_source`,
and `export_status` on the exception-recovery path.
```

In `README.md`, the "How a run works" step 3 sentence about context budgeting
(edited in Task 2; this is its current state).

Before:

```
   Context is budgeted per model (oldest tool results get
   trimmed first); three consecutive tool failures of one kind (malformed
```

After:

```
   Context is budgeted per model — dirtywork asks the server what window it
   actually loaded and reports both the value and its source, and the payload's
   `trimmed_turns` says on how many turns the oldest tool results had to be
   dropped to fit. Three consecutive tool failures of one kind (malformed
```

- [ ] **Step 8: Bump the version in both files**

In `pyproject.toml`.

Before:

```toml
version = "0.8.1"
```

After:

```toml
version = "0.9.0"
```

In `dirtywork/__init__.py`.

Before:

```python
__version__ = "0.8.1"
```

After:

```python
__version__ = "0.9.0"
```

- [ ] **Step 9: Prove the tag move is complete**

Run:

```bash
grep -rn "dirtywork-worker:0\.8\|my-worker:0\.8" --include="*.py" --include="*.md" --include="*.yml" . | grep -v docs/superpowers
```
Expected: **no output**. Any hit is a mention Step 1 missed; fix it before continuing. (Without the `grep -v`, the only remaining hits are this plan's and the 0.8 spec's **before** blocks, which must keep the old tag.)

- [ ] **Step 10: Run the full suite**

Run: `/usr/bin/python3 -m pytest -q`
Expected: `1014 passed, 1 skipped, 18 deselected` — unchanged from Task 6 (this task adds no test; `test_default_image_and_pinned_digest` and `test_version_is_in_step_with_pyproject` are existing tests that must now pass against the new values).

Then confirm the two version sources agree and the payload carries the full
0.9 key set, end to end:

```bash
/usr/bin/python3 -m pytest tests/test_transcript_schema.py tests/test_docker_args.py -q
/usr/bin/python3 -c "import dirtywork; print(dirtywork.__version__)"
```
Expected: both modules green, and `0.9.0`.

- [ ] **Step 11: Commit**

```bash
git add dirtywork/sandbox/docker_args.py dirtywork/__init__.py pyproject.toml .github/workflows/ci.yml docker/README.md docs/machine-contract.md docs/transcript-schema.md README.md tests/test_docker_args.py
git commit -m "chore: 0.9.0 — worker image :0.9, consolidated machine contract"
```

- [ ] **Step 12: File the two follow-up issues named by the spec**

Not repository changes, but part of shipping 0.9 (spec §6):

1. **Atomic write primitive for the in-place tools** — with §1.6's requirements
   spelled out: it must not re-open the final-component symlink TOCTOU that
   `O_NOFOLLOW` closes, must reproduce the three errno-specific refusal
   messages, must preserve inode/hardlink/directory-permission semantics and
   the exec bit under docker `mv`, and must leave no crash temps inside the
   export (`files_changed`, `diff.patch`).
2. **Ollama loaded-context probe** — §3.2: `/api/show` reports the model's
   architectural maximum rather than the loaded `num_ctx`; find the endpoint
   that reports the loaded value, or document that `--context-window` is
   required for Ollama.

---

## Self-review: design coverage

Every numbered section of `docs/superpowers/specs/2026-08-19-tools-context-timeouts-design.md` maps to at least one step.

| Spec section / item | Task / step |
|---|---|
| §1.1 `apply_edits(path, edits)` applies in order on the running text | Task 2, Step 3 (`_apply_edits_once`); test `test_apply_edits_applies_in_order` (Step 1) |
| §1.1 `str.count` uniqueness per edit; `text.replace(old, new, 1)` | Task 2, Step 3 |
| §1.1 all-or-nothing before the write (transform returns `None`) | Task 2, Step 3; tests `…rolls_back_when_a_later_edit_does_not_match`, `test_apply_edits_rollback_never_writes` (Steps 1, 11) |
| §1.1 success text `describe_change(..., verb="Applied N edit(s) to")` | Task 2, Step 3; tests in Steps 1, 11 |
| §1.1 empty list rejected as `bad_args` via `minItems: 1` | Task 1, Step 5 (`minItems`); Task 2, Step 17 (the schema); Task 1, Step 1 (message test) |
| §1.1 `MAX_APPLY_EDITS = 100` on the wire and at runtime | Task 2, Step 17 (`maxItems=MAX_APPLY_EDITS`); Task 1, Step 5 enforces it |
| §1.2 every error string, 1-based, nothing written | Task 2, Step 3 (the literals); Step 1 asserts each verbatim |
| §1.2 backend-specific read/write refusals deliberately NOT unified | Task 2 — no step changes `_read_raw`/`_open_regular`/`_write_raw` wording; noted in Task 2's parity test, which is scoped to matching/success/rollback |
| §1.3 `ParamSpec.schema` | Task 1, Step 3 |
| §1.3 `schemas()` emits it with `description` merged | Task 1, Step 4; test `test_schema_param_renders_the_nested_schema_with_the_description_merged` |
| §1.3 the Anthropic adapter passes `parameters` through unchanged | Task 1, Steps 9–10 (`test_nested_parameters_reach_input_schema_unchanged`) |
| §1.3 recursive validator: type/minItems/maxItems/items/properties/required/additionalProperties | Task 1, Step 5 (`_validate_against_schema`), Step 6 (wiring) |
| §1.3 `additionalProperties: false` enforced at runtime; top-level drop-unknown unchanged | Task 1, Step 5 (object branch) and Step 6 (`_validate_args` keeps its existing top-level loop) |
| §1.3 exact path-qualified messages, surfaced as `bad_args` | Task 1, Step 1 (parametrized, all seven) |
| §1.3 nested scalar coercion via `_coerce_numeric_string` | Task 1, Step 5; test `test_nested_validation_coerces_a_numeric_string_at_a_nested_leaf` |
| §1.3 fixture renamed to `tests/fixtures/tool_schemas.json`, test renamed | Task 1, Steps 11–12 |
| §1.3 `test_schemas_shape` name set and `FakeSandbox` gain `apply_edits` | Task 2, Step 15 |
| §1.4 recursive `max_input_bytes` (str values incl. top-level `path`, not keys) | Task 1, Steps 5 (`_input_bytes`), 7; tests in Step 1 |
| §1.4 `APPLY_EDITS_SPEC` caps: `fs="write"`, 2 MiB input, `TOOL_OUTPUT_CAP`, `transcript="preview"` | Task 2, Step 17; asserted in Step 15's test |
| §1.4 `edit_file` gains no cap | Task 2 — no step touches `EDIT_FILE_SPEC.caps` |
| §1.5 shared output cap in BOTH `_transform_file`s with the exact string | Task 2, Steps 3 (`_check_write_size`), 4 (host), 13 (docker); parity tests in Steps 1, 11 |
| §1.5 `write_file` keeps its own oversized wording; `tests/test_tools_files.py`'s `write limit` substring preserved | Task 2 — no step changes `write_file` or `_oversized`; `test_write_file_refuses_oversized_content` is untouched and stays green in Step 6 |
| §1.6 write semantics stated, not changed; caveat in `docs/operating.md` | Task 2, Step 26 (the callout); the description in Step 17 says the batch is refused before the write plainly |
| §1.7 one transform factory used by both backends | Task 2, Steps 3, 5, 13 |
| §1.7 `Sandbox` Protocol + `HostSandbox` (with `_check_budget`) + `DockerSandbox` | Task 2, Steps 9, 13 |
| §1.7 `APPLY_EDITS_SPEC` immediately after `EDIT_FILE_SPEC`; order documented | Task 2, Step 17; asserted in Step 15 |
| §1.7 module docstring "nine" → "ten" | Task 2, Step 17 |
| §1.7 `runner._MUTATING_TOOLS` gains `apply_edits` | Task 2, Step 20 |
| §1.7 system-prompt rule | Task 2, Step 20 |
| §1.7 docs: README tool list + Security enumeration, `docs/security.md`, `docs/transcript-schema.md` `tool` enum + result-format row | Task 2, Steps 22, 23, 24 |
| §1.7 `docs/machine-contract.md` gains a Tools subsection | Task 2, Step 25 |
| §1.7 `docs/operating.md` gains an `apply_edits` paragraph | Task 2, Step 26 |
| §1.8 host/docker parity, rollback, empty `old`, k>1, diff text, output-cap parity for `edit_file` too | Task 2, Steps 1, 7, 11 |
| §1.8 `tests/test_toolspec.py` nested rendering, validation messages, recursive input bytes | Task 1, Step 1 |
| §1.8 `tests/test_transcript_schema.py` tool list | Task 2, Step 21 |
| §2.1 task-size warning after `ctx` is built, `TASK_WARN_FRACTION = 0.20`, exact text | Task 3, Step 14 |
| §2.1 fires for `run` and `resume`, silent under 20% | Task 3, Step 12 (three tests) |
| §2.2 `trim_messages` returns `(fits, newly_trimmed)`; already-trimmed results not recounted | Task 3, Step 3 |
| §2.2 call site unpacks, counts, still ends `context_exhausted`, final trim counts | Task 3, Step 4; tests in Step 1 |
| §2.2 the three existing trim tests rewritten | Task 3, Step 1 |
| §2.2 `trimmed_turns` on `RunResult.extra` → payload/`run_end`/`run.json`, default 0 on failure paths | Task 3, Steps 4, 8, 9, 10 |
| §2.2 `runs show` plain (`SHOW_FIELDS`) and `--markdown` `## Result` | Task 3, Step 18 |
| §2.2 `bench summarize` mean + `--compare` pairing | Task 3, Step 22 |
| §2.3 `docs/operating.md` `## Sizing the context window` with the cited SP3 numbers | Task 3, Step 26 |
| §2.3 README *Requirements* links to it | Task 3, Step 27 |
| §3.1 `Provider` documents the optional hook; `getattr`; missing/raising → None | Task 4, Steps 3, 7 (decision recorded in the task header) |
| §3.1 `AnthropicClient.loaded_context_window` returns None explicitly | Task 4, Step 7 |
| §3.2 origin + `/api/v0/models`, proxy prefix dropped | Task 4, Step 7 (`_origin`); tests in Step 5 |
| §3.2 `LOADED_CONTEXT_PROBE_TIMEOUT = 2`, GET via `http_json`, `payload=None` | Task 4, Step 7; asserted in Step 5 |
| §3.2 exact accept rules (`id` equality, `state`, non-bool int > 0) and every reject case | Task 4, Step 7; parametrized test in Step 5 |
| §3.2 Ollama not probed; documented as a follow-up | Task 3, Step 26 (`docs/operating.md`); Task 4, Step 17 (`docs/machine-contract.md`); Task 7, Step 12 (the issue) |
| §3.3 precedence + `provider:<name>:server` | Task 4, Step 3; tests in Step 1 |
| §3.3 the "assuming 32768" warning still fires only for `default` | Task 4, Step 11 (`_resolve_context_window` keeps the `source == "default"` guard) |
| §3.3 `test_resolve_context_window_uses_the_real_openai_table` gets a stub transport | Task 4, Step 1 |
| §3.4 `RunContext.context_window_source`, placed before the first defaulted field | Task 4, Step 11 |
| §3.4 `run_start`, `run.json` at start, every payload, `run_end`, both failure paths | Task 4, Steps 12, 13 |
| §3.4 `runs show` plain + Markdown header | Task 4, Step 14 |
| §3.4 resume re-resolves and records its own | Task 4, Step 11 (`_workspace_resume`); test in Step 9 |
| §3.5 tests: runner precedence, provider probe, `test_main.py` records | Task 4, Steps 1, 5, 9 |
| §4.1 canonical text, no partial output, `TIMEOUT_PREFIX`/`is_timeout_result` in `tools.py` | Task 5, Steps 1, 3 |
| §4.2 `DockerError(timed_out=)` keyword-only; `docker_cli.run` sets it | Task 5, Steps 5, 7 |
| §4.2 `DockerSandbox.bash` discriminates; non-timeout → `ERROR: bash failed: …` | Task 5, Steps 9, 11 |
| §4.2 `DockerSandbox.grep` discriminates; timeout wording unchanged; non-timeout → `ERROR: grep failed: …` | Task 5, Steps 9, 11 |
| §4.2 `test_bash_timeout_returns_text_not_raise` rewritten to the full text | Task 5, Step 9 |
| §4.3 `tool_result.timed_out` sparse, bash only, `--verify` excluded | Task 5, Step 15; tests in Step 13 |
| §4.3 one `timeout` nudge per continuing turn, merged via `_join_nudges`, not a FailureTracker event | Task 5, Step 15; tests in Step 13 |
| §4.3 `timeouts` counter → extra/payload/`run_end`/`run.json` incl. failure-path defaults | Task 5, Steps 15, 19 |
| §4.3 `runs._tool_result_outcome` → `"timed out"`, `[timed out]` in both views, `SHOW_FIELDS` | Task 5, Step 23; test in Step 21 |
| §4.3 `bench`: `NUDGE_KINDS`, `timeouts` from the payload, `empty_reply` exclusion, 4-tuple, legends | Task 5, Step 27 (deviation on `_failure_cell` recorded in the task header); tests in Step 25 |
| §4.4 `docs/operating.md` "The bash tool" | Task 5, Step 29 |
| §4.4 `docs/machine-contract.md` (in the Tools subsection) | Task 2, Step 25 (the `bash` bullet states the timeout text, the flag and the counter) |
| §4.4 `docs/transcript-schema.md` rows + `tests/test_transcript_schema.py` lists | Task 5, Steps 30, 31 |
| §4.5 tests in `test_tools_bash`, `test_docker_sandbox`, `test_runner`, `test_runs`, `test_bench` | Task 5, Steps 1, 9, 13, 21, 25 |
| §5 `windows-unit` job: separate, `continue-on-error`, absent from `gate.needs` | Task 6, Steps 7, 8 |
| §5 `python -m pytest --junitxml=…` then `tools/junit_summary.py`, artifact SHA-pinned as `publish.yml` pins it | Task 6, Step 7 |
| §5 `tools/junit_summary.py` stdlib, per-file table, appends to `$GITHUB_STEP_SUMMARY` | Task 6, Step 3; tests in Step 1 |
| §5 `tests/test_budget.py` `import sys` + the Windows-safe skipif | Task 6, Step 5 |
| §5 README platform row + `docs/security.md` callout | Task 6, Steps 9, 10 |
| §5 no production code changed for Windows | Task 6 — no step touches `dirtywork/`; stated in the task header |
| §6 every payload gains the three fields; `_emit_result` seeds them before `update(extra)` | Task 3, Step 8; Task 4, Step 12; Task 5, Step 19 |
| §6 both manual `run_end` writes and both failure-path `_update_run_json` calls carry them | Task 3, Step 9 (`_contract_fields`, written once and reused) |
| §6 `run.json` gets `context_window_source` at start and the counters at end | Task 4, Step 12; Task 3, Step 10; Task 5, Step 19 |
| §6 transcript: `tool_result.timed_out`, nudge kind `timeout`, `run_start.context_window_source`; `schema_version` stays 2 | Task 4, Step 13; Task 5, Steps 15, 30 — no step changes `schema_version` |
| §6 ten tools; wire fixture regenerated and renamed | Task 1, Step 11; Task 2, Step 18 |
| §6 `DEFAULT_IMAGE`/CI tag/docs → `:0.9`, `PINNED_DIGEST = None` with the comment updated | Task 7, Steps 1–5, 9 |
| §6 `tests/test_docker_args.py` / `test_docker_image.py` | Task 7, Steps 2, 3 (the latter proven to need no change) |
| §6 version `0.9.0` in `pyproject.toml` and `dirtywork/__init__.py` | Task 7, Step 8 |
| §6 consolidated example JSON + `docs/transcript-schema.md` field lists | Task 7, Steps 6, 7 |
| §6 full unit suite green on 3.9 after every task | every task's final suite step |
| §6 follow-up issues (atomic write primitive; Ollama probe) | Task 7, Step 12 |

Not carried into this plan, deliberately: the spec's "post the per-file table on
#24" (a GitHub action for the PR author, not a repo change — recorded as a note
at the end of Task 6) and its "Deviation from the owner's review, stated"
paragraph, which is a statement about the spec's own reasoning and is already
reflected in §1.6's implementation (Task 2, Step 26).

## Type consistency checklist

- `toolspec.ParamSpec.schema: dict | None = None` — `None` for every flat param (so `schemas()` and `_validate_args` behave exactly as before for them); a plain `dict` for `apply_edits.edits`. Never mutated: `schemas()` copies it with `dict(...)` before merging `description`.
- `toolspec._validate_against_schema(value, schema: dict, path: str)` — returns the validated value with the SAME container types it was given (`list` → `list`, `dict` → `dict`) and numeric strings coerced at scalar leaves; raises only `ToolValidationError`. `path` is always a display string (`"edits"`, `"edits[2]"`, `"edits[2].new"`), never a data structure.
- `toolspec._input_bytes(value) -> int` — total over `str` values at any depth; `0` for every non-`str`, non-container. Called with `call_args` (a `dict`), so top-level string parameters are included exactly as they were before 0.9.
- `tools._check_write_size(new_text: str) -> str | None` — `None` means "small enough"; a non-empty `ERROR: …` string means refuse. Both `_transform_file` implementations test it with `if too_big:`, the same falsy-means-ok convention `docker._oversized` already uses.
- `tools._apply_edits_once(path: str, edits: list) -> Callable[[str], tuple]` — the returned `transform(text) -> (str | None, str)` matches `_replace_once`/`_insert_once` exactly, so a transform written for one backend works in the other unchanged. `edits` is always a `list` of `{"old": str, "new": str}` because the registry validated it; `_apply_edits_once` does no shape checking and must not start.
- `tools.apply_edits(worktree: Path, path: str, edits: list) -> str`; `HostSandbox.apply_edits(path: str, edits: list) -> str`; `DockerSandbox.apply_edits(path: str, edits: list) -> str`; `Sandbox.apply_edits(path: str, edits: list) -> str`. All four signatures are identical after the leading `worktree`/`self`, and all return `str` and raise nothing but `BudgetExceeded`/`SandboxError`.
- `builtin_tools.MAX_APPLY_EDITS: int` (100) is used **only** as the schema's `maxItems`; `MAX_APPLY_EDITS_INPUT_BYTES: int` (2 MiB) **only** as `Caps.max_input_bytes`. Neither is read by `dirtywork/tools.py`.
- `tools.TIMEOUT_PREFIX: str`, `tools.TIMEOUT_TEXT: str` (a format string with exactly one field, `{timeout}`), `tools.timeout_result(timeout: int) -> str`, `tools.is_timeout_result(text) -> bool` (accepts anything, returns `False` for a non-`str`). Every consumer — `runner`, `runs`, both backends — goes through `timeout_result`/`is_timeout_result`; no module re-derives the text or the predicate.
- `docker_cli.DockerError(*args, timed_out: bool = False)` — `.timed_out` is always a real `bool` and always present, because the custom `__init__` sets it unconditionally. Every existing `raise DockerError("…")` and `except DockerError` is unaffected; only `docker_cli.run`'s timeout path passes `timed_out=True`.
- `runner.trim_messages(messages: list, char_budget: int) -> tuple` — always `(bool, int)`; `newly_trimmed` is `>= 0` and counts only this call's replacements. The single production call site unpacks it; the four unit tests compare against a literal tuple.
- `runner.Runner.__init__`'s new parameter: `context_window_source: str | None = None` → `self.context_window_source`, written to `run_start` and to `finish()`'s `extra` unchanged. `None` only when a `Runner` is constructed directly (tests, embedders); the CLI always passes `ctx.context_window_source`, a `str`.
- `RunResult.extra` keys added by this plan and their types: `trimmed_turns: int`, `timeouts: int`, `context_window_source: str | None`. All three are present on every runner-returned result; the CLI re-reads the two ints with `extra.get(name, 0)` and takes the source from `ctx`, so a payload can never carry `None` for a counter.
- `__main__.TASK_WARN_FRACTION: float` (0.20); `__main__._warn_task_size(ctx) -> None` — reads `ctx.task` (`str`) and `ctx.context_window` (`int`), prints to stderr or returns; never raises and never records.
- `__main__._contract_fields(extra: dict, ctx: RunContext) -> dict` — always exactly three keys, always `{"trimmed_turns": int, "timeouts": int, "context_window_source": str}`. Splatted into `_emit_result`, `_update_run_json` and `transcript.write("run_end", …)`; because those three key names appear nowhere else in any of those calls, the splat can never collide with an explicit keyword.
- `__main__._resolve_context_window(args, provider=None) -> tuple` — always `(int, str)`; raises only `PreflightFailure`. `_workspace_new`/`_workspace_resume` take `context_window: int` and `context_window_source: str` positionally from `main`'s single call site each.
- `RunContext.context_window_source: str` is REQUIRED and declared immediately after `context_window`, i.e. before `branch_from` — the first field with a default. Both constructions pass it by keyword.
- `providers.openai_compat._origin(url: str) -> str` — `scheme://netloc` only, never a trailing slash. `OpenAICompatClient.loaded_context_window(model: str) -> int | None` and `AnthropicClient.loaded_context_window(model: str) -> int | None` share the signature; the openai one returns only a non-bool `int > 0` or `None`, and catches only `LLMError` (which `LLMTimeout` subclasses).
- `runner.resolve_context_window(...) -> tuple` still always `(int, str)`. The source strings are exactly `"flag"`, `"env"`, `f"provider:{name}:server"`, `f"provider:{name}"`, `"default"` — `runs._summary_value` renders whatever it finds and never parses it.
- `runs._tool_result_outcome(result_text) -> str` — one of `"timed out"`, `"ERROR"`, `"BLOCKED"`, `"ok"`; both renderers compose it as `[{outcome}]`, so the space in `"timed out"` is deliberate and safe in both.
- `runs._summary_value(key: str, data: dict) -> str` still always returns `str`; the new `context_window` branch reads a sibling key (`context_window_source`) and falls back to the bare number when it is absent, so a pre-0.9 `run.json` renders without raising.
- `bench._harness_failures(counts: dict, status, final_message, timeouts=0) -> dict` — `timeouts` is coerced with `int(timeouts or 0)`, so a payload missing the key (a pre-0.9 result row replayed through `summarize`) yields `0` rather than `None`. `harness["timeouts"]` is therefore always an `int`.
- `bench._harness_counts(summary) -> tuple` is now a **4**-tuple of `int`; `_fmt_component_delta` zips two of them, `_harness_cell` joins them with `/`, and `_paired_counts_cell` accepts `None` for a side with no harness rows. Every caller was already tuple-length agnostic — the three test assertions that hard-coded three components are updated in Task 5, Step 25.
- `bench` row keys added: `trimmed_turns` (`int | None` — `None` when a payload predates 0.9, which `_numbers` filters out of the mean) and `harness["timeouts"]` (`int`). `_summarize_model` returns `mean_trimmed_turns: float | None` and `timeouts: int`.
- `tools/junit_summary.py`: `summarize(xml_text: str) -> dict` maps `str` → a fresh `dict` with exactly the four `OUTCOMES` keys, all `int`; `render(table: dict) -> str`; `main(argv=None) -> int` returns only `0` or `2` and never raises for a test failure. Nothing in it imports `dirtywork` or `pytest`.
- Python 3.9 throughout: `X | None` appears only in annotations, in modules that already carry `from __future__ import annotations` (plus the new `tools/junit_summary.py`, which gets the import); no runtime union, no `match`, no `|` inside an `isinstance` call. `dict.fromkeys(OUTCOMES, 0)` and keyword-only parameters after `*args` are both 3.7+.
