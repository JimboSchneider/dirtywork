# Issue #135 W3: action kinds, capabilities and the checked-in tables — run ledger

Plan: [issue #135 plan](../plans/2026-09-08-issue-135-firewall-schema.md), Task 3 (brief W3 of 5). Spec: [issue #135 spec](../specs/2026-09-08-issue-135-firewall-schema-design.md) §5, §8. Base: `2c55b5c` (the W2 run branch, PR #166, stacked; `main` was `32036c2`). Branch: `dirtywork/issue-135-task-w3-of-0910005213-813032ab`.

## Worker setup

PyPI release checked on 2026-09-10: `dirtywork==0.13.2`, invoked through `pipx run --spec`, `--branch-from @issue-135-task-w2-of-0910004407-d0134765`. Host: oMLX 0.6.4 (`--provider openai --base-url http://localhost:8000/v1`, `OPENAI_API_KEY` set to the oMLX key). Worker: `Qwen3-Coder-Next-MLX-4bit` (dirtywork assumed a 32,768-token context; no known window). Image: `dirtywork-worker-pytest:0.13`, network disabled. Verify gate: the default suite with two fix rounds; `--max-turns 60 --timeout 1800`.

Brief shape: two new files carried verbatim between BEGIN/END markers (one `write_file` each), re-extracted from the plan's fenced `### Worker brief W3` block; 7,100 characters. Launched by the idle-wait driver described in the W2 ledger, on a host with no other dirtywork runs.

## Result

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `0910005213-813032ab` | completed | 6 | 192.7 | 35,494 | 1,790 | 9.29 | 0 | accept |

Wall and usage come from the transcript's `run_end`. The rate divides completion tokens by whole-run wall time; it is not model decode throughput. oMLX's own log puts decode at 21.6 tok/s mean over the run's 6 requests (12.3–35.9).

Tool calls: `write_file` 2, `bash` 3, `finish` 0 (plain-answer finish); `read_file` 0, `list_dir` 0; truncations 0; trimmed turns 0. The first write landed at turn 1 and both files were in place by turn 2, with no reads: the brief carried everything. One tool error, the worker's own: it passed `timeout: 120.0` to `bash`, which the tool refused, and it retried at once. In-container: the new test file 7 passed, then the default suite. Verify gate exit 0 on the first round.

`files_changed` = exactly the two files the brief named. Both landed without the trailing newline after the last line (the known `write_file` quirk); the orchestrator appended it on the branch, after which both are byte-identical to the blocks re-extracted from the plan (`cmp`). That one-byte-per-file fix is the only orchestrator edit to worker output.

Sampler (`tools/soak_sampler.sh`, [CSV](2026-09-10-issue-135-w3-sampler.csv)): 38 samples in the run window, free memory 15.4–19.4 GB, LM Studio 0 models loaded, oMLX holding Qwen3.6 and Qwen3-Coder-Next.

## Host validation

- Full suite, `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider`: **1,730 passed / 9 skipped / 38 deselected**, 118.2 s. Base `2c55b5c` (W2): 1,723 passed (7 new, as the plan expected).
- `git diff --check`: clean. `ast.parse(..., feature_version=(3, 9))` over `dirtywork/firewall/*.py` and `tests/test_firewall_*.py`: silent.
- No runtime behavior change: nothing outside `dirtywork/firewall/` imports the package.

## Preserved receipts

Slug `issue-135-task-w3-of-0910005213-813032ab`; local receipts at `~/.dirtywork/runs/<slug>/`: `run.json` (with the accept verdict), `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, stdout JSON, stderr and sampler CSV. Local provenance, not published artifacts.
