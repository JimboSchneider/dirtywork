# v1 soak — worker scoreboard (#48)

Draft — regenerated as legs complete. Matrix: `2026-08-23-v1-soak-matrix.md`; ledger: `2026-08-23-v1-soak-sdd-ledger.md`. Tables are emitted by `tools/soak_harvest.py` from each leg's JSONL (`~/.dirtywork/bench/soak-*.jsonl`); the **Feature fired** column lists the matrix F-ids whose transcript signal is present. Rows written before commit d648cae have no `final_message`, so their Harness-failures cell cannot show `abort=`; the ledger names the abort kind for those (F5@1024 rows: consecutive `truncated`; F5-default-dev: consecutive unknown-tool).

Environment: dirtywork 0.10.1 (`82a353e`) + this branch; worker image `:0.10` @ `sha256:4fc400ca…` (pinned); LM Studio `qwen/qwen3-coder-next` (65,536 ctx, PARALLEL 4) and `mistralai/devstral-small-2-2512` (loaded_context_length 325,120 — CLI `-c` ignored, see ledger), both resident; Apple Silicon, 128 GB. Runs are serial.

## Leg A — baseline `dirtywork bench` (3 tasks × 2 models × 2 repeats)
## Leg A — baseline `dirtywork bench` (3 tasks × 2 models × 2 repeats)
## Main
| Run | Model | Status | Turns | Wall | Prompt tok | Compl tok | Harness failures | Review verdict | Notes | Feature fired |
|---|---|---|---|---|---|---|---|---|---|---|
| sumrangelow-high-in-sumrangepy-must-0823180425-9326817c | qwen/qwen3-coder-next | completed | 7 | 0.2m | 18487 | 606 | - | - | - | F9 |
| sumrangelow-high-in-sumrangepy-must-0823180437-ea91e2d2 | qwen/qwen3-coder-next | completed | 6 | 0.1m | 15300 | 371 | - | - | - | F9 |
| add-a-loud-flag-to-0823180446-f6174e2e | qwen/qwen3-coder-next | completed | 7 | 0.1m | 17426 | 383 | - | - | - | F9 |
| add-a-loud-flag-to-0823180456-b3a332ba | qwen/qwen3-coder-next | completed | 6 | 0.1m | 15070 | 364 | - | - | - | F9 |
| reportsh-a-b-c-must-0823180506-2a3baf45 | qwen/qwen3-coder-next | completed | 7 | 0.1m | 17981 | 410 | - | - | - | F9 |
| reportsh-a-b-c-must-0823180516-4e261b7d | qwen/qwen3-coder-next | completed | 8 | 0.2m | 20842 | 432 | - | - | - | F9 |
| sumrangelow-high-in-sumrangepy-must-0823180526-12d0a778 | mistralai/devstral-small-2-2512 | completed | 9 | 0.4m | 20964 | 601 | - | - | - | F9 |
| sumrangelow-high-in-sumrangepy-must-0823180551-5fe86d67 | mistralai/devstral-small-2-2512 | completed | 9 | 0.4m | 20969 | 606 | - | - | - | F9 |
| add-a-loud-flag-to-0823180614-ef0657b5 | mistralai/devstral-small-2-2512 | completed | 13 | 0.4m | 32267 | 667 | - | - | - | F9 |
| add-a-loud-flag-to-0823180641-e7f8d7c8 | mistralai/devstral-small-2-2512 | completed | 12 | 0.4m | 28841 | 588 | - | - | - | F9 |
| reportsh-a-b-c-must-0823180704-2b5ce255 | mistralai/devstral-small-2-2512 | completed | 13 | 0.6m | 34690 | 953 | - | - | - | F9 |
| reportsh-a-b-c-must-0823180740-f797b829 | mistralai/devstral-small-2-2512 | completed | 12 | 0.4m | 30854 | 676 | - | - | - | F9 |

## Per-run metrics (auto)
| Run | Status | Turns | Wall | s/turn | Prompt tok | Compl tok | prompt tok/s | compl tok/s | nudges | guardrail blocks | tool mix |
|---|---|---|---|---|---|---|---|---|---|---|---|
| sumrangelow-high-in-sumrangepy-must-0823180425-9326817c | completed | 7 | 0.2m | 1.6s | 18487 | 606 | 1650.6 | 54.1 | 0 | 0 | bash:2 edit_file:1 list_dir:1 read_file:2 |
| sumrangelow-high-in-sumrangepy-must-0823180437-ea91e2d2 | completed | 6 | 0.1m | 1.4s | 15300 | 371 | 1865.9 | 45.2 | 0 | 0 | bash:1 edit_file:1 list_dir:1 read_file:4 |
| add-a-loud-flag-to-0823180446-f6174e2e | completed | 7 | 0.1m | 1.2s | 17426 | 383 | 2074.5 | 45.6 | 0 | 0 | bash:1 edit_file:1 list_dir:2 read_file:4 |
| add-a-loud-flag-to-0823180456-b3a332ba | completed | 6 | 0.1m | 1.4s | 15070 | 364 | 1815.7 | 43.9 | 0 | 0 | bash:1 edit_file:1 list_dir:1 read_file:4 |
| reportsh-a-b-c-must-0823180506-2a3baf45 | completed | 7 | 0.1m | 1.2s | 17981 | 410 | 2066.8 | 47.1 | 0 | 0 | bash:1 edit_file:1 finish:1 list_dir:2 read_file:6 |
| reportsh-a-b-c-must-0823180516-4e261b7d | completed | 8 | 0.2m | 1.2s | 20842 | 432 | 2241.1 | 46.5 | 0 | 0 | bash:1 edit_file:1 finish:1 list_dir:3 read_file:7 |
| sumrangelow-high-in-sumrangepy-must-0823180526-12d0a778 | completed | 9 | 0.4m | 2.7s | 20964 | 601 | 873.5 | 25.0 | 0 | 0 | bash:3 edit_file:1 finish:1 list_dir:1 read_file:3 |
| sumrangelow-high-in-sumrangepy-must-0823180551-5fe86d67 | completed | 9 | 0.4m | 2.4s | 20969 | 606 | 975.3 | 28.2 | 0 | 0 | bash:3 edit_file:1 finish:1 list_dir:1 read_file:3 |
| add-a-loud-flag-to-0823180614-ef0657b5 | completed | 13 | 0.4m | 2.0s | 32267 | 667 | 1255.5 | 26.0 | 0 | 0 | bash:8 edit_file:1 finish:1 read_file:3 |
| add-a-loud-flag-to-0823180641-e7f8d7c8 | completed | 12 | 0.4m | 1.9s | 28841 | 588 | 1293.3 | 26.4 | 0 | 0 | bash:7 edit_file:1 finish:1 list_dir:1 read_file:2 |
| reportsh-a-b-c-must-0823180704-2b5ce255 | completed | 13 | 0.6m | 2.7s | 34690 | 953 | 991.1 | 27.2 | 0 | 0 | bash:9 edit_file:1 exit code: 0 0000000 f i l ...:3 finish:1 read_file:4 |
| reportsh-a-b-c-must-0823180740-f797b829 | completed | 12 | 0.4m | 2.1s | 30854 | 676 | 1239.1 | 27.1 | 0 | 0 | bash:8 edit_file:1 exit code: 0 0000000 f i l ...:1 finish:1 read_file:4 |

