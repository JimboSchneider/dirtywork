from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

import pytest

from dirtywork.procs import Captured
from dirtywork.resume import stash_dir_for
from dirtywork.sandbox import SandboxError
from dirtywork.sandbox import docker as docker_mod
from dirtywork.sandbox.docker import DockerSandbox, docker_cli
from dirtywork.sandbox import strays
from dirtywork.sandbox.docker_args import DockerConfig
from tests.docker_fakes import FakeDocker, FakePopen, _fail, _ok, _rc

DockerError = docker_cli.DockerError

_TOP_HEADER = b"UID  PID  PPID  C  STIME  TTY  TIME  CMD\n"

_DISCOVERY_ARGV = ["exec", "-w", "/work", "dw-abc123", "/bin/sh", "-c", strays.TETHER_DISCOVERY_SCRIPT]
_KILL_ARGV = ["exec", "-w", "/work", "dw-abc123", "/bin/sh", "-c", strays.STRAY_KILL_SCRIPT, "_", "7"]
_SWEEP_ARGV = ["exec", "-w", "/work", "dw-abc123"] + strays.LOCK_SWEEP_ARGV
_OOM_ARGV = ["inspect", "--format", "{{.State.OOMKilled}}"]
_SAMPLE_ARGV = ["exec", "-w", "/work", "dw-abc123", "/bin/sh", "-c",
                "du -sk /work; find /work | wc -l"]


def _is_write_exec(call) -> bool:
    """True for a recorded docker call that ran DockerSandbox's write script.
    ONE place, so the next change to that script text touches one line instead
    of ten (spec §2.6's counted churn). `call` is FakeDocker's
    (argv, timeout, stdin) triple; the script is a single argv ELEMENT, so
    membership -- not a substring search over a joined string -- is the exact
    test."""
    return docker_mod.WRITE_SCRIPT in call[0]


def _is_append_write_exec(call) -> bool:
    """True for a recorded docker call that ran the append write script."""
    return docker_mod.APPEND_WRITE_SCRIPT in call[0]


def _script_append_guard(fake, response) -> None:
    """Script the append guard exec (the FIRST of append_file's three)."""
    fake.script(["exec", "-w", "/work", "dw-abc123", "/bin/sh", "-c",
                 docker_mod.APPEND_GUARD_SCRIPT], response)


@pytest.fixture()
def docker(tmp_path: Path):
    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["image", "inspect", "--format", "{{.Id}}"],
                _ok(b"sha256:" + b"a" * 64))
    fake.script(["volume", "create"], _ok())
    fake.script(["run"], _ok())   # prep container
    fake.script(["create"], _ok())  # worker create
    fake.script(["exec"], _ok())  # ready-wait /bin/true and init
    # #61: the in-place stray rung is ACTIVE by default -- discovery answers pid 7,
    # the kill and the lock sweep succeed; a test that wants the reset path scripts
    # the kill exec to a nonzero rc.
    fake.script(_DISCOVERY_ARGV, _ok(b"7\n"))
    fake.script(_KILL_ARGV, _ok())
    fake.script(_SWEEP_ARGV, _ok())
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
    fake.call_caps.clear()
    return sb, fake, run_dir


@pytest.fixture()
def started_with_transcript(tmp_path: Path):
    from dirtywork.transcript import Transcript
    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["image", "inspect", "--format", "{{.Id}}"],
                _ok(b"sha256:" + b"a" * 64))
    fake.script(["volume", "create"], _ok())
    fake.script(["run"], _ok())
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())
    fake.script(_DISCOVERY_ARGV, _ok(b"7\n"))
    fake.script(_KILL_ARGV, _ok())
    fake.script(_SWEEP_ARGV, _ok())
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
    fake.call_caps.clear()
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
    assert sb.image_ref == "sha256:" + "a" * 64
    assert isinstance(sb.uid, int)
    assert isinstance(sb.gid, int)


def test_start_uses_provided_image_ref_without_resolving_again(tmp_path):
    # Fix item 2: __main__'s docker preflight already resolved the tag to a
    # digest (and recorded it in run.json) before the sandbox is even
    # constructed -- start() must use that digest verbatim, not spend a
    # second `docker image inspect`/`pull` round trip re-resolving it.
    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["volume", "create"], _ok())
    fake.script(["run"], _ok())
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())
    cfg = DockerConfig()
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    preresolved = "dirtywork/worker@sha256:" + "d" * 64
    sb = DockerSandbox(cfg, run_dir=run_dir, image_ref=preresolved, run=fake.run, popen=fake.popen)
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    sb.start(worktree, repo, "abc123", "deadbeef" * 5)

    assert sb.image_ref == preresolved
    assert not any(c[0][:2] == ["image", "inspect"] for c in fake.calls)
    assert not any(c[0][0] == "pull" for c in fake.calls)


def test_start_resolves_image_when_no_image_ref_given(docker, tmp_path):
    # The fallback path (image_ref=None, the constructor default): every
    # other caller, including these tests, still gets the old behavior.
    sb, fake, run_dir = docker
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    sb.start(worktree, repo, "abc123", "deadbeef" * 5)

    assert any(c[0][:2] == ["image", "inspect"] for c in fake.calls)


def test_start_custom_image_resolves_without_pinning(tmp_path):
    # When cfg.image != DEFAULT_IMAGE, start() resolves it without checking
    # the digest pin. To verify: configure the fake to return a RepoDigests
    # value that would NOT match PINNED_DIGEST; start() must succeed,
    # proving the pin was never checked.
    from dirtywork.sandbox.docker_args import DEFAULT_IMAGE
    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["image", "inspect", "--format", "{{.Id}}"],
                _ok(b"sha256:" + b"b" * 64))
    # Return a RepoDigests value that would NOT match PINNED_DIGEST
    fake.script(["image", "inspect", "--format", "{{json .RepoDigests}}"],
                _ok(b'["custom/img:1@sha256:' + b"c" * 64 + b'"]'))
    fake.script(["volume", "create"], _ok())
    fake.script(["run"], _ok())   # prep container
    fake.script(["create"], _ok())  # worker create
    fake.script(["exec"], _ok())  # ready-wait /bin/true and init
    cfg = DockerConfig(image="custom/img:1")
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    sb = DockerSandbox(cfg, run_dir=run_dir, run=fake.run, popen=fake.popen)
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    # This should succeed without raising SandboxError about a mismatched pin
    sb.start(worktree, repo, "abc123", "deadbeef" * 5)

    # Verify that image resolution occurred
    assert any(c[0][:2] == ["image", "inspect"] for c in fake.calls)
    # Verify that the custom image was used (not DEFAULT_IMAGE)
    assert cfg.image != DEFAULT_IMAGE


def test_start_refuses_on_container_collision(docker, tmp_path):
    sb, fake, run_dir = docker
    fake.script(["container", "inspect"], _ok())  # already exists
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    # Fix item 7: no phantom `dirtywork runs …` subcommand -- the manual
    # docker recipe instead (SP3 hasn't shipped `runs clean`).
    with pytest.raises(SandboxError, match=r"docker rm -f dw-abc123.*docker volume rm dw-abc123-work"):
        sb.start(worktree, repo, "abc123", "deadbeef" * 5)
    # nothing created after the collision check
    assert not any(c[0][0] == "volume" and c[0][1] == "create" for c in fake.calls)


def test_start_refuses_on_volume_collision(docker, tmp_path):
    sb, fake, run_dir = docker
    fake.script(["volume", "inspect"], _ok())  # already exists
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    with pytest.raises(SandboxError, match=r"docker rm -f dw-abc123.*docker volume rm dw-abc123-work"):
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
    fake.call_caps.clear()

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
    fake.script(["image", "inspect", "--format", "{{.Id}}"],
                _ok(b"sha256:" + b"a" * 64))
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
    fake.script(["image", "inspect", "--format", "{{.Id}}"],
                _ok(b"sha256:" + b"a" * 64))

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
    # the pre-write read-back: a new file has nothing to read, so `head` fails
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"],
                _fail(b"head: cannot open 'deep/new/file.txt': No such file or directory"))
    out = sb.write_file("deep/new/file.txt", "hello")
    assert "Wrote 5 bytes" in out
    assert "(new file, 1 line)" in out
    argv, timeout, stdin = fake.calls[-1]
    # The temp basename is random per call (spec §2.2: the worker controls
    # sibling names), so the fixed prefix is asserted exactly and the temp by
    # shape -- test_write_exec_uses_the_atomic_script_and_a_sibling_temp pins
    # the rest.
    assert argv[:10] == [
        "exec", "-w", "/work", "-i", "dw-abc123",
        "/bin/sh", "-c", docker_mod.WRITE_SCRIPT,
        "_", "deep/new/file.txt",
    ]
    assert re.fullmatch(r"deep/new/\.dw-tmp\.file\.txt\.[0-9a-f]{8}", argv[10])
    assert len(argv) == 11
    assert stdin == b"hello"


def test_write_file_refuses_dot_git(started):
    sb, fake, run_dir = started
    out = sb.write_file(".git/hooks/pre-commit", "#!/bin/sh")
    assert out.startswith("ERROR:")
    # write_file's best-effort pre-read runs before _write_raw's checks (DRY:
    # _write_raw owns the checks), so one harmless `head` exec is expected —
    # but never the actual write.
    writes = [c for c in fake.calls if _is_write_exec(c)]
    assert not writes


def test_write_file_refuses_oversized_content(started):
    from dirtywork.tools import MAX_WRITE_BYTES
    sb, fake, run_dir = started
    out = sb.write_file("big.txt", "x" * (MAX_WRITE_BYTES + 1))
    assert out.startswith("ERROR:")
    writes = [c for c in fake.calls if _is_write_exec(c)]
    assert not writes


def test_edit_file_reads_then_writes(started):
    sb, fake, run_dir = started
    fake.script(["exec"], [_ok(b"def main():\n    return 42\n"), _ok()])
    out = sb.edit_file("src/app.py", "return 42", "return 43")
    assert "Edited" in out
    heads = [c for c in fake.calls if "/usr/bin/head" in c[0]]
    writes = [c for c in fake.calls if _is_write_exec(c)]
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
    writes = [c for c in fake.calls if _is_write_exec(c)]
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


def test_read_exec_requests_a_capture_cap_above_max_read_bytes(started):
    """PR #56 review: docker_cli.run used to call run_capped with no `cap`
    at all, so every docker exec's capture -- including a file read via
    `head` -- silently stopped at procs.MAX_CAPTURE_BYTES (1 MiB), well
    under MAX_READ_BYTES (5 MiB). _read_raw's read exec must request a cap
    above what `head -c MAX_READ_BYTES+1` can ever emit, so a 1-5 MiB file
    is never cut short at 1 MiB. (RED-FIRST: pre-fix, no `cap` reaches
    docker_cli.run for this exec, so the fake records cap=None.)"""
    from dirtywork.tools import MAX_READ_BYTES
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b"hello\n"))
    sb.read_file("src/app.py")
    idx = next(i for i, c in enumerate(fake.calls) if "/usr/bin/head" in c[0])
    assert fake.call_caps[idx] == MAX_READ_BYTES + 1


