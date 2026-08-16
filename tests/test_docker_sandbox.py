from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dirtywork.procs import Captured
from dirtywork.sandbox import SandboxError
from dirtywork.sandbox.docker import DockerSandbox, docker_cli
from dirtywork.sandbox.docker_args import DockerConfig
from tests.docker_fakes import FakeDocker, FakePopen, _fail, _ok

DockerError = docker_cli.DockerError

_TOP_HEADER = b"UID  PID  PPID  C  STIME  TTY  TIME  CMD\n"

_SAMPLE_ARGV = ["exec", "-w", "/work", "dw-abc123", "/bin/sh", "-c",
                "du -sk /work; find /work | wc -l"]


@pytest.fixture()
def docker(tmp_path: Path):
    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["image", "inspect", "--format", "{{json .RepoDigests}}"],
                _ok(b'["dirtywork/worker@sha256:' + b"a" * 64 + b'"]'))
    fake.script(["volume", "create"], _ok())
    fake.script(["run"], _ok())   # prep container
    fake.script(["create"], _ok())  # worker create
    fake.script(["exec"], _ok())  # ready-wait /bin/true and init
    cfg = DockerConfig()
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    sb = DockerSandbox(cfg, run_dir=run_dir, run=fake.run, popen=fake.popen)
    return sb, fake, run_dir


def _fake_repo(tmp_path: Path) -> Path:
    import subprocess as sp
    repo = tmp_path / "repo"
    repo.mkdir()
    sp.run(["git", "-C", str(repo), "init", "-b", "main"], capture_output=True, check=True)
    sp.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    sp.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "f.txt").write_text("hi")
    sp.run(["git", "-C", str(repo), "add", "."], check=True)
    sp.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True)
    return repo


@pytest.fixture()
def started(docker, tmp_path: Path):
    sb, fake, run_dir = docker
    fake.script(_SAMPLE_ARGV, _ok(b"1024\t/work\n5\n"))  # 1 MB, 5 files: safely under caps
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    sb.start(worktree, repo, "abc123", "deadbeef" * 5)
    fake.calls.clear()
    return sb, fake, run_dir


@pytest.fixture()
def started_with_transcript(tmp_path: Path):
    from dirtywork.transcript import Transcript
    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["image", "inspect", "--format", "{{json .RepoDigests}}"],
                _ok(b'["dirtywork/worker@sha256:' + b"a" * 64 + b'"]'))
    fake.script(["volume", "create"], _ok())
    fake.script(["run"], _ok())
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())
    fake.script(_SAMPLE_ARGV, _ok(b"1024\t/work\n5\n"))
    cfg = DockerConfig()
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    transcript = Transcript(run_dir / "transcript.jsonl")
    sb = DockerSandbox(cfg, run_dir=run_dir, transcript=transcript, run=fake.run, popen=fake.popen)
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    sb.start(worktree, repo, "abc123", "deadbeef" * 5)
    fake.calls.clear()
    return sb, fake, run_dir, transcript


def test_start_sets_attributes(docker, tmp_path):
    sb, fake, run_dir = docker
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /somewhere\n")

    sb.start(worktree, repo, "abc123", "deadbeef" * 5)

    assert sb.container == "dw-abc123"
    assert sb.volume == "dw-abc123-work"
    assert sb.image_ref == "dirtywork/worker@sha256:" + "a" * 64
    assert isinstance(sb.uid, int)
    assert isinstance(sb.gid, int)


def test_start_refuses_on_container_collision(docker, tmp_path):
    sb, fake, run_dir = docker
    fake.script(["container", "inspect"], _ok())  # already exists
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    with pytest.raises(SandboxError, match="runs clean abc123"):
        sb.start(worktree, repo, "abc123", "deadbeef" * 5)
    # nothing created after the collision check
    assert not any(c[0][0] == "volume" and c[0][1] == "create" for c in fake.calls)


def test_start_refuses_on_volume_collision(docker, tmp_path):
    sb, fake, run_dir = docker
    fake.script(["volume", "inspect"], _ok())  # already exists
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    with pytest.raises(SandboxError, match="runs clean abc123"):
        sb.start(worktree, repo, "abc123", "deadbeef" * 5)


def test_start_prep_failure_raises_sandboxerror(docker, tmp_path):
    sb, fake, run_dir = docker
    fake.script(["run"], _fail(b"chown: permission denied"))
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    with pytest.raises(SandboxError, match="chown"):
        sb.start(worktree, repo, "abc123", "deadbeef" * 5)


