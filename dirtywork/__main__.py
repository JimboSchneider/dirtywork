# dirtywork/__main__.py
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .budget import DEFAULT_MAX_WORKTREE_FILES, DEFAULT_MAX_WORKTREE_MB
from .llm import LLMError, LMStudioClient
from .rundir import RUNS_DIR, RunDirError, create_run_dir, ensure_runs_dir, read_run_json, write_run_json
from .runner import Runner
from .sandbox import SandboxError, docker_args, docker_cli
from .sandbox.docker import DockerSandbox
from .sandbox.docker_args import DEFAULT_IMAGE, DockerConfig
from .sandbox.docker_cli import DockerError, docker_version, resolve_image, validate_objects_dir
from .sandbox.host import HostSandbox
from .tools import ToolExecutor
from .transcript import Transcript
from .workspace import (
    WorkspaceError,
    create_worktree,
    ensure_worktrees_excluded,
    load_repo_context,
    make_slug,
    preflight_repo,
    remove_worktree,
    worktree_base_commit,
)

DEFAULT_MODEL = "qwen/qwen3-coder-next"
DOCKER_WORKDIR = "/work"


def build_system_prompt(display_root, repo_context: str | None) -> str:
    """`display_root` is what the model is told its files live at: the host
    worktree path in host mode, or the fixed in-container mount point
    (DOCKER_WORKDIR) in docker mode -- the model never sees a host path it
    cannot `cd` to (docker.py's `_rel()` rejects absolute paths outside the
    container's own tree)."""
    prompt = f"""You are a coding agent. Your files live at {display_root} -- treat it as your working directory for every tool call.
Complete the task, then reply with a plain-text summary of what you changed and what commands you ran.

Rules:
- Use edit_file or write_file for ALL file changes. Never modify files via bash (no sed -i, no echo redirects, no heredocs).
- Paths are relative to {display_root}.
- Explore before editing: use list_dir, grep, and read_file to understand the code first.
- Verify your work: run the repo's tests or build via bash before declaring the task complete.
- Do not run git commit or git branch commands; leave all changes uncommitted for review.
- When the task is complete, reply WITHOUT calling any tools — that final plain reply ends the run."""
    if repo_context:
        prompt += f"\n\nRepository conventions (from the repo's own docs):\n\n{repo_context}"
    return prompt


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def _docker_preflight(repo: Path, image: str) -> tuple[str, str | None, bool]:
    """Spec §2 step 1: docker_version (daemon reachable) → resolve_image
    (local Id, pulling if absent — the only network use at start) →
    validate_objects_dir (the only host path ever mounted). All read-only
    on the operator's clone; nothing is created yet.

    Returns (image_ref, image_digest, image_pinned). image_ref is the
    image's local content-addressed Id (`sha256:<64hex>`) — handed to
    DockerSandbox for EXECUTION, since an Id can never trigger a network
    pull at `docker run`/`create` time, unlike a `name@sha256:...` digest
    reference (see resolve_image's docstring). image_digest is the registry
    digest from RepoDigests, or None for a locally-built image that was
    never pulled — recorded for PROVENANCE only (sandbox_info, run.json),
    never used to run anything.

    PINNED_DIGEST is only ever passed for the maintained default image —
    the operator chose a `--image` deliberately, so 0.4.1 never second-
    guesses that choice against a pin meant for a different image (an
    unrecognized/mismatched image would just make every custom --image run
    refuse to start). image_pinned is True only when the pin was actually
    enforced: PINNED_DIGEST was passed for this run AND the image has a
    RepoDigests entry (i.e. it was pulled from the registry, not a local
    build resolve_image only warns about — see resolve_image's docstring).

    A DockerError raised while resolving either value is tagged with a
    `.preflight_step` attribute ("daemon" or "image") so main()'s exit-2
    handler can give a hint specific to what actually failed, instead of
    blaming an unreachable daemon for e.g. an unpullable image (Important
    #6). `validate_objects_dir` raises WorkspaceError, handled separately
    by its own except clause in main()."""
    try:
        docker_version()
    except DockerError as e:
        e.preflight_step = "daemon"
        raise
    pinned_digest = docker_args.PINNED_DIGEST if image == docker_args.DEFAULT_IMAGE else None
    try:
        image_ref = resolve_image(image, pinned_digest=pinned_digest)
        image_digest = docker_cli.image_repo_digest(image, run=docker_cli.run)
    except DockerError as e:
        e.preflight_step = "image"
        raise
    validate_objects_dir(repo)
    image_pinned = pinned_digest is not None and image_digest is not None
    return image_ref, image_digest, image_pinned


