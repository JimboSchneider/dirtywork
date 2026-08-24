# dirtywork/__main__.py
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
import re
import sys

if __name__ == "__main__":
    # `python -m dirtywork` executes this file as the module named "__main__";
    # `dirtywork.__main__` is NOT registered, so a later `import dirtywork.__main__`
    # (bench.run_once, lazily) would execute the file a SECOND time as another
    # module object. Alias the running module under its package name so that
    # import returns this one. The console-script entry point imports
    # dirtywork.__main__ normally and never hits this branch.
    sys.modules.setdefault("dirtywork.__main__", sys.modules[__name__])
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .budget import DEFAULT_MAX_WORKTREE_FILES, DEFAULT_MAX_WORKTREE_MB
from .llm import LLMError
from .providers import DEFAULT_BASE_URLS, PROVIDER_NAMES, get_provider
from .rundir import (RUNS_DIR, RunDirError, create_run_dir, ensure_runs_dir, read_run_json,
                     run_dir_for, write_run_json)
from .runner import (
    CHARS_PER_TOKEN,
    DEFAULT_MAX_TOKENS,
    DEFAULT_STALL_TURNS,
    DEFAULT_STUCK_REPEATS,
    DEFAULT_VERIFY_ROUNDS,
    DEFAULT_VERIFY_TIMEOUT,
    Runner,
    resolve_context_window,
)
from .sandbox import SandboxError, docker_args, docker_cli
from .sandbox.docker import DockerSandbox
from .sandbox.docker_args import DEFAULT_IMAGE, DockerConfig
from .sandbox.docker_cli import DockerError, docker_version, resolve_image, validate_objects_dir
from .sandbox.host import HostSandbox
from .builtin_tools import default_registry
from .transcript import Transcript
from .resume import (MAX_FEEDBACK_CHARS, ResumeError, build_resume_task, check_resumable,
                     load_prior_run, preflight_run_worktree, render_transcript_tail,
                     resolve_run_dir)
from .workspace import (
    WorkspaceError,
    commit_exists,
    create_worktree,
    ensure_worktrees_excluded,
    host_worktree_dirty,
    load_repo_context,
    make_slug,
    preflight_repo,
    remove_worktree,
    snapshot_worktree,
    worktree_base_commit,
)

DEFAULT_MODEL = "qwen/qwen3-coder-next"
DOCKER_WORKDIR = "/work"
# Spec §2.1: a brief past this fraction of the window earns one stderr line.
# 20% is where SP3's 1,084-line brief started thrashing the prompt cache on a
# 65k window: every turn re-sent a task that large, the per-turn trim
# invalidated the cache, and two runs died `context_exhausted`.
TASK_WARN_FRACTION = 0.20


def build_system_prompt(display_root, repo_context: str | None, *, allow_commit: bool = False) -> str:
    """`display_root` is what the model is told its files live at: the host
    worktree path in host mode, or the fixed in-container mount point
    (DOCKER_WORKDIR) in docker mode -- the model never sees a host path it
    cannot `cd` to (docker.py's `_rel()` rejects absolute paths outside the
    container's own tree).

    `allow_commit` (host mode only, enforced in `_resolve_allow_commit`) swaps
    the leave-it-uncommitted rule for a commit-as-you-go rule. Nothing else
    changes: no guardrail ever blocked `git commit`, and `git push` stays
    blocked by the denylist in both modes."""
    if allow_commit:
        commit_rule = ("- Commit your work in small conventional commits as you go (git add + git commit); "
                       "stay on the current branch -- do not create branches and never run git push.")
    else:
        commit_rule = "- Do not run git commit or git branch commands; leave all changes uncommitted for review."
    prompt = f"""You are a coding agent. Your files live at {display_root} -- treat it as your working directory for every tool call.
Complete the task, then reply with a plain-text summary of what you changed and what commands you ran.

Rules:
- Use edit_file, apply_edits (several exact replacements in one file at once), insert_before, insert_after, write_file or append_file for ALL file changes. Never modify files via bash (no sed -i, no echo redirects, no heredocs).
- A file too large for one reply: write_file the first part, then append_file each following part. append_file adds your text to the END of an existing file with nothing inserted between, so include a leading newline when the file does not end with one.
- Paths are relative to {display_root}.
- Explore before editing: use list_dir, grep, and read_file to understand the code first.
- Verify your work: run the repo's tests or build via bash before declaring the task complete.
{commit_rule}
- When the task is complete, call finish(summary=...) with a short summary of what you did and anything left undone. A plain reply with no tool calls also ends the run."""
    if repo_context:
        prompt += f"\n\nRepository conventions (from the repo's own docs):\n\n{repo_context}"
    return prompt


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def _warn_task_size(ctx) -> None:
    """Spec §2.1: one advisory stderr line when the brief itself eats too much
    of the window. Called once, after the RunContext exists, so it covers both
    `run` (args.task) and `resume` (the marker block build_resume_task made,
    which is what actually gets sent). Nothing is recorded anywhere: this is
    advice, and a run that ignores it is not a different kind of run."""
    task_tokens = math.ceil(len(ctx.task) / CHARS_PER_TOKEN)
    if task_tokens <= TASK_WARN_FRACTION * ctx.context_window:
        return
    pct = round(100 * task_tokens / ctx.context_window)
    print(f"warning: the task text is ~{task_tokens} tokens, {pct}% of the "
          f"{ctx.context_window}-token context window; long briefs thrash the prompt "
          f"cache and risk context_exhausted — split the task or load the model with "
          f"a larger context (docs/operating.md#sizing-the-context-window)",
          file=sys.stderr)


class PreflightFailure(Exception):
    """A preflight-stage refusal: main() prints the message and exits 2
    having created nothing."""


