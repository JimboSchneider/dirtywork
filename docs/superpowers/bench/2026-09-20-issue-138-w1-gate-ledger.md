# Issue #138 W1: the Runner-side Firewall gate module — run ledger

Plan: [issue #138 plan](../plans/2026-09-20-issue-138-firewall-gate.md), Task 1 (brief W1 of 3). Spec: [issue #138 spec](../specs/2026-09-20-issue-138-firewall-gate-design.md) §2–§7. Base: `a239f37` (`main`, the docs merge). Branch: `dirtywork/issue-138-w1-of-3-0920163537-1ee8bb9b`.

## Worker setup

Released `dirtywork==0.13.2` through `pipx run --spec`. Host: LM Studio Bionic 1.1.5 on `localhost:1234/v1` (default `openai` provider). Worker: `qwen3.6-35b-a3b-splash` (Splash engine, MoE, about 3B active, 4-bit, 20.95 GB, disk-mapped), the only resident model, 262,144-token context, `--max-tokens 16384`. Image: `dirtywork-worker-pytest:0.13`, network disabled. Verify gate: the default suite with two available fix rounds; `--max-turns 60 --timeout 1800`.

Brief shape: two `write_file` blocks, 27 KB, no edit pairs and no line anchors, since both files are new. The brief was generated from a dry run of the design in a throwaway clone of the same base, then replayed against a snapshot of this branch's starting tree and byte-compared before launch.

## Result

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `3-0920163537-1ee8bb9b` | completed | 7 | 155.5 | 99,922 | 8,046 | 51.7 | 0 | accept |

Wall and usage come from the transcript's `run_end`. Completion tokens include the hidden reasoning the server counts inside `completion_tokens`. The rate divides completion tokens by whole-run wall time, verification included; it is not decode throughput.

Tool calls: `list_dir` 3, `bash` 4, `write_file` 2, `finish` 1; tool errors 0; truncations 0; trimmed turns 0; timeouts 0. The worker listed the package and the tests directory, wrote both files, ran the new test file (68 passed) and then the full suite, and finished. In-container the suite reported 2,344 passed and 8 skipped, one more passed and one fewer skipped than the host, which is the usual container-only test. Verify gate exit 0 on the first round.

`files_changed` = exactly the two files the brief named. Diff: `dirtywork/firewall_gate.py` 190 lines and `tests/test_firewall_gate.py` 482 lines, both new. The worker's `write_file` dropped the trailing newline on both; appending it is the one orchestrator edit, disclosed here, after which both files are byte-identical to the brief.

Sampler (`tools/soak_sampler.sh`, [CSV](2026-09-20-issue-138-w1-sampler.csv)): 31 samples, free 22.4–30.4 GB, inactive 35.3–45.8 GB, one model resident.

## Host validation

- Full suite in the run's worktree, `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider`: **2,343 passed, 9 skipped, 38 deselected in 104.66 s**. Base (`main`): 2,275. The 68 new tests are the whole delta.
- `git diff --check`: clean. `ast.parse(..., feature_version=(3, 9))` over both new files: silent.
- Import contract: the module's only `dirtywork` imports are `dirtywork.firewall`, `dirtywork.firewall.bounds` and `dirtywork.providers`. Nothing in `dirtywork/` imports the gate yet; the wiring is W2.
- No runtime behavior change: the module is not yet called from anywhere.

## Preserved receipts

Slug `issue-138-w1-of-3-0920163537-1ee8bb9b`; local receipts at `~/.dirtywork/runs/<slug>/`: `run.json` with the accept verdict, `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, stdout, launch and end timestamps, the `lms ps` line at launch and the sampler CSV. Local provenance, not published artifacts.
