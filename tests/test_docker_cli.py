from __future__ import annotations

import json
from pathlib import Path

import pytest

from dirtywork.procs import Captured
from dirtywork.sandbox.docker_cli import (
    DockerError,
    T_QUERY,
    _split_image_ref,
    docker_storage_paths,
    docker_version,
    image_repo_digest,
    resolve_image,
    run,
    validate_objects_dir,
)
from dirtywork.workspace import WorkspaceError


def test_run_prefixes_docker(monkeypatch):
    seen = {}

    def fake_run_capped(argv, *, timeout, stdin=None, cap=None, cwd=None, env=None, kill_group=True):
        seen["argv"] = argv
        seen["timeout"] = timeout
        return Captured(returncode=0, output=b"ok", truncated=False, timed_out=False)

    monkeypatch.setattr("dirtywork.sandbox.docker_cli.run_capped", fake_run_capped)
    result = run(["version"], timeout=T_QUERY)
    assert seen["argv"] == ["docker", "version"]
    assert seen["timeout"] == T_QUERY
    assert result.returncode == 0


def test_run_raises_dockererror_on_timeout(monkeypatch):
    def fake_run_capped(argv, **kwargs):
        return Captured(returncode=None, output=b"", truncated=False, timed_out=True)

    monkeypatch.setattr("dirtywork.sandbox.docker_cli.run_capped", fake_run_capped)
    with pytest.raises(DockerError) as exc_info:
        run(["exec", "dw-x", "/bin/true"], timeout=5)
    assert "exec" in str(exc_info.value)
    assert "5" in str(exc_info.value)


def test_docker_version_returns_string_on_success():
    def fake_run(argv, *, timeout, stdin=None):
        assert argv == ["version", "--format", "{{.Server.Version}}"]
        return Captured(returncode=0, output=b"29.7.2\n", truncated=False, timed_out=False)

    assert docker_version(run=fake_run) == "29.7.2"


def test_docker_version_raises_on_nonzero(monkeypatch):
    def fake_run(argv, *, timeout, stdin=None):
        return Captured(returncode=1, output=b"Cannot connect to the Docker daemon",
                         truncated=False, timed_out=False)

    with pytest.raises(DockerError, match="Cannot connect"):
        docker_version(run=fake_run)


def test_resolve_image_returns_id_when_local_with_repodigests():
    # Case 1 of 3 (local, RepoDigests present): resolve_image never trusts
    # a RepoDigests candidate for EXECUTION -- buildx-loaded images can
    # carry one that points at a registry manifest never pulled into the
    # local store, and `docker run`/`create` on that ref would try to pull
    # it even though `docker image inspect` on it succeeds. Only the
    # local .Id is ever returned, and RepoDigests is never even consulted
    # when there is no pinned_digest to check.
    calls = []

    def fake_run(argv, *, timeout, stdin=None):
        calls.append(argv)
        if argv == ["image", "inspect", "--format", "{{.Id}}", "dirtywork/worker:0.3"]:
            return Captured(returncode=0, output=b"sha256:" + b"a" * 64,
                             truncated=False, timed_out=False)
        raise AssertionError(f"unexpected argv {argv}")

    ref = resolve_image("dirtywork/worker:0.3", run=fake_run)
    assert ref == "sha256:" + "a" * 64
    assert calls == [["image", "inspect", "--format", "{{.Id}}", "dirtywork/worker:0.3"]]
    assert not any(c[0] == "pull" for c in calls)


def test_resolve_image_returns_id_when_local_without_repodigests():
    # Case 2 of 3 (local, no RepoDigests at all -- built, never pushed or
    # pulled): same .Id-inspect call, same result shape as case 1.
    def fake_run(argv, *, timeout, stdin=None):
        if argv == ["image", "inspect", "--format", "{{.Id}}", "dirtywork/worker:0.3"]:
            return Captured(returncode=0, output=b"sha256:" + b"c" * 64,
                             truncated=False, timed_out=False)
        raise AssertionError(f"unexpected argv {argv}")

    ref = resolve_image("dirtywork/worker:0.3", run=fake_run)
    assert ref == "sha256:" + "c" * 64


