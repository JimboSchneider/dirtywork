# Issue #138 W2: the Runner and CLI wiring — run ledger

Plan: [issue #138 plan](../plans/2026-09-20-issue-138-firewall-gate.md), Task 2 (brief W2 of 3). Spec: [issue #138 spec](../specs/2026-09-20-issue-138-firewall-gate-design.md) §2–§7. Base: `0113124` (the W1 run branch, stacked; `main` was `a239f37`). Branch: `dirtywork/issue-138-w2-of-3-0920164152-5f0316aa`.

## Worker setup

Released `dirtywork==0.13.2` through `pipx run --spec`, with `--branch-from @issue-138-w1-of-3-0920163537-1ee8bb9b`. Host: LM Studio Bionic 1.1.5 on `localhost:1234/v1`. Worker: `qwen3.6-35b-a3b-splash`, the only resident model, 262,144-token context, `--max-tokens 16384`. Image: `dirtywork-worker-pytest:0.13`, network disabled. Verify gate: the default suite with two available fix rounds. `--max-turns 90`, raised from the usual 60 because this brief carries 51 edit pairs, the most of any brief in the series.

Brief shape: 51 `edit_file` pairs across five files, 25 KB, every anchor carrying its base line number. Twenty-two of them are the same one-line change, an existing `Runner(...)` gaining `policy_context=GATE_CTX`; three of those call sites share identical text, so those pairs carry extra surrounding lines. Generated from a dry run of the design and replayed against a snapshot of this branch's starting tree before launch.

## Result: two runs, one resume

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `3-0920164152-5f0316aa` (first) | completed | 44 | 423.7 | 2,154,879 | 29,292 | 69.1 | 0 | resumed |
| `3-0920165008-d074091d` (resume) | completed | 5 | 114.0 | 67,321 | 771 | 6.8 | 0 | accept |

The first run left one of the 51 pairs unapplied and its verify gate still passed, because no test in this task covers the path it missed. The byte comparison against the brief is what caught it: `dirtywork/runner.py` came back 140 bytes larger than the brief specifies, with the allowed-call branch holding both the new canonical handoff preamble and the old registry-spec finish handling. The other four files were already byte-identical.

The cause is visible in the transcript: the worker batched its edits through `apply_edits` eleven times, and one batch reported `edit 5 of 5: old text occurs 0 times in dirtywork/runner.py`, because an earlier edit in the same batch had already rewritten the text that later edit anchored on. Two more tool errors came from the same habit, one on `__main__.py` (`old text occurs 2 times`) and one on `tests/test_runner.py`.

The resume, per the repository's dogfood rule, carried feedback naming the single missed edit and its exact old and new text, and nothing else. It applied that one pair in five turns and the suite passed. The resume itself hit one tool error worth recording, `path '/work/dirtywork/runner.py' resolves outside the worktree (absolute ...)`: the worker reached for an absolute path and the existing guardrails refused it, which is the behavior the Firewall is being wired in to make authoritative.

After the resume, all five files are byte-identical to the brief. **No orchestrator edits.**

Tool calls, first run: `read_file` 81, `apply_edits` 11, `bash` 4, `edit_file` 4, `grep` 2, `finish` 1; tool errors 3; truncations 0; trimmed turns 0; timeouts 0. Resume: `bash` 2, `grep` 1, `edit_file` 1, `finish` 1; tool errors 1.

`files_changed` = exactly the five files the brief named. Diff: `runner.py` +71/−15, `__main__.py` +22/−2, `tests/test_runner.py` +57/−23, `tests/test_transcript_schema.py` +10/−4, `docs/transcript-schema.md` +28. No test was added, as the brief required.

Sampler: first run ([CSV](2026-09-20-issue-138-w2-sampler.csv)) 83 samples, free 19.6–28.0 GB, inactive 34.7–46.7 GB; resume ([CSV](2026-09-20-issue-138-w2fix-sampler.csv)) 23 samples, free 20.5–20.9 GB. One model resident throughout.

## Host validation

- Full suite in the run's worktree: **2,343 passed, 9 skipped, 38 deselected in 108.60 s** — unchanged from W1, as the brief required, since this task adds no test.
- `git diff --check`: clean. `ast.parse(..., feature_version=(3, 9))`: silent over `runner.py`, `__main__.py`, `firewall_gate.py` and both touched test files.
- `dirtywork/firewall/` untouched; the package's import isolation is unaffected.

## What this says about brief size

Fifty-one pairs in one brief is past this worker's comfortable limit. The first run spent 2.15 million prompt tokens and 44 turns, re-reading the file 81 times, and still dropped one pair silently. A brief of this shape should either be split, or state that edits are to be applied one at a time rather than batched through `apply_edits`. The byte comparison remains the thing that makes a silent miss visible, since the verify gate cannot see an edit that no test covers.

## Preserved receipts

Slugs `issue-138-w2-of-3-0920164152-5f0316aa` and `issue-138-w2-of-3-0920165008-d074091d` (the resume, `resumed_from` the first); local receipts at `~/.dirtywork/runs/<slug>/`: `run.json`, `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, the feedback text, stdout, timestamps and the sampler CSVs. Local provenance, not published artifacts.
