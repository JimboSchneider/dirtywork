# dirtywork/sandbox/docker_args.py
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_IMAGE = "dirtywork/worker:0.3"
# Set at release, after `docker build`/publish (docker/README.md documents the
# procedure). When set, resolve_image()'s pinned_digest check refuses to run
# any image whose resolved digest does not match.
PINNED_DIGEST: str | None = None
# Always passed explicitly on every docker create/run/exec so an image's own
# ENTRYPOINT/CMD/ENV can never substitute a different PATH for the tether,
# chown, or an export step (spec §3 "Entrypoint and PATH are always explicit").
PATH_ENV = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


@dataclass
class DockerConfig:
    image: str = DEFAULT_IMAGE
    network: str = "none"
    memory: str = "4g"
    cpus: str = "2"
    pids_limit: int = 512
    tmp_size: str = "1g"
    gitdir_size: str = "512m"
    home_size: str = "256m"
    max_worktree_mb: int = 2048
    max_worktree_files: int = 200_000
    min_free_mb: int = 2048
    max_patch_mb: int = 10
    keep_volume: bool = False


def container_name(slug: str) -> str:
    return f"dw-{slug}"


def volume_name(slug: str) -> str:
    return f"dw-{slug}-work"


def repo_label(repo: Path) -> str:
    """sha256 hex of the resolved repo path — the dirtywork.repo label value,
    used by `runs clean`'s collision rule to confirm a container/volume
    belongs to this repo before removing it."""
    return hashlib.sha256(str(repo.resolve()).encode("utf-8")).hexdigest()


def exec_argv(name: str, argv: list, *, workdir: str = "/work", stdin: bool = False,
              env: dict | None = None) -> list:
    out = ["exec", "-w", workdir]
    if stdin:
        out.append("-i")
    if env:
        for k, v in env.items():
            out += ["-e", f"{k}={v}"]
    out.append(name)
    out += list(argv)
    return out


def prep_run_argv(cfg: DockerConfig, slug: str, image_ref: str, uid: int, gid: int) -> list:
    """A throwaway container that chowns a freshly-created volume's root to
    the run user (a fresh Docker volume's root is root-owned — spec §2 step
    5). --user 0:0 so chown itself has permission; --cap-drop ALL plus
    --cap-add CHOWN is the minimum capability for that one syscall."""
    # cfg is unused here and kept for call-site symmetry with the other builders (interface fixed by the plan).
    return [
        "run", "--rm", "--network", "none", "--user", "0:0",
        "--cap-drop", "ALL", "--cap-add", "CHOWN",
        "--mount", f"type=volume,src={volume_name(slug)},dst=/work",
        "-e", f"PATH={PATH_ENV}",
        "--entrypoint", "/bin/chown",
        image_ref,
        f"{uid}:{gid}", "/work",
    ]


def _label_args(slug: str, repo_label: str) -> list:
    """Return label arguments for docker create/run."""
    return [
        "--label", f"dirtywork.run={slug}",
        "--label", f"dirtywork.repo={repo_label}",
    ]


def _security_args(pids_limit: int) -> list:
    """Return security-related arguments for docker create/run."""
    return [
        "--pids-limit", str(pids_limit),
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
    ]


def _env_entrypoint_args() -> list:
    """Return environment and entrypoint arguments for docker create/run."""
    return [
        "-e", "GIT_DIR=/gitdir",
        "-e", "GIT_WORK_TREE=/work",
        "-e", "HOME=/home/worker",
        "-e", "TMPDIR=/tmp",
        "-e", "LANG=C.UTF-8",
        "-e", "GIT_AUTHOR_NAME=dirtywork",
        "-e", "GIT_AUTHOR_EMAIL=dirtywork@localhost",
        "-e", "GIT_COMMITTER_NAME=dirtywork",
        "-e", "GIT_COMMITTER_EMAIL=dirtywork@localhost",
        "-e", f"PATH={PATH_ENV}",
        "--entrypoint", "/bin/cat",
    ]


def worker_create_argv(cfg: DockerConfig, slug: str, image_ref: str, uid: int, gid: int,
                        objects_dir: Path, *, repo_label: str) -> list:
    """Spec §3's exact create argv. Never passes -w/WORKDIR at container
    level (verified on Docker Desktop: it resets the volume root's ownership
    to root:root, persistently) — every tool exec passes -w /work itself.
    --entrypoint /bin/cat plus -i plus --init makes tini PID 1 with cat
    reading stdin as its only child (the lifetime tether, Task 6)."""
    name = container_name(slug)
    return [
        "create", "-i", "--init", "--name", name,
        *(_label_args(slug, repo_label)),
        "--network", cfg.network,
        "--memory", cfg.memory,
        "--memory-swap", cfg.memory,
        "--cpus", cfg.cpus,
        *(_security_args(cfg.pids_limit)),
        "--user", f"{uid}:{gid}",
        "--tmpfs", f"/tmp:rw,exec,size={cfg.tmp_size},mode=1777",
        "--tmpfs", f"/gitdir:rw,size={cfg.gitdir_size},mode=0700,uid={uid},gid={gid}",
        "--tmpfs", f"/home/worker:rw,size={cfg.home_size},mode=0700,uid={uid},gid={gid}",
        "--mount", f"type=volume,src={volume_name(slug)},dst=/work",
        "--mount", f"type=bind,src={objects_dir},dst=/repo.git/objects,readonly",
        *(_env_entrypoint_args()),
        image_ref,
    ]


def export_create_argv(cfg: DockerConfig, slug: str, image_ref: str, uid: int, gid: int,
                        objects_dir: Path, *, repo_label: str) -> list:
    """Spec §7: a FRESH container for export, always --network none
    regardless of cfg.network (export needs no network and gets none — this
    is asserted by test_export_create_argv_always_network_none_even_with_bridge_cfg
    even when the operator passed --allow-network for the worker container).
    Volume mounted readonly: no worker process is alive here, nothing
    should be able to write /work during export. --pids-limit 256 and
    smaller /tmp and /home/worker tmpfs than the worker container — only git
    runs here."""
    name = f"{container_name(slug)}-export"
    return [
        "create", "-i", "--init", "--name", name,
        *(_label_args(slug, repo_label)),
        "--network", "none",
        "--memory", cfg.memory,
        "--memory-swap", cfg.memory,
        "--cpus", cfg.cpus,
        *(_security_args(256)),
        "--user", f"{uid}:{gid}",
        "--tmpfs", "/tmp:rw,exec,size=256m,mode=1777",
        "--tmpfs", f"/gitdir:rw,size=2g,mode=0700,uid={uid},gid={gid}",
        "--tmpfs", f"/home/worker:rw,size=64m,mode=0700,uid={uid},gid={gid}",
        "--mount", f"type=volume,src={volume_name(slug)},dst=/work,readonly",
        "--mount", f"type=bind,src={objects_dir},dst=/repo.git/objects,readonly",
        *(_env_entrypoint_args()),
        image_ref,
    ]
