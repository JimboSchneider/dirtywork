# SP2 worker scoreboard

| run | model | status | turns | wall | prompt tok | compl tok | review |
|---|---|---|---|---|---|---|---|
| T1 impl | qwen/qwen3-coder-next | completed | 58 | 2.4m | 1040259 | 6068 | ❌ 2I/2M (2 plan-mandated) |
| T1 fix1 | qwen/qwen3-coder-next | max_turns | 40 | 0.9m | 743337 | 2661 | no commit (cd guardrail false-positive) |
| T1 fix2 | qwen/qwen3-coder-next | model_error | 8 | 0.2m | 77655 | 596 | aborted (unknown tool arg) |
| T1 fix3 | qwen/qwen3-coder-next | completed | 23 | 1.1m | 261331 | 2825 | ✅ 4/4 addressed |
| T2 impl | qwen/qwen3-coder-next | max_turns | 60 | 3.3m | 1899987 | 10339 | ❌ 4I/4M (max_turns after work done) |
| T2 fix1 | qwen/qwen3-coder-next | completed | 29 | 1.7m | 460537 | 4118 | ✅ 7/7 addressed |
| T3 impl | qwen/qwen3-coder-next | completed | 20 | 1.8m | 266869 | 4976 | ✅ clean, 2M |
| T4 impl | qwen/qwen3-coder-next | completed | 12 | 1.4m | 119113 | 4745 | 1I (plan-mandated dup)/2M |
| T4 fix1 | qwen/qwen3-coder-next | completed | 25 | 1.9m | 292944 | 5569 | ✅ 2/2 addressed |
| T5 impl (final) | qwen/qwen3-coder-next | completed | 27 | 1.8m | 527392 | 5517 | ❌ 1I (rewrote SP1 body vs ruling)/2M |
| T5 fix1 | qwen/qwen3-coder-next | max_turns | 40 | 1.6m | 739463 | 5726 | items 1-2 done, uncommitted (max_turns); ctrl committed |

## Lessons (updated as we go)
1. Every hard failure so far was harness, not model: `cd <abs worktree>` guardrail false positive; unknown `description` arg → 3× TypeError abort; `commit -am` skipping new files. Fixed in PR #9; turns per task dropped ~58 → 12–27.
2. qwen transcribes verbatim briefs near-perfectly; it drops rulings delivered as a preamble/appendix (T2 missed 3, T5 rewrote a function it was told to keep). → weave rulings INTO the brief text.
3. Any "compare with commit X" instruction costs 20–40 turns of `git show | grep`. → inline exact target text in fix briefs.
4. Wall-clock per run 1–3 min; reviews 2–5 min. Speed lever = fewer fix rounds (better briefs), not a faster model.
5. Plan-mandated duplication recurs (T1, T2, T4) → pre-scan briefs for duplicated blocks and put the extract-helper instruction in the brief before dispatch.
| T6 impl | qwen/qwen3-coder-next | model_error | 26 | 2.7m | 566125 | 7969 | 2I (plan-mandated dup + bare except)/4M; model_error AFTER commit (fake finish tool ×3) |
| T6 fix1 | qwen/qwen3-coder-next | completed | 34 | 1.6m | 400342 | 3136 | ✅ 3/3 addressed |
| T7a impl [qwen3-coder-next] | qwen/qwen3-coder-next | completed | 34 | 1.8m | 870531 | 6201 | ❌ 2I (edit_file drops write result; _read_raw truncation [plan]) /2M — WINNER (committed) |
| T7a impl [qwen3.6-35b-a3b] | qwen/qwen3.6-35b-a3b | completed | 23 | 2.1m | 610199 | 7916 | 3I (same 2 defects, one split) /1M; did NOT commit — tie on code, loses on instruction-following |
| T7a fix1 [qwen3-coder-next] | qwen/qwen3-coder-next | max_turns | 60 | 3.6m | 1424538 | 13955 | max_turns; work done, 1 mis-scripted test, uncommitted (ctrl committed WIP) |
| T7a fix2 [qwen3-coder-next] | qwen/qwen3-coder-next | completed | 11 | 0.8m | 57550 | 1520 | ✅ 3/3 addressed |