@dataclass
class RunContext:
    repo: Path
    slug: str
    branch: str
    worktree: Path
    base_commit: str
    task: str
    sandbox_mode: str
    provider: str
    image_ref: str | None
    image_digest: str | None
    image_pinned: bool
    context_window: int
    # Spec §3.4: which precedence step produced `context_window` --
    # flag|env|provider:<name>:server|provider:<name>|default. REQUIRED (no
    # default), so it must stay above the first defaulted field below; a run
    # record that reports 32768 without saying whether anybody chose it is not
    # a record.
    context_window_source: str
    branch_from: str | None = None
    branch_from_run: str | None = None   # the @<slug> --branch-from named, if any
    feedback: str | None = None          # resume only: the reviewer's instructions
    resumed_from: str | None = None
    prior_run_dir: Path | None = None
    seed_from_worktree: bool = False
    owns_worktree: bool = True   # False on resume: a setup failure must not remove prior work


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def _non_negative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return value


# Docker 29's own `--tmpfs .../size=VALUE` parser is looser than what we want
# to hand it: a leading zero is parsed as octal ("00256m" -> 174MiB, not
# 256MiB, per manual probe), a bare comma after the value silently splices in
# extra tmpfs mount options ("1g,exec" grants exec on the mount), and a
# unit-less value is accepted as page-rounded bytes. We accept only
# digits-then-unit -- no leading zero, no unit-less bytes, no comma, no
# percent, and no 't' (a terabyte cap is meaningless against --memory).
_TMPFS_SIZE_RE = re.compile(r"^[1-9][0-9]*[kmg]$")


def _tmpfs_size(value: str) -> str:
    canonical = value.lower()
    if not _TMPFS_SIZE_RE.fullmatch(canonical):  # fullmatch: `$` alone admits a trailing newline
        raise argparse.ArgumentTypeError(
            f"expected a size like 256m or 1g (digits followed by k, m or g), got {value!r}")
    return canonical


_ENDPOINT_HINTS = {
    "openai": "Is the OpenAI-compatible server running? Try: lms ps",
    "anthropic": "Check ANTHROPIC_API_KEY and that api.anthropic.com is reachable.",
    "ollama": "Is Ollama running? Try: ollama ps",
}

# Spec §3.1: what to tell the operator when the model is not there. A dict
# keyed by provider, replacing the two-branch ternary, so adding a provider is
# an entry rather than another branch. `{model}` is substituted with str.replace
# (never str.format): a model id is operator input and may contain braces.
_MODEL_HINTS = {
    "openai": "Load it with: lms load {model}",
    "ollama": ("Pull or run it first: ollama run {model} — Ollama model ids include "
               "the tag, e.g. 'gemma4:latest'"),
}
_DEFAULT_MODEL_HINT = "Pick one of the models listed above with --model."
# Ollama's /v1/models lists PULLED models, not resident ones, so "not loaded"
# would be the wrong word there.
_MODEL_ABSENT_WORD = {"ollama": "not available"}


def _preflight_llm(args):
    """Resolve --base-url against the chosen provider's default (recorded on
    args so run_start/run.json report the endpoint actually used), then prove
    the endpoint is reachable and the model is available."""
    if args.base_url is None:
        args.base_url = DEFAULT_BASE_URLS[args.provider]
    provider = get_provider(args.provider, args.base_url)
    try:
        models = provider.list_models()
    except LLMError as e:
        raise PreflightFailure(f"{e}\n{_ENDPOINT_HINTS.get(args.provider, '')}")
    if args.model not in models:
        hint = _MODEL_HINTS.get(args.provider, _DEFAULT_MODEL_HINT).replace(
            "{model}", args.model)
        absent = _MODEL_ABSENT_WORD.get(args.provider, "not loaded")
        raise PreflightFailure(
            f"model '{args.model}' {absent} (loaded: {', '.join(models) or 'none'}). {hint}")
    return provider


def _resolve_context_window(args, provider=None) -> tuple:
    """(tokens, source). The source was discarded before 0.9; it is now recorded
    on the run. The "assuming …" warning still fires only for "default" --
    "provider:openai:server" and "provider:openai" are both known values."""
    try:
        window, source = resolve_context_window(
            args.model, args.context_window, os.environ.get("DIRTYWORK_CONTEXT_WINDOW"),
            provider)
    except ValueError as e:
        raise PreflightFailure(str(e))
    if source == "default":
        print(f"warning: no known context window for '{args.model}'; assuming {window} tokens "
              f"(set --context-window or DIRTYWORK_CONTEXT_WINDOW)", file=sys.stderr)
    # Spec §1.4: prompt and reply share the window, so a cap at or above it
    # leaves no room for a prompt at all. Refused here, after the warning, so
    # an operator with an unknown model still sees which window was assumed.
    # Read with getattr: this defends a directly-built Namespace (e.g. in a
    # test that skips argparse), which may lack the attribute entirely. A
    # real CLI invocation always has it -- `_add_run_flags` sets a default for
    # both `run` and `resume` -- and `runs`/`bench` never reach this function
    # at all: `main()` dispatches and returns for both before this call.
    max_tokens = getattr(args, "max_tokens", None)
    if max_tokens is None:
        max_tokens = DEFAULT_MAX_TOKENS
    if max_tokens >= window:
        raise PreflightFailure(
            f"--max-tokens {max_tokens} must be smaller than the {window}-token "
            f"context window")
    return window, source


def _resolve_allow_commit(args) -> None:
    """Normalize `--allow-commit` to a real bool on args and refuse the
    combination that cannot work. The docker export builds a tree with
    `git add -A` and streams `git archive` through a validator that refuses
    every `.git` member (sandbox/export.py) -- a container's commits can never
    reach the host, so honouring the flag there would change the prompt and
    then silently discard the history it produced."""
    if args.allow_commit and args.sandbox == "docker":
        raise PreflightFailure(
            "--allow-commit requires --sandbox none (host mode): docker export "
            "carries files, not commits")
    args.allow_commit = bool(args.allow_commit)


