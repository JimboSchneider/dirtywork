from __future__ import annotations

from pathlib import Path

import pytest

from dirtywork.sandbox import RunArtifacts, SandboxError
from dirtywork.sandbox.host import HostSandbox


@pytest.fixture()
def wt(tmp_path: Path) -> Path:
    (tmp_path / "hello.txt").write_text("hi\n")
    return tmp_path


def test_run_artifacts_defaults():
    ra = RunArtifacts()
    assert ra.diff_stat == ""
    assert ra.patch_path is None
    assert ra.worktree_bytes is None
    assert ra.worktree_files is None
    assert ra.escaping_symlinks == []
    assert ra.dropped_git_entries == []
    assert ra.export_status == "ok"


def test_sandbox_error_is_exception():
    assert issubclass(SandboxError, Exception)


def test_host_sandbox_start_is_noop_and_read_file_works(wt: Path):
    sb = HostSandbox(wt)
    sb.start(wt, wt, "slug", "deadbeef")
    assert "hi" in sb.read_file("hello.txt")


def test_host_sandbox_write_edit_list_grep_bash(wt: Path):
    sb = HostSandbox(wt)
    sb.start(wt, wt, "slug", "deadbeef")
    assert "Wrote" in sb.write_file("new.txt", "content")
    assert "Edited" in sb.edit_file("new.txt", "content", "changed")
    assert "new.txt" in sb.list_dir(".")
    assert "hello.txt" in sb.grep("hi")
    out = sb.bash("echo hi")
    assert "exit code: 0" in out
    assert "hi" in out


def test_host_sandbox_finalize_returns_run_artifacts(wt: Path):
    sb = HostSandbox(wt)
    sb.start(wt, wt, "slug", "deadbeef")
    artifacts = sb.finalize()
    assert isinstance(artifacts, RunArtifacts)
    assert artifacts.worktree_bytes is not None
    assert artifacts.worktree_files is not None


def test_host_sandbox_finalize_reports_untracked(wt: Path):
    # Initialize git repo in the worktree
    import subprocess
    subprocess.run(["git", "init"], cwd=wt, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=wt, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=wt, check=True, capture_output=True)
    # Create initial commit
    (wt / "initial.txt").write_text("initial\n")
    subprocess.run(["git", "add", "."], cwd=wt, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=wt, check=True, capture_output=True)
    base_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt, check=True, capture_output=True, text=True).stdout.strip()

    sb = HostSandbox(wt)
    sb.start(wt, wt, "slug", base_commit)
    (wt / "brand_new.txt").write_text("new file\n")
    artifacts = sb.finalize()
    assert artifacts.untracked == "brand_new.txt"
    assert isinstance(artifacts.diff_stat, str)


def test_host_sandbox_stop_is_noop(wt: Path):
    sb = HostSandbox(wt)
    sb.start(wt, wt, "slug", "deadbeef")
    sb.stop()  # must not raise


def test_host_sandbox_bash_raises_budget_exceeded_over_cap(wt: Path):
    from dirtywork.budget import BudgetExceeded
    sb = HostSandbox(wt, max_worktree_mb=1, max_worktree_files=1)
    sb.start(wt, wt, "slug", "deadbeef")
    big = wt / "big.bin"
    with pytest.raises(BudgetExceeded):
        sb.bash("dd if=/dev/zero of=big2.bin bs=1M count=5 2>/dev/null")


def test_host_sandbox_insert_before_and_after(wt: Path):
    sb = HostSandbox(wt)
    sb.start(wt, wt, "slug", "deadbeef")
    assert "Inserted into" in sb.insert_after("hello.txt", "hi", "there")
    assert "Inserted into" in sb.insert_before("hello.txt", "there", "before-there")
    assert (wt / "hello.txt").read_text() == "hi\nbefore-there\nthere\n"


def test_host_sandbox_apply_edits(wt: Path):
    sb = HostSandbox(wt)
    sb.start(wt, wt, "slug", "deadbeef")
    (wt / "batch.txt").write_text("one\ntwo\n")
    out = sb.apply_edits("batch.txt", [{"old": "one", "new": "1"},
                                       {"old": "two", "new": "2"}])
    assert out.startswith("Applied 2 edits to batch.txt: ")
    assert (wt / "batch.txt").read_text() == "1\n2\n"


def test_host_sandbox_append_file(wt: Path):
    sb = HostSandbox(wt)
    sb.start(wt, wt, "slug", "deadbeef")
    assert sb.append_file("hello.txt", "there\n").startswith("Appended to hello.txt:")
    assert (wt / "hello.txt").read_text() == "hi\nthere\n"
    assert sb.append_file("gone.txt", "x") == (
        "ERROR: cannot append to 'gone.txt': it does not exist; create it with "
        "write_file first")


def test_host_sandbox_start_sweeps_a_leftover_temp_and_says_so(wt: Path, capsys):
    # Spec §2.5: only a KILL can leave one behind, and a resume is where it
    # shows up. Never silent.
    from dirtywork import tools
    leftover = wt / tools.tmp_name("hello.txt")
    leftover.write_text("half-written")
    sb = HostSandbox(wt)
    sb.start(wt, wt, "slug", "deadbeef")
    assert not leftover.exists()
    assert "swept 1 stale temp file" in capsys.readouterr().err


def test_host_sandbox_finalize_sweeps_and_reports(wt: Path, capsys):
    from dirtywork import tools
    sb = HostSandbox(wt)
    sb.start(wt, wt, "slug", "deadbeef")
    capsys.readouterr()
    (wt / tools.tmp_name("a.txt")).write_text("x")
    (wt / tools.tmp_name("b.txt")).write_text("y")
    sb.finalize()
    assert "swept 2 stale temp files" in capsys.readouterr().err
    assert [p.name for p in wt.iterdir() if p.name.startswith(tools.TMP_PREFIX)] == []
