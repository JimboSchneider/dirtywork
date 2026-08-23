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
# F4: run_end.verify non-null, with passed/rounds appended
# --------------------------------------------------------------------------

def test_f4_fires_on_non_null_verify_and_appends_passed_rounds(harvest):
    run_json = {**BASE_RUN_JSON, "verify": {"command": "pytest", "exit_code": 0,
                                             "rounds": 2, "passed": True}}
    fired = harvest.detect_features(run_json, [])
    assert "F4(passed=True,rounds=2)" in fired


def test_f4_does_not_fire_when_verify_is_null(harvest):
    run_json = {**BASE_RUN_JSON, "verify": None}
    assert not any(f.startswith("F4") for f in harvest.detect_features(run_json, []))


# --------------------------------------------------------------------------
# F5: finish_reason "length" followed later by a successful append_file
# --------------------------------------------------------------------------

def test_f5_fires_on_truncation_then_later_successful_append_file(harvest):
    events = [
        {"ts": _ts(0), "event": "assistant", "text": "...", "finish_reason": "length"},
        {"ts": _ts(1), "event": "nudge", "kind": "truncated", "turn": 1},
        {"ts": _ts(2), "event": "assistant", "tool_calls": [{"name": "append_file"}]},
        {"ts": _ts(3), "event": "tool_result", "tool": "append_file",
         "result": "Appended to fixtures/rows.csv: +200 -0"},
    ]
    assert "F5" in harvest.detect_features(BASE_RUN_JSON, events)


def test_f5_does_not_fire_without_a_later_append_file(harvest):
    events = [
        {"ts": _ts(0), "event": "assistant", "text": "...", "finish_reason": "length"},
        {"ts": _ts(1), "event": "tool_result", "tool": "write_file",
         "result": "Wrote 100 bytes to fixtures/rows.csv (new file, 5 lines)"},
    ]
    assert "F5" not in harvest.detect_features(BASE_RUN_JSON, events)


def test_f5_does_not_fire_on_append_file_before_any_truncation(harvest):
    events = [
        {"ts": _ts(0), "event": "tool_result", "tool": "append_file",
         "result": "Appended to fixtures/rows.csv: +200 -0"},
        {"ts": _ts(1), "event": "assistant", "text": "...", "finish_reason": "length"},
    ]
    assert "F5" not in harvest.detect_features(BASE_RUN_JSON, events)


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
        {"ts": _ts(11), "event": "run_end"},
    ]
    mt = harvest.model_tool_time(events)
    assert mt["model_s"] == pytest.approx(7.0)
    assert mt["tool_s"] == pytest.approx(4.0)
    assert mt["slowest"][0] == pytest.approx(3.0)
    assert mt["slowest"][1] == "bash"


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
