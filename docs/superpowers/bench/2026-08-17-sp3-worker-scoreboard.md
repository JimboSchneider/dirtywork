# SP3 worker scoreboard — 2026-08-17 (extensibility; branch sp3-extensibility)

Measured on: Apple Silicon (M-series), 128 GB unified memory, macOS, LM Studio (Metal), qwen/qwen3-coder-next (80B MoE, 44.9 GB). Numbers below are specific to that box; see README "Platform support".

Launcher: dirtywork main (0.5.1), --sandbox none, --max-turns 60 (host mode, HOME=worktree; pytest visible via 0.5.1 PYTHONPATH fix — smoke run: 12 passed, 2 turns, 5 s). Implementer: qwen/qwen3-coder-next (LM Studio, 65k ctx, --parallel 3). Reviews: Sonnet; scoped re-reviews: Haiku; final: Opus. From 6b on: qwen reloaded at 131072 ctx / --parallel 2 and runs pass --context-window 131072 (two consecutive context_exhausted/max_turns runs on Task 6 at 65k).

| Run | Model | Status | Turns | Wall | Prompt tok | Compl tok | Harness failures (nudge kinds / stalled) | Review verdict | Notes |
|---|---|---|---|---|---|---|---|---|---|
| smoke | qwen/qwen3-coder-next | completed | 2 | 5s | 3539 | 92 | – | – | pytest visibility check |
| task-1 | qwen/qwen3-coder-next | completed | 6 | 55s | 30565 | 1956 | – | ✅ Approved (Sonnet) | ctrl_commit=yes; transcription task |
| task-2 | qwen/qwen3-coder-next | completed | 11 | 109s | 172118 | 5831 | – | ✅ Approved (Sonnet) | ctrl_commit=yes |
| task-3 | qwen/qwen3-coder-next | max_turns | 80 | 273s | 2912789 | 15997 | – (turn cap too tight, real progress) | ✅ Approved (Sonnet), 1 Important cleanup fixed by ctrl | ctrl finished 9b + committed |
| task-4 | qwen/qwen3-coder-next | completed | 24 | 136s | 169979 | 3594 | – | ✅ Approved (2-lens workflow) | worker committed itself |
| task-5 | qwen/qwen3-coder-next | completed | 29 | 185s | 568653 | 10289 | – | ✅ Approved (2-lens workflow) | ctrl_commit=yes |
| task-6 (run 1) | qwen/qwen3-coder-next | max_turns | 120 | 29.4m | 5620048 | 35080 | – (context-bound: 1084-line brief, no edit_file use) | discarded | split into 6a/6b/6c |
| task-6a | qwen/qwen3-coder-next | context_exhausted | 119 | 32.9m | 5376660 | 44228 | – (context-bound again) | (T6 reviewed as a whole) | Steps 1–2 done; ctrl did Step 3 + commit |
| task-6b (qwen, 131k) | qwen/qwen3-coder-next | max_turns | 120 | 5.3m | 4180292 | 19406 | – | (T6 reviewed as a whole) | runner switched, 65/71 green; escalated to Sonnet |
| task-6b (Sonnet fix) | claude-sonnet | DONE | – | 6.1m | 114k (agent tokens) | – | – | (T6 reviewed as a whole) | restored SP2.5 nudge tail + 6a-deleted tests |
| task-7 (∥ T13) | qwen/qwen3-coder-next 131k | completed | 27 | 2.4m | 469887 | 5858 | – | ✅ Approved (2-lens workflow), 2 Minor DRY fixes by ctrl | worker committed; concurrent with T13 |
| task-13 (∥ T7, run 1) | qwen 131k×2 | model_error (LM Studio crash) | 29 | 7.7m | – | – | – | ✅ Approved (2-lens workflow) | tree complete at crash; ctrl committed |
| task-8 | qwen/qwen3-coder-next 131k | completed | 64 | 7.1m | 1298711 | 9510 | – | ✅ Approved (2-lens workflow) | worker committed |
| task-9 | qwen/qwen3-coder-next 131k | completed | 20 | 3.9m | 343411 | 7280 | – | ✅ quality; Important = misreported subset (ctrl verified full) | ctrl_commit=yes |
| task-10 | qwen/qwen3-coder-next 131k | completed | 30 | 6.9m | 537679 | 6736 | – | ✅ spec; 2 Important DRY/guard (plan-mandated) fixed by ctrl | ctrl_commit=yes |
| task-11 | qwen/qwen3-coder-next 131k | completed | 37 | 7.9m | 1013785 | 8869 | – | ✅ after 1 Important (plan-mandated guard) fixed by ctrl | worker committed |
| task-12 | qwen/qwen3-coder-next 131k | completed | 28 | 6.0m | 412983 | 3651 | – | ✅ after 1 Important (doc gap) fixed by ctrl | worker committed |
| task-14 | qwen/qwen3-coder-next 131k | completed | 41 | 13.7m | 1100734 | 12123 | – | ✅ spec; 2 Important (plan-mandated) fixed by ctrl c75812d | ctrl_commit=yes |
| task-15 | qwen/qwen3-coder-next 131k | max_turns (done in substance) | 60 | 18.4m | 1698135 | 12905 | – | ✅ after 1 Important (plan-mandated guard) fixed by ctrl | volta-slowed pytest ate the wall; ctrl committed |
| task-16 | qwen/qwen3-coder-next 131k | completed | 63 | 6.3m | 2559711 | 10050 | – | ✅ after 2 Important (doc row, test dup) fixed by ctrl | ctrl_commit=yes |
| task-6c (run 1) | qwen 131k×2 | model_error (LM Studio crash) | 13 | 0.9m | – | – | – | – | nothing written; re-dispatched |
| task-6c (run 2, qwen 131k solo) | qwen/qwen3-coder-next | max_turns | 100 | 6.8m | 5258357 | 20340 | – | (T6 reviewed as a whole) | 36/40 test_main green; escalated to Sonnet |
| task-6c (Sonnet fix) | claude-sonnet | DONE | – | 11.7m | 139k (agent tokens) | – | – | (T6 reviewed as a whole) | fixed 4 tests + self.calls off-by-one in 3 fakes; 695 passed; live 3 + docker 12 green (ctrl) |

