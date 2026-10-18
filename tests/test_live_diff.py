from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from dirtywork.procs import Captured
from dirtywork.sandbox import docker_cli
from dirtywork.sandbox.live_diff import (
    CAPTURE_TIMEOUT,
    MAX_PATCH_BYTES,
    MAX_UNTRACKED_FILES,
    LiveDiffTooLarge,
    LiveDiffUnavailable,
    capture_docker_patch,
    capture_host_patch,
)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Live diff test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    for name in ("changed.txt", "staged.txt", "deleted.txt"):
        (tmp_path / name).write_text("before\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    return tmp_path, git(tmp_path, "rev-parse", "HEAD").decode().strip()


def test_host_captures_current_files_from_base_and_preserves_worker_index(repo):
    path, base = repo
    (path / "changed.txt").write_text("changed after base\n")
    (path / "staged.txt").write_text("staged version\n")
    git(path, "add", "staged.txt")
    (path / "staged.txt").write_text("current version\n")
    (path / "deleted.txt").unlink()
    (path / "new file.txt").write_text("new content\n")
    (path / "new staged.txt").write_text("new staged content\n")
    git(path, "add", "new staged.txt")
    index = path / ".git" / "index"
    before = (index.read_bytes(), index.stat().st_mtime_ns)
    patch = capture_host_patch(path, base)
    assert b"+changed after base\n" in patch
    assert b"+current version\n" in patch
    assert b"+staged version\n" not in patch
    assert b"deleted file mode" in patch
    assert b"+new content\n" in patch
    assert b"+new staged content\n" in patch
    assert before == (index.read_bytes(), index.stat().st_mtime_ns)
    assert (path / "staged.txt").read_text() == "current version\n"
    assert not (path / "deleted.txt").exists()


def test_host_includes_commits_since_base_and_ignored_index_additions(repo):
    path, base = repo
    (path / "changed.txt").write_text("committed change\n")
    (path / ".gitignore").write_text("ignored.txt\n")
    (path / "ignored.txt").write_text("tracked despite ignore\n")
    git(path, "add", "changed.txt", ".gitignore")
    git(path, "add", "-f", "ignored.txt")
    git(path, "commit", "-qm", "worker commit")
    patch = capture_host_patch(path, base)
    assert b"+committed change\n" in patch
    assert b"+tracked despite ignore\n" in patch


def test_host_staged_deletion_does_not_hide_current_file(repo):
    path, base = repo
    git(path, "rm", "--cached", "changed.txt")
    (path / "changed.txt").write_text("still on disk\n")
    patch = capture_host_patch(path, base)
    assert b"+still on disk\n" in patch
    assert patch.count(b"diff --git a/changed.txt b/changed.txt") == 1


def test_host_config_cannot_launch_filters_hooks_fsmonitor_or_diff(repo):
    path, base = repo
    marker = path.parent / "executed"
    executable = path.parent / "hostile.sh"
    executable.write_text("#!/bin/sh\ntouch " + str(marker) + "\ncat\n")
    executable.chmod(0o755)
    for key in ("core.fsmonitor", "diff.external", "diff.hostile.command",
                "diff.hostile.textconv", "filter.hostile.clean", "filter.hostile.process"):
        git(path, "config", key, str(executable))
    git(path, "config", "filter.hostile.required", "true")
    hooks = path.parent / "hooks"
    hooks.mkdir()
    (hooks / "post-index-change").write_text(executable.read_text())
    (hooks / "post-index-change").chmod(0o755)
    git(path, "config", "core.hooksPath", str(hooks))
    (path / ".gitattributes").write_text("* filter=hostile diff=hostile\n")
    (path / "changed.txt").write_text("safe diff\n")
    (path / "new.txt").write_text("safe addition\n")
    patch = capture_host_patch(path, base)
    assert b"+safe diff\n" in patch
    assert b"+safe addition\n" in patch
    assert not marker.exists()


def test_host_untracked_symlinks_and_fifos_are_not_followed(repo):
    path, base = repo
    secret = path.parent / "secret"
    secret.write_text("must not appear in preview\n")
    (path / "link").symlink_to(secret)
    (path / "linked-dir").symlink_to(path.parent, target_is_directory=True)
    os.mkfifo(path / "pipe")
    (path / "real.txt").write_text("visible\n")
    patch = capture_host_patch(path, base)
    assert b"+visible\n" in patch
    assert b"must not appear" not in patch
    assert b"b/link" not in patch
    assert b"b/pipe" not in patch


def test_host_untracked_names_are_literal_and_patch_applies(repo):
    path, base = repo
    for name in ("-options.txt", "quote\".txt", "line\nbreak.txt", "$(touch injected).txt"):
        (path / name).write_text("literal path\n")
    patch = capture_host_patch(path, base)
    assert patch.count(b"+literal path\n") == 4
    assert not (path / "injected").exists()
    checked = subprocess.run(["git", "-C", str(path), "apply", "--check", "--reverse", "-"],
                             input=patch, capture_output=True)
    assert checked.returncode == 0, checked.stderr


def test_host_rejects_oversized_patch(repo):
    path, base = repo
    (path / "changed.txt").write_bytes(b"large line\n" * (MAX_PATCH_BYTES // 5))
    with pytest.raises(LiveDiffTooLarge):
        capture_host_patch(path, base)


def test_host_rejects_oversized_new_file(repo):
    path, base = repo
    (path / "new.txt").write_bytes(b"x" * (MAX_PATCH_BYTES + 1))
    with pytest.raises(LiveDiffTooLarge):
        capture_host_patch(path, base)


def test_host_rejects_too_many_new_files(repo):
    path, base = repo
    for i in range(MAX_UNTRACKED_FILES + 1):
        (path / f"new-{i}").write_text("small\n")
    with pytest.raises(LiveDiffTooLarge):
        capture_host_patch(path, base)


def test_host_empty_and_unavailable(repo, tmp_path):
    path, base = repo
    assert capture_host_patch(path, base) == b""
    with pytest.raises(LiveDiffUnavailable):
        capture_host_patch(path, "a" * 40)
    with pytest.raises(LiveDiffUnavailable):
        capture_host_patch(tmp_path / "missing", base)


@pytest.mark.parametrize("base", ["HEAD", "abc", "a" * 41, "a" * 63, "g" * 40,
                                 "a" * 40 + "; touch /tmp/bad", None])
def test_invalid_base_never_starts_docker(base):
    def fail(*args, **kwargs):
        pytest.fail("invalid base reached Docker")
    with pytest.raises(LiveDiffUnavailable):
        capture_docker_patch("worker", base, run=fail)


def test_docker_uses_fixed_argv_and_remote_timeout():
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return Captured(0, b"diff\n", False, False)
    assert capture_docker_patch("dw-test", "a" * 40, run=run) == b"diff\n"
    argv, kwargs = calls[0]
    assert argv[:3] == ["exec", "-w", "/work"]
    assert argv[argv.index("dw-test") + 1:][:6] == [
        "/usr/bin/timeout", "--signal=KILL", "10s", "/usr/bin/python3", "-I", "-c"]
    assert argv[-1] == "a" * 40
    assert "/bin/sh" not in argv
    assert kwargs == {"timeout": CAPTURE_TIMEOUT, "cap": MAX_PATCH_BYTES + 1}


@pytest.mark.parametrize("captured", [Captured(3, b"too big", False, False),
                                     Captured(0, b"x", True, False),
                                     Captured(0, b"x" * (MAX_PATCH_BYTES + 1), False, False)])
def test_docker_oversized_results_are_explicit(captured):
    with pytest.raises(LiveDiffTooLarge):
        capture_docker_patch("worker", "a" * 64, run=lambda *args, **kwargs: captured)


@pytest.mark.parametrize("captured", [Captured(2, b"bad", False, False),
                                     Captured(124, b"", False, False),
                                     Captured(137, b"", False, False),
                                     Captured(None, b"", False, True)])
def test_docker_unavailable_results_are_explicit(captured):
    with pytest.raises(LiveDiffUnavailable):
        capture_docker_patch("worker", "a" * 40, run=lambda *args, **kwargs: captured)


def test_docker_client_timeout_is_unavailable():
    def run(*args, **kwargs):
        raise docker_cli.DockerError("daemon stalled", timed_out=True)
    with pytest.raises(LiveDiffUnavailable):
        capture_docker_patch("worker", "a" * 40, run=run)


def test_host_nested_repository_does_not_hide_other_changes(repo):
    path, base = repo
    nested = path / "nested"
    nested.mkdir()
    git(nested, "init", "-q")
    git(nested, "config", "user.name", "Nested")
    git(nested, "config", "user.email", "nested@example.invalid")
    (nested / "nested.txt").write_text("nested contents\n")
    git(nested, "add", ".")
    git(nested, "commit", "-qm", "nested")
    (path / "changed.txt").write_text("outer change\n")
    patch = capture_host_patch(path, base)
    assert b"+outer change\n" in patch
    assert b"nested contents" not in patch


def test_host_linked_worktree_uses_common_objects_and_preserves_index(repo, tmp_path):
    path, base = repo
    linked = tmp_path / "linked"
    git(path, "worktree", "add", "-q", "--detach", str(linked), base)
    (linked / "changed.txt").write_text("linked change\n")
    (linked / "new.txt").write_text("linked addition\n")
    index = Path(git(linked, "rev-parse", "--path-format=absolute", "--git-path", "index").decode().strip())
    before = index.read_bytes()
    patch = capture_host_patch(linked, base)
    assert b"+linked change\n" in patch
    assert b"+linked addition\n" in patch
    assert index.read_bytes() == before


def test_host_sha256_repository(tmp_path):
    result = subprocess.run(["git", "-C", str(tmp_path), "init", "-q", "--object-format=sha256"],
                            capture_output=True)
    if result.returncode:
        pytest.skip("installed Git lacks SHA-256 repository support")
    git(tmp_path, "config", "user.name", "Live diff test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    (tmp_path / "tracked.txt").write_text("before\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD").decode().strip()
    assert len(base) == 64
    (tmp_path / "tracked.txt").write_text("sha256 change\n")
    (tmp_path / "new.txt").write_text("sha256 addition\n")
    patch = capture_host_patch(tmp_path, base)
    assert b"+sha256 change\n" in patch
    assert b"+sha256 addition\n" in patch