def _build_sandbox(args, *, run_dir: Path, worktree: Path, repo: Path, slug: str,
                    base_commit: str, transcript, image_ref, image_digest, image_pinned):
    """Construct and start the backend named by --sandbox, returning
    (sandbox, sandbox_info) for Runner's run_info. A docker-mode failure
    during start() is cleaned up here (best-effort stop(), so a half-created
    container/volume is not left running) before the exception propagates —
    callers only need to decide what to report, not what to tear down."""
    if args.sandbox == "docker":
        cfg = DockerConfig(
            image=args.image,
            network="bridge" if args.allow_network else "none",
            memory=args.memory,
            cpus=args.cpus,
            tmp_size=args.tmp_size,
            gitdir_size=args.gitdir_size,
            max_worktree_mb=args.max_worktree_mb,
            max_worktree_files=args.max_worktree_files,
            min_free_mb=args.min_free_mb,
            max_patch_mb=args.max_patch_mb,
            keep_volume=args.keep_volume,
        )
        sandbox = DockerSandbox(cfg, run_dir=run_dir, transcript=transcript, image_ref=image_ref)
        try:
            sandbox.start(worktree, repo, slug, base_commit)
        except Exception:
            try:
                sandbox.stop()
            except Exception:
                pass
            raise
        sandbox.watchdog.start()  # only place a real Watchdog thread is started
        sandbox_info = {
            "backend": "docker", "image": args.image, "image_digest": image_digest,
            "image_pinned": image_pinned,
            "network": cfg.network, "memory": cfg.memory, "cpus": cfg.cpus,
            "pids_limit": cfg.pids_limit, "tmp_size": cfg.tmp_size,
            "gitdir_size": cfg.gitdir_size, "max_worktree_mb": cfg.max_worktree_mb,
            "max_worktree_files": cfg.max_worktree_files,
            "user": f"{sandbox.uid}:{sandbox.gid}",
        }
    else:
        sandbox = HostSandbox(worktree, max_worktree_mb=args.max_worktree_mb,
                               max_worktree_files=args.max_worktree_files)
        sandbox.start(worktree, repo, slug, base_commit)
        sandbox_info = "none"
    return sandbox, sandbox_info


def _write_run_json_start(run_dir: Path, *, slug: str, repo: Path, worktree: Path,
                           branch: str, base_commit: str, args, image_digest,
                           image_pinned) -> None:
    write_run_json(run_dir, {
        "schema_version": 2,
        "status": "running",
        "slug": slug,
        "repo": str(repo),
        "worktree": str(worktree),
        "branch": branch,
        "base_commit": base_commit,
        "container": docker_args.container_name(slug) if args.sandbox == "docker" else None,
        "volume": docker_args.volume_name(slug) if args.sandbox == "docker" else None,
        "image": args.image if args.sandbox == "docker" else None,
        "image_digest": image_digest,
        "image_pinned": image_pinned,
        "host_pid": os.getpid(),
        "started": datetime.now(timezone.utc).isoformat(),
        "sandbox": args.sandbox,
    })


def _update_run_json(run_dir: Path, **fields) -> None:
    """Best-effort merge-update of run.json (step 11); never raises — a
    failure here must not break the stdout JSON contract of exactly one
    object per run."""
    try:
        existing = read_run_json(run_dir)
        existing.update(ended=datetime.now(timezone.utc).isoformat(), **fields)
        write_run_json(run_dir, existing)
    except Exception:
        pass


def _emit_result(*, status: str, worktree: Path, branch: str, transcript_path: Path,
                  run_dir: Path, turns, usage: dict, final_message: str, **extra) -> dict:
    """The one place that shapes the stdout JSON contract — both the success
    path and every failure path funnel through here so the field set can
    never drift between them."""
    payload = {
        "schema_version": 2,
        "status": status,
        "worktree": str(worktree),
        "branch": branch,
        "transcript": str(transcript_path),
        "turns": turns,
        "usage": usage,
        "final_message": final_message,
        "run_dir": str(run_dir),
    }
    payload.update(extra)
    return payload