def test_read_raw_refuses_a_truncated_capture_and_no_write_follows(started):
    """A Captured with truncated=True must refuse outright, even when the
    reported output is under MAX_READ_BYTES -- truncated is a defensive
    backstop for a capture cut short somewhere other than `head`'s own
    MAX_READ_BYTES+1 bound. Pre-fix, `truncated` was never consulted:
    _transform_file would proceed on the truncated content and a write exec
    would fire -- the silent data-loss scenario the owner's review found
    (edit_file writing back a truncated copy of a large file). (RED-FIRST:
    pre-fix the transform proceeds and a write exec runs.)"""
    from dirtywork.tools import MAX_READ_BYTES
    sb, fake, run_dir = started
    truncated = Captured(returncode=0, output=b"short but truncated",
                          truncated=True, timed_out=False)
    fake.script(["exec"], [truncated])
    out = sb.edit_file("big.txt", "short", "long")
    assert out.startswith("ERROR:")
    assert f"exceeds {MAX_READ_BYTES} bytes" in out
    writes = [c for c in fake.calls if _is_write_exec(c)]
    assert not writes


def test_transform_round_trips_a_file_between_one_and_five_mib_intact(started):
    """Pins the end-to-end behaviour the old 1 MiB default capture cap would
    have broken: a file between 1 and 5 MiB must round-trip through
    edit_file whole -- not silently truncated to the first 1 MiB, which
    would have made the transform operate on, and write back, a truncated
    copy of the file (silent data loss)."""
    sb, fake, run_dir = started
    body = ("x" * 79 + "\n") * ((2 * 1024 * 1024) // 80)
    content = (body + "MARKER-old\n").encode("utf-8")
    assert 1024 * 1024 < len(content) < 5 * 1024 * 1024
    fake.script(["exec"], [_ok(content), _ok()])
    out = sb.edit_file("big.txt", "MARKER-old", "MARKER-new")
    assert "Edited" in out
    writes = [c for c in fake.calls if _is_write_exec(c)]
    assert len(writes) == 1
    _write_argv, _write_timeout, write_stdin = writes[0]
    assert write_stdin is not None
    assert len(write_stdin) == len(content)  # same-length replacement
    assert write_stdin.startswith(b"x" * 79)
    assert write_stdin.endswith(b"MARKER-new\n")
    assert b"MARKER-old" not in write_stdin


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
    writes = [c for c in fake.calls if _is_write_exec(c)]
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
        raise DockerError("docker exec ... timed out after 40s", timed_out=True)

    sb._run = raise_timeout
    out = sb.grep("foo", timeout=30)
    # Unchanged wording (spec §4.2): a grep timeout is not a bash timeout.
    assert out == "ERROR: grep timed out after 30s — narrow the pattern or path."


def test_grep_generic_docker_error_is_not_reported_as_a_timeout(started):
    # Spec §4.2: before 0.9 EVERY DockerError out of grep rendered as "timed
    # out", so a killed container read as a slow search.
    sb, fake, run_dir = started

    def raise_failure(argv, *, timeout, stdin=None):
        fake.calls.append((list(argv), timeout, stdin))
        raise DockerError("No such container: dw-abc123")

    sb._run = raise_failure
    out = sb.grep("foo", timeout=30)
    assert out == "ERROR: grep failed: No such container: dw-abc123"
    assert "timed out" not in out


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


def test_bash_sandboxed_allows_host_only_git_config_but_blocks_push(started):
    # DockerSandbox.bash runs check_bash_command(..., sandboxed=True): a
    # host-only rule (git config, meaningless against the container's own
    # /gitdir) must NOT block and must reach exec, while a policy rule
    # (git push) must still block with no exec issued at all.
    sb, fake, run_dir = started
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    fake.script(["exec"], _ok(b""))

    allowed = sb.bash("git config core.hooksPath x")
    assert not allowed.startswith("BLOCKED:")
    exec_calls = [c for c in fake.calls if c[0][0] == "exec" and "/bin/bash" in str(c[0])]
    assert len(exec_calls) == 1

    fake.calls.clear()
    fake.call_caps.clear()
    blocked = sb.bash("git push origin main")
    assert blocked.startswith("BLOCKED:")
    assert not fake.calls


def test_bash_timeout_returns_text_not_raise(started):
    sb, fake, run_dir = started
    real_run = sb._run
    def run_with_timeout(argv, *, timeout, stdin=None):
        # Only the model's own bash exec times out; docker top/inspect keep working.
        if "sleep 600" in " ".join(argv):
            fake.calls.append((list(argv), timeout, stdin))
            raise DockerError("docker exec ... timed out after 1s", timed_out=True)
        return real_run(argv, timeout=timeout, stdin=stdin)
    sb._run = run_with_timeout
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    out = sb.bash("sleep 600", timeout=1)
    # Spec §4.2: the FULL canonical text -- a substring check would pass on the
    # non-timeout branch too, which is the bug this test now guards.
    assert out == (
        "ERROR: command timed out after 1s — it did not finish and its result is "
        "unknown. Re-run it with a larger timeout (up to 600) or split it into "
        "smaller commands; do not report it as passed.")
    assert not any(c[0][:1] == ["kill"] for c in fake.calls)  # healthy container: no reset


def test_bash_generic_docker_error_is_not_reported_as_a_timeout(started):
    sb, fake, run_dir = started
    real_run = sb._run

    def run_with_failure(argv, *, timeout, stdin=None):
        if "sleep 600" in " ".join(argv):
            fake.calls.append((list(argv), timeout, stdin))
            raise DockerError("No such container: dw-abc123")
        return real_run(argv, timeout=timeout, stdin=stdin)

    sb._run = run_with_failure
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    out = sb.bash("sleep 600", timeout=1)
    assert out == "ERROR: bash failed: No such container: dw-abc123"
    assert "timed out" not in out


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
    # Worker container uses gitfile layout: rm -rf /work/.git and --separate-git-dir=/gitdir
    assert "rm -rf -- /work/.git" in last_init_script
    assert "--separate-git-dir=/gitdir" in last_init_script
    # Check that no init exec has GIT_DIR= or GIT_WORK_TREE= in its env (worker uses gitfile)
    for c in init_calls:
        env_values = [c[0][i + 1] for i, a in enumerate(c[0]) if a == "-e"]
        assert not any(v.startswith("GIT_DIR=") or v.startswith("GIT_WORK_TREE=") for v in env_values)
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


def test_reset_issues_kill_then_wait_then_start_in_order(started_with_transcript):
    # Fix item 2: reset() must wait for the container to actually stop
    # (docker wait) before starting it again (docker start -ai) -- kill only
    # SIGKILLs PID 1, it does not block until the container namespace is
    # torn down, so starting immediately after kill can race a still-dying
    # container.
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["exec"], _ok())
    order = []
    orig_run = fake.run
    orig_popen = fake.popen

    def spy_run(argv, *, timeout, stdin=None):
        if argv and argv[0] in ("kill", "wait"):
            order.append(argv[0])
        return orig_run(argv, timeout=timeout, stdin=stdin)

    def spy_popen(argv, *, stdin=None, stdout=None, stderr=None):
        if len(argv) >= 2 and argv[0] == "docker" and argv[1] == "start":
            order.append("start")
        return orig_popen(argv, stdin=stdin, stdout=stdout, stderr=stderr)

    sb._run = spy_run
    sb._popen = spy_popen

    sb.reset("manual order test")

    assert order == ["kill", "wait", "start"]


def test_reset_concurrent_calls_are_serialized(started_with_transcript):
    # Fix item 4: reset() can be invoked from the watchdog thread
    # (_sample_worktree's reset path) and the main thread (_reap()) at the
    # same time. self._reset_lock must serialize the two full reset()
    # bodies so their kill/wait/start docker calls never interleave.
    import threading
    import time
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["exec"], _ok())

    order = []
    order_lock = threading.Lock()
    orig_run = fake.run
    orig_popen = fake.popen

    def spy_run(argv, *, timeout, stdin=None):
        if argv and argv[0] in ("kill", "wait"):
            time.sleep(0.02)  # widen the race window an unlocked reset() would expose
            with order_lock:
                order.append(argv[0])
        return orig_run(argv, timeout=timeout, stdin=stdin)

    def spy_popen(argv, *, stdin=None, stdout=None, stderr=None):
        if len(argv) >= 2 and argv[0] == "docker" and argv[1] == "start":
            time.sleep(0.02)
            with order_lock:
                order.append("start")
        return orig_popen(argv, stdin=stdin, stdout=stdout, stderr=stderr)

    sb._run = spy_run
    sb._popen = spy_popen

    t1 = threading.Thread(target=sb.reset, args=("thread1",))
    t2 = threading.Thread(target=sb.reset, args=("thread2",))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not t1.is_alive() and not t2.is_alive()
    # Two complete, non-interleaved kill/wait/start sequences: whichever
    # thread wins the lock finishes its whole reset() body before the other
    # even issues its "kill" -- never kill,kill,wait,wait,start,start.
    assert order == ["kill", "wait", "start"] * 2


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


def test_after_bash_skips_budget_sample_when_reap_already_reset(started_with_transcript):
    # Fix item 3: at most one reset per bash call. When _reap() resets
    # (here: `docker top` itself fails), _after_bash must skip the worktree
    # budget sample for this call -- the container was just rebuilt, so
    # there is nothing meaningful to measure yet; the next bash call
    # re-measures. Assert exactly one `kill` (the reap-triggered reset) and
    # no `du -sk /work` exec in this call.
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["top"], _fail(b"Error: No such container: dw-abc123"))
    fake.script(["exec"], _ok(b"ok\n"))

    sb.bash("echo ok")  # must not raise -- reap recovers via reset

    kill_calls = [c for c in fake.calls if c[0][0] == "kill"]
    assert len(kill_calls) == 1
    assert not any("du -sk /work" in str(arg) for c in fake.calls for arg in c[0])


def test_after_bash_raises_budget_exceeded_even_when_reap_reset_this_call(started_with_transcript):
    # Fix item 3: a violation the watchdog thread already recorded must
    # always be consumed and raised in _after_bash, even when _reap() reset
    # the container during this same call. Only the re-SAMPLING is skipped
    # after a reset, never the consumption of an already-recorded violation.
    from dirtywork.budget import BudgetExceeded
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["top"], _fail(b"Error: No such container: dw-abc123"))  # forces a reap-reset
    fake.script(["exec"], _ok(b"ok\n"))
    sb.watchdog.violation = "watchdog: worktree exceeds cap"

    with pytest.raises(BudgetExceeded, match="watchdog: worktree exceeds cap"):
        sb.bash("echo ok")

    # the reap-triggered reset still happened; no re-sample was attempted
    kill_calls = [c for c in fake.calls if c[0][0] == "kill"]
    assert len(kill_calls) == 1
    assert not any("du -sk /work" in str(arg) for c in fake.calls for arg in c[0])


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