## Concurrency metrics (qwen 131072 ctx, --parallel 2; T7 ∥ T13, 18:53)
| Sample | wired RAM | free | inactive | compressed | pressure | per-turn (T7 / T13) | notes |
|---|---|---|---|---|---|---|---|
| solo, 65k (T3, 17:15) | 51.7 GB | 12.8 GB | 32.2 GB | 1.6 GB | 57% free | 3.4 s (T3 solo) | baseline |
| 2 workers, 131k, ~1 min in | 51.9 GB | 13.8 GB | 29.5 GB | 1.8 GB | 57% free | 3.5 s / 3.8 s | ~10% per-worker slowdown, ~1.9× aggregate |
| 2 workers, 131k, T13@27 turns + 6c@6, 18:59:45 | 55.9 GB | 1.2 GB | 39.0 GB | 1.8 GB | 54% free | – | **model crashed 19:00:30** (both runs model_error) |
| after reload 131k / parallel 1 (T16 generating, 20:45–46) | 59.3–61.6 GB | 16–18 GB | 23.7 GB | 1.6 GB | ~50% free | 3.1 s model/turn (T16) | one worker at a time from here; busy peak seen 66.6 GB (T14) |

## Frontier vs local (per UTC day; frontier-vs-local.py; $eq = API-list weighting, Fable at Opus rates as placeholder — Jim is on Max 20x, so the real constraint is the usage meter, not $)
| day (UTC) | frontier out tok | cache write | cache read | $eq | main / subagents | fable / opus / sonnet / haiku | local runs | local prompt tok | local compl tok |
|---|---|---|---|---|---|---|---|---|---|
| 08-14 | 1,589,602 | 9,510,620 | 411,773,096 | 698 | 632 / 66 | 615 / 39 / 39 / 4 | 0 (dirs gone) | – | – |
| 08-15 | 1,815,153 | 10,796,936 | 651,439,477 | 1,246 | 1,087 / 159 | 752 / 482 / 10 / 2 | 0 (dirs gone) | – | – |
| 08-16 (SP2 day) | 4,141,129 | 22,368,318 | 692,729,283 | 1,098 | 781 / 316 | 822 / 114 / 159 / 2 | 0 (dirs gone) | – | – |
| 08-17 (SP2.5 + SP3) | 3,379,970 | 23,252,743 | 909,486,728 | 1,467 | 1,076 / 390 | 934 / 389 / 141 / 2 | 44 | 46,996,717 | 316,207 |
| 08-18 (SP3, in progress) | 342,106 | 2,682,223 | 101,661,286 | 109 | 80 / 29 | 66 / 14 / 28 / 1 | 5 | 6,903,047 | 37,176 |
Reading: no drop in absolute frontier tokens on the SP3 day (Opus plan re-baseline + scan ≈ $400 of it); the shift is that implementation tokens (~54M prompt) went local. Main-thread cache reads dominate frontier volume.

