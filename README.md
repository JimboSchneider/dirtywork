# localagent

Runs one coding task against a local LM Studio model in an agentic tool-use
loop, inside an isolated git worktree. Built to be driven by Claude Code —
the expensive model orchestrates and reviews, the free local model grinds.
Humans watch with `tail -f`.

**The division of labor:**

| | Role |
|---|---|
| Claude Code | Picks the task, invokes `localagent`, reviews the worktree diff and transcript, commits/PRs what survives review |
| Local model (via localagent) | Explores the repo, edits files, runs builds/tests — inside a worktree it cannot escape |

Nothing the local model does touches your main checkout, and nothing it
produces merges without review. Parallelism comes from launching multiple
processes — LM Studio serves 4 concurrent requests per model.

## Requirements

- macOS/Linux, Python 3.9+ (stdlib only — no venv, no pip deps)
- [LM Studio](https://lmstudio.ai) serving its OpenAI-compatible API at
  `localhost:1234` with a tool-calling-capable model loaded. Verified
  working: `qwen/qwen3-coder-next` (65k context, default) and
  `mistralai/devstral-small-2-2512` (32k context)
- The target repo must be a git repo with at least one commit

## Install

    chmod +x bin/localagent
    ln -sf ~/repos/localagent/bin/localagent ~/.local/bin/localagent

The launcher assumes this repo lives at `~/repos/localagent`; edit
`bin/localagent` if yours lives elsewhere.

## Use

    localagent run --repo ~/repos/someproject "Add a unit test for X"

- **Watch a run:** `tail -f` the transcript path printed on stderr.
- **Review a run:** `git -C <worktree> diff`, read the transcript, run the
  repo's tests — then commit the branch or discard it.
- **Discard a run:**
  `git -C <repo> worktree remove --force <worktree> && git -C <repo> branch -D localagent/<slug>`

## How a run works

1. **Preflight** — LM Studio reachable, model loaded, repo valid. Any
   failure exits 2 with nothing created.
2. **Worktree** — a fresh worktree at `<repo>/.worktrees/la-<slug>` on new
   branch `localagent/<slug>`, branched from `--branch-from` (default:
   repo HEAD). `.worktrees/` is added to the repo's local
   `.git/info/exclude` automatically. If the repo has a `CLAUDE.md` or
   `AGENTS.md` at its root, its content is injected into the worker's
   system prompt so it inherits your conventions.
3. **The loop** — the model gets six tools (`read_file`, `write_file`,
   `edit_file`, `list_dir`, `grep`, `bash`) via OpenAI function-calling and
   works until it replies without calling a tool. Context is budgeted per
   model (oldest tool results get trimmed first); three consecutive
   malformed tool calls abort the run.
4. **No auto-commit** — changes stay uncommitted in the worktree; the
   transcript lands at `~/.localagent/runs/<slug>/transcript.jsonl`
   (outside the worktree, so it can never pollute the diff).

## Safety model

Guardrails block **accidents, not adversaries** — the post-run review is
the real gate:

- All file tools are path-confined to the worktree (symlink-safe realpath
  checks; `.git/` is write-protected against hook injection).
- `bash` runs cwd-pinned in the worktree with a minimal environment (your
  shell's tokens/keys are not inherited) and a regex denylist: `sudo`,
  `git push`, `rm`/`mv`/`chmod`/`chown` on absolute or `~` paths,
  `cd`/`pushd` escapes, downloads piped to a shell, system-control
  commands, redirects outside the worktree.
- Every denylist rejection is logged to the transcript as a
  `guardrail_block` event, so attempted escapes are visible at review time.
- Network is allowed (package restores need it); per-command timeout 120s
  default, 600s max.

## Development

    python3 -m pytest              # unit suite (no LM Studio needed)
    python3 -m pytest -m live -v   # live suite (requires LM Studio running;
                                   # includes a real end-to-end agent run)

Design docs: `docs/superpowers/specs/2026-08-13-localagent-design.md`
(architecture and contracts) and
`docs/superpowers/plans/2026-08-14-localagent.md` (implementation plan).

## Troubleshooting

- **exit 2, "cannot reach LM Studio"** — server not running; check `lms ps`
  and `curl -s localhost:1234/v1/models`.
- **exit 2, "model not loaded"** — `lms load <model>` (the error names the
  loaded models).
- **exit 2, "branch already exists"** — two runs with the same first five
  task words in the same clock minute collide on the slug; re-run a moment
  later or reword the task.
- **status `max_turns` / `timeout`** — the worktree is kept; read the
  transcript to see where it stalled, salvage what's useful, or re-run with
  higher limits.
- **status `context_exhausted`** — the task needed more context than the
  model's window; split the task or use the larger-context model.

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

**Transcript events** (JSONL, one per line): `run_start` (task, repo, model,
config), `assistant` (text + tool calls), `tool_result` (truncated),
`guardrail_block`, `run_end` (status, turns, duration, cumulative usage).
