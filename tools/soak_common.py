"""Shared helpers for tools/soak_driver.py and tools/soak_harvest.py (issue
#48, docs/superpowers/bench/2026-08-23-v1-soak-matrix.md).

DRY (repo standing rule): everything here is either genuinely new to these
two scripts, or a thin existence-check wrapper around parsing the package
already does -- `dirtywork.bench._load_results` already parses a JSONL file
tolerantly, and `dirtywork.runs.read_transcript_events` already parses a
transcript tolerantly (missing/unreadable file included), so this module
calls those instead of re-implementing their loops (review of a09dd65, item
3 -- the two functions here used to be near-verbatim copies of those, plus
unused `RUNS_DIR`/`BENCH_HOME` re-exports of `dirtywork.rundir` that no
caller in tools/ ever read; both are gone now).

Stdlib only.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dirtywork import bench, rundir, runs  # noqa: E402  (path insert above must run first)


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
    or a bench results file), via `dirtywork.bench._load_results`. That
    function itself assumes the file exists -- its one in-package caller,
    `bench.cmd_summarize`, checks first -- so this wrapper adds the same
    check: a driver's --out file legitimately does not exist yet before a
    sweep's first row, and a plan path might simply be a typo."""
    path = Path(path)
    if not path.is_file():
        return []
    return bench._load_results(path)


def read_transcript(run_dir) -> list:
    """Every JSON object in `<run_dir>/transcript.jsonl`, via
    `dirtywork.runs.read_transcript_events` (already tolerant of a missing
    or unreadable file -- the error string it can also return is discarded
    here, same as every other caller in the package that only wants the
    events, e.g. `runs.cmd_show`)."""
    events, _error = runs.read_transcript_events(Path(run_dir) / "transcript.jsonl")
    return events
