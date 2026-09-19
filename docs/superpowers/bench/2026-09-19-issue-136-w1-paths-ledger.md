# Issue #136 W1: lexical path normalization and target classes — run ledger

Plan: [issue #136 plan](../plans/2026-09-19-issue-136-firewall-normalization.md), Task 1 (brief W1 of 3). Spec: [issue #136 spec](../specs/2026-09-19-issue-136-firewall-normalization-design.md) §6, §10, §12. Base: `ec16d8b` (`main`). Branch: `dirtywork/issue-136-task-w1-of-0919171842-89f8676e`.

## Worker setup

PyPI release checked on 2026-09-19: `dirtywork==0.13.2`, invoked through `pipx run --spec`. Host: LM Studio Bionic 1.1.5 serving the same `localhost:1234/v1` as LM Studio (default `openai` provider). Worker: `qwen3.6-35b-a3b-splash` (Inco AI Splash engine 0.0.4, mixture of experts, about 3B active, 4-bit, 20.95 GB, disk-mapped), the worker the W5 three-way A/B in issue #135 picked; the server reported a 262,144-token context and dirtywork used it. Reasoning on by default and `--max-tokens 16384` (issue #173). Image: `dirtywork-worker-pytest:0.13`, network disabled. Verify gate: the default suite with two fix rounds; `--max-turns 60 --timeout 1800`.

Brief shape: two new files carried verbatim between BEGIN/END markers (one `write_file` each), re-extracted from the plan's fenced `### Worker brief W1` block and byte-compared to the generator's output; 7,239 characters. Launched by hand on a host with no other dirtywork runs, the day the plan (PR #175) was opened, before it merged: the run branches from `main`, and the plan's block is the reference either way.

## Result

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `0919171842-89f8676e` | completed | 6 | 132.5 | 39,689 | 2,903 | 21.9 | 0 | accept |

Wall and usage come from the transcript's `run_end`. Completion tokens include hidden reasoning, which the server counts inside `completion_tokens`. The rate divides completion tokens by whole-run wall time; it is not decode throughput.

Tool calls: `list_dir` 2, `write_file` 2, `bash` 3, `finish` 1; tool errors 0; truncations 0; trimmed turns 0; timeouts 0. The worker listed the package and the tests directory, wrote both files in one turn, ran the new test file (37 passed), tried the full suite with a `--timeout=300` flag the container's pytest does not know (usage error, exit 0 reported by the wrapper), reran it plain, and finished. Verify gate exit 0 on the first round.

`files_changed` = exactly the two files the brief named. Both are byte-identical to the blocks re-extracted from the plan (`cmp`), trailing newlines included; no orchestrator edit.

Sampler (`tools/soak_sampler.sh`, [CSV](2026-09-19-issue-136-w1-sampler.csv)): 26 samples in the run window, macOS free pages 0.1–4.4 GB, inactive 19.7–53.2 GB. Two models were resident: this worker and a 44.86 GB `qwen/qwen3-coder-next` loaded in Bionic by the owner for other work, which is why free pages sit near zero where the issue #135 Splash runs showed 18–35 GB; the run was unaffected.

## Host validation

- Full suite in the run's worktree, `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider`: **1,866 passed / 9 skipped / 38 deselected**, 109.9 s. Base `ec16d8b`: 1,829 passed (37 new, as the plan's dry run expected).
- `git diff --check`: clean. `ast.parse(..., feature_version=(3, 9))` over `dirtywork/firewall/*.py` and `tests/test_firewall_*.py`: silent.
- No runtime behavior change: nothing outside the package imports it, and `paths.py` imports nothing from `dirtywork/`.

## Preserved receipts

Slug `issue-136-task-w1-of-0919171842-89f8676e`; local receipts at `~/.dirtywork/runs/<slug>/`: `run.json` (with the accept verdict), `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, stdout, launch/end timestamps, the `lms ps` lines at launch, the sampler CSV and the plan's whole dry-run diff. Local provenance, not published artifacts.
