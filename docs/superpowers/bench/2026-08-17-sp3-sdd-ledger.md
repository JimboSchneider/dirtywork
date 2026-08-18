# SP3 SDD ledger (mirror of .superpowers/sdd/2026-08-16-sp3-extensibility/progress.md at branch finish)

# SDD ledger — plan: docs/superpowers/plans/2026-08-16-sp3-extensibility.md
Branch: sp3-extensibility (worktree .worktrees/sp3), base main d9533c8 (v0.5.1). Spec: docs/superpowers/specs/2026-08-15-review-response-design.md §Sub-project 3 (lines 548–687).

## Phase 0 — plan re-baseline (2026-08-17)
The plan was written 2026-08-16 against pre-SP2.5 code. SP2.5 (v0.5.0/0.5.1) rewrote runner.py (finish tool, FailureTracker, ProgressTracker/stalled, reply classification, resolve_context_window), __main__.py (RunContext stages, resume), tools.py (finish schema, canonical_args). Tasks 3 and 6 mandate "full rewrites" of runner.py/__main__.py/test_runner.py — executing them as written would delete shipped behaviour. Ruling: revise the plan in place before Task 1 — two Opus revision agents (A: header+Tasks 1–8, B: Tasks 9–15 + new Task 16 --allow-commit), splice, commit as docs, then run the SDD pre-flight scan on the revised plan.
Rulings carried into the revision (R1–R9): see plan "Revision 2" section once written.
Part B (Tasks 9–16) revision done 16:42 — deviations ratified: run.json end key is `ended` (not ended_at); usage/final_message are stdout-only; R6 → a run whose worktree/branch a resume took over keeps both but its own dw-<slug> container/volume are still removed after the label check (they can't belong to the newer run); R8 message uses `--sandbox none (host mode)`; Task 16 is prompt-only (no guardrail blocks git commit; one pinning test); bench fixtures rewritten (sh fixture was self-passing; py fixture must not call pytest — image lacks it); bench never hardcodes a provider. Open: pyproject `packages` must gain `dirtywork.providers` in Task 4 (check part A).
- Task 1: dispatched (BASE 19e6076) 16:45 — qwen via run-task.sh task-1, 60 turns; overlaps with the pre-flight scan (Ruling: Task 1 creates only new files, so scan findings fold into its review round — cost if wrong: one fix round)
- Task 1 run: completed, 6 turns, 55s, 30565/1956 tok, 591 tests; landed f35d3ee (ctrl commit). Review dispatched (Sonnet).
- Task 1 review (Sonnet): ✅ spec compliant, Approved; Minor: register() overwrites duplicates silently (plan-mandated), names() untested. Ruling: Task 2 dispatch adds a duplicate-name ValueError in register() + test — why: extensibility invariant, 3 lines — cost if wrong: trivial.
Task 1: complete (f35d3ee)
- Task 2: dispatched (BASE f35d3ee) 16:50 qwen 60 turns (+ duplicate-name ruling)
- Task 2 run: completed, 11 turns, 109s, 172118/5831 tok, 613 tests, 0 nudges; landed 06f5c11 (ctrl commit). Review dispatched (Sonnet) 16:53.
- Task 2 review (Sonnet): ✅ spec compliant, Approved. Important (plan-mandated): registry clamps timeout to caps.timeout_max even with deadline=None, ToolExecutor only clamps under an active deadline. Ruling: accept registry semantics — Caps.timeout_max is a declared cap and should hold regardless of deadline; the runner always sets a deadline so production behaviour is identical — cost if wrong: none observable. Minor: transitional canonical_args duplication (ToolExecutor removed in Task 3).
Task 2: complete (06f5c11)
## Pre-flight scan (Opus, 17:11) — .superpowers/sdd/.../preflight-scan.md: 53 pairs, 16 tasks, 0 shipped-code drift misses; 3 BLOCKER / 10 DEFECT / 9 NOTE.
Rulings: B1 (T5 empty base_url) → `DEFAULT_BASE_URL if base_url is None else base_url`; B2/B3 (T6 two shipped tests missed) → add to rewrite block / rebuild on ToolCall; D4 counts restated (T3 21/622, T5 31, T14 20, T15 follows); D5 name the ten replaced tests; D6 delete the two renamed context-window originals; D7 T6 Step 12 keeps "record actual, never lower" (accepted — counts depend on upstream tasks; not a code placeholder); D8 DEFAULT_MODEL placed once (top); D9 add the T5→T6 interim-breakage note; D10 `_uid_gid` defined once in runs.py (T10) and imported by bench.py; `FakeCaptured` moves to tests/fake_docker.py (T9) shared with T14; D11 drop dead `slug` param; D12 add Tasks 1–8 coverage/type tables; D13 README row → Task 16; N14 names() in T1 Produces; N15 drop no-op git add; N16 13 docker + 3 live; N17 plain string; N18 run_once catches SystemExit → bench_error row; N19 move import; N20 T16 quote before-text; N21 keep NaN fixture with a comment (robustness against real-world garbage); N22 keep comment. Plan-fix dispatched to Sonnet (docs only, no commit — controller commits after Task 3 lands). Task 3 dispatched in parallel with the count correction (why: T3 has no blocker; cost if wrong: one fix round).
- Task 3: dispatched (BASE 06f5c11) 17:13 qwen 80 turns
- Task 3 run: max_turns (80), 273s, 2912789/15997 tok, 0 nudges; tree complete except brief step 9b (delete moved test) — controller finished it; 623 passed; schema fixture == registry schemas; landed ea4280d (ctrl commit). Review dispatched (Sonnet) 17:20. Note: max_turns after a green suite = re-verify spin (SP2.5 lesson 1) — the stall detector did not fire because each re-run had a new bash fingerprint (test durations vary?) — CORRECTION on inspection: not a spin; the worker reset test_runner.py once (git checkout) and redid the 9a regex script, running out of turns one step from the end. Stall detector correctly silent. Lesson: multi-file patch tasks need ~120 turns (Task 6).
- Task 3 review (Sonnet): ✅ compliant, Approved; Important: importorskip('time') workaround; Minor: dead HostSandbox import, double blank line. Controller fixed all three directly (8257af0, 623 passed); Haiku scoped re-review dispatched.
- Task 4: dispatched (BASE 8257af0) 17:28 qwen 60 turns
- Task 3 fix1 re-review (Haiku): all addressed. 
Task 3: complete (ea4280d + 8257af0)
- Task 4 run: completed, 24 turns, 136s, 169979/3594 tok, 632 tests, 0 nudges, worker committed 1d2223c itself; landed. Review = Workflow sdd-task-review (2 Sonnet lenses + Sonnet refuters) 17:33. Review workflow script: ~/.claude/projects/-Users-jimschneider-repos-dirtywork--worktrees-sp3/08d06ad4-7ebf-4e02-aea8-2b2868982b1a/workflows/scripts/sdd-task-review-wf_49eab1f2-db1.js (reuse via scriptPath + args).
- Plan fixes committed bf5fd65 (7931 lines); finding 19 applied to Task 10 instead of 11 (first use) — accepted.
- Task 5: dispatched (BASE 1d2223c) 17:34 qwen 120 turns
- Task 4 review (workflow, 2 lenses): approved/approved, 0 findings.
Task 4: complete (1d2223c)
- Task 5 run: completed, 29 turns, 185s, 568653/10289 tok, 0 nudges; landed 8868f18 (ctrl commit); 670 passed. Review workflow dispatched 17:38.
- Task 6: dispatched (BASE 8868f18) 17:39 qwen 120 turns
- Task 5 review (workflow, 2 lenses): approved/approved; Minor EOF newlines fixed by ctrl (7ebc940); preamble updated.
Task 5: complete (8868f18 + 7ebc940)
- Task 6 run 1: max_turns (120), 1765s, 5620048/35080 tok, 0 nudges — only runner.py (syntax-broken) + test_runner.py touched, 8 scratch scripts; worker never used edit_file/write_file (66 bash, 48 read_file): the 1084-line brief + re-reads exceeded its working context. Tree discarded. Ruling: split into 6a (test_runner migration, ends red, own commit), 6b (runner.py switch → green), 6c (CLI/doubles/test_main/live) — why: brief size, not reasoning, was the failure; cost if wrong: one more failed run then Sonnet escalation.
- Task 6a: dispatched (BASE 7ebc940) qwen 120 turns
- Task 6a run: context_exhausted (119 turns, 1972s, 5376660/44228 tok, 0 nudges) — Steps 1–2 done (73→61 tests, helpers + blocker tests rebuilt on ToolCall/ChatResponse), Step 3 not reached. Controller applied Step 3 (4 context-window tests; 63 tests) and committed 369a0e6; landed (test_runner.py RED by design until 6b). Second context-bound run → per user: reload qwen at 131072 ctx / parallel 2 before 6b.
- qwen reloaded 131072 ctx / parallel 2 (18:50); launcher passes --context-window 131072. Task 6b: dispatched (BASE 369a0e6) qwen 120 turns
- Task 6b qwen run (131k ctx): max_turns 120 in 320s, 4180292/19406 tok, 0 nudges — real progress (runner.py switched; 65/71 runner tests green; 96 grep-style bash calls, 10 edit_file), 6 failures all in the SP2.5 reply-classification/nudge paths + a `_tag` helper the 6a migration dropped. WIP committed on the worker branch (dw-task-6b…). Ruling: escalate 6b to Sonnet (fix round 2 of Task 6, more capable model) starting from the qwen tree — why: two qwen runs exhausted on this piece; the remaining failures need reasoning about SP2.5 semantics, not transcription — cost if wrong: Sonnet tokens.
- Tasks 7 and 13: dispatched concurrently on qwen (2 slots) 18:52 (BASE 369a0e6) — Ruling: disjoint files (T7: providers/anthropic.py + fixtures + test_provider_anthropic.py + test_providers.py; T13: bench/ + test_bench.py), neither touches Task 6's files; land.sh rebases — cost if wrong: a rebase conflict I resolve.
- Task 7 run: completed, 27 turns, 145s, 469887/5858 tok, worker committed 390e146; landed on top of 369a0e6; suite (excl. runner/main) 558 passed. Review workflow dispatched.
- Task 6b Sonnet fix: DONE 9ffc084 — WIP had dropped the SP2.5 empty/truncated/text-tool-call tail and hoisted the malformed-entry abort; restored. Also restored `_tag`/`_THINK*` + `test_classify_text_reply` (15 cases) that 6a deleted without mandate; test_finish_with_malformed_args rebuilt on `_bad_args()`. 86 runner tests green. Landed (rebased over T7 390e146). Suite excl. test_main: green. 6c next (qwen 131k, 100 turns).
- Task 6c: dispatched (BASE 1aadd61) 18:59 qwen 131k, 100 turns
- 19:00 LM Studio model CRASH (log: "The model has crashed without additional information") with T13 + 6c both mid-prompt at 131k×2; sample 15s earlier: wired 55.9 GB, free 1.2 GB. Both runs → model_error. Reloaded 131072 / --parallel 1 (19:03). Ruling: one worker at a time from here (concurrency only at ≤98k×2 if ever) — cost: no overlap. T13 worker tree kept (bench/ + tests/test_bench.py written, .volta/ junk) → will resume it; 6c tree empty → re-dispatch fresh.
- Task 13: landed 42ae0c2 (ctrl commit of the crash-interrupted but complete tree; 649 passed excl. test_main). Task 6c: re-dispatched (BASE 42ae0c2) qwen 131k solo, 100 turns 19:02
- Task 7 review (workflow): approved/approved; 2 Minor (DRY sanitize_usage, base_url falsy check) fixed by ctrl a8b14f8; Haiku re-review dispatched.
- Task 13 review (workflow): approved/approved, 0 findings.
Task 13: complete (42ae0c2)
- Task 7 re-review (Haiku): all addressed.
Task 7: complete (390e146 + a8b14f8)
- Task 6c qwen run 2 (131k solo): max_turns 100 in 6.8m, 5258357/20340 tok, 4.1 s/turn, 13k prompt tok/s, tool mix bash:33 edit_file:22 grep:13 read_file:34 — real progress: __main__.py patched, provider_doubles.py written, test_main 36/40 green (4 left: sandbox_error/budget_exceeded status mapping ×2, diff_stat/untracked from fake writes ×2). WIP committed 1017ae5. Ruling: Sonnet finishes 6c from that tree (same as 6b) — cost: Sonnet tokens.
- USER DECISION (19:11): platform-support docs fold into this branch as a final docs commit (after Task 16, with the bench write-up): README "Platform support" table (developed & benchmarked: macOS Apple Silicon + LM Studio; CI-tested: Linux x86_64 + macOS; unsupported: Windows) + one-liner near the top; same note in docs/index.html; pyproject classifiers Operating System :: MacOS / POSIX :: Linux; "measured on M-series, 128 GB, LM Studio/Metal" header on bench/scoreboard docs.
- Task 6c Sonnet: DONE ed5cf55 → landed f395793 (rebased). Full suite 695 passed. Controller ran the blind-patched suites for real: `-m live` 3 passed (LM Studio), `-m docker` 12 passed (Docker 29.7.2). Task 6 review workflow (path-filtered package, 8 files) dispatched. Task 6 = 6a (qwen 65k ctx-exhausted + ctrl Step 3) + 6b (qwen 131k → Sonnet) + 6c (qwen 131k → Sonnet).
- Task 8: dispatched (BASE f395793) 19:26 qwen 131k, 80 turns
- Task 6 review (workflow, 2 lenses): approved/approved; 5 Minor fixed by ctrl 2074d8e; Haiku re-review: all addressed.
Task 6: complete (369a0e6, 5b12548, 1aadd61, bf10d66, f395793, 2074d8e)
- Task 8 run: completed, 64 turns, 7.1m, 1298711/9510 tok, worker committed eeaeee7 → landed a051b94; 702 passed. Review workflow dispatched.
- Task 9: dispatched (BASE a051b94) 19:35 qwen 131k, 80 turns
- Task 8 review (workflow): approved/approved, 0 findings (full suite 702 confirmed by ctrl).
Task 8: complete (a051b94)
- Task 9 run: completed, 20 turns, 3.9m, 343411/7280 tok; landed efc1c9c (ctrl commit); 716 passed. Review workflow dispatched.
- Task 10: dispatched (BASE efc1c9c) 19:40 qwen 131k, 80 turns
- Metrics: added frontier-vs-local.py + csv (per-UTC-day frontier usage by model/role vs local qwen usage); scoreboard section added. Refresh at milestones.
- Task 9 review (workflow): quality approved; Important = worker misreported subset as full suite (bash 120 s timeout) — ctrl full run 716 passed; preamble updated (timeout: 600, no subset-as-full); Minor FakeCaptured shape fixed 86e8cc4. Ruling: no fix round needed. Note: worker suites are slower than the controller's 28 s (T8/T9 both hit 120 s) — cause unconfirmed (LM Studio CPU contention?); T13's earlier 'hang' was volta fetching Node into HOME=worktree/.volta.
Task 9: complete (efc1c9c + 86e8cc4)
- Task 10 run: completed, 30 turns, 6.9m, 537679/6736 tok; landed 30488eb (ctrl commit); 723 passed. Review workflow dispatched.
- Task 11: dispatched (BASE 30488eb) 19:48 qwen 131k, 80 turns
- Task 10 review (workflow): spec approved; quality needs_fixes → 2 Important (plan-mandated): pristine predicate ×3 → export.worktree_is_pristine; unguarded iterdir → OSError guard. Fixed by ctrl bd8c76b; Haiku re-review dispatched.
- Task 11 run: completed, 37 turns, 7.9m, 1013785/8869 tok, worker committed → landed 803f7a6 (rebased over bd8c76b); 736 passed. Review workflow dispatched.
- Task 12: dispatched (BASE 803f7a6) 19:59 qwen 131k, 60 turns
- Task 10 re-review (Haiku): all addressed.
Task 10: complete (30488eb + bd8c76b)
- Task 12 run: completed, 28 turns, 6.0m, 412983/3651 tok, worker committed → landed eafd0b1; 739 passed. Review workflow dispatched.
- Task 14: dispatched (BASE eafd0b1) 20:07 qwen 131k, 100 turns
- Task 11 review (workflow): needs_fixes ×2 lenses on ONE Important (plan-mandated): clean removed worktree/branch without worktree_belongs_to_repo guard. Fixed by ctrl a26fdfa (+ refusal test, 740 passed); Haiku re-review dispatched. Note the same class of finding as Task 10 (plan snippets that skip existing safety helpers) — flag for the final review's checklist.
- Task 11 re-review (Haiku): addressed.
Task 11: complete (803f7a6 + a26fdfa)
- Task 12 review (workflow): 1 Important (brief-scope gap): verdict keys undocumented in docs/transcript-schema.md → ctrl added 5 rows + post-hoc note (7facd75), verified against cmd_verdict/argparse. Ruling: no re-review for a doc-table edit verified against code. Reviewer noted my risk text was wrong (brief mandates accept|reject|cleanup and allows verdicts on running runs) — brief wins.
Task 12: complete (eafd0b1 + 7facd75)
- Task 14 run: completed, 41 turns, 13.7m, 1100734/12123 tok; landed 9281f0d (ctrl commit); 755 passed. Review workflow dispatched.
- Task 15: dispatched (BASE 9281f0d) 20:22 qwen 131k, 60 turns
- METRICS FINDING (20:30): run-split.py (model vs tool time from transcript timestamps) showed T8–T14 wall was 57–81% TOOL time; slowest calls 121–277 s = pytest runs. Reproduced under guardrails.build_env: tests/test_bench.py 120 s (vs 0.1 s) — the node acceptance check resolves `node` to the volta shim, and with HOME=worktree (VOLTA_HOME dropped by build_env) volta re-downloads Node into <worktree>/.volta every run (the .volta/ junk since T13). Ruling: harness fix on this branch (4eaa5ce): build_env carries VOLTA_HOME/RUSTUP_HOME/CARGO_HOME/NVM_DIR/PYENV_ROOT (kept if set, else defaulted to existing ~/.x dirs) + test + README; worker-env full suite 276 s → 36 s. Workers still launch from main (0.5.1) so T15/T16 still pay it; fixed for 0.6. Same lesson class as the PYTHONPATH fix — SP2.5 follow-up "HOME=worktree caches" now closed.
- Task 14 review (workflow): spec approved; quality needs_fixes → 2 Important (plan-mandated): (a) staging outside the per-case try → sweep aborts on git failure + temp dir leak; (b) _acceptance_base_argv duplicates docker_args._security_args. Ruling: ctrl fixes both after T15 lands (same file), + test for (a); then one re-review covering 14+15.
- Task 15 run: max_turns 60, 18.4m, 1698135/12905 tok — complete in substance (24 bench tests); ctrl committed → landed 05019da; T14 fixes applied c75812d (bench_error on staging failure + test, security_args shared, _stage_repo cleanup); 761 passed. T15 review + T14 re-review dispatched.
- Task 16: dispatched (BASE c75812d) 20:43 qwen 131k, 80 turns
- Task 14 re-review (Haiku): both addressed.
Task 14: complete (9281f0d + c75812d)
- Task 16 run: completed, 63 turns, 6.3m, 2559711/10050 tok; landed 8479bee (ctrl commit); 767 passed. Review workflow dispatched. ALL 16 TASKS LANDED.
- Task 15 review (workflow): 1 Important (plan-mandated): _verdict_for AttributeError on non-dict run.json → ctrl fix b895912 (isinstance guard + test; 768 passed). Refuted finding: 'newest jsonl when no file given' — brief mandates a required positional (my risk text was wrong). Re-review batched with T16's.
- Task 16 review (workflow): 2 Important (doc row placement; test duplication + dropped assertion) → ctrl fix e600d90. Haiku re-review (T15+T16 fixes): all addressed.
Task 15: complete (05019da + b895912)
Task 16: complete (8479bee + e600d90)
## ALL 16 TASKS COMPLETE (21:00). Final whole-branch review (workflow: Opus dimension reviewers + Sonnet skeptics) dispatched on main..e600d90 (768 passed; live 3 + docker 12 green at f395793 — re-run before PR).
- Docs: mirrored scoreboard/ledger/pre-flight scan into docs/superpowers/bench (staged, not committed until final review lands); Sonnet writing platform-support docs.
- FINAL REVIEW (6 lenses/48 agents): 21 confirmed (7 Critical-rated votes: anthropic serializer ×2 paths, runs clean vs --allow-commit, guardrails $TOOLCHAIN roots), 0 refuted, 18 Minor. Ruling: ONE fix wave — two Sonnet implementers in the sp3 worktree on disjoint files (A–F code+tests; G–H README + docker tests), then one scoped re-review; residual Minors adjudicated in ledger. Version bump deferred to the PR conversation.
- Final fix wave landed bdb43c8 (A–F Sonnet + G–H Sonnet; ctrl reverted the $HOME match — $HOME is the worktree in host mode, `rm -rf $HOME/.cache` stays allowed). Gates: 796 unit, 14 docker (incl. new tests/test_docker_runs.py e2e), 3 live. Re-review: 2 Sonnet seats (findings-addressed; new-breakage). Residual Minors adjudicated: version bump → PR conversation (0.6.0 proposed); runner's OpenAI wording for malformed entries, turns 1→0 on unreadable body, `from .__main__ import run_once` double import (plan-noted), docs/index.html feature copy → follow-ups, not blockers.
- Re-review seats: findings-addressed → all 23 ADDRESSED; new-breakage → 1 Critical (B4 kept the run dir on the benign 'already removed' outcome → every routine docker cleanup would need --force). Ctrl fix 6c13c44 (absent-container/volume outcome + test). 797 unit / 14 docker / 3 live green.
## BRANCH COMPLETE (2026-08-18 08:35) — 39 commits over main d9533c8; PR next (merge/release need Jim's go-ahead; version bump proposed 0.6.0, not applied).
