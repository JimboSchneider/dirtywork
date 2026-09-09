# Issue #161: OPENAI_API_KEY as a Bearer token in the OpenAI provider — run ledger

Issue: [#161](https://github.com/JimboSchneider/dirtywork/issues/161). Base: `06df625` (main, right after PR #162). Branch: `dirtywork/issue-161-make-the-openai-compatible-0909164947-4aefc39b`.

## Worker setup

PyPI release checked on 2026-09-08: `dirtywork==0.13.1`, invoked through `pipx run --spec`. Image `dirtywork-worker-pytest:0.13`, network disabled.

Worker: `Qwen3.6-35B-A3B-MLX-4bit` on oMLX 0.6.4 (`--provider anthropic --base-url http://localhost:8000`, oMLX key in `ANTHROPIC_API_KEY`, `--context-window 65536`), the same host, model and settings as the [W1 oMLX run](2026-09-09-issue-135-w1-ledger.md). This is the second run in that series, and the last one that needs the Anthropic path: once this merges, the OpenAI provider can reach oMLX with its key check on.

Brief shape: five exact `edit_file` pairs with line numbers (four on `dirtywork/providers/openai_compat.py`, one on `dirtywork/contract/machine-contract.md`) and one `append_file` block of five tests, generated from the dry-run edit script (`orchestrator/edits161.py` in the run dir) so the brief and the reference are the same pairs. Line numbers assume the edits are applied in order.

## Result

| Run suffix | Status | Turns | Wall seconds | Prompt tokens | Completion tokens | Completion tokens / wall second | Nudges | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `0909164947-4aefc39b` | completed | 12 | 144.2 | 206,619 | 2,291 | 15.89 | 0 | accept |

Wall and usage come from the transcript (`run_start` 21:49:47.9Z to `run_end` 21:52:12.1Z). The rate divides completion tokens by whole-run wall time; it is not model decode throughput.

Server-side, from the oMLX log: 12 `/v1/messages` requests, 2,291 completion tokens in 32.8 s of decode, **69.8 tok/s** average (range 23.8–100.7). Prompt tokens are three times the W1 run's for a brief of similar size: the worker read each target file before editing it (six `read_file` calls, the first three of them the 200-line provider module in full) and the growing transcript was re-sent every turn.

Tool calls: `read_file` 6, `edit_file` 5, `append_file` 1, `bash` 2, `finish` 1; no tool errors; truncations 0; trimmed turns 0. All five edits landed on the first attempt each.

`files_changed` = exactly the brief's three paths. Verify gate (in-container default suite) exit 0: 1,720 passed, 8 skipped.

Sampler (`tools/soak_sampler.sh`, [CSV](2026-09-09-issue-161-sampler.csv)): 29 samples, free memory 47.89–50.80 GiB; LM Studio idle, oMLX holding the 19.3 GB model.

## Host validation

- `dirtywork/providers/openai_compat.py` and `dirtywork/contract/machine-contract.md` byte-equal to the dry-run files. `tests/test_provider_openai.py` differed only by the known `append_file` quirk (one missing blank line at the join and no trailing newline); normalized to the dry-run file on the branch by the reviewer, whitespace only.
- Full suite, `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider`: **1,719 passed / 9 skipped / 38 deselected**, 105.2 s. Baseline `06df625`: 1,714 passed (5 new).
- Red check (scratch clone, new tests against the unmodified provider): 3 failed, 51 passed. The two "no header when unset/empty" tests pass on the baseline by design; they pin the unchanged behavior.
- `git diff --check` clean; `ast.parse(..., feature_version=(3, 9))` silent.
- Live, with the branch's code on the host: `list_models()` against oMLX (key check on) returns the three models with `OPENAI_API_KEY` set and raises `LLMError: HTTP 401` without it; against LM Studio without a key, unchanged.
- Docs after the run, by the reviewer: `README.md`, `docs/operating.md` and `dirtywork/contract/SKILL.md` each gained the `OPENAI_API_KEY` note. `tests/test_contract.py` green.

## Preserved receipts

Slug `issue-161-make-the-openai-compatible-0909164947-4aefc39b`; local receipts at `~/.dirtywork/runs/<slug>/`: `run.json` (with the accept verdict), `transcript.jsonl`, `diff.patch`, and `orchestrator/` (brief, edit script and pairs, launch command and timestamps, run stdout JSON and stderr).
