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
    SandboxError) or, at preflight, into an exit-2 hint.

    `timed_out` (spec §4.2) is True ONLY on run()'s expired-timeout path below --
    the one place a DockerError means "the command may still be running". Every
    other raise leaves it False, so DockerSandbox.bash and .grep can tell a real
    timeout from an ordinary docker failure instead of reporting both as a
    timeout. Keyword-only with a default, so every existing positional
    `DockerError("...")` construction and every `except DockerError` in the tree
    keeps working untouched."""

    def __init__(self, *args, timed_out: bool = False):
        super().__init__(*args)
        self.timed_out = timed_out


def _warn(msg: str) -> None:
    """Non-fatal operator-facing note, distinct from DockerError (which
    always fails the run) -- printed to stderr like main()'s `_err`."""
    print(f"warning: {msg}", file=sys.stderr)


def run(argv: list, *, timeout: float, stdin: bytes | None = None) -> Captured:
    """The one entry point to the docker CLI. Prefixes argv with "docker" and
    converts a timeout into a DockerError naming the command, instead of
    silently returning a Captured with timed_out=True — every docker call in
    this codebase must fail loud, not be ignored by an incomplete caller."""
    full = ["docker"] + list(argv)
    captured = run_capped(full, timeout=timeout, stdin=stdin)
    if captured.timed_out:
        raise DockerError(
            f"docker {' '.join(str(a) for a in argv)} timed out after {timeout}s",
            timed_out=True)
    return captured


def docker_version(*, run=run) -> str:
    captured = run(["version", "--format", "{{.Server.Version}}"], timeout=T_QUERY)
    if captured.returncode != 0:
        raise DockerError(
            f"docker version failed: {captured.output.decode('utf-8', 'replace')[:500]}"
        )
    return captured.output.decode("utf-8", "replace").strip()


def _split_image_ref(image: str) -> tuple[str, str | None]:
    """Split a (possibly digest-qualified) image reference into (name, tag).

    A `:` is only a tag separator when it comes after the last `/` — a `:`
    before the last `/` is a registry host:port, not a tag (e.g.
    `localhost:5000/foo:tag` is name `localhost:5000/foo`, tag `tag`;
    `registry:5000/ns/img` has no tag at all). Any `@sha256:...` digest
    suffix is stripped first and ignored here."""
    base = image.split("@", 1)[0]
    last_slash = base.rfind("/")
    last_colon = base.rfind(":")
    if last_colon > last_slash:
        return base[:last_colon], base[last_colon + 1:]
    return base, None


def image_repo_digest(image: str, *, run=run) -> str | None:
    """Return the image's registry digest reference (`<name>@sha256:...`)
    from RepoDigests, or None if the image was built locally and never
    pushed/pulled (no RepoDigests entry), or cannot be inspected at all.

    For PROVENANCE only -- record-keeping (run.json, the pinned-digest
    check) -- never for execution. `docker run`/`create` on a RepoDigests
    ref is not always safe: images loaded via `docker buildx build --load`
    can carry a RepoDigests entry pointing at a registry manifest that was
    never pulled into the local store, and running that ref would try to
    pull it from the network. resolve_image() below sidesteps this
    entirely by always executing the image's local content-addressed Id
    instead, which can never trigger a pull."""
    name, _tag = _split_image_ref(image)
    captured = run(["image", "inspect", "--format", "{{json .RepoDigests}}", image], timeout=T_QUERY)
    if captured.returncode != 0:
        return None
    raw = captured.output.decode("utf-8", "replace").strip()
    try:
        digests = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return None
    if isinstance(digests, list):
        for d in digests:
            if isinstance(d, str) and d.startswith(name + "@sha256:"):
                return d
    return None


def resolve_image(image: str, *, run=run, pinned_digest: str | None = None) -> str:
    """Resolve image to its local content-addressed Id (`sha256:<64hex>`)
    for EXECUTION, pulling if absent (the only network use at preflight).
    If pinned_digest is given, the image's registry digest (via
    image_repo_digest, which still compares against RepoDigests) must
    match it exactly or the run refuses to start -- UNLESS the image has no
    RepoDigests entry at all (built or `docker load`ed locally, never
    pushed/pulled), in which case resolve_image does not refuse: it returns
    the Id and warns on stderr instead. PINNED_DIGEST's job is to guarantee
    REGISTRY provenance -- that the bits behind a mutable tag are the exact
    ones the maintainer published -- and a local build was never fetched
    from a registry to begin with, so there is nothing for the pin to
    verify. It is also the operator's own artefact (and the CI gate that
    exercises this same code path builds the image locally), so refusing to
    run it would make the pin actively hostile to that case rather than
    protective.

    Returning the Id -- never a `name@sha256:...` digest reference -- is
    deliberate, not cosmetic: a RepoDigests candidate is not always
    locally addressable. Images loaded via `docker buildx build --load`
    can carry a RepoDigests entry pointing at a registry manifest that was
    never pulled into the local store, and `docker run`/`create` on that
    ref tries to pull it ("Unable to find image ... locally") -- even
    though `docker image inspect` on the very same ref succeeds, because
    inspect resolves it through the image's own Id under the hood.
    Verifying addressability by inspect is therefore not a sufficient
    check; it was tried and still let an unrunnable ref through. An Id, by
    contrast, is always the local image store's own identifier: `docker
    run`/`create` on an Id can never trigger a network pull, and it is
    unambiguous."""
    captured = run(["image", "inspect", "--format", "{{.Id}}", image], timeout=T_QUERY)
    if captured.returncode != 0:
        pulled = run(["pull", image], timeout=T_PULL)
        if pulled.returncode != 0:
            raise DockerError(
                f"docker pull {image} failed: {pulled.output.decode('utf-8', 'replace')[:500]}"
            )
        captured = run(["image", "inspect", "--format", "{{.Id}}", image], timeout=T_QUERY)
        if captured.returncode != 0:
            raise DockerError(
                f"docker image inspect {image} failed after pull: "
                f"{captured.output.decode('utf-8', 'replace')[:500]}"
            )

    image_id = captured.output.decode("utf-8", "replace").strip()
    if not image_id:
        raise DockerError(f"docker image inspect {image} returned an empty Id")

    if pinned_digest is not None:
        digest = image_repo_digest(image, run=run)
        if digest is None:
            _warn(f"{image} is a locally built image; PINNED_DIGEST is not enforced "
                  f"for local builds")
        else:
            digest_part = digest.split("@", 1)[1]
            if digest_part != pinned_digest:
                raise DockerError(
                    f"resolved image digest {digest_part!r} for {image!r} does not match "
                    f"PINNED_DIGEST {pinned_digest!r}; refusing to run an unpinned image"
                )

    return image_id


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
