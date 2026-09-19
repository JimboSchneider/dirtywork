# Issue #136 W2: name recovery, the field table and the single-call pass — run ledger

Plan: [issue #136 plan](../plans/2026-09-19-issue-136-firewall-normalization.md), Task 2 (brief W2 of 3). Spec: [issue #136 spec](../specs/2026-09-19-issue-136-firewall-normalization-design.md) §3, §4, §5, §7, §8, §10, §12. Base: `d60ac1c` (the W1 run branch at its ledger commit, PR #176, stacked; `main` was `ec16d8b`; attempt 1 based on `0a9cb60`, the same code). Branch: `dirtywork/issue-136-task-w2-of-0919173022-c3affa39`.

## Worker setup

PyPI release checked on 2026-09-19: `dirtywork==0.13.2`, invoked through `pipx run --spec`, `--branch-from @issue-136-task-w1-of-0919171842-89f8676e`. Host: LM Studio Bionic 1.1.5 on `localhost:1234/v1` (default `openai` provider). Worker: `qwen3.6-35b-a3b-splash` (Splash engine 0.0.4, MoE, about 3B active, 4-bit, 20.95 GB, disk-mapped); 262,144-token context reported and used; reasoning on by default and `--max-tokens 16384` (issue #173). Image: `dirtywork-worker-pytest:0.13`, network disabled. Verify gate: the default suite with two fix rounds; `--max-turns 60 --timeout 1800`.

Brief shape: two new files carried verbatim between BEGIN/END markers (one `write_file` each), re-extracted from the plan's fenced `### Worker brief W2` block and byte-compared to the generator's output; 38,877 characters, the largest brief of the series (a 388-line module and a 599-line test file).

**Attempt 1** (`issue-136-task-w2-of-0919172137-2e29dcb2`) ended `model_error` after zero worker turns: the Splash engine answered the first request with HTTP 400 `resource_timeout`, "memory did not become available within the resource wait limit". At the time a 44.86 GB `qwen/qwen3-coder-next` was resident in Bionic beside the 20.95 GB worker and the W1 host suite was running on the host; the sampler had free pages at 0.1–4.4 GB through the W1 window. With the owner's go the second model was unloaded (free pages 65.9 GB afterwards) and the run relaunched from the same base. Its receipts are kept under its own run directory.

**Attempt 2** (`issue-136-task-w2-of-0919172352-c5979752`, from the same base, host freed) completed in 8 turns and 220.1 s with `normalize.py` byte-identical, but the test file arrived with every literal `<tool_call>` inside its string fixtures replaced by `[]` (seven places: the recovery fixture, the two pathological strings and the one-mebibyte name). The worker then saw the giant-name test fail, because `"[]" * 100_000` is under a mebibyte, and patched that one fixture to `"[TOOL_CALLS]" * 100_000`; the other six stayed mangled and the file passed weaker than written. A direct probe of the engine confirmed the cause: in a tool-call argument, `<tool_call>` is a special token of the model's chat template and the parser consumes it, while `<function=`, `[TOOL_CALLS]` and `<|tool_call|>` pass through. The module was unaffected because it builds every marker by concatenation, for exactly this reason (the registry's own source says so). Verdict reject; `orchestrator/test-file-vs-brief.diff` in its run directory is the receipt. The reference test file was rebuilt to construct markers by concatenation too, the W2 and W3 briefs were regenerated (the test count is unchanged), and the run below is attempt 3.

## Result

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `0919173022-c3affa39` | completed | 6 | 164.5 | 122,647 | 10,829 | 65.8 | 0 | accept |

Wall and usage come from the transcript's `run_end`. Completion tokens include hidden reasoning, which the server counts inside `completion_tokens`. The rate divides completion tokens by whole-run wall time; it is not decode throughput.

Tool calls: `list_dir` 2, `write_file` 2, `edit_file` 1, `bash` 2, `finish` 1; tool errors 0; truncations 0; trimmed turns 0; timeouts 0. The worker listed the package and the tests directory, wrote both files in one turn, then noticed that its own 22 KB write of the test file had rendered one identifier as `_CAPABILITY_CASED` where the brief says `_CAPABILITY_CASES`, and repaired it with a one-line `edit_file` before running anything; the repaired file is byte-identical to the brief, so the slip and its fix are visible only in the transcript. In-container: the new test file 219 passed, then the default suite; verify gate exit 0 on the first round.

`files_changed` = exactly the two files the brief named. Both are byte-identical to the blocks re-extracted from the plan (`cmp`), trailing newlines included; no orchestrator edit.

Sampler (`tools/soak_sampler.sh`, [CSV](2026-09-19-issue-136-w2-sampler.csv)): 32 samples in the run window, macOS free pages 56.6–58.7 GB, inactive 18.9–30.0 GB, one model resident.

## Host validation

- Full suite in the run's worktree, `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider`: **2,085 passed / 9 skipped / 38 deselected**, 115.9 s. Base `d60ac1c` (W1): 1,866 passed (219 new, as the plan's dry run expected).
- `git diff --check`: clean. `ast.parse(..., feature_version=(3, 9))` over `dirtywork/firewall/*.py` and `tests/test_firewall_*.py`: silent. The import-isolation test and the field-table cross-check against the live registry are in the produced test file and pass.
- No runtime behavior change: nothing outside the package imports it.

## Preserved receipts

Slug `issue-136-task-w2-of-0919173022-c3affa39` (and attempt 1's slug above); local receipts at `~/.dirtywork/runs/<slug>/`: `run.json` (with the accept verdict), `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, stdout, launch/end timestamps, the `lms ps` lines at launch and the sampler CSV. Local provenance, not published artifacts.
