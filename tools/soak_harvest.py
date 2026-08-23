#!/usr/bin/env python3
"""Print the three scoreboard tables for one or more soak runs, issue #48
(docs/superpowers/bench/2026-08-23-v1-soak-matrix.md). Column headers are
copied verbatim from `docs/superpowers/bench/2026-08-18-ops-worker-scoreboard.md`
(main, per-run metrics, model-vs-tool time), with one column added to the
main table -- **Feature fired** -- computed per the matrix's Feature -> signal
table (F1/F2/F3/F4/F5/F7/F8/F9; F6 and F9's "no *.tmp left behind" half and
F10 are not run-local signals and are out of scope for this tool).

Takes run directories directly, and/or a `soak_driver.py --out` JSONL (every
row with a `run_dir` is harvested). Stdlib only, run from a source checkout.
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import soak_common  # noqa: E402
from dirtywork import bench  # noqa: E402

MAIN_COLUMNS = ("Run", "Model", "Status", "Turns", "Wall", "Prompt tok", "Compl tok",
                "Harness failures", "Review verdict", "Notes", "Feature fired")
PER_RUN_COLUMNS = ("Run", "Status", "Turns", "Wall", "s/turn", "Prompt tok", "Compl tok",
                   "prompt tok/s", "compl tok/s", "nudges", "guardrail blocks", "tool mix")
MODEL_TOOL_COLUMNS = ("Run", "Status", "Turns", "Wall", "Model time", "Tool time",
                      "model s/turn", "tool s/turn", "prompt tok/s (model time)",
                      "compl tok/s (model time)", "slowest tool call")

# Verified against the actual success strings, not just docs/transcript-schema.md's
# description of them: `dirtywork.tools._apply_edits_once` (dirtywork/tools.py:711)
# builds `verb = f"Applied {total} edit{'' if total == 1 else 's'} to"` (:762,
# `total = len(edits)` at :729) and `dirtywork.tools.append_file` (:787) calls
# `describe_change(..., verb="Appended to")` (:885); the Docker backend's
# `apply_edits`/`append_file` (dirtywork/sandbox/docker.py:709,631) call the exact same
# shared `_apply_edits_once`/`describe_change` (docker.py:677 for the append verb), so
# host and container success strings are byte-identical. `describe_change`
# (dirtywork/tools.py:421) always opens its result with `f"{verb} {path}: ..."` (:445),
# confirmed empirically too:
#   tools.apply_edits(wt, "f.py", [3 edits]) -> "Applied 3 edits to f.py: +3 -3 ..."
#   tools.apply_edits(wt, "h.py", [1 edit])  -> "Applied 1 edit to h.py: +1 -1 ..."
#   tools.append_file(wt, "g.txt", "y\n")    -> "Appended to g.txt: +1 -0"
# The hunk count is always the FIRST thing after "Applied " -- `total`, i.e. the number
# of edits in the call's `edits` argument, and only appears on this success path (every
# failure branch returns an "ERROR: edit N of M: ..." string starting with "ERROR",
# before `verb`/describe_change are ever reached) -- so there is no case where the count
# is present in a tool_call's args but absent from a successful result string, and no
# args-based fallback is needed.
_APPLY_EDITS_RE = re.compile(r"^Applied (\d+) edits? to ")
_APPEND_FILE_RE = re.compile(r"^Appended to ")


# --------------------------------------------------------------------------
# Feature detection (matrix's "Feature -> signal" table)
# --------------------------------------------------------------------------

def detect_features(run_json: dict, events: list) -> list:
    """F-ids that fired in one run, as compact strings (e.g. ["F1",
    "F2(repeats=4)"]), per the matrix doc's Feature -> signal table. F2/F4/
    F7/F9 read `run.json` (always available even off a truncated
    transcript); F1/F3/F5/F8 need per-event ordering and read the transcript."""
    fired = []

    # F1: an apply_edits tool_result success with >=3 hunks in one call.
    for e in events:
        if e.get("event") == "tool_result" and e.get("tool") == "apply_edits":
            m = _APPLY_EDITS_RE.match(e.get("result") or "")
            if m and int(m.group(1)) >= 3:
                fired.append("F1")
                break

    # F2: run_end.status == "stuck" -- append stuck_on.repeats.
    if run_json.get("status") == "stuck":
        repeats = (run_json.get("stuck_on") or {}).get("repeats")
        fired.append(f"F2(repeats={repeats})" if repeats is not None else "F2")

    # F3: a timed-out bash tool_result AND a "timeout" nudge in the same run.
    has_timeout_result = any(e.get("event") == "tool_result" and e.get("timed_out")
                             for e in events)
    has_timeout_nudge = any(e.get("event") == "nudge" and e.get("kind") == "timeout"
                            for e in events)
    if has_timeout_result and has_timeout_nudge:
        fired.append("F3")

    # F4: run_end.verify non-null -- append passed/rounds.
    verify = run_json.get("verify")
    if isinstance(verify, dict):
        fired.append(f"F4(passed={verify.get('passed')},rounds={verify.get('rounds')})")

    # F5: an assistant finish_reason=="length", followed later in the
    # transcript by a successful append_file call.
    seen_length = False
    for e in events:
        ev = e.get("event")
        if ev == "assistant" and e.get("finish_reason") == "length":
            seen_length = True
        elif (seen_length and ev == "tool_result" and e.get("tool") == "append_file"
              and _APPEND_FILE_RE.match(e.get("result") or "")):
            fired.append("F5")
            break

    # F7: provider == "ollama".
    if run_json.get("provider") == "ollama":
        fired.append("F7")

    # F8: a "stall" nudge, or terminal status "stalled".
    has_stall_nudge = any(e.get("event") == "nudge" and e.get("kind") == "stall"
                          for e in events)
    if has_stall_nudge or run_json.get("status") == "stalled":
        fired.append("F8")

    # F9: sandbox image pinned and enforced.
    if run_json.get("image_pinned") is True:
        fired.append("F9")

    return fired


# --------------------------------------------------------------------------
# Model time vs tool time
# --------------------------------------------------------------------------

def _parse_ts(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def model_tool_time(events: list) -> dict:
    """Model time vs tool time, derived from transcript event timestamps.

    AMBIGUITY: the transcript schema (docs/transcript-schema.md) gives every
    event a single `ts` -- when the line was WRITTEN -- not a start/end pair
    for the model call or the tool call it is about. There is no shipped
    tool that derives this split (the 2026-08-17 ad hoc `run-split.py`
    quoted in docs/superpowers/bench/2026-08-17-sp3-run-split.md was never
    checked into this repo, so its exact formula is not recoverable). The
    formula used here, chosen to match that doc's shape: walk events in
    timestamp order; the gap between two consecutive events' `ts` is charged
    to MODEL time when the SECOND event is `assistant` (the model was
    generating that reply in between), and to TOOL time otherwise (a
    `tool_result`, `nudge`, `guardrail_block`, `sandbox_reset` or `run_end`
    following an `assistant` event -- the harness or the sandboxed command
    was running in between). The slowest single tool call is the largest
    such gap ending on a `tool_result`."""
    model_s = 0.0
    tool_s = 0.0
    slowest = None  # (seconds, tool, args)
    prev_ts = None
    for e in events:
        ts = _parse_ts(e.get("ts"))
        if ts is None:
            continue
        if prev_ts is not None:
            gap = (ts - prev_ts).total_seconds()
            if gap > 0:
                if e.get("event") == "assistant":
                    model_s += gap
                else:
                    tool_s += gap
                    if e.get("event") == "tool_result" and (slowest is None or gap > slowest[0]):
                        slowest = (gap, e.get("tool") or "", e.get("args") or "")
        prev_ts = ts
    return {"model_s": model_s, "tool_s": tool_s, "slowest": slowest}


# --------------------------------------------------------------------------
# Per-run aggregates shared by all three tables
# --------------------------------------------------------------------------

def _last_event(events: list, name: str) -> dict:
    for e in reversed(events):
        if e.get("event") == name:
            return e
    return {}


def _wall_seconds(run_json: dict, run_end: dict):
    if isinstance(run_end.get("duration_s"), (int, float)):
        return float(run_end["duration_s"])
    a, b = _parse_ts(run_json.get("started")), _parse_ts(run_json.get("ended"))
    if a is not None and b is not None:
        return (b - a).total_seconds()
    return None


def _tool_mix(events: list) -> str:
    counts = {}
    for e in events:
        if e.get("event") == "tool_result":
            tool = e.get("tool")
            if tool:
                counts[tool] = counts.get(tool, 0) + 1
    return " ".join(f"{t}:{n}" for t, n in sorted(counts.items()))


def harvest_run(label: str, run_dir: Path, driver_row: dict = None) -> dict:
    """One run dir's raw numbers, gathered once and shared by all three
    table-row builders below."""
    run_json = soak_common.read_run_json(run_dir)
    events = soak_common.read_transcript(run_dir)
    run_end = _last_event(events, "run_end")
    usage = run_end.get("usage") or {}
    status = run_json.get("status") or run_end.get("status")
    turns = run_json.get("turns", run_end.get("turns"))
    wall_s = _wall_seconds(run_json, run_end)

    counts = bench._event_counts(run_dir)
    # `last_assistant_text` stands in for the runner's own `final_message`
    # (only available at run time, never persisted -- docs/transcript-schema.md's
    # `## run.json` note on `dirtywork runs show`) -- it carries the same
    # "aborted after N consecutive X failures" text on a model_error run.
    harness = bench._harness_failures(counts, status, run_json.get("last_assistant_text"),
                                      run_json.get("timeouts", 0))

    notes = "-"
    if driver_row is not None:
        bits = []
        if driver_row.get("acceptance_passed") is not None:
            bits.append(f"acceptance_passed={driver_row['acceptance_passed']}")
        if driver_row.get("error"):
            bits.append(f"error={driver_row['error']}")
        notes = "; ".join(bits) if bits else "-"

    return {
        "label": label, "run_dir": run_dir, "model": run_json.get("model"),
        "status": status, "turns": turns, "wall_s": wall_s,
        "prompt_tok": usage.get("prompt_tokens"), "compl_tok": usage.get("completion_tokens"),
        "harness": harness, "verdict": run_json.get("verdict"), "notes": notes,
        "features": detect_features(run_json, events),
        "nudges": sum(counts[f"nudge_{k}"] for k in bench.NUDGE_KINDS),
        "guardrail_blocks": counts["guardrail_block"],
        "tool_mix": _tool_mix(events),
        "model_tool": model_tool_time(events),
    }


# --------------------------------------------------------------------------
# Table rows
# --------------------------------------------------------------------------

def _fmt_minutes(seconds):
    return "-" if seconds is None else f"{seconds / 60:.1f}m"


def _fmt_int(value):
    return "-" if value is None else str(value)


def _fmt_rate(count, seconds):
    if count is None or not seconds:
        return "-"
    return f"{count / seconds:.1f}"


def _fmt_pct(part, whole):
    return f"{part / whole:.0%}" if whole else "0%"


def _main_row(h: dict) -> dict:
    return {
        "Run": h["label"], "Model": h.get("model") or "-", "Status": h.get("status") or "-",
        "Turns": _fmt_int(h.get("turns")), "Wall": _fmt_minutes(h.get("wall_s")),
        "Prompt tok": _fmt_int(h.get("prompt_tok")), "Compl tok": _fmt_int(h.get("compl_tok")),
        "Harness failures": bench._failure_cell(h["harness"]),
        "Review verdict": h.get("verdict") or "-", "Notes": h.get("notes", "-"),
        "Feature fired": ", ".join(h["features"]) if h["features"] else "-",
    }


def _per_run_row(h: dict) -> dict:
    wall_s = h.get("wall_s")
    turns = h.get("turns")
    return {
        "Run": h["label"], "Status": h.get("status") or "-", "Turns": _fmt_int(turns),
        "Wall": _fmt_minutes(wall_s),
        "s/turn": f"{wall_s / turns:.1f}s" if wall_s and turns else "-",
        "Prompt tok": _fmt_int(h.get("prompt_tok")), "Compl tok": _fmt_int(h.get("compl_tok")),
        "prompt tok/s": _fmt_rate(h.get("prompt_tok"), wall_s),
        "compl tok/s": _fmt_rate(h.get("compl_tok"), wall_s),
        "nudges": h.get("nudges", 0), "guardrail blocks": h.get("guardrail_blocks", 0),
        "tool mix": h.get("tool_mix") or "-",
    }


def _model_tool_row(h: dict) -> dict:
    mt = h["model_tool"]
    model_s, tool_s = mt["model_s"], mt["tool_s"]
    total = model_s + tool_s
    turns = h.get("turns")
    slowest = mt["slowest"]
    slowest_cell = f"{slowest[0]:.0f}s {slowest[1]} {slowest[2]}" if slowest else "-"
    return {
        "Run": h["label"], "Status": h.get("status") or "-", "Turns": _fmt_int(turns),
        "Wall": _fmt_minutes(h.get("wall_s")),
        "Model time": f"{_fmt_minutes(model_s)} ({_fmt_pct(model_s, total)})",
        "Tool time": f"{_fmt_minutes(tool_s)} ({_fmt_pct(tool_s, total)})",
        "model s/turn": f"{model_s / turns:.1f}s" if turns else "-",
        "tool s/turn": f"{tool_s / turns:.1f}s" if turns else "-",
        "prompt tok/s (model time)": _fmt_rate(h.get("prompt_tok"), model_s),
        "compl tok/s (model time)": _fmt_rate(h.get("compl_tok"), model_s),
        "slowest tool call": slowest_cell,
    }


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def _markdown_table(columns, rows) -> str:
    lines = ["| " + " | ".join(columns) + " |",
             "|" + "|".join(["---"] * len(columns)) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "-")) for c in columns) + " |")
    return "\n".join(lines)


def _render_table(title: str, columns, rows, csv_mode: bool) -> str:
    if csv_mode:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(columns)
        for r in rows:
            w.writerow([r.get(c, "") for c in columns])
        return f"# {title}\n" + buf.getvalue().rstrip("\n")
    return f"## {title}\n" + _markdown_table(columns, rows)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _entries_from_driver_out(path: Path) -> list:
    entries = []
    for row in soak_common.load_jsonl(path):
        if row.get("run_dir"):
            entries.append((row.get("label") or Path(row["run_dir"]).name,
                            Path(row["run_dir"]), row))
    return entries


def _entries_from_run_dirs(paths: list) -> list:
    return [(Path(p).name, Path(p), None) for p in paths]


def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="soak_harvest.py",
        description="Print scoreboard tables (main, per-run metrics, model-vs-tool "
                    "time) for one or more soak runs.")
    p.add_argument("run_dirs", nargs="*", default=[],
                  help="run directories under ~/.dirtywork/runs")
    p.add_argument("--driver-out", default=None, metavar="FILE",
                  help="soak_driver.py's --out JSONL; every row with a run_dir is harvested")
    p.add_argument("--csv", action="store_true", default=False,
                  help="print CSV instead of Markdown tables")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    entries = []
    if args.driver_out:
        entries += _entries_from_driver_out(Path(args.driver_out))
    entries += _entries_from_run_dirs(args.run_dirs)
    if not entries:
        print("error: give one or more RUN_DIR positional args and/or --driver-out FILE",
              file=sys.stderr)
        return 2

    harvested = []
    for label, run_dir, driver_row in entries:
        if not run_dir.is_dir():
            print(f"warning: no such run dir '{run_dir}', skipping", file=sys.stderr)
            continue
        harvested.append(harvest_run(label, run_dir, driver_row))
    if not harvested:
        print("error: no runs could be harvested", file=sys.stderr)
        return 2

    print(_render_table("Main", MAIN_COLUMNS, [_main_row(h) for h in harvested], args.csv))
    print()
    print(_render_table("Per-run metrics (auto)", PER_RUN_COLUMNS,
                        [_per_run_row(h) for h in harvested], args.csv))
    print()
    print(_render_table("Model vs tool time", MODEL_TOOL_COLUMNS,
                        [_model_tool_row(h) for h in harvested], args.csv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