def _final_status(result) -> str:
    """Spec: an export failure must be visible in the final status even
    though it is not one of Runner's own terminal states. `finalize_error`
    (Runner.finish() catching an exception out of `finalize()`) and an
    export_status the sandbox itself reported as failed both mean the
    worker's changes never safely reached the host — either way the run did
    not deliver what its own status claims. However, export_failed should
    only replace 'completed'; other statuses (budget_exceeded, timeout, etc.)
    are the actual cause of the run ending and should be preserved.

    A `watchdog_violation` (Fix item 1: a disk-floor or fail-closed kill
    that fired after the model's last tool call, with no bash call left to
    surface it via BudgetExceeded) is checked before export_status and
    takes precedence over it — the budget breach is the actual cause of the
    run ending, same as a BudgetExceeded raised mid-run already is; an
    export failure downstream of that kill is a secondary symptom. Same
    only-replaces-'completed' rule applies.

    D1: `watchdog_violation_kind` (RunArtifacts.watchdog_violation_kind,
    threaded through by DockerSandbox.finalize()) picks which status the
    violation maps to -- "sandbox_error" for a watchdog-thread sample()
    failure (spec §6's second-failure case), "budget_exceeded" (the
    default kind, "budget") for every other watchdog kill."""
    extra = result.extra or {}
    if extra.get("finalize_error"):
        # Only replace completed with export_failed; keep other statuses as-is
        if result.status == "completed":
            return "export_failed"
        # For non-completed statuses, keep the original status
        return result.status
    if extra.get("watchdog_violation"):
        if result.status == "completed":
            mapped = "sandbox_error" if extra.get("watchdog_violation_kind") == "sandbox_error" else "budget_exceeded"
            return mapped
        return result.status
    export_status = extra.get("export_status", "")
    if isinstance(export_status, str) and export_status.startswith("export_failed"):
        # Only replace completed with export_failed; keep other statuses as-is
        if result.status == "completed":
            return "export_failed"
        # For non-completed statuses, keep the original status
        return result.status
    return result.status


def _fail_setup(e: Exception, *, repo: Path, slug: str, run_dir: Path, transcript,
                 worktree: Path, branch: str, transcript_path: Path, base_commit: str) -> int:
    """A SandboxError/WorkspaceError raised while building the sandbox,
    before runner.run() ever started: nothing has run yet, so this is a
    preflight-shaped failure — roll the worktree back (binding adjustment
    #1) rather than leaving .worktrees/dw-<slug> + its branch orphaned."""
    message = str(e)
    if transcript is not None:
        try:
            transcript.write("run_end", status="sandbox_error", error=message)
        except Exception:
            pass
    remove_worktree(repo, slug)
    _err(message)
    _update_run_json(run_dir, status="sandbox_error")
    print(json.dumps(_emit_result(
        status="sandbox_error", worktree=worktree, branch=branch, transcript_path=transcript_path,
        run_dir=run_dir, turns=None, usage={}, final_message=message, base_commit=base_commit,
    ), indent=2))
    return 1


def _fail_run(e: Exception, *, sandbox, sandbox_started: bool, is_docker: bool, transcript,
              run_dir: Path, worktree: Path, branch: str, transcript_path: Path,
              base_commit: str) -> int:
    """An exception the runner did not itself convert to a terminal status
    (e.g. an LLMError that escapes runner.run()'s own try/except). The
    sandbox has already started here, so — unlike _fail_setup — there may be
    real agent work sitting in the container; recover it before it is lost
    to `finally`'s sandbox.stop()."""
    if isinstance(e, SandboxError):
        fail_status, message = "sandbox_error", str(e)
    elif isinstance(e, LLMError):
        fail_status, message = "model_error", str(e)
    else:
        fail_status, message = "model_error", f"unexpected error: {e!r}"

    export_status = None
    if sandbox_started and is_docker:
        try:
            artifacts = sandbox.finalize()
            export_status = artifacts.export_status
        except Exception:
            sandbox.cfg.keep_volume = True
            message += f" (docker volume kept for recovery: {sandbox.volume})"

    if transcript is not None:
        try:
            transcript.write("run_end", status=fail_status, error=message)
        except Exception:
            pass
    _err(message)

    run_json_fields = {"status": fail_status}
    extra_fields = {"base_commit": base_commit}
    if export_status is not None:
        run_json_fields["export_status"] = export_status
        extra_fields["export_status"] = export_status
    _update_run_json(run_dir, **run_json_fields)

    print(json.dumps(_emit_result(
        status=fail_status, worktree=worktree, branch=branch, transcript_path=transcript_path,
        run_dir=run_dir, turns=None, usage={}, final_message=message, **extra_fields,
    ), indent=2))
    return 1


