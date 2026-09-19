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

## Review fix (PR #179, reviewer P2)

The reviewer found that `canonicalize_batch` compared unvalidated ids for duplicates, so two deeply nested list ids make the `==` comparison recurse on Python 3.9 and take the whole batch down, a valid neighbour included; on newer Pythons the same input instead reports the second malformed id as `call_id_duplicate` rather than `call_id_invalid`. Both confirmed against `03d0371` (the recursion on the reviewer's 3.9 reproduction; no 3.9 interpreter on this host, the CI 3.9 job carries the regression test). The fix restricts duplicate detection to `str` ids: a non-string id is never compared and never a duplicate, and `check_request` rejects it as `call_id_invalid`. Two regression tests: two 2,000-deep nested-list ids beside a valid request (both `call_id_invalid`, the neighbour canonicalized), and two equal list ids (both invalid, neither a duplicate). Spec §9 updated in PR #178.

Dry run on the scratch clone: `tests/test_firewall_normalize.py` 230 passed, full suite 2,180 passed. The brief was generated from the same three edit pairs against the W3 branch head, with base line numbers next to each anchor (4,605 characters; `orchestrator/brief.txt` and `orchestrator/edit-pairs/` in the run directory).

Host for this run: LM Studio Bionic 1.1.5 serving `qwen3.6-35b-a3b-splash` on `localhost:1234/v1`, reasoning on by default, `--max-tokens 16384`; `--branch-from @issue-136-task-w3-of-0919173344-6ecff168`.

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `w3-review-fix-0919175145-01d5ee8d` | completed | 12 | 126.9 | 78,815 | 2,898 | 22.8 | 0 | accept |

Tool calls: `read_file` 6 (the anchors, before editing), `edit_file` 3, `bash` 3, `finish` 1; tool errors 0; truncations 0. In-container: 230 passed, then the default suite; verify gate exit 0 on the first round. Both files byte-identical to the dry run after the brief's pairs are applied to the base; no orchestrator edit. Host suite after the fix: **2,180 passed / 9 skipped / 38 deselected**, 106.9 s (base `03d0371`: 2,178; 2 new). Sampler ([CSV](2026-09-19-issue-136-w3fix-sampler.csv)): 25 samples, free 52.0–54.1 GB, inactive 21.8–32.4 GB, one model resident.

The plan's fenced `### Worker brief W3` block still carries the pre-review `canonicalize_batch`; this section and the run's `diff.patch` are the record of what changed after it.

## Preserved receipts

Slug `issue-136-task-w3-of-0919173344-6ecff168`; local receipts at `~/.dirtywork/runs/<slug>/`: `run.json` (with the accept verdict), `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, stdout, launch/end timestamps, the `lms ps` lines at launch and the sampler CSV. Local provenance, not published artifacts.
