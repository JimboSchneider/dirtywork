from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from dirtywork.sandbox import docker_cli
from dirtywork.procs import Captured
from dirtywork.sandbox.docker_args import DockerConfig
from dirtywork.sandbox.export import export_run, parse_git_entries, nested_roots, children, top_level_roots
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
    from dirtywork.sandbox.export import EXPORT_GIT_ENTRIES_SCRIPT
    fake = FakeDocker()
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())  # ready-wait, init
    # Updated find script with NUL-safe enumeration
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT], _ok(b""))
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
    # Export container uses env layout: must have GIT_DIR and GIT_WORK_TREE
    assert "-e" in create_argv
    i = create_argv.index("-e")
    assert create_argv[i:i + 4] == ["-e", "GIT_DIR=/gitdir", "-e", "GIT_WORK_TREE=/work"]

    inits = [c for c in fake.calls if c[0][0] == "exec" and "/bin/sh" in c[0] and "-c" in c[0]]
    assert inits, "no init exec call found"
    assert "--separate-git-dir" not in inits[0][0][inits[0][0].index("-c") + 1]


def test_export_run_parses_dropped_git_entries(tmp_path, empty_worktree):
    from dirtywork.sandbox.export import EXPORT_GIT_ENTRIES_SCRIPT
    fake = FakeDocker()
    fake.script(["exec"], _ok())
    # Updated find script with NUL-safe enumeration
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT],
                _ok(b"/work/payload/.git\0/work/other/.GIT\0"))
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


def test_parse_git_entries():
    """Test parse_git_entries with various inputs."""
    # Basic test
    assert parse_git_entries(b"/work/a/.git\0/work/b/.git\0") == ["/work/a/.git", "/work/b/.git"]

    # Test with spaces and newlines in names
    # Input ends with NUL, so split gives an empty last chunk that gets dropped
    assert parse_git_entries(b"/work/a b/.git\0/work/x\ny/.git\0garbage\0/tmp/z/.git\0/work/w/.GIT\0") == [
        "/work/a b/.git", "/work/x\ny/.git", "/work/w/.GIT"
    ]

    # Test empty input
    assert parse_git_entries(b"") == []

    # Test no matches
    assert parse_git_entries(b"/work/file.txt\0/usr/bin/.git\0") == []

    # Test with spaces and newlines in names (unterminated)
    assert parse_git_entries(b"/work/a b/.git\0/work/x\ny/.git\0garbage\0/tmp/z/.git\0/work/w/.GIT\0/work/unterminated/.git") == ["/work/a b/.git", "/work/x\ny/.git", "/work/w/.GIT"]


def test_nested_roots():
    """Test nested_roots function."""
    assert nested_roots(["a/.git", "a/b/.git", "c/.git", ".git"]) == ["a/b", "a", "c"]
    # Test empty list
    assert nested_roots([]) == []
    # Test with no parent paths
    assert nested_roots(["x/.git", "y/.git"]) == ["x", "y"]
    # Test duplicates are removed
    assert nested_roots(["a/.git", "a/b/.git", "a/.git"]) == ["a/b", "a"]


def test_children():
    """Test children function."""
    assert children("a", ["a/b/c", "a/b", "a", "c"]) == ["b"]
    assert children("a/b", ["a/b/c", "a/b", "a", "c"]) == ["c"]
    assert children("c", ["a/b/c", "a/b", "a", "c"]) == []
    # Test empty list
    assert children("x", []) == []
    # Test with no children
    assert children("a", ["b", "c"]) == []


def test_top_level_roots():
    """Test top_level_roots function."""
    assert top_level_roots(["a/b/c", "a/b", "a", "c"]) == ["a", "c"]
    # Test empty list
    assert top_level_roots([]) == []
    # Test all are top level (no ancestors)
    assert top_level_roots(["a", "b", "c"]) == ["a", "b", "c"]


def test_export_run_find_rc1_with_parseable_output(tmp_path, empty_worktree):
    """Test export continues with rc 1 but prints error message."""
    from dirtywork.sandbox.export import EXPORT_GIT_ENTRIES_SCRIPT
    fake = FakeDocker()
    fake.script(["exec"], _ok())
    # find returns rc 1 but has parseable output
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT],
                _fail(b"/work/a/.git\0"))
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

    assert artifacts.export_status == "ok"
    assert artifacts.dropped_git_entries == ["a/.git"]


