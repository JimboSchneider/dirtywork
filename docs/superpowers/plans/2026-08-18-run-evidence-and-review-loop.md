# Run Evidence & Review Loop (0.8) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Every code block below is the literal code to write — transcribe it, do not paraphrase it. Where a step shows a **before** block, that text exists verbatim on this branch; match it exactly and replace it with the **after** block.

**Goal:** Ship dirtywork **0.8.0** — the six issues (#30–#35) a day of real 0.7.0 runs against the invoicr repo produced. Give the orchestrator the evidence it needs the moment a run ends (`files_changed`, `last_tool_result`, `last_assistant_text` on every payload), end runs for one more honest reason (`stuck`: the same failing command N times), make the worker see its own edits (a unified diff echoed by `edit_file`/`write_file`, plus `insert_before`/`insert_after`), make the operator's gate first-class (`--verify`), give the image the four packages the bash suite needed, and make the review→fix round a one-liner (`runs snapshot`, `--branch-from @<slug>`, `resume --feedback`).

**Architecture:** Everything is additive over shipped structure. Task 1 adds one pure helper in `dirtywork/tools.py` (`describe_change`) that both sandbox backends call, so a host edit and a container edit produce byte-identical result text. Task 2 turns `edit_file` into one of three transforms over a single shared read→transform→write path per backend (`tools._transform_file`, `DockerSandbox._transform_file`) — the new `insert_before`/`insert_after` reuse every check `edit_file` already performed. Task 3 adds `RepeatTracker` beside `ProgressTracker` in `dirtywork/runner.py`, fed from the same call site and reusing the existing `_bash_fingerprint`. Task 4 tracks two values the runner already writes to the transcript and computes `files_changed` in the two places `diff_stat` is already computed (`sandbox/export.py` in the container, `sandbox/host.py` on the host), carrying both on `RunArtifacts`. Task 5 puts the verify call on the runner's two completion paths through the same `sandbox.bash` the tool uses, so every guardrail, budget and reaper already applies. Task 7 adds `workspace.snapshot_worktree`, the only new host-side write of worker content — built entirely from git plumbing (`hash-object --no-filters`, `update-index --index-info`, `write-tree`, `commit-tree`, `update-ref`) so no clean filter, `.gitattributes` rule, or hook can ever execute on the host. Tasks 8 and 9 are CLI plumbing over that. No new module, no new dependency, `schema_version` stays 2.

**Tech Stack:** Python ≥3.9, stdlib only (`difflib`, `hashlib`, `json`, `os`, `stat`, `subprocess`, `tempfile`, `argparse`, `pathlib`). Dev-only dependency: pytest.

**Spec:** `docs/superpowers/specs/2026-08-18-run-evidence-and-review-loop-design.md` (approved 2026-08-18 19:14 CDT, binding).

---

## Design

Restated from the spec named above. Section numbers below are the spec's.

### §1 — `stuck`: the same failing check, N times in a row (issue #32)

`runner.py` gains `RepeatTracker(limit)` beside `ProgressTracker`, fed only `bash` calls from the same place `ProgressTracker.note_call` is fed. It fingerprints with the **existing** `_bash_fingerprint` (command + volatile-token-stripped output). Only **failing** results count — a result is failing unless its first line is exactly `exit code: 0`, so `exit code: N≠0`, `ERROR: command timed out …`, `ERROR: bash failed …` and `BLOCKED: …` all count. A passing result resets the streak to zero. Non-`bash` calls neither count nor reset (edit→test→edit→test with an unchanged failure is exactly the loop this catches; the stall detector never fires on it because every `edit_file` counts as progress). Returns `"stuck"` at `repeats >= limit`; `limit <= 0` disables it. `--stuck-repeats N` (default **4**, `0` disables) on `run` and `resume`, independent of `--stall-turns`, no nudge. On `"stuck"` the runner finishes the turn's remaining tool calls, then ends with status **`stuck`** (exit 1, resumable). `stuck_on: {"command", "output" (≤4000 chars), "repeats"}` lands on `RunResult.extra` → stdout JSON, `run_end`, `run.json`; `null` on every other status. `runs show` shows it plain (`SHOW_FIELDS`) and in the Markdown `## Result`.

### §2 — End-of-run evidence on every payload (issue #33)

Three fields join the stdout JSON, `run_end` and `run.json` on **every** normal end-of-run path (whenever `runner.run()` returns a `RunResult`, whatever the status):

- `files_changed` — sorted repo-relative paths, capped at 1000 with `files_changed_truncated`. Docker: `git diff --cached --name-only <base_commit>` in the container right after the export's `git add -A` (no host git touches worker content). Host: `git diff --name-only <base_commit>` plus `git ls-files --others --exclude-standard`, where `host_diff_stat`/`host_untracked` already run. `[]` when nothing changed or the export never ran.
- `last_tool_result` — `{"tool", "args" ≤500 chars, "result" ≤2000 chars}` for the last tool call the runner executed **other than `finish`**; `null` if none.
- `last_assistant_text` — the last non-empty assistant text, ≤2000 chars; `null` if none.

`runs show --markdown` renders all three in `## Result`; the plain view shows `files_changed` via `SHOW_FIELDS`.

### §3 — Edits that show what they changed; insert primitives (issue #34)

**§3.1** `tools.describe_change(path, old_text, new_text, *, verb) -> str`: first line `<Verb> <path>: +A -D`, with `(removed N non-blank line(s))` appended only when N > 0 non-blank lines were deleted (a replaced non-blank line counts as removed); A/D from `difflib.SequenceMatcher` opcodes. Then `difflib.unified_diff(..., n=2)` capped at 40 lines / 3000 chars, with a final `[diff truncated: N more lines]` when capped. `write_file` on an existing file uses verb `Wrote`; on a new file it returns `Wrote N bytes to path (new file, M lines)` with no diff. Every existing `ERROR:` string is unchanged; only the success strings change.

**§3.2** `insert_before(path, anchor, text)` / `insert_after(path, anchor, text)`: `anchor` must occur exactly once (same error shape as `edit_file`, with `anchor` in place of `old_string`). `text` is inserted as whole line(s) — `insert_before` immediately before the start of the line holding the anchor's first character, `insert_after` immediately after the end of the line holding its last (the anchor may span lines); a trailing newline is added when missing; the anchor line itself is never modified. Implemented once as `tools.insert_text(text, anchor, insert, where)` and called from a shared read→transform→write path per backend. Success strings use `describe_change` with verb `Inserted into`. Registered in `builtin_tools.BUILTIN_SPECS` after `edit_file`, added to `runner._MUTATING_TOOLS`, named in the system prompt, README tool list ("seven tools" → nine), README Security enumeration, and `docs/transcript-schema.md`'s `tool` enum.

### §4 — `--verify "<cmd>"`: the operator's gate, first-class (issue #35)

`--verify CMD` (default none), `--verify-rounds N` (default **1**), `--verify-timeout S` (default **600**, clamped to 1–600) on `run`; `resume` accepts all three and inherits the command from the prior `run.json`. It runs inside the runner on the completion path — the `finish` tool or a plain-answer completion — **before** `finalize()`, through the same `sandbox.bash(command, timeout)` the tool uses. Passing iff the first line is `exit code: 0`; the exit code is the integer after `exit code: ` (`null` for `ERROR:`/`BLOCKED:`). Pass → `completed`. Fail with a fix round left (`rounds_used <= verify_rounds`; `--verify-rounds N` is the number of fix rounds after a failed verify, so the command may run N+1 times) → a `verify` transcript event, then a user message with the command, exit code and a 4000-char output tail, and the loop continues. Fail with no round left → **`verify_failed`** (exit 1, resumable). `BudgetExceeded`/`SandboxError` out of the verify call end the run with the existing `budget_exceeded`/`sandbox_error` statuses. A passing verify also writes a `verify` event. `verify` on every normal end-of-run payload: `null` without `--verify`, else `{"command", "exit_code", "output_tail", "rounds", "passed"}` for the last verify run.

### §5 — Sandbox deps: stock image + docs (issue #30)

`docker/Dockerfile`'s first apt line gains `jq uuid-runtime shellcheck curl`; nothing else in the image changes. `DEFAULT_IMAGE` → `ghcr.io/jimboschneider/dirtywork-worker:0.8`, CI's docker-live tag → `:0.8`, every README/docs mention → `:0.8`; `PINNED_DIGEST` stays `None` with the comment updated. README gains the "the worker cannot install dependencies in docker mode" callout plus the Node `node_modules` symlink trick, and the derived-image recipe next to `--image`; `docker/README.md` gets the same recipe and the new package list.

### §6 — The review→fix loop (issue #31)

**§6.1** `workspace.snapshot_worktree(worktree, branch, message) -> str | None` builds a commit of the worktree's current content on the run's branch without `git add`/`git commit`: walk in Python (`os.lstat`; skip the top-level `.git`, skip nothing else — ignore rules are not applied), regular files → blobs via one `git hash-object -w --no-filters --stdin-paths`, symlinks → the link *target string* via `hash-object -w --stdin` at mode `120000` (never followed), executable bit → `100755`, other file types skipped and counted; then `update-index --index-info` against a temporary index, `write-tree`, `commit-tree <tree> -p <branch-head>` with `dirtywork <dirtywork@localhost>` as author and committer, `update-ref refs/heads/<branch> <commit> <old-head>`, then the already-sanctioned `host_read_tree(worktree)`. Every git invocation uses the same config-neutral env as `host_read_tree` plus `-c commit.gpgsign=false`. Returns the new sha, or `None` when the tree equals the branch head's tree. `dirtywork runs snapshot <slug>` calls it with message `wip: dirtywork run <slug>`, prints `snapshot <sha> on <branch>` (or `nothing to snapshot`), exit 0; refuses (exit 2) a still-running run with a live pid, a missing worktree, a worktree that is not a linked worktree of the run's repo, or a pre-resume stash beside it.

**§6.2** `--branch-from @<slug>`: in `run` preflight, resolve the run dir, load `run.json`; unknown slug → exit 2 `error: unknown run '<slug>' (no run dir under ~/.dirtywork/runs)`; missing branch → exit 2. If that run's worktree exists and is dirty, snapshot it first and print `snapshot <sha> on <branch> (from @<slug>)` on stderr; then proceed with `branch_from = <that branch>`. `run_start.branch_from` records the resolved branch; `run.json` also records `branch_from_run: <slug>`.

**§6.3** `resume --feedback TEXT` / `--feedback-file PATH` (mutually exclusive; UTF-8, capped at 64 000 chars). `build_resume_task(..., feedback=None)` grows a feedback variant of the marker block; **both** markers are stripped from `prior_task` before building, so re-resuming never accumulates blocks. Resuming a `completed` run **without** feedback is refused (exit 2). The feedback text is recorded in the new `run.json` (`feedback`) and `run_start`.

### §7 — Cross-cutting

stdout JSON gains `stuck_on`, `files_changed`, `files_changed_truncated`, `last_tool_result`, `last_assistant_text`, `verify`; the `status` enum gains `stuck` and `verify_failed` (both exit 1); `run.json` gains those six plus `feedback` and `branch_from_run`; transcript gains the `verify` event. All additive, `schema_version` stays 2.

## Global Constraints

- **Python 3.9 floor.** No `match`, no runtime `X | Y` unions (`isinstance`, `cast` targets). `X | None` **in annotations** is fine only in modules that already carry `from __future__ import annotations` — every module this plan touches (`dirtywork/tools.py`, `runner.py`, `__main__.py`, `runs.py`, `resume.py`, `workspace.py`, `builtin_tools.py`, `sandbox/__init__.py`, `sandbox/host.py`, `sandbox/docker.py`, `sandbox/export.py`, `sandbox/docker_args.py`) already has it, and every test module this plan touches has it too. Check each file you touch and add the import if a new annotation needs it.
- **Stdlib only. No new dependencies.** The only new import in this whole plan is `difflib` (Task 1, `dirtywork/tools.py`) and `tempfile` (Task 7, `dirtywork/workspace.py`).
- **Every new CLI flag is read with `getattr(args, "<name>", <default>)`,** never `args.<name>`: existing tests build `argparse.Namespace` without the new attributes (e.g. `tests/test_runs.py` calls `runs.cmd_show(argparse.Namespace(slug="slug1", diff=False))`).
- **Additive only.** stdout JSON, transcript and `run.json` changes are additive; `schema_version` stays **2**; no existing key is renamed or removed; no existing CLI stdout line is lost.
- **Every existing test stays green after every task.** Run `python3 -m pytest -q` at the end of each task; the number may only rise from the baseline recorded in *Precondition*.
- **New tests go into the existing test modules.** No new test files — every module this plan touches already has one (`tests/test_tools_files.py`, `test_sandbox_host.py`, `test_docker_sandbox.py`, `test_builtin_tools.py`, `test_runner.py`, `test_main.py`, `test_runs.py`, `test_resume.py`, `test_workspace.py`, `test_export_flow.py`, `test_docker_args.py`, `test_transcript_schema.py`).
- **Commit after each task** with a conventional message (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`).
- **Never leave a placeholder.** Every code step below is the actual code; every test step the actual test.

## Precondition

Branch `run-evidence-0.8`, dirtywork **0.7.0**, working tree clean, off `main` = `5e41a78`.

**Baseline (measured on this branch with `python3 -m pytest -q` from the repo root): `837 passed, 18 deselected in ~34s`.** The 18 deselected are the `docker`/`live` markers excluded by `pyproject.toml`'s default addopts — that is normal and is not a failure. A task may only raise 837.

Every name below already exists exactly as written:

- `dirtywork/tools.py`: `MAX_RESULT_CHARS`, `MAX_READ_BYTES`, `MAX_WRITE_BYTES`, `MAX_LIST_ENTRIES`, `MAX_BASH_CHARS`, `MAX_BASH_CAPTURE_BYTES`, `_cap`, `_open_regular`, `_worktree_candidate`, `_number_lines`, `read_file`, `write_file` (`tools.py:119-150`), `edit_file` (`tools.py:153-193`), `list_dir`, `grep`, `bash`; imports `errno`, `os`, `shutil`, `stat`, `subprocess`, `Path`.
- `dirtywork/runner.py`: `MAX_ASSISTANT_TEXT_CHARS`, `FINISH_TOOL` (`runner.py:20`), `DEFAULT_STALL_TURNS` (`runner.py:122`), `STALL_NUDGE`, `_MUTATING_TOOLS` (`runner.py:126`), `_VOLATILE_RE`, `_bash_fingerprint` (`runner.py:141-150`), `ProgressTracker` (`runner.py:153-200`), `FailureTracker`, `RunResult` (`runner.py:256-262`), `Runner.__init__` (`runner.py:266-288`), `Runner.run` (`runner.py:290-482`) with its nested `finish` (`runner.py:306-321`) and `check_progress` (`runner.py:323-335`).
- `dirtywork/__main__.py`: `build_system_prompt` (`:53-81`, the rule line at `:73`), `PreflightFailure`, `RunContext` (`:93-111`), `_positive_int`, `_non_negative_int`, `_workspace_new` (`:180-205`), `_write_run_json_start` (`:321-345`), `_update_run_json` (`:348-360`), `_emit_result` (`:363-381`), `_load_resume_target` (`:497-523`), `_workspace_resume` (`:526-548`), `_execute` (`:551-661`) with its `finalize()` (`:585-598`) and `Runner(...)` construction (`:600-612`), `_add_run_flags` (`:664-691`), `_add_runs_parsers` (`:694-729`), `_parse_args` (`:759-773`), `main` (`:800-821`).
- `dirtywork/runs.py`: `SHOW_FIELDS` (`:31-32`), `TASK_PREVIEW_CHARS`, `RunsError`, `format_table`, `_open_run`, `_summary_value` (`:209-216`), `read_transcript_events`, `_timeline_line`, `MD_RESULT_FIELDS`, `_md_block`, `_md_inline`, `_md_result` (`:424-438`), `render_markdown`, `cmd_show`, `cmd_export`, `cmd_verdict`, `dispatch` (`:676-693`), `_worktree_is_dirty` (`:771-778`), `cmd_clean`.
- `dirtywork/workspace.py`: `WorkspaceError`, `_git` (`:21-24`), `create_worktree`, `remove_worktree`, `worktree_base_commit`, `host_diff_stat` (`:175-188`), `host_untracked` (`:191-212`), `host_read_tree` (`:269-281`), `commit_exists`.
- `dirtywork/resume.py`: `RESUME_MARKER` (`:13`), `resolve_run_dir`, `load_prior_run`, `check_resumable`, `find_stashes`, `worktree_belongs_to_repo`, `render_transcript_tail`, `build_resume_task` (`:191-204`), `ResumeError`.
- `dirtywork/sandbox/__init__.py`: `SandboxError`, `RunArtifacts` (`:15-36`), the `Sandbox` Protocol (`:39-64`).
- `dirtywork/sandbox/host.py`: `HostSandbox` with `write_file` (`:47-50`), `edit_file` (`:52-55`), `finalize` (`:69-80`).
- `dirtywork/sandbox/docker.py`: `_rel` (`:38-59`), `DockerSandbox._read_raw` (`:365-390`), `read_file` (`:392-396`), `write_file` (`:398-419`), `edit_file` (`:421-434`), `bash` (`:709`), `finalize` (`:239`).
- `dirtywork/sandbox/export.py`: `export_run` (`:171-361`) with the `git add -A` step at `:254-257` and the `git write-tree` step at `:259-263`.
- `dirtywork/sandbox/docker_args.py`: `DEFAULT_IMAGE` (`:8`), `PINNED_DIGEST` (`:25`).
- `dirtywork/builtin_tools.py`: `TOOL_OUTPUT_CAP`, `EDIT_FILE_SPEC` (`:81-93`), `BUILTIN_SPECS` (`:155-156`), `default_registry`.
- Test helpers: `tests/docker_fakes.py`'s `FakeDocker`/`_ok`/`_fail`; `tests/test_docker_sandbox.py`'s `docker`/`started` fixtures; `tests/test_runner.py`'s `FakeProvider`, `parts` fixture, `_resp`, `_call`, `_events`; `tests/test_main.py`'s `_host_repo`, `_install_host_harness`, `_ScriptedClient`, `_read_only_run_json`, `_first_run`, `_resume_responses`, `_install_docker_fake`; `tests/provider_doubles.py`'s `PreflightProvider`, `patch_provider`, `text_body`, `tool_call_body`; `tests/test_runs.py`'s `_write_run` and `repo` fixture; `tests/test_workspace.py`'s `_git` and `repo` fixture; `tests/test_export_flow.py`'s `_make_tar` and `empty_worktree` fixture.
- The frozen wire fixture `tests/fixtures/tool_schemas_v051.json` is exactly `json.dumps(default_registry().schemas(), indent=2, ensure_ascii=False) + "\n"` (verified).

## File Structure

```
dirtywork/
  tools.py                 # MODIFIED — Task 1 (describe_change, describe_write), Task 2 (insert_text, _transform_file, insert_before/after)
  runner.py                # MODIFIED — Task 3 (RepeatTracker), Task 4 (last_* tracking), Task 5 (verify)
  builtin_tools.py         # MODIFIED — Task 2 (two ToolSpecs)
  workspace.py             # MODIFIED — Task 4 (git_env, GIT_NEUTRAL_FLAGS, host_files_changed), Task 7 (snapshot_worktree), Task 8 (host_worktree_dirty)
  resume.py                # MODIFIED — Task 9 (feedback marker, build_resume_task)
  runs.py                  # MODIFIED — Task 3/4/5 (SHOW_FIELDS, _summary_value, _md_result), Task 7 (cmd_snapshot, dispatch)
  __main__.py              # MODIFIED — Tasks 2,3,4,5,8,9 (prompt, flags, payload, preflight)
  sandbox/
    __init__.py            # MODIFIED — Task 2 (Protocol), Task 4 (RunArtifacts)
    host.py                # MODIFIED — Task 2 (insert_before/after), Task 4 (files_changed)
    docker.py              # MODIFIED — Task 1 (_write_raw, diff echo), Task 2 (_transform_file, insert_before/after)
    export.py              # MODIFIED — Task 4 (files_changed)
    docker_args.py         # MODIFIED — Task 6 (:0.8)
docker/
  Dockerfile               # MODIFIED — Task 6 (apt packages)
  README.md                # MODIFIED — Task 6 (:0.8, packages, derived-image recipe)
.github/workflows/ci.yml   # MODIFIED — Task 6 (:0.8)
pyproject.toml             # MODIFIED — Task 10 (0.8.0)
README.md                  # MODIFIED — Tasks 2,3,4,5,6,8,9,10
docs/transcript-schema.md  # MODIFIED — Tasks 1,2,3,4,5,8,9,10
tests/
  test_tools_files.py      # MODIFIED — Tasks 1, 2
  test_sandbox_host.py     # MODIFIED — Task 2
  test_docker_sandbox.py   # MODIFIED — Tasks 1, 2
  test_builtin_tools.py    # MODIFIED — Task 2
  test_toolspec.py         # (unchanged — no tool-count assertion lives there)
  test_runner.py           # MODIFIED — Tasks 3, 4, 5
  test_main.py             # MODIFIED — Tasks 3, 4, 5, 8, 9
  test_runs.py             # MODIFIED — Tasks 3, 4, 5, 7
  test_resume.py           # MODIFIED — Task 9
  test_workspace.py        # MODIFIED — Tasks 4, 7, 8
  test_export_flow.py      # MODIFIED — Task 4
  test_docker_args.py      # MODIFIED — Task 6
  test_docker_live.py      # MODIFIED — Task 9 (one `docker`-marked resume call)
  test_transcript_schema.py# MODIFIED — Tasks 2, 3, 4, 5
  fixtures/tool_schemas_v051.json  # REGENERATED — Task 2
```

---

### Task 1: `describe_change` — every successful edit/write echoes its own diff (spec §3.1)

**Files:**
- Modify: `dirtywork/tools.py` (`tools.py:1-19` imports/constants; `tools.py:119-150` `write_file`; `tools.py:153-193` `edit_file`; new `_line_counts`, `describe_change`, `describe_write`, `_read_text_for_diff`)
- Modify: `dirtywork/sandbox/docker.py` (`docker.py:15` import; `docker.py:398-434` `write_file`/`edit_file`; new `_oversized`, `_write_raw`)
- Modify: `tests/test_tools_files.py` (5 new tests)
- Modify: `tests/test_docker_sandbox.py` (2 new tests; 1 existing test updated)
- Modify: `docs/transcript-schema.md` (the `tool_result.result` row)

**Interfaces:**
- Consumes: `difflib.SequenceMatcher`, `difflib.unified_diff` (stdlib); `tools._open_regular(path, flags, *, mode=0o644, max_size=None)` (`tools.py:29`); `tools.MAX_READ_BYTES`, `tools.MAX_WRITE_BYTES`; `docker._rel(path, *, writing=False) -> tuple` (`docker.py:38`); `DockerSandbox._read_raw(path, *, strict=False) -> tuple` (`docker.py:365`).
- Produces:
  - `tools.describe_change(path: str, old_text: str, new_text: str, *, verb: str) -> str`
  - `tools.describe_write(path: str, old_text, new_text: str, byte_count: int) -> str` (`old_text` is `str | None`)
  - `tools._line_counts(old_lines: list, new_lines: list) -> tuple` → `(added, deleted, removed_non_blank)`
  - `tools._read_text_for_diff(path: Path)` → `str | None`, never raises
  - `tools.MAX_DIFF_LINES = 40`, `tools.MAX_DIFF_CHARS = 3000`
  - `docker._oversized(encoded: bytes)` → `str | None`
  - `DockerSandbox._write_raw(path: str, encoded: bytes) -> str` (`""` on success, an `ERROR: …` string otherwise)
  - Task 2 replaces `edit_file`'s body on both backends but keeps `describe_change`/`_write_raw` exactly as defined here.

Note on `describe_write`'s `old_text=None`: it means "there was no previous text to diff against" — a new file, **or** an existing file the backend could not read back as UTF-8 text (binary, oversized, unreadable). Both render as `(new file, M lines)`. This only ever changes the wording of a result string; the write itself is unaffected, and the docstring says so. The alternative (a third state) would need an extra `test -f` exec per container write for a cosmetic distinction.

- [ ] **Step 1: Write the failing pure-helper tests**

Append to `tests/test_tools_files.py`:

```python
def test_describe_change_counts_and_diffs():
    old = "one\ntwo\nthree\nfour\nfive\n"
    new = "one\ntwo\nTWO AND A HALF\nthree\nfour\nfive\n"
    out = tools.describe_change("a/b.py", old, new, verb="Edited")
    lines = out.splitlines()
    assert lines[0] == "Edited a/b.py: +1 -0"      # pure insert: no removal note
    assert "--- a/a/b.py" in out and "+++ b/a/b.py" in out
    assert "+TWO AND A HALF" in out


def test_describe_change_reports_removed_non_blank_lines():
    old = "keep\ndrop me\n\nkeep2\n"
    new = "keep\nkeep2\n"
    out = tools.describe_change("x.txt", old, new, verb="Edited")
    # 'drop me' and the blank line go; only the non-blank one is counted
    assert out.splitlines()[0] == "Edited x.txt: +0 -2 (removed 1 non-blank line)"
    old2 = "a\nb\nc\n"
    new2 = "a\nB\nC\n"
    # a replaced non-blank line counts as removed
    assert tools.describe_change("x.txt", old2, new2, verb="Edited").splitlines()[0] == (
        "Edited x.txt: +2 -2 (removed 2 non-blank lines)")


def test_describe_change_truncates_a_huge_diff():
    old = "".join(f"line {i}\n" for i in range(200))
    new = "".join(f"changed {i}\n" for i in range(200))
    out = tools.describe_change("big.txt", old, new, verb="Edited")
    body = out.split("\n", 1)[1]
    assert len(body.splitlines()) <= tools.MAX_DIFF_LINES + 1     # + the marker line
    assert body.splitlines()[-1].startswith("[diff truncated: ")
    assert body.splitlines()[-1].endswith(" more lines]")


def test_describe_write_new_file_keeps_the_byte_count():
    assert tools.describe_write("new.txt", None, "a\nb\n", 4) == (
        "Wrote 4 bytes to new.txt (new file, 2 lines)")
    assert tools.describe_write("one.txt", None, "solo", 4) == (
        "Wrote 4 bytes to one.txt (new file, 1 line)")


def test_edit_and_write_echo_their_diff(wt: Path):
    out = tools.edit_file(wt, "src/app.py", "return 42", "return 43")
    assert out.startswith("Edited src/app.py: +1 -1 (removed 1 non-blank line)")
    assert "-    return 42" in out and "+    return 43" in out
    over = tools.write_file(wt, "src/app.py", "def main():\n    return 44\n")
    assert over.startswith("Wrote src/app.py: ")
    assert "+    return 44" in over
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_tools_files.py -q -k "describe or echo"`
Expected: 5 failed — the first four with `AttributeError: module 'dirtywork.tools' has no attribute 'describe_change'` / `'describe_write'` / `'MAX_DIFF_LINES'`, and `test_edit_and_write_echo_their_diff` with `AssertionError` on `out.startswith(...)` (today's `edit_file` returns `Edited src/app.py`).

- [ ] **Step 3: Add the `difflib` import and the diff caps**

In `dirtywork/tools.py`, the top of the file.

Before:

```python
from __future__ import annotations

import errno
import os
import shutil
import stat
import subprocess
from pathlib import Path

from .guardrails import GuardrailError, build_env, check_bash_command, resolve_in_worktree
from .procs import run_capped

MAX_RESULT_CHARS = 8000
```

After:

```python
from __future__ import annotations

import difflib
import errno
import os
import shutil
import stat
import subprocess
from pathlib import Path

from .guardrails import GuardrailError, build_env, check_bash_command, resolve_in_worktree
from .procs import run_capped

MAX_RESULT_CHARS = 8000
# Spec §3.1: every successful edit/write echoes the unified diff of what it
# actually changed, so a worker that meant to insert a line and replaced one
# instead sees that in the tool result rather than at review time.
MAX_DIFF_LINES = 40
MAX_DIFF_CHARS = 3000
```

- [ ] **Step 4: Add the pure helpers**

In `dirtywork/tools.py`, insert this block immediately **after** `_number_lines` (which ends with `return _cap(numbered, note=" — re-run with offset/limit to see more")`) and immediately **before** `def read_file(worktree: Path, path: str, offset: int = 0, limit: int = 400) -> str:`.

```python
def _line_counts(old_lines: list, new_lines: list) -> tuple:
    """(added, deleted, removed_non_blank) from SequenceMatcher opcodes. A
    REPLACED non-blank line counts as removed: the counter exists to answer
    'did I delete content I did not mean to delete', and a replace deletes
    before it inserts."""
    added = deleted = removed_non_blank = 0
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("delete", "replace"):
            deleted += i2 - i1
            removed_non_blank += sum(1 for line in old_lines[i1:i2] if line.strip())
        if tag in ("insert", "replace"):
            added += j2 - j1
    return added, deleted, removed_non_blank


def describe_change(path: str, old_text: str, new_text: str, *, verb: str) -> str:
    """Spec §3.1: '<Verb> <path>: +A -D [(removed N non-blank line(s))]' plus a
    capped unified diff. Pure — no filesystem access — so the host backend and
    the container backend produce byte-identical text for identical content."""
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    added, deleted, removed_non_blank = _line_counts(old_lines, new_lines)
    head = f"{verb} {path}: +{added} -{deleted}"
    if removed_non_blank > 0:
        plural = "" if removed_non_blank == 1 else "s"
        head += f" (removed {removed_non_blank} non-blank line{plural})"
    diff_lines = list(difflib.unified_diff(
        old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}", n=2, lineterm=""))
    if not diff_lines:
        return head
    kept = []
    total = 0
    for line in diff_lines:
        if len(kept) >= MAX_DIFF_LINES or total + len(line) + 1 > MAX_DIFF_CHARS:
            kept.append(f"[diff truncated: {len(diff_lines) - len(kept)} more lines]")
            break
        kept.append(line)
        total += len(line) + 1
    return head + "\n" + "\n".join(kept)


def describe_write(path: str, old_text, new_text: str, byte_count: int) -> str:
    """write_file's result string (spec §3.1). `old_text` is the file's previous
    content, or None when there was none to read — a new file, OR an existing
    file the backend could not read back as UTF-8 text (binary, oversized,
    unreadable). Both render as '(new file, M lines)'; that only ever changes
    the wording of a result string, never the write itself. The byte count
    stays in the new-file string so callers matching 'Wrote N bytes' match."""
    if old_text is None:
        lines = len(new_text.splitlines())
        plural = "" if lines == 1 else "s"
        return f"Wrote {byte_count} bytes to {path} (new file, {lines} line{plural})"
    return describe_change(path, old_text, new_text, verb="Wrote")


def _read_text_for_diff(path: Path):
    """The file's current text for describe_write, or None when there is none
    to read (missing, a symlink, a FIFO/device, over the read limit, or not
    valid UTF-8). Never raises: this is decoration on a write, and a write must
    never fail because its 'before' picture could not be taken."""
    try:
        fh = _open_regular(path, os.O_RDONLY, max_size=MAX_READ_BYTES)
    except OSError:
        return None
    try:
        raw = fh.read()
    except OSError:
        return None
    finally:
        fh.close()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
```

- [ ] **Step 5: Echo the diff from the host `write_file`**

In `dirtywork/tools.py`, inside `write_file`.

Before:

```python
    p = _worktree_candidate(path, worktree)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"ERROR: cannot write '{path}': {e}"
```

After:

```python
    p = _worktree_candidate(path, worktree)
    # Best-effort 'before' picture, taken after the containment check and
    # before the truncating open. None means "nothing to diff against".
    old_text = _read_text_for_diff(p)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"ERROR: cannot write '{path}': {e}"
```

And the function's last line.

Before:

```python
    return f"Wrote {len(encoded)} bytes to {path}"
```

After:

```python
    return describe_write(path, old_text, content, len(encoded))
```

- [ ] **Step 6: Echo the diff from the host `edit_file`**

In `dirtywork/tools.py`, `edit_file`'s last line.

Before:

```python
    return f"Edited {path}"
```

After:

```python
    return describe_change(path, text, new_text, verb="Edited")
```

- [ ] **Step 7: Run the host tests**

Run: `python3 -m pytest tests/test_tools_files.py tests/test_sandbox_host.py -q`
Expected: `38 passed` (33 today + the 5 new ones). The pre-existing `"Wrote 5 bytes" in out` and `"Edited" in out` assertions still hold: a new file keeps the byte count, and `describe_change`'s first word is still `Edited`.

- [ ] **Step 8: Write the failing docker-backend tests**

Append to `tests/test_docker_sandbox.py`:

```python
def test_write_file_over_existing_content_echoes_a_diff(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"],
                _ok(b"def main():\n    return 42\n"))
    out = sb.write_file("src/app.py", "def main():\n    return 43\n")
    assert out.startswith("Wrote src/app.py: +1 -1 (removed 1 non-blank line)")
    assert "-    return 42" in out and "+    return 43" in out


def test_edit_file_echoes_a_diff(started):
    sb, fake, run_dir = started
    fake.script(["exec"], [_ok(b"def main():\n    return 42\n"), _ok()])
    out = sb.edit_file("src/app.py", "return 42", "return 43")
    assert out.startswith("Edited src/app.py: +1 -1 (removed 1 non-blank line)")
    assert "--- a/src/app.py" in out and "+++ b/src/app.py" in out
```

- [ ] **Step 9: Update the one existing docker test that depends on the write result string**

`DockerSandbox.write_file` now reads the file back before writing it, so this test must say what that read returns. Scripting the read as a failure is what a *new* file looks like in the container, which is what this test writes.

In `tests/test_docker_sandbox.py`.

Before:

```python
def test_write_file_sends_content_on_stdin(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok())
    out = sb.write_file("deep/new/file.txt", "hello")
    assert "Wrote 5 bytes" in out
```

After:

```python
def test_write_file_sends_content_on_stdin(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok())
    # the pre-write read-back: a new file has nothing to read, so `head` fails
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"],
                _fail(b"head: cannot open 'deep/new/file.txt': No such file or directory"))
    out = sb.write_file("deep/new/file.txt", "hello")
    assert "Wrote 5 bytes" in out
    assert "(new file, 1 line)" in out
```

- [ ] **Step 10: Run the docker tests to verify the two new ones fail**

Run: `python3 -m pytest tests/test_docker_sandbox.py -q -k "echoes_a_diff or sends_content_on_stdin"`
Expected: 3 failed — the two new ones with `AssertionError` on `out.startswith(...)`, and `test_write_file_sends_content_on_stdin` with `AssertionError` on `"(new file, 1 line)" in out`.

- [ ] **Step 11: Import the helpers into the docker backend**

In `dirtywork/sandbox/docker.py`.

Before:

```python
from ..tools import MAX_BASH_CHARS, MAX_LIST_ENTRIES, MAX_READ_BYTES, MAX_WRITE_BYTES, _cap, _number_lines
```

After:

```python
from ..tools import (
    MAX_BASH_CHARS,
    MAX_LIST_ENTRIES,
    MAX_READ_BYTES,
    MAX_WRITE_BYTES,
    _cap,
    _number_lines,
    describe_change,
    describe_write,
)
```

- [ ] **Step 12: Add the shared oversize guard**

In `dirtywork/sandbox/docker.py`, insert immediately **after** `_rel` (which ends with `return normalized, None`) and immediately **before** `class DockerSandbox:`.

```python
def _oversized(encoded: bytes):
    """The one 'content too big' refusal for every in-container write, or None.
    write_file checks it BEFORE any exec (so an oversized write costs nothing);
    _write_raw checks it again for edit/insert, whose new text is built from a
    read that was only capped at MAX_READ_BYTES."""
    if len(encoded) > MAX_WRITE_BYTES:
        return (
            f"ERROR: content is {len(encoded)} bytes, over the "
            f"{MAX_WRITE_BYTES}-byte write limit"
        )
    return None
```

- [ ] **Step 13: Split the in-container write out of `write_file` and echo the diff**

In `dirtywork/sandbox/docker.py`, replace `write_file` and `edit_file` wholesale.

Before:

```python
    def write_file(self, path: str, content: str) -> str:
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_WRITE_BYTES:
            return (
                f"ERROR: content is {len(encoded)} bytes, over the "
                f"{MAX_WRITE_BYTES}-byte write limit"
            )
        rel, err = _rel(path, writing=True)
        if err:
            return err
        argv = docker_args.exec_argv(
            self.container,
            ["/bin/sh", "-c", 'mkdir -p "$(dirname -- "$1")" && cat > "$1"', "_", rel],
            stdin=True,
        )
        captured = self._run(argv, timeout=WRITE_EXEC_TIMEOUT, stdin=encoded)
        if captured.returncode != 0:
            return (
                f"ERROR: cannot write '{path}': "
                f"{captured.output.decode('utf-8', 'replace')[:500]}"
            )
        return f"Wrote {len(encoded)} bytes to {path}"

    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        text, err = self._read_raw(path, strict=True)
        if err:
            return err
        count = text.count(old_string)
        if count != 1:
            return (
                f"ERROR: old_string occurs {count} times in {path}; it must occur "
                f"exactly once. Include more surrounding context to make it unique."
            )
        result = self.write_file(path, text.replace(old_string, new_string, 1))
        if result.startswith("ERROR:"):
            return result
        return f"Edited {path}"
```

After:

```python
    def _write_raw(self, path: str, encoded: bytes) -> str:
        """The in-container write itself: '' on success, an 'ERROR: …' string
        otherwise. Split out of write_file so edit_file (and, from Task 2,
        insert_before/insert_after) can write WITHOUT paying for write_file's
        own read-back — one read exec per edit, as before."""
        too_big = _oversized(encoded)
        if too_big:
            return too_big
        rel, err = _rel(path, writing=True)
        if err:
            return err
        argv = docker_args.exec_argv(
            self.container,
            ["/bin/sh", "-c", 'mkdir -p "$(dirname -- "$1")" && cat > "$1"', "_", rel],
            stdin=True,
        )
        captured = self._run(argv, timeout=WRITE_EXEC_TIMEOUT, stdin=encoded)
        if captured.returncode != 0:
            return (
                f"ERROR: cannot write '{path}': "
                f"{captured.output.decode('utf-8', 'replace')[:500]}"
            )
        return ""

    def write_file(self, path: str, content: str) -> str:
        encoded = content.encode("utf-8")
        too_big = _oversized(encoded)
        if too_big:
            return too_big
        rel, err = _rel(path, writing=True)
        if err:
            return err
        # Best-effort 'before' picture for the echoed diff (spec §3.1); an
        # unreadable/missing file yields None and reads as a new file.
        old_text, _unused = self._read_raw(path, strict=True)
        err = self._write_raw(path, encoded)
        if err:
            return err
        return describe_write(path, old_text, content, len(encoded))

    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        text, err = self._read_raw(path, strict=True)
        if err:
            return err
        count = text.count(old_string)
        if count != 1:
            return (
                f"ERROR: old_string occurs {count} times in {path}; it must occur "
                f"exactly once. Include more surrounding context to make it unique."
            )
        new_text = text.replace(old_string, new_string, 1)
        err = self._write_raw(path, new_text.encode("utf-8"))
        if err:
            return err
        return describe_change(path, text, new_text, verb="Edited")
```

- [ ] **Step 14: Run the docker tests**

Run: `python3 -m pytest tests/test_docker_sandbox.py -q`
Expected: `80 passed` (78 today + 2). `test_edit_file_reads_then_writes` still asserts exactly one `head` exec and one write exec, because `edit_file` now calls `_write_raw` instead of `write_file`.

- [ ] **Step 15: Document the new result shape**

In `docs/transcript-schema.md`, the `tool_result` table's `result` row.

Before:

```
| `result` | ✓ | ✓ | string | the tool's result, trimmed per the tool's `Caps.transcript` setting. All seven built-in tools declare `preview`, which caps the record at 2000 chars; the registry also supports `full` and `none`, unused by any shipped tool |
```

After:

```
| `result` | ✓ | ✓ | string | the tool's result, trimmed per the tool's `Caps.transcript` setting. All built-in tools declare `preview`, which caps the record at 2000 chars; the registry also supports `full` and `none`, unused by any shipped tool. Since 0.8 a successful `edit_file`/`write_file` result is `<Verb> <path>: +A -D [(removed N non-blank lines)]` followed by a unified diff (capped at 40 lines / 3000 chars, then `[diff truncated: N more lines]`); `write_file` on a new file returns `Wrote N bytes to <path> (new file, M lines)` with no diff |
```

- [ ] **Step 16: Run the full suite**

Run: `python3 -m pytest -q`
Expected: `844 passed` (837 baseline + 7), 18 deselected.

- [ ] **Step 17: Commit**

```bash
git add dirtywork/tools.py dirtywork/sandbox/docker.py tests/test_tools_files.py tests/test_docker_sandbox.py docs/transcript-schema.md
git commit -m "feat: edit_file/write_file echo a unified diff of what they changed"
```

---

### Task 2: `insert_before` / `insert_after` over one shared transform path (spec §3.2)

**Files:**
- Modify: `dirtywork/tools.py` (new `insert_text`, `_transform_file`, `_replace_once`, `_insert_once`, `insert_before`, `insert_after`; `edit_file` at `tools.py:153-193` becomes a one-liner)
- Modify: `dirtywork/sandbox/docker.py` (new `_transform_file` method; `edit_file` rewritten; new `insert_before`/`insert_after`; import line)
- Modify: `dirtywork/sandbox/host.py` (`host.py:52-55` neighbourhood: two new methods)
- Modify: `dirtywork/sandbox/__init__.py` (`__init__.py:53` neighbourhood: two Protocol methods)
- Modify: `dirtywork/builtin_tools.py` (`builtin_tools.py:1-8` docstring, new `_insert_before`/`_insert_after`, `INSERT_BEFORE_SPEC`/`INSERT_AFTER_SPEC`, `BUILTIN_SPECS` at `:155-156`)
- Modify: `dirtywork/runner.py` (`runner.py:126` `_MUTATING_TOOLS`)
- Modify: `dirtywork/__main__.py` (`__main__.py:73` system-prompt rule)
- Modify: `tests/fixtures/tool_schemas_v051.json` (regenerated)
- Modify: `tests/test_tools_files.py`, `tests/test_sandbox_host.py`, `tests/test_docker_sandbox.py`, `tests/test_builtin_tools.py`, `tests/test_transcript_schema.py`
- Modify: `README.md` (`:46-47` Security tool enumeration; `:326-327` "seven tools")
- Modify: `docs/transcript-schema.md` (`:64` `tool` enum)

**Interfaces:**
- Consumes: `tools.describe_change(path, old_text, new_text, *, verb) -> str` (Task 1); `tools._open_regular`, `tools._worktree_candidate`, `guardrails.resolve_in_worktree`, `guardrails.GuardrailError`; `DockerSandbox._read_raw(path, *, strict=False) -> tuple`, `DockerSandbox._write_raw(path, encoded) -> str` (Task 1); `toolspec.ToolSpec`, `ParamSpec`, `Caps`.
- Produces:
  - `tools.insert_text(text: str, anchor: str, insert: str, where: str) -> str` — pure; `where` is `"before"` or `"after"`
  - `tools._transform_file(worktree: Path, path: str, transform, *, tool: str) -> str` — `transform(text) -> (new_text_or_None, result_string)`
  - `tools._replace_once(path: str, old_string: str, new_string: str)` → a `transform` callable
  - `tools._insert_once(path: str, anchor: str, insert: str, where: str)` → a `transform` callable
  - `tools.insert_before(worktree: Path, path: str, anchor: str, text: str) -> str`
  - `tools.insert_after(worktree: Path, path: str, anchor: str, text: str) -> str`
  - `HostSandbox.insert_before/insert_after(path, anchor, text) -> str`
  - `DockerSandbox._transform_file(path: str, transform) -> str`, `DockerSandbox.insert_before/insert_after(path, anchor, text) -> str`
  - `builtin_tools.INSERT_BEFORE_SPEC`, `builtin_tools.INSERT_AFTER_SPEC`; `BUILTIN_SPECS` becomes nine entries
- Both backends reuse `_replace_once`/`_insert_once`, so the anchor/`old_string` error strings exist once each.

- [ ] **Step 1: Write the failing pure-transform tests**

Append to `tests/test_tools_files.py`:

```python
def test_insert_text_places_whole_lines_around_the_anchor_line():
    text = "alpha\nbeta\ngamma\n"
    assert tools.insert_text(text, "beta", "NEW\n", "before") == "alpha\nNEW\nbeta\ngamma\n"
    assert tools.insert_text(text, "beta", "NEW\n", "after") == "alpha\nbeta\nNEW\ngamma\n"
    # a multi-line anchor: 'before' the first line, 'after' the last
    assert tools.insert_text(text, "beta\ngamma", "NEW\n", "before") == (
        "alpha\nNEW\nbeta\ngamma\n")
    assert tools.insert_text(text, "beta\ngamma", "NEW\n", "after") == (
        "alpha\nbeta\ngamma\nNEW\n")
    # an anchor in the middle of a line never splits that line
    assert tools.insert_text("x = f(1)\ny\n", "f(1", "NEW\n", "before") == (
        "NEW\nx = f(1)\ny\n")


def test_insert_text_adds_the_missing_newlines():
    # insert without a trailing newline gets one
    assert tools.insert_text("a\nb\n", "a", "NEW", "after") == "a\nNEW\nb\n"
    # a file with no final newline gets one before the appended line
    assert tools.insert_text("a\nb", "b", "NEW\n", "after") == "a\nb\nNEW\n"
    # inserting before the first line needs no leading newline
    assert tools.insert_text("a\nb\n", "a", "NEW\n", "before") == "NEW\na\nb\n"


def test_insert_before_and_after_write_the_file_and_echo_a_diff(wt: Path):
    (wt / "cfg.txt").write_text("alpha\nbeta\ngamma\n")
    out = tools.insert_after(wt, "cfg.txt", "beta", "beta-plus")
    assert out.startswith("Inserted into cfg.txt: +1 -0")
    assert "+beta-plus" in out
    assert (wt / "cfg.txt").read_text() == "alpha\nbeta\nbeta-plus\ngamma\n"
    out = tools.insert_before(wt, "cfg.txt", "gamma", "pre-gamma\n")
    assert out.startswith("Inserted into cfg.txt: +1 -0")
    assert (wt / "cfg.txt").read_text() == "alpha\nbeta\nbeta-plus\npre-gamma\ngamma\n"


def test_insert_requires_a_unique_anchor(wt: Path):
    (wt / "dup.txt").write_text("aa\naa\n")
    out = tools.insert_before(wt, "dup.txt", "aa", "x")
    assert out.startswith("ERROR: anchor occurs 2 times in dup.txt")
    assert "it must occur exactly once" in out
    assert (wt / "dup.txt").read_text() == "aa\naa\n"      # nothing written
    missing = tools.insert_after(wt, "dup.txt", "zz", "x")
    assert missing.startswith("ERROR: anchor occurs 0 times in dup.txt")


def test_insert_keeps_the_edit_file_guardrails(wt: Path):
    assert tools.insert_before(wt, "../../etc/passwd", "root", "x").startswith("ERROR:")
    assert tools.insert_before(wt, "nope.py", "x", "y").startswith("ERROR: cannot read")
    (wt / "bin2.dat").write_bytes(b"\xff\xfe\x00\x01")
    binary = tools.insert_after(wt, "bin2.dat", "x", "y")
    assert binary == "ERROR: bin2.dat is not valid UTF-8 text; insert_after only works on text files"
    # edit_file's own message is unchanged
    (wt / "bin3.dat").write_bytes(b"\xff\xfe\x00\x01")
    assert tools.edit_file(wt, "bin3.dat", "x", "y") == (
        "ERROR: bin3.dat is not valid UTF-8 text; edit_file only works on text files")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_tools_files.py -q -k insert`
Expected: 5 failed — `AttributeError: module 'dirtywork.tools' has no attribute 'insert_text'` (first two) / `'insert_after'` (last three).

- [ ] **Step 3: Add the pure insert transform**

In `dirtywork/tools.py`, insert immediately **after** `describe_write` (which ends with `return describe_change(path, old_text, new_text, verb="Wrote")`) and immediately **before** `def _read_text_for_diff(path: Path):`.

```python
def insert_text(text: str, anchor: str, insert: str, where: str) -> str:
    """Spec §3.2: place `insert` as WHOLE LINES relative to the line(s) holding
    `anchor`, never modifying the anchor's own line. `where` is 'before' (just
    before the start of the line holding the anchor's first character) or
    'after' (just after the end of the line holding its last character — the
    anchor may span lines). The caller has already proved the anchor occurs
    exactly once. Pure: both backends call this with the text they read."""
    start = text.index(anchor)
    end = start + len(anchor)
    if not insert.endswith("\n"):
        insert = insert + "\n"
    if where == "before":
        line_start = text.rfind("\n", 0, start) + 1
        return text[:line_start] + insert + text[line_start:]
    last = max(start, end - 1)
    newline = text.find("\n", last)
    if newline == -1:
        # the anchor sits on a final line with no trailing newline: give the
        # file one so the inserted text starts on a line of its own
        head = text + "\n" if text and not text.endswith("\n") else text
        return head + insert
    return text[:newline + 1] + insert + text[newline + 1:]
```

- [ ] **Step 4: Add the shared read→transform→write path and the three transforms**

In `dirtywork/tools.py`, replace the whole of `edit_file` (from `def edit_file(worktree: Path, path: str, old_string: str, new_string: str) -> str:` through its final `return describe_change(path, text, new_text, verb="Edited")`) with the block below.

Before:

```python
def edit_file(worktree: Path, path: str, old_string: str, new_string: str) -> str:
    try:
        p = resolve_in_worktree(path, worktree, writing=True)
    except GuardrailError as e:
        return f"ERROR: {e}"
    try:
        fh = _open_regular(p, os.O_RDONLY, max_size=MAX_READ_BYTES)
    except OSError as e:
        return f"ERROR: cannot read '{path}': {e}"
    try:
        raw = fh.read()
    finally:
        fh.close()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return f"ERROR: {path} is not valid UTF-8 text; edit_file only works on text files"
    count = text.count(old_string)
    if count != 1:
        return (
            f"ERROR: old_string occurs {count} times in {path}; it must occur exactly "
            f"once. Include more surrounding context to make it unique."
        )
    new_text = text.replace(old_string, new_string, 1)
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
    return describe_change(path, text, new_text, verb="Edited")
```

After:

```python
def _transform_file(worktree: Path, path: str, transform, *, tool: str) -> str:
    """Read → transform → write for every in-place file tool (spec §3.2).
    `transform(text) -> (new_text_or_None, result)`: a None new_text means the
    transform refused and `result` (an 'ERROR: …' string) is returned without
    writing anything. Every check edit_file used to perform itself lives here,
    unchanged: worktree containment, the regular-file/symlink refusals, the
    5 MB read limit, UTF-8 validation, and the O_NOFOLLOW write."""
    try:
        p = resolve_in_worktree(path, worktree, writing=True)
    except GuardrailError as e:
        return f"ERROR: {e}"
    try:
        fh = _open_regular(p, os.O_RDONLY, max_size=MAX_READ_BYTES)
    except OSError as e:
        return f"ERROR: cannot read '{path}': {e}"
    try:
        raw = fh.read()
    finally:
        fh.close()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return f"ERROR: {path} is not valid UTF-8 text; {tool} only works on text files"
    new_text, result = transform(text)
    if new_text is None:
        return result
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


def _replace_once(path: str, old_string: str, new_string: str):
    """edit_file's transform. Defined here (not in a backend) so the host and
    the container share one uniqueness rule and one error string."""
    def transform(text: str):
        count = text.count(old_string)
        if count != 1:
            return None, (
                f"ERROR: old_string occurs {count} times in {path}; it must occur exactly "
                f"once. Include more surrounding context to make it unique."
            )
        new_text = text.replace(old_string, new_string, 1)
        return new_text, describe_change(path, text, new_text, verb="Edited")
    return transform


def _insert_once(path: str, anchor: str, insert: str, where: str):
    """insert_before/insert_after's transform — the same uniqueness rule and
    the same error shape as _replace_once, with `anchor` in place of
    `old_string`."""
    def transform(text: str):
        count = text.count(anchor)
        if count != 1:
            return None, (
                f"ERROR: anchor occurs {count} times in {path}; it must occur exactly "
                f"once. Include more surrounding context to make it unique."
            )
        new_text = insert_text(text, anchor, insert, where)
        return new_text, describe_change(path, text, new_text, verb="Inserted into")
    return transform


def edit_file(worktree: Path, path: str, old_string: str, new_string: str) -> str:
    return _transform_file(worktree, path, _replace_once(path, old_string, new_string),
                           tool="edit_file")


def insert_before(worktree: Path, path: str, anchor: str, text: str) -> str:
    return _transform_file(worktree, path, _insert_once(path, anchor, text, "before"),
                           tool="insert_before")


def insert_after(worktree: Path, path: str, anchor: str, text: str) -> str:
    return _transform_file(worktree, path, _insert_once(path, anchor, text, "after"),
                           tool="insert_after")
```

- [ ] **Step 5: Run the host tool tests**

Run: `python3 -m pytest tests/test_tools_files.py -q`
Expected: `35 passed` (30 after Task 1 + 5).

- [ ] **Step 6: Write the failing HostSandbox test**

Append to `tests/test_sandbox_host.py`:

```python
def test_host_sandbox_insert_before_and_after(wt: Path):
    sb = HostSandbox(wt)
    sb.start(wt, wt, "slug", "deadbeef")
    assert "Inserted into" in sb.insert_after("hello.txt", "hi", "there")
    assert "Inserted into" in sb.insert_before("hello.txt", "there", "before-there")
    assert (wt / "hello.txt").read_text() == "hi\nbefore-there\nthere\n"
```

- [ ] **Step 7: Run it to verify it fails**

Run: `python3 -m pytest tests/test_sandbox_host.py -q -k insert`
Expected: 1 failed — `AttributeError: 'HostSandbox' object has no attribute 'insert_after'`.

- [ ] **Step 8: Add the two HostSandbox methods**

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

    def insert_before(self, path: str, anchor: str, text: str) -> str:
        result = tools.insert_before(self.worktree, path, anchor, text)
        self._check_budget()
        return result

    def insert_after(self, path: str, anchor: str, text: str) -> str:
        result = tools.insert_after(self.worktree, path, anchor, text)
        self._check_budget()
        return result
```

- [ ] **Step 9: Declare the two methods on the Sandbox Protocol**

In `dirtywork/sandbox/__init__.py`.

Before:

```python
    Tool methods (read_file/write_file/edit_file/list_dir/grep/bash) may raise BudgetExceeded (worktree over budget) or SandboxError (backend failure); the runner catches both."""

    def start(self, worktree: Path, repo: Path, slug: str, base_commit: str, *, branch: str | None = None, seed_from_worktree: bool = False) -> None: ...

    def read_file(self, path: str, offset: int = 0, limit: int = 400) -> str: ...

    def write_file(self, path: str, content: str) -> str: ...

    def edit_file(self, path: str, old_string: str, new_string: str) -> str: ...

    def list_dir(self, path: str = ".") -> str: ...
```

After:

```python
    Tool methods (read_file/write_file/edit_file/insert_before/insert_after/list_dir/grep/bash) may raise BudgetExceeded (worktree over budget) or SandboxError (backend failure); the runner catches both."""

    def start(self, worktree: Path, repo: Path, slug: str, base_commit: str, *, branch: str | None = None, seed_from_worktree: bool = False) -> None: ...

    def read_file(self, path: str, offset: int = 0, limit: int = 400) -> str: ...

    def write_file(self, path: str, content: str) -> str: ...

    def edit_file(self, path: str, old_string: str, new_string: str) -> str: ...

    def insert_before(self, path: str, anchor: str, text: str) -> str: ...

    def insert_after(self, path: str, anchor: str, text: str) -> str: ...

    def list_dir(self, path: str = ".") -> str: ...
```

- [ ] **Step 10: Run the host sandbox tests**

Run: `python3 -m pytest tests/test_sandbox_host.py -q`
Expected: `9 passed` (8 today + 1).

- [ ] **Step 11: Write the failing docker-backend tests**

Append to `tests/test_docker_sandbox.py`:

```python
def test_insert_after_reads_then_writes(started):
    sb, fake, run_dir = started
    fake.script(["exec"], [_ok(b"alpha\nbeta\ngamma\n"), _ok()])
    out = sb.insert_after("cfg.txt", "beta", "beta-plus")
    assert out.startswith("Inserted into cfg.txt: +1 -0")
    heads = [c for c in fake.calls if "/usr/bin/head" in c[0]]
    writes = [c for c in fake.calls if "cat > \"$1\"" in " ".join(c[0])]
    assert len(heads) == 1
    assert len(writes) == 1
    assert writes[0][2] == b"alpha\nbeta\nbeta-plus\ngamma\n"


def test_insert_before_refuses_a_repeated_anchor(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b"aa\naa\n"))
    out = sb.insert_before("dup.txt", "aa", "x")
    assert out.startswith("ERROR: anchor occurs 2 times in dup.txt")
    assert not [c for c in fake.calls if "cat > \"$1\"" in " ".join(c[0])]
```

- [ ] **Step 12: Run them to verify they fail**

Run: `python3 -m pytest tests/test_docker_sandbox.py -q -k "insert_after_reads or insert_before_refuses"`
Expected: 2 failed — `AttributeError: 'DockerSandbox' object has no attribute 'insert_after'` / `'insert_before'`.

- [ ] **Step 13: Give the docker backend the same transform shape**

In `dirtywork/sandbox/docker.py`, first extend the import.

Before:

```python
from ..tools import (
    MAX_BASH_CHARS,
    MAX_LIST_ENTRIES,
    MAX_READ_BYTES,
    MAX_WRITE_BYTES,
    _cap,
    _number_lines,
    describe_change,
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
    _cap,
    _insert_once,
    _number_lines,
    _replace_once,
    describe_write,
)
```

Then replace `edit_file` (the Task 1 version).

Before:

```python
    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        text, err = self._read_raw(path, strict=True)
        if err:
            return err
        count = text.count(old_string)
        if count != 1:
            return (
                f"ERROR: old_string occurs {count} times in {path}; it must occur "
                f"exactly once. Include more surrounding context to make it unique."
            )
        new_text = text.replace(old_string, new_string, 1)
        err = self._write_raw(path, new_text.encode("utf-8"))
        if err:
            return err
        return describe_change(path, text, new_text, verb="Edited")
```

After:

```python
    def _transform_file(self, path: str, transform) -> str:
        """Read → transform → write inside the container: the same shape as
        tools._transform_file, over the same transforms, so edit_file,
        insert_before and insert_after are three transforms over ONE path per
        backend (spec §3.2) and the two backends can never disagree about an
        anchor rule or an error string. The UTF-8 refusal comes from
        _read_raw(strict=True), which is why no `tool` name is needed here."""
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

    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        return self._transform_file(path, _replace_once(path, old_string, new_string))

    def insert_before(self, path: str, anchor: str, text: str) -> str:
        return self._transform_file(path, _insert_once(path, anchor, text, "before"))

    def insert_after(self, path: str, anchor: str, text: str) -> str:
        return self._transform_file(path, _insert_once(path, anchor, text, "after"))
```

Note: the old container-side `old_string occurs {count} times` message wrapped its lines differently from the host's (`it must occur\nexactly once` vs `it must occur exactly\nonce`); both render as the same single-line string once f-string concatenation runs, so this refactor changes no observable text. `describe_change` is no longer imported by name here because only `describe_write` is called directly; the transforms call it themselves.

- [ ] **Step 14: Run the docker tests**

Run: `python3 -m pytest tests/test_docker_sandbox.py -q`
Expected: `82 passed` (80 after Task 1 + 2).

- [ ] **Step 15: Write the failing registry tests**

In `tests/test_builtin_tools.py`, first give `FakeSandbox` the two methods.

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

    def insert_before(self, path, anchor, text):
        self.calls.append(("insert_before", path, anchor, text))
        return f"inserted-before:{path}"

    def insert_after(self, path, anchor, text):
        self.calls.append(("insert_after", path, anchor, text))
        return f"inserted-after:{path}"
```

Then update the shape test's name set.

Before:

```python
def test_schemas_shape():
    schemas = default_registry().schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {"read_file", "write_file", "edit_file", "list_dir", "grep", "bash", "finish"}
```

After:

```python
def test_schemas_shape():
    schemas = default_registry().schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {"read_file", "write_file", "edit_file", "insert_before", "insert_after",
                     "list_dir", "grep", "bash", "finish"}
```

Then append the two dispatch tests:

```python
def test_insert_before_dispatches():
    sandbox = FakeSandbox()
    result = default_registry().execute(
        "insert_before", {"path": "a.txt", "anchor": "x", "text": "y"},
        sandbox=sandbox, deadline=None)
    assert result.kind == "ok"
    assert sandbox.calls == [("insert_before", "a.txt", "x", "y")]


def test_insert_after_dispatches():
    sandbox = FakeSandbox()
    result = default_registry().execute(
        "insert_after", {"path": "a.txt", "anchor": "x", "text": "y"},
        sandbox=sandbox, deadline=None)
    assert result.kind == "ok"
    assert sandbox.calls == [("insert_after", "a.txt", "x", "y")]
```

- [ ] **Step 16: Run them to verify they fail**

Run: `python3 -m pytest tests/test_builtin_tools.py -q -k "schemas_shape or insert"`
Expected: 3 failed — `test_schemas_shape` with `AssertionError` on the name set, and both dispatch tests with `result.kind == "error"` (`ERROR: unknown tool 'insert_before'`).

- [ ] **Step 17: Register the two tools**

In `dirtywork/builtin_tools.py`, first the module docstring.

Before:

```python
"""The seven tools dirtywork ships, declared as ToolSpecs.
```

After:

```python
"""The nine tools dirtywork ships, declared as ToolSpecs.
```

Then the dispatch functions.

Before:

```python
def _edit_file(sandbox, path, old_string, new_string):
    return sandbox.edit_file(path, old_string, new_string)
```

After:

```python
def _edit_file(sandbox, path, old_string, new_string):
    return sandbox.edit_file(path, old_string, new_string)


def _insert_before(sandbox, path, anchor, text):
    return sandbox.insert_before(path, anchor, text)


def _insert_after(sandbox, path, anchor, text):
    return sandbox.insert_after(path, anchor, text)
```

Then the two specs, inserted immediately **after** `EDIT_FILE_SPEC` and immediately **before** `LIST_DIR_SPEC = ToolSpec(`.

```python
INSERT_BEFORE_SPEC = ToolSpec(
    name="insert_before",
    description="Insert text as whole new line(s) immediately BEFORE the line "
                "containing anchor. anchor must occur exactly once — include "
                "surrounding context. The anchor's own line is never modified; "
                "use this instead of edit_file when you mean to add a line, not "
                "replace one.",
    params={
        "path": ParamSpec(type="string"),
        "anchor": ParamSpec(type="string"),
        "text": ParamSpec(type="string"),
    },
    required=("path", "anchor", "text"),
    fn=_insert_before,
    caps=Caps(fs="write", max_output_chars=TOOL_OUTPUT_CAP, transcript="preview"),
)

INSERT_AFTER_SPEC = ToolSpec(
    name="insert_after",
    description="Insert text as whole new line(s) immediately AFTER the line "
                "containing anchor. anchor must occur exactly once — include "
                "surrounding context. The anchor's own line is never modified; "
                "use this instead of edit_file when you mean to add a line, not "
                "replace one.",
    params={
        "path": ParamSpec(type="string"),
        "anchor": ParamSpec(type="string"),
        "text": ParamSpec(type="string"),
    },
    required=("path", "anchor", "text"),
    fn=_insert_after,
    caps=Caps(fs="write", max_output_chars=TOOL_OUTPUT_CAP, transcript="preview"),
)
```

Then the registration tuple.

Before:

```python
BUILTIN_SPECS = (READ_FILE_SPEC, WRITE_FILE_SPEC, EDIT_FILE_SPEC, LIST_DIR_SPEC,
                 GREP_SPEC, BASH_SPEC, FINISH_SPEC)
```

After:

```python
BUILTIN_SPECS = (READ_FILE_SPEC, WRITE_FILE_SPEC, EDIT_FILE_SPEC, INSERT_BEFORE_SPEC,
                 INSERT_AFTER_SPEC, LIST_DIR_SPEC, GREP_SPEC, BASH_SPEC, FINISH_SPEC)
```

- [ ] **Step 18: Regenerate the frozen wire fixture**

`tests/test_builtin_tools.py::test_schemas_match_the_frozen_v051_wire_contract` freezes the model-facing schema list; two new tools are a deliberate, matching change, so the fixture is regenerated (its serialization is `json.dumps(..., indent=2, ensure_ascii=False) + "\n"`).

Run, from the repo root:

```bash
python3 -c "
import json
from pathlib import Path
from dirtywork.builtin_tools import default_registry
Path('tests/fixtures/tool_schemas_v051.json').write_text(
    json.dumps(default_registry().schemas(), indent=2, ensure_ascii=False) + '\n',
    encoding='utf-8')
"
```

Then confirm the diff added exactly the two new blocks and changed nothing else:

```bash
git diff --stat tests/fixtures/tool_schemas_v051.json
git diff tests/fixtures/tool_schemas_v051.json | grep '^-' | grep -v '^---'
```
Expected: the second command prints nothing (no removed lines).

- [ ] **Step 19: Count the inserts as progress in the stall detector**

In `dirtywork/runner.py`.

Before:

```python
_MUTATING_TOOLS = ("write_file", "edit_file")
```

After:

```python
_MUTATING_TOOLS = ("write_file", "edit_file", "insert_before", "insert_after")
```

- [ ] **Step 20: Name the new tools in the system prompt**

In `dirtywork/__main__.py`, inside `build_system_prompt`.

Before:

```
- Use edit_file or write_file for ALL file changes. Never modify files via bash (no sed -i, no echo redirects, no heredocs).
```

After:

```
- Use edit_file, insert_before, insert_after or write_file for ALL file changes. Never modify files via bash (no sed -i, no echo redirects, no heredocs).
```

- [ ] **Step 21: Run the registry and prompt tests**

Run: `python3 -m pytest tests/test_builtin_tools.py tests/test_toolspec.py tests/test_runner.py -q`
Expected: all pass, with `tests/test_builtin_tools.py` up by 2.

- [ ] **Step 22: Update the docs — README tool list and Security enumeration**

In `README.md`, the Security section.

Before:

```
**Docker is the default sandbox as of 0.4 — a breaking change from 0.2.**
Every tool call (`read_file`/`write_file`/`edit_file`/`list_dir`/`grep`/
`bash`) runs inside a locked-down container: `--network none` by default,
```

After:

```
**Docker is the default sandbox as of 0.4 — a breaking change from 0.2.**
Every tool call (`read_file`/`write_file`/`edit_file`/`insert_before`/
`insert_after`/`list_dir`/`grep`/`bash`) runs inside a locked-down
container: `--network none` by default,
```

And "How a run works", step 3.

Before:

```
3. **The loop** — the model gets seven tools (`read_file`, `write_file`,
   `edit_file`, `list_dir`, `grep`, `bash`, `finish`) via OpenAI
   function-calling. Context is budgeted per model (oldest tool results get
```

After:

```
3. **The loop** — the model gets nine tools (`read_file`, `write_file`,
   `edit_file`, `insert_before`, `insert_after`, `list_dir`, `grep`, `bash`,
   `finish`) via OpenAI function-calling. `insert_before`/`insert_after` add
   whole lines around a unique anchor without touching the anchor's own line
   — the primitive for "add a line here", which `edit_file` could only express
   as a replace. Every successful `edit_file`/`write_file`/`insert_*` result
   echoes a capped unified diff of what actually changed, so a replace that
   silently deleted a line is visible to the worker in the same turn.
   Context is budgeted per model (oldest tool results get
```

- [ ] **Step 23: Update `docs/transcript-schema.md`'s tool enum**

Before:

```
| `tool` | ✓ | ✓ | string | tool name — one of `read_file`, `write_file`, `edit_file`, `list_dir`, `grep`, `bash`, `finish`; `""` for a discarded malformed entry |
```

After:

```
| `tool` | ✓ | ✓ | string | tool name — one of `read_file`, `write_file`, `edit_file`, `insert_before`, `insert_after`, `list_dir`, `grep`, `bash`, `finish` (`insert_before`/`insert_after` are v2, added in 0.8); `""` for a discarded malformed entry |
```

- [ ] **Step 24: Teach the doc test about the two new tools**

In `tests/test_transcript_schema.py`.

Before:

```python
def test_doc_documents_the_finish_tool_and_the_seven_tools():
    tokens = _doc_tokens()
    for name in ("read_file", "write_file", "edit_file", "list_dir", "grep", "bash", "finish"):
        assert name in tokens, f"tool '{name}' is not documented"
```

After:

```python
def test_doc_documents_the_finish_tool_and_the_nine_tools():
    tokens = _doc_tokens()
    for name in ("read_file", "write_file", "edit_file", "insert_before", "insert_after",
                 "list_dir", "grep", "bash", "finish"):
        assert name in tokens, f"tool '{name}' is not documented"
```

- [ ] **Step 25: Run the full suite**

Run: `python3 -m pytest -q`
Expected: `854 passed` (844 after Task 1 + 10), 18 deselected.

- [ ] **Step 26: Commit**

```bash
git add dirtywork/tools.py dirtywork/sandbox/docker.py dirtywork/sandbox/host.py dirtywork/sandbox/__init__.py dirtywork/builtin_tools.py dirtywork/runner.py dirtywork/__main__.py tests/fixtures/tool_schemas_v051.json tests/test_tools_files.py tests/test_sandbox_host.py tests/test_docker_sandbox.py tests/test_builtin_tools.py tests/test_transcript_schema.py README.md docs/transcript-schema.md
git commit -m "feat: insert_before/insert_after tools over one shared transform path"
```

---

### Task 3: `RepeatTracker`, `--stuck-repeats`, status `stuck`, `stuck_on` (spec §1)

**Files:**
- Modify: `dirtywork/runner.py` (`runner.py:122-126` constants; new `RepeatTracker` after `ProgressTracker` at `:200`; `Runner.__init__` at `:266-288`; `Runner.run` at `:290-321` and `:458-471`)
- Modify: `dirtywork/__main__.py` (`:28` import, `:600-612` Runner construction, `:641-660` run.json + stdout, `:664-691` `_add_run_flags`)
- Modify: `dirtywork/runs.py` (`:31-32` `SHOW_FIELDS`, `:209-216` `_summary_value`, `:424-438` `_md_result`)
- Modify: `tests/test_runner.py`, `tests/test_main.py`, `tests/test_runs.py`, `tests/test_transcript_schema.py`
- Modify: `README.md` (Troubleshooting, Machine contract flags + flag prose, status enum, exit codes)
- Modify: `docs/transcript-schema.md` (`run_end.stuck_on`, statuses table, stdout field list, `run.json.stuck_on`)

**Interfaces:**
- Consumes: `runner._bash_fingerprint(command, result) -> str` (`runner.py:141`); `runner.ProgressTracker.note_call` call site (`runner.py:458`); `__main__._update_run_json`, `__main__._emit_result`; `runs._summary_value`, `runs._md_block`.
- Produces:
  - `runner.DEFAULT_STUCK_REPEATS = 4`, `runner.STUCK_OUTPUT_CHARS = 4000`
  - `runner.RepeatTracker(limit: int)` with `note_bash(command, result) -> str | None` (`"stuck"` or `None`) and `stuck_on() -> dict`
  - `Runner.__init__(..., stuck_repeats: int = DEFAULT_STUCK_REPEATS)`
  - `RunResult.extra["stuck_on"]` — `dict | None`, present on every runner-returned result
  - CLI: `--stuck-repeats N` on `run` and `resume`; status `stuck`
- Task 5 edits the same `if pending_finish is not None:` block; the ordering established here (finish wins, then stuck) is preserved there.

- [ ] **Step 1: Write the failing tracker tests**

Append to `tests/test_runner.py`:

```python
def test_repeat_tracker_counts_only_identical_failures():
    from dirtywork.runner import RepeatTracker
    t = RepeatTracker(limit=3)
    assert t.note_bash("pytest", "exit code: 1\n1 failed in 2.10s") is None
    # a timing-only difference is the SAME failure (existing _bash_fingerprint)
    assert t.note_bash("pytest", "exit code: 1\n1 failed in 2.44s") is None
    assert t.repeats == 2
    assert t.note_bash("pytest", "exit code: 1\n1 failed in 2.99s") == "stuck"
    assert t.stuck_on() == {"command": "pytest",
                            "output": "exit code: 1\n1 failed in 2.99s",
                            "repeats": 3}
    # a different failure restarts the streak at 1
    assert t.note_bash("pytest", "exit code: 2\ncollection error") is None
    assert t.repeats == 1
    # ERROR: and BLOCKED: results are failures too
    t2 = RepeatTracker(limit=2)
    assert t2.note_bash("sleep 999", "ERROR: command timed out after 120s.") is None
    assert t2.note_bash("sleep 999", "ERROR: command timed out after 120s.") == "stuck"


def test_only_the_same_command_passing_resets_the_stuck_streak():
    from dirtywork.runner import RepeatTracker
    t = RepeatTracker(limit=3)
    assert t.note_bash("pytest", "exit code: 1\nfailed") is None
    # a passing run of ANOTHER command (git status, cat, ls ...) neither counts
    # nor resets -- exactly like a non-bash tool call in between
    assert t.note_bash("git status", "exit code: 0\nclean") is None
    assert t.repeats == 1
    assert t.note_bash("pytest", "exit code: 1\nfailed") is None
    assert t.repeats == 2
    # the SAME command going green ends the episode: the streak restarts
    assert t.note_bash("pytest", "exit code: 0\n3 passed") is None
    assert t.repeats == 0
    assert t.note_bash("pytest", "exit code: 1\nfailed") is None
    assert t.note_bash("pytest", "exit code: 1\nfailed") is None
    assert t.note_bash("pytest", "exit code: 1\nfailed") == "stuck"


def test_repeat_tracker_limit_zero_disables():
    from dirtywork.runner import RepeatTracker
    t = RepeatTracker(limit=0)
    for _ in range(10):
        assert t.note_bash("pytest", "exit code: 1\nfailed") is None
    assert t.repeats == 0


def test_stuck_status_ends_the_run_and_reports_stuck_on(parts):
    wt, registry, sandbox, transcript, tmp = parts
    failing = _resp(tool_calls=[_call("c", "bash", {"command": "exit 7"})])
    provider = FakeProvider([failing] * 5)
    r = Runner(provider, registry, sandbox, transcript, model="m", max_turns=10,
               stuck_repeats=3)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "stuck"
    assert result.turns == 3
    assert result.extra["stuck_on"]["command"] == "exit 7"
    assert result.extra["stuck_on"]["repeats"] == 3
    assert result.extra["stuck_on"]["output"].startswith("exit code: 7")
    end = [e for e in _events(tmp) if e["event"] == "run_end"][-1]
    assert end["status"] == "stuck"
    assert end["stuck_on"]["command"] == "exit 7"


def test_stuck_on_is_null_on_every_other_status(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="all done")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert result.extra["stuck_on"] is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_runner.py -q -k "repeat_tracker or stuck"`
Expected: 5 failed — the first three with `ImportError: cannot import name 'RepeatTracker' from 'dirtywork.runner'`, `test_stuck_status_…` with `TypeError: __init__() got an unexpected keyword argument 'stuck_repeats'`, and `test_stuck_on_is_null_…` with `KeyError: 'stuck_on'`.

- [ ] **Step 3: Add the tracker**

In `dirtywork/runner.py`, first the constants.

Before:

```python
DEFAULT_STALL_TURNS = 12
STALL_NUDGE = ("No progress in the last {n} turns: no file changed and no command produced "
               "new output. If the task is complete, commit (if asked) and call "
               "finish(summary=...); otherwise change your approach.")
```

After:

```python
DEFAULT_STALL_TURNS = 12
# Spec §1: independent of the stall detector. The stall detector never fires on
# edit -> test -> edit -> test (every edit_file counts as progress), so a worker
# grinding on a check it cannot pass burns every remaining turn. This ends the
# run instead. No nudge: the point is to stop paying for turns.
DEFAULT_STUCK_REPEATS = 4
STUCK_OUTPUT_CHARS = 4000
STALL_NUDGE = ("No progress in the last {n} turns: no file changed and no command produced "
               "new output. If the task is complete, commit (if asked) and call "
               "finish(summary=...); otherwise change your approach.")
```

Then insert `RepeatTracker` immediately **after** `ProgressTracker` (which ends with `        return None` at the end of `end_turn`) and immediately **before** `def resolve_context_window(model: str, flag_value, env_value, provider=None) -> tuple:`.

```python
class RepeatTracker:
    """Spec §1.1: the same FAILING bash call, N times in a row.

    Fed only bash calls, from the same place ProgressTracker.note_call is fed.
    A non-bash call neither counts nor resets: edit -> test -> edit -> test
    with an unchanged failure is exactly the loop this catches. Identity is the
    EXISTING _bash_fingerprint (command + volatile-token-stripped output), so a
    timing-only difference is not a different result — but a changed test
    count, a new line, or a changed exit status is. A passing result (first
    line exactly 'exit code: 0') of the SAME command resets the streak to zero
    -- so a diligent worker re-running a green typecheck after every edit is
    never 'stuck' -- while passing runs of other commands neither count nor
    reset. limit <= 0 disables the tracker entirely."""

    def __init__(self, limit: int):
        self.limit = limit
        self.repeats = 0
        self.command = None
        self.output = None
        self._fingerprint = None

    @staticmethod
    def _failed(result) -> bool:
        """Anything whose first line is not exactly 'exit code: 0'. That makes
        'exit code: N', 'ERROR: command timed out ...', 'ERROR: bash failed ...'
        and 'BLOCKED: ...' all failures, which is the intent."""
        if not isinstance(result, str):
            return True
        return result.split("\n", 1)[0] != "exit code: 0"

    def note_bash(self, command, result):
        if self.limit <= 0:
            return None
        if not self._failed(result):
            # Only the SAME command going green ends the episode. A passing run
            # of some other command (git status, cat, ls ...) neither counts nor
            # resets -- exactly like a non-bash tool call in between -- so the
            # reads a model interleaves with its edit->test loop cannot hide an
            # unchanged failure.
            if command == self.command:
                self.repeats = 0
                self._fingerprint = None
                self.command = None
                self.output = None
            return None
        text = result if isinstance(result, str) else ""
        fingerprint = _bash_fingerprint(command, text)
        if fingerprint == self._fingerprint:
            self.repeats += 1
        else:
            self._fingerprint = fingerprint
            self.repeats = 1
        self.command = command
        self.output = text
        return "stuck" if self.repeats >= self.limit else None

    def stuck_on(self) -> dict:
        return {"command": self.command,
                "output": (self.output or "")[:STUCK_OUTPUT_CHARS],
                "repeats": self.repeats}
```

- [ ] **Step 4: Accept `stuck_repeats` on the Runner**

In `dirtywork/runner.py`, `Runner.__init__`.

Before:

```python
    def __init__(self, provider, registry, sandbox, transcript, model,
                 max_turns: int = 40, timeout: int = 1800,
                 temperature: float | None = None,
                 run_info: dict | None = None,
                 finalize: Callable[[], dict] | None = None,
                 stall_turns: int = DEFAULT_STALL_TURNS,
                 context_window: int | None = None):
```

After:

```python
    def __init__(self, provider, registry, sandbox, transcript, model,
                 max_turns: int = 40, timeout: int = 1800,
                 temperature: float | None = None,
                 run_info: dict | None = None,
                 finalize: Callable[[], dict] | None = None,
                 stall_turns: int = DEFAULT_STALL_TURNS,
                 context_window: int | None = None,
                 stuck_repeats: int = DEFAULT_STUCK_REPEATS):
```

And, in the same method's body.

Before:

```python
        self.stall_turns = stall_turns
```

After:

```python
        self.stall_turns = stall_turns
        self.stuck_repeats = stuck_repeats
```

- [ ] **Step 5: Track the streak and carry `stuck_on` on every result**

In `dirtywork/runner.py`, `Runner.run`.

Before:

```python
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        turns = 0
        failures = FailureTracker()
        progress = ProgressTracker(self.stall_turns)
        start = time.monotonic()
        deadline = start + self.timeout

        def finish(status: str, final: str) -> RunResult:
            extra: dict = {}
            finalize_error = None
```

After:

```python
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        turns = 0
        failures = FailureTracker()
        progress = ProgressTracker(self.stall_turns)
        repeats = RepeatTracker(self.stuck_repeats)
        stuck = None            # spec §1.2: set once, read by finish() below
        start = time.monotonic()
        deadline = start + self.timeout

        def finish(status: str, final: str) -> RunResult:
            # stuck_on rides on EVERY result (null unless the run ended 'stuck'),
            # so a consumer never has to branch on status to read the field.
            extra: dict = {"stuck_on": stuck}
            finalize_error = None
```

- [ ] **Step 6: Feed the tracker from the tool loop**

In `dirtywork/runner.py`, `Runner.run`'s tool-call loop.

Before:

```python
                    progress.note_call(name, self.registry.canonical_args(name, args), result)
                    self.transcript.write("tool_result", tool=name,
                                          args=raw_args[:500],
                                          result=self.registry.transcript_preview(name, result))
```

After:

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

Note: `stuck` is assigned inside `run()`'s own body, so `finish()` (a nested function) reads the current value with no `nonlocal` needed.

- [ ] **Step 7: End the run once the turn's remaining calls are done**

In `dirtywork/runner.py`, after the tool loop.

Before:

```python
                if pending_finish is not None:
                    return finish("completed", pending_finish)

                stalled, stall_text = check_progress()
```

After:

```python
                if pending_finish is not None:
                    return finish("completed", pending_finish)

                # Same rule as `finish` in a mixed turn: the turn's remaining
                # tool calls have already run. `finish` still wins — a worker
                # that declared itself done did so with full knowledge of the
                # failure it had just seen.
                if stuck is not None:
                    return finish("stuck",
                                  f"the same failing command ran {stuck['repeats']} "
                                  f"times in a row")

                stalled, stall_text = check_progress()
```

- [ ] **Step 8: Run the runner tests**

Run: `python3 -m pytest tests/test_runner.py -q`
Expected: `91 passed` (86 today + 5).

- [ ] **Step 9: Write the failing CLI test**

Append to `tests/test_main.py`:

```python
def test_stuck_repeats_flag_reaches_the_runner_and_stuck_on_lands_everywhere(
        tmp_path, monkeypatch, capsys):
    failing = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "b1", "type": "function", "function": {"name": "bash",
         "arguments": json.dumps({"command": "exit 7"})}}]}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    m = _install_host_harness(monkeypatch, tmp_path, [failing])
    repo = _host_repo(tmp_path)
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none",
                 "--stuck-repeats", "2", "--max-turns", "9", "grind"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1, payload
    assert payload["status"] == "stuck"
    assert payload["stuck_on"]["command"] == "exit 7"
    assert payload["stuck_on"]["repeats"] == 2
    data = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert data["status"] == "stuck"
    assert data["stuck_on"]["command"] == "exit 7"


def test_stuck_on_is_null_on_an_ordinary_run(tmp_path, monkeypatch, capsys):
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    assert m.main(["run", "--repo", str(repo), "--sandbox", "none", "t"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stuck_on"] is None
    assert json.loads((Path(payload["run_dir"]) / "run.json").read_text())["stuck_on"] is None
```

- [ ] **Step 10: Run it to verify it fails**

Run: `python3 -m pytest tests/test_main.py -q -k stuck`
Expected: 2 failed — the first with `SystemExit: 2` from argparse (`unrecognized arguments: --stuck-repeats 2`), the second with `KeyError: 'stuck_on'`.

- [ ] **Step 11: Add the flag**

In `dirtywork/__main__.py`, the runner import.

Before:

```python
from .runner import DEFAULT_STALL_TURNS, Runner, resolve_context_window
```

After:

```python
from .runner import DEFAULT_STALL_TURNS, DEFAULT_STUCK_REPEATS, Runner, resolve_context_window
```

And `_add_run_flags`.

Before:

```python
    p.add_argument("--stall-turns", type=_non_negative_int, default=DEFAULT_STALL_TURNS,
                   help="end the run as 'stalled' after N turns without progress (0 disables)")
```

After:

```python
    p.add_argument("--stall-turns", type=_non_negative_int, default=DEFAULT_STALL_TURNS,
                   help="end the run as 'stalled' after N turns without progress (0 disables)")
    p.add_argument("--stuck-repeats", type=_non_negative_int, default=DEFAULT_STUCK_REPEATS,
                   help="end the run as 'stuck' after the same failing bash command runs N "
                        "times in a row (0 disables); independent of --stall-turns")
```

- [ ] **Step 12: Thread it into the Runner and both payloads**

In `dirtywork/__main__.py`, `_execute`'s `Runner(...)` construction.

Before:

```python
            finalize=finalize,
            stall_turns=args.stall_turns, context_window=ctx.context_window,
        )
```

After:

```python
            finalize=finalize,
            stall_turns=args.stall_turns, context_window=ctx.context_window,
            stuck_repeats=getattr(args, "stuck_repeats", DEFAULT_STUCK_REPEATS),
        )
```

And the two end-of-run emitters.

Before:

```python
    _update_run_json(
        run_dir,
        status=final_status,
        diff_stat=extra.get("diff_stat"),
        export_status=extra.get("export_status", "n/a"),
        patch_path=extra.get("patch_path"),
        finalize_error=finalize_error,
        watchdog_violation=extra.get("watchdog_violation"),
        watchdog_violation_kind=extra.get("watchdog_violation_kind"),
        turns=result.turns,
    )

    print(json.dumps(_emit_result(
        status=final_status, worktree=ctx.worktree, branch=ctx.branch, transcript_path=transcript_path,
        run_dir=run_dir, turns=result.turns, usage=result.usage, final_message=result.final_message,
        base_commit=ctx.base_commit, finalize_error=finalize_error,
        watchdog_violation=extra.get("watchdog_violation"),
        watchdog_violation_kind=extra.get("watchdog_violation_kind"),
        resumed_from=ctx.resumed_from, provider=ctx.provider,
    ), indent=2))
```

After:

```python
    _update_run_json(
        run_dir,
        status=final_status,
        diff_stat=extra.get("diff_stat"),
        export_status=extra.get("export_status", "n/a"),
        patch_path=extra.get("patch_path"),
        finalize_error=finalize_error,
        watchdog_violation=extra.get("watchdog_violation"),
        watchdog_violation_kind=extra.get("watchdog_violation_kind"),
        stuck_on=extra.get("stuck_on"),
        turns=result.turns,
    )

    print(json.dumps(_emit_result(
        status=final_status, worktree=ctx.worktree, branch=ctx.branch, transcript_path=transcript_path,
        run_dir=run_dir, turns=result.turns, usage=result.usage, final_message=result.final_message,
        base_commit=ctx.base_commit, finalize_error=finalize_error,
        watchdog_violation=extra.get("watchdog_violation"),
        watchdog_violation_kind=extra.get("watchdog_violation_kind"),
        stuck_on=extra.get("stuck_on"),
        resumed_from=ctx.resumed_from, provider=ctx.provider,
    ), indent=2))
```

- [ ] **Step 13: Run the CLI tests**

Run: `python3 -m pytest tests/test_main.py -q`
Expected: `54 passed` (52 today + 2).

- [ ] **Step 14: Write the failing `runs show` test**

Append to `tests/test_runs.py`:

```python
def test_show_renders_stuck_on_plain_and_markdown(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "stuck1", {
        "slug": "stuck1", "status": "stuck", "task": "grind",
        "stuck_on": {"command": "npm test", "output": "exit code: 1\n3 failing",
                     "repeats": 4},
    })
    assert runs.cmd_show(argparse.Namespace(slug="stuck1", diff=False)) == 0
    out = capsys.readouterr().out
    assert "stuck_on: npm test" in out

    assert runs.cmd_show(argparse.Namespace(slug="stuck1", diff=False, markdown=True)) == 0
    md = capsys.readouterr().out
    assert "**stuck on**" in md
    assert "npm test" in md and "3 failing" in md
```

- [ ] **Step 15: Run it to verify it fails**

Run: `python3 -m pytest tests/test_runs.py -q -k stuck_on`
Expected: 1 failed — `AssertionError: assert 'stuck_on: npm test' in ...` (`stuck_on` is not in `SHOW_FIELDS`).

- [ ] **Step 16: Show `stuck_on` in both `runs show` renderers**

In `dirtywork/runs.py`, the field list.

Before:

```python
SHOW_FIELDS = ("slug", "status", "sandbox", "task", "model", "provider", "turns",
               "resumed_from", "resumed_by", "branch", "worktree", "started", "ended")
```

After:

```python
SHOW_FIELDS = ("slug", "status", "sandbox", "task", "model", "provider", "turns",
               "resumed_from", "resumed_by", "branch", "worktree", "started", "ended",
               "stuck_on")
```

And the summary formatter.

Before:

```python
def _summary_value(key: str, data: dict) -> str:
    value = data.get(key)
    if value is None or value == "":
        return "-"
    text = str(value)
```

After:

```python
def _summary_value(key: str, data: dict) -> str:
    value = data.get(key)
    if value is None or value == "":
        return "-"
    # Structured end-of-run evidence: the plain view shows the one thing an
    # operator scans for, not the whole object (the JSON dump below has it all).
    if key == "stuck_on" and isinstance(value, dict):
        return str(value.get("command") or "-")
    text = str(value)
```

And the Markdown result section.

Before:

```python
    lines.append("")
    diff_stat = data.get("diff_stat") or end.get("diff_stat")
    if diff_stat:
        lines += ["**diff_stat**", ""] + _md_block(str(diff_stat))
```

After:

```python
    lines.append("")
    stuck_on = data.get("stuck_on") or end.get("stuck_on")
    if isinstance(stuck_on, dict):
        lines += [f"**stuck on** — the same failing command ran "
                  f"{stuck_on.get('repeats')} times in a row", ""]
        lines += _md_block(str(stuck_on.get("command") or ""))
        lines += _md_block(str(stuck_on.get("output") or ""))
    diff_stat = data.get("diff_stat") or end.get("diff_stat")
    if diff_stat:
        lines += ["**diff_stat**", ""] + _md_block(str(diff_stat))
```

- [ ] **Step 17: Run the runs tests**

Run: `python3 -m pytest tests/test_runs.py -q`
Expected: `76 passed` (75 today + 1).

- [ ] **Step 18: Document the status, the flag and the field — README**

In `README.md`, Troubleshooting, after the `stalled` bullet.

Before:

```
- **status `stalled`** — N turns (`--stall-turns`) passed with no file change
  and no new command output; the worktree is kept. Usually the work is
  done but the model never called `finish` — inspect the worktree, or
  `dirtywork resume <slug>`.
```

After:

```
- **status `stalled`** — N turns (`--stall-turns`) passed with no file change
  and no new command output; the worktree is kept. Usually the work is
  done but the model never called `finish` — inspect the worktree, or
  `dirtywork resume <slug>`.
- **status `stuck`** — the same failing `bash` command ran `--stuck-repeats`
  times in a row (default 4) with output that differed only in timing; the
  worktree is kept. The payload's `stuck_on` names the command, its output and
  the repeat count. Usually the worker cannot run the repo's gate at all (a
  missing dependency in docker mode) or is re-running a test it has no way to
  pass — read `stuck_on`, fix the environment or the brief, then
  `dirtywork resume <slug> --feedback "..."`. `--stuck-repeats 0` disables it.
```

Then the Machine contract flag block.

Before:

```
    [--stall-turns 12]                # end as `stalled` after N no-progress turns; 0 disables
```

After:

```
    [--stall-turns 12]                # end as `stalled` after N no-progress turns; 0 disables
    [--stuck-repeats 4]               # end as `stuck` after N identical failing bash runs; 0 disables
```

Then the flag prose, after the `--stall-turns` bullet.

Before:

```
- `--stall-turns N` (default 12) — end the run with status `stalled` after N
  consecutive turns that changed no file and produced no new command output;
  the model gets one nudge halfway. `0` disables.
```

After:

```
- `--stall-turns N` (default 12) — end the run with status `stalled` after N
  consecutive turns that changed no file and produced no new command output;
  the model gets one nudge halfway. `0` disables.
- `--stuck-repeats N` (default 4) — end the run with status `stuck` after the
  same **failing** `bash` command has run N times in a row. "Same" uses the
  stall detector's own fingerprint (command plus output with timings, clock
  times and git shas stripped), so a rerun that differs only in duration
  counts; a passing run (`exit code: 0`) resets the streak to zero. Edits
  between the reruns do **not** reset it — that is the loop `--stall-turns`
  cannot see, since every `edit_file` counts as progress. No nudge is sent:
  the point is to stop paying for turns. `0` disables.
