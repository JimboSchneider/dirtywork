# tests/test_docker_runs.py
"""Docker-marked end-to-end tests (marker `docker`; tests/conftest.py skips
this whole file automatically when no daemon is reachable) that exercise two
paths only a real daemon can prove:

- `dirtywork runs clean`'s label-match safety rule (dirtywork/runs.py
  `_clean_docker_resource`) against real, labelled docker resources -- the
  rest of the test suite only ever exercises this against FakeCaptured.
- `dirtywork bench`'s acceptance scoring (dirtywork/bench.py
  `_run_acceptance`) against a real acceptance container: a solved fixture
  passes, and tampering with the worker's own copy of `acceptance/` (which
  is only ever hashed, never executed) is caught as `gamed`.

Every docker object this file creates uses a uuid-suffixed name and is
removed in a `finally` block, independent of test outcome.
"""
from __future__ import annotations

import argparse
import subprocess
import uuid
from pathlib import Path

import pytest

from dirtywork import bench, rundir, runs
from dirtywork.sandbox import docker_args, docker_cli


def _slug(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _git(repo: Path, *args) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


def _resolve_default_image() -> str:
    return docker_cli.resolve_image(
        docker_args.DEFAULT_IMAGE, pinned_digest=docker_args.pin_for(docker_args.DEFAULT_IMAGE))


def _create_labelled_container(slug: str, repo_label: str, image_ref: str) -> str:
    """A `docker create` (never started) is enough for `runs clean`'s
    inspect-then-rm flow -- it only needs the container to exist and carry
    the dirtywork.run/dirtywork.repo labels `docker_args._label_args` sets on
    the real worker/export containers."""
    name = docker_args.container_name(slug)
    docker_cli.run(
        ["create", "--name", name, *docker_args._label_args(slug, repo_label),
         "--entrypoint", "/bin/cat", image_ref],
        timeout=docker_cli.T_LIFECYCLE,
    )
    return name


def _create_labelled_volume(slug: str, repo_label: str) -> str:
    name = docker_args.volume_name(slug)
    docker_cli.run(
        ["volume", "create", *docker_args._label_args(slug, repo_label), name],
        timeout=docker_cli.T_QUERY,
    )
    return name


def _rm_container(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def _rm_volume(name: str) -> None:
    subprocess.run(["docker", "volume", "rm", "-f", name], capture_output=True)


def _container_exists(name: str) -> bool:
    return subprocess.run(["docker", "inspect", name], capture_output=True).returncode == 0


def _volume_exists(name: str) -> bool:
    return subprocess.run(["docker", "volume", "inspect", name], capture_output=True).returncode == 0


def _clean_args(slug=None, all=False, keep_transcript=False, force=False):
    return argparse.Namespace(slug=slug, all=all, keep_transcript=keep_transcript, force=force)


@pytest.mark.docker
def test_docker_runs_clean_label_match_removes_pair_and_spares_decoy(tmp_path, monkeypatch):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")

    image_ref = _resolve_default_image()

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "--allow-empty", "-m", "i")
    repo_label = docker_args.repo_label(repo)

    decoy_repo = tmp_path / "decoy-repo"  # never created on disk -- repo_label only hashes the path
    decoy_label = docker_args.repo_label(decoy_repo)

    slug = _slug("clean")
    decoy_slug = _slug("decoy")
    container = volume = decoy_container = decoy_volume = None
    try:
        container = _create_labelled_container(slug, repo_label, image_ref)
        volume = _create_labelled_volume(slug, repo_label)
        decoy_container = _create_labelled_container(decoy_slug, decoy_label, image_ref)
        decoy_volume = _create_labelled_volume(decoy_slug, decoy_label)

        run_dir = tmp_path / "runs" / slug
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(
            '{"slug": "%s", "status": "completed", "repo": "%s", "worktree": null, '
            '"branch": null, "container": "%s", "volume": "%s"}'
            % (slug, str(repo), container, volume)
        )

        rc = runs.cmd_clean(_clean_args(slug))
        assert rc == 0

        assert not _container_exists(container)
        assert not _volume_exists(volume)

        assert _container_exists(decoy_container)
        assert _volume_exists(decoy_volume)
    finally:
        for name in (container, decoy_container):
            if name:
                _rm_container(name)
        for name in (volume, decoy_volume):
            if name:
                _rm_volume(name)


_SOLVED_SUM_RANGE = '''def sum_range(low, high):
    """Return the sum of the integers from low to high, INCLUSIVE."""
    total = 0
    for i in range(low, high + 1):  # fixed: now inclusive of `high`
        total += i
    return total
'''


def _write_files_into_volume(volume: str, image_ref: str, files: dict) -> None:
    """Write {relative_path: content} into a docker volume's /work via a
    throwaway root container -- --user 0:0 gets write access to a freshly
    created (root-owned) volume, same rationale as
    docker_args.prep_run_argv's chown step."""
    lines = []
    for rel_path, content in files.items():
        dest = f"/work/{rel_path}"
        lines.append(f"mkdir -p {Path(dest).parent.as_posix()}")
        lines.append(f"cat > {dest} <<'DIRTYWORK_TEST_EOF'\n{content}DIRTYWORK_TEST_EOF")
    script = "\n".join(lines)
    result = docker_cli.run(
        ["run", "--rm", "--network", "none", "--user", "0:0",
         "--mount", f"type=volume,src={volume},dst=/work",
         "-e", f"PATH={docker_args.PATH_ENV}",
         "--entrypoint", "/bin/sh", image_ref, "-c", script],
        timeout=docker_cli.T_LIFECYCLE,
    )
    assert result.returncode == 0, result.output.decode("utf-8", "replace")


@pytest.mark.docker
def test_docker_bench_acceptance_pass_then_tampered_copy_is_gamed(tmp_path):
    task = "py-fix-off-by-one"
    bench_data = bench._bench_json(task)
    acceptance_src = (bench.BENCH_REPOS / task / "acceptance" / "check_sum_range.py").read_text()

    image_ref = _resolve_default_image()

    volume = f"dwtest-{_slug('acc')}"
    docker_cli.run(["volume", "create", volume], timeout=docker_cli.T_QUERY)
    try:
        _write_files_into_volume(volume, image_ref, {
            "sum_range.py": _SOLVED_SUM_RANGE,
            "acceptance/check_sum_range.py": acceptance_src,
        })
        assert bench._run_acceptance(task, bench_data, volume) == "pass"

        # Tamper with the WORKER's own copy of acceptance/ -- the acceptance
        # command itself always runs from the real, read-only
        # bench/repos/<task>/acceptance mount, so this can't flip the result
        # to "fail"; it can only be caught by the pre-run hash check, which
        # is exactly what "gamed" means.
        _write_files_into_volume(volume, image_ref, {
            "acceptance/check_sum_range.py": acceptance_src + "\n# tampered by worker\n",
        })
        assert bench._run_acceptance(task, bench_data, volume) == "gamed"
    finally:
        subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True)