def test_start_create_failure_raises_sandboxerror(docker, tmp_path):
    sb, fake, run_dir = docker
    fake.script(["create"], _fail(b"no such image"))
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    with pytest.raises(SandboxError, match="docker create"):
        sb.start(worktree, repo, "abc123", "deadbeef" * 5)


def test_start_call_order(docker, tmp_path):
    sb, fake, run_dir = docker
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    sb.start(worktree, repo, "abc123", "deadbeef" * 5)

    verbs = [tuple(c[0][:2]) for c in fake.calls]
    assert verbs.index(("container", "inspect")) < verbs.index(("volume", "inspect"))
    assert verbs.index(("volume", "inspect")) < verbs.index(("image", "inspect"))
    volume_create_idx = next(i for i, c in enumerate(fake.calls) if c[0][:2] == ["volume", "create"])
    prep_idx = next(i for i, c in enumerate(fake.calls) if c[0][0] == "run")
    create_idx = next(i for i, c in enumerate(fake.calls) if c[0][0] == "create")
    exec_idxs = [i for i, c in enumerate(fake.calls) if c[0][0] == "exec"]
    assert volume_create_idx < prep_idx < create_idx < min(exec_idxs)


def test_start_creates_tether_after_create(docker, tmp_path):
    sb, fake, run_dir = docker
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    sb.start(worktree, repo, "abc123", "deadbeef" * 5)

    assert len(fake.popens) == 1
    assert fake.popens[0].argv == ["docker", "start", "-ai", "dw-abc123"]


def test_wait_ready_retries_until_success(docker, tmp_path):
    sb, fake, run_dir = docker
    fake.script(["exec"], [_fail(), _fail(), _ok()])
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    sb.start(worktree, repo, "abc123", "deadbeef" * 5)  # must not raise

    assert sb.container == "dw-abc123"


def test_stop_is_idempotent_and_removes_container_and_volume(docker, tmp_path):
    sb, fake, run_dir = docker
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    sb.start(worktree, repo, "abc123", "deadbeef" * 5)
    fake.calls.clear()

    sb.stop()
    sb.stop()  # second call must no-op, not re-issue docker commands

    rm_calls = [c for c in fake.calls if c[0][:2] == ["rm", "-f"]]
    vol_rm_calls = [c for c in fake.calls if c[0][:2] == ["volume", "rm"]]
    assert len(rm_calls) == 1
    assert len(vol_rm_calls) == 1


def test_stop_keeps_volume_when_keep_volume_set(tmp_path):
    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["image", "inspect", "--format", "{{json .RepoDigests}}"],
                _ok(b'["dirtywork/worker@sha256:' + b"a" * 64 + b'"]'))
    fake.script(["volume", "create"], _ok())
    fake.script(["run"], _ok())
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())
    cfg = DockerConfig(keep_volume=True)
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    sb = DockerSandbox(cfg, run_dir=run_dir, run=fake.run, popen=fake.popen)
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    sb.start(worktree, repo, "abc123", "deadbeef" * 5)

    sb.stop()

    assert not any(c[0][:2] == ["volume", "rm"] for c in fake.calls)


def test_start_wraps_workspace_error_as_sandboxerror(monkeypatch, tmp_path):
    """Ruling C4: DockerSandbox.start must wrap WorkspaceError from validate_objects_dir
    as SandboxError (chain it)."""
    from dirtywork.workspace import WorkspaceError

    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["image", "inspect", "--format", "{{json .RepoDigests}}"],
                _ok(b'["dirtywork/worker@sha256:' + b"a" * 64 + b'"]'))

    # Monkeypatch validate_objects_dir to raise WorkspaceError
    def fake_validate_objects_dir(repo):
        raise WorkspaceError(f"objects dir {repo}/.git/objects is not accessible")

    monkeypatch.setattr("dirtywork.sandbox.docker_cli.validate_objects_dir", fake_validate_objects_dir)

    cfg = DockerConfig()
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    sb = DockerSandbox(cfg, run_dir=run_dir, run=fake.run, popen=fake.popen)
    repo = tmp_path / "repo"
    repo.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()

    with pytest.raises(SandboxError, match="objects dir"):
        sb.start(worktree, repo, "abc123", "deadbeef" * 5)