## Plan meter (Max 20x, screenshots from Jim; plan-meter.csv)
| when | 5-h session | weekly all models | weekly Fable | note |
|---|---|---|---|---|
| 2026-08-17 19:41 CDT | 27% (resets in 1h08m) | 10% (resets Sun 4:59 PM) | 11% | window covers SP2.5 + SP3 so far — the heaviest ~27 h in the logs (~$2.5k API-equiv) |

## Model time vs tool time (run-split.py; from transcript timestamps)
See run-split.md for the full table. Headline: at 131k the model sustains ~13k prompt tok/s and 50–75 tok/s decode (6b/6c, T2/T3 pure loops); T8–T14 wall was dominated by tool time (57–81%) because pytest ran 2–4× per task at 121–277 s in the worker env — root cause the volta shim re-downloading Node into HOME=worktree on every run (fixed on this branch, 4eaa5ce: 276 s → 36 s).

## Totals (qwen runs with a run.json)
20 qwen runs recorded (+ 1 crashed 6c run 1 overwritten, + 2 Sonnet finishes for 6b/6c): statuses {completed: 13, max_turns: 5, context_exhausted: 1, model_error: 1 (LM Studio crash)}; 981 turns; 167.3 min wall; 33,728,036 prompt tokens; 233,795 completion tokens. Reviews: Sonnet ×3 single-reviewer (T1–T3) + 12 two-lens workflows (2 Sonnet lenses + Sonnet skeptics on Critical/Important) + Haiku scoped re-reviews ×8; final whole-branch review = 6-lens workflow (3 Opus + 3 Sonnet lenses, 2 Sonnet skeptics per finding). Controller commits of worker diffs (worker did not commit): 9.