def _resolve_branch_from(args) -> tuple:
    """Spec §6.2: `--branch-from @<slug>` means 'the branch that run left
    behind'. Returns (branch_from, branch_from_run). Anything not starting with
    '@' passes through untouched, so an ordinary ref is unaffected.

    A dirty worktree is snapshotted FIRST, because the branch head alone does
    not carry the work the reviewer just read — that snapshot is the whole
    point of the flag, and it is the one thing this preflight creates before a
    later failure could still exit 2. Before that: if the run's worktree still
    exists, it must clear the same preflight resume uses (not still running,
    not a foreign worktree, no leftover stash) -- a run currently in progress
    or in an unsafe state must never be touched. If the worktree is gone
    (e.g. `runs clean` already removed it), branching from the recorded
    branch head is a legitimate use of `@<slug>` and nothing is snapshotted."""
    value = getattr(args, "branch_from", None)
    if not isinstance(value, str) or not value.startswith("@"):
        return value, None
    slug = value[1:]
    # Spec §4.1: the SAME rule `runs snapshot` uses. resolve_run_dir treats
    # anything with a separator as a path, which is right for
    # `dirtywork resume <run-dir>` and wrong for an `@<slug>` reference.
    try:
        run_dir = run_dir_for(slug, RUNS_DIR)
    except RunDirError as e:
        raise PreflightFailure(str(e))
    if not run_dir.is_dir():
        raise PreflightFailure(f"unknown run '{slug}' (no run dir under {RUNS_DIR})")
    try:
        prior = load_prior_run(run_dir)
    except ResumeError as e:
        raise PreflightFailure(str(e))
    branch = prior.get("branch")
    if not isinstance(branch, str) or not branch:
        raise PreflightFailure(f"run '{slug}' records no branch to branch from")
    worktree = Path(prior.get("worktree") or "")
    if str(worktree) and worktree.is_dir():
        try:
            preflight_run_worktree(prior, action="snapshot")
        except ResumeError as e:
            raise PreflightFailure(str(e))
        if host_worktree_dirty(worktree):
            try:
                sha = snapshot_worktree(worktree, branch, f"wip: dirtywork run {slug}")
            except WorkspaceError as e:
                raise PreflightFailure(str(e))
            if sha:
                print(f"snapshot {sha} on {branch} (from @{slug})", file=sys.stderr)
    return branch, slug


def _workspace_new(args, repo: Path, context_window: int,
                   context_window_source: str) -> RunContext:
    # First: it is the cheapest refusal in this function, and its snapshot must
    # happen before the run creates a worktree it might have to roll back.
    branch_from, branch_from_run = _resolve_branch_from(args)
    image_ref, image_digest, image_pinned = None, None, False
    if args.sandbox == "docker":
        image_ref, image_digest, image_pinned = _docker_preflight_or_fail(repo, args.image)
    slug = make_slug(args.task, datetime.now())
    branch = f"dirtywork/{slug}"
    if args.sandbox == "docker":
        # Spec §3 name collision: refuse (exit 2) BEFORE creating anything —
        # DockerSandbox.start() re-checks as defense in depth, but by then the
        # worktree and run dir exist, and "exit 2 creates nothing" must hold.
        try:
            DockerSandbox.check_name_collision(docker_cli.run, slug)
        except SandboxError as e:
            raise PreflightFailure(str(e))
    try:
        ensure_worktrees_excluded(repo)
        worktree = create_worktree(repo, slug, branch_from,
                                    no_checkout=(args.sandbox == "docker"))
    except WorkspaceError as e:
        raise PreflightFailure(str(e))
    return RunContext(
        repo=repo, slug=slug, branch=branch, worktree=worktree,
        base_commit=worktree_base_commit(worktree), task=args.task,
        sandbox_mode=args.sandbox, provider=args.provider, image_ref=image_ref, image_digest=image_digest,
        image_pinned=image_pinned, context_window=context_window,
        context_window_source=context_window_source, branch_from=branch_from,
        branch_from_run=branch_from_run,
    )


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
    pinned_digest = docker_args.pin_for(image)
    try:
        image_ref = resolve_image(image, pinned_digest=pinned_digest)
        image_digest = docker_cli.image_repo_digest(image, run=docker_cli.run)
    except DockerError as e:
        e.preflight_step = "image"
        raise
    validate_objects_dir(repo)
    image_pinned = pinned_digest is not None and image_digest is not None
    return image_ref, image_digest, image_pinned


def _docker_preflight_or_fail(repo: Path, image: str):
    """_docker_preflight with main()'s exit-2 hint texts attached (Important #6)."""
    try:
        return _docker_preflight(repo, image)
    except DockerError as e:
        if getattr(e, "preflight_step", "daemon") == "daemon":
            raise PreflightFailure(
                f"{e}\nDocker is the default sandbox since 0.4. Start Docker Desktop / "
                f"dockerd, or pass --sandbox none to run unsandboxed on the host.")
        raise PreflightFailure(
            f"{e}\nBuild or pull the worker image (see docker/README.md) or pass "
            f"--image <ref> to use a different one, or --sandbox none to run "
            f"unsandboxed on the host.")
    except WorkspaceError as e:
        raise PreflightFailure(
            f"{e}\nCheck that the repository's git object store is valid, or pass "
            f"--sandbox none to run unsandboxed on the host.")


def _build_sandbox(args, ctx: RunContext, *, run_dir: Path, transcript):
    """Construct and start the backend named by --sandbox, returning
    (sandbox, sandbox_info) for Runner's run_info. A docker-mode failure
    during start() is cleaned up here (best-effort stop(), so a half-created
    container/volume is not left running) before the exception propagates —
    callers only need to decide what to report, not what to tear down."""
    if ctx.sandbox_mode == "docker":
        cfg = DockerConfig(
            image=args.image,
            network="bridge" if args.allow_network else "none",
            memory=args.memory,
            cpus=args.cpus,
            tmp_size=args.tmp_size,
            gitdir_size=args.gitdir_size,
            home_size=args.home_size,
            max_worktree_mb=args.max_worktree_mb,
            max_worktree_files=args.max_worktree_files,
            min_free_mb=args.min_free_mb,
            max_patch_mb=args.max_patch_mb,
            keep_volume=args.keep_volume,
        )
        sandbox = DockerSandbox(cfg, run_dir=run_dir, transcript=transcript, image_ref=ctx.image_ref)
        try:
            sandbox.start(ctx.worktree, ctx.repo, ctx.slug, ctx.base_commit, branch=ctx.branch, seed_from_worktree=ctx.seed_from_worktree)
        except Exception:
            try:
                sandbox.stop()
            except Exception:
                pass
            raise
        sandbox.watchdog.start()  # only place a real Watchdog thread is started
        sandbox_info = {
            "backend": "docker", "image": args.image, "image_digest": ctx.image_digest,
            "image_pinned": ctx.image_pinned,
            "network": cfg.network, "memory": cfg.memory, "cpus": cfg.cpus,
            "pids_limit": cfg.pids_limit, "tmp_size": cfg.tmp_size,
            "gitdir_size": cfg.gitdir_size, "home_size": cfg.home_size,
            "max_worktree_mb": cfg.max_worktree_mb,
            "max_worktree_files": cfg.max_worktree_files,
            "user": f"{sandbox.uid}:{sandbox.gid}",
        }
    else:
        sandbox = HostSandbox(ctx.worktree, max_worktree_mb=args.max_worktree_mb,
                               max_worktree_files=args.max_worktree_files)
        sandbox.start(ctx.worktree, ctx.repo, ctx.slug, ctx.base_commit, branch=ctx.branch, seed_from_worktree=ctx.seed_from_worktree)
        sandbox_info = "none"
    return sandbox, sandbox_info