def test_read_file_exec_argv_and_shaping(started):
    from dirtywork.tools import MAX_READ_BYTES
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b"line one\nline two\n"))
    out = sb.read_file("src/app.py")
    assert fake.calls[-1][0] == [
        "exec", "-w", "/work", "dw-abc123",
        "/usr/bin/head", "-c", str(MAX_READ_BYTES + 1), "--", "src/app.py",
    ]
    assert "     1\tline one" in out
    assert "     2\tline two" in out


def test_read_file_rejects_absolute_path(started):
    sb, fake, run_dir = started
    out = sb.read_file("/etc/passwd")
    assert out.startswith("ERROR:")
    assert not fake.calls


def test_read_file_rejects_dotdot_escape(started):
    sb, fake, run_dir = started
    out = sb.read_file("../../etc/passwd")
    assert out.startswith("ERROR:")
    assert not fake.calls


def test_write_file_sends_content_on_stdin(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok())
    out = sb.write_file("deep/new/file.txt", "hello")
    assert "Wrote 5 bytes" in out
    argv, timeout, stdin = fake.calls[-1]
    assert argv == [
        "exec", "-w", "/work", "-i", "dw-abc123",
        "/bin/sh", "-c", 'mkdir -p "$(dirname -- "$1")" && cat > "$1"',
        "_", "deep/new/file.txt",
    ]
    assert stdin == b"hello"


def test_write_file_refuses_dot_git(started):
    sb, fake, run_dir = started
    out = sb.write_file(".git/hooks/pre-commit", "#!/bin/sh")
    assert out.startswith("ERROR:")
    assert not fake.calls


def test_write_file_refuses_oversized_content(started):
    from dirtywork.tools import MAX_WRITE_BYTES
    sb, fake, run_dir = started
    out = sb.write_file("big.txt", "x" * (MAX_WRITE_BYTES + 1))
    assert out.startswith("ERROR:")
    assert not fake.calls


def test_edit_file_reads_then_writes(started):
    sb, fake, run_dir = started
    fake.script(["exec"], [_ok(b"def main():\n    return 42\n"), _ok()])
    out = sb.edit_file("src/app.py", "return 42", "return 43")
    assert "Edited" in out
    heads = [c for c in fake.calls if "/usr/bin/head" in c[0]]
    writes = [c for c in fake.calls if "cat > \"$1\"" in " ".join(c[0])]
    assert len(heads) == 1
    assert len(writes) == 1


def test_edit_file_no_match(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b"nothing matches here\n"))
    out = sb.edit_file("src/app.py", "not here", "x")
    assert out.startswith("ERROR:") and "0 times" in out


def test_edit_file_multiple_matches(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b"aa\naa\n"))
    out = sb.edit_file("dup.txt", "aa", "bb")
    assert out.startswith("ERROR:") and "2 times" in out


def test_edit_file_write_failure(started):
    """When edit_file's write exec fails, the error should be propagated."""
    sb, fake, run_dir = started
    # Read succeeds with content containing "old", then write fails
    fake.script(["exec"], [_ok(b"def main():\n    return old\n"), _fail(b"write failed")])
    out = sb.edit_file("src/app.py", "old", "new")
    assert out.startswith("ERROR:")
    # Verify write was attempted (1 read + 1 write = 2 exec calls)
    heads = [c for c in fake.calls if "/usr/bin/head" in c[0]]
    writes = [c for c in fake.calls if "cat > \"$1\"" in " ".join(c[0])]
    assert len(heads) == 1
    assert len(writes) == 1


def test_read_file_oversize(started):
    """When file exceeds MAX_READ_BYTES, read_file should return error."""
    from dirtywork.tools import MAX_READ_BYTES
    sb, fake, run_dir = started
    # Script read to return MAX_READ_BYTES+1 bytes
    oversize_content = b"x" * (MAX_READ_BYTES + 1)
    fake.script(["exec"], _ok(oversize_content))
    out = sb.read_file("big.txt")
    assert out.startswith("ERROR:")
    assert f"exceeds {MAX_READ_BYTES} bytes" in out