def _parse_args(argv):
    parser = argparse.ArgumentParser(prog="dirtywork")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="run one task in an isolated worktree")
    run_p.add_argument("task")
    run_p.add_argument("--repo", required=True, type=Path)
    run_p.add_argument("--model", default=DEFAULT_MODEL)
    run_p.add_argument("--branch-from", default=None)
    run_p.add_argument("--max-turns", type=int, default=40)
    run_p.add_argument("--timeout", type=int, default=1800)
    run_p.add_argument("--temperature", type=float, default=None)
    run_p.add_argument("--base-url", default="http://localhost:1234/v1")
    run_p.add_argument("--max-worktree-mb", type=int, default=DEFAULT_MAX_WORKTREE_MB)
    run_p.add_argument("--max-worktree-files", type=int, default=DEFAULT_MAX_WORKTREE_FILES)
    run_p.add_argument("--sandbox", choices=["docker", "none"], default="docker")
    run_p.add_argument("--image", default=DEFAULT_IMAGE)
    run_p.add_argument("--allow-network", action="store_true", default=False)
    run_p.add_argument("--memory", default="4g")
    run_p.add_argument("--cpus", default="2")
    run_p.add_argument("--tmp-size", default="1g")
    run_p.add_argument("--gitdir-size", default="512m")
    run_p.add_argument("--min-free-mb", type=int, default=2048)
    run_p.add_argument("--keep-volume", action="store_true", default=False)
    run_p.add_argument("--max-patch-mb", type=int, default=10)
    return parser.parse_args(argv)


