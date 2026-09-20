# Issue #137 W3: policy and shell re-exports and the public API pin — run ledger

Plan: [issue #137 plan](../plans/2026-09-19-issue-137-firewall-policy.md), Task 3 (brief W3 of 3). Spec: [issue #137 spec](../specs/2026-09-19-issue-137-firewall-policy-design.md) §2. Base: `7dc1c33` (the W2 run branch, stacked; `main` was `b14784a`). Branch: `dirtywork/issue-137-task-w3-of-0920115604-82f022f9`.

## Worker setup

Released `dirtywork==0.13.2`, invoked through `pipx run --spec`, with `--branch-from @issue-137-task-w2-of-0920113630-d8e649e1`. Worker: `qwen3.6-35b-a3b-splash` through LM Studio on `localhost:1234/v1` (default `openai` provider), the only resident model, with a 262,144-token context and `--max-tokens 16384`. Image: `dirtywork-worker-pytest:0.13`, network disabled. Full-suite verification with two available fix rounds; `--max-turns 60 --timeout 1800`.

The saved brief was compared with the plan's fenced W3 brief before the worker started. It names two files and four exact `edit_file` pairs: three on `dirtywork/firewall/__init__.py`, one on `tests/test_firewall_request.py`.

## Launch recovery

The original launcher started the sampler in the foreground and waited there before reaching `dirtywork run`. No W3 run record or worker existed during that delay. The sampler process was stopped and restarted in the background, allowing the original queued launcher to proceed. The worker started at **2026-09-20 16:56:04 UTC** and completed at **16:57:10 UTC**. The original launcher then stopped the replacement sampler and exited 0.

The complete sampler CSV is preserved, including its pre-run idle samples. Worker duration and throughput below come from the transcript, excluding the launch delay. No changes to repository tooling were needed.

## Result

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `0920115604-82f022f9` | completed | 5 | 65.6 | 33,606 | 1,295 | 19.7 | 0 | accept |

Wall and usage come from `run_end`. Completion tokens include server-reported hidden reasoning; the rate divides completion tokens by whole-run wall time, including verification, and is not decode throughput.

Tool calls: `read_file` 3, `edit_file` 4, `bash` 2, `finish` 1. Tool errors 0; reported truncations 0; trimmed turns 0; timeouts 0. The worker ran the focused request/policy/shell suite (**135 passed**) and verified all 48 public names resolve. It then called `finish`; the harness ran the full suite (**2,276 passed, 8 skipped, 38 deselected in 53.03 s**), passing verification round 1.

`files_changed` names exactly the two briefed files. Diff: `dirtywork/firewall/__init__.py` +4 and `tests/test_firewall_request.py` +2. Applying the four approved edit pairs to the recorded base reproduces both files byte for byte. **No orchestrator code edits.** Independent read-only review found no issues.

Sampler ([CSV](2026-09-20-issue-137-w3-sampler.csv)): 138 total samples, of which 12 fall within the recorded worker run. During the run: free memory 25.61–27.22 GB, inactive memory 36.84–43.48 GB, one model resident.

## Host validation

- Full suite in the run worktree, `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider`: **2,275 passed, 9 skipped, 38 deselected in 115.19 s**. The count matches W2; W3 extends the existing export pin and adds no tests.
- The test log ends with that passing summary. The surrounding shell wrapper subsequently failed when assigning to zsh's read-only `status` variable; this post-test wrapper error is retained in the local receipts and did not affect the test run or files.
- `git diff --check`: clean. Python 3.9 grammar validation across `dirtywork/firewall/*.py` and `tests/test_firewall_*.py`: passed.
- `len(dirtywork.firewall.__all__) == 48` and every listed attribute resolves. The full suite includes import isolation, drift, parity, precedence, and fail-closed policy tests.
- No runtime integration is introduced; Runner integration remains issue #138.

## Preserved receipts

Slug `issue-137-task-w3-of-0920115604-82f022f9`; local receipts at `~/.dirtywork/runs/<slug>/`: `run.json`, `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, parsed result JSON, launch/end timestamps, original launcher log, sampler CSV and summary, launch-recovery note, host-suite output, and review notes. Local provenance, not published artifacts.