def test_edit_file_non_utf8(started):
    """When file contains non-UTF-8 bytes, edit_file should return error without writing."""
    sb, fake, run_dir = started
    # Read succeeds with invalid UTF-8 bytes - should trigger error before write
    fake.script(["exec"], [_ok(b"\xff\xfe old")])
    out = sb.edit_file("bin.dat", "old", "new")
    assert out.startswith("ERROR:")
    assert "not valid UTF-8" in out
    # Verify no write was attempted (1 read, 0 writes)
    heads = [c for c in fake.calls if "/usr/bin/head" in c[0]]
    writes = [c for c in fake.calls if "cat > \"$1\"" in " ".join(c[0])]
    assert len(heads) == 1
    assert len(writes) == 0


def test_list_dir_shapes_output(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b"d\t96\tsrc\nf\t18\tREADME.md\n"))
    out = sb.list_dir(".")
    assert "src/" in out
    assert "README.md  (18 bytes)" in out
    assert fake.calls[-1][0] == [
        "exec", "-w", "/work", "dw-abc123",
        "/usr/bin/find", ".", "-mindepth", "1", "-maxdepth", "1",
        "-printf", "%y\t%s\t%f\n",
    ]


def test_list_dir_caps_entries(started):
    from dirtywork.tools import MAX_LIST_ENTRIES
    sb, fake, run_dir = started
    lines = "".join(f"f\t1\tfile{i}\n" for i in range(MAX_LIST_ENTRIES + 50))
    fake.script(["exec"], _ok(lines.encode()))
    out = sb.list_dir(".")
    assert "capped" in out
    assert out.count("(1 bytes)") == MAX_LIST_ENTRIES


def test_grep_exec_argv_and_strips_leading_dot_slash(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b"./src/app.py:2:    return 42\n"))
    out = sb.grep("return 42")
    assert "src/app.py:2" in out
    assert "./" not in out
    assert fake.calls[-1][0] == [
        "exec", "-w", "/work", "dw-abc123",
        "/usr/bin/rg", "-n", "--no-heading", "-M", "300", "-e", "return 42", ".",
    ]


def test_grep_glob_appends_dash_g_flag(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b""))
    sb.grep("foo", glob="*.py")
    argv = fake.calls[-1][0]
    assert "-g" in argv and argv[argv.index("-g") + 1] == "*.py"


def test_grep_no_match(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b""))
    out = sb.grep("zzz_not_present")
    assert "No matches" in out


def test_grep_timeout_returns_error_text(started):
    sb, fake, run_dir = started

    def raise_timeout(argv, *, timeout, stdin=None):
        fake.calls.append((list(argv), timeout, stdin))
        raise DockerError("docker exec ... timed out after 40s")

    sb._run = raise_timeout
    out = sb.grep("foo", timeout=30)
    assert "timed out" in out.lower()


def test_bash_exec_argv_and_shaping(started):
    sb, fake, run_dir = started
    # Script the top and inspect calls that _reap() will make after bash
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    fake.script(["exec"], _ok(b"hi\n"))
    out = sb.bash("echo hi")
    assert "exit code: 0" in out
    assert "hi" in out
    # Find the last exec call (which is the bash command)
    exec_calls = [c for c in fake.calls if "exec" in c[0] and "/bin/bash" in str(c[0])]
    assert len(exec_calls) == 1
    argv, timeout, stdin = exec_calls[-1]
    assert argv == [
        "exec", "-w", "/work", "dw-abc123",
        "/bin/bash", "-c", 'ulimit -f 524288; exec bash -c "$1"', "_", "echo hi",
    ]
    assert timeout == 130  # 120s default + 10


def test_bash_blocked_command_never_execs(started):
    sb, fake, run_dir = started
    out = sb.bash("sudo ls")
    assert out.startswith("BLOCKED:")
    assert not fake.calls


def test_bash_timeout_returns_text_not_raise(started):
    sb, fake, run_dir = started
    real_run = sb._run
    def run_with_timeout(argv, *, timeout, stdin=None):
        # Only the model's own bash exec times out; docker top/inspect keep working.
        if "sleep 600" in " ".join(argv):
            fake.calls.append((list(argv), timeout, stdin))
            raise DockerError("docker exec ... timed out after 1s")
        return real_run(argv, timeout=timeout, stdin=stdin)
    sb._run = run_with_timeout
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    out = sb.bash("sleep 600", timeout=1)
    assert "timed out after 1s" in out
    assert not any(c[0][:1] == ["kill"] for c in fake.calls)  # healthy container: no reset


def test_bash_nonzero_exit_reported(started):
    sb, fake, run_dir = started
    from dirtywork.procs import Captured
    # Script the top and inspect calls that _reap() will make after bash
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    fake.script(["exec"], Captured(returncode=3, output=b"", truncated=False, timed_out=False))
    out = sb.bash("exit 3")
    assert "exit code: 3" in out


