# Issue #184: Firewall policy hardening — run ledger

Issue: [#184](https://github.com/jimboschneider/dirtywork/issues/184), the two optional items from the review of PR #182 (issue #137 W2). No spec or plan cycle: the issue body names the fix and the tests. Base: `73654f6` (`main`). Branch: `dirtywork/issue-184-two-hardening-items-1018112715-12a85bf6`.

## Worker setup

Released `dirtywork==0.13.2` through `pipx run --spec`, from `main` (no `--branch-from`). Host: Bionic 1.1.8 (it self-updated from 1.1.7 when launched for this run), serving on `http://localhost:1235/v1`, the port Bionic 1.1.8 opens by default, passed with `--base-url`. Worker: `qwen3.6-35b-a3b-splash`, the only resident model, 262,144-token context, reasoning on (issue #173 is still open), `--max-tokens 16384`. Image: `dirtywork-worker-pytest:0.13`, network disabled. Verify gate: the default suite with two available fix rounds; `--max-turns 60 --timeout 1800`.

Brief shape: ten `edit_file` pairs, 9.4 KB. Five in `dirtywork/firewall/policy.py` (the `PolicyContext` docstring, the root condition, a new `_canonicalize_checked` helper and its two call sites) and five in `tests/test_firewall_policy.py` (an import, one extended loop and three new test functions). Generated from the dry run's edit pairs with base line numbers, and replayed byte-identical against a snapshot of `main` before launch. The dry run was reviewed first by a nine-agent workflow (three lenses, two refuters per finding); its two confirmed findings, test the type check with a string as well as `None` and fix a code comment that overgeneralized about all-slash roots, went into the pairs before the brief was generated.

## Result

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `items-1018112715-12a85bf6` | completed | 32 | 150.0 | 621,709 | 6,582 | 43.9 | 0 | accept |

Wall and usage come from the transcript's `run_end`. Completion tokens include the hidden reasoning the server counts inside `completion_tokens`; the rate divides completion tokens by whole-run wall time, verification included, and is not decode throughput.

Tool calls: `read_file` 17, `edit_file` 11, `insert_before` 2, `bash` 2, `grep` 1, `finish` 1; tool errors 1; truncations 0; trimmed turns 0; nudges 0; guardrail blocks 0 (the released 0.13.2 runner predates the issue #138 gate, so it reports no firewall counters). The one tool error was an `edit_file` whose `old_string` matched nothing (the worker had trimmed a pair's context); it re-read the region and applied the pair as written on the next turn. Two of the appends went in through `insert_before` rather than `edit_file`, which the brief did not forbid and which produced the same bytes. The named test file reported 90 passed, the full suite 2,386 passed and 8 skipped in-container, and the verify gate exited 0 on the first round.

`files_changed` = exactly the two files the brief named. Diff: `dirtywork/firewall/policy.py` +29/−8, `tests/test_firewall_policy.py` +59. Both byte-identical to the brief and to the dry-run tree on the first pass. **No orchestrator edits to the worker's files.**

Sampler ([CSV](2026-10-18-issue-184-sampler.csv)): 30 samples, free 20.0–24.7 GB, inactive 34.2–34.4 GB, one model resident. A scratch copy of `tools/soak_sampler.sh` read `/api/v0/models` on port 1235 instead of calling the `lms` CLI, which times out against Bionic 1.1.8 even with the server up.

## Host validation

- Full suite in the run's worktree: **2,385 passed, 9 skipped, 38 deselected in 105.69 s**. Base: 2,382 passed, 9 skipped, 38 deselected. The delta of 3 is the three new test functions.
- `git diff --check`: clean. `ast.parse(..., feature_version=(3, 9))`: silent over both files.

## Orchestrator edits, disclosed

Docs only, in a separate commit on the same branch: two sentences in the issue #137 spec (`2026-09-19-issue-137-firewall-policy-design.md`, §3 and §11) that still described the root rule as "not `/`" now name all-slash roots with `//`; and this ledger with its CSV.

## Preserved receipts

Slug `issue-184-two-hardening-items-1018112715-12a85bf6`; local receipts at `~/.dirtywork/runs/<slug>/`: `run.json` with the accept verdict, `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, stdout, launch and end timestamps, the loaded-model line at launch and the sampler CSV. Local provenance, not published artifacts.
