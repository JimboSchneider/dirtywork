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