def test_bash_output_capped(started):
    sb, fake, run_dir = started
    from dirtywork.procs import Captured
    # Script the top and inspect calls that _reap() will make after bash
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    fake.script(["exec"], Captured(returncode=0, output=b"x" * 100, truncated=True, timed_out=False))
    out = sb.bash("big output")
    assert "capped" in out


def test_list_dir_falls_back_to_ls_when_no_gnu_find(started):
    sb, fake, run_dir = started
    # Script find --version to fail (no GNU find), then ls -1Ap, then wc -c
    # The _probe makes a call first to check for GNU find (fails)
    # Then list_dir uses the fallback which needs ls -1Ap and wc -c (2 more calls)
    fake.script(["exec"], [_fail(b"find: not found"), _ok(b"b.txt\nsub/\na.txt\n"), _ok(b"3 b.txt\n5 a.txt\n8 total\n")])
    out = sb.list_dir(".")
    assert out == "a.txt  (5 bytes)\nb.txt  (3 bytes)\nsub/"
    # Verify exactly three exec calls: one for find probe, one for ls -1Ap, one for wc -c
    # No per-file stat calls should exist
    assert len(fake.calls) == 3
    for call in fake.calls:
        assert "/usr/bin/stat" not in call[0]


def test_list_dir_fallback_passes_target_dir(started):
    sb, fake, run_dir = started
    # Script find --version to fail (no GNU find), then ls with "src", then wc -c
    fake.script(["exec"], [_fail(b""), _ok(b"file.txt\n"), _ok(b"10 file.txt\n10 total\n")])
    out = sb.list_dir("src")
    assert "file.txt  (10 bytes)" in out
    # Verify the ls exec's argv contains "src" (the target dir is passed)
    assert any("src" in str(call[0]) for call in fake.calls)


def test_grep_falls_back_to_grep_rn_when_no_rg(started):
    sb, fake, run_dir = started
    # Script rg --version to fail (no ripgrep), then grep -rn for search
    fake.script(["exec"], [_fail(b"rg: not found"), _ok(b"src/app.py:2:hello\n")])
    out = sb.grep("hello")
    assert "src/app.py:2" in out
    # Verify the exec uses grep -rn (fallback), not rg
    assert "-rn" in fake.calls[-1][0]
    assert "/usr/bin/rg" not in fake.calls[-1][0]


