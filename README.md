# localagent

Runs one coding task against a local LM Studio model in an agentic tool-use
loop, inside an isolated git worktree. Built to be driven by Claude Code;
humans watch with `tail -f`.

## Install

    chmod +x bin/localagent
    ln -sf ~/repos/localagent/bin/localagent ~/.local/bin/localagent

## Use

    localagent run --repo ~/repos/someproject "Add a unit test for X"

Watch a run: `tail -f` the transcript path printed on stderr.
Review a run: `git -C <worktree> diff`, then commit or discard.
Discard a run: `git -C <repo> worktree remove --force <worktree> &&
git -C <repo> branch -D localagent/<slug>`

Design: docs/superpowers/specs/2026-08-13-localagent-design.md

## Machine contract

`localagent` is built to be driven by another agent (Claude Code) rather than
read by a human — the primary consumer parses stdout, not the terminal.

**Flags:**

```
localagent run --repo <path> "<task>"
    [--model qwen/qwen3-coder-next]   # or mistralai/devstral-small-2-2512
    [--branch-from <ref>]             # default: repo HEAD
    [--max-turns 40]
    [--timeout 1800]                  # whole-run wall clock, seconds
    [--temperature <f>]               # omitted by default → server preset
    [--base-url http://localhost:1234/v1]  # LM Studio's OpenAI-compatible endpoint
```

**stdout:** on any run that gets past preflight, exactly one JSON object is
printed to stdout (nothing else goes to stdout):

```json
{
  "status": "completed",
  "worktree": "/path/to/repo/.worktrees/la-<slug>",
  "branch": "localagent/<slug>",
  "transcript": "/path/to/transcript.jsonl",
  "turns": 7,
  "usage": {"prompt_tokens": 0, "completion_tokens": 0},
  "final_message": "..."
}
```

`status` is one of: `completed`, `max_turns`, `timeout`,
`context_exhausted`, `model_error`, `interrupted`. When the run fails before
a `RunResult` exists (e.g. the LLM client raises mid-run), `turns` is `null`
and `usage` is `{}`, but `status`, `worktree`, `branch`, and `transcript` are
still populated so the worktree can be located for salvage.

**Exit codes:**

- `0` — `completed`.
- `1` — run ended abnormally (`max_turns`, `timeout`, `context_exhausted`,
  `model_error`, `interrupted`); the worktree and branch are kept for
  salvage/review.
- `2` — preflight or environment error (LM Studio unreachable, model not
  loaded, `--repo` not a git repo, etc.); nothing is created.

All progress (transcript path, worktree path, `error:`-prefixed messages) is
written to stderr; watch a live run with `tail -f` on the transcript path.
