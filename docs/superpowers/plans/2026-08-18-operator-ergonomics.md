# Operator Ergonomics (0.7) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Every code block below is the literal code to write — transcribe it, do not paraphrase it. Where a step shows a **before** block, that text exists verbatim on this branch; match it exactly and replace it with the **after** block.

**Goal:** Two operator-facing conveniences on top of the shipped `runs`/`bench` CLIs (dirtywork **0.6.1**, branch `operator-ergonomics` off `main` = `5e724d2`): `dirtywork bench summarize A.jsonl --compare B.jsonl` (issue #26) so two sweeps can be read side by side instead of diffed by eye, and `dirtywork runs show <slug> --markdown [--out FILE]` (issue #27) so a finished run can be pasted into a PR or an issue without hand-transcribing the timeline.

**Architecture:** Both features are additive renderers over data that already exists on disk; neither adds a module, a dependency, or a new parser. Task 1 extends `dirtywork/bench.py`'s existing aggregator `_summarize_model` with the means and harness counters the paired table needs, adds a `_load_results` / `_aggregate` pair so the same aggregation can be keyed by `(model, task)` as easily as by `model`, and renders the pairing through `runs.format_table` — the one table renderer both CLIs already share. Task 2 lifts the transcript read/parse loop out of `runs.cmd_show` into `runs.read_transcript_events`, then builds a Markdown renderer on top of that single parser; `_timeline_line` (the text formatter) is untouched, and the text timeline keeps rendering exactly as it does today.

**Tech Stack:** Python ≥3.9, stdlib only (`json`, `html`, `re`, `statistics`, `argparse`, `pathlib`). Dev-only dependency: pytest.

**Spec:** design approved in chat 2026-08-18; recorded in `.superpowers/sdd/2026-08-18-operator-ergonomics/design.md` and restated in the Design section below.

---

## Design

Restated from `.superpowers/sdd/2026-08-18-operator-ergonomics/design.md` (approved 2026-08-18 12:22, binding).

### Task A — `dirtywork bench summarize A.jsonl --compare B.jsonl` (issue #26)

- New flag on the existing `summarize` sub-subparser in `dirtywork/__main__.py` `_add_bench_parsers`: `--compare FILE`.
- With it, `bench.cmd_summarize` loads both results files, aggregates each with the EXISTING per-(model, task) aggregation, and prints one table keyed by `model | task` with paired columns `A → B (Δ)` for: turns, wall_s, prompt tokens, completion tokens, acceptance, verdict, and a compact harness-failure column (nudges/stalled/max_turns counts). Rows present in only one file show a dash on the other side. Then the existing per-model stats for each file, side by side. Deltas are B minus A, `+N`/`-N`, stated in the header. Reuse `runs.format_table` and the existing `_summarize_model` aggregation; no second aggregator.
- Tests (`tests/test_bench.py`): two small on-disk jsonl fixtures; assert paired columns, deltas, the missing-row dash, and that a `bench_error` row aggregates without crashing.
- README: one line under "Benchmarking".

### Task B — `dirtywork runs show <slug> --markdown [--out FILE]` (issue #27)

- `--markdown` renders a Markdown document instead of the JSON dump + text timeline: `# <slug>` header block from run.json (task, model/provider, status, turns, prompt/completion tokens, base_commit, worktree/branch, resumed_from/by, verdict/note if any); `## Timeline` with one `### Turn N` per assistant turn — assistant text, each tool call as `<details><summary>tool(args…)</summary>` + fenced result (capped at the transcript's preview lengths), `nudge` / `guardrail_block` / `sandbox_reset` events as blockquote callouts; `## Result` (status, diff_stat, export_status, final message). `--out FILE` writes instead of printing. Reuse `_timeline_line`'s parsing by refactoring it into an event-iterating helper both renderers share — no second transcript parser. `--diff` still works and, with `--markdown`, embeds the diff in a fenced block.
- Tests (`tests/test_runs.py`): render a fixture transcript + run.json; assert headings, one section per turn, details blocks, callouts, `--out` writes the file, `--diff --markdown` embeds a fenced diff.
- README: one line under "Inspecting, cleaning up and re-exporting runs"; `docs/transcript-schema.md`: one sentence pointing at the export.

## Global Constraints

Inherited from the SP3 plan, per design.md's "Global constraints" line.

- Python 3.9 floor: no `match`, no `X | Y` unions at runtime. Both files touched already carry `from __future__ import annotations`, so `X | None` **in annotations** is fine; never in a runtime expression (`isinstance`, `cast` targets).
- Stdlib only. No new dependencies. The only new import in this whole plan is `html` (Task 2, `dirtywork/runs.py`).
- The stdout JSON contract (`status, worktree, branch, transcript, turns, usage, final_message`) is untouched by both tasks; CLI stdout may only gain output, never lose or rename existing lines. Both new behaviours sit behind a new flag, so the default output of `bench summarize` and `runs show` is byte-identical to today's.
- Every existing test stays green after every task. Run `python3 -m pytest -q` at the end of each task. The baseline on this branch is **815 passed** (18 deselected: `docker`/`live`); a task may only raise that number.
- Commit after each task with a conventional message (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`).
- New tests go in the existing module test file: `tests/test_bench.py` for Task 1, `tests/test_runs.py` for Task 2. No new test modules.
- Never leave a placeholder: every code step below is the actual code, every test step the actual test.
- Existing callers construct `argparse.Namespace` without the new attributes (e.g. `tests/test_runs.py` calls `runs.cmd_show(argparse.Namespace(slug="slug1", diff=False))`). Every new flag must therefore be read with `getattr(args, "<name>", <default>)`, never `args.<name>`.

## Precondition

Branch `operator-ergonomics` at `main` = `5e724d2`, dirtywork **0.6.1**, working tree clean. Every name below already exists exactly as written:

- `dirtywork/bench.py`: `NUDGE_KINDS = ("stall", "empty", "truncated", "text_tool_call")`, `DETAIL_COLUMNS`, `_verdict_for`, `_failure_cell`, `_detail_row`, `_summarize_model`, `cmd_summarize`, `dispatch`, `cmd_bench`; imports `json`, `statistics`, `sys`, `Path`, `from . import rundir`, `from .runs import _uid_gid, format_table`.
- `dirtywork/runs.py`: `COLUMN_GAP`, `LIST_COLUMNS`, `SHOW_FIELDS`, `TASK_PREVIEW_CHARS = 200`, `RunsError`, `format_table`, `_open_run`, `_summary_value`, `_timeline_line`, `cmd_show`, `dispatch`; imports `json`, `os`, `re`, `shutil`, `subprocess`, `sys`, `Path`.
- `dirtywork/__main__.py`: `_add_runs_parsers(sub)` (with the `show` sub-subparser carrying `slug` and `--diff`), `_add_bench_parsers(sub)` (with the `summarize` sub-subparser carrying `file`).
- `tests/test_bench.py`: `_result_row(**over)` helper (32 tests). `tests/test_runs.py`: `_write_run(runs_dir, slug, data)` helper (60 tests).
- `run.json` keys, as written by `__main__._write_run_json_start` / `_update_run_json` / `runs.cmd_verdict`: `schema_version, status, slug, repo, worktree, branch, base_commit, task, model, provider, context_window, resumed_from, container, volume, image, image_digest, image_pinned, host_pid, started, sandbox, allow_commit`, then `ended, turns, diff_stat, export_status, patch_path, finalize_error, watchdog_violation, watchdog_violation_kind`, then `verdict, note, verdict_at, review_seconds, time_to_verdict_s`. **`run.json` records no token usage and no final message** — both are read from the transcript (`run_end.usage`, and the `finish` call's `summary`).

## File Structure

```
dirtywork/
  bench.py            # MODIFIED — Task 1 (_load_results, _aggregate, compare helpers, cmd_summarize)
  runs.py             # MODIFIED — Task 2 (read_transcript_events, Markdown renderer, cmd_show)
  __main__.py         # MODIFIED — Task 1 (--compare), Task 2 (--markdown/--out)
tests/
  test_bench.py       # MODIFIED — Task 1
  test_runs.py        # MODIFIED — Task 2
README.md             # MODIFIED — Task 1 (Benchmarking), Task 2 (Inspecting … runs)
docs/transcript-schema.md   # MODIFIED — Task 2
```

---

### Task 1: `bench summarize --compare` — paired A → B scoreboard

**Files:**
- Modify: `dirtywork/bench.py` (extend `_summarize_model`; add `_load_results`, `_model_task_key`, `_model_key`, `_aggregate`, `_fmt_cell`, `_fmt_delta`, `_compare_cell`, `_harness_cell`, `_compare_rows`, `_compare_model_rows`, `_print_comparison`, `COMPARE_COLUMNS`, `COMPARE_MODEL_COLUMNS`, `MISSING`; replace `cmd_summarize`)
- Modify: `dirtywork/__main__.py` (`--compare` on the `bench summarize` sub-subparser)
- Modify: `tests/test_bench.py` (two new tests)
- Modify: `README.md` ("Benchmarking")

**Interfaces:**
- Consumes: `dirtywork.bench._summarize_model` (the one aggregator — extended here, never duplicated), `dirtywork.bench._verdict_for`, `dirtywork.bench.NUDGE_KINDS`, `dirtywork.runs.format_table` (already imported at the top of `bench.py`).
- Produces: `dirtywork.bench.cmd_summarize(args) -> int` now honouring `args.compare`; CLI: `dirtywork bench summarize <file> [--compare FILE]`.

Cell shape is `A -> B (Δ)`. A key present in only one file prints `-` on the missing side and drops the delta (there is nothing to subtract). Deltas are B − A: `+N` / `-N`, `0` for no change. Means print with one decimal (wall seconds, turns, tokens); rates print as whole percents; counts print as integers.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bench.py`:

```python
def test_summarize_compare_pairs_rows_and_shows_deltas(tmp_path, monkeypatch, capsys):
    # Two sweeps of the same two tasks, plus one task only B ran. Rows carry
    # slug=None so _verdict_for never touches the (monkeypatched) runs dir.
    monkeypatch.setattr(bench.rundir, "RUNS_DIR", tmp_path / "runs")
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    a_rows = [
        _result_row(model="m1", task="t1", turns=4, wall_s=2.0, slug=None),
        _result_row(model="m1", task="t1", repeat=1, turns=6, wall_s=4.0,
                    acceptance="fail", slug=None),
        # a bench_error row: every numeric field is None and harness is {}
        _result_row(model="m1", task="t2", status="bench_error", turns=None, wall_s=1.0,
                    prompt_tokens=None, completion_tokens=None, acceptance="skipped",
                    harness={}, slug=None),
    ]
    b_rows = [
        _result_row(model="m1", task="t1", turns=2, wall_s=1.0, slug=None),
        _result_row(model="m1", task="t1", repeat=1, turns=4, wall_s=3.0, slug=None),
        _result_row(model="m1", task="t2", status="bench_error", turns=None, wall_s=1.0,
                    prompt_tokens=None, completion_tokens=None, acceptance="skipped",
                    harness={}, slug=None),
        _result_row(model="m1", task="t3", turns=8, wall_s=5.0, slug=None),
    ]
    a.write_text("\n".join(json.dumps(r) for r in a_rows) + "\n")
    b.write_text("\n".join(json.dumps(r) for r in b_rows) + "\n")

    rc = bench.cmd_summarize(argparse.Namespace(file=str(a), compare=str(b)))
    assert rc == 0
    out = capsys.readouterr().out
    # header names both files and states the delta direction
    assert f"A = {a}" in out
    assert f"B = {b}" in out
    assert "Δ = B - A" in out
    # m1/t1: mean turns 5.0 -> 3.0, mean wall 3.0 -> 2.0, acceptance 50% -> 100%
    assert "5.0 -> 3.0 (-2.0)" in out
    assert "3.0 -> 2.0 (-1.0)" in out
    assert "50% -> 100% (+50%)" in out
    # the bench_error row aggregates instead of crashing: no numbers on either side
    assert "t2" in out
    assert "- -> -" in out
    # a key only B ran shows the dash on the A side
    assert "t3" in out
    assert "- -> 1" in out
    # the per-model block is paired the same way
    assert "per-model (A -> B):" in out
    assert "MODEL" in out and "GAMED" in out


def test_summarize_compare_missing_file_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bench.rundir, "RUNS_DIR", tmp_path / "runs")
    a = tmp_path / "a.jsonl"
    a.write_text(json.dumps(_result_row(slug=None)) + "\n")
    rc = bench.cmd_summarize(argparse.Namespace(file=str(a),
                                                compare=str(tmp_path / "nope.jsonl")))
    assert rc == 2
    assert "no such file" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_bench.py -q -k compare`
Expected: 2 failed — `TypeError: cmd_summarize() ... unexpected` is *not* what you will see; the real failure is `AssertionError` on `"A = ..." in out` (today's `cmd_summarize` ignores an unknown Namespace attribute and prints the ordinary summary).

- [ ] **Step 3: Extend the aggregator**

In `dirtywork/bench.py`, replace the whole of `_summarize_model` with the version below. It keeps every key it returns today (the existing per-model block reads `runs`, `completion_rate`, `acceptance_rate`, `gamed`, `mean_tokens`, `mean_wall_s`, `verdict_rate`, `median_review_seconds` and is unchanged) and adds the six the paired table needs, so there is still exactly one aggregator.

Before:

```python
def _summarize_model(rows: list, verdicts: list, review_seconds: list) -> dict:
    n = len(rows)
    completed = sum(1 for r in rows if r.get("status") == "completed")
    accepted = sum(1 for r in rows if r.get("acceptance") == "pass")
    gamed = sum(1 for r in rows if r.get("acceptance") == "gamed")
    tokens = [(r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0)
              for r in rows if r.get("prompt_tokens") is not None
              or r.get("completion_tokens") is not None]
    walls = [r["wall_s"] for r in rows if isinstance(r.get("wall_s"), (int, float))]
    return {
        "runs": n,
        "completion_rate": completed / n if n else 0.0,
        "acceptance_rate": accepted / n if n else 0.0,
        "gamed": gamed,
        "mean_tokens": (sum(tokens) / len(tokens)) if tokens else None,
        "mean_wall_s": (sum(walls) / len(walls)) if walls else None,
        "verdict_rate": (verdicts.count("accept") / len(verdicts)) if verdicts else None,
        "median_review_seconds": statistics.median(review_seconds) if review_seconds else None,
    }
```

After:

```python
def _mean(values: list):
    """None for an empty sample -- a bench_error row records no turns, no
    tokens and no verdict, and a mean of nothing is not 0."""
    return (sum(values) / len(values)) if values else None


def _numbers(rows: list, key: str) -> list:
    return [r[key] for r in rows if isinstance(r.get(key), (int, float))]


def _summarize_model(rows: list, verdicts: list, review_seconds: list) -> dict:
    """The one aggregation in this module. `cmd_summarize` calls it per model;
    `--compare` calls it per (model, task) -- same function, different key, so
    the two blocks can never disagree about what a rate means."""
    n = len(rows)
    completed = sum(1 for r in rows if r.get("status") == "completed")
    accepted = sum(1 for r in rows if r.get("acceptance") == "pass")
    gamed = sum(1 for r in rows if r.get("acceptance") == "gamed")
    tokens = [(r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0)
              for r in rows if r.get("prompt_tokens") is not None
              or r.get("completion_tokens") is not None]
    harness = [r.get("harness") or {} for r in rows]
    return {
        "runs": n,
        "completion_rate": completed / n if n else 0.0,
        "acceptance_rate": accepted / n if n else 0.0,
        "gamed": gamed,
        "mean_tokens": _mean(tokens),
        "mean_wall_s": _mean(_numbers(rows, "wall_s")),
        "verdict_rate": (verdicts.count("accept") / len(verdicts)) if verdicts else None,
        "median_review_seconds": statistics.median(review_seconds) if review_seconds else None,
        # added for `--compare`; the per-model block above ignores them
        "mean_turns": _mean(_numbers(rows, "turns")),
        "mean_prompt_tokens": _mean(_numbers(rows, "prompt_tokens")),
        "mean_completion_tokens": _mean(_numbers(rows, "completion_tokens")),
        "nudges": sum((h.get(f"nudge_{kind}") or 0) for h in harness for kind in NUDGE_KINDS),
        "stalled": sum(1 for h in harness if h.get("stalled")),
        "max_turns": sum(1 for h in harness if h.get("max_turns")),
    }
```

- [ ] **Step 4: Add the comparison helpers**

Still in `dirtywork/bench.py`, insert the following block immediately **after** `_summarize_model` and immediately **before** `def cmd_summarize(args) -> int:`.

```python
MISSING = "-"
COMPARE_COLUMNS = ("model", "task", "runs", "turns", "wall_s", "prompt", "completion",
                   "accept", "verdict", "harness")
COMPARE_MODEL_COLUMNS = ("model", "runs", "completion", "accept", "gamed", "tokens",
                         "wall_s", "verdict", "review_s")


def _load_results(path: Path) -> list:
    """Every JSON object in a results JSONL file. Blank and malformed lines are
    skipped: a sweep killed mid-write must still summarize."""
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _model_key(row: dict) -> str:
    return row.get("model", "?")


def _model_task_key(row: dict) -> tuple:
    return (row.get("model", "?"), row.get("task", "?"))


def _aggregate(rows: list, key) -> dict:
    """{key: _summarize_model(...)} for one results file. Verdicts are re-read
    per row through `_verdict_for` (the operator records them after the sweep),
    exactly as the per-model block does."""
    grouped, verdicts, reviews = {}, {}, {}
    for row in rows:
        k = key(row)
        grouped.setdefault(k, []).append(row)
        verdict, review = _verdict_for(row)
        if verdict:
            verdicts.setdefault(k, []).append(verdict)
        if isinstance(review, (int, float)):
            reviews.setdefault(k, []).append(review)
    return {k: _summarize_model(v, verdicts.get(k, []), reviews.get(k, []))
            for k, v in grouped.items()}


def _fmt_cell(value, kind: str) -> str:
    """One side of a paired cell. None means the file has no rows for this key
    (or no sample for this statistic), and prints as a dash."""
    if value is None:
        return MISSING
    if kind == "pct":
        return f"{value:.0%}"
    if kind == "int":
        return f"{int(value)}"
    return f"{value:.1f}"


def _fmt_delta(a, b, kind: str) -> str:
    """`+N` / `-N` (`0` for no change), or "" when either side is missing --
    there is nothing to subtract from a key one file never ran."""
    if a is None or b is None:
        return ""
    diff = b - a
    if kind == "pct":
        text = f"{abs(diff):.0%}"
    elif kind == "int":
        text = f"{int(abs(diff))}"
    else:
        text = f"{abs(diff):.1f}"
    if diff == 0:
        return "0"
    return ("+" if diff > 0 else "-") + text


def _compare_cell(a, b, kind: str = "float") -> str:
    """`A -> B (delta)` for one statistic."""
    delta = _fmt_delta(a, b, kind)
    text = f"{_fmt_cell(a, kind)} -> {_fmt_cell(b, kind)}"
    return f"{text} ({delta})" if delta else text


def _stat(summary, key: str):
    """One statistic out of a summary, or None when that file has no such key."""
    return summary.get(key) if summary else None


def _harness_cell(summary) -> str:
    """nudges/stalled/max_turns for one side, compact."""
    if summary is None:
        return MISSING
    return f"{summary['nudges']}/{summary['stalled']}/{summary['max_turns']}"


def _compare_rows(agg_a: dict, agg_b: dict) -> list:
    """One row per (model, task) key present in either file, sorted."""
    rows = []
    for model, task in sorted(set(agg_a) | set(agg_b)):
        a, b = agg_a.get((model, task)), agg_b.get((model, task))
        rows.append({
            "model": model,
            "task": task,
            "runs": _compare_cell(_stat(a, "runs"), _stat(b, "runs"), "int"),
            "turns": _compare_cell(_stat(a, "mean_turns"), _stat(b, "mean_turns")),
            "wall_s": _compare_cell(_stat(a, "mean_wall_s"), _stat(b, "mean_wall_s")),
            "prompt": _compare_cell(_stat(a, "mean_prompt_tokens"),
                                    _stat(b, "mean_prompt_tokens")),
            "completion": _compare_cell(_stat(a, "mean_completion_tokens"),
                                        _stat(b, "mean_completion_tokens")),
            "accept": _compare_cell(_stat(a, "acceptance_rate"),
                                    _stat(b, "acceptance_rate"), "pct"),
            "verdict": _compare_cell(_stat(a, "verdict_rate"),
                                     _stat(b, "verdict_rate"), "pct"),
            "harness": f"{_harness_cell(a)} -> {_harness_cell(b)}",
        })
    return rows


def _compare_model_rows(agg_a: dict, agg_b: dict) -> list:
    """The per-model stats `bench summarize` already prints, paired A -> B."""
    rows = []
    for model in sorted(set(agg_a) | set(agg_b)):
        a, b = agg_a.get(model), agg_b.get(model)
        rows.append({
            "model": model,
            "runs": _compare_cell(_stat(a, "runs"), _stat(b, "runs"), "int"),
            "completion": _compare_cell(_stat(a, "completion_rate"),
                                        _stat(b, "completion_rate"), "pct"),
            "accept": _compare_cell(_stat(a, "acceptance_rate"),
                                    _stat(b, "acceptance_rate"), "pct"),
            "gamed": _compare_cell(_stat(a, "gamed"), _stat(b, "gamed"), "int"),
            "tokens": _compare_cell(_stat(a, "mean_tokens"), _stat(b, "mean_tokens")),
            "wall_s": _compare_cell(_stat(a, "mean_wall_s"), _stat(b, "mean_wall_s")),
            "verdict": _compare_cell(_stat(a, "verdict_rate"),
                                     _stat(b, "verdict_rate"), "pct"),
            "review_s": _compare_cell(_stat(a, "median_review_seconds"),
                                      _stat(b, "median_review_seconds")),
        })
    return rows


def _print_comparison(path_a: Path, rows_a: list, path_b: Path, rows_b: list) -> int:
    print(f"A = {path_a}")
    print(f"B = {path_b}")
    print("cells: A -> B (Δ); Δ = B - A; "
          f"'{MISSING}' = no rows for that key in that file")
    print("harness: nudges/stalled/max_turns")
    print()
    print(format_table(COMPARE_COLUMNS,
                       _compare_rows(_aggregate(rows_a, _model_task_key),
                                     _aggregate(rows_b, _model_task_key))))
    print()
    print("per-model (A -> B):")
    print(format_table(COMPARE_MODEL_COLUMNS,
                       _compare_model_rows(_aggregate(rows_a, _model_key),
                                           _aggregate(rows_b, _model_key))))
    return 0
```

- [ ] **Step 5: Route `--compare` through `cmd_summarize`**

Replace the whole of `cmd_summarize` in `dirtywork/bench.py`.

Before:

```python
def cmd_summarize(args) -> int:
    path = Path(args.file)
    if not path.is_file():
        print(f"error: no such file '{path}'", file=sys.stderr)
        return 2
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)

    detail, by_model, verdicts, reviews = [], {}, {}, {}
```

After (only the head of the function changes; everything from `detail, by_model, verdicts, reviews = [], {}, {}, {}` onward stays exactly as it is today):

```python
def cmd_summarize(args) -> int:
    path = Path(args.file)
    if not path.is_file():
        print(f"error: no such file '{path}'", file=sys.stderr)
        return 2
    rows = _load_results(path)

    # `getattr`, not `args.compare`: existing callers build a Namespace with
    # `file` only (tests/test_bench.py) and must keep working.
    compare = getattr(args, "compare", None)
    if compare:
        other = Path(compare)
        if not other.is_file():
            print(f"error: no such file '{other}'", file=sys.stderr)
            return 2
        return _print_comparison(path, rows, other, _load_results(other))

    detail, by_model, verdicts, reviews = [], {}, {}, {}
```

- [ ] **Step 6: Run the module tests**

Run: `python3 -m pytest tests/test_bench.py -q`
Expected: `34 passed` (32 today + the 2 new ones).

- [ ] **Step 7: Add the `--compare` flag**

In `dirtywork/__main__.py`, inside `_add_bench_parsers`.

Before:

```python
    bench_sub = bench_p.add_subparsers(dest="bench_cmd")
    summarize_p = bench_sub.add_parser("summarize", help="summarize a bench results file")
    summarize_p.add_argument("file")
```

After:

```python
    bench_sub = bench_p.add_subparsers(dest="bench_cmd")
    summarize_p = bench_sub.add_parser("summarize", help="summarize a bench results file")
    summarize_p.add_argument("file")
    summarize_p.add_argument("--compare", default=None, metavar="FILE",
                             help="second results file: print one paired 'A -> B (delta)' "
                                  "table keyed by model and task instead of the usual summary")
```

- [ ] **Step 8: README line**

In `README.md`, under "## Benchmarking".

Before:

```
    dirtywork bench summarize <results.jsonl>
```

After:

```
    dirtywork bench summarize <results.jsonl> [--compare <other.jsonl>]
```

And, in the same section, extend the closing sentence of the prose paragraph.

Before:

```
`~/.dirtywork/bench/<UTC-timestamp>.jsonl` (or `--out`); `dirtywork bench
summarize <file>` prints a per-case table plus a per-model summary
(completion/acceptance/verdict rates, gamed count, mean tokens/wall time,
median review seconds).
```

After:

```
`~/.dirtywork/bench/<UTC-timestamp>.jsonl` (or `--out`); `dirtywork bench
summarize <file>` prints a per-case table plus a per-model summary
(completion/acceptance/verdict rates, gamed count, mean tokens/wall time,
median review seconds). `--compare <other.jsonl>` prints one paired
`A -> B (Δ)` table instead — keyed by model and task, deltas are B minus A,
and a key only one sweep ran shows `-` on the other side.
```

- [ ] **Step 9: Run the full suite**

Run: `python3 -m pytest -q`
Expected: `817 passed` (815 baseline + 2), 18 deselected.

- [ ] **Step 10: Commit**

```bash
git add dirtywork/bench.py dirtywork/__main__.py tests/test_bench.py README.md
git commit -m "feat: 'bench summarize --compare' pairs two sweeps A -> B with deltas"
```

---

### Task 2: `runs show --markdown [--out FILE]` — Markdown run report

**Files:**
- Modify: `dirtywork/runs.py` (add `import html`; add `read_transcript_events` and the Markdown renderer; replace `cmd_show`)
- Modify: `dirtywork/__main__.py` (`--markdown` / `--out` on the `runs show` sub-subparser)
- Modify: `tests/test_runs.py` (five new tests)
- Modify: `README.md` ("Inspecting, cleaning up and re-exporting runs"), `docs/transcript-schema.md`

**Interfaces:**
- Consumes: `dirtywork.runs._open_run` (slug validation + `run.json`), `dirtywork.runs._summary_value` (the same one-line field formatter the text summary uses, including its 200-char task preview), `dirtywork.runs._timeline_line` (unchanged — the text formatter), the transcript events documented in `docs/transcript-schema.md`.
- Produces: `dirtywork.runs.read_transcript_events(path) -> tuple` (the single transcript parser), `dirtywork.runs.render_markdown(slug, data, events, *, diff=None, error=None) -> str`, and `cmd_show` honouring `args.markdown` / `args.out`; CLI: `dirtywork runs show <slug> [--diff] [--markdown] [--out FILE]`.

Caps follow the transcript's own preview lengths (`docs/transcript-schema.md`): a `tool_result.result` is already capped at 2000 chars by `Caps.transcript = preview`, so the fenced block caps at `MD_RESULT_CHARS = 2000`; `tool_result.args` is already capped at 500 chars, and the `<summary>` line shows the first `MD_ARGS_CHARS = 200` of it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runs.py`:

```python
FENCE = "`" * 3


def _markdown_run(tmp_path, **over):
    """A run dir with a two-turn transcript: a nudge, a guardrail_block, a
    finish call, and a run_end carrying usage."""
    data = {
        "status": "completed", "slug": "md1", "task": "fix the off-by-one",
        "model": "qwen/qwen3-coder-next", "provider": "openai", "turns": 2,
        "sandbox": "docker", "base_commit": "abc1234", "branch": "dirtywork/md1",
        "worktree": "/repo/.worktrees/dw-md1", "resumed_from": None, "resumed_by": None,
        "diff_stat": " x.py | 2 +-\n 1 file changed", "export_status": "ok",
        "verdict": "accept", "note": "clean patch",
    }
    data.update(over)
    run_dir = _write_run(tmp_path / "runs", "md1", data)
    (run_dir / "transcript.jsonl").write_text(
        json.dumps({"ts": "t0", "event": "run_start", "model": "qwen/qwen3-coder-next"}) + "\n"
        + json.dumps({"ts": "t1", "event": "assistant", "text": "Looking at the file.",
                      "tool_calls": [{"name": "bash", "arguments": "{}"}]}) + "\n"
        + json.dumps({"ts": "t2", "event": "tool_result", "tool": "bash",
                      "args": "{\"command\": \"rm -rf /\"}",
                      "result": "BLOCKED: refusing <destructive> command"}) + "\n"
        + json.dumps({"ts": "t3", "event": "guardrail_block", "tool": "bash",
                      "args": {"command": "rm -rf /"},
                      "reason": "BLOCKED: refusing <destructive> command"}) + "\n"
        + json.dumps({"ts": "t4", "event": "nudge", "kind": "stall", "turn": 1}) + "\n"
        + json.dumps({"ts": "t5", "event": "assistant", "text": "Fixed it.",
                      "tool_calls": [{"name": "finish", "arguments": "{}"}]}) + "\n"
        + json.dumps({"ts": "t6", "event": "tool_result", "tool": "finish",
                      "args": "{\"summary\": \"off-by-one corrected\"}",
                      "result": "run finished"}) + "\n"
        + json.dumps({"ts": "t7", "event": "run_end", "status": "completed", "turns": 2,
                      "usage": {"prompt_tokens": 1234, "completion_tokens": 56}}) + "\n"
    )
    return run_dir


def test_cmd_show_markdown_renders_document(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _markdown_run(tmp_path)
    rc = runs.cmd_show(argparse.Namespace(slug="md1", diff=False, markdown=True, out=None))
    assert rc == 0
    out = capsys.readouterr().out
    # header block
    assert out.startswith("# md1\n")
    assert "- **task:** fix the off-by-one" in out
    assert "- **model:** qwen/qwen3-coder-next" in out
    assert "- **provider:** openai" in out
    assert "- **base_commit:** abc1234" in out
    assert "- **branch:** dirtywork/md1" in out
    assert "- **prompt_tokens:** 1234" in out
    assert "- **completion_tokens:** 56" in out
    assert "- **verdict:** accept" in out
    assert "- **note:** clean patch" in out
    # one section per assistant turn, in order
    assert "## Timeline" in out
    assert "### Turn 1" in out
    assert "### Turn 2" in out
    assert out.index("### Turn 1") < out.index("### Turn 2")
    assert "Looking at the file." in out
    # tool calls become collapsible blocks with the result in a fenced block,
    # where text is literal and therefore NOT html-escaped
    assert "<details>" in out and "</details>" in out
    assert "<summary>bash(" in out
    assert "BLOCKED: refusing <destructive> command" in out
    assert FENCE in out
    # harness events become blockquote callouts -- inline context, so escaped
    assert "> **nudge**" in out
    assert "> **guardrail_block**" in out
    assert "refusing &lt;destructive&gt; command" in out
    # result block
    assert "## Result" in out
    assert "- **status:** completed" in out
    assert "- **export_status:** ok" in out
    assert "1 file changed" in out
    assert "off-by-one corrected" in out          # final message from the finish call


def test_cmd_show_markdown_out_writes_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _markdown_run(tmp_path)
    target = tmp_path / "report.md"
    rc = runs.cmd_show(argparse.Namespace(slug="md1", diff=False, markdown=True,
                                          out=str(target)))
    assert rc == 0
    text = target.read_text()
    assert text.startswith("# md1\n")
    assert "### Turn 2" in text
    assert str(target) in capsys.readouterr().out   # the path is reported, not the document


def test_cmd_show_markdown_diff_embeds_fenced_patch(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _markdown_run(tmp_path)
    (run_dir / "diff.patch").write_text("--- a/x.py\n+++ b/x.py\n@@\n-1\n+2\n")
    rc = runs.cmd_show(argparse.Namespace(slug="md1", diff=True, markdown=True, out=None))
    assert rc == 0
    out = capsys.readouterr().out
    assert "## Diff" in out
    assert FENCE + "diff" in out
    assert "--- a/x.py" in out


def test_cmd_show_out_without_markdown_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _markdown_run(tmp_path)
    rc = runs.cmd_show(argparse.Namespace(slug="md1", diff=False, markdown=False,
                                          out=str(tmp_path / "x.md")))
    assert rc == 2
    assert "--out requires --markdown" in capsys.readouterr().err


def test_cmd_show_markdown_caps_long_results_and_survives_inner_fences(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _write_run(tmp_path / "runs", "md1", {"status": "completed", "slug": "md1"})
    huge = FENCE + "\n" + ("x" * 5000)
    (run_dir / "transcript.jsonl").write_text(
        json.dumps({"ts": "t1", "event": "assistant", "text": "", "tool_calls": []}) + "\n"
        + json.dumps({"ts": "t2", "event": "tool_result", "tool": "read_file",
                      "args": "{\"path\": \"big.txt\"}", "result": huge}) + "\n"
    )
    rc = runs.cmd_show(argparse.Namespace(slug="md1", diff=False, markdown=True, out=None))
    assert rc == 0
    out = capsys.readouterr().out
    assert "x" * 100 in out
    assert "x" * 4000 not in out                  # capped at MD_RESULT_CHARS
    assert "[truncated]" in out
    assert FENCE + "`" in out                     # fence widened past the inner fence
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_runs.py -q -k markdown`
Expected: 5 failed — `AssertionError` on `out.startswith("# md1\n")` (today's `cmd_show` ignores `markdown` and prints the text summary).

- [ ] **Step 3: Add the `html` import**

In `dirtywork/runs.py`, at the top of the stdlib import block.

Before:

```python
import json
import os
import re
```

After:

```python
import html
import json
import os
import re
```

- [ ] **Step 4: Extract the single transcript parser**

`_timeline_line` is the **text** formatter and does not change — do not edit it. What moves is the read/parse loop that `cmd_show` runs around it, so the Markdown renderer can iterate the same events.

Before (`dirtywork/runs.py`, unchanged and shown only to locate the insertion point — the new helper goes immediately **above** it):

```python
def _timeline_line(event: dict) -> str:
    ts = event.get("ts", "")
    name = str(event.get("event", ""))
    if name == "tool_result":
        result = str(event.get("result", ""))
        outcome = ("ERROR" if result.startswith("ERROR")
                   else "BLOCKED" if result.startswith("BLOCKED") else "ok")
        tool = event.get("tool") or "(malformed call)"
        return f"{ts}  {name:<15} {tool:<12} {str(event.get('args', ''))[:80]:<80} [{outcome}]"
```

After — insert this function immediately before `def _timeline_line(event: dict) -> str:`:

```python
def read_transcript_events(path) -> tuple:
    """(events, error) -- the one transcript parser in this module. Both
    renderers of `runs show` (the text timeline and the Markdown document) read
    the file through here, so what counts as an event is decided once. A missing
    transcript is not an error (a run that died in preflight never wrote one); an
    unreadable one yields no events plus the message to report."""
    path = Path(path)
    if not path.is_file():
        return [], None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [], str(e)
    events = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events, None
```

- [ ] **Step 5: Add the Markdown renderer**

Insert this block immediately **after** `_timeline_line` and immediately **before** `def cmd_show(args) -> int:`.

```python
MD_HEADER_FIELDS = ("status", "task", "model", "provider", "sandbox", "turns",
                    "base_commit", "branch", "worktree", "resumed_from", "resumed_by")
MD_VERDICT_FIELDS = ("verdict", "note")
MD_RESULT_FIELDS = ("status", "export_status", "finalize_error", "watchdog_violation")
MD_ARGS_CHARS = 200      # the transcript already caps `args` at 500
MD_RESULT_CHARS = 2000   # the transcript's own `preview` cap for a tool result


def _md_trim(value, limit: int) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + " ... [truncated]"


def _md_fence(text: str) -> str:
    """A fence longer than any backtick run inside `text` -- a tool result (or a
    diff of a Markdown file) may itself contain a fence and would otherwise close
    the block early."""
    longest = max([len(m) for m in re.findall(r"`+", text)] or [0])
    return "`" * max(3, longest + 1)


def _md_block(text: str, lang: str = "") -> list:
    fence = _md_fence(text)
    return [f"{fence}{lang}", text, fence, ""]


def _md_inline(value, limit: int) -> str:
    """Model/tool output that lands in an inline Markdown or HTML context (a
    <summary> line, a blockquote callout): trimmed, then HTML-escaped, because
    tool arguments and guardrail reasons routinely contain `<` and `&`. Text
    that lands inside a fenced block is NOT escaped -- a fence is already
    literal, and escaping there would print `&lt;` to the reader. `quote=False`:
    this is element text, never an attribute value, and JSON arguments are full
    of quotes that would otherwise render as `&quot;` noise."""
    return html.escape(_md_trim(value, limit), quote=False)


def _md_event_lines(event: dict) -> list:
    """One non-assistant timeline event as Markdown: tool results become
    collapsible <details> blocks, harness events become blockquote callouts."""
    name = str(event.get("event", ""))
    if name == "tool_result":
        tool = event.get("tool") or "(malformed call)"
        result = str(event.get("result", ""))
        outcome = ("ERROR" if result.startswith("ERROR")
                   else "BLOCKED" if result.startswith("BLOCKED") else "ok")
        summary = (f"{html.escape(str(tool))}"
                   f"({_md_inline(event.get('args', ''), MD_ARGS_CHARS)}) [{outcome}]")
        lines = ["<details>", f"<summary>{summary}</summary>", ""]
        lines += _md_block(_md_trim(result, MD_RESULT_CHARS))
        lines += ["</details>", ""]
        return lines
    if name == "nudge":
        return [f"> **nudge** `{event.get('kind', '')}` (turn {event.get('turn', '')})", ""]
    if name == "guardrail_block":
        return [f"> **guardrail_block** `{event.get('tool', '')}`: "
                f"{_md_inline(event.get('reason', ''), MD_ARGS_CHARS)}", ""]
    if name == "sandbox_reset":
        return [f"> **sandbox_reset**: {_md_inline(event.get('reason', ''), MD_ARGS_CHARS)}", ""]
    return []


def _md_timeline(events: list) -> list:
    """`## Timeline`, one `### Turn N` per assistant event; every other event is
    rendered under the turn it followed."""
    lines = ["## Timeline", ""]
    turn = 0
    for event in events:
        name = str(event.get("event", ""))
        if name in ("run_start", "run_end"):
            continue
        if name == "assistant":
            turn += 1
            lines += [f"### Turn {turn}", ""]
            text = str(event.get("text") or "").strip()
            if text:
                lines += [text, ""]
            tools = ", ".join(f"`{tc.get('name')}`" for tc in (event.get("tool_calls") or [])
                              if isinstance(tc, dict))
            lines += [f"_tool calls: {tools}_" if tools else "_text reply, no tool calls_", ""]
            continue
        lines += _md_event_lines(event)
    if turn == 0 and len(lines) == 2:
        lines += ["_(no timeline events recorded)_", ""]
    return lines


def _last_event(events: list, name: str) -> dict:
    for event in reversed(events):
        if event.get("event") == name:
            return event
    return {}


def _final_message(events: list) -> str:
    """The run's final message, reconstructed from the transcript: run.json does
    not record it. A `finish(summary=...)` call is what the runner turned into
    `final_message`; otherwise (or when the 500-char `args` cap truncated the
    JSON) it is the last non-empty assistant reply."""
    for event in reversed(events):
        if event.get("event") == "tool_result" and event.get("tool") == "finish":
            try:
                args = json.loads(str(event.get("args") or "{}"))
            except ValueError:
                args = {}
            if isinstance(args, dict) and args.get("summary"):
                return str(args["summary"])
            break
    for event in reversed(events):
        if event.get("event") == "assistant":
            text = str(event.get("text") or "").strip()
            if text:
                return text
    return ""


def _md_result(data: dict, events: list) -> list:
    end = _last_event(events, "run_end")
    lines = ["## Result", ""]
    for key in MD_RESULT_FIELDS:
        value = data.get(key) or end.get(key)
        if value not in (None, ""):
            lines.append(f"- **{key}:** {str(value).splitlines()[0]}")
    lines.append("")
    diff_stat = data.get("diff_stat") or end.get("diff_stat")
    if diff_stat:
        lines += ["**diff_stat**", ""] + _md_block(str(diff_stat))
    final = _final_message(events)
    lines += ["**final message**", ""]
    lines += _md_block(final) if final else ["_(none recorded)_", ""]
    return lines


def render_markdown(slug: str, data: dict, events: list, *, diff=None, error=None) -> str:
    """The whole run as one Markdown document: run.json for the header and the
    result, the transcript for the turns. Token counts come from the transcript's
    `run_end.usage` -- run.json has never carried them."""
    lines = [f"# {slug}", ""]
    for key in MD_HEADER_FIELDS:
        lines.append(f"- **{key}:** {_summary_value(key, data)}")
    usage = _last_event(events, "run_end").get("usage")
    usage = usage if isinstance(usage, dict) else {}
    for key in ("prompt_tokens", "completion_tokens"):
        value = usage.get(key)
        lines.append(f"- **{key}:** {'-' if value is None else value}")
    for key in MD_VERDICT_FIELDS:
        if data.get(key):
            lines.append(f"- **{key}:** {_summary_value(key, data)}")
    lines.append("")
    if error:
        lines += [f"> **transcript unreadable:** {error}", ""]
    lines += _md_timeline(events)
    lines += _md_result(data, events)
    if diff is not None:
        lines += ["## Diff", ""] + _md_block(diff, "diff")
    return "\n".join(lines).rstrip() + "\n"
```

- [ ] **Step 6: Rewrite `cmd_show`**

Replace the whole function.

Before:

```python
def cmd_show(args) -> int:
    try:
        run_dir, data = _open_run(args.slug)
    except RunsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    for key in SHOW_FIELDS:
        print(f"{key}: {_summary_value(key, data)}")
    print()
    print(json.dumps(data, indent=2, sort_keys=True))

    transcript_path = run_dir / "transcript.jsonl"
    if transcript_path.is_file():
        print("\ntimeline:")
        try:
            lines = transcript_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            lines = []
            print(f"  (cannot read transcript: {e})")
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                print(_timeline_line(event))

    if getattr(args, "diff", False):
        patch_path = run_dir / "diff.patch"
        if patch_path.is_file():
            print("\ndiff:")
            print(patch_path.read_text(encoding="utf-8", errors="replace"))
        else:
            print("\nno diff.patch for this run (host mode, or the export never ran)")
    return 0
```

After:

```python
NO_PATCH_NOTE = "no diff.patch for this run (host mode, or the export never ran)"


def cmd_show(args) -> int:
    try:
        run_dir, data = _open_run(args.slug)
    except RunsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # `getattr`, not `args.x`: existing callers build a Namespace with
    # slug/diff only (tests/test_runs.py) and must keep working.
    want_diff = getattr(args, "diff", False)
    markdown = getattr(args, "markdown", False)
    out = getattr(args, "out", None)
    if out and not markdown:
        print("error: --out requires --markdown", file=sys.stderr)
        return 2

    transcript_path = run_dir / "transcript.jsonl"
    patch_path = run_dir / "diff.patch"

    if markdown:
        events, read_error = read_transcript_events(transcript_path)
        diff_text = None
        if want_diff:
            diff_text = (patch_path.read_text(encoding="utf-8", errors="replace")
                         if patch_path.is_file() else NO_PATCH_NOTE)
        document = render_markdown(args.slug, data, events, diff=diff_text, error=read_error)
        if out:
            try:
                Path(out).write_text(document, encoding="utf-8")
            except OSError as e:
                print(f"error: cannot write '{out}': {e}", file=sys.stderr)
                return 2
            print(f"wrote {out}")
        else:
            print(document, end="")
        return 0

    for key in SHOW_FIELDS:
        print(f"{key}: {_summary_value(key, data)}")
    print()
    print(json.dumps(data, indent=2, sort_keys=True))

    if transcript_path.is_file():
        print("\ntimeline:")
        events, read_error = read_transcript_events(transcript_path)
        if read_error:
            print(f"  (cannot read transcript: {read_error})")
        for event in events:
            print(_timeline_line(event))

    if want_diff:
        if patch_path.is_file():
            print("\ndiff:")
            print(patch_path.read_text(encoding="utf-8", errors="replace"))
        else:
            print(f"\n{NO_PATCH_NOTE}")
    return 0
```

- [ ] **Step 7: Run the module tests**

Run: `python3 -m pytest tests/test_runs.py -q`
Expected: `65 passed` (60 today + the 5 new ones). The three pre-existing `cmd_show` tests must still pass unchanged — they exercise the text path.

- [ ] **Step 8: Add the `--markdown` / `--out` flags**

In `dirtywork/__main__.py`, inside `_add_runs_parsers`.

Before:

```python
    show_p = runs_sub.add_parser("show", help="show one run's summary, run.json and timeline")
    show_p.add_argument("slug")
    show_p.add_argument("--diff", action="store_true", help="also print the run's diff.patch")
```

After:

```python
    show_p = runs_sub.add_parser("show", help="show one run's summary, run.json and timeline")
    show_p.add_argument("slug")
    show_p.add_argument("--diff", action="store_true", help="also print the run's diff.patch")
    show_p.add_argument("--markdown", action="store_true",
                        help="render the run as a Markdown document (header, one section per "
                             "turn, collapsible tool results) instead of the JSON dump")
    show_p.add_argument("--out", default=None, metavar="FILE",
                        help="with --markdown, write the document to FILE instead of stdout")
```

- [ ] **Step 9: README + transcript-schema lines**

In `README.md`, under "## Inspecting, cleaning up and re-exporting runs".

Before:

```
- `dirtywork runs show <slug> [--diff]` — the run's summary fields, its full
  `run.json`, and a timeline reconstructed from the transcript; `--diff`
  also prints `diff.patch`.
```

After:

```
- `dirtywork runs show <slug> [--diff] [--markdown] [--out FILE]` — the run's
  summary fields, its full `run.json`, and a timeline reconstructed from the
  transcript; `--diff` also prints `diff.patch`. `--markdown` renders the same
  run as a Markdown report instead (header block, one `### Turn N` section per
  assistant turn, collapsible `<details>` tool results, blockquote callouts for
  nudges/guardrail blocks/sandbox resets, a `## Result` section, and with
  `--diff` the patch in a fenced block) — paste-ready for a PR or an issue;
  `--out FILE` writes it to a file instead of stdout.
```

In `docs/transcript-schema.md`, the closing sentence.

Before:

```
`dirtywork runs show <slug>` prints this file alongside a tool-call timeline
reconstructed from the transcript.
```

After:

```
`dirtywork runs show <slug>` prints this file alongside a tool-call timeline
reconstructed from the transcript. `dirtywork runs show <slug> --markdown
[--out FILE]` exports those same two sources as one Markdown document —
`run.json` for the header block and the `## Result` section, the transcript for
one `### Turn N` per `assistant` event with its `tool_result`s as `<details>`
blocks (capped at the same 2000-char preview the transcript itself applies) and
its `nudge`/`guardrail_block`/`sandbox_reset` events as blockquote callouts.
Token counts in the header come from `run_end.usage`, and the final message from
the `finish` call's `summary`, because `run.json` records neither.
```

- [ ] **Step 10: Run the full suite**

Run: `python3 -m pytest -q`
Expected: `822 passed` (817 after Task 1 + 5), 18 deselected.

- [ ] **Step 11: Commit**

```bash
git add dirtywork/runs.py dirtywork/__main__.py tests/test_runs.py README.md docs/transcript-schema.md
git commit -m "feat: 'runs show --markdown [--out FILE]' exports a run as a Markdown report"
```

---

## Self-review: design coverage

| Design item | Task / step |
|---|---|
| A: `--compare FILE` on the existing `summarize` sub-subparser in `_add_bench_parsers` | Task 1, Step 7 |
| A: load both files, aggregate each with the existing aggregation | Task 1, Steps 3–5 (`_load_results`, `_aggregate`, `_summarize_model`) |
| A: one table keyed by model + task | Task 1, Step 4 (`_model_task_key`, `_compare_rows`, `COMPARE_COLUMNS`) |
| A: paired columns for turns, wall_s, prompt, completion, acceptance, verdict | Task 1, Step 4 (`_compare_rows`) |
| A: compact harness-failure column (nudges/stalled/max_turns) | Task 1, Steps 3–4 (`_summarize_model` counters, `_harness_cell`) |
| A: rows in only one file show a dash on the other side | Task 1, Step 4 (`MISSING`, `_fmt_cell`); test asserts `- -> 1` |
| A: per-model stats for each file, side by side | Task 1, Step 4 (`_compare_model_rows`, `COMPARE_MODEL_COLUMNS`) |
| A: deltas are B minus A, `+N`/`-N`, stated in the header | Task 1, Step 4 (`_fmt_delta`, `_print_comparison` header) |
| A: reuse `runs.format_table`; no second aggregator | Task 1, Step 4 (`format_table` calls only); Step 3 extends the one `_summarize_model` |
| A: tests — two jsonl fixtures, paired columns, deltas, missing-row dash, `bench_error` row | Task 1, Step 1 |
| A: README line under "Benchmarking" | Task 1, Step 8 |
| B: `--markdown` renders a document instead of JSON dump + text timeline | Task 2, Step 6 |
| B: `# <slug>` header block from run.json | Task 2, Step 5 (`MD_HEADER_FIELDS`, `render_markdown`) |
| B: prompt/completion tokens in the header | Task 2, Step 5 (`run_end.usage`) |
| B: verdict/note if any | Task 2, Step 5 (`MD_VERDICT_FIELDS`, emitted only when truthy) |
| B: `## Timeline`, one `### Turn N` per assistant turn | Task 2, Step 5 (`_md_timeline`) |
| B: tool calls as `<details><summary>tool(args…)</summary>` + fenced result, capped at the transcript's preview lengths | Task 2, Step 5 (`_md_event_lines`, `MD_ARGS_CHARS`, `MD_RESULT_CHARS`) |
| B: nudge / guardrail_block / sandbox_reset as blockquote callouts | Task 2, Step 5 (`_md_event_lines`) |
| B: `## Result` (status, diff_stat, export_status, final message) | Task 2, Step 5 (`_md_result`, `_final_message`) |
| B: `--out FILE` writes instead of printing | Task 2, Steps 6, 8 |
| B: refactor `_timeline_line`'s parsing into an event-iterating helper both renderers share; no second parser | Task 2, Steps 4, 6 (`read_transcript_events`; `_timeline_line` unchanged) |
| B: `--diff` still works and, with `--markdown`, embeds the diff in a fenced block | Task 2, Step 6 (`diff_text`), Step 5 (`## Diff` + `_md_block(diff, "diff")`) |
| B: tests — fixture transcript + run.json, headings, per-turn sections, details, callouts, `--out`, `--diff --markdown` | Task 2, Step 1 |
| B: README line + transcript-schema sentence | Task 2, Step 9 |
| Global: baseline 815 stays green, stdout additive only, conventional commits, tests in existing module files | Global Constraints; Task 1 Steps 9–10, Task 2 Steps 10–11 |

## Type consistency checklist

- `_summarize_model(rows: list, verdicts: list, review_seconds: list) -> dict` — unchanged signature; the returned dict only gains keys. `mean_*` values are `float | None`; `nudges`/`stalled`/`max_turns`/`gamed`/`runs` are `int`; `*_rate` values are `float` (`verdict_rate` is `float | None`).
- `_mean(values: list) -> float | None` and `_numbers(rows: list, key: str) -> list` — `_numbers` filters with `isinstance(x, (int, float))`, which also admits `bool`; no bool is ever stored in `turns`/`wall_s`/`*_tokens`, and `harness` flags are read separately with truthiness.
- `_aggregate(rows: list, key) -> dict` — `key` is a callable returning `str` (per-model) or `tuple` (per model+task); the two are never mixed inside one call, and `sorted(set(a) | set(b))` therefore never compares a `str` with a `tuple`.
- `_fmt_cell` / `_fmt_delta` / `_compare_cell` take `float | int | None` and always return `str`; every cell handed to `format_table` is a `str`, which is what `format_table`'s `len(str(...))` padding expects.
- `_stat(summary, key) -> Any | None` — `summary` is `dict | None`; `None` propagates to `_fmt_cell` and renders as `MISSING`.
- `cmd_summarize(args) -> int`; `_print_comparison(path_a: Path, rows_a: list, path_b: Path, rows_b: list) -> int` — both return `0`/`2` only.
- `read_transcript_events(path) -> tuple` — always `(list[dict], str | None)`; never raises, never returns `None` for the list.
- `render_markdown(slug: str, data: dict, events: list, *, diff=None, error=None) -> str` — `diff` is `str | None` (`None` means "no `## Diff` section"; the "no diff.patch" note is a `str` and still renders a section), `error` is `str | None`. Return value always ends with exactly one `\n`.
- `_md_trim(value, limit: int) -> str`, `_md_inline(value, limit: int) -> str` (trim then `html.escape(..., quote=False)`; used only for inline/HTML contexts, never for fenced content), `_md_fence(text: str) -> str`, `_md_block(text: str, lang: str = "") -> list` (list of `str`), `_md_event_lines(event: dict) -> list`, `_md_timeline(events: list) -> list`, `_md_result(data: dict, events: list) -> list`, `_last_event(events: list, name: str) -> dict` (empty dict, never `None`), `_final_message(events: list) -> str` (empty string when nothing is recorded).
- `cmd_show(args) -> int` — every new attribute is read through `getattr` with a default, so a `Namespace` built without `markdown`/`out` behaves exactly as before.
- Python 3.9: `X | None` appears only in annotations and docstrings (both modules carry `from __future__ import annotations`); no runtime union, no `match`, no walrus in a comprehension.
