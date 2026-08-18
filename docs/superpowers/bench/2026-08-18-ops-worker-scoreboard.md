# Operator-ergonomics (0.7.0) worker scoreboard — 2026-08-18 (branch operator-ergonomics)

Measured on: Apple M5 Max, 128 GB unified memory, macOS, LM Studio (Metal), qwen/qwen3-coder-next at 131072 ctx / 1 slot. Launcher: dirtywork 0.6.1 (main checkout), host mode, `--allow-commit` (first real use), `--context-window 131072`. Reviews: two-lens workflow (Sonnet) + skeptics; final: Opus.

| Run | Model | Status | Turns | Wall | Prompt tok | Compl tok | Harness failures | Review verdict | Notes |
|---|---|---|---|---|---|---|---|---|---|
| task-1 (bench --compare) | qwen/qwen3-coder-next 131k | completed | 44 | 3.8m | 1464685 | 8767 | – | ✅ Approved (2-lens); 2 Minor fixed by ctrl | worker committed via --allow-commit |
| task-2 (runs show --markdown) | qwen/qwen3-coder-next 131k | completed | 59 | 4.8m | 1759192 | 13220 | – | ✅ Approved (2-lens); 2 Minor fixed by ctrl | worker committed; 2 dispatch rulings skipped → ctrl |

## Notes
- `--allow-commit` (0.6.1) worked as intended on both runs: workers committed with the brief's message; no controller commits of worker diffs this round.
- Rulings placed above the brief in the dispatch note were skipped by qwen on Task 2 (both applied by the controller); put rulings into the relevant step text.

## Per-run metrics (auto)
| Run | Status | Turns | Wall | s/turn | Prompt tok | Compl tok | prompt tok/s | compl tok/s | nudges | guardrail blocks | tool mix |
|---|---|---|---|---|---|---|---|---|---|---|---|
| task-1 | completed | 44 | 3.8m | 5.1s | 1464685 | 8767 | 6507 | 39.0 | 0 | 0 | bash:14 edit_file:7 finish:1 grep:7 read_file:21 run:1 |
| task-2 | completed | 59 | 4.8m | 4.8s | 1759192 | 13220 | 6168 | 46.4 | 0 | 0 | bash:21 edit_file:10 finish:1 grep:6 read_file:21 |

## Model vs tool time
| Run | Status | Turns | Wall | Model time | Tool time | model s/turn | tool s/turn | prompt tok/s (model time) | compl tok/s (model time) | slowest tool call |
|---|---|---|---|---|---|---|---|---|---|---|
| task-1 | completed | 44 | 3.8m | 2.6m (69%) | 1.2m (31%) | 3.5s | 1.6s | 9,406 | 56.3 | 32s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-2 | completed | 59 | 4.8m | 3.6m (75%) | 1.2m (25%) | 3.6s | 1.2s | 8,248 | 62.0 | 32s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |

## Reviews
Per task: two-lens Sonnet workflow + skeptics (T1: 0 Critical/Important, 2 Minor; T2: 0/0, 2 Minor). Final: 3 lenses (Opus behaviour, Sonnet quality, Sonnet docs/tests) + skeptics → 6 confirmed (1 refuted, 8 Minor), one Sonnet fix wave, re-review clean. Notable: the behaviour lens verified byte-identical output vs main by running both trees on one fixture set.

## Lessons
1. `--allow-commit` closed the ctrl-commit gap: both workers committed with the brief's message; zero controller commits of worker diffs.
2. The toolchain-root fix from 0.6 shows up in the numbers: tool time 25–31% of wall (was 57–81%), slowest call 32 s (was 121–277 s).
3. Rulings placed above the brief in the dispatch note were skipped by qwen (both applied by the controller): put rulings into the relevant step text, not the preamble.
4. Plan-writer executed its own snippets against the real modules before handing over — 0 blockers in pre-flight, both tasks landed first try (44 and 59 turns).