def test_reap_resets_and_writes_sandbox_reset_event_on_stray_process(started_with_transcript):
    import json
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["top"], _ok(
        _TOP_HEADER
        + b"501  1  0  0  10:00  ?  00:00:00  cat\n"
        + b"501  42  1  0  10:00  ?  00:00:00  sleep 300\n"
    ))
    fake.script(["exec"], _ok(b"ok\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))

    sb.bash("echo ok")
    transcript.close()

    events = [json.loads(l) for l in (run_dir / "transcript.jsonl").read_text().splitlines()]
    reset_events = [e for e in events if e["event"] == "sandbox_reset"]
    assert reset_events and reset_events[0]["reason"] == "stray process after bash"
    assert any(c[0][0] == "kill" for c in fake.calls)


def test_reap_resets_on_oom(started_with_transcript):
    import json
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["exec"], _ok(b"ok\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"true\n"))

    sb.bash("echo ok")
    transcript.close()

    events = [json.loads(l) for l in (run_dir / "transcript.jsonl").read_text().splitlines()]
    reset_events = [e for e in events if e["event"] == "sandbox_reset"]
    assert reset_events and reset_events[0]["reason"] == "oom"


def test_reset_uses_restart_variant_init(started_with_transcript):
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["top"], _ok(
        _TOP_HEADER
        + b"501  1  0  0  10:00  ?  00:00:00  cat\n"
        + b"501  42  1  0  10:00  ?  00:00:00  sleep 300\n"
    ))
    fake.script(["exec"], _ok(b"ok\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))

    sb.bash("echo ok")

    # Filter out worktree sample execs (which also use /bin/sh)
    init_calls = [c for c in fake.calls if c[0][0] == "exec" and "/bin/sh" in c[0]
                  and not any("du -sk /work" in str(arg) for arg in c[0])]
    assert init_calls
    last_init_script = init_calls[-1][0][-1]
    assert "git read-tree HEAD" in last_init_script
    assert "read-tree -m -u HEAD" not in last_init_script


def test_reset_creates_a_fresh_tether(started_with_transcript):
    sb, fake, run_dir, transcript = started_with_transcript
    popens_before = len(fake.popens)
    fake.script(["top"], _ok(
        _TOP_HEADER
        + b"501  1  0  0  10:00  ?  00:00:00  cat\n"
        + b"501  42  1  0  10:00  ?  00:00:00  sleep 300\n"
    ))
    fake.script(["exec"], _ok(b"ok\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))

    sb.bash("echo ok")

    assert len(fake.popens) == popens_before + 1


def test_reset_can_be_called_directly(started_with_transcript):
    import json
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["exec"], _ok())

    sb.reset("manual test reset")
    transcript.close()

    events = [json.loads(l) for l in (run_dir / "transcript.jsonl").read_text().splitlines()]
    assert any(e["event"] == "sandbox_reset" and e["reason"] == "manual test reset" for e in events)


def test_reap_resets_when_docker_top_itself_fails(started_with_transcript):
    # A container killed while a docker exec was in flight (Task 16's live
    # lifecycle case) makes the SUBSEQUENT `docker top` call fail outright —
    # not "succeeds but shows a stray row". That must ALSO trigger a reset.
    import json
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["top"], _fail(b"Error: No such container: dw-abc123"))
    fake.script(["exec"], _ok(b"ok\n"))

    sb.bash("echo ok")  # must not raise — reap recovers via reset
    transcript.close()

    events = [json.loads(l) for l in (run_dir / "transcript.jsonl").read_text().splitlines()]
    reset_events = [e for e in events if e["event"] == "sandbox_reset"]
    assert reset_events and reset_events[0]["reason"] == "container unreachable after bash"
    assert any(c[0][0] == "kill" for c in fake.calls)


def test_reap_allows_bare_cat_tether(started):
    sb, fake, run_dir = started
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    fake.script(["exec"], _ok(b"ok\n"))

    out = sb.bash("echo ok")

    assert "ok" in out
    assert not any(c[0][0] == "kill" for c in fake.calls)


def test_reap_allows_docker_init_tether(started):
    sb, fake, run_dir = started
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  /sbin/docker-init -- cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    fake.script(["exec"], _ok(b"ok\n"))

    out = sb.bash("echo ok")

    assert not any(c[0][0] == "kill" for c in fake.calls)


def test_reset_raises_when_container_does_not_come_back(started, monkeypatch):
    # Make _wait_ready fail fast by monkeypatching the lifecycle timeout
    import dirtywork.sandbox.docker as docker_module
    monkeypatch.setattr(docker_module.docker_cli, "T_LIFECYCLE", 0.2)
    
    sb, fake, run_dir = started
    # Script exec to fail (the ready-wait /bin/true exec)
    fake.script(["exec"], _fail(b""))
    
    with pytest.raises(SandboxError):
        sb.reset("x")


def test_start_creates_watchdog_with_configured_caps_but_does_not_start_thread(docker, tmp_path):
    sb, fake, run_dir = docker
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    sb.start(worktree, repo, "abc123", "deadbeef" * 5)

    assert sb.watchdog is not None
    assert sb.watchdog.min_free_mb == sb.cfg.min_free_mb
    assert sb.watchdog.max_worktree_mb == sb.cfg.max_worktree_mb
    assert sb.watchdog.max_worktree_files == sb.cfg.max_worktree_files
    assert not sb.watchdog.is_alive()


def test_bash_calls_watchdog_note_start_and_end(started):
    sb, fake, run_dir = started
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    fake.script(["exec"], _ok(b"ok\n"))
    events = []
    sb.watchdog.note_bash_start = lambda: events.append("start")
    sb.watchdog.note_bash_end = lambda: events.append("end")

    sb.bash("echo ok")

    assert events == ["start", "end"]


def test_bash_raises_budget_exceeded_when_watchdog_violation_already_set(started):
    from dirtywork.budget import BudgetExceeded
    sb, fake, run_dir = started
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    fake.script(["exec"], _ok(b"ok\n"))
    sb.watchdog.violation = "pre-existing violation for this test"

    with pytest.raises(BudgetExceeded, match="pre-existing violation"):
        sb.bash("echo ok")


def test_bash_watchdog_detects_over_cap_sample_and_raises(started):
    from dirtywork.budget import BudgetExceeded
    sb, fake, run_dir = started
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    fake.script(["exec"], _ok(b"ok\n"))
    fake.script(_SAMPLE_ARGV, _ok(b"3145728\t/work\n10\n"))  # 3 GB, over the 2048 MB default

    with pytest.raises(BudgetExceeded, match="worktree exceeds"):
        sb.bash("echo ok")

    assert any(c[0][0] == "kill" for c in fake.calls)


def test_sample_worktree_failure_then_success_after_reset(started):
    sb, fake, run_dir = started
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    fake.script(["exec"], _ok(b"ok\n"))
    fake.script(_SAMPLE_ARGV, [_fail(b"exec failed: pid saturation"), _ok(b"1024\t/work\n5\n")])

    out = sb.bash("echo ok")  # must not raise

    assert "ok" in out
    assert any(c[0][0] == "kill" for c in fake.calls)  # the sample-failure reset


def test_sample_worktree_failure_twice_raises_sandboxerror(started):
    sb, fake, run_dir = started
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    fake.script(["exec"], _ok(b"ok\n"))
    fake.script(_SAMPLE_ARGV, _fail(b"exec failed: pid saturation"))  # always fails

    with pytest.raises(SandboxError, match="sample failed twice"):
        sb.bash("echo ok")


def test_finalize_stops_container_calls_export_run_and_host_read_tree(started, monkeypatch):
    from dirtywork.sandbox import RunArtifacts
    sb, fake, run_dir = started
    seen = {}

    def fake_export_run(cfg, **kwargs):
        seen["export_kwargs"] = kwargs
        return RunArtifacts(export_status="ok", diff_stat="stat", worktree_bytes=10, worktree_files=1)

    def fake_host_read_tree(worktree):
        seen["host_read_tree_worktree"] = worktree

    import dirtywork.sandbox.docker as docker_mod
    monkeypatch.setattr(docker_mod.export, "export_run", fake_export_run)
    monkeypatch.setattr(docker_mod, "host_read_tree", fake_host_read_tree)

    artifacts = sb.finalize()

    assert artifacts.export_status == "ok"
    assert seen["export_kwargs"]["slug"] == "abc123"
    assert seen["host_read_tree_worktree"] == sb._worktree
    assert any(c[0][:2] == ["rm", "-f"] for c in fake.calls)  # worker container removed


def test_stop_after_finalize_keeps_volume_when_export_failed(started, monkeypatch):
    from dirtywork.sandbox import RunArtifacts
    sb, fake, run_dir = started

    import dirtywork.sandbox.docker as docker_mod
    monkeypatch.setattr(docker_mod.export, "export_run",
                         lambda cfg, **kw: RunArtifacts(export_status="export_failed: worktree not empty"))
    monkeypatch.setattr(docker_mod, "host_read_tree", lambda worktree: None)

    sb.finalize()
    fake.calls.clear()
    sb.stop()

    assert not any(c[0][:2] == ["volume", "rm"] for c in fake.calls)


def test_stop_after_finalize_removes_volume_when_export_succeeded(started, monkeypatch):
    from dirtywork.sandbox import RunArtifacts
    sb, fake, run_dir = started

    import dirtywork.sandbox.docker as docker_mod
    monkeypatch.setattr(docker_mod.export, "export_run",
                         lambda cfg, **kw: RunArtifacts(export_status="ok"))
    monkeypatch.setattr(docker_mod, "host_read_tree", lambda worktree: None)

    sb.finalize()
    fake.calls.clear()
    sb.stop()

    assert any(c[0][:2] == ["volume", "rm"] for c in fake.calls)


def test_finalize_skips_host_read_tree_when_export_failed(started, monkeypatch):
    from dirtywork.sandbox import RunArtifacts
    sb, fake, run_dir = started

    import dirtywork.sandbox.docker as docker_mod
    monkeypatch.setattr(docker_mod.export, "export_run",
                         lambda cfg, **kw: RunArtifacts(export_status="export_failed: boom"))
    host_read_tree_called = []

    def track_host_read_tree(worktree):
        host_read_tree_called.append(worktree)

    monkeypatch.setattr(docker_mod, "host_read_tree", track_host_read_tree)

    artifacts = sb.finalize()

    assert artifacts.export_status == "export_failed: boom"
    assert not host_read_tree_called  # host_read_tree should NOT be called on export failure
    assert sb._export_failed is True
