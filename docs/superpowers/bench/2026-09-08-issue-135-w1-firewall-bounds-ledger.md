# Issue #135 W1: Firewall package, error type and bounds — run ledger

Plan: [issue #135 plan](../plans/2026-09-08-issue-135-firewall-schema.md), Task 1 (brief W1 of 5). Spec: [issue #135 spec](../specs/2026-09-08-issue-135-firewall-schema-design.md) §2, §6.1, §6.3. Base: `3fc17d8` (main, right after PR #157). Branch: `dirtywork/issue-135-task-w1-of-0908201738-b0e42b36`.

## Worker setup

PyPI release checked on 2026-09-08: `dirtywork==0.13.1`, invoked through `pipx run --spec`. Worker: `qwen/qwen3-coder-next` via LM Studio (`--provider openai --base-url http://localhost:1234/v1`), context window 65,536. Image: `dirtywork-worker-pytest:0.13`, network disabled. Verify gate: the default suite with two fix rounds; `--max-turns 60 --timeout 1800`.

Brief shape: four new files carried verbatim between BEGIN/END markers (one `write_file` each) and one exact `edit_file` pair for `pyproject.toml` line 34, generated from the orchestrator's scratch-clone dry run by `mkbrief.py`; 5,863 characters. The brief text was re-extracted from the plan's fenced block and was byte-identical to the dry-run copy.

## Result

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `0908201738-b0e42b36` | completed | 9 | 131.4 | 47,715 | 1,563 | 11.89 | 0 | accept |

Wall and usage come from the transcript's `run_end`. The rate divides completion tokens by whole-run wall time; it is not model decode throughput.

Tool calls: `write_file` 4, `edit_file` 1, `bash` 3, `finish` 1; `read_file` 0; truncations 0; trimmed turns 0. The first write landed at turn 1 and all five briefed changes were in place by turn 5, with no reads: the brief carried everything. One tool error, the worker's own: it passed `timeout: "120.0s"` to `bash`, which the tool refused as a non-integer, and it retried at once with `120s`. In-container: the new test file 5 passed, then the default suite 1,715 passed / 8 skipped. Verify gate exit 0 on the first round.

`files_changed` = exactly the five files the brief named. All four new files are byte-identical to the brief's blocks (checked with `cmp` against the blocks re-extracted from the plan); the `pyproject.toml` diff is the exact one-line `packages` change.

Sampler (`tools/soak_sampler.sh`): 28 samples in the run window, free memory 8.23–9.74 GiB, 4 models loaded.

## Host validation

- Full suite, `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider`: **1,714 passed / 9 skipped / 38 deselected**, 104.8 s. Baseline `3fc17d8`: 1,709 passed (5 new, as the plan expected).
- `git diff --check`: clean. `ast.parse(..., feature_version=(3, 9))` over `dirtywork/firewall/*.py` and `tests/test_firewall_*.py`: silent.
- Wheel gate (spec §11 test 15): `pipx run build --wheel` on a copy of the worktree produced `dirtywork-0.13.1-py3-none-any.whl` containing `dirtywork/firewall/{__init__,bounds,errors}.py`; `import dirtywork.firewall` succeeded from a fresh venv with only that wheel installed.
- No runtime behavior change: nothing outside `dirtywork/firewall/` imports the package.

## Preserved receipts

Slug `issue-135-task-w1-of-0908201738-b0e42b36`; local receipts at `~/.dirtywork/runs/<slug>/`: `run.json` (with the accept verdict), `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, launcher, stdout JSON, sampler CSV, the worker diff and the orchestrator's all-tasks dry-run diff. Local provenance, not published artifacts.
