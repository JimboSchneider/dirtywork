# Issue #137 W1: the shell analyzer and the drift pin — run ledger

Plan: [issue #137 plan](../plans/2026-09-19-issue-137-firewall-policy.md), Task 1 (brief W1 of 3). Spec: [issue #137 spec](../specs/2026-09-19-issue-137-firewall-policy-design.md) §3, §5, §11. Base: `b14784a` (`main`, right after PR #180). Branch: `dirtywork/issue-137-task-w1-of-0920113341-c569cfd4`.

## Worker setup

PyPI release checked on 2026-09-20: `dirtywork==0.13.2`, invoked through `pipx run --spec`. Host: LM Studio Bionic 1.1.5 on `localhost:1234/v1` (default `openai` provider). Worker: `qwen3.6-35b-a3b-splash` (Splash engine 0.0.4, MoE, about 3B active, 4-bit, 20.95 GB, disk-mapped), the only resident model; 262,144-token context reported and used; reasoning on by default and `--max-tokens 16384` (issue #173). Image: `dirtywork-worker-pytest:0.13`, network disabled. Verify gate: the default suite with two fix rounds; `--max-turns 60 --timeout 1800`.

Brief shape: two new files carried verbatim between BEGIN/END markers (one `write_file` each), re-extracted from the plan's fenced `### Worker brief W1` block on `main` and byte-compared to the generator's output; 15,252 characters; no literal tool-call markers. Launched by hand right after the plan merged, on a host with no other dirtywork runs.

## Result

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `0920113341-c569cfd4` | completed | 6 | 134.0 | 62,321 | 4,851 | 36.2 | 0 | accept |

Wall and usage come from the transcript's `run_end`. Completion tokens include hidden reasoning, which the server counts inside `completion_tokens`. The rate divides completion tokens by whole-run wall time; it is not decode throughput.

Tool calls: `list_dir` 2, `write_file` 2, `bash` 3, `finish` 1; tool errors 0; truncations 0; trimmed turns 0; timeouts 0. The worker listed the package and the tests directory, wrote both files in one turn, ran the new test file (8 passed), then the full suite, and finished. In-container: the new test file 8 passed, then the default suite; verify gate exit 0 on the first round.

`files_changed` = exactly the two files the brief named. Both are byte-identical to the blocks re-extracted from the plan (`cmp`), trailing newlines included; no orchestrator edit.

Sampler (`tools/soak_sampler.sh`, [CSV](2026-09-20-issue-137-w1-sampler.csv)): 26 samples in the run window, macOS free pages 30.4–31.8 GB, inactive 34.9–41.4 GB, one model resident.

## Host validation

- Full suite in the run's worktree, `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider`: **2,188 passed / 9 skipped / 38 deselected**. Base `b14784a`: 2,180 passed (8 new, as the plan's dry run expected).
- `git diff --check`: clean. `ast.parse(..., feature_version=(3, 9))` over `dirtywork/firewall/*.py` and `tests/test_firewall_*.py`: silent. The drift pin against the live `guardrails._RULES` and `LEGACY_RULES`, and the host and Docker parity on the 31-command corpus, are in the produced test file and pass.
- No runtime behavior change: nothing outside the package imports it; `guardrails.py` is untouched.

## Preserved receipts

Slug `issue-137-task-w1-of-0920113341-c569cfd4`; local receipts at `~/.dirtywork/runs/<slug>/`: `run.json` (with the accept verdict), `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, stdout, launch/end timestamps, the `lms ps` line at launch and the sampler CSV. Local provenance, not published artifacts.
