# v1 soak matrix — #48

Gate for 1.0: every 0.9/0.10 feature is exercised by a real local model in a real
run, with the firing recorded in the transcript and summarized in a scoreboard
(`2026-08-23-v1-soak-worker-scoreboard.md`) + ledger (`2026-08-23-v1-soak-sdd-ledger.md`).
Task success is *not* the bar for the provoker runs — the bar is "the feature fired
and did what the spec says". A provoker that ends `stuck` on schedule is a pass.

## Environment (recorded 2026-08-23 17:40 CDT)

- dirtywork 0.10.1 (`82a353e`), worker image `:0.10` local, digest matches `PINNED_DIGEST`.
- Host: 128 GB RAM, macOS 25.6. LM Studio: `qwen/qwen3-coder-next` resident (65 536 ctx, PARALLEL 1 —
  not the documented 4); `mistralai/devstral-small-2-2512` NOT loaded. Ollama: `qwen3.6`, `gemma4`, `llama3.3`.
- `dirtywork bench` passes through only `--models/--provider/--base-url/--repeats/--tasks/--out/--max-turns/--timeout`
  (`dirtywork/__main__.py:1049-1069`). Anything needing `--verify`, `--max-tokens`, `--stuck-repeats`,
  `--stall-turns` runs via `dirtywork run` from a driver script instead.
- `bash` tool timeout is model-chosen per call: default 120 s, max 600 s (`dirtywork/builtin_tools.py:240`).

## Feature → signal → provoker

| # | Feature (release) | Transcript signal that it fired | How we provoke it | Leg |
|---|---|---|---|---|
| F1 | `apply_edits` (0.9) | `tool_result.tool == "apply_edits"`, ≥1 success, ≥3 hunks in one call | `py-rename-symbol`: rename `calc_total` → `compute_total` across 8 call sites + docstring in one 120-line file; task text says "prefer a single multi-hunk edit" | B |
| F2 | `stuck` / `RepeatTracker` (0.9) | `run_end.status == "stuck"`, `stuck_on.repeats == 4` (default `--stuck-repeats`) | `py-impossible-test`: "make `pytest` pass"; the failing test imports a package that is absent and `network none` makes it uninstallable. Expect the model to rerun `pytest`/`pip install` until the tracker stops it. Pass = stopped ≤ turn 12 with `stuck_on` populated | B |
| F3 | `timeout` nudge (0.9) | `tool_result.timed_out == true` + `nudge.kind == "timeout"`; run continues and finishes | `sh-hanging-script`: `build.sh` blocks on `read` from stdin before doing its work; task = "make `build.sh` finish non-interactively". The first `bash build.sh` hits the 120 s default timeout | B |
| F4 | `--verify` (0.8/0.9 wiring) | `run_end.verify` non-null, `passed == true`, `rounds ≥ 1`; and one run where round 1 fails → `verify_failed` or a round-2 pass | Existing 3 bench tasks run with `--verify "<acceptance cmd, repo-relative>" --verify-rounds 2` via driver — the worker-side command is `python3 acceptance/check_sum_range.py` / `node acceptance/greet.test.js` / `bash acceptance/check.sh`, NOT bench.json's `/acceptance/...` form (that mount exists only in the scoring container). A second pair of rows (`F4b-round2-*`) uses a verify command that fails its first round by design, to exercise the retry path | A′ |
| F5 | `append_file` + truncation recovery (0.10) | `assistant.finish_reason == "length"` → `nudge.kind == "truncated"` or a `truncated_call_result` → later `append_file` success; final file complete | `py-big-fixture`: "write `fixtures/rows.csv` with exactly 400 rows of the schema in `README`" with `--max-tokens 1024`. The correct file is ~22 KB (≈5–10k tokens), so even the default 8192 cap can truncate a single write; the extra rows at 2048 and 4096 measure whether recovery completes once the model's chunk size fits under the cap (the nudge does not tell it the cap) | B |
| F6 | atomic writes (0.10) | passive: no `*.tmp` / staging files left in any worktree after any run (same regex as `TMP_FIND_REGEX` in the docker sweep); no partially-written file after F5's truncated call | all |
| F7 | `--provider ollama` (0.10) | `run.json` start record: `provider == "ollama"`, `context_window_source` from `/api/ps`; runs complete | Existing 3 bench tasks, `--models qwen3.6@ollama` after `ollama run qwen3.6` | C |
| F8 | stall nudge / `stalled` (0.9) | `nudge.kind == "stall"`; possibly `status == "stalled"` | passive — F2 will likely trip it first if the model stops calling tools; no dedicated provoker | B |
| F9 | pinned image enforced (0.10.1) | `sandbox.image_pinned == true`, `image_digest == sha256:4fc400ca…` | passive — every docker run | all |
| F10 | `resume --feedback` (0.8) | `feedback` non-null on the resumed run | one resume of a completed invoicr run with review feedback | D |