def test_export_run_find_truncated_output(tmp_path, empty_worktree):
    """Test export fails when find output is truncated."""
    from dirtywork.sandbox.export import EXPORT_GIT_ENTRIES_SCRIPT
    fake = FakeDocker()
    fake.script(["exec"], _ok())
    # Captured.truncated is True
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT],
                Captured(b"", 0, timed_out=False, truncated=True))
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig()

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert artifacts.export_status == "export_failed: could not enumerate .git entries"


def test_export_script_syntax():
    """Test that EXPORT_GIT_ENTRIES_SCRIPT is syntactically valid."""
    import subprocess
    from dirtywork.sandbox.export import EXPORT_GIT_ENTRIES_SCRIPT
    result = subprocess.run(["sh", "-n", "-c", EXPORT_GIT_ENTRIES_SCRIPT],
                           capture_output=True, text=True)
    assert result.returncode == 0, f"Script syntax error: {result.stderr}"


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
    from dirtywork.sandbox.export import EXPORT_GIT_ENTRIES_SCRIPT
    fake = FakeDocker()
    fake.script(["exec"], _ok())
    # Updated find script with NUL-safe enumeration
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT], _ok(b""))
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
    from dirtywork.sandbox.export import EXPORT_GIT_ENTRIES_SCRIPT
    fake = FakeDocker()
    fake.script(["exec"], _ok())
    # Updated find script with NUL-safe enumeration
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT], _ok(b""))
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


def test_export_run_tether_oserror_is_cleaned_up(tmp_path, empty_worktree):
    # Fix item 4a: the tether `popen(["docker", "start", "-ai", name], ...)`
    # runs before `_cleanup`/`_fail` exist (they close over `tether`) -- an
    # OSError there (docker binary missing, fork failure, ...) needs its own
    # best-effort teardown: just `rm -f` the export container this call
    # itself created, no volume rm (nothing about the volume changed).
    fake = FakeDocker()
    fake.script(["create"], _ok())

    def boom_popen(argv, *, stdin=None, stdout=None, stderr=None):
        if argv[:3] == ["docker", "start", "-ai"]:
            raise OSError("fork failed")
        return fake.popen(argv, stdin=stdin, stdout=stdout, stderr=stderr)

    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig()

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=boom_popen,
    )

    assert artifacts.export_status.startswith("export_failed")
    assert "cannot start export tether" in artifacts.export_status
    assert any(c[0][:2] == ["rm", "-f"] and "dw-abc123-export" in c[0] for c in fake.calls)
    assert not any(c[0][:2] == ["volume", "rm"] for c in fake.calls)


def test_export_run_diff_step_oserror_routes_through_fail(tmp_path, empty_worktree):
    from dirtywork.sandbox.export import EXPORT_GIT_ENTRIES_SCRIPT
    # Fix item 4b: an OSError raised by a raw OS call further into the
    # export (popen() for the streamed `git diff`, os.open() for the patch
    # file, ...) used to propagate past the `except SandboxError` guard
    # entirely, skipping cleanup. It must route through _fail like a
    # SandboxError does: export container removed, volume KEPT (retryable),
    # worktree cleaned back to .git only.
    fake = FakeDocker()
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())
    # Updated find script with NUL-safe enumeration
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT], _ok(b""))
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "write-tree"],
                _ok(b"treehash\n"))

    def boom_popen(argv, *, stdin=None, stdout=None, stderr=None):
        if "diff" in argv and "--stat" not in argv:
            raise OSError("cannot fork for git diff")
        return fake.popen(argv, stdin=stdin, stdout=stdout, stderr=stderr)

    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig()

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=boom_popen,
    )

    assert artifacts.export_status.startswith("export_failed: docker step failed")
    assert any(c[0][:2] == ["rm", "-f"] and "dw-abc123-export" in c[0] for c in fake.calls)
    assert not any(c[0][:2] == ["volume", "rm"] for c in fake.calls)
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


