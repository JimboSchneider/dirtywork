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
| T7b impl [qwen3-coder-next] | qwen/qwen3-coder-next | max_turns | 80 | 5.2m | 3021157 | 14490 | ❌ 5I (fallback path broken, N+1 stat, no fallback tests, dup)/1M; max_turns right after green |
| T7b impl [qwen3.6-35b-a3b] | qwen/qwen3.6-35b-a3b | completed | 16 | 3.2m | 385888 | 10855 | ❌ stopped at 16 turns, EMPTY final msg, 2 tests failing, no commit — LOSES |
| T7b fix1 [qwen3-coder-next] | qwen/qwen3-coder-next | completed | 47 | 3.4m | 857279 | 8202 | ✅ 5/5 addressed |
| T8 impl | qwen/qwen3-coder-next | completed | 71 | 11.8m | 2191037 | 16467 | ❌ 3I (swallowed SandboxError, dup branch, 60s test)/3M; no commit |
| T8 fix1 | qwen/qwen3-coder-next | completed | 39 | 3.1m | 717294 | 4163 | ✅ 3/3 addressed; suite 81s→21s |
| T9a impl | qwen/qwen3-coder-next | model_error | 15 | 1.2m | 129645 | 3515 | ✅ spec, 1I (plan-mandated fail-open)/3M; committed ✓ then fake-finish abort |
| T9a fix1 | qwen/qwen3-coder-next | completed | 13 | 1.4m | 100684 | 2882 | ✅ 3/3 addressed |
| T9b impl | qwen/qwen3-coder-next | completed | 71 | 5.3m | 2934644 | 11200 | ✅ clean, 3M |
| T10 impl | qwen/qwen3-coder-next | completed | 15 | 1.7m | 160205 | 5257 | ❌ 2I (OSError unwrapped; trailing PAX global unchecked)/3M |
| T10 fix1 | qwen/qwen3-coder-next | max_turns | 60 | 11.1m | 1791930 | 27595 | max_turns; impl done, bad test construction (my hint) |
| T10 fix2 | qwen/qwen3-coder-next | completed | 25 | 1.3m | 300654 | 2858 | OSError wrap ✅; trailing-PAX test wrong (my construction) |
| T11a impl | qwen/qwen3-coder-next | completed | 21 | 2.2m | 435416 | 7036 | ✅ spec, 4I (plan-mandated dup ×3, unguarded DockerError)/5M |
| T11b impl | qwen/qwen3-coder-next | completed | 68 | 3.3m | 2080684 | 7882 | ✅ spec, 2I (plan-mandated bare except regression; docstring)/2M |
| T10 fix3 | qwen/qwen3-coder-next | completed | 14 | 1.3m | 107919 | 1631 | ✅ 2/2 addressed (trailing PAX genuinely rejected + tested) |
| T11a fix1 | qwen/qwen3-coder-next | max_turns | 60 | 10.9m | 2163633 | 18250 | max_turns; refactor done, dropped one test constant (40 errors) |
| T11a fix2 | qwen/qwen3-coder-next | completed | 56 | 12.1m | 908344 | 5773 | ✅ green but import-time default → 60s test |
