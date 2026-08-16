from __future__ import annotations

import json
from pathlib import Path

import pytest

from dirtywork.__main__ import build_system_prompt, main


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
    import dirtywork.__main__ as m
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


def test_transcript_construction_failure_still_prints_json(tmp_path, monkeypatch, capsys):
    # The JSON exception boundary must cover more than runner.run() -- a failure
    # constructing Transcript itself (e.g. disk unavailable) must still produce
    # the documented stdout JSON instead of a traceback.
    import subprocess
    import dirtywork.__main__ as m
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)

    class BrokenTranscript:
        def __init__(self, path):
            raise OSError("disk unavailable")

    monkeypatch.setattr(m, "Transcript", BrokenTranscript)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])

    rc = m.main(["run", "--repo", str(repo), "some task"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "model_error"
    assert "disk unavailable" in payload["final_message"]


def test_load_repo_context_uses_worktree_not_caller_checkout(tmp_path, monkeypatch):
    import subprocess
    import dirtywork.__main__ as m
    from dirtywork.runner import RunResult
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
    import dirtywork.__main__ as m
    from dirtywork.llm import LLMError
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


def test_run_start_has_all_provenance_fields(tmp_path, monkeypatch):
    # Runner.run() itself writes the run_start transcript event — replacing
    # Runner.run wholesale (as other tests in this file do to short-circuit
    # the agent loop) would skip that write entirely. Drive a minimal fake
    # LLM client through the REAL Runner.run() instead, so run_start is
    # actually emitted.
    import subprocess
    import dirtywork.__main__ as m
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")

    class ImmediateDoneClient:
        def __init__(self, base_url=None):
            pass

        def list_models(self):
            return [m.DEFAULT_MODEL]

        def chat(self, model, messages, tools, temperature=None, max_tokens=4096, timeout=None):
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setattr(m, "LMStudioClient", ImmediateDoneClient)

    rc = m.main(["run", "--repo", str(repo), "some task"])
    assert rc == 0

    transcript_files = list((tmp_path / "runs").rglob("transcript.jsonl"))
    events = [json.loads(l) for l in transcript_files[0].read_text().splitlines()]
    run_start = next(e for e in events if e["event"] == "run_start")
    for key in ("base_commit", "branch", "branch_from", "base_url",
                "dirtywork_version", "temperature", "sandbox", "provider"):
        assert key in run_start, key
    assert run_start["sandbox"] == "none"
    assert run_start["provider"] == "openai"


def test_run_end_has_diff_stat_after_writing_tracked_file(tmp_path, monkeypatch):
    import subprocess
    import dirtywork.__main__ as m
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    (repo / "existing.txt").write_text("original\n")
    subprocess.run(["git", "-C", str(repo), "add", "existing.txt"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-m", "init"],
                   capture_output=True)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")

    class WritingFakeClient:
        def __init__(self, base_url=None):
            self.calls = 0

        def list_models(self):
            return [m.DEFAULT_MODEL]

        def chat(self, model, messages, tools, temperature=None, max_tokens=4096, timeout=None):
            self.calls += 1
            if self.calls == 1:
                return {"choices": [{"message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "c1", "type": "function",
                                     "function": {"name": "write_file",
                                                  "arguments": json.dumps(
                                                      {"path": "existing.txt", "content": "changed\n"})}}],
                }}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setattr(m, "LMStudioClient", WritingFakeClient)

    rc = m.main(["run", "--repo", str(repo), "some task"])
    assert rc == 0

    transcript_files = list((tmp_path / "runs").rglob("transcript.jsonl"))
    events = [json.loads(l) for l in transcript_files[0].read_text().splitlines()]
    run_end = next(e for e in events if e["event"] == "run_end")
    assert "diff_stat" in run_end
    assert "existing.txt" in run_end["diff_stat"]


def test_run_end_has_untracked_after_writing_new_file(tmp_path, monkeypatch):
    # A model deliverable that's a brand-new file it never `git add`ed is
    # invisible to diff_stat (tracked changes only) — this pins that
    # untracked picks it up instead.
    import subprocess
    import dirtywork.__main__ as m
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    (repo / "existing.txt").write_text("original\n")
    subprocess.run(["git", "-C", str(repo), "add", "existing.txt"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-m", "init"],
                   capture_output=True)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")

    class WritingFakeClient:
        def __init__(self, base_url=None):
            self.calls = 0

        def list_models(self):
            return [m.DEFAULT_MODEL]

        def chat(self, model, messages, tools, temperature=None, max_tokens=4096, timeout=None):
            self.calls += 1
            if self.calls == 1:
                return {"choices": [{"message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "c1", "type": "function",
                                     "function": {"name": "write_file",
                                                  "arguments": json.dumps(
                                                      {"path": "brand_new.txt", "content": "hi\n"})}}],
                }}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setattr(m, "LMStudioClient", WritingFakeClient)

    rc = m.main(["run", "--repo", str(repo), "some task"])
    assert rc == 0

    transcript_files = list((tmp_path / "runs").rglob("transcript.jsonl"))
    events = [json.loads(l) for l in transcript_files[0].read_text().splitlines()]
    run_end = next(e for e in events if e["event"] == "run_end")
    assert run_end["untracked"] == "brand_new.txt"
    assert run_end["diff_stat"] == ""


def test_rundir_error_exits_2(tmp_path, monkeypatch):
    import subprocess
    import dirtywork.__main__ as m
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(m, "RUNS_DIR", runs_dir)
    monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])
    monkeypatch.setattr(m, "make_slug", lambda task, now: "fixed-slug")
    runs_dir.mkdir(parents=True)
    (runs_dir / "fixed-slug").mkdir()  # pre-existing run dir collides

    rc = m.main(["run", "--repo", str(repo), "some task"])
    assert rc == 2


def test_rundir_error_removes_orphaned_worktree(tmp_path, monkeypatch):
    # create_worktree already succeeded by the time RunDirError fires, so
    # without rollback the worktree dir + branch are silently orphaned.
    import subprocess
    import dirtywork.__main__ as m
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(m, "RUNS_DIR", runs_dir)
    monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])
    monkeypatch.setattr(m, "make_slug", lambda task, now: "fixed-slug")
    runs_dir.mkdir(parents=True)
    (runs_dir / "fixed-slug").mkdir()  # pre-existing run dir collides

    rc = m.main(["run", "--repo", str(repo), "some task"])
    assert rc == 2

    assert not (repo / ".worktrees" / "dw-fixed-slug").exists()
    branch_check = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet",
         "refs/heads/dirtywork/fixed-slug"],
        capture_output=True,
    )
    assert branch_check.returncode != 0


def test_stdout_json_has_run_dir_and_base_commit(tmp_path, monkeypatch, capsys):
    import subprocess
    import dirtywork.__main__ as m
    from dirtywork.runner import RunResult
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])
    monkeypatch.setattr(m.Runner, "run", lambda self, sp, t: RunResult("completed", 1, "ok", {}))

    rc = m.main(["run", "--repo", str(repo), "some task"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_dir"].endswith("run_dir_placeholder") is False  # sanity: it's a real path
    assert "runs" in payload["run_dir"]
    assert payload["base_commit"]
    # existing contract fields must still be present and unrenamed
    for key in ("status", "worktree", "branch", "transcript", "turns", "usage", "final_message"):
        assert key in payload