def test_export_run_reports_files_changed(tmp_path, empty_worktree):
    from dirtywork.sandbox.export import EXPORT_GIT_ENTRIES_SCRIPT
    fake = FakeDocker()
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())
    # Updated find script with NUL-safe enumeration
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT], _ok(b""))
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff",
                 "--cached", "--name-only", "deadbeef" * 5],
                _ok(b"src/b.ts\nsrc/a.ts\nsrc/b.ts\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "write-tree"],
                _ok(b"treehash1234\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff", "--stat",
                 "deadbeef" * 5, "treehash1234"],
                _ok(b" 2 files changed\n"))
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff",
         "deadbeef" * 5, "treehash1234"], b"")
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "archive",
         "--format=tar", "treehash1234"],
        _make_tar([{"name": "src/a.ts", "content": b"a"}]))
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()

    artifacts = export_run(
        DockerConfig(), slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert artifacts.export_status == "ok"
    assert artifacts.files_changed == ["src/a.ts", "src/b.ts"]   # sorted, de-duplicated
    assert artifacts.files_changed_truncated is False
    # the name list is read from the INDEX, right after `git add -A`
    names_index = next(i for i, c in enumerate(fake.calls) if "--cached" in c[0])
    add_index = next(i for i, c in enumerate(fake.calls) if c[0][-2:] == ["add", "-A"])
    assert add_index < names_index


def test_export_run_never_execs_a_sweep_the_export_volume_is_readonly(tmp_path, empty_worktree):
    from dirtywork.sandbox.export import EXPORT_GIT_ENTRIES_SCRIPT
    # Fix round 1: the sweep moved OUT of export_run entirely -- the export
    # container's /work volume mount is readonly by design
    # (docker_args.export_create_argv), so a `find -delete` exec here would
    # get EROFS on every match and silently do nothing while `git add -A`
    # still staged `.dw-tmp.…` into the export. The sweep now runs in the
    # WORKER container, before it stops, ahead of export_run entirely (see
    # test_docker_sandbox.py). This is the negative half of that fix: prove
    # export_run's own exec stream never execs a sweep, so a future
    # regression that reintroduces one here is caught.
    fake = FakeDocker()
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())
    # Updated find script with NUL-safe enumeration
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT], _ok(b""))
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "write-tree"],
                _ok(b"treehash1234\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff", "--stat",
                 "deadbeef" * 5, "treehash1234"], _ok(b" 1 file changed\n"))
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff",
         "deadbeef" * 5, "treehash1234"], b"diff --git a/x b/x\n+hi\n")
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "archive",
         "--format=tar", "treehash1234"],
        _make_tar([{"name": "hello.txt", "content": b"hi there"}]))
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()

    artifacts = export_run(
        DockerConfig(), slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert artifacts.export_status == "ok"
    argvs = [c[0] for c in fake.calls]
    sweeps = [a for a in argvs if "-regextype" in a]
    adds = [a for a in argvs if a[-3:] == ["/usr/bin/git", "add", "-A"]]
    assert sweeps == []
    assert len(adds) == 1


def test_export_truncates_files_changed_and_flags_it(tmp_path, empty_worktree):
    from dirtywork.workspace import MAX_FILES_CHANGED
    from dirtywork.sandbox.export import EXPORT_GIT_ENTRIES_SCRIPT
    names = b"".join(f"f{i:06d}.txt\n".encode() for i in range(MAX_FILES_CHANGED + 5))
    fake = FakeDocker()
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())
    # Updated find script with NUL-safe enumeration
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT], _ok(b""))
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff",
                 "--cached", "--name-only", "deadbeef" * 5], _ok(names))
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "write-tree"],
                _ok(b"treehash1234\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff", "--stat",
                 "deadbeef" * 5, "treehash1234"], _ok(b" many files changed\n"))
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff",
         "deadbeef" * 5, "treehash1234"], b"diff --git a/x b/x\n+hi\n")
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "archive",
         "--format=tar", "treehash1234"],
        _make_tar([{"name": "hello.txt", "content": b"hi"}]))
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()

    artifacts = export_run(
        DockerConfig(), slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert len(artifacts.files_changed) == MAX_FILES_CHANGED
    assert artifacts.files_changed_truncated is True
    assert artifacts.files_changed == sorted(artifacts.files_changed)


def test_export_run_nested_repos_splice_order(tmp_path, empty_worktree):
    """Test 5a: entries /work/a/.git\0/work/a/b/.git with correct exec sequence"""
    from dirtywork.sandbox.export import EXPORT_GIT_ENTRIES_SCRIPT
    fake = FakeDocker()

    # Scripting for nested repo "a/b" (deepest first, index 0)
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())  # ready-wait, init
    # Find entries
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT],
                _ok(b"/work/a/.git\0/work/a/b/.git\0"))

    # Nested repo 0 (a/b): init
    fake.script(["exec", "-w", "/work/a/b",
                 "-e", "GIT_DIR=/tmp/nested-0", "-e", "GIT_WORK_TREE=/work/a/b",
                 "-e", "GIT_OBJECT_DIRECTORY=/gitdir/objects",
                 "-e", "GIT_CONFIG_GLOBAL=/dev/null", "-e", "GIT_CONFIG_NOSYSTEM=1",
                 "dw-abc123-export", "/usr/bin/git", "init", "-q", "--template="], _ok())
    # Nested repo 0: read-tree --empty
    fake.script(["exec", "-w", "/work/a/b",
                 "-e", "GIT_DIR=/tmp/nested-0", "-e", "GIT_WORK_TREE=/work/a/b",
                 "-e", "GIT_OBJECT_DIRECTORY=/gitdir/objects",
                 "-e", "GIT_CONFIG_GLOBAL=/dev/null", "-e", "GIT_CONFIG_NOSYSTEM=1",
                 "dw-abc123-export", "/usr/bin/git", "read-tree", "--empty"], _ok())
    # Nested repo 0: add -A (no children to exclude)
    fake.script(["exec", "-w", "/work/a/b",
                 "-e", "GIT_DIR=/tmp/nested-0", "-e", "GIT_WORK_TREE=/work/a/b",
                 "-e", "GIT_OBJECT_DIRECTORY=/gitdir/objects",
                 "-e", "GIT_CONFIG_GLOBAL=/dev/null", "-e", "GIT_CONFIG_NOSYSTEM=1",
                 "dw-abc123-export", "/usr/bin/git", "-c", "core.excludesFile=/work/.gitignore", "add", "-A", "--", "."], _ok())
    # Nested repo 0: write-tree
    fake.script(["exec", "-w", "/work/a/b",
                 "-e", "GIT_DIR=/tmp/nested-0", "-e", "GIT_WORK_TREE=/work/a/b",
                 "-e", "GIT_OBJECT_DIRECTORY=/gitdir/objects",
                 "-e", "GIT_CONFIG_GLOBAL=/dev/null", "-e", "GIT_CONFIG_NOSYSTEM=1",
                 "dw-abc123-export", "/usr/bin/git", "write-tree"], _ok(b"tree-a-b-123\n"))

    # Nested repo 1 (a): init
    fake.script(["exec", "-w", "/work/a",
                 "-e", "GIT_DIR=/tmp/nested-1", "-e", "GIT_WORK_TREE=/work/a",
                 "-e", "GIT_OBJECT_DIRECTORY=/gitdir/objects",
                 "-e", "GIT_CONFIG_GLOBAL=/dev/null", "-e", "GIT_CONFIG_NOSYSTEM=1",
                 "dw-abc123-export", "/usr/bin/git", "init", "-q", "--template="], _ok())
    # Nested repo 1: read-tree --empty
    fake.script(["exec", "-w", "/work/a",
                 "-e", "GIT_DIR=/tmp/nested-1", "-e", "GIT_WORK_TREE=/work/a",
                 "-e", "GIT_OBJECT_DIRECTORY=/gitdir/objects",
                 "-e", "GIT_CONFIG_GLOBAL=/dev/null", "-e", "GIT_CONFIG_NOSYSTEM=1",
                 "dw-abc123-export", "/usr/bin/git", "read-tree", "--empty"], _ok())
    # Nested repo 1: add -A with exclusion for "b"
    fake.script(["exec", "-w", "/work/a",
                 "-e", "GIT_DIR=/tmp/nested-1", "-e", "GIT_WORK_TREE=/work/a",
                 "-e", "GIT_OBJECT_DIRECTORY=/gitdir/objects",
                 "-e", "GIT_CONFIG_GLOBAL=/dev/null", "-e", "GIT_CONFIG_NOSYSTEM=1",
                 "dw-abc123-export", "/usr/bin/git", "-c", "core.excludesFile=/work/.gitignore", "add", "-A", "--",
                 ".", ": (exclude,literal)b"], _ok())
    # Nested repo 1: read-tree --prefix=b/ with tree-a-b-123
    fake.script(["exec", "-w", "/work/a",
                 "-e", "GIT_DIR=/tmp/nested-1", "-e", "GIT_WORK_TREE=/work/a",
                 "-e", "GIT_OBJECT_DIRECTORY=/gitdir/objects",
                 "-e", "GIT_CONFIG_GLOBAL=/dev/null", "-e", "GIT_CONFIG_NOSYSTEM=1",
                 "dw-abc123-export", "/usr/bin/git", "read-tree", "--prefix=b/", "tree-a-b-123"], _ok())
    # Nested repo 1: write-tree
    fake.script(["exec", "-w", "/work/a",
                 "-e", "GIT_DIR=/tmp/nested-1", "-e", "GIT_WORK_TREE=/work/a",
                 "-e", "GIT_OBJECT_DIRECTORY=/gitdir/objects",
                 "-e", "GIT_CONFIG_GLOBAL=/dev/null", "-e", "GIT_CONFIG_NOSYSTEM=1",
                 "dw-abc123-export", "/usr/bin/git", "write-tree"], _ok(b"tree-a-456\n"))

    # Main index: rm a
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "rm", "-r", "-q", "--cached", "--ignore-unmatch",
                 "--", ": (literal)a"], _ok())
    # Main index: read-tree --prefix=a/ with tree-a-456
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "read-tree", "--prefix=a/", "tree-a-456"], _ok())
    # Main index: add -A with exclusion for a
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "add", "-A", "--", ".", ": (exclude,literal)a"], _ok())
    # diff --cached
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "diff", "--cached", "--name-only", "deadbeef" * 5], _ok(b""))
    # write-tree
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "write-tree"], _ok(b"treehash123\n"))
    # ls-files
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "ls-files", "-s", "-z"], _ok(b""))
    # ls-tree
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "ls-tree", "-r", "-z", "deadbeef" * 5], _ok(b""))
    # diff --stat
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "diff", "--stat", "deadbeef" * 5, "treehash123"], _ok(b""))
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export",
         "/usr/bin/git", "diff", "deadbeef" * 5, "treehash123"], b"")
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export",
         "/usr/bin/git", "archive", "--format=tar", "treehash123"], _make_tar([]))

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
    # Check stderr has notification
    # Check dropped_git_entries
    assert artifacts.dropped_git_entries == ["a/.git", "a/b/.git"]


