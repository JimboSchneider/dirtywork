from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from dirtywork.sandbox import docker_cli
from dirtywork.procs import Captured
from dirtywork.sandbox.docker_args import DockerConfig
from dirtywork.sandbox.export import export_run
from tests.docker_fakes import FakeDocker, FakePopen, _fail, _ok


def _make_tar(entries: list) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for e in entries:
            info = tarfile.TarInfo(e["name"])
            data = e.get("content", b"")
            info.size = len(data)
            info.mode = e.get("mode", 0o644)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture()
def empty_worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /somewhere\n")
    return wt


def test_export_run_refuses_when_worktree_not_empty(tmp_path, empty_worktree):
    (empty_worktree / "leftover.txt").write_text("stray")
    fake = FakeDocker()
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig()

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert artifacts.export_status == "export_failed: worktree not empty"
    assert not fake.calls


def test_export_run_happy_path(tmp_path, empty_worktree):
    fake = FakeDocker()
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())  # ready-wait, init, find, git add -A
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "write-tree"],
                _ok(b"treehash1234\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff", "--stat",
                 "deadbeef" * 5, "treehash1234"],
                _ok(b" 1 file changed, 1 insertion(+)\n"))
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff",
         "deadbeef" * 5, "treehash1234"],
        b"diff --git a/x b/x\n+hi\n")
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "archive",
         "--format=tar", "treehash1234"],
        _make_tar([{"name": "hello.txt", "content": b"hi there"}]))
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig()

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert artifacts.export_status == "ok"
    assert artifacts.diff_stat == " 1 file changed, 1 insertion(+)\n"
    assert artifacts.worktree_files == 1
    assert (empty_worktree / "hello.txt").read_bytes() == b"hi there"
    assert artifacts.patch_path == str(run_dir / "diff.patch")
    assert (run_dir / "diff.patch").read_bytes() == b"diff --git a/x b/x\n+hi\n"
    assert any(c[0][:2] == ["volume", "rm"] for c in fake.calls)

    create_argv = next(c[0] for c in fake.calls if c[0][0] == "create")
    assert create_argv[create_argv.index("--network") + 1] == "none"
    assert any(a.startswith("type=volume") and "readonly" in a and "dw-abc123-work" in a
               for a in create_argv)


def test_export_run_parses_dropped_git_entries(tmp_path, empty_worktree):
    fake = FakeDocker()
    fake.script(["exec"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/find", "/work",
                 "-mindepth", "1", "-iname", ".git"],
                _ok(b"/work/payload/.git\n/work/other/.GIT\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "write-tree"],
                _ok(b"treehash\n"))
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "archive",
         "--format=tar", "treehash"],
        _make_tar([]))
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig()

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert artifacts.dropped_git_entries == ["payload/.git", "other/.GIT"]


def test_export_run_git_add_failure_marks_export_failed_and_keeps_volume(tmp_path, empty_worktree):
    fake = FakeDocker()
    fake.script(["exec"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "add", "-A"],
                _fail(b"fatal: unable to add"))
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig()

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert artifacts.export_status.startswith("export_failed: git add -A failed")
    assert not any(c[0][:2] == ["volume", "rm"] for c in fake.calls)
    remaining = list(empty_worktree.iterdir())
    assert len(remaining) == 1 and remaining[0].name == ".git"


def test_export_run_patch_truncated_with_marker(tmp_path, empty_worktree):
    fake = FakeDocker()
    fake.script(["exec"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "write-tree"],
                _ok(b"treehash\n"))
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff",
         "deadbeef" * 5, "treehash"],
        b"x" * 2_000_000)
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "archive",
         "--format=tar", "treehash"],
        _make_tar([]))
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig(max_patch_mb=1)

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert artifacts.export_status == "ok"
    patch_bytes = (run_dir / "diff.patch").read_bytes()
    assert len(patch_bytes) <= 1024 * 1024 + 100
    assert b"[patch truncated at 1 MB]" in patch_bytes


def test_export_run_extract_validation_failure_marks_export_failed(tmp_path, empty_worktree):
    fake = FakeDocker()
    fake.script(["exec"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "write-tree"],
                _ok(b"treehash\n"))
    hostile_tar = _make_tar([{"name": "/etc/passwd", "content": b"pwned"}])
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "archive",
         "--format=tar", "treehash"],
        hostile_tar)
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig()

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert artifacts.export_status.startswith("export_failed:")
    assert "absolute" in artifacts.export_status
    remaining = list(empty_worktree.iterdir())
    assert len(remaining) == 1 and remaining[0].name == ".git"
    assert not any(c[0][:2] == ["volume", "rm"] for c in fake.calls)


def test_export_run_docker_error_routes_through_fail(tmp_path, empty_worktree):
    fake = FakeDocker()
    fake.script(["create"], _ok())
    
    def custom_run(argv, *, timeout, stdin=None):
        if "add" in argv and "-A" in argv:
            raise docker_cli.DockerError("timed out")
        return fake.run(argv, timeout=timeout, stdin=stdin)
    
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig()

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=custom_run, popen=fake.popen,
    )

    assert artifacts.export_status.startswith("export_failed: docker step failed")
    assert any(c[0][:2] == ["rm", "-f"] and "dw-abc123-export" in c[0] for c in fake.calls)
    remaining = list(empty_worktree.iterdir())
    assert len(remaining) == 1 and remaining[0].name == ".git"


def test_export_run_create_docker_error_no_cleanup_needed(tmp_path, empty_worktree):
    fake = FakeDocker()
    
    def custom_run(argv, *, timeout, stdin=None):
        if argv[0] == "create":
            raise docker_cli.DockerError("timed out")
        return fake.run(argv, timeout=timeout, stdin=stdin)
    
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig()

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=custom_run, popen=fake.popen,
    )

    assert artifacts.export_status.startswith("export_failed: docker create")
    assert not any(c[0][:2] == ["rm", "-f"] for c in fake.calls)
