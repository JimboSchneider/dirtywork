# tests/conftest.py
from __future__ import annotations

import shutil
import subprocess

import pytest


def _docker_available() -> bool:
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return False
    try:
        result = subprocess.run([docker_bin, "version"], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


@pytest.fixture(scope="session")
def docker_available() -> bool:
    return _docker_available()


def pytest_collection_modifyitems(config, items):
    """Every test marked `docker` is skipped automatically when no daemon
    is reachable — `-m 'not live and not docker'` in addopts already
    excludes them from the default run, but running `pytest -m docker`
    explicitly (or any invocation that overrides addopts) must still not
    hang or fail hard on a machine without Docker."""
    if _docker_available():
        return
    skip_docker = pytest.mark.skip(reason="docker daemon not available")
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(skip_docker)
