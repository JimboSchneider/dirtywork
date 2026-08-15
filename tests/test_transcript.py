from __future__ import annotations

import json
from pathlib import Path

from dirtywork.transcript import Transcript


def test_writes_jsonl_events_with_ts(tmp_path: Path):
    t = Transcript(tmp_path / "sub" / "transcript.jsonl")  # parent dirs auto-created
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


def test_non_finite_field_stays_valid_json(tmp_path: Path):
    t = Transcript(tmp_path / "t.jsonl")
    t.write("run_end", usage={"prompt_tokens": float("nan"), "completion_tokens": float("inf")})
    t.close()
    text = (tmp_path / "t.jsonl").read_text()
    assert "NaN" not in text and "Infinity" not in text
    import json as _json
    for line in text.splitlines():
        _json.loads(line)  # each line parses
