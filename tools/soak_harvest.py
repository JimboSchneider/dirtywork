#!/usr/bin/env python3
"""Print the three scoreboard tables for one or more soak runs, issue #48
(docs/superpowers/bench/2026-08-23-v1-soak-matrix.md). Column headers are
copied verbatim from `docs/superpowers/bench/2026-08-18-ops-worker-scoreboard.md`
(main, per-run metrics, model-vs-tool time), with one column added to the
main table -- **Feature fired** -- computed per the matrix's Feature -> signal
table (F1/F2/F3/F4/F5/F7/F8/F9/F10; F6 and the "no *.tmp left behind" half of F9 have no
run-local signal and are out of scope for this tool).

Takes run directories directly, and/or a `soak_driver.py --out` JSONL (every
row with a `run_dir` is harvested). Stdlib only, run from a source checkout.
"""
from __future__ import annotations

import argparse
import ast
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
from dirtywork.runs import _last_event  # noqa: E402  (DRY: reuse, don't re-copy -- review item 4)
from dirtywork.runner import _recovered_path  # noqa: E402  (same regex scan runner.py itself
# uses to recover a truncated write_file/append_file call's "path" argument out of raw,
# possibly-incomplete JSON bytes -- reused here for F5 path matching, not re-copied)

MAIN_COLUMNS = ("Run", "Model", "Status", "Turns", "Wall", "Prompt tok", "Compl tok",
                "Harness failures", "Review verdict", "Notes", "Feature fired")
PER_RUN_COLUMNS = ("Run", "Status", "Turns", "Wall", "s/turn", "Prompt tok", "Compl tok",
                   "prompt tok/s", "compl tok/s", "nudges", "guardrail blocks", "stray kills", "tool mix")
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
# write_file's success result always starts "Wrote " -- either "Wrote N bytes
# to <path> (new file, M lines)" (dirtywork/tools.py:488, describe_write's
# old_text-is-None branch) or, for an existing file, describe_change's own
# "Wrote <path>: +A -D ..." (describe_write:489, verb="Wrote"). Both branches
# share the prefix, so one regex covers write_file success either way.
_WRITE_FILE_RE = re.compile(r"^Wrote ")

# 2026-08-23 review round 2: tool_result.args is capped at 500 chars
# (runner.py:810, `args=raw_args[:500]`) -- a call whose JSON puts a long
# "text" value before "path" pushes the path past that cap, so
# _recovered_path(args) can silently come back None even though the path is
# perfectly readable out of the RESULT text instead. One regex per shape,
# read straight off dirtywork/tools.py: describe_write's new-file branch
# (:485-488, "Wrote N bytes to <path> (new file, M lines)"), and
# describe_change's own head line (:445, "<verb> <path>: +A -D ...", or,
# when the diff itself was skipped for size, :439 "<verb> <path>: N lines
# (diff omitted: file too large)") -- verb="Wrote" for an existing-file
# write_file (describe_write:489) and verb="Appended to" for append_file
# (tools.py:885).
_WRITE_NEW_FILE_PATH_RE = re.compile(r"^Wrote \d+ bytes to (.+) \(new file, \d+ lines?\)$")
_WRITE_EXISTING_FILE_PATH_RE = re.compile(
    r"^Wrote (.+?): (?:\+\d+ -\d+|\d+ lines \(diff omitted: file too large\))")
_APPEND_FILE_PATH_RE = re.compile(
    r"^Appended to (.+?): (?:\+\d+ -\d+|\d+ lines \(diff omitted: file too large\))")
# The write_file variant of truncated_call_result (runner.py:135-141) embeds the
# recovered path as {path!r} -- a Python repr, so single- or double-quoted;
# ast.literal_eval undoes the repr exactly.
_TRUNCATED_WRITE_PATH_RE = re.compile(r"^ERROR: your write_file for ('.*?'|\".*?\") was cut off")

# The two message shapes `dirtywork.runner.truncated_call_result` (runner.py:128-144)
# can produce for a tool call cut off by finish_reason=="length": the write_file
# variant with a recoverable path ("ERROR: your write_file for %r was cut off at
# the token limit — nothing was written. ...") and the generic variant for every
# other tool, or a write_file whose path could not be recovered ("ERROR: your
# {tool} call was cut off at the token limit before it completed. ..."). Matched
# by prefix only -- the matrix doc's signal is "result starts with the
# truncated_call_result text", not a byte-for-byte reproduction.
_TRUNCATED_CALL_RESULT_RE = re.compile(
    r"^ERROR: your (?:write_file for |\S+ call was cut off at the token limit "
    r"before it completed\.)")


