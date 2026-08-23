"""Shared run-dir discovery/parsing for tools/soak_driver.py and
tools/soak_harvest.py (issue #48, docs/superpowers/bench/2026-08-23-v1-soak-matrix.md).

Both scripts read `~/.dirtywork/runs/<slug>/run.json` and
`~/.dirtywork/runs/<slug>/transcript.jsonl` (docs/transcript-schema.md) and a
driver's own results JSONL -- this module is the one place that knows how to
find and tolerantly parse those three file shapes, so a schema quirk gets
fixed once instead of twice.

Stdlib only. Mirrors the tolerant-parse style of `dirtywork.bench._load_results`
and `dirtywork.rundir.read_run_json`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dirtywork import rundir  # noqa: E402  (path insert above must run first)

RUNS_DIR = rundir.RUNS_DIR
BENCH_HOME = rundir.BENCH_HOME


def read_run_json(run_dir) -> dict:
    """`run.json` for one run dir, or {} if missing/corrupt -- a soak sweep
    outlives individual bad runs, same tolerance `dirtywork.bench` gives
    bench_error rows."""
    try:
        return rundir.read_run_json(Path(run_dir))
    except (OSError, ValueError):
        return {}


def load_jsonl(path) -> list:
    """Every JSON object in a JSONL file (a soak plan, a driver --out file,
    or a bench results file). Blank and malformed lines are skipped: a file
    written by a killed sweep, or a hand-edited plan with a stray blank
    line, must still load the rows that parse."""
    path = Path(path)
    if not path.is_file():
        return []
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


def read_transcript(run_dir) -> list:
    """Every JSON object in `<run_dir>/transcript.jsonl`, in file order (the
    events are line-flushed as written, so file order is chronological
    order). `[]` if the run dir has no transcript yet."""
    path = Path(run_dir) / "transcript.jsonl"
    return load_jsonl(path)
