from __future__ import annotations

import json
from pathlib import Path

import pytest

from localagent.__main__ import build_system_prompt, main


def test_build_system_prompt_includes_rules_and_context(tmp_path: Path):
    p = build_system_prompt(tmp_path, "REPO RULES HERE")
    assert str(tmp_path) in p
    assert "edit_file" in p
    assert "REPO RULES HERE" in p
    assert "uncommitted" in p


def test_build_system_prompt_no_context(tmp_path: Path):
    p = build_system_prompt(tmp_path, None)
    assert "Repository conventions" not in p


def test_main_bad_repo_exits_2(tmp_path: Path, capsys):
    rc = main(["run", "--repo", str(tmp_path / "nope"), "do things"])
    assert rc == 2
    assert "error" in capsys.readouterr().err.lower()


def test_main_lmstudio_down_exits_2(tmp_path: Path, capsys, monkeypatch):
    import subprocess
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    rc = main(["run", "--repo", str(repo), "--base-url",
               "http://127.0.0.1:1/v1", "do things"])
    assert rc == 2


def test_transcript_closed_even_on_unexpected_error(tmp_path, monkeypatch):
    import subprocess
    import localagent.__main__ as m
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    closed = {}
    class SpyTranscript(m.Transcript):
        def close(self):
            closed["yes"] = True
            super().close()
    monkeypatch.setattr(m, "Transcript", SpyTranscript)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])
    def boom(self, system_prompt, task):
        raise RuntimeError("boom")
    monkeypatch.setattr(m.Runner, "run", boom)
    import pytest as _pytest
    with _pytest.raises(RuntimeError):
        m.main(["run", "--repo", str(repo), "some task"])
    assert closed.get("yes")