## Legs

| Leg | What | Runner | Models | Repeats | Runs |
|---|---|---|---|---|---|
| A | Baseline: 3 existing bench tasks | `dirtywork bench` | qwen, devstral | 2 | 12 |
| A′ | Same 3 tasks with `--verify`/`--verify-rounds 2` | driver → `dirtywork run` | qwen, devstral | 1 | 6 |
| B | 4 provokers (F1, F2, F3, F5) | driver → `dirtywork run` | qwen, devstral | 2 (F5: +1 at default max-tokens) | 18 |
| C | Ollama leg: 3 existing tasks | `dirtywork bench --provider ollama` | qwen3.6 | 1 | 3 |
| D | Real tasks: invoicr #94 (billing data model + migration), #97 (RequirePro policy, on #94's branch) | `dirtywork run --verify "dotnet test …"` | qwen (devstral if #94 goes well) | 1 | 2–4 |

≈ 40–45 runs. Prior soaks averaged 2–8 min/run on qwen; budget an afternoon, not an hour.
Legs A→A′→B are the gate; C and D are the "in anger" halves the issue asks for. Serial, not
parallel — LM Studio is PARALLEL 1 right now and the sampler numbers are only comparable one run at a time.

## New bench tasks (leg B)

Committed under `bench/repos/` with a `bench.json` each so they're reusable, but their
`acceptance` is the *harness* check, not task correctness — noted in each `task` string.

- `py-rename-symbol` — `ledger.py` (120 lines, 8 uses of `calc_total`), `tests/test_ledger.py`.
  Acceptance: pytest passes with the new name. Harness pass: an `apply_edits` call with ≥3 hunks succeeded.
- `py-impossible-test` — `tests/test_api.py` imports `httpx` (not in the image); `requirements.txt` lists it.
  Acceptance: none can pass. Harness pass: `status == stuck`, `stuck_on.repeats == 4`, ended ≤ turn 12.
- `sh-hanging-script` — `build.sh` does `read -r name` then `echo "built for $name" > out.txt`.
  Acceptance: `out.txt` exists and `build.sh` exits in < 5 s with stdin closed. Harness pass: one `timed_out: true` result + one `timeout` nudge, then completion.
- `py-big-fixture` — `README.md` gives a 6-column schema; task is a 400-row CSV.
  Acceptance: `wc -l == 401`, header matches. Harness pass (at `--max-tokens 1024`): `finish_reason == length` at least once, recovery via `append_file`, no truncated file left behind.

## Harvest → scoreboard columns

Per-run rows follow `2026-08-18-ops-worker-scoreboard.md` (three tables: main, per-run
metrics, model-vs-tool time). Add one column to the main table: **Feature fired** —
the F-ids whose signal above is present, extracted from `run.json` by a small harvest
script (`tools/soak_harvest.py`, proposed; reads `run_end.status/stuck_on/verify`,
`nudge.kind`, `tool_result.timed_out`, `assistant.finish_reason`, tool mix).

Sampler: ad hoc as in prior soaks (no shipped tool) — a 5 s loop logging `vm_stat` free/inactive,
`lms ps` load state, and LM Studio's per-request tok/s from the response `usage`/timing
into `~/.dirtywork/bench/<stamp>-sampler.csv`. Started before leg A, stopped after leg D.

## Decisions (Jim, 2026-08-23 17:50 CDT)

1. **Devstral**: load it; both models resident, LM Studio PARALLEL back to 4. Soak runs qwen + devstral rows.
2. **invoicr #94 + #97** are the leg-D tasks (on-hold label acknowledged; work stays on a worktree branch, nothing merges).
3. **Ollama model**: `qwen3.6`.
4. **Provoker tasks are committed** under `bench/repos/` as the regression suite for these features.

## Order of execution

1. Pre-flight: load models, `ollama run qwen3.6`, start sampler, record `lms ps`, `docker image inspect`.
2. Leg A (bench baseline) → confirm scoring + harvest script work on the easy case.
3. Leg B provokers, qwen first; fix any provoker that doesn't actually provoke before repeating on devstral.
4. Leg A′, then C.
5. Leg D (invoicr), reviewed with the usual per-task review before anything is proposed as a PR.
6. Scoreboard + ledger written as we go; a 1.0 fix issue per harness defect found.