def test_export_run_no_nested_repos(tmp_path, empty_worktree):
    """Test 5b: no entries -> the add argv is exactly ["/usr/bin/git", "add", "-A"]"""
    from dirtywork.sandbox.export import EXPORT_GIT_ENTRIES_SCRIPT
    fake = FakeDocker()

    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())  # ready-wait, init
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT], _ok(b""))
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "add", "-A"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "diff", "--cached", "--name-only", "deadbeef" * 5], _ok(b""))
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "write-tree"], _ok(b"treehash123\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "ls-files", "-s", "-z"], _ok(b""))
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "ls-tree", "-r", "-z", "deadbeef" * 5], _ok(b""))
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "diff", "--stat", "deadbeef" * 5, "treehash123"], _ok(b""))
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export",
         "/usr/bin/git", "diff", "deadbeef" * 5, "treehash123"], b"")
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export",
         "/usr/bin/git", "archive", "--format=tar", "treehash123"], _make_tar([{"name": "hello.txt", "content": b"hi"}]))

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

    # Check that add -A was called without "--" or "." in the git args
    adds = [c[0] for c in fake.calls if c[0][-2:] == ["add", "-A"]]
    assert len(adds) == 1
    # The full argv is ["exec", "-w", "/work", "dw-abc123-export", ...git args...]
    # Check the git command part is correct
    assert "/usr/bin/git" in adds[0]
    # The git args should end with just ["add", "-A"] (no "--" or ".")
    # Find the position of "/usr/bin/git"
    git_idx = adds[0].index("/usr/bin/git")
    assert adds[0][git_idx+1:] == ["add", "-A"]


