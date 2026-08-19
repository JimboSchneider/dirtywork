from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from dirtywork.resume import (
    RESUME_MARKER,
    RESUME_TAIL_CHARS,
    ResumeError,
    build_resume_task,
    check_resumable,
    find_stashes,
    load_prior_run,
    pid_alive,
    render_transcript_tail,
    resolve_run_dir,
    stash_dir_for,
    worktree_belongs_to_repo,
)
from dirtywork.rundir import write_run_json
from dirtywork.workspace import commit_exists


def _prior(tmp_path, **over):
    repo = tmp_path / "repo"
    (repo / ".git" / "worktrees" / "wt").mkdir(parents=True, exist_ok=True)
    wt = tmp_path / "wt"
    wt.mkdir(exist_ok=True)
    (wt / ".git").write_text(f"gitdir: {repo / '.git' / 'worktrees' / 'wt'}\n")
    data = {"slug": "s1", "repo": str(repo), "worktree": str(wt),
            "branch": "dirtywork/s1", "base_commit": "a" * 40, "sandbox": "none",
            "status": "max_turns", "host_pid": 999999, "task": "do it", "model": "m",
            "turns": 40}
    data.update(over)
    return data


def _write_transcript(run_dir: Path, events):
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events))


def test_resolve_run_dir_slug_vs_path(tmp_path):
    assert resolve_run_dir("abc", tmp_path / "runs") == tmp_path / "runs" / "abc"
    p = tmp_path / "elsewhere" / "abc"
    assert resolve_run_dir(str(p), tmp_path / "runs") == p.resolve()
    assert resolve_run_dir("./rel/abc", tmp_path / "runs") == (Path("./rel/abc")).resolve()


def test_load_prior_run_reads_run_json(tmp_path):
    run_dir = tmp_path / "run"; run_dir.mkdir()
    write_run_json(run_dir, _prior(tmp_path))
    prior = load_prior_run(run_dir)
    assert prior["task"] == "do it" and prior["model"] == "m" and prior["turns"] == 40


def test_load_prior_run_falls_back_to_transcript_for_task_model_turns(tmp_path):
    run_dir = tmp_path / "run"; run_dir.mkdir()
    data = _prior(tmp_path)
    for k in ("task", "model", "turns"):
        data.pop(k)
    write_run_json(run_dir, data)
    _write_transcript(run_dir, [
        {"event": "run_start", "task": "old task", "model": "old/model"},
        {"event": "assistant", "text": "hi", "tool_calls": []},
        {"event": "run_end", "status": "max_turns", "turns": 17},
    ])
    prior = load_prior_run(run_dir)
    assert prior["task"] == "old task" and prior["model"] == "old/model" and prior["turns"] == 17


def test_load_prior_run_errors(tmp_path):
    with pytest.raises(ResumeError):
        load_prior_run(tmp_path / "missing")
    run_dir = tmp_path / "run"; run_dir.mkdir()
    data = _prior(tmp_path); data.pop("task")
    write_run_json(run_dir, data)
    with pytest.raises(ResumeError, match="run_start"):
        load_prior_run(run_dir)          # no transcript to fall back to
    data = _prior(tmp_path); data.pop("worktree")
    write_run_json(run_dir, data)
    with pytest.raises(ResumeError, match="worktree"):
        load_prior_run(run_dir)


def test_pid_alive():
    import os
    assert pid_alive(os.getpid()) is True
    assert pid_alive(0) is False
    assert pid_alive(None) is False
    assert pid_alive(2 ** 22 + 12345) is False


def test_check_resumable_refuses_running_with_live_pid(tmp_path):
    prior = _prior(tmp_path, status="running")
    with pytest.raises(ResumeError, match="still in progress"):
        check_resumable(prior, alive=lambda pid: True)
    check_resumable(prior, alive=lambda pid: False)   # crashed run: fine


def test_check_resumable_refuses_missing_worktree(tmp_path):
    prior = _prior(tmp_path)
    (Path(prior["worktree"]) / ".git").unlink()
    with pytest.raises(ResumeError, match="worktree"):
        check_resumable(prior)
    prior["worktree"] = str(tmp_path / "gone")
    with pytest.raises(ResumeError, match="worktree"):
        check_resumable(prior)


def test_render_transcript_tail_formats_and_truncates(tmp_path):
    run_dir = tmp_path / "run"; run_dir.mkdir()
    events = [{"event": "run_start", "task": "t"}]
    for i in range(300):
        events.append({"event": "assistant", "text": f"step {i} " + "x" * 100,
                       "tool_calls": [{"name": "bash", "arguments": "{}"}]})
        events.append({"event": "tool_result", "tool": "bash", "args": "{}", "result": "y" * 100})
    events.append({"event": "nudge", "kind": "stall", "turn": 5})
    events.append({"event": "run_end", "status": "stalled", "turns": 12})
    _write_transcript(run_dir, events)
    tail = render_transcript_tail(run_dir / "transcript.jsonl")
    assert len(tail) <= RESUME_TAIL_CHARS
    lines = tail.splitlines()
    assert lines[-1] == "run_end: stalled"
    assert lines[-2] == "nudge: stall"
    assert lines[-3].startswith("tool_result bash: yyyy")
    assert lines[-4].startswith("assistant: step 299 ") and lines[-4].endswith("[tools: bash]")
    assert "step 0 " not in tail                       # oldest events dropped first
    assert render_transcript_tail(tmp_path / "nope.jsonl") == ""