def _event_path(e: dict):
    """The path a write_file/append_file tool_result event names, preferring
    the RESULT text (immune to args' 500-char transcript cap -- see the
    _WRITE_*_PATH_RE/_APPEND_FILE_PATH_RE block above) and falling back to
    `_recovered_path(args)` only when the result names no path. The
    write_file variant of `truncated_call_result` names the path it recovered
    (repr-quoted) in its ERROR text, so that is parsed too -- the args field
    is capped and may have lost the key the runner itself still saw."""
    tool = e.get("tool")
    result = e.get("result")
    if isinstance(result, str):
        if tool == "write_file":
            m = _WRITE_NEW_FILE_PATH_RE.match(result) or _WRITE_EXISTING_FILE_PATH_RE.match(result)
            if m:
                return m.group(1)
        elif tool == "append_file":
            m = _APPEND_FILE_PATH_RE.match(result)
            if m:
                return m.group(1)
        m = _TRUNCATED_WRITE_PATH_RE.match(result)
        if m:
            try:
                return ast.literal_eval(m.group(1))
            except (ValueError, SyntaxError):
                pass
    return _recovered_path(e.get("args"))


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

    # F4: run_end.verify non-null AND passed == True -- append rounds. A
    # verify_failed run (verify present but passed is not True) is NOT the
    # feature firing -- --verify ran and the run's own check failed, which is
    # an outcome, not confirmation the feature worked -- so it must not be
    # counted as F4. The round count is still worth keeping, so it renders as
    # F4-failed(rounds=N): visible in the table, but never mistaken for F4
    # having fired by anything doing an exact/prefix match on "F4(".
    verify = run_json.get("verify")
    if isinstance(verify, dict):
        if verify.get("passed") is True:
            fired.append(f"F4(passed=True,rounds={verify.get('rounds')})")
        else:
            fired.append(f"F4-failed(rounds={verify.get('rounds')})")

    # F5: the harness actually communicated a truncation back to the model --
    # an assistant finish_reason=="length" followed, anywhere in that SAME
    # turn's results, by either a "truncated" nudge (a truncated text reply,
    # no tool call) or a tool_result whose result starts with
    # truncated_call_result's text (a truncated tool call) -- and AFTER that
    # point a successful append_file (`^Appended to `) whose path is either
    # the path that truncated call named (when recoverable), or the path of
    # ANY successful write_file anywhere in the run, before or after the
    # truncation (a model may write_file to start a large file, then get
    # truncated mid-append while extending it, unrelated to which call the
    # truncation itself happened to name). A finish_reason=="length" that
    # ISN'T followed by one of those two signals anywhere in its own turn
    # (e.g. every tool call in that turn finished successfully despite
    # hitting the length limit) establishes no truncation point at all --
    # any append_file success after it is unrelated and must not count.
    #
    # "Same turn" is NOT just the next transcript event: per runner.py
    # (:704-817) one assistant event is followed by one tool_result PER call
    # attempted that turn, in order -- malformed entries (tool="", no `id`)
    # first (:741-749), then the real named calls (:754-817) -- so a
    # truncated call can be the 2nd or 3rd tool_result after the assistant
    # event, not just the 1st. The scan below covers every event from right
    # after an assistant(length) event up to (not including) the next
    # `assistant` event, which is exactly that turn's results.
    # `written_paths` accumulates DURING the scan so only a successful
    # write_file that precedes an append can vouch for it -- a write that
    # happens after the append is not the file the append extended (PR #62
    # review round 2).
    written_paths = set()

    truncation_active = False
    truncation_path = None      # the specific path a truncated call named, if recoverable
    scanning_turn = False       # True across an entire assistant(length) turn's own results
    for e in events:
        ev = e.get("event")
        if ev == "assistant":
            scanning_turn = e.get("finish_reason") == "length"
        elif scanning_turn:
            if ev == "nudge" and e.get("kind") == "truncated":
                truncation_active = True
                truncation_path = None
                scanning_turn = False   # signal found; rest of this turn no longer matters here
            elif ev == "tool_result" and _TRUNCATED_CALL_RESULT_RE.match(e.get("result") or ""):
                truncation_active = True
                truncation_path = _event_path(e)
                scanning_turn = False
        if (ev == "tool_result" and e.get("tool") == "write_file"
                and _WRITE_FILE_RE.match(e.get("result") or "")):
            written = _event_path(e)
            if written is not None:
                written_paths.add(written)
        if (truncation_active and ev == "tool_result" and e.get("tool") == "append_file"
                and _APPEND_FILE_RE.match(e.get("result") or "")):
            append_path = _event_path(e)
            if append_path is not None and (
                    append_path == truncation_path or append_path in written_paths):
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

    # F10: a resume that carried reviewer feedback (`resume --feedback`):
    # run.json.feedback is the verbatim text, null otherwise.
    if run_json.get("feedback"):
        fired.append("F10")

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
    `tool_result`, `nudge`, `guardrail_block` or `sandbox_reset` following an
    `assistant` event -- the harness or the sandboxed command was running in
    between). The slowest single tool call is the largest such gap ending on
    a `tool_result`.

    EXCLUDED: the final gap, between the last in-loop event and `run_end`,
    is charged to NEITHER bucket (review item 8). That gap is the runner's
    own finalize/export step after the agent loop is done -- computing
    `git diff --stat`, writing `diff.patch`, the Docker-mode export commit --
    not tool-call latency or model latency, and counting it as tool time
    inflated every run's tool-time share (and could make an otherwise-fast
    run's "slowest tool call" cell report the export step instead of an
    actual tool call)."""
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
            if gap > 0 and e.get("event") != "run_end":
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
                # A model can return a garbage tool name (devstral emitted the
                # previous result + "[TOOL_CALLS]bash" as the name); cap it so
                # one bad call cannot blow the cell up to kilobytes.
                tool = _truncate(tool, 30)
                counts[tool] = counts.get(tool, 0) + 1
    return " ".join(f"{t}:{n}" for t, n in sorted(counts.items()))


def harvest_run(label: str, run_dir: Path, driver_row: dict = None) -> dict:
    """One run dir's raw numbers, gathered once and shared by all three
    table-row builders below. `driver_row` (a row from a `soak_driver.py
    --out` file, when harvesting via `--driver-out`) supplies `final_message`
    -- see the comment on the `_harness_failures` call below for why that
    can't come from `run_dir` alone."""
    run_json = soak_common.read_run_json(run_dir)
    events = soak_common.read_transcript(run_dir)   # parsed once, shared below (item 5)
    run_end = _last_event(events, "run_end")
    usage = run_end.get("usage") or {}
    status = run_json.get("status") or run_end.get("status")
    turns = run_json.get("turns", run_end.get("turns"))
    wall_s = _wall_seconds(run_json, run_end)

    # events= (2026-08-23 review item 10) avoids a second parse of the same
    # transcript file -- `events` above already parsed it once (item 5).
    counts = bench._event_counts(None, events=events)
    # The runner's own `final_message` (what `_abort_kind` regex-matches for
    # a model_error run's "aborted after N consecutive X failures" text) is
    # NEVER persisted to run.json or the transcript -- it exists only in the
    # `dirtywork run` process's stdout JSON at run time (runner.py's
    # RunResult.final -> __main__._emit_result; docs/transcript-schema.md's
    # "## run.json" section says so explicitly: "the final message from the
    # finish call's summary, because run.json records neither"). `run_json`'s
    # `last_assistant_text` is the MODEL's own last reply, a different
    # string, and using it here was wrong (review item 1) -- harvesting a
    # bare run dir (no `--driver-out`) genuinely cannot recover this value,
    # so `abort=` in the Harness-failures cell is only ever populated when
    # `soak_driver.py`'s result row (which captures `final_message` off its
    # own subprocess's stdout) is available.
    final_message = driver_row.get("final_message") if driver_row else None
    harness = bench._harness_failures(counts, status, final_message,
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
        "stray_kills": counts.get("stray_kill", 0),
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


def _truncate(text, limit: int) -> str:
    """`text`'s whitespace collapsed to single spaces, then cut to at most
    `limit` chars (ellipsis included when it was cut). A misbehaving model
    can put its own huge raw text into a `tool_result.tool`/`args` field --
    seen for real on a devstral run, where `tool` held the PREVIOUS result's
    text plus a stray "[TOOL_CALLS]bash" -- and an uncapped 'slowest tool
    call' cell can reach several KB, wide enough to make the rendered table
    unreadable (2026-08-23 soak review)."""
    collapsed = " ".join(str(text).split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:max(limit - 3, 0)] + "..."


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
        "stray kills": h.get("stray_kills", 0), "tool mix": h.get("tool_mix") or "-",
    }


def _model_tool_row(h: dict) -> dict:
    mt = h["model_tool"]
    model_s, tool_s = mt["model_s"], mt["tool_s"]
    total = model_s + tool_s
    turns = h.get("turns")
    slowest = mt["slowest"]
    slowest_cell = (f"{slowest[0]:.0f}s {_truncate(slowest[1], 40)} {_truncate(slowest[2], 120)}"
                    if slowest else "-")
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

def _md_cell(value) -> str:
    """One cell's text, made safe to sit inside a `| ... |` Markdown row.
    Real data breaks a naive `str(value)` two ways: a `|` (e.g. the slowest
    tool call's raw `args` for `{"command": "pytest -q | tail"}`) would be
    read as a column separator and shift every cell after it, and an
    embedded `\\n`/`\\r` (a multi-line error string) would split the row
    across physical lines and corrupt the table. CSV mode is unaffected --
    it renders through `csv.writer`, which already quotes/escapes correctly
    for that format (review item 2)."""
    text = str(value).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return text.replace("|", "\\|")


def _markdown_table(columns, rows) -> str:
    lines = ["| " + " | ".join(columns) + " |",
             "|" + "|".join(["---"] * len(columns)) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(_md_cell(r.get(c, "-")) for c in columns) + " |")
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
