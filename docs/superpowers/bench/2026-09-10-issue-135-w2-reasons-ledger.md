# Issue #135 W2: reason vocabularies — run ledger

Plan: [issue #135 plan](../plans/2026-09-08-issue-135-firewall-schema.md), Task 2 (brief W2 of 5). Spec: [issue #135 spec](../specs/2026-09-08-issue-135-firewall-schema-design.md) §7. Base: `32036c2` (main, the 0.13.2 release). Branch: `dirtywork/issue-135-task-w2-of-0910004407-d0134765`.

## Worker setup

PyPI release checked on 2026-09-10: `dirtywork==0.13.2`, invoked through `pipx run --spec`. Host: oMLX 0.6.4 (`--provider openai --base-url http://localhost:8000/v1`, `OPENAI_API_KEY` set to the oMLX key, the 0.13.2 Bearer path from issue #161). Worker models: `Qwen3.6-35B-A3B-MLX-4bit` for attempts 1–3, `Qwen3-Coder-Next-MLX-4bit` for attempt 4; dirtywork assumed a 32,768-token context for both (no known window). Image: `dirtywork-worker-pytest:0.13`, network disabled. Verify gate: the default suite with two fix rounds; `--max-turns 60 --timeout 1800`.

Brief shape: two new files carried verbatim between BEGIN/END markers (one `write_file` each), re-extracted from the plan's fenced `### Worker brief W2` block; 8,179 characters. Attempts 2–4 were launched by a driver that waits until no other `dirtywork run` is alive, oMLX has logged no completion for 90 s, and at least 20 GB is free.

## Result

Four attempts, one accepted.

| Attempt | Run suffix | Model | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | `0910001437-a7d2bf70` | Qwen3.6-35B-A3B-MLX-4bit | completed | 2 | 333.5 | 9,646 | 10 | 0.03 | 1 | reject |
| 2 | `0910002316-1df751a8` | Qwen3.6-35B-A3B-MLX-4bit | completed | 3 | 71.5 | 14,573 | 56 | 0.78 | 2 | reject |
| 3 | `0910004006-fb5e7b4b` | Qwen3.6-35B-A3B-MLX-4bit | completed | 2 | 58.2 | 9,658 | 34 | 0.58 | 1 | reject |
| 4 | `0910004407-d0134765` | Qwen3-Coder-Next-MLX-4bit | completed | 9 | 181.4 | 60,853 | 3,078 | 16.97 | 0 | accept |

Wall and usage come from each transcript's `run_end`. The rate divides completion tokens by whole-run wall time; it is not model decode throughput. oMLX's own log puts attempt 4's decode at 43.1 tok/s mean over its 9 requests (10.0–51.2).

**Attempts 1–3 (rejected).** The worker never called a tool. Each run was a plain answer of gibberish (`ImageButton`; `realm substraat sub3ast (subtest)`; `String 4 class </think> 2. The class "My.TestCase" between my sponsor:`), one `unchanged_finish` nudge, another gibberish answer, and then dirtywork ran verify on the untouched tree, which passed, and reported `completed`, exit 0, `changed: false`. That exit-0-on-nothing path is documented 1.0 behaviour for a fresh run and is now [issue #165](https://github.com/JimboSchneider/dirtywork/issues/165).

Why the model broke, in order of evidence:

- Attempt 1 ran while another session had two dirtywork jobs on oMLX's `Qwen3-Coder-Next` (13k–24k-token prompts). Loading Qwen3.6 beside it put oMLX at 63.8 GB while LM Studio still held its own idle copy of Qwen3-Coder-Next (~45 GB). Sampler: free memory 0.06–1.6 GB for the whole run on a 128 GB machine; oMLX took 233.8 s to emit 2 tokens for a 4,789-token prompt. LM Studio auto-unloaded at 00:18 and free memory jumped to 45 GB.
- Attempt 2 had 30 GB free but the other session's runs were still saturating oMLX: Qwen3.6 decoded at 4.5 tok/s and still emitted junk.
- Attempt 3 ran on an idle host (24 GB free, no other runs, 14–20 tok/s) and still emitted junk, with a stray `</think>` although thinking is off for this model in `model_settings.json`. oMLX settings are unchanged since the clean W1 and issue #161 runs on the same model the day before.
- Direct probes of Qwen3.6 with 29-, 2,209- and 4,383-token prompts that share no prefix with dirtywork's returned correct text throughout. The failure is specific to dirtywork's exact prompt prefix, which oMLX first prefilled and cached during the memory-starved attempt 1 (`storing live non-sliceable` cache entries in its log). Poisoned prefix cache is the working hypothesis; not proven, and a model reload would test it.

**Attempt 4 (accepted).** Switched to `Qwen3-Coder-Next-MLX-4bit`, already resident in oMLX with its own cache. Tool calls: `write_file` 3, `list_dir` 3, `bash` 2, `finish` 0 (plain-answer finish); `read_file` 0; truncations 0; trimmed turns 0. One tool error, the worker's own: turn 1 wrote to the absolute path `/work/dirtywork/firewall/reasons.py`, which the tool refused (outside the worktree); it then listed `.`, `dirtywork` and `dirtywork/firewall` and wrote both files with relative paths at turns 5 and 6. In-container: the new test file 4 passed, then the default suite (turn 8). Verify gate exit 0 on the first round.

`files_changed` = exactly the two files the brief named. Both landed one byte short of the brief's blocks: the trailing newline after the last line was missing (the known `write_file` quirk). The orchestrator appended the newline to each on the branch; after that both files are byte-identical to the blocks re-extracted from the plan (`cmp`). That one-byte-per-file fix is the only orchestrator edit to worker output.

Sampler (`tools/soak_sampler.sh`, [CSV](2026-09-10-issue-135-w2-sampler.csv)): 36 samples in the attempt 4 window, free memory 18.1–21.6 GB, LM Studio 0 models loaded, oMLX holding both models. Attempts 1–3 CSVs are in their run dirs' `orchestrator/`.

## Host validation

- Full suite, `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider`: **1,723 passed / 9 skipped / 38 deselected**, 117.8 s. Baseline `32036c2`: 1,719 passed (4 new, as the plan expected).
- `git diff --check`: clean. `ast.parse(..., feature_version=(3, 9))` over `dirtywork/firewall/*.py` and `tests/test_firewall_*.py`: silent.
- No runtime behavior change: nothing outside `dirtywork/firewall/` imports the package.

## Preserved receipts

Slugs above under `~/.dirtywork/runs/<slug>/`: `run.json` (with the verdict), `transcript.jsonl`, `diff.patch`, and `orchestrator/` with the exact brief, stdout JSON, stderr and sampler CSV. Local provenance, not published artifacts.
