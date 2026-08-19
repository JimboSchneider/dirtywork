# SDD ledger — plan: docs/superpowers/plans/2026-08-18-run-evidence-and-review-loop.md
Spec: docs/superpowers/specs/2026-08-18-run-evidence-and-review-loop-design.md (binding). Worktree: .worktrees/re-0.8, branch run-evidence-0.8. Baseline 837 passed (interpreter: /usr/bin/python3 = 3.9.6 floor; homebrew python3 has no pytest).

## Pre-flight scan (2026-08-18 20:30)
Method: parsed all 156 Before/After pairs; every Before exists verbatim in the repo or in an earlier After / insertion block (3 chain via plain insertions: T2 edit_file <- T1's last-line change; T5 test header <- T5's own append; T8 README bullet <- T6's insertion). Type-consistency checklist at plan end reviewed.
| Pair | Shared surface | Produces / consumes | Finding |
|---|---|---|---|
| T1/T2 | tools.py edit_file, docker.py edit_file | T1 describe_change + new success string; T2 refactors into _transform_file and calls describe_change verb "Inserted into" | chain verified; ok |
| T2/T3 | runner._MUTATING_TOOLS | T2 adds insert_before/after; T3 wires RepeatTracker beside note_call | disjoint edits; ok |
| T3/T4/T5 | runner finish closure, RunResult.extra, __main__._emit_result/_update_run_json, runs SHOW_FIELDS/MD result, README status list, transcript-schema | stuck_on -> evidence fields -> verify; each Before = prior After | chain verified; ok |
| T4/T7/T8 | workspace.py git_env/GIT_NEUTRAL_FLAGS (T4) used by snapshot (T7) and host_worktree_dirty (T8) | T4 must land first | order respected; ok |
| T5/T9 | test_main resume-of-completed tests | T5 adds a skip marker for --feedback until T9 removes it | explicit in both tasks; ok |
| T6/T8 | README --image bullet | T6 inserts, T8 anchors on it | verified; ok |
| T7/T8 | workspace.snapshot_worktree; runs._worktree_is_dirty vs workspace.host_worktree_dirty | Ruling (plan patched): runs._worktree_is_dirty delegates to host_worktree_dirty | ok |
| T8/T9 | __main__ run/resume parsers, _write_run_json_start | branch_from_run (T8) and feedback (T9) fields | additive; ok |
| T10 | version, consolidated README JSON, transcript-schema field list | depends on all | ok |
Self-consistency per task: T5 tests updated to fix-round semantics (verify_rounds=0 / =1) match implementation `verify_rounds_used > self.verify_rounds`. T3 tests match same-command-reset rule.

## Rulings (pre-execution, applied to plan text 3febb01)
- Ruling: --verify-rounds N = fix rounds after a failed verify (default 1 = one retry; 0 = verify once) — the spec's literal `rounds_used < verify_rounds` with default 1 gave zero fix rounds, contradicting issue #35 — cost if wrong: one flag's semantics/README wording.
- Ruling: stuck streak resets only when the SAME command passes; other passing bash calls neither count nor reset — otherwise `git status`/`cat` between failing test runs hides the streak — cost if wrong: a few more `stuck` ends than intended (opt-out via --stuck-repeats 0).
- Ruling: runs._worktree_is_dirty delegates to workspace.host_worktree_dirty (DRY) — cost if wrong: none material.
- Plan-agent decisions accepted: write_file best-effort pre-read (old_text=None → "new file"); resume inherits only the verify command; snapshot temp index in tempfile dir; runs snapshot refuses a pristine (.git-only) worktree; completed-without-feedback gate ordered last in _load_resume_target.

## Tasks
Task 1: implemented d9f8248 (844 passed); review dispatched 20:37
Task 1: review — spec ✅, Approved; 1 Important plan-mandated DRY (docker write_file repeats _oversized/_rel that _write_raw runs); Minors (deferred): docker write_file pre-read adds an exec whose DockerError-on-timeout aborts the run (consistent with edit_file); docker tests cover only the missing-file pre-read case.
Task 1: Ruling: remove the duplicated checks from docker write_file (trust _write_raw) — brief mandated the redundancy — cost if wrong: one wasted head exec on an invalid write.
Task 1: fix round 1/5 dispatched (resumed implementer) 20:41
Task 1: fix round 1/5 (1 addressed, 0 open — docker write_file trusts _write_raw; commits d9f8248..12597e2)
Task 1: complete (commits 3febb01..12597e2, review clean) — 844 passed
Task 2: implementer dispatched 20:46 (BASE 12597e2)
Task 2: implemented d4e13c7 (854 passed); review dispatched 20:54
Task 2: review — spec ✅, Approved, no Important. Minors (deferred): docker _read_raw UTF-8 error says "refusing to edit" for inserts (pre-existing wording, host names the tool); insert_text `where` not validated (internal only); README "insert_* echoes a diff" imprecise for new-file write; transcript-schema `result` row does not name insert tools; no test that _MUTATING_TOOLS counts inserts as stall progress.
Task 2: complete (commits 12597e2..d4e13c7, review clean) — 854 passed
Task 3: implementer dispatched 20:58 (BASE d4e13c7)
Task 3: implemented da85731 (862 passed; DONE_WITH_CONCERNS: pre-existing extra-equality test widened for stuck_on=None); review dispatched 21:06
Task 3: review — spec ✅, Approved, no Important. Minor (deferred, plan-mandated): README exit-code line wraps long.
Task 3: complete (commits d4e13c7..da85731, review clean) — 862 passed
Task 4: implementer dispatched 21:11 (BASE da85731)
Task 4: implemented 3194b10 (869 passed); review dispatched 21:20
Task 4: review — spec ✅ but quality Needs fixes: 1 Important (last_tool_result not updated in the malformed_entries loop though a tool_result event is written there). Minors (deferred): docker truncation path + markdown "list truncated" note untested; sort→cap idiom appears in host and container computations (kept separate deliberately).
Task 4: fix round 1/5 dispatched (resumed implementer) 21:26
Task 4: fix round 1/5 (1 addressed, 0 open — note_last_tool_result helper shared by both loops + test; commits 3194b10..f6b02aa)
Task 4: complete (commits da85731..f6b02aa, review clean) — 870 passed
Task 5: implementer dispatched 21:30 (BASE f6b02aa)
Task 5: implemented 2918028 (876 passed +1 skip; DONE_WITH_CONCERNS: extra-equality test widened for verify=None; plain-answer verify test given verify_rounds=0 — consequence of the rounds ruling, correct); review dispatched 21:41
Task 5: review — spec ✅, Approved; 1 Important: README "see the callout under Review a run" dangles. Ruling: forward reference — Task 6 (spec §5) adds that callout; carry into Task 6 dispatch and verify there — cost if wrong: one dangling doc sentence. Minors (deferred): RepeatTracker._failed could delegate to parse_exit_code (3-line duplication — FIX IN FINAL WAVE); verify_timeout clamp untested. ⚠️ resume verify-inheritance path only covered by the Task-9-skipped test (by design).
Task 5: complete (commits f6b02aa..2918028, review clean w/ ruling) — 876 passed + 1 skip
Task 6: implementer dispatched 21:47 (BASE 2918028)
Task 6: implemented 0ff4d65 (877 passed + 1 skip); review dispatched 21:51
Task 6: review — spec ✅, Approved, no findings; Task 5 forward reference resolves (README Review-a-run callout).
Task 6: complete (commits 2918028..0ff4d65, review clean) — 877 passed + 1 skip
Task 7: implementer dispatched 21:53 (BASE 0ff4d65)
Task 7: implemented 2c4bee4 (884 passed + 1 skip); review dispatched 21:58
Task 7: review (Opus, security) — spec ❌: CRITICAL worker filenames steer git stdin protocols (update-index --index-info C-unquoting; hash-object --stdin-paths CR stripping); IMPORTANT branch≠HEAD unchecked; IMPORTANT plan-mandated dup of resume.check_resumable in cmd_snapshot; Minors: empty-tree guard only at CLI level, _skipped discarded, unreadable-file docstring wrong, rev-parse failure swallowed, no return annotation, six identical error blocks.
Task 7: Ruling: fix all in round 1 — -z/NUL index-info, control-char refusal, resolve() worktree, symbolic-ref HEAD == branch check, extract resume.preflight_run_worktree shared by check_resumable/cmd_snapshot (Task 8 = 3rd caller), empty-tree guard moved INTO snapshot_worktree (CLI pristine check removed), rev-parse raise, annotation, _check helper, skipped count surfaced via optional report dict; unreadable files keep failing loudly (docstring fixed) — cost if wrong: extra refusals on odd trees.
Task 7: fix round 1/5 dispatched (resumed implementer) 22:07
Task 7: fix round 1 landed 79837f1 (890 passed + 1 skip); Minor (deferred, FIX IN FINAL WAVE): shared preflight messages say 'resume/resuming' when invoked from runs snapshot — give preflight_run_worktree an action word; re-review dispatched 22:20
Task 7: fix round 1/5 (6 addressed, 0 open; commits 2c4bee4..79837f1). Minors (deferred, FIX IN FINAL WAVE): cmd_snapshot KeyError on malformed run.json (preflight_run_worktree indexes keys; catch KeyError or .get); EMPTY_TREE_SHA is SHA-1-only; CR test uses mid-name \r (add trailing-CR shape); symlink blob hash-object --stdin lacks explicit --no-filters; "(1 non-regular entries skipped)" grammar; preflight messages say resume/resuming from runs snapshot.
Task 7: complete (commits 0ff4d65..79837f1, review clean) — 890 passed + 1 skip
Task 8: Ruling: _resolve_branch_from must call resume.preflight_run_worktree(prior) (ResumeError → PreflightFailure) BEFORE the dirty check/snapshot whenever the run's worktree dir exists (a missing worktree is not an error for @slug — branch from the head, no snapshot); brief predates the Task 7 extraction — cost if wrong: an extra refusal on a live/foreign run's slug.
Task 8: implementer dispatched 22:27 (BASE 79837f1)
Task 8: implemented b964933 (896 passed + 1 skip; DONE_WITH_CONCERNS: brief's Step 1 test fixture swapped for a plain repo — a repo-local clean filter makes git status report snapshotted raw content as modified; host_worktree_dirty has no 10 s timeout the old runs._worktree_is_dirty had). Minors (deferred, FIX IN FINAL WAVE): give _git/host_worktree_dirty a timeout (fail closed); README Security note: the @slug dirty check / runs clean run host `git status` on the exported tree — repo-LOCAL filters (git lfs install --local) still apply, same as running it yourself. review dispatched 22:39
Task 8: review — spec ✅, Approved; only Minor: host_worktree_dirty lost the 10 s timeout (already in FINAL WAVE list).
Task 8: complete (commits 79837f1..b964933, review clean) — 896 passed + 1 skip
Task 9: implementer dispatched 22:43 (BASE b964933)
Task 9: implemented 71d5375 (902 passed, skip removed); review dispatched 22:52
Task 9: review — spec ✅ (one textual nit), Approved. Minors (deferred, FIX IN FINAL WAVE): drop the mid-sentence "\n" after "and" in the feedback block (resume.py ~227); non-UTF-8 --feedback-file untested; `--feedback ""` reads as no feedback (docstring note).
Task 9: complete (commits b964933..71d5375, review clean) — 902 passed
Task 10: implementer dispatched 22:57 (BASE 71d5375)
Task 10: implemented e20d83c (903 passed); review dispatched 23:00
Task 10: review — spec ✅, Approved, no findings.
Task 10: complete (commits 71d5375..e20d83c, review clean) — 903 passed
ALL TASKS COMPLETE 23:02. Final whole-branch review dispatched (Opus) over 3febb01..e20d83c (merge-base main = 5e41a78; 3febb01 = spec+plan docs only).
FINAL REVIEW (Opus, 23:15): Ready WITH FIXES. Critical: describe_change uses SequenceMatcher(autojunk=False) → quadratic (233 s on 80k lines) in-process, uninterruptible. Important: (2) resume inherits verify only from verify.command which is null on max_turns/stalled/stuck/timeout ends; (3) latched `stuck` ends the run stuck on the turn after a verify feedback round; (4) host_worktree_dirty lost the 10 s timeout. Minors #5–#14 + deferred triage (see agent report). Security half verified clean.
Ruling: FINAL FIX WAVE (one dispatch) = Critical #1 (drop autojunk=False + size guard 20 000 lines → header + "[diff omitted: N lines]"), #2 (record verify_command/verify_rounds/verify_timeout at run start; resume inherits all three unless overridden; README/schema updated — matches spec §4.1 better than the plan agent's command-only decision), #3 (clear stuck + reset RepeatTracker when a verify fix round begins), #4 (_git timeout param; host_worktree_dirty timeout=10, TimeoutExpired → dirty), plus cheap minors: #5 surrogate paths refused, #6 cmd_snapshot uses load_prior_run, #8 _failed delegates to parse_exit_code, #10 files_changed empty renders '-', #12 symlink hash-object --no-filters, resume/resuming wording (preflight_run_worktree action word), feedback-block newline, "1 entries" grammar, README security note (git status on exported tree runs repo-local filters), verify_timeout clamp test, trailing-CR test shape. FOLLOW-UPS (not this release): #7 slug validation parity for @slug, #9 SHA-256 empty tree, #11 UTF-8 wording parity, #13/#14 perf nits, docker truncation tests, insert-tools schema `result` row, _MUTATING_TOOLS test, `--feedback ""` docstring. Cost if wrong: a slightly larger diff to review.
Final fix wave dispatched 23:17 (BASE e20d83c)
Final fix wave landed 2c82475 (917 passed + 1 fs-dependent skip); scoped re-review dispatched 23:40
Final fix wave re-review (Opus): all 15 findings ADDRESSED, no new Critical/Important. Residuals PARKED — Ruling: ship as-is, fold into a follow-up commit on Jim's word: runner.py:182 docstring still names deleted RepeatTracker._failed; runs.py:749 hand-rolls the "no such run" message _open_run also builds; __main__.py:224 --branch-from @slug calls preflight_run_worktree with default action="resume" (message says "before resuming"; one-word fix action="snapshot"); runs.py:762 redundant branch check after load_prior_run; verify_rounds/timeout explicit-null in a hand-edited run.json → TypeError (unreachable from dirtywork-written files). Cost if wrong: cosmetic.
FOLLOW-UP ISSUES (not this release): @slug vs runs snapshot slug validation parity (#7); SHA-256 empty-tree constant (#9); host/docker UTF-8 refusal wording parity (#11); single SequenceMatcher pass + hash-object -w before no-op check (#13/#14); docker files_changed truncation + markdown note tests; transcript-schema `result` row naming insert tools; _MUTATING_TOOLS insert test; `--feedback ""` docstring; docker write_file pre-read failure-mode tests.
BRANCH COMPLETE 23:47 — head 2c82475, 917 passed + 1 fs-dependent skip (baseline 837). 15 commits over main 5e41a78 (2 docs + 13 code).
