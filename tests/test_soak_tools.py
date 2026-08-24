"""Tests for tools/soak_common.py, tools/soak_driver.py and tools/soak_harvest.py
(issue #48, docs/superpowers/bench/2026-08-23-v1-soak-matrix.md).

tools/ is deliberately not a package (test_junit_summary.py's own note: adding
__init__.py would put these operator scripts into the installable surface),
so each module is loaded by path, same as test_junit_summary.py does for
tools/junit_summary.py.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def harvest():
    return _load("soak_harvest")


@pytest.fixture()
def driver():
    return _load("soak_driver")


@pytest.fixture()
def common():
    return _load("soak_common")


# --------------------------------------------------------------------------
# Synthetic run dir helper
# --------------------------------------------------------------------------

def _write_run(base: Path, slug: str, run_json: dict, events: list) -> Path:
    """A minimal run dir matching docs/transcript-schema.md: run.json plus a
    line-delimited transcript.jsonl."""
    run_dir = base / slug
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps(run_json), encoding="utf-8")
    text = "".join(json.dumps(e) + "\n" for e in events)
    (run_dir / "transcript.jsonl").write_text(text, encoding="utf-8")
    return run_dir


def _ts(n: int) -> str:
    """The n-th second past a fixed base instant, transcript `ts` shape."""
    return f"2026-08-23T10:00:{n:02d}Z"


BASE_RUN_JSON = {
    "status": "completed", "turns": 3, "model": "qwen/qwen3-coder-next",
    "provider": "openai", "started": "2026-08-23T10:00:00Z",
    "ended": "2026-08-23T10:00:10Z",
}


# --------------------------------------------------------------------------
# F1: apply_edits, >=3 hunks
# --------------------------------------------------------------------------

def test_f1_fires_on_apply_edits_with_three_or_more_hunks(harvest):
    # Result string captured empirically from the real `dirtywork.tools.apply_edits`
    # (see tools/soak_harvest.py's comment above _APPLY_EDITS_RE for the byte-for-byte
    # verification against dirtywork/tools.py -- this is that exact captured output,
    # not a guessed shape).
    events = [
        {"ts": _ts(0), "event": "run_start"},
        {"ts": _ts(1), "event": "assistant", "tool_calls": [{"name": "apply_edits"}]},
        {"ts": _ts(2), "event": "tool_result", "tool": "apply_edits",
         "result": "Applied 3 edits to f.py: +3 -3 (removed 3 non-blank lines)"},
        {"ts": _ts(3), "event": "run_end", "status": "completed"},
    ]
    fired = harvest.detect_features(BASE_RUN_JSON, events)
    assert "F1" in fired


def test_f1_does_not_fire_below_three_hunks(harvest):
    events = [
        {"ts": _ts(0), "event": "run_start"},
        {"ts": _ts(1), "event": "assistant", "tool_calls": [{"name": "apply_edits"}]},
        # empirically captured singular form: "1 edit", not "1 edits"
        {"ts": _ts(2), "event": "tool_result", "tool": "apply_edits",
         "result": "Applied 1 edit to h.py: +1 -1 (removed 1 non-blank line)"},
    ]
    assert "F1" not in harvest.detect_features(BASE_RUN_JSON, events)


def test_f1_does_not_fire_on_a_failed_apply_edits_call(harvest):
    # Real error shape from dirtywork.tools._apply_edits_once (dirtywork/tools.py:747-750):
    # every failure path returns "ERROR: edit N of M: ..." and never reaches the
    # "Applied ..." success line at all.
    events = [
        {"ts": _ts(0), "event": "tool_result", "tool": "apply_edits",
         "result": "ERROR: edit 2 of 3: old text occurs 0 times in ledger.py; it must "
                   "occur exactly once (after edits 1..1 are applied); no edits applied"},
    ]
    assert "F1" not in harvest.detect_features(BASE_RUN_JSON, events)


# --------------------------------------------------------------------------
# F2: stuck, with stuck_on.repeats appended
# --------------------------------------------------------------------------

def test_f2_fires_on_stuck_status_and_appends_repeats(harvest):
    run_json = {**BASE_RUN_JSON, "status": "stuck", "stuck_on": {"repeats": 4}}
    fired = harvest.detect_features(run_json, [])
    assert "F2(repeats=4)" in fired


def test_f2_does_not_fire_on_other_statuses(harvest):
    run_json = {**BASE_RUN_JSON, "status": "max_turns"}
    assert not any(f.startswith("F2") for f in harvest.detect_features(run_json, []))


# --------------------------------------------------------------------------
# F3: timed_out tool_result AND a "timeout" nudge, both required
# --------------------------------------------------------------------------

def test_f3_fires_only_with_both_timeout_signals(harvest):
    events = [
        {"ts": _ts(0), "event": "tool_result", "tool": "bash", "timed_out": True,
         "result": "TIMED OUT after 120s"},
        {"ts": _ts(1), "event": "nudge", "kind": "timeout", "turn": 1},
    ]
    assert "F3" in harvest.detect_features(BASE_RUN_JSON, events)


@pytest.mark.parametrize("events", [
    [{"ts": _ts(0), "event": "tool_result", "tool": "bash", "timed_out": True}],
    [{"ts": _ts(0), "event": "nudge", "kind": "timeout", "turn": 1}],
])
def test_f3_does_not_fire_with_only_one_signal(harvest, events):
    assert "F3" not in harvest.detect_features(BASE_RUN_JSON, events)


# --------------------------------------------------------------------------
# F4: run_end.verify non-null AND passed == True, with rounds appended.
# A verify_failed run (verify present, passed is not True) must not count as
# fired -- it renders as F4-failed(rounds=N) instead (2026-08-23 review).
# --------------------------------------------------------------------------

def test_f4_fires_on_passed_verify_and_appends_passed_rounds(harvest):
    run_json = {**BASE_RUN_JSON, "verify": {"command": "pytest", "exit_code": 0,
                                             "rounds": 2, "passed": True}}
    fired = harvest.detect_features(run_json, [])
    assert "F4(passed=True,rounds=2)" in fired
    assert not any(f.startswith("F4-failed") for f in fired)


def test_f4_does_not_fire_when_verify_is_null(harvest):
    run_json = {**BASE_RUN_JSON, "verify": None}
    assert not any(f.startswith("F4") for f in harvest.detect_features(run_json, []))


def test_f4_renders_as_failed_and_does_not_count_as_fired_when_verify_failed(harvest):
    run_json = {**BASE_RUN_JSON, "verify": {"command": "pytest", "exit_code": 1,
                                             "rounds": 3, "passed": False}}
    fired = harvest.detect_features(run_json, [])
    assert "F4-failed(rounds=3)" in fired
    # Not "fired": no entry that is F4 itself or that a prefix match on
    # "F4(" (the real firing shape) would pick up.
    assert not any(f.startswith("F4(") for f in fired)
    assert "F4" not in fired


# --------------------------------------------------------------------------
# F5: finish_reason "length" IMMEDIATELY followed by an actual truncation
# signal (a "truncated" nudge, or a tool_result starting with
# truncated_call_result's text), and AFTER that a successful append_file
# whose path matches either the truncated call's own path or any successful
# write_file's path anywhere in the run (2026-08-23 review: the old version
# remembered ANY earlier finish_reason=="length" forever and accepted ANY
# later append_file, with no path check and no requirement that the
# truncation was ever actually communicated back to the model).
# --------------------------------------------------------------------------

def test_f5_fires_when_append_after_truncation_targets_the_written_path(harvest):
    events = [
        {"ts": _ts(0), "event": "tool_result", "tool": "write_file",
         "args": '{"path": "fixtures/rows.csv", "text": "header\\n"}',
         "result": "Wrote 7 bytes to fixtures/rows.csv (new file, 1 line)"},
        {"ts": _ts(1), "event": "assistant", "text": "...", "finish_reason": "length"},
        {"ts": _ts(2), "event": "nudge", "kind": "truncated", "turn": 1},
        {"ts": _ts(3), "event": "assistant", "tool_calls": [{"name": "append_file"}]},
        {"ts": _ts(4), "event": "tool_result", "tool": "append_file",
         "args": '{"path": "fixtures/rows.csv", "text": "row1\\n"}',
         "result": "Appended to fixtures/rows.csv: +200 -0"},
    ]
    assert "F5" in harvest.detect_features(BASE_RUN_JSON, events)


def test_f5_fires_on_truncated_call_result_path_match(harvest):
    # Truncated TOOL CALL (not a text reply): the model's write_file call
    # itself got cut off, and the tool_result carries runner.py's
    # truncated_call_result text -- no "truncated" nudge at all in this
    # shape. The later append_file's path matches the path recovered from
    # THAT tool_result's own args, with no write_file success anywhere.
    events = [
        {"ts": _ts(0), "event": "assistant", "text": "", "finish_reason": "length",
         "tool_calls": [{"name": "write_file", "arguments": '{"path": "big.txt", "tex'}]},
        {"ts": _ts(1), "event": "tool_result", "tool": "write_file",
         "args": '{"path": "big.txt", "tex',
         "result": "ERROR: your write_file for 'big.txt' was cut off at the token limit "
                   "— nothing was written. Write the file in chunks: write_file with "
                   "the first part, then append_file for each following part."},
        {"ts": _ts(2), "event": "assistant", "tool_calls": [{"name": "append_file"}]},
        {"ts": _ts(3), "event": "tool_result", "tool": "append_file",
         "args": '{"path": "big.txt", "text": "rest"}',
         "result": "Appended to big.txt: +50 -0"},
    ]
    assert "F5" in harvest.detect_features(BASE_RUN_JSON, events)


def test_f5_does_not_fire_without_a_later_append_file(harvest):
    events = [
        {"ts": _ts(0), "event": "assistant", "text": "...", "finish_reason": "length"},
        {"ts": _ts(1), "event": "tool_result", "tool": "write_file",
         "args": '{"path": "fixtures/rows.csv", "text": "..."}',
         "result": "Wrote 100 bytes to fixtures/rows.csv (new file, 5 lines)"},
    ]
    assert "F5" not in harvest.detect_features(BASE_RUN_JSON, events)


def test_f5_does_not_fire_on_append_file_before_any_truncation(harvest):
    # All appends precede the truncation, none after (the real
    # F5-trunc2048-dev-r1 run's shape: 38 appends before, none after).
    events = [
        {"ts": _ts(0), "event": "tool_result", "tool": "write_file",
         "args": '{"path": "fixtures/rows.csv", "text": "header\\n"}',
         "result": "Wrote 7 bytes to fixtures/rows.csv (new file, 1 line)"},
        {"ts": _ts(1), "event": "tool_result", "tool": "append_file",
         "args": '{"path": "fixtures/rows.csv", "text": "row1\\n"}',
         "result": "Appended to fixtures/rows.csv: +200 -0"},
        {"ts": _ts(2), "event": "assistant", "text": "...", "finish_reason": "length"},
        {"ts": _ts(3), "event": "nudge", "kind": "truncated", "turn": 1},
    ]
    assert "F5" not in harvest.detect_features(BASE_RUN_JSON, events)


def test_f5_does_not_fire_when_append_after_truncation_targets_an_unrelated_path(harvest):
    events = [
        {"ts": _ts(0), "event": "tool_result", "tool": "write_file",
         "args": '{"path": "fixtures/rows.csv", "text": "header\\n"}',
         "result": "Wrote 7 bytes to fixtures/rows.csv (new file, 1 line)"},
        {"ts": _ts(1), "event": "assistant", "text": "...", "finish_reason": "length"},
        {"ts": _ts(2), "event": "nudge", "kind": "truncated", "turn": 1},
        {"ts": _ts(3), "event": "assistant", "tool_calls": [{"name": "append_file"}]},
        {"ts": _ts(4), "event": "tool_result", "tool": "append_file",
         "args": '{"path": "unrelated/other.txt", "text": "row1\\n"}',
         "result": "Appended to unrelated/other.txt: +200 -0"},
    ]
    assert "F5" not in harvest.detect_features(BASE_RUN_JSON, events)


# --------------------------------------------------------------------------
# F5 round 2 (2026-08-23 adversarial re-review): the truncation marker can be
# the 2nd/3rd tool_result of an assistant(length) turn, not just the 1st
# (runner.py:704-817: one assistant event, then one tool_result per
# malformed entry (tool="", :741-749), then one tool_result per real call
# attempted that turn, in order, :754-817) -- and tool_result.args is capped
# at 500 chars (runner.py:810) so a long "text" value ahead of "path" in the
# call's own JSON key order can push the path past that cap.
# --------------------------------------------------------------------------

def test_f5_fires_when_the_truncated_call_is_the_second_tool_result_in_its_turn(harvest):
    # Same turn: write_file completes fine, THEN append_file gets cut off --
    # the truncation marker is the 2nd tool_result after assistant(length),
    # not the 1st (the old "immediate next event" rule would have missed
    # this entirely).
    events = [
        {"ts": _ts(0), "event": "assistant", "text": "", "finish_reason": "length",
         "tool_calls": [
             {"name": "write_file",
              "arguments": '{"path": "fixtures/rows.csv", "text": "header\\n"}'},
             {"name": "append_file", "arguments": '{"path": "fixtures/rows.csv", "tex'},
         ]},
        {"ts": _ts(1), "event": "tool_result", "tool": "write_file",
         "args": '{"path": "fixtures/rows.csv", "text": "header\\n"}',
         "result": "Wrote 7 bytes to fixtures/rows.csv (new file, 1 line)"},
        {"ts": _ts(2), "event": "tool_result", "tool": "append_file",
         "args": '{"path": "fixtures/rows.csv", "tex',
         "result": "ERROR: your append_file call was cut off at the token limit before "
                   "it completed. Emit smaller tool calls — for a large file, write_file "
                   "the first part and append_file the rest."},
        {"ts": _ts(3), "event": "assistant", "tool_calls": [{"name": "append_file"}]},
        {"ts": _ts(4), "event": "tool_result", "tool": "append_file",
         "args": '{"path": "fixtures/rows.csv", "text": "row1\\n"}',
         "result": "Appended to fixtures/rows.csv: +200 -0"},
    ]
    assert "F5" in harvest.detect_features(BASE_RUN_JSON, events)


def test_f5_fires_when_a_malformed_entry_precedes_the_truncated_call(harvest):
    # A malformed tool-call entry (tool="", no `id`) gets its own tool_result
    # BEFORE the real named calls' tool_results in the same turn -- the
    # truncation marker is still found by scanning past it.
    events = [
        {"ts": _ts(0), "event": "assistant", "text": "", "finish_reason": "length",
         "tool_calls": [{"name": "write_file", "arguments": '{"path": "big.txt", "tex'}]},
        {"ts": _ts(1), "event": "tool_result", "tool": "", "args": "",
         "result": "ERROR: could not parse tool call arguments"},
        {"ts": _ts(2), "event": "tool_result", "tool": "write_file",
         "args": '{"path": "big.txt", "tex',
         "result": "ERROR: your write_file for 'big.txt' was cut off at the token limit "
                   "— nothing was written. Write the file in chunks: write_file with "
                   "the first part, then append_file for each following part."},
        {"ts": _ts(3), "event": "assistant", "tool_calls": [{"name": "append_file"}]},
        {"ts": _ts(4), "event": "tool_result", "tool": "append_file",
         "args": '{"path": "big.txt", "text": "rest"}',
         "result": "Appended to big.txt: +50 -0"},
    ]
    assert "F5" in harvest.detect_features(BASE_RUN_JSON, events)


def test_f5_does_not_fire_when_a_truncated_marker_only_appears_in_a_later_turn(harvest):
    # The finish_reason=="length" turn's OWN results (just the one
    # write_file tool_result) carry no truncation marker at all -- the call
    # simply finished despite hitting the length limit. A "truncated" nudge
    # shows up later, but in a SEPARATE, later turn; it must not
    # retroactively satisfy the earlier length event's signal requirement.
    events = [
        {"ts": _ts(0), "event": "assistant", "text": "", "finish_reason": "length",
         "tool_calls": [{"name": "write_file",
                          "arguments": '{"path": "fixtures/rows.csv", "text": "header\\n"}'}]},
        {"ts": _ts(1), "event": "tool_result", "tool": "write_file",
         "args": '{"path": "fixtures/rows.csv", "text": "header\\n"}',
         "result": "Wrote 7 bytes to fixtures/rows.csv (new file, 1 line)"},
        {"ts": _ts(2), "event": "assistant", "tool_calls": [{"name": "bash"}]},
        {"ts": _ts(3), "event": "nudge", "kind": "truncated", "turn": 2},
        {"ts": _ts(4), "event": "assistant", "tool_calls": [{"name": "append_file"}]},
        {"ts": _ts(5), "event": "tool_result", "tool": "append_file",
         "args": '{"path": "fixtures/rows.csv", "text": "row1\\n"}',
         "result": "Appended to fixtures/rows.csv: +200 -0"},
    ]
    assert "F5" not in harvest.detect_features(BASE_RUN_JSON, events)


def test_f5_fires_using_result_text_path_when_args_cap_truncates_it(harvest):
    # A 600-char "text" value ahead of "path" in the call's own JSON key
    # order means args[:500] (runner.py:810) never contains "path" at all --
    # _recovered_path(args) alone would find nothing for either event. The
    # path must come from the RESULT text instead.
    long_text = "x" * 600
    capped_args = ('{"text": "' + long_text + '", "path": "fixtures/rows.csv"}')[:500]
    assert '"path"' not in capped_args   # sanity: the cap really did eat the key
    events = [
        {"ts": _ts(0), "event": "tool_result", "tool": "write_file",
         "args": capped_args,
         "result": "Wrote 22446 bytes to fixtures/rows.csv (new file, 401 lines)"},
        {"ts": _ts(1), "event": "assistant", "text": "...", "finish_reason": "length"},
        {"ts": _ts(2), "event": "nudge", "kind": "truncated", "turn": 1},
        {"ts": _ts(3), "event": "assistant", "tool_calls": [{"name": "append_file"}]},
        {"ts": _ts(4), "event": "tool_result", "tool": "append_file",
         "args": capped_args,
         "result": "Appended to fixtures/rows.csv: +100 -0"},
    ]
    assert "F5" in harvest.detect_features(BASE_RUN_JSON, events)


# --------------------------------------------------------------------------
# F7 / F8 / F9 -- quick coverage, run.json-only signals
# --------------------------------------------------------------------------

def test_f7_fires_on_ollama_provider(harvest):
    run_json = {**BASE_RUN_JSON, "provider": "ollama"}
    assert "F7" in harvest.detect_features(run_json, [])


def test_f8_fires_on_stall_nudge_or_stalled_status(harvest):
    events = [{"ts": _ts(0), "event": "nudge", "kind": "stall", "turn": 1}]
    assert "F8" in harvest.detect_features(BASE_RUN_JSON, events)
    run_json = {**BASE_RUN_JSON, "status": "stalled"}
    assert "F8" in harvest.detect_features(run_json, [])


def test_f9_fires_on_pinned_image(harvest):
    run_json = {**BASE_RUN_JSON, "image_pinned": True}
    assert "F9" in harvest.detect_features(run_json, [])
    run_json = {**BASE_RUN_JSON, "image_pinned": False}
    assert "F9" not in harvest.detect_features(run_json, [])


# --------------------------------------------------------------------------
# model_tool_time: timestamp-gap split
# --------------------------------------------------------------------------

def test_model_tool_time_splits_gaps_by_which_event_ends_them(harvest):
    events = [
        {"ts": _ts(0), "event": "run_start"},
        {"ts": _ts(5), "event": "assistant"},       # 5s of model time
        {"ts": _ts(8), "event": "tool_result", "tool": "bash", "args": '{"command":"ls"}'},
        {"ts": _ts(10), "event": "assistant"},       # 2s of model time
        {"ts": _ts(11), "event": "run_end"},         # trailing 1s gap excluded (item 8, below)
    ]
    mt = harvest.model_tool_time(events)
    assert mt["model_s"] == pytest.approx(7.0)
    assert mt["tool_s"] == pytest.approx(3.0)
    assert mt["slowest"][0] == pytest.approx(3.0)
    assert mt["slowest"][1] == "bash"


def test_model_tool_time_excludes_the_trailing_run_end_gap(harvest):
    # The gap between the last real event and run_end is the runner's own
    # finalize/export wait (git diff/patch, Docker export), not tool-call or
    # model latency -- it must be charged to NEITHER bucket, and must not
    # win "slowest tool call" (review item 8).
    events = [
        {"ts": _ts(0), "event": "assistant"},
        {"ts": _ts(2), "event": "tool_result", "tool": "bash", "args": "{}"},
        {"ts": _ts(300), "event": "run_end"},   # 298s finalize/export wait
    ]
    mt = harvest.model_tool_time(events)
    assert mt["tool_s"] == pytest.approx(2.0)    # only the real tool_result gap
    assert mt["model_s"] == pytest.approx(0.0)
    assert mt["slowest"][0] == pytest.approx(2.0)


# --------------------------------------------------------------------------
# follow-up: 'slowest tool call' cell is capped and whitespace-collapsed
# (a devstral run put its own huge raw text into tool_result.tool/args)
# --------------------------------------------------------------------------

def test_truncate_collapses_whitespace_and_caps_length(harvest):
    assert harvest._truncate("a  b\nc\r\nd", 100) == "a b c d"
    result = harvest._truncate("x" * 50, 10)
    assert result == "xxxxxxx..."
    assert len(result) == 10


def test_truncate_leaves_short_text_alone(harvest):
    assert harvest._truncate("short", 40) == "short"


def test_slowest_tool_call_cell_is_capped_and_whitespace_collapsed(harvest):
    # Simulates the real devstral finding: tool holds ~200 chars of runaway
    # text (e.g. the previous result plus a stray "[TOOL_CALLS]bash"), args
    # holds a multi-line blob well past 120 chars.
    long_tool = "resultofpreviouscall[TOOL_CALLS]" + ("bash" * 40)
    long_args = "line one\n\nline   two\r\n" + ("z" * 300)
    events = [
        {"ts": _ts(0), "event": "assistant"},
        {"ts": _ts(5), "event": "tool_result", "tool": long_tool, "args": long_args},
        {"ts": _ts(6), "event": "run_end"},
    ]
    row = harvest._model_tool_row({
        "label": "r", "status": "completed", "turns": 1, "wall_s": 6.0,
        "prompt_tok": 10, "compl_tok": 5, "model_tool": harvest.model_tool_time(events),
    })
    cell = row["slowest tool call"]
    assert cell.startswith("5s ")
    tool_part, args_part = cell[len("5s "):].split(" ", 1)
    assert len(tool_part) == 40 and tool_part.endswith("...")
    assert len(args_part) == 120 and args_part.endswith("...")
    assert "\n" not in cell and "\r" not in cell and "  " not in cell


# --------------------------------------------------------------------------
# review item 4: _last_event is dirtywork.runs's, not a local copy
# --------------------------------------------------------------------------

def test_last_event_is_reused_from_dirtywork_runs(harvest):
    from dirtywork import runs
    assert harvest._last_event is runs._last_event


# --------------------------------------------------------------------------
# review items 5 & 10: transcript parsed once; harvest_run passes the
# already-parsed events into dirtywork.bench._event_counts(events=...)
# instead of re-reading transcript.jsonl (no local duplicate left in
# soak_harvest.py at all once bench.py grew the events= param)
# --------------------------------------------------------------------------

def test_harvest_run_passes_events_into_bench_event_counts(tmp_path, harvest, monkeypatch):
    events = [
        {"ts": _ts(0), "event": "nudge", "kind": "stall"},
        {"ts": _ts(1), "event": "guardrail_block"},
    ]
    run_dir = _write_run(tmp_path, "run-ec", BASE_RUN_JSON, events)

    calls = []
    real = harvest.bench._event_counts

    def spy(run_dir_arg, *, events=None):
        calls.append((run_dir_arg, events))
        return real(run_dir_arg, events=events)

    monkeypatch.setattr(harvest.bench, "_event_counts", spy)
    h = harvest.harvest_run("run-ec", run_dir)
    assert calls == [(None, events)]        # run_dir arg unused; events passed instead
    assert h["nudges"] == 1
    assert h["guardrail_blocks"] == 1


# --------------------------------------------------------------------------
# review item 1: final_message (abort kind) comes from the driver row, not
# run_json['last_assistant_text']
# --------------------------------------------------------------------------

def test_harvest_run_reads_abort_kind_from_driver_rows_final_message(tmp_path, harvest):
    run_json = {**BASE_RUN_JSON, "status": "model_error", "last_assistant_text": "I give up."}
    run_dir = _write_run(tmp_path, "run-abort", run_json, [])
    driver_row = {"final_message": "aborted after 3 consecutive empty_reply failures"}
    h = harvest.harvest_run("run-abort", run_dir, driver_row)
    assert "abort=empty_reply" in harvest.bench._failure_cell(h["harness"])


def test_harvest_run_without_a_driver_row_does_not_fabricate_abort_kind(tmp_path, harvest):
    # Before the fix, last_assistant_text stood in for final_message -- wrong,
    # since it is the MODEL's text, not the runner's. Even when it happens to
    # look like an abort message, it must NOT be read as one.
    run_json = {**BASE_RUN_JSON, "status": "model_error",
                "last_assistant_text": "aborted after 3 consecutive empty_reply failures"}
    run_dir = _write_run(tmp_path, "run-abort2", run_json, [])
    h = harvest.harvest_run("run-abort2", run_dir, None)
    assert "abort=" not in harvest.bench._failure_cell(h["harness"])


# --------------------------------------------------------------------------
# review item 2: _markdown_table escapes '|' and collapses embedded newlines
# --------------------------------------------------------------------------

def test_markdown_table_escapes_pipe_and_collapses_newlines(harvest):
    rows = [{"a": "pytest -q | tail", "b": "line1\nline2\r\nline3"}]
    text = harvest._markdown_table(("a", "b"), rows)
    lines = text.splitlines()
    assert len(lines) == 3   # header + separator + exactly one data row
    assert lines[2] == "| pytest -q \\| tail | line1 line2 line3 |"


def test_harvest_main_markdown_survives_a_piped_command_in_slowest_call(tmp_path, harvest, capsys):
    events = [
        {"ts": _ts(0), "event": "assistant"},
        {"ts": _ts(5), "event": "tool_result", "tool": "bash",
         "args": '{"command":"pytest -q | tail -20"}'},
        {"ts": _ts(6), "event": "run_end", "status": "completed",
         "usage": {"prompt_tokens": 10, "completion_tokens": 5}, "turns": 2},
    ]
    run_dir = _write_run(tmp_path, "run-pipe", BASE_RUN_JSON, events)
    assert harvest.main([str(run_dir)]) == 0
    out = capsys.readouterr().out
    # every physical line of the "Model vs tool time" table must be a
    # complete, unbroken markdown row -- the embedded '|' in the bash
    # command must not have split it into two
    mt_lines = [l for l in out.split("## Model vs tool time\n", 1)[1].strip().splitlines()]
    assert mt_lines   # sanity: the table actually printed something
    for line in mt_lines:
        assert line.startswith("|") and line.endswith("|")


# --------------------------------------------------------------------------
# harvest main(): a full run dir end to end
# --------------------------------------------------------------------------

def test_harvest_main_prints_feature_fired_column(tmp_path, harvest, capsys):
    events = [
        {"ts": _ts(0), "event": "run_start"},
        {"ts": _ts(1), "event": "assistant", "tool_calls": [{"name": "apply_edits"}]},
        {"ts": _ts(3), "event": "tool_result", "tool": "apply_edits",
         "result": "Applied 4 edits to ledger.py: +20 -8"},
        {"ts": _ts(4), "event": "run_end", "status": "completed",
         "usage": {"prompt_tokens": 1000, "completion_tokens": 50}, "turns": 3},
    ]
    run_dir = _write_run(tmp_path, "run-1", BASE_RUN_JSON, events)
    rc = harvest.main([str(run_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "## Main" in out
    assert "Feature fired" in out
    assert "F1" in out
    assert "## Per-run metrics (auto)" in out
    assert "## Model vs tool time" in out


def test_harvest_main_requires_at_least_one_source(harvest, capsys):
    assert harvest.main([]) == 2
    assert "RUN_DIR" in capsys.readouterr().err


def test_harvest_csv_mode_emits_csv_headers(tmp_path, harvest, capsys):
    run_dir = _write_run(tmp_path, "run-1", BASE_RUN_JSON, [])
    assert harvest.main([str(run_dir), "--csv"]) == 0
    out = capsys.readouterr().out
    assert "Run,Model,Status" in out


# --------------------------------------------------------------------------
# driver: task resolution + argv shape
# --------------------------------------------------------------------------

@pytest.fixture()
def bench_task_dir(tmp_path):
    task_dir = tmp_path / "sometask"
    task_dir.mkdir()
    (task_dir / "bench.json").write_text(
        json.dumps({"task": "say hi in greeting.txt"}), encoding="utf-8")
    return task_dir


def test_resolve_task_source_reads_bench_json(driver, bench_task_dir):
    source_dir, bench_data = driver._resolve_task_source(str(bench_task_dir))
    assert source_dir == bench_task_dir
    assert bench_data["task"] == "say hi in greeting.txt"


def test_resolve_task_source_raises_without_bench_json(driver, tmp_path):
    empty = tmp_path / "no-bench-json"
    empty.mkdir()
    with pytest.raises(ValueError, match="no bench.json"):
        driver._resolve_task_source(str(empty))


def test_build_argv_shape(driver):
    argv = driver._build_argv("qwen/qwen3-coder-next", "openai", "http://localhost:1234/v1",
                              "do the thing", "/tmp/repo", ["--verify", "pytest"])
    assert argv[:6] == ["run", "do the thing", "--repo", "/tmp/repo",
                        "--model", "qwen/qwen3-coder-next"]
    assert "--sandbox" in argv and "docker" in argv
    assert "--keep-volume" in argv
    assert argv[-2:] == ["--verify", "pytest"]
    assert "--provider" in argv and "openai" in argv
    assert "--base-url" in argv and "http://localhost:1234/v1" in argv


# --------------------------------------------------------------------------
# driver: _resolve_row -- the two plan-row shapes ('task' vs 'repo') and
# their mutual exclusion
# --------------------------------------------------------------------------

def test_resolve_row_task_shape_stages(driver, bench_task_dir):
    resolved = driver._resolve_row({"task": str(bench_task_dir)})
    assert resolved["stage"] is True
    assert resolved["repo_path"] is None
    assert resolved["source_dir"] == bench_task_dir
    assert resolved["task_text"] == "say hi in greeting.txt"
    assert resolved["bench_data"] is not None


def test_resolve_row_repo_shape_with_task_text_does_not_stage(driver, tmp_path):
    repo = tmp_path / "invoicr"
    repo.mkdir()
    resolved = driver._resolve_row({"repo": str(repo), "task_text": "fix billing model"})
    assert resolved["stage"] is False
    assert resolved["repo_path"] == repo
    assert resolved["source_dir"] is None
    assert resolved["task_text"] == "fix billing model"
    assert resolved["bench_data"] is None
    assert resolved["acceptance_dir"] is None


def test_resolve_row_repo_shape_with_task_file(driver, tmp_path):
    repo = tmp_path / "invoicr"
    repo.mkdir()
    task_file = tmp_path / "task.txt"
    task_file.write_text("do the invoicr thing\n", encoding="utf-8")
    resolved = driver._resolve_row({"repo": str(repo), "task_file": str(task_file)})
    assert resolved["task_text"] == "do the invoicr thing"   # trailing newline stripped


def test_resolve_row_rejects_both_task_and_repo(driver, tmp_path, bench_task_dir):
    with pytest.raises(ValueError, match="exactly one of 'task'"):
        driver._resolve_row({"task": str(bench_task_dir), "repo": str(tmp_path)})


def test_resolve_row_rejects_neither_task_nor_repo(driver):
    with pytest.raises(ValueError, match="exactly one of 'task'"):
        driver._resolve_row({"model": "qwen/qwen3-coder-next"})


def test_resolve_row_rejects_relative_repo_path(driver):
    with pytest.raises(ValueError, match="must be an absolute path"):
        driver._resolve_row({"repo": "relative/path", "task_text": "x"})


def test_resolve_row_rejects_missing_repo_dir(driver, tmp_path):
    with pytest.raises(ValueError, match="no such repo directory"):
        driver._resolve_row({"repo": str(tmp_path / "nope"), "task_text": "x"})


def test_resolve_task_text_rejects_both_task_text_and_task_file(driver, tmp_path):
    with pytest.raises(ValueError, match="exactly one of 'task_text' or 'task_file'"):
        driver._resolve_task_text({"task_text": "x", "task_file": str(tmp_path / "t.txt")})


def test_resolve_task_text_rejects_neither(driver):
    with pytest.raises(ValueError, match="exactly one of 'task_text' or 'task_file'"):
        driver._resolve_task_text({})


# --------------------------------------------------------------------------
# driver: resumability + --dry-run
# --------------------------------------------------------------------------

def _plan_row(label, task_dir):
    return {"task": str(task_dir), "model": "qwen/qwen3-coder-next", "provider": "openai",
            "base_url": "http://localhost:1234/v1", "flags": [], "label": label}


def _repo_plan_row(label, repo_dir, task_text):
    return {"repo": str(repo_dir), "task_text": task_text, "model": "qwen/qwen3-coder-next",
            "provider": "openai", "base_url": "http://localhost:1234/v1",
            "flags": [], "label": label}


def test_dry_run_prints_a_command_per_row(tmp_path, driver, bench_task_dir, capsys):
    plan = tmp_path / "plan.jsonl"
    plan.write_text(json.dumps(_plan_row("row-a", bench_task_dir)) + "\n", encoding="utf-8")
    out_path = tmp_path / "out.jsonl"
    rc = driver.main([str(plan), "--out", str(out_path), "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "row-a" in out
    assert "dirtywork" in out
    assert "say hi in greeting.txt" in out
    assert not out_path.exists()   # dry-run touches nothing on disk


def test_dry_run_skips_rows_whose_label_is_already_in_the_out_file(
        tmp_path, driver, bench_task_dir, capsys):
    plan = tmp_path / "plan.jsonl"
    plan.write_text(
        json.dumps(_plan_row("row-a", bench_task_dir)) + "\n"
        + json.dumps(_plan_row("row-b", bench_task_dir)) + "\n",
        encoding="utf-8")
    out_path = tmp_path / "out.jsonl"
    out_path.write_text(json.dumps({"label": "row-a", "status": "completed"}) + "\n",
                        encoding="utf-8")

    rc = driver.main([str(plan), "--out", str(out_path), "--dry-run"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "row-b" in captured.out
    assert "row-a" not in captured.out            # not re-printed as a command...
    assert "skip row-a" in captured.err            # ...it was skipped instead


def test_main_errors_on_missing_plan_file(tmp_path, driver, capsys):
    missing = tmp_path / "nope.jsonl"
    assert driver.main([str(missing)]) == 2
    assert "no such plan file" in capsys.readouterr().err


def test_dry_run_prints_a_command_for_a_repo_row(tmp_path, driver, capsys):
    repo = tmp_path / "invoicr"
    repo.mkdir()
    plan = tmp_path / "plan.jsonl"
    plan.write_text(json.dumps(_repo_plan_row("D-invoicr-94", repo,
                                              "billing data model + migration")) + "\n",
                    encoding="utf-8")
    out_path = tmp_path / "out.jsonl"
    rc = driver.main([str(plan), "--out", str(out_path), "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "D-invoicr-94" in out
    assert "billing data model + migration" in out
    # a repo row is run IN PLACE, never staged -- the real path appears verbatim,
    # not a "<fresh copy of ...>" placeholder
    assert str(repo) in out
    assert "fresh copy" not in out


def test_dry_run_reports_mutual_exclusion_error_inline(tmp_path, driver, bench_task_dir, capsys):
    plan = tmp_path / "plan.jsonl"
    bad_row = {"task": str(bench_task_dir), "repo": str(tmp_path), "label": "bad-row",
               "model": "qwen/qwen3-coder-next"}
    plan.write_text(json.dumps(bad_row) + "\n", encoding="utf-8")
    out_path = tmp_path / "out.jsonl"
    rc = driver.main([str(plan), "--out", str(out_path), "--dry-run"])
    assert rc == 0                                  # a bad row is reported, not fatal
    out = capsys.readouterr().out
    assert "bad-row: ERROR resolving task:" in out
    assert "exactly one of 'task'" in out


# --------------------------------------------------------------------------
# review item 6: _run_one cleans up a staged repo dir even when interrupted
# --------------------------------------------------------------------------

def test_run_one_cleans_up_staged_repo_dir_on_keyboard_interrupt(driver, bench_task_dir, monkeypatch):
    # KeyboardInterrupt is a BaseException, not an Exception -- a bare
    # `except Exception` around the subprocess call (the previous shape of
    # this function) does not catch it, and would leak the staged temp dir.
    # `_run_one` must still remove it via an outer try/finally.
    staged = {}
    real_stage_repo = driver._stage_repo

    def spy_stage_repo(source_dir, tag):
        d = real_stage_repo(source_dir, tag)
        staged["dir"] = d
        return d

    def boom(argv):
        raise KeyboardInterrupt()

    monkeypatch.setattr(driver, "_stage_repo", spy_stage_repo)
    monkeypatch.setattr(driver, "_invoke_dirtywork", boom)

    row = _plan_row("row-a", bench_task_dir)
    with pytest.raises(KeyboardInterrupt):
        driver._run_one(row)
    assert "dir" in staged                 # staging did happen...
    assert not staged["dir"].exists()      # ...but was cleaned up despite the interrupt


def test_run_one_cleans_up_staged_repo_dir_on_ordinary_exception(driver, bench_task_dir, monkeypatch):
    staged = {}
    real_stage_repo = driver._stage_repo

    def spy_stage_repo(source_dir, tag):
        d = real_stage_repo(source_dir, tag)
        staged["dir"] = d
        return d

    def boom(argv):
        raise RuntimeError("simulated subprocess crash")

    monkeypatch.setattr(driver, "_stage_repo", spy_stage_repo)
    monkeypatch.setattr(driver, "_invoke_dirtywork", boom)

    row = _plan_row("row-a", bench_task_dir)
    result = driver._run_one(row)
    assert result["error"] == "subprocess failed: simulated subprocess crash"
    assert result["status"] is None        # never ran -- must stay resumable (item 7 below)
    assert not staged["dir"].exists()


# --------------------------------------------------------------------------
# review item 7: resume-by-label only counts rows with a real status;
# duplicate labels within one plan are refused
# --------------------------------------------------------------------------

def test_existing_labels_ignores_rows_whose_spawn_never_produced_a_status(tmp_path, driver):
    out_path = tmp_path / "out.jsonl"
    out_path.write_text(
        json.dumps({"label": "spawn-failed", "status": None, "error": "no bench.json"}) + "\n"
        + json.dumps({"label": "ran-ok", "status": "completed"}) + "\n"
        + json.dumps({"label": "also-ran", "status": "max_turns"}) + "\n",
        encoding="utf-8")
    assert driver._existing_labels(out_path) == {"ran-ok", "also-ran"}


def test_dry_run_retries_a_label_whose_prior_row_never_got_a_status(
        tmp_path, driver, bench_task_dir, capsys):
    plan = tmp_path / "plan.jsonl"
    plan.write_text(json.dumps(_plan_row("row-a", bench_task_dir)) + "\n", encoding="utf-8")
    out_path = tmp_path / "out.jsonl"
    out_path.write_text(
        json.dumps({"label": "row-a", "status": None, "error": "boom"}) + "\n", encoding="utf-8")

    rc = driver.main([str(plan), "--out", str(out_path), "--dry-run"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "row-a" in captured.out          # re-attempted, not treated as done
    assert "skip row-a" not in captured.err


def test_duplicate_labels_detects_repeats_and_ignores_unlabeled_rows(driver):
    rows = [{"label": "a"}, {"label": "b"}, {"label": "a"}, {"label": None}, {}]
    assert driver._duplicate_labels(rows) == ["a"]


def test_main_refuses_a_plan_with_duplicate_labels(tmp_path, driver, bench_task_dir, capsys):
    plan = tmp_path / "plan.jsonl"
    plan.write_text(
        json.dumps(_plan_row("dup", bench_task_dir)) + "\n"
        + json.dumps(_plan_row("dup", bench_task_dir)) + "\n",
        encoding="utf-8")
    rc = driver.main([str(plan), "--dry-run"])
    assert rc == 2
    assert "duplicate labels: dup" in capsys.readouterr().err


# --------------------------------------------------------------------------
# soak_common
# --------------------------------------------------------------------------

def test_load_jsonl_skips_blank_and_malformed_lines(tmp_path, common):
    path = tmp_path / "rows.jsonl"
    path.write_text('{"a": 1}\n\nnot json\n{"a": 2}\n', encoding="utf-8")
    rows = common.load_jsonl(path)
    assert rows == [{"a": 1}, {"a": 2}]


def test_load_jsonl_missing_file_returns_empty_list(tmp_path, common):
    assert common.load_jsonl(tmp_path / "nope.jsonl") == []


def test_read_run_json_missing_returns_empty_dict(tmp_path, common):
    assert common.read_run_json(tmp_path / "no-such-run") == {}


def test_read_transcript_delegates_to_dirtywork_runs(tmp_path, common):
    run_dir = tmp_path / "run-x"
    run_dir.mkdir()
    (run_dir / "transcript.jsonl").write_text(
        '{"event": "run_start"}\nnot json\n{"event": "run_end"}\n', encoding="utf-8")
    assert common.read_transcript(run_dir) == [{"event": "run_start"}, {"event": "run_end"}]


def test_read_transcript_missing_returns_empty_list(tmp_path, common):
    assert common.read_transcript(tmp_path / "no-such-run") == []


def test_soak_common_no_longer_re_exports_unused_constants(common):
    # review item 3: RUNS_DIR/BENCH_HOME were dead re-exports of
    # dirtywork.rundir that nothing in tools/ ever read.
    assert not hasattr(common, "RUNS_DIR")
    assert not hasattr(common, "BENCH_HOME")


def test_load_jsonl_and_read_transcript_delegate_to_the_package(common):
    # review item 3 (DRY): these must be thin wrappers that CALL
    # dirtywork.bench._load_results / dirtywork.runs.read_transcript_events,
    # not re-implementations of their parsing loops -- checked at the source
    # level since the behavioral tests above can't tell "reused" from
    # "copied and happens to match".
    import inspect
    assert "bench._load_results" in inspect.getsource(common.load_jsonl)
    assert "runs.read_transcript_events" in inspect.getsource(common.read_transcript)


def test_detect_features_f10_feedback_resume():
    from soak_harvest import detect_features
    assert "F10" in detect_features({"feedback": "reviewer: continue"}, [])
    assert "F10" not in detect_features({"feedback": None}, [])
