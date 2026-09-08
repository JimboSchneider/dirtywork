# Issue #151: one shared rg/grep argv builder — run ledger

Plan: [issue #151 plan](../plans/2026-09-08-issue-151-shared-grep-argv.md). Base: `0aab2c6` (main, right after PR #155). Branch: `dirtywork/issue-151-dry-host-grep-0908160656-881e042d`.

## Worker setup

PyPI release checked on 2026-09-08: `dirtywork==0.13.1`, invoked through `pipx run --spec`. Worker: `qwen/qwen3-coder-next` via LM Studio (`--provider openai --base-url http://localhost:1234/v1`), context window 65,536. Image: `dirtywork-worker-pytest:0.13`, network disabled. Verify gate: the default suite with two fix rounds; `--max-turns 60 --timeout 1800`.

Brief shape: fix-first, four exact `edit_file` pairs with line numbers generated from the dry-run script, six tests appended verbatim, never `write_file`. Line numbers were recomputed against the post-PR #155 head (the grep body moved by seven lines) and the dry run repeated there: red 5 / green 277.

## Result

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `0908160656-881e042d` | completed | 21 | 102.6 | 162,822 | 2,395 | 23.34 | 0 | accept |

Wall and usage come from the transcript's `run_end`. The rate divides completion tokens by whole-run wall time; it is not model decode throughput.

Tool calls: `read_file` 10, `list_dir` 2, `apply_edits` 4, `append_file` 1, `bash` 3; `write_file` 0; truncations 0; trimmed turns 0. Two tool errors, both the worker's own path mistakes (`read_file("work/dirtywork/tools.py")` and `list_dir("/work")`, refused as expected), after which it recovered. The first edit landed at turn 17; the worker applied each of the four briefed pairs through `apply_edits` rather than `edit_file`, which is fine (same exact-match semantics). The sixteen turns before the first edit were reads of the quoted ranges plus the two mis-pathed calls: the line numbers did their job once the worker looked in the right file.

`files_changed` = `dirtywork/tools.py`, `dirtywork/sandbox/docker.py`, `tests/test_tools_files.py` only; `tests/test_docker_sandbox.py` untouched, as the brief required. Verify gate (in-container default suite) exit 0 on the first round. Diff identical to the orchestrator's dry-run patch except one blank line before the appended tests (the worker sent one leading newline instead of two, the third time today); the orchestrator added it before committing.

Sampler (`tools/soak_sampler.sh`): 21 samples in the run window, free memory 3.51–5.53 GiB, 4 models loaded.

## Host validation

- `tests/test_tools_files.py` + `tests/test_docker_sandbox.py`: **277 passed** (272 at baseline + 5 new; four builder parametrizations and one host argv test).
- Full suite, `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider`: **1,709 passed / 9 skipped / 38 deselected**, 100.7 s. Baseline `0aab2c6`: 1,704 passed.
- Red check (scratch clone, tests only, baseline production module): 5 failed.
- `git diff --check`: clean.
- The Docker argv is byte-identical to before (its tests did not change); the host argv now ends `["--", <absolute path>]`.

## Preserved receipts

Slug `issue-151-dry-host-grep-0908160656-881e042d`; local receipts at `~/.dirtywork/runs/<slug>/`: `run.json` (with the accept verdict), `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, launcher, stdout JSON, stderr, sampler CSV, and the dry-run and worker diffs. Local provenance, not published artifacts.
