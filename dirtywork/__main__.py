from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .llm import LLMError, LMStudioClient
from .runner import Runner
from .tools import ToolExecutor
from .transcript import Transcript
from .workspace import (
    WorkspaceError,
    create_worktree,
    ensure_worktrees_excluded,
    load_repo_context,
    make_slug,
    preflight_repo,
)

RUNS_DIR = Path.home() / ".dirtywork" / "runs"
DEFAULT_MODEL = "qwen/qwen3-coder-next"


def build_system_prompt(worktree: Path, repo_context: str | None) -> str:
    prompt = f"""You are a coding agent working in a git worktree at {worktree}.
Complete the task, then reply with a plain-text summary of what you changed and what commands you ran.

Rules:
- Use edit_file or write_file for ALL file changes. Never modify files via bash (no sed -i, no echo redirects, no heredocs).
- Paths are relative to the worktree root.
- Explore before editing: use list_dir, grep, and read_file to understand the code first.
- Verify your work: run the repo's tests or build via bash before declaring the task complete.
- Do not run git commit or git branch commands; leave all changes uncommitted for review.
- When the task is complete, reply WITHOUT calling any tools — that final plain reply ends the run."""
    if repo_context:
        prompt += f"\n\nRepository conventions (from the repo's own docs):\n\n{repo_context}"
    return prompt


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def main(argv: list | None = None) -> int:
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
    args = parser.parse_args(argv)

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

    # ---- workspace ----
    slug = make_slug(args.task, datetime.now())
    try:
        ensure_worktrees_excluded(repo)
        worktree = create_worktree(repo, slug, args.branch_from)
    except WorkspaceError as e:
        _err(str(e))
        return 2

    transcript_path = RUNS_DIR / slug / "transcript.jsonl"
    print(f"transcript: {transcript_path}", file=sys.stderr)
    print(f"worktree:   {worktree}", file=sys.stderr)

    # ---- run ----
    # Everything from here on -- Transcript/ToolExecutor/Runner construction,
    # system-prompt assembly, and the run itself -- is wrapped in one boundary so
    # the machine contract (exactly one JSON object on stdout, post-preflight)
    # holds even if a component other than runner.run() blows up.
    transcript = None
    try:
        transcript = Transcript(transcript_path)
        executor = ToolExecutor(worktree, transcript=transcript)
        runner = Runner(client, executor, transcript, model=args.model,
                        max_turns=args.max_turns, timeout=args.timeout,
                        temperature=args.temperature,
                        run_info={"repo": str(repo), "worktree": str(worktree)})
        system_prompt = build_system_prompt(worktree, load_repo_context(worktree))
        result = runner.run(system_prompt, args.task)
    except Exception as e:
        message = str(e) if isinstance(e, LLMError) else f"unexpected error: {e!r}"
        if transcript is not None:
            try:
                transcript.write("run_end", status="model_error", error=message)
            except Exception:
                pass
        _err(message)
        print(json.dumps({
            "status": "model_error",
            "worktree": str(worktree),
            "branch": f"dirtywork/{slug}",
            "transcript": str(transcript_path),
            "turns": None,
            "usage": {},
            "final_message": message,
        }, indent=2))
        return 1
    finally:
        if transcript is not None:
            try:
                transcript.close()
            except Exception:
                pass

    print(json.dumps({
        "status": result.status,
        "worktree": str(worktree),
        "branch": f"dirtywork/{slug}",
        "transcript": str(transcript_path),
        "turns": result.turns,
        "usage": result.usage,
        "final_message": result.final_message,
    }, indent=2))
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
