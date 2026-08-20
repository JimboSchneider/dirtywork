# SDD ledger — plan: docs/superpowers/plans/2026-08-19-tools-context-timeouts.md
Spec: docs/superpowers/specs/2026-08-19-tools-context-timeouts-design.md (v3.3, binding; owner-approved v3 + amendments). Worktree: .worktrees/tc09, branch tools-context-0.9 (head 9fc1e91). Baseline 926 passed + 1 fs-dependent skip (+1 pre-existing ResourceWarning in test_docker_sandbox::test_reset_creates_a_fresh_tether — GC-timing, not ours; hygiene minor). Interpreter: /usr/bin/python3 (3.9.6 floor; only one with pytest).

## Pre-flight scan (2026-08-19 14:05)
Method: (1) spec red-teamed by 3 Opus/Sonnet lenses before approval (4+1+1 blockers fixed → v3); (2) plan red-teamed by 3 Opus lenses — Tasks 1–2 transcribed and RUN to exact predicted counts; Tasks 3–7 one Blocker (junit sample backslashes), 2 Important (bench-cell spec ratification → v3.3; machine-contract stale paragraphs), ~20 minors — all applied in 9fc1e91; (3) controller Before/After chain check: 159 pairs, 1 expected miss (Task 7 docker/README block follows a scripted :0.8→:0.9 replace in the prior step).
| Pair | Shared surface | Finding |
|---|---|---|
| T1/T2 | toolspec ParamSpec.schema/_validate_args → APPLY_EDITS_SPEC; fixture rename (T1) then regeneration (T2) | verified by execution (planrt:tools) |
| T2/T5 | docs/machine-contract.md Tools subsection written in T2 documents T5's timeout text/timed_out (noted as forward reference) | ok |
| T3/T4/T5 | runner finish closure extra dict; _emit_result seeds; _contract_fields (T3) consumed by T4/T5; failure-path _update_run_json; SHOW_FIELDS | chain verified |
| T3/T4 | docs/operating.md sizing section (T3) documents T4's probe (noted) | ok |
| T4/T7 | machine-contract context rows | ok |
| T5/T7 | bench legend; transcript-schema lists; machine-contract example JSON | ok |
| T6 | isolated (CI job, junit_summary, test_budget) | ok |
Rulings so far: spec v3.2 (i=1 wording), v3.3 (bench cell additive) — both recorded in the spec.

