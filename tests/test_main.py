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


def test_transcript_closed_even_on_unexpected_error(tmp_path, monkeypatch, capsys):
    # Machine contract: every post-preflight run prints exactly one JSON object,
    # even on an exception the runner doesn't itself convert to a status (e.g. a
    # bare RuntimeError escaping runner.run). No traceback, no missing run_end.
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

    rc = m.main(["run", "--repo", str(repo), "some task"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "model_error"
    assert "unexpected" in payload["final_message"]
    assert closed.get("yes")

    transcript_files = list((tmp_path / "runs").rglob("transcript.jsonl"))
    assert len(transcript_files) == 1
    events = [json.loads(line) for line in transcript_files[0].read_text().splitlines()]
    assert events[-1]["event"] == "run_end"


def test_load_repo_context_uses_worktree_not_caller_checkout(tmp_path, monkeypatch):
    import subprocess
    import localagent.__main__ as m
    from localagent.runner import RunResult
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    (repo / "CLAUDE.md").write_text("CONVENTIONS-FROM-COMMIT")
    subprocess.run(["git", "-C", str(repo), "add", "CLAUDE.md"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-m", "add conventions"],
                   capture_output=True)
    # Dirty the working tree AFTER the commit — the worktree branches from
    # HEAD (the commit), so it must never see this uncommitted content.
    (repo / "CLAUDE.md").write_text("CONVENTIONS-DIRTY")

    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])

    captured = {}

    def fake_run(self, system_prompt, task):
        captured["system_prompt"] = system_prompt
        return RunResult("completed", 1, "ok", {})

    monkeypatch.setattr(m.Runner, "run", fake_run)

    rc = m.main(["run", "--repo", str(repo), "some task"])
    assert rc == 0
    assert "CONVENTIONS-FROM-COMMIT" in captured["system_prompt"]
    assert "CONVENTIONS-DIRTY" not in captured["system_prompt"]


def test_llm_error_during_run_prints_model_error_json(tmp_path, monkeypatch, capsys):
    import subprocess
    import localagent.__main__ as m
    from localagent.llm import LLMError
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])

    def boom(self, system_prompt, task):
        raise LLMError("boom")
    monkeypatch.setattr(m.Runner, "run", boom)

    rc = m.main(["run", "--repo", str(repo), "some task"])
    assert rc == 1
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["status"] == "model_error"
    assert "worktree" in payload