def test_tether_pid_discovery(tmp_path):
    # Fix item a: the discovery exec runs after _wait_ready and parses PID 7
    from dirtywork.transcript import Transcript
    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["image", "inspect", "--format", "{{.Id}}"],
                _ok(b"sha256:" + b"a" * 64))
    fake.script(["volume", "create"], _ok())
    fake.script(["run"], _ok())
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())  # ready-wait
    fake.script(
        ["exec", "-w", "/work", "dw-abc123", "/bin/sh", "-c",
         strays.TETHER_DISCOVERY_SCRIPT],
        _ok(b"7\n")
    )
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

    # Verify tether pid was discovered
    assert sb._tether_pid == 7


def test_tether_discovery_failure(tmp_path, capsys):
    # Script discovery exec to rc 3 -> None and exactly one stderr line
    from dirtywork.transcript import Transcript
    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["image", "inspect", "--format", "{{.Id}}"],
                _ok(b"sha256:" + b"a" * 64))
    fake.script(["volume", "create"], _ok())
    fake.script(["run"], _ok())
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())  # ready-wait
    fake.script(
        ["exec", "-w", "/work", "dw-abc123", "/bin/sh", "-c",
         strays.TETHER_DISCOVERY_SCRIPT],
        _rc(3)
    )
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

    assert sb._tether_pid is None
    captured = capsys.readouterr()
    assert "tether pid unknown" in captured.err
    # After reset(), the flag is cleared and discovery can warn again
    fake.script(
        ["exec", "-w", "/work", "dw-abc123", "/bin/sh", "-c",
         strays.TETHER_DISCOVERY_SCRIPT],
        _rc(3)
    )
    fake.script(["exec"], _ok())
    sb.reset("y")
    captured2 = capsys.readouterr()
    # The second discovery (after reset) also warns
    assert "tether pid unknown" in captured2.err


def test_tether_discovery_output_none(tmp_path, capsys):
    # Output b"x\n" -> None (parse_tether_pid returns None)
    from dirtywork.transcript import Transcript
    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["image", "inspect", "--format", "{{.Id}}"],
                _ok(b"sha256:" + b"a" * 64))
    fake.script(["volume", "create"], _ok())
    fake.script(["run"], _ok())
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())  # ready-wait
    fake.script(
        ["exec", "-w", "/work", "dw-abc123", "/bin/sh", "-c",
         strays.TETHER_DISCOVERY_SCRIPT],
        _ok(b"x\n")
    )
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

    assert sb._tether_pid is None
    captured = capsys.readouterr()
    assert "tether pid unknown" in captured.err


def test_tether_discovery_callable_raises_dockererror(tmp_path, capsys):
    # Script callable raising DockerError -> None
    from dirtywork.transcript import Transcript
    def fail_run(argv):
        from dirtywork.sandbox.docker_cli import DockerError
        raise DockerError("test error")
    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["image", "inspect", "--format", "{{.Id}}"],
                _ok(b"sha256:" + b"a" * 64))
    fake.script(["volume", "create"], _ok())
    fake.script(["run"], _ok())
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())  # ready-wait
    fake.script(
        ["exec", "-w", "/work", "dw-abc123", "/bin/sh", "-c",
         strays.TETHER_DISCOVERY_SCRIPT],
        fail_run
    )
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

    assert sb._tether_pid is None
    captured = capsys.readouterr()
    assert "tether pid unknown" in captured.err


def test_tether_warned_flag_reset_on_new_container(tmp_path, capsys):
    # After `sb.reset("x")` the discovery exec ran again and a warned flag is reset
    from dirtywork.transcript import Transcript
    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["image", "inspect", "--format", "{{.Id}}"],
                _ok(b"sha256:" + b"a" * 64))
    fake.script(["volume", "create"], _ok())
    fake.script(["run"], _ok())
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())  # ready-wait
    fake.script(
        ["exec", "-w", "/work", "dw-abc123", "/bin/sh", "-c",
         strays.TETHER_DISCOVERY_SCRIPT],
        _rc(3)
    )
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

    # First failure - sets warned flag
    assert sb._tether_warned is True
    captured1 = capsys.readouterr()
    assert "tether pid unknown" in captured1.err

    # Reset clears the warned flag (simulates new container life)
    sb.reset("y")
    assert sb._tether_warned is True
    captured2 = capsys.readouterr()
    assert "tether pid unknown" in captured2.err  # Second container life warns again


def test_reset_with_strays_param(tmp_path):
    # sb.reset("x") direct: the sandbox_reset event has no "strays"/"strays_total" keys
    from dirtywork.transcript import Transcript
    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["image", "inspect", "--format", "{{.Id}}"],
                _ok(b"sha256:" + b"a" * 64))
    fake.script(["volume", "create"], _ok())
    fake.script(["run"], _ok())
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())  # ready-wait
    fake.script(["exec"], _ok())  # init
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
    fake.script(["exec"], _ok())  # reset will need an exec call

    sb.reset("manual test reset")
    transcript.close()

    events = [json.loads(l) for l in (run_dir / "transcript.jsonl").read_text().splitlines()]
    reset_events = [e for e in events if e["event"] == "sandbox_reset"]
    assert reset_events
    assert reset_events[0]["reason"] == "manual test reset"
    assert "strays" not in reset_events[0]
    assert "strays_total" not in reset_events[0]


def test_reset_with_strays_list(tmp_path):
    from dirtywork.transcript import Transcript
    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["image", "inspect", "--format", "{{.Id}}"],
                _ok(b"sha256:" + b"a" * 64))
    fake.script(["volume", "create"], _ok())
    fake.script(["run"], _ok())
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())  # ready-wait
    fake.script(["exec"], _ok())  # init
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
    fake.script(["exec"], _ok())  # reset will need an exec call

    sb.reset("stray process after bash", strays=["sleep 300"], strays_total=None)
    transcript.close()

    events = [json.loads(l) for l in (run_dir / "transcript.jsonl").read_text().splitlines()]
    reset_events = [e for e in events if e["event"] == "sandbox_reset"]
    assert reset_events
    assert reset_events[0]["reason"] == "stray process after bash"
    assert reset_events[0].get("strays") == ["sleep 300"]
    assert "strays_total" not in reset_events[0]


def test_reset_with_strays_and_total(tmp_path):
    from dirtywork.transcript import Transcript
    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["image", "inspect", "--format", "{{.Id}}"],
                _ok(b"sha256:" + b"a" * 64))
    fake.script(["volume", "create"], _ok())
    fake.script(["run"], _ok())
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())  # ready-wait
    fake.script(["exec"], _ok())  # init
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
    fake.script(["exec"], _ok())  # reset will need an exec call

    sb.reset("stray process after bash", strays=["sleep 300"], strays_total=25)
    transcript.close()

    events = [json.loads(l) for l in (run_dir / "transcript.jsonl").read_text().splitlines()]
    reset_events = [e for e in events if e["event"] == "sandbox_reset"]
    assert reset_events
    assert reset_events[0]["reason"] == "stray process after bash"
    assert reset_events[0].get("strays") == ["sleep 300"]
    assert reset_events[0]["strays_total"] == 25


def test_drain_notices(tmp_path):
    from dirtywork.transcript import Transcript
    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["image", "inspect", "--format", "{{.Id}}"],
                _ok(b"sha256:" + b"a" * 64))
    fake.script(["volume", "create"], _ok())
    fake.script(["run"], _ok())
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())  # ready-wait
    fake.script(["exec"], _ok())  # init
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
    fake.script(["exec"], _ok())  # reset will need an exec call

    sb.reset("test reset")
    notices = sb.drain_notices()

    assert len(notices) == 1
    kind, text = notices[0]
    assert kind == "sandbox_reset"
    assert "re-initialized" in text

    # Second drain returns []
    notices2 = sb.drain_notices()
    assert notices2 == []


def test_host_sandbox_drain_notices(tmp_path):
    from dirtywork.sandbox.host import HostSandbox
    sb = HostSandbox(tmp_path / "worktree")
    assert sb.drain_notices() == []