def test_render_transcript_tail_caps_field_lengths(tmp_path):
    run_dir = tmp_path / "run"; run_dir.mkdir()
    _write_transcript(run_dir, [
        {"event": "assistant", "text": "a" * 5000, "tool_calls": []},
        {"event": "tool_result", "tool": "read_file", "args": "", "result": "r" * 5000},
    ])
    tail = render_transcript_tail(run_dir / "transcript.jsonl")
    a, r = tail.splitlines()
    assert a == "assistant: " + "a" * 1000
    assert r == "tool_result read_file: " + "r" * 500


def test_build_resume_task_text():
    text = build_resume_task("Fix the bug", "max_turns", 40, "assistant: hi\nrun_end: max_turns")
    assert text.startswith("Fix the bug\n\n--- RESUMED RUN ---\n")
    assert "ended with status 'max_turns' after 40 turns" in text
    assert "`git status` and `git diff`" in text
    assert "assistant: hi\nrun_end: max_turns\n" in text
    assert text.rstrip().endswith("call finish(summary=...).")
    assert "after unknown turns" in build_resume_task("t", "model_error", None, "")


def test_commit_exists(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "--allow-empty", "-m", "i"], capture_output=True, check=True)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert commit_exists(repo, head) is True
    assert commit_exists(repo, "b" * 40) is False


def test_check_resumable_accepts_linked_worktree(tmp_path):
    check_resumable(_prior(tmp_path))          # happy path: no exception


def test_check_resumable_refuses_plain_clone_and_foreign_gitdir(tmp_path):
    prior = _prior(tmp_path)
    wt = Path(prior["worktree"])
    (wt / ".git").unlink()
    (wt / ".git").mkdir()                       # a plain clone: .git is a directory
    with pytest.raises(ResumeError, match="not a linked worktree"):
        check_resumable(prior)
    (wt / ".git").rmdir()
    (wt / ".git").write_text(f"gitdir: {tmp_path / 'elsewhere' / '.git' / 'worktrees' / 'x'}\n")
    with pytest.raises(ResumeError, match="not a linked worktree"):
        check_resumable(prior)


def test_worktree_belongs_to_repo_relative_gitdir(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git" / "worktrees" / "wt").mkdir(parents=True)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: ../repo/.git/worktrees/wt\n")
    assert worktree_belongs_to_repo(wt, repo) is True
    assert worktree_belongs_to_repo(wt, tmp_path / "other") is False


def test_build_resume_task_does_not_stack_preambles():
    once = build_resume_task("Fix the bug", "max_turns", 40, "run_end: max_turns")
    twice = build_resume_task(once, "stalled", 12, "run_end: stalled")
    assert twice.count(RESUME_MARKER) == 1
    assert twice.startswith("Fix the bug" + RESUME_MARKER)
    assert "after 12 turns" in twice and "after 40 turns" not in twice


def test_check_resumable_refuses_while_a_stash_exists(tmp_path):
    prior = _prior(tmp_path)
    wt = Path(prior["worktree"])
    stash = stash_dir_for(wt, "older-run")
    stash.mkdir()
    (stash / "precious.txt").write_text("x")
    with pytest.raises(ResumeError, match="pre-resume stash"):
        check_resumable(prior)
    assert (stash / "precious.txt").exists()      # refusing never touches it
    stash.rename(wt / "restored-by-operator")     # operator moves it back → resume allowed again
    check_resumable(prior)


def test_stash_helpers(tmp_path):
    wt = tmp_path / "dw-abc"
    wt.mkdir()
    assert stash_dir_for(wt, "s1") == tmp_path / "dw-abc.pre-resume-s1"
    assert find_stashes(wt) == []
    (tmp_path / "dw-abc.pre-resume-s1").mkdir()
    (tmp_path / "dw-abc.pre-resume-s0").mkdir()
    (tmp_path / "dw-abcd.pre-resume-s9").mkdir()   # a different worktree's stash
    assert find_stashes(wt) == [tmp_path / "dw-abc.pre-resume-s0", tmp_path / "dw-abc.pre-resume-s1"]


def test_build_resume_task_with_feedback_uses_the_feedback_block():
    text = build_resume_task("Fix the bug", "completed", 12, "run_end: completed",
                             feedback="You deleted the retry loop; put it back.")
    assert text.startswith("Fix the bug\n\n--- RESUMED RUN: REVIEW FEEDBACK ---\n")
    assert "--- RESUMED RUN ---" not in text
    assert "ended with status 'completed' after 12 turns" in text
    assert "A reviewer read that run's work and sent this feedback:" in text
    assert "You deleted the retry loop; put it back." in text
    assert "apply the feedback. Make no other changes." in text
    assert text.endswith("When the task is complete, call finish(summary=...).")


def test_build_resume_task_strips_both_markers_so_blocks_never_stack():
    plain = build_resume_task("Fix the bug", "max_turns", 40, "run_end: max_turns")
    with_feedback = build_resume_task(plain, "stalled", 12, "run_end: stalled",
                                      feedback="try again")
    assert with_feedback.count("--- RESUMED RUN ---") == 0
    assert with_feedback.count("--- RESUMED RUN: REVIEW FEEDBACK ---") == 1
    assert with_feedback.startswith("Fix the bug\n\n--- RESUMED RUN")
    back_to_plain = build_resume_task(with_feedback, "max_turns", 3, "run_end: max_turns")
    assert back_to_plain.count("--- RESUMED RUN: REVIEW FEEDBACK ---") == 0
    assert back_to_plain.count("--- RESUMED RUN ---") == 1
    assert back_to_plain.startswith("Fix the bug\n\n--- RESUMED RUN ---")