def main(argv: list | None = None) -> int:
    args = _parse_args(argv)
    repo = args.repo.expanduser().resolve()

    # ---- preflight (exit 2, create nothing) ----
    client = LMStudioClient(base_url=args.base_url)
    try:
        preflight_repo(repo)
        models = client.list_models()
    except WorkspaceError as e:
        _err(str(e))
        return 2
    except LLMError as e:
        _err(f"{e}\nIs LM Studio running? Try: lms ps")
        return 2
    if args.model not in models:
        _err(f"model '{args.model}' not loaded (loaded: {', '.join(models) or 'none'}). "
             f"Load it with: lms load {args.model}")
        return 2

    image_ref = None
    image_digest = None
    image_pinned = False
    if args.sandbox == "docker":
        try:
            image_ref, image_digest, image_pinned = _docker_preflight(repo, args.image)
        except DockerError as e:
            # Important #6: don't blame a running daemon for a failure that
            # isn't the daemon's — branch the hint on which preflight step
            # actually raised (see _docker_preflight's .preflight_step tag).
            if getattr(e, "preflight_step", "daemon") == "daemon":
                _err(f"{e}\nDocker is the default sandbox since 0.4. Start Docker Desktop / "
                     f"dockerd, or pass --sandbox none to run unsandboxed on the host.")
            else:
                _err(f"{e}\nBuild or pull the worker image (see docker/README.md) or pass "
                     f"--image <ref> to use a different one, or --sandbox none to run "
                     f"unsandboxed on the host.")
            return 2
        except WorkspaceError as e:
            _err(f"{e}\nCheck that the repository's git object store is valid, or pass "
                 f"--sandbox none to run unsandboxed on the host.")
            return 2

    # ---- workspace ----
    slug = make_slug(args.task, datetime.now())
    branch = f"dirtywork/{slug}"
    if args.sandbox == "docker":
        # Spec §3 name collision: refuse (exit 2) BEFORE creating anything —
        # DockerSandbox.start() re-checks as defense in depth, but by then the
        # worktree and run dir exist, and "exit 2 creates nothing" must hold.
        try:
            DockerSandbox.check_name_collision(docker_cli.run, slug)
        except SandboxError as e:
            _err(str(e))
            return 2
    try:
        ensure_worktrees_excluded(repo)
        worktree = create_worktree(repo, slug, args.branch_from,
                                    no_checkout=(args.sandbox == "docker"))
    except WorkspaceError as e:
        _err(str(e))
        return 2
    base_commit = worktree_base_commit(worktree)

    try:
        runs_dir = ensure_runs_dir(RUNS_DIR)
        run_dir = create_run_dir(runs_dir, slug)
    except RunDirError as e:
        remove_worktree(repo, slug)  # SP1 rule: never orphan the worktree on a preflight-style failure
        _err(str(e))
        return 2
    transcript_path = run_dir / "transcript.jsonl"
    print(f"transcript: {transcript_path}", file=sys.stderr)
    print(f"worktree:   {worktree}", file=sys.stderr)

    _write_run_json_start(run_dir, slug=slug, repo=repo, worktree=worktree, branch=branch,
                           base_commit=base_commit, args=args, image_digest=image_digest,
                           image_pinned=image_pinned)

    # ---- run ----
    # Everything from here on is wrapped in one boundary so the machine
    # contract (exactly one JSON object on stdout, post-preflight) holds
    # even if a component other than runner.run() blows up.
    transcript = None
    sandbox = None
    sandbox_started = False
    try:
        transcript = Transcript(transcript_path)  # constructed BEFORE the sandbox so sandbox_reset events reach it
        sandbox, sandbox_info = _build_sandbox(
            args, run_dir=run_dir, worktree=worktree, repo=repo, slug=slug,
            base_commit=base_commit, transcript=transcript, image_ref=image_ref,
            image_digest=image_digest, image_pinned=image_pinned,
        )
        sandbox_started = True

        executor = ToolExecutor(sandbox, transcript=transcript)

        def finalize():
            artifacts = sandbox.finalize()
            return {
                "diff_stat": artifacts.diff_stat,
                "untracked": artifacts.untracked,  # host mode: git status ?? entries; docker mode: "" (git add -A folds new files into diff_stat)
                "patch_path": artifacts.patch_path,
                "worktree_bytes": artifacts.worktree_bytes,
                "worktree_files": artifacts.worktree_files,
                "escaping_symlinks": artifacts.escaping_symlinks,
                "dropped_git_entries": artifacts.dropped_git_entries,
                "export_status": artifacts.export_status,
                "watchdog_violation": artifacts.watchdog_violation,
                "watchdog_violation_kind": artifacts.watchdog_violation_kind,
            }

        runner = Runner(
            client, executor, transcript, model=args.model,
            max_turns=args.max_turns, timeout=args.timeout, temperature=args.temperature,
            run_info={
                "repo": str(repo), "worktree": str(worktree), "branch": branch,
                "branch_from": args.branch_from, "base_commit": base_commit,
                "base_url": args.base_url, "dirtywork_version": __version__,
                "temperature": args.temperature, "sandbox": sandbox_info, "provider": "openai",
            },
            finalize=finalize,
        )
        display_root = DOCKER_WORKDIR if args.sandbox == "docker" else str(worktree)
        system_prompt = build_system_prompt(display_root, load_repo_context(repo, base_commit))
        result = runner.run(system_prompt, args.task)
    except Exception as e:
        if not sandbox_started and isinstance(e, (SandboxError, WorkspaceError)):
            return _fail_setup(e, repo=repo, slug=slug, run_dir=run_dir, transcript=transcript,
                                worktree=worktree, branch=branch, transcript_path=transcript_path,
                                base_commit=base_commit)
        return _fail_run(e, sandbox=sandbox, sandbox_started=sandbox_started,
                          is_docker=(args.sandbox == "docker"), transcript=transcript,
                          run_dir=run_dir, worktree=worktree, branch=branch,
                          transcript_path=transcript_path, base_commit=base_commit)
    finally:
        if sandbox is not None:
            try:
                sandbox.stop()
            except Exception:
                pass
        if transcript is not None:
            try:
                transcript.close()
            except Exception:
                pass

    extra = result.extra or {}
    final_status = _final_status(result)
    finalize_error = extra.get("finalize_error")

    _update_run_json(
        run_dir,
        status=final_status,
        diff_stat=extra.get("diff_stat"),
        export_status=extra.get("export_status", "n/a"),
        patch_path=extra.get("patch_path"),
        finalize_error=finalize_error,
        watchdog_violation=extra.get("watchdog_violation"),
        watchdog_violation_kind=extra.get("watchdog_violation_kind"),
    )

    print(json.dumps(_emit_result(
        status=final_status, worktree=worktree, branch=branch, transcript_path=transcript_path,
        run_dir=run_dir, turns=result.turns, usage=result.usage, final_message=result.final_message,
        base_commit=base_commit, finalize_error=finalize_error,
        watchdog_violation=extra.get("watchdog_violation"),
        watchdog_violation_kind=extra.get("watchdog_violation_kind"),
    ), indent=2))
    return 0 if final_status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
