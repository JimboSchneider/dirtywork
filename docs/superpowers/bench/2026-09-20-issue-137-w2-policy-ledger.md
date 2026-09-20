# Issue #137 W2: policy context, the file-target rules, evaluate and the fail-closed entry points — run ledger

Plan: [issue #137 plan](../plans/2026-09-19-issue-137-firewall-policy.md), Task 2 (brief W2 of 3). Spec: [issue #137 spec](../specs/2026-09-19-issue-137-firewall-policy-design.md) §2, §3, §4, §6, §7, §11. Base: `c29cfa3` (the W1 run branch, stacked; `main` was `b14784a`). Branch: `dirtywork/issue-137-task-w2-of-0920113630-d8e649e1`.

## Worker setup

PyPI release checked on 2026-09-20: `dirtywork==0.13.2`, invoked through `pipx run --spec`, `--branch-from @issue-137-task-w1-of-0920113341-c569cfd4`. Host: LM Studio Bionic 1.1.5 on `localhost:1234/v1` (default `openai` provider). Worker: `qwen3.6-35b-a3b-splash` (Splash engine 0.0.4, MoE, about 3B active, 4-bit, 20.95 GB, disk-mapped), the only resident model; 262,144-token context reported and used; reasoning on by default, and `--max-tokens 24576` for this run because its brief carries a 41 KB test-file write (issue #173). Image: `dirtywork-worker-pytest:0.13`, network disabled. Verify gate: the default suite with two fix rounds; `--max-turns 60 --timeout 1800`.

Brief shape: one `edit_file` pair on `normalize.py` (`duplicate_positions` added and `canonicalize_batch` refactored onto it, base line numbers next to the anchor) plus two new files carried verbatim between BEGIN/END markers, re-extracted from the plan's fenced `### Worker brief W2` block and byte-compared to the generator's output; 57 KB, the largest brief of the series; no literal tool-call markers.

## Result

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `0920113630-d8e649e1` | completed | 10 | 193.0 | 297,153 | 15,902 | 82.4 | 0 | accept |

Wall and usage come from the transcript's `run_end`. Completion tokens include hidden reasoning, which the server counts inside `completion_tokens`. The rate divides completion tokens by whole-run wall time; it is not decode throughput.

Tool calls: `read_file` 1, `list_dir` 2, `edit_file` 3, `write_file` 2, `grep` 1, `bash` 3, `finish` 1; tool errors 0; truncations 0; trimmed turns 0; timeouts 0. The worker read `normalize.py`, listed the package and the tests directory, applied the three `edit_file` pairs, wrote both new files, ran the two test files (317 passed), then the full suite (2,276 passed, 8 skipped, 38 deselected in the container), and finished. In-container: the policy and normalize test files 317 passed, then the default suite; verify gate exit 0 on the first round.

`files_changed` = exactly the three files the brief named. Diff: `normalize.py` +29/−16; `policy.py` 369 lines (new); `tests/test_firewall_policy.py` 949 lines (new). The worker's `write_file` dropped the trailing newline on both new files; appending it is the one orchestrator edit, disclosed here, after which all three files are byte-identical to the brief.

Sampler (`tools/soak_sampler.sh`, [CSV](2026-09-20-issue-137-w2-sampler.csv)): 37 samples, free 27.8–30.1 GB, inactive 34.9–42.6 GB, one model resident.

## Host validation

- Full suite in the run's worktree, `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider`: **2,275 passed, 9 skipped, 38 deselected in 107.56 s**. Base (W1): 2,188 passed (87 new, as the plan's dry run expected).
- `git diff --check`: clean. `ast.parse(..., feature_version=(3, 9))` over `dirtywork/firewall/*.py` and `tests/test_firewall_*.py`: silent. The import-isolation test and the parent design §19 invariant tests are in the produced test file and pass; the existing `canonicalize_batch` tests pass unchanged on the refactor.
- No runtime behavior change: nothing outside the package imports it.

## Preserved receipts

Slug `issue-137-task-w2-of-0920113630-d8e649e1`; local receipts at `~/.dirtywork/runs/<slug>/`: `run.json` (with the accept verdict), `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, stdout, launch/end timestamps, the `lms ps` line at launch and the sampler CSV. Local provenance, not published artifacts.
