"""Pure helpers for `dirtywork resume` (spec §5): find the prior run, decide
whether it can be resumed, and build the resumed task text from the prior
transcript. No sandbox or LLM access here — the CLI wires these in."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .rundir import read_run_json

RESUME_TAIL_CHARS = 12_000
RESUME_MARKER = "\n\n--- RESUMED RUN ---\n"
PRE_RESUME_SUFFIX = ".pre-resume"
_REQUIRED_STR_KEYS = ("slug", "repo", "worktree", "branch", "base_commit", "sandbox", "status")


def stash_dir_for(worktree: Path, slug: str) -> Path:
    """Where a docker resume parks the prior worktree content while its own
    export runs: a sibling of the worktree (under .worktrees/), unique per
    resumed run so no run can ever touch another run's stash."""
    return worktree.parent / f"{worktree.name}{PRE_RESUME_SUFFIX}-{slug}"


def find_stashes(worktree: Path) -> list:
    """Every pre-resume stash left beside `worktree` (any slug), sorted."""
    return sorted(worktree.parent.glob(f"{worktree.name}{PRE_RESUME_SUFFIX}-*"))


class ResumeError(Exception):
    """The prior run cannot be resumed; the message says why."""


def worktree_belongs_to_repo(worktree: Path, repo: Path) -> bool:
    """True only for a linked git worktree of `repo`: `.git` must be a FILE
    (not a directory — a plain clone is not ours) whose `gitdir:` target
    resolves inside repo/.git. A resume seeds this directory into a
    container and, at export, moves its content aside — it must never be
    pointed at a directory dirtywork did not create."""
    dotgit = worktree / ".git"
    if not dotgit.is_file() or dotgit.is_symlink():
        return False
    try:
        text = dotgit.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not text.startswith("gitdir:"):
        return False
    gitdir = Path(text[len("gitdir:"):].strip())
    if not gitdir.is_absolute():
        gitdir = worktree / gitdir
    try:
        gitdir = gitdir.resolve()
        common = (Path(repo) / ".git").resolve()
    except OSError:
        return False
    return gitdir == common or common in gitdir.parents


def resolve_run_dir(ref: str, runs_dir: Path) -> Path:
    """A bare slug lives under runs_dir; anything containing a path separator
    (or an absolute path) is taken as the run directory itself."""
    candidate = Path(ref).expanduser()
    if os.sep in ref or "/" in ref or candidate.is_absolute():
        return candidate.resolve()
    return Path(runs_dir) / ref


def _iter_events(transcript_path: Path):
    try:
        with open(transcript_path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    yield event
    except OSError:
        return


def _find_event(transcript_path: Path, name: str, *, last: bool = False):
    found = None
    for event in _iter_events(transcript_path):
        if event.get("event") == name:
            found = event
            if not last:
                break
    return found


def load_prior_run(run_dir: Path) -> dict:
    """run.json plus task/model/turns filled from the transcript for runs
    that predate SP2.5 (which started recording them in run.json)."""
    run_dir = Path(run_dir)
    try:
        data = read_run_json(run_dir)
    except (OSError, ValueError) as e:
        raise ResumeError(f"cannot read {run_dir / 'run.json'}: {e}")
    if not isinstance(data, dict):
        raise ResumeError(f"{run_dir / 'run.json'} is not a JSON object")
    for key in _REQUIRED_STR_KEYS:
        if not isinstance(data.get(key), str):
            raise ResumeError(f"{run_dir / 'run.json'} is missing '{key}'")
    transcript_path = run_dir / "transcript.jsonl"
    if not isinstance(data.get("task"), str) or not isinstance(data.get("model"), str):
        start = _find_event(transcript_path, "run_start")
        if start is None:
            raise ResumeError(f"{run_dir}: run.json has no task/model and the transcript "
                              f"has no run_start event")
        for key in ("task", "model"):
            if not isinstance(data.get(key), str):
                data[key] = start.get(key)
            if not isinstance(data.get(key), str):
                raise ResumeError(f"{run_dir}: cannot determine the prior run's {key}")
    if not isinstance(data.get("turns"), int):
        end = _find_event(transcript_path, "run_end", last=True)
        turns = end.get("turns") if end is not None else None
        data["turns"] = turns if isinstance(turns, int) else None
    return data


def pid_alive(pid) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def check_resumable(prior: dict, *, alive=pid_alive) -> None:
    if prior.get("status") == "running" and alive(prior.get("host_pid")):
        raise ResumeError(f"run {prior['slug']} is still in progress (pid {prior['host_pid']}); "
                          f"wait for it or stop it before resuming")
    worktree = Path(prior["worktree"])
    if not worktree.is_dir() or not (worktree / ".git").exists():
        raise ResumeError(f"worktree {worktree} is missing; nothing to resume")
    if not worktree_belongs_to_repo(worktree, Path(prior["repo"])):
        raise ResumeError(f"worktree {worktree} is not a linked worktree of {prior['repo']}; "
                          f"refusing to resume into a directory dirtywork did not create")
    stashes = find_stashes(worktree)
    if stashes:
        listing = ", ".join(str(p) for p in stashes)
        raise ResumeError(
            f"an earlier resume of this run was interrupted and left its pre-resume stash at "
            f"{listing}; that stash holds the worktree content from before that resume. Move "
            f"its contents back into {worktree} (or delete the stash if you no longer need it) "
            f"before resuming again")


def _render_event(event: dict) -> str | None:
    name = event.get("event")
    if name == "assistant":
        text = event.get("text") if isinstance(event.get("text"), str) else ""
        tools = ",".join(str(tc.get("name")) for tc in (event.get("tool_calls") or [])
                         if isinstance(tc, dict))
        return f"assistant: {text[:1000]}" + (f" [tools: {tools}]" if tools else "")
    if name == "tool_result":
        result = event.get("result") if isinstance(event.get("result"), str) else ""
        return f"tool_result {event.get('tool', '')}: {result[:500]}"
    if name == "nudge":
        return f"nudge: {event.get('kind', '')}"
    if name == "run_end":
        return f"run_end: {event.get('status', '')}"
    return None


def render_transcript_tail(transcript_path: Path, max_chars: int = RESUME_TAIL_CHARS) -> str:
    """The newest transcript events, one line each, taken from the end until
    the next line would push the text past max_chars. Missing/unreadable
    transcript → ''."""
    lines = [line for line in (_render_event(e) for e in _iter_events(transcript_path))
             if line is not None]
    kept = []
    total = 0
    for line in reversed(lines):
        if total + len(line) + 1 > max_chars:
            break
        kept.append(line)
        total += len(line) + 1
    return "\n".join(reversed(kept))


def build_resume_task(prior_task: str, prior_status: str, prior_turns, transcript_tail: str) -> str:
    prior_task = prior_task.split(RESUME_MARKER, 1)[0]
    turns_text = str(prior_turns) if isinstance(prior_turns, int) else "unknown"
    return (
        f"{prior_task}{RESUME_MARKER}"
        f"This run continues an earlier run that ended with status '{prior_status}' after "
        f"{turns_text} turns.\n"
        "The worktree already contains that run's work: inspect it with `git status` and "
        "`git diff` before doing anything else, and continue from there — do not start over "
        "or revert prior work.\n"
        "The last events of the earlier run were:\n"
        f"{transcript_tail}\n"
        "When the task is complete, call finish(summary=...)."
    )