```

Then the status enum.

Before:

```
`status` is one of: `completed`, `max_turns`, `timeout`, `stalled`,
`context_exhausted`, `model_error`, `interrupted`, `budget_exceeded`,
`sandbox_error`, `export_failed`. When the run fails before a `RunResult`
```

After:

```
`status` is one of: `completed`, `max_turns`, `timeout`, `stalled`, `stuck`,
`context_exhausted`, `model_error`, `interrupted`, `budget_exceeded`,
`sandbox_error`, `export_failed`. When the run fails before a `RunResult`
```

Then the exit-code list.

Before:

```
- `1` — any non-`completed` status (`max_turns`, `timeout`, `stalled`,
  `context_exhausted`, `model_error`, `interrupted`, `budget_exceeded`,
  `sandbox_error`, `export_failed`); the worktree and branch are kept for
```

After:

```
- `1` — any non-`completed` status (`max_turns`, `timeout`, `stalled`,
  `stuck`, `context_exhausted`, `model_error`, `interrupted`,
  `budget_exceeded`, `sandbox_error`, `export_failed`); the worktree and branch are kept for
```

- [ ] **Step 19: Document the field — `docs/transcript-schema.md`**

The `run_end` table, after the `finalize_error` row.

Before:

```
| `finalize_error` | | ✓ | string \| null | set when the finalize/export step itself raised after the agent loop finished; the run's own status is unaffected except that `completed` becomes `export_failed` |