def _write_run_json_start(run_dir: Path, ctx: RunContext, args) -> None:
    is_docker = ctx.sandbox_mode == "docker"
    write_run_json(run_dir, {
        "schema_version": 2,
        "status": "running",
        "slug": ctx.slug,
        "repo": str(ctx.repo),
        "worktree": str(ctx.worktree),
        "branch": ctx.branch,
        "base_commit": ctx.base_commit,
        "task": ctx.task,
        "model": args.model,
        "provider": args.provider,
        "context_window": ctx.context_window,
        "context_window_source": ctx.context_window_source,
        "max_tokens": getattr(args, "max_tokens", DEFAULT_MAX_TOKENS),
        "branch_from_run": ctx.branch_from_run,
        "feedback": ctx.feedback,
        "resumed_from": ctx.resumed_from,
        "verify_command": args.verify,
        "verify_rounds": args.verify_rounds,
        "verify_timeout": args.verify_timeout,
        "container": docker_args.container_name(ctx.slug) if is_docker else None,
        "volume": docker_args.volume_name(ctx.slug) if is_docker else None,
        "tmp_size": args.tmp_size if is_docker else None,
        "gitdir_size": args.gitdir_size if is_docker else None,
        "home_size": args.home_size if is_docker else None,
        "image": args.image if is_docker else None,
        "image_digest": ctx.image_digest,
        "image_pinned": ctx.image_pinned,
        "host_pid": os.getpid(),
        "started": datetime.now(timezone.utc).isoformat(),
        "sandbox": ctx.sandbox_mode,
        "allow_commit": bool(args.allow_commit),
    })


def _update_run_json(run_dir: Path, *, mark_ended: bool = True, **fields) -> None:
    """Best-effort merge-update of run.json (step 11); never raises — a
    failure here must not break the stdout JSON contract of exactly one
    object per run."""
    try:
        existing = read_run_json(run_dir)
        updates = dict(fields)
        if mark_ended:
            updates["ended"] = datetime.now(timezone.utc).isoformat()
        existing.update(updates)
        write_run_json(run_dir, existing)
    except Exception:
        pass


def _emit_result(*, status: str, worktree: Path, branch: str, transcript_path: Path,
                  run_dir: Path, turns, usage: dict, final_message: str, provider: str, **extra) -> dict:
    """The one place that shapes the stdout JSON contract — both the success
    path and every failure path funnel through here so the field set can
    never drift between them. Fix item 3: the six 0.8 evidence keys
    (`stuck_on`, `files_changed`, `files_changed_truncated`,
    `last_tool_result`, `last_assistant_text`, `verify`) are seeded with
    their null/empty defaults BEFORE `extra` is applied, so every payload —
    including `_fail_setup`'s and `_fail_run`'s, where `runner.run()` never
    returned and so never had real values for them — carries the full key
    set; the normal end-of-run path's real values (passed via `extra`) still
    override these defaults.

    0.9 seeds three more the same way (spec §6): `trimmed_turns` and `timeouts`
    (ints, default 0) and `context_window_source` (string). Both failure paths
    pass real values for all three through `_contract_fields`, so the seeds are
    only a backstop against a future caller that forgets."""
    payload = {
        "schema_version": 2,
        "status": status,
        "worktree": str(worktree),
        "branch": branch,
        "transcript": str(transcript_path),
        "turns": turns,
        "usage": usage,
        "final_message": final_message,
        "provider": provider,
        "run_dir": str(run_dir),
        "stuck_on": None,
        "files_changed": [],
        "files_changed_truncated": False,
        "last_tool_result": None,
        "last_assistant_text": None,
        "verify": None,
        "trimmed_turns": 0,
        "timeouts": 0,
        "context_window_source": None,
    }
    payload.update(extra)
    return payload


def _contract_fields(extra: dict, ctx: RunContext) -> dict:
    """The 0.9 contract fields that ride on EVERY payload, `run_end` event and
    `run.json` write (spec §6). One dict feeds the stdout payload and the
    `_update_run_json` call on every path, so those two can never disagree.

    The two failure paths call it with `extra={}`: `runner.run()` never
    returned there, so the documented defaults are exactly what `.get` yields.
    `context_window_source` comes from the RunContext instead, because it is
    resolved in preflight and is therefore known on every path a payload can
    exist on -- a context-window preflight failure exits 2 with no payload at
    all, as it did before 0.9."""
    return {"trimmed_turns": extra.get("trimmed_turns", 0),
            "timeouts": extra.get("timeouts", 0),
            "context_window_source": ctx.context_window_source}


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


