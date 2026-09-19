# Issue #135 W5: the boundary validator, Rejection and the package re-exports — run ledger

Plan: [issue #135 plan](../plans/2026-09-08-issue-135-firewall-schema.md), Task 5 (brief W5 of 5, the last). Spec: [issue #135 spec](../specs/2026-09-08-issue-135-firewall-schema-design.md) §2, §6. Base: `e9a1502` (the W4 run branch, PR #171, stacked on W3's PR #167; `main` was `0237bf5`). Branch: `dirtywork/issue-135-task-w5-of-0919122804-ad2f9c4b`.

## Worker setup

PyPI release checked on 2026-09-19: `dirtywork==0.13.2`, invoked through `pipx run --spec`, `--branch-from @issue-135-task-w4-of-0919121850-f11bfebd`. Host: LM Studio (the default `openai` provider at `http://localhost:1234/v1`), as for W4 the same day; oMLX was not running. Worker: `qwen/qwen3-coder-next`, 65,536-token context reported by the server (`context_window_source: provider:openai:server`). Image: `dirtywork-worker-pytest:0.13`, network disabled. Verify gate: the default suite with two fix rounds; `--max-turns 60 --timeout 1800`.

Brief shape: two new files carried verbatim between BEGIN/END markers (one `write_file` each) plus one `edit_file` on `dirtywork/firewall/__init__.py` whose new text is the complete 48-line file, re-extracted from the plan's fenced `### Worker brief W5` block; 18,200 characters. Launched by hand right after W4's review, on a host with no other dirtywork runs.

## Result

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `0919122804-ad2f9c4b` | completed | 7 | 166.3 | 69,167 | 4,480 | 26.9 | 0 | accept |

Wall and usage come from the transcript's `run_end`. The rate divides completion tokens by whole-run wall time; it is not model decode throughput, which LM Studio's server log does not record.

Tool calls: `read_file` 1, `write_file` 2, `edit_file` 1, `bash` 2, `finish` 0 (plain-answer finish); `list_dir` 0; tool errors 0; truncations 0; trimmed turns 0; timeouts 0. The worker read the one-line `__init__.py` before editing it, wrote both new files, then applied the edit; all three changes were in place by turn 4. In-container: the new test file 40 passed, then the default suite (exit 0; the transcript caps the output, so the in-container total is not recorded). Verify gate exit 0 on the first round.

`files_changed` = exactly the three files the brief named. All three are byte-identical to the blocks re-extracted from the plan (`cmp`), trailing newlines included; as in W4, no orchestrator edit was needed. Two LM Studio runs in a row without the missing-newline quirk that both oMLX runs (W2, W3) showed.

Sampler (`tools/soak_sampler.sh`, [CSV](2026-09-19-issue-135-w5-sampler.csv)): 32 samples in the run window, macOS free pages 0.2–3.2 GB with 35.0–57.5 GB inactive (reclaimable), LM Studio 1 model loaded (Coder-Next, 44.86 GB), oMLX not running.

## Host validation

- Full suite, `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider`: **1,817 passed / 9 skipped / 38 deselected**, 102.0 s. Base `e9a1502` (W4): 1,777 passed (40 new, as the plan expected; the five-brief series adds 108 tests over the plan's 1,709 starting baseline).
- `git diff --check`: clean. `ast.parse(..., feature_version=(3, 9))` over `dirtywork/firewall/*.py` and `tests/test_firewall_*.py`: silent.
- `import dirtywork.firewall` from the worktree: 33 names in `__all__`, `check_request` callable. No runtime behavior change: nothing outside the package imports it.

## Preserved receipts

Slug `issue-135-task-w5-of-0919122804-ad2f9c4b`; local receipts at `~/.dirtywork/runs/<slug>/`: `run.json` (with the accept verdict), `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, stdout, launch/end timestamps and the sampler CSV. Local provenance, not published artifacts.