def test_export_run_ls_files_verify_gitlink(tmp_path, empty_worktree):
    """Test 5c: ls-files/ls-tree verification scenarios"""
    from dirtywork.sandbox.export import EXPORT_GIT_ENTRIES_SCRIPT
    fake = FakeDocker()

    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())  # ready-wait, init
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT], _ok(b""))
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "add", "-A"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "diff", "--cached", "--name-only", "deadbeef" * 5], _ok(b""))
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "write-tree"], _ok(b"treehash123\n"))

    # ls-files returns gitlink for vendor/x (mode 160000) but ls-tree lacks it
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "ls-files", "-s", "-z"],
                _ok(b"160000 " + b"a" * 40 + b" 0\tvendor/x\0" + b"100644 " + b"b" * 40 + b" 0\tREADME.md\0"))
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "ls-tree", "-r", "-z", "deadbeef" * 5], _ok(b"100644 blob " + b"c" * 40 + b"\tREADME.md\0"))

    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "diff", "--stat", "deadbeef" * 5, "treehash123"], _ok(b""))
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export",
         "/usr/bin/git", "diff", "deadbeef" * 5, "treehash123"], b"")
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export",
         "/usr/bin/git", "archive", "--format=tar", "treehash123"], _make_tar([]))

    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig()

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert artifacts.export_status.startswith("export_failed: nested repository at vendor/x was not masked")