def _fail_setup(e: Exception, ctx: RunContext, *, run_dir, transcript, transcript_path) -> int:
    """A SandboxError/WorkspaceError raised while building the sandbox,
    before runner.run() ever started: nothing has run yet, so this is a
    preflight-shaped failure — roll the worktree back (binding adjustment
    #1) rather than leaving .worktrees/dw-<slug> + its branch orphaned."""
    message = str(e)
    contract = _contract_fields({}, ctx)
    if transcript is not None:
        try:
            transcript.write("run_end", status="sandbox_error", error=message, **contract)
        except Exception:
            pass
    if ctx.owns_worktree:
        remove_worktree(ctx.repo, ctx.slug)
    _err(message)
    _update_run_json(run_dir, status="sandbox_error", **contract)
    print(json.dumps(_emit_result(
        status="sandbox_error", worktree=ctx.worktree, branch=ctx.branch, transcript_path=transcript_path,
        run_dir=run_dir, turns=None, usage={}, final_message=message, base_commit=ctx.base_commit,
        resumed_from=ctx.resumed_from, provider=ctx.provider, **contract,
    ), indent=2))
    return 1


def _fail_run(e: Exception, ctx: RunContext, *, sandbox, sandbox_started: bool, transcript,
              run_dir: Path, transcript_path: Path) -> int:
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
    if sandbox_started and ctx.sandbox_mode == "docker":
        try:
            artifacts = sandbox.finalize()
            export_status = artifacts.export_status
        except Exception:
            sandbox.cfg.keep_volume = True
            message += f" (docker volume kept for recovery: {sandbox.volume})"

    contract = _contract_fields({}, ctx)
    if transcript is not None:
        try:
            transcript.write("run_end", status=fail_status, error=message, **contract)
        except Exception:
            pass
    _err(message)

    run_json_fields = dict(contract, status=fail_status)
    extra_fields = dict(contract, base_commit=ctx.base_commit,
                        resumed_from=ctx.resumed_from)
    if export_status is not None:
        run_json_fields["export_status"] = export_status
        extra_fields["export_status"] = export_status
    _update_run_json(run_dir, **run_json_fields)

    print(json.dumps(_emit_result(
        status=fail_status, worktree=ctx.worktree, branch=ctx.branch, transcript_path=transcript_path,
        run_dir=run_dir, turns=None, usage={}, final_message=message, provider=ctx.provider,
        **extra_fields,
    ), indent=2))
    return 1


def _load_feedback(args):
    """Spec §6.3: --feedback / --feedback-file, mutually exclusive, UTF-8,
    capped. Returns the text or None. Raises PreflightFailure (exit 2)."""
    text = getattr(args, "feedback", None)
    path = getattr(args, "feedback_file", None)
    if text is not None and path is not None:
        raise PreflightFailure("--feedback and --feedback-file are mutually exclusive")
    if path is not None:
        try:
            text = Path(path).expanduser().read_text(encoding="utf-8")
        except (OSError, ValueError) as e:
            raise PreflightFailure(f"cannot read feedback file '{path}': {e}")
    if text is None:
        return None
    if not text.strip():
        # Spec §6: an empty or whitespace-only --feedback is ABSENT, not
        # feedback. Normalized HERE, at parse, so the completed-run gate, the
        # resume prompt and run.json's `feedback` field can never disagree --
        # which is what makes docs/transcript-schema.md's "null means a resume
        # without feedback" a true statement rather than an aspiration.
        return None
    if len(text) > MAX_FEEDBACK_CHARS:
        raise PreflightFailure(
            f"feedback is {len(text)} chars, over the {MAX_FEEDBACK_CHARS}-char limit")
    return text


def _load_resume_target(args) -> dict:
    """Spec §5 lookup + refusals; also applies the prior run's defaults to
    args (sandbox mode always; model/image unless given on the command line)."""
    run_dir = resolve_run_dir(args.run, RUNS_DIR)
    try:
        prior = load_prior_run(run_dir)
        check_resumable(prior)
    except ResumeError as e:
        raise PreflightFailure(str(e))
    prior["run_dir"] = str(run_dir)
    args.sandbox = prior["sandbox"]
    prior_provider = prior.get("provider") or "openai"
    if args.provider is None:
        args.provider = prior_provider
    elif args.provider != prior_provider:
        # Same rule as --sandbox (which resume does not expose at all): the
        # prior run's history was shaped by that provider's wire format.
        # Deliberately NO openai<->ollama carve-out (spec §3.1), even though
        # the wire format is identical: the inherited --model would carry the
        # wrong id (Ollama's carry a tag) and base_url is not recorded on the
        # run, so the run is not portable between the two. Anyone who reached
        # Ollama before 0.10 with `--provider openai --base-url …:11434/v1`
        # keeps resuming exactly that way.
        raise PreflightFailure(
            f"run {prior['slug']} used provider '{prior_provider}'; resume it with that "
            f"provider (drop --provider {args.provider}) or start a new run")
    if args.model is None:
        args.model = prior["model"]
    if args.image is None:
        args.image = prior.get("image") or DEFAULT_IMAGE
    if args.allow_commit is None:
        args.allow_commit = bool(prior.get("allow_commit", False))
    if getattr(args, "verify", None) is None:
        # run.json records verify_command/verify_rounds/verify_timeout at run
        # START (from the args given, null/defaults otherwise) so a run that
        # never reached the verify step (max_turns/stalled/stuck/timeout/
        # budget_exceeded — the statuses people actually resume) still hands
        # its gate on. Fall back to the verify RESULT object's command for
        # run.json files written before this field existed.
        verify_command = prior.get("verify_command")
        if verify_command is None:
            prior_verify = prior.get("verify")
            if isinstance(prior_verify, dict):
                verify_command = prior_verify.get("command")
        if verify_command:
            args.verify = verify_command
    if getattr(args, "verify_rounds", None) is None:
        # Spec §6: `.get(k) if … is not None else default`, never
        # `.get(k, default)` -- a hand-edited run.json carrying an explicit
        # `null` would otherwise leave args.verify_rounds None.
        args.verify_rounds = (prior.get("verify_rounds")
                              if prior.get("verify_rounds") is not None
                              else DEFAULT_VERIFY_ROUNDS)
    if getattr(args, "verify_timeout", None) is None:
        args.verify_timeout = (prior.get("verify_timeout")
                               if prior.get("verify_timeout") is not None
                               else DEFAULT_VERIFY_TIMEOUT)
    if getattr(args, "max_tokens", None) is None:
        # Spec §1.4/§6, hardened in fix round 1: a hand-edited run.json can
        # carry anything JSON allows for this key -- a string, a float, a
        # bool, zero, a negative number -- not just a missing key or an
        # explicit null. Only a real positive int (bool excluded, since
        # `isinstance(True, int)` is True) is inherited; everything else
        # falls back to the default rather than tracebacking in preflight's
        # `>=` comparison or bypassing `_positive_int` into a cap-blind or
        # inflated budget.
        inherited = prior.get("max_tokens")
        if (isinstance(inherited, int) and not isinstance(inherited, bool)
                and inherited > 0):
            args.max_tokens = inherited
        else:
            args.max_tokens = DEFAULT_MAX_TOKENS
    # Last, so the earlier refusals (still running, missing worktree, provider
    # switch) keep their own messages when they apply too.
    args.feedback_text = _load_feedback(args)
    if prior.get("status") == "completed" and not args.feedback_text:
        raise PreflightFailure(
            f"run '{prior['slug']}' ended 'completed'; pass --feedback to continue it "
            f"with new instructions")
    return prior


