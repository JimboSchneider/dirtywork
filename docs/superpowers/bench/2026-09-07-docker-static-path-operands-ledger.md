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
