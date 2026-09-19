# Issue #136 W3: batch, re-exports and parity — run ledger

Plan: [issue #136 plan](../plans/2026-09-19-issue-136-firewall-normalization.md), Task 3 (brief W3 of 3, the last). Spec: [issue #136 spec](../specs/2026-09-19-issue-136-firewall-normalization-design.md) §2, §9, §12, §13. Base: `164ddf5` (the W2 run branch, stacked; `main` was `ec16d8b`). Branch: `dirtywork/issue-136-task-w3-of-0919173344-6ecff168`.

## Worker setup

PyPI release checked on 2026-09-19: `dirtywork==0.13.2`, invoked through `pipx run --spec`, `--branch-from @issue-136-task-w2-of-0919173022-c3affa39`. Host: LM Studio Bionic 1.1.5 on `localhost:1234/v1` (default `openai` provider). Worker: `qwen3.6-35b-a3b-splash` (Splash engine 0.0.4, MoE, about 3B active, 4-bit, 20.95 GB, disk-mapped); 262,144-token context reported and used; reasoning on by default and `--max-tokens 16384` (issue #173). Image: `dirtywork-worker-pytest:0.13`, network disabled. Verify gate: the default suite with two fix rounds; `--max-turns 60 --timeout 1800`.

Brief shape (as launched): five `edit_file` pairs (two of them "appends" anchored on the last lines of `normalize.py` and its test file, two on `__init__.py`, one on the #135 `__all__` pin in `tests/test_firewall_request.py`) and one new file (`tests/test_firewall_parity.py`) carried verbatim between BEGIN/END markers, with base line numbers next to every anchor, re-extracted from the plan's fenced `### Worker brief W3` block and byte-compared to the generator's output; 24,547 characters; no literal tool-call markers anywhere in it.

## Result

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `0919173344-6ecff168` | completed | 9 | 154.4 | 219,543 | 8,358 | 54.1 | 0 | accept |

Wall and usage come from the transcript's `run_end`. Completion tokens include hidden reasoning, which the server counts inside `completion_tokens`. The rate divides completion tokens by whole-run wall time; it is not decode throughput.

Tool calls: `read_file` 8, `grep` 1, `edit_file` 6, `write_file` 1, `bash` 2, `finish` 0 (plain-answer finish); tool errors 0; truncations 0; trimmed turns 0; timeouts 0. The worker read every anchor before editing (seven reads and a grep before the first edit), applied the five edits and the write in two turns, then added a sixth edit the brief did not ask for: `Sequence` on the `typing` import line, because `canonicalize_batch`'s string annotation names it. In-container: the three named test files 352 passed, then the default suite; verify gate exit 0 on the first round.

`files_changed` = exactly the five files the brief named. Four of the five are byte-identical to the brief (`cmp` after applying its pairs to the base). `normalize.py` differs by that one import. The dry-run reference had omitted it on purpose, since under `from __future__ import annotations` the annotation is never evaluated, but the worker's file is the more correct one, so the deviation was accepted rather than reverted: the reference and the plan's W3 brief block now carry the import as a sixth edit pair, and this run's file is byte-identical to the updated brief. No orchestrator edit to the worker's files.

Sampler (`tools/soak_sampler.sh`, [CSV](2026-09-19-issue-136-w3-sampler.csv)): 30 samples in the run window, macOS free pages 53.8–57.0 GB, inactive 19.8–31.4 GB, one model resident.

## Host validation

- Full suite in the run's worktree, `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider`: **2,178 passed / 9 skipped / 38 deselected**, 108.1 s. Base (W2): 2,085 passed (93 new, as the plan's dry run expected; 349 across the three tasks over the 1,829 baseline).
- `git diff --check`: clean. `ast.parse(..., feature_version=(3, 9))` over `dirtywork/firewall/*.py` and `tests/test_firewall_*.py`: silent. `len(dirtywork.firewall.__all__) == 40` and every name resolves.
- No runtime behavior change: nothing outside the package imports it.

## Preserved receipts

Slug `issue-136-task-w3-of-0919173344-6ecff168`; local receipts at `~/.dirtywork/runs/<slug>/`: `run.json` (with the accept verdict), `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, stdout, launch/end timestamps, the `lms ps` lines at launch and the sampler CSV. Local provenance, not published artifacts.