def _workspace_resume(args, prior: dict, context_window: int,
                      context_window_source: str) -> RunContext:
    repo = Path(prior["repo"]).expanduser().resolve()
    if not commit_exists(repo, prior["base_commit"]):
        raise PreflightFailure(f"base commit {prior['base_commit']} no longer exists in {repo}")
    image_ref, image_digest, image_pinned = None, None, False
    if args.sandbox == "docker":
        image_ref, image_digest, image_pinned = _docker_preflight_or_fail(repo, args.image)
    slug = make_slug(prior["task"], datetime.now())
    if args.sandbox == "docker":
        try:
            DockerSandbox.check_name_collision(docker_cli.run, slug)
        except SandboxError as e:
            raise PreflightFailure(str(e))
    tail = render_transcript_tail(Path(prior["run_dir"]) / "transcript.jsonl")
    feedback = getattr(args, "feedback_text", None)
    task = build_resume_task(prior["task"], prior["status"], prior.get("turns"), tail,
                             feedback)
    return RunContext(
        repo=repo, slug=slug, branch=prior["branch"], worktree=Path(prior["worktree"]),
        base_commit=prior["base_commit"], task=task, sandbox_mode=args.sandbox,
        provider=args.provider, image_ref=image_ref, image_digest=image_digest, image_pinned=image_pinned,
        context_window=context_window, context_window_source=context_window_source,
        resumed_from=prior["slug"], feedback=feedback,
        prior_run_dir=Path(prior["run_dir"]), seed_from_worktree=(args.sandbox == "docker"),
        owns_worktree=False,
    )


def _execute(ctx: RunContext, args, client) -> int:
    try:
        runs_dir = ensure_runs_dir(RUNS_DIR)
        run_dir = create_run_dir(runs_dir, ctx.slug)
    except RunDirError as e:
        # SP1 rule: never orphan the worktree on a preflight-style failure
        if ctx.owns_worktree:
            remove_worktree(ctx.repo, ctx.slug)
        _err(str(e))
        return 2
    transcript_path = run_dir / "transcript.jsonl"
    print(f"transcript: {transcript_path}", file=sys.stderr)
    print(f"worktree:   {ctx.worktree}", file=sys.stderr)

    _write_run_json_start(run_dir, ctx, args)
    if ctx.prior_run_dir is not None:
        _update_run_json(ctx.prior_run_dir, mark_ended=False, resumed_by=ctx.slug)

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
            args, ctx=ctx, run_dir=run_dir, transcript=transcript
        )
        sandbox_started = True

        registry = default_registry(transcript=transcript)

        def finalize():
            artifacts = sandbox.finalize()
            return {
                "diff_stat": artifacts.diff_stat,
                "untracked": artifacts.untracked,  # host mode: git status ?? entries; docker mode: "" (git add -A folds new files into diff_stat)
                "files_changed": artifacts.files_changed,
                "files_changed_truncated": artifacts.files_changed_truncated,
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
            client, registry, sandbox, transcript, model=args.model,
            max_turns=args.max_turns, timeout=args.timeout,
            max_tokens=getattr(args, "max_tokens", DEFAULT_MAX_TOKENS),
            temperature=args.temperature,
            run_info={
                "repo": str(ctx.repo), "worktree": str(ctx.worktree), "branch": ctx.branch,
                "branch_from": ctx.branch_from, "base_commit": ctx.base_commit,
                "base_url": args.base_url, "dirtywork_version": __version__,
                "temperature": args.temperature, "sandbox": sandbox_info, "provider": ctx.provider,
                "resumed_from": ctx.resumed_from, "feedback": ctx.feedback,
            },
            finalize=finalize,
            stall_turns=args.stall_turns, context_window=ctx.context_window,
            context_window_source=ctx.context_window_source,
            stuck_repeats=getattr(args, "stuck_repeats", DEFAULT_STUCK_REPEATS),
            verify=getattr(args, "verify", None),
            verify_rounds=getattr(args, "verify_rounds", DEFAULT_VERIFY_ROUNDS),
            verify_timeout=getattr(args, "verify_timeout", DEFAULT_VERIFY_TIMEOUT),
        )
        display_root = DOCKER_WORKDIR if ctx.sandbox_mode == "docker" else str(ctx.worktree)
        system_prompt = build_system_prompt(display_root,
                                            load_repo_context(ctx.repo, ctx.base_commit),
                                            allow_commit=bool(args.allow_commit))
        result = runner.run(system_prompt, ctx.task)
    except Exception as e:
        if not sandbox_started and isinstance(e, (SandboxError, WorkspaceError)):
            return _fail_setup(e, ctx=ctx, run_dir=run_dir, transcript=transcript,
                                transcript_path=transcript_path)
        return _fail_run(e, ctx=ctx, sandbox=sandbox, sandbox_started=sandbox_started,
                          transcript=transcript, run_dir=run_dir,
                          transcript_path=transcript_path)
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
        stuck_on=extra.get("stuck_on"),
        files_changed=extra.get("files_changed") or [],
        files_changed_truncated=bool(extra.get("files_changed_truncated")),
        last_tool_result=extra.get("last_tool_result"),
        last_assistant_text=extra.get("last_assistant_text"),
        verify=extra.get("verify"),
        turns=result.turns,
        **_contract_fields(extra, ctx),
    )

    print(json.dumps(_emit_result(
        status=final_status, worktree=ctx.worktree, branch=ctx.branch, transcript_path=transcript_path,
        run_dir=run_dir, turns=result.turns, usage=result.usage, final_message=result.final_message,
        base_commit=ctx.base_commit, finalize_error=finalize_error,
        watchdog_violation=extra.get("watchdog_violation"),
        watchdog_violation_kind=extra.get("watchdog_violation_kind"),
        stuck_on=extra.get("stuck_on"),
        files_changed=extra.get("files_changed") or [],
        files_changed_truncated=bool(extra.get("files_changed_truncated")),
        last_tool_result=extra.get("last_tool_result"),
        last_assistant_text=extra.get("last_assistant_text"),
        verify=extra.get("verify"),
        resumed_from=ctx.resumed_from, provider=ctx.provider,
        **_contract_fields(extra, ctx),
    ), indent=2))
    return 0 if final_status == "completed" else 1


