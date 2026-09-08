# Docker static-tool path operands: run ledger

Plan: [Docker static-tool path operands](../plans/2026-09-07-docker-static-path-operands.md). Baseline: `da9094d`; worker base `45b42c1` adds only the plan. Final branch: `codex/docker-static-paths`.

## Worker setup and results

PyPI release checked on 2026-09-07: `dirtywork==0.13.1`, invoked through `pipx run --spec`. Local worker: `qwen/qwen3-coder-next`, OpenAI-compatible LM Studio endpoint `http://localhost:1234/v1`, reported context window 65,536. Docker image: `dirtywork-worker-pytest:0.13`, digest `sha256:ef1a33aa5cd8e6a76e0f1062aea9c626cda91356af99c108d1627a2f7f96aebc`; network disabled. Both runs used the default suite as the verification command with two verification rounds, but neither reached that gate.

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `0906202556-593549d4` | interrupted | 45 | 295.8 | 1,048,718 | 9,678 | 32.72 | 1 | reject |
| `0906203327-e1d8b961` (resume) | interrupted | 15 | 124.4 | 211,725 | 8,101 | 65.12 | 0 | reject |

Wall and usage come from each transcript's `run_end`. The rate divides completion tokens by whole-run wall time; it is not model decode throughput. Nudges count transcript `nudge` events; the first run's one nudge was `no_change`.

`tools/soak_sampler.sh` ran during both attempts. Within each run's UTC start/end interval, it captured 57 and 24 samples, respectively. Free-memory ranges were 23.76–62.97 GiB and 22.89–30.83 GiB. The resumed CSV includes earlier sampler setup rows; these are excluded from those figures.

The initial worker repeatedly assumed that capability probes rerun on every call to the same sandbox. It was interrupted and resumed with exact parameterized test feedback. The resumed worker rewrote/truncated most of the test file, then restored the baseline file, leaving no completed regressions. It was interrupted and rejected. Both exports succeeded. The final patch uses the repository's fallback permission after a failed resume: Codex completed the minimal operand change and tests on the planned branch. No broad worker test rewrite was retained.

## Preserved receipts

Full slugs are `fix-docker-static-tool-pathoption-confus-0906202556-593549d4` and `fix-docker-static-tool-pathoption-confus-0906203327-e1d8b961`. For each slug, local receipts remain at `~/.dirtywork/runs/<slug>/`: `run.json` (including reject verdict), `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the invocation, stdout/stderr, exact brief/feedback and sampler CSV. The initial run's `orchestrator/verify_operands.py` is the disposable-container verification script; the resumed run's `orchestrator/red.log` records the baseline test failures. These paths are local provenance, not published artifacts.

## Independent validation

- All eight new parameterized regressions failed against the original production module, specifically on the unsafe emitted path arguments. No fixture or collection failures.
- With the fix, `pipx run --spec pytest python -m pytest -q -p no:cacheprovider tests/test_docker_sandbox.py`: **164 passed**.
- A disposable, read-only, network-disabled container with a writable temporary directory executed actual argv emitted by `DockerSandbox`. **Eight cases passed**: GNU find listing `-delete`, `!`, `(` and `.`, ripgrep searching `--pre=./helper` and an ordinary directory with a glob, and fallback grep searching `-v` and an ordinary directory with a glob. Every sentinel survived, the helper marker was absent, and globs excluded the nonmatching extension.
- Separate baseline reproduction confirmed GNU find 4.9.0 still honors `-delete` after `--`; prefixing the starting point with `./` resolves the ambiguity. All destructive effects were confined to temporary fixtures. Ripgrep version: 13.0.0.
- Independent code review: **no findings**. The reviewer checked the final production/test diff, path normalization, argument order, output preservation and scope; test-execution evidence came from the orchestrator.
- Full host gate: `pipx run --spec pytest python -m pytest -q -p no:cacheprovider` — **1,689 passed, 9 skipped, 38 deselected**, 105.99 seconds. The baseline was 1,681 passed / 9 skipped; the eight new cases account for the increase. Host suite output is preserved as the resumed run's `orchestrator/host-suite.log`.
- `git diff --check`: clean.

## Comparison run (maximal brief, fix-first) — 2026-09-07

Same invocation as the #145 plan (released 0.13.1 via pipx, `qwen/qwen3-coder-next`, `--provider openai --base-url http://localhost:1234/v1`, `--sandbox docker --image dirtywork-worker-pytest:0.13`, default suite as `--verify` with 2 rounds, `--max-turns 60 --timeout 1800`), but `--branch-from da9094d` so the bug is present regardless of #145's merge state. Brief: production fix first (two exact `edit_file` old/new pairs), two existing expectations as exact `edit_file` pairs, the eight tests pasted verbatim, an explicit "NEVER write_file this file" rule and a read-only-the-ranges-you-need hint. No worker red step. One deliberate deviation from the drafted brief: the eight tests went in through ONE `append_file` rather than `insert_after`, because the test file's last line (`assert sb.watchdog.violation is None`) occurs six times at `da9094d` and the only unique line near EOF is the one before it, so no `insert_after` anchor can land at end-of-file.

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `0906210541-f78bce4f` | completed | 21 | 155.1 | 200,630 | 2,384 | 15.37 | 0 | reject (comparison only; #145 carries the fix) |

Tool calls: `read_file` 6, `grep` 6, `edit_file` 4, `append_file` 1, `bash` 3; `write_file` 0; truncations 0; trimmed turns 0. `files_changed` = `dirtywork/sandbox/docker.py`, `tests/test_docker_sandbox.py` only. Verify gate (in-container default suite) exit 0 on the first round. Diff identical to PR #145's except one blank line fewer before the appended tests (the worker sent one leading newline instead of two). Sampler: 30 samples in the run window, free memory 23.21–27.19 GiB, 4 models loaded. Host: the eight regressions fail on the baseline production module (8 failed), pass on the fix (164 passed in `test_docker_sandbox.py`), full suite 1,689 passed / 9 skipped / 38 deselected. `git diff --check` clean.

Reading: the brief was the problem. Same model, same image, same budgets; the difference is that the fix and every string were given exactly, the test file was declared off-limits to `write_file`, and the worker was told which line ranges to read.

Receipts: `~/.dirtywork/runs/fix-docker-static-tool-pathoption-confus-0906210541-f78bce4f/` — `run.json` (reject verdict), `transcript.jsonl`, and `orchestrator/` (brief, argv, stdout JSON, stderr, sampler CSV, red/green/host-suite logs, `worker.diff`). Note: `runs clean --force --keep-transcript` deleted `diff.patch` and the `orchestrator/` directory; they were restored from copies afterwards.