## Statuses
```

After:

```
| `finalize_error` | | ✓ | string \| null | set when the finalize/export step itself raised after the agent loop finished; the run's own status is unaffected except that `completed` becomes `export_failed` |
| `stuck_on` | | ✓ | object \| null | 0.8: `{command, output, repeats}` for the failing bash call that ended the run as `stuck` (`output` capped at 4000 chars); `null` on every other status |

## Statuses
```

The statuses table, after the `stalled` row.

Before:

```
| `stalled` | | ✓ | `--stall-turns` consecutive turns with no progress (no new tool call, no successful write, no new command output) |
```

After:

```
| `stalled` | | ✓ | `--stall-turns` consecutive turns with no progress (no new tool call, no successful write, no new command output) |
| `stuck` | | ✓ | 0.8: the same **failing** bash command ran `--stuck-repeats` times in a row (fingerprint as the stall detector's: timings/shas stripped); edits in between do not reset the streak, a passing run does |
```

The stdout field list.

Before:

```
`transcript`, `turns`, `usage`, `final_message`, `run_dir`, `provider`,
`base_commit`, `resumed_from`, `finalize_error`, `watchdog_violation`,
`watchdog_violation_kind`, and `export_status` on the exception-recovery path.
```

After:

```
`transcript`, `turns`, `usage`, `final_message`, `run_dir`, `provider`,
`base_commit`, `resumed_from`, `finalize_error`, `watchdog_violation`,
`watchdog_violation_kind`, `stuck_on`, and `export_status` on the
exception-recovery path.
```

The `run.json` table, after the `watchdog_violation_kind` row.

Before:

```
| `watchdog_violation_kind` | end | |
| `allow_commit` | start | (bool) records whether the run's system prompt told the worker to commit as it went (`--allow-commit`, host mode only — see the README). A run that predates the flag has no such key. |
```

After:

```
| `watchdog_violation_kind` | end | |
| `stuck_on` | end | 0.8: `{command, output, repeats}` when the run ended `stuck`, else null |
| `allow_commit` | start | (bool) records whether the run's system prompt told the worker to commit as it went (`--allow-commit`, host mode only — see the README). A run that predates the flag has no such key. |
```

- [ ] **Step 20: Teach the doc test about the new status and field**

In `tests/test_transcript_schema.py`.

Before:

```python
STATUSES = ["completed", "max_turns", "timeout", "context_exhausted", "model_error",
            "interrupted", "stalled", "budget_exceeded", "sandbox_error", "export_failed"]
RUN_END_FIELDS = ["diff_stat", "untracked", "patch_path", "escaping_symlinks",
                  "dropped_git_entries", "worktree_bytes", "worktree_files",
                  "export_status", "watchdog_violation", "watchdog_violation_kind",
                  "finalize_error"]
```

After:

```python
STATUSES = ["completed", "max_turns", "timeout", "context_exhausted", "model_error",
            "interrupted", "stalled", "stuck", "budget_exceeded", "sandbox_error",
            "export_failed"]
RUN_END_FIELDS = ["diff_stat", "untracked", "patch_path", "escaping_symlinks",
                  "dropped_git_entries", "worktree_bytes", "worktree_files",
                  "export_status", "watchdog_violation", "watchdog_violation_kind",
                  "finalize_error", "stuck_on"]
```

- [ ] **Step 21: Run the full suite**

Run: `python3 -m pytest -q`
Expected: `862 passed` (854 after Task 2 + 8), 18 deselected.

- [ ] **Step 22: Commit**

```bash
git add dirtywork/runner.py dirtywork/__main__.py dirtywork/runs.py tests/test_runner.py tests/test_main.py tests/test_runs.py tests/test_transcript_schema.py README.md docs/transcript-schema.md
git commit -m "feat: end a run as 'stuck' when the same failing command repeats"
```

---

### Task 4: end-of-run evidence — `files_changed`, `last_tool_result`, `last_assistant_text` (spec §2)

**Files:**
- Modify: `dirtywork/workspace.py` (`:269-281` `host_read_tree` refactor; new `MAX_FILES_CHANGED`, `git_env`, `GIT_NEUTRAL_FLAGS`, `host_files_changed`)
- Modify: `dirtywork/sandbox/__init__.py` (`:27-36` `RunArtifacts` fields)
- Modify: `dirtywork/sandbox/host.py` (`:12` import, `:69-80` `finalize`)
- Modify: `dirtywork/sandbox/export.py` (`:15` import, `:178-183` locals, `:254-257` after `git add -A`, `:228-236` `_fail`, `:356-361` success return)
- Modify: `dirtywork/runner.py` (`:15` constants, `run()` locals + `finish()`, the `assistant` write at `:385-388`, the `tool_result` write at `:459-461`)
- Modify: `dirtywork/__main__.py` (`:585-598` `finalize()`, `:641-660` run.json + stdout)
- Modify: `dirtywork/runs.py` (`SHOW_FIELDS`, `_summary_value`, `_md_result`, `cmd_export`'s run.json update at `:620-628`)
- Modify: `tests/test_workspace.py`, `tests/test_export_flow.py`, `tests/test_runner.py`, `tests/test_main.py`, `tests/test_runs.py`, `tests/test_transcript_schema.py`
- Modify: `README.md` (Machine contract prose), `docs/transcript-schema.md` (`run_end`, stdout list, `run.json`)

**Interfaces:**
- Consumes: `workspace._git(repo, *args, env=None)` (`workspace.py:21`); `workspace.host_diff_stat`, `workspace.host_untracked`; `docker_args.exec_argv`; `RunArtifacts`; `runner.FINISH_TOOL` (`runner.py:20`).
- Produces:
  - `workspace.MAX_FILES_CHANGED = 1000`
  - `workspace.git_env() -> dict` — the config-neutral environment (`GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`); **Task 7 and Task 8 both reuse this**
  - `workspace.GIT_NEUTRAL_FLAGS: tuple` — `("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "commit.gpgsign=false")`; **Task 7 and Task 8 both reuse this**
  - `workspace.host_files_changed(worktree: Path, base_commit: str, cap: int = MAX_FILES_CHANGED) -> tuple` → `(list_of_str, bool)`
  - `RunArtifacts.files_changed: list`, `RunArtifacts.files_changed_truncated: bool`
  - `runner.LAST_ARGS_CHARS = 500`, `runner.LAST_RESULT_CHARS = 2000`, `runner.LAST_TEXT_CHARS = 2000`
  - `RunResult.extra["last_tool_result"]` (`dict | None`), `RunResult.extra["last_assistant_text"]` (`str | None`), plus `files_changed`/`files_changed_truncated` when `finalize()` ran
  - stdout JSON / `run_end` / `run.json` keys `files_changed`, `files_changed_truncated`, `last_tool_result`, `last_assistant_text`

- [ ] **Step 1: Write the failing host-side test**

Append to `tests/test_workspace.py`:

```python
def test_host_files_changed_lists_tracked_and_untracked(repo: Path, tmp_path: Path):
    from dirtywork.workspace import host_files_changed
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "f.txt").write_text("changed")
    (repo / "brand-new.txt").write_text("new")
    (repo / "sub").mkdir()
    (repo / "sub" / "deep.txt").write_text("deep")
    (repo / ".gitignore").write_text("ignored.txt\n")
    (repo / "ignored.txt").write_text("nope")
    _git(repo, "add", ".gitignore")
    paths, truncated = host_files_changed(repo, base)
    assert paths == [".gitignore", "brand-new.txt", "f.txt", "sub/deep.txt"]
    assert truncated is False
    assert "ignored.txt" not in paths


def test_host_files_changed_caps_and_reports_truncation(repo: Path):
    from dirtywork.workspace import host_files_changed
    base = _git(repo, "rev-parse", "HEAD").strip()
    for i in range(12):
        (repo / f"n{i:02d}.txt").write_text("x")
    paths, truncated = host_files_changed(repo, base, cap=5)
    assert len(paths) == 5
    assert paths == sorted(paths)
    assert truncated is True
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_workspace.py -q -k files_changed`
Expected: 2 failed — `ImportError: cannot import name 'host_files_changed' from 'dirtywork.workspace'`.

- [ ] **Step 3: Factor the config-neutral git environment out of `host_read_tree`**

In `dirtywork/workspace.py`, the constants at the top.

Before:

```python
MAX_CONTEXT_CHARS = 32_000
# Separate from tools.MAX_READ_BYTES (also 5 MB) even though the value is the
# same today — this bounds a git blob size, not a filesystem read.
MAX_CONTEXT_BYTES = 5 * 1024 * 1024
```

After:

```python
MAX_CONTEXT_CHARS = 32_000
# Separate from tools.MAX_READ_BYTES (also 5 MB) even though the value is the
# same today — this bounds a git blob size, not a filesystem read.
MAX_CONTEXT_BYTES = 5 * 1024 * 1024
# Spec §2: the end-of-run file list, capped with a companion truncation flag.
MAX_FILES_CHANGED = 1000
# The ONE config-neutral git invocation shape for every host git command that
# looks at worker content (spec §2, §6.1, §6.2). No global/system config, no
# hooks, no fsmonitor, no commit signing: nothing the operator has configured
# can execute or interfere when dirtywork reads or commits what a worker wrote.
GIT_NEUTRAL_FLAGS = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                     "-c", "commit.gpgsign=false")


def git_env() -> dict:
    """os.environ plus the config-neutral overrides. A fresh dict per call, so
    a caller can add GIT_INDEX_FILE / GIT_AUTHOR_* without touching the next."""
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env
```

And `host_read_tree` itself.

Before:

```python
def host_read_tree(worktree: Path) -> None:
    """The only host git command that runs after the worker has produced
    anything (spec §2 step 11): index-only, against the base tree, using the
    operator's own object store — writes no working-tree files (verified).
    Config-neutral env so no checked-out state can influence it, even though
    only objects/ was ever mounted into any container."""
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    res = _git(worktree, "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
               "read-tree", "HEAD", env=env)
    if res.returncode != 0:
        raise WorkspaceError(f"git read-tree HEAD failed in {worktree}: {res.stderr.strip()}")
```

After:

```python
def host_read_tree(worktree: Path) -> None:
    """Index-only, against the base tree, using the operator's own object store
    — writes no working-tree files (verified). Config-neutral env (git_env +
    GIT_NEUTRAL_FLAGS) so no checked-out state, hook or filter can influence
    it, even though only objects/ was ever mounted into any container."""
    res = _git(worktree, *GIT_NEUTRAL_FLAGS, "read-tree", "HEAD", env=git_env())
    if res.returncode != 0:
        raise WorkspaceError(f"git read-tree HEAD failed in {worktree}: {res.stderr.strip()}")


def host_files_changed(worktree: Path, base_commit: str, cap: int = MAX_FILES_CHANGED) -> tuple:
    """(paths, truncated) — repo-relative paths that differ from base_commit
    plus every untracked, non-ignored path, sorted and de-duplicated, capped at
    `cap`. Host mode's half of spec §2's `files_changed`; the docker export
    computes the same list inside the container. A git failure on either half
    contributes nothing rather than aborting: this is evidence, not a gate."""
    env = git_env()
    paths = set()
    for args in (("diff", "--name-only", base_commit),
                 ("ls-files", "--others", "--exclude-standard")):
        res = _git(worktree, *GIT_NEUTRAL_FLAGS, *args, env=env)
        if res.returncode != 0:
            continue
        for line in res.stdout.splitlines():
            line = line.strip()
            if line:
                paths.add(line)
    ordered = sorted(paths)
    return ordered[:cap], len(ordered) > cap
```

- [ ] **Step 4: Run the workspace tests**

Run: `python3 -m pytest tests/test_workspace.py -q`
Expected: `48 passed` (46 today + 2).

- [ ] **Step 5: Write the failing export-flow test**

Append to `tests/test_export_flow.py`:

```python
def test_export_run_reports_files_changed(tmp_path, empty_worktree):
    fake = FakeDocker()
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff",
                 "--cached", "--name-only", "deadbeef" * 5],
                _ok(b"src/b.ts\nsrc/a.ts\nsrc/b.ts\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "write-tree"],
                _ok(b"treehash1234\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff", "--stat",
                 "deadbeef" * 5, "treehash1234"],
                _ok(b" 2 files changed\n"))
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff",
         "deadbeef" * 5, "treehash1234"], b"")
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "archive",
         "--format=tar", "treehash1234"],
        _make_tar([{"name": "src/a.ts", "content": b"a"}]))
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()

    artifacts = export_run(
        DockerConfig(), slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert artifacts.export_status == "ok"
    assert artifacts.files_changed == ["src/a.ts", "src/b.ts"]   # sorted, de-duplicated
    assert artifacts.files_changed_truncated is False
    # the name list is read from the INDEX, right after `git add -A`
    names_index = next(i for i, c in enumerate(fake.calls) if "--cached" in c[0])
    add_index = next(i for i, c in enumerate(fake.calls) if c[0][-2:] == ["add", "-A"])
    assert add_index < names_index
```

- [ ] **Step 6: Run it to verify it fails**

Run: `python3 -m pytest tests/test_export_flow.py -q -k files_changed`
Expected: 1 failed — `AttributeError: 'RunArtifacts' object has no attribute 'files_changed'`.

- [ ] **Step 7: Add the fields to `RunArtifacts`**

In `dirtywork/sandbox/__init__.py`.

Before:

```python
    export_status: str = "ok"
    watchdog_violation: str | None = None
    watchdog_violation_kind: str | None = None
```

After:

```python
    export_status: str = "ok"
    watchdog_violation: str | None = None
    watchdog_violation_kind: str | None = None
    # Spec §2: repo-relative paths the run changed, sorted and capped at
    # workspace.MAX_FILES_CHANGED. Docker mode computes it in the container
    # (no host git ever touches worker content); host mode computes it beside
    # diff_stat. Empty list when nothing changed or the export never ran.
    files_changed: list = field(default_factory=list)
    files_changed_truncated: bool = False
```

- [ ] **Step 8: Compute it in the container**

In `dirtywork/sandbox/export.py`, first the import.

Before:

```python
from . import RunArtifacts, SandboxError, docker_args, docker_cli, lifecycle
```

After:

```python
from ..workspace import MAX_FILES_CHANGED
from . import RunArtifacts, SandboxError, docker_args, docker_cli, lifecycle
```

Then the function's locals.

Before:

```python
    diff_stat = ""
    patch_path = None
    dropped_git_entries: list = []
    escaping_symlinks: list = []
    worktree_bytes = None
    worktree_files = None
```

After:

```python
    diff_stat = ""
    patch_path = None
    dropped_git_entries: list = []
    escaping_symlinks: list = []
    files_changed: list = []
    files_changed_truncated = False
    worktree_bytes = None
    worktree_files = None
```

Then the step itself, right after `git add -A`.

Before:

```python
        add_argv = docker_args.exec_argv(name, ["/usr/bin/git", "add", "-A"])
        add_captured = run(add_argv, timeout=docker_cli.T_EXPORT_STEP)
        if add_captured.returncode != 0:
            return _fail(f"git add -A failed: {add_captured.output.decode('utf-8', 'replace')[:500]}")
```

After:

```python
        add_argv = docker_args.exec_argv(name, ["/usr/bin/git", "add", "-A"])
        add_captured = run(add_argv, timeout=docker_cli.T_EXPORT_STEP)
        if add_captured.returncode != 0:
            return _fail(f"git add -A failed: {add_captured.output.decode('utf-8', 'replace')[:500]}")

        # Spec §2: the file list, read from the index the `git add -A` above just
        # built, INSIDE the container — the same rule diff_stat follows, so no
        # host git ever touches worker content. A failure here is not fatal:
        # this is evidence for the orchestrator, not a correctness gate.
        names_argv = docker_args.exec_argv(
            name, ["/usr/bin/git", "diff", "--cached", "--name-only", base_commit])
        names_captured = run(names_argv, timeout=docker_cli.T_EXPORT_STEP)
        if names_captured.returncode == 0:
            ordered = sorted({
                line.strip()
                for line in names_captured.output.decode("utf-8", errors="replace").splitlines()
                if line.strip()
            })
            files_changed = ordered[:MAX_FILES_CHANGED]
            files_changed_truncated = len(ordered) > MAX_FILES_CHANGED
```

Then both `RunArtifacts` returns that carry artifacts.

Before:

```python
    def _fail(reason: str) -> RunArtifacts:
        _cleanup(keep_volume=True)  # export_failed always keeps the volume for retry
        _cleanup_to_dot_git_only(worktree)
        return RunArtifacts(
            diff_stat=diff_stat, patch_path=patch_path,
            worktree_bytes=worktree_bytes, worktree_files=worktree_files,
            escaping_symlinks=escaping_symlinks, dropped_git_entries=dropped_git_entries,
            export_status=f"export_failed: {reason}",
        )
```

After:

```python
    def _fail(reason: str) -> RunArtifacts:
        _cleanup(keep_volume=True)  # export_failed always keeps the volume for retry
        _cleanup_to_dot_git_only(worktree)
        return RunArtifacts(
            diff_stat=diff_stat, patch_path=patch_path,
            worktree_bytes=worktree_bytes, worktree_files=worktree_files,
            escaping_symlinks=escaping_symlinks, dropped_git_entries=dropped_git_entries,
            files_changed=files_changed, files_changed_truncated=files_changed_truncated,
            export_status=f"export_failed: {reason}",
        )
```

Note: `_fail` is a closure over `export_run`'s locals, so it reads whatever `files_changed` held when it was called (`[]` on any failure before the name step).

Before:

```python
    return RunArtifacts(
        diff_stat=diff_stat, patch_path=patch_path,
        worktree_bytes=worktree_bytes, worktree_files=worktree_files,
        escaping_symlinks=escaping_symlinks, dropped_git_entries=dropped_git_entries,
        export_status="ok",
    )
```

After:

```python
    return RunArtifacts(
        diff_stat=diff_stat, patch_path=patch_path,
        worktree_bytes=worktree_bytes, worktree_files=worktree_files,
        escaping_symlinks=escaping_symlinks, dropped_git_entries=dropped_git_entries,
        files_changed=files_changed, files_changed_truncated=files_changed_truncated,
        export_status="ok",
    )
```

- [ ] **Step 9: Compute it on the host**

In `dirtywork/sandbox/host.py`, the import.

Before:

```python
from ..workspace import host_diff_stat, host_untracked
```

After:

```python
from ..workspace import host_diff_stat, host_files_changed, host_untracked
```

And `finalize`.

Before:

```python
        report = self._measure()
        return RunArtifacts(
            diff_stat=host_diff_stat(self.worktree, self.base_commit),
            untracked=host_untracked(self.worktree),
            worktree_bytes=report.bytes,
            worktree_files=report.files,
            escaping_symlinks=list(report.escaping_symlinks),
            export_status="n/a",
        )
```

After:

```python
        report = self._measure()
        files_changed, files_changed_truncated = host_files_changed(
            self.worktree, self.base_commit)
        return RunArtifacts(
            diff_stat=host_diff_stat(self.worktree, self.base_commit),
            untracked=host_untracked(self.worktree),
            files_changed=files_changed,
            files_changed_truncated=files_changed_truncated,
            worktree_bytes=report.bytes,
            worktree_files=report.files,
            escaping_symlinks=list(report.escaping_symlinks),
            export_status="n/a",
        )
```

- [ ] **Step 10: Run the export and host-sandbox tests**

Run: `python3 -m pytest tests/test_export_flow.py tests/test_sandbox_host.py tests/test_docker_sandbox.py -q`
Expected: `102 passed` (test_export_flow 11, test_sandbox_host 9, test_docker_sandbox 82).

- [ ] **Step 11: Write the failing runner tests**

Append to `tests/test_runner.py`:

```python
def test_last_tool_result_and_assistant_text_ride_on_every_result(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(content="looking now", tool_calls=[_call("c1", "read_file", {"path": "f.txt"})]),
        _resp(content="", tool_calls=[_call("c2", "list_dir", {"path": "."})]),
        _resp(content="all done"),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    last = result.extra["last_tool_result"]
    assert last["tool"] == "list_dir"
    assert '"path": "."' in last["args"]
    assert "f.txt" in last["result"]
    # the empty second reply must not overwrite the last non-empty text, and the
    # plain answer that ended the run is the newest non-empty one
    assert result.extra["last_assistant_text"] == "all done"


def test_last_tool_result_ignores_finish_and_is_null_when_no_tool_ran(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="nothing to do")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    assert result.extra["last_tool_result"] is None

    transcript2 = Transcript(tmp / "t2.jsonl")
    registry2 = default_registry(transcript=transcript2)
    provider2 = FakeProvider([
        _resp(tool_calls=[_call("c1", "read_file", {"path": "f.txt"})]),
        _resp(tool_calls=[_call("f1", "finish", {"summary": "done"})]),
    ])
    r2 = Runner(provider2, registry2, HostSandbox(wt), transcript2, model="m")
    result2 = r2.run("s", "t")
    transcript2.close()
    assert result2.status == "completed"
    assert result2.extra["last_tool_result"]["tool"] == "read_file"   # finish is skipped
    assert result2.extra["last_assistant_text"] is None               # both replies were empty
```

`Transcript`, `default_registry` and `HostSandbox` are already imported at the top of `tests/test_runner.py`.

- [ ] **Step 12: Run them to verify they fail**

Run: `python3 -m pytest tests/test_runner.py -q -k "last_tool_result or assistant_text"`
Expected: 2 failed — `KeyError: 'last_tool_result'`.

- [ ] **Step 13: Track the two values in the loop**

In `dirtywork/runner.py`, the constants.

Before:

```python
MAX_ASSISTANT_TEXT_CHARS = 64_000
```

After:

```python
MAX_ASSISTANT_TEXT_CHARS = 64_000
# Spec §2: end-of-run evidence caps. These match the transcript's own preview
# caps on purpose — the values are taken from the very same variables the
# transcript records, so a payload and a transcript can never disagree.
LAST_ARGS_CHARS = 500
LAST_RESULT_CHARS = 2000
LAST_TEXT_CHARS = 2000
```

Then the run-scope locals and `finish`'s extra (as left by Task 3).

Before:

```python
        repeats = RepeatTracker(self.stuck_repeats)
        stuck = None            # spec §1.2: set once, read by finish() below
        start = time.monotonic()
        deadline = start + self.timeout

        def finish(status: str, final: str) -> RunResult:
            # stuck_on rides on EVERY result (null unless the run ended 'stuck'),
            # so a consumer never has to branch on status to read the field.
            extra: dict = {"stuck_on": stuck}
            finalize_error = None
```

After:

```python
        repeats = RepeatTracker(self.stuck_repeats)
        stuck = None            # spec §1.2: set once, read by finish() below
        last_tool_result = None     # spec §2: the newest non-finish tool call
        last_assistant_text = None  # spec §2: the newest non-empty reply text
        start = time.monotonic()
        deadline = start + self.timeout

        def finish(status: str, final: str) -> RunResult:
            # This evidence rides on EVERY result (null when there is none), so
            # a consumer never has to branch on status to read the fields. A
            # `max_turns` run with final_message "" is the case that made this
            # necessary: without it there was nothing left to triage from.
            extra: dict = {"stuck_on": stuck,
                           "last_tool_result": last_tool_result,
                           "last_assistant_text": last_assistant_text}
            finalize_error = None
```

Then the assistant record.

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
                                for tc in tool_calls])
                if isinstance(transcript_text, str) and transcript_text.strip():
                    last_assistant_text = transcript_text[:LAST_TEXT_CHARS]
