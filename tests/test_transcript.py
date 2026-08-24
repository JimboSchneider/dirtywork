from __future__ import annotations

import json
from pathlib import Path

import pytest

from dirtywork.transcript import Transcript


def test_writes_jsonl_events_with_ts(tmp_path: Path):
    (tmp_path / "sub").mkdir()  # the run dir now exists before Transcript is built
    t = Transcript(tmp_path / "sub" / "transcript.jsonl")
    t.write("run_start", task="do a thing", model="m")
    t.write("assistant", text="hello")
    t.close()

    lines = (tmp_path / "sub" / "transcript.jsonl").read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event"] == "run_start"
    assert first["task"] == "do a thing"
    assert "T" in first["ts"]  # ISO-8601 timestamp

    second = json.loads(lines[1])
    assert second["event"] == "assistant"


def test_flushes_each_line_before_close(tmp_path: Path):
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    t.write("run_start", task="x")
    # Do NOT close — the line must already be on disk (tail -f contract)
    assert path.read_text().count("\n") == 1
    t.close()


def _lines(path: Path) -> list:
    return [json.loads(l) for l in path.read_text().splitlines()]


def test_turn_buffers_writes_until_exit_and_keeps_order(tmp_path: Path):
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    t.write("run_start", task="x")
    with t.turn():
        rec = t.write("guardrail_block", tool="bash", reason="no")
        t.write("tool_result", tool="bash", result="r")
        t.write("nudge", kind="timeout", turn=1)
        assert isinstance(rec, dict) and rec["event"] == "guardrail_block"
        assert path.read_text().count("\n") == 1          # nothing of the turn is on disk yet
    events = [e["event"] for e in _lines(path)]
    assert events == ["run_start", "guardrail_block", "tool_result", "nudge"]
    t.close()


def test_turn_write_returns_none_outside_a_turn(tmp_path: Path):
    t = Transcript(tmp_path / "t.jsonl")
    assert t.write("run_start", task="x") is None
    t.close()


def test_record_amended_inside_the_turn_is_written_amended(tmp_path: Path):
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    with t.turn():
        rec = t.write("tool_result", tool="finish", result="provisional")
        rec["result"] = "resolved"
        rec["follow_up"] = "extra"
    ev = _lines(path)[0]
    assert ev["result"] == "resolved" and ev["follow_up"] == "extra"
    t.close()


def test_amendment_after_flush_does_not_reach_disk(tmp_path: Path):
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    with t.turn():
        rec = t.write("tool_result", tool="bash", result="a")
    rec["result"] = "late"
    assert _lines(path)[0]["result"] == "a"
    t.close()


def test_ts_is_stamped_at_write_time_and_non_decreasing(tmp_path: Path):
    import time
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    with t.turn():
        r1 = t.write("tool_result", tool="bash", result="a")
        time.sleep(0.01)
        r2 = t.write("tool_result", tool="bash", result="b")
        assert r1["ts"] < r2["ts"]
    a, b = _lines(path)
    assert a["ts"] == r1["ts"] and b["ts"] == r2["ts"]
    t.close()


def test_turn_flushes_on_keyboard_interrupt(tmp_path: Path):
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    with pytest.raises(KeyboardInterrupt):
        with t.turn():
            t.write("tool_result", tool="bash", result="a")
            raise KeyboardInterrupt
    assert [e["event"] for e in _lines(path)] == ["tool_result"]
    t.close()


def test_record_resolved_in_an_except_handler_inside_the_turn_is_written_resolved(tmp_path: Path):
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    with t.turn():
        rec = t.write("tool_result", tool="finish", result="provisional")
        try:
            raise KeyboardInterrupt
        except KeyboardInterrupt:
            rec["result"] = "run not finished: interrupted"
    assert _lines(path)[0]["result"] == "run not finished: interrupted"
    t.close()


def test_flush_mid_turn_writes_and_keeps_the_turn_open(tmp_path: Path):
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    with t.turn():
        t.write("tool_result", tool="bash", result="a")
        t.flush()
        assert [e["event"] for e in _lines(path)] == ["tool_result"]
        rec = t.write("run_end", status="completed")
        assert rec is not None                           # still buffering
        assert path.read_text().count("\n") == 1
    assert [e["event"] for e in _lines(path)] == ["tool_result", "run_end"]
    t.close()


def test_turn_is_not_reentrant(tmp_path: Path):
    t = Transcript(tmp_path / "t.jsonl")
    with t.turn():
        with pytest.raises(RuntimeError):
            with t.turn():
                pass
    t.close()


def test_close_flushes_an_open_turn(tmp_path: Path):
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    cm = t.turn()
    cm.__enter__()
    t.write("tool_result", tool="bash", result="a")
    t.close()
    assert [e["event"] for e in _lines(path)] == ["tool_result"]


def test_write_from_another_thread_during_a_turn_lands_with_the_turn(tmp_path: Path):
    import threading
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    started = threading.Event()
    with t.turn():
        t.write("tool_result", tool="bash", result="a")

        def other():
            t.write("sandbox_reset", reason="stray")
            started.set()
        th = threading.Thread(target=other)
        th.start()
        started.wait(5)
        th.join(5)
        assert path.read_text() == ""                     # buffered, not written directly
    assert [e["event"] for e in _lines(path)] == ["tool_result", "sandbox_reset"]
    t.close()


def test_atomic_close_never_loses_a_racing_write(tmp_path: Path):
    """Spec §6.1 (v4): a writer racing the turn's exit either lands in the
    buffer (and is flushed with it) or writes directly after the close. Every
    write is on disk exactly once."""
    import threading
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    total = 0
    for _ in range(200):
        stop = threading.Event()
        wrote = [0]

        def hammer():
            while not stop.is_set():
                t.write("sandbox_reset", reason="r")
                wrote[0] += 1
        th = threading.Thread(target=hammer)
        with t.turn():
            t.write("tool_result", tool="bash", result="a")
            th.start()
        stop.set()
        th.join(5)
        total += wrote[0] + 1
    t.close()
    lines = _lines(path)
    assert len(lines) == total
    # every turn's tool_result precedes the resets written after its close
    assert lines[0]["event"] == "tool_result"


def test_non_finite_field_stays_valid_json(tmp_path: Path):
    t = Transcript(tmp_path / "t.jsonl")
    t.write("run_end", usage={"prompt_tokens": float("nan"), "completion_tokens": float("inf")})
    t.close()
    text = (tmp_path / "t.jsonl").read_text()
    assert "NaN" not in text and "Infinity" not in text
    import json as _json
    for line in text.splitlines():
        _json.loads(line)  # each line parses


def test_refuses_preexisting_file(tmp_path: Path):
    path = tmp_path / "transcript.jsonl"
    path.write_text("stale content from a slug collision\n")
    with pytest.raises(FileExistsError):
        Transcript(path)


def test_refuses_symlink(tmp_path: Path):
    real = tmp_path / "elsewhere.jsonl"
    link = tmp_path / "transcript.jsonl"
    link.symlink_to(real)
    with pytest.raises(OSError):
        Transcript(link)
    assert not real.exists()  # nothing was ever written through the symlink


def test_file_mode_is_0600(tmp_path: Path):
    import stat
    path = tmp_path / "transcript.jsonl"
    t = Transcript(path)
    t.close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
