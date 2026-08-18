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
| `verdict` | verdict | written by `dirtywork runs verdict`: `"accept"` \| `"reject"` \| `"cleanup"` |
| `note` | verdict | `--note` text, or null |
| `verdict_at` | verdict | UTC ISO-8601 |
| `review_seconds` | verdict | `--review-seconds` as given, or null |
| `time_to_verdict_s` | verdict | seconds from `ended` to `verdict_at`; null when the run has no `ended` yet |

Rows marked *verdict* are added post hoc by `dirtywork runs verdict <slug> …`
(a merge-update; no existing key is dropped) and are absent until then.

`dirtywork runs show <slug>` prints this file alongside a tool-call timeline
reconstructed from the transcript.