## Model vs tool time
| Run | Status | Turns | Wall | Model time | Tool time | model s/turn | tool s/turn | prompt tok/s (model time) | compl tok/s (model time) | slowest tool call |
|---|---|---|---|---|---|---|---|---|---|---|
| sumrangelow-high-in-sumrangepy-must-0823180425-9326817c | completed | 7 | 0.2m | 0.2m (93%) | 0.0m (7%) | 1.4s | 0.1s | 1951.0 | 64.0 | 0s bash {"command":"python3 acceptance/check_sum_range.py"} |
| sumrangelow-high-in-sumrangepy-must-0823180437-ea91e2d2 | completed | 6 | 0.1m | 0.1m (91%) | 0.0m (9%) | 1.1s | 0.1s | 2295.1 | 55.7 | 0s list_dir {"path":"."} |
| add-a-loud-flag-to-0823180446-f6174e2e | completed | 7 | 0.1m | 0.1m (88%) | 0.0m (12%) | 0.9s | 0.1s | 2706.7 | 59.5 | 1s bash {"command":"node acceptance/greet.test.js"} |
| add-a-loud-flag-to-0823180456-b3a332ba | completed | 6 | 0.1m | 0.1m (91%) | 0.0m (9%) | 1.1s | 0.1s | 2265.0 | 54.7 | 0s bash {"command":"node acceptance/greet.test.js"} |
| reportsh-a-b-c-must-0823180506-2a3baf45 | completed | 7 | 0.1m | 0.1m (92%) | 0.0m (8%) | 1.1s | 0.1s | 2443.6 | 55.7 | 0s bash {"command":"bash acceptance/check.sh"} |
| reportsh-a-b-c-must-0823180516-4e261b7d | completed | 8 | 0.2m | 0.1m (90%) | 0.0m (10%) | 1.0s | 0.1s | 2721.6 | 56.4 | 0s bash {"command":"bash acceptance/check.sh"} |
| sumrangelow-high-in-sumrangepy-must-0823180526-12d0a778 | completed | 9 | 0.4m | 0.4m (95%) | 0.0m (5%) | 2.4s | 0.1s | 971.3 | 27.8 | 0s bash {"command":"cd /work && python3 acceptance/check_sum_range.py"} |
| sumrangelow-high-in-sumrangepy-must-0823180551-5fe86d67 | completed | 9 | 0.4m | 0.3m (95%) | 0.0m (5%) | 2.1s | 0.1s | 1087.0 | 31.4 | 0s bash {"command":"cd /work && python3 acceptance/check_sum_range.py"} |
| add-a-loud-flag-to-0823180614-ef0657b5 | completed | 13 | 0.4m | 0.4m (90%) | 0.0m (10%) | 1.7s | 0.2s | 1475.1 | 30.5 | 0s bash {"command":"cd /work && node acceptance/greet.test.js"} |
| add-a-loud-flag-to-0823180641-e7f8d7c8 | completed | 12 | 0.4m | 0.3m (89%) | 0.0m (11%) | 1.6s | 0.2s | 1517.4 | 30.9 | 0s bash {"command":"node acceptance/greet.test.js"} |
| reportsh-a-b-c-must-0823180704-2b5ce255 | completed | 13 | 0.6m | 0.5m (94%) | 0.0m (6%) | 2.5s | 0.2s | 1084.2 | 29.8 | 0s bash {"command":"find . -type f -name \"*.sh\" \| head -20"} |
| reportsh-a-b-c-must-0823180740-f797b829 | completed | 12 | 0.4m | 0.4m (91%) | 0.0m (9%) | 1.8s | 0.2s | 1423.5 | 31.2 | 0s bash {"command":"cd /work && bash acceptance/check.sh"} |

## Leg A′ — same tasks with `--verify <acceptance> --verify-rounds 2`
## Main
| Run | Model | Status | Turns | Wall | Prompt tok | Compl tok | Harness failures | Review verdict | Notes | Feature fired |
|---|---|---|---|---|---|---|---|---|---|---|
| F4-py-fix-off-by-one-qwen | qwen/qwen3-coder-next | completed | 6 | 0.1m | 15198 | 339 | - | - | acceptance_passed=True | F4(passed=True,rounds=1), F9 |
| F4-node-add-cli-flag-qwen | qwen/qwen3-coder-next | completed | 7 | 0.2m | 17478 | 423 | - | - | acceptance_passed=True | F4(passed=True,rounds=1), F9 |
| F4-sh-fix-script-qwen | qwen/qwen3-coder-next | completed | 4 | 0.1m | 10184 | 248 | - | - | acceptance_passed=True | F4(passed=True,rounds=1), F9 |
| F4-py-fix-off-by-one-dev | mistralai/devstral-small-2-2512 | completed | 9 | 0.4m | 21576 | 704 | - | - | acceptance_passed=True | F4(passed=True,rounds=1), F9 |
| F4-node-add-cli-flag-dev | mistralai/devstral-small-2-2512 | completed | 11 | 0.5m | 27430 | 765 | - | - | acceptance_passed=True | F4(passed=True,rounds=1), F9 |
| F4-sh-fix-script-dev | mistralai/devstral-small-2-2512 | completed | 21 | 0.6m | 59064 | 936 | - | - | acceptance_passed=True | F4(passed=True,rounds=1), F9 |