def test_stray_rows_parsing():
    # Test strays.stray_rows parses docker top output correctly
    assert strays.stray_rows(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n") == []
    assert strays.stray_rows(_TOP_HEADER + b"501  42  1  0  10:00  ?  00:00:00  sleep 300\n") == ["sleep 300"]


def test_cap_strays():
    # Test strays.cap_strays caps correctly
    assert strays.cap_strays([]) == ([], None)
    assert strays.cap_strays(["sleep 1"]) == (["sleep 1"], None)
    # When > MAX_STRAYS, return len and capped list
    many = [f"cmd{i}" for i in range(strays.MAX_STRAYS + 5)]
    capped, total = strays.cap_strays(many)
    assert len(capped) == strays.MAX_STRAYS
    assert total == len(many)


def test_sandbox_reset_text():
    text = strays.sandbox_reset_text("oom")
    assert "re-initialized" in text
    assert "oom" in text


def test_parse_tether_pid():
    # Test parse_tether_pid
    assert strays.parse_tether_pid(b"7\n") == 7
    assert strays.parse_tether_pid(b"12345\n") == 12345
    assert strays.parse_tether_pid(b"0\n") is None  # pid must be > 0
    assert strays.parse_tether_pid(b"abc\n") is None
    assert strays.parse_tether_pid(b"-1\n") is None


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


def test_bash_raises_sandbox_error_when_violation_kind_is_sandbox_error(started):
    # D1: a watchdog-thread sample() failure is a sandbox failure, not a
    # budget breach -- _after_bash must raise SandboxError, not
    # BudgetExceeded, when the consumed violation's kind says so.
    sb, fake, run_dir = started
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    fake.script(["exec"], _ok(b"ok\n"))
    sb.watchdog.violation = "watchdog: worktree budget sample failed twice in a row"
    sb.watchdog.violation_kind = "sandbox_error"

    with pytest.raises(SandboxError, match="worktree budget sample failed twice"):
        sb.bash("echo ok")

    # consumed, not left for a later reader, and reset to the default kind
    assert sb.watchdog.violation is None
    assert sb.watchdog.violation_kind == "budget"


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


def test_sample_worktree_no_retry_when_reset_already_happened_this_call(started):
    # D2: _sample_worktree is also the watchdog THREAD's own `sample`
    # callback (Watchdog.check_worktree_budget_once(), ticking every 5s
    # while a bash call is in flight) -- independent of _after_bash, which
    # only calls it when no reset has happened yet this call. So the race
    # this closes is thread-vs-thread: _reap() (main thread, end of a
    # PREVIOUS call within the same still-in-flight watchdog window) or an
    # earlier _sample_worktree call already reset the container and set
    # _reset_this_call, and the watchdog thread's sample ticks again before
    # that flag clears. The old guard (`attempt == 0 and not
    # self._reset_this_call`) would retry immediately with no fresh reset
    # in between, against a container that may still be mid-reset. The new
    # rule: this attempt is itself already "post-reset" (a reset already
    # happened this call) -- one failure is enough to raise, with no second
    # exec and no second reset.
    sb, fake, run_dir = started
    fake.script(_SAMPLE_ARGV, _fail(b"exec failed: pid saturation"))  # always fails
    sb._reset_this_call = True  # simulate: a reset already happened earlier this call

    with pytest.raises(SandboxError, match="worktree budget sample failed"):
        sb._sample_worktree()

    sample_calls = [c for c in fake.calls if list(c[0]) == _SAMPLE_ARGV]
    assert len(sample_calls) == 1  # exactly one measured attempt -- no retry
    assert not any(c[0][0] == "kill" for c in fake.calls)  # no reset triggered by this call


def test_sample_worktree_still_retries_once_with_reset_when_no_reset_yet_this_call(started):
    # D2 companion: the normal (no prior reset) path is unchanged -- one
    # failed attempt, exactly one reset, one re-measure.
    sb, fake, run_dir = started
    fake.script(_SAMPLE_ARGV, [_fail(b"exec failed: pid saturation"), _ok(b"1024\t/work\n5\n")])

    result = sb._sample_worktree()

    assert result == (1024, 5)
    sample_calls = [c for c in fake.calls if list(c[0]) == _SAMPLE_ARGV]
    assert len(sample_calls) == 2  # pre-reset attempt + post-reset re-measure
    assert sum(1 for c in fake.calls if c[0][0] == "kill") == 1  # exactly one reset


def test_finalize_stops_container_calls_export_run_and_host_read_tree(started, monkeypatch):
    from dirtywork.sandbox import RunArtifacts
    from dirtywork.tools import TMP_FIND_REGEX
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

    # Fix round 1 (spec §2.5 execution amendment): the stale-temp sweep runs
    # against the still-alive WORKER container -- the export container's
    # /work is readonly by design, so a sweep exec there would silently
    # no-op. One exec, same argv shape as the host sweep's regex, and it
    # must land BEFORE the `rm -f` that stops the worker container.
    argvs = [c[0] for c in fake.calls]
    sweeps = [a for a in argvs if "-regextype" in a]
    removes = [a for a in argvs if a[:2] == ["rm", "-f"]]
    assert len(sweeps) == 1
    assert sweeps[0] == ["exec", "-w", "/work", "dw-abc123", "/usr/bin/find", "/work",
                         "-type", "f", "-regextype", "posix-extended", "-regex",
                         TMP_FIND_REGEX, "-print", "-delete"]
    assert argvs.index(sweeps[0]) < argvs.index(removes[0])


def test_finalize_sweep_docker_error_is_contained_export_still_runs(started, monkeypatch, capsys):
    # Fix wave: a DockerError out of the best-effort sweep exec (timeout,
    # daemon hiccup) must never escape finalize() and skip export -- doing
    # so would leave _export_failed False while nothing was ever exported,
    # and stop() would then delete the volume out from under a transient
    # docker hiccup, destroying the run's real output. The sweep is
    # best-effort; the salvage path (export -> host_read_tree) is sacred.
    from dirtywork.sandbox import RunArtifacts
    sb, fake, run_dir = started
    real_run = sb._run

    def sweep_raises(argv, *, timeout, stdin=None):
        if "-regextype" in argv:
            fake.calls.append((list(argv), timeout, stdin))
            raise DockerError("docker exec ... timed out after 40s", timed_out=True)
        return real_run(argv, timeout=timeout, stdin=stdin)

    sb._run = sweep_raises

    export_called = []

    def fake_export_run(cfg, **kwargs):
        export_called.append(True)
        return RunArtifacts(export_status="ok", diff_stat="stat", worktree_bytes=10, worktree_files=1)

    monkeypatch.setattr(docker_mod.export, "export_run", fake_export_run)
    monkeypatch.setattr(docker_mod, "host_read_tree", lambda worktree: None)

    artifacts = sb.finalize()

    assert export_called == [True]
    assert artifacts.export_status == "ok"
    assert sb._export_failed is False
    assert "sweep failed: docker exec ... timed out after 40s" in capsys.readouterr().err

    # Volume-preserving path intact: export succeeded, so stop() removes the
    # volume exactly as it would with no sweep failure at all.
    fake.calls.clear()
    fake.call_caps.clear()
    sb.stop()
    assert any(c[0][:2] == ["volume", "rm"] for c in fake.calls)


def test_finalize_sweep_counts_only_real_temp_paths(started, monkeypatch, capsys):
    # Fix wave: Captured.output merges stderr (procs.py runs the exec with
    # stderr=subprocess.STDOUT), so a `find: permission denied` line or other
    # daemon chatter sits in the same stream as the real -print'ed paths. The
    # swept count must be lines that actually match TMP_FIND_REGEX, not just
    # non-blank lines -- one real temp path plus one permission-denied line
    # must count as 1 swept, not 2.
    from dirtywork.sandbox import RunArtifacts
    from dirtywork.tools import TMP_FIND_REGEX
    sb, fake, run_dir = started

    sweep_argv = ["exec", "-w", "/work", "dw-abc123", "/usr/bin/find", "/work",
                  "-type", "f", "-regextype", "posix-extended", "-regex",
                  TMP_FIND_REGEX, "-print", "-delete"]
    fake.script(sweep_argv, _rc(1, b"/work/.dw-tmp.foo.a1b2c3d4\n"
                                   b"find: '/work/x': Permission denied\n"))

    monkeypatch.setattr(docker_mod.export, "export_run",
                         lambda cfg, **kw: RunArtifacts(export_status="ok"))
    monkeypatch.setattr(docker_mod, "host_read_tree", lambda worktree: None)

    sb.finalize()

    err = capsys.readouterr().err
    assert "swept 1 stale temp file" in err
    assert "stale temp files" not in err  # would only appear if the count were != 1
    assert "sweep incomplete (rc 1)" in err


def test_stop_after_finalize_keeps_volume_when_export_failed(started, monkeypatch):
    from dirtywork.sandbox import RunArtifacts
    sb, fake, run_dir = started

    import dirtywork.sandbox.docker as docker_mod
    monkeypatch.setattr(docker_mod.export, "export_run",
                         lambda cfg, **kw: RunArtifacts(export_status="export_failed: worktree not empty"))
    monkeypatch.setattr(docker_mod, "host_read_tree", lambda worktree: None)

    sb.finalize()
    fake.calls.clear()
    fake.call_caps.clear()
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
    fake.call_caps.clear()
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


def test_finalize_consumes_watchdog_violation_and_still_exports(started, monkeypatch):
    # Fix item 1: a disk-floor (or fail-closed) kill that fires after the
    # model's last tool call has no bash call left to surface it via
    # _after_bash's BudgetExceeded raise -- finalize() must consume the
    # violation itself (so the run isn't reported "completed") while still
    # running the export (the volume is intact and the work is worth
    # salvaging).
    from dirtywork.sandbox import RunArtifacts
    sb, fake, run_dir = started
    sb.watchdog.violation = "host free space below 2048 MB"

    export_calls = []

    def fake_export_run(cfg, **kwargs):
        export_calls.append(kwargs)
        return RunArtifacts(export_status="ok", diff_stat="stat")

    import dirtywork.sandbox.docker as docker_mod
    monkeypatch.setattr(docker_mod.export, "export_run", fake_export_run)
    monkeypatch.setattr(docker_mod, "host_read_tree", lambda worktree: None)

    artifacts = sb.finalize()

    assert artifacts.watchdog_violation == "host free space below 2048 MB"
    assert artifacts.watchdog_violation_kind == "budget"  # default kind, unset by this test
    assert artifacts.export_status == "ok"  # export still ran to completion
    assert export_calls  # export_run was actually called
    assert sb.watchdog.violation is None  # consumed, not left for a later reader
    assert sb.watchdog.violation_kind == "budget"  # kind reset to its default too


def test_finalize_reports_watchdog_violation_kind_sandbox_error(started, monkeypatch):
    # D1: finalize() must also consume the kind so _final_status can map a
    # watchdog-thread sample failure to "sandbox_error" instead of the
    # default "budget_exceeded".
    from dirtywork.sandbox import RunArtifacts
    sb, fake, run_dir = started
    sb.watchdog.violation = "watchdog: worktree budget sample failed twice in a row"
    sb.watchdog.violation_kind = "sandbox_error"

    import dirtywork.sandbox.docker as docker_mod
    monkeypatch.setattr(docker_mod.export, "export_run",
                         lambda cfg, **kw: RunArtifacts(export_status="ok"))
    monkeypatch.setattr(docker_mod, "host_read_tree", lambda worktree: None)

    artifacts = sb.finalize()

    assert artifacts.watchdog_violation == "watchdog: worktree budget sample failed twice in a row"
    assert artifacts.watchdog_violation_kind == "sandbox_error"


def test_finalize_reports_no_watchdog_violation_when_none_occurred(started, monkeypatch):
    from dirtywork.sandbox import RunArtifacts
    sb, fake, run_dir = started

    import dirtywork.sandbox.docker as docker_mod
    monkeypatch.setattr(docker_mod.export, "export_run",
                         lambda cfg, **kw: RunArtifacts(export_status="ok"))
    monkeypatch.setattr(docker_mod, "host_read_tree", lambda worktree: None)

    artifacts = sb.finalize()

    assert artifacts.watchdog_violation is None
    assert artifacts.watchdog_violation_kind is None


def _started_worktree(tmp_path):
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /somewhere\n")
    return repo, worktree


def _init_script(fake) -> str:
    inits = [c for c in fake.calls if "/usr/bin/git init" in " ".join(c[0])]
    assert inits, "no in-container git init call recorded"
    return " ".join(inits[0][0])


def test_start_default_branch_is_dirtywork_slug(docker, tmp_path):
    sb, fake, run_dir = docker
    repo, worktree = _started_worktree(tmp_path)
    sb.start(worktree, repo, "abc123", "deadbeef" * 5)
    script = _init_script(fake)
    # Worker container uses gitfile layout
    assert "refs/heads/dirtywork/abc123" in script
    assert "read-tree -m -u HEAD" in script
    assert "--separate-git-dir=/gitdir" in script
    assert "rm -rf -- /work/.git" in script
    # Check that no init exec has GIT_DIR= or GIT_WORK_TREE= in its env (worker uses gitfile)
    inits = [c for c in fake.calls if "/usr/bin/git init" in " ".join(c[0])]
    for c in inits:
        env_values = [c[0][i + 1] for i, a in enumerate(c[0]) if a == "-e"]
        assert not any(v.startswith("GIT_DIR=") or v.startswith("GIT_WORK_TREE=") for v in env_values)
    assert not [p for p in fake.popens if p.argv[:1] == ["tar"]]


def test_start_seed_from_worktree_uses_restart_init_and_tar_pipeline(docker, tmp_path):
    sb, fake, run_dir = docker
    repo, worktree = _started_worktree(tmp_path)
    sb.start(worktree, repo, "new1", "deadbeef" * 5,
             branch="dirtywork/orig", seed_from_worktree=True)
    script = _init_script(fake)
    # Worker container uses gitfile layout
    assert "refs/heads/dirtywork/orig" in script
    assert "read-tree HEAD" in script and "read-tree -m -u HEAD" not in script
    assert "--separate-git-dir=/gitdir" in script
    assert "rm -rf -- /work/.git" in script
    tar_out = [p for p in fake.popens if p.argv[:1] == ["tar"]]
    tar_in = [p for p in fake.popens if p.argv[:2] == ["docker", "exec"] and "-xf" in p.argv]
    assert len(tar_out) == 1
    assert tar_out[0].argv == ["tar", "-C", str(worktree), "--exclude=./.git", "-cf", "-", "."]
    assert tar_out[0].env is not None and tar_out[0].env.get("COPYFILE_DISABLE") == "1"
    assert len(tar_in) == 1
    assert "-i" in tar_in[0].argv and "dw-new1" in tar_in[0].argv
    assert tar_in[0].argv[-5:] == ["tar", "-C", "/work", "-xf", "-"]
    assert sb.container == "dw-new1" and sb.volume == "dw-new1-work"


def test_seed_failure_raises_sandbox_error(docker, tmp_path):
    sb, fake, run_dir = docker
    repo, worktree = _started_worktree(tmp_path)
    real_popen = fake.popen

    def failing_popen(argv, **kw):
        p = real_popen(argv, **kw)
        if argv[:2] == ["docker", "exec"] and "-xf" in argv:
            p.returncode = 1          # FakePopen.wait() keeps a preset returncode
        return p

    sb._popen = failing_popen
    with pytest.raises(SandboxError, match="resume seed failed"):
        sb.start(worktree, repo, "new1", "deadbeef" * 5,
                 branch="dirtywork/orig", seed_from_worktree=True)


def test_finalize_stashes_seeded_worktree_and_removes_stash_after_ok_export(docker, tmp_path, monkeypatch):
    from dirtywork.sandbox.export import RunArtifacts
    sb, fake, run_dir = docker
    repo, worktree = _started_worktree(tmp_path)
    sb.start(worktree, repo, "new1", "deadbeef" * 5,
             branch="dirtywork/orig", seed_from_worktree=True)
    (worktree / "left.txt").write_text("x")
    seen = {}

    def fake_export_run(cfg, **kw):
        seen["entries"] = sorted(p.name for p in kw["worktree"].iterdir())
        seen["stash_has_left"] = stash_dir_for(worktree, "new1") / "left.txt"
        seen["stash_has_left"] = seen["stash_has_left"].exists()
        return RunArtifacts(export_status="ok")

    monkeypatch.setattr("dirtywork.sandbox.docker.export.export_run", fake_export_run)
    monkeypatch.setattr("dirtywork.sandbox.docker.host_read_tree", lambda wt: None)
    sb.finalize()
    assert seen["entries"] == [".git"]           # export saw an empty worktree
    assert seen["stash_has_left"] is True         # the prior work was moved aside, not deleted
    assert not stash_dir_for(worktree, "new1").exists()  # stash removed after ok


def test_finalize_restores_seeded_worktree_after_failed_export(docker, tmp_path, monkeypatch):
    from dirtywork.sandbox.export import RunArtifacts
    sb, fake, run_dir = docker
    repo, worktree = _started_worktree(tmp_path)
    sb.start(worktree, repo, "new1", "deadbeef" * 5,
             branch="dirtywork/orig", seed_from_worktree=True)
    (worktree / "left.txt").write_text("x")
    (worktree / "sub").mkdir()
    (worktree / "sub" / "deep.txt").write_text("y")

    def failing_export_run(cfg, **kw):
        return RunArtifacts(export_status="export_failed: worktree too big")

    monkeypatch.setattr("dirtywork.sandbox.docker.export.export_run", failing_export_run)
    monkeypatch.setattr("dirtywork.sandbox.docker.host_read_tree", lambda wt: None)
    artifacts = sb.finalize()
    assert artifacts.export_status.startswith("export_failed")
    assert (worktree / "left.txt").read_text() == "x"
    assert (worktree / "sub" / "deep.txt").read_text() == "y"
    assert (worktree / ".git").is_file()
    assert not stash_dir_for(worktree, "new1").exists()


def test_finalize_leaves_unseeded_worktree_alone(docker, tmp_path, monkeypatch):
    from dirtywork.sandbox.export import RunArtifacts
    sb, fake, run_dir = docker
    repo, worktree = _started_worktree(tmp_path)
    sb.start(worktree, repo, "abc123", "deadbeef" * 5)
    (worktree / "left.txt").write_text("x")   # export_run itself would refuse; here it is faked
    seen = {}

    def fake_export_run(cfg, **kw):
        seen["entries"] = sorted(p.name for p in kw["worktree"].iterdir())
        return RunArtifacts(export_status="ok")

    monkeypatch.setattr("dirtywork.sandbox.docker.export.export_run", fake_export_run)
    monkeypatch.setattr("dirtywork.sandbox.docker.host_read_tree", lambda wt: None)
    sb.finalize()
    assert seen["entries"] == [".git", "left.txt"]


def test_stash_never_clears_a_foreign_stash(docker, tmp_path, monkeypatch):
    from dirtywork.sandbox.export import RunArtifacts
    sb, fake, run_dir = docker
    repo, worktree = _started_worktree(tmp_path)
    leftover = worktree.parent / f"{worktree.name}.pre-resume-older"
    leftover.mkdir()
    (leftover / "precious.txt").write_text("from an interrupted resume")
    sb.start(worktree, repo, "new1", "deadbeef" * 5,
             branch="dirtywork/orig", seed_from_worktree=True)
    monkeypatch.setattr("dirtywork.sandbox.docker.export.export_run",
                        lambda cfg, **kw: RunArtifacts(export_status="ok"))
    monkeypatch.setattr("dirtywork.sandbox.docker.host_read_tree", lambda wt: None)
    sb.finalize()
    assert (leftover / "precious.txt").read_text() == "from an interrupted resume"
    assert not stash_dir_for(worktree, "new1").exists()


def test_finalize_restores_stash_when_export_raises(docker, tmp_path, monkeypatch):
    sb, fake, run_dir = docker
    repo, worktree = _started_worktree(tmp_path)
    sb.start(worktree, repo, "new1", "deadbeef" * 5,
             branch="dirtywork/orig", seed_from_worktree=True)
    (worktree / "left.txt").write_text("x")

    def exploding_export_run(cfg, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr("dirtywork.sandbox.docker.export.export_run", exploding_export_run)
    with pytest.raises(RuntimeError, match="boom"):
        sb.finalize()
    assert (worktree / "left.txt").read_text() == "x"
    assert not stash_dir_for(worktree, "new1").exists()


def test_write_file_over_existing_content_echoes_a_diff(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"],
                _ok(b"def main():\n    return 42\n"))
    out = sb.write_file("src/app.py", "def main():\n    return 43\n")
    assert out.startswith("Wrote src/app.py: +1 -1 (removed 1 non-blank line)")
    assert "-    return 42" in out and "+    return 43" in out


def test_edit_file_echoes_a_diff(started):
    sb, fake, run_dir = started
    fake.script(["exec"], [_ok(b"def main():\n    return 42\n"), _ok()])
    out = sb.edit_file("src/app.py", "return 42", "return 43")
    assert out.startswith("Edited src/app.py: +1 -1 (removed 1 non-blank line)")
    assert "--- a/src/app.py" in out and "+++ b/src/app.py" in out


def test_insert_after_reads_then_writes(started):
    sb, fake, run_dir = started
    fake.script(["exec"], [_ok(b"alpha\nbeta\ngamma\n"), _ok()])
    out = sb.insert_after("cfg.txt", "beta", "beta-plus")
    assert out.startswith("Inserted into cfg.txt: +1 -0")
    heads = [c for c in fake.calls if "/usr/bin/head" in c[0]]
    writes = [c for c in fake.calls if _is_write_exec(c)]
    assert len(heads) == 1
    assert len(writes) == 1
    assert writes[0][2] == b"alpha\nbeta\nbeta-plus\ngamma\n"


def test_insert_before_refuses_a_repeated_anchor(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b"aa\naa\n"))
    out = sb.insert_before("dup.txt", "aa", "x")
    assert out.startswith("ERROR: anchor occurs 2 times in dup.txt")
    assert not [c for c in fake.calls if _is_write_exec(c)]


def test_apply_edits_reads_then_writes(started):
    sb, fake, run_dir = started
    fake.script(["exec"], [_ok(b"one\ntwo\n"), _ok()])
    out = sb.apply_edits("batch.txt", [{"old": "one", "new": "1"},
                                       {"old": "two", "new": "2"}])
    assert out.startswith("Applied 2 edits to batch.txt: ")
    heads = [c for c in fake.calls if "/usr/bin/head" in c[0]]
    writes = [c for c in fake.calls if _is_write_exec(c)]
    assert len(heads) == 1 and len(writes) == 1        # one read, one write, per batch
    assert writes[0][2] == b"1\n2\n"                    # the batch's final text


def test_apply_edits_rollback_never_writes(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b"one\ntwo\n"))
    out = sb.apply_edits("batch.txt", [{"old": "one", "new": "1"},
                                       {"old": "nope", "new": "x"}])
    # Byte-identical to the host's text (spec §1.8 parity), and no write exec.
    assert out == ("ERROR: edit 2 of 2: old text occurs 0 times in batch.txt; it must "
                   "occur exactly once (after edits 1..1 are applied); no edits applied")
    assert not [c for c in fake.calls if _is_write_exec(c)]