## Tasks
Task 1: implementer dispatched 14:08 (BASE 9fc1e91)
Task 1: implemented 3947575 (939 passed); review dispatched 14:12
Task 1: review — spec ✅; 1 Important plan-mandated DRY (scalar check/coerce duplicated flat vs nested). Ruling: extract _check_scalar(ptype, value, label) — leaf only, not wholesale routing — cost if wrong: none material. Minors folded into the round: top-level object schema test; docstring caveat on canonical_args.
Task 1: fix round 1/5 dispatched (resumed implementer) 14:16
Task 1: fix round 1/5 (3 addressed, 0 open — _check_scalar helper, object-schema test, docstring; commits 3947575..2409e18)
Task 1: complete (commits 9fc1e91..2409e18, review clean) — 940 passed + 1 skip (plan predicted 939; +1 from the added object-schema test — downstream predictions shift by +1)
Task 2: implementer dispatched 14:21 (BASE 2409e18)
Task 2: implemented 2b5fcd3 (954 passed); review dispatched 14:31
Task 2: review — spec ✅, Approved, no findings.
Task 2: complete (commits 2409e18..2b5fcd3, review clean) — 954 passed + 1 skip
Task 3: implementer dispatched 14:34 (BASE 2b5fcd3)
Task 3: implemented e3e5017 (965 passed; extra-equality test widened for trimmed_turns — expected); review dispatched 14:47
Task 3: review — spec ✅, Approved. Minors (deferred): runs._md_result `data.get(key) or end.get(key)` could hide a legit 0 when run.json and run_end disagree (unreachable for real runs; pre-existing pattern — FINAL WAVE: use an explicit None check); _contract_fields called twice in _execute (plan-mandated, pure dict — cosmetic).
Task 3: complete (commits 2b5fcd3..e3e5017, review clean) — 965 passed + 1 skip
Task 4: implementer dispatched 14:52 (BASE e3e5017)
Task 4: implemented 7693710 (996 passed; extra-equality test widened again — expected); review dispatched 15:03
Task 4: review — spec ✅, Approved. Minors (deferred): positive-int predicate repeated in runner + openai_compat (two layers; FINAL WAVE optional); GET sends Content-Type with no body (pre-existing style).
Task 4: complete (commits e3e5017..7693710, review clean) — 996 passed + 1 skip
Task 5: implementer dispatched 15:06 (BASE 7693710)
Task 5: implemented 716c4e1 (1009 passed); review dispatched 15:21
Task 5: review (Opus) — spec ✅, Approved. Minors (deferred, FINAL WAVE): verify-feedback continue path skips the timeout nudge (same as the stall convention) — hoist the nudge composition above pending_finish and append to feedback; grep-timeout text duplicated host/docker → GREP_TIMEOUT_TEXT in tools.py; test that a grep timeout sets no timed_out and leaves timeouts 0; compare legend clause noting timeout nudges also count in `nudges`; splat style nit.
Task 5: complete (commits 7693710..716c4e1, review clean) — 1009 passed + 1 skip
Task 6: implementer dispatched 15:27 (BASE 716c4e1)
Task 6: implemented c65a68d (1017 passed); review dispatched 15:33
Task 6: review — spec ✅, Approved. Minors (deferred, FINAL WAVE): junit_summary.py comment stating the XXE trust boundary (CI's own XML; ET does not resolve external entities by default); tests for zero-testcase XML and a testcase with no file/classname (the "unknown.py" fallback).
Task 6: complete (commits 716c4e1..c65a68d, review clean) — 1017 passed + 1 skip
Task 7: implementer dispatched 15:37 (BASE c65a68d)
Task 7: implemented ab553de (1017 passed); implementer filed #43 + commented on #22 per plan Step 12 (outward-facing — content checked OK; NOTE for future plans: issue filing should be a controller/Jim step); review dispatched 15:46
Task 7: review — spec ✅, Approved, no findings.
Task 7: complete (commits c65a68d..ab553de, review clean) — 1017 passed + 1 skip
ALL TASKS COMPLETE 15:48. Final whole-branch review (Opus) over 9fc1e91..ab553de (merge-base main = 5c2128c; 9fc1e91 = spec+plan docs only).
FINAL REVIEW (Opus, 15:57): Ready WITH FIXES. Important: (1) grep-timeout literal duplicated tools.py:524 / docker.py:587 → GREP_TIMEOUT_TEXT + grep_timeout_result() in tools.py; (2) --context-window --help text stale (server step). Minors #3–#9 + triage. Security half verified clean; integration chain traced.
Ruling: FINAL FIX WAVE (one dispatch) = Important 1–2 + Minor 3 (_md_result None check), 4 (verify-feedback path gets the timeout nudge — spec "turns that continue"), 5 (junit classname fallback), 6 (malformed edit item → ERROR string, precondition note), 7 (context_window_source in _DEFAULT_EVIDENCE + RUN_END_FIELDS), 8 (raw docstring for the SyntaxWarning on 3.12+), 9 (machine-contract indent), plus ledger: XXE trust-boundary comment + zero-testcase test, grep-timeout-sets-nothing test, compare-legend clause. FOLLOW-UP (not this release): none load-bearing left. Cost if wrong: a slightly larger diff.
Final fix wave dispatched 15:58 (BASE ab553de)
Final fix wave landed 9672bea (1026 passed); re-review dispatched 16:17
Final fix wave re-review (Opus): all 11 ADDRESSED, no new breakage; M4 loop cases (a)–(d) + multi-round empirically traced; strict-chat-template invariant (no consecutive user messages) holds. Out-of-scope folded into the closing docs commit by the controller: spec §4.3 parenthetical tightened (a finish that continues into a verify fix round DOES nudge); machine-contract.md:24 continuation aligned. PARKED (cosmetic, on Jim's word): tools.py forward-reference ordering of grep_timeout_result vs grep(); test name `test_emit_result_seeds_the_six_evidence_keys_with_defaults` (eight keys now).
BRANCH COMPLETE 16:26 — head 9672bea + docs commit; 1026 passed + 1 fs-dependent skip (baseline 926). 11 commits over main 5c2128c (3 docs + 8 code) + closing docs.
