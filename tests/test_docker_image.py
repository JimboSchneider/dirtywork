# tests/test_docker_image.py
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKER_DIR = REPO_ROOT / "docker"
IMAGE_TAG = "ghcr.io/jimboschneider/dirtywork-worker:0.5-test"


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


def test_dockerfile_installs_the_packages_the_docs_promise():
    """Unmarked (no daemon needed): the Dockerfile is read as text. The four
    0.8 additions come from a real run whose bash suite needed them (issue
    #30); docker/README.md's package list must stay in step with this."""
    text = (DOCKER_DIR / "Dockerfile").read_text(encoding="utf-8")
    for package in ("git", "bash", "coreutils", "findutils", "python3",
                    "ripgrep", "jq", "uuid-runtime", "shellcheck", "curl"):
        assert f"\n        {package} \\\n" in text, f"{package} is not in the apt list"
    assert "\nFROM node:22-bookworm-slim\n" in text, "the base image must be the official Node 22 image"
    assert "\nRUN userdel -r node && useradd -u 1000 -m worker\n" in text
    for package in ("nodejs", "npm"):
        assert f"\n        {package} \\\n" not in text, f"{package} comes from the base image, not apt"
    readme = (DOCKER_DIR / "README.md").read_text(encoding="utf-8")
    for package in ("jq", "uuid-runtime", "shellcheck", "curl", "node:22-bookworm-slim"):
        assert package in readme, f"{package} is not documented in docker/README.md"