## Per-run metrics (auto)
| Run | Status | Turns | Wall | s/turn | Prompt tok | Compl tok | prompt tok/s | compl tok/s | nudges | guardrail blocks | tool mix |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F4-py-fix-off-by-one-qwen | completed | 6 | 0.1m | 1.4s | 15198 | 339 | 1809.3 | 40.4 | 0 | 0 | bash:1 edit_file:1 finish:1 list_dir:1 read_file:4 |
| F4-node-add-cli-flag-qwen | completed | 7 | 0.2m | 1.3s | 17478 | 423 | 1859.4 | 45.0 | 0 | 0 | bash:1 edit_file:1 finish:1 list_dir:2 read_file:4 |
| F4-sh-fix-script-qwen | completed | 4 | 0.1m | 1.8s | 10184 | 248 | 1454.9 | 35.4 | 0 | 0 | bash:1 edit_file:1 read_file:3 |
| F4-py-fix-off-by-one-dev | completed | 9 | 0.4m | 2.8s | 21576 | 704 | 856.2 | 27.9 | 0 | 0 | bash:3 edit_file:1 exit code: 0 sum_range(1, 5...:1 finish:1 list_dir:1 read_file:3 |
| F4-node-add-cli-flag-dev | completed | 11 | 0.5m | 2.6s | 27430 | 765 | 969.3 | 27.0 | 0 | 0 | bash:7 edit_file:1 exit code: 0 HELLO, ADA![TO...:2 finish:1 read_file:2 |
| F4-sh-fix-script-dev | completed | 21 | 0.6m | 1.7s | 59064 | 936 | 1692.4 | 26.8 | 0 | 0 | 1 files: 3 2 Now let's look...:1 bash:10 edit_file:3 exit code: 0 0000000 f i l ...:1 exit code: 0 files: 1[TOOL_...:1 finish:1 list_dir:2 read_file:5 |

## Model vs tool time
| Run | Status | Turns | Wall | Model time | Tool time | model s/turn | tool s/turn | prompt tok/s (model time) | compl tok/s (model time) | slowest tool call |
|---|---|---|---|---|---|---|---|---|---|---|
| F4-py-fix-off-by-one-qwen | completed | 6 | 0.1m | 0.1m (86%) | 0.0m (14%) | 1.1s | 0.2s | 2374.6 | 53.0 | 0s bash {"command":"python3 acceptance/check_sum_range.py"} |
| F4-node-add-cli-flag-qwen | completed | 7 | 0.2m | 0.1m (87%) | 0.0m (13%) | 1.1s | 0.2s | 2373.3 | 57.4 | 0s bash {"command":"node acceptance/greet.test.js"} |
| F4-sh-fix-script-qwen | completed | 4 | 0.1m | 0.1m (87%) | 0.0m (13%) | 1.2s | 0.2s | 2104.4 | 51.2 | 0s bash {"command":"cd /work && bash acceptance/check.sh"} |
| F4-py-fix-off-by-one-dev | completed | 9 | 0.4m | 0.4m (94%) | 0.0m (6%) | 2.5s | 0.2s | 949.9 | 31.0 | 0s bash {"command":"cd /work && python3 -c \"from sum_range import sum_range; print('sum_range(1, 5) =', sum_range(1, 5)); pr... |
| F4-node-add-cli-flag-dev | completed | 11 | 0.5m | 0.4m (91%) | 0.0m (9%) | 2.2s | 0.2s | 1128.7 | 31.5 | 0s bash {"command":"cd /work && node acceptance/greet.test.js"} |
| F4-sh-fix-script-dev | completed | 21 | 0.6m | 0.5m (90%) | 0.1m (10%) | 1.4s | 0.2s | 1945.7 | 30.8 | 0s bash {"command":"od -c acceptance/expected_output.txt"} |

## Leg A2 — verify retry path (`F4b-*`: verify fails its first round by design)
## Main
| Run | Model | Status | Turns | Wall | Prompt tok | Compl tok | Harness failures | Review verdict | Notes | Feature fired |
|---|---|---|---|---|---|---|---|---|---|---|
| F4b-round2-qwen | qwen/qwen3-coder-next | completed | 8 | 0.2m | 21659 | 452 | - | - | acceptance_passed=True | F4(passed=True,rounds=2), F9 |
| F4b-round2-dev | mistralai/devstral-small-2-2512 | model_error | - | 0.4m | - | - | - | - | - | F9 |

## Per-run metrics (auto)
| Run | Status | Turns | Wall | s/turn | Prompt tok | Compl tok | prompt tok/s | compl tok/s | nudges | guardrail blocks | tool mix |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F4b-round2-qwen | completed | 8 | 0.2m | 1.2s | 21659 | 452 | 2210.1 | 46.1 | 0 | 0 | bash:2 edit_file:1 finish:1 list_dir:1 read_file:4 |
| F4b-round2-dev | model_error | - | 0.4m | - | - | - | - | - | 0 | 0 | bash:3 edit_file:1 finish:1 list_dir:1 read_file:3 |

## Model vs tool time
| Run | Status | Turns | Wall | Model time | Tool time | model s/turn | tool s/turn | prompt tok/s (model time) | compl tok/s (model time) | slowest tool call |
|---|---|---|---|---|---|---|---|---|---|---|
| F4b-round2-qwen | completed | 8 | 0.2m | 0.1m (88%) | 0.0m (12%) | 1.0s | 0.1s | 2711.2 | 56.6 | 0s bash {"command":"python3 acceptance/check_sum_range.py"} |
| F4b-round2-dev | model_error | - | 0.4m | 0.3m (95%) | 0.0m (5%) | - | - | - | - | 0s bash {"command":"cd /work && python3 -c \"from sum_range import sum_range; print('sum_range(1, 5) =', sum_range(1, 5)); pr... |

## Leg B — v1 provokers (F1 apply_edits, F2 stuck, F3 timeout, F5 truncation)
F2/F3 v1 provokers were superseded after this leg (see ledger); their rows are kept as evidence of *why*.
## Main
| Run | Model | Status | Turns | Wall | Prompt tok | Compl tok | Harness failures | Review verdict | Notes | Feature fired |
|---|---|---|---|---|---|---|---|---|---|---|
| F1-rename-qwen-r1 | qwen/qwen3-coder-next | completed | 4 | 0.3m | 19760 | 1174 | - | - | acceptance_passed=True | F1, F9 |
| F2-stuck-qwen-r1 | qwen/qwen3-coder-next | max_turns | 20 | 0.5m | 63648 | 826 | max_turns | - | - | F9 |
| F3-timeout-qwen-r1 | qwen/qwen3-coder-next | completed | 17 | 0.3m | 52450 | 1105 | - | - | acceptance_passed=True | F9 |
| F5-trunc-qwen-r1 | qwen/qwen3-coder-next | model_error | 4 | 0.7m | 10074 | 3113 | empty_reply=3 | - | - | F9 |
| F1-rename-qwen-r2 | qwen/qwen3-coder-next | completed | 6 | 0.4m | 25336 | 1443 | - | - | acceptance_passed=True | F1, F9 |
| F2-stuck-qwen-r2 | qwen/qwen3-coder-next | max_turns | 20 | 0.3m | 59027 | 577 | max_turns,empty_reply=1 | - | - | F9 |
| F3-timeout-qwen-r2 | qwen/qwen3-coder-next | completed | 8 | 0.2m | 20601 | 628 | - | - | acceptance_passed=True | F9 |
| F5-trunc-qwen-r2 | qwen/qwen3-coder-next | model_error | 4 | 0.7m | 9956 | 3091 | empty_reply=3 | - | - | F9 |
| F5-default-qwen | qwen/qwen3-coder-next | completed | 8 | 4.6m | 81574 | 22422 | empty_reply=1 | - | acceptance_passed=True | F5, F9 |
| F1-rename-dev-r1 | mistralai/devstral-small-2-2512 | completed | 19 | 1.6m | 123688 | 2689 | - | - | acceptance_passed=True | F1, F9 |
| F2-stuck-dev-r1 | mistralai/devstral-small-2-2512 | max_turns | 20 | 0.5m | 55312 | 752 | max_turns | - | - | F9 |
| F3-timeout-dev-r1 | mistralai/devstral-small-2-2512 | completed | 16 | 0.5m | 43191 | 852 | - | - | acceptance_passed=True | F9 |
| F5-trunc-dev-r1 | mistralai/devstral-small-2-2512 | max_turns | 40 | 15.0m | 193918 | 30585 | max_turns,empty_reply=17 | - | - | F9 |
| F1-rename-dev-r2 | mistralai/devstral-small-2-2512 | completed | 18 | 1.0m | 104126 | 1546 | - | - | acceptance_passed=True | F1, F9 |
| F2-stuck-dev-r2 | mistralai/devstral-small-2-2512 | max_turns | 20 | 0.5m | 54536 | 745 | max_turns | - | - | F9 |
| F3-timeout-dev-r2 | mistralai/devstral-small-2-2512 | completed | 23 | 0.9m | 71957 | 1553 | - | - | acceptance_passed=True | F9 |
| F5-trunc-dev-r2 | mistralai/devstral-small-2-2512 | model_error | 10 | 2.3m | 71541 | 3997 | empty_reply=3 | - | - | F9 |
| F5-default-dev | mistralai/devstral-small-2-2512 | model_error | 14 | 11.8m | 181643 | 23215 | empty_reply=1 | - | - | F5, F9 |

## Per-run metrics (auto)
| Run | Status | Turns | Wall | s/turn | Prompt tok | Compl tok | prompt tok/s | compl tok/s | nudges | guardrail blocks | tool mix |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F1-rename-qwen-r1 | completed | 4 | 0.3m | 4.8s | 19760 | 1174 | 1023.8 | 60.8 | 0 | 0 | apply_edits:1 bash:1 finish:1 read_file:3 |
| F2-stuck-qwen-r1 | max_turns | 20 | 0.5m | 1.4s | 63648 | 826 | 2257.0 | 29.3 | 0 | 0 | bash:13 list_dir:4 read_file:3 |
| F3-timeout-qwen-r1 | completed | 17 | 0.3m | 1.2s | 52450 | 1105 | 2558.5 | 53.9 | 0 | 0 | bash:9 edit_file:2 finish:1 list_dir:2 read_file:3 |
| F5-trunc-qwen-r1 | model_error | 4 | 0.7m | 9.8s | 10074 | 3113 | 257.6 | 79.6 | 3 | 0 | read_file:1 |
| F1-rename-qwen-r2 | completed | 6 | 0.4m | 3.7s | 25336 | 1443 | 1151.6 | 65.6 | 0 | 0 | apply_edits:1 bash:1 list_dir:1 read_file:6 |
| F2-stuck-qwen-r2 | max_turns | 20 | 0.3m | 0.8s | 59027 | 577 | 3689.2 | 36.1 | 1 | 0 | bash:15 list_dir:2 read_file:2 |
| F3-timeout-qwen-r2 | completed | 8 | 0.2m | 1.5s | 20601 | 628 | 1716.8 | 52.3 | 0 | 0 | bash:2 edit_file:1 finish:1 list_dir:2 read_file:2 |
| F5-trunc-qwen-r2 | model_error | 4 | 0.7m | 9.8s | 9956 | 3091 | 255.3 | 79.3 | 3 | 0 | read_file:1 |
| F5-default-qwen | completed | 8 | 4.6m | 34.2s | 81574 | 22422 | 297.8 | 81.9 | 1 | 0 | append_file:3 bash:1 finish:1 read_file:1 write_file:1 |
| F1-rename-dev-r1 | completed | 19 | 1.6m | 5.1s | 123688 | 2689 | 1267.3 | 27.6 | 0 | 0 | apply_edits:1 bash:10 exit code: 0 Successfully i...:1 exit code: 0 Traceback (mos...:1 exit code: 0 ledger.py:3:Th...:1 exit code: 0 test_average_t...:1 exit code: 1 Let me also ve...:1 finish:1 grep:1 list_dir:2 read_file:4 |
| F2-stuck-dev-r1 | max_turns | 20 | 0.5m | 1.4s | 55312 | 752 | 1907.3 | 25.9 | 0 | 0 | bash:11 exit code: 0 ./acceptance/r...:1 list_dir:3 read_file:6 |
| F3-timeout-dev-r1 | completed | 16 | 0.5m | 1.9s | 43191 | 852 | 1420.8 | 28.0 | 0 | 0 | bash:10 edit_file:1 exit code: 0 [TOOL_CALLS]bash:2 finish:1 list_dir:2 read_file:3 |
| F5-trunc-dev-r1 | max_turns | 40 | 15.0m | 22.5s | 193918 | 30585 | 215.6 | 34.0 | 17 | 0 | bash:4 list_dir:1 read_file:1 write_file:17 |
| F1-rename-dev-r2 | completed | 18 | 1.0m | 3.2s | 104126 | 1546 | 1820.4 | 27.0 | 0 | 0 | apply_edits:1 bash:8 exit code: 0 calc_total not...:1 exit code: 1 grep: ledger.p...:1 finish:1 grep:2 list_dir:2 read_file:4 |
| F2-stuck-dev-r2 | max_turns | 20 | 0.5m | 1.4s | 54536 | 745 | 1900.2 | 26.0 | 0 | 0 | bash:12 exit code: 0 /usr/bin/pip3 ...:1 exit code: 127 apt-get: com...:1 list_dir:3 read_file:5 |
| F3-timeout-dev-r2 | completed | 23 | 0.9m | 2.2s | 71957 | 1553 | 1394.5 | 30.1 | 0 | 0 | bash:9 edit_file:4 exit code: 0 [TOOL_CALLS]re...:2 finish:1 list_dir:2 read_file:10 |
| F5-trunc-dev-r2 | model_error | 10 | 2.3m | 13.8s | 71541 | 3997 | 518.4 | 29.0 | 3 | 0 | bash:2 list_dir:2 read_file:2 write_file:1 |
| F5-default-dev | model_error | 14 | 11.8m | 50.6s | 181643 | 23215 | 256.6 | 32.8 | 1 | 0 | append_file:4 bash:5 exit code: 0 400,User400,us...:1 exit code: 0 401 fixtures/r...:1 exit code: 0 id,name,email,...:2 list_dir:1 read_file:2 write_file:1 |

## Model vs tool time
| Run | Status | Turns | Wall | Model time | Tool time | model s/turn | tool s/turn | prompt tok/s (model time) | compl tok/s (model time) | slowest tool call |
|---|---|---|---|---|---|---|---|---|---|---|
| F1-rename-qwen-r1 | completed | 4 | 0.3m | 0.3m (96%) | 0.0m (4%) | 4.4s | 0.2s | 1133.1 | 67.3 | 0s bash {"command":"cd /work && bash acceptance/check.sh"} |
| F2-stuck-qwen-r1 | max_turns | 20 | 0.5m | 0.3m (56%) | 0.2m (44%) | 0.8s | 0.6s | 4194.1 | 54.4 | 9s bash {"command":"python3 -m venv /tmp/venv && /tmp/venv/bin/pip install httpx 2>&1","description":"Create a venv and insta... |
| F3-timeout-qwen-r1 | completed | 17 | 0.3m | 0.3m (87%) | 0.0m (13%) | 1.0s | 0.2s | 3097.3 | 65.3 | 0s bash {"command":"cd /work && bash build.sh < /dev/null && cat out.txt"} |
| F5-trunc-qwen-r1 | model_error | 4 | 0.7m | 0.6m (100%) | 0.0m (0%) | 9.5s | 0.0s | 264.3 | 81.7 | 0s read_file {"path":"README.md"} |
| F1-rename-qwen-r2 | completed | 6 | 0.4m | 0.3m (96%) | 0.0m (4%) | 3.4s | 0.1s | 1257.5 | 71.6 | 0s bash {"command":"cd /work && bash acceptance/check.sh"} |
| F2-stuck-qwen-r2 | max_turns | 20 | 0.3m | 0.2m (78%) | 0.1m (22%) | 0.6s | 0.2s | 5166.9 | 50.5 | 0s bash {"command":"python3 -I -c 'import sys; print(sys.path)'"} |
| F3-timeout-qwen-r2 | completed | 8 | 0.2m | 0.2m (92%) | 0.0m (8%) | 1.2s | 0.1s | 2064.9 | 62.9 | 0s bash {"command":"cat out.txt"} |
| F5-trunc-qwen-r2 | model_error | 4 | 0.7m | 0.6m (100%) | 0.0m (0%) | 9.5s | 0.0s | 262.0 | 81.3 | 0s read_file {"path":"README.md"} |
| F5-default-qwen | completed | 8 | 4.6m | 4.5m (100%) | 0.0m (0%) | 34.0s | 0.1s | 300.2 | 82.5 | 0s append_file {"path":"fixtures/rows.csv","text":"201,User201,user201@example.com,enterprise,2024-07-19,669.67\n202,User202,user202... |
| F1-rename-dev-r1 | completed | 19 | 1.6m | 1.6m (97%) | 0.0m (3%) | 4.9s | 0.2s | 1321.4 | 28.7 | 0s bash {"command":"cd /work && python3 -m unittest tests.test_ledger -v"} |
| F2-stuck-dev-r1 | max_turns | 20 | 0.5m | 0.4m (89%) | 0.1m (11%) | 1.3s | 0.2s | 2190.8 | 29.8 | 0s bash {"command":"ls -la"} |
| F3-timeout-dev-r1 | completed | 16 | 0.5m | 0.4m (91%) | 0.0m (9%) | 1.7s | 0.2s | 1613.3 | 31.8 | 0s bash {"command":"cd /work && cat out.txt"} |
| F5-trunc-dev-r1 | max_turns | 40 | 15.0m | 14.9m (100%) | 0.1m (0%) | 22.4s | 0.1s | 216.7 | 34.2 | 0s bash {"command":"rm -f fixtures/rows.csv"} |
| F1-rename-dev-r2 | completed | 18 | 1.0m | 0.9m (95%) | 0.0m (5%) | 3.0s | 0.1s | 1942.8 | 28.8 | 0s bash {"command":"cd /work && bash acceptance/check.sh"} |
| F2-stuck-dev-r2 | max_turns | 20 | 0.5m | 0.4m (89%) | 0.1m (11%) | 1.2s | 0.2s | 2206.1 | 30.1 | 0s bash {"command":"which python3"} |
| F3-timeout-dev-r2 | completed | 23 | 0.9m | 0.8m (93%) | 0.1m (7%) | 2.0s | 0.1s | 1532.4 | 33.1 | 0s bash {"command":"cd /work && rm -f out.txt && bash build.sh < /dev/null"} |
| F5-trunc-dev-r2 | model_error | 10 | 2.3m | 2.3m (99%) | 0.0m (1%) | 13.6s | 0.1s | 526.7 | 29.4 | 0s bash {"command":"cd /work && python3 << 'EOF'\nimport datetime\n\n# Start date for id=1\ntotal_rows = 400\nstart_date = da... |
| F5-default-dev | model_error | 14 | 11.8m | 11.7m (100%) | 0.0m (0%) | 50.3s | 0.2s | 257.8 | 33.0 | 0s bash {"command":"head -5 fixtures/rows.csv"} |

## Leg B2 — v2 provokers (F2 canonical-config, F3 wait-for-service) and F5 at 2048/4096
## Main
| Run | Model | Status | Turns | Wall | Prompt tok | Compl tok | Harness failures | Review verdict | Notes | Feature fired |
|---|---|---|---|---|---|---|---|---|---|---|
| F2v2-canon-qwen-r1 | qwen/qwen3-coder-next | max_turns | 20 | 0.3m | 72150 | 840 | max_turns | - | - | F9 |
| F3v2-wait-qwen-r1 | qwen/qwen3-coder-next | completed | 18 | 1.6m | 88073 | 2281 | - | - | acceptance_passed=True | F9 |
| F5-trunc2048-qwen-r1 | qwen/qwen3-coder-next | model_error | 4 | 1.2m | 10073 | 6163 | empty_reply=3,abort=empty_reply | - | - | F9 |
| F2v2-canon-qwen-r2 | qwen/qwen3-coder-next | max_turns | 20 | 0.6m | 73630 | 2084 | max_turns | - | - | F9 |
| F3v2-wait-qwen-r2 | qwen/qwen3-coder-next | completed | 20 | 2.2m | 109317 | 3071 | - | - | acceptance_passed=True | F9 |
| F5-trunc4096-qwen-r2 | qwen/qwen3-coder-next | model_error | 4 | 2.4m | 10120 | 12307 | empty_reply=3,abort=empty_reply | - | - | F9 |
| F2v2-canon-dev-r1 | mistralai/devstral-small-2-2512 | max_turns | 20 | 0.8m | 78036 | 1168 | max_turns | - | - | F9 |
| F3v2-wait-dev-r1 | mistralai/devstral-small-2-2512 | completed | 17 | 2.2m | 69332 | 1784 | - | - | acceptance_passed=True | F9 |
| F5-trunc2048-dev-r1 | mistralai/devstral-small-2-2512 | model_error | - | 2.4m | - | - | empty_reply=1 | - | - | F9 |
| F2v2-canon-dev-r2 | mistralai/devstral-small-2-2512 | max_turns | 20 | 0.8m | 76033 | 1184 | max_turns | - | - | F9 |
| F3v2-wait-dev-r2 | mistralai/devstral-small-2-2512 | completed | 28 | 4.4m | 142640 | 2789 | - | - | acceptance_passed=True | F9 |
| F5-trunc4096-dev-r2 | mistralai/devstral-small-2-2512 | model_error | 8 | 6.8m | 37044 | 12835 | empty_reply=3,abort=empty_reply | - | - | F9 |

## Per-run metrics (auto)
| Run | Status | Turns | Wall | s/turn | Prompt tok | Compl tok | prompt tok/s | compl tok/s | nudges | guardrail blocks | tool mix |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F2v2-canon-qwen-r1 | max_turns | 20 | 0.3m | 1.0s | 72150 | 840 | 3625.6 | 42.2 | 0 | 0 | bash:17 read_file:3 |
| F3v2-wait-qwen-r1 | completed | 18 | 1.6m | 5.2s | 88073 | 2281 | 933.0 | 24.2 | 0 | 0 | bash:10 edit_file:3 finish:1 list_dir:2 read_file:3 |
| F5-trunc2048-qwen-r1 | model_error | 4 | 1.2m | 18.7s | 10073 | 6163 | 134.8 | 82.5 | 3 | 0 | read_file:1 |
| F2v2-canon-qwen-r2 | max_turns | 20 | 0.6m | 1.7s | 73630 | 2084 | 2165.6 | 61.3 | 0 | 0 | bash:14 list_dir:3 read_file:3 |
| F3v2-wait-qwen-r2 | completed | 20 | 2.2m | 6.7s | 109317 | 3071 | 820.1 | 23.0 | 0 | 0 | bash:10 edit_file:4 list_dir:1 read_file:5 |
| F5-trunc4096-qwen-r2 | model_error | 4 | 2.4m | 36.6s | 10120 | 12307 | 69.1 | 84.0 | 3 | 0 | read_file:1 |
| F2v2-canon-dev-r1 | max_turns | 20 | 0.8m | 2.4s | 78036 | 1168 | 1649.8 | 24.7 | 0 | 0 | bash:16 exit code: 1 fatal: ambiguo...:1 exit code: 1 fatal: bad obj...:2 exit code: 1 find: /work/__...:1 list_dir:1 read_file:4 |
| F3v2-wait-dev-r1 | completed | 17 | 2.2m | 7.9s | 69332 | 1784 | 518.2 | 13.3 | 0 | 0 | Edited build.sh: +1 -1 (rem...:1 bash:8 edit_file:3 exit code: 1 No processes f...:1 finish:1 list_dir:2 read_file:5 |
| F5-trunc2048-dev-r1 | model_error | - | 2.4m | - | - | - | - | - | 1 | 0 | append_file:38 list_dir:2 read_file:1 write_file:1 |
| F2v2-canon-dev-r2 | max_turns | 20 | 0.8m | 2.3s | 76033 | 1184 | 1652.9 | 25.7 | 0 | 0 | bash:14 exit code: 1 fatal: not a r...:2 grep:4 list_dir:1 read_file:5 |
| F3v2-wait-dev-r2 | completed | 28 | 4.4m | 9.4s | 142640 | 2789 | 541.5 | 10.6 | 0 | 0 | bash:18 edit_file:4 exit code: 0 Timeout: Healt...:3 exit code: 0 no output Let ...:1 exit code: 1 + name=anon + ...:1 finish:1 list_dir:2 read_file:4 |
| F5-trunc4096-dev-r2 | model_error | 8 | 6.8m | 51.4s | 37044 | 12835 | 90.2 | 31.2 | 3 | 0 | bash:1 list_dir:2 read_file:1 write_file:1 |

## Model vs tool time
| Run | Status | Turns | Wall | Model time | Tool time | model s/turn | tool s/turn | prompt tok/s (model time) | compl tok/s (model time) | slowest tool call |
|---|---|---|---|---|---|---|---|---|---|---|
| F2v2-canon-qwen-r1 | max_turns | 20 | 0.3m | 0.3m (82%) | 0.1m (18%) | 0.8s | 0.2s | 4654.2 | 54.2 | 0s bash {"command":"cd / && find . -name \"config.json\" 2>/dev/null \| head -20"} |
| F3v2-wait-qwen-r1 | completed | 18 | 1.6m | 0.5m (35%) | 1.0m (65%) | 1.8s | 3.3s | 2670.9 | 69.2 | 22s bash {"command":"bash acceptance/check.sh 2>&1"} |
| F5-trunc2048-qwen-r1 | model_error | 4 | 1.2m | 1.2m (100%) | 0.0m (0%) | 18.4s | 0.0s | 136.9 | 83.7 | 0s read_file {"path":"README.md"} |
| F2v2-canon-qwen-r2 | max_turns | 20 | 0.6m | 0.5m (90%) | 0.1m (10%) | 1.5s | 0.2s | 2463.0 | 69.7 | 0s bash {"command":"bash acceptance/check.sh 2>&1"} |
| F3v2-wait-qwen-r2 | completed | 20 | 2.2m | 0.7m (34%) | 1.5m (66%) | 2.2s | 4.4s | 2465.7 | 69.3 | 27s bash {"command":"for i in 1 2 3; do echo \"=== Run $i ===\"; bash acceptance/check.sh 2>&1; done"} |
| F5-trunc4096-qwen-r2 | model_error | 4 | 2.4m | 2.4m (100%) | 0.0m (0%) | 36.3s | 0.0s | 69.7 | 84.7 | 0s read_file {"path":"README.md"} |
| F2v2-canon-dev-r1 | max_turns | 20 | 0.8m | 0.7m (92%) | 0.1m (8%) | 2.1s | 0.2s | 1847.6 | 27.7 | 0s bash {"command":"cd /work && git fsck --lost-found 2>&1 \| head -20"} |
| F3v2-wait-dev-r1 | completed | 17 | 2.2m | 1.0m (44%) | 1.2m (56%) | 3.4s | 4.4s | 1193.1 | 30.7 | 22s bash {"command":"cd /work && bash acceptance/check.sh"} |
| F5-trunc2048-dev-r1 | model_error | - | 2.4m | 2.3m (95%) | 0.1m (5%) | - | - | - | - | 0s append_file {"path":"fixtures/rows.csv","text":"6,User6,user6@example.com,enterprise,2024-01-06,19.98\n"} |
| F2v2-canon-dev-r2 | max_turns | 20 | 0.8m | 0.7m (92%) | 0.1m (8%) | 2.1s | 0.2s | 1843.7 | 28.7 | 0s bash {"command":"cd /work && python3 -B -m unittest tests.test_config 2>&1"} |
| F3v2-wait-dev-r2 | completed | 28 | 4.4m | 1.5m (35%) | 2.9m (65%) | 3.2s | 6.1s | 1571.3 | 30.7 | 26s bash {"command":"cd /work && bash -x ./build.sh 2>&1 \| head -30"} |
| F5-trunc4096-dev-r2 | model_error | 8 | 6.8m | 6.8m (100%) | 0.0m (0%) | 51.1s | 0.1s | 90.6 | 31.4 | 0s bash {"command":"python3 << 'EOF'\nimport datetime\n\n# Start date for id=1\nstart_date = datetime.date(2024, 1, 1)\n\n# G... |
## Leg B3 — instruction-driven F2/F3 (`F2v2-iterate-*`: "re-run the check after every change"; `F3v2-run-*`: "run build.sh and report")
## Main
| Run | Model | Status | Turns | Wall | Prompt tok | Compl tok | Harness failures | Review verdict | Notes | Feature fired |
|---|---|---|---|---|---|---|---|---|---|---|
| F2v2-iterate-qwen | qwen/qwen3-coder-next | max_turns | 24 | 1.1m | 112209 | 4813 | max_turns | - | - | F9 |
| F3v2-run-qwen | qwen/qwen3-coder-next | model_error | 11 | 2.5m | 32720 | 789 | timeouts=1,abort=bad_args | - | - | F3, F9 |
| F2v2-iterate-dev | mistralai/devstral-small-2-2512 | max_turns | 24 | 2.0m | 108132 | 3665 | max_turns | - | - | F9 |
| F3v2-run-dev | mistralai/devstral-small-2-2512 | model_error | - | 2.2m | - | - | - | - | - | F3, F9 |

## Per-run metrics (auto)
| Run | Status | Turns | Wall | s/turn | Prompt tok | Compl tok | prompt tok/s | compl tok/s | nudges | guardrail blocks | tool mix |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F2v2-iterate-qwen | max_turns | 24 | 1.1m | 2.9s | 112209 | 4813 | 1630.9 | 70.0 | 0 | 0 | bash:24 |
| F3v2-run-qwen | model_error | 11 | 2.5m | 13.4s | 32720 | 789 | 222.6 | 5.4 | 1 | 0 | bash:6 list_dir:2 read_file:3 |
| F2v2-iterate-dev | max_turns | 24 | 2.0m | 5.0s | 108132 | 3665 | 896.6 | 30.4 | 0 | 0 | bash:20 exit code: 0 ./config.json ...:1 exit code: 0 000002a0 72 61...:1 exit code: 0 Hash of actual...:1 exit code: 1 fatal: not a r...:1 exit code: 128 fatal: not a...:1 list_dir:1 read_file:4 |
| F3v2-run-dev | model_error | - | 2.2m | - | - | - | - | - | 1 | 0 | bash:1 list_dir:1 read_file:1 |

## Model vs tool time
| Run | Status | Turns | Wall | Model time | Tool time | model s/turn | tool s/turn | prompt tok/s (model time) | compl tok/s (model time) | slowest tool call |
|---|---|---|---|---|---|---|---|---|---|---|
| F2v2-iterate-qwen | max_turns | 24 | 1.1m | 1.0m (93%) | 0.1m (7%) | 2.6s | 0.2s | 1781.7 | 76.4 | 0s bash {"command":"python3 -m unittest tests.test_config 2>&1"} |
| F3v2-run-qwen | model_error | 11 | 2.5m | 0.2m (9%) | 2.2m (91%) | 1.2s | 12.1s | 2520.0 | 60.8 | 0s bash {"command":"which python3 python nc ncat socat 2>/dev/null; ls -la /usr/bin/python* 2>/dev/null \|\| true"} |
| F2v2-iterate-dev | max_turns | 24 | 2.0m | 1.9m (97%) | 0.1m (3%) | 4.8s | 0.2s | 937.0 | 31.8 | 0s bash {"command":"cat -A config.json"} |
| F3v2-run-dev | model_error | - | 2.2m | 0.0m (1%) | 2.2m (99%) | - | - | - | - | 0s list_dir {"path":"."} |

## Leg C2 — Ollama `qwen3.6:latest` (`dirtywork bench`, 3 tasks × 1). Leg C (`soak-C.jsonl`) was a preflight refusal on the tag-less model id and is not tabulated.
## Main
| Run | Model | Status | Turns | Wall | Prompt tok | Compl tok | Harness failures | Review verdict | Notes | Feature fired |
|---|---|---|---|---|---|---|---|---|---|---|
| sumrangelow-high-in-sumrangepy-must-0823193743-f6aca66a | qwen3.6:latest | completed | 7 | 0.2m | 17021 | 663 | - | - | - | F7, F9 |
| add-a-loud-flag-to-0823193758-46b3f99a | qwen3.6:latest | completed | 9 | 0.3m | 23411 | 1078 | - | - | - | F7, F9 |
| reportsh-a-b-c-must-0823193818-eeca7da2 | qwen3.6:latest | completed | 5 | 0.2m | 11883 | 517 | - | - | - | F7, F9 |

## Per-run metrics (auto)
| Run | Status | Turns | Wall | s/turn | Prompt tok | Compl tok | prompt tok/s | compl tok/s | nudges | guardrail blocks | tool mix |
|---|---|---|---|---|---|---|---|---|---|---|---|
| sumrangelow-high-in-sumrangepy-must-0823193743-f6aca66a | completed | 7 | 0.2m | 2.0s | 17021 | 663 | 1224.5 | 47.7 | 0 | 0 | bash:1 edit_file:1 finish:1 list_dir:2 read_file:4 |
| add-a-loud-flag-to-0823193758-46b3f99a | completed | 9 | 0.3m | 2.1s | 23411 | 1078 | 1251.9 | 57.6 | 0 | 0 | bash:2 edit_file:1 finish:1 grep:1 list_dir:4 read_file:4 |
| reportsh-a-b-c-must-0823193818-eeca7da2 | completed | 5 | 0.2m | 2.0s | 11883 | 517 | 1200.3 | 52.2 | 0 | 0 | bash:1 edit_file:1 finish:1 list_dir:1 read_file:3 |

## Model vs tool time
| Run | Status | Turns | Wall | Model time | Tool time | model s/turn | tool s/turn | prompt tok/s (model time) | compl tok/s (model time) | slowest tool call |
|---|---|---|---|---|---|---|---|---|---|---|
| sumrangelow-high-in-sumrangepy-must-0823193743-f6aca66a | completed | 7 | 0.2m | 0.2m (93%) | 0.0m (7%) | 1.7s | 0.1s | 1444.4 | 56.3 | 0s edit_file {"path":"sum_range.py","old_string":" for i in range(low, high): # BUG: excludes `high`","new_string":" for i in rang... |
| add-a-loud-flag-to-0823193758-46b3f99a | completed | 9 | 0.3m | 0.3m (92%) | 0.0m (8%) | 1.8s | 0.2s | 1471.6 | 67.8 | 0s bash {"command":"node acceptance/greet.test.js"} |
| reportsh-a-b-c-must-0823193818-eeca7da2 | completed | 5 | 0.2m | 0.1m (92%) | 0.0m (8%) | 1.6s | 0.1s | 1512.4 | 65.8 | 0s bash {"command":"bash acceptance/check.sh"} |