def test_resolve_image_pulls_when_absent_then_returns_id():
    # Case 3 of 3 (absent locally): pull, then inspect .Id -- RepoDigests
    # is never consulted for execution, pulled or not.
    calls = []

    def fake_run(argv, *, timeout, stdin=None):
        calls.append(argv)
        if argv == ["image", "inspect", "--format", "{{.Id}}", "dirtywork/worker:0.3"]:
            if len([c for c in calls if c[0] == "image"]) == 1:
                return Captured(returncode=1, output=b"no such image", truncated=False, timed_out=False)
            return Captured(returncode=0, output=b"sha256:" + b"b" * 64,
                             truncated=False, timed_out=False)
        if argv[0] == "pull":
            return Captured(returncode=0, output=b"", truncated=False, timed_out=False)
        raise AssertionError(f"unexpected argv {argv}")

    ref = resolve_image("dirtywork/worker:0.3", run=fake_run)
    assert ref == "sha256:" + "b" * 64
    assert ["pull", "dirtywork/worker:0.3"] in calls


def test_resolve_image_pull_failure_raises():
    def fake_run(argv, *, timeout, stdin=None):
        if argv[0] == "pull":
            return Captured(returncode=1, output=b"not found", truncated=False, timed_out=False)
        return Captured(returncode=1, output=b"no such image", truncated=False, timed_out=False)

    with pytest.raises(DockerError, match="pull"):
        resolve_image("dirtywork/worker:0.3", run=fake_run)


def test_resolve_image_pinned_digest_mismatch_raises():
    def fake_run(argv, *, timeout, stdin=None):
        if argv == ["image", "inspect", "--format", "{{.Id}}", "dirtywork/worker:0.3"]:
            return Captured(returncode=0, output=b"sha256:" + b"a" * 64,
                             truncated=False, timed_out=False)
        digests = ["dirtywork/worker@sha256:" + "a" * 64]
        return Captured(returncode=0, output=json.dumps(digests).encode(),
                         truncated=False, timed_out=False)

    with pytest.raises(DockerError, match="PINNED_DIGEST"):
        resolve_image("dirtywork/worker:0.3", run=fake_run, pinned_digest="sha256:" + "z" * 64)


def test_resolve_image_pinned_digest_match_returns_id():
    def fake_run(argv, *, timeout, stdin=None):
        if argv == ["image", "inspect", "--format", "{{.Id}}", "dirtywork/worker:0.3"]:
            return Captured(returncode=0, output=b"sha256:" + b"a" * 64,
                             truncated=False, timed_out=False)
        digests = ["dirtywork/worker@sha256:" + "a" * 64]
        return Captured(returncode=0, output=json.dumps(digests).encode(),
                         truncated=False, timed_out=False)

    ref = resolve_image("dirtywork/worker:0.3", run=fake_run, pinned_digest="sha256:" + "a" * 64)
    assert ref == "sha256:" + "a" * 64


def test_resolve_image_pinned_digest_no_repodigests_raises():
    # A locally-built image with no RepoDigests entry can never satisfy a
    # pinned digest -- there is nothing to compare against, so it must
    # refuse rather than silently let an unpinned image through.
    def fake_run(argv, *, timeout, stdin=None):
        if argv == ["image", "inspect", "--format", "{{.Id}}", "dirtywork/worker:0.3"]:
            return Captured(returncode=0, output=b"sha256:" + b"a" * 64,
                             truncated=False, timed_out=False)
        return Captured(returncode=0, output=b"[]", truncated=False, timed_out=False)

    with pytest.raises(DockerError, match="PINNED_DIGEST"):
        resolve_image("dirtywork/worker:0.3", run=fake_run, pinned_digest="sha256:" + "a" * 64)


def test_image_repo_digest_returns_matching_repodigests_entry():
    candidate = "dirtywork/worker@sha256:" + "a" * 64

    def fake_run(argv, *, timeout, stdin=None):
        assert argv == ["image", "inspect", "--format", "{{json .RepoDigests}}",
                         "dirtywork/worker:0.3"]
        digests = [candidate]
        return Captured(returncode=0, output=json.dumps(digests).encode(),
                         truncated=False, timed_out=False)

    assert image_repo_digest("dirtywork/worker:0.3", run=fake_run) == candidate


def test_image_repo_digest_none_when_repodigests_empty():
    def fake_run(argv, *, timeout, stdin=None):
        return Captured(returncode=0, output=b"[]", truncated=False, timed_out=False)

    assert image_repo_digest("dirtywork/worker:0.3", run=fake_run) is None


def test_image_repo_digest_none_when_inspect_fails():
    # Best-effort provenance only -- an uninspectable image (e.g. absent
    # locally) yields None, never a raise (unlike resolve_image's pull
    # path, which fails loud on the execution ref).
    def fake_run(argv, *, timeout, stdin=None):
        return Captured(returncode=1, output=b"no such image", truncated=False, timed_out=False)

    assert image_repo_digest("dirtywork/worker:0.3", run=fake_run) is None


