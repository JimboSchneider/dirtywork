# Operator-ergonomics SDD ledger (mirror)

# SDD ledger — plan: docs/superpowers/plans/2026-08-18-operator-ergonomics.md
Branch: operator-ergonomics (worktree .worktrees/ops), base main 5e724d2 (v0.6.1 + docs). Design approved in chat 2026-08-18 12:22 (issues #26 bench --compare, #27 runs show --markdown); no separate spec file — the plan's Design section is the authority. Workers: qwen/qwen3-coder-next 131k via dirtywork 0.6.1 launcher from the main checkout, host mode, `--allow-commit` (first real use). Reviews: two-lens workflow (Sonnet) + skeptics; final review Opus.
- Setup done 12:2x: worktree .worktrees/ops, workspace, scripts (--allow-commit), sampler started; Opus writing the plan.
- Task 1: dispatched (BASE b7eae2f) 12:38 qwen 131k, 80 turns, --allow-commit
- Task 1 run: completed, 44 turns, 3.8m, 1464685/8767 tok, worker committed 94b1cd1 (--allow-commit works); landed; 817 passed. Review workflow dispatched.
- Task 1 review (workflow): approved/approved; 2 Minor (display-rounded zero delta; no zero-delta assertion) fixed by ctrl a453f00 (818 passed).
Task 1: complete (94b1cd1 + a453f00)
- Pre-flight scan (Sonnet): 0 BLOCKER / 1 DEFECT / 2 NOTE. Rulings: D1 → Task 2 factors the ERROR/BLOCKED/ok classification into one `_tool_result_outcome(result_text)` helper used by both `_timeline_line` and `_md_event_lines`; N2 → in --markdown the header prints the FULL task text (no `(full text below)` truncation suffix). Carried in the Task 2 dispatch.
- Task 2: dispatched (BASE a453f00) qwen 131k, 100 turns, --allow-commit
- Task 2 run: completed, 59 turns, 4.8m, 1759192/13220 tok, worker committed 3670aa4 (--allow-commit); landed; 823 passed. Worker skipped both dispatch rulings → ctrl applied: _tool_result_outcome helper (93e4f14), full task text under '## Task' (+test) 58ef11e; 824 passed. Review workflow dispatched (covers 3 commits). Lesson: rulings placed ABOVE the brief in the dispatch note were ignored twice by qwen — put rulings INTO the relevant step text next time (SP2 lesson 'weave rulings into briefs' confirmed again).
- Task 2 review (workflow): approved/approved; 2 Minor fixed by ctrl be85cce (824 passed). Task 2: complete (3670aa4, 93e4f14, 58ef11e, be85cce)
- Final review dispatched (workflow: behaviour/quality/docs lenses + skeptics) on main..be85cce.
- Final review (3 lenses + skeptics): 6 confirmed (delta vs displayed rounding; unbalanced assistant fence swallows report; README/help say one table; docs miss '## Task'; no sandbox_reset callout test; error= branch untested) + 8 Minor. One Sonnet fix wave dispatched (brief: final-fix-brief.md; 3 Minors deferred: 3rd jsonl reader, --out write_text hardening, median format).
- Fix wave cc54a75 (Sonnet): all 6 addressed, no breakage (re-review). 0.7.0 bump 341b910 (:0.7, PINNED_DIGEST=None). Gates: 832 unit / 14 docker (with :0.7 tagged locally from the identical :0.6 image) / 3 live. BRANCH COMPLETE 13:40 — PR next; merge/tag on Jim's word.
