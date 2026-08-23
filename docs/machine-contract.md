# Machine contract

`dirtywork` is built to be driven by another agent (Claude Code) rather than
read by a human — the primary consumer parses stdout, not the terminal.

**Flags:**

```
dirtywork run --repo <path> "<task>"
    [--model qwen/qwen3-coder-next]   # or mistralai/devstral-small-2-2512
    [--branch-from <ref>|@<slug>]     # default: repo HEAD; @<slug> = an earlier run's branch
    [--max-turns 40]
    [--stall-turns 12]                # end as `stalled` after N no-progress turns; 0 disables
    [--stuck-repeats 4]               # end as `stuck` after N identical failing bash runs; 0 disables
    [--verify "<cmd>"]                # run this in the sandbox on completion; non-zero → `verify_failed`
    [--verify-rounds 1]               # fix rounds after a failed --verify (0 = verify once, no retry)
    [--verify-timeout 600]            # seconds per --verify run, clamped to 1-600
    [--context-window <tokens>]       # default: the server's loaded window, else the
                                      # built-in table, else 32768 (+ stderr warning)
    [--timeout 1800]                  # whole-run wall clock, seconds
    [--temperature <f>]               # omitted by default → server preset
    [--max-tokens 8192]               # per-reply output cap; must be < the context window
    [--provider openai|anthropic]     # default: openai; anthropic needs ANTHROPIC_API_KEY
    [--base-url <url>]                # default depends on --provider (LM Studio for openai,
                                      # https://api.anthropic.com for anthropic)
    [--max-worktree-mb 2048]
    [--max-worktree-files 200000]
    [--sandbox docker|none]           # default: docker
    [--image ghcr.io/jimboschneider/dirtywork-worker:0.9]  # docker mode only
    [--allow-network]                 # docker mode only; default --network none
    [--memory 4g]                     # docker mode only
    [--cpus 2]                        # docker mode only
    [--tmp-size 1g]                   # docker mode only
    [--gitdir-size 512m]              # docker mode only
    [--min-free-mb 2048]              # docker mode only; host free-space floor
    [--keep-volume]                   # docker mode only; skip volume cleanup
    [--max-patch-mb 10]               # docker mode only; diff.patch cap
    [--allow-commit]                  # host mode only; worker commits its own work
```

