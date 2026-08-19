# Run evidence & review loop (0.8) — stuck, end-of-run evidence, edit diffs, --verify, image deps, review-fix loop

**Date:** 2026-08-18
**Status:** Approved design (owner approved all seven sections in chat, 2026-08-18 19:14 CDT)
**Origin:** GitHub issues #30–#35, all filed from a day of running dirtywork 0.7.0 (pipx, docker
sandbox, `qwen/qwen3-coder-next`) against the invoicr repo: a worker that could not run the
repo's gate shipped gate-failing code with a confident `finish` (#30); the review→fix loop needed a
manual wip commit plus a re-stated task and caused two orchestrator errors in one afternoon (#31); a
run burned to `--max-turns 50` re-running an unpassable test (#32); a `max_turns` run came back with
`final_message: ""` and nothing else to triage from (#33); an "insert a line" brief was executed as a
replace that silently deleted a call (#34); the operator re-ran the repo's gate on the host after
every run because the worker's "tests pass" claim needed confirmation (#35).
**Parent specs:** `2026-08-15-review-response-design.md` (v3.1, security posture) and
`2026-08-17-sp2.5-harness-robustness-design.md` (completion signal, stall detector, resume).
Ships as **dirtywork 0.8.0**.

## Purpose

Give the orchestrator the evidence it needs at the moment a run ends, end runs for one more honest
reason, make the worker see its own edits, let the operator's gate be first-class, and make the
review→fix round a one-liner. Everything is Python-stdlib-only, keeps `schema_version` 2 (every
transcript, stdout, and `run.json` change is additive), and changes no security property of SP2:
the container recipe (apart from four apt packages), the export validator, the guardrails, and the
**no-host-git-on-worker-content rule** are untouched — the one new host-side write of worker
content (§6.1 snapshot) is done with plumbing that never applies filters or hooks, and the one new
piece of container-side data (§2 `files_changed`) is computed where `diff_stat` already is.

## 1. `stuck`: the same failing check, N times in a row (issue #32)

### 1.1 Detector

`runner.py` gains a small `RepeatTracker(limit: int)` next to `ProgressTracker`. It is fed only
`bash` calls, from the same place `ProgressTracker.note_call` is fed:

- `note_bash(command, result) -> "stuck" | None`. It computes `fp = _bash_fingerprint(command,
  result)` — the **existing** function (command + volatile-token-stripped output; the same one the
  stall detector uses, so a timing-only difference is not "different"). If `fp` equals the previous
  bash fingerprint, `repeats += 1`; otherwise `repeats = 1` and the previous fingerprint is replaced.
- Only **failing** results count. A result is failing unless its first line is exactly
  `exit code: 0` (so `exit code: N≠0`, `ERROR: command timed out …`, `ERROR: bash failed …` and
  `BLOCKED: …` all count). A passing result **of the same command** resets the streak to zero — a
  diligent worker that re-runs a green typecheck after every edit is never "stuck"; passing runs
  of *other* commands (`git status`, `cat`, `ls` …) neither count nor reset, exactly like a
  non-bash tool call, so the reads a model interleaves with its edit→test loop cannot hide an
  unchanged failure.
- Non-`bash` tool calls (edits, reads) do **not** reset the streak. Edit→test→edit→test with an
  unchanged failure is exactly the loop this catches; the stall detector never fires on it because
  every `edit_file` counts as progress.
- Returns `"stuck"` when `repeats >= limit`. `limit <= 0` disables the tracker entirely.

### 1.2 Flag, status, payload

- `--stuck-repeats N` on `run` and `resume` (default **4**; `0` disables). Independent of
  `--stall-turns`. No nudge — the point is to stop paying for turns.
- When the tracker returns `"stuck"`, the runner finishes the turn's remaining tool calls as usual
  (same rule as `finish` in a mixed turn), then ends the run with **`status: "stuck"`**. Exit code
  1, like every non-`completed` status. Resumable like `max_turns`/`stalled`.
- `stuck_on: {"command": <the bash command>, "output": <its result, capped at 4000 chars>,
  "repeats": N}` is set on `RunResult.extra` and therefore appears in the stdout JSON, in the
  `run_end` transcript event, and in `run.json`. On every other status `stuck_on` is `null`.
- `runs show` prints `stuck_on` in the plain view (it is in `run.json`, so the JSON dump already
  carries it; add it to `SHOW_FIELDS` so the summary lines show `stuck_on: <command>`), and the
  Markdown renderer's `## Result` shows the command and a fenced output tail.
- README status list, exit codes, flag table, `docs/transcript-schema.md` (`run_end.stuck_on`,
  `run.json.stuck_on`) updated.

## 2. End-of-run evidence on every payload (issue #33)

Three fields join the stdout JSON, `run_end`, and `run.json` **on every normal end-of-run path**
(whenever `runner.run()` returns a `RunResult`, whatever the status). Not only abnormal ends: on a
`completed` run they show the orchestrator "the last thing the worker checked failed, and it called
`finish` anyway" — the #30 written-blind case — for free.

- `files_changed`: list of repo-relative paths (strings), sorted, capped at 1000 entries with a
  companion boolean `files_changed_truncated`. **Docker mode:** computed in the container in the
  same export step that computes `diff_stat` and the patch (`git diff --cached --name-only`
  against the base commit after the export's `git add -A`) — no host git touches worker content.
  **Host mode:** `git diff --name-only <base_commit>` plus `git ls-files --others
  --exclude-standard`, run where `host_diff_stat`/`host_untracked` already run. `[]` when nothing
  changed or when the export never ran (setup failure paths do not carry it).
- `last_tool_result`: `{"tool": name, "args": raw args ≤500 chars, "result": result preview ≤2000
  chars}` for the last tool call the runner executed **other than `finish`**; `null` if no tool call
  was ever executed. Tracked in the loop from the same values already written to the `tool_result`
  transcript event.
- `last_assistant_text`: the model's last non-empty assistant text, capped at 2000 chars; `null` if
  none. Tracked in the loop from the same value written to the `assistant` transcript event.

`runs show --markdown` renders all three in `## Result` (a bullet list of files, a
`<details>` block for the last tool result, a blockquote for the last assistant text); the plain
`runs show` view shows `files_changed` (count + first few paths) via `SHOW_FIELDS`. README Machine
contract and `docs/transcript-schema.md` document them.

## 3. Edits that show what they changed; insert primitives (issue #34)

### 3.1 `describe_change`

`tools.py` gains one pure helper, `describe_change(path, old_text, new_text, *, verb) -> str`,
used by **both** sandbox backends (host `tools.py` functions and `DockerSandbox`'s in-container
`edit_file`/`write_file`, which already hold old and new text in Python). It returns:

```
Edited web/src/lib/api.ts: +2 -1 (removed 1 non-blank line)
--- a/web/src/lib/api.ts
+++ b/web/src/lib/api.ts
@@ -10,3 +10,4 @@
 context
-removed line
+added line
+added line
 context
```

- First line: `<Verb> <path>: +A -D` where A/D are added/deleted line counts from
  `difflib.SequenceMatcher` opcodes; the parenthetical `(removed N non-blank line(s))` appears only
  when N > 0 non-blank lines were deleted (a replaced non-blank line counts as removed).
- Then `difflib.unified_diff(old, new, fromfile="a/<path>", tofile="b/<path>", n=2)`, capped at
  **40 lines / 3000 chars**; when capped, the last line is `[diff truncated: N more lines]`.
- `write_file` on an existing file: same format with verb `Wrote` (`Wrote path: +A -D …`). On a new
  file: `Wrote N bytes to path (new file, M lines)` — no diff. The byte count stays in the string
  so existing tests that match `Wrote` still hold.
- `edit_file` keeps every existing `ERROR:` string unchanged; only the success string changes from
  `Edited {path}` to the block above.

### 3.2 `insert_before` / `insert_after`

Two new tools, `insert_before(path, anchor, text)` and `insert_after(path, anchor, text)`:

- `anchor` must occur **exactly once** in the file — the same rule and the same error text shape as
  `edit_file` (`ERROR: anchor occurs {count} times in {path}; it must occur exactly once. Include
  more surrounding context to make it unique.`).
- `text` is inserted as whole line(s): `insert_before` puts it immediately before the start of the
  line containing the anchor's first character; `insert_after` puts it immediately after the end of
  the line containing the anchor's last character (the anchor may span lines). A trailing newline is
  added to `text` when it lacks one; the anchor line itself is never modified.
- Implemented once as a pure transform in `tools.py` (`insert_text(text, anchor, insert, where)`),
  called from a shared read→transform→write path: `tools.py`'s host `edit_file` is refactored into
  `_transform_file(worktree, path, transform)` (all its symlink/regular-file/5 MB checks and error
  strings preserved), and `DockerSandbox.edit_file` into the same shape around its in-container
  read/write execs. `edit_file`, `insert_before`, and `insert_after` are three transforms over one
  path per backend. Success strings use `describe_change` with verb `Inserted into`.
- Registered in `builtin_tools.py` (`ToolSpec`s with `path`, `anchor`, `text` required; caps as
  `edit_file`), appended to `BUILTIN_SPECS` after `edit_file`, added to `_MUTATING_TOOLS` so the
  stall detector treats them as progress; the system prompt rule "Use edit_file or write_file for
  ALL file changes" becomes "Use edit_file, insert_before, insert_after or write_file for ALL file
  changes"; README's tool list ("seven tools" → nine), `docs/transcript-schema.md`'s `tool` enum,
  and the Security section's tool enumeration updated.

## 4. `--verify "<cmd>"`: the operator's gate, first-class (issue #35)

### 4.1 Flags

`--verify CMD` (default none), `--verify-rounds N` (default **1**), `--verify-timeout S` (default
**600**, clamped to the bash tool's 1–600 range) on `run`. `resume` accepts all three and inherits
the prior run's values from `run.json` when not given (as `--allow-commit` does).

### 4.2 Where it runs

Inside the runner, on the completion path — the `finish` tool or a plain-answer completion —
**before** `finalize()` (in docker mode the container is still alive; export has not happened).
The command runs through the same `sandbox.bash(command, timeout)` the tool uses, so the guardrail
denylist, `--network none`, the budget watchdog and the process reaper all apply; it is the same
environment the worker's own `bash` had.

- Result parsed like §1.1: passing iff the first line is `exit code: 0`; the exit code is the
  integer after `exit code: ` (or `null` for `ERROR:`/`BLOCKED:` results).
- **Pass** → `status: "completed"` as today.
- **Fail with a round left** (`rounds_used < verify_rounds`): a `verify` transcript event
  (`{"event": "verify", "round": k, "exit_code": E, "passed": false}`) is written, then a user
  message is appended and the loop continues:

  ```
  VERIFY FAILED (round k of N). The verification command
    <cmd>
  exited with code E. Output tail:
  <last 4000 chars>
  Fix the problem, then call finish(summary=...) again.
  ```

  The extra round spends ordinary turns/time against `--max-turns`/`--timeout`; if those trip first
  the run ends with their status as usual.
- **Fail with no round left** → **`status: "verify_failed"`**, exit code 1. Resumable like any
  non-`completed` status.
- `BudgetExceeded`/`SandboxError` raised by the verify call end the run with the existing
  `budget_exceeded`/`sandbox_error` statuses, exactly as a tool call would.
- A passing verify also writes a `verify` event with `"passed": true`.

### 4.3 Payload

`verify` on every normal end-of-run payload (stdout JSON, `run_end`, `run.json`): `null` when
`--verify` was not given; otherwise `{"command": CMD, "exit_code": E|null, "output_tail": ≤4000
chars, "rounds": rounds_used, "passed": bool}` for the **last** verify run. `runs show` plain view
shows `verify: passed|failed (exit E)` via `SHOW_FIELDS`; the Markdown `## Result` shows the
command, exit code and a fenced tail. README (flags, status list, exit codes, a short "Verifying a
run" paragraph under *Use*), `docs/transcript-schema.md` (`verify` event, `run_end.verify`,
`run.json.verify`) updated.

## 5. Sandbox deps: stock image + docs (issue #30)

- `docker/Dockerfile`: the apt line gains `jq uuid-runtime shellcheck curl` (the four the invoicr
  Entra bash suite needed). Nothing else changes (base image, `USER worker`, no entrypoint).
- Following the 0.7 precedent (`40e94e1`): `DEFAULT_IMAGE` → `ghcr.io/jimboschneider/dirtywork-worker:0.8`,
  `.github/workflows/ci.yml` docker-live tag → `:0.8`, README/site mentions → `:0.8`;
  `PINNED_DIGEST` stays `None` with the comment updated (the pin follows in 0.8.1 once
  `publish-image.yml` has pushed the multi-arch image on the `v0.8.0` release).
- README: under *Review a run*, a callout — "The worker cannot install dependencies in docker mode
  (`--network none`, no host dirs mounted); it can only run what the image ships. Always run the
  repo's own gate yourself on the exported worktree (or pass it as `--verify`)." — plus the Node
  trick (symlink `node_modules` into the worktree for the gate, remove it after; a `node_modules/`
  gitignore pattern does not match a symlink, so it shows as untracked if forgotten). Next to
  `--image` in the Machine contract, the derived-image recipe:

  ```Dockerfile
  FROM ghcr.io/jimboschneider/dirtywork-worker:0.8
  USER root
  RUN apt-get update && apt-get install -y --no-install-recommends <packages> \
      && rm -rf /var/lib/apt/lists/*
  USER worker
  ```

  with `docker build -t my-worker:0.8 .` and `--image my-worker:0.8` (custom images are never
  digest-pinned — say so). `docker/README.md` gets the same recipe and the new package list.

## 6. The review→fix loop (issue #31)

### 6.1 `dirtywork runs snapshot <slug>` — a commit that never runs filters or hooks

A new `workspace.snapshot_worktree(worktree, branch, message) -> str` builds a commit of the
worktree's current content on the run's branch **without** `git add`/`git commit`, honoring the
rule that the only host porcelain dirtywork runs on worker content is `read-tree HEAD`:

1. Walk the worktree in Python (`os.lstat`, skip the top-level `.git` file), then apply the repo's
   ignore rules like `git add -A` would (`git check-ignore`, config-neutral). Regular files → blobs via one
   `git hash-object -w --no-filters --stdin-paths` (never applies clean filters or `.gitattributes`;
   paths are given via stdin, so filenames with newlines are refused with an error rather than
   mis-hashed); symlinks → the link *target string* hashed with `hash-object -w --stdin` and
   recorded as mode `120000` (never followed, escaping or not); executable bit → mode `100755`,
   else `100644`; other file types skipped and counted.
2. Feed `<mode> SP <sha> TAB <path>` lines to `git update-index --index-info` against a temporary
   index (`GIT_INDEX_FILE` in the run dir), `git write-tree`, `git commit-tree <tree> -p
   <branch-head> -m <message>` with `GIT_AUTHOR_NAME=dirtywork`, `GIT_AUTHOR_EMAIL=dirtywork@localhost`
   (committer likewise), `git update-ref refs/heads/<branch> <commit> <old-head>`, then the
   already-sanctioned `host_read_tree(worktree)` so the worktree's index matches its new HEAD and
   `git status` is clean. All git invocations use the same config-neutral env as `host_read_tree`
   (`GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, `-c core.hooksPath=/dev/null`,
   `-c core.fsmonitor=false`, plus `-c commit.gpgsign=false`).
3. Returns the new commit sha. If the tree equals the branch head's tree (nothing to snapshot),
   returns `None` and creates no commit.

`dirtywork runs snapshot <slug>` calls it with message `wip: dirtywork run <slug>`, prints
`snapshot <sha> on <branch>` (or `nothing to snapshot`), exit 0; refuses (exit 2) when the run is
still running with a live pid, when the worktree is missing or is not a linked worktree of the
run's repo (`resume.worktree_belongs_to_repo`), or when a pre-resume stash exists beside it. It
works in both sandbox modes; the branch is the run's `branch` from `run.json`.

### 6.2 `--branch-from @<slug>`

In `run` preflight, a `--branch-from` value beginning with `@` is a run reference: resolve the run
dir (`resume.resolve_run_dir`), load `run.json`; unknown slug → exit 2 `error: unknown run '<slug>'
(no run dir under ~/.dirtywork/runs)`; missing branch → exit 2. If the run's worktree exists and is
dirty (`git status --porcelain` non-empty, run with the config-neutral env), call
`snapshot_worktree` first and print `snapshot <sha> on <branch> (from @<slug>)` on stderr; then
proceed with `branch_from = <that branch>`. `run_start.branch_from` records the resolved branch
name; `run.json` also records `branch_from_run: <slug>`. Nothing else about `run` changes.

### 6.3 `resume --feedback`

- `resume` gains `--feedback TEXT` and `--feedback-file PATH` (mutually exclusive; the file is read
  as UTF-8, capped at 64 000 chars with exit 2 beyond).
- `build_resume_task(prior_task, prior_status, prior_turns, transcript_tail, feedback=None)`: with
  feedback, the marker block becomes:

  ```
  {prior_task}

  --- RESUMED RUN: REVIEW FEEDBACK ---
  This run continues an earlier run that ended with status '{status}' after {turns} turns.
  A reviewer read that run's work and sent this feedback:

  {feedback}

  The worktree already contains the earlier run's work: inspect it with `git status` and
  `git diff` first, then apply the feedback. Make no other changes.
  The last events of the earlier run were:
  {transcript_tail}
  When the task is complete, call finish(summary=...).
  ```

  Both markers (`--- RESUMED RUN ---` and `--- RESUMED RUN: REVIEW FEEDBACK ---`) are stripped from
  `prior_task` before building, so re-resuming never accumulates blocks.
- Gate: resuming a run whose status is `completed` **without** `--feedback` is refused (exit 2,
  `error: run '<slug>' ended 'completed'; pass --feedback to continue it with new instructions`).
  Every other status resumes as before, with or without feedback.
- The feedback text is recorded in the new run's `run.json` (`feedback`) and `run_start`
  (`feedback`); the README *Resuming a run* section gains a "Sending review feedback" paragraph and
  the Machine contract lists the flags.

## 7. Cross-cutting

- **Contract:** stdout JSON gains `stuck_on`, `files_changed`, `files_changed_truncated`,
  `last_tool_result`, `last_assistant_text`, `verify` on every normal end-of-run payload; the
  `status` enum gains `stuck` and `verify_failed` (both exit 1); flags `--stuck-repeats`,
  `--verify`, `--verify-rounds`, `--verify-timeout` (run + resume), `--feedback`/`--feedback-file`
  (resume), `--branch-from @<slug>` (run); `runs snapshot <slug>`; tools `insert_before`,
  `insert_after`; transcript event `verify`; `run.json` fields `stuck_on`, `files_changed`,
  `files_changed_truncated`, `last_tool_result`, `last_assistant_text`, `verify`, `feedback`,
  `branch_from_run`. All additive; `schema_version` stays 2. Existing callers build
  `argparse.Namespace` without the new attributes, so every new flag is read with
  `getattr(args, name, default)`.
- **Tests:** TDD per task; new tests live in the existing modules (`test_runner.py`,
  `test_tools_files.py`, `test_docker_sandbox.py`, `test_builtin_tools.py`, `test_toolspec.py`,
  `test_main.py`, `test_resume.py`, `test_runs.py`, `test_workspace.py`, `test_export_flow.py`,
  `test_docker_image.py`, `test_transcript_schema.py`); a snapshot test builds a real repo in
  `tmp_path` with a symlink and an executable file and asserts the commit's tree via `git ls-tree`,
  and that no filter or hook ran (a `.gitattributes` with `filter=x` and a hook that would fail).
  Baseline may only rise. `python3 -m pytest -q` green after every task.
- **Version:** 0.8.0 (`pyproject.toml`, `dirtywork/__init__.py`), image tag `:0.8` per §5;
  release notes list the six issues.
- **Docs touched:** README (Use, Resuming, Inspecting/`runs`, How a run works, Security tool list,
  Machine contract, Troubleshooting), `docs/transcript-schema.md`, `docker/README.md`.
