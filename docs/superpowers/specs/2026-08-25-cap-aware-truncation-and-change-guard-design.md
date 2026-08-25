# Cap-aware truncation, the truncation budget, and the change guard (#65, #66)

**Date:** 2026-08-25
**Status:** Design v4 — approach B (cap-aware nudges + truncation budget + worktree fingerprint)
chosen by the owner 2026-08-25 ≈17:25 CDT with the four defaults in §0. **Owner review of v3
(18:31 CDT): architecture sound, none of the six flagged choices vetoed, four items to resolve
and two clarifications — all folded in v4 (the first row of §0.1); approved for `writing-plans`
once folded.** v2 folded a six-lens red-team with two-refuter adversarial verification
(66 agents, 17:35–17:51 CDT): 61 findings (4 Blocker, 30 Important, 27 Minor); 30
Blocker/Important verified (29 kept, 1 refuted — a claim that no docker-level resume test exists;
`test_docker_live_resume_seeds_worktree_keeps_branch_and_exports` does), the 4 unverified
Blocker/Important and the Minors read and folded by the author. §0.1 maps every fold. The four
Blockers all concerned §3.1's chunk basis and a circular import; both are rewritten. v3 folds the
P4 probe of the v2 script (§1.5, passed on every case) and a closure pass (2 agents, 18:00–18:12
CDT: 15 consistency and 16 fold-coverage findings, 0 Blocker; all applied — the
`soak_harvest` pattern is now stated in full, `changed_reason`'s lifecycle and the stderr line's
owner are fixed, the F5 rule makes the original S2 shape an explicit fail, C1 gained a failure
consequence and the target-size gate, and the script gained an `EXIT` trap). Nothing in v3
changes a decision of §0.
**Origin:** issues #65 (soak finding S2) and #66 (S3), milestone **1.0.0 — contract freeze**, plus
the six S14 data points the #61 build handed over (ledger
`docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md`, "#61" section): four zero-change
`finish`es on feedback resumes (W2a, W3, W4b, W8b), one zero-edit run (W7 run 1, 60 turns), one
resume that finished while skipping its only item (W10). Evidence: the F5 rows of `soak-B.jsonl` /
`soak-B2.jsonl`, the six run directories under `~/.dirtywork/runs/issue-61-*`, and the live probes
of 2026-08-25 recorded in §1.5.
**Parent specs:** `2026-08-20-v1rc-large-writes-atomic-ollama-design.md` (§1.3 truncated tool
calls, `truncated_call_result`, `_recovered_path`), `2026-08-23-harness-followups-after-tool-results-design.md`
(#60: `deliver()` carriers, `follow_up`, `via`), `2026-08-18-run-evidence-and-review-loop-design.md`
(§4 `--verify`, `resume --feedback`), `2026-08-19-tools-context-timeouts-design.md` (§4.3 timeout
nudge, `timeouts` on `run_end`), `2026-08-17-sp2.5-harness-robustness-design.md` (the trackers),
`2026-08-25-sandbox-strays-gitfile-and-reset-notices-design.md` (#61: the gitfile layout and the
`/gitdir` alternates the fingerprint runs under; the `:(exclude,literal)` recipe of its §4.4; the
§6.2 form this spec's §5.2 copies).
Ships in **dirtywork 1.0.0**. Stdlib-only, Python 3.9 floor (`from __future__ import
annotations` in every new module), `schema_version` stays 2: every addition below is a new
`nudge.kind` value, a new `status` value, or a new `run_end` / `run.json` / stdout-JSON field —
never a removed or renamed one, no new event name, no new `via` value.

## 0. The owner's decisions and where each is resolved

| # | Decision (2026-08-25, approach question) | Resolved in |
|---|---|---|
| 1 | Approach **B**: cap-aware nudges + cumulative truncation stop + tree-hash fingerprint at start, on every completion path and every K turns; a zero-change finish is rejected once, then ends `unchanged` on a feedback resume | §3, §4 |
| 2 | A zero-change finish on a run **without** feedback (fresh run, plain resume) is, after the one rejection round, verified as today (`completed` when verify passes) with `run_end.changed = false`. **Cost stated (v2):** every zero-change completion — including a report-only task and the `_first_run` shape behind 17 CLI-test callers — now takes two turns (§4.3, §7) | §4.3 |
| 3 | The truncation stop is a constant (`MAX_TRUNCATED_REPLIES = 6`) ending the run `model_error` with a reason that names the cap and the fix; bench parses it as `abort_kind = truncated`; no flag, no new status | §3.3 |
| 4 | The nudge-only "no file changed in K turns" guard ships, K = 10, never aborts; **v2 states it is diagnostic** (it produces `changed` evidence and a nudge; it is not shown to fix the W7 shape) | §4.4, §8 |

### 0.1 Red-team fold (v2)

| finding (lens) | fold |
|---|---|
| **B** chunk basis measures the visible reply; on LM Studio a cut tool call is dropped and only the prose preamble survives, so the target collapsed to the 200-char floor and §8's F5 recovery could not hold (model-behaviour ×2, acceptance) | §3.1 rewritten: basis = the cap unless the cut call's raw arguments are actually present; per-line ratio only from raw arguments; the text path says how much the harness *received*, never "after about N characters of content"; worked values recomputed |
| **B** third worked value contradicted the code (200/5, not 1024/17) (model-behaviour) | §3.1: `chunk_target(max_tokens, cut_chars, cut_lines)` (v4 name: the cut call's own arguments); the example now follows from the code |
| **B** `changes.py` importing `parse_exit_code` from `runner.py` is a circular import (tests-feasibility) | §4.1: `parse_exit_code` moves to `tools.py` beside `is_timeout_result` (its only two callers are in `runner.py`, which imports it from there) |
| `git add -A` into a scratch *index* still writes every new blob and tree into the real object store — `/gitdir` is a 512 MiB tmpfs in docker; loose objects accumulate on the host (turn-loop, sandbox-exec) | §4.1: scratch object directory (`GIT_OBJECT_DIRECTORY=$tmp/objects`) with the real store as `GIT_ALTERNATE_OBJECT_DIRECTORIES`, resolved per repository *before* the variables are exported; `mktemp -d`; P4 case 2/10 measures the store |
| an unborn nested repository makes `git add -A` fatal → guard silently off, including for a whole resume (sandbox-exec) | §4.1: no gitlink recursion; every nested `.git` (any depth, file or dir) is enumerated once and each repository is snapshotted separately with its nested children excluded by `:(exclude,literal)` — #61 §4.4's recipe; unborn repositories snapshot fine (`add` + `write-tree` need no `HEAD`); P4 case 5 |
| fail-open is silent; `core.quotePath` C-quotes non-ASCII paths (sandbox-exec) | §4.1: `git add` stderr is kept in the bash result; `run_end.changed_reason` (sparse) + one stderr line explain every `null`; the enumeration is `find -print0`, no `ls-files` parsing; P4 case 6 |
| worker-writable git config/hooks can make the snapshot lie; host `HOME` is the worktree so `~/.gitconfig` is worker-writable (sandbox-exec, Minor) | §4.1: `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1`, `-c core.fsmonitor=false`; hooks do not run on `add`/`write-tree`; clean filters are deterministic |
| host mode: caches under the redirected `HOME` count as changes; the modes differ (sandbox-exec) | §4.1 Known limits + docs: documented, not excluded (a fixed dot-dir list is brittle and would change hashes for repos that track such paths); invariant 6 qualified |
| `finish()`'s fingerprint position vs `drain_sandbox()`/`run_finalize()` unspecified; docker `finalize()` removes the container; its `_after_bash` notices need a drain (turn-loop, sandbox-exec) | §4.1 (4) + §4.3: first statement of `finish()`, before `drain_sandbox()`; skipped for `interrupted`, `timeout`, `budget_exceeded`, `sandbox_error`; a `BudgetExceeded`/`SandboxError` there is recorded in `changed_reason`, never swallowed silently; the exec's stray-ladder exposure equals `--verify`'s; test 19 rewritten |
| `changed` could be stale; the K check's effect on it unstated; `interrupted` contradicted "always" (turn-loop, contract-ripple) | §4.3 one rule: every successful fingerprint after the start one sets `fp_turn`, `fp_value`, `changed`; `finish()` re-measures unless one was taken this turn; a failed finish-time measurement sets `null` + reason; `interrupted` reports the newest known value |
| the every-K fingerprint's `BudgetExceeded`/`SandboxError` unhandled; `check_verify` pseudo-code called it bare with the wrong resolve text (turn-loop ×2) | §4.3/§4.4: `take_fingerprint()` propagates; `check_verify` and `check_no_change` wrap it exactly as `run_verify` is wrapped, resolving the finish record to `run not finished: change check could not run (…)`; `check_no_change` returns `(ended, text, record)` like `check_progress` |
| invariant 5's bound ignored verify rounds (turn-loop) | §2 (5): 1 + ⌊`max_turns`/K⌋ + (`verify_rounds` + 2) + 1 |
| the start fingerprint sat outside the loop's `KeyboardInterrupt` handling (sandbox-exec, tests) | §4.1 (1): inside the existing `try:` immediately before `while True:` → `interrupted` with a `run_end`, `turns = 0` |
| doubles without `bash`, doubles whose `bash` always raises, `parts` on a non-repo, `_first_run` with `--max-turns 1`, exact-`extra` and `commands ==` pins (tests-feasibility ×3, sandbox-exec) | §4.1: `getattr(self.sandbox, "bash", None)` → guard off, one exec per run when the start fingerprint fails; §7 preamble names the doubles (`FingerprintSandbox`, a `git_parts` fixture), the pins and the `test_main` consequences |
| new `truncated_call_result` text breaks `soak_harvest`'s F5 regex; `machine-contract.md` quotes both texts verbatim (contract-ripple ×2, tests) | §5.2/§6: `_TRUNCATED_CALL_RESULT_RE` widened to both wordings (historical run dirs keep the old), test fixtures added — **in T1** with the signature change; machine-contract's quotes become prose + the invariant-1 numbers + the six-cut-off rule |
| `NO_CHANGE_NUDGE` invites `finish` on a tree the guard will reject; the operative wording that moved W7 was "edit first, grep -n, do not re-read whole files" (model-behaviour ×2) | §4.4: two texts keyed on `fp == fp_start`; the operative wording folded in; the nudge declared diagnostic; §8's W7 replay replaced by a deterministic read-only task plus a behavioural criterion for a fresh edit task |
| F5 pass criterion (`acceptance_passed` is the strict checker; the cited 8192 recovery fails it), `--max-turns` 40 too small at 1024, no repeat rule, #67 confounds; the S14 acceptance ran on 0.10.1 which has no guard; task ordering leaves the gate red (acceptance ×4) | §8 rewritten: recovery = `completed` + `truncations ≥ 1` + 401 lines, `acceptance_passed` reported separately; plan rows `--max-turns 60`; per-cap outcomes and the rerun rule; post-T3 acceptance step **C1** on the branch runtime; the schema-doc rows and test lists move into T1/T3; T4 depends on T1 and T3 |
| Minors (27): `n` known only after the increment (per-turn `counted` flag); 0-char replies; case (b) counting; two delivery orders; `fp_check` initial value; `timeout` exempt from the finish-time fingerprint; plain-answer wording; export failure on `unchanged`; `runs show` skips a null `changed`; "three in a row also end it"; characters before lines; the guard measures change, not compliance; tail A/B row; §1.1 omitted rows; §1.3 tool-mix precision; `describe_change`'s `diff omitted` head; `_tool_call_arg_chars` reuse; `DEFAULT_NO_CHANGE_TURNS` naming; literal counts in docs/tests; a `S14` feature code in `soak_harvest`; README's abort sentence; bench's docstring | each folded in place (§3.1–§3.3, §4.1–§4.5, §5, §6, §7, §9) |
| **Owner review of v3 (v4)** — (1) test 10 expected `timeout_result` to carry `exit code: 124`; the canonical text is `ERROR: command timed out after …` with no exit line (`tools.py:980-992`), so the parser treats a result with no `exit code:` head as fail-open with its first line as the reason; (2) `sandbox.bash` caps output at `MAX_BASH_CHARS` (10 000) with `[output truncated at … — bash output capped]` (`tools.py:78-82`, `:1017`), so a capped result must fail open — a partial listing is never a fingerprint; (3) §5.1's rejection-turn order contradicted #60 (§"Order of operations in a turn": the `finish` message keeps its call position, resolution rewrites it in place) — only the text is rewritten; (4) a `BudgetExceeded`/`SandboxError` raised by a measurement must store `changed = None` + `changed_reason` before it ends the run, since `finish()` measures nothing more for those statuses; the `_fail_run` paths seed every field. Clarifications: the target's basis is the **cut call's** own arguments (`call_size(tc)`), not the reply's aggregate; the parser **sorts** the hash lines so the joined fingerprint is order-independent (the `find`-order caveat disappears) | §4.1 (`parse_fingerprint`, Failure, What the lines mean), §4.3 (rule), §5.1, §3.1–§3.2, §2 (6), §7 (2, 4, 10) |
| **Closure pass (v3, 31 findings, 0 Blocker)**: the `soak_harvest` pattern as described could not match the new generic text; `changed_reason`'s lifecycle after a failed mid-run measurement; "every `null` is explained" vs early-ended runs; the F5 rule let the S2 shape (`empty_reply` abort) count as inconclusive and had no mixed-pair, sink or target-size rule; C1 had no failure consequence, (e) was not a criterion, (d) undefined on an early finish and lacked the resume-gate check; the stderr line's owner; `$tmp` leak on a killed exec, `$TMPDIR` ENOSPC, unreadable files, nested commits invisible, "not adversarially robust"; the lost `watchdog_violation`; the plain-mode `no_change` text ordering an edit on a read-only task; no real-script nested/unborn/non-ASCII test; two `changed` test cases; the docker-mode `test_main` fakes; `trunc` on non-truncated turns; the `record["via"]` guard; the no-tool-path transcript order; test 23 owned by three tasks; rc-0 git warnings; the transcript-schema merge-order lists; `S14` untested; anchors (`_harness_cell`, `_abort_kind`'s docstring, `render_transcript_tail`, `machine-contract.md:365`, `sandbox/` prefixes, `__main__.py:1012`, 17 callers, `:844`, `docker.py:389`) | §5.2 (full pattern); §4.3 (rule); §4.1 (Failure, Known limits, `trap`, `parse_fingerprint`); §8 (F5 rule, C1); §4.4 (`NO_CHANGE_SINCE_START_{REQUIRED,PLAIN}`); §7 (11b, 12, 14, 18, 19, 20, 22, 23a–c, preamble); §5.1; §6; §2 (1), (4); anchors throughout |

## Purpose

Three failures of the same kind: the harness knows something the model needs and does not say it.

1. **#65 / S2.** `NUDGES["truncated"]` says "write the first part and append_file the rest" without
   the cap or a size, so the model cannot size a chunk. At `--max-tokens` 1024, 2048 and 4096 every
   qwen `py-big-fixture` run died the same way — 4 turns, 3 `truncated` nudges, `model_error` —
   and the default 8192 recovered only because the model's guess happened to fit (§1.1). After
   this change every truncation message states the cap, how much of the reply the harness
   received, a concrete per-call target, and how many cut-off replies the run has left.
2. **#66 / S3.** A truncated reply alternating with a small *successful* `write_file` to the same
   path evaded every guard for 40 turns / 900 s / 194k prompt tokens: the write reset
   `FailureTracker`, counted as progress, and `RepeatTracker` only watches bash (§1.2). After this
   change truncations are counted for the whole run and never reset (six end it), and a write that
   changes nothing is not progress.
3. **S14.** `resume --feedback` hands the model the prior run's last events — for a `completed`
   prior, literally `tool_result finish: run finished` / `run_end: completed` — *after* the
   feedback and *before* "When the task is complete, call finish(summary=...)". Five feedback
   resumes of the #61 build called `finish` with zero mutating calls (three on turn 1, two of them
   claiming to have applied every item) and were recorded `completed` with verify green on the
   prior run's work; nothing on the finish path asks whether the tree changed, and the diff
   evidence is against the original `base_commit` so a zero-edit resume still exports a 7–25 KB
   patch (§1.3, §1.4). After this change the harness snapshots the worktree when the run starts and
   compares on every completion: a finish that changed nothing is rejected once with the reason as
   the finish tool's own result, and a second one ends a feedback resume as **`unchanged`** (exit 1)
   instead of `completed`. The same snapshot, taken every ten turns, tells a model that has read
   for ten turns without changing a file to make its first edit — a diagnostic nudge (W7 read 13
   files 34 times in 60 turns and never wrote one; only a forcing preamble moved it, §8).

## 1. The facts (measured 2026-08-25)

### 1.1 Truncation today (`dirtywork/runner.py`, the F5 rows)

Two detection sites, no numbers in either text:

- **Text reply cut off** — `classify_text_reply(content, finish_reason)` (`:190-199`) returns
  `"truncated"` for any reply with no addressable tool call and `finish_reason == "length"`; the
  no-tool-call branch (`:841-860`) writes `nudge{kind: "truncated", turn}`, records
  `failures.record("empty_reply")` (three consecutive → `model_error`), then delivers
  `NUDGES["truncated"]` (`:89-92`) joined with any sandbox and stall texts through `deliver()`.
  **On LM Studio this is the path that fires for a cut tool call**: the server drops the
  unfinished call and returns only the model's prose preamble as a `length` reply (#65's
  observation; every F5 `length` turn on record shows 0–481 visible characters for ≈cap tokens
  generated).
- **Tool call cut off** — inside the tool-call loop (`:877-898`) a call with `tc.error` set on a
  `length` turn (case a) or one that parsed but lacks a required parameter (case b,
  `_missing_required`, `:521-530`) gets `truncated_call_result(name, tc.raw_arguments)`
  (`:146-160`) as its **tool result** and a `malformed_args` strike; no `nudge` event. A `length`
  turn whose call parsed with every required parameter dispatches normally (pinned by
  `tests/test_runner.py:537-554`) and is not a truncation. `_recovered_path` (`:120-143`) scans the
  first 8 192 chars of the raw arguments for a `path`.

Numbers in scope at both sites: `self.max_tokens` (`Runner` attribute, passed on every `chat`,
recorded on `run_start`; on resume inherited from the prior `run.json`, `__main__.py:825-839`),
`resp.text`, every `tc.raw_arguments` (the model's raw JSON string on `openai_compat` — present
only in cases a/b; `""` on the Anthropic adapter's error branch), `resp.usage["completion_tokens"]`
(backend-dependent; not used below). `CHARS_PER_TOKEN = 4` (`:46`) converts;
`_tool_call_arg_chars(tc)` (`:426`) already measures a call's raw arguments.

The F5 rows (`py-big-fixture`: a 400-row, ≈22.4 KB / 401-line CSV; `soak-B.jsonl`,
`soak-B2.jsonl`, run dirs linked from each row):

| row | model | cap | status | turns | `truncated` nudges | wall |
|---|---|---|---|---|---|---|
| F5-trunc-qwen-r1 / r2 | qwen3-coder-next | 1024 | `model_error` | 4 / 4 | 3 / 3 | 40 s |
| F5-trunc2048-qwen-r1 | qwen | 2048 | `model_error` | 4 | 3 | 76 s |
| F5-trunc4096-qwen-r2 | qwen | 4096 | `model_error` | 4 | 3 | 147 s |
| F5-default-qwen | qwen | 8192 | `completed`; acceptance passed under the lax 6-field checker of the time — the strict per-row checker that ships now **fails** it on one arithmetic slip (ledger re-score note) | 8 | 1 | 275 s |
| F5-trunc-dev-r1 | devstral | 1024 | `max_turns` | **40** | **17** | **900 s** |
| F5-trunc-dev-r2 | devstral | 1024 | `model_error` | 10 | 3 | 139 s |
| F5-trunc4096-dev-r2 | devstral | 4096 | `model_error` | 8 | 3 | 412 s |
| F5-default-dev | devstral | 8192 | `model_error` (S6/#67, file complete) | 14 | 1 | 709 s |

Omitted: F5-trunc2048-dev-r1 (B2) ended on the #60 strict-template HTTP 400; the #60 reruns
F5-trunc2048-{qwen,dev}-60 (`soak-60.jsonl`) reproduce the 3-consecutive abort at 76 s / 196 s.
Every qwen abort is "aborted after 3 consecutive empty_reply failures" after three `length`
replies in a row; the model announced "header and first part" and overran again each time. The
one recovery (8192) used `write_file` + 3–4 `append_file` chunks of ≈4–5 KB — about 15 % of the
cap's character capacity, chosen blind.

### 1.2 Why the rewrite loop survives (`runner.py`, the trackers)

- `FailureTracker` (`:53-75`) counts *consecutive* failures per kind; **any** tool execution with
  `ToolResult.failure is None` resets it entirely (`:914`) — a successful `write_file`, a
  `BLOCKED:` result, a deadline refusal.
- `ProgressTracker.note_call` (`:294-310`): a non-`ERROR` result from `_MUTATING_TOOLS`
  (`write_file`, `append_file`, `edit_file`, `apply_edits`, `insert_before`, `insert_after`) is
  *always* progress — including `Wrote P: +0 -0`, the head `describe_change` (`tools.py:421-450`)
  returns for a byte-identical rewrite.
- `RepeatTracker` (`:328-385`) is fed only `bash`.
- Nothing counts truncations across turns. F5-trunc-dev-r1 alternated `length` reply → small
  successful `write_file` (same path) → `length` reply seventeen times; r2 of the same
  configuration aborted cleanly in 139 s because no write landed between three truncations.

### 1.3 The six S14 data points (`~/.dirtywork/runs/`, read 2026-08-25)

| run | prior | resume turn 1 | mutating calls | ended | evidence |
|---|---|---|---|---|---|
| `issue-61-task-w2a-…-6bbcd080` (feedback: 2 fixes) | `completed`, 57 turns | bash `pytest`, then `finish` on turn 2 | 0 | `completed`, verify passed, 117 s | `diff.patch` 18 311 B — all prior work |
| `issue-61-task-w3-…-516c446d` (6 edits with checks) | `completed`, 55 turns | `finish` — "Applied all six feedback edits" | 0 | `completed`, 61 s | `diff.patch` 24 912 B |
| `issue-61-task-w4b-…-3e14c990` (3 fixes; "do NOT call finish until every check passes") | `max_turns`, 60 turns | `finish` — excuse about "pre-existing test failures" | 0 | `completed`, 58 s | no finish replay in the tail |
| `issue-61-task-w8b-…-ee187f86` (4 test edits; "Do NOT call finish until every check passes") | `completed`, 33 turns | `finish` — "Successfully implemented all feedback" | 0 | `completed`, 57 s | tail ends `run_end: completed` |
| `issue-61-review-follow-up-w10-…-4d0c3387` (delete one file, run suite, finish) | `max_turns`, 60 turns | eight turns of bash `grep` / `read_file` / pytest, `finish` on turn 9 | 0 | `completed`, 75 s | `CHANGES_SUMMARY.md` still in `files_changed` |
| `issue-61-task-w7-…-e1610416` (fresh run, W7) | — | 60 turns: `read_file` 34 / `grep` 18 / `bash` 7 / `list_dir` 1 | 0 | `max_turns`, 313 s, 2.23 M prompt tokens | `diff.patch` 0 B; `runner.py` opened 9× (6 `read_file` + 3 `grep`), `bench.py` 7×; no stall (every read had new args) |

Two of the five resumes (w4b, w8b) carried an explicit "do not call finish until…" and finished on
turn 1 anyway: prompt wording alone is not a fix. All five were recorded `completed` with
`verify.passed = true` — the suite was green from the prior run.

### 1.4 What a resume hands the model (`dirtywork/resume.py`, `__main__.py`)

`resume` replays **no** messages. `build_resume_task` (`resume.py:224-256`) builds one user turn:
the prior task (markers stripped), `--- RESUMED RUN: REVIEW FEEDBACK ---`, a status line, the
feedback paragraph ("A reviewer read that run's work and sent this feedback: … inspect it with `git
status` and `git diff` first, then apply the feedback. Make no other changes."), then "The last
events of the earlier run were:" + `render_transcript_tail` (`:208-221`; `_render_event` `:191`;
newest 12 000 chars of one-line-per-event renderings: `assistant: <text[:1000]> [tools: …]`, `tool_result <tool>:
<result[:500]>`, `nudge: <kind>`, `run_end: <status>`), then "When the task is complete, call
finish(summary=...)." For a `completed` prior the tail's last three lines are always
`assistant: … [tools: finish]`, `tool_result finish: run finished`, `run_end: completed` — they are
the newest events and the tail is cut from the oldest end. The `Runner` has no resume flag: `feedback`
and `resumed_from` reach it only inside `run_info` (stamped on `run_start`) and inside the task
string. A `completed` prior requires `--feedback` (`__main__.py:842-847`, exit 2); every other status
resumes with or without it. On resume `base_commit` is the original run's (`:870`), docker mode
seeds `/work` from the host worktree by tar (`sandbox/docker.py:507-547`, nested `.git`
directories included) with no snapshot of the seeded state, and `files_changed` / `diff_stat` / `diff.patch` are
computed only in `finalize()` against that base — so no existing evidence can say "changed during
*this* resume".

### 1.5 The fingerprint probes (P1–P4, this macOS host / Docker Desktop, `dirtywork-worker-pytest:0.10`, git 2.39.5 in the image)

**P1 — the scratch-index snapshot** (`GIT_INDEX_FILE=$(mktemp) git add -A -- . && git write-tree;
git rev-parse HEAD`) on a throwaway clone of this repository (207 tracked files):

| case | tree hash | wall | note |
|---|---|---|---|
| clean tree, twice | `06f5e8706605` both | ≈50 ms | `git status --porcelain` empty before and after: the real index is untouched |
| untracked file added / tracked file modified / tracked file deleted | `3a674c77e26f` / `ab383bc03de9` / `1ae5781228f6` | | each restored to the baseline hash afterwards |
| `.gitignore`d file (`__pycache__/x.pyc`) | `06f5e8706605` | | ignored, as `git add -A` ignores it |
| byte-identical rewrite of `README.md` | `06f5e8706605` | | content-addressed: a no-op write is no change |
| linked worktree (`git worktree add`, `.git` is a gitfile) | `06f5e8706605` | ≈54 ms | the main clone's index untouched |
| `GIT_DIR`/`GIT_WORK_TREE` exported (the 0.10 sandbox's S13 layout) | reflects the worktree | | git dereferences the gitfile |
| +2 000 untracked one-line files | `504ef2a068f0` | 790 ms cold, 170–180 ms warm | |
| inside the worker container, `/work`, user `worker` (uid 1000) | `06f5e8706605` | 10–11 ms | **identical to the host hash** for the same tree (git tree entries carry content + the executable bit only) |
| container, #61 gitfile layout (`/work/.git` → `gitdir: /gitdir`) | `06f5e8706605` | 20 ms | index untouched |

`mktemp` exists in the image (`/usr/bin/mktemp`). Cheaper alternatives measured and rejected: `git
status --porcelain --untracked-files=all | sha256sum` + `git diff HEAD | sha256sum` (≈20 ms, hashes
untracked *names*, not content); `git stash create` (0–3 ms, tracked changes only). **What P1 did
not measure** (red-team): the object store — a scratch index does not stop `git add` from writing
blobs into the repository's objects; §4.1's script sends them to a scratch object directory
instead, and P4 measures the store.

**P2 — guardrails.** The P1 command passes `check_bash_command` unchanged in both modes
(`worktree=<root>` host scan: `None`; `sandboxed=True`: `None`) — no rule in
`guardrails.py:_RULES` (`:119-152`) matches `mktemp`, `rm`, `find`, `git add`, `git write-tree` or
`git rev-parse`; the `rm` rule needs an escape target (`~`, `$HOME`, an absolute path outside the
worktree). P4 case 11 repeats the check on the v2 script.

**P3 — nested repositories, v1 recursion over gitlinks** (superseded by §4.1's enumeration, kept
as evidence of the mechanism): fresh clone + linked worktree on the host, and `/work` in the
container; the v1 script via `/bin/sh -c` and `/bin/bash -c` (byte-identical output):

| case | lines printed (12-char prefixes, in order) | exit | wall |
|---|---|---|---|
| clean tree, twice | `06f5e8706605` (root, = P1's baseline) / `3e92fd797cab` (HEAD) | 0 | 50–60 ms |
| `vendor/inner` created and committed | `acfd34694c0b` / `08585692ce06` / HEAD | 0 | |
| uncommitted edit inside `inner` | root **unchanged** / `fd6008e89830` / HEAD | 0 | a gitlink freezes the outer view; only a per-repository snapshot sees the edit |
| commit inside `inner` | `9cc8ef99d7fd` (gitlink moved) / inner unchanged / HEAD | 0 | v1 behaviour: under v2's exclusion the root line stays put (P4) — a nested commit alone is no change |
| untracked file inside `inner` | root unchanged / `54cccea4f101` / HEAD | 0 | |
| `inner/deeper` (nested in nested), committed; then an uncommitted edit in `deeper` | 4 lines, depth-first; only the `deeper` line moves | 0 | |
| `vendor/sp ace` (space in the path) committed | 5 lines | 0 | |
| `rm -rf vendor/inner/.git` after its commit (separate minimal repo) | 3 lines → 2 lines; the files become plain blobs of the root tree | 0 | |
| a directory that is not a repository | no output | 1 | `$TMPDIR` file count 782 before and after: nothing leaks |
| three nested repositories present, ×3 | identical 5 lines each run | 0 | 120 / 110 / 110 ms host; **19 ms** in the container, identical hashes |

**P4 — the v2 script (§4.1) byte-for-byte, via `bash -c` only.** Fresh clone + linked worktree
on the host (`$TMPDIR` entry count 1 137 before and after every case: nothing leaks; the real
object store counted with `find <objects> -type f | wc -l`), and the worker container:

| case | lines (12-char prefixes) | exit | wall | note |
|---|---|---|---|---|
| clean tree, twice | `06f5e8706605` / `3e92fd797cab` | 0 | 60–80 ms | root hash **equals P1/P3's** although objects now flow through the scratch directory; index untouched; real object count **4 646 before and after** |
| untracked 1 MB random file | `ef767ffce63e` | 0 | | object count **still 4 646** — the blob went to the scratch directory; restore → baseline |
| modify `README.md` / delete `SECURITY.md` / ignored file / byte-identical rewrite | `dacbeaafa19a` / `1ae5781228f6` (= P1) / baseline / baseline | 0 | | object count 4 646 throughout |
| `vendor/inner` committed | root **unchanged** / `08585692ce06` / HEAD | 0 | | the nested repository is excluded by pathspec, not recorded as a gitlink: the root line never reflects it |
| uncommitted edit, then a commit, then an untracked file inside `inner` | root unchanged each time / inner `e8d45c8d1fdf` → unchanged by the commit (same content) → `d9bb81aa996b` | 0 | | **each repository's line is its own working tree; a commit alone moves nothing** (the root's `HEAD` line covers the root's commits) |
| `inner/deeper` committed; an edit inside `deeper` | 4 lines; only `deeper`'s moves; `inner`'s excludes `deeper`'s files | 0 | | nested lines print in `find` order (`deeper` before `inner` on APFS) — deterministic within a sandbox, not topological |
| **unborn** `vendor/unborn` (`git init`, a file, no commit); then an edit | its own line `0479003445f4` → `db343cdcba58`; exit 0 | 0 | | the case that was fatal to a plain `git add -A` |
| `vendor/sp ace` and `vendor/café` committed | own lines (`ec67420ed747` = P3's identical-content tree; `cf67e9ef3a0f`); edits move only their line | 0 | | |
| a nested `.git` **file** (`git worktree add vendor/wt`) | its own line (`06f5e8706605`, the same clean commit) | 0 | | `find -name .git` catches gitfiles |
| a directory that is not a repository | no stdout, `fatal: not a git repository` on stderr | 1 | | no leaked `mktemp -d` directory |
| seven nested repositories, ×3 | identical 8 lines | 0 | 240 / 240 / 240 ms | ≈ 4× the single-repository cost: one scratch cycle per repository |
| +2 000 untracked files | root `504ef2a068f0` (= P1's hash for the same scenario) | 0 | 1.11–1.12 s | object count still 4 646; slower than P1's 170 ms because the blobs are now actually written (to scratch) and eight repositories cycle |
| container: `/repo.git` bare (read-only), `/work` with `.git` → `gitdir: /gitdir`, `/gitdir/objects/info/alternates` → `/repo.git/objects` | root `06f5e8706605` — **equals the host** | 0 | 106 ms | `/gitdir/objects` held 1 loose file, `/repo.git/objects` 4 646 |
| same layout, untracked file added | `b0a8b27dd8ba` | 0 | | **both** stores unchanged (1 / 4 646): the two-hop alternates chain resolves and nothing is written to either |
| guardrails (`check_bash_command`, host with worktree / `sandboxed=True`) | `None` / `None` | | | |
| `sh -c` | `syntax error near unexpected token '<'` at the process substitution | 2 | | bash-only, fails at parse time before any side effect (documented) |

No change to the script was needed. **P4 is satisfied** (§8).

## 2. The invariants (the contract after this change)

1. **Every truncation message carries the numbers**: the `--max-tokens` cap, how much of the reply
   the harness received (characters; lines when the cut call's arguments are present), a per-call
   target (characters and lines), and "cut-off reply *n* of *N*" (the text-reply nudge adds the
   reminder that three in a row also end the run). Both texts — the text-reply nudge and the
   tool-call result — derive the target from one function (§3.1).
2. **Truncations are counted per run and never reset.** A turn with a `length` reply that produced
   a truncation nudge or a `truncated_call_result` counts once; `MAX_TRUNCATED_REPLIES` (6) ends the
   run `model_error` with a reason naming the cap and the fix (§3.3). The existing three-consecutive
   rule is unchanged and wins when both fire on the same turn.
3. **A write that changes nothing is not progress** (`+0 -0` from any mutating tool, §4.2).
4. **A completion that changed nothing is never accepted silently.** On every completion path
   (the `finish` tool and the plain answer) the harness compares the worktree fingerprint with the
   one taken at run start; equal ⇒ the first such completion is rejected with the reason as the
   finish tool's own result (or a user message on the plain path), verify does not run, and the run
   continues; the second ⇒ `unchanged` when the run required changes (`Runner(require_changes=True)`,
   set by the CLI for `resume --feedback`), else it is verified as today (`completed` when verify
   passes) with `run_end.changed = false` (§4.3). The guard detects *that* something changed, not
   whether the feedback was applied — the reviewer still reads `files_changed` / `diff.patch`
   against the items — and it is not adversarially robust: a worker can touch a file or commit;
   it catches a lazy completion, not a hostile one.
5. **The guard fails open, says so, and is bounded.** A fingerprint the sandbox cannot produce
   (non-zero exit, unparseable output, timeout, a sandbox without `bash`) disables the comparison
   it was for — never a rejection, never a nudge — and `run_end.changed` is `null` with the reason
   in `changed_reason`, which the CLI echoes on stderr. A run takes at most 1 (start) + ⌊`max_turns` / K⌋
   (K checks) + (`verify_rounds` + 2) (one unchanged rejection plus up to `verify_rounds` + 1
   verified completions) + 1 (the `finish()` measurement when the run ends on a turn that took
   none) fingerprints, each one `sandbox.bash` exec bounded by `FINGERPRINT_TIMEOUT` (60 s); a run
   whose start fingerprint fails takes exactly one (§4.1, §4.4).
6. **Host and docker run the same script through the same seam** (`sandbox.bash`, as `--verify`
   does), parsed by one function; every per-repository hash is content-addressed and equal across
   modes for the same tree (P1, P4), and the parser sorts them, so the joined fingerprint is
   order-independent — equal across hosts and containers, not merely within one sandbox (every
   comparison the guard makes is within one sandbox anyway). The modes differ only in what the
   worktree contains: host mode's `HOME` is
   the worktree, so tool caches written there count as changes (§4.1 Known limits).
7. **The resume task reads in the right order**: prior task → tail of the earlier run → its status
   → the feedback, marked as not yet applied → the finish instruction; the `unchanged` status
   requires `--feedback` to resume, like `completed` (§4.5).
8. **Additive only** (`schema_version` 2): two `nudge.kind` values (`no_change`,
   `unchanged_finish`), one `status` (`unchanged`), two always-present `run_end` / `run.json` /
   stdout-JSON fields (`truncations`, `changed`) and one sparse one (`changed_reason`); no new
   event name, no new `via` value.

## 3. Cap-aware truncation (#65)

### 3.1 The numbers and the chunk target (`runner.py`, beside `truncated_call_result`)

The cap is the one number that is always true on a `length` stop: the model generated ≈`max_tokens`
tokens whatever the server returned. What the harness *received* varies by adapter — on LM Studio
a cut tool call is dropped and only the prose preamble arrives (§1.1); on `openai_compat` cases
a/b the cut call's raw JSON arrives; on the Anthropic error branch `raw_arguments` is `""`. So the
target's basis is the cap unless **the cut call's own** arguments are actually present, and the
per-line ratio is measured only from those arguments — the other calls of the same reply, and its
prose, say nothing about how densely the cut content tokenizes. Two measures, two purposes:

```python
MIN_CHUNK_CHARS = 200
MIN_CHUNK_LINES = 5
CHUNK_DIVISOR = 4
DEFAULT_LINE_CHARS = 60

def reply_size(resp) -> tuple[int, int]:
    """(text_chars, raw_chars): what the harness RECEIVED -- the reply's prose
    and the raw argument strings of every addressable tool call
    (`_tool_call_arg_chars`). Reported to the model as `received`; never the
    target's basis."""
    return (len(resp.text or ""),
            sum(_tool_call_arg_chars(tc) for tc in resp.tool_calls))

def call_size(tc) -> tuple[int, int]:
    """(chars, lines) of ONE tool call's raw arguments -- the call that was
    cut (cases a/b). Newlines inside JSON string arguments arrive escaped
    (`\\n`), so both forms are counted. (0, 0) when the adapter kept nothing
    (Anthropic's error branch); the text path has no call and passes (0, 0)."""
    raw = tc.raw_arguments or ""
    chars = _tool_call_arg_chars(tc)
    return chars, (raw.count("\\n") + raw.count("\n") + 1) if chars else 0

def chunk_target(max_tokens: int, cut_chars: int, cut_lines: int) -> tuple[int, int]:
    """(characters, lines) a single tool call's content must stay under.
    Basis: the cap's character capacity (max_tokens * CHARS_PER_TOKEN), or the
    smaller of that and what the CUT CALL actually got out when its raw
    arguments are present (cut_chars > 0) -- that call's own ratio reflects
    how densely THIS model tokenizes THIS content. A quarter of the basis
    leaves room for JSON escaping, the call's other fields and any prose
    around it. Lines come from the cut call's own characters-per-line when it
    had enough lines to measure, else DEFAULT_LINE_CHARS."""
    cap_chars = max_tokens * CHARS_PER_TOKEN
    basis = min(cap_chars, cut_chars) if cut_chars > 0 else cap_chars
    chars = max(MIN_CHUNK_CHARS, basis // CHUNK_DIVISOR)
    per_line = (cut_chars / cut_lines
                if cut_chars > 0 and cut_lines >= 3 else DEFAULT_LINE_CHARS)
    return chars, max(MIN_CHUNK_LINES, int(chars / per_line))
```

Worked values (`CHARS_PER_TOKEN = 4`): cap 1024, LM Studio text path (`cut_chars == 0`) → basis
4 096 → **1 024 chars ≈ 17 lines** (the 401-line fixture in ≈ 22–31 `append_file` calls); cap
2048 → 2 048 chars ≈ 34 lines (≈ 11 calls); cap 4096 → 4 096 ≈ 68 lines (≈ 6); cap 8192 → 8 192
≈ 136 lines (≈ 3; the 8192 recoveries used four or five); cap 1024, `openai_compat` case a with a
`write_file` cut at 3 000 raw chars / 55 escaped lines → basis 3 000 → 750 chars ≈ 13 lines; cap
1024, Anthropic error branch (`raw_arguments == ""`) → basis 4 096 → 1 024 / 17.

The truncation count `truncations` (§3.3) and `self.max_tokens` complete the parameter set.
`trunc: dict = {}` at the top of `one_turn`; it is filled at the first truncation site the turn
reaches (a per-turn `counted` flag: `if not counted: truncations += 1; counted = True`, then the
dict — so `n` is the incremented value) and handed to both sites; on an `empty` /
`text_tool_call` turn it stays empty and `.format(**{})` is a no-op. On the tool path
`cut_chars, cut_lines = call_size(tc)` of the call being handled at that first site (a second
cut call in the same turn — case (b) twice — reuses the dict); on the text path `(0, 0)`:

```python
text_chars, raw_chars = reply_size(resp)
tc_chars, tc_lines = chunk_target(self.max_tokens, cut_chars, cut_lines)
trunc = {"cap": self.max_tokens, "cap_chars": self.max_tokens * CHARS_PER_TOKEN,
         "received": text_chars + raw_chars,
         "cut_chars": cut_chars, "cut_lines": cut_lines,
         "target_chars": tc_chars, "target_lines": tc_lines,
         "n": truncations, "max": MAX_TRUNCATED_REPLIES}
```

### 3.2 Texts (constants in `runner.py`; wording can change without a schema change)

`NUDGES["truncated"]` becomes a format template — the key stays (the
`bench.EMPTY_REPLY_NUDGE_KINDS == tuple(runner.NUDGES)` pin holds) and the delivery site formats
every `NUDGES[kind]` with the turn's dict (`str.format` ignores unused keys; the other two texts
have no fields). The text path never claims to know what was cut — it says what arrived:

```python
NUDGES = {
    "truncated": ("Your reply was cut off at the --max-tokens cap of {cap} tokens (about "
                  "{cap_chars} characters); the harness received only {received} characters of "
                  "it — cut-off reply {n} of {max} (the run ends at {max}; three in a row also "
                  "end it). Keep each tool call's content under about {target_chars} characters "
                  "(about {target_lines} lines; split long lines if you must) and emit one tool "
                  "call at a time; for a large file, write_file the first part and append_file "
                  "the rest."),
    "empty": …unchanged…,
    "text_tool_call": …unchanged…,
}
```

`truncated_call_result(tool, raw_arguments, trunc)` keeps its two shapes and gains the numbers —
here the arguments did arrive, so "after about … lines" is honest:

```python
def truncated_call_result(tool: str, raw_arguments, trunc: dict) -> str:
    if tool == "write_file":
        path = _recovered_path(raw_arguments)
        if path is not None:
            return (f"ERROR: your write_file for {path!r} was cut off at the --max-tokens cap of "
                    f"{trunc['cap']} tokens after about {trunc['cut_chars']} characters "
                    f"(~{trunc['cut_lines']} lines) — nothing was written; cut-off reply "
                    f"{trunc['n']} of {trunc['max']}. Write the file in chunks of at most about "
                    f"{trunc['target_chars']} characters (about {trunc['target_lines']} lines): "
                    f"write_file with the first part, then append_file for each following part.")
    return (f"ERROR: your {tool} call was cut off at the --max-tokens cap of {trunc['cap']} tokens "
            f"after about {trunc['cut_chars']} characters (~{trunc['cut_lines']} lines) before it "
            f"completed — cut-off reply {trunc['n']} of {trunc['max']}. Keep each tool call under "
            f"about {trunc['target_chars']} characters (about {trunc['target_lines']} lines); for "
            f"a large file, write_file the first part and append_file the rest.")
```

Case (b) — a parsed call missing a required parameter on a `length` turn — keeps today's rule
(every such call in the reply gets the text and the strike; only the turn's count is once):
stated as an accepted limitation, not changed here.

### 3.3 The truncation budget

```python
MAX_TRUNCATED_REPLIES = 6
TRUNCATION_ABORT = ("aborted after {n} cut-off replies at --max-tokens {cap}: raise --max-tokens "
                    "or split the writes")
```

- `truncations` is a run-scoped counter in `Runner.run` beside `timeouts` and `trimmed_turns`
  (`:546-549`, `nonlocal` in `one_turn`). It increments **once per turn**, at the first truncation
  the turn produces: the text path (`:851`, before the nudge record is written) or the first
  `truncated_call_result` of the tool loop (cases a and b). A `length` turn whose calls all parsed
  completely does not count (invariant 2; the pin at `tests/test_runner.py:537-554` stands).
- After the increment, `truncations >= MAX_TRUNCATED_REPLIES` sets the turn's abort reason to
  `TRUNCATION_ABORT.format(n=truncations, cap=self.max_tokens)` — on the text path right after
  `failures.record("empty_reply")` (the consecutive-failure reason, if any, is computed first and
  wins, as the existing code returns before reaching the budget check); on the tool path the
  truncated result is still produced, recorded and appended (the transcript shows the cut call and
  its result) and the loop's existing `if abort_reason is not None: return finish("model_error",
  abort_reason)` (`:945`) ends the run. The nudge record on the text path is written before the
  abort, exactly as the third strike is today (sparse `via`).
- `run_end` / `run.json` / stdout JSON carry `truncations` (integer, **always**, `0` when none),
  seeded like `timeouts` in `_seed_payload` (`__main__.py:590-591`) and `_contract_fields` (`:609`).
- `bench._abort_kind` (`bench.py:246-252`) gains a second pattern, `aborted after \d+ cut-off
  replies`, returning `"truncated"`; `_ABORT_RE` is unchanged; the docstring at `:255-263` that
  enumerates the abort rules names the sixth cut-off reply. The scoreboard's `abort_kind` column
  therefore reads `truncated` for these runs (no new column).

## 4. The change guard (#66)

### 4.1 The fingerprint (new module `dirtywork/changes.py`)

```python
from __future__ import annotations
import re
from .tools import parse_exit_code   # moved from runner.py (K: circular import)

FINGERPRINT_TIMEOUT = 60
# bash-only (both sandboxes run `bash -c`; macOS /bin/bash 3.2 suffices: arrays,
# `read -d ''`, process substitution -- no mapfile). One exec, no recursion.
FINGERPRINT_SCRIPT = r"""export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
roots=()
while IFS= read -r -d '' g; do roots+=("${g%/.git}"); done < <(find . \( -path ./.git -prune \) -o \( -name .git -prune -print0 \) 2>/dev/null)
dw_snap() (
  d=$1; shift
  cd "$d" || exit 1
  real=$(git rev-parse --git-path objects) || exit 1
  case $real in /*) ;; *) real=$PWD/$real ;; esac
  tmp=$(mktemp -d) || exit 1
  trap 'rm -rf "$tmp"' EXIT
  mkdir "$tmp/objects" || exit 1
  GIT_INDEX_FILE=$tmp/index GIT_OBJECT_DIRECTORY=$tmp/objects GIT_ALTERNATE_OBJECT_DIRECTORIES=$real
  export GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
  git -c core.fsmonitor=false add -A -- . "$@" >/dev/null && git write-tree
)
snap() {
  d=$1; ex=()
  for r in "${roots[@]}"; do
    if [ "$d" = . ]; then ex+=(":(exclude,literal)${r#./}")
    elif [[ $r == "$d/"* ]]; then ex+=(":(exclude,literal)${r#"$d/"}")
    fi
  done
  dw_snap "$d" "${ex[@]}"
}
snap . || exit 1
for r in "${roots[@]}"; do snap "$r" || exit 1; done
git rev-parse HEAD"""
_HEX40 = re.compile(r"^[0-9a-f]{40}$")

def parse_fingerprint(result: str) -> tuple[str | None, str | None]:
    """(fingerprint, reason) from the sandbox's bash result (stdout and
    stderr merged; `exit code: N` first when the command ran to an exit).
    Exit 0 -> the fingerprint is the SORTED 40-hex lines (at least two: a
    tree and HEAD); sorting makes it independent of `find`'s order, so the
    same tree gives the same string on any host or container. Any other
    line under exit 0 (an rc-0 `warning:`/`hint:` from git) is ignored --
    the script exits non-zero on every failure, so rc 0 vouches for every
    hash. Everything else is (None, reason), `reason` capped at 200 chars:
    a non-zero exit (reason = the first non-empty non-hex line, git's own
    diagnostic, else the exit line); a result with no `exit code:` head --
    `timeout_result`'s `ERROR: command timed out after …`, `ERROR: bash
    failed …`, a `BLOCKED:` -- (reason = its first line); a CAPPED result,
    whose last line is `[output truncated at 10000 chars — bash output
    capped]` (`MAX_BASH_CHARS`; about 240 repositories' worth of lines) --
    (reason = that line): a partial listing must never pass as a
    fingerprint, whatever its exit code says."""

def fingerprint(sandbox) -> tuple[str | None, str | None]:
    bash = getattr(sandbox, "bash", None)
    if bash is None:
        return None, "sandbox has no bash"
    return parse_fingerprint(bash(FINGERPRINT_SCRIPT, FINGERPRINT_TIMEOUT))
```

- **What it hashes.** Every repository under the worktree, each as `git add -A` sees it —
  tracked, modified, deleted, untracked-but-not-ignored, executable bits — into a scratch index,
  with new blobs and trees written to a **scratch object directory** whose alternate is the
  repository's real store (`git rev-parse --git-path objects`, resolved *before* the variables are
  exported, absolutized; in a linked worktree that is the parent repository's store, in the worker
  container `/gitdir/objects`, whose own `info/alternates` reaches `/repo.git/objects` — git
  follows alternates of alternates). The real index and the real object store are never written
  (P1, P4). Nested repositories — every `.git` entry below the root, file or directory, at any
  depth (`find … -prune -print0`, the root's own `.git` pruned without printing) — are excluded
  from their parent's snapshot by `:(exclude,literal)` (#61 §4.4's recipe,
  `sandbox/export.py:410/:454`)
  and snapshotted separately with *their* nested children excluded the same way; no gitlink is ever
  recorded, so an uncommitted edit inside a nested repository changes that repository's line, and
  an **unborn** nested repository (`git init` without a commit — fatal to a plain `git add -A`,
  #61 §1.4) snapshots like any other (`add` + `write-tree` need no `HEAD`). The last line is the
  root's `HEAD`, so a commit with no file change (host mode keeps real commits) counts as a change.
  The fingerprint is the sorted hash lines; the harness never interprets them beyond equality.
- **Determinism and config.** `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1` make host and
  container read the same config (host mode's `HOME` is the worktree, so `~/.gitconfig` there is
  worker-writable; the operator's real global config — `core.autocrlf`, filters — is out of the
  picture); `-c core.fsmonitor=false` keeps a planted fsmonitor from running; `add`/`write-tree`
  run no hooks; a `clean` filter, if a repository configures one, is deterministic. The
  enumeration order is `find`'s directory order — stable within one sandbox for the life of a run,
  which is all equality needs.
- **Where it runs.** `sandbox.bash(FINGERPRINT_SCRIPT, FINGERPRINT_TIMEOUT)` — the seam
  `run_verify` uses (`runner.py:731`): host `HostSandbox.bash` → `tools.bash` (`bash -c`,
  `tools.py:1010`) on the worktree with the worktree-rooted `HOME` and the host guardrail scan,
  then `_check_budget()`; docker `DockerSandbox.bash` → one `docker exec` (`/bin/bash -c`) in the
  worker container as `worker`, `sandboxed=True` scan, `ulimit -f`, then #61's `_after_bash` —
  the stray ladder and the budget sample — like any other harness command. Both scans pass (P2,
  P4). **The exec's exposure to the stray ladder and the budget sample is exactly `--verify`'s**
  (a reset it triggers is the same reset the model's own last bash call could trigger); at run
  start on a resume it means an over-budget seeded worktree ends the run `budget_exceeded` before
  the first turn — documented ("clean the worktree or raise `--max-worktree-mb` before resuming").
  `mktemp -d` writes under `$TMPDIR` (the container's own `/tmp`, #63). No transcript event is
  written for the exec itself: its outcomes surface as `nudge` events, `run_end.changed` and
  `changed_reason` (§5.1); a `stray_kill` / `sandbox_reset` notice its `_after_bash` queues rides
  the turn's ordinary `drain_sandbox()` (or `finish()`'s, §4.3).
- **When.** `take_fingerprint()` (a closure in `Runner.run`) calls `changes.fingerprint`,
  propagates `BudgetExceeded` / `SandboxError`, and on success sets `fp_turn = turns`, `fp_value =
  fp` and — after the start one — `changed = fp != fp_start`; on failure it stores the reason. It
  runs: (1) once at run start, **inside the existing `try:` that wraps the turn loop
  (`runner.py:1005`), immediately before `while True:`** — after the `run_start` event, before the
  first `chat`; a Ctrl-C there reaches the outer `except KeyboardInterrupt` and ends `interrupted`
  with `turns = 0` and a `run_end`; in docker mode the container is started and, on resume, seeded
  before `runner.run()` (`__main__.py:476-497`, `:954`), so the snapshot is the resumed state, not
  `base_commit`; (2) on every completion path (§4.3); (3) every K turns (§4.4); (4) as the **first
  statement of `finish()`** — before `drain_sandbox()`, `transcript.flush()` and `run_finalize()`
  (docker `finalize()`, `sandbox/docker.py:389`, stops and removes the worker container first
  (`_stop_container`, `:361`), after which `bash` returns `ERROR: bash failed` and parses to
  `None`) — for every status **except `interrupted`,
  `timeout`, `budget_exceeded` and `sandbox_error`** (get out; the deadline is spent; the
  violation/error is already the reason), unless `fp_turn == turns` (a completion-path or K-check
  measurement this turn, which also covers the top-of-turn exits where no model action occurred
  since the last check). So `run_end.changed` is known for `max_turns`, `stalled`, `stuck`,
  `model_error`, `verify_failed` and `context_exhausted` too (W7 would have read `changed: false`).
- **Failure.** `BudgetExceeded` / `SandboxError` raised by `sandbox.bash`: `take_fingerprint()`
  **first stores `changed = None` and `changed_reason = "budget: <reason>"` /
  `"sandbox: <error>"`, then re-raises** — so the `run_end` that follows carries the diagnostic
  although `finish()` measures nothing more for those statuses. At run start, on completion paths
  and in the K check the exception ends the run with `budget_exceeded` / `sandbox_error` exactly
  as `run_verify`'s are mapped (`:761-767`; the completion paths resolve the finish record to
  `run not finished: change check could not run (<reason>)`); inside `finish()` it is caught and
  the same two fields stand — never swallowed silently: in docker mode the exec's `_after_bash` budget sample *consumes* the
  watchdog violation (`take_violation`) before raising, so after `extra.update(finalize_state
  ["result"])` `finish()` sets `extra["watchdog_violation"] = e.reason` and
  `watchdog_violation_kind = "budget"` when `finalize()` left them unset (never overwriting a value
  it set); a `SandboxError` there is already what `finalize()` will report as the export failure.
  Any other exception from the exec propagates as today's unhandled-error contract does: it
  escapes `run()` to `__main__._fail_run` (`:683-695`), which writes no runner `run_end`,
  builds the payload from `_contract_fields(extra={})` — `truncations: 0`, `changed: null`, no
  `changed_reason`, as `timeouts` is seeded there today — and prints no guard line; the same holds
  for an `LLMError` escaping `run()`. A `(None, reason)` fingerprint disables only the comparison
  it was for: at run start it turns the guard
  off for the run (`fp_start = None` ⇒ (2)–(4) are all skipped, `changed: null`, `changed_reason`
  = the reason, no rejection, no `no_change` nudge — **exactly one exec for the whole run**); in a
  K check or a completion check it skips that check, keeps the previous baseline and stores the
  reason until the next measurement (§4.3's rule); in `finish()` it sets `changed = None` + reason.
  Every `null` produced by an attempted-and-failed measurement is explained by `changed_reason` on
  `run_end`, which the CLI echoes once after `run()` returns as `dirtywork: change guard off:
  <reason>` on stderr (the `Runner` never writes stderr); a `null` from a run that ended before
  any post-start measurement (e.g. `interrupted` on turn 1) carries no reason. The guard never
  rejects or nudges on a fingerprint it does not have (invariant 5). A sandbox without a `bash`
  attribute (test doubles; `drain_notices` precedent at `runner.py:600`) is the same as a failed
  start fingerprint.
- **Cost.** 60–80 ms host / ≈106 ms container (with the `/gitdir` alternates layout) on this
  repository; 240 ms with seven nested repositories (one scratch cycle each); ≈1.1 s with 2 000
  extra untracked files, whose blobs are now written to the scratch directory instead of the real
  store (P4). A 2 GB worktree at the budget ceiling is bounded by `FINGERPRINT_TIMEOUT`.
- **What the lines mean.** Each line is one repository's working tree; the root line never
  reflects a nested repository (excluded, not a gitlink), a commit inside a nested repository moves
  nothing by itself (its files are unchanged), and only the root's `HEAD` line tracks commits. The
  script prints the nested lines in `find`'s order (not topological, P4); the parser sorts them,
  so the fingerprint does not depend on it.
- **Known limits.** Ignored files never count — a run whose only output is under `.gitignore`
  reads as unchanged, which is also what `files_changed` says of it today. **Host mode:**
  `build_env` redirects `HOME` into the worktree, so `HOME`-keyed caches an install or test run
  writes (`.npm/`, `.cache/`, `.nuget/`, `.dotnet/`, …) are untracked, non-ignored paths and count
  as a change — the same way `files_changed` and `host_worktree_dirty` count them today; a
  feedback resume that only runs `npm ci` therefore passes the finish check in host mode, and the
  K-turn nudge is delayed by one window rather than suppressed. Docker mode's `HOME` is a tmpfs
  outside `/work` and is unaffected; an operator who wants host mode to ignore such caches
  redirects them per tool (`PIP_CACHE_DIR`, `npm_config_cache`, `NUGET_PACKAGES`, …) or lists them
  in the worktree's `.git/info/exclude`. **Only the root's `HEAD` is fingerprinted**: a commit
  inside a nested repository that leaves its working tree as it was reads as no change (P4). A
  fingerprint killed at `FINGERPRINT_TIMEOUT` (SIGKILL — the `EXIT` trap does not run) leaves its
  `$tmp` under `$TMPDIR` — the container's tmpfs, reset with the container; the OS temp dir on the
  host. A worktree with more unique non-ignored content than `$TMPDIR` holds (`--tmp-size`, 1 GiB
  default; the scratch store receives every blob not already in the real store) or an unreadable
  file (`chmod 000`) makes `git add` fail → `(None, reason)` with git's diagnostic (fail open,
  explained). A path containing a newline anywhere under a nested `.git`'s parent makes `find`'s
  NUL record read fine but a later `cd` may fail → `(None, reason)`. Two nested repositories share
  nothing; a nested `.git` whose directory was deleted makes its files plain blobs of the parent
  (P3). The guard is not adversarially robust — a worker can touch a file, commit, or configure a
  `clean` filter in a nested repository's own `.git/config`; it exists to catch a lazy completion,
  not a hostile one (invariant 4).

### 4.2 Net change in `ProgressTracker` (`tools.py`, `runner.py`)

`tools.parse_change_head(result: str) -> tuple[str, str, int, int] | None` — `(verb, path,
added, deleted)` from the head line of a `describe_change` result — and `tools.net_change(result)
-> bool | None`, both defined directly below `describe_change` (`tools.py:421-450`) so the parser
and the producer sit together (`soak_harvest`'s `_WRITE_*_PATH_RE` may adopt the parser in T4;
optional):

- head `^(Wrote|Edited|Appended to|Inserted into|Applied \d+ edits? to) (.+): \+(\d+) -(\d+)`
  (with or without the ` (removed N non-blank line(s))` suffix and the diff body) →
  `added + deleted > 0`;
- `^Wrote \d+ bytes to .+ \(new file, \d+ lines?\)$` → `True`;
- the third `describe_change` head, `… : N lines (diff omitted: file too large)`, an `ERROR:` /
  `BLOCKED:` result, a `[output truncated …]`-capped or foreign string → `None`.

`ProgressTracker.note_call` (`runner.py:294-310`): a mutating tool's result with `net_change(result)
is False` is **idle** (no `_progressed`); `True` and `None` keep today's "always progress" (fail
open for a result shape the parser does not know). Consequence: a model that rewrites the same
bytes for `--stall-turns` turns now stalls; `Wrote P: +0 -0` no longer resets the idle count. The
docker backend returns the same `describe_write` / `describe_change` strings (`docker.py:632`,
`:719`), so the rule holds in both modes. `FailureTracker.reset()` on any non-failing execution
(`:914`) is unchanged — the S3 loop is ended by the truncation budget (§3.3), not by this rule.

### 4.3 The finish check

`Runner(…, require_changes: bool = False)`; the CLI passes `require_changes=ctx.feedback is not
None` (`__main__.py:929-941`). Run-scoped state in `Runner.run`: `fp_start`, `fp_check = fp_start`
(baseline for §4.4), `fp_turn` / `fp_value` (the newest fingerprint and the turn it was taken
on), `changed = None`, `changed_reason = None`, `unchanged_finishes = 0`.

**One rule for `changed`:** every successful fingerprint after the start one — completion check,
K check, `finish()` — sets `fp_turn`, `fp_value`, `changed = fp != fp_start` **and
`changed_reason = None`**; a failed K-check or completion-check measurement stores its reason
until the next measurement (so a failure at turn 10 followed by a successful `finish()`
measurement reports `changed` with no reason); a measurement that **raises**
`BudgetExceeded` / `SandboxError` — anywhere — sets `changed = None` and `changed_reason` before
the exception ends the run; a failed measurement in `finish()` sets the same two; `interrupted`,
`timeout`, and a `budget_exceeded` / `sandbox_error` caused by a *tool call* or verify report the
newest known value (`null` only if none was taken — a run ending on a K-check turn reuses that
turn's measurement). `changed` is therefore never a stale verdict from an
earlier completion: `finish()` re-measures unless this turn already did.

`check_verify(final, via)` (`runner.py:746-786`) — the one function both completion paths go
through — gains the change check **before** anything else, so a rejected completion never runs the
verify command:

```python
def check_verify(final, via):
    nonlocal stuck, unchanged_finishes
    try:
        fp = take_fingerprint() if fp_start is not None else None   # None: guard off / this one failed
    except BudgetExceeded as e:
        resolve_finish(f"run not finished: change check could not run ({e.reason})")
        return finish("budget_exceeded", e.reason), None
    except SandboxError as e:
        resolve_finish(f"run not finished: change check could not run ({e})")
        return finish("sandbox_error", str(e)), None
    if fp is not None and fp == fp_start:
        if unchanged_finishes == 0:
            unchanged_finishes = 1
            stuck = None; repeats.reset()          # a rejection round is a fresh episode (as verify feedback)
            record = self.transcript.write("nudge", kind="unchanged_finish", turn=turns)
            if record is not None:                 # Transcript.write -> dict | None (as :776)
                record["via"] = "tool_result" if via == "finish_result" else "user"
            text = UNCHANGED_REQUIRED if self.require_changes else UNCHANGED_PLAIN
            resolve_finish(text)                   # finish path: the finish tool's own result
            return None, text                      # plain path: the caller delivers it as a user message
        if self.require_changes:
            resolve_finish("run not finished: nothing changed")
            return finish("unchanged", final), None
    … existing verify logic unchanged …
```

- **Order.** Change check → verify. The rejection consumes no verify round; a verify-feedback
  round that follows an edit is unaffected; a `stuck` latched in the same turn is cleared as it is
  for verify feedback (`:768-775`). A rejection on the run's last turn ends `max_turns` at the top
  of the next (invariant 4 holds: nothing was accepted).
- **Carriers.** On the finish-tool path the text *is* the finish `tool_result.result` — the
  `VERIFY_FEEDBACK` pattern (`resolve_finish`), so `runs show` already renders it as `not finished`
  (`runs.py:276-278`) and the history never carries a user message after a tool result (#60 §3).
  The turn's remaining tool calls have already run (finish is checked after the loop, `:958`);
  the finish tool message and its transcript record **keep their call position** — only the
  `result` text is rewritten in place, as #60 defines for every resolution (§"Order of operations
  in a turn": wire order equals call order) — and the timeout/sandbox nudges of that turn are
  delivered as today (`:962-970`). On the plain-answer
  path (`via="user"`, `:843-850`) the text goes out as the user message the caller already
  delivers. The `nudge` event's `via` follows the carrier (`tool_result` / `user`); its text lives
  in the finish `tool_result.result`, not in a `follow_up` (§5.1).
- **Second unchanged completion.** `require_changes` ⇒ status **`unchanged`**, `final_message` =
  the summary, finish result `run not finished: nothing changed`, verify not run, `changed: false`;
  `finalize()` still exports (the evidence is the prior work, unchanged); an export failure leaves
  the status `unchanged` with `export_status` / `finalize_error` carrying it, as for every
  non-`completed` status (`_final_status` rewrites only `completed`, `__main__.py:614-655`).
  Otherwise ⇒ the existing verify logic and `completed`, with `changed: false` on `run_end`.
- **Guard off** (`fp_start is None`, or this fingerprint `None`) ⇒ straight to the existing logic.
- **Exit code.** `unchanged` exits 1 (`__main__.py:1012`, `return 0 if final_status ==
  "completed" else 1`).

Texts (`changes.py`; neutral wording so the same text serves the finish result and the
plain-answer user message):

```python
UNCHANGED_REQUIRED = (
    "Not accepted as the end of the run: nothing in the worktree changed since this run started, "
    "but the reviewer's feedback asks for changes. Apply every item of the feedback and run the "
    "check each item names, then call finish(summary=...). A second completion with no change "
    "ends the run as `unchanged`.")
UNCHANGED_PLAIN = (
    "Not accepted as the end of the run: nothing in the worktree changed since this run started. "
    "If the task requires changes, make them now, then call finish(summary=...); if the task is "
    "complete without changes, call finish(summary=...) and say so in the summary.")
```

### 4.4 The no-change nudge (every K turns)

`DEFAULT_NO_CHANGE_TURNS = 10` (`changes.py`, imported by `runner.py` beside the other
`DEFAULT_*`); `Runner(…, no_change_turns: int = DEFAULT_NO_CHANGE_TURNS)` — a constructor
argument like `stall_turns` so tests can shorten it and `0` disables it; **not** a CLI flag
(decision 4). `check_no_change()` is a sibling of `check_progress()` returning the same shape
`(ended, text, record)` and called right after it at both turn-end sites (`runner.py:855`,
`:985`):

- fires only when the guard is on (`fp_start is not None`), `no_change_turns > 0`, the run
  continues, `turns % no_change_turns == 0`, and the turn carried no `pending_finish` (the finish
  check already measured);
- takes a fingerprint with the same `try/except` as `check_verify` (`BudgetExceeded` /
  `SandboxError` → `finish(...)` returned in the `ended` slot); `(None, reason)` ⇒ nothing
  (baseline kept); equal to `fp_check` ⇒ writes `nudge{kind: "no_change", turn}` and returns the
  text for `deliver()` to join with the stall/malformed/timeout/sandbox texts of that turn (the
  ordinary carrier: `follow_up` on the last tool result, or the user message on a no-tool turn);
  different ⇒ no nudge; either way `fp_check = fp`.

Windows are aligned to the run (K, 2K, …); a change observed by the finish check does not move
`fp_check`. A write on turn 3 followed by reads means the K check sees a changed tree (no nudge) and
the 2K check nudges — at most one window late, stated in the docs. Three texts, keyed on the
guard's own verdict and the mode — **with `require_changes` the text never names `finish` on a
tree equal to `fp_start`**, where a finish is certain to be rejected (S14 models finish on the
slightest invitation); without it the tree-unchanged text keeps the conditional, because a
read-only task may legitimately be complete (C1 (d) schedules exactly one):

```python
NO_CHANGE_SINCE_START_REQUIRED = (        # require_changes: an edit is what the run is for
    "Nothing in the worktree has changed since this run started ({k} turns or more) and the "
    "reviewer's feedback is not applied yet. Make the first edit now: stop reading whole files — "
    "grep -n for the line you need to change, then edit it.")
NO_CHANGE_SINCE_START_PLAIN = (           # mirrors UNCHANGED_PLAIN: a read-only task may be done
    "Nothing in the worktree has changed since this run started ({k} turns or more). If the task "
    "needs changes, make the first edit now — stop reading whole files: grep -n for the line you "
    "need, then edit it; if the task is complete without changes, call finish(summary=...) and "
    "say so in the summary.")
NO_CHANGE_RECENT = (
    "No file in the worktree has changed in the last {k} turns and nothing was committed. If the "
    "task needs more changes, stop reading whole files — grep -n for the line you need, then edit "
    "it; if the task is complete, call finish(summary=...).")
```

The `stall` nudge (`STALL_NUDGE`, fires at `--stall-turns // 2` idle turns) is unchanged; the two
answer different questions (nothing *new* vs nothing *changed*) and rarely coincide; when they do,
both ride the same carrier (order in §5.1). **This nudge is diagnostic**: it produces a `nudge`
event and the `changed` evidence and carries the wording that moved W7 when it came as a preamble;
whether a nudge riding a tool result moves a model is what §8's replay measures — the spec does not
claim it.

### 4.5 The resume task and the resume gate (`resume.py`, `__main__.py`)

`build_resume_task` keeps its inputs and markers and changes the **order and framing** of the
block (both variants, one builder):

```
{prior_task}
--- RESUMED RUN: REVIEW FEEDBACK ---
The last events of the earlier run were:
{transcript_tail}
That run ended with status '{prior_status}' after {turns} turns; its events above are history,
not instructions.
A reviewer read that run's work and sent this feedback — none of it is applied yet:

{feedback}

The worktree already contains the earlier run's work: inspect it with `git status` and `git diff`
first, then apply every item of the feedback and run the check it names. Make no other changes.
The harness does not accept a completion that changes nothing; a second one ends the run as
`unchanged`.
When every item of the feedback is applied, call finish(summary=...).
```

The plain variant, in full:

```
{prior_task}
--- RESUMED RUN ---
The last events of the earlier run were:
{transcript_tail}
That run ended with status '{prior_status}' after {turns} turns; its events above are history,
not instructions.
The worktree already contains that run's work: inspect it with `git status` and `git diff` before
doing anything else, and continue from there — do not start over or revert prior work.
When the task is complete, call finish(summary=...).
```

The tail is rendered exactly as today (`render_transcript_tail` unchanged, 12 000 chars): the prior
`finish` / `run_end` lines stay — they are the truth — but they are followed by the status sentence
and the feedback rather than by "call finish". Whether that is enough against a `completed` tail is
measured, not assumed: §8 records, for every feedback resume of the build, whether its first
completion was a zero-change one (the ledger's A/B row against #61's 4 of 12). Both markers are
still stripped from the prior task before a new block is built.

Resume gate (`__main__.py:842-847`): a prior that ended **`unchanged`** requires `--feedback`
exactly as `completed` does — "run '<slug>' ended 'unchanged' (the worker changed nothing); pass
--feedback to tell it what to change" — because a plain resume strips the feedback block and would
let the same non-work end `completed`. Every other status is unchanged.

## 5. Transcript and evidence

### 5.1 Events, kinds and fields (schema v2, additive)

- **`nudge`** gains two documented `kind` values:
  - `no_change` — the §4.4 guard; `turn`, `via` as every nudge (`tool_result` when it rode the last
    tool result's `follow_up`, `user` on a no-tool turn); at most one per K turns.
  - `unchanged_finish` — the §4.3 rejection; `turn`; `via` = `tool_result` on the finish-tool path
    (the text is that finish `tool_result.result` — the first documented nudge whose text is not a
    `follow_up`) or `user` on the plain-answer path. At most one per run.
  `EMPTY_REPLY_NUDGE_KINDS` is unchanged: neither kind is a model-failure strike.
- **`run_end` / `run.json` / stdout JSON** gain:
  - `truncations` — integer, **always**; turns on which a truncation nudge or
    `truncated_call_result` was produced (§3.3); `0` when none.
  - `changed` — `true` / `false` / `null`, **always**: whether the newest worktree fingerprint
    differed from the one at run start (§4.3's rule); `null` when the guard could not measure.
  - `changed_reason` — string, **sparse**: present exactly when `changed` is `null` because a
    fingerprint was attempted and failed or raised (§4.1: the first diagnostic line, ≤ 200 chars,
    e.g. `error: 'vendor/x/' …`, `ERROR: command timed out after 60 s…`, `[output truncated at
    10000 chars — bash output capped]`, `budget: …`, `sandbox: …`, `sandbox has no bash`); absent
    when `changed` is `null` only because the run ended before any measurement after the start
    one, and on the `_fail_run` paths, which seed every field.
- **`status`** gains **`unchanged`** — "the run required changes (`resume --feedback`) and the
  worker completed twice without changing the worktree; nothing was verified". Exit 1.
- **`tool_result`** for `finish`: four new documented `result` texts — the two rejection texts,
  `run not finished: nothing changed`, and `run not finished: change check could not run (…)` —
  all `not finished` to `runs show` (`_tool_result_outcome`).
- **Ordering within a turn.** Rejection turn: `assistant` → the turn's `tool_result`s **in call
  order** — the finish record at its own call position, its `result` rewritten in place to the
  rejection text by `resolve_finish` (#60: wire and transcript order equal call order; resolution
  never moves a record) → `nudge{unchanged_finish}` → the `timeout` / sandbox `nudge`s of that
  turn (as today). K-check turn, transcript order on a tool turn
  (`runner.py:986-1001`): `nudge{stall}?` → `nudge{no_change}` → `nudge{malformed_entry}?` →
  `nudge{timeout}?` → sandbox notices; on the no-tool path (`:851-860`): `nudge{kind}` →
  `nudge{stall}?` → `nudge{no_change}` → sandbox notices. Delivered text order (the
  `_join_nudges` argument order at the two sites): malformed, sandbox, timeout, stall, no_change on
  tool turns; kind, sandbox, stall, no_change on the no-tool path. `finish()`: the fingerprint's
  own notices, if any, precede `run_end` via the drain that follows it.
- No new event name (the fingerprint exec leaves none), no new `via` value, `schema_version` 2.

### 5.2 Where counts surface (named against the code)

- `dirtywork/bench.py`: `NUDGE_KINDS` (`:48`) gains `no_change`, `unchanged_finish` **appended at
  the end** (8 → 10); `_event_counts` (`:213-238`) counts them by construction; `_harness_failures`
  (`:268-275`) and `_failure_cell` (`:425-433`) add `unchanged` to the status tuple
  `("stalled", "max_turns", "sandbox_error")`; `_abort_kind` (`:246-252`) recognises the cut-off
  form (§3.3) and its docstring (`:242-243`) names the sixth cut-off reply. Consequences, stated:
  the `summarize` detail `nudges` cell (`:438`) and its legend (`:783`) widen from eight to ten
  slash-separated components; `tests/test_bench.py:845` (`len == 8`), `:844` (`[-2:]`) and
  `:864` (the literal `0/0/0/0/0/0/3/0`, tightened to the full ten-wide cell; the test named for
  "eight kinds" is renamed for ten) change accordingly; the `--compare` harness cell
  (`_harness_cell` / `_harness_counts`, `:607-622`) is unchanged in shape.
- `tools/soak_harvest.py`: `_TRUNCATED_CALL_RESULT_RE` (`:100-102`, comment `:92-99`) becomes
  `^ERROR: your (?:write_file for |\S+ call was cut off at the (?:token limit|--max-tokens cap)\b)`
  — the old `before it completed\.` tail is dropped, since the new generic text puts the numbers
  after the cap — so the F5 detector matches both the historical run dirs (the S14 evidence
  carries the old text) and the new wording — **T1**, with the signature change
  (`tests/test_soak_tools.py:939` calls `truncated_call_result("write_file", raw)`); the per-run
  `nudges` total (`:396`) follows `bench.NUDGE_KINDS`; a new feature code
  **`S14`** in `detect_features` (`:179-235`) fires on `nudge.kind in ("unchanged_finish",
  "no_change")`, `status == "unchanged"` or `run.json.changed is False` — the guard is not folded
  into F8 (stall); `PER_RUN_COLUMNS` (`:36-37`) unchanged — `truncations` and `changed` are read
  from `run.json` into the ledger prose, not a column.
- `dirtywork/runs.py`: the nudge renderers (`:304-305`, `:405-406`) print `kind` generically —
  nothing to change; `MD_RESULT_FIELDS` (`:331-332`) gains `truncations`, `changed`,
  `changed_reason` so `runs show --markdown` prints them beside `timeouts` (the loop skips `None`,
  so a null `changed` does not render); `_tool_result_outcome` (`:276-278`) already classifies the
  rejection texts; `runs list` is unchanged (status shows `unchanged`; `truncations` does not join
  `LIST_COLUMNS` — §9).
- `dirtywork/__main__.py`: `_seed_payload` (`:590-591`) seeds `"truncations": 0, "changed": None`;
  `_contract_fields` (`:609`) carries `truncations`, `changed` and (when present) `changed_reason`
  from `extra`; the `Runner(...)` call (`:929-941`) passes `require_changes`; the resume gate
  (`:842-847`) adds `unchanged`; after `runner.run()` returns (`:954`), one stderr line
  `dirtywork: change guard off: <changed_reason>` whenever `changed_reason` is present (start
  failure, a failed finish-time measurement, a sandbox without `bash`).
- `dirtywork/tools.py`: `parse_exit_code` moves here beside `is_timeout_result` /
  `timeout_result` (`:995-1000`); `runner.py:362`, `:731` import it from `.tools`; `changes.py`
  too (no cycle).
- `tests/test_transcript_schema.py`: `NUDGE_KINDS` (`:20-21`) + 2, `STATUSES` (`:22-24`) +
  `unchanged`, `RUN_END_FIELDS` (`:26-31`) + `truncations`, `changed`, `changed_reason`; the
  real-run field test (`:97-102`) then requires the new fields to be backticked in the schema doc
  — which is why the doc rows land with the code (T1: `truncations`; T3: the rest, §8).

## 6. Docs and contract

- `docs/transcript-schema.md`: the `nudge.kind` row (`:101-119`) documents `no_change` and
  `unchanged_finish` (1.0, #66) with the carrier note of §5.1; the `follow_up` row (`:84`) and the
  nudge prose (`:104-111`) that enumerate the joined-text order gain `no_change` in §5.1's
  positions; the forward-compat paragraph
  (`:22-31`) adds "#65/#66 add two `nudge.kind` values, one status and three `run_end` fields";
  `run_end` rows for `truncations`, `changed` (**always**) and `changed_reason` (**sparse**) beside
  `timeouts` (`:239-241`); the Statuses table (`:243-258`) gains `unchanged`; the `run.json` field
  table (`:325-332`) and the stdout-JSON key list (`:269`) gain the fields; the `tool_result` prose
  for `finish` names the four new `result` texts; the `runs show --markdown` paragraph
  (`:358-359`) is unchanged (no new callout event).
- `docs/machine-contract.md`: the status enumerations (`:180`, `:374-376`, `:415`) gain
  `unchanged` with the exit-1 rule; the `nudge` shape (`:440-443`) lists ten kinds; the stdout-JSON
  example (`:352-353`) and its prose (`:360`, `:365` "the nine keys above" → eleven, `:369-370`,
  `:391-392`, `:405-406`) add `truncations`, `changed`, `changed_reason`; the `append_file` bullet
  (`:246-252`) replaces its two verbatim `truncated_call_result` quotes with a prose description
  plus invariant 1's numbers and the six-cut-off rule (wording is not contract; the numbers are);
  a paragraph under the finish rules: "a completion that changed nothing is rejected once (its
  `tool_result.result` says why); on a feedback resume a second one ends the run `unchanged`; the
  guard detects change, not compliance"; the `--stall-turns` paragraph (`:160-169`) gets one
  sentence on the every-ten-turns `no_change` nudge (not a flag).
- `docs/operating.md`: the resume/feedback paragraph (`:172-187`) describes the new block order,
  the "none of it is applied yet" framing, the rejection, `unchanged`, and the widened gate; the
  troubleshooting list (`:398-482`) gains three entries: **status `unchanged`**; **`changed:
  null` / `changed_reason`** (guard off: the reason, the over-budget resume); **`changed: true`
  on a run that edited nothing (host mode)** — the `HOME` cache limit with the per-tool redirects
  and `.git/info/exclude` (§4.1); the `--max-tokens` paragraph
  (`:330-341`) says the truncation messages now state the cap and a per-call target and that six
  cut-off replies end the run `model_error`; the bench harness-count sentence (`:370-371`) if it
  enumerates kinds.
- `README.md` (`:172`, `:191-193`, `:195`): the truncation-recovery sentences mention that the
  message states the cap and the chunk size; the abort-rule sentence appends "or six cut-off
  replies at `--max-tokens`".
- `docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md`: a new section for this build (one row
  per run, the S14 A/B row of §4.5, the C1 acceptance of §8, the F5 reruns).

## 7. Tests

Doubles and fixtures (named so no ad-hoc class grows):

- `tests/provider_doubles.py::FingerprintSandbox(HostSandbox)` — `bash(command, timeout)` records
  `(command, timeout)`; for `command == FINGERPRINT_SCRIPT` it pops the next scripted entry: a
  string `h` → `"exit code: 0\n<h>\n<head>"`, `None` → `"exit code: 1\nerror: boom"`, an
  exception instance → raised (the last entry repeats); every other command delegates to
  `HostSandbox.bash` (so `--verify` commands and file tools are real). Runner tests that need a
  *real* fingerprint use the **`git_parts`** fixture: `parts` plus `git init` + one commit in
  `wt` (the plain `parts` fixture's `wt` is deliberately not a repository: `git add` fails →
  guard off → `changed: null`, `changed_reason` set, exactly one extra `bash` per test — ≈ 5 s
  across the suite, stated).
- Existing doubles: those without `bash` (`BudgetBustingSandbox` `:726`/`:1887`,
  `_GrepTimeoutSandbox` `:1666`, `ExplodingSandbox` `:2613`) pass unchanged via the `getattr`
  rule; those whose `bash` raises unconditionally or asserts on its commands must discriminate on
  `command == FINGERPRINT_SCRIPT` (return `"exit code: 1\n"` / filter it out of `commands`):
  `Raising` `:1857-1876`, `InterruptingSandbox` `:1891` (the start fingerprint would otherwise
  fire the interrupt before turn 1), `:1562`, `:1922`, `box.commands == ["npm test"]` `:1804`,
  and `test_finalize_merges_into_run_end_and_result_extra` `:667-671` (exact `extra` gains
  `"truncations": 0, "changed": None`). Verbatim text pins rewritten against the templates:
  `:283-299`, `:361-373`, `:440-461` (`_GENERIC_TRUNCATION` and the `write_file` hint), `:573-577`,
  `:921-930`, `:473/:487/:501`. `tests/test_main.py`: every host run that completes via a plain
  `done` answer now takes two turns — `_first_run` (`:1365-1369`, 17 callers) and the other
  single-turn host runs (`:1463`, `:1631`, `:1823`, `:1830`, `:2016`) pass `--max-turns 2`, and
  `_resume_responses` (`:1372-1380`) inserts a `write_file` before `finish` (the resume tests that
  assert `completed`); assertions on `turns == 1` become `2`. The docker-mode `test_main` fakes
  (`bash` → `""` at `:195/:318/:392/:642`, `"exit code: 0\n"` at `:1539`) run guard-off: their
  payloads carry `changed: null` with a `changed_reason` (`no output` / fewer than two hex lines)
  and the stderr line; nothing else changes for them.

1. `chunk_target`: cap basis when `cut_chars == 0`; basis = min(cap chars, cut chars) when
   present; floors 200 chars / 5 lines; per-line from the arguments (≥ 3 lines) vs
   `DEFAULT_LINE_CHARS`; the worked values of §3.1 (1024 → 1024/17; 2048 → 2048/34; 4096 →
   4096/68; 8192 → 8192/136; case a 3 000/55 → 750/13).
2. `reply_size`: prose only; two calls summed (`_tool_call_arg_chars`). `call_size`: escaped
   `\n` in raw arguments + real newlines; Anthropic's empty `raw_arguments` → `(0, 0)`; a call
   with arguments but no newline → `(chars, 1)`.
3. Text-path nudge: `NUDGES["truncated"].format(**trunc)` is the delivered user message and the
   `follow_up` on a tool turn, with `cap == max_tokens` (a Runner built with `max_tokens=1234`),
   `received == len(text)`, `n == 1`, `max == 6`; a 0-character `length` reply renders "received
   only 0 characters".
4. Tool-path results: `write_file` with a recovered path and the generic form carry the numbers
   (`cut_chars` / `cut_lines` are the cut call's own, `received` the whole reply's); case (b)
   (missing required param) uses the same dict; two cut calls in one turn both get the text with
   the same `n` and a target sized by the first; a reply with one complete call and one cut call
   sizes the target from the cut one only.
5. `truncations` counts once per turn with two truncated calls; text and tool paths both count; a
   `length` turn with a complete call does not (the `:537-554` pin stands); the value is on
   `run_end`, `run.json` and the stdout payload; the failure-path seeds are `0`.
6. Budget abort (the S3 shape): `length` reply, successful `write_file`, `length`, … — six
   truncations interleaved with five successful writes → `model_error`,
   `final_message == TRUNCATION_ABORT.format(n=6, cap=…)`, `truncations == 6`, the aborting turn's
   `nudge{truncated}` has no `via`; when the sixth truncation is also a third consecutive
   `empty_reply` the consecutive reason wins; the tool-path sixth truncation still records its
   `tool_result` before the abort.
7. `bench._abort_kind(TRUNCATION_ABORT.format(n=6, cap=1024)) == "truncated"`; the existing forms
   unchanged; `soak_harvest.detect_features` fires F5 for both the old and the new generic text
   (`tests/test_soak_tools.py`, beside the `:215/:294/:316` fixtures), and `:939` passes the dict.
8. `tools.parse_change_head` / `net_change` round-trip `describe_change` for every verb (`Wrote`,
   `Edited`, `Appended to`, `Inserted into`, `Applied 1 edit to`, `Applied 2 edits to`), with and
   without the `(removed N non-blank line(s))` suffix and the diff body; the new-file form →
   `True`; the `diff omitted: file too large` head, `ERROR:` / `BLOCKED:` / `[output truncated …]`
   / foreign strings → `None`.
9. `ProgressTracker`: `+0 -0` from a mutating tool is idle (`idle_turns` rises); `+1 -0` and the
   new-file form are progress; a `None` result keeps today's behaviour; a new runner-level test:
   byte-identical `write_file` × `stall_turns` → `stalled`.
10. `parse_fingerprint`: two hex lines → the sorted join; four (nested) in two different orders
    → the same fingerprint; rc 0 with an extra `warning:` line → the hex lines, the warning
    ignored; rc ≠ 0 with a git diagnostic → `(None, "error: …")`; empty output; rc 0 with one hex
    line; `timeout_result(60)` (no `exit code:` head) → `(None, "ERROR: command timed out after
    …")`; an `ERROR: bash failed …` and a `BLOCKED:` result → `(None, <first line>)`; rc 0 with
    240 hex lines followed by `_cap`'s `[output truncated at 10000 chars — bash output capped]`
    → `(None, "[output truncated …")` — the partial listing is refused; the reason is capped at
    200 chars.
11. `FINGERPRINT_SCRIPT` passes `check_bash_command` in both modes (host with a worktree,
    sandboxed) and contains `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`,
    `:(exclude,literal)`, `trap`, `git add -A -- .`, `write-tree`, `rev-parse HEAD` (the probed
    shape); `fingerprint(object())` (no `bash`) → `(None, "sandbox has no bash")`.
    **11b — the real script on the host** (`HostSandbox(git_parts.wt).bash(FINGERPRINT_SCRIPT,
    FINGERPRINT_TIMEOUT)`, skipped without `git` on `PATH`): a committed nested repository, an
    unborn one (`git init` only) containing a file, a nested-in-nested one, a nested root named
    `vendor/café` and one named `vendor/sp ace` → rc 0, line count = repositories + 1, all lines
    40-hex; a file written inside the unborn repository changes only its line; a byte-identical
    rewrite at the root changes nothing; the real object store's file count is unchanged after a
    snapshot that includes a new 100 KB untracked file; `$TMPDIR` has no new entry afterwards.
12. Runner start (`FingerprintSandbox`): the first `bash` command is `FINGERPRINT_SCRIPT` with
    `FINGERPRINT_TIMEOUT`, sent after `run_start` and before the first `chat`; `changed` on
    `run_end` is `false` when the finish-time fingerprint equals it, `true` otherwise; a `None`
    start entry → `changed: null`, `changed_reason == "error: boom"`, **no further
    `FINGERPRINT_SCRIPT` command for the whole run** (no rejection, no `no_change` nudge, no
    `finish()` measurement); the stderr line is test 22's (the `Runner` prints nothing).
13. Zero-change finish, `require_changes=False` (`git_parts`, real fingerprint): first `finish` →
    its `tool_result.result == UNCHANGED_PLAIN`, `nudge{unchanged_finish, via: tool_result}`, the
    verify command was **not** run, `stuck` cleared and `repeats` reset; second `finish` →
    `completed`, verify ran once, `changed: false`.
14. `require_changes=True`: second unchanged `finish` → status `unchanged`, `final_message` = the
    summary, finish result `run not finished: nothing changed`, verify never ran, `finalize()`
    called, exit path; a `write_file` between the finishes → `completed`, `changed: true`; a
    `finalize` that raises on an `unchanged` run → status still `unchanged`, `finalize_error`
    set.
15. Plain-answer path: an answer on an unchanged tree is rejected as a **user** message equal to
    the text; `nudge.via == "user"`; the second answer → `completed` / `unchanged` per
    `require_changes`; a rejection on the last turn → `max_turns`.
16. Mixed turn: `finish` plus other calls in one turn — the other calls run, then the rejection is
    the finish result; the turn's timeout nudge is still delivered.
17. Exceptions: `BudgetExceeded` / `SandboxError` from the start fingerprint → `budget_exceeded` /
    `sandbox_error` with a `run_end`; from a completion-path fingerprint → the same statuses with
    the finish result `run not finished: change check could not run (…)`; from the K-check
    fingerprint → the same statuses; `KeyboardInterrupt` from the start fingerprint →
    `interrupted`, `turns == 0`, one `run_end`.
18. No-change nudge (`no_change_turns=3`, `FingerprintSandbox` with a scripted hash sequence):
    three read-only turns with `require_changes` → `nudge{no_change, turn: 3}` with
    `NO_CHANGE_SINCE_START_REQUIRED` (the tree equals `fp_start`; the text never names
    `finish`) as the `follow_up`; a changed
    hash at turn 3 and equal hashes at 3 and 6 → `NO_CHANGE_RECENT` at 6 (names `finish`), none at
    3; the plain-mode text at 3 without `require_changes` (`NO_CHANGE_SINCE_START_PLAIN`); a
    `None` entry at K → no nudge, `fp_check` unchanged, the next window compares against the old
    baseline, `changed_reason` set until the next measurement clears it; a turn with
    `pending_finish` at K → no K check; `no_change_turns=0` → no fingerprint after the start one;
    the guard-off run → none; the transcript and text orders of §5.1 (both paths) when a stall
    nudge coincides.
19. `finish()` fingerprint: a `max_turns` run reports `changed` (the fingerprint is the first
    `bash` call after the last turn, before any `drain_notices`); a docker-style fake whose
    `finalize()` flips a flag after which `bash` returns `ERROR: bash failed` still yields a
    non-null `changed`; a fake whose fingerprint `bash` queues a notice → that `nudge` precedes
    `run_end`; a `None` finish-time entry after a rejection on turn 2 → `changed: null` (not the
    stale `false`) with `changed_reason`; `BudgetExceeded` there → status preserved, `changed:
    null`, `changed_reason` starts with `budget:`, and `run_end.watchdog_violation` /
    `watchdog_violation_kind == "budget"` set when `finalize()` left them unset (and untouched
    when it set them); `no_change_turns=2`, a rejection on turn 1, a `write_file` on turn 3,
    `max_turns=4` → exactly one `FINGERPRINT_SCRIPT` exec on turn 4 (the K check), no finish-time
    exec, `changed: true`; a rejection on turn 2 then `KeyboardInterrupt` on turn 3 →
    `interrupted` with `changed: false` and no `changed_reason`; `interrupted`, `timeout`,
    `budget_exceeded`, `sandbox_error` take no finish-time fingerprint.
20. `build_resume_task`: both variants in full (§4.5) — order (tail before feedback), the status
    sentence, "none of it is applied yet" and the rejection sentence (feedback variant), the
    closing sentence per variant; markers still stripped; the pins at
    `tests/test_resume.py:148-156`, `:272-303` rewritten (the feedback variant ends with "When
    every item of the feedback is applied, …").
21. Resume gate: a prior with `status: "unchanged"` and no `--feedback` → exit 2 with the new
    message; with feedback → runs.
22. End-to-end (`tests/test_main.py`, `--sandbox none`, real linked worktrees): run → `completed`
    in two turns with `changed: false`; `resume --feedback` with a scripted client that calls
    `finish` twice → `unchanged`, exit 1, `run.json.changed is False`, `truncations == 0`; the
    same with a `write_file` between the finishes → `completed`, `changed is True`; the payload
    and `run.json` carry `truncations` and `changed` on the failure paths (`0` / `null`); a
    guard-off run (a fake docker sandbox, or `HostSandbox` on a non-repo) prints exactly one
    `dirtywork: change guard off: <reason>` line on stderr (capsys) and a guard-on run prints
    none; a `resume` of an `unchanged` run without `--feedback` exits 2 with the §4.5 message.
23. Evidence surfaces, in three parts: **23a** the lists in `test_transcript_schema.py`
    (`RUN_END_FIELDS` + `truncations` — T1; `STATUSES` + `unchanged`, `NUDGE_KINDS` + 2,
    `RUN_END_FIELDS` + `changed`, `changed_reason` — T3) and the real-run doc-token test passing
    on each; **23b** `test_bench` widths, the ten-component legend and the renamed test, and
    `soak_harvest.detect_features` firing `S14` for each of its three triggers and not for a run
    with `changed: true` and no guard nudges (F8 unaffected) — T4; **23c** `runs show --markdown`
    prints `truncations` and a non-null `changed`, skips a null one; `_tool_result_outcome` says
    `not finished` for the four new texts — T4.
24. Live (`tests/test_docker_live.py`, docker-marked): `DockerSandbox.bash(FINGERPRINT_SCRIPT)` in
    the worker image under the #61 gitfile layout equals the host fingerprint of the same tree;
    a second call after `write_file` differs; a byte-identical rewrite does not; `/gitdir/objects`
    has the same file count before and after (the scratch object directory).

## 8. Acceptance evidence, gates, and the build

- **P4 is satisfied** (§1.5): the v2 script leaves the object store unchanged, handles unborn,
  non-ASCII, space-containing and gitfile nested roots, and resolves the worker's `/gitdir`
  alternates layout; tests 10–11 and 24 keep it so.
- Built by the released dirtywork (0.10.1) against this repository per the owner's dogfood rule:
  chained runs on branch `issue-65-66-change-guard` off `main` (post-#74), qwen3-coder-next,
  image `dirtywork-worker-pytest:0.10`, ≥ 60 turns, sampler on, one ledger row per run, verify
  gate `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider`; Claude writes
  the prose docs and reviews every branch; a Claude implementer only after a failed
  resume-with-feedback, stated in the PR. Every feedback resume of the build is recorded in the
  ledger's **S14 A/B row** (first completion zero-change: yes/no — an observation of the new
  resume block under the released runtime, which has no guard).
- **C1 — the guard's own acceptance (after T3, before T4's runs; the branch runtime from the
  integration worktree, `python3 -m dirtywork …`, host and docker):** (a) `resume --feedback` of
  a completed prior with the feedback "no change is needed; call finish" → the first `finish`'s
  `tool_result.result == UNCHANGED_REQUIRED` with `nudge{unchanged_finish}`, the second → status
  `unchanged`, exit 1, `run.json.changed == false`; (b) the same prior with one real one-line
  item → `completed`, `changed: true`; (c) a fresh run whose model finishes at once →
  `UNCHANGED_PLAIN` first, then `completed` with `changed: false`; (a′) `resume` of the
  `unchanged` run of (a) without `--feedback` → exit 2 with the §4.5 message; (d) a read-only
  task on the built branch that cannot finish quickly ("read these N files fully with
  `read_file` and list every public function of each in the finish summary; do not edit
  anything", N sized for > 20 turns, `--max-turns 30`) → `nudge{no_change}` at exactly every
  tenth turn before its first completion (`NO_CHANGE_SINCE_START_PLAIN`), `changed: false`, the
  first completion rejected with `UNCHANGED_PLAIN`, the second `completed` with `changed:
  false`; if the first completion arrives before turn 10, double N and rerun once. **C1
  (a)–(d) failing blocks T4 and returns the spec to §4.3/§4.4.** (e) is an **observation**, not
  a gate: a fresh W7-shaped edit task (items not yet on the branch — e.g. the ledger section's
  harvest columns) recorded in the ledger with whether a mutating call came within K turns of the
  first `no_change` nudge (the nudge is diagnostic, §4.4). T4's own dogfood runs then use the
  branch runtime too, so their resumes are guard evidence.
- **F5 reruns** on the built branch (`tools/soak_driver.py` plan rows run from the integration
  worktree, `py-big-fixture`, `--max-tokens` 1024 / 2048 / 4096, qwen and devstral, r1–r2, rows
  carry `--max-turns 60`, driver timeout 1800 s). **Recovery** = `status == completed` **and**
  `run.json.truncations ≥ 1` **and** `fixtures/rows.csv` has 401 lines; `acceptance_passed` (the
  strict per-column `check.sh`) is reported beside it as the task verdict, not the harness
  verdict. Per cap and model, **pass** = both repeats end in recovery; at 1024 a repeat may
  instead end `model_error` with `abort_kind == "truncated"`, `truncations == 6` and ≤ 16 turns,
  and a mixed pair (one recovery, one truncated abort) passes. **Fail** = any run reaching 60
  turns or the 1800 s driver timeout, ending `stalled`, `stuck` or `verify_failed`, or
  `model_error` with `abort_kind == "empty_reply"` — the S2 shape the new text exists to prevent
  — and, **regardless of outcome**, any `truncated` nudge or `truncated_call_result` in the run
  whose `target_lines` is below 13 at 1024, 25 at 2048 or 50 at 4096 (read from the transcript's
  `follow_up` / user / `tool_result` texts: the direct check that the model saw the cap-based
  target, not a preamble-based floor). `model_error` with another abort kind (`unknown_tool` —
  #67's `[TOOL_CALLS]` names on devstral — `malformed_args`, `mixed`) = inconclusive: one
  tie-break rerun, a second inconclusive = fail; any other disagreeing pair = one tie-break
  rerun, then the majority. Expected from §3.1: 2048 and 4096 recover in ≈ 12–20 turns; 1024
  recovers in ≈ 25–35 turns or aborts early — never a 40-turn / 900 s sink. Per-run `truncations`
  and the first delivered `target_lines` go in the ledger.
- **Task boundaries** (each independently testable and green on its own — the schema-doc rows and
  test lists land with the code that emits the field; order T1 → T2 → T3 → T4, T4 depends on T1
  and T3; each sized for a 65k context; the plan step refines them):
  - **T1 — #65** (§3): `runner.py` `reply_size`, `chunk_target`, the two texts, `truncations`,
    the abort; `bench.py` `_abort_kind` + docstring; `__main__.py` seed/contract fields for
    `truncations`; `tools/soak_harvest.py` `_TRUNCATED_CALL_RESULT_RE`; `docs/transcript-schema.md`
    `truncations` rows (run_end, run.json, stdout key list); `tests/test_transcript_schema.py`
    `RUN_END_FIELDS`; tests 1–7, the `truncations` assertions of 22, and 23a's T1 half.
  - **T2 — net change and the fingerprint primitives** (§4.1, §4.2): `tools.py`
    (`parse_exit_code` moved, `parse_change_head`, `net_change`); `changes.py` (script, parser,
    `fingerprint`, texts, `DEFAULT_NO_CHANGE_TURNS`); `ProgressTracker`; `FingerprintSandbox` and
    `git_parts`; tests 8–11.
  - **T3 — the guard in the runner and the CLI** (§4.3–§4.5): `runner.py` state,
    `take_fingerprint`, `check_verify`, `check_no_change`, `finish()`; `__main__.py`
    `require_changes`, `changed`/`changed_reason` seed and contract fields, the stderr line, the
    resume gate; `resume.py` block order; `docs/transcript-schema.md` `changed` / `changed_reason`
    rows, the `unchanged` status row, the two nudge kinds; `tests/test_transcript_schema.py`
    `STATUSES`, `NUDGE_KINDS`, `RUN_END_FIELDS`; the existing-double changes of §7; tests 12–22.
  - **T4 — evidence and docs** (§5.2, §6): `bench.py` kinds/status tuple, `soak_harvest.py`
    `S14` feature, `runs.py`, `docs/machine-contract.md`, `docs/operating.md`, `README.md`, the
    ledger section; tests 23b, 23c and 24 (24 is live).

## 9. Out of scope

S4 (a run-level token/wall budget; the truncation budget is per-kind, not per-run); #67
(`[TOOL_CALLS]` tool names); a `--max-truncations` flag (decision 3) and a flag for K (decision 4);
`truncations` or `changed` as `runs list` columns; carrying a prior resume's feedback into a plain
resume (today's stripping; the widened gate covers `unchanged`); a fingerprint that sees changes
under `.gitignore`, or that excludes host-mode `HOME` caches (documented instead); shortening the
resume tail (measured first, §4.5); the Anthropic adapter's empty `raw_arguments` on the error
branch (the numbers fall back to the cap); replaying prior messages on resume (the text tail
stays); tightening `ProgressTracker`'s "new read is progress" rule; `FailureTracker.reset()`
semantics; a sandbox-level "harness exec" that bypasses the stray ladder and budget sample (the
fingerprint accepts `--verify`'s exposure).