## Per-run metrics (auto, run-summary.sh)
| Run | Status | Turns | Wall | s/turn | Prompt tok | Compl tok | prompt tok/s | compl tok/s | nudges | guardrail blocks | tool mix |
|---|---|---|---|---|---|---|---|---|---|---|---|
| smoke | completed | 2 | 0.1m | 2.2s | 3539 | 92 | 794 | 20.6 | 0 | 0 | bash:1 finish:1 |
| task-1 | completed | 6 | 0.9m | 9.1s | 30565 | 1956 | 561 | 35.9 | 0 | 0 | bash:3 finish:1 write_file:2 |
| task-2 | completed | 11 | 1.8m | 9.8s | 172118 | 5831 | 1589 | 53.8 | 0 | 0 | bash:2 edit_file:6 finish:1 read_file:4 |
| task-3 | max_turns | 80 | 4.5m | 3.4s | 2912789 | 15997 | 10673 | 58.6 | 0 | 0 | bash:43 edit_file:7 read_file:25 run_step:2 write_file:3 |
| task-4 | completed | 24 | 2.3m | 5.6s | 169979 | 3594 | 1258 | 26.6 | 0 | 0 | bash:16 edit_file:1 finish:1 list_dir:3 read_file:1 write_file:2 |
| task-5 | completed | 29 | 3.1m | 6.4s | 568653 | 10289 | 3075 | 55.6 | 0 | 0 | bash:5 edit_file:5 finish:1 read_file:12 write_file:13 |
| task-6 | max_turns | 120 | 29.4m | 14.7s | 5620048 | 35080 | 3185 | 19.9 | 0 | 1 | bash:66 grep:4 list_dir:2 read_file:48 |
| task-6a | context_exhausted | 119 | 32.9m | 16.6s | 5376660 | 44228 | 2728 | 22.4 | 0 | 1 | bash:85 edit_file:10 grep:2 read_file:22 |
| task-6b | max_turns | 120 | 5.3m | 2.7s | 4180292 | 19406 | 13059 | 60.6 | 0 | 0 | bash:96 edit_file:10 grep:1 list_dir:1 read_file:12 |
| task-7 | completed | 27 | 2.4m | 5.4s | 469887 | 5858 | 3241 | 40.4 | 0 | 0 | bash:9 edit_file:1 finish:1 list_dir:2 read_file:7 write_file:10 |
| task-6c | model_error | 13 | 0.9m | 4.3s | 0 | 0 | 0 | 0.0 | 0 | 0 | bash:2 list_dir:1 read_file:10 |
| task-13 | model_error | 29 | 7.7m | 15.8s | 0 | 0 | 0 | 0.0 | 0 | 0 | bash:16 list_dir:3 write_file:10 |
| task-6c | max_turns | 100 | 6.8m | 4.1s | 5258357 | 20340 | 12976 | 50.2 | 0 | 0 | bash:33 edit_file:22 grep:13 read_file:34 write_file:1 |
| task-8 | completed | 64 | 7.1m | 6.6s | 1298711 | 9510 | 3063 | 22.4 | 0 | 0 | bash:44 edit_file:1 finish:1 read_file:16 write_file:2 |
| task-9 | completed | 20 | 3.9m | 11.7s | 343411 | 7280 | 1465 | 31.1 | 0 | 0 | bash:4 edit_file:4 finish:1 read_file:8 write_file:3 |
| task-10 | completed | 30 | 6.9m | 13.8s | 537679 | 6736 | 1299 | 16.3 | 0 | 0 | bash:10 edit_file:8 finish:1 grep:2 read_file:10 run:1 |
| task-11 | completed | 37 | 7.9m | 12.8s | 1013785 | 8869 | 2135 | 18.7 | 0 | 0 | bash:9 edit_file:6 finish:1 grep:6 list_dir:2 read_file:16 |
| task-12 | completed | 28 | 6.0m | 12.9s | 412983 | 3651 | 1143 | 10.1 | 0 | 0 | bash:11 edit_file:5 finish:1 grep:3 read_file:8 |
| task-14 | completed | 41 | 13.7m | 20.0s | 1100734 | 12123 | 1340 | 14.8 | 0 | 0 | bash:14 edit_file:7 grep:4 list_dir:3 read_file:12 write_file:1 |
| task-15 | max_turns | 60 | 18.4m | 18.4s | 1698135 | 12905 | 1536 | 11.7 | 0 | 0 | bash:32 edit_file:4 grep:3 read_file:21 |
| task-16 | completed | 63 | 6.3m | 6.0s | 2559711 | 10050 | 6757 | 26.5 | 0 | 0 | bash:5 edit_file:14 finish:1 grep:9 read_file:34 |

