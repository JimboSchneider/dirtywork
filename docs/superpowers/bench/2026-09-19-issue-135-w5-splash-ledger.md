# Issue #135 W5 on Bionic + Splash Qwen3.8-27B: the boundary validator, Rejection and the package re-exports — A/B run ledger

Plan: [issue #135 plan](../plans/2026-09-08-issue-135-firewall-schema.md), Task 5 / worker brief W5, run a second time on a different host. Base: `e9a1502` (the W4 run branch, PR #171), the same base as the canonical W5 run. Branch: `dirtywork/issue-135-task-w5-of-0919131854-20c5243b`, **not opened as a PR**: its code is byte-identical to PR #172's, which carries W5. This ledger is the receipt for the host, not for the code.

## Worker setup

PyPI release checked on 2026-09-19: `dirtywork==0.13.2`, invoked through `pipx run --spec`, `--branch-from @issue-135-task-w4-of-0919121850-f11bfebd`. Image `dirtywork-worker-pytest:0.13`, network disabled. Verify gate: the default suite with two fix rounds; `--max-turns 60 --timeout 1800`.

This run departs from the canonical W5 run in three ways, all deliberate and all on the record here:

- **Host.** LM Studio Bionic 1.1.5, the LM Studio team's separate agent app, which runs the LM Studio runtime and serves the same local server on `127.0.0.1:1234` (`/v1/*` and the native `/api/v0/models`). LM Studio itself was closed. dirtywork's default `openai` provider and base URL were used unchanged; the `lms` CLI drives Bionic's runtime the same way.
- **Model and engine.** `incoai/Qwen3.8-27B-Splash` (dense 27B, 4-bit, 17.4 GB, with a bundled DFlash 2 draft model for speculative decoding), model key `qwen3.8-27b-splash`, on the Splash engine `splash-mac-arm64-apple-metal-advsimd@0.0.4` from Inco AI, installed under LM Studio's experimental runtimes. The server reported a 262,144-token loaded context and dirtywork used it (`context_window_source: provider:openai:server`).
- **Reasoning on, output cap raised.** Splash models reason by default at `xhigh`. Only the request field `reasoning_effort` turns it off; the released dirtywork cannot send that field (issue #173), so the worker ran with reasoning on and `--max-tokens 16384` instead of the default 8,192, so that hidden reasoning could not starve a large verbatim write. Probes before the run, same model, temperature 0:

| Probe | `max_tokens` | Completion tokens | Reasoning tokens | Outcome |
| --- | ---: | ---: | ---: | --- |
| 40-line module, default | 600 | 600 | 600 | `finish_reason: length`, no content |
| same, `reasoning_effort: none` | 600 | 235 | 0 | code, 1.9 s |
| ~150-line file via `write_file`, default | 4,096 | 2,955 | 1,546 | one tool call, 24.1 s |
| same, `reasoning_effort: none` | 4,096 | 1,577 | 0 | one tool call, 15.0 s |

Brief shape: identical to the canonical run (two new files verbatim between BEGIN/END markers plus one `edit_file` whose new text is the complete 48-line `__init__.py`; 18,200 characters). Launched by hand on a host with no other dirtywork runs.

## Result

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `0919131854-20c5243b` | completed | 9 | 182.7 | 98,728 | 5,653 | 30.9 | 0 | accept |

Wall and usage come from the transcript's `run_end`. Completion tokens include the model's hidden reasoning tokens, which the server counts inside `completion_tokens`; dirtywork's transcript does not record the split. The rate divides completion tokens by whole-run wall time; it is not decode throughput. Standalone decode probes on this Mac ran at 97–124 tok/s.

Tool calls: `list_dir` 2, `read_file` 2, `grep` 1, `bash` 5, `write_file` 2, `edit_file` 1, `finish` 1 (an explicit finish call, where Coder-Next ended with a plain answer). Truncations 0; trimmed turns 0; timeouts 0. One tool error, self-corrected: the first `list_dir` used the absolute path `/work`, which the tool refused, and the worker retried relative in the same turn (Coder-Next made the same slip on its first `write_file` in W4). Before the first write the worker made five reads and two shell probes (a `wc -l` over the package and an `ls` of the design docs); after the edit it checked `__init__.py`'s bytes with `od` before running the tests. In-container: the new test file 40 passed, then the default suite (exit 0; the transcript caps the output). Verify gate exit 0 on the first round.

`files_changed` = exactly the three files the brief named. All three are byte-identical to the blocks re-extracted from the plan (`cmp`), trailing newlines included; no orchestrator edit.

Sampler (`tools/soak_sampler.sh`, [CSV](2026-09-19-issue-135-w5-splash-sampler.csv)): 35 samples in the run window, macOS free pages 30.9–34.9 GB with 32.1–42.3 GB inactive, one model loaded (the 17.38 GB Splash package; Coder-Next's 44.86 GB was not resident).

## Side by side: the two W5 runs

| | PR #172: Bionic/LM Studio runtime, `qwen/qwen3-coder-next` (MoE, 3B active, 4-bit MLX) | this run: Bionic, `qwen3.8-27b-splash` (dense 27B, 4-bit, Splash engine, reasoning on) |
| --- | ---: | ---: |
| Provider path | openai, `localhost:1234/v1` | openai, `localhost:1234/v1` |
| Status / turns | completed / 7 | completed / 9 |
| Wall seconds | 166.3 | 182.7 |
| Prompt tokens | 69,167 | 98,728 |
| Completion tokens | 4,480 | 5,653 (reasoning included) |
| Completion tokens / wall second | 26.9 | 30.9 |
| Reads before the first write | 1 | 5 (+2 shell probes) |
| Nudges / tool errors | 0 / 0 | 0 / 1 |
| Worker files vs brief | identical | identical |
| Host suite | 1,817 passed | 1,817 passed |
| Free memory during the run | 0.2–3.2 GB (44.86 GB model resident) | 30.9–34.9 GB (17.38 GB model resident) |

Reading: on a verbatim brief the two are within ten percent on wall time. The dense Splash model spent its extra turns reading the package before writing and verifying its own edit afterwards, which is the reasoning-model style, and it paid for hidden reasoning inside its completion budget. It did this on 27 GB less resident memory. Whether it is the better worker on a non-verbatim brief is not established by this run; the MoE sibling `incoai/Qwen3.6-35B-A3B-Splash`, which its card rates at about three times this model's decode rate, has not been run.

## Host validation

- Full suite in the run's worktree, `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider`: **1,817 passed / 9 skipped / 38 deselected**, 104.8 s.
- `git diff --check`: clean. `ast.parse(..., feature_version=(3, 9))` over `dirtywork/firewall/*.py` and `tests/test_firewall_*.py`: silent.

## Preserved receipts

Slug `issue-135-task-w5-of-0919131854-20c5243b`; local receipts at `~/.dirtywork/runs/<slug>/`: `run.json` (with the accept verdict and its note), `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, stdout, launch/end timestamps, the `lms ps` line at launch and the sampler CSV. Local provenance, not published artifacts.