```

Then the tool record (this is the same block Task 3 edited).

Before:

```python
                    self.transcript.write("tool_result", tool=name,
                                          args=raw_args[:500],
                                          result=self.registry.transcript_preview(name, result))
                    messages.append(tool_message(tc.id, result))
```

After:

```python
                    self.transcript.write("tool_result", tool=name,
                                          args=raw_args[:500],
                                          result=self.registry.transcript_preview(name, result))
                    if name != FINISH_TOOL:
                        last_tool_result = {
                            "tool": name,
                            "args": raw_args[:LAST_ARGS_CHARS],
                            "result": result[:LAST_RESULT_CHARS] if isinstance(result, str) else "",
                        }
                    messages.append(tool_message(tc.id, result))
```

- [ ] **Step 14: Run the runner tests**

Run: `python3 -m pytest tests/test_runner.py -q`
Expected: `93 passed` (91 after Task 3 + 2).

- [ ] **Step 15: Write the failing CLI test**

Append to `tests/test_main.py`:

```python
def test_end_of_run_evidence_lands_in_stdout_and_run_json(tmp_path, monkeypatch, capsys):
    write_then_answer = [
        {"choices": [{"message": {"role": "assistant", "content": "writing it", "tool_calls": [
            {"id": "w1", "type": "function", "function": {"name": "write_file",
             "arguments": json.dumps({"path": "evidence.txt", "content": "hi\n"})}}]}}],
         "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        {"choices": [{"message": {"role": "assistant", "content": "done writing"}}],
         "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
    ]
    m = _install_host_harness(monkeypatch, tmp_path, write_then_answer)
    repo = _host_repo(tmp_path)
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "write a file"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0, payload
    assert payload["files_changed"] == ["evidence.txt"]
    assert payload["files_changed_truncated"] is False
    assert payload["last_tool_result"]["tool"] == "write_file"
    assert "Wrote 3 bytes" in payload["last_tool_result"]["result"]
    assert payload["last_assistant_text"] == "done writing"
    data = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert data["files_changed"] == ["evidence.txt"]
    assert data["last_assistant_text"] == "done writing"
```

- [ ] **Step 16: Run it to verify it fails**

Run: `python3 -m pytest tests/test_main.py -q -k end_of_run_evidence`
Expected: 1 failed — `KeyError: 'files_changed'`.

- [ ] **Step 17: Carry the evidence through the CLI**

In `dirtywork/__main__.py`, `_execute`'s `finalize()`.

Before:

```python
            return {
                "diff_stat": artifacts.diff_stat,
                "untracked": artifacts.untracked,  # host mode: git status ?? entries; docker mode: "" (git add -A folds new files into diff_stat)
                "patch_path": artifacts.patch_path,
```

After:

```python
            return {
                "diff_stat": artifacts.diff_stat,
                "untracked": artifacts.untracked,  # host mode: git status ?? entries; docker mode: "" (git add -A folds new files into diff_stat)
                "files_changed": artifacts.files_changed,
                "files_changed_truncated": artifacts.files_changed_truncated,
                "patch_path": artifacts.patch_path,
```

And the two end-of-run emitters (as left by Task 3).

Before:

```python
        watchdog_violation=extra.get("watchdog_violation"),
        watchdog_violation_kind=extra.get("watchdog_violation_kind"),
        stuck_on=extra.get("stuck_on"),
        turns=result.turns,
    )
```

After:

```python
        watchdog_violation=extra.get("watchdog_violation"),
        watchdog_violation_kind=extra.get("watchdog_violation_kind"),
        stuck_on=extra.get("stuck_on"),
        files_changed=extra.get("files_changed") or [],
        files_changed_truncated=bool(extra.get("files_changed_truncated")),
        last_tool_result=extra.get("last_tool_result"),
        last_assistant_text=extra.get("last_assistant_text"),
        turns=result.turns,
    )
```

Before:

```python
        watchdog_violation=extra.get("watchdog_violation"),
        watchdog_violation_kind=extra.get("watchdog_violation_kind"),
        stuck_on=extra.get("stuck_on"),
        resumed_from=ctx.resumed_from, provider=ctx.provider,
    ), indent=2))
```

After:

```python
        watchdog_violation=extra.get("watchdog_violation"),
        watchdog_violation_kind=extra.get("watchdog_violation_kind"),
        stuck_on=extra.get("stuck_on"),
        files_changed=extra.get("files_changed") or [],
        files_changed_truncated=bool(extra.get("files_changed_truncated")),
        last_tool_result=extra.get("last_tool_result"),
        last_assistant_text=extra.get("last_assistant_text"),
        resumed_from=ctx.resumed_from, provider=ctx.provider,
    ), indent=2))
```

`extra.get("files_changed") or []` (not a bare `.get`): the two paths where `finalize()` never produced a dict — no finalize callable, or finalize raised — must still emit `[]`, per spec §2.

- [ ] **Step 18: Run the CLI tests**

Run: `python3 -m pytest tests/test_main.py -q`
Expected: `55 passed` (54 after Task 3 + 1).

- [ ] **Step 19: Write the failing `runs show` test**

Append to `tests/test_runs.py`:

```python
def test_show_renders_end_of_run_evidence(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "eve1", {
        "slug": "eve1", "status": "max_turns", "task": "do it",
        "files_changed": ["a.ts", "b.ts", "c.ts", "d.ts"],
        "files_changed_truncated": False,
        "last_tool_result": {"tool": "bash", "args": '{"command": "npm test"}',
                             "result": "exit code: 1\n3 failing"},
        "last_assistant_text": "I could not get the suite green.",
    })
    assert runs.cmd_show(argparse.Namespace(slug="eve1", diff=False)) == 0
    out = capsys.readouterr().out
    assert "files_changed: 4 (a.ts, b.ts, c.ts, ...)" in out

    assert runs.cmd_show(argparse.Namespace(slug="eve1", diff=False, markdown=True)) == 0
    md = capsys.readouterr().out
    assert "**files changed (4)**" in md
    assert "- `d.ts`" in md
    assert "<details><summary>last tool result: bash(" in md
    assert "3 failing" in md
    assert "> I could not get the suite green." in md
```

- [ ] **Step 20: Run it to verify it fails**

Run: `python3 -m pytest tests/test_runs.py -q -k end_of_run_evidence`
Expected: 1 failed — `AssertionError` on `"files_changed: 4 (a.ts, b.ts, c.ts, ...)" in out`.

- [ ] **Step 21: Render the evidence in both `runs show` views**

In `dirtywork/runs.py`, the field list (as left by Task 3).

Before:

```python
SHOW_FIELDS = ("slug", "status", "sandbox", "task", "model", "provider", "turns",
               "resumed_from", "resumed_by", "branch", "worktree", "started", "ended",
               "stuck_on")
```

After:

```python
SHOW_FIELDS = ("slug", "status", "sandbox", "task", "model", "provider", "turns",
               "resumed_from", "resumed_by", "branch", "worktree", "started", "ended",
               "stuck_on", "files_changed")
```

And the summary formatter (as left by Task 3).

Before:

```python
    if key == "stuck_on" and isinstance(value, dict):
        return str(value.get("command") or "-")
    text = str(value)
```

After:

```python
    if key == "stuck_on" and isinstance(value, dict):
        return str(value.get("command") or "-")
    if key == "files_changed" and isinstance(value, list):
        head = ", ".join(str(p) for p in value[:3])
        tail = ", ..." if len(value) > 3 else ""
        return f"{len(value)} ({head}{tail})"
    text = str(value)
```

And the Markdown result section (as left by Task 3).

Before:

```python
    diff_stat = data.get("diff_stat") or end.get("diff_stat")
    if diff_stat:
        lines += ["**diff_stat**", ""] + _md_block(str(diff_stat))
    final = _final_message(events)
```

After:

```python
    files_changed = data.get("files_changed") or end.get("files_changed")
    if isinstance(files_changed, list) and files_changed:
        truncated = data.get("files_changed_truncated") or end.get("files_changed_truncated")
        note = " — list truncated" if truncated else ""
        lines += [f"**files changed ({len(files_changed)}){note}**", ""]
        lines += [f"- `{_md_inline(path, MD_ARGS_CHARS)}`" for path in files_changed]
        lines.append("")
    last_tool = data.get("last_tool_result") or end.get("last_tool_result")
    if isinstance(last_tool, dict):
        lines.append(f"<details><summary>last tool result: "
                     f"{_md_inline(last_tool.get('tool'), MD_ARGS_CHARS)}"
                     f"({_md_inline(last_tool.get('args'), MD_ARGS_CHARS)})</summary>")
        lines.append("")
        lines += _md_block(str(last_tool.get("result") or ""))
        lines += ["</details>", ""]
    last_text = data.get("last_assistant_text") or end.get("last_assistant_text")
    if last_text:
        lines += ["**last assistant text**", ""]
        lines += [f"> {line}" for line in str(last_text).splitlines()] + [""]
    diff_stat = data.get("diff_stat") or end.get("diff_stat")
    if diff_stat:
        lines += ["**diff_stat**", ""] + _md_block(str(diff_stat))
    final = _final_message(events)
```

And keep a re-export in step with the run (`cmd_export`'s run.json merge).

Before:

```python
    data["escaping_symlinks"] = artifacts.escaping_symlinks
    data["dropped_git_entries"] = artifacts.dropped_git_entries
    rundir.write_run_json(run_dir, data)
```

After:

```python
    data["escaping_symlinks"] = artifacts.escaping_symlinks
    data["dropped_git_entries"] = artifacts.dropped_git_entries
    data["files_changed"] = artifacts.files_changed
    data["files_changed_truncated"] = artifacts.files_changed_truncated
    rundir.write_run_json(run_dir, data)
```

- [ ] **Step 22: Run the runs tests**

Run: `python3 -m pytest tests/test_runs.py -q`
Expected: `77 passed` (76 after Task 3 + 1).

- [ ] **Step 23: Document the three fields — `docs/transcript-schema.md`**

The `run_end` table, after the `stuck_on` row Task 3 added.

Before:

```
| `stuck_on` | | ✓ | object \| null | 0.8: `{command, output, repeats}` for the failing bash call that ended the run as `stuck` (`output` capped at 4000 chars); `null` on every other status |

## Statuses
```

After:

```
| `stuck_on` | | ✓ | object \| null | 0.8: `{command, output, repeats}` for the failing bash call that ended the run as `stuck` (`output` capped at 4000 chars); `null` on every other status |
| `files_changed` | | ✓ | list | 0.8: repo-relative paths the run changed, sorted, capped at 1000. Docker mode: `git diff --cached --name-only <base_commit>` in the container right after the export's `git add -A`. Host mode: `git diff --name-only <base_commit>` plus `git ls-files --others --exclude-standard`. `[]` when nothing changed or the export never ran |
| `files_changed_truncated` | | ✓ | boolean | 0.8: true when `files_changed` was cut at the 1000-path cap |
| `last_tool_result` | | ✓ | object \| null | 0.8: `{tool, args, result}` for the last tool call the runner executed other than `finish` (`args` ≤500 chars, `result` ≤2000 chars); `null` if no tool ever ran |
| `last_assistant_text` | | ✓ | string \| null | 0.8: the model's last non-empty assistant text, capped at 2000 chars; `null` if there was none |

## Statuses
```

The stdout field list (as left by Task 3).

Before:

```
`watchdog_violation_kind`, `stuck_on`, and `export_status` on the
exception-recovery path.
```

After:

```
`watchdog_violation_kind`, `stuck_on`, `files_changed`,
`files_changed_truncated`, `last_tool_result`, `last_assistant_text`, and
`export_status` on the exception-recovery path.
```

The `run.json` table, after the `stuck_on` row Task 3 added.

Before:

```
| `stuck_on` | end | 0.8: `{command, output, repeats}` when the run ended `stuck`, else null |
```

After:

```
| `stuck_on` | end | 0.8: `{command, output, repeats}` when the run ended `stuck`, else null |
| `files_changed` | end, export | 0.8: sorted repo-relative paths the run changed, capped at 1000; rewritten by `dirtywork runs export` |
| `files_changed_truncated` | end, export | 0.8: true when the 1000-path cap cut the list |
| `last_tool_result` | end | 0.8: `{tool, args, result}` for the last non-`finish` tool call, or null |
| `last_assistant_text` | end | 0.8: the last non-empty assistant text (≤2000 chars), or null |
```

- [ ] **Step 24: Document the three fields — README**

In `README.md`, the Machine contract prose.

Before:

```
`finalize_error`, `watchdog_violation`, and `watchdog_violation_kind` are added on the normal
end-of-run path — i.e. whenever `runner.run()` returns a result, `completed`
or not — normally `null`; see `run_end` below for what each means. The two
```

After:

```
`finalize_error`, `watchdog_violation`, `watchdog_violation_kind`, `stuck_on`,
`files_changed`, `files_changed_truncated`, `last_tool_result` and
`last_assistant_text` are added on the normal
end-of-run path — i.e. whenever `runner.run()` returns a result, `completed`
or not — normally `null` (`[]`/`false` for the two list/flag fields); see
`run_end` below for what each means. The last four are there so a run that
ends with an empty `final_message` is still triageable without opening the
transcript: what it changed, what it last ran and what it last said. On a
`completed` run they are just as useful — "the last thing the worker checked
failed, and it called `finish` anyway" reads straight off `last_tool_result`.
The two
```

- [ ] **Step 25: Teach the doc test about the new run_end fields**

In `tests/test_transcript_schema.py` (as left by Task 3).

Before:

```python
RUN_END_FIELDS = ["diff_stat", "untracked", "patch_path", "escaping_symlinks",
                  "dropped_git_entries", "worktree_bytes", "worktree_files",
                  "export_status", "watchdog_violation", "watchdog_violation_kind",
                  "finalize_error", "stuck_on"]
```

After:

```python
RUN_END_FIELDS = ["diff_stat", "untracked", "patch_path", "escaping_symlinks",
                  "dropped_git_entries", "worktree_bytes", "worktree_files",
                  "export_status", "watchdog_violation", "watchdog_violation_kind",
                  "finalize_error", "stuck_on", "files_changed",
                  "files_changed_truncated", "last_tool_result", "last_assistant_text"]
