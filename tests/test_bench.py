from __future__ import annotations

import hashlib
import json
import shlex
import shutil
import subprocess
from pathlib import Path

BENCH_REPOS = Path(__file__).resolve().parent.parent / "bench" / "repos"
TASK_NAMES = ["py-fix-off-by-one", "node-add-cli-flag", "sh-fix-script"]


def _bench_json(task_name: str) -> dict:
    return json.loads((BENCH_REPOS / task_name / "bench.json").read_text())


def _local_command(task_dir: Path, command: str) -> str:
    """The container command with /acceptance rewritten to this checkout's copy,
    so the same string can be exercised on the host."""
    return command.replace("/acceptance/", str(task_dir / "acceptance") + "/")


def test_every_task_dir_exists_and_is_tiny():
    assert BENCH_REPOS.is_dir()
    for name in TASK_NAMES:
        task_dir = BENCH_REPOS / name
        assert task_dir.is_dir(), f"missing bench fixture: {name}"
        files = [p for p in task_dir.rglob("*") if p.is_file()]
        assert len(files) <= 5, f"{name} has {len(files)} files, expected <= 5"


def test_bench_json_schema():
    for name in TASK_NAMES:
        data = _bench_json(name)
        assert isinstance(data.get("task"), str) and data["task"]
        acceptance = data.get("acceptance")
        assert isinstance(acceptance, dict)
        assert isinstance(acceptance.get("command"), str) and acceptance["command"]
        assert isinstance(acceptance.get("hashes"), dict) and acceptance["hashes"]


def test_bench_json_hashes_match_files_on_disk():
    for name in TASK_NAMES:
        task_dir = BENCH_REPOS / name
        data = _bench_json(name)
        for rel_path, expected_hash in data["acceptance"]["hashes"].items():
            assert rel_path.startswith("acceptance/"), (
                f"{name}: hashed path '{rel_path}' is not under acceptance/")
            p = task_dir / rel_path
            assert p.is_file(), f"{name}: hashed path '{rel_path}' does not exist"
            actual = hashlib.sha256(p.read_bytes()).hexdigest()
            assert actual == expected_hash, (
                f"{name}: {rel_path} hash mismatch -- recompute the hashes map with "
                f"hashlib.sha256(path.read_bytes()).hexdigest()")


def test_acceptance_commands_use_the_mounted_copy():
    # Spec SP3 section 5: the acceptance command never comes from the worktree.
    # Task 14 mounts acceptance/ read-only at /acceptance, so every command must
    # name that absolute path and never a /work-relative one.
    for name in TASK_NAMES:
        command = _bench_json(name)["acceptance"]["command"]
        assert "/acceptance/" in command, f"{name}: command does not use /acceptance"
        assert not command.startswith("acceptance/"), f"{name}: command is worktree-relative"


def test_task_source_files_are_unsolved():
    # Fixtures ship the BUGGY state -- if the acceptance check already passes
    # against the fixture as committed, the task gives the model nothing to do.
    runtimes = {"py-fix-off-by-one": "python3", "sh-fix-script": "bash",
                "node-add-cli-flag": "node"}
    for name in TASK_NAMES:
        if shutil.which(runtimes[name]) is None:
            continue  # optional runtime not installed in this environment
        task_dir = BENCH_REPOS / name
        command = _local_command(task_dir, _bench_json(name)["acceptance"]["command"])
        result = subprocess.run(shlex.split(command), cwd=str(task_dir),
                                capture_output=True, text=True)
        assert result.returncode != 0, (
            f"{name}: the acceptance check already passes on the unsolved fixture")