def test_apply_edits_matches_the_host_text_for_success_and_every_refusal(started, tmp_path):
    """Spec §1.8: host/docker parity scoped to matching, success and rollback."""
    from dirtywork import tools
    sb, fake, run_dir = started
    wt = tmp_path / "parity"
    wt.mkdir()
    cases = [
        ("one\ntwo\n", [{"old": "one", "new": "1"}, {"old": "two", "new": "2"}]),
        ("one\ntwo\n", [{"old": "one", "new": "1"}, {"old": "nope", "new": "x"}]),
        ("one\ntwo\n", [{"old": "", "new": "x"}]),
        ("aa\naa\n", [{"old": "aa", "new": "b"}]),
        ("one\ntwo\n", [{"old": "one"}]),           # missing "new" (host+docker parity, M6)
        ("one\ntwo\n", ["one"]),                     # not a dict at all
        ("one\ntwo\n", [{"old": "one", "new": 2}]),  # "new" not a string
    ]
    for content, edits in cases:
        (wt / "f.txt").write_text(content)
        host_out = tools.apply_edits(wt, "f.txt", edits)
        fake.script(["exec"], [_ok(content.encode("utf-8")), _ok()])
        docker_out = sb.apply_edits("f.txt", edits)
        assert docker_out == host_out


