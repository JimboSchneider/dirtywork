# Issue #146: bare `-` path operands — run ledger

Plan: [Issue #146: bare `-` path operands](../plans/2026-09-08-issue-146-bare-dash-operands.md). Base: `516a3c1` (main). Branch: `dirtywork/issue-146-finish-the-docker-0908124429-0e7f123f`.

## Worker setup

PyPI release checked on 2026-09-08: `dirtywork==0.13.1`, invoked through `pipx run --spec`. Worker: `qwen/qwen3-coder-next` via LM Studio (`--provider openai --base-url http://localhost:1234/v1`), reported context window 65,536. Image: `dirtywork-worker-pytest:0.13`, network disabled. Verify gate: the default suite (`python3 -m pytest -q -p no:cacheprovider`) with two fix rounds; `--max-turns 60 --timeout 1800`.

Brief shape: the same fix-first, exact-`edit_file`-pairs, tests-appended-verbatim shape as the 2026-09-06 comparison run in the [PR #145 ledger](2026-09-07-docker-static-path-operands-ledger.md), plus a "read only the line ranges you need" hint. Every old string was checked to occur exactly once, and the whole patch was dry-run on a scratch clone (red on baseline, green with the fix) before the worker saw it.

## Result

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `0908124429-0e7f123f` | completed | 45 | 149.7 | 731,503 | 4,749 | 31.72 | 1 | accept |

Wall and usage come from the transcript's `run_end`. The rate divides completion tokens by whole-run wall time; it is not model decode throughput. The one nudge was `no_change` at turn 10.

Tool calls: `read_file` 16, `grep` 15, `edit_file` 10, `append_file` 1, `bash` 2; `write_file` 0; truncations 0; trimmed turns 0. Every `edit_file` succeeded on the first try; the ten calls covered the twelve briefed pairs because the worker combined adjacent pairs. The first edit landed at turn 18: the seventeen turns before it were `read_file`/`grep` calls locating anchors the brief had already quoted, which is where this run's 45 turns and 731k prompt tokens went (the 2026-09-06 comparison run, same brief shape, took 21 turns and 200k). A brief that quotes anchors could also quote their line numbers; worth trying on the next one of these.

`files_changed` = `dirtywork/sandbox/docker.py`, `tests/test_docker_sandbox.py` only. Verify gate (in-container default suite) exit 0 on the first round. Diff byte-identical to the orchestrator's dry-run patch.

Sampler (`tools/soak_sampler.sh`): 30 samples in the run window, free memory 5.13–9.37 GiB, 4 models loaded. Free memory is far below the 2026-09-06 run's 23–27 GiB because `qwen/qwen3.6-35b-a3b` (20.4 GB) was resident alongside `qwen3-coder-next` and `devstral-small-2`; the run did not slow down measurably (149.7 s vs 155.1 s).

## Host validation

- `tests/test_docker_sandbox.py`: **176 passed** (164 at baseline + 12 new cases).
- Full suite, `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider`: **1,701 passed / 9 skipped / 38 deselected**, 103.0 s. Baseline `516a3c1`: 1,689 passed.
- Red check (scratch clone, test edits only, baseline production module): 18 failed — the nine updated expectations plus nine of the twelve new cases. The three new cases that pass on baseline are `_rel(".")`, `_rel("./")` and the GNU-find `-` parameter, which PR #145's `./` prefix already covered.
- `git diff --check`: clean.
- Fourth site: the issue listed `head`, `rg`/`grep` and the `ls` fallback's `cd`. Anchoring in `_rel` also fixes `APPEND_GUARD_SCRIPT`'s `stat -Lc %s -- "$1"`, which reads stdin for `-` and reports size 0 (verified in the image), so `append_file("-")`'s cap check underestimated by the file's real size. The write-side scripts (`cat >`, `cp --`, `mv -fT --`, `chmod --reference=`) already treated `-` as a file.

## Preserved receipts

Slug `issue-146-finish-the-docker-0908124429-0e7f123f`; local receipts at `~/.dirtywork/runs/<slug>/`: `run.json` (with the accept verdict), `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, launcher script, stdout JSON, stderr, sampler CSV, and the dry-run and worker diffs. These paths are local provenance, not published artifacts.
