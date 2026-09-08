# Issue #150: `_probe` must not cache a DockerError — run ledger

Plan: [Issue #150 plan](../plans/2026-09-08-issue-150-probe-docker-error.md). Base: `e916b55` (main). Branch: `dirtywork/issue-150-dockersandboxprobe-caches-fals-0908141731-2b9877a0`.

## Worker setup

PyPI release checked on 2026-09-08: `dirtywork==0.13.1`, invoked through `pipx run --spec`. Worker: `qwen/qwen3-coder-next` via LM Studio (`--provider openai --base-url http://localhost:1234/v1`), context window 65,536. Image: `dirtywork-worker-pytest:0.13`, network disabled. Verify gate: the default suite with two fix rounds; `--max-turns 60 --timeout 1800`.

Brief shape: the fix-first, exact-`edit_file`-pair, tests-appended-verbatim shape of the issue #146 run, plus the experiment that ledger proposed: **line numbers next to every anchor** ("the old string is `docker.py` lines 769-778, the body of `def _probe`, which starts at line 768"; "the test file has 3101 lines and you only need to append"). Dry-run on a scratch clone before briefing: the two new tests fail on baseline, 178 pass with the fix.

## Result

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `0908141731-2b9877a0` | completed | 7 | 83.8 | 36,243 | 1,173 | 14.00 | 0 | accept |

Wall and usage come from the transcript's `run_end`. The rate divides completion tokens by whole-run wall time; it is not model decode throughput.

Tool calls: `read_file` 2, `edit_file` 1, `append_file` 1, `bash` 2; `write_file` 0; tool errors 0; truncations 0. The first edit landed at turn 3. Compared with the issue #146 run (same brief shape without line numbers): 45 turns → 7, 731,503 prompt tokens → 36,243, 17 anchor-hunting turns → 2 reads. The two briefs differ in size too (twelve edit pairs vs one), so this is one data point, not a controlled comparison; but the worker went straight to the quoted lines, which is what the line numbers were for.

`files_changed` = `dirtywork/sandbox/docker.py`, `tests/test_docker_sandbox.py` only. Verify gate (in-container default suite) exit 0 on the first round. Diff byte-identical to the orchestrator's dry-run patch.

Sampler (`tools/soak_sampler.sh`): 17 samples in the run window, free memory 6.05–10.03 GiB, 4 models loaded (same resident set as the issue #146 run).

## Host validation

- `tests/test_docker_sandbox.py`: **178 passed** (176 at baseline + 2 new).
- Full suite, `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider`: **1,703 passed / 9 skipped / 38 deselected**, 110.6 s. Baseline `e916b55`: 1,701 passed.
- Red check (scratch clone, tests only, baseline production module): both new tests fail.
- `git diff --check`: clean.

## Preserved receipts

Slug `issue-150-dockersandboxprobe-caches-fals-0908141731-2b9877a0`; local receipts at `~/.dirtywork/runs/<slug>/`: `run.json` (with the accept verdict), `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, launcher, stdout JSON, stderr, sampler CSV, and the dry-run and worker diffs. Local provenance, not published artifacts.