def test_transform_result_over_the_write_cap_is_refused(started):
    from dirtywork.tools import MAX_WRITE_BYTES
    sb, fake, run_dir = started
    huge = "x" * (MAX_WRITE_BYTES + 1)
    expected = (f"ERROR: result is {MAX_WRITE_BYTES + 2} bytes, over the "
                f"{MAX_WRITE_BYTES}-byte write limit; nothing was written")
    fake.script(["exec"], _ok(b"seed\n"))
    assert sb.edit_file("grow.txt", "seed", huge) == expected
    fake.script(["exec"], _ok(b"seed\n"))
    assert sb.apply_edits("grow.txt", [{"old": "seed", "new": huge}]) == expected
    assert not [c for c in fake.calls if _is_write_exec(c)]


# --- spec §2.6/§5.1: the atomic container write script and the tool-aware
# --- UTF-8 refusal.


def test_write_exec_uses_the_atomic_script_and_a_sibling_temp(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"],
                _fail(b"head: cannot open 'deep/new/file.txt'"))
    sb.write_file("deep/new/file.txt", "hello")
    argv, timeout, stdin = fake.calls[-1]
    assert argv[:8] == ["exec", "-w", "/work", "-i", "dw-abc123",
                        "/bin/sh", "-c", docker_mod.WRITE_SCRIPT]
    assert argv[8] == "_"
    assert argv[9] == "deep/new/file.txt"
    # The temp is a SIBLING of the target (same directory => same filesystem =>
    # `mv` is an atomic rename) and its name is generated HOST-side, so worker
    # bytes never reach the script text.
    assert re.fullmatch(r"deep/new/\.dw-tmp\.file\.txt\.[0-9a-f]{8}", argv[10])
    assert len(argv) == 11
    assert stdin == b"hello"
    # Spec §2.6: `&&`-chained, never move INTO a directory, guards echo their
    # own diagnostic, the temp is removed on any failure.
    assert 'mv -fT -- "$2" "$1"' in docker_mod.WRITE_SCRIPT
    assert 'chmod --reference="$1" "$2" 2>/dev/null || chmod 644 "$2"' in docker_mod.WRITE_SCRIPT
    assert '{ rm -f -- "$2"; exit 1; }' in docker_mod.WRITE_SCRIPT


def test_transform_non_utf8_refusals_name_the_tool_and_match_the_host(started, tmp_path):
    """Spec §5.1: docker's wording becomes the host's, and names the tool the
    model actually called -- never the legacy `refusing to edit`."""
    from dirtywork import tools
    sb, fake, run_dir = started
    wt = tmp_path / "utf8parity"
    wt.mkdir()
    (wt / "bin.dat").write_bytes(b"\xff\xfe old")
    cases = [
        ("edit_file", lambda: sb.edit_file("bin.dat", "old", "new"),
         lambda: tools.edit_file(wt, "bin.dat", "old", "new")),
        ("insert_before", lambda: sb.insert_before("bin.dat", "old", "x"),
         lambda: tools.insert_before(wt, "bin.dat", "old", "x")),
        ("insert_after", lambda: sb.insert_after("bin.dat", "old", "x"),
         lambda: tools.insert_after(wt, "bin.dat", "old", "x")),
        ("apply_edits", lambda: sb.apply_edits("bin.dat", [{"old": "old", "new": "new"}]),
         lambda: tools.apply_edits(wt, "bin.dat", [{"old": "old", "new": "new"}])),
    ]
    for tool, docker_call, host_call in cases:
        fake.script(["exec"], _ok(b"\xff\xfe old"))
        docker_out = docker_call()
        assert docker_out == (f"ERROR: bin.dat is not valid UTF-8 text; {tool} only "
                              f"works on text files")
        assert docker_out == host_call()
        assert "refusing to edit" not in docker_out
    assert not [c for c in fake.calls if _is_write_exec(c)]


def test_read_raw_without_a_tool_keeps_the_legacy_wording(started):
    # No shipped caller reaches this branch since 0.10 (every strict read names
    # its tool). It stays so a direct caller of this private method gets a
    # coherent refusal instead of "None only works on text files".
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b"\xff\xfe"))
    text, err = sb._read_raw("bin.dat", strict=True)
    assert text is None
    assert err == "ERROR: 'bin.dat' is not valid UTF-8; refusing to edit"


# --- spec §1.2 (docker half) / §2.6: append_file in the container.


def test_append_file_takes_three_execs_guard_read_write(started):
    sb, fake, run_dir = started
    _script_append_guard(fake, _ok(b"4\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"], _ok(b"one\n"))
    fake.script(["exec", "-w", "/work", "-i", "dw-abc123", "/bin/sh", "-c",
                 docker_mod.APPEND_WRITE_SCRIPT], _ok())
    out = sb.append_file("notes.md", "two\n")
    assert out.startswith("Appended to notes.md: +1 -0")
    guards = [c for c in fake.calls if docker_mod.APPEND_GUARD_SCRIPT in c[0]]
    heads = [c for c in fake.calls if "/usr/bin/head" in c[0]]
    writes = [c for c in fake.calls if _is_append_write_exec(c)]
    assert len(guards) == 1 and len(heads) == 1 and len(writes) == 1
    assert fake.calls.index(guards[0]) < fake.calls.index(heads[0]) < fake.calls.index(writes[0])
    assert writes[0][2] == b"two\n"          # only the NEW bytes go on stdin
    assert not [c for c in fake.calls if _is_write_exec(c)]   # never write_file's script


def test_append_file_write_script_shape(started):
    sb, fake, run_dir = started
    _script_append_guard(fake, _ok(b"4\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"], _ok(b"one\n"))
    fake.script(["exec", "-w", "/work", "-i", "dw-abc123", "/bin/sh", "-c",
                 docker_mod.APPEND_WRITE_SCRIPT], _ok())
    sb.append_file("deep/notes.md", "two\n")
    argv = [c for c in fake.calls if _is_append_write_exec(c)][0][0]
    assert argv[:8] == ["exec", "-w", "/work", "-i", "dw-abc123",
                        "/bin/sh", "-c", docker_mod.APPEND_WRITE_SCRIPT]
    assert argv[8] == "_" and argv[9] == "deep/notes.md"
    assert re.fullmatch(r"deep/\.dw-tmp\.notes\.md\.[0-9a-f]{8}", argv[10])
    assert len(argv) == 11
    # Spec §2.6: the missing-target guard is re-checked at write time, the copy
    # is made before the append, and the promote tail is shared with WRITE_SCRIPT.
    assert docker_mod.APPEND_WRITE_SCRIPT.startswith('[ -f "$1" ] || exit 2; ')
    assert 'cp -- "$1" "$2" && cat >> "$2"' in docker_mod.APPEND_WRITE_SCRIPT
    assert docker_mod.APPEND_WRITE_SCRIPT.endswith(docker_mod._PROMOTE)
    assert docker_mod.WRITE_SCRIPT.endswith(docker_mod._PROMOTE)
    # Fix round 1: the write script's own writability guard (WRITE_SCRIPT's
    # counterpart) sits between the missing-target check and the copy.
    assert ('[ -w "$1" ] || { echo "cannot append to $1: Permission denied" '
            '>&2; exit 1; }') in docker_mod.APPEND_WRITE_SCRIPT
    # Fix round 1: the guard script refuses a symlink (dangling included)
    # BEFORE the existence/regular-file checks, and stats through -L.
    assert docker_mod.APPEND_GUARD_SCRIPT.startswith('[ ! -h "$1" ] || exit 3; ')
    assert docker_mod.APPEND_GUARD_SCRIPT.endswith('stat -Lc %s -- "$1"')


def test_append_file_guard_rc2_is_the_does_not_exist_string(started):
    sb, fake, run_dir = started
    _script_append_guard(fake, _rc(2))
    out = sb.append_file("nope.md", "x")
    assert out == ("ERROR: cannot append to 'nope.md': it does not exist; create it "
                   "with write_file first")
    assert not [c for c in fake.calls if "/usr/bin/head" in c[0]]      # no read
    assert not [c for c in fake.calls if _is_append_write_exec(c)]     # no write


def test_append_file_guard_rc3_refuses_before_any_read(started):
    # Spec §1.2: a FIFO/device/directory is refused by the GUARD, before any
    # reader exec exists that a FIFO could block.
    sb, fake, run_dir = started
    _script_append_guard(fake, _rc(3))
    out = sb.append_file("pipe", "x")
    assert out == "ERROR: cannot append to 'pipe': not a regular file"
    assert not [c for c in fake.calls if "/usr/bin/head" in c[0]]
    assert not [c for c in fake.calls if _is_append_write_exec(c)]


def test_append_file_guard_size_over_the_read_cap_uses_the_result_wording(started):
    from dirtywork.tools import MAX_READ_BYTES, MAX_WRITE_BYTES
    sb, fake, run_dir = started
    _script_append_guard(fake, _ok(f"{MAX_READ_BYTES + 1}\n".encode()))
    out = sb.append_file("huge.txt", "y")
    # `_read_raw` alone discards the size (`head -c N+1` only proves "exceeds"),
    # which is exactly why the guard exec reports it: docker can name the
    # EXACT number even for a file it will never read.
    assert out == (f"ERROR: result is {MAX_READ_BYTES + 2} bytes, over the "
                   f"{MAX_WRITE_BYTES}-byte write limit; nothing was written")
    assert not [c for c in fake.calls if "/usr/bin/head" in c[0]]


def test_append_file_guard_size_plus_text_over_the_write_cap(started):
    from dirtywork.tools import MAX_READ_BYTES, MAX_WRITE_BYTES
    sb, fake, run_dir = started
    _script_append_guard(fake, _ok(f"{MAX_READ_BYTES}\n".encode()))
    out = sb.append_file("atlimit.txt", "y" * 100)
    assert out == (f"ERROR: result is {MAX_READ_BYTES + 100} bytes, over the "
                   f"{MAX_WRITE_BYTES}-byte write limit; nothing was written")
    assert not [c for c in fake.calls if "/usr/bin/head" in c[0]]


