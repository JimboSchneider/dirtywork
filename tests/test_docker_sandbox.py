from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest

from dirtywork.procs import Captured
from dirtywork.sandbox import SandboxError
from dirtywork.sandbox.docker import DockerSandbox
from dirtywork.sandbox.docker_args import DockerConfig


class FakePopen:
    """Stand-in for subprocess.Popen, used both for the `docker start -ai`
    tether (only .argv and .stdin matter there) and for streamed commands
    like `git diff`/`git archive` whose stdout the caller reads (Task 11)
    — .stdout is a real io.BytesIO pre-loaded with `stdout_data` so callers
    can .read() it exactly like a real pipe. .stdin is a real io.BytesIO so
    callers can .write()/.close(); .wait()/.poll()/.kill() are scripted
    to look like a clean-running process unless a test overrides
    .returncode directly."""

    def __init__(self, argv, *, stdin=None, stdout=None, stderr=None, stdout_data: bytes = b""):
        self.argv = list(argv)
        self.stdin = io.BytesIO() if stdin == subprocess.PIPE else None
        self.stdout = io.BytesIO(stdout_data) if stdout == subprocess.PIPE else None
        self.returncode = None
        self.killed = False

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def terminate(self):
        self.kill()


class FakeDocker:
    """Scriptable stand-in for docker_cli.run and subprocess.Popen, shared by
    every DockerSandbox/export unit test in this plan.

    `script(prefix, response)` maps an argv-prefix tuple to either a single
    Captured (always returned for matching calls) or a list of Captured
    (popped in call order; the last item repeats once the list is down to
    one element, so a test can script "fails twice then succeeds" with
    [fail, fail, ok] and further matching calls keep returning ok). Any argv
    with no matching prefix gets `.default` (returncode 0, empty output) so
    a test only scripts the calls it cares about. When more than one
    registered prefix matches a call, the LONGEST (most specific) prefix
    wins — this lets a test register a broad default like `["exec"]` for
    "any exec call returns ok" and separately override one specific exec
    call (e.g. Task 9's worktree-size sample, which is also a `docker exec`
    under the same `["exec", ...]` prefix) without the broad default
    shadowing it. Every call is recorded in `.calls` (list of
    (argv, timeout, stdin)) for order/content assertions; every FakePopen
    created is recorded in `.popens`.

    `script_popen_stdout(prefix, data)` maps an argv-prefix tuple (matched
    the same longest-prefix-wins way, against the argv passed to `popen()`
    — which for every real call in this codebase is `["docker", ...]`,
    since `popen` is always called with the full `docker` argv already
    prefixed, unlike `run()` which prefixes it internally) to the bytes a
    FakePopen's `.stdout` should yield. Used by Task 11's export-flow tests
    to feed a real in-memory tar into `git archive`'s simulated stdout.
    """

    def __init__(self):
        self.responses = {}
        self.popen_stdout = {}
        self.calls = []
        self.popens = []
        self.default = Captured(returncode=0, output=b"", truncated=False, timed_out=False)

    def script(self, prefix, response) -> None:
        self.responses[tuple(prefix)] = response

    def script_popen_stdout(self, prefix, data: bytes) -> None:
        self.popen_stdout[tuple(prefix)] = data

    def run(self, argv, *, timeout, stdin=None):
        self.calls.append((list(argv), timeout, stdin))
        best_prefix = None
        best_response = None
        for prefix, response in self.responses.items():
            if tuple(argv[: len(prefix)]) == prefix:
                if best_prefix is None or len(prefix) > len(best_prefix):
                    best_prefix, best_response = prefix, response
        if best_prefix is None:
            return self.default
        if isinstance(best_response, list):
            if len(best_response) > 1:
                return best_response.pop(0)
            return best_response[0]
        return best_response

    def popen(self, argv, *, stdin=None, stdout=None, stderr=None):
        best_prefix = None
        best_data = b""
        for prefix, data in self.popen_stdout.items():
            if tuple(argv[: len(prefix)]) == prefix:
                if best_prefix is None or len(prefix) > len(best_prefix):
                    best_prefix, best_data = prefix, data
        p = FakePopen(argv, stdin=stdin, stdout=stdout, stderr=stderr, stdout_data=best_data)
        self.popens.append(p)
        return p


def _ok(output: bytes = b"") -> Captured:
    return Captured(returncode=0, output=output, truncated=False, timed_out=False)


def _fail(output: bytes = b"error") -> Captured:
    return Captured(returncode=1, output=output, truncated=False, timed_out=False)


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
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    sb.start(worktree, repo, "abc123", "deadbeef" * 5)
    fake.calls.clear()
    return sb, fake, run_dir


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
