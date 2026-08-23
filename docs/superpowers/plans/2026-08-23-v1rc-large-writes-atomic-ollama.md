# 0.10 (v1 RC) — Large Writes Recoverable, Atomic Writes, Ollama First-Class, Backlog Zero — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Every code block below is the literal code to write — transcribe it, do not paraphrase it. Where a step shows a **before** block, that text exists verbatim on this branch; match it exactly and replace it with the **after** block. A later task's **before** block is the earlier task's **after** block wherever they touch the same lines, so tasks must be done in order.

**Goal:** Ship dirtywork **0.10.0**, the v1 release candidate — close the last core-job holes. A file larger than one turn's output becomes a recoverable two-step (`append_file` plus a tool-aware truncation recovery and a configurable `--max-tokens`) instead of a silent `model_error` (#36). An interrupted write can no longer leave a truncated file (#43). Ollama becomes a verified first-class provider with a real loaded-context probe (#47). The self-reported defect backlog — snapshot follow-ups, tool wording/perf nits, test-coverage gaps — goes to zero (#40, #41, #42).

**Architecture:** Everything is additive over shipped structure; no new dependency, `schema_version` stays 2. The spine is one new host primitive: `tools._write_atomic(target, data, *, path, verb, create_parents, must_exist)` stages every host write in a same-directory temp file and promotes it with `os.replace`, keeping today's exact refusal strings and deliberately keeping today's in-place behaviour in the two branches where a rename is wrong (a hardlinked target) or impossible (an unwritable directory). `append_file` is then one more caller of that primitive on the host, and one more three-exec script pair in the container; both backends share the caps (`tools._append_oversized`, `tools._result_too_big`) and the strings (`tools._append_missing`, `tools._not_utf8`) the way `MAX_WRITE_BYTES` is already shared, so the host and the container cannot disagree about what an append refuses. Docker's write path grows the same temp+`mv` shape, with the temp NAME generated host-side (`tools.tmp_name`) and passed in as `$2`, so worker data never reaches the script text. The runner learns two small things: an explicit `max_tokens` on its single `provider.chat` call site (which also makes the prompt budget cap-aware) and a tool-aware recovery for a call the model was cut off mid-emit. Ollama arrives as a three-method subclass of the existing OpenAI-compatible adapter. `workspace.snapshot_worktree` gets a two-pass hash (so a no-op snapshot writes no loose objects) and a breadth-first walk that prunes an ignored directory before descending into it.

**Tech Stack:** Python ≥3.9, stdlib only (`difflib`, `errno`, `json`, `os`, `posixpath`, `re`, `stat`, `sys`, `urllib.parse`). Dev-only dependency: pytest. CI: GitHub Actions. Container scripts: POSIX `sh` plus GNU coreutils/findutils as shipped in the Debian bookworm worker image.

**Spec:** `docs/superpowers/specs/2026-08-20-v1rc-large-writes-atomic-ollama-design.md` (v3, owner-approved 2026-08-23 09:51 CDT, binding).

---

## Design

Restated from the spec named above. Section numbers below are the spec's.

### §1 — Large writes (#36)

**§1.2** `append_file(path, text)` is the eleventh tool: it appends `text` **verbatim** to an existing regular file, inserting nothing between the old bytes and the new ones. A missing target refuses with `ERROR: cannot append to '<path>': it does not exist; create it with write_file first`. Three caps fire **in this order on both backends**, so an append can never surface `write_file`'s or `read_file`'s wording: (1) the `text` argument, through the shared `tools._append_oversized`; (2) the current file's size against `MAX_READ_BYTES`; (3) the result size — (2) and (3) both render the shared `ERROR: result is <n> bytes, over the <limit>-byte write limit; nothing was written`. Host: the §2.2 probe unchanged (so ELOOP/FIFO refusals are byte-identical to `write_file`'s) except that `ENOENT` is the does-not-exist refusal, then a second `_open_regular(O_RDONLY, max_size=MAX_READ_BYTES)` whose `fstat` must match the probe's `st_ino`/`st_dev`, then `_write_atomic(..., verb="append")`. Docker: three execs — a guard+size exec (rc 2 missing, rc 3 non-regular **before any read**, rc 0 the exact byte size on stdout), the existing `_read_raw(strict=True, tool="append_file")`, and the append write script.

**§1.3** The `finish_reason == "length"` branch becomes tool-aware. It fires (a) as today when `tc.error is not None`, and (b) NEW when `tc.error is None` but the parsed argument dict is missing a required parameter of the tool — the Anthropic shape, where a truncated `tool_use` whose `input` came back `{}` parses "successfully". Case (b) is checked **before dispatch** so the registry's `bad_args` path never swallows it; both cases account as `malformed_args`. A `write_file` whose `path` can be recovered from the raw fragment gets a targeted sentence naming `write_file` + `append_file`; anything else gets the generic one. `NUDGES["truncated"]` is reworded to name the same two tools.

**§1.4** `--max-tokens` (default `DEFAULT_MAX_TOKENS = 8192`) is threaded `__main__` → `Runner.__init__` → the single `provider.chat` call site. The prompt budget becomes cap-aware: `char_budget = int(max(0, context_window - max_tokens) * BUDGET_FRACTION * CHARS_PER_TOKEN)`. Preflight refuses `--max-tokens >= context_window`. Resume inherits the prior run's value, falling back to the new default (which covers pre-0.10 `run.json` files). Recorded on `run_start` and `run.json`, **not** echoed on the stdout payload.

**§1.5** The `assistant` transcript event gains `finish_reason`, written as `finish_reason if isinstance(finish_reason, str) else None`. Documented as `string | null`, common values `stop`/`length`/`tool_calls`, **not a closed enum**.

### §2 — Atomic writes (#43)

**§2.1/§2.2** `tools._write_atomic` covers every host in-place write and docker's `_write_raw`/`_append_raw` get the same shape in the container. **Robustness, not a security fix**: the `O_NOFOLLOW` refusals stay exactly as deterministic as today. The host primitive is: optional `mkdir` → side-effect-free `O_WRONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC` probe (no `O_CREAT`) → hardlink fallback when `st_nlink > 1` → same-directory `.dw-tmp.<basename>.<8 hex>` temp opened `O_EXCL` mode `0o600` → write → `fchmod` to the target's mode (or `0o644 & ~_UMASK` for a new file) → **close the temp fd** → `os.replace`. From temp creation onward one `try` catches everything: an `OSError` unlinks the temp and **returns** the generic tail; any other `BaseException` unlinks the temp and **re-raises**.

**§2.3/§2.4** "Nothing was written" now covers a failure *during* the write, except the two named fd-fallback branches. `os.replace` changes the inode, so a worker-held fd on the old file keeps seeing old content. A symlink present at call time refuses exactly as today; one that appears between probe and `os.replace` gets replaced *as a link* — `rename(2)` does not dereference its destination, so nothing is ever written through it.

**§2.5** The sweep matches the full generated shape with an anchored regex (a worker file named `.dw-tmp.notes` is left alone) and runs where a leftover can actually exist — after a kill: `HostSandbox.start()` and, folded into `measure_worktree`'s existing walk, `HostSandbox.finalize()`; in docker, one `find … -delete` exec against the still-alive worker container, immediately before `DockerSandbox.finalize()` stops it. The count goes to stderr, never silent.

(Execution amendment, 2026-08-23: Task 6's fix round 1 relocated the docker sweep and tightened its regex. Originally placed inside `export_run`, immediately before the export's `git add -A` — but export's `/work` volume mount is readonly by design, so a `find … -delete` there gets EROFS on every match and silently does nothing. The sweep instead runs against the still-alive WORKER container, ahead of export entirely. Reporting is never silent on a partial failure: the swept-N note fires whenever the sweep printed any lines regardless of exit code, and a non-zero rc additionally notes `sweep incomplete (rc N)`. The same review found `TMP_FIND_REGEX`'s basename component (`.+`) over-matching across a directory boundary — `find -regex` matches the whole path and POSIX ERE `.` crosses `/` — and tightened it to `[^/]+`.)

**§2.6** Docker's write script becomes `&&`-chained temp+`mv -fT` with a directory guard, a writability guard (host parity: today an unwritable file refuses `EACCES`), `chmod --reference` with a `chmod 644` fallback, and `rm -f` of the temp on any failure. Each guard echoes its own diagnostic so the stderr wrap never renders empty. The append write script re-checks `[ -f "$1" ]` at write time (rc 2 → the does-not-exist string) and uses `cp` + `cat >>` before the shared promote.

### §3 — Ollama first-class (#47)

**§3.1** `OllamaClient(OpenAICompatClient)` with `name = "ollama"` and `OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434/v1"`. It overrides exactly three things: `__init__` (only to change the default base URL — a class attribute would not survive the parent's unconditional assignment), `context_window` (returns **None**: `CONTEXT_WINDOWS` is LM Studio's table and an Ollama user with a same-named model must not inherit LM Studio's number), and `loaded_context_window` (`GET {origin}/api/ps`, matching on the `model` key only, first match wins, `context_length` accepted only as a positive non-bool int). Cold start is stated honestly: Ollama's `/v1/models` lists **pulled** models, so preflight passes for a model that is not resident and the window falls to `default`. Provider-switch on resume keeps **no** `openai`↔`ollama` carve-out.

**§3.2** Eight fixtures under `tests/fixtures/providers/ollama/`, the `/api/ps` cases as standalone `RecordingTransport` tests, a new `ollama` marker added to `addopts`, and a new live smoke file with its own skipif.

### §4 — Snapshot follow-ups (#40)

Slug validation for `--branch-from @<slug>` moves onto the same rule `runs snapshot` uses, lifted into `rundir.run_dir_for`. `EMPTY_TREE_SHA` is replaced by a per-invocation empty-tree id (SHA-256 repos). Hashing becomes two passes — without `-w` to decide whether anything changed, then with `-w` rebuilding the entries from the second pass's shas — so a no-op snapshot writes no loose objects. The walk becomes an explicit BFS with **one** batched index-aware `check-ignore` call per tree depth, so an ignored directory is pruned before it is descended into; index-awareness is what keeps a tracked `build/keep.txt` in the snapshot when `build/` is ignored.

### §5 — Tools wording/perf nits (#41)

Docker's non-UTF-8 refusal becomes the host's, via a `tool` parameter on `_read_raw` and `_transform_file`. `describe_change` becomes single-pass — one `SequenceMatcher` feeding both the counts and `get_grouped_opcodes(2)`, with `difflib._format_range_unified`'s two special cases reproduced — proved byte-identical to `difflib.unified_diff` by a seeded property test. CRLF rendering and two doc nits are documented.

### §6 — Test-coverage gaps (#42)

Tests plus three one-line hardenings: `--feedback ""`/whitespace-only normalizes to `None` at parse, and explicit-`null` `verify_rounds`/`verify_timeout` in a hand-edited `run.json` fall back to the defaults.

### §7 — Cross-cutting

`DEFAULT_IMAGE` → `:0.10`, `PINNED_DIGEST = None` (pin in 0.10.1), version `0.10.0` in both sources, CI docker-live tag, every doc mention, and the consolidated contract prose.

## Global Constraints

- **Python stdlib only. 3.9.6 floor.** No `match`, no runtime `X | Y` unions, no 3.10+ APIs. `X | None` **in annotations** is fine only in modules that already carry `from __future__ import annotations` — every production module this plan touches (`dirtywork/tools.py`, `builtin_tools.py`, `runner.py`, `runs.py`, `rundir.py`, `budget.py`, `workspace.py`, `__main__.py`, `sandbox/__init__.py`, `sandbox/host.py`, `sandbox/docker.py`, `sandbox/export.py`, `sandbox/docker_args.py`, `providers/__init__.py`, `providers/openai_compat.py`) already has it, and so does every test module this plan touches. The one NEW production module, `dirtywork/providers/ollama.py`, gets the import as its first line.
- **Test command: `/usr/bin/python3 -m pytest -q`.** That is the ONLY interpreter on this machine with pytest installed, and it is the 3.9 floor. Every gate in this plan is on the **exit code** of that command — never on parsing its tail output.
- **Transcript JSONL `schema_version` stays 2 and is additive-only.** The stdout JSON contract only ever gains keys; no existing key is renamed or removed, and no existing CLI stdout line is lost.
- **No host git ever touches worker-controlled content** except the existing index-only plumbing path in `dirtywork/workspace.py`. No task adds a host `git add`, `git commit`, `git status` or `git checkout` against a worktree the worker wrote into.
- **Tool functions never raise.** They return `str` results or `ERROR:` strings. The ONE spec'd exception is `tools._write_atomic`, which re-raises a non-`OSError` `BaseException` (KeyboardInterrupt, `BudgetExceeded`, `SandboxError` from a budget hook) after unlinking its temp, per spec §2.2 step 4 — those are run-level signals the runner owns, not tool results.
- **Host/docker parity.** Identical user-visible strings from identical conditions, asserted by parity tests wherever the spec says so: the three `append_file` caps, the does-not-exist refusal and the non-UTF-8 refusal. Two strings are deliberately **not** unified because the spec spells them differently per backend — the non-regular-file refusal for an append (host `ERROR: cannot append to '<path>': '<abs>' is not a regular file (refusing FIFO/device/socket)` from the probe, docker `ERROR: cannot append to '<path>': not a regular file` from guard rc 3) and the pre-existing `_read_raw` size wording. That divergence is stated in Task 4's header, not silently absorbed.
- **Filing or commenting on GitHub issues is NEVER a task step.** The controller handles every outward-facing action. No task may run `gh`.
- **DRY.** A string or a rule that two call sites need lives in exactly one place and is imported: `tools._append_oversized`, `tools._append_missing`, `tools._not_utf8`, `tools._result_too_big`, `tools.tmp_name`, `tools.is_temp_name`, `docker._PROMOTE` (shared by both container write scripts), `rundir.run_dir_for`, `workspace._hash_entries`, `workspace._check_snapshot_path`. `dirtywork/sandbox/docker.py` imports the shared names from `..tools` exactly the way it already imports `MAX_WRITE_BYTES`.
- **Every existing test stays green after every task.** Run `/usr/bin/python3 -m pytest -q` at the end of each task; the number may only rise from the baseline in *Precondition*.
- **The expected pass counts below assume exactly the tests this plan writes.** If you add one more assertion inside an existing test the number does not change; if you split a test in two it rises by one. The binding invariant is that the count never falls and nothing fails.
- **New tests go into existing test modules**, with exactly three exceptions, all sanctioned by spec §7: `tests/fixtures/providers/ollama/` (eight files), `tests/test_provider_ollama.py`, and `tests/test_live_ollama.py`.
- **Commit after each task** with the exact message given in the task's final step.
- **Never leave a placeholder.** Every code step below is the actual code; every test step the actual test.

## Precondition

Branch `v1rc-0.10`, dirtywork **0.9.1**, working tree clean.

**Line numbers in this plan are navigational only.** Every `file.py:NN` anchor below was read off this tree while the plan was written and some have since drifted by a line or ten; they tell you where to *look*, never what to *match*. The verbatim **Before/After** blocks are the sole binding edit targets — locate each edit by its quoted text, and if the quoted text is not there, stop and re-read rather than trusting the number.

**Baseline (measured on this branch with `/usr/bin/python3 -m pytest -q` from the repo root): `1026 passed, 1 skipped, 18 deselected in ~41s`.** The 18 deselected are the `docker`/`live` markers excluded by `pyproject.toml`'s `addopts = "-m 'not live and not docker'"`; the 1 skip is the undecodable-filename test in `tests/test_workspace.py`, which needs a filesystem that accepts one. Both are normal. A task may only raise 1026.

Every name below already exists exactly as written:

- `dirtywork/tools.py`: `MAX_RESULT_CHARS`, `MAX_DIFF_LINES` (`:18`), `MAX_DIFF_CHARS` (`:19`), `DESCRIBE_DIFF_MAX_LINES` (`:23`), `MAX_READ_BYTES` (`:27`), `MAX_WRITE_BYTES` (`:28`), `MAX_LIST_ENTRIES`, `_cap` (`:32`), `_open_regular` (`:39`), `_worktree_candidate` (`:82`), `_number_lines`, `_lines_keep_newlines`, `_line_counts` (`:143`), `describe_change` (`:149`), `describe_write` (`:203`), `insert_text`, `_read_text_for_diff` (`:246`), `read_file` (`:261`), `write_file` (`:277-312`), `_transform_file` (`:315-360`), `_replace_once`, `_insert_once`, `_check_write_size` (`:391-407`), `_apply_edits_once`, `edit_file` (`:468`), `insert_before`, `insert_after`, `apply_edits`, `list_dir`, `GREP_TIMEOUT_TEXT`, `grep`, `MAX_BASH_CHARS`, `TIMEOUT_PREFIX`, `timeout_result`, `is_timeout_result`, `bash`.
- `dirtywork/builtin_tools.py`: module docstring (`:1`, "The ten tools…"), `TOOL_OUTPUT_CAP` (`:19`), `_write_file` (`:34`), `WRITE_FILE_SPEC` (`:89-98`), `EDIT_FILE_SPEC`, `APPLY_EDITS_SPEC`, `INSERT_BEFORE_SPEC`, `BUILTIN_SPECS` (`:243-245`), `default_registry` (`:248`).
- `dirtywork/runner.py`: `DEFAULT_WINDOW` (`:31`), `TRIM_MARKER`, `CHARS_PER_TOKEN` (`:33`), `BUDGET_FRACTION` (`:34`), `FAILURE_KINDS`, `FailureTracker`, `NUDGES` (`:76-84`), `_join_nudges`, `classify_text_reply` (`:117`), `TIMEOUT_NUDGE`, `_MUTATING_TOOLS` (`:160`), `parse_exit_code`, `ProgressTracker`, `RepeatTracker`, `resolve_context_window` (`:314`), `trim_messages` (`:370`), `RunResult`, `Runner.__init__` (`:392-431`), `Runner.run` (`:440-…`) with the `run_start` write at `:445-449`, the `provider.chat` call at `:589-592`, the `assistant` write at `:627-630`, and the `finish_reason == "length"` branch at `:678-688`.
- `dirtywork/rundir.py`: `RUNS_DIR` (`:9`), `RunDirError` (`:13`), `create_run_dir`, `write_run_json`, `read_run_json`.
- `dirtywork/runs.py`: `_SLUG_RE` (`:66`), `_run_dir_for` (`:70-84`), `_existing_run_dir`, `SHOW_FIELDS` (`:34-38`), `RunsError`.
- `dirtywork/budget.py`: `BudgetReport` (`:20-25`), `_UnreadableDir`, `_measure_posix` (`:45`), `_measure_windows` (`:89`), `measure_worktree` (`:139-142`).
- `dirtywork/workspace.py`: `GIT_NEUTRAL_FLAGS`, `git_env`, `_git`, `host_read_tree`, `_walk_worktree` (`:349-421`), `EMPTY_TREE_SHA` (`:428`), `_ignored_relpaths` (`:431-455`), `_check` (`:457`), `snapshot_worktree` (`:464-626`), `WorkspaceError`, `SNAPSHOT_AUTHOR`.
- `dirtywork/__main__.py`: `build_system_prompt` (`:71-99`, the file-change rule at `:88`), `_err`, `PreflightFailure`, `RunContext`, `_positive_int` (`:157`), `_ENDPOINT_HINTS` (`:171-174`), `_preflight_llm` (`:177-193`), `_resolve_context_window` (`:196-210`), `_resolve_branch_from` (`:226-267`), `_write_run_json_start` (`:417-447`), `_update_run_json`, `_emit_result`, `_contract_fields`, `_load_feedback` (`:639-656`), `_load_resume_target` (`:659-711`), `_workspace_resume`, `_execute` (`:742-…`, the `Runner(...)` construction at `:793-810`), `_add_run_flags` (`:876-921`), `main` (`:1043-1064`).
- `dirtywork/sandbox/__init__.py`: `Sandbox` Protocol (`:45-83`) with its tool enumeration sentence at `:52`.
- `dirtywork/sandbox/host.py`: `HostSandbox` (`:16`), `start` (`:29-33`), `_measure` (`:35-37`), `_check_budget` (`:39-42`), `write_file` (`:47-50`), `insert_after` (`:67-70`), `finalize` (`:84-99`).
- `dirtywork/sandbox/docker.py`: the `..tools` import block (`:15-29`), `READ_EXEC_TIMEOUT`/`WRITE_EXEC_TIMEOUT` (`:44-45`), `_rel` (`:49`), `_oversized` (`:72-83`), `DockerSandbox` (`:86`), `start` (`:159`), `_read_raw` (`:392-417`), `read_file` (`:419`), `_write_raw` (`:425-447`), `write_file` (`:449-459`), `_transform_file` (`:461-484`), `edit_file` (`:486`), `apply_edits` (`:489`), `insert_before` (`:492`), `insert_after` (`:495`).
- `dirtywork/sandbox/export.py`: `export_run`'s `.git`-entry `find` (`:245-256`) and the `git add -A` at `:258-261`.
- `dirtywork/sandbox/docker_args.py`: `DEFAULT_IMAGE` (`:8`), `PINNED_DIGEST` (`:23`), `exec_argv` (`:70-80`).
- `dirtywork/providers/__init__.py`: `PROVIDER_NAMES` (`:14`), `DEFAULT_BASE_URLS` (`:16-19`), `ToolCall` (`:22-41`), `ChatResponse`, `Provider` (`:52-74`), `get_provider` (`:88-99`), `sanitize_usage`.
- `dirtywork/providers/openai_compat.py`: `DEFAULT_BASE_URL` (`:9`), `CONTEXT_WINDOWS` (`:13-16`), `LOADED_CONTEXT_PROBE_TIMEOUT` (`:21`), `_origin` (`:26-33`), `parse_chat_response`, `OpenAICompatClient` (`:125`) with `__init__` (`:131-135`), `list_models`, `context_window` (`:150-151`), `loaded_context_window` (`:153-190`), `chat` (`:192-203`).
- Test helpers: `tests/docker_fakes.py`'s `FakeDocker`/`FakePopen`/`_ok`/`_fail`; `tests/test_docker_sandbox.py`'s `docker`/`started` fixtures; `tests/test_runner.py`'s `FakeProvider`, `parts` fixture, `_resp`, `_call`, `_bad_args`, `_events`; `tests/test_main.py`'s `_host_repo`, `_install_host_harness`, `_ScriptedClient`, `_read_only_run_json`, `_first_run`; `tests/provider_doubles.py`'s `DictProvider`, `patch_provider`, `text_body`, `tool_call_body`; `tests/provider_contract.py`'s `ProviderContract` and `RecordingTransport`; `tests/test_provider_openai.py`'s `_client`; `tests/test_tools_files.py`'s `wt` fixture and `_hang_guard`; `tests/test_sandbox_host.py`'s `wt` fixture; `tests/test_workspace.py`'s `_git` helper and `_snapshot_repo`.
- The frozen wire fixture `tests/fixtures/tool_schemas.json` is exactly `json.dumps(default_registry().schemas(), indent=2, ensure_ascii=False) + "\n"`.

## File Structure

```
dirtywork/
  tools.py                 # MODIFIED — T1 (_UMASK, TMP_*, tmp_name, is_temp_name, _write_all,
                           #   _unlink_quietly, _write_atomic, _result_too_big, _append_oversized,
                           #   _append_missing, _not_utf8), T2 (append_file), T6 (write_file,
                           #   _transform_file onto _write_atomic), T11 (describe_change single-pass)
  builtin_tools.py         # MODIFIED — T5 (APPEND_FILE_SPEC, _append_file, docstring, BUILTIN_SPECS)
  runner.py                # MODIFIED — T5 (_MUTATING_TOOLS), T7 (DEFAULT_MAX_TOKENS, max_tokens,
                           #   char_budget, run_start, finish_reason), T8 (truncation recovery, NUDGES)
  runs.py                  # MODIFIED — T10 (_run_dir_for onto the shared helper)
  rundir.py                # MODIFIED — T10 (_SLUG_RE, run_dir_for)
  budget.py                # MODIFIED — T6 (BudgetReport.swept, sweep_temps)
  workspace.py             # MODIFIED — T10 (_check_snapshot_path, BFS _walk_worktree,
                           #   _hash_entries, _head_entries, per-invocation empty tree)
  __main__.py              # MODIFIED — T5 (system prompt), T7 (--max-tokens), T9 (_MODEL_HINTS,
                           #   _ENDPOINT_HINTS), T10 (_resolve_branch_from), T12 (feedback/verify)
  sandbox/
    __init__.py            # MODIFIED — T5 (Protocol.append_file + docstring enumeration)
    host.py                # MODIFIED — T2 (append_file), T6 (sweep at start/finalize)
    docker.py              # MODIFIED — T3 (_read_raw tool=, _transform_file tool=, WRITE_SCRIPT),
                           #   T4 (_append_guard, _append_write, append_file), T6 fix round 1
                           #   (temp sweep in finalize() before _stop_container() -- moved here
                           #   from export.py, whose /work mount is readonly)
    export.py              # MODIFIED — T6 (temp sweep before git add -A; reverted in fix round 1,
                           #   see the Task 6 amendment note -- export's /work mount is readonly)
    docker_args.py         # MODIFIED — T13 (:0.10, PINNED_DIGEST = None)
  providers/
    __init__.py            # MODIFIED — T9 (PROVIDER_NAMES, DEFAULT_BASE_URLS, get_provider)
    ollama.py              # NEW — T9
  __init__.py              # MODIFIED — T13 (0.10.0)
pyproject.toml             # MODIFIED — T9 (ollama marker + addopts), T13 (0.10.0)
.github/workflows/ci.yml   # MODIFIED — T13 (:0.10)
README.md                  # MODIFIED — T5, T6, T9, T11, T13
docker/README.md           # MODIFIED — T13 (:0.10)
docs/operating.md          # MODIFIED — T5, T6, T7, T9
docs/security.md           # MODIFIED — T5, T6
docs/machine-contract.md   # MODIFIED — T5, T6, T7, T9, T11, T13
docs/transcript-schema.md  # MODIFIED — T5, T7, T11, T13
tests/
  docker_fakes.py          # MODIFIED — T4 (_rc helper)
  test_tools_files.py      # MODIFIED — T1, T2, T6, T11
  test_sandbox_host.py     # MODIFIED — T2, T6
  test_docker_sandbox.py   # MODIFIED — T3, T4, T12
  test_builtin_tools.py    # MODIFIED — T5 (FakeSandbox, schemas shape, dispatch)
  test_transcript_schema.py# MODIFIED — T5, T7, T11, T13
  test_runner.py           # MODIFIED — T5, T7, T8
  test_main.py             # MODIFIED — T7, T9, T10, T12
  test_runs.py             # MODIFIED — T10
  test_workspace.py        # MODIFIED — T10
  test_providers.py        # MODIFIED — T9
  test_budget.py           # MODIFIED — T6
  test_export_flow.py      # MODIFIED — T6
  test_docker_args.py      # MODIFIED — T13
  test_provider_ollama.py  # NEW — T9
  test_live_ollama.py      # NEW — T9
  fixtures/providers/ollama/*.json  # NEW (8 files) — T9
  fixtures/tool_schemas.json        # REGENERATED — T5
```

---

### Task 1: the shared write helpers — `_write_atomic`, the temp-name rule, and the strings both backends will share (spec §2.2, §2.5, §1.2 caps)

Nothing calls `_write_atomic` yet: Task 1 builds and proves the primitive on its own, so the tasks that adopt it (2, 3, 4, 6) are each one small rewiring rather than a rewiring plus a new primitive. The suite is green at the end of this task with exactly the shipped behaviour it has today plus the new, directly-tested helpers.

**Files:**
- Modify: `dirtywork/tools.py` (the import block `:1-12`; a new constants+helpers block immediately after `MAX_LIST_ENTRIES` at `:29`; `_write_all`/`_unlink_quietly`/`_write_atomic` immediately after `_worktree_candidate` which ends at `:95`; `_result_too_big`/`_append_oversized`/`_append_missing`/`_not_utf8` immediately before `_check_write_size` at `:391`; `_check_write_size`'s body `:391-407`; `_transform_file`'s UTF-8 line `:335`)
- Modify: `tests/test_tools_files.py` (18 new tests, appended at the end of the file)

**Interfaces:**
- Consumes: `tools.MAX_WRITE_BYTES` (`tools.py:28`), `tools._open_regular` (`tools.py:39`), `tools._worktree_candidate` (`tools.py:82`).
- Produces:
  - `tools._UMASK: int` — the process umask, read once at import.
  - `tools.TMP_PREFIX: str` (`".dw-tmp."`), `tools.TMP_NAME_RE: re.Pattern`, `tools.TMP_FIND_REGEX: str`
  - `tools.tmp_name(basename: str) -> str`
  - `tools.is_temp_name(name: str) -> bool`
  - `tools._write_all(fd: int, data: bytes) -> None`
  - `tools._unlink_quietly(p: Path) -> None`
  - `tools._write_atomic(target: Path, data: bytes, *, path: str, verb: str = "write", create_parents: bool = False, must_exist: bool = False) -> str | None`
  - `tools._result_too_big(size: int) -> str`
  - `tools._append_oversized(encoded: bytes) -> str | None`
  - `tools._append_missing(path: str) -> str`
  - `tools._not_utf8(path: str, tool: str) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools_files.py` (the module already imports `errno`, `os` and `signal` at `:145-148`, inside the file, and `pytest`/`Path`/`tools` at the top):

```python
# --- spec §2.2/§2.5/§1.2: the shared write primitive and the shared strings.
# --- Nothing in dirtywork calls _write_atomic yet (Tasks 2, 3, 4 and 6 wire it
# --- up); these tests are the primitive's own contract.

import stat as _stat


def _temp_leftovers(directory: Path) -> list:
    return sorted(p.name for p in directory.iterdir() if p.name.startswith(tools.TMP_PREFIX))


def test_tmp_name_has_the_generated_shape_and_is_random(wt: Path):
    first = tools.tmp_name("app.py")
    second = tools.tmp_name("app.py")
    assert re.fullmatch(r"\.dw-tmp\.app\.py\.[0-9a-f]{8}", first)
    assert first != second          # the worker controls sibling names
    assert tools.is_temp_name(first) and tools.is_temp_name(second)


def test_is_temp_name_ignores_a_worker_file_that_only_starts_like_one(wt: Path):
    # Spec §2.5: the sweep matches the FULL generated shape, never a bare glob.
    assert not tools.is_temp_name(".dw-tmp.notes")
    assert not tools.is_temp_name(".dw-tmp.notes.txt")
    assert not tools.is_temp_name(".dw-tmp.app.py.DEADBEEF")   # we only ever emit lowercase
    assert not tools.is_temp_name("app.py")


def test_write_atomic_creates_a_new_file_with_umask_default_mode(wt: Path):
    target = wt / "new.txt"
    assert tools._write_atomic(target, b"hello\n", path="new.txt") is None
    assert target.read_bytes() == b"hello\n"
    # Exactly what _open_regular(..., O_CREAT, mode=0o644) produced before 0.10.
    assert _stat.S_IMODE(target.stat().st_mode) == 0o644 & ~tools._UMASK
    assert _temp_leftovers(wt) == []


def test_write_atomic_preserves_an_existing_files_mode(wt: Path):
    target = wt / "script.sh"
    target.write_text("#!/bin/sh\n")
    target.chmod(0o755)
    assert tools._write_atomic(target, b"#!/bin/sh\necho hi\n", path="script.sh") is None
    assert target.read_bytes() == b"#!/bin/sh\necho hi\n"
    assert _stat.S_IMODE(target.stat().st_mode) == 0o755
    assert _temp_leftovers(wt) == []


def test_write_atomic_promotes_by_rename_so_the_inode_changes(wt: Path):
    target = wt / "swap.txt"
    target.write_text("old\n")
    before = target.stat().st_ino
    assert tools._write_atomic(target, b"new\n", path="swap.txt") is None
    assert target.read_bytes() == b"new\n"
    assert target.stat().st_ino != before   # spec §2.3: os.replace changes the inode


def test_write_atomic_refuses_a_symlink_with_the_shipped_wording(wt: Path):
    real = wt / "real.txt"
    real.write_text("original")
    link = wt / "link.txt"
    os.symlink(real, link)
    out = tools._write_atomic(link, b"new", path="link.txt")
    assert out == ("ERROR: 'link.txt' is a symlink; writing through a symlink is not "
                   "allowed even when its target is inside the worktree")
    assert real.read_text() == "original"
    assert _temp_leftovers(wt) == []


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs require a POSIX OS")
def test_write_atomic_refuses_a_fifo_with_the_shipped_wording(wt: Path):
    fifo = wt / "pipe"
    os.mkfifo(fifo)
    with _hang_guard():
        out = tools._write_atomic(fifo, b"new", path="pipe")
    assert out == "ERROR: 'pipe' is not a regular file (refusing FIFO/device/socket)"
    assert _temp_leftovers(wt) == []


def test_write_atomic_writes_through_a_hardlinked_target(wt: Path):
    # Spec §2.2 step 2: a hardlink is MEANT to see the write, so shared-inode
    # semantics (and today's non-atomicity) are preserved on purpose.
    a = wt / "a.txt"
    a.write_text("old\n")
    b = wt / "b.txt"
    os.link(a, b)
    before = a.stat().st_ino
    assert tools._write_atomic(a, b"new\n", path="a.txt") is None
    assert a.read_bytes() == b"new\n"
    assert b.read_bytes() == b"new\n"          # the link sees it
    assert a.stat().st_ino == before           # no rename happened
    assert _temp_leftovers(wt) == []


# `os.geteuid` does not exist on Windows and this decorator runs at COLLECTION
# time, so it is guarded exactly the way tests/test_budget.py:79 guards its own.
@pytest.mark.skipif(getattr(os, "geteuid", lambda: -1)() == 0 or os.name == "nt",
                    reason="root (and Windows) ignore directory permissions")
def test_write_atomic_falls_back_to_the_fd_in_an_unwritable_directory(wt: Path):
    # Spec §2.2 step 5: a writable file in a 0555 directory cannot be renamed
    # into place, so the probe fd is used -- today's semantics, preserved.
    sub = wt / "locked"
    sub.mkdir()
    target = sub / "f.txt"
    target.write_text("old\n")
    sub.chmod(0o555)
    try:
        assert tools._write_atomic(target, b"new\n", path="locked/f.txt") is None
        assert target.read_bytes() == b"new\n"
    finally:
        sub.chmod(0o755)


@pytest.mark.skipif(getattr(os, "geteuid", lambda: -1)() == 0 or os.name == "nt",
                    reason="root (and Windows) ignore directory permissions")
def test_write_atomic_refuses_a_new_file_in_an_unwritable_directory(wt: Path):
    # No probe fd exists (ENOENT), so there is nothing to fall back to: the
    # temp-creation errno is reported, preserving today's EACCES refusal.
    sub = wt / "locked2"
    sub.mkdir()
    sub.chmod(0o555)
    try:
        out = tools._write_atomic(sub / "new.txt", b"x", path="locked2/new.txt")
    finally:
        sub.chmod(0o755)
    assert out.startswith("ERROR: cannot write 'locked2/new.txt': ")
    assert "Permission denied" in out
    assert not (sub / "new.txt").exists()


def test_write_atomic_creates_parents_only_when_asked(wt: Path):
    made = wt / "deep" / "new" / "f.txt"
    assert tools._write_atomic(made, b"hi", path="deep/new/f.txt",
                               create_parents=True) is None
    assert made.read_bytes() == b"hi"
    missing = wt / "other" / "f.txt"
    out = tools._write_atomic(missing, b"hi", path="other/f.txt")
    assert out.startswith("ERROR: cannot write 'other/f.txt': ")
    assert not (wt / "other").exists()


def test_write_atomic_append_verb_changes_only_the_generic_tail(wt: Path, monkeypatch):
    target = wt / "f.txt"
    target.write_text("old\n")

    def _boom(fd, data):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(tools, "_write_all", _boom)
    out = tools._write_atomic(target, b"new\n", path="f.txt", verb="append")
    assert out.startswith("ERROR: cannot append to 'f.txt': ")
    assert "No space left on device" in out


def test_write_atomic_returns_an_error_string_on_an_oserror_during_the_write(wt: Path, monkeypatch):
    target = wt / "f.txt"
    target.write_text("old\n")

    def _boom(fd, data):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(tools, "_write_all", _boom)
    out = tools._write_atomic(target, b"new\n", path="f.txt")
    assert out.startswith("ERROR: cannot write 'f.txt': ")
    assert target.read_bytes() == b"old\n"     # spec §2.3: byte-identical
    assert _temp_leftovers(wt) == []           # the temp was unlinked in-call


def test_write_atomic_surfaces_a_close_failure_without_raising(wt: Path, monkeypatch):
    # Spec §2.2: the temp fd is closed BEFORE the promote precisely so a
    # DEFERRED write error surfaces while the target is still untouched. The
    # handle is cleared before that close, so the except arm's own cleanup
    # never closes an already-closed fd -- an EBADF escaping the handler would
    # be a tool function raising, which the contract forbids.
    target = wt / "deferred.txt"
    target.write_text("old\n")
    staged = {}
    real_write_all = tools._write_all
    real_close = os.close

    def _record(fd, data):
        staged["fd"] = fd            # _write_all only ever gets the temp fd
        return real_write_all(fd, data)

    def _closing(fd):
        real_close(fd)
        if fd == staged.get("fd"):
            raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(tools, "_write_all", _record)
    monkeypatch.setattr(os, "close", _closing)
    out = tools._write_atomic(target, b"new\n", path="deferred.txt")
    assert out.startswith("ERROR: cannot write 'deferred.txt': ")
    assert "Input/output error" in out
    assert target.read_bytes() == b"old\n"     # never promoted
    assert _temp_leftovers(wt) == []


def test_write_atomic_reraises_a_non_oserror_and_unlinks_its_temp(wt: Path, monkeypatch):
    # Spec §2.2 step 4: KeyboardInterrupt / BudgetExceeded / SandboxError are
    # run-level signals the runner owns, NOT tool results.
    target = wt / "f.txt"
    target.write_text("old\n")

    def _boom(fd, data):
        raise KeyboardInterrupt

    monkeypatch.setattr(tools, "_write_all", _boom)
    with pytest.raises(KeyboardInterrupt):
        tools._write_atomic(target, b"new\n", path="f.txt")
    assert target.read_bytes() == b"old\n"
    assert _temp_leftovers(wt) == []


def test_append_oversized_wording_is_not_the_write_file_wording(wt: Path):
    # Spec §1.2 cap 1: an append's fix is "append in smaller pieces", never
    # write_file's "write the file in smaller pieces".
    assert tools._append_oversized(b"x" * 10) is None
    out = tools._append_oversized(b"x" * (tools.MAX_WRITE_BYTES + 1))
    assert out == (f"ERROR: text is {tools.MAX_WRITE_BYTES + 1} bytes, over the "
                   f"{tools.MAX_WRITE_BYTES}-byte write limit; append in smaller pieces")
    assert "write the file in smaller pieces" not in out


def test_result_too_big_is_the_shared_transform_string(wt: Path):
    # The same sentence _check_write_size has emitted since 0.9, now built in
    # one place so docker's append (which learns the size from `stat`, not from
    # a buffer) can render it byte-identically.
    assert tools._result_too_big(99) == (
        f"ERROR: result is 99 bytes, over the {tools.MAX_WRITE_BYTES}-byte "
        f"write limit; nothing was written")
    huge = "x" * (tools.MAX_WRITE_BYTES + 1)
    assert tools._check_write_size(huge) == tools._result_too_big(tools.MAX_WRITE_BYTES + 1)
    assert tools._check_write_size("small") is None


def test_append_missing_and_not_utf8_strings(wt: Path):
    assert tools._append_missing("notes.md") == (
        "ERROR: cannot append to 'notes.md': it does not exist; create it with "
        "write_file first")
    assert tools._not_utf8("bin.dat", "append_file") == (
        "ERROR: bin.dat is not valid UTF-8 text; append_file only works on text files")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_tools_files.py -q -k "tmp_name or is_temp_name or write_atomic or append_oversized or result_too_big or append_missing"`
Expected: collection succeeds and 18 tests fail, every one with `AttributeError: module 'dirtywork.tools' has no attribute '<name>'` (`tmp_name`, `is_temp_name`, `_write_atomic`, `_append_oversized`, `_result_too_big`, `_append_missing`, `_not_utf8`, `TMP_PREFIX`, `_UMASK`, `_write_all`). `test_write_atomic_surfaces_a_close_failure_without_raising` is among them: its `real_write_all = tools._write_all` line raises the same `AttributeError` on `_write_all` before it reaches `_write_atomic`.

- [ ] **Step 3: Add `re` to the imports**

In `dirtywork/tools.py`.

Before:

```python
import difflib
import errno
import os
import shutil
import stat
import subprocess
```

After:

```python
import difflib
import errno
import os
import re
import shutil
import stat
import subprocess
```

- [ ] **Step 4: Add the umask and temp-name block**

In `dirtywork/tools.py`, immediately **after** `MAX_LIST_ENTRIES = 2000` and immediately **before** `def _cap(`.

```python
# Read ONCE, at import, before any thread exists: os.umask is process-global
# and changing it is not thread-safe, so it can never be queried lazily inside
# a tool call. A brand-new file staged through _write_atomic is chmod'd to
# `0o644 & ~_UMASK`, which is exactly the mode
# _open_regular(..., O_CREAT, mode=0o644) produced before 0.10 -- masking a
# 0o666 base instead would silently drop the group/other read bits under a
# `umask 0` operator.
_UMASK = os.umask(0)
os.umask(_UMASK)

# Spec §2.2/§2.5: every staged write lands in a sibling temp named
# `.dw-tmp.<basename>.<8 lowercase hex>`. The random suffix is required because
# the WORKER controls sibling names; the sweep matches the full generated shape
# with an anchored regex (never a bare glob), so a worker file literally named
# `.dw-tmp.notes` is left alone.
TMP_PREFIX = ".dw-tmp."
TMP_NAME_RE = re.compile(r"\.dw-tmp\..+\.[0-9a-f]{8}")
# The same shape as TMP_NAME_RE written as the POSIX extended regex GNU
# `find -regex` wants (it matches the WHOLE path, hence the leading `.*/`).
# Kept here, beside TMP_NAME_RE, so the host sweep and the container sweep can
# never drift apart.
TMP_FIND_REGEX = r".*/\.dw-tmp\..+\.[0-9a-f]{8}"


def tmp_name(basename: str) -> str:
    """The staging name for a write to `basename`. The ONE generator: the host
    primitive uses it directly and DockerSandbox generates the name host-side
    with it too, passing it into the container as "$2" so worker-controlled
    bytes never reach the script text."""
    return f"{TMP_PREFIX}{basename}.{os.urandom(4).hex()}"


def is_temp_name(name: str) -> bool:
    """True for a name tmp_name() generated (spec §2.5). Anchored on the FULL
    shape: `.dw-tmp.notes` is a worker's file, not ours, and is never swept."""
    return bool(TMP_NAME_RE.fullmatch(name))
```

- [ ] **Step 5: Add `_write_all`, `_unlink_quietly` and `_write_atomic`**

In `dirtywork/tools.py`, immediately **after** `_worktree_candidate` (which ends with `return candidate.parent.resolve() / candidate.name`) and immediately **before** `def _number_lines(`.

```python
def _write_all(fd: int, data: bytes) -> None:
    """os.write may write fewer bytes than asked; loop until the buffer is
    gone. Raw os.write is used rather than a buffered file object precisely so
    there is no userspace buffer left to flush -- which is what lets
    _write_atomic surface a deferred write error from its os.close BEFORE it
    promotes the temp over the target."""
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _unlink_quietly(p: Path) -> None:
    """Remove a staging temp, ignoring the case where it is already gone. Never
    raises: it only ever runs on an unwind path that has its own outcome."""
    try:
        os.unlink(str(p))
    except OSError:
        pass


def _write_atomic(target: Path, data: bytes, *, path: str, verb: str = "write",
                  create_parents: bool = False, must_exist: bool = False):
    """Spec §2.2: write `data` to `target` so a failure or a kill during the
    write leaves the file byte-identical instead of truncated. Returns None on
    success or an `ERROR: …` string -- never an OSError, because a tool
    function's contract is to return its failure as text.

    `target` is the caller's already-containment-checked `_worktree_candidate`
    path (callers keep their own `resolve_in_worktree` call). `path` is the
    MODEL-FACING path string every message renders -- the caller's own
    argument -- so the refusals read exactly as they did before 0.10 rather
    than leaking an absolute host path. `verb` picks the generic wording:
    "write" -> `cannot write '<path>'`, "append" -> `cannot append to
    '<path>'`; the ELOOP and non-regular-file strings are shared verbatim
    between the two.

    `must_exist=True` turns off §2.2's new-file branch: an ENOENT probe then
    returns `_append_missing(path)` instead of staging a temp and creating the
    target. Spec §1.2 requires it -- "ENOENT on the probe is the does-not-exist
    error above, never §2.2's new-file branch" -- and it is what makes the host
    refuse the delete-between-read-and-write race the container already refuses
    with the append script's `[ -f "$1" ] || exit 2`. `append_file` is the only
    caller that passes it.

    Two branches deliberately keep today's in-place, non-atomic behaviour and
    are named in docs/machine-contract.md and docs/operating.md:
      * `st_nlink > 1` -- a hardlink is MEANT to see the write, so the shared
        inode is written through rather than replaced;
      * a directory this process cannot create a temp in (0555) -- a rename is
        impossible there, so the probe fd is the only write left.

    Robustness, not a security fix (spec §2.1): the O_NOFOLLOW refusals stay
    exactly as deterministic as today. A symlink present at call time refuses
    below; one that appears between the probe and os.replace is replaced AS A
    LINK, because rename(2) does not dereference its destination -- so nothing
    is ever written through it (spec §2.4)."""
    lead = "cannot write" if verb == "write" else "cannot append to"
    if create_parents:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return f"ERROR: {lead} '{path}': {e}"
    probe_fd = None
    try:
        # Side-effect-free probe: no O_CREAT, so a refusal never leaves a file
        # behind. O_NONBLOCK makes a FIFO with no reader fail with ENXIO
        # instead of hanging; O_NOFOLLOW closes the final-component symlink
        # TOCTOU.
        probe_fd = os.open(str(target),
                           os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as e:
        if e.errno == errno.ELOOP:
            return (f"ERROR: '{path}' is a symlink; writing through a symlink is not "
                    f"allowed even when its target is inside the worktree")
        if e.errno == errno.ENXIO:
            return f"ERROR: '{path}' is not a regular file (refusing FIFO/device/socket)"
        if e.errno != errno.ENOENT:
            return f"ERROR: {lead} '{path}': {e}"
        if must_exist:
            # Spec §1.2: for an append, ENOENT is the does-not-exist refusal,
            # never the new-file branch below -- the target must not be created
            # by a write that was only ever meant to extend it.
            return _append_missing(path)
        # ENOENT: there is no file yet. Fall through to the temp with no mode
        # to preserve and no fd to fall back to.
    try:
        st = None
        if probe_fd is not None:
            st = os.fstat(probe_fd)
            if not stat.S_ISREG(st.st_mode):
                return (f"ERROR: {lead} '{path}': '{target}' is not a regular file "
                        f"(refusing FIFO/device/socket)")
            if st.st_nlink > 1:
                try:
                    os.ftruncate(probe_fd, 0)
                    _write_all(probe_fd, data)
                except OSError as e:
                    return f"ERROR: {lead} '{path}': {e}"
                return None
        tmp = target.parent / tmp_name(target.name)
        try:
            tmp_fd = os.open(
                str(tmp),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600)
        except OSError as e:
            if probe_fd is not None and e.errno in (errno.EACCES, errno.EROFS):
                try:
                    os.ftruncate(probe_fd, 0)
                    _write_all(probe_fd, data)
                except OSError as e2:
                    return f"ERROR: {lead} '{path}': {e2}"
                return None
            return f"ERROR: {lead} '{path}': {e}"
        # One catch boundary from here on (spec §2.2 step 4).
        try:
            _write_all(tmp_fd, data)
            os.fchmod(tmp_fd, stat.S_IMODE(st.st_mode) if st is not None
                      else 0o644 & ~_UMASK)
            # Closed BEFORE the promote so a deferred write error surfaces
            # while the target is still untouched. The handle is CLEARED
            # first: os.close consumes the fd whether or not it raises, so if
            # this close is the one that reports the deferred error, the
            # handlers below must not try to close it a second time -- an
            # EBADF out of an except arm would be a tool function raising.
            fd, tmp_fd = tmp_fd, None
            os.close(fd)
            os.replace(str(tmp), str(target))
        except OSError as e:
            if tmp_fd is not None:
                # Defensive: cleanup must not replace the real diagnosis with
                # its own errno.
                try:
                    os.close(tmp_fd)
                except OSError:
                    pass
            _unlink_quietly(tmp)
            return f"ERROR: {lead} '{path}': {e}"
        except BaseException:
            if tmp_fd is not None:
                try:
                    os.close(tmp_fd)
                except OSError:
                    pass
            _unlink_quietly(tmp)
            raise
        return None
    finally:
        if probe_fd is not None:
            os.close(probe_fd)
```

- [ ] **Step 6: Add the four shared strings and route the two existing sites through them**

In `dirtywork/tools.py`, insert immediately **before** `def _check_write_size(new_text: str):`.

```python
def _result_too_big(size: int) -> str:
    """Spec §1.5/§1.2 cap 3: the ONE place the over-the-write-limit RESULT
    sentence is built. `_check_write_size` renders it from a buffer it holds;
    `append_file` renders it from a size it learned some other way (an
    `os.stat` on the host, a `stat -c %s` exec in the container), so both modes
    can refuse a file too large to even read with the same bytes."""
    return (f"ERROR: result is {size} bytes, over the {MAX_WRITE_BYTES}-byte "
            f"write limit; nothing was written")


def _append_oversized(encoded: bytes):
    """Spec §1.2 cap 1: append_file's own refusal for the `text` ARGUMENT, or
    None. Imported by dirtywork.sandbox.docker the way MAX_WRITE_BYTES already
    is, so both backends emit the byte-identical string.

    Deliberately NOT any of the three write-side strings, because an append's
    fix is a different action from a write's. The three, verbatim, are:
    docker._oversized's `ERROR: content is <n> bytes, over the <limit>-byte
    write limit` (no trailing advice); tools.write_file's own inline
    `ERROR: content is <n> bytes, over the <limit>-byte write limit; write the
    file in smaller pieces` (the `; write the file in smaller pieces` tail is
    host-only and exists nowhere in docker.py); and _result_too_big's
    `…; nothing was written`. None of them may ever surface from an append."""
    if len(encoded) > MAX_WRITE_BYTES:
        return (f"ERROR: text is {len(encoded)} bytes, over the {MAX_WRITE_BYTES}-byte "
                f"write limit; append in smaller pieces")
    return None


def _append_missing(path: str) -> str:
    """Spec §1.2: the does-not-exist refusal. Three call sites -- the host
    probe's ENOENT branch, docker's guard exec (rc 2) and docker's write exec
    (rc 2, which is how a delete BETWEEN the two execs still refuses
    correctly) -- so it is built here once."""
    return (f"ERROR: cannot append to '{path}': it does not exist; create it with "
            f"write_file first")


def _not_utf8(path: str, tool: str) -> str:
    """Spec §5.1: the ONE non-UTF-8 refusal. The host transform path has always
    worded it this way; from 0.10 docker's `_read_raw` renders it from here too
    (with the tool it was called for), so a binary file refuses identically in
    both modes and names the tool the model actually called."""
    return f"ERROR: {path} is not valid UTF-8 text; {tool} only works on text files"
```

Then replace `_check_write_size`'s body.

Before:

```python
    size = len(new_text.encode("utf-8"))
    if size > MAX_WRITE_BYTES:
        return (f"ERROR: result is {size} bytes, over the {MAX_WRITE_BYTES}-byte "
                f"write limit; nothing was written")
    return None
```

After:

```python
    size = len(new_text.encode("utf-8"))
    if size > MAX_WRITE_BYTES:
        return _result_too_big(size)
    return None
```

And route `_transform_file`'s UTF-8 refusal through the shared helper.

Before:

```python
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return f"ERROR: {path} is not valid UTF-8 text; {tool} only works on text files"
```

After:

```python
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _not_utf8(path, tool)
```

- [ ] **Step 7: Run the new tests and see them pass**

Run: `/usr/bin/python3 -m pytest tests/test_tools_files.py -q`
Expected: exit code 0.

- [ ] **Step 8: Run the full suite**

Run: `/usr/bin/python3 -m pytest -q`
Expected: exit code 0; `1044 passed, 1 skipped, 18 deselected` (1026 + 18).

- [ ] **Step 9: Commit**

```bash
git add dirtywork/tools.py tests/test_tools_files.py
git commit -m "feat(tools): _write_atomic and the shared write/append strings both backends will use"
```

---

**Task 1 amendment (executed 2026-08-23):** the post-review fix round (commit `4b3070d`) restructured `_write_atomic`'s probe-fd close — in the two in-place branches the close is the write's completion (`OSError` → the generic tail, never a raise) and the function-level `finally` close is defensive — and added a 19th test (`test_write_atomic_surfaces_a_close_failure_on_the_hardlink_path_too`). Task 1 therefore ends at `1045 passed`, and every full-suite gate below reads +1 relative to the original chain (final: 1156 — Task 4's fix round added two more). The `TMP_FIND_REGEX` comment now names the `-regextype posix-extended` requirement.

### Task 2: host `append_file` (spec §1.2, host half)

The tool is not registered yet (Task 5 does that, once both backends have it), so nothing in the run loop can reach it until then. `HostSandbox.append_file` exists from this task on so the two backends can be built and tested independently.

**Files:**
- Modify: `dirtywork/tools.py` (new `append_file` immediately after `apply_edits`, which ends at `:485`)
- Modify: `dirtywork/sandbox/host.py` (new `append_file` immediately after `insert_after` at `:67-70`)
- Modify: `tests/test_tools_files.py` (15 new tests)
- Modify: `tests/test_sandbox_host.py` (1 new test)

**Interfaces:**
- Consumes: `tools._append_oversized(encoded: bytes) -> str | None`, `tools._append_missing(path: str) -> str`, `tools._not_utf8(path: str, tool: str) -> str`, `tools._result_too_big(size: int) -> str`, `tools._write_atomic(target, data, *, path, verb, create_parents, must_exist)` (all Task 1 — `append_file` is the one caller that passes `must_exist=True`); `tools._open_regular`, `tools._worktree_candidate`, `tools.describe_change`, `guardrails.resolve_in_worktree`.
- Produces:
  - `tools.append_file(worktree: Path, path: str, text: str) -> str`
  - `HostSandbox.append_file(path: str, text: str) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools_files.py`:

```python
# --- spec §1.2: host append_file.


def test_append_file_appends_verbatim_with_no_separator(wt: Path):
    target = wt / "notes.md"
    target.write_text("one\n")
    out = tools.append_file(wt, "notes.md", "two\n")
    assert target.read_text() == "one\ntwo\n"
    assert out.startswith("Appended to notes.md: +1 -0")
    assert "+two" in out


def test_append_file_header_when_the_file_did_not_end_in_a_newline(wt: Path):
    # Spec §1.2: not including a leading newline REPLACES the final line, and
    # the header says so. No new counting rule -- this is describe_change.
    target = wt / "tail.txt"
    target.write_text("one")
    out = tools.append_file(wt, "tail.txt", "two\n")
    assert target.read_text() == "onetwo\n"
    assert out.startswith("Appended to tail.txt: +1 -1 (removed 1 non-blank line)")


def test_append_file_refuses_a_missing_target(wt: Path):
    out = tools.append_file(wt, "nope.md", "x")
    assert out == ("ERROR: cannot append to 'nope.md': it does not exist; create it "
                   "with write_file first")
    assert not (wt / "nope.md").exists()


def test_append_file_refuses_a_symlink(wt: Path):
    real = wt / "real3.txt"
    real.write_text("original\n")
    link = wt / "link3.txt"
    os.symlink(real, link)
    out = tools.append_file(wt, "link3.txt", "more\n")
    assert out == ("ERROR: 'link3.txt' is a symlink; writing through a symlink is not "
                   "allowed even when its target is inside the worktree")
    assert real.read_text() == "original\n"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs require a POSIX OS")
def test_append_file_refuses_a_fifo(wt: Path):
    fifo = wt / "pipe2"
    os.mkfifo(fifo)
    with _hang_guard():
        out = tools.append_file(wt, "pipe2", "x")
    assert out == "ERROR: 'pipe2' is not a regular file (refusing FIFO/device/socket)"


def test_append_file_refuses_an_oversized_text_argument_before_touching_the_file(wt: Path):
    # Cap 1, and it fires before the containment check, so a missing file with
    # an oversized text still reports the text problem.
    huge = "x" * (tools.MAX_WRITE_BYTES + 1)
    out = tools.append_file(wt, "nothing-here.md", huge)
    assert out == (f"ERROR: text is {tools.MAX_WRITE_BYTES + 1} bytes, over the "
                   f"{tools.MAX_WRITE_BYTES}-byte write limit; append in smaller pieces")


def test_append_file_refuses_when_the_result_would_be_over_the_write_limit(wt: Path):
    target = wt / "atlimit.txt"
    target.write_bytes(b"x" * tools.MAX_READ_BYTES)      # readable, but full
    out = tools.append_file(wt, "atlimit.txt", "y" * 100)
    assert out == (f"ERROR: result is {tools.MAX_READ_BYTES + 100} bytes, over the "
                   f"{tools.MAX_WRITE_BYTES}-byte write limit; nothing was written")
    assert target.stat().st_size == tools.MAX_READ_BYTES


def test_append_file_refuses_a_file_too_large_to_read_with_the_result_cap_wording(wt: Path):
    # Cap 2 (spec §1.2): an un-appendable file must NEVER surface read_file's
    # "refusing to read"/"read limit" wording.
    target = wt / "huge.txt"
    target.write_bytes(b"x" * (tools.MAX_READ_BYTES + 1))
    out = tools.append_file(wt, "huge.txt", "y")
    assert out == (f"ERROR: result is {tools.MAX_READ_BYTES + 2} bytes, over the "
                   f"{tools.MAX_WRITE_BYTES}-byte write limit; nothing was written")
    assert "read limit" not in out and "refusing to read" not in out


def test_append_file_refuses_non_utf8_with_the_tool_named(wt: Path):
    target = wt / "bin.dat"
    target.write_bytes(b"\xff\xfe binary")
    out = tools.append_file(wt, "bin.dat", "text")
    assert out == ("ERROR: bin.dat is not valid UTF-8 text; append_file only works "
                   "on text files")
    assert target.read_bytes() == b"\xff\xfe binary"


def test_append_file_refuses_a_path_outside_the_worktree(wt: Path):
    out = tools.append_file(wt, "../../etc/hosts", "x")
    assert out.startswith("ERROR:") and "outside the worktree" in out


def test_append_file_refuses_writing_inside_dot_git(wt: Path):
    (wt / ".git").mkdir()
    (wt / ".git" / "config").write_text("[core]\n")
    out = tools.append_file(wt, ".git/config", "\tfoo = bar\n")
    assert out.startswith("ERROR:") and ".git/" in out
    assert (wt / ".git" / "config").read_text() == "[core]\n"


def test_append_file_never_creates_parent_directories(wt: Path):
    out = tools.append_file(wt, "brand/new/f.txt", "x")
    assert out == ("ERROR: cannot append to 'brand/new/f.txt': it does not exist; "
                   "create it with write_file first")
    assert not (wt / "brand").exists()


def test_append_file_preserves_mode_and_leaves_no_temp(wt: Path):
    target = wt / "run.sh"
    target.write_text("#!/bin/sh\n")
    target.chmod(0o755)
    assert tools.append_file(wt, "run.sh", "echo hi\n").startswith("Appended to run.sh:")
    assert target.read_text() == "#!/bin/sh\necho hi\n"
    assert _stat.S_IMODE(target.stat().st_mode) == 0o755
    assert _temp_leftovers(wt) == []


def test_append_file_is_atomic_the_target_is_unchanged_when_the_write_fails(wt: Path, monkeypatch):
    target = wt / "safe.txt"
    target.write_text("one\n")

    def _boom(fd, data):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(tools, "_write_all", _boom)
    out = tools.append_file(wt, "safe.txt", "two\n")
    assert out.startswith("ERROR: cannot append to 'safe.txt': ")
    assert target.read_text() == "one\n"
    assert _temp_leftovers(wt) == []


def test_append_file_refuses_when_the_target_vanishes_before_the_promote(wt: Path, monkeypatch):
    # Spec §1.2: ENOENT on the write probe is the does-not-exist refusal,
    # never §2.2's new-file branch -- docker's append write script refuses the
    # same race with `[ -f "$1" ] || exit 2`. `must_exist=True` is what carries
    # that rule into the shared primitive, and this pins BOTH halves: that
    # append_file wires the flag through, and that the primitive honours it.
    target = wt / "vanishing.md"
    target.write_text("one\n")
    real_write_atomic = tools._write_atomic
    seen = {}

    def _spy(t, data, **kwargs):
        seen.update(kwargs)
        os.unlink(str(t))            # the race: gone before the probe runs
        return real_write_atomic(t, data, **kwargs)

    monkeypatch.setattr(tools, "_write_atomic", _spy)
    out = tools.append_file(wt, "vanishing.md", "two\n")
    assert seen["must_exist"] is True
    assert out == ("ERROR: cannot append to 'vanishing.md': it does not exist; create "
                   "it with write_file first")
    assert not target.exists()       # never re-created by the new-file branch
    assert _temp_leftovers(wt) == []
```

Append to `tests/test_sandbox_host.py`:

```python
def test_host_sandbox_append_file(wt: Path):
    sb = HostSandbox(wt)
    sb.start(wt, wt, "slug", "deadbeef")
    assert sb.append_file("hello.txt", "there\n").startswith("Appended to hello.txt:")
    assert (wt / "hello.txt").read_text() == "hi\nthere\n"
    assert sb.append_file("gone.txt", "x") == (
        "ERROR: cannot append to 'gone.txt': it does not exist; create it with "
        "write_file first")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_tools_files.py tests/test_sandbox_host.py -q -k "append_file"`
Expected: 16 failed — the 15 in `tests/test_tools_files.py` with `AttributeError: module 'dirtywork.tools' has no attribute 'append_file'` (including `test_append_file_refuses_when_the_target_vanishes_before_the_promote`: `tools._write_atomic` exists from Task 1, so its `monkeypatch.setattr` succeeds and the `AttributeError` comes from the `tools.append_file(...)` call on the next line), and `test_host_sandbox_append_file` with `AttributeError: 'HostSandbox' object has no attribute 'append_file'`.

- [ ] **Step 3: Add `tools.append_file`**

In `dirtywork/tools.py`, immediately **after** `apply_edits` (which ends with `tool="apply_edits")`) and immediately **before** `def list_dir(`.

```python
def append_file(worktree: Path, path: str, text: str) -> str:
    """Spec §1.2: append `text` VERBATIM to the end of an existing regular
    file. Nothing is inserted between the old bytes and the new ones -- the
    model owns line discipline, and APPEND_FILE_SPEC's description says so.

    Three caps fire in a fixed order, mirrored exactly by
    DockerSandbox.append_file, so neither backend can surface the other's
    wording: (1) the `text` ARGUMENT (_append_oversized), before the file is
    touched at all; (2) the current file's size against MAX_READ_BYTES; (3)
    the RESULT size. Caps 2 and 3 share ONE sentence (_result_too_big), so a
    file too large to read reads as un-appendable rather than surfacing
    read_file's "read limit" wording.

    The §2.2 probe runs UNCHANGED, so the symlink and FIFO refusals are
    byte-identical to write_file's -- except ENOENT, which for an append is
    the does-not-exist refusal, never §2.2's new-file branch. That holds for
    BOTH probes: this function's own, and _write_atomic's, which is told so
    with must_exist=True. The content is
    then read through a SECOND open of the same candidate path: O_NONBLOCK
    (which _open_regular always adds) is what keeps a FIFO swapped in between
    the two opens from blocking the read, and the read fd's st_ino/st_dev must
    match the probe's or the append refuses rather than pasting one file's
    bytes onto another inode. append_file NEVER creates parent directories."""
    encoded = text.encode("utf-8")
    too_big = _append_oversized(encoded)
    if too_big:
        return too_big
    try:
        resolve_in_worktree(path, worktree, writing=True)  # containment check only
    except GuardrailError as e:
        return f"ERROR: {e}"
    p = _worktree_candidate(path, worktree)
    try:
        probe_fd = os.open(str(p), os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as e:
        if e.errno == errno.ENOENT:
            return _append_missing(path)
        if e.errno == errno.ELOOP:
            return (f"ERROR: '{path}' is a symlink; writing through a symlink is not "
                    f"allowed even when its target is inside the worktree")
        if e.errno == errno.ENXIO:
            return f"ERROR: '{path}' is not a regular file (refusing FIFO/device/socket)"
        return f"ERROR: cannot append to '{path}': {e}"
    try:
        probe_st = os.fstat(probe_fd)
        if not stat.S_ISREG(probe_st.st_mode):
            return (f"ERROR: cannot append to '{path}': '{p}' is not a regular file "
                    f"(refusing FIFO/device/socket)")
        if probe_st.st_size > MAX_READ_BYTES or probe_st.st_size + len(encoded) > MAX_WRITE_BYTES:
            # Caps 2 and 3, decided before the read so a 6 MB file is never
            # loaded just to be refused. MAX_READ_BYTES == MAX_WRITE_BYTES
            # today, so the first test already implies the second; both are
            # written out so the rule survives the two constants diverging.
            return _result_too_big(probe_st.st_size + len(encoded))
        try:
            fh = _open_regular(p, os.O_RDONLY, max_size=MAX_READ_BYTES)
        except OSError as e:
            if e.errno is None:
                # _open_regular's two errno-less refusals -- the read cap and
                # the non-regular-file check -- which the probe fd disproved a
                # moment ago. Reaching here means the target was replaced
                # between the two opens; refuse with the append's own wording,
                # never read_file's (spec §1.2).
                return (f"ERROR: cannot append to '{path}': the file changed between "
                        f"opening it and reading it")
            return f"ERROR: cannot append to '{path}': {e}"
        try:
            read_st = os.fstat(fh.fileno())
            if (read_st.st_ino, read_st.st_dev) != (probe_st.st_ino, probe_st.st_dev):
                return (f"ERROR: cannot append to '{path}': the file changed between "
                        f"opening it and reading it")
            raw = fh.read()
        except OSError as e:
            return f"ERROR: cannot append to '{path}': {e}"
        finally:
            fh.close()
        try:
            old_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return _not_utf8(path, "append_file")
        if len(raw) + len(encoded) > MAX_WRITE_BYTES:
            # Re-checked against what was actually read: the probe's size is a
            # moment old, and the file may have grown in place since.
            return _result_too_big(len(raw) + len(encoded))
        # must_exist=True: spec §1.2 forbids §2.2's new-file branch here, so a
        # target deleted between the read and this probe refuses rather than
        # being re-created -- the same race docker's append write script
        # refuses with `[ -f "$1" ] || exit 2`.
        err = _write_atomic(p, raw + encoded, path=path, verb="append",
                            must_exist=True)
        if err:
            return err
        return describe_change(path, old_text, old_text + text, verb="Appended to")
    finally:
        os.close(probe_fd)
```

- [ ] **Step 4: Add `HostSandbox.append_file`**

In `dirtywork/sandbox/host.py`, immediately **after** `insert_after` and immediately **before** `def list_dir(`.

```python
    def append_file(self, path: str, text: str) -> str:
        result = tools.append_file(self.worktree, path, text)
        self._check_budget()
        return result
```

- [ ] **Step 5: Run the new tests and see them pass**

Run: `/usr/bin/python3 -m pytest tests/test_tools_files.py tests/test_sandbox_host.py -q`
Expected: exit code 0.

- [ ] **Step 6: Run the full suite**

Run: `/usr/bin/python3 -m pytest -q`
Expected: exit code 0; `1061 passed, 1 skipped, 18 deselected` (1045 + 16).

- [ ] **Step 7: Commit**

```bash
git add dirtywork/tools.py dirtywork/sandbox/host.py tests/test_tools_files.py tests/test_sandbox_host.py
git commit -m "feat(tools): host append_file — verbatim append with the three ordered caps"
```

---

### Task 3: docker's atomic write script and the tool-aware UTF-8 refusal (spec §2.6 `_write_raw`, §5.1)

Two changes that share one file and one test module, done together because §2.6's counted test churn (the `'cat > "$1"'` matcher) touches the same eleven assertions §5.1's new tests sit beside.

**The counted churn, resolved:** the spec says twelve matchers, "ten substring matchers in that file, one in `tests/test_docker_runs.py`". On this tree the real count is **eleven**, all in `tests/test_docker_sandbox.py`: one exact-argv assertion at `:377-382` and ten substring matchers at `:393`, `:402`, `:412`, `:440`, `:467`, `:1412`, `:1423`, `:1433`, `:1446`, `:1482`. `tests/test_docker_runs.py:153` also contains the text `cat > ` but it is the **test's own** heredoc inside `_write_files_into_volume`, which seeds a docker volume — not a matcher for the production script — so it must NOT be touched. Verify both facts in Step 1 before editing.

**Files:**
- Modify: `dirtywork/sandbox/docker.py` (the `..tools` import block `:15-29`; new `_PROMOTE`/`WRITE_SCRIPT` constants after `_oversized` at `:83`; `_read_raw` `:392-417`; `_write_raw` `:425-447`; `write_file` `:449-459`; `_transform_file` `:461-484`; `edit_file`/`apply_edits`/`insert_before`/`insert_after` `:486-496`)
- Modify: `tests/test_docker_sandbox.py` (new `_is_write_exec` helper + `import re` + the `docker_mod` import; eleven assertions rewritten; 3 new tests)

**Interfaces:**
- Consumes: `tools.tmp_name(basename: str) -> str` and `tools._not_utf8(path: str, tool: str) -> str` (Task 1); `docker_args.exec_argv(name, argv, *, workdir="/work", stdin=False, env=None) -> list`.
- Produces:
  - `docker._sibling_tmp(rel: str) -> str`
  - `docker._PROMOTE: str` — the shared `chmod`+`mv -fT`+`rm -f` tail of both container write scripts.
  - `docker.WRITE_SCRIPT: str` — spec §2.6's `_write_raw` script, verbatim.
  - `DockerSandbox._read_raw(path: str, *, strict: bool = False, tool: str | None = None) -> tuple` — unchanged `(text, None)` / `(None, error)` shape.
  - `DockerSandbox._transform_file(path: str, transform, *, tool: str) -> str`
  - `tests.test_docker_sandbox._is_write_exec(call) -> bool`

- [ ] **Step 1: Prove the matcher inventory before touching it**

The eleven live in **two different byte shapes**, and one grep cannot see both. Nine of the ten substring matchers plus the tenth are Python source reading `"cat > \"$1\"" in " ".join(c[0])`, so the bytes ON DISK are `cat > \"$1\"` — backslash, quote. Only the exact-argv literal (the `'mkdir -p …'` single-quoted string) carries unescaped quotes. So there are three gates, not one:

```bash
grep -c 'cat > \\"\$1' tests/test_docker_sandbox.py
grep -c 'cat > "\$1"' tests/test_docker_sandbox.py
grep -rn 'cat > ' tests/test_docker_runs.py
```

Expected, exactly (all three run on this tree while this plan was written):

```
10
1
tests/test_docker_runs.py:153:        lines.append(f"cat > {dest} <<'DIRTYWORK_TEST_EOF'\n{content}DIRTYWORK_TEST_EOF")
```

`10` is the escaped substring matchers (`:393`, `:402`, `:412`, `:440`, `:467`, `:1412`, `:1423`, `:1433`, `:1446`, `:1482`), `1` is the exact-argv literal inside `test_write_file_sends_content_on_stdin` (`:380`), and the single `tests/test_docker_runs.py` line is the test's own volume-seeding heredoc, which must NOT be touched. If either number differs, stop and re-count before editing — Step 8's ten-substring rewrite and Step 9's single-literal rewrite both assume them. (Note the first pattern is deliberately unterminated after `$1`: the trailing `\"` would have to be escaped again and adds nothing.)

- [ ] **Step 2: Write the failing tests**

In `tests/test_docker_sandbox.py`, add `import re` to the import block.

Before:

```python
import subprocess
from pathlib import Path
```

After:

```python
import re
import subprocess
from pathlib import Path
```

Then add the `docker_mod` import.

Before:

```python
from dirtywork.sandbox.docker import DockerSandbox, docker_cli
```

After:

```python
from dirtywork.sandbox import docker as docker_mod
from dirtywork.sandbox.docker import DockerSandbox, docker_cli
```

Then add the shared helper immediately after `_SAMPLE_ARGV` (`:20-21`):

```python
def _is_write_exec(call) -> bool:
    """True for a recorded docker call that ran DockerSandbox's write script.
    ONE place, so the next change to that script text touches one line instead
    of ten (spec §2.6's counted churn). `call` is FakeDocker's
    (argv, timeout, stdin) triple; the script is a single argv ELEMENT, so
    membership -- not a substring search over a joined string -- is the exact
    test."""
    return docker_mod.WRITE_SCRIPT in call[0]
```

Append these three tests at the end of `tests/test_docker_sandbox.py`:

```python
# --- spec §2.6/§5.1: the atomic container write script and the tool-aware
# --- UTF-8 refusal.


def test_write_exec_uses_the_atomic_script_and_a_sibling_temp(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"],
                _fail(b"head: cannot open 'deep/new/file.txt'"))
    sb.write_file("deep/new/file.txt", "hello")
    argv, timeout, stdin = fake.calls[-1]
    assert argv[:8] == ["exec", "-w", "/work", "-i", "dw-abc123",
                        "/bin/sh", "-c", docker_mod.WRITE_SCRIPT]
    assert argv[8] == "_"
    assert argv[9] == "deep/new/file.txt"
    # The temp is a SIBLING of the target (same directory => same filesystem =>
    # `mv` is an atomic rename) and its name is generated HOST-side, so worker
    # bytes never reach the script text.
    assert re.fullmatch(r"deep/new/\.dw-tmp\.file\.txt\.[0-9a-f]{8}", argv[10])
    assert len(argv) == 11
    assert stdin == b"hello"
    # Spec §2.6: `&&`-chained, never move INTO a directory, guards echo their
    # own diagnostic, the temp is removed on any failure.
    assert 'mv -fT -- "$2" "$1"' in docker_mod.WRITE_SCRIPT
    assert 'chmod --reference="$1" "$2" 2>/dev/null || chmod 644 "$2"' in docker_mod.WRITE_SCRIPT
    assert '{ rm -f -- "$2"; exit 1; }' in docker_mod.WRITE_SCRIPT


def test_transform_non_utf8_refusals_name_the_tool_and_match_the_host(started, tmp_path):
    """Spec §5.1: docker's wording becomes the host's, and names the tool the
    model actually called -- never the legacy `refusing to edit`."""
    from dirtywork import tools
    sb, fake, run_dir = started
    wt = tmp_path / "utf8parity"
    wt.mkdir()
    (wt / "bin.dat").write_bytes(b"\xff\xfe old")
    cases = [
        ("edit_file", lambda: sb.edit_file("bin.dat", "old", "new"),
         lambda: tools.edit_file(wt, "bin.dat", "old", "new")),
        ("insert_before", lambda: sb.insert_before("bin.dat", "old", "x"),
         lambda: tools.insert_before(wt, "bin.dat", "old", "x")),
        ("insert_after", lambda: sb.insert_after("bin.dat", "old", "x"),
         lambda: tools.insert_after(wt, "bin.dat", "old", "x")),
        ("apply_edits", lambda: sb.apply_edits("bin.dat", [{"old": "old", "new": "new"}]),
         lambda: tools.apply_edits(wt, "bin.dat", [{"old": "old", "new": "new"}])),
    ]
    for tool, docker_call, host_call in cases:
        fake.script(["exec"], _ok(b"\xff\xfe old"))
        docker_out = docker_call()
        assert docker_out == (f"ERROR: bin.dat is not valid UTF-8 text; {tool} only "
                              f"works on text files")
        assert docker_out == host_call()
        assert "refusing to edit" not in docker_out
    assert not [c for c in fake.calls if _is_write_exec(c)]


def test_read_raw_without_a_tool_keeps_the_legacy_wording(started):
    # No shipped caller reaches this branch since 0.10 (every strict read names
    # its tool). It stays so a direct caller of this private method gets a
    # coherent refusal instead of "None only works on text files".
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b"\xff\xfe"))
    text, err = sb._read_raw("bin.dat", strict=True)
    assert text is None
    assert err == "ERROR: 'bin.dat' is not valid UTF-8; refusing to edit"
```

- [ ] **Step 3: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_docker_sandbox.py -q -k "atomic_script or non_utf8_refusals or without_a_tool"`
Expected: **3 selected, 2 failed, 1 passed**, and each for its own reason:

- `test_write_exec_uses_the_atomic_script_and_a_sibling_temp` — `AttributeError: module 'dirtywork.sandbox.docker' has no attribute 'WRITE_SCRIPT'`, from the `argv[:8] == [… docker_mod.WRITE_SCRIPT]` assertion.
- `test_transform_non_utf8_refusals_name_the_tool_and_match_the_host` — **`AssertionError`, not an `AttributeError`**: the first case's wording comparison fires long before the `_is_write_exec` line at the end, so the failure reads `assert "ERROR: 'bin.dat' is not valid UTF-8; refusing to edit" == "ERROR: bin.dat is not valid UTF-8 text; edit_file only works on text files"`. That legacy string is exactly what Step 5 replaces.
- `test_read_raw_without_a_tool_keeps_the_legacy_wording` — PASSES. It is a pin on the `tool=None` branch Step 5 must NOT change; a failure here means Step 5 went too far.

- [ ] **Step 4: Import the shared helpers into docker.py and add the scripts**

In `dirtywork/sandbox/docker.py`.

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
    grep_timeout_result,
    timeout_result,
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
    _append_missing,
    _append_oversized,
    _cap,
    _check_write_size,
    _insert_once,
    _not_utf8,
    _number_lines,
    _replace_once,
    _result_too_big,
    describe_change,
    describe_write,
    grep_timeout_result,
    timeout_result,
    tmp_name,
)
```

(`_append_missing`, `_append_oversized`, `_result_too_big` and `describe_change` are used by Task 4; importing them now keeps this sorted block edited once instead of twice. `dirtywork/sandbox/export.py` imports `TMP_FIND_REGEX` from `..tools` for itself in Task 6. Execution amendment, 2026-08-23: Task 6's fix round 1 removed that import from `export.py` — the sweep it supported moved to the WORKER container, so `dirtywork/sandbox/docker.py` imports `TMP_FIND_REGEX` from `..tools` instead; see the Task 6 amendment note.)

Then add the scripts immediately **after** `_oversized` (which ends with `return None`) and immediately **before** `class DockerSandbox:`.

```python
# Spec §2.6. The promote tail is shared by both write scripts so a change to
# how a staged file becomes the real file happens in ONE place:
# `chmod --reference` copies the target's mode (today's `cat >` wrote through
# the inode and preserved it for free -- the temp+`mv` shape is what creates
# the need); `chmod 644` is the new-file fallback; `mv -fT` never moves INTO a
# directory; `rm -f` on any failure is harmless when the temp never existed.
# GNU coreutils (`--reference`, `-T`) ship in the bookworm worker image.
_PROMOTE = ('{ chmod --reference="$1" "$2" 2>/dev/null || chmod 644 "$2"; } && '
            'mv -fT -- "$2" "$1" || { rm -f -- "$2"; exit 1; }')

# `$1` is the target relpath, `$2` the host-generated temp relpath; worker DATA
# arrives on stdin and is never inside the script text. `&&`-chained so a
# failed `cat` can never promote. The writability guard keeps host parity:
# today an unwritable file refuses EACCES, and without it temp+`mv` would
# silently overwrite a 0444 file, since rename needs only directory write.
# Each guard echoes its own diagnostic so _write_raw's stderr wrap never
# renders empty.
WRITE_SCRIPT = (
    'mkdir -p "$(dirname -- "$1")" && '
    '{ [ ! -d "$1" ] || { echo "cannot write $1: Is a directory" >&2; exit 1; }; } && '
    '{ [ -w "$1" ] || [ ! -e "$1" ] || { echo "cannot write $1: Permission denied" >&2; exit 1; }; } && '
    'cat > "$2" && ' + _PROMOTE
)


def _sibling_tmp(rel: str) -> str:
    """The staging path for an in-container write to `rel`: the same directory
    (so `mv` is an atomic same-filesystem rename) with tools.tmp_name()'s
    generated basename."""
    return posixpath.join(posixpath.dirname(rel), tmp_name(posixpath.basename(rel)))
```

- [ ] **Step 5: Teach `_read_raw` the tool name**

In `dirtywork/sandbox/docker.py`.

Before:

```python
    def _read_raw(self, path: str, *, strict: bool = False):
        rel, err = _rel(path)
```

After:

```python
    def _read_raw(self, path: str, *, strict: bool = False, tool: str | None = None):
        """Spec §5.1: `tool` names the tool whose call this read serves, so a
        non-UTF-8 file refuses with the HOST's wording (`<path> is not valid
        UTF-8 text; <tool> only works on text files`) instead of the legacy
        `refusing to edit`, which was wrong for every insert/apply/append. It
        is only consulted on a `strict` read."""
        rel, err = _rel(path)
```

Before:

```python
        if strict:
            try:
                text = captured.output.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                return None, (
                    f"ERROR: '{path}' is not valid UTF-8; refusing to edit"
                )
            return text, None
```

After:

```python
        if strict:
            try:
                text = captured.output.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                if tool is not None:
                    return None, _not_utf8(path, tool)
                # No shipped caller reaches this since 0.10 -- every strict
                # read names its tool. Kept so a direct caller of this private
                # method gets a coherent refusal rather than a formatted None.
                return None, (
                    f"ERROR: '{path}' is not valid UTF-8; refusing to edit"
                )
            return text, None
```

- [ ] **Step 6: Move `_write_raw` onto the atomic script**

In `dirtywork/sandbox/docker.py`.

Before:

```python
        argv = docker_args.exec_argv(
            self.container,
            ["/bin/sh", "-c", 'mkdir -p "$(dirname -- "$1")" && cat > "$1"', "_", rel],
            stdin=True,
        )
```

After:

```python
        argv = docker_args.exec_argv(
            self.container,
            ["/bin/sh", "-c", WRITE_SCRIPT, "_", rel, _sibling_tmp(rel)],
            stdin=True,
        )
```

- [ ] **Step 7: Pass the tool name from every transform caller**

In `dirtywork/sandbox/docker.py`.

Before:

```python
        old_text, _unused = self._read_raw(path, strict=True)
```

After:

```python
        old_text, _unused = self._read_raw(path, strict=True, tool="write_file")
```

Before:

```python
    def _transform_file(self, path: str, transform) -> str:
        """Read → transform → write inside the container: the same shape as
        tools._transform_file, over the same transforms, so edit_file,
        insert_before and insert_after are three transforms over ONE path per
        backend (spec §3.2) and the two backends can never disagree about an
        anchor rule or an error string. The UTF-8 refusal comes from
        _read_raw(strict=True), which is why no `tool` name is needed here."""
        text, err = self._read_raw(path, strict=True)
```

After:

```python
    def _transform_file(self, path: str, transform, *, tool: str) -> str:
        """Read → transform → write inside the container: the same shape as
        tools._transform_file, over the same transforms, so edit_file,
        insert_before and insert_after are three transforms over ONE path per
        backend (spec §3.2) and the two backends can never disagree about an
        anchor rule or an error string. `tool` is forwarded to _read_raw so a
        non-UTF-8 file refuses with the host's wording, naming the tool the
        model called (spec §5.1)."""
        text, err = self._read_raw(path, strict=True, tool=tool)
```

Before:

```python
    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        return self._transform_file(path, _replace_once(path, old_string, new_string))

    def apply_edits(self, path: str, edits: list) -> str:
        return self._transform_file(path, _apply_edits_once(path, edits))

    def insert_before(self, path: str, anchor: str, text: str) -> str:
        return self._transform_file(path, _insert_once(path, anchor, text, "before"))

    def insert_after(self, path: str, anchor: str, text: str) -> str:
        return self._transform_file(path, _insert_once(path, anchor, text, "after"))
```

After:

```python
    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        return self._transform_file(path, _replace_once(path, old_string, new_string),
                                    tool="edit_file")

    def apply_edits(self, path: str, edits: list) -> str:
        return self._transform_file(path, _apply_edits_once(path, edits),
                                    tool="apply_edits")

    def insert_before(self, path: str, anchor: str, text: str) -> str:
        return self._transform_file(path, _insert_once(path, anchor, text, "before"),
                                    tool="insert_before")

    def insert_after(self, path: str, anchor: str, text: str) -> str:
        return self._transform_file(path, _insert_once(path, anchor, text, "after"),
                                    tool="insert_after")
```

- [ ] **Step 8: Rewrite the ten substring matchers onto the shared helper**

In `tests/test_docker_sandbox.py`, replace **every** occurrence of the two shapes below. There are ten in total, at lines `:393`, `:402`, `:412`, `:440`, `:467`, `:1412`, `:1423`, `:1433`, `:1446`, `:1482` before this edit.

Replace each

```python
    writes = [c for c in fake.calls if "cat > \"$1\"" in " ".join(c[0])]
```

with

```python
    writes = [c for c in fake.calls if _is_write_exec(c)]
```

and each

```python
    assert not [c for c in fake.calls if "cat > \"$1\"" in " ".join(c[0])]
```

with

```python
    assert not [c for c in fake.calls if _is_write_exec(c)]
```

(`:1423`, `:1446` and `:1482` are the `assert not` shape; the other seven are the `writes = ` shape.)

- [ ] **Step 9: Rewrite the exact-argv assertion**

In `tests/test_docker_sandbox.py`, inside `test_write_file_sends_content_on_stdin`.

Before:

```python
    argv, timeout, stdin = fake.calls[-1]
    assert argv == [
        "exec", "-w", "/work", "-i", "dw-abc123",
        "/bin/sh", "-c", 'mkdir -p "$(dirname -- "$1")" && cat > "$1"',
        "_", "deep/new/file.txt",
    ]
    assert stdin == b"hello"
```

After:

```python
    argv, timeout, stdin = fake.calls[-1]
    # The temp basename is random per call (spec §2.2: the worker controls
    # sibling names), so the fixed prefix is asserted exactly and the temp by
    # shape -- test_write_exec_uses_the_atomic_script_and_a_sibling_temp pins
    # the rest.
    assert argv[:10] == [
        "exec", "-w", "/work", "-i", "dw-abc123",
        "/bin/sh", "-c", docker_mod.WRITE_SCRIPT,
        "_", "deep/new/file.txt",
    ]
    assert re.fullmatch(r"deep/new/\.dw-tmp\.file\.txt\.[0-9a-f]{8}", argv[10])
    assert len(argv) == 11
    assert stdin == b"hello"
```

- [ ] **Step 10: Prove the old matcher is gone — in BOTH byte shapes**

A `grep 'cat > "\$1"'` alone cannot prove this: the ten substring matchers were never spelled that way on disk (Step 1), so that pattern would come back clean even if Step 8 had been skipped entirely. Run the broad grep, which sees every shape, plus the escaped-form count:

```bash
grep -rn 'cat > ' tests/ dirtywork/
grep -c 'cat > \\"\$1' tests/test_docker_sandbox.py
```

Expected — exactly **two** lines from the first command and **`0`** from the second (`grep -c` exits 1 when it counts nothing; that is the pass, not a failure):

```
tests/test_docker_runs.py:153:        lines.append(f"cat > {dest} <<'DIRTYWORK_TEST_EOF'\n{content}DIRTYWORK_TEST_EOF")
dirtywork/sandbox/docker.py:<line>:    'cat > "$2" && ' + _PROMOTE
0
```

Those are the only two survivors this task may leave: the test's own volume-seeding heredoc in `tests/test_docker_runs.py`, untouched by design, and `WRITE_SCRIPT`'s own `cat > "$2"` from Step 4 (its line number is wherever the block landed after `_oversized`). `tests/test_docker_sandbox.py` must contribute **nothing** — that is what proves all eleven assertions were rewritten. Anything else, especially a `cat > "$1"` in `dirtywork/sandbox/docker.py`, means Step 6 or Steps 8–9 were left half-done. (Task 4's `APPEND_WRITE_SCRIPT` uses `cat >> "$2"` and never matches this pattern, so this gate reads the same after Task 4.)

- [ ] **Step 11: Run the module and see it pass**

Run: `/usr/bin/python3 -m pytest tests/test_docker_sandbox.py -q`
Expected: exit code 0.

- [ ] **Step 12: Run the full suite**

Run: `/usr/bin/python3 -m pytest -q`
Expected: exit code 0; `1064 passed, 1 skipped, 18 deselected` (1061 + 3).

- [ ] **Step 13: Commit**

```bash
git add dirtywork/sandbox/docker.py tests/test_docker_sandbox.py
git commit -m "feat(docker): atomic temp+mv write script and the host's non-UTF-8 wording, tool-aware"
```

---

### Task 4: docker `append_file` — three execs, the guard first (spec §1.2 docker half, §2.6 append script)

**A stated divergence:** the spec spells the non-regular-file refusal differently per backend — the host's probe produces `ERROR: cannot append to '<path>': '<abs>' is not a regular file (refusing FIFO/device/socket)` (the §2.2 probe "runs unchanged"), while docker's guard exec rc 3 produces `ERROR: cannot append to '<path>': not a regular file`. Both are implemented exactly as written; parity is asserted where the spec asks for it — the three caps, the does-not-exist string and the non-UTF-8 string — and the non-regular wording is deliberately **not** unified, in the same class as the pre-existing `_read_raw` size wording that spec §1.8 of the 0.9 design also declined to unify.

**Files:**
- Modify: `dirtywork/sandbox/docker.py` (`APPEND_GUARD_SCRIPT`/`APPEND_WRITE_SCRIPT` beside `WRITE_SCRIPT`; new `_append_guard`, `_append_write`, `append_file` immediately after `write_file`, which ends at `:459`)
- Modify: `tests/docker_fakes.py` (new `_rc` helper after `_fail` at `:125-126`)
- Modify: `tests/test_docker_sandbox.py` (import `_rc`, add `_is_append_write_exec`, 12 new tests)

**Interfaces:**
- Consumes: `docker._PROMOTE`, `docker._sibling_tmp(rel)`, `docker.WRITE_SCRIPT` (Task 3); `tools._append_oversized`, `tools._append_missing`, `tools._result_too_big`, `tools._not_utf8`, `tools.describe_change`, `tools.MAX_READ_BYTES`, `tools.MAX_WRITE_BYTES`; `DockerSandbox._read_raw(path, *, strict, tool)` (Task 3); `docker._rel(path, *, writing)`.
- Produces:
  - `docker.APPEND_GUARD_SCRIPT: str`, `docker.APPEND_WRITE_SCRIPT: str`
  - `DockerSandbox._append_guard(path: str, rel: str, text_len: int) -> tuple` — `(size: int, None)` or `(None, error: str)`
  - `DockerSandbox._append_write(path: str, rel: str, encoded: bytes) -> str` — `""` on success, an `ERROR: …` string otherwise
  - `DockerSandbox.append_file(path: str, text: str) -> str`
  - `tests.docker_fakes._rc(code: int, output: bytes = b"") -> Captured`
  - `tests.test_docker_sandbox._is_append_write_exec(call) -> bool`

- [ ] **Step 1: Add the arbitrary-exit-code fake**

In `tests/docker_fakes.py`, immediately after `_fail`.

```python
def _rc(code: int, output: bytes = b"") -> Captured:
    """A Captured with an arbitrary exit code. `_ok`/`_fail` only cover 0 and
    1; DockerSandbox's append guard exec signals "does not exist" with 2 and
    "not a regular file" with 3 (spec §1.2)."""
    return Captured(returncode=code, output=output, truncated=False, timed_out=False)
```

- [ ] **Step 2: Write the failing tests**

In `tests/test_docker_sandbox.py`, extend the fakes import.

Before:

```python
from tests.docker_fakes import FakeDocker, FakePopen, _fail, _ok
```

After:

```python
from tests.docker_fakes import FakeDocker, FakePopen, _fail, _ok, _rc
```

Add, immediately after `_is_write_exec`:

```python
def _is_append_write_exec(call) -> bool:
    """True for a recorded docker call that ran the append write script."""
    return docker_mod.APPEND_WRITE_SCRIPT in call[0]


def _script_append_guard(fake, response) -> None:
    """Script the append guard exec (the FIRST of append_file's three)."""
    fake.script(["exec", "-w", "/work", "dw-abc123", "/bin/sh", "-c",
                 docker_mod.APPEND_GUARD_SCRIPT], response)
```

Append these twelve tests at the end of `tests/test_docker_sandbox.py`:

```python
# --- spec §1.2 (docker half) / §2.6: append_file in the container.


def test_append_file_takes_three_execs_guard_read_write(started):
    sb, fake, run_dir = started
    _script_append_guard(fake, _ok(b"4\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"], _ok(b"one\n"))
    fake.script(["exec", "-w", "/work", "-i", "dw-abc123", "/bin/sh", "-c",
                 docker_mod.APPEND_WRITE_SCRIPT], _ok())
    out = sb.append_file("notes.md", "two\n")
    assert out.startswith("Appended to notes.md: +1 -0")
    guards = [c for c in fake.calls if docker_mod.APPEND_GUARD_SCRIPT in c[0]]
    heads = [c for c in fake.calls if "/usr/bin/head" in c[0]]
    writes = [c for c in fake.calls if _is_append_write_exec(c)]
    assert len(guards) == 1 and len(heads) == 1 and len(writes) == 1
    assert fake.calls.index(guards[0]) < fake.calls.index(heads[0]) < fake.calls.index(writes[0])
    assert writes[0][2] == b"two\n"          # only the NEW bytes go on stdin
    assert not [c for c in fake.calls if _is_write_exec(c)]   # never write_file's script


def test_append_file_write_script_shape(started):
    sb, fake, run_dir = started
    _script_append_guard(fake, _ok(b"4\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"], _ok(b"one\n"))
    fake.script(["exec", "-w", "/work", "-i", "dw-abc123", "/bin/sh", "-c",
                 docker_mod.APPEND_WRITE_SCRIPT], _ok())
    sb.append_file("deep/notes.md", "two\n")
    argv = [c for c in fake.calls if _is_append_write_exec(c)][0][0]
    assert argv[:8] == ["exec", "-w", "/work", "-i", "dw-abc123",
                        "/bin/sh", "-c", docker_mod.APPEND_WRITE_SCRIPT]
    assert argv[8] == "_" and argv[9] == "deep/notes.md"
    assert re.fullmatch(r"deep/\.dw-tmp\.notes\.md\.[0-9a-f]{8}", argv[10])
    assert len(argv) == 11
    # Spec §2.6: the missing-target guard is re-checked at write time, the copy
    # is made before the append, and the promote tail is shared with WRITE_SCRIPT.
    assert docker_mod.APPEND_WRITE_SCRIPT.startswith('[ -f "$1" ] || exit 2; ')
    assert 'cp -- "$1" "$2" && cat >> "$2"' in docker_mod.APPEND_WRITE_SCRIPT
    assert docker_mod.APPEND_WRITE_SCRIPT.endswith(docker_mod._PROMOTE)
    assert docker_mod.WRITE_SCRIPT.endswith(docker_mod._PROMOTE)
    # Fix round 1: the write script's own writability guard (WRITE_SCRIPT's
    # counterpart) sits between the missing-target check and the copy.
    assert ('[ -w "$1" ] || { echo "cannot append to $1: Permission denied" '
            '>&2; exit 1; }') in docker_mod.APPEND_WRITE_SCRIPT
    # Fix round 1: the guard script refuses a symlink (dangling included)
    # BEFORE the existence/regular-file checks, and stats through -L.
    assert docker_mod.APPEND_GUARD_SCRIPT.startswith('[ ! -h "$1" ] || exit 3; ')
    assert docker_mod.APPEND_GUARD_SCRIPT.endswith('stat -Lc %s -- "$1"')


def test_append_file_guard_rc2_is_the_does_not_exist_string(started):
    sb, fake, run_dir = started
    _script_append_guard(fake, _rc(2))
    out = sb.append_file("nope.md", "x")
    assert out == ("ERROR: cannot append to 'nope.md': it does not exist; create it "
                   "with write_file first")
    assert not [c for c in fake.calls if "/usr/bin/head" in c[0]]      # no read
    assert not [c for c in fake.calls if _is_append_write_exec(c)]     # no write


def test_append_file_guard_rc3_refuses_before_any_read(started):
    # Spec §1.2: a FIFO/device/directory is refused by the GUARD, before any
    # reader exec exists that a FIFO could block.
    sb, fake, run_dir = started
    _script_append_guard(fake, _rc(3))
    out = sb.append_file("pipe", "x")
    assert out == "ERROR: cannot append to 'pipe': not a regular file"
    assert not [c for c in fake.calls if "/usr/bin/head" in c[0]]
    assert not [c for c in fake.calls if _is_append_write_exec(c)]


def test_append_file_guard_size_over_the_read_cap_uses_the_result_wording(started):
    from dirtywork.tools import MAX_READ_BYTES, MAX_WRITE_BYTES
    sb, fake, run_dir = started
    _script_append_guard(fake, _ok(f"{MAX_READ_BYTES + 1}\n".encode()))
    out = sb.append_file("huge.txt", "y")
    # `_read_raw` alone discards the size (`head -c N+1` only proves "exceeds"),
    # which is exactly why the guard exec reports it: docker can name the
    # EXACT number even for a file it will never read.
    assert out == (f"ERROR: result is {MAX_READ_BYTES + 2} bytes, over the "
                   f"{MAX_WRITE_BYTES}-byte write limit; nothing was written")
    assert not [c for c in fake.calls if "/usr/bin/head" in c[0]]


def test_append_file_guard_size_plus_text_over_the_write_cap(started):
    from dirtywork.tools import MAX_READ_BYTES, MAX_WRITE_BYTES
    sb, fake, run_dir = started
    _script_append_guard(fake, _ok(f"{MAX_READ_BYTES}\n".encode()))
    out = sb.append_file("atlimit.txt", "y" * 100)
    assert out == (f"ERROR: result is {MAX_READ_BYTES + 100} bytes, over the "
                   f"{MAX_WRITE_BYTES}-byte write limit; nothing was written")
    assert not [c for c in fake.calls if "/usr/bin/head" in c[0]]


def test_append_file_oversized_text_argument_costs_no_exec(started):
    from dirtywork.tools import MAX_WRITE_BYTES
    sb, fake, run_dir = started
    out = sb.append_file("notes.md", "x" * (MAX_WRITE_BYTES + 1))
    assert out == (f"ERROR: text is {MAX_WRITE_BYTES + 1} bytes, over the "
                   f"{MAX_WRITE_BYTES}-byte write limit; append in smaller pieces")
    assert not fake.calls                       # capped BEFORE any exec
    assert "write the file in smaller pieces" not in out   # never _oversized's wording


def test_append_file_non_utf8_names_append_file(started):
    sb, fake, run_dir = started
    _script_append_guard(fake, _ok(b"8\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"], _ok(b"\xff\xfe old"))
    out = sb.append_file("bin.dat", "text")
    assert out == ("ERROR: bin.dat is not valid UTF-8 text; append_file only works "
                   "on text files")
    assert "refusing to edit" not in out
    assert not [c for c in fake.calls if _is_append_write_exec(c)]


def test_append_file_write_exec_rc2_still_refuses_as_missing(started):
    # Spec §2.6: a delete BETWEEN the guard exec and the write exec still
    # refuses correctly, because the write script re-checks `[ -f "$1" ]`.
    sb, fake, run_dir = started
    _script_append_guard(fake, _ok(b"4\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"], _ok(b"one\n"))
    fake.script(["exec", "-w", "/work", "-i", "dw-abc123", "/bin/sh", "-c",
                 docker_mod.APPEND_WRITE_SCRIPT], _rc(2))
    out = sb.append_file("notes.md", "two\n")
    assert out == ("ERROR: cannot append to 'notes.md': it does not exist; create it "
                   "with write_file first")


def test_append_file_write_exec_failure_wraps_stderr(started):
    sb, fake, run_dir = started
    _script_append_guard(fake, _ok(b"4\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"], _ok(b"one\n"))
    fake.script(["exec", "-w", "/work", "-i", "dw-abc123", "/bin/sh", "-c",
                 docker_mod.APPEND_WRITE_SCRIPT], _fail(b"cp: cannot stat: I/O error"))
    out = sb.append_file("notes.md", "two\n")
    assert out == "ERROR: cannot append to 'notes.md': cp: cannot stat: I/O error"


def test_append_file_refuses_dot_git(started):
    sb, fake, run_dir = started
    out = sb.append_file(".git/config", "\tfoo = bar\n")
    assert out == "ERROR: writing inside .git/ is not allowed (got '.git/config')"
    assert not fake.calls


def test_append_file_matches_the_host_text_for_every_shared_refusal(started, tmp_path):
    """Spec §1.2: the three caps, the does-not-exist string and the non-UTF-8
    string are byte-identical in both modes. (The non-regular-file refusal is
    deliberately NOT shared -- see this task's header.)"""
    from dirtywork import tools
    sb, fake, run_dir = started
    wt = tmp_path / "appendparity"
    wt.mkdir()

    # Cap 1: the text argument.
    huge_text = "x" * (tools.MAX_WRITE_BYTES + 1)
    assert sb.append_file("f.txt", huge_text) == tools.append_file(wt, "f.txt", huge_text)

    # The does-not-exist refusal.
    _script_append_guard(fake, _rc(2))
    assert sb.append_file("f.txt", "x") == tools.append_file(wt, "f.txt", "x")

    # Cap 2: a file too large to read.
    (wt / "f.txt").write_bytes(b"x" * (tools.MAX_READ_BYTES + 1))
    _script_append_guard(fake, _ok(f"{tools.MAX_READ_BYTES + 1}\n".encode()))
    assert sb.append_file("f.txt", "y") == tools.append_file(wt, "f.txt", "y")

    # Cap 3: the result over the write limit.
    (wt / "f.txt").write_bytes(b"x" * tools.MAX_READ_BYTES)
    _script_append_guard(fake, _ok(f"{tools.MAX_READ_BYTES}\n".encode()))
    assert sb.append_file("f.txt", "y" * 100) == tools.append_file(wt, "f.txt", "y" * 100)

    # The non-UTF-8 refusal.
    (wt / "f.txt").write_bytes(b"\xff\xfe old")
    _script_append_guard(fake, _ok(b"8\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"], _ok(b"\xff\xfe old"))
    assert sb.append_file("f.txt", "text") == tools.append_file(wt, "f.txt", "text")


# --- Fix round 1 (spec/plan amended 2026-08-23): post-read result re-check
# restores cap-3 parity when the guard's snapshot goes stale between the
# guard exec and the read exec.


def test_append_file_read_exceeds_traps_to_result_cap(started):
    # The guard approved a small size, but the read exec's own cap fires
    # (a race: the file grew between the two execs). This must surface the
    # result-cap string -- never _read_raw's "exceeds ... refusing to read",
    # which is read_file's noun, not append's -- and must cost no write exec.
    from dirtywork.tools import MAX_READ_BYTES, MAX_WRITE_BYTES
    sb, fake, run_dir = started
    _script_append_guard(fake, _ok(b"4\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"],
                _ok(b"x" * (MAX_READ_BYTES + 1)))
    out = sb.append_file("racy.txt", "y")
    assert out == (f"ERROR: result is 5 bytes, over the "
                   f"{MAX_WRITE_BYTES}-byte write limit; nothing was written")
    assert not [c for c in fake.calls if _is_append_write_exec(c)]


def test_append_file_post_read_size_over_the_write_cap_traps_to_result_cap(started):
    # The guard approved a small size, the read exec itself stays under
    # MAX_READ_BYTES (so _read_raw succeeds), but the ACTUAL content read
    # plus the new text exceeds MAX_WRITE_BYTES -- the guard's snapshot was a
    # moment old. Must refuse with the recomputed sum and cost no write exec.
    from dirtywork.tools import MAX_READ_BYTES, MAX_WRITE_BYTES
    sb, fake, run_dir = started
    _script_append_guard(fake, _ok(b"4\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"],
                _ok(b"x" * MAX_READ_BYTES))
    out = sb.append_file("racy2.txt", "y" * 100)
    assert out == (f"ERROR: result is {MAX_READ_BYTES + 100} bytes, over the "
                   f"{MAX_WRITE_BYTES}-byte write limit; nothing was written")
    assert not [c for c in fake.calls if _is_append_write_exec(c)]
```

(These two tests were added in Task 4's fix round 1, after the original Step 3 red
gate below — which still reads "12 failed" for the original 12, unrenumbered; see
the amendment note after Step 8.)

- [ ] **Step 3: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_docker_sandbox.py -q -k "append_file"`
Expected: 12 failed, every one with `AttributeError: module 'dirtywork.sandbox.docker' has no attribute 'APPEND_GUARD_SCRIPT'` (raised while building the fixture scripting) or `AttributeError: 'DockerSandbox' object has no attribute 'append_file'`.

- [ ] **Step 4: Add the two append scripts**

In `dirtywork/sandbox/docker.py`, immediately **after** `_sibling_tmp` and immediately **before** `class DockerSandbox:`.

```python
# Spec §1.2: exec 1 of three. `[ ! -h ]` FIRST refuses any symlink (dangling
# included) as exit 3, restoring parity with the host's O_NOFOLLOW probe
# (which refuses symlinks too) and closing a cap bypass: plain `stat -c %s`
# is an lstat, so a symlink to an oversized file dodged caps 2 and 3. Then
# `[ -e ]` then `[ -f ]` -- so a FIFO, device or directory is refused with
# exit 3 BEFORE any reader exec exists that a FIFO could block -- then the
# EXACT byte size on stdout via `stat -Lc`, which follows the link as
# belt-and-suspenders for a race after the `-h` check. The size matters
# because `_read_raw` alone discards it (`head -c N+1` only proves
# "exceeds"), and docker must be able to name the exact result size for a
# file it will never read.
APPEND_GUARD_SCRIPT = ('[ ! -h "$1" ] || exit 3; [ -e "$1" ] || exit 2; '
                       '[ -f "$1" ] || exit 3; stat -Lc %s -- "$1"')

# Spec §2.6: exec 3 of three. `[ -f "$1" ]` is re-checked here so a delete
# between the guard exec and this one still refuses as "does not exist"
# (exit 2) rather than silently creating the file; `-f` also re-excludes
# directories and FIFOs. The writability guard is WRITE_SCRIPT's
# counterpart: without it a 0444 target either leaks a temp-path in a
# cp/cat stderr wrap or, run as root, silently succeeds -- neither is the
# EACCES parity spec §2.6 asks for. `cp` without `-p` is fine -- the shared
# promote's `chmod --reference` runs afterward.
APPEND_WRITE_SCRIPT = (
    '[ -f "$1" ] || exit 2; '
    '[ -w "$1" ] || { echo "cannot append to $1: Permission denied" >&2; exit 1; }; '
    'cp -- "$1" "$2" && cat >> "$2" && ' + _PROMOTE
)
```

**Fix round 1 (executed 2026-08-23):** the code block above is the FINAL shipped
text; it originally read `APPEND_GUARD_SCRIPT = '[ -e "$1" ] || exit 2; [ -f "$1" ]
|| exit 3; stat -c %s -- "$1"'` and an `APPEND_WRITE_SCRIPT` with no writability
guard. Post-review, the reviewer proved in-container that plain `stat -c %s` is an
lstat: a symlink to an oversized file dodged both size caps. See this task's
amendment note after Step 8 for the full fix-round writeup.

- [ ] **Step 5: Add the three methods**

In `dirtywork/sandbox/docker.py`, immediately **after** `write_file` (which ends with `return describe_write(path, old_text, content, len(encoded))`) and immediately **before** `def _transform_file(`.

```python
    def _append_guard(self, path: str, rel: str, text_len: int):
        """Spec §1.2 exec 1: (size, None) or (None, error). Decides existence,
        regular-file-ness and both size caps before anything is read."""
        argv = docker_args.exec_argv(
            self.container, ["/bin/sh", "-c", APPEND_GUARD_SCRIPT, "_", rel])
        captured = self._run(argv, timeout=READ_EXEC_TIMEOUT)
        if captured.returncode == 2:
            return None, _append_missing(path)
        if captured.returncode == 3:
            return None, f"ERROR: cannot append to '{path}': not a regular file"
        text = captured.output.decode("utf-8", "replace")
        if captured.returncode != 0:
            return None, f"ERROR: cannot append to '{path}': {text[:500]}"
        try:
            size = int(text.strip())
        except ValueError:
            return None, f"ERROR: cannot append to '{path}': {text[:500]}"
        if size > MAX_READ_BYTES or size + text_len > MAX_WRITE_BYTES:
            # Caps 2 and 3 share one sentence, exactly as on the host, so a
            # file too large to read reads as un-appendable rather than
            # surfacing _read_raw's "refusing to read" wording.
            return None, _result_too_big(size + text_len)
        return size, None

    def _append_write(self, path: str, rel: str, encoded: bytes) -> str:
        """Spec §2.6 exec 3: '' on success, an 'ERROR: …' string otherwise."""
        argv = docker_args.exec_argv(
            self.container,
            ["/bin/sh", "-c", APPEND_WRITE_SCRIPT, "_", rel, _sibling_tmp(rel)],
            stdin=True,
        )
        captured = self._run(argv, timeout=WRITE_EXEC_TIMEOUT, stdin=encoded)
        if captured.returncode == 2:
            return _append_missing(path)
        if captured.returncode != 0:
            return (f"ERROR: cannot append to '{path}': "
                    f"{captured.output.decode('utf-8', 'replace')[:500]}")
        return ""

    def append_file(self, path: str, text: str) -> str:
        """Spec §1.2: three execs, in the same cap order tools.append_file
        uses, so both modes emit identical strings from identical conditions.
        The `text` argument is capped by _append_oversized BEFORE any exec;
        this path never routes the payload through _oversized, which says
        `ERROR: content is <n> bytes, over the <limit>-byte write limit` --
        write_file's noun, and the wrong fix for an append. (The longer
        host-only form with the `; write the file in smaller pieces` tail
        lives in tools.write_file and has no counterpart in this module.)"""
        encoded = text.encode("utf-8")
        too_big = _append_oversized(encoded)
        if too_big:
            return too_big
        rel, err = _rel(path, writing=True)
        if err:
            return err
        size, err = self._append_guard(path, rel, len(encoded))
        if err:
            return err
        # The guard already refused a file over MAX_READ_BYTES with the
        # result-cap string; the exceeds-trap just below covers the race
        # where the file grows between that exec and this one.
        # `tool="append_file"` is what makes a non-UTF-8 file refuse with
        # the host's append wording rather than the legacy `refusing to
        # edit` (spec §5.1).
        old_text, err = self._read_raw(path, strict=True, tool="append_file")
        if err:
            if err.startswith(f"ERROR: '{path}' exceeds "):
                # TOCTOU: the guard's snapshot approved a size that
                # _read_raw's own cap disproved a moment later. Trapped
                # here so read_file's "refusing to read" wording -- the
                # wrong noun for an append -- can never surface; reported
                # against the guard's last-known size, the best number
                # available (mirrors tools.append_file's probe-then-read
                # race handling, spec §2.6).
                return _result_too_big(size + len(encoded))
            return err
        # Re-checked against what was actually read, mirroring
        # tools.append_file: the guard's size is a moment old, and the file
        # may have grown in place since ("the probe's size is a moment
        # old", spec §2.6).
        if len(old_text.encode("utf-8")) + len(encoded) > MAX_WRITE_BYTES:
            return _result_too_big(len(old_text.encode("utf-8")) + len(encoded))
        err = self._append_write(path, rel, encoded)
        if err:
            return err
        return describe_change(path, old_text, old_text + text, verb="Appended to")
```

**Fix round 1 (executed 2026-08-23):** `append_file`'s body above is the FINAL
shipped text; the `_size, err = ...` local was originally discarded (named `_size`)
and there was no exceeds-trap or post-read re-check. See the amendment note after
Step 8.

- [ ] **Step 6: Run the module and see it pass**

Run: `/usr/bin/python3 -m pytest tests/test_docker_sandbox.py -q`
Expected: exit code 0.

- [ ] **Step 7: Run the full suite**

Run: `/usr/bin/python3 -m pytest -q`
Expected: exit code 0; `1078 passed, 1 skipped, 18 deselected` (1064 + 14).

- [ ] **Step 8: Commit**

```bash
git add dirtywork/sandbox/docker.py tests/docker_fakes.py tests/test_docker_sandbox.py
git commit -m "feat(docker): append_file — guard+size exec, tool-aware read, atomic append write"
```

---

**Task 4 amendment (fix round 1, executed 2026-08-23):** post-review found two
Important cap-bypass defects, both spec-authored (the transcription into code was
faithful), plus a missing writability guard, and fixed all three in one round: (1)
`APPEND_GUARD_SCRIPT`'s original `stat -c %s` was an **lstat** — a symlink to an
oversized file dodged both size caps, since the guard never dereferenced it; fixed
by refusing any symlink (dangling included) with `[ ! -h "$1" ] || exit 3` FIRST
(restoring parity with the host's O_NOFOLLOW probe, which also refuses symlinks)
and switching the size read to `stat -Lc`. (2) `APPEND_WRITE_SCRIPT` was missing
WRITE_SCRIPT's own writability guard, so a 0444 target either leaked a temp path
in a `cp`/`cat` stderr wrap or, run as root, silently succeeded; fixed by adding
`[ -w "$1" ] || { echo "cannot append to $1: Permission denied" >&2; exit 1; }`
right after the missing-target check. (3) `append_file` trusted the guard's
snapshot with no re-check: a race between the guard exec and the read exec (the
file grows in between) could surface `_read_raw`'s own "exceeds ... refusing to
read" wording — read_file's noun, not append's — or silently write a result over
`MAX_WRITE_BYTES`. Fixed by trapping `_read_raw`'s exceeds error and remapping it
to the result-cap string built from the guard's last-known size, and by
re-checking `len(old_text.encode("utf-8")) + len(encoded)` against
`MAX_WRITE_BYTES` after a successful read, before the write exec — both mirror
`tools.append_file`'s own probe-then-read race handling (tools.py ~:830). Two new
tests were added (`test_append_file_read_exceeds_traps_to_result_cap`,
`test_append_file_post_read_size_over_the_write_cap_traps_to_result_cap`), plus
two pin assertions in `test_append_file_write_script_shape`. Commit:
`fix(docker): append guard refuses symlinks and stats through -L; writability
guard in the append script; post-read result re-check restores cap-3 parity
(spec/plan amended)`. This task's own gates now read `105 passed` for the module
(103 + 2) and `1078 passed, 1 skipped, 18 deselected` for the full suite (1076 +
2); the Step 7 gate above and every downstream task's documented count are left
as originally written — renumbering the plan's global count chain is the
controller's call, not this fix round's.

### Task 5: register `append_file` as the eleventh tool (spec §1.2 registration, §7 contract)

Both backends implement it now, so the Protocol, the registry, the runner's mutating-tool set, the system prompt, the wire fixture and every "ten tools" count move together — one task, so the tree is never advertising a tool one backend lacks.

**Files:**
- Modify: `dirtywork/builtin_tools.py` (module docstring `:1`; new `_append_file` dispatcher after `_write_file` at `:34-35`; new `APPEND_FILE_SPEC` immediately after `WRITE_FILE_SPEC` which ends at `:98`; `BUILTIN_SPECS` `:243-245`)
- Modify: `dirtywork/sandbox/__init__.py` (Protocol docstring `:52`; new `append_file` after `write_file` at `:66`)
- Modify: `dirtywork/runner.py` (`_MUTATING_TOOLS` `:160`)
- Modify: `dirtywork/__main__.py` (`build_system_prompt`'s file rule at `:88`)
- Modify: `tests/fixtures/tool_schemas.json` (REGENERATED)
- Modify: `tests/test_builtin_tools.py` (`FakeSandbox` `:17-55`, `test_schemas_shape` `:77-84`, 2 new tests)
- Modify: `tests/test_transcript_schema.py` (`test_doc_documents_the_finish_tool_and_the_ten_tools` `:56-61`, renamed)
- Modify: `tests/test_runner.py` (1 new test)
- Modify: `README.md` (`:46-47` tool enumeration, `:159-161` the "ten tools" list, `:168-169` the diff-echo sentence)
- Modify: `docs/security.md` (`:25-26` tool enumeration)
- Modify: `docs/machine-contract.md` (`:119` "exactly ten tools" + a new `append_file` bullet after the `write_file` bullet at `:123-125`)
- Modify: `docs/transcript-schema.md` (`tool` enum row `:70`, result-format row `:72`)
- Modify: `docs/operating.md` (the tool sentence at `:37-38`)

**Interfaces:**
- Consumes: `tools.append_file` (Task 2), `HostSandbox.append_file` (Task 2), `DockerSandbox.append_file` (Task 4); `toolspec.ToolSpec`, `ParamSpec`, `Caps`; `builtin_tools.TOOL_OUTPUT_CAP`.
- Produces:
  - `builtin_tools._append_file(sandbox, path, text)`
  - `builtin_tools.APPEND_FILE_SPEC: ToolSpec` (name `"append_file"`, required `("path", "text")`)
  - `Sandbox.append_file(path: str, text: str) -> str` on the Protocol
  - `runner._MUTATING_TOOLS` gains `"append_file"`

- [ ] **Step 1: Write the failing tests**

In `tests/test_builtin_tools.py`, add the method to `FakeSandbox`, immediately after `write_file`.

Before:

```python
    def write_file(self, path, content):
        self.calls.append(("write_file", path, content))
        return f"wrote:{path}:{len(content)}"
```

After:

```python
    def write_file(self, path, content):
        self.calls.append(("write_file", path, content))
        return f"wrote:{path}:{len(content)}"

    def append_file(self, path, text):
        self.calls.append(("append_file", path, text))
        return f"appended:{path}:{len(text)}"
```

Update `test_schemas_shape`'s name set.

Before:

```python
    assert names == {"read_file", "write_file", "edit_file", "apply_edits", "insert_before",
                     "insert_after", "list_dir", "grep", "bash", "finish"}
```

After:

```python
    assert names == {"read_file", "write_file", "append_file", "edit_file", "apply_edits",
                     "insert_before", "insert_after", "list_dir", "grep", "bash", "finish"}
```

Append to `tests/test_builtin_tools.py`:

```python
def test_append_file_dispatches_and_declares_its_caps():
    sandbox = FakeSandbox()
    registry = default_registry()
    result = registry.execute("append_file", {"path": "notes.md", "text": "more\n"},
                              sandbox=sandbox, deadline=None)
    assert result.kind == "ok" and result.text == "appended:notes.md:5"
    assert sandbox.calls == [("append_file", "notes.md", "more\n")]
    caps = registry.spec("append_file").caps
    assert caps.fs == "write"
    assert caps.max_output_chars == TOOL_OUTPUT_CAP
    assert caps.transcript == "preview"
    # order is significant and documented in BUILTIN_SPECS (spec §1.2)
    names = registry.names()
    assert names[names.index("write_file") + 1] == "append_file"
    assert len(names) == 11


def test_append_file_description_warns_about_the_leading_newline():
    spec = default_registry().spec("append_file")
    assert spec.description == (
        "Append text verbatim to the END of an existing file (create the file "
        "with write_file first). Nothing is inserted between the old content and "
        "your text — include a leading newline if the file does not end with one. "
        "Use write_file + append_file to produce a file too large for one reply.")
    assert set(spec.required) == {"path", "text"}
```

`tests/test_builtin_tools.py` must import `TOOL_OUTPUT_CAP`; check its import block and add it if absent:

```bash
grep -n "TOOL_OUTPUT_CAP" tests/test_builtin_tools.py
```
If there is no hit, change the import line `from dirtywork.builtin_tools import default_registry` to `from dirtywork.builtin_tools import TOOL_OUTPUT_CAP, default_registry`.

In `tests/test_transcript_schema.py`, rename and extend the tool-doc test.

Before:

```python
def test_doc_documents_the_finish_tool_and_the_ten_tools():
    tokens = _doc_tokens()
    for name in ("read_file", "write_file", "edit_file", "apply_edits", "insert_before",
                 "insert_after", "list_dir", "grep", "bash", "finish"):
        assert name in tokens, f"tool '{name}' is not documented"
```

After:

```python
def test_doc_documents_the_finish_tool_and_the_eleven_tools():
    tokens = _doc_tokens()
    for name in ("read_file", "write_file", "append_file", "edit_file", "apply_edits",
                 "insert_before", "insert_after", "list_dir", "grep", "bash", "finish"):
        assert name in tokens, f"tool '{name}' is not documented"
```

Append to `tests/test_runner.py`:

```python
def test_mutating_tools_includes_every_tool_that_changes_a_file():
    # Spec §6: a run whose only progress is inserts/batches/appends must not be
    # called stalled. _MUTATING_TOOLS is what ProgressTracker reads.
    from dirtywork.runner import _MUTATING_TOOLS
    assert set(_MUTATING_TOOLS) == {"write_file", "append_file", "edit_file",
                                    "apply_edits", "insert_before", "insert_after"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_builtin_tools.py tests/test_transcript_schema.py tests/test_runner.py -q -k "append_file or schemas_shape or eleven_tools or mutating_tools"`
Expected: failures — `test_append_file_dispatches_and_declares_its_caps` and `test_append_file_description_warns_about_the_leading_newline` with `ToolResult` `unknown_tool` / `AttributeError` on `spec("append_file")` being `None`; `test_schemas_shape` with an `AssertionError` on the name set; `test_doc_documents_the_finish_tool_and_the_eleven_tools` with `AssertionError: tool 'append_file' is not documented`; `test_mutating_tools_includes_every_tool_that_changes_a_file` with an `AssertionError` on the set.

- [ ] **Step 3: Add the dispatcher and the spec**

In `dirtywork/builtin_tools.py`, immediately **after** `_write_file` and immediately **before** `def _edit_file(`.

```python
def _append_file(sandbox, path, text):
    return sandbox.append_file(path, text)
```

Then immediately **after** `WRITE_FILE_SPEC` (which ends with `caps=Caps(fs="write", max_output_chars=TOOL_OUTPUT_CAP, transcript="preview"),\n)`) and immediately **before** `EDIT_FILE_SPEC = ToolSpec(`.

```python
APPEND_FILE_SPEC = ToolSpec(
    name="append_file",
    description="Append text verbatim to the END of an existing file (create "
                "the file with write_file first). Nothing is inserted between "
                "the old content and your text — include a leading newline if "
                "the file does not end with one. Use write_file + append_file "
                "to produce a file too large for one reply.",
    params={
        "path": ParamSpec(type="string"),
        "text": ParamSpec(type="string"),
    },
    required=("path", "text"),
    fn=_append_file,
    caps=Caps(fs="write", max_output_chars=TOOL_OUTPUT_CAP, transcript="preview"),
)
```

Then update `BUILTIN_SPECS`.

Before:

```python
BUILTIN_SPECS = (READ_FILE_SPEC, WRITE_FILE_SPEC, EDIT_FILE_SPEC, APPLY_EDITS_SPEC,
                 INSERT_BEFORE_SPEC, INSERT_AFTER_SPEC, LIST_DIR_SPEC, GREP_SPEC,
                 BASH_SPEC, FINISH_SPEC)
```

After:

```python
BUILTIN_SPECS = (READ_FILE_SPEC, WRITE_FILE_SPEC, APPEND_FILE_SPEC, EDIT_FILE_SPEC,
                 APPLY_EDITS_SPEC, INSERT_BEFORE_SPEC, INSERT_AFTER_SPEC, LIST_DIR_SPEC,
                 GREP_SPEC, BASH_SPEC, FINISH_SPEC)
```

And the module docstring's first line.

Before:

```python
"""The ten tools dirtywork ships, declared as ToolSpecs.
```

After:

```python
"""The eleven tools dirtywork ships, declared as ToolSpecs.
```

- [ ] **Step 4: Add the Protocol method**

In `dirtywork/sandbox/__init__.py`.

Before:

```python
    Tool methods (read_file/write_file/edit_file/apply_edits/insert_before/insert_after/list_dir/grep/bash) may raise BudgetExceeded (worktree over budget) or SandboxError (backend failure); the runner catches both.
```

After:

```python
    Tool methods (read_file/write_file/append_file/edit_file/apply_edits/insert_before/insert_after/list_dir/grep/bash) may raise BudgetExceeded (worktree over budget) or SandboxError (backend failure); the runner catches both.
```

Before:

```python
    def write_file(self, path: str, content: str) -> str: ...
```

After:

```python
    def write_file(self, path: str, content: str) -> str: ...

    def append_file(self, path: str, text: str) -> str: ...
```

- [ ] **Step 5: Add it to the runner's mutating set**

In `dirtywork/runner.py`.

Before:

```python
_MUTATING_TOOLS = ("write_file", "edit_file", "apply_edits", "insert_before", "insert_after")
```

After:

```python
_MUTATING_TOOLS = ("write_file", "append_file", "edit_file", "apply_edits",
                   "insert_before", "insert_after")
```

- [ ] **Step 6: Name it in the system prompt**

In `dirtywork/__main__.py`, inside `build_system_prompt`.

Before:

```python
- Use edit_file, apply_edits (several exact replacements in one file at once), insert_before, insert_after or write_file for ALL file changes. Never modify files via bash (no sed -i, no echo redirects, no heredocs).
```

After:

```python
- Use edit_file, apply_edits (several exact replacements in one file at once), insert_before, insert_after, write_file or append_file for ALL file changes. Never modify files via bash (no sed -i, no echo redirects, no heredocs).
- A file too large for one reply: write_file the first part, then append_file each following part. append_file adds your text to the END of an existing file with nothing inserted between, so include a leading newline when the file does not end with one.
```

- [ ] **Step 7: Regenerate the frozen wire fixture**

Run:

```bash
/usr/bin/python3 -c "import json; from dirtywork.builtin_tools import default_registry; \
  open('tests/fixtures/tool_schemas.json','w',encoding='utf-8').write(\
  json.dumps(default_registry().schemas(), indent=2, ensure_ascii=False) + '\n')"
git diff --stat tests/fixtures/tool_schemas.json
```
Expected: the diff adds exactly the `append_file` function object, in third position (after `write_file`), and changes nothing else. Read the diff before continuing.

- [ ] **Step 8: Update the docs — README**

In `README.md`.

Before:

```
Every tool call (`read_file`/`write_file`/`edit_file`/`apply_edits`/
`insert_before`/`insert_after`/`list_dir`/`grep`/`bash`) runs inside a
```

After:

```
Every tool call (`read_file`/`write_file`/`append_file`/`edit_file`/`apply_edits`/
`insert_before`/`insert_after`/`list_dir`/`grep`/`bash`) runs inside a
```

Before:

```
3. **The loop** — the model gets ten tools (`read_file`, `write_file`,
   `edit_file`, `apply_edits`, `insert_before`, `insert_after`, `list_dir`,
   `grep`, `bash`, `finish`) via OpenAI function-calling.
```

After:

```
3. **The loop** — the model gets eleven tools (`read_file`, `write_file`,
   `append_file`, `edit_file`, `apply_edits`, `insert_before`, `insert_after`,
   `list_dir`, `grep`, `bash`, `finish`) via OpenAI function-calling.
   `append_file` adds text verbatim to the end of an existing file, so a file
   larger than one reply is `write_file` for the first part and `append_file`
   for each part after it — the recovery a truncated `write_file` is now told
   to use by name.
```

Before:

```
   `edit_file`/`apply_edits`/`write_file`/`insert_*` result
   echoes a capped unified diff of what actually changed, so a replace that
   silently deleted a line is visible to the worker in the same turn.
```

After:

```
   `edit_file`/`apply_edits`/`write_file`/`append_file`/`insert_*` result
   echoes a capped unified diff of what actually changed, so a replace that
   silently deleted a line is visible to the worker in the same turn — except
   `write_file` on a NEW file, which has nothing to diff against and reports
   its byte and line count instead.
```

- [ ] **Step 9: Update the docs — `docs/security.md`**

Before:

```
Every tool call (`read_file`/`write_file`/`edit_file`/`apply_edits`/
`insert_before`/`insert_after`/`list_dir`/`grep`/`bash`) runs inside a
```

After:

```
Every tool call (`read_file`/`write_file`/`append_file`/`edit_file`/`apply_edits`/
`insert_before`/`insert_after`/`list_dir`/`grep`/`bash`) runs inside a
```

- [ ] **Step 10: Update the docs — `docs/machine-contract.md`**

Before:

```
**Tools:** the worker is advertised exactly ten tools, in this order. They are
not configurable; a run's tool surface is the same in host and docker mode.
```

After:

```
**Tools:** the worker is advertised exactly eleven tools, in this order. They are
not configurable; a run's tool surface is the same in host and docker mode.
```

Then add a bullet immediately **after** the `write_file` bullet (which ends `and line count instead).`) and immediately **before** the `- \`edit_file(path, old_string, new_string)\`` bullet.

```
- `append_file(path, text)` — append `text` **verbatim** to the end of an
  EXISTING file; nothing is inserted between the old content and the new, so a
  file that does not end in a newline needs one at the start of `text`. A
  missing target refuses with `ERROR: cannot append to '<path>': it does not
  exist; create it with write_file first` — `append_file` never creates a file
  or a parent directory. Three caps, in order and identical in both modes: the
  `text` argument (`ERROR: text is <n> bytes, over the <limit>-byte write
  limit; append in smaller pieces`), the current file's size, and the result
  size (both of the latter render `ERROR: result is <n> bytes, over the
  <limit>-byte write limit; nothing was written`). This is the second half of
  the large-file recipe: `write_file` the first part, `append_file` the rest.
```

- [ ] **Step 11: Update the docs — `docs/transcript-schema.md`**

Before:

```
| `tool` | ✓ | ✓ | string | tool name — one of `read_file`, `write_file`, `edit_file`, `apply_edits`, `insert_before`, `insert_after`, `list_dir`, `grep`, `bash`, `finish` (`insert_before`/`insert_after` are v2, added in 0.8; `apply_edits` in 0.9); `""` for a discarded malformed entry |
```

After:

```
| `tool` | ✓ | ✓ | string | tool name — one of `read_file`, `write_file`, `append_file`, `edit_file`, `apply_edits`, `insert_before`, `insert_after`, `list_dir`, `grep`, `bash`, `finish` (`insert_before`/`insert_after` are v2, added in 0.8; `apply_edits` in 0.9; `append_file` in 0.10); `""` for a discarded malformed entry |
```

In the `result` row (`:72`), append this sentence immediately before the closing ` |`, after `… on both backends (0.9)`:

```
. 0.10's `append_file` uses the same shape with the verb `Appended to`; it reads `+A -0` only when the file already ended in a newline — when it did not, the final line is a REPLACE and the header reads `+A -1 (removed 1 non-blank line)`, which is the visible consequence of not starting `text` with a newline
```

- [ ] **Step 12: Update the docs — `docs/operating.md`**

Before:

```
The worker changes files only through tools — `write_file`, `edit_file`,
`apply_edits`, `insert_before`, `insert_after` — never through `bash`. When a
```

After:

```
The worker changes files only through tools — `write_file`, `append_file`,
`edit_file`, `apply_edits`, `insert_before`, `insert_after` — never through
`bash`. A file larger than one reply is `write_file` for the first part and
`append_file` for each part after it; `append_file` adds text verbatim to the
end of an EXISTING file, inserting nothing between the old content and the
new, so `text` needs a leading newline when the file does not end with one.
When a
```

- [ ] **Step 13: Run the affected modules and see them pass**

Run: `/usr/bin/python3 -m pytest tests/test_builtin_tools.py tests/test_transcript_schema.py tests/test_runner.py -q`
Expected: exit code 0.

- [ ] **Step 14: Run the full suite**

Run: `/usr/bin/python3 -m pytest -q`
Expected: exit code 0; `1081 passed, 1 skipped, 18 deselected` (1078 + 3).

- [ ] **Step 15: Commit**

```bash
git add dirtywork/builtin_tools.py dirtywork/sandbox/__init__.py dirtywork/runner.py \
        dirtywork/__main__.py tests/fixtures/tool_schemas.json tests/test_builtin_tools.py \
        tests/test_transcript_schema.py tests/test_runner.py README.md docs/security.md \
        docs/machine-contract.md docs/transcript-schema.md docs/operating.md
git commit -m "feat: register append_file as the eleventh tool"
```

---

### Task 6: every host write goes through `_write_atomic`, plus the stale-temp sweep (spec §2.1, §2.3, §2.4, §2.5)

**Files:**
- Modify: `dirtywork/tools.py` (`write_file` `:277-312`, `_transform_file`'s write tail `:341-360`)
- Modify: `dirtywork/budget.py` (`BudgetReport` `:20-25`, `_measure_posix` `:45-86`, `_measure_windows` signature `:89`, `measure_worktree` `:139-142`)
- Modify: `dirtywork/sandbox/host.py` (imports `:1-13`, `_measure` `:35-37`, new `_sweep_note`, `start` `:29-33`, `finalize` `:84-99`)
- Modify: `dirtywork/sandbox/export.py` (imports `:15-16`, a sweep exec immediately before the `git add -A` at `:258`)
- Modify: `tests/test_tools_files.py` (5 new tests)
- Modify: `tests/test_budget.py` (2 new tests)
- Modify: `tests/test_sandbox_host.py` (2 new tests)
- Modify: `tests/test_export_flow.py` (1 new test)
- Modify: `docs/machine-contract.md` (`:153-155`), `docs/operating.md` (`:47-56`), `docs/security.md` (`:128-132`)

**Interfaces:**
- Consumes: `tools._write_atomic(target, data, *, path, verb, create_parents, must_exist)`, `tools.is_temp_name(name)`, `tools.TMP_FIND_REGEX` (all Task 1). Neither of this task's two call sites passes `must_exist`: `write_file` and `_transform_file` both keep §2.2's new-file branch, which is exactly what `must_exist=False` (the default) means.
- Produces:
  - `budget.BudgetReport.swept: int = 0`
  - `budget.measure_worktree(worktree, *, max_bytes: int, max_files: int, sweep_temps: bool = False) -> BudgetReport`
  - `HostSandbox._measure(*, sweep_temps: bool = False) -> BudgetReport`
  - `HostSandbox._sweep_note(report) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools_files.py`:

```python
# --- spec §2.1/§2.3: every host write now goes through _write_atomic.


def test_write_file_promotes_by_rename_and_leaves_no_temp(wt: Path):
    target = wt / "README.md"
    before = target.stat().st_ino
    assert tools.write_file(wt, "README.md", "# Demo v2\n").startswith("Wrote README.md:")
    assert target.read_text() == "# Demo v2\n"
    assert target.stat().st_ino != before
    assert _temp_leftovers(wt) == []


def test_write_file_is_atomic_the_target_is_unchanged_when_the_write_fails(wt: Path, monkeypatch):
    target = wt / "README.md"

    def _boom(fd, data):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(tools, "_write_all", _boom)
    out = tools.write_file(wt, "README.md", "clobbered")
    assert out.startswith("ERROR: cannot write 'README.md': ")
    assert target.read_text() == "# Demo\n"      # spec §2.3: byte-identical
    assert _temp_leftovers(wt) == []


def test_edit_file_is_atomic_the_target_is_unchanged_when_the_write_fails(wt: Path, monkeypatch):
    target = wt / "src" / "app.py"
    original = target.read_text()

    def _boom(fd, data):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(tools, "_write_all", _boom)
    out = tools.edit_file(wt, "src/app.py", "return 42", "return 43")
    assert out.startswith("ERROR: cannot write 'src/app.py': ")
    assert target.read_text() == original
    assert _temp_leftovers(wt / "src") == []


def test_transform_preserves_mode_through_the_promote(wt: Path):
    target = wt / "hook.sh"
    target.write_text("old\n")
    target.chmod(0o750)
    assert tools.edit_file(wt, "hook.sh", "old", "new").startswith("Edited hook.sh:")
    assert target.read_text() == "new\n"
    assert _stat.S_IMODE(target.stat().st_mode) == 0o750


def test_write_file_still_creates_parents_and_uses_the_umask_default_mode(wt: Path):
    out = tools.write_file(wt, "a/b/c.txt", "deep\n")
    assert out.startswith("Wrote 5 bytes to a/b/c.txt (new file, 1 line)")
    made = wt / "a" / "b" / "c.txt"
    assert made.read_text() == "deep\n"
    assert _stat.S_IMODE(made.stat().st_mode) == 0o644 & ~tools._UMASK
```

Append to `tests/test_budget.py`:

```python
def test_measure_worktree_sweeps_only_generated_temps(wt: Path):
    # Spec §2.5: the sweep matches the FULL generated shape, so a worker file
    # that merely starts like one survives.
    from dirtywork import tools
    ours = wt / tools.tmp_name("app.py")
    ours.write_text("staged")
    theirs = wt / ".dw-tmp.notes"
    theirs.write_text("mine")
    (wt / "keep.txt").write_text("keep")
    report = measure_worktree(wt, max_bytes=10 * 1024 * 1024, max_files=1000,
                              sweep_temps=True)
    assert report.swept == 1
    assert not ours.exists()
    assert theirs.read_text() == "mine"
    assert (wt / "keep.txt").read_text() == "keep"
    assert report.files == 2          # a swept temp is not counted


def test_measure_worktree_does_not_sweep_unless_asked(wt: Path):
    from dirtywork import tools
    ours = wt / tools.tmp_name("app.py")
    ours.write_text("staged")
    report = measure_worktree(wt, max_bytes=10 * 1024 * 1024, max_files=1000)
    assert report.swept == 0
    assert ours.exists()
    assert report.files == 1
```

Append to `tests/test_sandbox_host.py`:

```python
def test_host_sandbox_start_sweeps_a_leftover_temp_and_says_so(wt: Path, capsys):
    # Spec §2.5: only a KILL can leave one behind, and a resume is where it
    # shows up. Never silent.
    from dirtywork import tools
    leftover = wt / tools.tmp_name("hello.txt")
    leftover.write_text("half-written")
    sb = HostSandbox(wt)
    sb.start(wt, wt, "slug", "deadbeef")
    assert not leftover.exists()
    assert "swept 1 stale temp file" in capsys.readouterr().err


def test_host_sandbox_finalize_sweeps_and_reports(wt: Path, capsys):
    from dirtywork import tools
    sb = HostSandbox(wt)
    sb.start(wt, wt, "slug", "deadbeef")
    capsys.readouterr()
    (wt / tools.tmp_name("a.txt")).write_text("x")
    (wt / tools.tmp_name("b.txt")).write_text("y")
    sb.finalize()
    assert "swept 2 stale temp files" in capsys.readouterr().err
    assert [p.name for p in wt.iterdir() if p.name.startswith(tools.TMP_PREFIX)] == []
```

Append to `tests/test_export_flow.py`:

```python
def test_export_sweeps_stale_temps_before_git_add(tmp_path, empty_worktree):
    from dirtywork.tools import TMP_FIND_REGEX
    fake = FakeDocker()
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/find", "/work",
                 "-type", "f", "-regextype", "posix-extended", "-regex", TMP_FIND_REGEX],
                _ok(b"/work/src/.dw-tmp.app.py.deadbeef\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "write-tree"],
                _ok(b"treehash1234\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff", "--stat",
                 "deadbeef" * 5, "treehash1234"], _ok(b" 1 file changed\n"))
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff",
         "deadbeef" * 5, "treehash1234"], b"diff --git a/x b/x\n+hi\n")
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "archive",
         "--format=tar", "treehash1234"],
        _make_tar([{"name": "hello.txt", "content": b"hi there"}]))
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()

    export_run(
        DockerConfig(), slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    argvs = [c[0] for c in fake.calls]
    sweeps = [a for a in argvs if "-regextype" in a]
    adds = [a for a in argvs if a[-3:] == ["/usr/bin/git", "add", "-A"]]
    assert len(sweeps) == 1 and len(adds) == 1
    assert sweeps[0][4:] == ["/usr/bin/find", "/work", "-type", "f", "-regextype",
                             "posix-extended", "-regex", TMP_FIND_REGEX,
                             "-print", "-delete"]
    assert argvs.index(sweeps[0]) < argvs.index(adds[0])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_budget.py tests/test_sandbox_host.py tests/test_export_flow.py -q -k "sweep"`
Expected: 5 failed — the two `tests/test_budget.py` ones with `TypeError: measure_worktree() got an unexpected keyword argument 'sweep_temps'`, the two `tests/test_sandbox_host.py` ones with `AssertionError` on the missing stderr line, and the export one with `AssertionError: assert 0 == 1` on `len(sweeps)`.

Run: `/usr/bin/python3 -m pytest tests/test_tools_files.py -q -k "promotes_by_rename or is_atomic or preserves_mode_through or umask_default_mode"`
Expected: **5 selected, 3 failed, 2 passed.** Failing: `test_write_file_promotes_by_rename_and_leaves_no_temp` on the inode assertion, and the two `is_atomic` ones because `_write_all` is not on today's write path (so no exception fires and the file IS clobbered). Passing already, and they are pins on behaviour this task must preserve rather than tests it must turn green: `test_transform_preserves_mode_through_the_promote` and `test_write_file_still_creates_parents_and_uses_the_umask_default_mode`.

- [ ] **Step 3: Move `write_file` onto the primitive**

In `dirtywork/tools.py`.

Before:

```python
    p = _worktree_candidate(path, worktree)
    # Best-effort 'before' picture, taken after the containment check and
    # before the truncating open. None means "nothing to diff against".
    old_text = _read_text_for_diff(p)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"ERROR: cannot write '{path}': {e}"
    try:
        fh = _open_regular(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    except OSError as e:
        if e.errno == errno.ELOOP:
            return (
                f"ERROR: '{path}' is a symlink; writing through a symlink is not "
                f"allowed even when its target is inside the worktree"
            )
        if e.errno == errno.ENXIO:
            return f"ERROR: '{path}' is not a regular file (refusing FIFO/device/socket)"
        return f"ERROR: cannot write '{path}': {e}"
    try:
        fh.write(encoded)
    finally:
        fh.close()
    return describe_write(path, old_text, content, len(encoded))
```

After:

```python
    p = _worktree_candidate(path, worktree)
    # Best-effort 'before' picture, taken after the containment check and
    # before the write. None means "nothing to diff against".
    old_text = _read_text_for_diff(p)
    # Spec §2.1: staged through a sibling temp and promoted with os.replace, so
    # a kill or an I/O error mid-write leaves the old file byte-identical
    # instead of truncated. Every refusal string is unchanged --
    # _write_atomic's probe is the same open, minus O_CREAT|O_TRUNC.
    err = _write_atomic(p, encoded, path=path, create_parents=True)
    if err:
        return err
    return describe_write(path, old_text, content, len(encoded))
```

- [ ] **Step 4: Move `_transform_file` onto the primitive**

In `dirtywork/tools.py`.

Before:

```python
    write_target = _worktree_candidate(path, worktree)
    try:
        wfh = _open_regular(write_target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    except OSError as e:
        if e.errno == errno.ELOOP:
            return (
                f"ERROR: '{path}' is a symlink; writing through a symlink is not "
                f"allowed even when its target is inside the worktree"
            )
        if e.errno == errno.ENXIO:
            return f"ERROR: '{path}' is not a regular file (refusing FIFO/device/socket)"
        return f"ERROR: cannot write '{path}': {e}"
    try:
        wfh.write(new_text.encode("utf-8"))
    finally:
        wfh.close()
    return result
```

After:

```python
    write_target = _worktree_candidate(path, worktree)
    # Spec §2.1: the same staged write write_file uses. _transform_file never
    # creates parents -- it only ever writes a file it just read.
    err = _write_atomic(write_target, new_text.encode("utf-8"), path=path)
    if err:
        return err
    return result
```

Then update `_transform_file`'s docstring's last sentence.

Before:

```python
    5 MB read limit, UTF-8 validation, and the O_NOFOLLOW write -- plus, since
    0.9, the shared output cap (_check_write_size, spec §1.5)."""
```

After:

```python
    5 MB read limit, UTF-8 validation, and the O_NOFOLLOW write -- plus, since
    0.9, the shared output cap (_check_write_size, spec §1.5) and, since 0.10,
    the staged write (_write_atomic, spec §2.1)."""
```

- [ ] **Step 5: Give `BudgetReport` a swept count**

In `dirtywork/budget.py`.

Before:

```python
@dataclass
class BudgetReport:
    bytes: int
    files: int
    escaping_symlinks: list
    violation: str | None
```

After:

```python
@dataclass
class BudgetReport:
    bytes: int
    files: int
    escaping_symlinks: list
    violation: str | None
    # Spec §2.5: staging temps this walk removed. Defaulted and LAST, so every
    # existing four-positional construction stays valid; 0 unless the caller
    # asked for the sweep.
    swept: int = 0
```

Add the import at the top of `dirtywork/budget.py`.

Before:

```python
import os
import stat
from dataclasses import dataclass
from pathlib import Path
```

After:

```python
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .tools import is_temp_name
```

- [ ] **Step 6: Fold the sweep into the POSIX walk**

In `dirtywork/budget.py`, replace `_measure_posix` entirely.

Before:

```python
def _measure_posix(worktree: Path, max_bytes: int, max_files: int) -> BudgetReport:
    root = str(worktree)
    total_bytes = 0
    total_files = 0
    escaping: list = []

    def _onerror(err: OSError) -> None:
        raise _UnreadableDir(err.filename or str(err))

    try:
        for dirpath, dirnames, filenames, dirfd in os.fwalk(
            root, onerror=_onerror, follow_symlinks=False
        ):
            for name in dirnames + filenames:
                try:
                    st = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
                except OSError as e:
                    raise _UnreadableDir(e.filename or str(e))
                total_files += 1
                total_bytes += (
                    st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size
                )
                if stat.S_ISLNK(st.st_mode):
                    target = os.readlink(name, dir_fd=dirfd)
                    if _is_escaping(dirpath, target, root):
                        rel = os.path.relpath(os.path.join(dirpath, name), root)
                        escaping.append(rel)
                if total_bytes > max_bytes:
                    return BudgetReport(
                        total_bytes, total_files, escaping,
                        f"worktree exceeds {max_bytes // (1024 * 1024)} MB",
                    )
                if total_files > max_files:
                    return BudgetReport(
                        total_bytes, total_files, escaping,
                        f"worktree exceeds {max_files} entries",
                    )
    except _UnreadableDir as e:
        return BudgetReport(total_bytes, total_files, escaping,
                             f"unreadable directory: {e.path}")

    return BudgetReport(total_bytes, total_files, escaping, None)
```

After:

```python
def _measure_posix(worktree: Path, max_bytes: int, max_files: int,
                   sweep_temps: bool = False) -> BudgetReport:
    """`sweep_temps` (spec §2.5) removes a leftover `.dw-tmp.<name>.<8 hex>`
    staging file as this walk passes it, rather than counting it. Folded in
    here so the sweep costs NO second traversal: HostSandbox already walks the
    worktree at start and again at finalize. A swept entry is not counted
    toward `files`/`bytes` -- it is about to stop existing."""
    root = str(worktree)
    total_bytes = 0
    total_files = 0
    swept = 0
    escaping: list = []

    def _onerror(err: OSError) -> None:
        raise _UnreadableDir(err.filename or str(err))

    try:
        for dirpath, dirnames, filenames, dirfd in os.fwalk(
            root, onerror=_onerror, follow_symlinks=False
        ):
            for name in dirnames + filenames:
                try:
                    st = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
                except OSError as e:
                    raise _UnreadableDir(e.filename or str(e))
                if sweep_temps and stat.S_ISREG(st.st_mode) and is_temp_name(name):
                    try:
                        os.unlink(name, dir_fd=dirfd)
                    except OSError:
                        pass          # still there next walk; never fail a run for it
                    else:
                        swept += 1
                        continue
                total_files += 1
                total_bytes += (
                    st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size
                )
                if stat.S_ISLNK(st.st_mode):
                    target = os.readlink(name, dir_fd=dirfd)
                    if _is_escaping(dirpath, target, root):
                        rel = os.path.relpath(os.path.join(dirpath, name), root)
                        escaping.append(rel)
                if total_bytes > max_bytes:
                    return BudgetReport(
                        total_bytes, total_files, escaping,
                        f"worktree exceeds {max_bytes // (1024 * 1024)} MB", swept,
                    )
                if total_files > max_files:
                    return BudgetReport(
                        total_bytes, total_files, escaping,
                        f"worktree exceeds {max_files} entries", swept,
                    )
    except _UnreadableDir as e:
        return BudgetReport(total_bytes, total_files, escaping,
                             f"unreadable directory: {e.path}", swept)

    return BudgetReport(total_bytes, total_files, escaping, None, swept)
```

- [ ] **Step 7: Thread the flag through the two entry points**

In `dirtywork/budget.py`.

Before:

```python
def _measure_windows(worktree: Path, max_bytes: int, max_files: int) -> BudgetReport:
    # Best-effort; not exercised by this (POSIX-developed) test suite. `\\?\`
```

After:

```python
def _measure_windows(worktree: Path, max_bytes: int, max_files: int,
                     sweep_temps: bool = False) -> BudgetReport:
    # `sweep_temps` is accepted and IGNORED here: Windows is unsupported (see
    # README's platform table), the sweep is a POSIX dir_fd unlink, and a
    # silently skipped sweep only means a leftover temp is reported as an
    # ordinary file rather than removed. Stated, not hidden.
    # Best-effort; not exercised by this (POSIX-developed) test suite. `\\?\`
```

Before:

```python
def measure_worktree(worktree: Path, *, max_bytes: int, max_files: int) -> BudgetReport:
    if os.name == "nt":
        return _measure_windows(worktree, max_bytes, max_files)
    return _measure_posix(worktree, max_bytes, max_files)
```

After:

```python
def measure_worktree(worktree: Path, *, max_bytes: int, max_files: int,
                     sweep_temps: bool = False) -> BudgetReport:
    if os.name == "nt":
        return _measure_windows(worktree, max_bytes, max_files, sweep_temps)
    return _measure_posix(worktree, max_bytes, max_files, sweep_temps)
```

- [ ] **Step 8: Sweep at host start and finalize**

In `dirtywork/sandbox/host.py`.

Before:

```python
from __future__ import annotations

from pathlib import Path

from .. import tools
from ..budget import (
    DEFAULT_MAX_WORKTREE_FILES,
    DEFAULT_MAX_WORKTREE_MB,
    BudgetExceeded,
    measure_worktree,
)
```

After:

```python
from __future__ import annotations

import sys
from pathlib import Path

from .. import tools
from ..budget import (
    DEFAULT_MAX_WORKTREE_FILES,
    DEFAULT_MAX_WORKTREE_MB,
    BudgetExceeded,
    BudgetReport,
    measure_worktree,
)
```

Before:

```python
    def start(self, worktree: Path, repo: Path, slug: str, base_commit: str, *, branch: str | None = None, seed_from_worktree: bool = False) -> None:
        self.worktree = worktree  # host mode: no container to create
        self.base_commit = base_commit
        self.repo = repo
        self.slug = slug

    def _measure(self) -> dict:
        return measure_worktree(self.worktree, max_bytes=self.max_worktree_mb * 1024 * 1024,
                                   max_files=self.max_worktree_files)
```

After:

```python
    def start(self, worktree: Path, repo: Path, slug: str, base_commit: str, *, branch: str | None = None, seed_from_worktree: bool = False) -> None:
        self.worktree = worktree  # host mode: no container to create
        self.base_commit = base_commit
        self.repo = repo
        self.slug = slug
        # Spec §2.5: every completed write unlinks its own staging temp, so the
        # only way one survives is a kill -- which means a RESUMED run's
        # worktree is where it turns up. One sweep, here, folded into a
        # measurement walk that costs what any budget check costs.
        self._sweep_note(self._measure(sweep_temps=True))

    def _measure(self, *, sweep_temps: bool = False) -> BudgetReport:
        return measure_worktree(self.worktree, max_bytes=self.max_worktree_mb * 1024 * 1024,
                                   max_files=self.max_worktree_files,
                                   sweep_temps=sweep_temps)

    def _sweep_note(self, report: BudgetReport) -> None:
        """Spec §2.5: a swept temp is evidence a previous run was killed
        mid-write. Worth one stderr line; never silent."""
        if report.swept:
            plural = "" if report.swept == 1 else "s"
            print(f"swept {report.swept} stale temp file{plural}", file=sys.stderr)
```

Before:

```python
        report = self._measure()
        files_changed, files_changed_truncated = host_files_changed(
            self.worktree, self.base_commit)
```

After:

```python
        # Spec §2.5: swept BEFORE host_files_changed, so a temp left by a kill
        # during this very run can never appear in the run's evidence.
        report = self._measure(sweep_temps=True)
        self._sweep_note(report)
        files_changed, files_changed_truncated = host_files_changed(
            self.worktree, self.base_commit)
```

- [ ] **Step 9: Sweep in the container before `git add -A`**

In `dirtywork/sandbox/export.py`.

Before:

```python
from ..workspace import MAX_FILES_CHANGED
from . import RunArtifacts, SandboxError, docker_args, docker_cli, lifecycle
```

After:

```python
from ..tools import TMP_FIND_REGEX
from ..workspace import MAX_FILES_CHANGED
from . import RunArtifacts, SandboxError, docker_args, docker_cli, lifecycle
```

Before:

```python
        add_argv = docker_args.exec_argv(name, ["/usr/bin/git", "add", "-A"])
```

After:

```python
        # Spec §2.5: remove any staging temp a kill left behind, immediately
        # before the index is built, so `.dw-tmp.…` can never land in
        # files_changed/diff.patch. One exec, anchored on the full generated
        # shape (`find -regex` matches the WHOLE path), `-type f` so a
        # similarly-named directory is never touched, `-print` so the count is
        # reportable. A failure here is not fatal: an unswept temp is untidy,
        # not wrong.
        sweep_argv = docker_args.exec_argv(
            name, ["/usr/bin/find", "/work", "-type", "f", "-regextype", "posix-extended",
                   "-regex", TMP_FIND_REGEX, "-print", "-delete"])
        sweep_captured = run(sweep_argv, timeout=docker_cli.T_EXPORT_STEP)
        if sweep_captured.returncode == 0:
            swept = [line for line
                     in sweep_captured.output.decode("utf-8", errors="replace").splitlines()
                     if line.strip()]
            if swept:
                plural = "" if len(swept) == 1 else "s"
                print(f"swept {len(swept)} stale temp file{plural}", file=sys.stderr)

        add_argv = docker_args.exec_argv(name, ["/usr/bin/git", "add", "-A"])
```

- [ ] **Step 10: Update the write-semantics docs (spec §2.3, §2.4)**

In `docs/machine-contract.md`.

Before:

```
container. "Nothing was written" covers every failure **before** the write
begins; a failure *during* the write (I/O error, kill) can still leave a
truncated file — see `docs/operating.md`.
```

After:

```
container. Since 0.10 "nothing was written" also covers a failure **during**
the write: every host write and every container write is staged in a sibling
temp file and promoted with an atomic rename, so an I/O error or a kill leaves
the target byte-identical. Two branches keep the old in-place behaviour and are
named here rather than hidden: a target with more than one hard link (a
hardlink is *meant* to see the write through the shared inode) and a target in
a directory the process cannot write (a rename is impossible there). The
promote changes the file's inode, so a worker process holding the old file open
keeps reading the old content until it re-opens.
```

In `docs/operating.md`.

Before:

```
> **In-place edits are atomic *before* the write, not *through* it.** Every
> refusal — validation, a non-matching `old`, an unreadable or non-UTF-8 file,
> a result over the 5 MB write limit — happens before the file is opened, so
> the file is untouched. Once the write starts, an I/O error or a kill can
> still leave the file truncated; that is true of `edit_file`, `insert_*` and
> `write_file` too and is unchanged in 0.9. The worktree is a scratch branch,
> so the recovery is `git -C <worktree> checkout -- <path>`. A temp-file/rename
> primitive was considered and deferred: done naively it re-opens the
> final-component symlink race that the current `O_NOFOLLOW` write closes.
```

After:

```
> **File writes are atomic as of 0.10.** Every refusal — validation, a
> non-matching `old`, an unreadable or non-UTF-8 file, a result over the 5 MB
> write limit — still happens before the file is opened. And now the write
> itself is staged: `write_file`, `append_file`, `edit_file`, `apply_edits` and
> `insert_*` write into a sibling `.dw-tmp.<name>.<8 hex>` file and promote it
> with an atomic rename, so an I/O error or a kill mid-write leaves the target
> byte-identical instead of truncated. The file's mode is carried across the
> promote (an executable stays executable). Two exceptions keep the old
> behaviour on purpose: a target with more than one hard link is written
> through the shared inode, because that is what a hardlink is *for*; and a
> target in a directory dirtywork cannot write is written in place, because a
> rename is impossible there. The promote changes the inode, so a background
> process the worker left holding the file open keeps seeing the old content.
> Recovery for a genuinely bad write is still `git -C <worktree> checkout --
> <path>` — the worktree is a scratch branch.
```

- [ ] **Step 11: Update `docs/security.md` for the §2.4 race delta**

In `docs/security.md`.

Before:

```
- File tools refuse to operate on anything that isn't a regular file (FIFOs,
  devices, sockets) and refuse to write through a symlink at the final path
  component, even when its target is inside the worktree. `write_file`
```

After:

```
- File tools refuse to operate on anything that isn't a regular file (FIFOs,
  devices, sockets) and refuse to write through a symlink at the final path
  component, even when its target is inside the worktree. As of 0.10 a write is
  staged in a sibling temp and promoted with `rename(2)`; a symlink present at
  call time refuses exactly as before, and one that appears in the gap between
  the check and the promote gets **replaced as a link** — `rename(2)` does not
  follow its destination, so nothing is ever written through it. That is a
  robustness change, not a security change: host tool calls are serial, every
  `bash` call SIGKILLs its process group when it returns, and the realistic
  adversary here is a confused or prompt-injected model, not a racing process.
  `write_file`
```

- [ ] **Step 12: Run the affected modules and see them pass**

Run: `/usr/bin/python3 -m pytest tests/test_tools_files.py tests/test_budget.py tests/test_sandbox_host.py tests/test_export_flow.py -q`
Expected: exit code 0.

- [ ] **Step 13: Run the full suite**

Run: `/usr/bin/python3 -m pytest -q`
Expected: exit code 0; `1091 passed, 1 skipped, 18 deselected` (1081 + 10).

- [ ] **Step 14: Commit**

```bash
git add dirtywork/tools.py dirtywork/budget.py dirtywork/sandbox/host.py \
        dirtywork/sandbox/export.py tests/test_tools_files.py tests/test_budget.py \
        tests/test_sandbox_host.py tests/test_export_flow.py docs/machine-contract.md \
        docs/operating.md docs/security.md
git commit -m "feat: stage every write through _write_atomic, and sweep stale temps"
```

---

**Task 6 amendment (executed 2026-08-23):** review found a BLOCKER in Step 9's location — the sweep exec ran in the EXPORT container, whose `/work` volume mount is readonly by design (`docker_args.export_create_argv`, pinned by its own tests). `find … -delete` gets `EROFS` on every match and exits non-zero, so Step 9's `sweep_captured.returncode == 0` gate silently suppressed the note and `git add -A` went on to stage any `.dw-tmp.…` straight into the export — a guaranteed silent no-op, not the fix §2.5 asked for. Fix round 1 (one commit, `fix(sweep): run the docker temp sweep in the worker container (export /work is readonly); TMP_FIND_REGEX is per-component (spec/plan amended)`):

1. Removed the sweep exec from `dirtywork/sandbox/export.py` entirely (reverting Step 9's `export.py` diff).
2. Added it to `dirtywork/sandbox/docker.py`'s `DockerSandbox.finalize()`, one exec against the still-alive WORKER container (`self.container`, not the export container) immediately before `self._stop_container()` — ahead of export starting at all. Same find argv, built with `docker_args.exec_argv` (`-w /work` default).
3. Reporting is no longer silent on a partial failure: the swept-N note now fires whenever the sweep printed any lines, **regardless of exit code**, and a non-zero rc additionally notes `sweep incomplete (rc N)` to stderr — so an `EROFS` (or any other mid-sweep error) is never mistaken for "nothing to sweep".
4. `TMP_FIND_REGEX` itself over-matched: `find -regex` matches the WHOLE path and POSIX ERE `.` crosses `/`, so the original `.+` basename component could match INTO a `.dw-tmp.`-named DIRECTORY and delete a worker's own file underneath it (e.g. `/work/.dw-tmp.build.1234abcd/out/asset.deadbeef`) — a case `is_temp_name`'s name-only match never had, since a single path component can never contain `/`. Tightened to `[^/]+`; one new pinning test (`test_tmp_find_regex_is_per_component_not_greedy_across_a_slash`, `tests/test_tools_files.py`) fixtures `re.fullmatch` against two true positives and three negatives, confirmed red against the old pattern before the fix.
5. `tests/test_export_flow.py`'s `test_export_sweeps_stale_temps_before_git_add` renamed to `test_export_run_never_execs_a_sweep_the_export_volume_is_readonly` and rewritten to assert the negative (`export_run`'s own exec stream never contains a `-regextype` call); `tests/test_docker_sandbox.py`'s `test_finalize_stops_container_calls_export_run_and_host_read_tree` extended in place (no new test function — this is why the net test-count delta is +1, not +2) with assertions that the sweep exec appears in the WORKER container's exec stream and precedes the `rm -f` that stops it.
6. `docs/machine-contract.md`'s §2.3 paragraph now scopes "Two branches (host mode only) keep the old in-place behaviour" — the docker-mode delta (no fd fallback) sits in the sentences immediately after it.

Net new tests this round: 1 (the regex-pin test) — Task 6's `1091 passed` becomes `1092 passed, 1 skipped, 18 deselected` after this fix round; every gate-count number inside Task 6's own step list above is left as originally executed and not restated here.

### Task 7: `--max-tokens` and `finish_reason` on the transcript (spec §1.4, §1.5)

**Stated consequence, carried into the docs:** resuming a 0.9 run silently moves the effective output cap from the adapters' 4096 to 8192. That is deliberate (a 0.9 `run.json` has no `max_tokens` to inherit) and the resume's own transcript records the value actually used.

**Second stated consequence, inside the suite:** an 8192-token default cap and a flat `--max-tokens >= context_window` refusal make every shipped test that ran on a sub-8192 window an exit-2 preflight failure. Seven `tests/test_main.py` tests are in that set and Step 6 breaks all seven simultaneously; **Step 7 repairs them and must be done in the same commit as Step 6**, or this task cannot reach its own gate. The refusal itself is not negotiable — spec §1.4's rule has no small-window exemption.

**Files:**
- Modify: `dirtywork/runner.py` (`DEFAULT_MAX_TOKENS` beside `DEFAULT_WINDOW` at `:31`; `Runner.__init__` `:392-431`; the `run_start` write `:445-449`; the `provider.chat` call `:589-592`; the `assistant` write `:627-630`)
- Modify: `dirtywork/__main__.py` (the `.runner` import `:29-37`; `_resolve_context_window` `:196-210`; `_write_run_json_start` `:417-447`; `_load_resume_target` `:659-711`; the `Runner(...)` construction `:793-810`; `_add_run_flags` `:876-921`)
- Modify: `tests/test_runner.py` (5 new tests; `test_runner_context_window_param_sets_budget_and_run_start` at `:873-883`; the comment at `:451-454`)
- Modify: `tests/test_main.py` (4 new tests, **plus seven shipped tests whose small windows the new preflight refusal breaks — Step 7**)
- Modify: `tests/test_transcript_schema.py` (`ASSISTANT_FIELDS` + 1 new test)
- Modify: `docs/machine-contract.md` (the flag block `:20-37`, a `--max-tokens` bullet in the flag list)
- Modify: `docs/operating.md` (a cap + decode-cost paragraph in the context-sizing section)
- Modify: `docs/transcript-schema.md` (`run_start` table `:37-38`, `assistant` table `:58-61`, `run.json` table `:209`)

**Interfaces:**
- Consumes: `runner.BUDGET_FRACTION`, `runner.CHARS_PER_TOKEN`, `runner.DEFAULT_WINDOW`; `__main__._positive_int`; `__main__.PreflightFailure`.
- Produces:
  - `runner.DEFAULT_MAX_TOKENS: int` (`8192`)
  - `Runner.__init__(..., max_tokens: int = DEFAULT_MAX_TOKENS, ...)` → `self.max_tokens: int`
  - `run_start` event key `max_tokens: int`; `run.json` key `max_tokens: int`
  - `assistant` event key `finish_reason: str | None`
  - CLI flag `--max-tokens` on both `run` and `resume`; `args.max_tokens: int | None`

- [ ] **Step 1: Write the failing runner tests**

Append to `tests/test_runner.py`:

```python
# --- spec §1.4/§1.5: the output cap and the recorded finish reason.


def test_max_tokens_defaults_to_8192_and_reaches_the_provider(parts):
    from dirtywork.runner import DEFAULT_MAX_TOKENS
    wt, registry, sandbox, transcript, tmp = parts

    seen = {}

    class _RecordingProvider(FakeProvider):
        def chat(self, model, history, tools, *, temperature=None, max_tokens=4096,
                 timeout=None):
            seen["max_tokens"] = max_tokens
            return super().chat(model, history, tools, temperature=temperature,
                                max_tokens=max_tokens, timeout=timeout)

    r = Runner(_RecordingProvider([_resp(content="done")]), registry, sandbox, transcript,
               model="m")
    r.run("s", "t")
    transcript.close()
    assert DEFAULT_MAX_TOKENS == 8192
    assert seen["max_tokens"] == 8192


def test_char_budget_subtracts_max_tokens_from_the_window(parts):
    # Spec §1.4: the window is SHARED. Budgeting the prompt as if the whole
    # window were available is what made a long reply run off the end.
    wt, registry, sandbox, transcript, tmp = parts
    from dirtywork.runner import BUDGET_FRACTION, CHARS_PER_TOKEN
    r = Runner(FakeProvider([_resp(content="done")]), registry, sandbox, transcript,
               model="m", context_window=32768, max_tokens=8192)
    assert r.char_budget == int((32768 - 8192) * BUDGET_FRACTION * CHARS_PER_TOKEN)
    # A cap larger than the window cannot go negative here (preflight refuses
    # that combination; a directly-built Runner must still not explode).
    r2 = Runner(FakeProvider([_resp(content="done")]), registry, sandbox, transcript,
                model="m", context_window=1000, max_tokens=8192)
    assert r2.char_budget == 0


def test_run_start_records_max_tokens(parts):
    wt, registry, sandbox, transcript, tmp = parts
    r = Runner(FakeProvider([_resp(content="done")]), registry, sandbox, transcript,
               model="m", max_tokens=1234)
    r.run("s", "t")
    transcript.close()
    start = next(e for e in _events(tmp) if e["event"] == "run_start")
    assert start["max_tokens"] == 1234


def test_assistant_event_records_finish_reason(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("c1", "list_dir", {"path": "."})],
              finish_reason="tool_calls"),
        _resp(content="done", finish_reason="stop"),
    ])
    Runner(provider, registry, sandbox, transcript, model="m").run("s", "t")
    transcript.close()
    reasons = [e["finish_reason"] for e in _events(tmp) if e["event"] == "assistant"]
    assert reasons == ["tool_calls", "stop"]


def test_assistant_event_finish_reason_is_null_for_a_non_string(parts):
    # Adapters do not guarantee a string -- the Anthropic adapter passes an
    # unknown stop reason through raw -- so anything non-str is recorded as
    # null rather than emitted as some other JSON type (spec §1.5).
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="done", finish_reason=17)])
    Runner(provider, registry, sandbox, transcript, model="m").run("s", "t")
    transcript.close()
    assistant = next(e for e in _events(tmp) if e["event"] == "assistant")
    assert assistant["finish_reason"] is None
```

Update the existing budget test.

Before:

```python
def test_runner_context_window_param_sets_budget_and_run_start(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="unknown/model", context_window=1000)
    assert r.context_window == 1000
    assert r.char_budget == int(1000 * 0.75 * 4)
```

After:

```python
def test_runner_context_window_param_sets_budget_and_run_start(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="unknown/model",
               context_window=1000, max_tokens=200)
    assert r.context_window == 1000
    # Spec §1.4: the prompt budget is what is left AFTER the output cap.
    assert r.char_budget == int((1000 - 200) * 0.75 * 4)
```

Update the stale comment on the transcript-cap test.

Before:

```python
    # Over MAX_ASSISTANT_TEXT_CHARS (64_000) but comfortably under the
    # default model's char_budget (~98_304 for the fallback DEFAULT_WINDOW),
    # so trim_messages doesn't ALSO trigger context_exhausted — this test is
    # about the transcript-only cap, not the trim path.
```

After:

```python
    # Over MAX_ASSISTANT_TEXT_CHARS (64_000) but under the default model's
    # char_budget, which since 0.10 is (32768 - 8192) * 0.75 * 4 = 73_728 for
    # the fallback DEFAULT_WINDOW and the default --max-tokens. The whole
    # history here is ~70_050 chars, so trim_messages doesn't ALSO trigger
    # context_exhausted — this test is about the transcript-only cap, not the
    # trim path.
```

- [ ] **Step 2: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_runner.py -q -k "max_tokens or char_budget_subtracts or assistant_event or context_window_param"`
Expected: exactly 6 failures — `ImportError: cannot import name 'DEFAULT_MAX_TOKENS' from 'dirtywork.runner'` (`test_max_tokens_defaults_to_8192_and_reaches_the_provider`), `TypeError: __init__() got an unexpected keyword argument 'max_tokens'` (`test_char_budget_subtracts_max_tokens_from_the_window`, `test_run_start_records_max_tokens`, `test_runner_context_window_param_sets_budget_and_run_start`), and `KeyError: 'finish_reason'` (`test_assistant_event_records_finish_reason`, `test_assistant_event_finish_reason_is_null_for_a_non_string`). The `-k` expression deliberately says `assistant_event`, not `finish_reason`, so it does not also select the shipped `test_length_finish_reason_gives_helpful_hint`.

- [ ] **Step 3: Add the constant and thread it through the runner**

In `dirtywork/runner.py`.

Before:

```python
DEFAULT_WINDOW = 32768
```

After:

```python
DEFAULT_WINDOW = 32768
# Spec §1.4: the per-reply output cap. Both adapters default to 4096 for direct
# callers; the runner now always passes this explicitly, so a large write_file
# has room to finish instead of being cut off mid-JSON.
DEFAULT_MAX_TOKENS = 8192
```

Before:

```python
                 max_turns: int = 40, timeout: int = 1800,
                 temperature: float | None = None,
```

After:

```python
                 max_turns: int = 40, timeout: int = 1800,
                 max_tokens: int = DEFAULT_MAX_TOKENS,
                 temperature: float | None = None,
```

Before:

```python
        self.max_turns = max_turns
        self.timeout = timeout
        self.temperature = temperature
```

After:

```python
        self.max_turns = max_turns
        self.timeout = timeout
        # Spec §1.4: passed explicitly on every chat call. The adapters keep
        # their own 4096 defaults for direct callers.
        self.max_tokens = max_tokens
        self.temperature = temperature
```

Before:

```python
        self.char_budget = int(self.context_window * BUDGET_FRACTION * CHARS_PER_TOKEN)
```

After:

```python
        # Spec §1.4: prompt and reply share ONE window, so the prompt budget is
        # what is left after the output cap. max(0, …) keeps a directly-built
        # Runner with a cap larger than its window from going negative;
        # preflight refuses that combination for a real run.
        self.char_budget = int(max(0, self.context_window - self.max_tokens)
                               * BUDGET_FRACTION * CHARS_PER_TOKEN)
```

Before:

```python
        self.transcript.write("run_start", task=task, model=self.model,
                              max_turns=self.max_turns, timeout=self.timeout,
                              context_window=self.context_window,
```

After:

```python
        self.transcript.write("run_start", task=task, model=self.model,
                              max_turns=self.max_turns, timeout=self.timeout,
                              max_tokens=self.max_tokens,
                              context_window=self.context_window,
```

Before:

```python
                    resp = self.provider.chat(self.model, messages, self.registry.schemas(),
                                              temperature=self.temperature,
                                              timeout=max(1.0, remaining))
```

After:

```python
                    resp = self.provider.chat(self.model, messages, self.registry.schemas(),
                                              temperature=self.temperature,
                                              max_tokens=self.max_tokens,
                                              timeout=max(1.0, remaining))
```

Before:

```python
                self.transcript.write(
                    "assistant", text=transcript_text,
                    tool_calls=[{"name": tc.name, "arguments": (tc.raw_arguments or "")[:2000]}
                                for tc in tool_calls])
```

After:

```python
                self.transcript.write(
                    "assistant", text=transcript_text,
                    tool_calls=[{"name": tc.name, "arguments": (tc.raw_arguments or "")[:2000]}
                                for tc in tool_calls],
                    # Spec §1.5: an OPEN enum. Adapters do not guarantee a
                    # string (Anthropic passes an unknown stop reason through
                    # raw), so anything else is recorded as null rather than
                    # emitted as some other JSON type.
                    finish_reason=finish_reason if isinstance(finish_reason, str) else None)
```

- [ ] **Step 4: Write the failing CLI tests**

Append to `tests/test_main.py`:

```python
def test_max_tokens_flag_is_recorded_in_run_json(tmp_path, monkeypatch, capsys):
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    assert m.main(["run", "--repo", str(repo), "--sandbox", "none",
                   "--context-window", "20000", "--max-tokens", "3000", "t"]) == 0
    assert _read_only_run_json(tmp_path)["max_tokens"] == 3000
    payload = json.loads(capsys.readouterr().out)
    events = [json.loads(line) for line
              in Path(payload["transcript"]).read_text().splitlines()]
    start = next(e for e in events if e["event"] == "run_start")
    assert start["max_tokens"] == 3000


def test_max_tokens_defaults_to_8192_on_a_fresh_run(tmp_path, monkeypatch, capsys):
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    assert m.main(["run", "--repo", str(repo), "--sandbox", "none", "t"]) == 0
    capsys.readouterr()
    assert _read_only_run_json(tmp_path)["max_tokens"] == 8192


def test_max_tokens_at_or_over_the_context_window_exits_2(tmp_path, monkeypatch, capsys):
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none",
                 "--context-window", "4096", "--max-tokens", "4096", "t"])
    assert rc == 2
    assert ("--max-tokens 4096 must be smaller than the 4096-token context window"
            in capsys.readouterr().err)
    assert not (tmp_path / "runs").exists()


def test_resume_inherits_max_tokens_and_a_pre_0_10_run_falls_back_to_the_default(
        tmp_path, monkeypatch, capsys):
    m, repo, rc = _first_run(monkeypatch, tmp_path, None)
    first = json.loads(capsys.readouterr().out)
    run_dir = Path(first["run_dir"])
    prior = json.loads((run_dir / "run.json").read_text())
    assert prior["max_tokens"] == 8192

    # A run recorded with an explicit cap is inherited verbatim.
    prior["max_tokens"] = 2000
    prior["status"] = "max_turns"
    (run_dir / "run.json").write_text(json.dumps(prior))
    patch_provider(monkeypatch, m, lambda base_url=None: _ScriptedClient(base_url))
    assert m.main(["resume", run_dir.name]) == 0
    second = json.loads(capsys.readouterr().out)
    assert json.loads((Path(second["run_dir"]) / "run.json").read_text())["max_tokens"] == 2000

    # A pre-0.10 run.json has no max_tokens at all: the new default applies.
    prior.pop("max_tokens")
    prior["status"] = "max_turns"
    (run_dir / "run.json").write_text(json.dumps(prior))
    patch_provider(monkeypatch, m, lambda base_url=None: _ScriptedClient(base_url))
    assert m.main(["resume", run_dir.name]) == 0
    third = json.loads(capsys.readouterr().out)
    assert json.loads((Path(third["run_dir"]) / "run.json").read_text())["max_tokens"] == 8192
```

Append to `tests/test_transcript_schema.py`, and add the field list beside `RUN_END_FIELDS`.

Before:

```python
RUN_END_FIELDS = ["diff_stat", "untracked", "patch_path", "escaping_symlinks",
```

After:

```python
ASSISTANT_FIELDS = ["text", "tool_calls", "finish_reason"]
RUN_END_FIELDS = ["diff_stat", "untracked", "patch_path", "escaping_symlinks",
```

```python
def test_doc_documents_every_assistant_field():
    tokens = _doc_tokens()
    for field in ASSISTANT_FIELDS:
        assert field in tokens, f"assistant field '{field}' is not documented in {DOC.name}"
```

- [ ] **Step 5: Run the CLI tests to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_main.py tests/test_transcript_schema.py -q -k "max_tokens or assistant_field"`
Expected: 5 failed — the `test_main.py` ones with `SystemExit: 2` / `error: unrecognized arguments: --max-tokens` or `KeyError: 'max_tokens'`, and `test_doc_documents_every_assistant_field` with `AssertionError: assistant field 'finish_reason' is not documented`.

- [ ] **Step 6: Add the flag and thread it through `__main__`**

In `dirtywork/__main__.py`.

Before:

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

After:

```python
from .runner import (
    CHARS_PER_TOKEN,
    DEFAULT_MAX_TOKENS,
    DEFAULT_STALL_TURNS,
    DEFAULT_STUCK_REPEATS,
    DEFAULT_VERIFY_ROUNDS,
    DEFAULT_VERIFY_TIMEOUT,
    Runner,
    resolve_context_window,
)
```

Before:

```python
    if source == "default":
        print(f"warning: no known context window for '{args.model}'; assuming {window} tokens "
              f"(set --context-window or DIRTYWORK_CONTEXT_WINDOW)", file=sys.stderr)
    return window, source
```

After:

```python
    if source == "default":
        print(f"warning: no known context window for '{args.model}'; assuming {window} tokens "
              f"(set --context-window or DIRTYWORK_CONTEXT_WINDOW)", file=sys.stderr)
    # Spec §1.4: prompt and reply share the window, so a cap at or above it
    # leaves no room for a prompt at all. Refused here, after the warning, so
    # an operator with an unknown model still sees which window was assumed.
    # Read with getattr: `runs`/`bench` build Namespaces without this attribute.
    max_tokens = getattr(args, "max_tokens", None)
    if max_tokens is None:
        max_tokens = DEFAULT_MAX_TOKENS
    if max_tokens >= window:
        raise PreflightFailure(
            f"--max-tokens {max_tokens} must be smaller than the {window}-token "
            f"context window")
    return window, source
```

Before:

```python
        "context_window": ctx.context_window,
        "context_window_source": ctx.context_window_source,
```

After:

```python
        "context_window": ctx.context_window,
        "context_window_source": ctx.context_window_source,
        "max_tokens": getattr(args, "max_tokens", DEFAULT_MAX_TOKENS),
```

Before:

```python
    if getattr(args, "verify_timeout", None) is None:
        args.verify_timeout = prior.get("verify_timeout", DEFAULT_VERIFY_TIMEOUT)
```

After:

```python
    if getattr(args, "verify_timeout", None) is None:
        args.verify_timeout = prior.get("verify_timeout", DEFAULT_VERIFY_TIMEOUT)
    if getattr(args, "max_tokens", None) is None:
        # Spec §1.4/§6: `.get(k) if … is not None else default` rather than
        # `.get(k, default)`, so a pre-0.10 run.json (no key) AND a
        # hand-edited one carrying an explicit null both land on the default.
        args.max_tokens = (prior.get("max_tokens") if prior.get("max_tokens") is not None
                           else DEFAULT_MAX_TOKENS)
```

Before:

```python
            max_turns=args.max_turns, timeout=args.timeout, temperature=args.temperature,
```

After:

```python
            max_turns=args.max_turns, timeout=args.timeout,
            max_tokens=getattr(args, "max_tokens", DEFAULT_MAX_TOKENS),
            temperature=args.temperature,
```

Before:

```python
    p.add_argument("--context-window", type=_positive_int, default=None,
                   help="model context window in tokens (default: the server's loaded window, "
                        "else the built-in table, else 32768)")
```

After:

```python
    p.add_argument("--context-window", type=_positive_int, default=None,
                   help="model context window in tokens (default: the server's loaded window, "
                        "else the built-in table, else 32768)")
    p.add_argument("--max-tokens", type=_positive_int,
                   default=None if resume else DEFAULT_MAX_TOKENS,
                   help="max tokens the model may generate per reply (default 8192; must be "
                        "smaller than the context window, and subtracted from it when the "
                        "prompt budget is computed; resume inherits this from the run it "
                        "continues)")
```

- [ ] **Step 7: Repair the seven shipped tests the new refusal breaks**

Step 6's preflight is flat, exactly as spec §1.4 requires: `max_tokens >= window` → exit 2, with no exemption for a test-sized window. `DEFAULT_MAX_TOKENS` is 8192, so **every shipped `tests/test_main.py` test that resolves a window below 8192 now exits 2** where it used to exit 0 or 1. There are seven of them, and Step 6 breaks all seven at once. Measured on this tree by applying Step 6's refusal alone and running `/usr/bin/python3 -m pytest -q`:

```
FAILED tests/test_main.py::test_run_json_records_task_model_context_window_and_turns
FAILED tests/test_main.py::test_context_window_env_and_unknown_model_warning
FAILED tests/test_main.py::test_task_size_warning_fires_for_a_long_brief
FAILED tests/test_main.py::test_task_size_warning_is_silent_under_the_threshold
FAILED tests/test_main.py::test_task_size_warning_fires_on_resume
FAILED tests/test_main.py::test_context_window_source_lands_in_run_json_run_start_and_stdout
FAILED tests/test_main.py::test_resume_records_its_own_context_window_source
7 failed, 1019 passed, 1 skipped, 18 deselected
```

Each stderr reads `error: --max-tokens 8192 must be smaller than the <W>-token context window`. Do **not** soften the refusal to make them pass — give each invocation an explicit `--max-tokens` that is strictly smaller than that site's window. Six of the seven need nothing else; the seventh's window is too small to host any reply worth capping and is scaled up instead.

(`tests/test_runner.py:876`'s `test_runner_context_window_param_sets_budget_and_run_start` is an eighth casualty of this task, via the new `char_budget` formula rather than preflight — Step 1 already rewrites it to `context_window=1000, max_tokens=200`. Nothing more to do there.)

In `tests/test_main.py`, seven edits:

**1. `:1310-1311`, `test_run_json_records_task_model_context_window_and_turns`** (window 5000).

Before:

```python
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "--context-window", "5000",
                 "--stall-turns", "7", "some task"])
```

After:

```python
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "--context-window", "5000",
                 "--max-tokens", "1000", "--stall-turns", "7", "some task"])
```

**2. `:1332`, `test_context_window_env_and_unknown_model_warning`** (window 4096, from `DIRTYWORK_CONTEXT_WINDOW`). Only the FIRST of that test's two `m.main` calls needs the flag — the second runs after `monkeypatch.delenv`, on the 32768 default.

Before:

```python
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "--model", "other/model", "t"])
    assert rc == 0
    assert _read_only_run_json(tmp_path)["context_window"] == 4096
```

After:

```python
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "--model", "other/model",
                 "--max-tokens", "1000", "t"])
    assert rc == 0
    assert _read_only_run_json(tmp_path)["context_window"] == 4096
```

**3. `:2100-2103`, `test_task_size_warning_fires_for_a_long_brief`** — the one that cannot be fixed with a flag alone. Its assertion pins a 4000-char brief at 50% of a 2000-token window, and the run must still finish `rc == 0`. Under the new shared-window budget that leaves `(2000 - max_tokens) * 0.75 * 4` chars for a prompt whose system block alone grew ~267 chars in Task 5; measured on this tree with Tasks 5 and 7 applied, only `--max-tokens 50` still completes, and `100`, `150`, `200`, `300`, `400` and `500` all end `context_exhausted`. A 2000-token window with an 8192-token default cap is not a configuration 0.10 can serve at all, so scale the whole scenario ×10 and keep the 50% relationship the assertion is actually about.

Before:

```python
    task = "x" * 4000                     # ~1000 tokens at 4 chars/token
    assert m.main(["run", "--repo", str(repo), "--sandbox", "none",
                   "--context-window", "2000", task]) == 0
    err = capsys.readouterr().err
    assert "warning: the task text is ~1000 tokens, 50% of the 2000-token context window" in err
```

After:

```python
    task = "x" * 40000                    # ~10000 tokens at 4 chars/token
    assert m.main(["run", "--repo", str(repo), "--sandbox", "none",
                   "--context-window", "20000", "--max-tokens", "1000", task]) == 0
    err = capsys.readouterr().err
    assert "warning: the task text is ~10000 tokens, 50% of the 20000-token context window" in err
```

**4. `:2111-2113`, `test_task_size_warning_is_silent_under_the_threshold`** (window 2000; the brief is 400 chars, so the budget is never in question).

Before:

```python
    task = "x" * 400                      # ~100 tokens = 5% of 2000
    assert m.main(["run", "--repo", str(repo), "--sandbox", "none",
                   "--context-window", "2000", task]) == 0
```

After:

```python
    task = "x" * 400                      # ~100 tokens = 5% of 2000
    assert m.main(["run", "--repo", str(repo), "--sandbox", "none",
                   "--context-window", "2000", "--max-tokens", "500", task]) == 0
```

**5. `:2127-2128`, `test_task_size_warning_fires_on_resume`** (resume, window 2000). Only the `resume` invocation carries `--context-window`; the `run` that seeds it uses the default window and needs nothing.

Before:

```python
    assert m.main(["resume", Path(prior["run_dir"]).name, "--context-window", "2000",
                   "--max-turns", "1"]) == 1
```

After:

```python
    assert m.main(["resume", Path(prior["run_dir"]).name, "--context-window", "2000",
                   "--max-tokens", "500", "--max-turns", "1"]) == 1
```

**6. `:2139-2140`, `test_context_window_source_lands_in_run_json_run_start_and_stdout`** (window 5000).

Before:

```python
    assert m.main(["run", "--repo", str(repo), "--sandbox", "none",
                   "--context-window", "5000", "t"]) == 0
```

After:

```python
    assert m.main(["run", "--repo", str(repo), "--sandbox", "none",
                   "--context-window", "5000", "--max-tokens", "1000", "t"]) == 0
```

**7. `:2166-2167`, `test_resume_records_its_own_context_window_source`** (resume, window 7000). Again only the `resume` invocation.

Before:

```python
    assert m.main(["resume", Path(prior["run_dir"]).name, "--context-window", "7000",
                   "--max-turns", "1"]) == 1
```

After:

```python
    assert m.main(["resume", Path(prior["run_dir"]).name, "--context-window", "7000",
                   "--max-tokens", "1000", "--max-turns", "1"]) == 1
```

None of the seven adds or removes a test, so no pass count moves. Edits 4 and 5 keep those two runs on exactly the outcome they already have on the shipped tree (`test_task_size_warning_fires_on_resume` ends `context_exhausted` today and still does — its `== 1` was never about `max_turns`, whatever the stale comment above it says; leave the comment alone, it is not this task's to fix).

- [ ] **Step 8: Run the affected modules and see them pass**

Run: `/usr/bin/python3 -m pytest tests/test_runner.py tests/test_main.py -q`
Expected: exit code 0.

- [ ] **Step 9: Document the flag and the two new fields**

In `docs/machine-contract.md`, add the flag to the `run` block.

Before:

```
    [--temperature <f>]               # omitted by default → server preset
```

After:

```
    [--temperature <f>]               # omitted by default → server preset
    [--max-tokens 8192]               # per-reply output cap; must be < the context window
```

Then add a bullet immediately **after** the `--context-window` bullet (which ends with `[Sizing the context window](operating.md#sizing-the-context-window).`) and immediately **before** the `- \`--allow-commit\`` bullet.

```
- `--max-tokens` (default 8192) — the per-reply output cap sent to the provider
  on every request, and subtracted from the context window before the prompt
  budget is computed (`(window - max_tokens) * 0.75 * 4` chars), so a long reply
  can no longer run off the end of a window the prompt already filled. Preflight
  refuses `--max-tokens` at or above the window with
  `--max-tokens <N> must be smaller than the <W>-token context window` (exit 2).
  Recorded on `run_start` and in `run.json`; **not** echoed on the stdout payload
  (it is configuration, not evidence). `dirtywork resume` inherits it; a run
  recorded before 0.10 has no value to inherit and gets the 8192 default, which
  raises its effective cap from the adapters' old 4096. Pass `--max-tokens 4096`
  for models that cap output there — some older Claude models reject a larger
  value outright.
```

In `docs/operating.md`, add a paragraph at the end of the `## Sizing the context window` section, immediately after the paragraph that ends `…so pass \`--context-window\` there.` and immediately before `**Rules of thumb**`:

```
The window is shared between the prompt and the reply, so `--max-tokens`
(default 8192) is subtracted from it before the prompt budget is computed:
`(window - max_tokens) * 0.75 * 4` characters. At the 32768 default that is
about 18.4k tokens' worth of prompt, versus the cap-blind 24.5k before 0.10 —
real slack instead of a reply that runs off the end. Raising `--max-tokens`
buys longer single replies at the cost of prompt room, and decode is the slow
half on a local model: a cap you never reach costs nothing, but a cap you do
reach costs seconds per turn. Lower it when a model rejects it (some older
Claude models cap output at 4096) or when you would rather spend the window on
context than on one long reply.
```

In `docs/transcript-schema.md`, add the `run_start` row.

Before:

```
| `timeout` | ✓ | ✓ | integer | seconds, whole-run wall clock |
```

After:

```
| `timeout` | ✓ | ✓ | integer | seconds, whole-run wall clock |
| `max_tokens` | | ✓ | integer | 0.10: `--max-tokens`, the per-reply output cap sent to the provider. Subtracted from `context_window` before the prompt budget is computed. Refused at preflight when it is not smaller than the window |
```

Add the `assistant` row.

Before:

```
| `tool_calls` | ✓ | ✓ | list | `[{name, arguments}, …]` — `arguments` is the model's own raw JSON argument string, capped at 2000 chars. Structurally invalid entries the provider could not address are **not** listed here; they appear as `tool_result` records with an empty `tool` |
```

After:

```
| `tool_calls` | ✓ | ✓ | list | `[{name, arguments}, …]` — `arguments` is the model's own raw JSON argument string, capped at 2000 chars. Structurally invalid entries the provider could not address are **not** listed here; they appear as `tool_result` records with an empty `tool` |
| `finish_reason` | | ✓ | string \| null | 0.10: the provider's own stop reason for this turn, passed through. Common values are `stop`, `length` and `tool_calls`, but this is **not a closed enum** — a provider may report anything, and a non-string value (the Anthropic adapter passes unknown stop reasons through raw) is recorded as `null` |
```

Add the `run.json` row.

Before:

```
| `context_window` | start | resolved tokens |
```

After:

```
| `context_window` | start | resolved tokens |
| `max_tokens` | start | 0.10: `--max-tokens`, the per-reply output cap. `dirtywork resume` inherits it; a `run.json` written before 0.10 has no such key, and the resume falls back to the 8192 default |
```

- [ ] **Step 10: Run the full suite**

Run: `/usr/bin/python3 -m pytest -q`
Expected: exit code 0; `1101 passed, 1 skipped, 18 deselected` (1091 + 10).

- [ ] **Step 11: Commit**

```bash
git add dirtywork/runner.py dirtywork/__main__.py tests/test_runner.py tests/test_main.py \
        tests/test_transcript_schema.py docs/machine-contract.md docs/operating.md \
        docs/transcript-schema.md
git commit -m "feat: --max-tokens (default 8192), cap-aware prompt budget, finish_reason on assistant events"
```

---

### Task 8: targeted recovery for a length-truncated tool call (spec §1.3)

**Files:**
- Modify: `dirtywork/runner.py` (`NUDGES["truncated"]` `:76-78`; new `_TRUNCATED_PATH_RE`/`TRUNCATED_ARGS_SCAN_CHARS`/`TRUNCATED_PATH_CHARS`/`_recovered_path`/`truncated_call_result` immediately after `_join_nudges` at `:86-88`; new `Runner._missing_required` immediately before `def run(` at `:440`; the `finish_reason == "length"` branch `:678-688`)
- Modify: `tests/test_runner.py` (`test_length_finish_reason_gives_helpful_hint` `:322-333` rewritten; 6 new tests)
- Modify: `docs/machine-contract.md` (the `append_file` bullet Task 5 added)

**Interfaces:**
- Consumes: `providers.ToolCall.raw_arguments: str` (already a neutral field, always a `str` — `""` on Anthropic, whose error branches never set it); `toolspec.ToolSpec.required: tuple`; `ToolRegistry.spec(name) -> ToolSpec | None`.
- Produces:
  - `runner.TRUNCATED_ARGS_SCAN_CHARS: int` (8192), `runner.TRUNCATED_PATH_CHARS: int` (200)
  - `runner._recovered_path(raw_arguments) -> str | None`
  - `runner.truncated_call_result(tool: str, raw_arguments) -> str`
  - `Runner._missing_required(self, name: str, args) -> bool`

- [ ] **Step 1: Write the failing tests**

In `tests/test_runner.py`, rewrite the existing truncation test.

Before:

```python
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
```

After:

```python
def test_length_finish_reason_gives_helpful_hint(parts):
    # Spec §1.3: the fixture already carries a recoverable `path`, so this is
    # now the PATH-RECOVERED case and pins the whole sentence.
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
    assert tool_msgs[0]["content"] == (
        "ERROR: your write_file for 'x' was cut off at the token limit — nothing was "
        "written. Write the file in chunks: write_file with the first part, then "
        "append_file for each following part.")
```

Append to `tests/test_runner.py`:

```python
_GENERIC_TRUNCATION = ("ERROR: your {tool} call was cut off at the token limit before it "
                       "completed. Emit smaller tool calls — for a large file, write_file "
                       "the first part and append_file the rest.")


def test_length_truncation_of_a_non_write_file_tool_gives_the_generic_form(parts):
    wt, registry, sandbox, transcript, tmp = parts
    truncated = _resp(tool_calls=[_bad_args("c", "edit_file", '{"path": "x", "old_string": "a')],
                      finish_reason="length",
                      usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([truncated, _resp(content="done")])
    Runner(provider, registry, sandbox, transcript, model="m").run("s", "t")
    transcript.close()
    tool_msgs = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert tool_msgs[0]["content"] == _GENERIC_TRUNCATION.format(tool="edit_file")


def test_length_truncation_with_no_raw_arguments_gives_the_generic_form(parts):
    # The Anthropic shape: its error branches never set raw_arguments, so path
    # recovery has nothing to scan and degrades to the generic sentence.
    wt, registry, sandbox, transcript, tmp = parts
    truncated = _resp(tool_calls=[_bad_args("c", "write_file", "")],
                      finish_reason="length",
                      usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([truncated, _resp(content="done")])
    Runner(provider, registry, sandbox, transcript, model="m").run("s", "t")
    transcript.close()
    tool_msgs = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert tool_msgs[0]["content"] == _GENERIC_TRUNCATION.format(tool="write_file")


def test_length_truncation_with_an_invalid_escape_degrades_to_generic(parts):
    # A raw fragment whose escape sequence is not valid JSON must not raise
    # inside the turn loop.
    wt, registry, sandbox, transcript, tmp = parts
    truncated = _resp(tool_calls=[_bad_args("c", "write_file", '{"path": "a\\qb", "content": "z')],
                      finish_reason="length",
                      usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([truncated, _resp(content="done")])
    Runner(provider, registry, sandbox, transcript, model="m").run("s", "t")
    transcript.close()
    tool_msgs = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert tool_msgs[0]["content"] == _GENERIC_TRUNCATION.format(tool="write_file")


def test_recovered_path_is_truncated_and_rendered_with_repr(parts):
    wt, registry, sandbox, transcript, tmp = parts
    long_path = "z" * 300
    raw = '{"path": "' + long_path + '", "content": "abc'
    truncated = _resp(tool_calls=[_bad_args("c", "write_file", raw)],
                      finish_reason="length",
                      usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([truncated, _resp(content="done")])
    Runner(provider, registry, sandbox, transcript, model="m").run("s", "t")
    transcript.close()
    tool_msgs = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert f"for {'z' * 200!r} was cut off" in tool_msgs[0]["content"]
    assert "z" * 201 not in tool_msgs[0]["content"]


def test_length_truncation_with_empty_args_counts_as_malformed_args_not_bad_args(parts):
    # Spec §1.3 case (b): a truncated Anthropic tool_use whose `input` came
    # back {} PARSES, so tc.error is None -- but a required parameter is
    # missing. It must be caught before dispatch and accounted as
    # malformed_args, so three of them abort on THAT kind rather than bad_args.
    wt, registry, sandbox, transcript, tmp = parts
    empty = _resp(tool_calls=[_call("c", "write_file", {})], finish_reason="length",
                  usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([empty, empty, empty])
    result = Runner(provider, registry, sandbox, transcript, model="m").run("s", "t")
    transcript.close()
    assert result.status == "model_error"
    assert result.final_message == "aborted after 3 consecutive malformed_args failures"
    results = [e["result"] for e in _events(tmp) if e["event"] == "tool_result"]
    assert results[0] == _GENERIC_TRUNCATION.format(tool="write_file")
    assert "bad arguments" not in results[0]


def test_truncated_nudge_names_write_file_and_append_file(parts):
    assert NUDGES["truncated"] == (
        "Your reply was cut off at the token limit. Continue with smaller steps — "
        "emit one tool call at a time; for a large file, write_file the first part "
        "and append_file the rest.")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_runner.py -q -k "length_finish_reason or length_truncation or recovered_path or truncated_nudge"`
Expected: 7 failed — six `AssertionError`s comparing against today's single generic sentence (`ERROR: your reply was cut off at the token limit before the tool call completed. …`) or, for the case-(b) test, against `ERROR: bad arguments for write_file: missing required parameter 'path'`, plus `test_truncated_nudge_names_write_file_and_append_file` on the old nudge text.

- [ ] **Step 3: Reword the text-side nudge**

In `dirtywork/runner.py`.

Before:

```python
    "truncated": ("Your reply was cut off at the token limit. Continue with smaller steps — "
                  "emit one tool call at a time and write large files in pieces."),
```

After:

```python
    "truncated": ("Your reply was cut off at the token limit. Continue with smaller steps — "
                  "emit one tool call at a time; for a large file, write_file the first "
                  "part and append_file the rest."),
```

- [ ] **Step 4: Add the recovery helpers**

In `dirtywork/runner.py`, immediately **after** `_join_nudges` (which ends with `return "\n\n".join(p for p in parts if p)`) and immediately **before** `def strip_think(`.

```python
# Spec §1.3. The fragment is the MODEL's own bytes and may be arbitrarily
# malformed, so it is scanned, never parsed: one bounded regex over the first
# TRUNCATED_ARGS_SCAN_CHARS characters, and the capture is unescaped through
# json.loads inside a try. Any failure degrades to the generic sentence.
_TRUNCATED_PATH_RE = re.compile(r'"path"\s*:\s*"((?:[^"\\]|\\.)*)"')
TRUNCATED_ARGS_SCAN_CHARS = 8192
TRUNCATED_PATH_CHARS = 200


def _recovered_path(raw_arguments):
    """The `path` a length-truncated tool call was building, or None.

    Returns at most TRUNCATED_PATH_CHARS characters: the value is
    model-controlled and goes straight into a tool result the model reads back,
    so a 40 KB "path" must not become a 40 KB error message. On Anthropic
    `raw_arguments` is "" (its error branches never set it) and this returns
    None, which is exactly the degradation spec §1.3 asks for."""
    if not isinstance(raw_arguments, str) or not raw_arguments:
        return None
    m = _TRUNCATED_PATH_RE.search(raw_arguments[:TRUNCATED_ARGS_SCAN_CHARS])
    if m is None:
        return None
    try:
        value = json.loads('"' + m.group(1) + '"')
    except ValueError:
        return None            # json.JSONDecodeError subclasses ValueError
    if not isinstance(value, str):
        return None
    return value[:TRUNCATED_PATH_CHARS]


def truncated_call_result(tool: str, raw_arguments) -> str:
    """Spec §1.3: the tool result a call cut off at the token limit gets.

    A write_file whose path can be recovered is told exactly which write to
    redo and how; anything else gets the generic form. Both name append_file,
    because before 0.10 "write it in pieces" was not honest advice for a NEW
    large file -- no tool could add to one."""
    if tool == "write_file":
        path = _recovered_path(raw_arguments)
        if path is not None:
            return (f"ERROR: your write_file for {path!r} was cut off at the token "
                    f"limit — nothing was written. Write the file in chunks: "
                    f"write_file with the first part, then append_file for each "
                    f"following part.")
    return (f"ERROR: your {tool} call was cut off at the token limit before it "
            f"completed. Emit smaller tool calls — for a large file, write_file "
            f"the first part and append_file the rest.")
```

- [ ] **Step 5: Add the case-(b) predicate**

In `dirtywork/runner.py`, immediately **before** `def run(self, system_prompt: str, task: str) -> RunResult:`.

```python
    def _missing_required(self, name: str, args) -> bool:
        """Spec §1.3 case (b): True when `args` parsed but is missing a
        required parameter of tool `name`. An unknown tool and a non-dict
        `args` are False -- those have their own accounting (`unknown_tool`,
        `bad_args`) and are not truncations."""
        spec = self.registry.spec(name)
        if spec is None or not isinstance(args, dict):
            return False
        return any(p not in args for p in spec.required)
```

- [ ] **Step 6: Make the length branch tool-aware**

In `dirtywork/runner.py`.

Before:

```python
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
```

After:

```python
                    if tc.error is not None:
                        abort_reason = failures.record("malformed_args")
                        if finish_reason == "length":
                            result = truncated_call_result(name, tc.raw_arguments)
                        else:
                            result = f"ERROR: {tc.error}"
                    elif finish_reason == "length" and self._missing_required(name, args):
                        # Spec §1.3 case (b): the Anthropic shape. A truncated
                        # tool_use whose `input` came back {} parses
                        # "successfully", so tc.error is None -- but a required
                        # parameter is simply absent. Checked BEFORE dispatch so
                        # the registry's bad_args path never swallows it: this
                        # is a truncation, not an argument mistake, and it is
                        # accounted as malformed_args exactly like case (a).
                        abort_reason = failures.record("malformed_args")
                        result = truncated_call_result(name, tc.raw_arguments)
                    else:
```

- [ ] **Step 7: Document the two recovery strings (spec §7 lists them as contract)**

In `docs/machine-contract.md`, extend the `append_file` bullet Task 5 added. Its
last sentence is currently:

```
  the large-file recipe: `write_file` the first part, `append_file` the rest.
```

Replace it with:

```
  the large-file recipe: `write_file` the first part, `append_file` the rest —
  and it is what a truncated call is told to do by name. When a tool call is cut
  off at the token limit (`finish_reason: "length"`), the harness answers it with
  `ERROR: your write_file for '<path>' was cut off at the token limit — nothing
  was written. Write the file in chunks: write_file with the first part, then
  append_file for each following part.` when the path can be recovered from the
  model's own argument fragment, and otherwise with `ERROR: your <tool> call was
  cut off at the token limit before it completed. Emit smaller tool calls — for a
  large file, write_file the first part and append_file the rest.` Either way the
  turn counts as a `malformed_args` failure, including the Anthropic shape where
  the truncated arguments parse as `{}` and a required parameter is simply
  missing.
```

- [ ] **Step 8: Run the module and see it pass**

Run: `/usr/bin/python3 -m pytest tests/test_runner.py -q`
Expected: exit code 0.

- [ ] **Step 9: Run the full suite**

Run: `/usr/bin/python3 -m pytest -q`
Expected: exit code 0; `1107 passed, 1 skipped, 18 deselected` (1101 + 6).

- [ ] **Step 10: Commit**

```bash
git add dirtywork/runner.py tests/test_runner.py docs/machine-contract.md
git commit -m "feat(runner): tool-aware recovery for a length-truncated tool call"
```

---

### Task 9: `--provider ollama` — a first-class adapter with a real loaded-context probe (spec §3)

**On the fixtures, stated plainly:** the eight files below are hand-built to Ollama's OpenAI-compatible response shape (tagged model ids, the extra `system_fingerprint` and `message.reasoning` fields our parser ignores). They assert **our parser**, not a live server — which is exactly what `tests/fixtures/providers/openai/` and `.../anthropic/` already do. What proves a real Ollama still matches is `tests/test_live_ollama.py`, which is marked `ollama` and deselected by default. If you have a live Ollama when you run this task, re-capture `simple_ok`, `finish_reason_stop`, `finish_reason_length_text` and `usage_missing` from it and confirm the committed files still parse to the same `ChatResponse`; the four hand-built ones (`parallel_tool_calls`, `malformed_tool_call`, `usage_nan_negative`, `bad_json_arguments`) stay hand-built either way, because a server will not produce a malformed body on demand. **Parallel tool calls are unverified on Ollama** — noted in the docs below, not claimed.

**Files:**
- Create: `dirtywork/providers/ollama.py`
- Create: `tests/fixtures/providers/ollama/{simple_ok,parallel_tool_calls,malformed_tool_call,finish_reason_stop,finish_reason_length_text,usage_missing,usage_nan_negative,bad_json_arguments}.json`
- Create: `tests/test_provider_ollama.py`
- Create: `tests/test_live_ollama.py`
- Modify: `dirtywork/providers/__init__.py` (`PROVIDER_NAMES` `:14`, `DEFAULT_BASE_URLS` `:16-19`, `get_provider` `:88-99`)
- Modify: `dirtywork/__main__.py` (`_ENDPOINT_HINTS` `:171-174`, new `_MODEL_HINTS`, `_preflight_llm` `:190-192`, the resume provider-switch comment `:673-679`)
- Modify: `pyproject.toml` (`[tool.pytest.ini_options]` markers + addopts)
- Modify: `tests/test_providers.py` (`test_provider_names_and_default_base_urls_agree` `:16-22`, `test_get_provider_explicit_empty_base_url_is_not_replaced_by_default` `:93-97`, 1 new test)
- Modify: `tests/test_main.py` (3 new tests)
- Modify: `README.md` (`:65-68`, `:84-86`, `:89-92`), `docs/machine-contract.md` (`:22`, `:106-109`), `docs/operating.md` (`:258-260`, a quickstart line)

**Interfaces:**
- Consumes: `providers.openai_compat.OpenAICompatClient`, `.LOADED_CONTEXT_PROBE_TIMEOUT`, `._origin`; `llm.LLMError`; `tests.provider_contract.ProviderContract`, `.RecordingTransport`.
- Produces:
  - `providers.ollama.OLLAMA_DEFAULT_BASE_URL: str` (`"http://localhost:11434/v1"`)
  - `providers.ollama.OllamaClient` with `name = "ollama"`, `__init__(base_url=OLLAMA_DEFAULT_BASE_URL, timeout=600, **kwargs)`, `context_window(model) -> None`, `loaded_context_window(model) -> int | None`
  - `providers.PROVIDER_NAMES == ("openai", "anthropic", "ollama")`; `DEFAULT_BASE_URLS["ollama"]`
  - `__main__._MODEL_HINTS: dict`, `__main__._DEFAULT_MODEL_HINT: str`
  - pytest marker `ollama`; `addopts = "-m 'not live and not docker and not ollama'"`

- [ ] **Step 1: Write the eight fixtures**

Create `tests/fixtures/providers/ollama/simple_ok.json`:

```json
{"id": "chatcmpl-511", "object": "chat.completion", "created": 1755900000, "model": "gemma4:latest", "system_fingerprint": "fp_ollama", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
```

Create `tests/fixtures/providers/ollama/parallel_tool_calls.json`:

```json
{
  "id": "chatcmpl-512",
  "object": "chat.completion",
  "created": 1755900001,
  "model": "gemma4:latest",
  "system_fingerprint": "fp_ollama",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant", "content": "",
      "tool_calls": [
        {"id": "call_ol1", "index": 0, "type": "function", "function": {"name": "read_file", "arguments": "{\"path\":\"a.txt\"}"}},
        {"id": "call_ol2", "index": 1, "type": "function", "function": {"name": "read_file", "arguments": "{\"path\":\"b.txt\"}"}}
      ]
    },
    "finish_reason": "tool_calls"
  }],
  "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}
}
```

Create `tests/fixtures/providers/ollama/malformed_tool_call.json`:

```json
{
  "id": "chatcmpl-513",
  "object": "chat.completion",
  "created": 1755900002,
  "model": "gemma4:latest",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant", "content": "",
      "tool_calls": [
        {"id": "call_ok", "index": 0, "type": "function", "function": {"name": "list_dir", "arguments": "{}"}},
        {"id": "call_bad", "index": 1, "type": "function"}
      ]
    },
    "finish_reason": "tool_calls"
  }],
  "usage": {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16}
}
```

Create `tests/fixtures/providers/ollama/finish_reason_stop.json`:

```json
{"id": "chatcmpl-514", "object": "chat.completion", "created": 1755900003, "model": "gemma4:latest", "choices": [{"index": 0, "message": {"role": "assistant", "content": "done", "reasoning": "the user asked for a short answer"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
```

Create `tests/fixtures/providers/ollama/finish_reason_length_text.json`:

```json
{"id": "chatcmpl-515", "object": "chat.completion", "created": 1755900004, "model": "gemma4:latest", "choices": [{"index": 0, "message": {"role": "assistant", "content": "cut off mid"}, "finish_reason": "length"}], "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}}
```

Create `tests/fixtures/providers/ollama/usage_missing.json`:

```json
{"id": "chatcmpl-516", "object": "chat.completion", "created": 1755900005, "model": "gemma4:latest", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
```

Create `tests/fixtures/providers/ollama/usage_nan_negative.json`:

```json
{"id": "chatcmpl-517", "object": "chat.completion", "created": 1755900006, "model": "gemma4:latest", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": NaN, "completion_tokens": -5, "total_tokens": 0}}
```

Create `tests/fixtures/providers/ollama/bad_json_arguments.json`:

```json
{
  "id": "chatcmpl-518",
  "object": "chat.completion",
  "created": 1755900007,
  "model": "gemma4:latest",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant", "content": "",
      "tool_calls": [
        {"id": "call_badargs", "index": 0, "type": "function", "function": {"name": "write_file", "arguments": "{\"path\": \"x\", \"content\": \"abc"}}
      ]
    },
    "finish_reason": "length"
  }],
  "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
}
```

- [ ] **Step 2: Write the failing provider tests**

Create `tests/test_provider_ollama.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from dirtywork.llm import LLMError, LLMTimeout
from dirtywork.providers import DEFAULT_BASE_URLS, PROVIDER_NAMES, get_provider
from dirtywork.providers.ollama import OLLAMA_DEFAULT_BASE_URL, OllamaClient
from dirtywork.providers.openai_compat import CONTEXT_WINDOWS, LOADED_CONTEXT_PROBE_TIMEOUT

from .provider_contract import ProviderContract, RecordingTransport

FIXTURES = Path(__file__).parent / "fixtures" / "providers" / "ollama"


def _client(transport, base_url=OLLAMA_DEFAULT_BASE_URL):
    return OllamaClient(base_url=base_url, http_json=transport)


class TestOllamaProviderContract(ProviderContract):
    """Ollama speaks the same wire format as the parent adapter; running the
    shared contract against ITS OWN fixtures is what keeps that claim honest
    instead of assumed."""

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


def test_name_and_default_base_url():
    assert OllamaClient.name == "ollama"
    assert OLLAMA_DEFAULT_BASE_URL == "http://localhost:11434/v1"
    assert "ollama" in PROVIDER_NAMES
    assert DEFAULT_BASE_URLS["ollama"] == OLLAMA_DEFAULT_BASE_URL
    assert get_provider("ollama").base_url == OLLAMA_DEFAULT_BASE_URL


def test_explicit_empty_base_url_is_not_replaced_by_the_ollama_default():
    # The subclass keeps the parent's None-only defaulting: an explicit "" is a
    # caller choice, not "unset".
    assert OllamaClient(base_url="").base_url == ""
    assert get_provider("ollama", "").base_url == ""


def test_context_window_is_always_none_not_lm_studios_table():
    # Spec §3.1: CONTEXT_WINDOWS is LM STUDIO's table. An Ollama user whose
    # model happens to share a name must not silently inherit its number under
    # the source `provider:ollama`.
    known = next(iter(CONTEXT_WINDOWS))
    assert CONTEXT_WINDOWS[known] > 0
    assert OllamaClient().context_window(known) is None
    assert OllamaClient().context_window("gemma4:latest") is None


def _ps_body(models):
    return {"models": models}


def test_loaded_context_window_returns_the_loaded_num_ctx():
    transport = RecordingTransport([_ps_body([
        {"name": "gemma4:latest", "model": "gemma4:latest", "size": 1, "context_length": 16384},
    ])])
    assert _client(transport).loaded_context_window("gemma4:latest") == 16384


def test_loaded_context_window_probes_api_ps_on_the_origin_with_the_short_timeout():
    transport = RecordingTransport([_ps_body([])])
    _client(transport, base_url="http://h:11434/prefix/v1").loaded_context_window("m")
    call = transport.calls[0]
    # /api/ps lives on the SERVER, not under the OpenAI-compatible /v1 prefix.
    assert call["url"] == "http://h:11434/api/ps"
    assert call["method"] == "GET"
    assert call["payload"] is None
    assert call["timeout"] == LOADED_CONTEXT_PROBE_TIMEOUT


def test_loaded_context_window_matches_the_model_key_only():
    # Ollama sets `model` and `name` to the same tagged id; matching both would
    # make one entry matchable twice (spec §3.1).
    transport = RecordingTransport([_ps_body([
        {"name": "gemma4:latest", "model": "other:latest", "context_length": 16384},
    ])])
    assert _client(transport).loaded_context_window("gemma4:latest") is None


def test_loaded_context_window_returns_none_on_the_first_match_without_scanning_on():
    # First match wins: a later, better-looking entry is NOT consulted.
    transport = RecordingTransport([_ps_body([
        {"model": "gemma4:latest", "context_length": None},
        {"model": "gemma4:latest", "context_length": 32768},
    ])])
    assert _client(transport).loaded_context_window("gemma4:latest") is None


def test_loaded_context_window_none_for_every_unusable_shape():
    cases = [
        _ps_body([]),                                                   # nothing resident
        _ps_body([{"model": "gemma4:latest"}]),                         # no context_length
        _ps_body([{"model": "gemma4:latest", "context_length": None}]),
        _ps_body([{"model": "gemma4:latest", "context_length": 0}]),
        _ps_body([{"model": "gemma4:latest", "context_length": -1}]),
        _ps_body([{"model": "gemma4:latest", "context_length": True}]),  # bool is not an int here
        _ps_body([{"model": "gemma4:latest", "context_length": "16384"}]),
        _ps_body([["gemma4:latest", 16384]]),                            # entry not an object
        {"models": "gemma4:latest"},                                     # models not a list
        {},                                                              # no models key
        [],                                                              # body not an object
    ]
    for body in cases:
        transport = RecordingTransport([body])
        assert _client(transport).loaded_context_window("gemma4:latest") is None, body


def test_loaded_context_window_none_when_unreachable():
    class _Boom:
        def __call__(self, url, payload, headers, timeout, *, method="POST"):
            raise LLMError("connection refused")

    class _Slow:
        def __call__(self, url, payload, headers, timeout, *, method="POST"):
            raise LLMTimeout("timed out")

    assert _client(_Boom()).loaded_context_window("gemma4:latest") is None
    assert _client(_Slow()).loaded_context_window("gemma4:latest") is None


def test_chat_payload_is_the_parents_shape():
    transport = RecordingTransport([json.loads((FIXTURES / "simple_ok.json").read_text())])
    _client(transport).chat("gemma4:latest", [{"role": "user", "content": "hi"}], [],
                            temperature=None, max_tokens=256, timeout=30)
    call = transport.calls[0]
    assert call["url"] == "http://localhost:11434/v1/chat/completions"
    assert call["payload"] == {"model": "gemma4:latest",
                               "messages": [{"role": "user", "content": "hi"}],
                               "max_tokens": 256}


def test_resolve_context_window_prefers_the_server_then_falls_to_default():
    from dirtywork.runner import DEFAULT_WINDOW, resolve_context_window
    loaded = _client(RecordingTransport([_ps_body([
        {"model": "gemma4:latest", "context_length": 16384}])]))
    assert resolve_context_window("gemma4:latest", None, None, loaded) == (
        16384, "provider:ollama:server")
    # Cold start (spec §3.1): /v1/models lists PULLED models, so preflight
    # passes for a model that is not resident; /api/ps then has no entry and
    # the window falls all the way to the default.
    cold = _client(RecordingTransport([_ps_body([])]))
    assert resolve_context_window("gemma4:latest", None, None, cold) == (
        DEFAULT_WINDOW, "default")
```

Create `tests/test_live_ollama.py`:

```python
"""Live smoke against a real Ollama server. Marked `ollama`, which
pyproject.toml's addopts deselects, AND skipped when no server answers -- so it
never runs by accident and never fails a normal suite. tests/test_live.py is
untouched: its module-level skipif probes LM Studio, a different server."""
from __future__ import annotations

import os

import pytest

from dirtywork.llm import LLMError
from dirtywork.providers import get_provider
from dirtywork.providers.ollama import OLLAMA_DEFAULT_BASE_URL

# Ollama model ids carry a tag; override for a machine with a different model.
MODEL = os.environ.get("DIRTYWORK_OLLAMA_MODEL", "gemma4:latest")

PROBE_TOOL = [{"type": "function", "function": {
    "name": "list_dir",
    "description": "List files in a directory",
    "parameters": {"type": "object",
                   "properties": {"path": {"type": "string"}},
                   "required": ["path"]}}}]


def _ollama_up() -> bool:
    try:
        get_provider("ollama", timeout=5).list_models()
        return True
    except LLMError:
        return False


pytestmark = [pytest.mark.ollama,
              pytest.mark.skipif(not _ollama_up(), reason="Ollama not running")]


def test_live_models_list_includes_the_tagged_id():
    models = get_provider("ollama").list_models()
    assert models, "Ollama reports no pulled models"
    assert all(isinstance(m, str) for m in models)
    assert MODEL in models, f"{MODEL} is not pulled; set DIRTYWORK_OLLAMA_MODEL"


def test_live_tool_call_and_loaded_window():
    client = get_provider("ollama")
    assert client.base_url == OLLAMA_DEFAULT_BASE_URL
    resp = client.chat(MODEL, [{"role": "user", "content": "What files are in src?"}],
                       tools=PROBE_TOOL, max_tokens=200, temperature=0)
    assert resp.tool_calls, f"{MODEL} returned no tool_calls: {resp.text!r:.200}"
    assert resp.tool_calls[0].name == "list_dir"
    assert resp.tool_calls[0].id
    assert resp.finish_reason in ("tool_calls", "stop", "length")
    # The model is resident now (the chat above loaded it), so /api/ps answers.
    window = client.loaded_context_window(MODEL)
    assert window is None or (isinstance(window, int) and window > 0)
```

- [ ] **Step 3: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_provider_ollama.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'dirtywork.providers.ollama'`.

- [ ] **Step 4: Write the adapter**

Create `dirtywork/providers/ollama.py`:

```python
from __future__ import annotations

from ..llm import LLMError
from .openai_compat import LOADED_CONTEXT_PROBE_TIMEOUT, OpenAICompatClient, _origin

# Ollama's OpenAI-compatible endpoint. The same string is DEFAULT_BASE_URLS's
# "ollama" entry -- duplicated the way openai_compat.DEFAULT_BASE_URL already
# is, so dirtywork.providers can keep importing its adapters lazily; a test
# pins the two together.
OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434/v1"


class OllamaClient(OpenAICompatClient):
    """Ollama as a first-class provider (spec §3.1).

    The wire format is the parent's for everything dirtywork uses: ids with a
    `:tag`, `tool_calls` with string `arguments` and ids, `finish_reason:
    "tool_calls"`, `role: "tool"` accepted, and an extra `message.reasoning`
    field the parent's parser already ignores. Exactly three things differ, and
    each is overridden below for a stated reason.

    Parallel tool calls are UNVERIFIED on Ollama: the fixtures assert our
    parser, not the server."""

    name = "ollama"

    def __init__(self, base_url: str = OLLAMA_DEFAULT_BASE_URL, timeout: int = 600,
                 **kwargs):
        """The only reason this override exists: the parent assigns
        `self.base_url` from ITS OWN default when `base_url` is None, so a
        class attribute could never change the default. `None` -> the Ollama
        default; an explicit "" is a caller choice and is passed through, same
        as the parent. Everything else -- the transport, the rstrip, the
        timeout -- is the parent's."""
        super().__init__(OLLAMA_DEFAULT_BASE_URL if base_url is None else base_url,
                         timeout, **kwargs)

    def context_window(self, model: str):
        """Always None. The parent's CONTEXT_WINDOWS is LM STUDIO's table,
        keyed by LM Studio's model ids; without this override an Ollama user
        running a same-named model would inherit LM Studio's number and see it
        recorded as `provider:ollama` -- a fabricated answer. Falling through
        to /api/ps, then to the default, is the honest behaviour."""
        return None

    def loaded_context_window(self, model: str):
        """The context length Ollama currently has `model` loaded with, or
        None. `GET {origin}/api/ps` lists ONLY resident models, so no `state`
        check is needed or possible; `context_length` there is the loaded
        `num_ctx` and moves when a chat sets `options.num_ctx`.

        Matching is on the `model` key ONLY: Ollama sets `model` and `name` to
        the same tagged id, so matching both would make one entry matchable
        twice. The FIRST match decides -- an entry that matches but cannot
        answer returns None rather than letting a later entry answer for it.

        Cold start, stated: Ollama's /v1/models lists PULLED models, so
        preflight passes for a model that is not resident. /api/ps then has no
        entry, the window falls to the default (32768), and Ollama loads its
        own smaller num_ctx -- silent server-side truncation, not a visible
        failure. `docs/operating.md` tells Ollama users to `ollama run <model>`
        first or pass --context-window.

        Same swallow-everything contract as the parent: connection error,
        timeout, non-2xx, non-JSON, wrong shape -- all None."""
        url = f"{_origin(self.base_url)}/api/ps"
        try:
            body = self._http_json(url, None, {"Content-Type": "application/json"},
                                   LOADED_CONTEXT_PROBE_TIMEOUT, method="GET")
        except LLMError:
            return None      # LLMTimeout is an LLMError: both mean "no answer"
        if not isinstance(body, dict) or not isinstance(body.get("models"), list):
            return None
        for entry in body["models"]:
            if not isinstance(entry, dict) or entry.get("model") != model:
                continue
            loaded = entry.get("context_length")
            if isinstance(loaded, int) and not isinstance(loaded, bool) and loaded > 0:
                return loaded
            return None
        return None
```

- [ ] **Step 5: Register the provider**

In `dirtywork/providers/__init__.py`.

Before:

```python
PROVIDER_NAMES = ("openai", "anthropic")

DEFAULT_BASE_URLS = {
    "openai": "http://localhost:1234/v1",
    "anthropic": "https://api.anthropic.com",
}
```

After:

```python
PROVIDER_NAMES = ("openai", "anthropic", "ollama")

DEFAULT_BASE_URLS = {
    "openai": "http://localhost:1234/v1",
    "anthropic": "https://api.anthropic.com",
    # Kept in step with providers.ollama.OLLAMA_DEFAULT_BASE_URL by a test, not
    # by an import: the adapters below are imported LAZILY on purpose.
    "ollama": "http://localhost:11434/v1",
}
```

Before:

```python
    if name == "anthropic":
        from .anthropic import AnthropicClient
        return AnthropicClient(base_url=url, timeout=timeout)
```

After:

```python
    if name == "anthropic":
        from .anthropic import AnthropicClient
        return AnthropicClient(base_url=url, timeout=timeout)
    if name == "ollama":
        from .ollama import OllamaClient
        return OllamaClient(base_url=url, timeout=timeout)
```

- [ ] **Step 6: Add the marker and deselect it by default**

In `pyproject.toml`.

Before:

```toml
[tool.pytest.ini_options]
markers = [
    "live: requires a running LM Studio server",
    "docker: requires a running Docker daemon",
]
addopts = "-m 'not live and not docker'"
```

After:

```toml
[tool.pytest.ini_options]
markers = [
    "live: requires a running LM Studio server",
    "docker: requires a running Docker daemon",
    "ollama: requires a running Ollama server",
]
addopts = "-m 'not live and not docker and not ollama'"
```

(The docker CI job passes its own `-m docker`, which overrides `addopts` entirely and is unaffected.)

- [ ] **Step 7: Run the provider tests and see them pass**

Run: `/usr/bin/python3 -m pytest tests/test_provider_ollama.py -q`
Expected: exit code 0; 18 passed (7 inherited contract tests + 11 standalone).

Run: `/usr/bin/python3 -m pytest tests/test_live_ollama.py -q`
Expected: exit code 5 (`no tests ran`) with `2 deselected` — the `ollama` marker is excluded by `addopts`. That is the correct outcome, not a failure.

- [ ] **Step 8: Update the CLI hints**

In `dirtywork/__main__.py`.

Before:

```python
_ENDPOINT_HINTS = {
    "openai": "Is the OpenAI-compatible server running? Try: lms ps",
    "anthropic": "Check ANTHROPIC_API_KEY and that api.anthropic.com is reachable.",
}
```

After:

```python
_ENDPOINT_HINTS = {
    "openai": "Is the OpenAI-compatible server running? Try: lms ps",
    "anthropic": "Check ANTHROPIC_API_KEY and that api.anthropic.com is reachable.",
    "ollama": "Is Ollama running? Try: ollama ps",
}

# Spec §3.1: what to tell the operator when the model is not there. A dict
# keyed by provider, replacing the two-branch ternary, so adding a provider is
# an entry rather than another branch. `{model}` is substituted with str.replace
# (never str.format): a model id is operator input and may contain braces.
_MODEL_HINTS = {
    "openai": "Load it with: lms load {model}",
    "ollama": ("Pull or run it first: ollama run {model} — Ollama model ids include "
               "the tag, e.g. 'gemma4:latest'"),
}
_DEFAULT_MODEL_HINT = "Pick one of the models listed above with --model."
# Ollama's /v1/models lists PULLED models, not resident ones, so "not loaded"
# would be the wrong word there.
_MODEL_ABSENT_WORD = {"ollama": "not available"}
```

Before:

```python
    if args.model not in models:
        hint = (f"Load it with: lms load {args.model}" if args.provider == "openai"
                else "Pick one of the models listed above with --model.")
        raise PreflightFailure(
            f"model '{args.model}' not loaded (loaded: {', '.join(models) or 'none'}). {hint}")
```

After:

```python
    if args.model not in models:
        hint = _MODEL_HINTS.get(args.provider, _DEFAULT_MODEL_HINT).replace(
            "{model}", args.model)
        absent = _MODEL_ABSENT_WORD.get(args.provider, "not loaded")
        raise PreflightFailure(
            f"model '{args.model}' {absent} (loaded: {', '.join(models) or 'none'}). {hint}")
```

Then amend the resume refusal comment.

Before:

```python
    elif args.provider != prior_provider:
        # Same rule as --sandbox (which resume does not expose at all): the
        # prior run's history was shaped by that provider's wire format.
```

After:

```python
    elif args.provider != prior_provider:
        # Same rule as --sandbox (which resume does not expose at all): the
        # prior run's history was shaped by that provider's wire format.
        # Deliberately NO openai<->ollama carve-out (spec §3.1), even though
        # the wire format is identical: the inherited --model would carry the
        # wrong id (Ollama's carry a tag) and base_url is not recorded on the
        # run, so the run is not portable between the two. Anyone who reached
        # Ollama before 0.10 with `--provider openai --base-url …:11434/v1`
        # keeps resuming exactly that way.
```

- [ ] **Step 9: Update the provider tests**

In `tests/test_providers.py`.

Before:

```python
def test_provider_names_and_default_base_urls_agree():
    assert PROVIDER_NAMES == ("openai", "anthropic")
    assert DEFAULT_BASE_URLS == {
        "openai": "http://localhost:1234/v1",
        "anthropic": "https://api.anthropic.com",
    }
    assert set(DEFAULT_BASE_URLS) == set(PROVIDER_NAMES)
```

After:

```python
def test_provider_names_and_default_base_urls_agree():
    assert PROVIDER_NAMES == ("openai", "anthropic", "ollama")
    assert DEFAULT_BASE_URLS == {
        "openai": "http://localhost:1234/v1",
        "anthropic": "https://api.anthropic.com",
        "ollama": "http://localhost:11434/v1",
    }
    assert set(DEFAULT_BASE_URLS) == set(PROVIDER_NAMES)
```

Before:

```python
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert get_provider("anthropic", "").base_url == ""
    assert get_provider("openai", "").base_url == ""
```

After:

```python
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert get_provider("anthropic", "").base_url == ""
    assert get_provider("openai", "").base_url == ""
    assert get_provider("ollama", "").base_url == ""
```

Append to `tests/test_providers.py`:

```python
def test_ollama_default_base_url_agrees_with_the_adapter():
    # The string is written in two places on purpose (providers/__init__.py
    # imports its adapters lazily); this is what keeps them in step.
    from dirtywork.providers.ollama import OLLAMA_DEFAULT_BASE_URL
    assert DEFAULT_BASE_URLS["ollama"] == OLLAMA_DEFAULT_BASE_URL
```

- [ ] **Step 10: Write the failing CLI tests**

Append to `tests/test_main.py`:

```python
def test_ollama_missing_model_says_not_available_and_names_ollama_run(tmp_path, monkeypatch, capsys):
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "--provider", "ollama",
                 "--model", "gemma4:latest", "t"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "model 'gemma4:latest' not available" in err
    assert "Pull or run it first: ollama run gemma4:latest" in err
    assert "'gemma4:latest'" in err            # the tag reminder
    assert "lms load" not in err


def test_openai_missing_model_still_says_lms_load(tmp_path, monkeypatch, capsys):
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none",
                 "--model", "no/such-model", "t"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "model 'no/such-model' not loaded" in err
    assert "Load it with: lms load no/such-model" in err


def test_resume_refuses_switching_between_openai_and_ollama(tmp_path, monkeypatch, capsys):
    m, repo, rc = _first_run(monkeypatch, tmp_path, None)
    first = json.loads(capsys.readouterr().out)
    run_dir = Path(first["run_dir"])
    prior = json.loads((run_dir / "run.json").read_text())
    prior["status"] = "max_turns"
    (run_dir / "run.json").write_text(json.dumps(prior))
    assert m.main(["resume", run_dir.name, "--provider", "ollama"]) == 2
    assert "resume it with that provider" in capsys.readouterr().err
```

`_ScriptedClient.list_models` returns `[m.DEFAULT_MODEL, "other/model"]` regardless of provider, which is exactly what the first two tests need: the requested model is absent, so preflight refuses and the hint is what is under test.

- [ ] **Step 11: Run the CLI tests and see them pass**

Run: `/usr/bin/python3 -m pytest tests/test_main.py tests/test_providers.py -q`
Expected: exit code 0.

- [ ] **Step 12: Replace the "not probed in 0.9" docs**

In `docs/machine-contract.md`.

Before:

```
    [--provider openai|anthropic]     # default: openai; anthropic needs ANTHROPIC_API_KEY
```

After:

```
    [--provider openai|anthropic|ollama]  # default: openai; anthropic needs ANTHROPIC_API_KEY
```

Before:

```
  `default`. Ollama is not probed in 0.9 — its `/api/show` reports the model's
  architectural maximum rather than the loaded `num_ctx` — so pass
  `--context-window` there. See
```

After:

```
  `default`. `--provider ollama` is probed with `GET /api/ps`, whose
  `context_length` is the loaded `num_ctx` (recorded as
  `provider:ollama:server`); Ollama has no static table in dirtywork, so a
  model that is not resident falls straight through to `default` — its
  `/v1/models` lists PULLED models, so preflight passes for a model Ollama has
  not loaded yet. Run `ollama run <model>` before the run, or pass
  `--context-window`, or Ollama will quietly serve its own smaller `num_ctx`.
  See
```

In `docs/operating.md`.

Before:

```
when you said so, `default` when nothing knew. Ollama is not probed in 0.9: its
`/api/show` reports the model's architectural maximum rather than the loaded
`num_ctx`, so pass `--context-window` there.
```

After:

```
when you said so, `default` when nothing knew. `--provider ollama` is probed
too, with `GET /api/ps` — the `context_length` it reports is the loaded
`num_ctx` and moves when a chat sets `options.num_ctx` — and shows up as
`provider:ollama:server`. There is no static table for Ollama, so a model that
is not resident goes straight to `default` (32768): Ollama's `/v1/models` lists
models you have *pulled*, not models it has *loaded*, so preflight cannot tell.
Run `ollama run <model>` before the run (or pass `--context-window`) or Ollama
will load its own, usually smaller, `num_ctx` and truncate server-side without
telling anyone.

**Ollama quickstart:**

    ollama run gemma4:latest            # make it resident first
    dirtywork run --provider ollama --model gemma4:latest \
      --repo ~/repos/someproject "Add a unit test for X"

The full tag is required — `gemma4` and `gemma4:latest` are different ids to
Ollama, and `--model` must match what `/v1/models` lists. Parallel tool calls
are not verified on Ollama; if a model emits them, dirtywork parses them the
same way it parses LM Studio's.
```

- [ ] **Step 13: Update the README**

Before:

```
Other OpenAI-compatible servers (Ollama, vLLM, llama.cpp) should work via
`--base-url`/`--provider`; only LM Studio and the Anthropic API adapter
(`--provider anthropic`, recorded-fixture tests, no live tests) are
exercised by the test suites.
```

After:

```
Other OpenAI-compatible servers (vLLM, llama.cpp) should work via
`--base-url`/`--provider`. LM Studio (`--provider openai`) and Ollama
(`--provider ollama`) are both exercised — recorded-fixture contract tests for
each, plus an opt-in live smoke per server; the Anthropic API adapter
(`--provider anthropic`) has recorded-fixture tests and no live tests. Parallel
tool calls are unverified on Ollama.
```

Before:

```
- `--provider anthropic` needs the `ANTHROPIC_API_KEY` environment variable
  set; the default (`--provider openai`, LM Studio or any OpenAI-compatible
  server) needs no key.
```

After:

```
- `--provider anthropic` needs the `ANTHROPIC_API_KEY` environment variable
  set; the default (`--provider openai`, LM Studio or any OpenAI-compatible
  server) and `--provider ollama` need no key.
- `--provider ollama` talks to `http://localhost:11434/v1` and asks
  `GET /api/ps` what context length the model is actually loaded with. Run
  `ollama run <model>` first — Ollama lists *pulled* models, not resident ones,
  so an unloaded model passes preflight and then gets whatever `num_ctx`
  Ollama picks. Model ids include the tag (`gemma4:latest`).
```

Before:

```
**Other servers:** anything speaking the OpenAI chat-completions API with tool
calling should work via `--base-url` (e.g. Ollama at
`http://localhost:11434/v1`) — see [Platform support](#platform-support) for
what's actually exercised by the test suites. Reports welcome.
```

After:

```
**Other servers:** anything speaking the OpenAI chat-completions API with tool
calling should work via `--base-url`. Ollama has its own `--provider ollama`
as of 0.10 (default base URL `http://localhost:11434/v1`, with a real
loaded-context probe) — see [Platform support](#platform-support) for what's
actually exercised by the test suites. Reports welcome.
```

- [ ] **Step 14: Run the full suite**

Run: `/usr/bin/python3 -m pytest -q`
Expected: exit code 0; `1129 passed, 1 skipped, 20 deselected` (1107 + 22 passed; 18 + 2 deselected, the two `tests/test_live_ollama.py` tests).

- [ ] **Step 15: Commit**

```bash
git add dirtywork/providers/ollama.py dirtywork/providers/__init__.py dirtywork/__main__.py \
        pyproject.toml tests/fixtures/providers/ollama tests/test_provider_ollama.py \
        tests/test_live_ollama.py tests/test_providers.py tests/test_main.py \
        README.md docs/machine-contract.md docs/operating.md
git commit -m "feat(providers): --provider ollama with an /api/ps loaded-context probe"
```

---

### Task 10: snapshot follow-ups — one slug rule, a per-invocation empty tree, two-pass hashing, a pruning walk (spec §4)

**One spec detail corrected against the tree:** §4.2 says `EMPTY_TREE_SHA` has "two uses". On this branch `grep -rn EMPTY_TREE_SHA dirtywork/ tests/` finds exactly one reader (`workspace.py:566`) plus the definition at `:428`, and no test pins it. Both lines go; the per-invocation id replaces the single reader. Verify with the grep in Step 8 before deleting.

**Files:**
- Modify: `dirtywork/rundir.py` (imports `:1-6`; new `_SLUG_RE` + `run_dir_for` after `create_run_dir` at `:66`)
- Modify: `dirtywork/runs.py` (`_SLUG_RE` `:66`, `_run_dir_for` `:70-84`)
- Modify: `dirtywork/__main__.py` (the `.rundir` import `:28`; `_resolve_branch_from` `:243-246`)
- Modify: `dirtywork/workspace.py` (new `_check_snapshot_path` before `_walk_worktree` at `:349`; `_walk_worktree` `:349-421`; `EMPTY_TREE_SHA` `:424-428` deleted; new `_hash_entries`/`_head_entries` after `_ignored_relpaths` at `:455`; `snapshot_worktree` `:504-596`)
- Modify: `tests/test_rundir.py` (2 new tests)
- Modify: `tests/test_main.py` (1 new test)
- Modify: `tests/test_workspace.py` (6 new tests)

**Interfaces:**
- Consumes: `workspace._git(repo, *args, env=None, stdin_text=None)`, `workspace.GIT_NEUTRAL_FLAGS`, `workspace.git_env()`, `workspace._check(res, what, worktree)`, `workspace._ignored_relpaths(worktree, rels)`, `workspace.WorkspaceError`; `rundir.RunDirError`.
- Produces:
  - `rundir._SLUG_RE: re.Pattern`
  - `rundir.run_dir_for(slug: str, runs_dir: Path) -> Path` — raises `RunDirError`
  - `workspace._check_snapshot_path(worktree: Path, rel: str) -> None` — raises `WorkspaceError`
  - `workspace._hash_entries(worktree: Path, files: list, links: list, env: dict, *, write: bool) -> list` — `["<mode> <sha>\t<rel>", …]`
  - `workspace._head_entries(worktree: Path, head_tree: str, env: dict) -> list` — the same normalized shape
  - `workspace._walk_worktree(worktree: Path) -> tuple` — signature unchanged: `(files, links, skipped, unreadable_dirs)`

- [ ] **Step 1: Write the failing slug tests**

Append to `tests/test_rundir.py`:

```python
def test_run_dir_for_returns_the_managed_directory(tmp_path: Path):
    from dirtywork.rundir import run_dir_for
    runs = tmp_path / "runs"
    runs.mkdir()
    assert run_dir_for("2026-08-23-abc123", runs) == runs / "2026-08-23-abc123"


@pytest.mark.parametrize("slug", ["../escape", "/etc", ".", "..", "", "a/b", "-leading"])
def test_run_dir_for_refuses_a_slug_that_could_name_a_path(tmp_path: Path, slug):
    from dirtywork.rundir import run_dir_for
    runs = tmp_path / "runs"
    runs.mkdir()
    with pytest.raises(RunDirError) as excinfo:
        run_dir_for(slug, runs)
    assert f"invalid run slug '{slug}'" in str(excinfo.value)
```

Append to `tests/test_main.py`:

```python
def test_branch_from_an_invalid_slug_exits_2_and_creates_nothing(tmp_path, monkeypatch, capsys):
    # Spec §4.1: `--branch-from @<slug>` now goes through the SAME rule
    # `runs snapshot` uses, instead of resume.resolve_run_dir, which treats
    # anything with a separator as a path.
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none",
                 "--branch-from", "@../escape", "t"])
    assert rc == 2
    assert "invalid run slug '../escape'" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()
    assert not (repo / ".worktrees").exists()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_rundir.py tests/test_main.py -q -k "run_dir_for or branch_from_an_invalid_slug"`
Expected: 9 failed — the eight `tests/test_rundir.py` cases with `ImportError: cannot import name 'run_dir_for' from 'dirtywork.rundir'`, and the `test_main.py` one with `AssertionError` on the stderr text (today it reports `unknown run '../escape' (no run dir under …)`).

- [ ] **Step 3: Add the shared slug rule**

In `dirtywork/rundir.py`.

Before:

```python
import json
import os
import stat
from pathlib import Path
```

After:

```python
import json
import os
import re
import stat
from pathlib import Path
```

Then, immediately **after** `create_run_dir` and immediately **before** `def write_run_json(`.

```python
# Spec §4.1. A slug arrives from the command line (`runs show <slug>`,
# `--branch-from @<slug>`) or from a results file; it must never be able to
# name a path outside `runs_dir`.
_SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def run_dir_for(slug: str, runs_dir: Path) -> Path:
    """`<runs_dir>/<slug>` for a plain slug ONLY. Raises RunDirError.

    The ONE rule, in one place: `dirtywork runs …` reaches it through
    `runs._run_dir_for` (re-raised as RunsError) and `--branch-from @<slug>`
    through `__main__._resolve_branch_from` (re-raised as PreflightFailure).
    Before 0.10 the second route went through the path-permissive
    `resume.resolve_run_dir`, so `@../x` and `@/etc` were accepted by one
    entry point and refused by the other. This does NOT require the directory
    to exist -- a syntactically valid slug that names no run gets each
    caller's own "unknown run" message."""
    if not _SLUG_RE.fullmatch(slug) or slug in (".", ".."):
        raise RunDirError(f"invalid run slug '{slug}'")
    runs_dir = Path(runs_dir)
    run_dir = runs_dir / slug
    try:
        if run_dir.resolve().parent != runs_dir.resolve():
            raise RunDirError(f"invalid run slug '{slug}'")
    except OSError as e:
        raise RunDirError(f"cannot resolve run '{slug}': {e}")
    return run_dir
```

- [ ] **Step 4: Route `runs.py` through it**

In `dirtywork/runs.py`.

Before:

```python
_SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_DOCKER_ABSENT_RE = re.compile(r"no such (?:object|container|volume)\b", re.IGNORECASE)
```

After:

```python
_DOCKER_ABSENT_RE = re.compile(r"no such (?:object|container|volume)\b", re.IGNORECASE)
```

Before:

```python
def _run_dir_for(slug: str) -> Path:
    """`<RUNS_DIR>/<slug>` for a plain slug ONLY. A slug is data from the
    command line (or a results file); it must never be able to name a path
    outside RUNS_DIR (`../x`, `/etc`, `.`), so `runs clean --force <slug>`
    can only ever operate on a managed run directory."""
    if not _SLUG_RE.fullmatch(slug) or slug in (".", ".."):
        raise RunsError(f"invalid run slug '{slug}'")
    runs_dir = Path(rundir.RUNS_DIR)
    run_dir = runs_dir / slug
    try:
        if run_dir.resolve().parent != runs_dir.resolve():
            raise RunsError(f"invalid run slug '{slug}'")
    except OSError as e:
        raise RunsError(f"cannot resolve run '{slug}': {e}")
    return run_dir
```

After:

```python
def _run_dir_for(slug: str) -> Path:
    """`<RUNS_DIR>/<slug>` via the ONE shared rule (rundir.run_dir_for, spec
    §4.1), re-raised as the RunsError every `dirtywork runs` subcommand
    reports. RUNS_DIR is read from the module here (not captured at import) so
    a test can point it at a tmp_path."""
    try:
        return rundir.run_dir_for(slug, Path(rundir.RUNS_DIR))
    except rundir.RunDirError as e:
        raise RunsError(str(e))
```

- [ ] **Step 5: Route `--branch-from @<slug>` through it**

In `dirtywork/__main__.py`.

Before:

```python
from .rundir import RUNS_DIR, RunDirError, create_run_dir, ensure_runs_dir, read_run_json, write_run_json
```

After:

```python
from .rundir import (RUNS_DIR, RunDirError, create_run_dir, ensure_runs_dir, read_run_json,
                     run_dir_for, write_run_json)
```

Before:

```python
    slug = value[1:]
    run_dir = resolve_run_dir(slug, RUNS_DIR)
    if not run_dir.is_dir():
        raise PreflightFailure(f"unknown run '{slug}' (no run dir under {RUNS_DIR})")
```

After:

```python
    slug = value[1:]
    # Spec §4.1: the SAME rule `runs snapshot` uses. resolve_run_dir treats
    # anything with a separator as a path, which is right for
    # `dirtywork resume <run-dir>` and wrong for an `@<slug>` reference.
    try:
        run_dir = run_dir_for(slug, RUNS_DIR)
    except RunDirError as e:
        raise PreflightFailure(str(e))
    if not run_dir.is_dir():
        raise PreflightFailure(f"unknown run '{slug}' (no run dir under {RUNS_DIR})")
```

- [ ] **Step 6: Run the slug tests and see them pass**

Run: `/usr/bin/python3 -m pytest tests/test_rundir.py tests/test_runs.py tests/test_main.py -q`
Expected: exit code 0. (`tests/test_main.py:1941`'s `unknown run 'no-such-run'` message is unchanged: `no-such-run` is a syntactically valid slug.)

- [ ] **Step 7: Write the failing workspace tests**

Append to `tests/test_workspace.py`:

```python
def _loose_objects(repo: Path) -> int:
    for line in _git(repo, "count-objects", "-v").splitlines():
        if line.startswith("count: "):
            return int(line[len("count: "):])
    raise AssertionError("git count-objects printed no count")


def test_snapshot_worktree_writes_no_loose_objects_on_a_no_op(tmp_path: Path):
    # Spec §4.3: a snapshot that changes nothing must not litter the object
    # store. Pass 1 hashes WITHOUT -w purely to decide.
    from dirtywork.workspace import snapshot_worktree
    repo, wt = _snapshot_repo(tmp_path)
    assert snapshot_worktree(wt, "dirtywork/snap", "wip: one") is not None
    before = _loose_objects(repo)
    assert snapshot_worktree(wt, "dirtywork/snap", "wip: two") is None
    assert _loose_objects(repo) == before


def test_snapshot_worktree_tree_only_references_written_blobs(tmp_path: Path):
    # Spec §4.3: the entries are rebuilt from the SECOND (-w) pass's shas, so
    # the tree can never point at a blob that was only ever hashed.
    from dirtywork.workspace import snapshot_worktree
    repo, wt = _snapshot_repo(tmp_path)
    sha = snapshot_worktree(wt, "dirtywork/snap", "wip: one")
    assert sha is not None
    for line in _git(repo, "ls-tree", "-r", sha).splitlines():
        meta, _tab, _name = line.partition("\t")
        _mode, _kind, blob = meta.split()
        _git(repo, "cat-file", "-e", blob)      # check=True: raises if absent


def test_snapshot_worktree_computes_the_empty_tree_id_per_invocation(tmp_path: Path):
    # Spec §4.2: the hard-coded SHA-1 empty tree is gone, so a SHA-256 repo
    # gets the right id.
    import dirtywork.workspace as workspace_mod
    from dirtywork.workspace import snapshot_worktree
    assert not hasattr(workspace_mod, "EMPTY_TREE_SHA")
    repo = tmp_path / "repo_empty"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "commit", "--allow-empty", "-m", "init")
    wt = tmp_path / "wt_empty"
    _git(repo, "worktree", "add", "-b", "dirtywork/snapE", str(wt))
    # An empty worktree over an empty head is a genuine no-op, not a refusal.
    assert snapshot_worktree(wt, "dirtywork/snapE", "wip: nothing") is None


def test_snapshot_worktree_prunes_an_ignored_directory_before_descending(tmp_path: Path):
    # Spec §4.4: the ignore check happens per DEPTH, before the walk descends.
    # The proof is a file whose name holds a newline: reaching it at all would
    # raise WorkspaceError from the control-character guard.
    from dirtywork.workspace import snapshot_worktree
    repo = tmp_path / "repo_prune"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text("build/\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    wt = tmp_path / "wt_prune"
    _git(repo, "worktree", "add", "-b", "dirtywork/snapP", str(wt))
    (wt / "build").mkdir()
    (wt / "build" / "we\nird.o").write_text("junk\n")
    (wt / "keep.txt").write_text("keep\n")

    sha = snapshot_worktree(wt, "dirtywork/snapP", "wip: pruned")
    assert sha is not None
    names = _git(repo, "ls-tree", "-r", "--name-only", sha).splitlines()
    assert "keep.txt" in names
    assert not any(n.startswith("build/") for n in names)


def test_snapshot_worktree_keeps_a_tracked_file_inside_an_ignored_directory(tmp_path: Path):
    # Spec §4.4's named regression: check-ignore is INDEX-AWARE, so `build`
    # is NOT reported ignored while a tracked file lives in it -- which is what
    # keeps depth-pruning from dropping tracked content. `--no-index` here
    # would silently delete build/keep.txt from every snapshot.
    from dirtywork.workspace import snapshot_worktree
    repo = tmp_path / "repo_tracked"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text("build/\n")
    (repo / "build").mkdir()
    (repo / "build" / "keep.txt").write_text("keep\n")
    _git(repo, "add", "-f", ".gitignore", "build/keep.txt")
    _git(repo, "commit", "-m", "init")
    wt = tmp_path / "wt_tracked"
    _git(repo, "worktree", "add", "-b", "dirtywork/snapT", str(wt))
    (wt / "build" / "out.o").write_text("junk\n")
    (wt / "build" / "keep.txt").write_text("changed\n")

    sha = snapshot_worktree(wt, "dirtywork/snapT", "wip: tracked inside ignored")
    assert sha is not None
    names = _git(repo, "ls-tree", "-r", "--name-only", sha).splitlines()
    assert "build/keep.txt" in names
    assert "build/out.o" not in names


def test_snapshot_worktree_runs_one_check_ignore_per_tree_depth(tmp_path: Path, monkeypatch):
    # Spec §4.4: batched per DEPTH, not per directory and not per file.
    import dirtywork.workspace as workspace_mod
    from dirtywork.workspace import snapshot_worktree
    repo = tmp_path / "repo_depth"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "seed.txt").write_text("seed\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    wt = tmp_path / "wt_depth"
    _git(repo, "worktree", "add", "-b", "dirtywork/snapD", str(wt))
    (wt / "a").mkdir()
    (wt / "b").mkdir()
    (wt / "a" / "x").mkdir()
    (wt / "top.txt").write_text("t\n")
    (wt / "a" / "f.txt").write_text("f\n")
    (wt / "a" / "x" / "g.txt").write_text("g\n")

    calls = []
    real = workspace_mod._ignored_relpaths

    def _counting(worktree, rels):
        calls.append(list(rels))
        return real(worktree, rels)

    monkeypatch.setattr(workspace_mod, "_ignored_relpaths", _counting)
    assert snapshot_worktree(wt, "dirtywork/snapD", "wip: depths") is not None
    # depth 1 (a, b), depth 2 (a/x), then snapshot_worktree's own file batch.
    assert len(calls) == 3
    assert calls[0] == ["a", "b"]
    assert calls[1] == ["a/x"]
    assert sorted(calls[2]) == ["a/f.txt", "a/x/g.txt", "seed.txt", "top.txt"]
```

- [ ] **Step 8: Run them to verify they fail, and confirm the `EMPTY_TREE_SHA` inventory**

Run: `/usr/bin/python3 -m pytest tests/test_workspace.py -q -k "no_loose_objects or written_blobs or empty_tree_id or prunes_an_ignored or tracked_file_inside or one_check_ignore"`
Expected: 4 failed, 2 passed. Failing: `test_snapshot_worktree_writes_no_loose_objects_on_a_no_op` (today's `-w` pass writes blobs on every call), `test_snapshot_worktree_computes_the_empty_tree_id_per_invocation` (`EMPTY_TREE_SHA` still exists), `test_snapshot_worktree_prunes_an_ignored_directory_before_descending` (`WorkspaceError: … contains a control character`), and `test_snapshot_worktree_runs_one_check_ignore_per_tree_depth` (`assert 1 == 3`). Passing already (they are pins on behaviour this task must preserve): `test_snapshot_worktree_tree_only_references_written_blobs` and `test_snapshot_worktree_keeps_a_tracked_file_inside_an_ignored_directory`.

Run:

```bash
grep -rn "EMPTY_TREE_SHA" dirtywork/ tests/
```
Expected: exactly two lines, both in `dirtywork/workspace.py` (the definition at `:428` and the single reader at `:566`). If a third appears, stop and adapt Step 11.

- [ ] **Step 9: Factor out the path guard**

In `dirtywork/workspace.py`, immediately **before** `def _walk_worktree(worktree: Path) -> tuple:`.

```python
def _check_snapshot_path(worktree: Path, rel: str) -> None:
    """Refuse a repo-relative path git's stdin protocols cannot carry safely.

    Factored out of snapshot_worktree (spec §4.4) so the breadth-first walk can
    run it on every DIRECTORY path before that path reaches `check-ignore` --
    preserving this module's promise that an unsafe name raises WorkspaceError,
    never a UnicodeEncodeError from inside a text=True _git call."""
    if any(ord(c) < 32 for c in rel):
        raise WorkspaceError(
            f"cannot snapshot {worktree}: path {rel!r} contains a control character, "
            f"which git's stdin path protocols cannot carry safely"
        )
    try:
        rel.encode("utf-8")
    except UnicodeEncodeError:
        # An undecodable filename (surrogate-escaped by os.fsdecode) is
        # ord(c) >= 32 for every char, so the guard above lets it through;
        # _git's text=True calls would then raise UnicodeEncodeError themselves
        # (not a WorkspaceError) trying to encode it for git's stdin.
        raise WorkspaceError(
            f"cannot snapshot {worktree}: path {rel!r} is not valid UTF-8 "
            f"(undecodable filename), which git's stdin path protocols "
            f"cannot carry safely"
        )
```

- [ ] **Step 10: Replace the walk with a depth-batched BFS**

In `dirtywork/workspace.py`, replace `_walk_worktree` entirely.

Before:

```python
def _walk_worktree(worktree: Path) -> tuple:
    """(files, links, skipped, unreadable_dirs) for everything under
    `worktree`.
```

…through…

```python
    files.sort()
    links.sort()
    return files, links, skipped, unreadable_dirs
```

After:

```python
def _walk_worktree(worktree: Path) -> tuple:
    """(files, links, skipped, unreadable_dirs) for everything under
    `worktree`, walked breadth-first ONE TREE DEPTH at a time (spec §4.4).

    `files` is [(repo-relative path, is_executable)], `links` is
    [(repo-relative path, link target string)], `skipped` counts entries that
    are neither a regular file nor a symlink (FIFOs, sockets, devices) or that
    failed `os.stat`/`os.readlink` outright.

    Why breadth-first: each level's candidate DIRECTORIES go through ONE
    batched `git check-ignore` call, and an ignored directory is dropped before
    it is descended into -- so an ignored `node_modules/` costs one path in one
    batch instead of a full traversal plus a per-file filter. The check is
    deliberately INDEX-AWARE (never `--no-index`): `check-ignore` does not
    report a directory as ignored while a TRACKED file lives inside it, which
    is exactly what keeps a tracked `build/keep.txt` in the snapshot when
    `build/` matches an ignore pattern. Every directory path passes
    `_check_snapshot_path` BEFORE it reaches `check-ignore`, so the module's
    WorkspaceError-not-UnicodeEncodeError promise holds for directories too.
    Files and symlinks are NOT filtered here -- snapshot_worktree still runs
    its own single batch over them, unchanged.

    A DIRECTORY that cannot be listed (`chmod 000` on the directory itself) is
    recorded in `unreadable_dirs` rather than raised on: snapshot_worktree runs
    it through the ignore check afterward and raises only for one that is NOT
    ignored. An ignored directory is never listed at all, so it can never be
    recorded. The TOP-LEVEL `.git` entry is skipped and nothing else is skipped
    by name. Symlinks -- including symlinked directories -- are recorded by
    their target string and never followed or descended into."""
    files, links, skipped, unreadable_dirs = [], [], 0, []
    level = [""]                       # "" is the worktree root itself
    while level:
        children = []
        for rel_dir in level:
            here = worktree / rel_dir if rel_dir else worktree
            try:
                with os.scandir(str(here)) as it:
                    entries = sorted(it, key=lambda e: e.name)
            except OSError as e:
                unreadable_dirs.append((rel_dir or ".", e))
                continue
            for entry in entries:
                if not rel_dir and entry.name == ".git":
                    continue
                rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    skipped += 1
                    continue
                if stat.S_ISLNK(st.st_mode):
                    try:
                        links.append((rel, os.readlink(entry.path)))
                    except OSError:
                        skipped += 1
                elif stat.S_ISDIR(st.st_mode):
                    children.append(rel)
                elif stat.S_ISREG(st.st_mode):
                    files.append((rel, bool(st.st_mode & 0o111)))
                else:
                    skipped += 1
        if not children:
            break
        for rel in children:
            _check_snapshot_path(worktree, rel)
        ignored = _ignored_relpaths(worktree, children)
        level = [rel for rel in children if rel not in ignored]
    files.sort()
    links.sort()
    return files, links, skipped, unreadable_dirs
```

- [ ] **Step 11: Delete the hard-coded empty tree and add the hashing helpers**

In `dirtywork/workspace.py`, delete the constant and its comment.

Before:

```python
# The well-known SHA of `git write-tree` on an empty index — content-addressed,
# so this is the same value in every git repository. Used by snapshot_worktree
# to tell "this branch's head genuinely has no files" from "there would be
# something to delete" without needing a git call to compute it.
EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _ignored_relpaths(worktree: Path, rels: list) -> set:
```

After:

```python
def _ignored_relpaths(worktree: Path, rels: list) -> set:
```

Then, immediately **after** `_check` (which ends with `raise WorkspaceError(f"git {what} failed in {worktree}: {res.stderr.strip()}")`) and immediately **before** `def snapshot_worktree(`.

```python
def _hash_entries(worktree: Path, files: list, links: list, env: dict, *,
                  write: bool) -> list:
    """The `<mode> <sha>\\t<relpath>` index lines for `files` + `links`.

    `write` selects `-w` (write each blob into the object store) or not. Spec
    §4.3 calls this TWICE: once WITHOUT -w, only to decide whether anything
    changed at all (a no-op snapshot must not litter the object store with
    loose objects), and then -- only when something did -- once WITH -w,
    rebuilding the entries from the SECOND pass's shas. Rebuilding is
    load-bearing: a file that changed between the two passes must not leave the
    tree pointing at a blob that was never written. (Verified: `write-tree`
    fails on absent blobs, so the no-op decision cannot instead be made after a
    -w-less update-index.)

    `--no-filters` is load-bearing too: the filter that would otherwise run is
    configured REPO-locally, which GIT_CONFIG_GLOBAL=/dev/null does not
    disable. `worktree` is absolute, so every line handed to `--stdin-paths`
    starts with `/` and a worker-chosen filename can never be the FIRST
    character of that line -- which neutralises `--stdin-paths`' own C-quoting
    of a leading `"`."""
    hash_argv = ["hash-object"] + (["-w"] if write else []) + ["--no-filters"]
    entries = []
    if files:
        paths = "".join(str(worktree / rel) + "\n" for rel, _ in files)
        res = _git(worktree, *GIT_NEUTRAL_FLAGS, *hash_argv, "--stdin-paths",
                   env=env, stdin_text=paths)
        _check(res, "hash-object", worktree)
        shas = res.stdout.split()
        if len(shas) != len(files):
            raise WorkspaceError(
                f"git hash-object returned {len(shas)} hashes for {len(files)} files "
                f"in {worktree}")
        for (rel, is_exec), sha in zip(files, shas):
            entries.append(f"{'100755' if is_exec else '100644'} {sha}\t{rel}")
    for rel, target in links:
        res = _git(worktree, *GIT_NEUTRAL_FLAGS, *hash_argv, "--stdin",
                   env=env, stdin_text=target)
        _check(res, f"hash-object for symlink {rel}", worktree)
        entries.append(f"120000 {res.stdout.strip()}\t{rel}")
    return entries


def _head_entries(worktree: Path, head_tree: str, env: dict) -> list:
    """`head_tree`'s contents in the SAME normalized form _hash_entries
    produces, so the two can be compared directly (spec §4.3). `-r -z`: `-r`
    because _hash_entries lists leaves only, `-z` because a path with a
    newline or a quote in it must round-trip literally."""
    res = _git(worktree, *GIT_NEUTRAL_FLAGS, "ls-tree", "-r", "-z", head_tree, env=env)
    _check(res, f"ls-tree {head_tree}", worktree)
    out = []
    for record in res.stdout.split("\0"):
        if not record:
            continue
        meta, _tab, rel = record.partition("\t")
        mode, _kind, sha = meta.split(" ", 2)
        out.append(f"{mode} {sha}\t{rel}")
    return out
```

- [ ] **Step 12: Rewrite `snapshot_worktree`'s hashing and empty-tree logic**

In `dirtywork/workspace.py`.

Before:

```python
    symref_res = _git(worktree, *GIT_NEUTRAL_FLAGS, "symbolic-ref", "-q", "HEAD", env=env)
    current = symref_res.stdout.strip() if symref_res.returncode == 0 else "(detached HEAD)"
    if current != f"refs/heads/{branch}":
        raise WorkspaceError(
            f"worktree {worktree} has {current} checked out, not refs/heads/{branch}; "
            f"refusing to commit its content onto a branch it is not on"
        )
```

After:

```python
    symref_res = _git(worktree, *GIT_NEUTRAL_FLAGS, "symbolic-ref", "-q", "HEAD", env=env)
    current = symref_res.stdout.strip() if symref_res.returncode == 0 else "(detached HEAD)"
    if current != f"refs/heads/{branch}":
        raise WorkspaceError(
            f"worktree {worktree} has {current} checked out, not refs/heads/{branch}; "
            f"refusing to commit its content onto a branch it is not on"
        )

    # Spec §4.2: the empty tree's id is content-addressed, so it depends on the
    # repo's OBJECT FORMAT -- the well-known 4b825dc… is the SHA-1 answer only.
    # Computed here, once per call, from the repo itself. `--stdin` with empty
    # stdin (never a /dev/null path) and no `-w`: this asks for an id, it does
    # not write an object.
    empty_tree_res = _git(worktree, *GIT_NEUTRAL_FLAGS, "hash-object", "-t", "tree",
                          "--stdin", env=env, stdin_text="")
    _check(empty_tree_res, "hash-object -t tree", worktree)
    empty_tree = empty_tree_res.stdout.strip()
```

Before:

```python
    unreadable_rels = [rel for rel, _exc in unreadable_dirs]
    for rel in [r for r, _ in files] + [r for r, _ in links] + unreadable_rels:
        if any(ord(c) < 32 for c in rel):
            raise WorkspaceError(
                f"cannot snapshot {worktree}: path {rel!r} contains a control character, "
                f"which git's stdin path protocols cannot carry safely"
            )
        try:
            rel.encode("utf-8")
        except UnicodeEncodeError:
            # An undecodable filename (surrogate-escaped by os.fsdecode) is
            # ord(c) >= 32 for every char, so the guard above lets it through;
            # _git's text=True calls below would then raise UnicodeEncodeError
            # themselves (not a WorkspaceError) trying to encode it for git's
            # stdin. Refuse it here with the same error type every other
            # unsafe path in this function raises.
            raise WorkspaceError(
                f"cannot snapshot {worktree}: path {rel!r} is not valid UTF-8 "
                f"(undecodable filename), which git's stdin path protocols "
                f"cannot carry safely"
            )
```

After:

```python
    unreadable_rels = [rel for rel, _exc in unreadable_dirs]
    for rel in [r for r, _ in files] + [r for r, _ in links] + unreadable_rels:
        _check_snapshot_path(worktree, rel)
```

Before:

```python
    if not files and not links and head_tree != EMPTY_TREE_SHA:
```

After:

```python
    if not files and not links and head_tree != empty_tree:
```

Before:

```python
    entries = []
    if files:
        paths = "".join(str(worktree / rel) + "\n" for rel, _ in files)
        res = _git(worktree, *GIT_NEUTRAL_FLAGS, "hash-object", "-w", "--no-filters",
                   "--stdin-paths", env=env, stdin_text=paths)
        _check(res, "hash-object", worktree)
        shas = res.stdout.split()
        if len(shas) != len(files):
            raise WorkspaceError(
                f"git hash-object returned {len(shas)} hashes for {len(files)} files "
                f"in {worktree}")
        for (rel, is_exec), sha in zip(files, shas):
            entries.append(f"{'100755' if is_exec else '100644'} {sha}\t{rel}")
    for rel, target in links:
        res = _git(worktree, *GIT_NEUTRAL_FLAGS, "hash-object", "-w", "--no-filters", "--stdin",
                   env=env, stdin_text=target)
        _check(res, f"hash-object for symlink {rel}", worktree)
        entries.append(f"120000 {res.stdout.strip()}\t{rel}")
```

After:

```python
    # Spec §4.3, pass 1: hash WITHOUT -w and compare against the head tree. If
    # they agree there is nothing to snapshot, and returning here means a no-op
    # call has written no objects at all.
    probe_entries = _hash_entries(worktree, files, links, env, write=False)
    if sorted(probe_entries) == sorted(_head_entries(worktree, head_tree, env)):
        return None
    # Pass 2: the same hashing WITH -w, and the entries rebuilt from THESE
    # shas -- a file that changed between the passes must not leave the tree
    # pointing at a blob that was never written.
    entries = _hash_entries(worktree, files, links, env, write=True)
```

(The `tree == head_tree` check further down stays exactly as it is: pass 2 can legitimately produce the head tree again if a file changed back between the passes, and that is still a no-op commit to refuse.)

- [ ] **Step 13: Run the workspace tests and see them pass**

Run: `/usr/bin/python3 -m pytest tests/test_workspace.py -q`
Expected: exit code 0.

- [ ] **Step 14: Run the full suite**

Run: `/usr/bin/python3 -m pytest -q`
Expected: exit code 0; `1144 passed, 1 skipped, 20 deselected` (1129 + 15: `tests/test_rundir.py` contributes 8 — one direct test plus the seven parametrized slug cases — `tests/test_main.py` 1, and `tests/test_workspace.py` 6).

- [ ] **Step 15: Commit**

```bash
git add dirtywork/rundir.py dirtywork/runs.py dirtywork/__main__.py dirtywork/workspace.py \
        tests/test_rundir.py tests/test_main.py tests/test_workspace.py
git commit -m "fix(workspace): shared slug rule, per-invocation empty tree, two-pass hashing, depth-batched walk"
```

---

### Task 11: single-pass `describe_change`, CRLF and the doc nits (spec §5.2, §5.3, §5.4)

`_line_counts` and `difflib.unified_diff` each build their own `SequenceMatcher` today, so every echoed diff computes the matching blocks twice. One matcher feeds both: `get_opcodes()` caches, and `get_grouped_opcodes()` reads that cache.

**Files:**
- Modify: `dirtywork/tools.py` (`_line_counts` `:133-146` replaced by `_count_opcodes`; new `DIFF_CONTEXT_LINES`, `_format_range_unified`, `_unified_diff_lines`; `describe_change` `:149-201`)
- Modify: `tests/test_tools_files.py` (3 new tests)
- Modify: `docs/machine-contract.md` (the in-place-tools paragraph at `:148-155`)
- Modify: `docs/transcript-schema.md` (the `result` row `:72`)

**Interfaces:**
- Consumes: `tools._lines_keep_newlines`, `tools.DESCRIBE_DIFF_MAX_LINES`, `tools.MAX_DIFF_LINES`, `tools.MAX_DIFF_CHARS`.
- Produces:
  - `tools.DIFF_CONTEXT_LINES: int` (`2`)
  - `tools._format_range_unified(start: int, stop: int) -> str`
  - `tools._count_opcodes(opcodes, old_lines: list) -> tuple` — `(added, deleted, removed_non_blank)`, all `int`
  - `tools._unified_diff_lines(matcher, old_lines: list, new_lines: list, path: str) -> list`
- `_line_counts` is **removed**. It is private, `describe_change` is its only caller (`grep -rn "_line_counts" dirtywork/ tests/` finds the definition and that one call), and no test names it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools_files.py`:

```python
# --- spec §5.2/§5.3: single-pass diff rendering, proved byte-identical.


def test_format_range_unified_matches_the_stdlibs_two_special_cases():
    import difflib
    for start, stop in [(0, 0), (0, 1), (0, 3), (5, 5), (5, 6), (5, 9), (12, 12)]:
        assert tools._format_range_unified(start, stop) == \
            difflib._format_range_unified(start, stop), (start, stop)


def test_unified_diff_lines_match_difflib_on_seeded_random_pairs():
    """Spec §5.2: the single-pass renderer must be BYTE-identical to
    difflib.unified_diff(..., n=2, lineterm=''). Seeded, so any failure is
    reproducible from the printed (index, old, new)."""
    import difflib
    import random
    rng = random.Random(20260823)
    # A vocabulary with a DUPLICATE ("alpha") and blank-ish lines, because
    # popular repeated lines are exactly where opcode grouping gets
    # interesting.
    vocab = ["alpha\n", "beta\n", "gamma\n", "alpha\n", "\n", "  \n", "x = 1\n"]
    for i in range(1000):
        old = [rng.choice(vocab) for _ in range(rng.randint(0, 12))]
        new = [rng.choice(vocab) for _ in range(rng.randint(0, 12))]
        # roughly a third of the sides lose their trailing newline, like a real
        # file's final line
        if old and rng.random() < 0.33:
            old[-1] = old[-1].rstrip("\n")
        if new and rng.random() < 0.33:
            new[-1] = new[-1].rstrip("\n")
        matcher = difflib.SequenceMatcher(a=old, b=new)
        mine = tools._unified_diff_lines(matcher, old, new, "f.txt")
        theirs = list(difflib.unified_diff(old, new, fromfile="a/f.txt",
                                           tofile="b/f.txt", n=2, lineterm=""))
        assert mine == theirs, (i, old, new)


def test_describe_change_renders_crlf_as_git_does():
    # Spec §5.3: only "\n" is the separator, so a CRLF line keeps its "\r".
    out = tools.describe_change("f.txt", "a\r\nb\r\n", "a\r\nc\r\n", verb="Edited")
    assert out.startswith("Edited f.txt: +1 -1 (removed 1 non-blank line)")
    assert "-b\r" in out
    assert "+c\r" in out
```

- [ ] **Step 2: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_tools_files.py -q -k "format_range_unified or unified_diff_lines or renders_crlf"`
Expected: 3 tests selected, 2 failed with `AttributeError: module 'dirtywork.tools' has no attribute '_format_range_unified'` / `'_unified_diff_lines'`. `test_describe_change_renders_crlf_as_git_does` PASSES already — it is a pin on behaviour this task must not change. Confirm exactly two fail.

- [ ] **Step 3: Replace `_line_counts` with an opcode-consuming counter**

In `dirtywork/tools.py`.

Before:

```python
def _line_counts(old_lines: list, new_lines: list) -> tuple:
    """(added, deleted, removed_non_blank) from SequenceMatcher opcodes. A
    REPLACED non-blank line counts as removed: the counter exists to answer
    'did I delete content I did not mean to delete', and a replace deletes
    before it inserts."""
    added = deleted = removed_non_blank = 0
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("delete", "replace"):
            deleted += i2 - i1
            removed_non_blank += sum(1 for line in old_lines[i1:i2] if line.strip())
        if tag in ("insert", "replace"):
            added += j2 - j1
    return added, deleted, removed_non_blank
```

After:

```python
# The unified diff's context width. difflib's own default is 3; dirtywork has
# echoed n=2 since 0.8, and _unified_diff_lines must be called with the same
# value the old difflib.unified_diff(..., n=2) call used.
DIFF_CONTEXT_LINES = 2


def _count_opcodes(opcodes, old_lines: list) -> tuple:
    """(added, deleted, removed_non_blank) from opcodes the CALLER already has
    (spec §5.2 -- one SequenceMatcher per describe_change, not two). A REPLACED
    non-blank line counts as removed: the counter exists to answer 'did I
    delete content I did not mean to delete', and a replace deletes before it
    inserts."""
    added = deleted = removed_non_blank = 0
    for tag, i1, i2, j1, j2 in opcodes:
        if tag in ("delete", "replace"):
            deleted += i2 - i1
            removed_non_blank += sum(1 for line in old_lines[i1:i2] if line.strip())
        if tag in ("insert", "replace"):
            added += j2 - j1
    return added, deleted, removed_non_blank


def _format_range_unified(start: int, stop: int) -> str:
    """difflib._format_range_unified, reproduced. Copied rather than imported
    because it is a PRIVATE stdlib name; its two special cases -- a length of 1
    renders as a bare number, and an EMPTY range begins one line earlier -- are
    exactly what make the `@@` headers byte-identical to
    difflib.unified_diff's. A seeded property test pins that."""
    beginning = start + 1                 # lines are numbered from one
    length = stop - start
    if length == 1:
        return str(beginning)
    if not length:
        beginning -= 1                    # an empty range begins just before it
    return f"{beginning},{length}"


def _unified_diff_lines(matcher, old_lines: list, new_lines: list, path: str) -> list:
    """Exactly what `difflib.unified_diff(old_lines, new_lines,
    fromfile=f"a/{path}", tofile=f"b/{path}", n=DIFF_CONTEXT_LINES,
    lineterm="")` yields -- rendered from a matcher the caller ALREADY built.

    Spec §5.2: that is the whole point. SequenceMatcher caches its opcodes, so
    describe_change's get_opcodes() (for the counts) and this function's
    get_grouped_opcodes() (for the hunks) share ONE matching-block
    computation instead of building a second matcher over the same two lists.
    The header lines are emitted lazily, on the first group, so an unchanged
    pair returns [] exactly as unified_diff does."""
    out = []
    for group in matcher.get_grouped_opcodes(DIFF_CONTEXT_LINES):
        if not out:
            out.append(f"--- a/{path}")
            out.append(f"+++ b/{path}")
        first, last = group[0], group[-1]
        out.append("@@ -{} +{} @@".format(
            _format_range_unified(first[1], last[2]),
            _format_range_unified(first[3], last[4])))
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for line in old_lines[i1:i2]:
                    out.append(" " + line)
                continue
            if tag in ("replace", "delete"):
                for line in old_lines[i1:i2]:
                    out.append("-" + line)
            if tag in ("replace", "insert"):
                for line in new_lines[j1:j2]:
                    out.append("+" + line)
    return out
```

- [ ] **Step 4: Make `describe_change` build one matcher**

In `dirtywork/tools.py`.

Before:

```python
    added, deleted, removed_non_blank = _line_counts(old_lines, new_lines)
    head = f"{verb} {path}: +{added} -{deleted}"
    if removed_non_blank > 0:
        plural = "" if removed_non_blank == 1 else "s"
        head += f" (removed {removed_non_blank} non-blank line{plural})"
    diff_lines = list(difflib.unified_diff(
        old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}", n=2, lineterm=""))
```

After:

```python
    # ONE matcher for both the counts and the hunks (spec §5.2): get_opcodes()
    # caches, and get_grouped_opcodes() reads that cache, so the matching
    # blocks are computed once per call instead of twice.
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    added, deleted, removed_non_blank = _count_opcodes(matcher.get_opcodes(), old_lines)
    head = f"{verb} {path}: +{added} -{deleted}"
    if removed_non_blank > 0:
        plural = "" if removed_non_blank == 1 else "s"
        head += f" (removed {removed_non_blank} non-blank line{plural})"
    diff_lines = _unified_diff_lines(matcher, old_lines, new_lines, path)
```

- [ ] **Step 5: Run the module and see it pass**

Run: `/usr/bin/python3 -m pytest tests/test_tools_files.py -q`
Expected: exit code 0. The existing `test_describe_change_*` tests (`:232`, `:242`, `:255`, `:269`, `:282`, `:295`, `:305`, `:335`) are the regression net for "byte-identical output"; none of them changes.

- [ ] **Step 6: Prove `_line_counts` is gone and nothing referenced it**

Run:

```bash
grep -rn "_line_counts" dirtywork/ tests/
```
Expected: **no output**.

- [ ] **Step 7: Document CRLF and name every mutating tool in the result row**

In `docs/machine-contract.md`, extend the in-place-tools paragraph.

Before:

```
container. Since 0.10 "nothing was written" also covers a failure **during**
```

After:

```
container. Diff bodies use `\n` as the only line separator, so **CRLF content
keeps its carriage return**: a line ending `\r\n` renders as `-foo\r` /
`+foo\r`, exactly as `git diff` shows it, and a line that merely *contains* a
form feed or other vertical whitespace is never split. A final line with no
trailing newline is followed by git's own `\ No newline at end of file` marker
on its own output line.

Since 0.10 "nothing was written" also covers a failure **during**
```

In `docs/transcript-schema.md`, name the insert tools in the result row. In the `result` row's text, replace

```
Since 0.8 a successful `edit_file`/`write_file` result is
```

with

```
Since 0.8 a successful `edit_file`/`write_file`/`insert_before`/`insert_after` result is
```

- [ ] **Step 8: Run the full suite**

Run: `/usr/bin/python3 -m pytest -q`
Expected: exit code 0; `1147 passed, 1 skipped, 20 deselected` (1144 + 3).

- [ ] **Step 9: Commit**

```bash
git add dirtywork/tools.py tests/test_tools_files.py docs/machine-contract.md \
        docs/transcript-schema.md
git commit -m "perf(tools): single-pass describe_change, proved byte-identical to difflib"
```

---

### Task 12: close the self-reported test-coverage gaps (spec §6)

Tests, plus the three one-line hardenings the spec names. No behaviour changes beyond those three.

**Files:**
- Modify: `dirtywork/__main__.py` (`_load_feedback` `:639-656`; the `verify_rounds`/`verify_timeout` inheritance at `:699-702`)
- Modify: `tests/test_export_flow.py` (1 new test)
- Modify: `tests/test_runs.py` (1 new test)
- Modify: `tests/test_runner.py` (1 new test)
- Modify: `tests/test_docker_sandbox.py` (2 new tests)
- Modify: `tests/test_main.py` (4 new tests)
- Modify: `docs/transcript-schema.md` (the `feedback` row `:52` — the "null means a resume without feedback" claim becomes true)

**Interfaces:**
- Consumes: `workspace.MAX_FILES_CHANGED`, `runs.render_markdown`, `runner._MUTATING_TOOLS`, `DockerSandbox.write_file`, `__main__._load_feedback`, `__main__.DEFAULT_VERIFY_ROUNDS`, `__main__.DEFAULT_VERIFY_TIMEOUT`.
- Produces: no new names. `_load_feedback` gains one normalization; `_load_resume_target` gains two `is not None` guards.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_export_flow.py`:

```python
def test_export_truncates_files_changed_and_flags_it(tmp_path, empty_worktree):
    from dirtywork.workspace import MAX_FILES_CHANGED
    names = b"".join(f"f{i:06d}.txt\n".encode() for i in range(MAX_FILES_CHANGED + 5))
    fake = FakeDocker()
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff",
                 "--cached", "--name-only", "deadbeef" * 5], _ok(names))
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "write-tree"],
                _ok(b"treehash1234\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff", "--stat",
                 "deadbeef" * 5, "treehash1234"], _ok(b" many files changed\n"))
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff",
         "deadbeef" * 5, "treehash1234"], b"diff --git a/x b/x\n+hi\n")
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "archive",
         "--format=tar", "treehash1234"],
        _make_tar([{"name": "hello.txt", "content": b"hi"}]))
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()

    artifacts = export_run(
        DockerConfig(), slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert len(artifacts.files_changed) == MAX_FILES_CHANGED
    assert artifacts.files_changed_truncated is True
    assert artifacts.files_changed == sorted(artifacts.files_changed)
```

Append to `tests/test_runs.py`:

```python
def test_render_markdown_notes_a_truncated_files_changed_list():
    doc = runs.render_markdown("slug1", {"files_changed": ["a.py", "b.py"],
                                         "files_changed_truncated": True}, [])
    assert "**files changed (2) — list truncated**" in doc
    plain = runs.render_markdown("slug1", {"files_changed": ["a.py", "b.py"],
                                           "files_changed_truncated": False}, [])
    assert "**files changed (2)**" in plain
    assert "list truncated" not in plain
```

Append to `tests/test_runner.py`:

```python
def test_an_append_only_turn_counts_as_progress_and_does_not_stall(parts):
    # Spec §6: _MUTATING_TOOLS is what ProgressTracker reads, and append_file
    # is in it -- a run whose only work is appending must not be called stalled.
    wt, registry, sandbox, transcript, tmp = parts
    (wt / "notes.md").write_text("one\n")
    calls = [_resp(tool_calls=[_call(f"c{i}", "append_file",
                                     {"path": "notes.md", "text": f"line {i}\n"})])
             for i in range(3)]
    provider = FakeProvider(calls + [_resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m", stall_turns=2)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert (wt / "notes.md").read_text() == "one\nline 0\nline 1\nline 2\n"
```

Append to `tests/test_docker_sandbox.py`:

```python
def test_docker_write_file_still_writes_when_the_pre_read_is_oversized(started):
    # Spec §6: the pre-read is DECORATION on the write. An unreadable "before"
    # picture must not stop the write; the result just reads as a new file.
    from dirtywork.tools import MAX_READ_BYTES
    sb, fake, run_dir = started
    fake.script(["exec"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"],
                _ok(b"x" * (MAX_READ_BYTES + 1)))
    out = sb.write_file("big.txt", "replacement")
    assert out == "Wrote 11 bytes to big.txt (new file, 1 line)"
    assert len([c for c in fake.calls if _is_write_exec(c)]) == 1


def test_docker_write_file_still_writes_when_the_pre_read_is_not_utf8(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"], _ok(b"\xff\xfe"))
    out = sb.write_file("bin.dat", "now text\n")
    assert out == "Wrote 9 bytes to bin.dat (new file, 1 line)"
    assert len([c for c in fake.calls if _is_write_exec(c)]) == 1
```

Append to `tests/test_main.py`:

```python
def test_empty_feedback_is_treated_as_absent_and_recorded_null(tmp_path, monkeypatch, capsys):
    # Spec §6: normalized at PARSE, so the completed-run gate, the resume
    # prompt and run.json all agree -- which is what makes
    # docs/transcript-schema.md's "null means a resume without feedback" true.
    m, repo, rc = _first_run(monkeypatch, tmp_path, None)
    first = json.loads(capsys.readouterr().out)
    run_dir = Path(first["run_dir"])
    prior = json.loads((run_dir / "run.json").read_text())
    prior["status"] = "completed"
    (run_dir / "run.json").write_text(json.dumps(prior))
    assert m.main(["resume", run_dir.name, "--feedback", ""]) == 2
    assert "pass --feedback to continue it" in capsys.readouterr().err


def test_whitespace_only_feedback_is_absent_too_and_recorded_null(tmp_path, monkeypatch, capsys):
    m, repo, rc = _first_run(monkeypatch, tmp_path, None)
    first = json.loads(capsys.readouterr().out)
    run_dir = Path(first["run_dir"])
    prior = json.loads((run_dir / "run.json").read_text())
    prior["status"] = "max_turns"
    (run_dir / "run.json").write_text(json.dumps(prior))
    patch_provider(monkeypatch, m, lambda base_url=None: _ScriptedClient(base_url))
    assert m.main(["resume", run_dir.name, "--feedback", "   \n\t "]) == 0
    second = json.loads(capsys.readouterr().out)
    assert json.loads((Path(second["run_dir"]) / "run.json").read_text())["feedback"] is None


def test_non_utf8_feedback_file_exits_2(tmp_path, monkeypatch, capsys):
    m, repo, rc = _first_run(monkeypatch, tmp_path, None)
    first = json.loads(capsys.readouterr().out)
    run_dir = Path(first["run_dir"])
    prior = json.loads((run_dir / "run.json").read_text())
    prior["status"] = "max_turns"
    (run_dir / "run.json").write_text(json.dumps(prior))
    bad = tmp_path / "feedback.bin"
    bad.write_bytes(b"\xff\xfe not text")
    assert m.main(["resume", run_dir.name, "--feedback-file", str(bad)]) == 2
    assert f"cannot read feedback file '{bad}'" in capsys.readouterr().err


def test_explicit_null_verify_fields_in_run_json_fall_back_to_defaults(tmp_path, monkeypatch, capsys):
    # Spec §6: a hand-edited run.json carrying `"verify_rounds": null` must not
    # make args.verify_rounds None -- `.get(k, default)` would.
    m, repo, rc = _first_run(monkeypatch, tmp_path, None)
    first = json.loads(capsys.readouterr().out)
    run_dir = Path(first["run_dir"])
    prior = json.loads((run_dir / "run.json").read_text())
    prior["status"] = "max_turns"
    prior["verify_rounds"] = None
    prior["verify_timeout"] = None
    (run_dir / "run.json").write_text(json.dumps(prior))
    patch_provider(monkeypatch, m, lambda base_url=None: _ScriptedClient(base_url))
    assert m.main(["resume", run_dir.name]) == 0
    second = json.loads(capsys.readouterr().out)
    resumed = json.loads((Path(second["run_dir"]) / "run.json").read_text())
    assert resumed["verify_rounds"] == m.DEFAULT_VERIFY_ROUNDS
    assert resumed["verify_timeout"] == m.DEFAULT_VERIFY_TIMEOUT
```

- [ ] **Step 2: Run them to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_export_flow.py tests/test_runs.py tests/test_runner.py tests/test_docker_sandbox.py tests/test_main.py -q -k "truncates_files_changed or truncated_files_changed or append_only_turn or pre_read or empty_feedback or whitespace_only_feedback or non_utf8_feedback or explicit_null_verify"`

The `-k` expression names all nine of this step's tests and nothing else — verified with `--collect-only` on this tree, which reports `9/394 tests collected (385 deselected)` and lists exactly the nine written in Step 1. (A looser `… or feedback` also drags in five shipped `feedback` tests from `test_runner.py`/`test_main.py`, and `truncated_files_changed` alone never matches `test_export_**truncates**_files_changed_and_flags_it`.)

Expected: **9 selected, 2 failed, 7 passed.** Failing:

- `test_whitespace_only_feedback_is_absent_too_and_recorded_null` — `"   \n\t "` is truthy today, so `feedback` is recorded as that string. Step 3 fixes it.
- `test_explicit_null_verify_fields_in_run_json_fall_back_to_defaults` — `prior.get("verify_rounds", DEFAULT)` returns the explicit `None`, and the run dies with `TypeError: int() argument must be a string, a bytes-like object or a number, not 'NoneType'`. Step 4 fixes it.

The other seven are pins on behaviour that already works and must keep working — including **`test_empty_feedback_is_treated_as_absent_and_recorded_null`, which passes as written**: `""` is already falsy, so the completed-run gate already refuses it. Do not expect that one to be red.

- [ ] **Step 3: Normalize empty feedback at parse**

In `dirtywork/__main__.py`.

Before:

```python
    if text is None:
        return None
    if len(text) > MAX_FEEDBACK_CHARS:
```

After:

```python
    if text is None:
        return None
    if not text.strip():
        # Spec §6: an empty or whitespace-only --feedback is ABSENT, not
        # feedback. Normalized HERE, at parse, so the completed-run gate, the
        # resume prompt and run.json's `feedback` field can never disagree --
        # which is what makes docs/transcript-schema.md's "null means a resume
        # without feedback" a true statement rather than an aspiration.
        return None
    if len(text) > MAX_FEEDBACK_CHARS:
```

- [ ] **Step 4: Harden the explicit-null verify inheritance**

In `dirtywork/__main__.py`.

Before:

```python
    if getattr(args, "verify_rounds", None) is None:
        args.verify_rounds = prior.get("verify_rounds", DEFAULT_VERIFY_ROUNDS)
    if getattr(args, "verify_timeout", None) is None:
        args.verify_timeout = prior.get("verify_timeout", DEFAULT_VERIFY_TIMEOUT)
```

After:

```python
    if getattr(args, "verify_rounds", None) is None:
        # Spec §6: `.get(k) if … is not None else default`, never
        # `.get(k, default)` -- a hand-edited run.json carrying an explicit
        # `null` would otherwise leave args.verify_rounds None.
        args.verify_rounds = (prior.get("verify_rounds")
                              if prior.get("verify_rounds") is not None
                              else DEFAULT_VERIFY_ROUNDS)
    if getattr(args, "verify_timeout", None) is None:
        args.verify_timeout = (prior.get("verify_timeout")
                               if prior.get("verify_timeout") is not None
                               else DEFAULT_VERIFY_TIMEOUT)
```

- [ ] **Step 5: Make the transcript-schema claim true in writing**

In `docs/transcript-schema.md`.

Before:

```
| `feedback` | | ✓ | string \| null | 0.8: `resume --feedback`/`--feedback-file` text, verbatim (max 64 000 chars); null on a fresh run or a resume without feedback |
```

After:

```
| `feedback` | | ✓ | string \| null | 0.8: `resume --feedback`/`--feedback-file` text, verbatim (max 64 000 chars); null on a fresh run or a resume without feedback. Since 0.10 an EMPTY or whitespace-only `--feedback`/`--feedback-file` is normalized to null at parse, so it is treated as absent everywhere — including by the gate that refuses to resume a `completed` run without feedback |
```

- [ ] **Step 6: Run the affected modules and see them pass**

Run: `/usr/bin/python3 -m pytest tests/test_export_flow.py tests/test_runs.py tests/test_runner.py tests/test_docker_sandbox.py tests/test_main.py -q`
Expected: exit code 0.

- [ ] **Step 7: Run the full suite**

Run: `/usr/bin/python3 -m pytest -q`
Expected: exit code 0; `1156 passed, 1 skipped, 20 deselected` (1147 + 9).

- [ ] **Step 8: Commit**

```bash
git add dirtywork/__main__.py tests/test_export_flow.py tests/test_runs.py \
        tests/test_runner.py tests/test_docker_sandbox.py tests/test_main.py \
        docs/transcript-schema.md
git commit -m "test: close the 0.9 coverage gaps; normalize empty feedback and null verify fields"
```

---

### Task 13: 0.10.0 wrap-up — image tag, version, consolidated contract, final gate (spec §7)

No new tests. Two existing tests (`test_default_image_and_pinned_digest`, `test_version_is_in_step_with_pyproject`) must pass against the new values, and `test_stdout_and_run_json_fields_are_all_documented` must stay green now that `run.json` carries `max_tokens`.

**Files:**
- Modify: `dirtywork/sandbox/docker_args.py` (`DEFAULT_IMAGE` `:8`, the pin comment `:9-22`, `PINNED_DIGEST` `:23`)
- Modify: `dirtywork/__init__.py`, `pyproject.toml` (`version`)
- Modify: `.github/workflows/ci.yml` (`:89`)
- Modify: `docker/README.md` (every `:0.9` mention and the pin paragraph)
- Modify: `docs/machine-contract.md` (`:28`, `:62`, `:67`, `:74`, the payload-key paragraphs `:199-205` and `:242-244`)
- Modify: `tests/test_docker_args.py` (`test_default_image_and_pinned_digest` `:21-25`)

**Interfaces:** none new. `docker_args.PINNED_DIGEST` becomes `None` (its declared type `str | None` is unchanged).

- [ ] **Step 1: Move the image tag and unpin**

In `dirtywork/sandbox/docker_args.py`.

Before:

```python
DEFAULT_IMAGE = "ghcr.io/jimboschneider/dirtywork-worker:0.9"
# Pinned for 0.9.1: the multi-arch index digest of the :0.9 image published by
# the v0.9.0 release, resolved with
# `docker pull ghcr.io/jimboschneider/dirtywork-worker:0.9` and cross-checked
# against `docker image inspect --format '{{json .RepoDigests}}'` and
# `docker buildx imagetools inspect` (all agree); docker/README.md documents
# the procedure. This only ever pins a REGISTRY digest
# -- resolve_image() enforces it against a *pulled* DEFAULT_IMAGE only; a
# locally built/loaded image warns instead of refusing, and a user-supplied
# --image is never checked. MUST be re-resolved whenever the :0.9 tag is
# re-pushed. (0.8.x pinned :0.8 at
# sha256:d8ca51c169cd93b53120485cbcf3c092363587285a06b43ca97df8bd625495d8;
# 0.7.x shipped unpinned; 0.6.x pinned :0.6 at
# sha256:1f7b98898001b7064d8db396a8a5a1a324df4ce48692597fcd4381ea90e4354a;
# 0.5.x pinned :0.5 at
# sha256:3b8d019a2f20a9df55a72ed51139076f02f2feb597243a69519bc41db1029648.)
PINNED_DIGEST: str | None = "sha256:7f73656478d37a9f08769a51ba6b7bca5fceca53f914bdd4b9ef48ec11b6a172"
```

After:

```python
DEFAULT_IMAGE = "ghcr.io/jimboschneider/dirtywork-worker:0.10"
# UNPINNED for 0.10.0, on purpose: the first release of a minor ships before
# publish-image.yml has pushed the tag, so there is no registry digest to pin
# yet. 0.10.1 pins it -- pull `ghcr.io/jimboschneider/dirtywork-worker:0.10`,
# take the multi-arch index digest, cross-check it against
# `docker image inspect --format '{{json .RepoDigests}}'` and
# `docker buildx imagetools inspect` (all three must agree), and set it here;
# docker/README.md documents that procedure. When set, this only ever pins a
# REGISTRY digest -- resolve_image() enforces it against a *pulled*
# DEFAULT_IMAGE only; a locally built/loaded image warns instead of refusing,
# and a user-supplied --image is never checked. (0.9.x pinned :0.9 at
# sha256:7f73656478d37a9f08769a51ba6b7bca5fceca53f914bdd4b9ef48ec11b6a172;
# 0.8.x pinned :0.8 at
# sha256:d8ca51c169cd93b53120485cbcf3c092363587285a06b43ca97df8bd625495d8;
# 0.7.x shipped unpinned; 0.6.x pinned :0.6 at
# sha256:1f7b98898001b7064d8db396a8a5a1a324df4ce48692597fcd4381ea90e4354a;
# 0.5.x pinned :0.5 at
# sha256:3b8d019a2f20a9df55a72ed51139076f02f2feb597243a69519bc41db1029648.)
PINNED_DIGEST: str | None = None
```

- [ ] **Step 2: Update the literal-pinning test**

In `tests/test_docker_args.py`.

Before:

```python
def test_default_image_and_pinned_digest():
    assert DEFAULT_IMAGE == "ghcr.io/jimboschneider/dirtywork-worker:0.9"
    # Pinned in 0.9.1 to the :0.9 multi-arch index digest published by the
    # v0.9.0 release (docker/README.md documents how to re-resolve it).
    assert PINNED_DIGEST == "sha256:7f73656478d37a9f08769a51ba6b7bca5fceca53f914bdd4b9ef48ec11b6a172"
```

After:

```python
def test_default_image_and_pinned_digest():
    assert DEFAULT_IMAGE == "ghcr.io/jimboschneider/dirtywork-worker:0.10"
    # Unpinned in 0.10.0: the :0.10 tag is published BY this release, so there
    # is no registry digest to pin until it exists. 0.10.1 pins it, and this
    # assertion goes back to an exact digest literal then.
    assert PINNED_DIGEST is None
```

- [ ] **Step 3: Move the CI docker-live tag**

In `.github/workflows/ci.yml`.

Before:

```yaml
          tags: ghcr.io/jimboschneider/dirtywork-worker:0.9
```

After:

```yaml
          tags: ghcr.io/jimboschneider/dirtywork-worker:0.10
```

- [ ] **Step 4: Move every doc mention of the tag**

Run:

```bash
grep -rln "dirtywork-worker:0\.9\|my-worker:0\.9" --include="*.md" --include="*.yml" . | grep -v docs/superpowers
```
Expected: `docker/README.md` and `docs/machine-contract.md`. In each of those two files replace every `dirtywork-worker:0.9` with `dirtywork-worker:0.10` and every `my-worker:0.9` with `my-worker:0.10`, and update `docker/README.md`'s prose:

- `docker/README.md:5` — "The 0.9 image is a" becomes "The 0.10 image is a".
- `docker/README.md:79` — the minor-release list `(0.4.0, 0.5.0, 0.6.0, 0.7.0, 0.8.0, 0.9.0)` becomes `(0.4.0, 0.5.0, 0.6.0, 0.7.0, 0.8.0, 0.9.0, 0.10.0)`.
- `docker/README.md:83` — the patch-pin list `(0.4.1 for 0.4; 0.5.1 for 0.5; 0.6.1 for 0.6; 0.8.1 for 0.8; 0.9.1 for 0.9 — 0.7.x shipped unpinned)` becomes `(0.4.1 for 0.4; 0.5.1 for 0.5; 0.6.1 for 0.6; 0.8.1 for 0.8; 0.9.1 for 0.9; 0.10.1 for 0.10 — 0.7.x shipped unpinned)`.

- [ ] **Step 5: Consolidate the contract prose**

In `docs/machine-contract.md`.

Before:

```
Six of those keys are 0.8 additions (`stuck_on`, `files_changed`,
`files_changed_truncated`, `last_tool_result`, `last_assistant_text`,
`verify`) and the last three are 0.9's (`trimmed_turns`, `timeouts`,
`context_window_source`). Every one of them is present on every payload —
```

After:

```
Six of those keys are 0.8 additions (`stuck_on`, `files_changed`,
`files_changed_truncated`, `last_tool_result`, `last_assistant_text`,
`verify`) and the last three are 0.9's (`trimmed_turns`, `timeouts`,
`context_window_source`). **0.10 adds no stdout key at all**: its additions are
the eleventh tool (`append_file`), the `--max-tokens` flag, `max_tokens` on
`run_start` and `run.json`, `finish_reason` on `assistant` events, and
`--provider ollama` — all additive, `schema_version` still `2`. Every one of
the nine keys above is present on every payload —
```

- [ ] **Step 6: Bump the version in both sources**

In `pyproject.toml`.

Before:

```toml
version = "0.9.1"
```

After:

```toml
version = "0.10.0"
```

In `dirtywork/__init__.py`.

Before:

```python
__version__ = "0.9.1"
```

After:

```python
__version__ = "0.10.0"
```

- [ ] **Step 7: Prove the tag move is complete**

Run:

```bash
grep -rn "dirtywork-worker:0\.9\|my-worker:0\.9" --include="*.py" --include="*.md" --include="*.yml" . | grep -v docs/superpowers
```
Expected: **no output**. (Without the `grep -v`, the only remaining hits are this plan's and the 0.9 plan's **before** blocks, which must keep the old tag.)

- [ ] **Step 8: Run the full suite and confirm the two version sources agree**

Run: `/usr/bin/python3 -m pytest -q`
Expected: exit code 0; `1156 passed, 1 skipped, 20 deselected` — unchanged from Task 12 (this task adds no test).

Then:

```bash
/usr/bin/python3 -m pytest tests/test_transcript_schema.py tests/test_docker_args.py -q
/usr/bin/python3 -c "import dirtywork; print(dirtywork.__version__)"
```
Expected: exit code 0 from the first command, and `0.10.0` from the second.

- [ ] **Step 9: Prove the full 0.10 contract end to end**

Run:

```bash
/usr/bin/python3 -c "
import json
from dirtywork.builtin_tools import default_registry
names = [s['function']['name'] for s in default_registry().schemas()]
print(len(names), names)
assert len(names) == 11 and names[2] == 'append_file'
from dirtywork.providers import PROVIDER_NAMES
print(PROVIDER_NAMES)
assert PROVIDER_NAMES == ('openai', 'anthropic', 'ollama')
from dirtywork.runner import DEFAULT_MAX_TOKENS
print(DEFAULT_MAX_TOKENS)
assert DEFAULT_MAX_TOKENS == 8192
"
```
Expected: exit code 0, printing `11 [...]`, the three provider names, and `8192`.

- [ ] **Step 10: Commit**

```bash
git add dirtywork/sandbox/docker_args.py dirtywork/__init__.py pyproject.toml \
        .github/workflows/ci.yml docker/README.md docs/machine-contract.md \
        tests/test_docker_args.py
git commit -m "chore: 0.10.0 — worker image :0.10, consolidated machine contract"
```

---

## Self-review: spec coverage

Every numbered item of `docs/superpowers/specs/2026-08-20-v1rc-large-writes-atomic-ollama-design.md` maps to at least one step.

| Spec section / item | Task / step |
|---|---|
| §1.1 the failure today | Facts, not a requirement — no step. The three fixes it names are §1.2 (Tasks 2/4/5), §1.3 (Task 8) and §1.4 (Task 7) |
| §1.2 `append_file(path, text)` appends verbatim to an existing file | Task 2, Step 3 (host); Task 4, Step 5 (docker); tests in Task 2 Step 1, Task 4 Step 2 |
| §1.2 missing target → `ERROR: cannot append to '<path>': it does not exist; create it with write_file first` | `tools._append_missing`, Task 1 Step 6; used at Task 2 Step 3 (host ENOENT) and Task 4 Step 5 (guard rc 2, write rc 2) |
| §1.2 no newline inserted; the description says so | Task 5, Step 3 (`APPEND_FILE_SPEC.description`); tests `test_append_file_appends_verbatim_with_no_separator`, `test_append_file_description_warns_about_the_leading_newline` |
| §1.2 cap 1: `text` argument via a shared `_append_oversized` in `tools.py`, imported by `docker.py` like `MAX_WRITE_BYTES` | Task 1, Step 6; imported at Task 3, Step 4; used at Task 2 Step 3 and Task 4 Step 5 |
| §1.2 `_oversized`'s write-file wording must never surface from an append | Task 4, Step 5 (`append_file` never calls `_oversized`); asserted in `test_append_file_oversized_text_argument_costs_no_exec` and `test_append_oversized_wording_is_not_the_write_file_wording` |
| §1.2 parity tests for each of the three caps | Task 4, Step 2 (`test_append_file_matches_the_host_text_for_every_shared_refusal`), which also covers the does-not-exist and non-UTF-8 strings |
| §1.2 cap 2: current file under `MAX_READ_BYTES`, refused with the RESULT-cap string | Task 2, Step 3 (`os.stat` on the probe fd); Task 4, Step 5 (guard exec `stat -c %s`) |
| §1.2 cap 3: `len(old) + len(text)` over `MAX_WRITE_BYTES` → the shared string | `tools._result_too_big`, Task 1 Step 6; Task 2 Step 3 (twice — before and after the read); Task 4 Step 5 |
| §1.2 host: `resolve_in_worktree(writing=True)`; the §2.2 probe unchanged; ELOOP/FIFO byte-identical; ENOENT is the does-not-exist branch | Task 2, Step 3; tests `…refuses_a_symlink`, `…refuses_a_fifo`, `…refuses_a_missing_target` |
| §1.2 host: second open via `_open_regular(O_RDONLY, max_size=MAX_READ_BYTES)`, O_NONBLOCK, S_ISREG, re-blocking | Task 2, Step 3 (the existing helper is used unchanged) |
| §1.2 host: strict UTF-8, `<path> is not valid UTF-8 text; append_file only works on text files` | `tools._not_utf8`, Task 1 Step 6; Task 2 Step 3; test `test_append_file_refuses_non_utf8_with_the_tool_named` |
| §1.2 host: read fd's `st_ino`/`st_dev` must match the probe's | Task 2, Step 3 |
| §1.2 host: `_write_atomic(target, old+text, verb="append")` | Task 2, Step 3; Task 1 Steps 5 (the `verb` parameter) and 1 (`test_write_atomic_append_verb_changes_only_the_generic_tail`) |
| §1.2 host: `ENOENT` on the probe is the does-not-exist error, **never §2.2's new-file branch** — in `_write_atomic` too, not only in `append_file`'s own probe | Task 1, Step 5 (`must_exist`); Task 2, Step 3 (`must_exist=True`); test `test_append_file_refuses_when_the_target_vanishes_before_the_promote` |
| §1.2 host: never creates parent directories | Task 2, Step 3 (`create_parents` not passed); test `test_append_file_never_creates_parent_directories` |
| §1.2 docker: `_rel(writing=True)` and three execs | Task 4, Step 5; test `test_append_file_takes_three_execs_guard_read_write` |
| §1.2 docker: guard script `[ -e ] || exit 2; [ -f ] || exit 3; stat -c %s`, FIFO refused before any read | Task 4, Steps 4 and 5; tests `…guard_rc2…`, `…guard_rc3_refuses_before_any_read` |
| §1.2 docker: the exact result-cap string even for an unreadable file | Task 4, Step 5; tests `…guard_size_over_the_read_cap…`, `…guard_size_plus_text_over_the_write_cap` |
| §1.2 docker: `_read_raw(strict=True, tool="append_file")` | Task 3, Step 5 (the parameter); Task 4, Step 5 (the call); test `test_append_file_non_utf8_names_append_file` |
| §1.2 docker: `text` capped before any exec | Task 4, Step 5; test `test_append_file_oversized_text_argument_costs_no_exec` (`assert not fake.calls`) |
| §1.2 docker: write script's missing-target guard exits 2; other failures exit 1 and wrap stderr | Task 4, Step 4 (`APPEND_WRITE_SCRIPT`), Step 5 (`_append_write`); tests `…write_exec_rc2_still_refuses_as_missing`, `…write_exec_failure_wraps_stderr` |
| §1.2 result string `describe_change(..., verb="Appended to")`, `+A -0` vs `+A -1` | Task 2, Step 3; tests `…appends_verbatim…`, `…header_when_the_file_did_not_end_in_a_newline` |
| §1.2 `APPEND_FILE_SPEC` right after `WRITE_FILE_SPEC`; params, caps, exact description | Task 5, Step 3; asserted in `test_append_file_dispatches_and_declares_its_caps` and `test_append_file_description_warns_about_the_leading_newline` |
| §1.2 updates: `tools.py` | Tasks 1 and 2 |
| §1.2 updates: `Sandbox` Protocol method + the tool enumeration in its docstring | Task 5, Step 4 |
| §1.2 updates: `HostSandbox` (+`_check_budget` wrap) | Task 2, Step 4 |
| §1.2 updates: `DockerSandbox` | Task 4, Step 5 |
| §1.2 updates: `runner._MUTATING_TOOLS` | Task 5, Step 5; tests in Task 5 Step 1 and Task 12 Step 1 |
| §1.2 updates: the system-prompt file rule | Task 5, Step 6 |
| §1.2 updates: the wire fixture regenerated | Task 5, Step 7 |
| §1.2 updates: ten → eleven in README ×2, `docs/security.md`, `docs/transcript-schema.md` (enum + result row), `docs/machine-contract.md` Tools subsection, `builtin_tools.py` docstring | Task 5, Steps 3, 8, 9, 10, 11, 12 |
| §1.2 updates: `test_transcript_schema.py` hand list AND the `…_ten_tools` → `…_eleven_tools` rename; `test_schemas_shape`'s name set; `FakeSandbox` | Task 5, Step 1 |
| §1.3 fires for (a) `tc.error is not None` + `length` | Task 8, Step 6 |
| §1.3 fires for (b) `tc.error is None`, `length`, a required parameter missing — checked BEFORE dispatch | Task 8, Steps 5 and 6; test `test_length_truncation_with_empty_args_counts_as_malformed_args_not_bad_args` |
| §1.3 path recovery: the exact regex, `json.loads` unescape inside `try/except ValueError`, 200-char truncation, `!r` | Task 8, Step 4 (`_recovered_path`); tests `…recovered_path_is_truncated_and_rendered_with_repr`, `…invalid_escape_degrades_to_generic` |
| §1.3 Anthropic `raw_arguments == ""` degrades to generic | Task 8, Step 4; test `test_length_truncation_with_no_raw_arguments_gives_the_generic_form` |
| §1.3 the two exact strings | Task 8, Step 4 (`truncated_call_result`); asserted verbatim in the rewritten `test_length_finish_reason_gives_helpful_hint` and `_GENERIC_TRUNCATION` |
| §1.3 `NUDGES["truncated"]` reworded; `tests/test_runner.py:682` needs no change | Task 8, Step 3; test `test_truncated_nudge_names_write_file_and_append_file`. The `:682` comparison is against the constant, so it is untouched |
| §1.3 failure accounting otherwise unchanged | Task 8, Step 6 — both branches call `failures.record("malformed_args")` and nothing else changes |
| §1.3 the recovery strings are contract (§7) | Task 8, Step 7 (`docs/machine-contract.md`) |
| §1.4 `--max-tokens` via `_add_run_flags`, `type=_positive_int`, `default=None if resume else DEFAULT_MAX_TOKENS` | Task 7, Step 6 |
| §1.4 resume inheritance in the hardened `is not None` shape, covering pre-0.10 `run.json` | Task 7, Step 6; test `test_resume_inherits_max_tokens_and_a_pre_0_10_run_falls_back_to_the_default` |
| §1.4 stated consequence: a resumed 0.9 run moves 4096 → 8192 | Task 7's header and the `docs/machine-contract.md` bullet in Step 8 |
| §1.4 threaded `__main__` → `Runner.__init__` → the single `provider.chat` call as an explicit kwarg | Task 7, Steps 3 and 6; test `test_max_tokens_defaults_to_8192_and_reaches_the_provider` |
| §1.4 adapters keep their own 4096 defaults for direct callers | Task 7 — no step changes either adapter; `tests/test_llm.py:102` (`payload["max_tokens"] == 4096`) is untouched and stays green |
| §1.4 `char_budget = int(max(0, window - max_tokens) * BUDGET_FRACTION * CHARS_PER_TOKEN)` | Task 7, Step 3; test `test_char_budget_subtracts_max_tokens_from_the_window` |
| §1.4 preflight refuses `>=` the window with the exact message, exit 2 | Task 7, Step 6; test `test_max_tokens_at_or_over_the_context_window_exits_2`. The rule is flat, so Task 7 Step 7 repairs the seven shipped `tests/test_main.py` tests that ran on sub-8192 windows rather than exempting them |
| §1.4 recorded on `run_start` and `run.json`; NOT on the stdout payload | Task 7, Steps 3 and 6; tests `test_run_start_records_max_tokens`, `test_max_tokens_flag_is_recorded_in_run_json`. No step touches `_emit_result`/`_contract_fields` |
| §1.4 Anthropic note: "pass `--max-tokens 4096` for models that cap output there" | Task 7, Step 9 (the machine-contract bullet) |
| §1.4 docs: machine-contract flag list, `docs/operating.md`, `docs/transcript-schema.md` `run_start` + `run.json` | Task 7, Step 9 |
| §1.5 `assistant` gains `finish_reason`, written as `… if isinstance(…, str) else None` | Task 7, Step 3; tests `test_assistant_event_records_finish_reason`, `…_is_null_for_a_non_string` |
| §1.5 documented as `string \| null`, open enum | Task 7, Step 9 |
| §1.5 `ASSISTANT_FIELDS` + a doc-token assertion mirroring `RUN_END_FIELDS` | Task 7, Step 4 |
| §2.1 scope: every host in-place write and docker's `_write_raw`/`_append_raw` | Task 6 Steps 3–4 (host `write_file`/`_transform_file`), Task 2 Step 3 (host `append_file`), Task 3 Step 6 (docker `_write_raw`), Task 4 Steps 4–5 (docker append) |
| §2.1 honest threat model: robustness, not security; `O_NOFOLLOW` refusals as deterministic as today | Task 1's `_write_atomic` docstring (Step 5); Task 6, Step 11 (`docs/security.md`) |
| §2.2 signature and `verb`-selected wording | Task 1, Step 5 |
| §2.2 step 0: `create_parents` mkdir, `OSError` → generic tail; transforms/append never mkdir | Task 1, Step 5; test `test_write_atomic_creates_parents_only_when_asked` |
| §2.2 step 1: side-effect-free probe, ELOOP/ENXIO/other/ENOENT, `fstat` + `S_ISREG`, the doubled-path refusal preserved verbatim, capture `st_mode`/`st_nlink`/`st_ino`/`st_dev` | Task 1, Step 5; tests `…refuses_a_symlink…`, `…refuses_a_fifo…` |
| §2.2 step 2: hardlink fallback, probe fd open until replace or fallback, closed in `finally` | Task 1, Step 5; test `test_write_atomic_writes_through_a_hardlinked_target` |
| §2.2 step 3: same-directory temp, generated name, `O_EXCL` `0o600`, `fchmod` to the target's mode or `0o644 & ~_UMASK`, `os.close` BEFORE `os.replace` | Task 1, Steps 4 and 5; tests `…creates_a_new_file_with_umask_default_mode`, `…preserves_an_existing_files_mode`, `…promotes_by_rename_so_the_inode_changes` |
| §2.2 step 4: one catch boundary; `OSError` → unlink + return; other `BaseException` → unlink + re-raise; the pre-promote `os.close` may itself raise and must still return a string | Task 1, Step 5; tests `…returns_an_error_string_on_an_oserror_during_the_write`, `…reraises_a_non_oserror_and_unlinks_its_temp`, `…surfaces_a_close_failure_without_raising` |
| §2.2 step 5: `EACCES`/`EROFS` with a live probe fd → fd fallback; `ENOENT` probe → generic tail with the temp errno; any other temp error → generic tail | Task 1, Step 5; tests `…falls_back_to_the_fd_in_an_unwritable_directory`, `…refuses_a_new_file_in_an_unwritable_directory` |
| §2.3 what "nothing was written" means, plus the two named exceptions and the inode change | Task 6, Step 10 (`docs/machine-contract.md` and `docs/operating.md`, both anchors replaced) |
| §2.4 the accepted race delta, stated in `docs/security.md`'s host-mode notes | Task 6, Step 11 |
| §2.5 anchored full-shape regex, never a bare glob | Task 1, Step 4; test `test_is_temp_name_ignores_a_worker_file_that_only_starts_like_one` |
| §2.5 sweep at `HostSandbox.start()` and folded into `measure_worktree`'s walk at `finalize` | Task 6, Steps 6–8; tests `test_host_sandbox_start_sweeps_a_leftover_temp_and_says_so`, `test_host_sandbox_finalize_sweeps_and_reports` |
| §2.5 docker: one `find … -delete` against the still-alive worker container, immediately before `DockerSandbox.finalize()` stops it (execution amendment, 2026-08-23: relocated from `export_run`, whose `/work` mount is readonly, in Task 6's fix round 1; `TMP_FIND_REGEX` also tightened to `[^/]+`) | Task 6, Step 9 as amended; tests `test_finalize_stops_container_calls_export_run_and_host_read_tree` (worker-side sweep, extended in the fix round), `test_export_run_never_execs_a_sweep_the_export_volume_is_readonly` (the negative), `test_tmp_find_regex_is_per_component_not_greedy_across_a_slash` |
| §2.5 swept count → stderr, never silent | Task 6, Steps 8 and 9 |
| §2.6 the `_write_raw` script, verbatim, with every guard | Task 3, Step 4 (`WRITE_SCRIPT`); test `test_write_exec_uses_the_atomic_script_and_a_sibling_temp` |
| §2.6 host-generated temp name passed as `$2`; worker data never in the script | Task 1 Step 4 (`tmp_name`), Task 3 Step 4 (`_sibling_tmp`), Step 6 |
| §2.6 the `_append_raw` write script | Task 4, Step 4; test `test_append_file_write_script_shape` |
| §2.6 the counted test churn → one `_is_write_exec` helper | Task 3, Steps 1, 2, 8, 9, 10 (the count corrected from 12 to 11, with a verification step) |
| §3.1 `OllamaClient(OpenAICompatClient)`, `name`, default base URL, `__init__` override, `DEFAULT_BASE_URLS`, `PROVIDER_NAMES`, `get_provider`, CLI `choices` | Task 9, Steps 4 and 5; tests `test_name_and_default_base_url`, `test_explicit_empty_base_url_is_not_replaced_by_the_ollama_default` |
| §3.1 `context_window()` overridden to return `None` | Task 9, Step 4; test `test_context_window_is_always_none_not_lm_studios_table` |
| §3.1 `loaded_context_window`: `/api/ps`, same probe timeout, `models` list guard, `model`-key-only match, first match, positive non-bool int, no `state` check | Task 9, Step 4; tests `…returns_the_loaded_num_ctx`, `…probes_api_ps_on_the_origin_with_the_short_timeout`, `…matches_the_model_key_only`, `…returns_none_on_the_first_match_without_scanning_on`, `…none_for_every_unusable_shape`, `…none_when_unreachable` |
| §3.1 source `provider:ollama:server`, then `default` + the existing warning | Task 9, Step 2 (`test_resolve_context_window_prefers_the_server_then_falls_to_default`) — `runner.resolve_context_window` needs no change |
| §3.1 cold start stated | Task 9, Step 4 (the docstring), Step 12 (both docs), Step 13 (README) |
| §3.1 `_ENDPOINT_HINTS["ollama"]`; a `_MODEL_HINTS` dict replacing the ternary; "not available" for ollama | Task 9, Step 8; tests `test_ollama_missing_model_says_not_available_and_names_ollama_run`, `test_openai_missing_model_still_says_lms_load` |
| §3.1 resume: no `openai`↔`ollama` carve-out, comment amended | Task 9, Step 8; test `test_resume_refuses_switching_between_openai_and_ollama` |
| §3.1 wire shape identical; parallel tool calls unverified, noted | Task 9's header, the adapter docstring (Step 4), the README (Step 13) and `docs/operating.md` (Step 12) |
| §3.2 the eight fixture filenames `ProviderContract` hard-codes | Task 9, Step 1 |
| §3.2 `/api/ps` cases as standalone `RecordingTransport` tests in `tests/test_provider_ollama.py`, no fixture files | Task 9, Step 2 |
| §3.2 `pyproject.toml` marker + `addopts` gains `not ollama`; the docker CI job unaffected | Task 9, Step 6 |
| §3.2 a NEW `tests/test_live_ollama.py` with its own marker + skipif; `tests/test_live.py` untouched | Task 9, Step 2 |
| §3.2 `tests/test_providers.py:17-22`, `:93-97`, `:103` | Task 9, Step 9 (`:103`'s `test_every_provider_name_is_constructible` needs no edit — it iterates `PROVIDER_NAMES`) |
| §3.2 docs: `docs/machine-contract.md:106-109` and `docs/operating.md:258-260` REPLACED; README `:65-68`, `:83-85`, `:88-91`; an operating.md Ollama quickstart | Task 9, Steps 12 and 13 |
| §4.1 `--branch-from @<slug>` through the same rule as `runs snapshot`, lifted into one shared helper | Task 10, Steps 3, 4, 5; tests in Steps 1 |
| §4.1 a valid-but-missing slug keeps `unknown run '<slug>' (no run dir under <RUNS_DIR>)` | Task 10, Step 5 (the `is_dir()` check is after the shared helper); pinned by the existing `tests/test_main.py:1941` |
| §4.2 `EMPTY_TREE_SHA` replaced by a per-invocation id via `printf '' \| git hash-object -t tree --stdin`, after the `symbolic-ref` check | Task 10, Steps 11 and 12; test `test_snapshot_worktree_computes_the_empty_tree_id_per_invocation` (the "two uses" count corrected to one, with a verification step) |
| §4.3 two-pass hashing: pass 1 without `-w` for files and symlink targets, compared against `ls-tree -r -z` with the same normalization; equal → `None`; different → re-run with `-w` and rebuild from the second pass | Task 10, Steps 11 and 12; tests `test_snapshot_worktree_writes_no_loose_objects_on_a_no_op`, `test_snapshot_worktree_tree_only_references_written_blobs` |
| §4.4 explicit BFS, one batched index-aware `_ignored_relpaths` per DEPTH, never `--no-index`, ignored dirs dropped before descending | Task 10, Step 10; tests `…prunes_an_ignored_directory_before_descending`, `…runs_one_check_ignore_per_tree_depth` |
| §4.4 the named regression: a tracked `build/keep.txt` survives an ignored `build/` | Task 10, Step 7 (`test_snapshot_worktree_keeps_a_tracked_file_inside_an_ignored_directory`) |
| §4.4 the control-character/UTF-8 guard factored out and applied to directory paths BEFORE `check-ignore` | Task 10, Steps 9, 10, 12 |
| §5.1 `_read_raw` gains `tool`; `_transform_file` gains `tool` passed by all four callers; `write_file`'s pre-read passes `tool="write_file"`; the docstring sentence removed | Task 3, Steps 5 and 7 |
| §5.1 docker-mode non-UTF-8 tests for insert/apply **and `append_file`** | Task 3, Step 2 (`test_transform_non_utf8_refusals_name_the_tool_and_match_the_host`); Task 4, Step 2 (`test_append_file_non_utf8_names_append_file`) |
| §5.2 single-pass `describe_change` via `get_grouped_opcodes(n)` + `_format_range_unified`'s two special cases | Task 11, Steps 3 and 4 |
| §5.2 a seeded, randomized property test (≥1000 pairs, no-trailing-newline / duplicate-line / empty sides) asserting byte-equality with `difflib.unified_diff` | Task 11, Step 1 (`test_unified_diff_lines_match_difflib_on_seeded_random_pairs`, 1000 pairs from a vocabulary containing a duplicate and blank-ish lines, with 0-length sides and stripped final newlines) |
| §5.3 CRLF rendering documented in the tool-results paragraph | Task 11, Step 7; test `test_describe_change_renders_crlf_as_git_does` |
| §5.4 `docs/transcript-schema.md` result row names all mutating tools | Task 5, Step 11 (`append_file`) and Task 11, Step 7 (`insert_before`/`insert_after`) |
| §5.4 README's "insert_* echoes a diff" sentence gains the new-file `write_file` exception | Task 5, Step 8 |
| §6 docker `files_changed` truncation + the Markdown "— list truncated" note | Task 12, Step 1 (`test_export_truncates_files_changed_and_flags_it`, `test_render_markdown_notes_a_truncated_files_changed_list`) |
| §6 `_MUTATING_TOOLS` counts `insert_*`/`apply_edits`/`append_file` as stall progress | Task 5, Step 1 (the set); Task 12, Step 1 (`test_an_append_only_turn_counts_as_progress_and_does_not_stall`) |
| §6 docker `write_file` pre-read failure modes still write and render "new file" | Task 12, Step 1 (two tests) |
| §6 `--feedback ""`/whitespace-only normalized to `None` at parse, treated as absent AND recorded null; docstring + test | Task 12, Steps 3 and 5; two tests in Step 1 |
| §6 non-UTF-8 `--feedback-file` → exit 2 test | Task 12, Step 1 (`test_non_utf8_feedback_file_exits_2`) |
| §6 explicit-`null` `verify_rounds`/`verify_timeout` hardened + test | Task 12, Steps 4 and 1 |
| §7 contract additions, all additive, `schema_version` stays 2 | Tasks 5, 7, 8, 9; consolidated in Task 13, Step 5. No step changes `schema_version` |
| §7 `DEFAULT_IMAGE` → `:0.10`, CI tag, every doc mention, `PINNED_DIGEST = None` with the comment updated | Task 13, Steps 1, 3, 4, 7 |
| §7 `test_default_image_and_pinned_digest` pins both literals — tag updated, digest assertion becomes `is None` with a 0.10.1 comment | Task 13, Step 2 |
| §7 version `0.10.0` in `pyproject.toml` + `dirtywork/__init__.py` | Task 13, Step 6 |
| §7 tests: TDD, new tests in existing modules except the three sanctioned new files, suite green on 3.9 after every task | Every task's Step 1 (tests first) and final full-suite step; the three new files are created in Task 9 |
| §7 docs: README, `docs/operating.md`, `docs/machine-contract.md`, `docs/security.md`, `docs/transcript-schema.md`, `docker/README.md` | Tasks 5, 6, 7, 8, 9, 11, 12, 13 |

**Deliberately NOT carried into this plan** (Global Constraints: the controller
owns every outward-facing action, and no task may run `gh`):

- §3.2's "Issue #22's Ollama follow-up closes with this section".
- §7's "Issue hygiene at ship: #36, #40, #41, #42, #43, #47 close; #22's follow-up resolved; #48 starts on the 0.10.0 release."

Both are actions on GitHub, not repository changes. They are the controller's to
take once this branch is merged and 0.10.0 is cut.

## Ambiguities found in the spec, and the resolution taken

1. **`_write_atomic`'s signature has no way to render `'<path>'`.** §2.2 gives
   `_write_atomic(target, data, *, verb, create_parents)` but every refusal it
   specifies renders the MODEL-FACING path (`cannot write '<path>'`,
   `'<path>' is a symlink`), which `target` — a `_worktree_candidate` absolute
   path — is not. **Resolved:** one extra keyword-only parameter,
   `path: str`, carrying the caller's own argument. Without it the shipped
   strings would silently change from `'notes.md'` to
   `'/Users/…/.worktrees/dw-…/notes.md'`, which §2.2 explicitly forbids
   ("preserved verbatim").
2. **The swap-between-opens refusal has no text.** §1.2 says a read fd whose
   `st_ino`/`st_dev` differ from the probe's "refuses with the generic tail",
   but the generic tail is `cannot append to '<path>': {e}` and there is no
   exception here. **Resolved:** `ERROR: cannot append to '<path>': the file
   changed between opening it and reading it`, used for both the ino/dev
   mismatch and the errno-less `_open_regular` failure that can only mean the
   same thing.
3. **The append's non-regular-file wording differs per backend within the
   spec.** §1.2 says the host probe "runs unchanged" (yielding
   `ERROR: '<path>' is not a regular file (refusing FIFO/device/socket)` from
   ENXIO, or the `cannot append to '<path>': '<abs>' is not a regular file …`
   form from the `S_ISREG` branch) while docker's rc 3 yields
   `ERROR: cannot append to '<path>': not a regular file`. It also says "both
   modes emit identical strings from identical conditions". **Resolved:** both
   backends implement exactly what the spec spells for them; the parity tests
   cover the five strings the spec explicitly calls out (three caps,
   does-not-exist, non-UTF-8), and Task 4's header states the divergence
   openly rather than absorbing it silently.
4. **The `'cat > "$1"'` matcher count.** §2.6 says twelve, "ten substring
   matchers in that file, one in `tests/test_docker_runs.py`". On this tree
   there are eleven, all in `tests/test_docker_sandbox.py`;
   `tests/test_docker_runs.py:153` contains `cat > {dest}` but it is the test's
   own volume-seeding heredoc, not a matcher. **Resolved:** Task 3 Step 1
   verifies the inventory with a grep before editing, rewrites the ten
   substring matchers and the one exact-argv assertion, and leaves
   `test_docker_runs.py` untouched.
5. **`EMPTY_TREE_SHA` "two uses".** §4.2 says two; `grep -rn EMPTY_TREE_SHA
   dirtywork/ tests/` finds the definition plus one reader. **Resolved:** Task
   10 Step 8 re-verifies the inventory, then Steps 11–12 delete both lines and
   replace the single reader.
6. **The sweep note's `(s)`.** §2.5 writes the stderr note as
   `swept N stale temp file(s)`. **Resolved:** rendered with real
   pluralization (`swept 1 stale temp file` / `swept 2 stale temp files`),
   matching every other pluralized string in the codebase
   (`removed N non-blank line{plural}`); `(s)` is read as prose notation, not
   as a literal.
7. **Ollama fixtures "live-captured where Ollama produced them".** A plan
   cannot capture from a server it has no access to. **Resolved:** Task 9's
   header states plainly that the eight files are hand-built to Ollama's
   OpenAI-compatible shape and assert OUR PARSER, that `tests/test_live_ollama.py`
   is what proves a real server still matches, and that an implementer with a
   live Ollama should re-capture the four capturable ones and confirm they
   still parse identically.
8. **`docker._read_raw`'s legacy UTF-8 branch becomes unreachable.** §5.1 has
   every strict-read caller pass a `tool`, so the `refusing to edit` string has
   no shipped caller left, but the spec still declares the parameter as
   `tool: str | None = None`. **Resolved:** the parameter keeps its default and
   the legacy string stays as the `None` branch, with a comment saying no
   shipped caller reaches it and why it is kept (a direct caller of the private
   method must not get `None only works on text files`). A test pins it.

## Type consistency checklist

- `tools._UMASK: int` — read once at import via `os.umask(0)`/restore, before any thread exists. Only `_write_atomic` reads it, and only for a NEW file (`0o644 & ~_UMASK`).
- `tools.TMP_PREFIX: str` (`".dw-tmp."`), `tools.TMP_NAME_RE: re.Pattern`, `tools.TMP_FIND_REGEX: str`. `TMP_NAME_RE` is used only through `is_temp_name` (always `fullmatch`); `TMP_FIND_REGEX` is used only as an argument to `find -regex` inside the container and is imported by `dirtywork/sandbox/export.py`, never by `docker.py`.
- `tools.tmp_name(basename: str) -> str` — always `f".dw-tmp.{basename}.{8 lowercase hex}"`. Called with a POSIX basename in docker (`posixpath.basename(rel)`) and with `Path.name` on the host; it never inspects the string it is given.
- `tools.is_temp_name(name: str) -> bool` — takes a bare NAME, never a path. `budget._measure_posix` calls it with `os.fwalk`'s entry name, which is exactly that.
- `tools._write_all(fd: int, data: bytes) -> None` — raises `OSError`; loops until the buffer is gone. It is the single monkeypatch point the atomicity tests use, so every write inside `_write_atomic` goes through it and none uses bare `os.write`.
- `tools._unlink_quietly(p: Path) -> None` — never raises.
- `tools._write_atomic(target: Path, data: bytes, *, path: str, verb: str = "write", create_parents: bool = False, must_exist: bool = False) -> str | None` — `None` means success; a non-empty `ERROR: …` string means refuse. `verb` is exactly `"write"` or `"append"`; anything else is treated as `"append"` by the `lead` ternary, and no caller passes anything else. `must_exist` is `True` at exactly one call site (`tools.append_file`) and turns an ENOENT probe into `_append_missing(path)` instead of the new-file branch; `create_parents` and `must_exist` are never both `True`. The ONLY exception it lets escape is a non-`OSError` `BaseException` re-raised after the temp is unlinked — and never an `EBADF` from its own cleanup, because the temp fd handle is cleared before the pre-promote `os.close` and both handlers' cleanup closes are wrapped in `try/except OSError: pass`.
- `tools._result_too_big(size: int) -> str` — always a string, never `None`; `_check_write_size(new_text: str) -> str | None` is the falsy-means-ok wrapper both `_transform_file`s already test with `if too_big:`.
- `tools._append_oversized(encoded: bytes) -> str | None` — takes BYTES (the caller has already encoded), same falsy-means-ok convention as `docker._oversized`.
- `tools._append_missing(path: str) -> str` and `tools._not_utf8(path: str, tool: str) -> str` — always strings. `path` is the model-facing path in every call site, host and docker alike; `tool` is a registered tool name (`"write_file"`, `"edit_file"`, `"apply_edits"`, `"insert_before"`, `"insert_after"`, `"append_file"`), never `None` — `docker._read_raw` checks for `None` before calling.
- `tools.append_file(worktree: Path, path: str, text: str) -> str`; `HostSandbox.append_file(path: str, text: str) -> str`; `DockerSandbox.append_file(path: str, text: str) -> str`; `Sandbox.append_file(path: str, text: str) -> str`. All four are identical after the leading `worktree`/`self`, all return `str`, and none raises anything but `BudgetExceeded`/`SandboxError`.
- `tools.DIFF_CONTEXT_LINES: int` (2) — the ONLY place the context width is written; `_unified_diff_lines` reads it and nothing else does. `tools._format_range_unified(start: int, stop: int) -> str` returns `"N"` or `"N,M"`, never anything else. `tools._count_opcodes(opcodes, old_lines: list) -> tuple` is always `(int, int, int)`. `tools._unified_diff_lines(matcher, old_lines, new_lines, path) -> list` is a list of `str` and is `[]` for an unchanged pair. `tools._line_counts` no longer exists.
- `budget.BudgetReport.swept: int` — always an `int`, always present (defaulted `0`), and always LAST in the field order, so every existing four-positional construction stays valid. `budget.measure_worktree(..., sweep_temps: bool = False)`; `_measure_posix` sweeps, `_measure_windows` accepts and documents that it ignores the flag.
- `HostSandbox._measure(*, sweep_temps: bool = False) -> BudgetReport` — the annotation is corrected from the pre-0.10 `-> dict`, which was already wrong. `HostSandbox._sweep_note(report: BudgetReport) -> None` prints to stderr or returns; never raises, never records.
- `docker._PROMOTE: str`, `docker.WRITE_SCRIPT: str`, `docker.APPEND_GUARD_SCRIPT: str`, `docker.APPEND_WRITE_SCRIPT: str` — module-level constants, each a single `sh -c` script body passed as ONE argv element. Both write scripts end with `_PROMOTE`, asserted by `test_append_file_write_script_shape`.
- `docker._sibling_tmp(rel: str) -> str` — a POSIX RELATIVE path in the same directory as `rel`; built with `posixpath`, never `os.path`, because it names a path inside the Linux container regardless of the host OS.
- `DockerSandbox._read_raw(path, *, strict: bool = False, tool: str | None = None) -> tuple` — `(text: str, None)` or `(None, error: str)`, unchanged shape. `DockerSandbox._transform_file(path, transform, *, tool: str) -> str` — `tool` is REQUIRED (keyword-only, no default), so a new caller cannot forget it.
- `DockerSandbox._append_guard(path: str, rel: str, text_len: int) -> tuple` — `(size: int, None)` or `(None, error: str)`; `size` is discarded by `append_file` (bound to `_size`) because the read that follows re-derives the content, but it is returned rather than dropped so the guard has one return shape. `DockerSandbox._append_write(path: str, rel: str, encoded: bytes) -> str` — `""` on success (falsy), an `ERROR: …` string otherwise: the same convention `_write_raw` already uses.
- `runner.DEFAULT_MAX_TOKENS: int` (8192). `Runner.max_tokens: int` is always an `int` — the CLI's `_positive_int` guarantees it, and a directly-built `Runner` gets the default. `Runner.char_budget: int` is `>= 0` for every combination, including a cap larger than the window.
- `runner.TRUNCATED_ARGS_SCAN_CHARS: int` (8192), `runner.TRUNCATED_PATH_CHARS: int` (200). `runner._recovered_path(raw_arguments) -> str | None` accepts ANY value (non-`str` → `None`) and never raises. `runner.truncated_call_result(tool: str, raw_arguments) -> str` always returns a string. `Runner._missing_required(name: str, args) -> bool` accepts a non-dict `args` and an unknown tool, returning `False` for both.
- `assistant` event `finish_reason` is `str | None` — never any other JSON type, because the write site coerces with `isinstance(finish_reason, str)`.
- `run_start.max_tokens` and `run.json["max_tokens"]` are `int`; `args.max_tokens` is `int | None` between `_parse_args` and `_load_resume_target` (a resume with no flag), and `int` from there on. Every read outside those two functions uses `getattr(args, "max_tokens", DEFAULT_MAX_TOKENS)`, per the project rule about new flags and `argparse.Namespace`-building tests.
- `providers.PROVIDER_NAMES: tuple` is `("openai", "anthropic", "ollama")` — order matters, because `--provider`'s `choices=list(PROVIDER_NAMES)` renders it in `--help` and `get_provider`'s error lists it. `DEFAULT_BASE_URLS` keys equal `set(PROVIDER_NAMES)`, asserted.
- `OllamaClient.name` is the CLASS attribute `"ollama"` (never assigned per-instance), so `resolve_context_window`'s `getattr(provider, "name", "provider")` yields `provider:ollama:server`/`provider:ollama`. `OllamaClient.context_window(model) -> None` — always `None`, so `resolve_context_window`'s `if window:` never produces a `provider:ollama` source. `OllamaClient.loaded_context_window(model) -> int | None` returns only a non-bool `int > 0` or `None`, and catches only `LLMError` (which `LLMTimeout` subclasses).
- `__main__._MODEL_HINTS: dict[str, str]` and `__main__._MODEL_ABSENT_WORD: dict[str, str]` — both `.get`-ed with a default, so an unknown provider never raises. The `{model}` placeholder is substituted with `str.replace`, never `str.format`, because a model id is operator input and may contain braces.
- `rundir._SLUG_RE: re.Pattern`; `rundir.run_dir_for(slug: str, runs_dir: Path) -> Path` raises only `RunDirError` and does NOT require the directory to exist. `runs._run_dir_for(slug) -> Path` re-raises as `RunsError` with the identical message text; `__main__._resolve_branch_from` re-raises as `PreflightFailure`. `runs._SLUG_RE` no longer exists (`runs.py` keeps `import re` for `_DOCKER_ABSENT_RE`).
- `workspace._check_snapshot_path(worktree: Path, rel: str) -> None` — raises only `WorkspaceError`; `rel` is always a repo-relative POSIX string, never a `Path`.
- `workspace._walk_worktree(worktree: Path) -> tuple` — the shape is UNCHANGED: `(files: list[(str, bool)], links: list[(str, str)], skipped: int, unreadable_dirs: list[(str, OSError)])`. The root's own entry in `unreadable_dirs` is recorded as `"."`, matching what `os.walk`'s `onerror` produced before.
- `workspace._hash_entries(...) -> list` and `workspace._head_entries(...) -> list` return lists of the SAME normalized `"<mode> <sha>\t<rel>"` string, which is why they can be compared with `sorted(a) == sorted(b)`; `_hash_entries`' output is also exactly what `update-index -z --index-info` consumes, so no second formatting step exists. `workspace.EMPTY_TREE_SHA` no longer exists; the per-invocation `empty_tree` is a local `str`.
- Python 3.9 throughout: `X | None` appears only in annotations, in modules that already carry `from __future__ import annotations`; the one new module, `dirtywork/providers/ollama.py`, gets that import as its first line. No `match`, no runtime union, no `|` inside an `isinstance` call. `os.scandir` as a context manager (3.6+), `os.unlink(..., dir_fd=…)` (3.3+), keyword-only parameters and `memoryview` slicing are all well below the floor.