def test_image_repo_digest_registry_port_name_match():
    # Same name-matching fix as _split_image_ref: a ':' before the last
    # '/' is a registry host:port, not a tag separator.
    def fake_run(argv, *, timeout, stdin=None):
        digests = ["localhost:5000/foo@sha256:" + "e" * 64]
        return Captured(returncode=0, output=json.dumps(digests).encode(),
                         truncated=False, timed_out=False)

    assert image_repo_digest("localhost:5000/foo:tag", run=fake_run) == (
        "localhost:5000/foo@sha256:" + "e" * 64)


def test_split_image_ref_registry_with_tag():
    assert _split_image_ref("ghcr.io/jimboschneider/dirtywork-worker:0.4") == (
        "ghcr.io/jimboschneider/dirtywork-worker", "0.4")


def test_split_image_ref_localhost_port_with_tag():
    # Fix item 3: a ':' before the last '/' is a registry host:port, not a
    # tag separator -- the old `image.split("@")[0].split(":")[0]` broke
    # this, returning "localhost" as the name.
    assert _split_image_ref("localhost:5000/foo:tag") == ("localhost:5000/foo", "tag")


def test_split_image_ref_bare_name_no_tag():
    assert _split_image_ref("foo") == ("foo", None)


def test_split_image_ref_digest_only():
    assert _split_image_ref("foo@sha256:" + "a" * 64) == ("foo", None)


def test_split_image_ref_registry_port_no_tag():
    assert _split_image_ref("registry:5000/ns/img") == ("registry:5000/ns/img", None)


def test_docker_storage_paths_linux(monkeypatch):
    monkeypatch.setattr("dirtywork.sandbox.docker_cli.sys.platform", "linux")
    monkeypatch.setattr("dirtywork.sandbox.docker_cli.os.name", "posix")

    def fake_run(argv, *, timeout, stdin=None):
        assert argv == ["info", "--format", "{{.DockerRootDir}}"]
        return Captured(returncode=0, output=b"/var/lib/docker\n", truncated=False, timed_out=False)

    paths = docker_storage_paths(run=fake_run)
    assert Path("/var/lib/docker") in paths
    assert Path("/") in paths


def test_docker_storage_paths_darwin_uses_home(monkeypatch):
    monkeypatch.setattr("dirtywork.sandbox.docker_cli.sys.platform", "darwin")
    monkeypatch.setattr("dirtywork.sandbox.docker_cli.os.name", "posix")

    def fake_run(argv, *, timeout, stdin=None):
        raise AssertionError("docker info should not be called on darwin")

    paths = docker_storage_paths(run=fake_run)
    assert Path.home() in paths
    assert Path("/") in paths


def test_docker_storage_paths_dedupes(monkeypatch):
    monkeypatch.setattr("dirtywork.sandbox.docker_cli.sys.platform", "linux")
    monkeypatch.setattr("dirtywork.sandbox.docker_cli.os.name", "posix")

    def fake_run(argv, *, timeout, stdin=None):
        return Captured(returncode=0, output=b"/\n", truncated=False, timed_out=False)

    paths = docker_storage_paths(run=fake_run)
    assert paths.count(Path("/")) == 1


def _git(repo: Path, *args: str) -> None:
    import subprocess
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "f.txt").write_text("hi")
    _git(r, "add", ".")
    _git(r, "commit", "-m", "init")
    return r


def test_validate_objects_dir_accepts_normal_repo(repo: Path):
    objects = validate_objects_dir(repo)
    assert objects == (repo / ".git" / "objects").resolve()


def test_validate_objects_dir_refuses_symlinked_objects(repo: Path, tmp_path: Path):
    import shutil
    real_objects = repo / ".git" / "objects"
    outside = tmp_path / "outside-objects"
    shutil.move(str(real_objects), str(outside))
    real_objects.symlink_to(outside)
    with pytest.raises(WorkspaceError, match="symlink"):
        validate_objects_dir(repo)


def test_validate_objects_dir_refuses_objects_outside_common_dir(repo: Path, tmp_path: Path, monkeypatch):
    import subprocess
    outside = tmp_path / "outside-objects"
    outside.mkdir()

    real_run = subprocess.run

    def fake_run(argv, **kwargs):
        if "--git-path" in argv and argv[-1] == "objects":
            class R:
                returncode = 0
                stdout = str(outside) + "\n"
            return R()
        return real_run(argv, **kwargs)

    monkeypatch.setattr("dirtywork.sandbox.docker_cli.subprocess.run", fake_run)
    with pytest.raises(WorkspaceError, match="escapes"):
        validate_objects_dir(repo)