## Model time vs tool time (run-split.py)
| Run | Status | Turns | Wall | Model time | Tool time | model s/turn | tool s/turn | prompt tok/s (model time) | compl tok/s (model time) | slowest tool call |
|---|---|---|---|---|---|---|---|---|---|---|
| task-1 | completed | 6 | 0.9m | 0.4m (47%) | 0.5m (53%) | 4.3s | 4.8s | 1,184 | 75.8 | 28s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-2 | completed | 11 | 1.8m | 1.3m (74%) | 0.5m (26%) | 7.3s | 2.6s | 2,158 | 73.1 | 28s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-3 | max_turns | 80 | 4.5m | 4.3m (94%) | 0.3m (6%) | 3.2s | 0.2s | 11,303 | 62.1 | 7s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-4 | completed | 24 | 2.3m | 0.9m (39%) | 1.4m (61%) | 2.2s | 3.4s | 3,233 | 68.4 | 28s bash {"command":"python3 -m pytest -q 2>&1 | head -20"} |
| task-5 | completed | 29 | 3.1m | 2.4m (78%) | 0.7m (22%) | 5.0s | 1.4s | 3,932 | 71.1 | 29s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-6 | max_turns | 120 | 29.4m | 28.7m (98%) | 0.7m (2%) | 14.4s | 0.3s | 3,259 | 20.3 | 29s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-6a | context_exhausted | 119 | 32.9m | 32.7m (100%) | 0.1m (0%) | 16.5s | 0.1s | 2,739 | 22.5 | 1s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-6b | max_turns | 120 | 5.3m | 5.2m (98%) | 0.1m (2%) | 2.6s | 0.1s | 13,348 | 62.0 | 1s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-6c | max_turns | 100 | 6.8m | 6.3m (94%) | 0.4m (6%) | 3.8s | 0.3s | 13,869 | 53.6 | 5s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-7 | completed | 27 | 2.4m | 1.9m (78%) | 0.5m (22%) | 4.2s | 1.2s | 4,146 | 51.7 | 30s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-8 | completed | 64 | 7.1m | 2.8m (39%) | 4.3m (61%) | 2.6s | 4.0s | 7,861 | 57.6 | 121s bash {"command":"python3 -m pytest -q 2>&1 | tail -5"} |
| task-9 | completed | 20 | 3.9m | 1.7m (43%) | 2.2m (57%) | 5.1s | 6.6s | 3,386 | 71.8 | 121s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-10 | completed | 30 | 6.9m | 1.7m (25%) | 5.2m (75%) | 3.5s | 10.3s | 5,161 | 64.7 | 183s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-11 | completed | 37 | 7.9m | 3.2m (41%) | 4.7m (59%) | 5.2s | 7.6s | 5,223 | 45.7 | 277s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-12 | completed | 28 | 6.0m | 1.3m (22%) | 4.7m (78%) | 2.8s | 10.1s | 5,290 | 46.8 | 276s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-13 | model_error | 29 | 7.7m | 1.5m (19%) | 6.2m (81%) | 3.0s | 12.8s | 0 | 0.0 | 127s bash {"command":"python3 -m pytest tests/test_bench.py -q"} |
| task-14 | completed | 41 | 13.7m | 3.0m (22%) | 10.7m (78%) | 4.4s | 15.6s | 6,108 | 67.3 | 183s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |

## Lessons (SP3)
1. **Brief size is the binding constraint for a local model at 65k.** Task 6's 1,084-line brief plus re-reads exhausted the context twice (the worker never used `edit_file`, wrote patch scripts, reset and retried). Splitting into 6a/6b/6c and reloading at 131k got qwen to 65/71 and 36/40 green before a Sonnet finish landed each. Rule of thumb from this run: ≤ ~450 brief lines per dispatch, biased to whole-file writes over many small before→after edits.
2. **Context size drove throughput more than anything.** At 65k the trims invalidated the prompt cache every turn (15–17 s/turn, ~3k prompt tok/s); at 131k, 2.6–5 s/turn and ~13k prompt tok/s with 50–75 tok/s decode. Two 131k slots crashed LM Studio (wired 55.9 GB, free 1.2 GB just before); one slot at 131k peaks around 66 GB wired on this 128 GB box — that is the shape to run.
3. **Tool time, not model time, dominated the second half.** pytest took 121–277 s in worker worktrees (vs 30 s on the host) because HOME=worktree made the volta `node` shim re-download Node every run. Fixed on the branch (toolchain roots VOLTA_HOME/RUSTUP_HOME/CARGO_HOME/NVM_DIR/PYENV_ROOT carried into the worker env; 276 s → 36 s). Same class as SP2.5's PYTHONPATH fix: HOME-keyed toolchain managers must be pointed home.
4. **Plan-mandated defects were the biggest review category.** Most Important findings were verbatim from the plan: re-implementing helpers that already existed (pristine-worktree predicate, security flags, usage sanitiser), skipping existing safety guards (`worktree_belongs_to_repo`), unguarded I/O in destructive commands. The pre-flight scan caught the cross-task blockers but not intra-snippet quality; a two-lens review + skeptic verification per task caught them cheaply.
5. **Workers report a subset as "the suite" when the suite times out** (bash tool default 120 s). The preamble now mandates `timeout: 600` and honest reporting; the controller re-runs the full suite at landing regardless.
6. **Frontier usage shifted rather than dropped.** The SP3 day was the largest frontier day in the logs (Opus plan re-baseline + pre-flight scan ≈ 27% of it), but implementation tokens (~34M prompt) went local; the Max meter read 10% weekly after ~27 h of heavy use.
7. **Metrics-as-you-go paid for itself in minutes:** the sampler + per-run rows + model/tool split found the crash cause, the volta tax and the cache thrash the same hour each appeared.
