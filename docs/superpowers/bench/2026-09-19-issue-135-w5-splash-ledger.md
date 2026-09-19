# Issue #135 W5 on Bionic + Splash, dense and MoE: the boundary validator, Rejection and the package re-exports — A/B run ledger

Plan: [issue #135 plan](../plans/2026-09-08-issue-135-firewall-schema.md), Task 5 / worker brief W5, run on two Splash models beside the canonical run. Base for both: `8e4186c` (the W4 run branch after its review fix, PR #171). None of these branches is opened as a PR: their code is the same three files PR #172 carries. This ledger is the receipt for the hosts, not for the code.

Branches: `dirtywork/issue-135-task-w5-of-0919144356-8410f815` (dense) and `dirtywork/issue-135-task-w5-of-0919151531-eb267bc0` (MoE). An earlier dense run from the pre-fix base `e9a1502`, `dirtywork/issue-135-task-w5-of-0919131854-20c5243b`, is kept at the end for the record.

## Worker setup

PyPI release checked on 2026-09-19: `dirtywork==0.13.2`, invoked through `pipx run --spec`, `--branch-from @issue-135-task-w4-of-0919121850-f11bfebd`. Image `dirtywork-worker-pytest:0.13`, network disabled. Verify gate: the default suite with two fix rounds; `--max-turns 60 --timeout 1800`. Brief: the plan's fenced `### Worker brief W5` block, re-extracted (two new files verbatim between BEGIN/END markers plus one `edit_file` whose new text is the complete 48-line `__init__.py`; 18,200 characters), identical for every run here and for PR #172.

What differs from the canonical run, deliberately and on the record:

- **Host.** LM Studio Bionic 1.1.5, the LM Studio team's separate agent app, which runs the LM Studio runtime and serves the same local server on `127.0.0.1:1234` (`/v1/*` and the native `/api/v0/models`). LM Studio itself was closed. dirtywork's default `openai` provider and base URL were used unchanged; the `lms` CLI drives Bionic's runtime the same way.
- **Models and engine.** Both are Inco AI's Splash packages on the Splash engine `splash-mac-arm64-apple-metal-advsimd@0.0.4`, installed under LM Studio's experimental runtimes; each ships a DFlash 2 draft model for speculative decoding and is a fixed-layout binary mapped straight from disk, so the weights show up as file cache rather than app memory. `incoai/Qwen3.8-27B-Splash` (dense 27B, 4-bit, 17.4 GB) is model key `qwen3.8-27b-splash`; `incoai/Qwen3.6-35B-A3B-Splash` (mixture of experts, 35B total, about 3B active, 4-bit weights with 8-bit routers, 20.9 GB) is model key `qwen3.6-35b-a3b-splash`. The server reported a 262,144-token loaded context for the dense model and 262,144 for the MoE; dirtywork used the reported value (`context_window_source: provider:openai:server`). One model was loaded at a time.
- **Reasoning on, output cap raised.** Splash models reason by default. Only the request field `reasoning_effort` turns it off; the released dirtywork cannot send that field (issue #173), so both workers ran with reasoning on and `--max-tokens 16384` instead of the default 8,192, so that hidden reasoning could not starve a large verbatim write. Probes on the dense model before the first run, temperature 0:

| Probe | `max_tokens` | Completion tokens | Reasoning tokens | Outcome |
| --- | ---: | ---: | ---: | --- |
| 40-line module, default | 600 | 600 | 600 | `finish_reason: length`, no content |
| same, `reasoning_effort: none` | 600 | 235 | 0 | code, 1.9 s |
| ~150-line file via `write_file`, default | 4,096 | 2,955 | 1,546 | one tool call, 24.1 s |
| same, `reasoning_effort: none` | 4,096 | 1,577 | 0 | one tool call, 15.0 s |

Launched by hand, one at a time, on a host with no other dirtywork runs.

## Result

| Run suffix | Model | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `0919144356-8410f815` | `qwen3.8-27b-splash` | completed | 5 | 165.7 | 52,532 | 5,115 | 30.9 | 0 | accept |
| `0919151531-eb267bc0` | `qwen3.6-35b-a3b-splash` | completed | 8 | 140.8 | 97,729 | 5,700 | 40.5 | 0 | accept |

Wall and usage come from each transcript's `run_end`. Completion tokens include the model's hidden reasoning tokens, which the server counts inside `completion_tokens`; dirtywork's transcript does not record the split. The rate divides completion tokens by whole-run wall time; it is not decode throughput. Standalone decode probes on this Mac ran at 97–124 tok/s for the dense model and about 275 tok/s for the MoE, both with reasoning on.

**Dense.** Tool calls: `list_dir` 1, `read_file` 2, `write_file` 2, `edit_file` 1, `bash` 2, `finish` 1; tool errors 0; truncations 0; trimmed turns 0. Three reads before the first write, both writes and the edit in place by turn 3, then the new test file (40 passed) and the default suite in the container; verify gate exit 0 on the first round. `request.py` and its test are byte-identical to the blocks re-extracted from the plan; `__init__.py` landed with one extra blank line at end of file (`git diff --check` flags it), the opposite of the missing-newline quirk seen on oMLX, and not present in this model's earlier run from `e9a1502`, so run-to-run rather than host-specific. Sampler ([CSV](2026-09-19-issue-135-w5-dense-sampler.csv)): 32 samples, macOS free pages 32.1–35.4 GB, inactive 31.5–42.0 GB; the engine process itself sat at 4–7 GB resident with the weights in file cache.

**MoE.** Tool calls: `list_dir` 1, `read_file` 5, `write_file` 2, `edit_file` 1, `bash` 2, `finish` 1; tool errors 0; truncations 0; trimmed turns 0. Six reads before the first write, the most of the three workers: it listed the package and read the existing modules and the test file head before writing, then wrote both files and applied the edit, ran the new test file (40 passed) and the default suite in the container; verify gate exit 0 on the first round. All three files are byte-identical to the blocks re-extracted from the plan, trailing newlines included. Sampler ([CSV](2026-09-19-issue-135-w5-moe-sampler.csv)): 27 samples, macOS free pages 18.0–19.3 GB, inactive 38.0–48.6 GB; the dense model had been unloaded a minute earlier and its pages were still in the file cache.

## Side by side: three workers on the W5 brief

| | PR #172: `qwen/qwen3-coder-next` (MoE, 3B active, 4-bit MLX, LM Studio runtime) | `qwen3.8-27b-splash` (dense 27B, Splash, reasoning on) | `qwen3.6-35b-a3b-splash` (MoE, 3B active, Splash, reasoning on) |
| --- | ---: | ---: | ---: |
| Base | `e9a1502` | `8e4186c` | `8e4186c` |
| Status / turns | completed / 7 | completed / 5 | completed / 8 |
| Wall seconds | 166.3 | 165.7 | 140.8 |
| Prompt tokens | 69,167 | 52,532 | 97,729 |
| Completion tokens | 4,480 | 5,115 (reasoning included) | 5,700 (reasoning included) |
| Completion tokens / wall second | 26.9 | 30.9 | 40.5 |
| Reads before the first write | 1 | 3 | 6 |
| Nudges / tool errors | 0 / 0 | 0 / 0 | 0 / 0 |
| Worker files vs brief | identical | identical but one trailing blank line | identical |
| Host suite in the run's worktree | 1,817 passed | 1,829 passed | 1,829 passed |
| Free memory during the run | 0.2–3.2 GB (44.86 GB model in app memory) | 32.1–35.4 GB (17.4 GB model in file cache) | 18.0–19.3 GB (20.95 GB model in file cache, dense pages still cached) |

The base differs between the canonical run and the two Splash runs by PR #171's review-fix commit, which touches `schema.py` and its test only; the brief's three files are unaffected, and the host counts differ by that fix's 12 tests.

Reading: on a verbatim brief the three workers finish within half a minute of each other, and the MoE Splash model is the fastest despite reading the most and carrying twice the prompt tokens of the dense run, because its decode rate is roughly 2.7× the dense model's and 3× Coder-Next's. Both Splash models read before they write, where Coder-Next wrote from the brief alone; both paid for hidden reasoning inside their completion budgets. All three produced the brief's files exactly, the dense rerun's trailing blank line aside. The MoE Splash model is the one to prefer for the worker role on this evidence: fastest, cleanest output this run, and the least resident memory of the three MLX-or-Splash options. Whether it holds up on a non-verbatim brief is the next question, and issue #173 (turning reasoning off) is the obvious lever on all of these numbers.

## Earlier dense run, for the record

`dirtywork/issue-135-task-w5-of-0919131854-20c5243b`, same model and settings, from the pre-fix base `e9a1502`: completed, 9 turns, 182.7 s, 98,728 prompt / 5,653 completion tokens, one self-corrected tool error (an absolute `/work` path on the first `list_dir`), five reads and two shell probes before the first write, an `od` check of its own edit afterwards, all three files byte-identical, host suite 1,817 passed. Sampler ([CSV](2026-09-19-issue-135-w5-splash-sampler.csv)): 35 samples, free 30.9–34.9 GB. Verdict accept, ledger only.

## Host validation

- Full suite in each run's worktree, `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider`: dense **1,829 passed / 9 skipped / 38 deselected**, 106.0 s; MoE **1,829 passed / 9 skipped / 38 deselected**, 113.2 s**.
- `ast.parse(..., feature_version=(3, 9))` over `dirtywork/firewall/*.py` and `tests/test_firewall_*.py`: silent for both. `git diff --check`: clean for the MoE run; the dense run's extra blank line is noted above.

## Preserved receipts

Slugs `issue-135-task-w5-of-0919144356-8410f815`, `issue-135-task-w5-of-0919151531-eb267bc0` and `issue-135-task-w5-of-0919131854-20c5243b`; local receipts at `~/.dirtywork/runs/<slug>/`: `run.json` (with the accept verdict and its note), `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, stdout, launch/end timestamps, the `lms ps` line at launch and the sampler CSV. Local provenance, not published artifacts.
