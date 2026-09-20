# Issue #138 W3: the Runner and CLI integration tests — run ledger

Plan: [issue #138 plan](../plans/2026-09-20-issue-138-firewall-gate.md), Task 3 (brief W3 of 3, the last). Spec: [issue #138 spec](../specs/2026-09-20-issue-138-firewall-gate-design.md) §10. Base: `355faad` (the W2 run branch, stacked; `main` was `a239f37`). Branch: `dirtywork/issue-138-w3-of-3-0920165527-63817205`.

## Worker setup

Released `dirtywork==0.13.2` through `pipx run --spec`, with `--branch-from @issue-138-w2-of-3-0920164152-5f0316aa`. Host: LM Studio Bionic 1.1.5 on `localhost:1234/v1`. Worker: `qwen3.6-35b-a3b-splash`, the only resident model, 262,144-token context, `--max-tokens 16384`. Image: `dirtywork-worker-pytest:0.13`, network disabled. Verify gate: the default suite with two available fix rounds; `--max-turns 60 --timeout 1800`.

Brief shape: two `edit_file` pairs, 23 KB. The first appends a 447-line block of Runner tests after `test_mixed_turn_finish_first_then_timeout_with_passing_verify_ends_clean`; the second appends one CLI test to `tests/test_main.py`. Generated from the dry run and replayed against a snapshot of this branch's starting tree before launch.

## Result

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `3-0920165527-63817205` | completed | 22 | 287.4 | 374,588 | 35,811 | 124.6 | 0 | accept |

Wall and usage come from the transcript's `run_end`. Completion tokens include the hidden reasoning the server counts inside `completion_tokens`; the rate divides completion tokens by whole-run wall time, verification included, and is not decode throughput.

Tool calls: `list_dir` 2, `read_file` 14, `grep` 10, `edit_file` 3, `bash` 2, `finish` 1; tool errors 3; truncations 0; trimmed turns 0; timeouts 0. Twenty of the 22 turns were orientation: the worker read and grepped both target files before its first write. The three tool errors were all recovered from in the next turn: `path '/work' resolves outside the worktree (absolute paths are not allowed)` (the existing guardrails refusing an absolute path, the behavior the Firewall is now wired to own), a mistyped relative path, and one `old_string occurs 2 times in tests/test_main.py` before it widened the anchor. The two named test files then reported 403 passed, the full suite passed, and it finished. In-container the suite reported 2,383 passed and 8 skipped, one more passed and one fewer skipped than the host, the usual container-only test. Verify gate exit 0 on the first round.

`files_changed` = exactly the two files the brief named. Diff: `tests/test_runner.py` +447, `tests/test_main.py` +40. Both byte-identical to the brief on the first pass. **No orchestrator edits.**

Sampler ([CSV](2026-09-20-issue-138-w3-sampler.csv)): 57 samples, free 17.0–20.3 GB, inactive 36.5–48.6 GB, one model resident.

## Host validation

- Full suite in the run's worktree: **2,382 passed, 9 skipped, 38 deselected in 108.04 s**. Base (W2): 2,343. The delta of 39 cases comes from 20 new test functions, several parametrized, among them the sweep that drives one crafted turn per `ReasonCode` through `run()`.
- `git diff --check`: clean. `ast.parse(..., feature_version=(3, 9))`: silent over both test files, `runner.py` and `firewall_gate.py`.
- `dirtywork/` untouched, as the brief required.

## The series

| Brief | Turns | Wall s | Prompt tok | Completion tok | Orchestrator edits | Host suite |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| W1 gate module | 7 | 155.5 | 99,922 | 8,046 | trailing newline ×2 | 2,343 |
| W2 wiring (first run) | 44 | 423.7 | 2,154,879 | 29,292 | none; one pair missed, resumed | 2,343 |
| W2 wiring (resume) | 5 | 114.0 | 67,321 | 771 | none | 2,343 |
| W3 tests | 22 | 287.4 | 374,588 | 35,811 | none | 2,382 |

Every brief replayed byte-identical to its base before launch, and every produced branch matches its brief byte for byte after review. The one miss in the series was invisible to the verify gate and visible only to the byte comparison.

## Preserved receipts

Slug `issue-138-w3-of-3-0920165527-63817205`; local receipts at `~/.dirtywork/runs/<slug>/`: `run.json` with the accept verdict, `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, stdout, launch and end timestamps, the `lms ps` line at launch and the sampler CSV. Local provenance, not published artifacts.