```

- [ ] **Step 26: Run the full suite**

Run: `python3 -m pytest -q`
Expected: `869 passed` (862 after Task 3 + 7), 18 deselected.

- [ ] **Step 27: Commit**

```bash
git add dirtywork/workspace.py dirtywork/sandbox/__init__.py dirtywork/sandbox/host.py dirtywork/sandbox/export.py dirtywork/runner.py dirtywork/__main__.py dirtywork/runs.py tests/test_workspace.py tests/test_export_flow.py tests/test_runner.py tests/test_main.py tests/test_runs.py tests/test_transcript_schema.py README.md docs/transcript-schema.md
git commit -m "feat: files_changed, last_tool_result and last_assistant_text on every payload"
```

---

### Task 5: `--verify` — the operator's gate, first-class (spec §4)

**Files:**
- Modify: `dirtywork/runner.py` (constants + `parse_exit_code`; `Runner.__init__`; `run()`'s `run_verify`/`check_verify` closures; the plain-answer completion at `:396-398` and the `finish`-tool completion at `:466-467`)
- Modify: `dirtywork/__main__.py` (`_add_run_flags`, `_load_resume_target`, `Runner(...)`, run.json + stdout)
- Modify: `dirtywork/runs.py` (`SHOW_FIELDS`, `_summary_value`, `_md_result`)
- Modify: `tests/test_runner.py`, `tests/test_main.py`, `tests/test_runs.py`, `tests/test_transcript_schema.py`
- Modify: `README.md` (a "Verifying a run" paragraph under *Use*, flags, flag prose, status enum, exit codes, Troubleshooting)
- Modify: `docs/transcript-schema.md` (a `verify` event section, `run_end.verify`, statuses table, stdout list, `run.json.verify`)

**Interfaces:**
- Consumes: `Sandbox.bash(command, timeout) -> str` (`sandbox/__init__.py:60`) — the same method the `bash` tool calls, so the guardrail denylist, `--network none`, the budget watchdog and the process reaper all apply unchanged; `budget.BudgetExceeded`, `sandbox.SandboxError` (both already imported in `runner.py:10,13`); `__main__._load_resume_target`'s inheritance pattern for `--allow-commit` (`__main__.py:521-522`).
- Produces:
  - `runner.DEFAULT_VERIFY_ROUNDS = 1`, `runner.DEFAULT_VERIFY_TIMEOUT = 600`, `runner.VERIFY_OUTPUT_CHARS = 4000`, `runner.VERIFY_FEEDBACK: str`
  - `runner.parse_exit_code(result) -> int | None`
  - `Runner.__init__(..., verify: str | None = None, verify_rounds: int = DEFAULT_VERIFY_ROUNDS, verify_timeout: int = DEFAULT_VERIFY_TIMEOUT)`
  - `RunResult.extra["verify"]` — `dict | None` with `{command, exit_code, output_tail, rounds, passed}`
  - transcript event `verify` with `{round, exit_code, passed}`
  - CLI: `--verify CMD`, `--verify-rounds N`, `--verify-timeout S` on `run` and `resume`; status `verify_failed`

Two decisions taken here, both recorded in the README text this task writes:

1. **`--verify-rounds N` is the number of FIX ROUNDS after a failed verify** (owner ruling 2026-08-18 19:58, correcting the spec's literal `rounds_used < verify_rounds`): the command may run N+1 times. The default `1` hands the first failure back to the worker once; `0` verifies once and ends the run either way. The `rounds` field in the payload still counts executions.
2. **Only the verify *command* is inherited on resume.** `run.json` records `verify` (the result object, which carries `command`) and nothing else — spec §7's `run.json` field list adds `verify` alone, so there is no recorded rounds/timeout to inherit. `--verify-rounds`/`--verify-timeout` fall back to their own defaults on a resume that does not pass them.

- [ ] **Step 1: Write the failing runner tests**

Append to `tests/test_runner.py`:

```python
def test_verify_passes_and_the_run_completes(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "done"})])])
    r = Runner(provider, registry, sandbox, transcript, model="m", verify="true")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    verify = result.extra["verify"]
    assert verify["command"] == "true"
    assert verify["exit_code"] == 0
    assert verify["passed"] is True
    assert verify["rounds"] == 1
    # tools.bash returns "exit code: 0\n" for a command with no output
    assert verify["output_tail"].startswith("exit code: 0")
    event = next(e for e in _events(tmp) if e["event"] == "verify")
    assert event == {"ts": event["ts"], "event": "verify", "round": 1,
                     "exit_code": 0, "passed": True}


def test_verify_failure_with_no_round_left_is_verify_failed(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "done"})])])
    r = Runner(provider, registry, sandbox, transcript, model="m",
               verify="echo boom; exit 3", verify_rounds=0)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "verify_failed"
    assert result.final_message == "done"          # the worker's own summary is kept
    assert result.extra["verify"]["passed"] is False
    assert result.extra["verify"]["exit_code"] == 3
    assert "boom" in result.extra["verify"]["output_tail"]
    assert result.extra["verify"]["rounds"] == 1


def test_verify_failure_with_a_round_left_feeds_back_and_retries(parts, tmp_path):
    wt, registry, sandbox, transcript, tmp = parts
    marker = wt / "fixed"
    provider = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "first try"})]),
        _resp(tool_calls=[_call("w1", "write_file", {"path": "fixed", "content": "y"})]),
        _resp(tool_calls=[_call("f2", "finish", {"summary": "second try"})]),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m",
               verify="test -e fixed", verify_rounds=1)
    result = r.run("s", "t")
    transcript.close()
    assert marker.is_file()
    assert result.status == "completed"
    assert result.final_message == "second try"
    assert result.extra["verify"]["rounds"] == 2 and result.extra["verify"]["passed"] is True
    # the failed round was fed back as a user message naming the command
    feedback = [m for m in provider.requests[-1] if m["role"] == "user"]
    assert any("VERIFY FAILED (round 1 of 2)" in m["content"] for m in feedback)
    assert any("test -e fixed" in m["content"] for m in feedback)
    verify_events = [e for e in _events(tmp) if e["event"] == "verify"]
    assert [e["passed"] for e in verify_events] == [False, True]


def test_verify_on_a_plain_answer_completion_and_error_passthrough(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="I am done")])
    r = Runner(provider, registry, sandbox, transcript, model="m", verify="exit 1")
    result = r.run("s", "t")
    assert result.status == "verify_failed"
    assert result.final_message == "I am done"

    class ExplodingSandbox:
        def bash(self, command, timeout=120):
            from dirtywork.budget import BudgetExceeded
            raise BudgetExceeded("worktree over budget")

    transcript2 = Transcript(tmp / "t2.jsonl")
    provider2 = FakeProvider([_resp(content="I am done")])
    r2 = Runner(provider2, default_registry(transcript=transcript2), ExplodingSandbox(),
                transcript2, model="m", verify="true")
    result2 = r2.run("s", "t")
    transcript2.close()
    assert result2.status == "budget_exceeded"
    assert result2.extra["verify"] is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_runner.py -q -k verify`
Expected: 4 failed — `TypeError: __init__() got an unexpected keyword argument 'verify'`.

- [ ] **Step 3: Add the verify constants and the exit-code parser**

In `dirtywork/runner.py`, after the stuck constants Task 3 added.

Before:

```python
DEFAULT_STUCK_REPEATS = 4
STUCK_OUTPUT_CHARS = 4000
```

After:

```python
DEFAULT_STUCK_REPEATS = 4
STUCK_OUTPUT_CHARS = 4000
# Spec §4: the operator's own gate, run inside the sandbox on the completion
# path. `verify_rounds` is how many FIX ROUNDS follow a failed verify — the
# command may run verify_rounds + 1 times. The default 1 hands the first failure
# back to the worker once; 0 verifies once and ends the run either way.
DEFAULT_VERIFY_ROUNDS = 1
DEFAULT_VERIFY_TIMEOUT = 600
VERIFY_OUTPUT_CHARS = 4000
VERIFY_FEEDBACK = (
    "VERIFY FAILED (round {round} of {rounds}). The verification command\n"
    "  {command}\n"
    "exited with code {exit_code}. Output tail:\n"
    "{output}\n"
    "Fix the problem, then call finish(summary=...) again."
)
```

Then the parser, inserted immediately **after** `_bash_fingerprint` and immediately **before** `class ProgressTracker:`.

```python
def parse_exit_code(result):
    """The integer after 'exit code: ' on a bash result's first line, or None
    for an ERROR:/BLOCKED: result that never produced an exit status at all.
    The same first-line rule RepeatTracker._failed uses, read for its value."""
    if not isinstance(result, str):
        return None
    head = result.split("\n", 1)[0]
    prefix = "exit code: "
    if not head.startswith(prefix):
        return None
    try:
        return int(head[len(prefix):].strip())
    except ValueError:
        return None
```

- [ ] **Step 4: Accept the three verify settings on the Runner**

In `dirtywork/runner.py`, `Runner.__init__` (as left by Task 3).

Before:

```python
                 stall_turns: int = DEFAULT_STALL_TURNS,
                 context_window: int | None = None,
                 stuck_repeats: int = DEFAULT_STUCK_REPEATS):
```

After:

```python
                 stall_turns: int = DEFAULT_STALL_TURNS,
                 context_window: int | None = None,
                 stuck_repeats: int = DEFAULT_STUCK_REPEATS,
                 verify: str | None = None,
                 verify_rounds: int = DEFAULT_VERIFY_ROUNDS,
                 verify_timeout: int = DEFAULT_VERIFY_TIMEOUT):
```

And, in the body.

Before:

```python
        self.stall_turns = stall_turns
        self.stuck_repeats = stuck_repeats
```

After:

```python
        self.stall_turns = stall_turns
        self.stuck_repeats = stuck_repeats
        self.verify = verify
        self.verify_rounds = verify_rounds
        # Clamped to the bash tool's own range so --verify can never ask the
        # sandbox for a timeout the bash path would refuse.
        self.verify_timeout = max(1, min(int(verify_timeout), 600))
```

- [ ] **Step 5: Run the gate on both completion paths**

In `dirtywork/runner.py`, `Runner.run` — the run-scope locals and `finish`'s extra (as left by Task 4).

Before:

```python
        last_tool_result = None     # spec §2: the newest non-finish tool call
        last_assistant_text = None  # spec §2: the newest non-empty reply text
        start = time.monotonic()
        deadline = start + self.timeout
```

After:

```python
        last_tool_result = None     # spec §2: the newest non-finish tool call
        last_assistant_text = None  # spec §2: the newest non-empty reply text
        verify_state = None         # spec §4.3: the LAST verify run, or None
        verify_rounds_used = 0
        start = time.monotonic()
        deadline = start + self.timeout
```

Before:

```python
            extra: dict = {"stuck_on": stuck,
                           "last_tool_result": last_tool_result,
                           "last_assistant_text": last_assistant_text}
```

After:

```python
            extra: dict = {"stuck_on": stuck,
                           "last_tool_result": last_tool_result,
                           "last_assistant_text": last_assistant_text,
                           "verify": verify_state}
```

Then insert the two closures immediately **after** `check_progress` (which ends with `            return None, None`) and immediately **before** `        try:` (the loop's outer try).

```python
        def run_verify():
            """One execution of the operator's gate (spec §4.2). Runs through
            the same sandbox.bash the tool uses — same guardrails, same budget
            watchdog, same reaper, same environment the worker's bash had — and
            happens BEFORE finalize(), so in docker mode the container is still
            alive and nothing has been exported yet. Returns the feedback text
            for another round, or None when the run may end now (verify_state
            says whether it passed)."""
            nonlocal verify_state, verify_rounds_used
            verify_rounds_used += 1
            result = self.sandbox.bash(self.verify, self.verify_timeout)
            exit_code = parse_exit_code(result)
            passed = exit_code == 0
            tail = result[-VERIFY_OUTPUT_CHARS:] if isinstance(result, str) else ""
            verify_state = {"command": self.verify, "exit_code": exit_code,
                            "output_tail": tail, "rounds": verify_rounds_used,
                            "passed": passed}
            self.transcript.write("verify", round=verify_rounds_used,
                                  exit_code=exit_code, passed=passed)
            if passed or verify_rounds_used > self.verify_rounds:
                return None
            return VERIFY_FEEDBACK.format(round=verify_rounds_used,
                                          rounds=self.verify_rounds + 1,
                                          command=self.verify,
                                          exit_code=exit_code, output=tail)

        def check_verify(final: str):
            """(RunResult to return, or None; feedback to append, or None) for a
            completion path. Both completion paths — the finish tool and a plain
            answer — go through this one function, so they can never disagree
            about what verifying means. BudgetExceeded/SandboxError end the run
            with the same statuses a tool call would."""
            if not self.verify:
                return finish("completed", final), None
            try:
                feedback = run_verify()
            except BudgetExceeded as e:
                return finish("budget_exceeded", e.reason), None
            except SandboxError as e:
                return finish("sandbox_error", str(e)), None
            if feedback is not None:
                return None, feedback
            if verify_state["passed"]:
                return finish("completed", final), None
            return finish("verify_failed", final), None
```

Then the plain-answer completion.

Before:

```python
                    if kind == "answer":
                        messages.append(assistant_message(content, None))
                        return finish("completed", content)
```

After:

```python
                    if kind == "answer":
                        messages.append(assistant_message(content, None))
                        ended, feedback = check_verify(content)
                        if ended is not None:
                            return ended
                        messages.append({"role": "user", "content": feedback})
                        continue
```

Then the `finish`-tool completion (the block Task 3 edited).

Before:

```python
                if pending_finish is not None:
                    return finish("completed", pending_finish)

                # Same rule as `finish` in a mixed turn: the turn's remaining
```

After:

```python
                if pending_finish is not None:
                    ended, feedback = check_verify(pending_finish)
                    if ended is not None:
                        return ended
                    messages.append({"role": "user", "content": feedback})
                    continue

                # Same rule as `finish` in a mixed turn: the turn's remaining
```

Appending a user message after the turn's `tool` messages is the same history shape the malformed/nudge path already produces, so no chat template ever sees two consecutive user messages.

- [ ] **Step 6: Run the runner tests**

Run: `python3 -m pytest tests/test_runner.py -q`
Expected: `97 passed` (93 after Task 4 + 4).

- [ ] **Step 7: Write the failing CLI tests**

Append to `tests/test_main.py`:

```python
def test_verify_flag_records_the_gate_and_can_fail_the_run(tmp_path, monkeypatch, capsys):
    finished = [{"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "f1", "type": "function", "function": {"name": "finish",
         "arguments": json.dumps({"summary": "claimed done"})}}]}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1}}]
    m = _install_host_harness(monkeypatch, tmp_path, finished)
    repo = _host_repo(tmp_path)
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none",
                 "--verify", "echo nope; exit 4", "do it"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1, payload
    assert payload["status"] == "verify_failed"
    assert payload["verify"]["command"] == "echo nope; exit 4"
    assert payload["verify"]["exit_code"] == 4
    assert payload["verify"]["passed"] is False
    assert "nope" in payload["verify"]["output_tail"]
    data = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert data["status"] == "verify_failed"
    assert data["verify"]["exit_code"] == 4


def test_verify_is_null_without_the_flag_and_resume_inherits_the_command(
        tmp_path, monkeypatch, capsys):
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    assert m.main(["run", "--repo", str(repo), "--sandbox", "none", "--max-turns", "1",
                   "--verify", "true", "do it"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["verify"]["command"] == "true"

    patch_provider(monkeypatch, m,
                        lambda base_url=None: _ScriptedClient(base_url, _resume_responses()))
    assert m.main(["resume", Path(first["run_dir"]).name, "--feedback", "again"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["verify"]["command"] == "true"      # inherited from the prior run.json
```

Note: the second test passes `--feedback` because the prior run ended `completed` and Task 9's gate refuses that without feedback. Write it with `--feedback` from the start; Task 9 adds the flag, so **run this test only after Task 9** — see Step 9 below.

- [ ] **Step 8: Run the first CLI test to verify it fails**

Run: `python3 -m pytest tests/test_main.py -q -k verify_flag_records`
Expected: 1 failed — `SystemExit: 2` from argparse (`unrecognized arguments: --verify ...`).

- [ ] **Step 9: Temporarily mark the resume-inheritance test**

`--feedback` does not exist until Task 9. Add the skip marker now and remove it in Task 9, so this task's suite is green without weakening the assertion.

In `tests/test_main.py`, immediately above the second test.

Before:

```python
def test_verify_is_null_without_the_flag_and_resume_inherits_the_command(
        tmp_path, monkeypatch, capsys):
```

After:

```python
@pytest.mark.skip(reason="needs resume --feedback (Task 9); unskipped there")
def test_verify_is_null_without_the_flag_and_resume_inherits_the_command(
        tmp_path, monkeypatch, capsys):
```

`pytest` is already imported at the top of `tests/test_main.py`.

- [ ] **Step 10: Add the three flags and the resume inheritance**

In `dirtywork/__main__.py`, the runner import (as left by Task 3).

Before:

```python
from .runner import DEFAULT_STALL_TURNS, DEFAULT_STUCK_REPEATS, Runner, resolve_context_window
```

After:

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

Then `_add_run_flags`, after the `--stuck-repeats` argument Task 3 added.

Before:

```python
    p.add_argument("--stuck-repeats", type=_non_negative_int, default=DEFAULT_STUCK_REPEATS,
                   help="end the run as 'stuck' after the same failing bash command runs N "
                        "times in a row (0 disables); independent of --stall-turns")
```

After:

```python
    p.add_argument("--stuck-repeats", type=_non_negative_int, default=DEFAULT_STUCK_REPEATS,
                   help="end the run as 'stuck' after the same failing bash command runs N "
                        "times in a row (0 disables); independent of --stall-turns")
    p.add_argument("--verify", default=None, metavar="CMD",
                   help="run CMD in the sandbox when the worker declares itself done; a "
                        "non-zero exit ends the run as 'verify_failed' (resume inherits the "
                        "command from the run it continues)")
    p.add_argument("--verify-rounds", type=_non_negative_int, default=DEFAULT_VERIFY_ROUNDS,
                   help="fix rounds after a failed --verify (default 1: the first failure goes "
                        "back to the worker once; 0 verifies once and ends the run either way)")
    p.add_argument("--verify-timeout", type=_positive_int, default=DEFAULT_VERIFY_TIMEOUT,
                   help="seconds for the --verify command (default 600, clamped to 1-600)")
```

Then the resume inheritance, at the end of `_load_resume_target`.

Before:

```python
    if args.allow_commit is None:
        args.allow_commit = bool(prior.get("allow_commit", False))
    return prior
```

After:

```python
    if args.allow_commit is None:
        args.allow_commit = bool(prior.get("allow_commit", False))
    if getattr(args, "verify", None) is None:
        # run.json records the verify RESULT object (spec §4.3), which carries
        # the command; rounds/timeout are not recorded, so they keep their own
        # defaults unless this invocation passes them.
        prior_verify = prior.get("verify")
        if isinstance(prior_verify, dict) and prior_verify.get("command"):
            args.verify = prior_verify["command"]
    return prior
```

- [ ] **Step 11: Thread the settings into the Runner and both payloads**

In `dirtywork/__main__.py`, `_execute`'s `Runner(...)` (as left by Task 3).

Before:

```python
            stall_turns=args.stall_turns, context_window=ctx.context_window,
            stuck_repeats=getattr(args, "stuck_repeats", DEFAULT_STUCK_REPEATS),
        )
```

After:

```python
            stall_turns=args.stall_turns, context_window=ctx.context_window,
            stuck_repeats=getattr(args, "stuck_repeats", DEFAULT_STUCK_REPEATS),
            verify=getattr(args, "verify", None),
            verify_rounds=getattr(args, "verify_rounds", DEFAULT_VERIFY_ROUNDS),
            verify_timeout=getattr(args, "verify_timeout", DEFAULT_VERIFY_TIMEOUT),
        )
```

And both emitters (as left by Task 4).

Before:

```python
        last_tool_result=extra.get("last_tool_result"),
        last_assistant_text=extra.get("last_assistant_text"),
        turns=result.turns,
    )
```

After:

```python
        last_tool_result=extra.get("last_tool_result"),
        last_assistant_text=extra.get("last_assistant_text"),
        verify=extra.get("verify"),
        turns=result.turns,
    )
```

Before:

```python
        last_tool_result=extra.get("last_tool_result"),
        last_assistant_text=extra.get("last_assistant_text"),
        resumed_from=ctx.resumed_from, provider=ctx.provider,
    ), indent=2))
```

After:

```python
        last_tool_result=extra.get("last_tool_result"),
        last_assistant_text=extra.get("last_assistant_text"),
        verify=extra.get("verify"),
        resumed_from=ctx.resumed_from, provider=ctx.provider,
    ), indent=2))
```

- [ ] **Step 12: Run the CLI tests**

Run: `python3 -m pytest tests/test_main.py -q`
Expected: `56 passed, 1 skipped` (55 after Task 4, plus the 2 tests written in Step 7 — one of them skipped until Task 9).

- [ ] **Step 13: Write the failing `runs show` test**

Append to `tests/test_runs.py`:

```python
def test_show_renders_verify(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "ver1", {
        "slug": "ver1", "status": "verify_failed", "task": "do it",
        "verify": {"command": "npm run gate", "exit_code": 2,
                   "output_tail": "exit code: 2\n2 failing", "rounds": 1, "passed": False},
    })
    assert runs.cmd_show(argparse.Namespace(slug="ver1", diff=False)) == 0
    assert "verify: failed (exit 2)" in capsys.readouterr().out

    assert runs.cmd_show(argparse.Namespace(slug="ver1", diff=False, markdown=True)) == 0
    md = capsys.readouterr().out
    assert "**verify** — failed (exit 2) after 1 round(s)" in md
    assert "npm run gate" in md and "2 failing" in md
```

- [ ] **Step 14: Run it to verify it fails**

Run: `python3 -m pytest tests/test_runs.py -q -k renders_verify`
Expected: 1 failed — `AssertionError: assert 'verify: failed (exit 2)' in ...`.

- [ ] **Step 15: Render `verify` in both `runs show` views**

In `dirtywork/runs.py`, the field list (as left by Task 4).

Before:

```python
SHOW_FIELDS = ("slug", "status", "sandbox", "task", "model", "provider", "turns",
               "resumed_from", "resumed_by", "branch", "worktree", "started", "ended",
               "stuck_on", "files_changed")
```

After:

```python
SHOW_FIELDS = ("slug", "status", "sandbox", "task", "model", "provider", "turns",
               "resumed_from", "resumed_by", "branch", "worktree", "started", "ended",
               "stuck_on", "files_changed", "verify")
```

And the summary formatter (as left by Task 4).

Before:

```python
    if key == "files_changed" and isinstance(value, list):
        head = ", ".join(str(p) for p in value[:3])
        tail = ", ..." if len(value) > 3 else ""
        return f"{len(value)} ({head}{tail})"
    text = str(value)
```

After:

```python
    if key == "files_changed" and isinstance(value, list):
        head = ", ".join(str(p) for p in value[:3])
        tail = ", ..." if len(value) > 3 else ""
        return f"{len(value)} ({head}{tail})"
    if key == "verify" and isinstance(value, dict):
        state = "passed" if value.get("passed") else "failed"
        return f"{state} (exit {value.get('exit_code')})"
    text = str(value)
```

And the Markdown result section (as left by Task 4) — the verify block goes first, because it is the answer to "did this run actually work".

Before:

```python
    lines.append("")
    stuck_on = data.get("stuck_on") or end.get("stuck_on")
```

After:

```python
    lines.append("")
    verify = data.get("verify") or end.get("verify")
    if isinstance(verify, dict):
        state = "passed" if verify.get("passed") else "failed"
        lines += [f"**verify** — {state} (exit {verify.get('exit_code')}) after "
                  f"{verify.get('rounds')} round(s)", ""]
        lines += _md_block(str(verify.get("command") or ""))
        lines += _md_block(str(verify.get("output_tail") or ""))
    stuck_on = data.get("stuck_on") or end.get("stuck_on")
