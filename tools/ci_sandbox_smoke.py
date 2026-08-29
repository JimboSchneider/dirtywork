# tools/ci_sandbox_smoke.py
"""CI diagnostic: exercises DockerSandbox.start() against the real Docker
daemon and prints exactly why it fails when it fails. The docker-live job's
docker-mode runs have so far collapsed to an opaque sandbox_error on
ubuntu-latest with no captured detail (see .superpowers/sdd/
2026-08-16-sp2-docker-sandbox/ci-smoke-brief.md), while the same suites pass
on the owner's Mac -- this script is meant to surface the actual failure
text plus the uid/gid and mount context needed to diagnose the platform
difference.

Run directly (stdlib only, plus the dirtywork package itself, which has no
third-party runtime dependencies -- no pytest, no fixtures):

    python tools/ci_sandbox_smoke.py

Exits non-zero (and re-raises) on any failure, so it is meant to run as its
own CI step before the pytest step that actually needs a healthy sandbox.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dirtywork.sandbox import docker_args, docker_cli
from dirtywork.sandbox.docker import DockerSandbox


def _diag_cmd(argv: list) -> str:
    """Run a diagnostic docker CLI command and return its stdout (falling
    back to stderr). Diagnostics should never raise -- they should just
    show what happened, even if that is "docker itself is unreachable"."""
    try:
        result = subprocess.run(["docker"] + argv, capture_output=True, text=True, timeout=30)
    except Exception as e:  # pragma: no cover - purely diagnostic
        return f"<failed to run docker {' '.join(argv)}: {type(e).__name__}: {e}>"
    return result.stdout.strip() or result.stderr.strip()


def _make_smoke_repo(tmp_path: Path) -> Path:
    """A throwaway git repo with one commit -- mirrors
    tests/docker_live_helpers.py's _make_live_repo, kept independent here
    so this script has no dependency on the test suite."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)

    git("init", "-b", "main")
    git("config", "user.email", "smoke@dirtywork.local")
    git("config", "user.name", "dirtywork-smoke")
    (repo / "README.md").write_text("# smoke\n")
    git("add", ".")
    git("commit", "-m", "init")
    return repo


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="dw-smoke-") as tmp:
        tmp_path = Path(tmp)
        repo = _make_smoke_repo(tmp_path)

        worktree = repo / ".worktrees" / "dw-smoke"
        worktree.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "worktree", "add", "--no-checkout", "-b", "dirtywork/smoke", str(worktree), "HEAD"],
            cwd=str(repo), check=True, capture_output=True, text=True,
        )
        base_commit = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

        run_dir = tmp_path / "run"
        run_dir.mkdir()

        cfg = docker_args.DockerConfig()

        uid = os.getuid()
        gid = os.getgid()
        print(f"os.getuid()={uid} os.getgid()={gid}")
        print("docker version:", _diag_cmd(
            ["version", "--format", "{{.Server.Version}} {{.Server.Arch}} {{.Server.Os}}"]))
        print("docker info:", _diag_cmd(
            ["info", "--format", "{{.CgroupVersion}} {{.Driver}} {{.DockerRootDir}}"]))

        # Independently resolved here (start() below resolves its own copy)
        # purely so the exact create argv -- uid/gid and mounts included --
        # can be printed before start() runs.
        objects_dir = docker_cli.validate_objects_dir(repo)
        image_ref = docker_cli.resolve_image(cfg.image, pinned_digest=docker_args.PINNED_DIGEST)
        image_digest = docker_cli.image_repo_digest(cfg.image)
        print("resolved image ref (local Id, used for execution):", image_ref)
        print("resolved image digest (registry, provenance only):", image_digest)
        label = docker_args.repo_label(repo)
        create_argv = docker_args.worker_create_argv(
            cfg, "smoke", image_ref, uid, gid, objects_dir, repo_label=label,
        )
        print("docker create argv:", create_argv)

        sb = DockerSandbox(cfg, run_dir=run_dir)
        try:
            sb.start(worktree, repo, "smoke", base_commit)
            print("START OK")
            node_out = sb.bash("node --version")
            print("node --version:", node_out.strip())
            node_version = node_out.strip().splitlines()[-1]
            assert node_version.startswith("v22."), f"expected Node 22 in the image, got {node_out!r}"
            print(sb.bash("id; git --version; ls -la /work /gitdir | head"))
            print(sb.finalize().export_status)
        except Exception as e:
            print(f"START FAILED: {type(e).__name__}: {e}")
            raise
        finally:
            sb.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
