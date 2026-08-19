# Transcript schema

`dirtywork` writes one JSON object per line to
`~/.dirtywork/runs/<slug>/transcript.jsonl` (`tail -f` friendly — each line is
flushed immediately). Every line has at least `ts` (UTC ISO-8601) and `event`
(one of the eight event names below). `schema_version` marks the overall
version and appears once, on `run_start`, and again in the CLI's stdout JSON
and in `run.json` — not on every line.

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

## Events

### `run_start`

One per run, always the first line.

| Field | v1 | v2 | Type | Notes |
|---|---|---|---|---|
| `ts` | ✓ | ✓ | string | UTC ISO-8601 |
| `event` | ✓ | ✓ | `"run_start"` | |
| `task` | ✓ | ✓ | string | the task text; on a resumed run it also carries the `--- RESUMED RUN ---` block, or the `--- RESUMED RUN: REVIEW FEEDBACK ---` block when `--feedback` was given. Both markers are stripped from the prior task before a new block is built, so resuming a resume never stacks them |
| `model` | ✓ | ✓ | string | |
| `max_turns` | ✓ | ✓ | integer | |
| `timeout` | ✓ | ✓ | integer | seconds, whole-run wall clock |
| `repo` | ✓ | ✓ | string | absolute path |
| `worktree` | ✓ | ✓ | string | absolute path |
| `schema_version` | | ✓ | `2` | present from v2 onward; its absence marks v1 |
| `context_window` | | ✓ | integer | tokens; the resolved value (`--context-window` > `DIRTYWORK_CONTEXT_WINDOW` > what the server reports it loaded the model with > the provider's static table > 32768) |
| `context_window_source` | | ✓ | string \| null | 0.9: which of those steps answered — `flag`, `env`, `provider:<name>:server` (the server's own report, e.g. LM Studio's `loaded_context_length`), `provider:<name>` (the built-in table), or `default` (nothing knew; the "assuming 32768 tokens" warning fires only for this one). `null` only for a `Runner` constructed directly without a source |
| `base_commit` | | ✓ | string | resolved commit the worktree branched from |
| `branch` | | ✓ | string | `dirtywork/<slug>` |
| `branch_from` | | ✓ | string \| null | the ref the worktree was branched from, or null for repo HEAD. For `--branch-from @<slug>` this is the **resolved branch name** of that run, not the `@<slug>` text; `run.json`'s `branch_from_run` records the slug |
| `base_url` | | ✓ | string | the provider endpoint actually used (after the per-provider default is applied) |
| `dirtywork_version` | | ✓ | string | `dirtywork.__version__` |
| `temperature` | | ✓ | number \| null | omitted from the request when null |
| `provider` | | ✓ | `"openai"` \| `"anthropic"` | |
| `resumed_from` | | ✓ | string \| null | slug of the run this one continues |
| `feedback` | | ✓ | string \| null | 0.8: `resume --feedback`/`--feedback-file` text, verbatim (max 64 000 chars); null on a fresh run or a resume without feedback |
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
| `tool` | ✓ | ✓ | string | tool name — one of `read_file`, `write_file`, `edit_file`, `apply_edits`, `insert_before`, `insert_after`, `list_dir`, `grep`, `bash`, `finish` (`insert_before`/`insert_after` are v2, added in 0.8; `apply_edits` in 0.9); `""` for a discarded malformed entry |
| `args` | ✓ | ✓ | string | the raw JSON argument string, capped at 500 chars; `""` for a discarded malformed entry |
| `result` | ✓ | ✓ | string | the tool's result, trimmed per the tool's `Caps.transcript` setting. All built-in tools declare `preview`, which caps the record at 2000 chars; the registry also supports `full` and `none`, unused by any shipped tool. Since 0.8 a successful `edit_file`/`write_file` result is `<Verb> <path>: +A -D [(removed N non-blank lines)]` followed by a unified diff (capped at 40 lines / 3000 chars, then `[diff truncated: N more lines]`); `write_file` on a new file returns `Wrote N bytes to <path> (new file, M lines)` with no diff. 0.9's `apply_edits` uses the same shape with the verb `Applied N edits to` (`Applied 1 edit to` for a single edit). When either side of the edit exceeds 20000 lines, the diff itself is never computed (it is quadratic-ish on files with popular repeated lines) — the result is just `<Verb> <path>: <N> lines (diff omitted: file too large)`. An in-place tool whose RESULT would exceed the 5 MB write limit returns `ERROR: result is <n> bytes, over the <limit>-byte write limit; nothing was written` on both backends (0.9) |
| `timed_out` | | ✓ | boolean | 0.9: `true` on a `bash` tool result whose command hit its timeout. **Sparse** — the key is absent, not `false`, on every other result, including a `grep` timeout (a different wording and a different meaning: the harness's search, not the worker's command) and the `--verify` command (not a tool call, so it produces no `tool_result` at all; its outcome is in `verify`) |

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
| `kind` | | ✓ | string | `truncated` (the reply hit the token limit), `empty` (no tool call and no answer), `text_tool_call` (a tool call written as prose instead of through the tools API), `stall` (no progress for `--stall-turns // 2` turns), `timeout` (0.9: at least one `bash` command timed out on this turn — exactly one per turn however many timed out, and only on a turn that continues; a timeout is not a `FailureTracker` event) |
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

One per run, always the last line. Written by the runner on every terminal
status, and by the CLI's failure paths when the runner never returned (in that
case it carries `status`, `error` and the rows marked **always** below —
run-level fields that are known even when the agent loop never started).

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
| `stuck_on` | | ✓ | object \| null | 0.8: `{command, output, repeats}` for the failing bash call that ended the run as `stuck` (`output` capped at 4000 chars); `null` on every other status |
| `files_changed` | | ✓ | list | 0.8: repo-relative paths the run changed, sorted, capped at 1000. Docker mode: `git diff --cached --name-only <base_commit>` in the container right after the export's `git add -A`. Host mode: `git diff --name-only <base_commit>` plus `git ls-files --others --exclude-standard`. `[]` when nothing changed or the export never ran |
| `files_changed_truncated` | | ✓ | boolean | 0.8: true when `files_changed` was cut at the 1000-path cap |
| `last_tool_result` | | ✓ | object \| null | 0.8: `{tool, args, result}` for the last tool call the runner executed other than `finish` (`args` ≤500 chars, `result` ≤2000 chars); `null` if no tool ever ran |
| `last_assistant_text` | | ✓ | string \| null | 0.8: the model's last non-empty assistant text, capped at 2000 chars; `null` if there was none |
| `verify` | | ✓ | object \| null | 0.8: `{command, exit_code, output_tail, rounds, passed}` for the LAST `--verify` execution (`output_tail` capped at 4000 chars); `null` when `--verify` was not given |
| `trimmed_turns` | | ✓ | integer | **always** — 0.9: the number of turns on which the runner had to replace at least one tool result with `[result trimmed — re-run the tool if needed]` to fit the char budget. A result already trimmed is never recounted, and the final failing trim (the one that ends the run `context_exhausted`) counts if it trimmed anything. `0` on a run that never trimmed, and on the two failure paths where the runner never returned |
| `context_window_source` | | ✓ | string | **always** — 0.9: the same value as `run_start.context_window_source`, repeated at the end so a consumer that reads only the last line still knows where the window came from |
| `timeouts` | | ✓ | integer | **always** — 0.9: how many `bash` TOOL CALLS timed out during the run (per call, not per turn). `grep` timeouts and the `--verify` command are excluded. `0` on a run where nothing timed out, and on the two failure paths where the runner never returned |

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
| `stuck` | | ✓ | 0.8: the same **failing** bash command ran `--stuck-repeats` times in a row (fingerprint as the stall detector's: timings/shas stripped); edits in between do not reset the streak, a passing run does |
| `verify_failed` | | ✓ | 0.8: the worker declared itself done, but the `--verify` command exited non-zero on its last allowed round |
| `budget_exceeded` | | ✓ | worktree size/file budget or host disk floor breached |
| `sandbox_error` | | ✓ | the sandbox backend failed in a way the run cannot continue past |
| `export_failed` | | ✓ | the run itself completed, but the validated export of the worker's files did not |

## `schema_version` and the stdout JSON contract

`schema_version: 2` also appears in the CLI's single stdout JSON object
(`dirtywork run`'s machine contract — see
[docs/machine-contract.md](machine-contract.md)). Its fields: `schema_version`, `status`, `worktree`, `branch`,
`transcript`, `turns`, `usage`, `final_message`, `run_dir`, `provider`,
`base_commit`, `resumed_from`, `finalize_error`, `watchdog_violation`,
`watchdog_violation_kind`, `stuck_on`, `files_changed`,
`files_changed_truncated`, `last_tool_result`, `last_assistant_text`, `verify`,
`trimmed_turns`, `timeouts`, `context_window_source`,
and `export_status` on the exception-recovery path.
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
| `context_window_source` | start, end | 0.9: `flag` \| `env` \| `provider:<name>:server` \| `provider:<name>` \| `default` — which precedence step produced `context_window`. Written at start and repeated at end (including on the two failure paths) so the plain `dirtywork runs show`, which reads only `run.json`, never shows `-` |
| `resumed_from` | start | slug of the run this one continues, or null |
| `resumed_by` | — | written onto the **prior** run's `run.json` when a resume starts |
| `branch_from_run` | start | 0.8: the slug `--branch-from @<slug>` named, or null. The resolved branch itself is `run_start.branch_from` |
| `feedback` | start | 0.8: `resume --feedback`/`--feedback-file` text, or null |
| `verify_command` | start | 0.8: `--verify` as given, or null. `dirtywork resume` inherits this (and `verify_rounds`/`verify_timeout` below) when not given, so the gate survives a run that ended before verify ever ran (`max_turns`/`stalled`/`stuck`/`timeout`/`budget_exceeded`) |
| `verify_rounds` | start | 0.8: `--verify-rounds` as given, else `1` |
| `verify_timeout` | start | 0.8: `--verify-timeout` as given (clamped to 1-600 by the runner), else `600` |
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
| `diff_stat` | end, export | rewritten by `dirtywork runs export` when it succeeds |
| `export_status` | end, export | `"ok"` \| `"export_failed: …"` \| `"n/a"`; rewritten by `dirtywork runs export` |
| `patch_path` | end, export | rewritten by `dirtywork runs export` |
| `worktree_bytes` | end (docker), or export | sampled worktree size from `budget.measure_worktree`; absent in host mode until an export is run |
| `worktree_files` | end (docker), or export | sampled worktree entry count; absent in host mode until an export is run |
| `escaping_symlinks` | end (docker), or export | symlinks whose target is absolute or escapes the worktree — never followed, always reported |
| `dropped_git_entries` | end (docker), or export | `.git`-named entries the export refused to add |
| `finalize_error` | end | |
| `watchdog_violation` | end | |
| `watchdog_violation_kind` | end | |
| `stuck_on` | end | 0.8: `{command, output, repeats}` when the run ended `stuck`, else null |
| `files_changed` | end, export | 0.8: sorted repo-relative paths the run changed, capped at 1000; rewritten by `dirtywork runs export` |
| `files_changed_truncated` | end, export | 0.8: true when the 1000-path cap cut the list |
| `last_tool_result` | end | 0.8: `{tool, args, result}` for the last non-`finish` tool call, or null |
| `last_assistant_text` | end | 0.8: the last non-empty assistant text (≤2000 chars), or null |
| `verify` | end | 0.8: `{command, exit_code, output_tail, rounds, passed}` for the last `--verify` execution, or null (null whenever verify never ran, even if `--verify` was given — see `verify_command` above, which `dirtywork resume` reads from instead) |
| `trimmed_turns` | end | 0.9: turns on which at least one tool result was trimmed to fit the context budget; `0` when nothing was trimmed |
| `timeouts` | end | 0.9: how many `bash` tool calls timed out; `0` when none did |
| `allow_commit` | start | (bool) records whether the run's system prompt told the worker to commit as it went (`--allow-commit`, host mode only — see [docs/machine-contract.md](machine-contract.md)). A run that predates the flag has no such key. |
| `verdict` | verdict | written by `dirtywork runs verdict`: `"accept"` \| `"reject"` \| `"cleanup"` |
| `note` | verdict | `--note` text, or null |
| `verdict_at` | verdict | UTC ISO-8601 |
| `review_seconds` | verdict | `--review-seconds` as given, or null |
| `time_to_verdict_s` | verdict | seconds from `ended` to `verdict_at`; null when the run has no `ended` yet |

Rows marked *verdict* are added post hoc by `dirtywork runs verdict <slug> …`
(a merge-update; no existing key is dropped) and are absent until then.

`dirtywork runs snapshot <slug>` writes no `run.json` field: it reads `branch`,
`worktree`, `repo`, `status` and `host_pid` and commits the worktree's current
content onto that branch with git plumbing only. The run directory is unchanged.

`dirtywork runs show <slug>` prints this file alongside a tool-call timeline
reconstructed from the transcript. `dirtywork runs show <slug> --markdown
[--out FILE]` exports those same two sources as one Markdown document —
`run.json` for the header block and the `## Result` section (the header's own
`task` field is only a one-line preview; a `## Task` section holds the full
task text), the transcript for one `### Turn N` per `assistant` event with its
`tool_result`s as `<details>` blocks (capped at the same 2000-char preview the
transcript itself applies) and its `nudge`/`guardrail_block`/`sandbox_reset`
events as blockquote callouts. Token counts in the header come from
`run_end.usage`, and the final message from the `finish` call's `summary`,
because `run.json` records neither.