```

- [ ] **Step 16: Run the runs tests**

Run: `python3 -m pytest tests/test_runs.py -q`
Expected: `78 passed` (77 after Task 4 + 1).

- [ ] **Step 17: Document `--verify` — README *Use* section**

In `README.md`, under "## Use", after the "Clean up a run" bullet.

Before:

```
- **Clean up a run:** `dirtywork runs clean <slug>` — see
  [Inspecting, cleaning up and re-exporting runs](#inspecting-cleaning-up-and-re-exporting-runs)
  for the safety rules and the rest of the `runs` subcommands.

- **All flags, stdout JSON, exit codes, transcript events:** see
  [Machine contract](#machine-contract).
```

After:

```
- **Clean up a run:** `dirtywork runs clean <slug>` — see
  [Inspecting, cleaning up and re-exporting runs](#inspecting-cleaning-up-and-re-exporting-runs)
  for the safety rules and the rest of the `runs` subcommands.

- **All flags, stdout JSON, exit codes, transcript events:** see
  [Machine contract](#machine-contract).

#### Verifying a run

    dirtywork run --repo ~/repos/someproject --verify 'npm test' "Add a unit test for X"

`--verify CMD` makes your gate the harness's gate. The moment the worker
declares itself done — `finish(summary=…)` or a plain answer — dirtywork runs
`CMD` inside the sandbox, through the same `bash` path the worker used (same
guardrails, same `--network none`, same budget watchdog), and **before** the
export. A zero exit leaves the run `completed`; anything else ends it
`verify_failed` (exit 1), with the command, its exit code and a 4000-char
output tail in `verify` on the stdout JSON, the `run_end` event and `run.json`.
`--verify-rounds N` (default 1) is how many fix rounds follow a failure: the
default hands the first failure back to the worker as a message naming the
command, the exit code and the output tail, and lets it try once more against
the ordinary `--max-turns`/`--timeout` budget (the command may run N+1 times);
`0` verifies once and ends the run either way. `--verify-timeout S` (default 600, clamped to
1–600) bounds each run. In docker mode the command can only use what the image
ships — see the callout under *Review a run*. `dirtywork resume` inherits the
verify command from the run it continues.
```

- [ ] **Step 18: Document `--verify` — README Machine contract**

The flag block, after the `--stuck-repeats` line Task 3 added.

Before:

```
    [--stuck-repeats 4]               # end as `stuck` after N identical failing bash runs; 0 disables
```

After:

```
    [--stuck-repeats 4]               # end as `stuck` after N identical failing bash runs; 0 disables
    [--verify "<cmd>"]                # run this in the sandbox on completion; non-zero → `verify_failed`
    [--verify-rounds 1]               # fix rounds after a failed --verify (0 = verify once, no retry)
    [--verify-timeout 600]            # seconds per --verify run, clamped to 1-600
```

The flag prose, after the `--stuck-repeats` bullet Task 3 added.

Before:

```
  cannot see, since every `edit_file` counts as progress. No nudge is sent:
  the point is to stop paying for turns. `0` disables.
```

After:

```
  cannot see, since every `edit_file` counts as progress. No nudge is sent:
  the point is to stop paying for turns. `0` disables.
- `--verify CMD` / `--verify-rounds N` / `--verify-timeout S` — see
  [Verifying a run](#verifying-a-run). `--verify-rounds` counts **fix rounds
  after a failed verify** — the command may run N+1 times; `0` verifies once and
  ends the run either way. `dirtywork resume` inherits the command (not the rounds or the
  timeout, which `run.json` does not record) from the run it continues.
```

The status enum (as left by Task 3).

Before:

```
`status` is one of: `completed`, `max_turns`, `timeout`, `stalled`, `stuck`,
`context_exhausted`, `model_error`, `interrupted`, `budget_exceeded`,
`sandbox_error`, `export_failed`. When the run fails before a `RunResult`
```

After:

```
`status` is one of: `completed`, `max_turns`, `timeout`, `stalled`, `stuck`,
`verify_failed`, `context_exhausted`, `model_error`, `interrupted`,
`budget_exceeded`, `sandbox_error`, `export_failed`. When the run fails before a `RunResult`
```

The exit-code list (as left by Task 3).

Before:

```
- `1` — any non-`completed` status (`max_turns`, `timeout`, `stalled`,
  `stuck`, `context_exhausted`, `model_error`, `interrupted`,
  `budget_exceeded`, `sandbox_error`, `export_failed`); the worktree and branch are kept for
```

After:

```
- `1` — any non-`completed` status (`max_turns`, `timeout`, `stalled`,
  `stuck`, `verify_failed`, `context_exhausted`, `model_error`, `interrupted`,
  `budget_exceeded`, `sandbox_error`, `export_failed`); the worktree and branch are kept for
```

Troubleshooting, after the `stuck` bullet Task 3 added.

Before:

```
  `dirtywork resume <slug> --feedback "..."`. `--stuck-repeats 0` disables it.
```

After:

```
  `dirtywork resume <slug> --feedback "..."`. `--stuck-repeats 0` disables it.
- **status `verify_failed`** — the worker declared itself done but the
  `--verify` command exited non-zero on its last allowed run; the worktree is
  kept and the export still ran. Read `verify.output_tail` in the payload, then
  `dirtywork resume <slug> --feedback "<what to fix>"` — the resume inherits
  the same verify command. In docker mode, check first that the command can run
  at all in the image (`--network none`, nothing installed at run time).
```

- [ ] **Step 19: Document the `verify` event and field — `docs/transcript-schema.md`**

Insert a new section immediately **after** the `sandbox_reset` section and immediately **before** `### run_end`.

Before:

```
| `reason` | | ✓ | string | why the reset happened |

### `run_end`
```

After:

```
| `reason` | | ✓ | string | why the reset happened |

### `verify`

**v2 only**, 0.8 and later, and only when `--verify CMD` was given. One per
execution of the verification command, written on the completion path before
the export runs.

| Field | v1 | v2 | Type | Notes |
|---|---|---|---|---|
| `round` | | ✓ | integer | 1-based; at most `--verify-rounds` + 1 of them |
| `exit_code` | | ✓ | integer \| null | the integer after `exit code: ` in the bash result; `null` for an `ERROR:`/`BLOCKED:` result that never produced a status |
| `passed` | | ✓ | boolean | true only for exit code 0 |

### `run_end`
```

The `run_end` table, after the `last_assistant_text` row Task 4 added.

Before:

```
| `last_assistant_text` | | ✓ | string \| null | 0.8: the model's last non-empty assistant text, capped at 2000 chars; `null` if there was none |

## Statuses
```

After:

```
| `last_assistant_text` | | ✓ | string \| null | 0.8: the model's last non-empty assistant text, capped at 2000 chars; `null` if there was none |
| `verify` | | ✓ | object \| null | 0.8: `{command, exit_code, output_tail, rounds, passed}` for the LAST `--verify` execution (`output_tail` capped at 4000 chars); `null` when `--verify` was not given |

## Statuses
```

The statuses table, after the `stuck` row Task 3 added.

Before:

```
| `stuck` | | ✓ | 0.8: the same **failing** bash command ran `--stuck-repeats` times in a row (fingerprint as the stall detector's: timings/shas stripped); edits in between do not reset the streak, a passing run does |
```

After:

```
| `stuck` | | ✓ | 0.8: the same **failing** bash command ran `--stuck-repeats` times in a row (fingerprint as the stall detector's: timings/shas stripped); edits in between do not reset the streak, a passing run does |
| `verify_failed` | | ✓ | 0.8: the worker declared itself done, but the `--verify` command exited non-zero on its last allowed round |
```

The stdout field list (as left by Task 4).

Before:

```
`files_changed_truncated`, `last_tool_result`, `last_assistant_text`, and
`export_status` on the exception-recovery path.
```

After:

```
`files_changed_truncated`, `last_tool_result`, `last_assistant_text`, `verify`,
and `export_status` on the exception-recovery path.
```

The `run.json` table, after the `last_assistant_text` row Task 4 added.

Before:

```
| `last_assistant_text` | end | 0.8: the last non-empty assistant text (≤2000 chars), or null |
```

After:

```
| `last_assistant_text` | end | 0.8: the last non-empty assistant text (≤2000 chars), or null |
| `verify` | end | 0.8: `{command, exit_code, output_tail, rounds, passed}` for the last `--verify` execution, or null. `dirtywork resume` reads `verify.command` back out of here to inherit the gate |
```

- [ ] **Step 20: Teach the doc test about the new event, status and field**

In `tests/test_transcript_schema.py` (as left by Tasks 3 and 4).

Before:

```python
EVENT_NAMES = ["run_start", "assistant", "tool_result", "guardrail_block", "nudge",
               "sandbox_reset", "run_end"]
```

After:

```python
EVENT_NAMES = ["run_start", "assistant", "tool_result", "guardrail_block", "nudge",
               "sandbox_reset", "verify", "run_end"]
```

Before:

```python
STATUSES = ["completed", "max_turns", "timeout", "context_exhausted", "model_error",
            "interrupted", "stalled", "stuck", "budget_exceeded", "sandbox_error",
            "export_failed"]
```

After:

```python
STATUSES = ["completed", "max_turns", "timeout", "context_exhausted", "model_error",
            "interrupted", "stalled", "stuck", "verify_failed", "budget_exceeded",
            "sandbox_error", "export_failed"]
```

Before:

```python
                  "finalize_error", "stuck_on", "files_changed",
                  "files_changed_truncated", "last_tool_result", "last_assistant_text"]
```

After:

```python
                  "finalize_error", "stuck_on", "files_changed",
                  "files_changed_truncated", "last_tool_result", "last_assistant_text",
                  "verify"]
```

- [ ] **Step 21: Run the full suite**

Run: `python3 -m pytest -q`
Expected: `875 passed, 1 skipped` (869 after Task 4 + 6 running + 1 skipped until Task 9), 18 deselected.

- [ ] **Step 22: Commit**

```bash
git add dirtywork/runner.py dirtywork/__main__.py dirtywork/runs.py tests/test_runner.py tests/test_main.py tests/test_runs.py tests/test_transcript_schema.py README.md docs/transcript-schema.md
git commit -m "feat: --verify runs the operator's gate in the sandbox before the run ends"
```

---

### Task 6: image dependencies + the `:0.8` tag (spec §5)

**Files:**
- Modify: `docker/Dockerfile` (`:4-17` apt line)
- Modify: `dirtywork/sandbox/docker_args.py` (`:8-25` `DEFAULT_IMAGE` + `PINNED_DIGEST` comment)
- Modify: `.github/workflows/ci.yml` (`:58` docker-live build tag)
- Modify: `docker/README.md` (`:4` package list; `:17`, `:21`, `:22`, `:23`, `:24`, `:67`, `:80`, `:87`, `:88`, `:103`, `:104` tag mentions; `:77` and `:81` release-list prose; a new "Derived images" section)
- Modify: `README.md` (`:513` `--image` default; a callout under *Review a run*; the derived-image recipe next to `--image`)
- Modify: `tests/test_docker_args.py` (`:21-26`)
- Modify: `tests/test_docker_image.py` (1 new unmarked test)

**Interfaces:**
- Consumes: `docker_args.DEFAULT_IMAGE`, `docker_args.PINNED_DIGEST`, `docker_args.pin_for` (unchanged behaviour — `PINNED_DIGEST` stays `None`, so `resolve_image()` performs no pin check for 0.8.0).
- Produces: `DEFAULT_IMAGE == "ghcr.io/jimboschneider/dirtywork-worker:0.8"`; the stock image ships `jq`, `uuid-runtime`, `shellcheck` and `curl` in addition to today's set.

Every `dirtywork-worker:0.7` occurrence on this branch (from `grep -rn 'dirtywork-worker:0\.7'`, excluding `docs/superpowers/`):

| File | Line | Context |
|---|---|---|
| `dirtywork/sandbox/docker_args.py` | 8 | `DEFAULT_IMAGE = …` |
| `dirtywork/sandbox/docker_args.py` | 14 | in the `PINNED_DIGEST` comment (`docker pull …`) |
| `tests/test_docker_args.py` | 22 | the `DEFAULT_IMAGE` assertion |
| `.github/workflows/ci.yml` | 58 | `tags:` for the docker-live build |
| `README.md` | 513 | `[--image ghcr.io/…:0.7]` in the flag block |
| `docker/README.md` | 17 | Build |
| `docker/README.md` | 21–24 | Verify locally (4 lines) |
| `docker/README.md` | 67 | the locally-built-image paragraph |
| `docker/README.md` | 80 | "trusts whatever `docker image inspect` … reports for" |
| `docker/README.md` | 87–88 | Resolve the published digest (2 lines) |
| `docker/README.md` | 103–104 | Manual build and push (2 lines) |

`dirtywork/sandbox/docker_args.py:9,21` and `tests/test_docker_args.py:23,25` also say `:0.7` without the image name; both blocks are rewritten wholesale below.

- [ ] **Step 1: Write the failing image test**

Append to `tests/test_docker_image.py`:

```python
def test_dockerfile_installs_the_packages_the_docs_promise():
    """Unmarked (no daemon needed): the Dockerfile is read as text. The four
    0.8 additions come from a real run whose bash suite needed them (issue
    #30); docker/README.md's package list must stay in step with this."""
    text = (DOCKER_DIR / "Dockerfile").read_text(encoding="utf-8")
    for package in ("git", "bash", "coreutils", "findutils", "python3", "nodejs",
                    "npm", "ripgrep", "jq", "uuid-runtime", "shellcheck", "curl"):
        assert f"\n        {package} \\\n" in text, f"{package} is not in the apt list"
    readme = (DOCKER_DIR / "README.md").read_text(encoding="utf-8")
    for package in ("jq", "uuid-runtime", "shellcheck", "curl"):
        assert package in readme, f"{package} is not documented in docker/README.md"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_docker_image.py -q`
Expected: 1 failed — `AssertionError: jq is not in the apt list`.

- [ ] **Step 3: Add the four packages**

In `docker/Dockerfile`.

Before:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        bash \
        coreutils \
        findutils \
        python3 \
        python3-venv \
        nodejs \
        npm \
        ripgrep \
        ca-certificates \
        wget \
        gnupg \
    && rm -rf /var/lib/apt/lists/*
```

After:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        bash \
        coreutils \
        findutils \
        python3 \
        python3-venv \
        nodejs \
        npm \
        ripgrep \
        jq \
        uuid-runtime \
        shellcheck \
        curl \
        ca-certificates \
        wget \
        gnupg \
    && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 4: Move the default image to `:0.8`**

In `dirtywork/sandbox/docker_args.py`, replace the whole `DEFAULT_IMAGE`/`PINNED_DIGEST` block.

Before:

```python
DEFAULT_IMAGE = "ghcr.io/jimboschneider/dirtywork-worker:0.7"
# Unset for 0.7.0: the :0.7 image is first published by the v0.7.0 release
# itself (publish-image.yml runs on x.y.0 tags; the Dockerfile is unchanged
# from 0.6 but the tag tracks the minor), so there is no prior publish to pin
# against -- resolve_image() performs no pin check and trusts whatever
# `docker image inspect` reports for the tag. The next patch (0.7.1) pins the
# multi-arch index digest: `docker pull ghcr.io/jimboschneider/dirtywork-worker:0.7`
# then `docker image inspect --format '{{json .RepoDigests}}' ...` (or
# `docker buildx imagetools inspect ...`), cross-checked against the
# publish-image.yml job summary; docker/README.md documents the procedure.
# This only pins a REGISTRY digest -- resolve_image() enforces it against a
# *pulled* DEFAULT_IMAGE only; a locally built/loaded image warns instead of
# refusing, and a user-supplied --image is never checked. MUST be re-resolved
# whenever the :0.7 tag is re-pushed. (0.6.x pinned :0.6 at
# sha256:1f7b98898001b7064d8db396a8a5a1a324df4ce48692597fcd4381ea90e4354a;
# 0.5.x pinned :0.5 at
# sha256:3b8d019a2f20a9df55a72ed51139076f02f2feb597243a69519bc41db1029648.)
PINNED_DIGEST: str | None = None
```

After:

```python
DEFAULT_IMAGE = "ghcr.io/jimboschneider/dirtywork-worker:0.8"
# Unset for 0.8.0: the :0.8 image is first published by the v0.8.0 release
# itself (publish-image.yml runs on x.y.0 tags; 0.8's Dockerfile adds jq,
# uuid-runtime, shellcheck and curl, so this really is a new build), so there
# is no prior publish to pin against -- resolve_image() performs no pin check
# and trusts whatever `docker image inspect` reports for the tag. The next
# patch (0.8.1) pins the multi-arch index digest:
# `docker pull ghcr.io/jimboschneider/dirtywork-worker:0.8`
# then `docker image inspect --format '{{json .RepoDigests}}' ...` (or
# `docker buildx imagetools inspect ...`), cross-checked against the
# publish-image.yml job summary; docker/README.md documents the procedure.
# This only pins a REGISTRY digest -- resolve_image() enforces it against a
# *pulled* DEFAULT_IMAGE only; a locally built/loaded image warns instead of
# refusing, and a user-supplied --image is never checked. MUST be re-resolved
# whenever the :0.8 tag is re-pushed. (0.7.x shipped unpinned; 0.6.x pinned
# :0.6 at
# sha256:1f7b98898001b7064d8db396a8a5a1a324df4ce48692597fcd4381ea90e4354a;
# 0.5.x pinned :0.5 at
# sha256:3b8d019a2f20a9df55a72ed51139076f02f2feb597243a69519bc41db1029648.)
PINNED_DIGEST: str | None = None
```

- [ ] **Step 5: Update the pinned-tag test**

In `tests/test_docker_args.py`.

Before:

```python
def test_default_image_and_pinned_digest():
    assert DEFAULT_IMAGE == "ghcr.io/jimboschneider/dirtywork-worker:0.7"
    # Unset for 0.7.0: the :0.7 image is first published by the v0.7.0
    # release itself, so the pin follows in 0.7.1 (docker/README.md
    # documents how to resolve and re-pin it whenever :0.7 is re-pushed).
    assert PINNED_DIGEST is None
```

After:

```python
def test_default_image_and_pinned_digest():
    assert DEFAULT_IMAGE == "ghcr.io/jimboschneider/dirtywork-worker:0.8"
    # Unset for 0.8.0: the :0.8 image is first published by the v0.8.0
    # release itself, so the pin follows in 0.8.1 (docker/README.md
    # documents how to resolve and re-pin it whenever :0.8 is re-pushed).
    assert PINNED_DIGEST is None
```

- [ ] **Step 6: Retag every remaining `:0.7` mention**

Run, from the repo root:

```bash
python3 - <<'PY'
from pathlib import Path
for name in ("docker/README.md", "README.md", ".github/workflows/ci.yml"):
    p = Path(name)
    p.write_text(p.read_text(encoding="utf-8").replace(
        "dirtywork-worker:0.7", "dirtywork-worker:0.8"), encoding="utf-8")
PY
```

Then verify nothing outside `docs/superpowers/` still says `:0.7`:

```bash
grep -rn 'dirtywork-worker:0\.7' --include='*.py' --include='*.md' --include='*.yml' . | grep -v docs/superpowers
```
Expected: no output.

- [ ] **Step 7: Update `docker/README.md`'s package list and release prose**

Before:

```
Built from `docker/Dockerfile`: Debian bookworm-slim, `USER worker` (uid
1000), git, bash, coreutils, findutils, python3, node, .NET SDK, ripgrep.
```

After:

```
Built from `docker/Dockerfile`: Debian bookworm-slim, `USER worker` (uid
1000), git, bash, coreutils, findutils, python3, node, .NET SDK, ripgrep,
and (since 0.8) jq, uuid-runtime, shellcheck and curl.
```

Before:

```
The first release of a minor (0.4.0, 0.5.0, 0.6.0, 0.7.0) ships with `PINNED_DIGEST = None`: there is no prior publish to pin
```

After:

```
The first release of a minor (0.4.0, 0.5.0, 0.6.0, 0.7.0, 0.8.0) ships with `PINNED_DIGEST = None`: there is no prior publish to pin
```

Before:

```
(0.4.1 for 0.4; 0.5.1 for 0.5; 0.6.1 for 0.6; 0.7.1 for 0.7) pins — once `publish-image.yml` has run, take the
```

After:

```
(0.4.1 for 0.4; 0.5.1 for 0.5; 0.6.1 for 0.6; 0.7.1 for 0.7; 0.8.1 for 0.8) pins — once `publish-image.yml` has run, take the
```

- [ ] **Step 8: Add the derived-image recipe to `docker/README.md`**

Insert a new section immediately **before** `## Updating the image`. (The blocks below are fenced with FOUR backticks because their content contains a three-backtick fence; transcribe the inner three-backtick fence literally.)

Before:

````
then resolve/pin the digest as above.

## Updating the image
````

After:

````
then resolve/pin the digest as above.

## Derived images (extra packages)

The worker cannot install anything at run time: docker mode runs with
`--network none` and mounts no host directories, so `apt-get`/`npm i -g`
inside a run will always fail. If your gate needs a tool this image does not
ship, build a derived image once and point `--image` at it:

```Dockerfile
FROM ghcr.io/jimboschneider/dirtywork-worker:0.8
USER root
RUN apt-get update && apt-get install -y --no-install-recommends <packages> \
    && rm -rf /var/lib/apt/lists/*
USER worker
```

    docker build -t my-worker:0.8 .
    dirtywork run --repo ~/repos/thing --image my-worker:0.8 "..."

Keep `USER worker` as the last instruction and add no `ENTRYPOINT`/`CMD` —
dirtywork always passes its own `--entrypoint` and absolute binary paths, and
the uid must stay 1000 so the run's volume ownership matches. A custom
`--image` is **never** checked against `PINNED_DIGEST`; that pin protects the
maintained default image only, so a derived image's provenance is yours to
manage.

## Updating the image
````

- [ ] **Step 9: Add the README callout under *Review a run***

In `README.md`, under "## Use".

Before:

```
- **Review a run:** `git -C <worktree> diff`, read the transcript, run the
  repo's tests — then commit the branch or discard it. (The worktree is
  only populated after the run ends, once the export step completes.)
```

After:

```
- **Review a run:** `git -C <worktree> diff`, read the transcript, run the
  repo's tests — then commit the branch or discard it. (The worktree is
  only populated after the run ends, once the export step completes.)

  > **The worker cannot install dependencies in docker mode** (`--network
  > none`, no host directories mounted); it can only run what the image
  > ships — git, bash, coreutils, findutils, python3, node/npm, the .NET SDK,
  > ripgrep, jq, uuid-runtime, shellcheck and curl. Always run the repo's own
  > gate yourself on the exported worktree, or pass it as
  > [`--verify`](#verifying-a-run). For a Node repo whose gate needs
  > `node_modules`, symlink your own into the exported worktree for the gate
  > and remove it afterwards — a `node_modules/` gitignore pattern does **not**
  > match a symlink, so a forgotten one shows up as an untracked path. If the
  > gate needs a tool the image lacks, build a derived image (see the recipe
  > next to `--image` in [Machine contract](#machine-contract)).
```

- [ ] **Step 10: Add the derived-image recipe to the README Machine contract**

In `README.md`, immediately after the `dirtywork resume` flag block and before the `--stall-turns` bullet. (Four-backtick fences again; the inner three-backtick fences are literal README content.)

Before:

````
dirtywork resume <slug | run-dir>     # same flags as run, minus --repo/--branch-from/--sandbox/<task>;
    [--model <m>]                     # defaults to the earlier run's model; --image defaults to its image
```

- `--stall-turns N` (default 12) — end the run with status `stalled` after N
````

After:

````
dirtywork resume <slug | run-dir>     # same flags as run, minus --repo/--branch-from/--sandbox/<task>;
    [--model <m>]                     # defaults to the earlier run's model; --image defaults to its image
```

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

- `--stall-turns N` (default 12) — end the run with status `stalled` after N
````

The **before** block's first line is the last line inside the README's existing `dirtywork resume` fence and its third line is that fence's closing ` ``` `; matching from there makes the anchor unambiguous.

- [ ] **Step 11: Run the full suite**

Run: `python3 -m pytest -q`
Expected: `876 passed, 1 skipped` (875 after Task 5 + 1), 18 deselected.

- [ ] **Step 12: Commit**

```bash
git add docker/Dockerfile docker/README.md dirtywork/sandbox/docker_args.py .github/workflows/ci.yml tests/test_docker_args.py tests/test_docker_image.py README.md
git commit -m "feat: worker image ships jq/uuid-runtime/shellcheck/curl; tag :0.8"
```

---

### Task 7: `workspace.snapshot_worktree` + `dirtywork runs snapshot <slug>` (spec §6.1)

**Files:**
- Modify: `dirtywork/workspace.py` (`:3-9` imports, `:21-24` `_git`; new `SNAPSHOT_AUTHOR`, `_walk_worktree`, `snapshot_worktree`)
- Modify: `dirtywork/runs.py` (`:24-26` imports; new `cmd_snapshot`; `dispatch` at `:676-693`)
- Modify: `dirtywork/__main__.py` (`_add_runs_parsers` at `:694-729`)
- Modify: `tests/test_workspace.py`, `tests/test_runs.py`, `tests/test_main.py`
- Modify: `README.md` (the `runs` subcommand list)
- Modify: `docs/transcript-schema.md` (a sentence on `runs snapshot` under `run.json`)

**Interfaces:**
- Consumes: `workspace.git_env()`, `workspace.GIT_NEUTRAL_FLAGS` (Task 4), `workspace.host_read_tree` (`workspace.py:269`), `workspace.WorkspaceError`; `resume.worktree_belongs_to_repo`, `resume.find_stashes`, `resume.pid_alive` (all already imported in `runs.py:25`); `runs._open_run`, `runs.RunsError`; `sandbox.export.worktree_is_pristine` (`export.py:65`, already imported as `export` in `runs.py:26`).
- Produces:
  - `workspace.SNAPSHOT_AUTHOR = ("dirtywork", "dirtywork@localhost")`
  - `workspace._walk_worktree(worktree: Path) -> tuple` → `(files: list[(rel, is_exec)], links: list[(rel, target)], skipped: int)`
  - `workspace.snapshot_worktree(worktree: Path, branch: str, message: str) -> str | None`
  - `runs.cmd_snapshot(args) -> int`; CLI `dirtywork runs snapshot <slug>`
- **Task 8 calls `snapshot_worktree` with exactly this signature.**

Two facts this task depends on, both verified on this machine (git 2.48.1) before the plan was written:

1. `git hash-object -w --no-filters --stdin-paths` stores the file's **raw** bytes even with `* filter=x` in `.gitattributes` and `filter.x.clean` configured in the repo; dropping `--no-filters` runs the filter. `GIT_CONFIG_GLOBAL=/dev/null` alone does **not** help — the filter here is repo-local config — which is why `--no-filters` is load-bearing, not decoration.
2. `update-index --index-info` → `write-tree` → `commit-tree` → `update-ref` builds and installs a commit without any porcelain, and a following `read-tree HEAD` leaves the worktree's index matching its new HEAD.

One deviation from the spec's letter, taken deliberately: the spec says the temporary index lives "in the run dir", but `snapshot_worktree(worktree, branch, message)` has no run dir in its signature (Task 8 calls it with no run dir at all). It uses a private `tempfile.TemporaryDirectory` instead — never inside the worktree, so it cannot pollute the snapshot, and cleaned up unconditionally.

- [ ] **Step 1: Write the failing snapshot tests**

Append to `tests/test_workspace.py`:

```python
def _snapshot_repo(tmp_path: Path):
    """A repo plus a linked worktree rigged so a snapshot built from porcelain
    would be caught: a clean filter on every path, a pre-commit hook that
    leaves a sentinel and fails, a symlink, an executable file, a deletion."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "keep.txt").write_text("keep\n")
    (repo / "gone.txt").write_text("gone\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-b", "dirtywork/snap", str(wt))
    _git(repo, "config", "filter.x.clean", "sed s/RAW/FILTERED/")
    (wt / ".gitattributes").write_text("* filter=x\n")
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text('#!/bin/sh\ntouch "$(git rev-parse --git-common-dir)/hook-ran"\nexit 1\n')
    hook.chmod(0o755)
    (wt / "raw.txt").write_text("RAW content\n")
    (wt / "script.sh").write_text("#!/bin/sh\necho hi\n")
    (wt / "script.sh").chmod(0o755)
    (wt / "link").symlink_to("/etc/passwd")
    (wt / "gone.txt").unlink()
    return repo, wt


def test_snapshot_worktree_commits_raw_content_without_filters_or_hooks(tmp_path: Path):
    from dirtywork.workspace import snapshot_worktree
    repo, wt = _snapshot_repo(tmp_path)
    sha = snapshot_worktree(wt, "dirtywork/snap", "wip: dirtywork run snap")
    assert sha is not None and len(sha) == 40

    entries = {}
    for line in _git(repo, "ls-tree", "-r", sha).splitlines():
        meta, _, name = line.partition("\t")
        mode, _kind, blob = meta.split()
        entries[name] = (mode, blob)
    assert set(entries) == {".gitattributes", "keep.txt", "raw.txt", "script.sh", "link"}
    assert "gone.txt" not in entries                 # a deletion is part of the snapshot
    assert entries["keep.txt"][0] == "100644"
    assert entries["script.sh"][0] == "100755"       # executable bit preserved
    assert entries["link"][0] == "120000"            # symlink, recorded not followed
    assert _git(repo, "cat-file", "-p", entries["link"][1]) == "/etc/passwd"
    # the clean filter never ran: the blob holds the raw bytes
    assert _git(repo, "cat-file", "-p", entries["raw.txt"][1]) == "RAW content\n"
    # the pre-commit hook never ran
    assert not (repo / ".git" / "hook-ran").exists()
    # the branch moved onto the new commit, authored by dirtywork
    assert _git(repo, "rev-parse", "dirtywork/snap").strip() == sha
    assert _git(repo, "log", "-1", "--format=%an <%ae>", sha).strip() == (
        "dirtywork <dirtywork@localhost>")
    assert _git(repo, "log", "-1", "--format=%s", sha).strip() == "wip: dirtywork run snap"


def test_snapshot_worktree_returns_none_when_the_tree_is_unchanged(tmp_path: Path):
    from dirtywork.workspace import snapshot_worktree
    repo, wt = _snapshot_repo(tmp_path)
    first = snapshot_worktree(wt, "dirtywork/snap", "wip: one")
    assert first is not None
    assert snapshot_worktree(wt, "dirtywork/snap", "wip: two") is None
    assert _git(repo, "rev-parse", "dirtywork/snap").strip() == first


def test_snapshot_worktree_refuses_a_path_containing_a_newline(tmp_path: Path):
    from dirtywork.workspace import snapshot_worktree
    repo, wt = _snapshot_repo(tmp_path)
    (wt / "we\nird.txt").write_text("x")
    with pytest.raises(WorkspaceError) as excinfo:
        snapshot_worktree(wt, "dirtywork/snap", "wip: newline")
    assert "newline" in str(excinfo.value)
```

`pytest` and `WorkspaceError` are already imported at the top of `tests/test_workspace.py`.

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_workspace.py -q -k snapshot`
Expected: 3 failed — `ImportError: cannot import name 'snapshot_worktree' from 'dirtywork.workspace'`.

- [ ] **Step 3: Let `_git` feed stdin**

In `dirtywork/workspace.py`, the imports.

Before:

```python
import os
import re
import secrets
import stat
import subprocess
from datetime import datetime
from pathlib import Path
```

After:

```python
import os
import re
import secrets
import stat
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
```

And the runner.

Before:

```python
def _git(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env
    )
```

After:

```python
def _git(repo: Path, *args: str, env: dict | None = None,
         stdin_text: str | None = None) -> subprocess.CompletedProcess:
    """`stdin_text` is how the snapshot plumbing feeds path lists and index
    lines to git (hash-object --stdin-paths, update-index --index-info);
    None keeps today's behaviour of inheriting stdin."""
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env,
        input=stdin_text
    )
```

- [ ] **Step 4: Add the worktree walk and the snapshot builder**

In `dirtywork/workspace.py`, append this block at the very **end** of the file (after `commit_exists`).

```python
SNAPSHOT_AUTHOR = ("dirtywork", "dirtywork@localhost")


def _walk_worktree(worktree: Path) -> tuple:
    """(files, links, skipped) for everything under `worktree`.

    `files` is [(repo-relative path, is_executable)], `links` is
    [(repo-relative path, link target string)], `skipped` counts entries that
    are neither (FIFOs, sockets, devices, unreadable entries). The TOP-LEVEL
    `.git` entry is skipped and NOTHING else is: ignore rules are deliberately
    not applied, because a wip snapshot is a snapshot. Symlinks — including
    symlinked directories — are recorded by their target string and never
    followed or descended into (os.lstat/os.readlink only)."""
    files, links, skipped = [], [], 0
    for root, dirnames, filenames in os.walk(worktree, followlinks=False):
        root_path = Path(root)
        rel_root = root_path.relative_to(worktree)
        if root_path == worktree:
            dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in list(dirnames):
            entry = root_path / name
            if os.path.islink(entry):
                dirnames.remove(name)
                try:
                    links.append(((rel_root / name).as_posix(), os.readlink(entry)))
                except OSError:
                    skipped += 1
        for name in filenames:
            if root_path == worktree and name == ".git":
                continue
            entry = root_path / name
            rel = (rel_root / name).as_posix()
            try:
                st = os.lstat(entry)
            except OSError:
                skipped += 1
                continue
            if stat.S_ISLNK(st.st_mode):
                try:
                    links.append((rel, os.readlink(entry)))
                except OSError:
                    skipped += 1
            elif stat.S_ISREG(st.st_mode):
                files.append((rel, bool(st.st_mode & 0o111)))
            else:
                skipped += 1
    files.sort()
    links.sort()
    return files, links, skipped


def snapshot_worktree(worktree: Path, branch: str, message: str):
    """Spec §6.1: commit the worktree's CURRENT content onto `branch` using
    nothing but git plumbing — no `git add`, no `git commit`, so no clean
    filter, no `.gitattributes` rule and no hook can ever execute on the host
    against content a worker wrote. Returns the new commit sha, or None when
    the resulting tree equals the branch head's tree (nothing to snapshot).
    Raises WorkspaceError.

    `hash-object -w --no-filters` is load-bearing: the filter that would
    otherwise run is configured REPO-locally, which GIT_CONFIG_GLOBAL=/dev/null
    does not disable (verified, git 2.48.1). Paths reach it over stdin, so a
    filename containing a newline is refused outright rather than mis-hashed."""
    worktree = Path(worktree)
    env = git_env()
    files, links, _skipped = _walk_worktree(worktree)
    for rel in [r for r, _ in files] + [r for r, _ in links]:
        if "\n" in rel:
            raise WorkspaceError(
                f"cannot snapshot {worktree}: path {rel!r} contains a newline, which "
                f"`git hash-object --stdin-paths` cannot address"
            )

    entries = []
    if files:
        paths = "".join(str(worktree / rel) + "\n" for rel, _ in files)
        res = _git(worktree, *GIT_NEUTRAL_FLAGS, "hash-object", "-w", "--no-filters",
                   "--stdin-paths", env=env, stdin_text=paths)
        if res.returncode != 0:
            raise WorkspaceError(
                f"git hash-object failed in {worktree}: {res.stderr.strip()}")
        shas = res.stdout.split()
        if len(shas) != len(files):
            raise WorkspaceError(
                f"git hash-object returned {len(shas)} hashes for {len(files)} files "
                f"in {worktree}")
        for (rel, is_exec), sha in zip(files, shas):
            entries.append(f"{'100755' if is_exec else '100644'} {sha}\t{rel}")
    for rel, target in links:
        res = _git(worktree, *GIT_NEUTRAL_FLAGS, "hash-object", "-w", "--stdin",
                   env=env, stdin_text=target)
        if res.returncode != 0:
            raise WorkspaceError(
                f"git hash-object failed for symlink {rel} in {worktree}: "
                f"{res.stderr.strip()}")
        entries.append(f"120000 {res.stdout.strip()}\t{rel}")

    head_res = _git(worktree, *GIT_NEUTRAL_FLAGS, "rev-parse", "--verify", "--quiet",
                    f"refs/heads/{branch}", env=env)
    if head_res.returncode != 0:
        raise WorkspaceError(f"branch {branch} does not exist in {worktree}")
    head = head_res.stdout.strip()

    with tempfile.TemporaryDirectory(prefix="dirtywork-snapshot-") as tmpdir:
        index_env = dict(env)
        index_env["GIT_INDEX_FILE"] = str(Path(tmpdir) / "index")
        res = _git(worktree, *GIT_NEUTRAL_FLAGS, "update-index", "--index-info",
                   env=index_env, stdin_text="".join(e + "\n" for e in entries))
        if res.returncode != 0:
            raise WorkspaceError(
                f"git update-index failed in {worktree}: {res.stderr.strip()}")
        res = _git(worktree, *GIT_NEUTRAL_FLAGS, "write-tree", env=index_env)
        if res.returncode != 0:
            raise WorkspaceError(
                f"git write-tree failed in {worktree}: {res.stderr.strip()}")
        tree = res.stdout.strip()

    head_tree = _git(worktree, *GIT_NEUTRAL_FLAGS, "rev-parse", f"{head}^{{tree}}", env=env)
    if head_tree.returncode == 0 and head_tree.stdout.strip() == tree:
        return None

    commit_env = dict(env)
    commit_env.update({
        "GIT_AUTHOR_NAME": SNAPSHOT_AUTHOR[0], "GIT_AUTHOR_EMAIL": SNAPSHOT_AUTHOR[1],
        "GIT_COMMITTER_NAME": SNAPSHOT_AUTHOR[0], "GIT_COMMITTER_EMAIL": SNAPSHOT_AUTHOR[1],
    })
    res = _git(worktree, *GIT_NEUTRAL_FLAGS, "commit-tree", tree, "-p", head, "-m", message,
               env=commit_env)
    if res.returncode != 0:
        raise WorkspaceError(f"git commit-tree failed in {worktree}: {res.stderr.strip()}")
    commit = res.stdout.strip()

    # The old-value argument makes this a compare-and-swap: if anything moved
    # the branch since `head` was read, the update fails instead of clobbering.
    res = _git(worktree, *GIT_NEUTRAL_FLAGS, "update-ref", f"refs/heads/{branch}",
               commit, head, env=env)
    if res.returncode != 0:
        raise WorkspaceError(f"git update-ref failed in {worktree}: {res.stderr.strip()}")
    # The already-sanctioned index-only refresh, so the worktree's index matches
    # its new HEAD and `git status` is clean rather than showing every file.
    host_read_tree(worktree)
    return commit
```

- [ ] **Step 5: Run the workspace tests**

Run: `python3 -m pytest tests/test_workspace.py -q`
Expected: `51 passed` (48 after Task 4 + 3).

- [ ] **Step 6: Write the failing `runs snapshot` tests**

Append to `tests/test_runs.py`:

```python
def _linked_worktree(repo: Path, slug: str) -> Path:
    wt = repo / ".worktrees" / f"dw-{slug}"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-b", f"dirtywork/{slug}", str(wt))
    return wt


def test_cmd_snapshot_commits_the_worktree_on_the_run_branch(
        tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = _linked_worktree(repo, "snap1")
    (wt / "new.txt").write_text("work in progress\n")
    _write_run(tmp_path / "runs", "snap1", {
        "slug": "snap1", "status": "max_turns", "repo": str(repo),
        "worktree": str(wt), "branch": "dirtywork/snap1", "host_pid": None,
    })
    rc = runs.cmd_snapshot(argparse.Namespace(slug="snap1"))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert out.startswith("snapshot ") and "on dirtywork/snap1" in out
    assert "new.txt" in _git(repo, "ls-tree", "-r", "--name-only", "dirtywork/snap1").stdout

    head = _git(repo, "rev-parse", "dirtywork/snap1").stdout.strip()
    assert runs.cmd_snapshot(argparse.Namespace(slug="snap1")) == 0
    assert "nothing to snapshot" in capsys.readouterr().out
    assert _git(repo, "rev-parse", "dirtywork/snap1").stdout.strip() == head


def test_cmd_snapshot_refuses_a_live_run_and_a_foreign_worktree(
        tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = _linked_worktree(repo, "snap2")
    (wt / "new.txt").write_text("x\n")
    _write_run(tmp_path / "runs", "snap2", {
        "slug": "snap2", "status": "running", "repo": str(repo),
        "worktree": str(wt), "branch": "dirtywork/snap2", "host_pid": os.getpid(),
    })
    assert runs.cmd_snapshot(argparse.Namespace(slug="snap2")) == 2
    assert "still running" in capsys.readouterr().err

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "f.txt").write_text("x\n")
    _write_run(tmp_path / "runs", "snap3", {
        "slug": "snap3", "status": "stalled", "repo": str(repo),
        "worktree": str(outside), "branch": "dirtywork/snap3", "host_pid": None,
    })
    assert runs.cmd_snapshot(argparse.Namespace(slug="snap3")) == 2
    assert "not a linked worktree" in capsys.readouterr().err


def test_cmd_snapshot_refuses_a_worktree_the_export_never_populated(
        tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = _linked_worktree(repo, "snap4")     # the repo fixture's HEAD is an empty commit
    _write_run(tmp_path / "runs", "snap4", {
        "slug": "snap4", "status": "sandbox_error", "repo": str(repo),
        "worktree": str(wt), "branch": "dirtywork/snap4", "host_pid": None,
    })
    assert runs.cmd_snapshot(argparse.Namespace(slug="snap4")) == 2
    assert "nothing to snapshot" in capsys.readouterr().err
```

`os` and `argparse` are already imported at the top of `tests/test_runs.py`.

- [ ] **Step 7: Run them to verify they fail**

Run: `python3 -m pytest tests/test_runs.py -q -k cmd_snapshot`
Expected: 3 failed — `AttributeError: module 'dirtywork.runs' has no attribute 'cmd_snapshot'`.

- [ ] **Step 8: Add the subcommand**

In `dirtywork/runs.py`, the imports.

Before:

```python
from . import rundir
from .resume import find_stashes, pid_alive, stash_dir_for, worktree_belongs_to_repo
from .sandbox import docker_args, docker_cli, export
```

After:

```python
from . import rundir
from .resume import find_stashes, pid_alive, stash_dir_for, worktree_belongs_to_repo
from .sandbox import docker_args, docker_cli, export
from .workspace import WorkspaceError, snapshot_worktree
```

Then insert `cmd_snapshot` immediately **after** `cmd_verdict` (which ends with `    return 0`) and immediately **before** `def dispatch(args) -> int:`.

```python
def cmd_snapshot(args) -> int:
    """Spec §6.1: commit the run worktree's current content onto the run's own
    branch, using plumbing that never runs a filter, a hook or an ignore rule.
    Plumbing-only by design — the review→fix loop needs a commit to branch from
    (`--branch-from @<slug>`) without asking the operator for a manual wip
    commit that their own git config would have filtered."""
    try:
        run_dir, data = _open_run(args.slug)
    except RunsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if data.get("status") == "running" and pid_alive(data.get("host_pid")):
        print(f"error: run '{args.slug}' is still running (pid {data.get('host_pid')}); "
              f"wait for it to finish before snapshotting", file=sys.stderr)
        return 2
    branch = data.get("branch") or ""
    if not branch:
        print(f"error: run.json for '{args.slug}' records no branch", file=sys.stderr)
        return 2
    worktree = Path(data.get("worktree", ""))
    if not worktree.is_dir():
        print(f"error: worktree {worktree} is missing; nothing to snapshot", file=sys.stderr)
        return 2
    if not worktree_belongs_to_repo(worktree, Path(data.get("repo", ""))):
        print(f"error: worktree {worktree} is not a linked worktree of "
              f"{data.get('repo')}; refusing to snapshot a directory dirtywork did "
              f"not create", file=sys.stderr)
        return 2
    stashes = find_stashes(worktree)
    if stashes:
        listing = ", ".join(str(p) for p in stashes)
        print(f"error: a pre-resume stash exists beside {worktree} ({listing}); the "
              f"worktree does not hold this run's content until you move it back or "
              f"delete it", file=sys.stderr)
        return 2
    try:
        pristine = export.worktree_is_pristine(worktree)
    except OSError as e:
        print(f"error: cannot read worktree {worktree}: {e}", file=sys.stderr)
        return 2
    if pristine:
        # Committing an empty tree here would record "delete everything" on the
        # run's branch. A docker run whose export never landed is the case.
        print(f"error: worktree {worktree} holds only its .git file; there is "
              f"nothing to snapshot (the export never populated it)", file=sys.stderr)
        return 2

    try:
        sha = snapshot_worktree(worktree, branch, f"wip: dirtywork run {args.slug}")
    except WorkspaceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"snapshot {sha} on {branch}" if sha else "nothing to snapshot")
    return 0
```

Then the dispatch table.

Before:

```python
    handlers = {
        "list": cmd_list,
        "show": cmd_show,
        "export": cmd_export,
        "clean": cmd_clean,
        "verdict": cmd_verdict,
    }
```

After:

```python
    handlers = {
        "list": cmd_list,
        "show": cmd_show,
        "export": cmd_export,
        "clean": cmd_clean,
        "verdict": cmd_verdict,
        "snapshot": cmd_snapshot,
    }
```

- [ ] **Step 9: Run the runs tests**

Run: `python3 -m pytest tests/test_runs.py -q`
Expected: `81 passed` (78 after Task 5 + 3).

- [ ] **Step 10: Write the failing parser test**

Append to `tests/test_main.py`:

```python
def test_runs_snapshot_dispatches(tmp_path, monkeypatch, capsys):
    import dirtywork.__main__ as m
    from dirtywork import rundir as rundir_mod
    monkeypatch.setattr(rundir_mod, "RUNS_DIR", tmp_path / "runs")
    rc = m.main(["runs", "snapshot", "no-such-run"])
    assert rc == 2
    assert "no such run" in capsys.readouterr().err
```

- [ ] **Step 11: Run it to verify it fails**

Run: `python3 -m pytest tests/test_main.py -q -k runs_snapshot`
Expected: 1 failed — `SystemExit: 2` from argparse (`invalid choice: 'snapshot'`).

- [ ] **Step 12: Add the parser**

In `dirtywork/__main__.py`, `_add_runs_parsers`.

Before:

```python
    verdict_p = runs_sub.add_parser("verdict", help="record accept/reject/cleanup for a run")
    verdict_p.add_argument("slug")
    verdict_p.add_argument("verdict", choices=["accept", "reject", "cleanup"])
    verdict_p.add_argument("--note", default=None)
    verdict_p.add_argument("--review-seconds", type=float, default=None)
```

After:

```python
    verdict_p = runs_sub.add_parser("verdict", help="record accept/reject/cleanup for a run")
    verdict_p.add_argument("slug")
    verdict_p.add_argument("verdict", choices=["accept", "reject", "cleanup"])
    verdict_p.add_argument("--note", default=None)
    verdict_p.add_argument("--review-seconds", type=float, default=None)

    snapshot_p = runs_sub.add_parser(
        "snapshot", help="commit the run worktree's current content onto its branch")
    snapshot_p.add_argument("slug")
```

- [ ] **Step 13: Run the CLI tests**

Run: `python3 -m pytest tests/test_main.py -q`
Expected: `57 passed, 1 skipped` (56 after Task 5 + 1).

- [ ] **Step 14: Document `runs snapshot`**

In `README.md`, the `runs` subcommand list, after the `runs verdict` bullet.

Before:

```
- `dirtywork runs verdict <slug> accept|reject|cleanup [--note TEXT] [--review-seconds N]` —
  record the operator's verdict on a run into its `run.json`.
```

After:

```
- `dirtywork runs verdict <slug> accept|reject|cleanup [--note TEXT] [--review-seconds N]` —
  record the operator's verdict on a run into its `run.json`.
- `dirtywork runs snapshot <slug>` — commit the run worktree's current content
  onto the run's own branch as `wip: dirtywork run <slug>`, then print
  `snapshot <sha> on <branch>` (or `nothing to snapshot` when the tree already
  matches the branch head). Built entirely from git plumbing — no `git add`, no
  `git commit` — so a worker-authored `.gitattributes` plus a configured clean
  filter, and any hook in your repo, are bypassed rather than executed; ignore
  rules are deliberately not applied either, because a wip snapshot is a
  snapshot. Symlinks are recorded by their target string, never followed;
  executable bits are preserved; anything that is not a regular file or a
  symlink is skipped. Refuses (exit 2) a run still going with a live pid, a
  missing worktree, a worktree that is not a linked worktree of the run's repo,
  a worktree with a pre-resume stash beside it, and one the export never
  populated. Mostly you will not call it by hand: `--branch-from @<slug>` calls
  it for you.
```

- [ ] **Step 15: Document it in `docs/transcript-schema.md`**

The closing paragraph.

Before:

```
Rows marked *verdict* are added post hoc by `dirtywork runs verdict <slug> …`
(a merge-update; no existing key is dropped) and are absent until then.
```

After:

```
Rows marked *verdict* are added post hoc by `dirtywork runs verdict <slug> …`
(a merge-update; no existing key is dropped) and are absent until then.

`dirtywork runs snapshot <slug>` writes no `run.json` field: it reads `branch`,
`worktree`, `repo`, `status` and `host_pid` and commits the worktree's current
content onto that branch with git plumbing only. The run directory is unchanged.
```

- [ ] **Step 16: Run the full suite**

Run: `python3 -m pytest -q`
Expected: `883 passed, 1 skipped` (876 after Task 6 + 7), 18 deselected.

- [ ] **Step 17: Commit**

```bash
git add dirtywork/workspace.py dirtywork/runs.py dirtywork/__main__.py tests/test_workspace.py tests/test_runs.py tests/test_main.py README.md docs/transcript-schema.md
git commit -m "feat: 'runs snapshot <slug>' commits a worktree with plumbing only"
```

---

### Task 8: `--branch-from @<slug>` (spec §6.2)

**Files:**
- Modify: `dirtywork/workspace.py` (new `host_worktree_dirty`)
- Modify: `dirtywork/__main__.py` (`:37-47` workspace import; `RunContext` at `:93-111`; new `_resolve_branch_from`; `_workspace_new` at `:180-205`; `_write_run_json_start` at `:321-345`; `--branch-from` help at `:765`)
- Modify: `tests/test_workspace.py`, `tests/test_main.py`
- Modify: `README.md` (`--branch-from` in the flag block + a prose bullet; the *Resuming a run* neighbourhood)
- Modify: `docs/transcript-schema.md` (`run_start.branch_from` note, `run.json.branch_from_run`)

**Interfaces:**
- Consumes: `workspace.snapshot_worktree(worktree, branch, message) -> str | None` (Task 7); `workspace.git_env`, `workspace.GIT_NEUTRAL_FLAGS` (Task 4); `resume.resolve_run_dir(ref, runs_dir) -> Path` (`resume.py:60`); `resume.load_prior_run(run_dir) -> dict` (`resume.py:93`); `resume.ResumeError`; `__main__.PreflightFailure`, `__main__.RUNS_DIR`.
- Produces:
  - `workspace.host_worktree_dirty(worktree: Path) -> bool` — fail-closed (a git failure reads as dirty)
  - `__main__._resolve_branch_from(args) -> tuple` → `(branch_from: str | None, branch_from_run: str | None)`
  - `RunContext.branch_from_run: str | None = None`
  - `run.json` key `branch_from_run`; `run_start.branch_from` now carries the resolved branch NAME for an `@slug` value

- [ ] **Step 1: Write the failing dirty-check test**

Append to `tests/test_workspace.py`:

```python
def test_host_worktree_dirty_sees_untracked_and_modified_and_fails_closed(tmp_path: Path):
    from dirtywork.workspace import host_worktree_dirty
    repo, wt = _snapshot_repo(tmp_path)
    assert host_worktree_dirty(wt) is True         # the fixture leaves real changes
    from dirtywork.workspace import snapshot_worktree
    snapshot_worktree(wt, "dirtywork/snap", "wip: clean it")
    assert host_worktree_dirty(wt) is False        # the snapshot made it clean
    assert host_worktree_dirty(tmp_path / "not-a-repo") is True   # fail closed
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_workspace.py -q -k worktree_dirty`
Expected: 1 failed — `ImportError: cannot import name 'host_worktree_dirty' from 'dirtywork.workspace'`.

- [ ] **Step 3: Add the dirty check**

In `dirtywork/workspace.py`, insert immediately **after** `host_files_changed` (Task 4) and immediately **before** `def load_repo_context(repo: Path, base_commit: str) -> str | None:`.

```python
def host_worktree_dirty(worktree) -> bool:
    """True when `git status --porcelain` reports anything, or cannot be run at
    all (fail closed: an unanswerable worktree is treated as having work worth
    snapshotting). Config-neutral, like every host git command that looks at
    worker content — the operator's own filters must not run here. This is the
    ONE dirty check in the codebase: `runs._worktree_is_dirty` delegates here."""
    try:
        res = _git(Path(worktree), *GIT_NEUTRAL_FLAGS, "status", "--porcelain",
                   "--untracked-files=normal", env=git_env())
    except (OSError, subprocess.SubprocessError):
        return True
    return res.returncode != 0 or bool(res.stdout.strip())
```

- [ ] **Step 3b: Make `runs._worktree_is_dirty` delegate (DRY — one dirty check)**

`dirtywork/runs.py:771-778` already has a `git status --porcelain` dirty check used by `runs clean`. Two copies of "is this worktree dirty" would drift; keep the name `runs clean` calls, but make it a one-line delegate.

Before (`dirtywork/runs.py:771-778`):

```python
def _worktree_is_dirty(worktree: str) -> bool:
    """Fail closed: if git cannot be asked, treat the worktree as dirty."""
    try:
        cp = subprocess.run(["git", "-C", str(worktree), "status", "--porcelain"],
                            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return True
    return cp.returncode != 0 or bool(cp.stdout.strip())
```

After:

```python
def _worktree_is_dirty(worktree: str) -> bool:
    """Fail closed: if git cannot be asked, treat the worktree as dirty.
    Delegates to the one config-neutral dirty check in workspace.py."""
    return host_worktree_dirty(worktree)
```

and add the import at the top of `dirtywork/runs.py`, immediately after the existing line `from .sandbox import docker_args, docker_cli, export`:

```python
from .workspace import host_worktree_dirty
```

(`dirtywork/workspace.py` does not import `runs`, so this introduces no cycle. If `subprocess` is now unused in `runs.py`, leave the import — other functions in the module still use it; check with `grep -n "subprocess\." dirtywork/runs.py` and only remove the import if nothing else uses it.)

Run: `python3 -m pytest tests/test_runs.py -q`
Expected: all pass (the `runs clean` dirty-worktree tests still pass through the delegate).

- [ ] **Step 4: Run the workspace tests**

Run: `python3 -m pytest tests/test_workspace.py -q`
Expected: `52 passed` (51 after Task 7 + 1).

- [ ] **Step 5: Write the failing CLI tests**

Append to `tests/test_main.py`:

```python
def test_branch_from_run_reference_snapshots_and_branches_from_the_prior_run(
        tmp_path, monkeypatch, capsys):
    write_then_loop = [
        {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "w1", "type": "function", "function": {"name": "write_file",
             "arguments": json.dumps({"path": "first.txt", "content": "from run 1\n"})}}]}}],
         "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
    ]
    m, repo, rc = _first_run(monkeypatch, tmp_path, write_then_loop)
    first = json.loads(capsys.readouterr().out)
    assert rc == 1 and first["status"] == "max_turns"
    slug = Path(first["run_dir"]).name

    patch_provider(monkeypatch, m, lambda base_url=None: _ScriptedClient(base_url))
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none",
                 "--branch-from", f"@{slug}", "keep going"])
    err = capsys.readouterr()
    second = json.loads(err.out)
    assert rc == 0, second
    assert "snapshot " in err.err and f"(from @{slug})" in err.err

    data = json.loads((Path(second["run_dir"]) / "run.json").read_text())
    assert data["branch_from_run"] == slug
    # the new worktree starts from the SNAPSHOT of the earlier run's work
    assert (Path(second["worktree"]) / "first.txt").read_text() == "from run 1\n"
    start = next(e for e in (json.loads(l) for l in
                             Path(second["transcript"]).read_text().splitlines())
                 if e["event"] == "run_start")
    assert start["branch_from"] == first["branch"]      # the resolved branch NAME


def test_branch_from_unknown_run_exits_2_and_creates_nothing(tmp_path, monkeypatch, capsys):
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none",
                 "--branch-from", "@no-such-run", "t"])
    assert rc == 2
    assert "unknown run 'no-such-run'" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()
    assert not (repo / ".worktrees").exists()


def test_branch_from_a_clean_run_takes_no_snapshot(tmp_path, monkeypatch, capsys):
    m, repo, rc = _first_run(monkeypatch, tmp_path, None)
    first = json.loads(capsys.readouterr().out)
    slug = Path(first["run_dir"]).name
    patch_provider(monkeypatch, m, lambda base_url=None: _ScriptedClient(base_url))
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none",
                 "--branch-from", f"@{slug}", "keep going"])
    err = capsys.readouterr()
    assert rc == 0, err.out
    assert "snapshot " not in err.err                  # nothing was dirty
    assert json.loads((Path(json.loads(err.out)["run_dir"]) / "run.json")
                      .read_text())["branch_from_run"] == slug


def test_branch_from_a_plain_ref_is_unchanged(tmp_path, monkeypatch, capsys):
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    assert m.main(["run", "--repo", str(repo), "--sandbox", "none",
                   "--branch-from", "HEAD", "t"]) == 0
    payload = json.loads(capsys.readouterr().out)
    data = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert data["branch_from_run"] is None
```

- [ ] **Step 6: Run them to verify they fail**

Run: `python3 -m pytest tests/test_main.py -q -k branch_from`
Expected: 4 failed — three with `KeyError: 'branch_from_run'`, and `test_branch_from_unknown_run_exits_2_and_creates_nothing` with `WorkspaceError: git worktree add failed: fatal: invalid reference: @no-such-run` reported as exit 2 but with the wrong message (`"unknown run 'no-such-run'" in ...` fails).

- [ ] **Step 7: Import the two workspace helpers**

In `dirtywork/__main__.py`.

Before:

```python
from .workspace import (
    WorkspaceError,
    commit_exists,
    create_worktree,
    ensure_worktrees_excluded,
    load_repo_context,
    make_slug,
    preflight_repo,
    remove_worktree,
    worktree_base_commit,
)
```

After:

```python
from .workspace import (
    WorkspaceError,
    commit_exists,
    create_worktree,
    ensure_worktrees_excluded,
    host_worktree_dirty,
    load_repo_context,
    make_slug,
    preflight_repo,
    remove_worktree,
    snapshot_worktree,
    worktree_base_commit,
)
```

- [ ] **Step 8: Record the source run on the RunContext**

In `dirtywork/__main__.py`.

Before:

```python
    context_window: int
    branch_from: str | None = None
    resumed_from: str | None = None
```

After:

```python
    context_window: int
    branch_from: str | None = None
    branch_from_run: str | None = None   # the @<slug> --branch-from named, if any
    resumed_from: str | None = None
```

- [ ] **Step 9: Resolve `@<slug>` in preflight**

In `dirtywork/__main__.py`, insert `_resolve_branch_from` immediately **before** `def _workspace_new(args, repo: Path, context_window: int) -> RunContext:`.

```python
def _resolve_branch_from(args) -> tuple:
    """Spec §6.2: `--branch-from @<slug>` means 'the branch that run left
    behind'. Returns (branch_from, branch_from_run). Anything not starting with
    '@' passes through untouched, so an ordinary ref is unaffected.

    A dirty worktree is snapshotted FIRST, because the branch head alone does
    not carry the work the reviewer just read — that snapshot is the whole
    point of the flag, and it is the one thing this preflight creates before a
    later failure could still exit 2."""
    value = getattr(args, "branch_from", None)
    if not isinstance(value, str) or not value.startswith("@"):
        return value, None
    slug = value[1:]
    run_dir = resolve_run_dir(slug, RUNS_DIR)
    if not run_dir.is_dir():
        raise PreflightFailure(f"unknown run '{slug}' (no run dir under {RUNS_DIR})")
    try:
        prior = load_prior_run(run_dir)
    except ResumeError as e:
        raise PreflightFailure(str(e))
    branch = prior.get("branch")
    if not isinstance(branch, str) or not branch:
        raise PreflightFailure(f"run '{slug}' records no branch to branch from")
    worktree = Path(prior.get("worktree") or "")
    if str(worktree) and worktree.is_dir() and host_worktree_dirty(worktree):
        try:
            sha = snapshot_worktree(worktree, branch, f"wip: dirtywork run {slug}")
        except WorkspaceError as e:
            raise PreflightFailure(str(e))
        if sha:
            print(f"snapshot {sha} on {branch} (from @{slug})", file=sys.stderr)
    return branch, slug
```

Then wire it into `_workspace_new`.

Before:

```python
def _workspace_new(args, repo: Path, context_window: int) -> RunContext:
    image_ref, image_digest, image_pinned = None, None, False
    if args.sandbox == "docker":
        image_ref, image_digest, image_pinned = _docker_preflight_or_fail(repo, args.image)
```

After:

```python
def _workspace_new(args, repo: Path, context_window: int) -> RunContext:
    # First: it is the cheapest refusal in this function, and its snapshot must
    # happen before the run creates a worktree it might have to roll back.
    branch_from, branch_from_run = _resolve_branch_from(args)
    image_ref, image_digest, image_pinned = None, None, False
    if args.sandbox == "docker":
        image_ref, image_digest, image_pinned = _docker_preflight_or_fail(repo, args.image)
```

Before:

```python
    try:
        ensure_worktrees_excluded(repo)
        worktree = create_worktree(repo, slug, args.branch_from,
                                    no_checkout=(args.sandbox == "docker"))
    except WorkspaceError as e:
        raise PreflightFailure(str(e))
    return RunContext(
        repo=repo, slug=slug, branch=branch, worktree=worktree,
        base_commit=worktree_base_commit(worktree), task=args.task,
        sandbox_mode=args.sandbox, provider=args.provider, image_ref=image_ref, image_digest=image_digest,
        image_pinned=image_pinned, context_window=context_window, branch_from=args.branch_from,
    )
```

After:

```python
    try:
        ensure_worktrees_excluded(repo)
        worktree = create_worktree(repo, slug, branch_from,
                                    no_checkout=(args.sandbox == "docker"))
    except WorkspaceError as e:
        raise PreflightFailure(str(e))
    return RunContext(
        repo=repo, slug=slug, branch=branch, worktree=worktree,
        base_commit=worktree_base_commit(worktree), task=args.task,
        sandbox_mode=args.sandbox, provider=args.provider, image_ref=image_ref, image_digest=image_digest,
        image_pinned=image_pinned, context_window=context_window, branch_from=branch_from,
        branch_from_run=branch_from_run,
    )
```

`run_start.branch_from` is `ctx.branch_from` (`__main__.py:605`), which is now the resolved branch name for an `@slug` value — exactly what spec §6.2 asks for.

- [ ] **Step 10: Record it in `run.json`**

In `dirtywork/__main__.py`, `_write_run_json_start`.

Before:

```python
        "context_window": ctx.context_window,
        "resumed_from": ctx.resumed_from,
```

After:

```python
        "context_window": ctx.context_window,
        "branch_from_run": ctx.branch_from_run,
        "resumed_from": ctx.resumed_from,
```

- [ ] **Step 11: Describe the flag in `--help`**

In `dirtywork/__main__.py`, `_parse_args`.

Before:

```python
    run_p.add_argument("--branch-from", default=None)
```

After:

```python
    run_p.add_argument("--branch-from", default=None, metavar="REF",
                       help="branch the new worktree from REF (default: repo HEAD). "
                            "'@<slug>' means an earlier run's branch: its worktree is "
                            "snapshotted first if dirty, so the new run starts from that "
                            "run's work as it stands")
```

- [ ] **Step 12: Run the CLI tests**

Run: `python3 -m pytest tests/test_main.py -q`
Expected: `61 passed, 1 skipped` (57 after Task 7 + 4).

- [ ] **Step 13: Document `--branch-from @<slug>` — README flag block**

In `README.md`, the Machine contract flag block.

Before:

```
    [--branch-from <ref>]             # default: repo HEAD
```

After:

```
    [--branch-from <ref>|@<slug>]     # default: repo HEAD; @<slug> = an earlier run's branch
```

- [ ] **Step 14: Document it — README prose**

In `README.md`, immediately before the `--image REF` bullet Task 6 added.

Before:

```
- `--image REF` (docker mode) — the worker image, default
```

After:

```
- `--branch-from @<slug>` — start the new run from the branch an earlier run
  left behind instead of from repo HEAD. If that run's worktree still has
  uncommitted work, dirtywork snapshots it first (`dirtywork runs snapshot`'s
  plumbing-only commit — no filters, no hooks) and prints
  `snapshot <sha> on <branch> (from @<slug>)` on stderr, so the new run starts
  from the work as the reviewer actually saw it, not from the last commit.
  Unknown slug, or a run whose `run.json` records no branch, is a preflight
  refusal (exit 2, nothing created). The resolved branch NAME is what
  `run_start.branch_from` records; `run.json` also records
  `branch_from_run: "<slug>"`. This is the "start a fresh run from what that
  one produced" half of the review→fix loop — the other half is
  `dirtywork resume <slug> --feedback "..."`, which continues the same run on
  the same worktree.

- `--image REF` (docker mode) — the worker image, default
```

- [ ] **Step 15: Document it — `docs/transcript-schema.md`**

The `run_start` table's `branch_from` row.

Before:

```
| `branch_from` | | ✓ | string \| null | `--branch-from` as given, or null for repo HEAD |
```

After:

```
| `branch_from` | | ✓ | string \| null | the ref the worktree was branched from, or null for repo HEAD. For `--branch-from @<slug>` this is the **resolved branch name** of that run, not the `@<slug>` text; `run.json`'s `branch_from_run` records the slug |
```

The `run.json` table, after the `resumed_by` row.

Before:

```
| `resumed_by` | — | written onto the **prior** run's `run.json` when a resume starts |
```

After:

```
| `resumed_by` | — | written onto the **prior** run's `run.json` when a resume starts |
| `branch_from_run` | start | 0.8: the slug `--branch-from @<slug>` named, or null. The resolved branch itself is `run_start.branch_from` |
```

- [ ] **Step 16: Run the full suite**

Run: `python3 -m pytest -q`
Expected: `888 passed, 1 skipped` (883 after Task 7 + 5), 18 deselected.

- [ ] **Step 17: Commit**

```bash
git add dirtywork/workspace.py dirtywork/__main__.py tests/test_workspace.py tests/test_main.py README.md docs/transcript-schema.md
git commit -m "feat: '--branch-from @<slug>' branches from an earlier run, snapshotting it first"
```

---

### Task 9: `resume --feedback` / `--feedback-file` (spec §6.3)

**Files:**
- Modify: `dirtywork/resume.py` (`:12-15` constants, `:191-204` `build_resume_task`)
- Modify: `dirtywork/__main__.py` (`:36` import; new `_load_feedback`; `_load_resume_target` at `:497-523`; `RunContext`; `_workspace_resume` at `:526-548`; `_write_run_json_start`; `run_info` at `:603-609`; `_parse_args` at `:768-770`)
- Modify: `tests/test_resume.py` (2 new tests)
- Modify: `tests/test_main.py` (3 new tests; 4 existing resume calls updated; 1 skip marker removed)
- Modify: `tests/test_docker_live.py` (1 `docker`-marked resume call updated)
- Modify: `README.md` (*Resuming a run*, the `resume` flag block)
- Modify: `docs/transcript-schema.md` (`run_start.feedback`, `run.json.feedback`)

**Interfaces:**
- Consumes: `resume.RESUME_MARKER` (`resume.py:13`); `resume.render_transcript_tail`; `__main__.PreflightFailure`.
- Produces:
  - `resume.RESUME_FEEDBACK_MARKER = "\n\n--- RESUMED RUN: REVIEW FEEDBACK ---\n"`
  - `resume.MAX_FEEDBACK_CHARS = 64_000`
  - `resume.build_resume_task(prior_task, prior_status, prior_turns, transcript_tail, feedback=None) -> str` — the new parameter is keyword-or-positional with a default, so the four existing call sites are unaffected
  - `__main__._load_feedback(args) -> str | None`
  - `RunContext.feedback: str | None = None`; `run.json` key `feedback`; `run_start.feedback`
  - CLI: `dirtywork resume <run> [--feedback TEXT | --feedback-file PATH]`; a `completed` run without feedback is refused (exit 2)

- [ ] **Step 1: Write the failing `build_resume_task` tests**

Append to `tests/test_resume.py`:

```python
def test_build_resume_task_with_feedback_uses_the_feedback_block():
    text = build_resume_task("Fix the bug", "completed", 12, "run_end: completed",
                             feedback="You deleted the retry loop; put it back.")
    assert text.startswith("Fix the bug\n\n--- RESUMED RUN: REVIEW FEEDBACK ---\n")
    assert "--- RESUMED RUN ---" not in text
    assert "ended with status 'completed' after 12 turns" in text
    assert "A reviewer read that run's work and sent this feedback:" in text
    assert "You deleted the retry loop; put it back." in text
    assert "apply the feedback. Make no other changes." in text
    assert text.endswith("When the task is complete, call finish(summary=...).")


def test_build_resume_task_strips_both_markers_so_blocks_never_stack():
    plain = build_resume_task("Fix the bug", "max_turns", 40, "run_end: max_turns")
    with_feedback = build_resume_task(plain, "stalled", 12, "run_end: stalled",
                                      feedback="try again")
    assert with_feedback.count("--- RESUMED RUN ---") == 0
    assert with_feedback.count("--- RESUMED RUN: REVIEW FEEDBACK ---") == 1
    assert with_feedback.startswith("Fix the bug\n\n--- RESUMED RUN")
    back_to_plain = build_resume_task(with_feedback, "max_turns", 3, "run_end: max_turns")
    assert back_to_plain.count("--- RESUMED RUN: REVIEW FEEDBACK ---") == 0
    assert back_to_plain.count("--- RESUMED RUN ---") == 1
    assert back_to_plain.startswith("Fix the bug\n\n--- RESUMED RUN ---")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_resume.py -q -k "feedback or stacks"`
Expected: 2 failed — `TypeError: build_resume_task() got an unexpected keyword argument 'feedback'`.

- [ ] **Step 3: Add the feedback marker and the feedback task shape**

In `dirtywork/resume.py`, the constants.

Before:

```python
RESUME_TAIL_CHARS = 12_000
RESUME_MARKER = "\n\n--- RESUMED RUN ---\n"
PRE_RESUME_SUFFIX = ".pre-resume"
```

After:

```python
RESUME_TAIL_CHARS = 12_000
RESUME_MARKER = "\n\n--- RESUMED RUN ---\n"
# Spec §6.3: a resume that carries a reviewer's instructions gets its own
# marker, so the two block shapes never mix — and BOTH are stripped from a
# prior task before a new block is built, so re-resuming never accumulates.
RESUME_FEEDBACK_MARKER = "\n\n--- RESUMED RUN: REVIEW FEEDBACK ---\n"
MAX_FEEDBACK_CHARS = 64_000
PRE_RESUME_SUFFIX = ".pre-resume"
```

And `build_resume_task`.

Before:

```python
def build_resume_task(prior_task: str, prior_status: str, prior_turns, transcript_tail: str) -> str:
    prior_task = prior_task.split(RESUME_MARKER, 1)[0]
    turns_text = str(prior_turns) if isinstance(prior_turns, int) else "unknown"
    return (
        f"{prior_task}{RESUME_MARKER}"
        f"This run continues an earlier run that ended with status '{prior_status}' after "
        f"{turns_text} turns.\n"
        "The worktree already contains that run's work: inspect it with `git status` and "
        "`git diff` before doing anything else, and continue from there — do not start over "
        "or revert prior work.\n"
        "The last events of the earlier run were:\n"
        f"{transcript_tail}\n"
        "When the task is complete, call finish(summary=...)."
    )
```

After:

```python
def build_resume_task(prior_task: str, prior_status: str, prior_turns, transcript_tail: str,
                      feedback: str | None = None) -> str:
    prior_task = prior_task.split(RESUME_MARKER, 1)[0].split(RESUME_FEEDBACK_MARKER, 1)[0]
    turns_text = str(prior_turns) if isinstance(prior_turns, int) else "unknown"
    if feedback:
        return (
            f"{prior_task}{RESUME_FEEDBACK_MARKER}"
            f"This run continues an earlier run that ended with status '{prior_status}' after "
            f"{turns_text} turns.\n"
            "A reviewer read that run's work and sent this feedback:\n\n"
            f"{feedback}\n\n"
            "The worktree already contains the earlier run's work: inspect it with "
            "`git status` and\n"
            "`git diff` first, then apply the feedback. Make no other changes.\n"
            "The last events of the earlier run were:\n"
            f"{transcript_tail}\n"
            "When the task is complete, call finish(summary=...)."
        )
    return (
        f"{prior_task}{RESUME_MARKER}"
        f"This run continues an earlier run that ended with status '{prior_status}' after "
        f"{turns_text} turns.\n"
        "The worktree already contains that run's work: inspect it with `git status` and "
        "`git diff` before doing anything else, and continue from there — do not start over "
        "or revert prior work.\n"
        "The last events of the earlier run were:\n"
        f"{transcript_tail}\n"
        "When the task is complete, call finish(summary=...)."
    )
```

- [ ] **Step 4: Run the resume tests**

Run: `python3 -m pytest tests/test_resume.py -q`
Expected: `19 passed` (17 today + 2).

- [ ] **Step 5: Write the failing CLI tests**

Append to `tests/test_main.py`:

```python
def test_resume_feedback_reaches_the_task_run_json_and_run_start(
        tmp_path, monkeypatch, capsys):
    m, repo, rc = _first_run(monkeypatch, tmp_path, None)
    first = json.loads(capsys.readouterr().out)
    assert first["status"] == "completed"
    patch_provider(monkeypatch, m,
                        lambda base_url=None: _ScriptedClient(base_url, _resume_responses()))
    rc = m.main(["resume", Path(first["run_dir"]).name,
                 "--feedback", "You removed the null check; restore it."])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0, out
    data = json.loads((Path(out["run_dir"]) / "run.json").read_text())
    assert data["feedback"] == "You removed the null check; restore it."
    assert "--- RESUMED RUN: REVIEW FEEDBACK ---" in data["task"]
    assert "You removed the null check; restore it." in data["task"]
    start = next(e for e in (json.loads(l) for l in
                             Path(out["transcript"]).read_text().splitlines())
                 if e["event"] == "run_start")
    assert start["feedback"] == "You removed the null check; restore it."


def test_resume_of_a_completed_run_without_feedback_is_refused(tmp_path, monkeypatch, capsys):
    m, repo, rc = _first_run(monkeypatch, tmp_path, None)
    first = json.loads(capsys.readouterr().out)
    slug = Path(first["run_dir"]).name
    assert m.main(["resume", slug]) == 2
    err = capsys.readouterr().err
    assert f"run '{slug}' ended 'completed'" in err
    assert "--feedback" in err
    assert len(list((tmp_path / "runs").iterdir())) == 1     # nothing created


def test_resume_feedback_file_and_its_refusals(tmp_path, monkeypatch, capsys):
    m, repo, rc = _first_run(monkeypatch, tmp_path, None)
    first = json.loads(capsys.readouterr().out)
    slug = Path(first["run_dir"]).name

    assert m.main(["resume", slug, "--feedback", "a", "--feedback-file", "b"]) == 2
    assert "mutually exclusive" in capsys.readouterr().err

    assert m.main(["resume", slug, "--feedback-file", str(tmp_path / "nope.txt")]) == 2
    assert "cannot read feedback file" in capsys.readouterr().err

    big = tmp_path / "big.txt"
    big.write_text("x" * 64_001, encoding="utf-8")
    assert m.main(["resume", slug, "--feedback-file", str(big)]) == 2
    assert "over the 64000-char limit" in capsys.readouterr().err

    note = tmp_path / "note.txt"
    note.write_text("Restore the retry loop.\n", encoding="utf-8")
    patch_provider(monkeypatch, m,
                        lambda base_url=None: _ScriptedClient(base_url, _resume_responses()))
    assert m.main(["resume", slug, "--feedback-file", str(note)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert json.loads((Path(out["run_dir"]) / "run.json").read_text())["feedback"] == (
        "Restore the retry loop.\n")
```

- [ ] **Step 6: Run them to verify they fail**

Run: `python3 -m pytest tests/test_main.py -q -k "resume_feedback or completed_run_without_feedback"`
Expected: 3 failed — two with `SystemExit: 2` from argparse (`unrecognized arguments: --feedback ...`), and `test_resume_of_a_completed_run_without_feedback_is_refused` with `assert 0 == 2` (today a completed run resumes fine).

- [ ] **Step 7: Add the two flags**

In `dirtywork/__main__.py`, `_parse_args`.

Before:

```python
    resume_p = sub.add_parser("resume", help="continue an earlier run on its worktree")
    resume_p.add_argument("run", help="run slug (under ~/.dirtywork/runs) or a run directory path")
    _add_run_flags(resume_p, resume=True)
```

After:

```python
    resume_p = sub.add_parser("resume", help="continue an earlier run on its worktree")
    resume_p.add_argument("run", help="run slug (under ~/.dirtywork/runs) or a run directory path")
    resume_p.add_argument("--feedback", default=None, metavar="TEXT",
                          help="reviewer instructions for this resume; the resumed task tells "
                               "the worker to inspect the earlier work and apply exactly this "
                               "and nothing else. Required to resume a 'completed' run")
    resume_p.add_argument("--feedback-file", default=None, metavar="PATH",
                          help="read --feedback from a UTF-8 file instead (max 64000 chars)")
    _add_run_flags(resume_p, resume=True)
```

Exclusivity is enforced in `_load_feedback` rather than by `add_mutually_exclusive_group`, so a misuse returns 2 through `main()`'s normal path (one machine-contract-shaped refusal) instead of raising `SystemExit` out of argparse.

- [ ] **Step 8: Load, cap and gate the feedback**

In `dirtywork/__main__.py`, the resume import.

Before:

```python
from .resume import ResumeError, build_resume_task, check_resumable, load_prior_run, render_transcript_tail, resolve_run_dir
```

After:

```python
from .resume import MAX_FEEDBACK_CHARS, ResumeError, build_resume_task, check_resumable, load_prior_run, render_transcript_tail, resolve_run_dir
```

Then insert `_load_feedback` immediately **before** `def _load_resume_target(args) -> dict:`.

```python
def _load_feedback(args):
    """Spec §6.3: --feedback / --feedback-file, mutually exclusive, UTF-8,
    capped. Returns the text or None. Raises PreflightFailure (exit 2)."""
    text = getattr(args, "feedback", None)
    path = getattr(args, "feedback_file", None)
    if text is not None and path is not None:
        raise PreflightFailure("--feedback and --feedback-file are mutually exclusive")
    if path is not None:
        try:
            text = Path(path).expanduser().read_text(encoding="utf-8")
        except (OSError, ValueError) as e:
            raise PreflightFailure(f"cannot read feedback file '{path}': {e}")
    if text is None:
        return None
    if len(text) > MAX_FEEDBACK_CHARS:
        raise PreflightFailure(
            f"feedback is {len(text)} chars, over the {MAX_FEEDBACK_CHARS}-char limit")
    return text
```

Then the gate, at the end of `_load_resume_target` (as left by Task 5).

Before:

```python
        prior_verify = prior.get("verify")
        if isinstance(prior_verify, dict) and prior_verify.get("command"):
            args.verify = prior_verify["command"]
    return prior
```

After:

```python
        prior_verify = prior.get("verify")
        if isinstance(prior_verify, dict) and prior_verify.get("command"):
            args.verify = prior_verify["command"]
    # Last, so the earlier refusals (still running, missing worktree, provider
    # switch) keep their own messages when they apply too.
    args.feedback_text = _load_feedback(args)
    if prior.get("status") == "completed" and not args.feedback_text:
        raise PreflightFailure(
            f"run '{prior['slug']}' ended 'completed'; pass --feedback to continue it "
            f"with new instructions")
    return prior
```

- [ ] **Step 9: Carry the feedback into the task, `run.json` and `run_start`**

In `dirtywork/__main__.py`, `RunContext` (as left by Task 8).

Before:

```python
    branch_from_run: str | None = None   # the @<slug> --branch-from named, if any
    resumed_from: str | None = None
```

After:

```python
    branch_from_run: str | None = None   # the @<slug> --branch-from named, if any
    feedback: str | None = None          # resume only: the reviewer's instructions
    resumed_from: str | None = None
```

Then `_workspace_resume`.

Before:

```python
    tail = render_transcript_tail(Path(prior["run_dir"]) / "transcript.jsonl")
    task = build_resume_task(prior["task"], prior["status"], prior.get("turns"), tail)
    return RunContext(
        repo=repo, slug=slug, branch=prior["branch"], worktree=Path(prior["worktree"]),
        base_commit=prior["base_commit"], task=task, sandbox_mode=args.sandbox,
        provider=args.provider, image_ref=image_ref, image_digest=image_digest, image_pinned=image_pinned,
        context_window=context_window, resumed_from=prior["slug"],
        prior_run_dir=Path(prior["run_dir"]), seed_from_worktree=(args.sandbox == "docker"),
        owns_worktree=False,
    )
```

After:

```python
    tail = render_transcript_tail(Path(prior["run_dir"]) / "transcript.jsonl")
    feedback = getattr(args, "feedback_text", None)
    task = build_resume_task(prior["task"], prior["status"], prior.get("turns"), tail,
                             feedback)
    return RunContext(
        repo=repo, slug=slug, branch=prior["branch"], worktree=Path(prior["worktree"]),
        base_commit=prior["base_commit"], task=task, sandbox_mode=args.sandbox,
        provider=args.provider, image_ref=image_ref, image_digest=image_digest, image_pinned=image_pinned,
        context_window=context_window, resumed_from=prior["slug"], feedback=feedback,
        prior_run_dir=Path(prior["run_dir"]), seed_from_worktree=(args.sandbox == "docker"),
        owns_worktree=False,
    )
```

Then `_write_run_json_start` (as left by Task 8).

Before:

```python
        "context_window": ctx.context_window,
        "branch_from_run": ctx.branch_from_run,
        "resumed_from": ctx.resumed_from,
```

After:

```python
        "context_window": ctx.context_window,
        "branch_from_run": ctx.branch_from_run,
        "feedback": ctx.feedback,
        "resumed_from": ctx.resumed_from,
```

Then the `run_info` block.

Before:

```python
                "temperature": args.temperature, "sandbox": sandbox_info, "provider": ctx.provider,
                "resumed_from": ctx.resumed_from,
            },
```

After:

```python
                "temperature": args.temperature, "sandbox": sandbox_info, "provider": ctx.provider,
                "resumed_from": ctx.resumed_from, "feedback": ctx.feedback,
            },
```

- [ ] **Step 10: Update the four existing resume calls whose prior run is `completed`**

`_first_run(monkeypatch, tmp_path, None)` scripts a single plain-text reply, so its run ends `completed` — which the new gate refuses without feedback. Each of these tests is about something else entirely, so each gains a `--feedback`.

In `tests/test_main.py`, `test_resume_uses_prior_model_unless_overridden`.

Before:

```python
    rc = m.main(["resume", "--model", "other/model", Path(first["run_dir"]).name])
```

After:

```python
    rc = m.main(["resume", "--model", "other/model", "--feedback", "keep going",
                 Path(first["run_dir"]).name])
```

In `test_resume_setup_failure_keeps_worktree`.

Before:

```python
    monkeypatch.setattr(m, "HostSandbox", ExplodingHost)
    rc = m.main(["resume", Path(first["run_dir"]).name])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["status"] == "sandbox_error"
```

After:

```python
    monkeypatch.setattr(m, "HostSandbox", ExplodingHost)
    rc = m.main(["resume", Path(first["run_dir"]).name, "--feedback", "keep going"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["status"] == "sandbox_error"
```

In `test_resume_docker_mode_seeds_and_keeps_branch`.

Before:

```python
    rc = m.main(["resume", Path(first["run_dir"]).name])
    second = json.loads(capsys.readouterr().out)
    assert rc == 0, second
    assert len(start_calls) == 2
```

After:

```python
    rc = m.main(["resume", Path(first["run_dir"]).name, "--feedback", "keep going"])
    second = json.loads(capsys.readouterr().out)
    assert rc == 0, second
    assert len(start_calls) == 2
```

In `test_resume_inherits_the_prior_provider`.

Before:

```python
    assert m2.main(["resume", str(tmp_path / "runs" / slug)]) == 0
```

After:

```python
    assert m2.main(["resume", str(tmp_path / "runs" / slug), "--feedback", "keep going"]) == 0
```

- [ ] **Step 11: Update the one `docker`-marked live resume**

`tests/test_docker_live.py`'s resume also continues a `completed` run. It is deselected by default but runs in CI's docker-live job.

Before:

```python
    rc = _run_main(monkeypatch, tmp_path, second_responses, ["resume", Path(first["run_dir"]).name])
```

After:

```python
    rc = _run_main(monkeypatch, tmp_path, second_responses,
                   ["resume", Path(first["run_dir"]).name, "--feedback", "keep going"])
```

- [ ] **Step 12: Unskip the Task 5 resume-inheritance test**

In `tests/test_main.py`.

Before:

```python
@pytest.mark.skip(reason="needs resume --feedback (Task 9); unskipped there")
def test_verify_is_null_without_the_flag_and_resume_inherits_the_command(
        tmp_path, monkeypatch, capsys):
```

After:

```python
def test_verify_is_null_without_the_flag_and_resume_inherits_the_command(
        tmp_path, monkeypatch, capsys):
```

- [ ] **Step 13: Run the CLI tests**

Run: `python3 -m pytest tests/test_main.py -q`
Expected: `65 passed`, 0 skipped (61 after Task 8 + 3 new + the 1 unskipped).

- [ ] **Step 14: Document it — README *Resuming a run***

In `README.md`, at the end of the *Resuming a run* section, immediately before the "Docker-mode limit" paragraph.

Before:

```
Docker-mode limit: export stores files, not the worker's in-container
commits, so a resumed docker worker sees the earlier work as uncommitted
changes against the base commit — not as its old commit history. Host mode
(`--sandbox none`) keeps the real commits.
```

After:

```
**Sending review feedback.** `--feedback TEXT` (or `--feedback-file PATH`, a
UTF-8 file, max 64 000 chars; the two are mutually exclusive) turns a resume
into a review round: the resumed task keeps the original brief, then tells the
worker that a reviewer read its work and sent *this*, and to inspect the
worktree with `git status`/`git diff` and apply the feedback and nothing else.

    dirtywork resume <slug> --feedback "You dropped the null check in api.ts; restore it."

Resuming a run that ended `completed` **requires** feedback — without it the
command refuses (exit 2, nothing created), because a completed run that is
continued with its own original brief just re-does work it already declared
done. Every other status resumes with or without feedback, as before. The
feedback text is recorded in the new run's `run.json` (`feedback`) and its
`run_start` event; both resume markers (`--- RESUMED RUN ---` and
`--- RESUMED RUN: REVIEW FEEDBACK ---`) are stripped from the prior task before
a new block is built, so resuming a resume never accumulates preambles.

Docker-mode limit: export stores files, not the worker's in-container
commits, so a resumed docker worker sees the earlier work as uncommitted
changes against the base commit — not as its old commit history. Host mode
(`--sandbox none`) keeps the real commits.
```

- [ ] **Step 15: Document it — README Machine contract**

The `resume` flag block. (Four-backtick outer fence: the block contains a three-backtick fence.)

Before:

````
```
dirtywork resume <slug | run-dir>     # same flags as run, minus --repo/--branch-from/--sandbox/<task>;
    [--model <m>]                     # defaults to the earlier run's model; --image defaults to its image
```
````

After:

````
```
dirtywork resume <slug | run-dir>     # same flags as run, minus --repo/--branch-from/--sandbox/<task>;
    [--model <m>]                     # defaults to the earlier run's model; --image defaults to its image
    [--feedback "<text>"]             # reviewer instructions; REQUIRED to resume a `completed` run
    [--feedback-file <path>]          # same, read from a UTF-8 file (max 64000 chars)
```
````

- [ ] **Step 16: Document it — `docs/transcript-schema.md`**

The `run_start` table, after the `resumed_from` row.

Before:

```
| `resumed_from` | | ✓ | string \| null | slug of the run this one continues |
```

After:

```
| `resumed_from` | | ✓ | string \| null | slug of the run this one continues |
| `feedback` | | ✓ | string \| null | 0.8: `resume --feedback`/`--feedback-file` text, verbatim (max 64 000 chars); null on a fresh run or a resume without feedback |
```

The `run_start.task` row (the marker note).

Before:

```
| `task` | ✓ | ✓ | string | the task text; on a resumed run it also carries the `--- RESUMED RUN ---` block |
```

After:

```
| `task` | ✓ | ✓ | string | the task text; on a resumed run it also carries the `--- RESUMED RUN ---` block, or the `--- RESUMED RUN: REVIEW FEEDBACK ---` block when `--feedback` was given. Both markers are stripped from the prior task before a new block is built, so resuming a resume never stacks them |
```

The `run.json` table, after the `branch_from_run` row Task 8 added.

Before:

```
| `branch_from_run` | start | 0.8: the slug `--branch-from @<slug>` named, or null. The resolved branch itself is `run_start.branch_from` |
```

After:

```
| `branch_from_run` | start | 0.8: the slug `--branch-from @<slug>` named, or null. The resolved branch itself is `run_start.branch_from` |
| `feedback` | start | 0.8: `resume --feedback`/`--feedback-file` text, or null |
```

- [ ] **Step 17: Run the full suite**

Run: `python3 -m pytest -q`
Expected: `894 passed` (888 after Task 8 + 5 new + 1 unskipped), 0 skipped, 18 deselected.

- [ ] **Step 18: Commit**

```bash
git add dirtywork/resume.py dirtywork/__main__.py tests/test_resume.py tests/test_main.py tests/test_docker_live.py README.md docs/transcript-schema.md
git commit -m "feat: 'resume --feedback' turns a resume into a review round"
```

---

### Task 10: 0.8.0 wrap-up — version, consolidated contract, final gate

**Files:**
- Modify: `pyproject.toml` (`:7`)
- Modify: `dirtywork/__init__.py` (`:1`)
- Modify: `README.md` (the Machine contract stdout JSON example)
- Modify: `docs/transcript-schema.md` (the intro's event count and the v1/v2 paragraph)
- Modify: `tests/test_transcript_schema.py` (1 new test)

**Interfaces:**
- Consumes: `dirtywork.__version__`; every field, status and flag Tasks 1–9 produced.
- Produces: `dirtywork.__version__ == "0.8.0"`; one stdout JSON example in the README that lists every 0.8 field.

No behaviour changes here. The per-feature documentation already landed in its own task; this task only fixes the two whole-document counts that no single feature owns, bumps the version, and runs the final gate.

- [ ] **Step 1: Write the failing version test**

Append to `tests/test_transcript_schema.py`:

```python
def test_version_is_in_step_with_pyproject():
    import dirtywork
    pyproject = (Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert dirtywork.__version__ == "0.8.0"
    assert f'version = "{dirtywork.__version__}"' in pyproject
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_transcript_schema.py -q -k version_is_in_step`
Expected: 1 failed — `AssertionError: assert '0.7.0' == '0.8.0'`.

- [ ] **Step 3: Bump the version**

In `pyproject.toml`.

Before:

```toml
version = "0.7.0"
```

After:

```toml
version = "0.8.0"
```

In `dirtywork/__init__.py`.

Before:

```python
__version__ = "0.7.0"
```

After:

```python
__version__ = "0.8.0"
```

- [ ] **Step 4: Consolidate the stdout JSON example**

In `README.md`, the Machine contract's example payload. (Four-backtick outer fence: the block contains a three-backtick fence.)

Before:

````
```json
{
  "schema_version": 2,
  "status": "completed",
  "worktree": "/path/to/repo/.worktrees/dw-<slug>",
  "branch": "dirtywork/<slug>",
  "transcript": "/path/to/transcript.jsonl",
  "turns": 7,
  "usage": {"prompt_tokens": 0, "completion_tokens": 0},
  "final_message": "...",
  "provider": "openai",
  "run_dir": "/home/you/.dirtywork/runs/<slug>",
  "base_commit": "abc123...",
  "resumed_from": null,
  "finalize_error": null,
  "watchdog_violation": null,
  "watchdog_violation_kind": null
}
```
````

After:

````
```json
{
  "schema_version": 2,
  "status": "completed",
  "worktree": "/path/to/repo/.worktrees/dw-<slug>",
  "branch": "dirtywork/<slug>",
  "transcript": "/path/to/transcript.jsonl",
  "turns": 7,
  "usage": {"prompt_tokens": 0, "completion_tokens": 0},
  "final_message": "...",
  "provider": "openai",
  "run_dir": "/home/you/.dirtywork/runs/<slug>",
  "base_commit": "abc123...",
  "resumed_from": null,
  "finalize_error": null,
  "watchdog_violation": null,
  "watchdog_violation_kind": null,
  "stuck_on": null,
  "files_changed": ["web/src/lib/api.ts", "web/src/lib/api.test.ts"],
  "files_changed_truncated": false,
  "last_tool_result": {
    "tool": "bash",
    "args": "{\"command\": \"npm test\"}",
    "result": "exit code: 0\n12 passing"
  },
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

The last six keys are 0.8 additions (`stuck_on`, `files_changed`,
`files_changed_truncated`, `last_tool_result`, `last_assistant_text`,
`verify`). Every one of them is present on every normal end-of-run payload:
`null` when it does not apply, `[]`/`false` for the list and its flag.
````

- [ ] **Step 5: Fix the two whole-document counts in `docs/transcript-schema.md`**

These are the only doc edits no single feature owns: one counts events across the whole file, the other summarises v2 as a whole.

Before:

```
(one of the seven event names below). `schema_version` marks the overall
```

After:

```
(one of the eight event names below). `schema_version` marks the overall
```

Before:

```
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
```

After:

```
**v1** is the pre-hardening shape (dirtywork ≤ 0.2.0, host-only execution, no
`schema_version` field at all — its absence *is* the v1 marker). **v2** (0.3.0
and later, including the 0.4.x Docker sandbox, the 0.5.x harness-robustness
releases and the 0.8 run-evidence release) adds Docker-sandbox provenance,
provider identity, resume lineage, six new terminal statuses, the `nudge`,
`sandbox_reset` and `verify` events, richer `run_end` fields from the export
validator, and 0.8's end-of-run evidence (`files_changed`,
`files_changed_truncated`, `last_tool_result`, `last_assistant_text`,
`stuck_on`, `verify`). A v1 reader that ignores unknown
fields keeps working unmodified against v2 output: every v2 addition is a new
field, a new event, or a new enum value — never a removed or renamed one. That
is the same compatibility rule the stdout JSON contract follows, for the same
reason. **0.8 keeps `schema_version` at 2** for exactly that reason: everything
it adds is additive.
```

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest -q`
Expected: `895 passed` (894 after Task 9 + 1), 0 skipped, 18 deselected.

- [ ] **Step 7: Prove nothing stale is left**

```bash
grep -rn 'dirtywork-worker:0\.7' --include='*.py' --include='*.md' --include='*.yml' . | grep -v docs/superpowers
grep -rn '0\.7\.0' pyproject.toml dirtywork/__init__.py
grep -rn 'seven tools\|the seven event names' README.md docs/transcript-schema.md
```
Expected: all three print nothing.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml dirtywork/__init__.py README.md docs/transcript-schema.md tests/test_transcript_schema.py
git commit -m "chore: 0.8.0 — run evidence and review loop"
```

---

## Self-review: design coverage

| Spec section / item | Task / step |
|---|---|
| §1.1 `RepeatTracker(limit)` beside `ProgressTracker`, fed from `note_call`'s call site | Task 3, Steps 3, 6 |
| §1.1 `note_bash` reuses the existing `_bash_fingerprint` | Task 3, Step 3 (`RepeatTracker.note_bash`) |
| §1.1 only failing results count; first line exactly `exit code: 0` is passing | Task 3, Step 3 (`_failed`); test in Step 1 covers `ERROR:` |
| §1.1 a passing result of the SAME command resets the streak; other passing commands and non-`bash` calls neither count nor reset | Task 3, Steps 3, 6; test `test_only_the_same_command_passing_resets_the_stuck_streak` |
| §1.1 `limit <= 0` disables | Task 3, Step 3; test `test_repeat_tracker_limit_zero_disables` |
| §1.2 `--stuck-repeats N` (default 4) on `run` and `resume`, no nudge | Task 3, Step 11 (`_add_run_flags` serves both parsers) |
| §1.2 finish the turn's remaining calls, then status `stuck`, exit 1, resumable | Task 3, Step 7 |
| §1.2 `stuck_on` on `RunResult.extra` → stdout JSON, `run_end`, `run.json`; null otherwise | Task 3, Steps 5, 12 |
| §1.2 `runs show` plain (`SHOW_FIELDS`) + Markdown `## Result` | Task 3, Step 16 |
| §1.2 README status list, exit codes, flag table; transcript-schema | Task 3, Steps 18, 19 |
| §2 `files_changed` docker: `git diff --cached --name-only` after `git add -A`, in the container | Task 4, Step 8 |
| §2 `files_changed` host: `git diff --name-only` + `git ls-files --others --exclude-standard` beside `host_diff_stat` | Task 4, Steps 3, 9 |
| §2 sorted, capped at 1000, `files_changed_truncated` | Task 4, Steps 3, 8 (`MAX_FILES_CHANGED`) |
| §2 `[]` when nothing changed / the export never ran | Task 4, Step 17 (`extra.get("files_changed") or []`) |
| §2 `last_tool_result` `{tool, args ≤500, result ≤2000}`, skipping `finish`, null if none | Task 4, Step 13 |
| §2 `last_assistant_text` ≤2000, null if none | Task 4, Step 13 |
| §2 on every normal end-of-run path, whatever the status | Task 4, Step 13 (`finish()`'s extra), Step 17 |
| §2 `runs show --markdown` renders all three; plain view shows `files_changed` | Task 4, Step 21 |
| §2 README Machine contract + transcript-schema | Task 4, Steps 23, 24 |
| §3.1 `describe_change(path, old, new, *, verb)` in `tools.py`, used by both backends | Task 1, Steps 4, 5, 6, 13 |
| §3.1 `+A -D` from SequenceMatcher; `(removed N non-blank line(s))` only when N>0; replace counts as removed | Task 1, Step 4 (`_line_counts`) |
| §3.1 `unified_diff(..., n=2)` capped at 40 lines / 3000 chars with `[diff truncated: N more lines]` | Task 1, Step 4 |
| §3.1 `write_file` verb `Wrote` on an existing file; `Wrote N bytes to path (new file, M lines)` on a new one | Task 1, Step 4 (`describe_write`), Steps 5, 13 |
| §3.1 every existing `ERROR:` string unchanged | Task 1, Steps 5, 6, 13 (only the success returns change); Task 2, Step 4 preserves them verbatim |
| §3.2 `insert_before`/`insert_after` with a unique-anchor rule and `edit_file`'s error shape | Task 2, Step 4 (`_insert_once`) |
| §3.2 whole-line insertion, anchor may span lines, trailing newline added, anchor line untouched | Task 2, Step 3 (`insert_text`); tests in Step 1 |
| §3.2 one pure transform + a shared read→transform→write per backend; `edit_file` refactored into it | Task 2, Steps 3, 4, 13 |
| §3.2 success strings use `describe_change` with verb `Inserted into` | Task 2, Step 4 |
| §3.2 registered in `BUILTIN_SPECS` after `edit_file`, caps as `edit_file` | Task 2, Step 17 |
| §3.2 added to `_MUTATING_TOOLS` | Task 2, Step 19 |
| §3.2 system prompt rule names the new tools | Task 2, Step 20 |
| §3.2 README tool list ("seven" → nine), Security enumeration, transcript-schema `tool` enum | Task 2, Steps 22, 23 |
| §3.2 Sandbox Protocol + both backends implement the two methods | Task 2, Steps 8, 9, 13 |
| §4.1 `--verify`, `--verify-rounds` (1), `--verify-timeout` (600, clamped 1–600) on `run` | Task 5, Steps 4, 10 |
| §4.1 `resume` accepts all three and inherits from the prior `run.json` | Task 5, Step 10 (`_load_resume_target`); decision 2 in the task header records that only the command is recorded to inherit |
| §4.2 runs on the completion path (finish tool **and** plain answer), before `finalize()` | Task 5, Step 5 |
| §4.2 through `sandbox.bash(command, timeout)` — same guardrails/budget/reaper | Task 5, Step 5 (`run_verify`) |
| §4.2 pass iff first line `exit code: 0`; exit code parsed, null for `ERROR:`/`BLOCKED:` | Task 5, Step 3 (`parse_exit_code`) |
| §4.2 pass → `completed` | Task 5, Step 5 (`check_verify`) |
| §4.2 fail with a round left → `verify` event + the feedback user message + loop continues | Task 5, Steps 3 (`VERIFY_FEEDBACK`), 5 |
| §4.2 fail with no round left → `verify_failed`, exit 1, resumable | Task 5, Step 5 |
| §4.2 `BudgetExceeded`/`SandboxError` → existing statuses | Task 5, Step 5 (`check_verify`'s excepts) |
| §4.2 a passing verify also writes a `verify` event | Task 5, Step 3/5 (`run_verify` writes before returning) |
| §4.3 `verify` on every normal payload; null without `--verify`; `{command, exit_code, output_tail, rounds, passed}` | Task 5, Steps 5, 11 |
| §4.3 `runs show` plain + Markdown `## Result` | Task 5, Step 15 |
| §4.3 README (flags, status list, exit codes, "Verifying a run" under *Use*), transcript-schema | Task 5, Steps 17, 18, 19 |
| §5 Dockerfile apt line gains `jq uuid-runtime shellcheck curl`, nothing else changes | Task 6, Step 3 |
| §5 `DEFAULT_IMAGE` → `:0.8`, CI tag → `:0.8`, README/docs mentions → `:0.8` | Task 6, Steps 4, 6 |
| §5 `PINNED_DIGEST` stays `None`, comment updated | Task 6, Step 4 |
| §5 README callout ("cannot install dependencies…", always run the gate / `--verify`) + the Node symlink trick | Task 6, Step 9 |
| §5 derived-image recipe next to `--image` in the Machine contract, custom images never pinned | Task 6, Step 10 |
| §5 `docker/README.md` gets the same recipe and the new package list | Task 6, Steps 7, 8 |
| §6.1 `snapshot_worktree(worktree, branch, message) -> str \| None` | Task 7, Step 4 |
| §6.1 Python walk, `os.lstat`, skip the top-level `.git` only, ignore rules not applied | Task 7, Step 4 (`_walk_worktree`) |
| §6.1 one `hash-object -w --no-filters --stdin-paths`; newline paths refused | Task 7, Step 4 |
| §6.1 symlinks → target string via `hash-object -w --stdin` at mode `120000`, never followed | Task 7, Step 4 |
| §6.1 exec bit → `100755`; other file types skipped and counted | Task 7, Step 4 |
| §6.1 `update-index --index-info` on a temp index → `write-tree` → `commit-tree` → `update-ref` → `host_read_tree` | Task 7, Step 4 (temp-index location deviation recorded in the task header) |
| §6.1 `dirtywork <dirtywork@localhost>` as author and committer | Task 7, Step 4 (`SNAPSHOT_AUTHOR`) |
| §6.1 config-neutral env everywhere, plus `-c commit.gpgsign=false` | Task 4, Step 3 (`git_env`, `GIT_NEUTRAL_FLAGS`); Task 7, Step 4 |
| §6.1 returns `None` and creates nothing when the tree matches the branch head | Task 7, Step 4; test in Step 1 |
| §6.1 `runs snapshot <slug>` message, output lines, exit 0 | Task 7, Step 8 |
| §6.1 refusals: live pid, missing worktree, not a linked worktree, pre-resume stash | Task 7, Step 8 (plus the pristine-worktree refusal, justified inline) |
| §6.1 works in both sandbox modes; branch comes from `run.json` | Task 7, Step 8 |
| §6.2 `@`-prefixed `--branch-from` resolved via `resolve_run_dir` + `load_prior_run` | Task 8, Step 9 |
| §6.2 unknown slug → exit 2 with the specified message; missing branch → exit 2 | Task 8, Step 9; test in Step 5 |
| §6.2 dirty worktree → snapshot first + the stderr line | Task 8, Steps 3, 9 |
| §6.2 `run_start.branch_from` records the resolved branch name; `run.json.branch_from_run` records the slug | Task 8, Steps 9, 10, 15 |
| §6.3 `--feedback` / `--feedback-file`, mutually exclusive, UTF-8, 64 000-char cap → exit 2 | Task 9, Steps 7, 8 |
| §6.3 `build_resume_task(..., feedback=None)` and the exact feedback block | Task 9, Step 3 |
| §6.3 both markers stripped before building | Task 9, Step 3; test `test_build_resume_task_strips_both_markers_so_blocks_never_stack` |
| §6.3 resuming `completed` without feedback refused (exit 2) with the specified message | Task 9, Step 8 |
| §6.3 feedback recorded in `run.json` and `run_start` | Task 9, Step 9 |
| §6.3 README *Resuming a run* paragraph + Machine contract flags | Task 9, Steps 14, 15 |
| §7 all six new payload fields, additive, `schema_version` stays 2 | Tasks 3–5; Task 10, Steps 4, 5 |
| §7 status enum gains `stuck` and `verify_failed`, both exit 1 | Task 3, Step 18; Task 5, Step 18 |
| §7 every new flag read with `getattr(args, name, default)` | Task 3, Step 12; Task 5, Steps 10, 11; Task 8, Step 9; Task 9, Step 8 |
| §7 tests live in the existing modules; baseline may only rise | Global Constraints; every task's final suite step |
| §7 version 0.8.0 in `pyproject.toml` and `dirtywork/__init__.py` | Task 10, Step 3 |
| §7 docs touched: README, `docs/transcript-schema.md`, `docker/README.md` | Tasks 1–10 |

Not carried into this plan, deliberately: the spec's "release notes list the six issues" (a release artifact, not a repo change — the wrap-up commit message names the release) and its `bench/`-style ledger note (explicitly not required).

## Type consistency checklist

- `tools.describe_change(path: str, old_text: str, new_text: str, *, verb: str) -> str` — `verb` is always one of `"Edited"`, `"Wrote"`, `"Inserted into"`; both backends call it through `_replace_once`/`_insert_once`/`describe_write`, never with a fourth verb.
- `tools.describe_write(path: str, old_text, new_text: str, byte_count: int) -> str` — `old_text` is `str | None`; `None` is the only "new file" signal, and both backends produce it the same way (a failed/undecodable read-back).
- `tools._line_counts(old_lines: list, new_lines: list) -> tuple` — always `(int, int, int)`.
- `tools._read_text_for_diff(path: Path)` → `str | None`, never raises.
- `tools.insert_text(text: str, anchor: str, insert: str, where: str) -> str` — `where` is exactly `"before"` or `"after"`; only `_insert_once` supplies it, from the two `insert_before`/`insert_after` wrappers.
- `transform` callables (`tools._replace_once`, `tools._insert_once`) return `(str | None, str)`. `None` in the first slot means "refused, do not write"; the second slot is always the result string. `tools._transform_file` and `DockerSandbox._transform_file` consume exactly this shape, so a transform written for one backend works in the other unchanged.
- `tools._transform_file(worktree: Path, path: str, transform, *, tool: str) -> str`; `DockerSandbox._transform_file(path: str, transform) -> str` — the docker one takes no `tool` because its UTF-8 refusal comes from `_read_raw(strict=True)`, not from the transform path.
- `DockerSandbox._write_raw(path: str, encoded: bytes) -> str` returns `""` for success (falsy) and an `ERROR: …` string for failure; every caller tests it with `if err:`. `docker._oversized(encoded: bytes) -> str | None` follows the same convention.
- Sandbox tool methods all return `str` and never raise except `BudgetExceeded`/`SandboxError`; `insert_before`/`insert_after` match `edit_file` exactly (`(path: str, anchor: str, text: str) -> str`).
- `runner.RepeatTracker.note_bash(command, result) -> str | None` — only ever `"stuck"` or `None`; `command` may be `None` (a malformed bash call), which `_bash_fingerprint` already handles via `str(command)`. `RepeatTracker.stuck_on() -> dict` always returns all three keys.
- `runner.parse_exit_code(result) -> int | None`; `None` means "no `exit code:` line", which `check_verify` treats as not passing (`exit_code == 0` is False for `None`).
- `RunResult.extra` keys added by this plan and their types: `stuck_on: dict | None`, `last_tool_result: dict | None`, `last_assistant_text: str | None`, `verify: dict | None`, plus `files_changed: list` / `files_changed_truncated: bool` when `finalize()` ran. The CLI re-reads them with `extra.get(...)`, defaulting the list/flag pair so the payload always has them.
- `Runner.__init__`'s new parameters: `stuck_repeats: int`, `verify: str | None`, `verify_rounds: int`, `verify_timeout: int` (clamped to 1–600 in `__init__`, so `self.verify_timeout` is always a valid `sandbox.bash` timeout).
- `RunArtifacts.files_changed: list` and `files_changed_truncated: bool` — defaults `[]`/`False`, so every existing `RunArtifacts(...)` construction (all keyword-based) stays valid and every consumer gets the documented empty value.
- `workspace.git_env() -> dict` returns a FRESH dict each call; `GIT_NEUTRAL_FLAGS` is a `tuple` of `str` splatted into `_git(...)`, never mutated.
- `workspace._git(repo, *args, env=None, stdin_text=None) -> subprocess.CompletedProcess` with `text=True`, so `.stdout`/`.stderr` are `str` and `stdin_text` must be `str | None` (never `bytes`).
- `workspace.host_files_changed(...) -> tuple` is always `(list_of_str, bool)`; `workspace.host_worktree_dirty(...) -> bool`; `workspace._walk_worktree(...) -> (list, list, int)` with `list` elements `(str, bool)` and `(str, str)` respectively.
- `workspace.snapshot_worktree(worktree: Path, branch: str, message: str) -> str | None` — a 40-char sha or `None`; raises `WorkspaceError` only. Both callers (`runs.cmd_snapshot`, `__main__._resolve_branch_from`) catch exactly `WorkspaceError`.
- `resume.build_resume_task(prior_task, prior_status, prior_turns, transcript_tail, feedback=None) -> str` — `feedback` is `str | None`; the four pre-existing call sites pass four positional arguments and are unaffected.
- `__main__._load_feedback(args) -> str | None`; `__main__._resolve_branch_from(args) -> tuple` is always `(str | None, str | None)`.
- `RunContext`'s new fields are `str | None` with `None` defaults (`branch_from_run`, `feedback`), so `_workspace_new` and `_workspace_resume` each set only the one that applies.
- `runs._summary_value(key: str, data: dict) -> str` still always returns `str`; the three new branches (`stuck_on`, `files_changed`, `verify`) are guarded by `isinstance` so a hand-edited `run.json` with a wrong type falls through to `str(value)` rather than raising.
- `runs._md_result(data: dict, events: list) -> list` still returns a list of `str`; every new block appends only `str`.
- `runs.cmd_snapshot(args) -> int` returns `0` or `2` only.
- Python 3.9: `X | None` appears only in annotations and docstrings, in modules that already carry `from __future__ import annotations`; no runtime union, no `match`, no `|` in an `isinstance` call. `dataclasses.field(default_factory=list)` (used for the two new `RunArtifacts` fields) is 3.7+.