def test_export_run_ls_files_gitlink_masked(tmp_path, empty_worktree):
    """Test 5c continued: gitlink present in both ls-files and ls-tree -> success"""
    from dirtywork.sandbox.export import EXPORT_GIT_ENTRIES_SCRIPT
    fake = FakeDocker()

    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())  # ready-wait, init
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT], _ok(b""))
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "add", "-A"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "diff", "--cached", "--name-only", "deadbeef" * 5], _ok(b""))
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "write-tree"], _ok(b"treehash123\n"))

    # ls-files returns gitlink for vendor/x (mode 160000)
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "ls-files", "-s", "-z"],
                _ok(b"160000 " + b"a" * 40 + b" 0\tvendor/x\0" + b"100644 " + b"b" * 40 + b" 0\tREADME.md\0"))
    # ls-tree ALSO has vendor/x as a commit (gitlink)
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "ls-tree", "-r", "-z", "deadbeef" * 5],
                _ok(b"160000 commit " + b"c" * 40 + b"\tvendor/x\0" + b"100644 blob " + b"d" * 40 + b"\tREADME.md\0"))

    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "diff", "--stat", "deadbeef" * 5, "treehash123"], _ok(b""))
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export",
         "/usr/bin/git", "diff", "deadbeef" * 5, "treehash123"], b"")
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export",
         "/usr/bin/git", "archive", "--format=tar", "treehash123"], _make_tar([]))

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


def test_export_run_ls_files_rc1(tmp_path, empty_worktree):
    """Test 5c continued: ls-files rc 1 -> export failed"""
    from dirtywork.sandbox.export import EXPORT_GIT_ENTRIES_SCRIPT
    fake = FakeDocker()

    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())  # ready-wait, init
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT], _ok(b""))
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "add", "-A"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "diff", "--cached", "--name-only", "deadbeef" * 5], _ok(b""))
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "write-tree"], _ok(b"treehash123\n"))

    # ls-files returns rc 1
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "ls-files", "-s", "-z"], _fail(b"error"))

    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig()

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert artifacts.export_status == "export_failed: could not verify the export index (ls-files)"


def test_export_run_ls_tree_truncated(tmp_path, empty_worktree):
    """Test 5c continued: ls-tree truncated -> export failed"""
    from dirtywork.sandbox.export import EXPORT_GIT_ENTRIES_SCRIPT
    fake = FakeDocker()

    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())  # ready-wait, init
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT], _ok(b""))
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "add", "-A"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "diff", "--cached", "--name-only", "deadbeef" * 5], _ok(b""))
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "write-tree"], _ok(b"treehash123\n"))

    # ls-files ok
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "ls-files", "-s", "-z"], Captured(returncode=0, output=b"", truncated=False, timed_out=False))
    # ls-tree truncated
    fake.script(["exec", "-w", "/work", "dw-abc123-export",
                 "/usr/bin/git", "ls-tree", "-r", "-z", "deadbeef" * 5],
                Captured(returncode=0, output=b"", truncated=True, timed_out=False))

    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig()

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert artifacts.export_status == "export_failed: could not verify the export index (ls-tree)"


def test_export_run_nested_splice_rc128(tmp_path, empty_worktree):
    """Test 5d: a nested splice exec returning rc 128 -> export_status starts with 'export_failed: nested repository splice failed at a'"""
    from dirtywork.sandbox.export import EXPORT_GIT_ENTRIES_SCRIPT
    fake = FakeDocker()

    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())  # ready-wait, init
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT],
                _ok(b"/work/a/.git\0"))

    # Nested repo 0 (a): init fails with rc 128
    fake.script(["exec", "-w", "/work/a",
                 "-e", "GIT_DIR=/tmp/nested-0", "-e", "GIT_WORK_TREE=/work/a",
                 "-e", "GIT_OBJECT_DIRECTORY=/gitdir/objects",
                 "-e", "GIT_CONFIG_GLOBAL=/dev/null", "-e", "GIT_CONFIG_NOSYSTEM=1",
                 "dw-abc123-export", "/usr/bin/git", "init", "-q", "--template="],
                Captured(returncode=128, output=b"fatal: could not create repository", truncated=False, timed_out=False))

    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig()

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert artifacts.export_status.startswith("export_failed: nested repository splice failed at a")
