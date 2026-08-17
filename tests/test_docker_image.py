# tests/test_docker_image.py
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKER_DIR = REPO_ROOT / "docker"
IMAGE_TAG = "ghcr.io/jimboschneider/dirtywork-worker:0.4-test"


@pytest.mark.docker
def test_docker_build_succeeds_and_image_has_required_tools():
    build = subprocess.run(
        ["docker", "build", "-t", IMAGE_TAG, str(DOCKER_DIR)],
        capture_output=True, text=True, timeout=600,
    )
    assert build.returncode == 0, build.stderr

    git_version = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "/usr/bin/git", IMAGE_TAG, "--version"],
        capture_output=True, text=True, timeout=30,
    )
    assert git_version.returncode == 0
    assert "git version" in git_version.stdout

    subprocess.run(["docker", "rmi", "-f", IMAGE_TAG], capture_output=True, timeout=60)