def _add_run_flags(p, *, resume: bool) -> None:
    p.add_argument("--model", default=None if resume else DEFAULT_MODEL)
    p.add_argument("--max-turns", type=int, default=40)
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--temperature", type=float, default=None)
    p.add_argument("--provider", choices=list(PROVIDER_NAMES),
                   default=None if resume else "openai",
                   help="model provider (default: openai — any OpenAI-compatible endpoint)")
    p.add_argument("--base-url", default=None,
                   help="provider endpoint (default: the provider's own default)")
    p.add_argument("--stall-turns", type=_non_negative_int, default=DEFAULT_STALL_TURNS,
                   help="end the run as 'stalled' after N turns without progress (0 disables)")
    p.add_argument("--stuck-repeats", type=_non_negative_int, default=DEFAULT_STUCK_REPEATS,
                   help="end the run as 'stuck' after the same failing bash command runs N "
                        "times in a row (0 disables); independent of --stall-turns")
    p.add_argument("--verify", default=None, metavar="CMD",
                   help="run CMD in the sandbox when the worker declares itself done; a "
                        "non-zero exit ends the run as 'verify_failed' (resume inherits the "
                        "command from the run it continues)")
    p.add_argument("--verify-rounds", type=_non_negative_int,
                   default=None if resume else DEFAULT_VERIFY_ROUNDS,
                   help="fix rounds after a failed --verify (default 1: the first failure goes "
                        "back to the worker once; 0 verifies once and ends the run either way; "
                        "resume inherits this from the run it continues)")
    p.add_argument("--verify-timeout", type=_positive_int,
                   default=None if resume else DEFAULT_VERIFY_TIMEOUT,
                   help="seconds for the --verify command (default 600, clamped to 1-600; "
                        "resume inherits this from the run it continues)")
    p.add_argument("--context-window", type=_positive_int, default=None,
                   help="model context window in tokens (default: the server's loaded window, "
                        "else the built-in table, else 32768)")
    p.add_argument("--max-tokens", type=_positive_int,
                   default=None if resume else DEFAULT_MAX_TOKENS,
                   help="max tokens the model may generate per reply (default 8192; must be "
                        "smaller than the context window, and subtracted from it when the "
                        "prompt budget is computed; resume inherits this from the run it "
                        "continues)")
    p.add_argument("--max-worktree-mb", type=int, default=DEFAULT_MAX_WORKTREE_MB)
    p.add_argument("--max-worktree-files", type=int, default=DEFAULT_MAX_WORKTREE_FILES)
    p.add_argument("--image", default=None if resume else DEFAULT_IMAGE)
    p.add_argument("--allow-network", action="store_true", default=False)
    p.add_argument("--memory", default="4g")
    p.add_argument("--cpus", default="2")
    p.add_argument("--tmp-size", type=_tmpfs_size, default="1g")
    p.add_argument("--gitdir-size", type=_tmpfs_size, default="512m")
    p.add_argument("--home-size", type=_tmpfs_size, default="256m",
                   help="docker mode only: cap of the /home/worker tmpfs (default 256m); "
                        "package caches (NuGet ~/.nuget/packages, npm ~/.npm, pip "
                        "~/.cache/pip) live under $HOME; this, --tmp-size and "
                        "--gitdir-size all count against --memory")
    p.add_argument("--min-free-mb", type=int, default=2048)
    p.add_argument("--keep-volume", action="store_true", default=False)
    p.add_argument("--max-patch-mb", type=int, default=10)
    p.add_argument("--allow-commit", action="store_true", default=None,
                   help="host mode only: tell the worker to commit its work as it goes "
                        "(resume inherits this from the run it continues)")


def _add_runs_parsers(sub) -> None:
    """`dirtywork runs ...` (spec SP3 section 4). Every subcommand is
    implemented in dirtywork/runs.py and routed by `runs.dispatch()`."""
    runs_p = sub.add_parser("runs", help="inspect and manage dirtywork runs")
    runs_sub = runs_p.add_subparsers(dest="runs_cmd", required=True)

    list_p = runs_sub.add_parser("list", help="list every run under ~/.dirtywork/runs")
    list_p.add_argument("--json", action="store_true", help="machine-readable output")

    show_p = runs_sub.add_parser("show", help="show one run's summary, run.json and timeline")
    show_p.add_argument("slug")
    show_p.add_argument("--diff", action="store_true", help="also print the run's diff.patch")
    show_p.add_argument("--markdown", action="store_true",
                        help="render the run as a Markdown document (header, one section per "
                             "turn, collapsible tool results) instead of the JSON dump")
    show_p.add_argument("--out", default=None, metavar="FILE",
                        help="with --markdown, write the document to FILE instead of stdout")

    export_p = runs_sub.add_parser("export", help="re-run the export flow for a run")
    export_p.add_argument("slug")
    export_p.add_argument("--max-patch-mb", type=int, default=10)
    export_p.add_argument("--keep-volume", action="store_true", default=False)
    export_p.add_argument("--max-worktree-mb", type=int, default=DEFAULT_MAX_WORKTREE_MB)
    export_p.add_argument("--max-worktree-files", type=int, default=DEFAULT_MAX_WORKTREE_FILES)

    clean_p = runs_sub.add_parser("clean", help="remove a run's container/volume/worktree/run dir")
    clean_p.add_argument("slug", nargs="?", default=None)
    clean_p.add_argument("--all", action="store_true", default=False)
    clean_p.add_argument("--keep-transcript", action="store_true", default=False)
    clean_p.add_argument("--force", action="store_true", default=False)

    verdict_p = runs_sub.add_parser("verdict", help="record accept/reject/cleanup for a run")
    verdict_p.add_argument("slug")
    verdict_p.add_argument("verdict", choices=["accept", "reject", "cleanup"])
    verdict_p.add_argument("--note", default=None)
    verdict_p.add_argument("--review-seconds", type=float, default=None)

    snapshot_p = runs_sub.add_parser(
        "snapshot", help="commit the run worktree's current content onto its branch")
    snapshot_p.add_argument("slug")