```
dirtywork resume <slug | run-dir>     # same flags as run, minus --repo/--branch-from/--sandbox/<task>;
    [--model <m>]                     # defaults to the earlier run's model; --image defaults to its image
    [--feedback "<text>"]             # reviewer instructions; REQUIRED to resume a `completed` run
    [--feedback-file <path>]          # same, read from a UTF-8 file (max 64000 chars)
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
- `--verify CMD` / `--verify-rounds N` / `--verify-timeout S` — see
  [Verifying a run](operating.md#verifying-a-run). `--verify-rounds` counts **fix rounds
  after a failed verify** — the command may run N+1 times; `0` verifies once and
  ends the run either way. `dirtywork resume` inherits all three — the command,
  the rounds, and the timeout — from the run it continues (recorded in `run.json`
  at run start, so this works even when the prior run ended before verify ever
  ran, e.g. `max_turns`/`stalled`/`stuck`/`timeout`/`budget_exceeded`); an
  explicit flag on `resume` overrides the inherited value.
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

- `--max-tokens` (default 8192) — the per-reply output cap sent to the provider
  on every request, and subtracted from the context window before the prompt
  budget is computed (`(window - max_tokens) * 0.75 * 4` chars), so a long reply
  can no longer run off the end of a window the prompt already filled. Preflight
  refuses `--max-tokens` at or above the window with
  `--max-tokens <N> must be smaller than the <W>-token context window` (exit 2).
  The rule is flat, with no small-window exemption: a server-reported context
  window at or below 8192 refuses every run until `--max-tokens` is passed and
  lowered below it. Recorded on `run_start` and in `run.json`; **not** echoed on the stdout payload
  (it is configuration, not evidence). `dirtywork resume` inherits it; a run
  recorded before 0.10 has no value to inherit and gets the 8192 default, which
  raises its effective cap from the adapters' old 4096. Pass `--max-tokens 4096`
  for models that cap output there — some older Claude models reject a larger
  value outright.

- `--allow-commit` (host mode only) — replaces the prompt's "leave all changes
  uncommitted for review" rule with "commit your work in small conventional
  commits as you go", so the run's branch comes back as real history instead of
  a dirty worktree. Rejected in preflight with `--sandbox docker`: the export
  carries files, not commits (its archive can never contain a `.git` entry), so
  a container's commits could not reach the host anyway. `dirtywork resume`
  inherits the setting from the run it continues.

**Tools:** the worker is advertised exactly eleven tools, in this order. They are
not configurable; a run's tool surface is the same in host and docker mode.

- `read_file(path, offset=0, limit=400)` — numbered lines; files over ~5 MB and
  non-regular files are refused.
- `write_file(path, content)` — create or overwrite; parent directories are
  created. The result echoes a capped unified diff (a new file reports its byte
  and line count instead).
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
container. Since 0.10 "nothing was written" also covers a failure **during**
the write: every host write and every container write is staged in a sibling
temp file and promoted with an atomic rename, so an I/O error or a kill leaves
the target byte-identical. Two branches (host mode only) keep the old in-place
behaviour and are named here rather than hidden: a target with more than one
hard link (a hardlink is *meant* to see the write through the shared inode)
and a target in a directory the process cannot write (a rename is impossible
there). The promote changes the file's inode, so a worker process holding the
old file open keeps reading the old content until it re-opens. In docker mode
there is no fd fallback: a writable file inside an unwritable directory
refuses (Permission denied) where host mode writes in place.

**stdout:** on any run that gets past preflight, exactly one JSON object is
printed to stdout (nothing else goes to stdout):

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
  },
  "trimmed_turns": 0,
  "timeouts": 0,
  "context_window_source": "provider:openai:server"
}
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

`status` is one of: `completed`, `max_turns`, `timeout`, `stalled`, `stuck`,
`verify_failed`, `context_exhausted`, `model_error`, `interrupted`,
`budget_exceeded`, `sandbox_error`, `export_failed`. When the run fails before a `RunResult`
exists — the LLM client raises, post-worktree setup fails (e.g. the
transcript can't be created), or any other exception escapes the run
(status `model_error` in every case) — `turns` is `null` and `usage` is
`{}`, but `status`, `worktree`, `branch`, `transcript`, and `run_dir` are
still populated so the worktree and run directory can be located for
salvage.

`base_commit` and `provider` (`"openai"` or `"anthropic"`) are present on
every post-preflight payload. `resumed_from` is
the slug of the run this one continued, or `null` if this was a fresh run.
`finalize_error`, `watchdog_violation` and `watchdog_violation_kind` are
added on the normal end-of-run path — i.e. whenever `runner.run()` returns a
result, `completed` or not — normally `null`; see `run_end` below for what
each means. `stuck_on`, `files_changed`, `files_changed_truncated`,
`last_tool_result`, `last_assistant_text`, `verify`, `trimmed_turns`,
`timeouts` and `context_window_source` are present on
**every** payload (`null`/`[]`/`false`/`0` when they do not apply) — including
the two paths below where `runner.run()` never returns, where they carry
those same defaults rather than being omitted. Four of those 0.8 keys
are there so a run that ends with an empty `final_message` is still
triageable without opening the transcript: what it changed, what it last ran
and what it last said. On a `completed` run they are just as useful — "the
last thing the worker checked failed, and it called `finish` anyway" reads
straight off `last_tool_result`.
The two
paths where `runner.run()` never returns (sandbox setup fails before it
starts, or an exception escapes the loop and is caught in `main()`) report
`base_commit` and `resumed_from`, the six 0.8 evidence keys above (as their
null/empty defaults) **and** the three 0.9 contract keys — `trimmed_turns` and
`timeouts` as `0`, `context_window_source` as the value preflight actually
resolved — plus `export_status` too if a docker `finalize()` ran
during that exception recovery — `finalize_error`, `watchdog_violation` and
`watchdog_violation_kind` are not present on these two paths, since they
never got far enough to know.

**Exit codes:**

- `0` — `completed`.
- `1` — any non-`completed` status (`max_turns`, `timeout`, `stalled`,
  `stuck`, `verify_failed`, `context_exhausted`, `model_error`, `interrupted`,
  `budget_exceeded`, `sandbox_error`, `export_failed`); the worktree and branch are kept for
  salvage/review. `main` catches every `Exception` the run raises (not
  just ones the runner itself converts to a status) and reports
  it as `model_error` via the same JSON contract, so a post-preflight run
  never tracebacks. (Ctrl-C is a `KeyboardInterrupt`, a `BaseException`, not caught
  here — but the run loop itself already converts in-loop Ctrl-C to status
  `interrupted` before it would reach this point.)
- `2` — preflight or environment error (LM Studio unreachable, model not
  loaded, `--repo` not a git repo, etc.); nothing is created.

All progress (transcript path, worktree path, `error:`-prefixed messages) is
written to stderr; watch a live run with `tail -f` on the transcript path.

Full field-by-field schema, including every v1→v2 addition and the
`run.json` field list: [`docs/transcript-schema.md`](transcript-schema.md).

**Transcript events** (JSONL, one per line): `run_start` (task, repo, model,
config, `schema_version: 2`, plus provenance: `worktree`, `base_commit`,
`branch`, `branch_from`, `base_url`, `dirtywork_version`, `temperature`,
`sandbox` — the docker settings dict, or `"none"` — and `provider`,
`context_window`, `context_window_source`, `resumed_from`),
`assistant` (text + tool calls — text capped at 64 000 chars in the
transcript only, the full text is still sent to the model), `tool_result`
(truncated), `guardrail_block`, `nudge` (`{"event": "nudge", "kind":
"truncated|empty|text_tool_call|stall", "turn": N}`), `sandbox_reset`
(docker mode: the container was reset — reason), and `run_end` (status, turns,
duration, cumulative
usage, plus the run's artifacts: in host mode `diff_stat` — `git diff
--stat` against the base commit, tracked changes only — and `untracked` —
`git status --porcelain` `??` entries — each capped at 64 000 chars; in
docker mode `diff_stat` (which already includes new files, since the
export stages everything first), `untracked` (always `""`), `patch_path`,
`worktree_bytes`, `worktree_files`, `escaping_symlinks`,
`dropped_git_entries`, `export_status`, `watchdog_violation` (docker mode;
null unless the watchdog killed the container), `watchdog_violation_kind`
(set alongside `watchdog_violation`: `"budget"` for a worktree-size or
host-disk-floor breach, `"sandbox_error"` for the watchdog's own
worktree-sampling exec failing twice; otherwise `null`), and
`finalize_error` (set when the finalize/export step itself raised an
exception after the agent loop otherwise finished; `null` normally)).
A `finish(summary=...)` call appears in the transcript as an ordinary tool call in its `assistant` event followed by a `tool_result` event whose `result` is `run finished`; the summary becomes the run's `final_message`.

The docker settings dict (`run_start`'s `sandbox`, and the same fields in
`run.json`) includes `image` (the `--image` argument as given),
`image_digest` (the registry digest from `RepoDigests`, or `null` for a
locally-built image that was never pushed/pulled) — provenance only — and
`image_pinned` (`true` only when `--image` was left at its default AND
`PINNED_DIGEST` was enforced against a pulled default image; `false` for a
custom `--image` — never pinned — or a locally built/loaded default image,
which only warns). `run.json` also records the run's key fields: `task`,
`model`, `context_window`, `resumed_from`, and `turns` (at the end); when a
run is resumed, the earlier run's `resumed_by` field records the slug of the new run that continued it.
The container itself always runs from the image's local
content-addressed Id, never a registry digest, so a run can never trigger a
network pull.

