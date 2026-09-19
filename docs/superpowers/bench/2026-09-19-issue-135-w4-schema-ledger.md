# Issue #135 W4: ActionRequest, the canonical shapes, identity and FirewallEvent — run ledger

Plan: [issue #135 plan](../plans/2026-09-08-issue-135-firewall-schema.md), Task 4 (brief W4 of 5). Spec: [issue #135 spec](../specs/2026-09-08-issue-135-firewall-schema-design.md) §3, §4, §8, §9. Base: `8d76cd4` (the W3 run branch, PR #167, stacked; rebased onto `main` `0237bf5` after PR #166 merged). Branch: `dirtywork/issue-135-task-w4-of-0919121850-f11bfebd`.

## Worker setup

PyPI release checked on 2026-09-19: `dirtywork==0.13.2`, invoked through `pipx run --spec`, `--branch-from @issue-135-task-w3-of-0910005213-813032ab`. Host: LM Studio (the default `openai` provider at `http://localhost:1234/v1`; oMLX was not running that day, so this run is the series' first on LM Studio — same MLX weights as the oMLX runs). Worker: `qwen/qwen3-coder-next`, loaded with a 65,536-token context, which the server reported and dirtywork used (`context_window_source: provider:openai:server`). Image: `dirtywork-worker-pytest:0.13`, network disabled. Verify gate: the default suite with two fix rounds; `--max-turns 60 --timeout 1800`.

Brief shape: two new files carried verbatim between BEGIN/END markers (one `write_file` each), re-extracted from the plan's fenced `### Worker brief W4` block; 32,077 characters, the largest brief in the series. Launched by hand on a host with no other dirtywork runs. A first launch minutes earlier exited before any worker turn (`error: model 'qwen/qwen3-coder-next' not loaded`, exit 2, no run directory): LM Studio had just been updated and had dropped the loaded model. The relaunch loaded the model and started the run in the same command.

## Result

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `0919121850-f11bfebd` | completed | 6 | 255.7 | 105,312 | 11,065 | 43.3 | 0 | accept |

Wall and usage come from the transcript's `run_end`. The rate divides completion tokens by whole-run wall time; it is not model decode throughput. LM Studio's server log records no per-request token rates, so decode throughput for this run is not on the record.

Tool calls: `write_file` 3, `bash` 2, `finish` 0 (plain-answer finish); `read_file` 0, `list_dir` 0; truncations 0; trimmed turns 0; timeouts 0. The first `write_file` used the absolute path `/work/dirtywork/firewall/schema.py`, which the tool refused (`resolves outside the worktree`); the worker retried at once with the relative path, and both files were in place by turn 3 with no reads. In-container: the new test file 47 passed, then the default suite (exit 0; the transcript caps the output, so the in-container total is not recorded). Verify gate exit 0 on the first round.

`files_changed` = exactly the two files the brief named. Both are byte-identical to the blocks re-extracted from the plan (`cmp`), trailing newline included: unlike W2 and W3, no orchestrator edit was needed. Whether the missing-newline quirk is host-specific (oMLX there, LM Studio here) or run-to-run is not established by one run.

Sampler (`tools/soak_sampler.sh`, [CSV](2026-09-19-issue-135-w4-sampler.csv)): 50 samples in the run window, macOS free pages 0.1–3.5 GB with 34.8–58.3 GB inactive (reclaimable; free alone is not headroom on macOS, which is what stalled the overnight W4 driver for four days), LM Studio 1 model loaded (Coder-Next, 44.86 GB), oMLX not running. The `lms_status` column reflects the newer `lms ps` output format after the LM Studio update.

## Host validation

- Full suite, `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider`: **1,777 passed / 9 skipped / 38 deselected**, 102.1 s. Base `8d76cd4` (W3): 1,730 passed (47 new, as the plan expected).
- `git diff --check`: clean. `ast.parse(..., feature_version=(3, 9))` over `dirtywork/firewall/*.py` and `tests/test_firewall_*.py`: silent.
- No runtime behavior change: `schema.py` imports only its siblings in `dirtywork/firewall/`, and nothing outside the package imports it.

## Preserved receipts

Slug `issue-135-task-w4-of-0919121850-f11bfebd`; local receipts at `~/.dirtywork/runs/<slug>/`: `run.json` (with the accept verdict), `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, stdout, launch/end timestamps and the sampler CSV. Local provenance, not published artifacts.