def test_append_file_oversized_text_argument_costs_no_exec(started):
    from dirtywork.tools import MAX_WRITE_BYTES
    sb, fake, run_dir = started
    out = sb.append_file("notes.md", "x" * (MAX_WRITE_BYTES + 1))
    assert out == (f"ERROR: text is {MAX_WRITE_BYTES + 1} bytes, over the "
                   f"{MAX_WRITE_BYTES}-byte write limit; append in smaller pieces")
    assert not fake.calls                       # capped BEFORE any exec
    assert "write the file in smaller pieces" not in out   # never _oversized's wording


def test_append_file_non_utf8_names_append_file(started):
    sb, fake, run_dir = started
    _script_append_guard(fake, _ok(b"8\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"], _ok(b"\xff\xfe old"))
    out = sb.append_file("bin.dat", "text")
    assert out == ("ERROR: bin.dat is not valid UTF-8 text; append_file only works "
                   "on text files")
    assert "refusing to edit" not in out
    assert not [c for c in fake.calls if _is_append_write_exec(c)]


def test_append_file_write_exec_rc2_still_refuses_as_missing(started):
    # Spec §2.6: a delete BETWEEN the guard exec and the write exec still
    # refuses correctly, because the write script re-checks `[ -f "$1" ]`.
    sb, fake, run_dir = started
    _script_append_guard(fake, _ok(b"4\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"], _ok(b"one\n"))
    fake.script(["exec", "-w", "/work", "-i", "dw-abc123", "/bin/sh", "-c",
                 docker_mod.APPEND_WRITE_SCRIPT], _rc(2))
    out = sb.append_file("notes.md", "two\n")
    assert out == ("ERROR: cannot append to 'notes.md': it does not exist; create it "
                   "with write_file first")


def test_append_file_write_exec_failure_wraps_stderr(started):
    sb, fake, run_dir = started
    _script_append_guard(fake, _ok(b"4\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"], _ok(b"one\n"))
    fake.script(["exec", "-w", "/work", "-i", "dw-abc123", "/bin/sh", "-c",
                 docker_mod.APPEND_WRITE_SCRIPT], _fail(b"cp: cannot stat: I/O error"))
    out = sb.append_file("notes.md", "two\n")
    assert out == "ERROR: cannot append to 'notes.md': cp: cannot stat: I/O error"


def test_append_file_refuses_dot_git(started):
    sb, fake, run_dir = started
    out = sb.append_file(".git/config", "\tfoo = bar\n")
    assert out == "ERROR: writing inside .git/ is not allowed (got '.git/config')"
    assert not fake.calls


def test_append_file_matches_the_host_text_for_every_shared_refusal(started, tmp_path):
    """Spec §1.2: the three caps, the does-not-exist string and the non-UTF-8
    string are byte-identical in both modes. (The non-regular-file refusal is
    deliberately NOT shared -- see this task's header.)"""
    from dirtywork import tools
    sb, fake, run_dir = started
    wt = tmp_path / "appendparity"
    wt.mkdir()

    # Cap 1: the text argument.
    huge_text = "x" * (tools.MAX_WRITE_BYTES + 1)
    assert sb.append_file("f.txt", huge_text) == tools.append_file(wt, "f.txt", huge_text)

    # The does-not-exist refusal.
    _script_append_guard(fake, _rc(2))
    assert sb.append_file("f.txt", "x") == tools.append_file(wt, "f.txt", "x")

    # Cap 2: a file too large to read.
    (wt / "f.txt").write_bytes(b"x" * (tools.MAX_READ_BYTES + 1))
    _script_append_guard(fake, _ok(f"{tools.MAX_READ_BYTES + 1}\n".encode()))
    assert sb.append_file("f.txt", "y") == tools.append_file(wt, "f.txt", "y")

    # Cap 3: the result over the write limit.
    (wt / "f.txt").write_bytes(b"x" * tools.MAX_READ_BYTES)
    _script_append_guard(fake, _ok(f"{tools.MAX_READ_BYTES}\n".encode()))
    assert sb.append_file("f.txt", "y" * 100) == tools.append_file(wt, "f.txt", "y" * 100)

    # The non-UTF-8 refusal.
    (wt / "f.txt").write_bytes(b"\xff\xfe old")
    _script_append_guard(fake, _ok(b"8\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"], _ok(b"\xff\xfe old"))
    assert sb.append_file("f.txt", "text") == tools.append_file(wt, "f.txt", "text")


# --- Fix round 1 (spec/plan amended 2026-08-23): post-read result re-check
# restores cap-3 parity when the guard's snapshot goes stale between the
# guard exec and the read exec.


def test_append_file_read_exceeds_traps_to_result_cap(started):
    # The guard approved a small size, but the read exec's own cap fires
    # (a race: the file grew between the two execs). This must surface the
    # result-cap string -- never _read_raw's "exceeds ... refusing to read",
    # which is read_file's noun, not append's -- and must cost no write exec.
    from dirtywork.tools import MAX_READ_BYTES, MAX_WRITE_BYTES
    sb, fake, run_dir = started
    _script_append_guard(fake, _ok(b"4\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"],
                _ok(b"x" * (MAX_READ_BYTES + 1)))
    out = sb.append_file("racy.txt", "y")
    assert out == (f"ERROR: result is 5 bytes, over the "
                   f"{MAX_WRITE_BYTES}-byte write limit; nothing was written")
    assert not [c for c in fake.calls if _is_append_write_exec(c)]


def test_append_file_post_read_size_over_the_write_cap_traps_to_result_cap(started):
    # The guard approved a small size, the read exec itself stays under
    # MAX_READ_BYTES (so _read_raw succeeds), but the ACTUAL content read
    # plus the new text exceeds MAX_WRITE_BYTES -- the guard's snapshot was a
    # moment old. Must refuse with the recomputed sum and cost no write exec.
    from dirtywork.tools import MAX_READ_BYTES, MAX_WRITE_BYTES
    sb, fake, run_dir = started
    _script_append_guard(fake, _ok(b"4\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"],
                _ok(b"x" * MAX_READ_BYTES))
    out = sb.append_file("racy2.txt", "y" * 100)
    assert out == (f"ERROR: result is {MAX_READ_BYTES + 100} bytes, over the "
                   f"{MAX_WRITE_BYTES}-byte write limit; nothing was written")
    assert not [c for c in fake.calls if _is_append_write_exec(c)]


def test_docker_write_file_still_writes_when_the_pre_read_is_oversized(started):
    # Spec §6: the pre-read is DECORATION on the write. An unreadable "before"
    # picture must not stop the write; the result just reads as a new file.
    from dirtywork.tools import MAX_READ_BYTES
    sb, fake, run_dir = started
    fake.script(["exec"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"],
                _ok(b"x" * (MAX_READ_BYTES + 1)))
    out = sb.write_file("big.txt", "replacement")
    assert out == "Wrote 11 bytes to big.txt (new file, 1 line)"
    assert len([c for c in fake.calls if _is_write_exec(c)]) == 1


def test_docker_write_file_still_writes_when_the_pre_read_is_not_utf8(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"], _ok(b"\xff\xfe"))
    out = sb.write_file("bin.dat", "now text\n")
    assert out == "Wrote 9 bytes to bin.dat (new file, 1 line)"
    assert len([c for c in fake.calls if _is_write_exec(c)]) == 1


# --------------------------------------------------------------------------- #61 stray ladder
_TOP_TETHER_ONLY = (_TOP_HEADER
                    + b"501  1  0  0  10:00  ?  00:00:00  /sbin/docker-init -- /bin/cat\n"
                    + b"501  7  1  0  10:00  ?  00:00:00  /bin/cat\n")
_TOP_WITH_SLEEP = _TOP_TETHER_ONLY + b"501  42  1  0  10:00  ?  00:00:00  sleep 300\n"


def _events(run_dir, transcript):
    transcript.close()
    return [json.loads(l) for l in (run_dir / "transcript.jsonl").read_text().splitlines()]


def _no_settle(monkeypatch):
    monkeypatch.setattr(docker_mod.time, "sleep", lambda s: None)


def test_reap_kills_stray_in_place(started_with_transcript, monkeypatch):
    _no_settle(monkeypatch)
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["top"], [_ok(_TOP_WITH_SLEEP), _ok(_TOP_TETHER_ONLY)])
    fake.script(_OOM_ARGV, _ok(b"false\n"))
    sb.bash("true")
    argvs = [c[0] for c in fake.calls]
    assert not any(a[:1] == ["kill"] for a in argvs)
    assert argvs.count(_KILL_ARGV) == 1
    assert _SWEEP_ARGV in argvs
    assert sum(1 for a in argvs if a[:1] == ["top"]) == 2
    tops = [i for i, a in enumerate(argvs) if a[:1] == ["top"]]
    oom = next(i for i, a in enumerate(argvs) if a[:3] == _OOM_ARGV)
    assert tops[1] < oom < argvs.index(_SWEEP_ARGV)
    assert _SAMPLE_ARGV in argvs  # the post-call budget sample still ran
    assert sb._reset_this_call is False
    kills = [e for e in _events(run_dir, transcript) if e["event"] == "stray_kill"]
    assert kills[0]["strays"] == ["sleep 300"]
    assert "strays_total" not in kills[0] and "locks_removed" not in kills[0]
    notices = sb.drain_notices()
    assert len(notices) == 1 and notices[0][0] == "stray_kill"
    assert notices[0][1].startswith("The sandbox killed 1 background process ")
    assert sb.drain_notices() == []


def test_reap_sweep_reports_locks(started_with_transcript, monkeypatch):
    _no_settle(monkeypatch)
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["top"], [_ok(_TOP_WITH_SLEEP), _ok(_TOP_TETHER_ONLY)])
    fake.script(_OOM_ARGV, _ok(b"false\n"))
    fake.script(_SWEEP_ARGV, _ok(b"/gitdir/index.lock\0/gitdir/gc.pid\0"))
    sb.bash("true")
    kill = [e for e in _events(run_dir, transcript) if e["event"] == "stray_kill"][0]
    assert kill["locks_removed"] == ["/gitdir/index.lock", "/gitdir/gc.pid"]
    assert "locks_removed_total" not in kill
    assert "Stale git lock files" in sb.drain_notices()[0][1]


