# Issue #135 W4: ActionRequest, the canonical shapes, identity and FirewallEvent — run ledger

Plan: [issue #135 plan](../plans/2026-09-08-issue-135-firewall-schema.md), Task 4 (brief W4 of 5). Spec: [issue #135 spec](../specs/2026-09-08-issue-135-firewall-schema-design.md) §3, §4, §8, §9. Base: `8d76cd4` (the W3 run branch, PR #167, stacked; rebased onto `main` `0237bf5` after PR #166 merged). Branch: `dirtywork/issue-135-task-w4-of-0919121850-f11bfebd`.

## Worker setup

PyPI release checked on 2026-09-19: `dirtywork==0.13.2`, invoked through `pipx run --spec`, `--branch-from @issue-135-task-w3-of-0910005213-813032ab`. Host: LM Studio (the default `openai` provider at `http://localhost:1234/v1`; oMLX was not running that day, so this run is the series' first on LM Studio — same MLX weights as the oMLX runs). Worker: `qwen/qwen3-coder-next`, loaded with a 65,536-token context, which the server reported and dirtywork used (`context_window_source: provider:openai:server`). Image: `dirtywork-worker-pytest:0.13`, network disabled. Verify gate: the default suite with two fix rounds; `--max-turns 60 --timeout 1800`.

Brief shape: two new files carried verbatim between BEGIN/END markers (one `write_file` each), re-extracted from the plan's fenced `### Worker brief W4` block; 32,077 characters, the largest brief in the series. Launched by hand on a host with no other dirtywork runs. A first launch minutes earlier exited before any worker turn (`error: model 'qwen/qwen3-coder-next' not loaded`, exit 2, no run directory): LM Studio had just been updated and had dropped the loaded model. The relaunch loaded the model and started the run in the same command.

## Result

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `0919121850-f11bfebd` | completed | 6 | 255.7 | 105,312 | 11,065 | 43.3 | 0 | accept |

Wall and usage come from the transcript's `run_end`. The rate divides completion tokens by whole-run wall time; it is not model decode throughput. LM Studio's server log records no per-request token rates, so decode throughput for this run is not on the record.

Tool calls: `write_file` 3, `bash` 2, `finish` 0 (plain-answer finish); `read_file` 0, `list_dir` 0; truncations 0; trimmed turns 0; timeouts 0. The first `write_file` used the absolute path `/work/dirtywork/firewall/schema.py`, which the tool refused (`resolves outside the worktree`); the worker retried at once with the relative path, and both files were in place by turn 3 with no reads. In-container: the new test file 47 passed, then the default suite (exit 0; the transcript caps the output, so the in-container total is not recorded). Verify gate exit 0 on the first round.

`files_changed` = exactly the two files the brief named. Both are byte-identical to the blocks re-extracted from the plan (`cmp`), trailing newline included: unlike W2 and W3, no orchestrator edit was needed. Whether the missing-newline quirk is host-specific (oMLX there, LM Studio here) or run-to-run is not established by one run.

Sampler (`tools/soak_sampler.sh`, [CSV](2026-09-19-issue-135-w4-sampler.csv)): 50 samples in the run window, macOS free pages 0.1–3.5 GB with 34.8–58.3 GB inactive (reclaimable; free alone is not headroom on macOS, which is what stalled the overnight W4 driver for four days), LM Studio 1 model loaded (Coder-Next, 44.86 GB), oMLX not running. The `lms_status` column reflects the newer `lms ps` output format after the LM Studio update.

## Host validation

- Full suite, `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider`: **1,777 passed / 9 skipped / 38 deselected**, 102.1 s. Base `8d76cd4` (W3): 1,730 passed (47 new, as the plan expected).
- `git diff --check`: clean. `ast.parse(..., feature_version=(3, 9))` over `dirtywork/firewall/*.py` and `tests/test_firewall_*.py`: silent.
- No runtime behavior change: `schema.py` imports only its siblings in `dirtywork/firewall/`, and nothing outside the package imports it.

## Review fix (PR #171, reviewer P2)

The reviewer found that `FirewallEvent`'s public constructor accepted values outside the closed event contract: on a valid action-stage denial, `dataclasses.replace(event, decision="deny")` constructed and `to_dict()` then raised `AttributeError`; a `reason_class` contradicting `reason_code` was accepted; `capabilities` given as a list was accepted despite the frozen dataclass. All three reproduced on `e9a1502`. The fix makes `__post_init__` check `schema_version`, enum membership of `decision`, `kind`, `semantic_status`, `reason_code` and `reason_class`, that `capabilities` is a tuple of `Capability` values, and that `reason_class` is `reason_class(reason_code)`; 12 regression tests cover direct construction and `dataclasses.replace` for each invalid state and one valid replace.

Dry run on a scratch clone at `e9a1502`: tests-only 11 failed / 48 passed; with the fix 59 passed; full suite 1,789 passed. The brief was generated from the same four edit pairs, with base line numbers next to each anchor (6,406 characters; `orchestrator/brief.txt` and `orchestrator/apply_w4fix.py` in the run directory).

Host for this run: LM Studio Bionic 1.1.5 serving `qwen3.8-27b-splash` (Splash engine 0.0.4) on the same `localhost:1234/v1`, reasoning on by default (issue #173), `--max-tokens 16384`; `--branch-from @issue-135-task-w4-of-0919121850-f11bfebd`.

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `w4-review-fix-0919135849-86309d24` | completed | 6 | 137.1 | 38,767 | 2,114 | 15.4 | 0 | accept |

Tool calls: `read_file` 3 (the two anchors and the test file head), `edit_file` 4 (all four in one turn), `bash` 2, `finish` 1; tool errors 0; truncations 0. In-container: 59 passed, then the default suite; verify gate exit 0 on the first round. `schema.py` came out byte-identical to the dry run. The test file carried one extra blank line after the import block; the orchestrator removed it so the file matches the dry run, the only orchestrator edit. Host suite after the fix: **1,789 passed / 9 skipped / 38 deselected**, 105.0 s (base `e9a1502`: 1,777; 12 new). Sampler ([CSV](2026-09-19-issue-135-w4fix-sampler.csv)): 27 samples, free 30.7–32.9 GB, inactive 33.6–42.9 GB, one model loaded.

The plan's fenced `### Worker brief W4` block still carries the pre-review `schema.py`; this section and the run's `diff.patch` are the record of what changed after it.

## Preserved receipts

Slug `issue-135-task-w4-of-0919121850-f11bfebd`; local receipts at `~/.dirtywork/runs/<slug>/`: `run.json` (with the accept verdict), `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, stdout, launch/end timestamps and the sampler CSV. Local provenance, not published artifacts.
