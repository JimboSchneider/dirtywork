from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from ..procs import Captured, run_capped
from ..workspace import WorkspaceError
from . import SandboxError

T_QUERY = 10        # version/inspect/top/volume *
T_LIFECYCLE = 60     # create/start/exec true/rm -f/kill
T_PULL = 600         # pull
T_EXPORT_STEP = 300  # each export docker exec


class DockerError(SandboxError):
    """Raised on a nonzero docker CLI exit or an expired timeout. Callers
    turn this into status sandbox_error (via the runner catching
    SandboxError) or, at preflight, into an exit-2 hint."""


def run(argv: list, *, timeout: float, stdin: bytes | None = None) -> Captured:
    """The one entry point to the docker CLI. Prefixes argv with "docker" and
    converts a timeout into a DockerError naming the command, instead of
    silently returning a Captured with timed_out=True — every docker call in
    this codebase must fail loud, not be ignored by an incomplete caller."""
    full = ["docker"] + list(argv)
    captured = run_capped(full, timeout=timeout, stdin=stdin)
    if captured.timed_out:
        raise DockerError(f"docker {' '.join(str(a) for a in argv)} timed out after {timeout}s")
    return captured


def docker_version(*, run=run) -> str:
    captured = run(["version", "--format", "{{.Server.Version}}"], timeout=T_QUERY)
    if captured.returncode != 0:
        raise DockerError(
            f"docker version failed: {captured.output.decode('utf-8', 'replace')[:500]}"
        )
    return captured.output.decode("utf-8", "replace").strip()


def resolve_image(image: str, *, run=run, pinned_digest: str | None = None) -> str:
    """Resolve image to <name>@sha256:<digest>, pulling if absent (the only
    network use at preflight). Falls back to .Id for locally-built images
    with no RepoDigests. If pinned_digest is given, the resolved digest must
    match it exactly or the run refuses to start."""
    name = image.split("@")[0].split(":")[0]

    captured = run(["image", "inspect", "--format", "{{json .RepoDigests}}", image], timeout=T_QUERY)
    if captured.returncode != 0:
        pulled = run(["pull", image], timeout=T_PULL)
        if pulled.returncode != 0:
            raise DockerError(
                f"docker pull {image} failed: {pulled.output.decode('utf-8', 'replace')[:500]}"
            )
        captured = run(["image", "inspect", "--format", "{{json .RepoDigests}}", image], timeout=T_QUERY)
        if captured.returncode != 0:
            raise DockerError(
                f"docker image inspect {image} failed after pull: "
                f"{captured.output.decode('utf-8', 'replace')[:500]}"
            )

    raw = captured.output.decode("utf-8", "replace").strip()
    try:
        digests = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        digests = []

    ref = None
    if isinstance(digests, list):
        for d in digests:
            if isinstance(d, str) and d.startswith(name + "@sha256:"):
                ref = d
                break

    if ref is None:
        id_captured = run(["image", "inspect", "--format", "{{.Id}}", image], timeout=T_QUERY)
        if id_captured.returncode != 0:
            raise DockerError(
                f"cannot resolve a digest for {image}: "
                f"{id_captured.output.decode('utf-8', 'replace')[:500]}"
            )
        image_id = id_captured.output.decode("utf-8", "replace").strip()
        ref = f"{name}@{image_id}"

    if pinned_digest is not None:
        digest_part = ref.split("@", 1)[1] if "@" in ref else ""
        if digest_part != pinned_digest:
            raise DockerError(
                f"resolved image digest {digest_part!r} for {image!r} does not match "
                f"PINNED_DIGEST {pinned_digest!r}; refusing to run an unpinned image"
            )
    return ref


def docker_storage_paths(*, run=run) -> list:
    """Filesystems the watchdog polls for free space (spec §6): Docker's own
    data root on Linux, the user's home volume on macOS/Windows (Docker
    Desktop's VM disk lives there), and always "/" on POSIX (the union mount
    for tmpfs-backed containers can also live on the root filesystem)."""
    paths = []
    if sys.platform.startswith("linux"):
        captured = run(["info", "--format", "{{.DockerRootDir}}"], timeout=T_QUERY)
        if captured.returncode == 0:
            root = captured.output.decode("utf-8", "replace").strip()
            if root:
                paths.append(Path(root))
    else:
        paths.append(Path.home())
    if os.name == "posix":
        paths.append(Path("/"))
    deduped = []
    seen = set()
    for p in paths:
        key = str(p)
        if key not in seen:
            seen.add(key)
            deduped.append(p)
    return deduped


def validate_objects_dir(repo: Path) -> Path:
    """Spec §2 step 1: the object store is the ONLY host path ever mounted
    into a container, so it gets its own validation, independent of docker.
    A symlink at the final path component is refused outright (no host
    directory should ever be silently substituted); the resolved path must
    lie inside the resolved git common dir (no ../ escape via a crafted
    .git file or a linked-worktree gitdir)."""
    common = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
                             capture_output=True, text=True)
    if common.returncode != 0:
        raise WorkspaceError(f"cannot locate git common dir for {repo}")
    common_dir = Path(common.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = repo / common_dir
    common_resolved = common_dir.resolve()

    objects = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-path", "objects"],
                              capture_output=True, text=True)
    if objects.returncode != 0:
        raise WorkspaceError(f"cannot locate objects dir for {repo}")
    objects_dir = Path(objects.stdout.strip())
    if not objects_dir.is_absolute():
        objects_dir = repo / objects_dir

    try:
        st = os.lstat(objects_dir)
    except OSError as e:
        raise WorkspaceError(f"cannot stat objects dir {objects_dir}: {e}")
    if not stat.S_ISDIR(st.st_mode):
        raise WorkspaceError(
            f"objects dir {objects_dir} is a symlink or non-directory — refusing to "
            f"mount it into a container"
        )

    objects_resolved = objects_dir.resolve()
    if objects_resolved != common_resolved and common_resolved not in objects_resolved.parents:
        raise WorkspaceError(
            f"objects dir {objects_resolved} escapes git common dir {common_resolved}"
        )
    return objects_resolved