def test_reap_sweep_docker_error_keeps_stray_kill(started_with_transcript, monkeypatch, capsys):
    _no_settle(monkeypatch)
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["top"], [_ok(_TOP_WITH_SLEEP), _ok(_TOP_TETHER_ONLY)])
    fake.script(_OOM_ARGV, _ok(b"false\n"))

    def boom(argv):
        raise docker_cli.DockerError("boom")
    fake.script(_SWEEP_ARGV, boom)
    sb.bash("true")
    events = _events(run_dir, transcript)
    kill = [e for e in events if e["event"] == "stray_kill"][0]
    assert "locks_removed" not in kill
    assert not [e for e in events if e["event"] == "sandbox_reset"]
    assert not any(c[0][:1] == ["kill"] for c in fake.calls)
    assert "lock sweep incomplete" in capsys.readouterr().err


def test_reap_escalates_when_kill_fails(started_with_transcript, monkeypatch):
    _no_settle(monkeypatch)
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["top"], _ok(_TOP_WITH_SLEEP))
    fake.script(_KILL_ARGV, _rc(3))
    sb.bash("true")
    assert any(c[0][:2] == ["kill", "dw-abc123"] for c in fake.calls)
    events = _events(run_dir, transcript)
    resets = [e for e in events if e["event"] == "sandbox_reset"]
    assert resets[0]["reason"] == "stray process after bash" and resets[0]["strays"] == ["sleep 300"]
    assert not [e for e in events if e["event"] == "stray_kill"]


def test_reap_escalates_after_three_dirty_looks(started_with_transcript, monkeypatch):
    _no_settle(monkeypatch)
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["top"], [_ok(_TOP_WITH_SLEEP)] * 4)
    sb.bash("true")
    assert sum(1 for c in fake.calls if c[0][:1] == ["top"]) == 4
    events = _events(run_dir, transcript)
    assert [e for e in events if e["event"] == "sandbox_reset"][0]["strays"] == ["sleep 300"]
    assert not [e for e in events if e["event"] == "stray_kill"]


def test_reap_settles_on_third_look(started_with_transcript, monkeypatch):
    _no_settle(monkeypatch)
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["top"], [_ok(_TOP_WITH_SLEEP), _ok(_TOP_WITH_SLEEP), _ok(_TOP_TETHER_ONLY)])
    fake.script(_OOM_ARGV, _ok(b"false\n"))
    sb.bash("true")
    assert sum(1 for c in fake.calls if c[0][:1] == ["top"]) == 3
    assert [e for e in _events(run_dir, transcript) if e["event"] == "stray_kill"]


def test_reap_recheck_unreachable_resets(started_with_transcript, monkeypatch):
    _no_settle(monkeypatch)
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["top"], [_ok(_TOP_WITH_SLEEP), _rc(1)])
    sb.bash("true")
    resets = [e for e in _events(run_dir, transcript) if e["event"] == "sandbox_reset"]
    assert resets[0]["reason"] == "container unreachable after bash"


def test_reap_no_tether_pid_resets_without_kill(started_with_transcript, monkeypatch):
    _no_settle(monkeypatch)
    sb, fake, run_dir, transcript = started_with_transcript
    sb._tether_pid = None
    fake.script(["top"], _ok(_TOP_WITH_SLEEP))
    sb.bash("true")
    assert _KILL_ARGV not in [c[0] for c in fake.calls]
    assert [e for e in _events(run_dir, transcript) if e["event"] == "sandbox_reset"][0]["strays"] == ["sleep 300"]


def test_reap_oom_after_clean_kill_resets(started_with_transcript, monkeypatch):
    _no_settle(monkeypatch)
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["top"], [_ok(_TOP_WITH_SLEEP), _ok(_TOP_TETHER_ONLY)])
    fake.script(_OOM_ARGV, _ok(b"true\n"))
    sb.bash("true")
    assert _SWEEP_ARGV not in [c[0] for c in fake.calls]
    events = _events(run_dir, transcript)
    reset = [e for e in events if e["event"] == "sandbox_reset"][0]
    assert reset["reason"] == "oom" and reset["strays"] == ["sleep 300"]
    assert not [e for e in events if e["event"] == "stray_kill"]


def test_reap_caps_strays(started_with_transcript, monkeypatch):
    _no_settle(monkeypatch)
    sb, fake, run_dir, transcript = started_with_transcript
    rows = b"501  200  1  0  10:00  ?  00:00:00  " + b"x" * 300 + b"\n"
    rows += b"".join(b"501  %d  1  0  10:00  ?  00:00:00  sleep %d\n" % (100 + i, i) for i in range(24))
    fake.script(["top"], [_ok(_TOP_TETHER_ONLY + rows), _ok(_TOP_TETHER_ONLY)])
    fake.script(_OOM_ARGV, _ok(b"false\n"))
    sb.bash("true")
    kill = [e for e in _events(run_dir, transcript) if e["event"] == "stray_kill"][0]
    assert len(kill["strays"]) == 20 and kill["strays_total"] == 25
    assert len(kill["strays"][0]) == 201 and kill["strays"][0].endswith("\u2026")

import threading

def test_reap_holds_lock_during_worktree_sampling(started, monkeypatch):
    # Test that _reap holds _reap_lock during its entire body, preventing
    # concurrent non-blocking samples from running docker calls
    sb, fake, run_dir = started

    # Track when the top command starts and finishes
    entered_event = threading.Event()
    release_event = threading.Event()

    # Script top to set an event when entered, then wait for release
    def script_top(argv):
        entered_event.set()
        # Wait for release, but timeout after 5 seconds
        if not release_event.wait(timeout=5):
            raise RuntimeError("release_event timeout")
        return _ok(_TOP_TETHER_ONLY)

    fake.script(["top"], script_top)
    fake.script(_OOM_ARGV, _ok(b"false\n"))

    # Record the number of calls before we start
    calls_before = len(fake.calls)

    # Run sb.bash on a thread (this will call _reap which holds the lock)
    bash_result = [None]
    bash_error = [None]

    def run_bash():
        try:
            bash_result[0] = sb.bash("true")
        except Exception as e:
            bash_error[0] = e

    bash_thread = threading.Thread(target=run_bash)
    bash_thread.start()

    # Wait for _reap's top command to be entered
    assert entered_event.wait(timeout=5), "top didn't start within timeout"

    # Now try a non-blocking sample - should return None immediately without making docker calls
    result = sb._sample_worktree(wait=False)
    assert result is None, "Non-blocking sample should return None when lock is held"

    # Verify no docker calls were made for sampling
    sample_calls_after = [c for c in fake.calls if list(c[0]) == _SAMPLE_ARGV]
    assert len(sample_calls_after) == 0, "No sample calls should be made while lock is held"

    # Release the lock by setting release_event
    release_event.set()

    # Wait for bash thread to complete (with timeout)
    bash_thread.join(timeout=5)
    assert not bash_thread.is_alive(), "Bash thread did not complete within timeout"

    # Verify the sample call happened after we released
    sample_calls_after = [c for c in fake.calls if list(c[0]) == _SAMPLE_ARGV]
    assert len(sample_calls_after) >= 1, "Sample call should be made after lock is released"

    # Now a blocking sample should work
    result = sb._sample_worktree(wait=True)
    assert result == (1024, 5), "Blocking sample should succeed after lock is released"


def test_sample_worktree_wait_false_with_reset(started, monkeypatch):
    # Test wait=False with failing measure and _reset_this_call False
    sb, fake, run_dir = started
    # Script sample to fail first, then succeed (after reset)
    fake.script(_SAMPLE_ARGV, [_fail(b"exec failed"), _ok(b"1024\t/work\n5\n")])

    # First call with wait=False should reset once and return success
    result = sb._sample_worktree(wait=False)

    # Verify exactly one reset was called
    kill_calls = [c for c in fake.calls if c[0][0] == "kill"]
    assert len(kill_calls) == 1, "Should have exactly one reset"

    # Result should be successful
    assert result == (1024, 5), "Should return success after one reset"

    # Second call with _reset_this_call True should NOT reset
    sb._reset_this_call = True
    fake.script(_SAMPLE_ARGV, _fail(b"exec failed again"))  # will fail but no reset
    result = sb._sample_worktree(wait=False)
    assert result is None, "Should return None with _reset_this_call True"


def test_sample_worktree_wait_false_failing_twice(started, monkeypatch):
    # Test wait=False with sample failing twice - should return None without exception
    sb, fake, run_dir = started
    # Script sample to always fail
    fake.script(_SAMPLE_ARGV, _fail(b"exec failed"))

    # First call with wait=False should reset once and return None on second failure
    result = sb._sample_worktree(wait=False)

    # Verify exactly one reset was called
    kill_calls = [c for c in fake.calls if c[0][0] == "kill"]
    assert len(kill_calls) == 1, "Should have exactly one reset"

    # Result should be None (no exception)
    assert result is None, "Should return None on second failure with wait=False"


def test_sample_worktree_wait_false_with_reset_already_happened(started):
    # Test wait=False when _reset_this_call is already True
    sb, fake, run_dir = started
    sb._reset_this_call = True

    # Script sample to fail
    fake.script(_SAMPLE_ARGV, _fail(b"exec failed"))

    # Should return None without trying to reset
    result = sb._sample_worktree(wait=False)

    # Verify no reset was called
    kill_calls = [c for c in fake.calls if c[0][0] == "kill"]
    assert len(kill_calls) == 0, "Should not reset when _reset_this_call is True"

    assert result is None, "Should return None"


def test_reap_dont_deadlock_with_concurrent_reset(started, monkeypatch):
    # Lock order is _reap_lock -> _reset_lock (spec #61 §3.6). A reset() running on
    # another thread holds _reset_lock; _reap on this side runs its top / kill /
    # re-checks under _reap_lock meanwhile, and only its OWN reset (the escalation
    # rung) waits for the other reset to finish. Neither side can deadlock, and the
    # two resets are serialized by _reset_lock.
    _no_settle(monkeypatch)
    sb, fake, run_dir = started
    call_order = []
    release = threading.Event()

    def blocking_kill(argv):
        call_order.append("kill-start")
        assert release.wait(timeout=5), "reset kill never released"
        call_order.append("kill-end")
        return _ok()

    fake.script(["kill"], blocking_kill)
    fake.script(["wait"], lambda argv: _ok())
    fake.script(["top"], _ok(_TOP_WITH_SLEEP))  # a stray row on every look
    fake.script(_KILL_ARGV, _rc(3))             # the in-place kill fails -> _reap escalates
    t_reset = threading.Thread(target=lambda: sb.reset("concurrent test"))
    t_reap = threading.Thread(target=sb._reap)
    t_reset.start()
    time.sleep(0.2)
    t_reap.start()
    time.sleep(0.2)
    release.set()
    t_reset.join(timeout=5)
    t_reap.join(timeout=5)
    assert not t_reset.is_alive() and not t_reap.is_alive(), "deadlock"
    kills = [c for c in call_order if c.startswith("kill")]
    assert kills == ["kill-start", "kill-end", "kill-start", "kill-end"], call_order