def _add_bench_parsers(sub) -> None:
    """`dirtywork bench ...` (spec SP3 section 5). --provider/--base-url are
    passed straight through to `dirtywork run`; leaving them unset means `run`'s
    own defaults apply, and a per-model override uses the
    `model[@provider][=base_url]` spec syntax."""
    bench_p = sub.add_parser("bench", help="benchmark models against the fixture tasks")
    bench_p.add_argument("--models", default=None,
                         help="comma-separated model[@provider][=base_url] specs")
    bench_p.add_argument("--provider", default=None, help="default provider for every model")
    bench_p.add_argument("--base-url", default=None, help="default base URL for every model")
    bench_p.add_argument("--repeats", type=_positive_int, default=1)
    bench_p.add_argument("--tasks", default=None,
                         help="comma-separated fixture names (default: all of bench/repos)")
    bench_p.add_argument("--out", default=None,
                         help="results JSONL path (default: ~/.dirtywork/bench/<stamp>.jsonl)")
    bench_p.add_argument("--max-turns", type=_positive_int, default=40)
    bench_p.add_argument("--timeout", type=_positive_int, default=1800)

    bench_sub = bench_p.add_subparsers(dest="bench_cmd")
    summarize_p = bench_sub.add_parser("summarize", help="summarize a bench results file")
    summarize_p.add_argument("file")
    summarize_p.add_argument("--compare", default=None, metavar="FILE",
                             help="second results file: print two paired 'A -> B (delta)' "
                                  "tables instead of the usual summary -- the per-(model, task) "
                                  "table and the paired per-model summary")


def _parse_args(argv):
    parser = argparse.ArgumentParser(prog="dirtywork")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="run one task in an isolated worktree")
    run_p.add_argument("task")
    run_p.add_argument("--repo", required=True, type=Path)
    run_p.add_argument("--branch-from", default=None, metavar="REF",
                       help="branch the new worktree from REF (default: repo HEAD). "
                            "'@<slug>' means an earlier run's branch: its worktree is "
                            "snapshotted first if dirty, so the new run starts from that "
                            "run's work as it stands")
    run_p.add_argument("--sandbox", choices=["docker", "none"], default="docker")
    _add_run_flags(run_p, resume=False)
    resume_p = sub.add_parser("resume", help="continue an earlier run on its worktree")
    resume_p.add_argument("run", help="run slug (under ~/.dirtywork/runs) or a run directory path")
    resume_p.add_argument("--feedback", default=None, metavar="TEXT",
                          help="reviewer instructions for this resume; the resumed task tells "
                               "the worker to inspect the earlier work and apply exactly this "
                               "and nothing else. Required to resume a 'completed' run")
    resume_p.add_argument("--feedback-file", default=None, metavar="PATH",
                          help="read --feedback from a UTF-8 file instead (max 64000 chars)")
    _add_run_flags(resume_p, resume=True)
    _add_runs_parsers(sub)
    _add_bench_parsers(sub)
    return parser.parse_args(argv)


def run_once(argv: list) -> dict:
    """Run one dirtywork invocation in-process and return its stdout JSON.
    Relies on the machine contract -- exactly one JSON object on stdout after
    preflight -- so `dirtywork bench` can drive many runs without paying for a
    subprocess (and a fresh interpreter) per run. stderr is captured too, so a
    preflight refusal shows up in the raised error rather than vanishing."""
    out_buf, err_buf = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
        try:
            rc = main(argv)
        except SystemExit as e:
            # argparse calls sys.exit() on a bad flag (e.g. an invalid --provider
            # choice). SystemExit is a BaseException, not an Exception, so left
            # uncaught it would escape run_one_bench_case's `except Exception` and
            # abort the whole bench sweep instead of recording one bench_error row.
            raise RuntimeError(f"dirtywork exited via SystemExit({e.code}): "
                               f"{err_buf.getvalue().strip()}") from None
    text = out_buf.getvalue()
    if not text.strip():
        raise RuntimeError(f"dirtywork produced no stdout JSON (exit {rc}): "
                           f"{err_buf.getvalue().strip()}")
    return json.loads(text)


def main(argv: list | None = None) -> int:
    args = _parse_args(argv)
    if args.cmd == "runs":
        from . import runs as runs_mod
        return runs_mod.dispatch(args)
    if args.cmd == "bench":
        from . import bench as bench_mod
        return bench_mod.dispatch(args)
    try:
        prior = _load_resume_target(args) if args.cmd == "resume" else None
        repo = Path(prior["repo"]) if prior else args.repo
        repo = repo.expanduser().resolve()
        preflight_repo(repo)
        _resolve_allow_commit(args)
        client = _preflight_llm(args)
        context_window, window_source = _resolve_context_window(args, client)
        ctx = (_workspace_resume(args, prior, context_window, window_source) if prior
               else _workspace_new(args, repo, context_window, window_source))
    except (PreflightFailure, WorkspaceError) as e:
        _err(str(e))
        return 2
    _warn_task_size(ctx)
    return _execute(ctx, args, client)


if __name__ == "__main__":
    sys.exit(main())
