# dirtywork

[![CI](https://github.com/JimboSchneider/dirtywork/actions/workflows/ci.yml/badge.svg)](https://github.com/JimboSchneider/dirtywork/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/dirtywork.svg)](https://pypi.org/project/dirtywork/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/JimboSchneider/dirtywork/blob/main/LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://github.com/JimboSchneider/dirtywork/blob/main/pyproject.toml)

*Frontier models do the thinking. Local models do the dirty work.*

Runs one coding task against a local LM Studio model in an agentic tool-use
loop, inside an isolated git worktree. Built to be driven by an orchestrating
agent (Claude Code, in our case) — the expensive frontier model orchestrates
and reviews, the free local model grinds. Humans watch with `tail -f`.

**The division of labor:**

| | Role |
|---|---|
| Orchestrator (a frontier model, e.g. Claude Code — or you) | Picks the task, invokes `dirtywork`, reviews the worktree diff and transcript, commits/PRs what survives review |
| Worker (local model, via dirtywork) | Explores the repo, edits files, runs builds/tests — file tools are confined to the worktree; `bash` is a real shell (see [Security & trust](#security--trust)) |

File edits go through path confinement into an isolated git worktree, and nothing
the worker produces merges without your review. But the worker can run `bash`, and
a shell is a shell — read [Security & trust](#security--trust) before pointing this
at a model or repo you don't trust. Parallelism comes from launching multiple
processes — LM Studio serves 4 concurrent requests per model.

## Security & trust

dirtywork's containment is honest about its limits:

- **File tools (`read_file`/`write_file`/`edit_file`/`list_dir`/`grep`) are confined
  to the worktree** by real path resolution — symlinks, `..`, and absolute paths that
  escape are rejected. Writes additionally refuse to go through a symlink at the
  final path component (even one pointing back inside the worktree) and refuse
  any non-regular-file target (FIFO/device/socket) outright.
- **`bash` is a general shell, not a sandbox.** A denylist blocks common *accidents*
  (destructive commands aimed outside the worktree, shared-git-state writes, piping a
  download into an interpreter), and `HOME` is redirected into the worktree so `~`/
  `$HOME` can't reach your real `~/.ssh` or `~/.aws`. But a determined or
  prompt-injected model can still read absolute host paths (`cat /etc/…`) — the
  denylist raises the bar for a confused model, it does not stop an adversarial one.
  Concretely, host mode (`--sandbox none`, the only mode this version has) does
  **not** block writes made through an interpreter — e.g.
  `python3 -c "open('/tmp/x','w').write('y')"` succeeds. Enumerating every
  interpreter's write primitive is not a regex-shaped problem; the real fix is a
  process boundary (an OS-level sandbox), tracked as the next release.
- **`.git/info/exclude` gains a line.** The first run against a repo appends
  `.worktrees/` to the shared repository's `.git/info/exclude` (not tracked, not
  committed, idempotent) so worktree directories don't show up as untracked noise
  in `git status`. This is the only host-side git state a run writes outside its
  own worktree.
- **Worktree growth is checked after every tool call.** Past `--max-worktree-mb`
  (default 2048) or `--max-worktree-files` (default 200000) the run ends with
  status `budget_exceeded`. This is a best-effort, sampled bound, not a kernel
  quota — see SECURITY.md.
- **Review is the real boundary.** Read the transcript and diff before you merge. Note
  that `bash` side-effects happen at run time, so review catches what lands in the
  diff, not what a command already did. `git diff --stat` in host mode compares
  against the run's base commit, so unstaged, staged, and committed changes to
  **tracked** files all show up — but a new file the model wrote and never
  `git add`ed won't appear in `diff_stat`. Such files are listed separately in
  `run_end.untracked` (untracked paths, one per line; a whole untracked
  directory collapses to a single `dir/` entry).

**Practical guidance:** run dirtywork against models and repositories you'd trust with
shell access on your machine. A malicious target repo's `CLAUDE.md`/`AGENTS.md` is
injected into the worker's prompt, so treat untrusted repos as you would untrusted
code. True per-run isolation (OS sandbox / container) is the tracked next step.

## Requirements

- macOS/Linux, Python 3.9+ (stdlib only — no venv, no pip deps)
- [LM Studio](https://lmstudio.ai) serving its OpenAI-compatible API at
  `localhost:1234` with a tool-calling-capable model loaded. Verified
  working: `qwen/qwen3-coder-next` (65k context, default) and
  `mistralai/devstral-small-2-2512` (32k context)
- The target repo must be a git repo with at least one commit

**Other servers:** anything speaking the OpenAI chat-completions API with tool
calling should work via `--base-url` (e.g. Ollama at
`http://localhost:11434/v1`) — but only LM Studio is tested today. Reports
welcome.

## Install

**pipx (PyPI):**

    pipx install dirtywork

**pipx (straight from GitHub):**

    pipx install git+https://github.com/JimboSchneider/dirtywork

**From source:**

    git clone https://github.com/JimboSchneider/dirtywork
    cd dirtywork
    chmod +x bin/dirtywork
    ln -sf "$PWD/bin/dirtywork" ~/.local/bin/dirtywork

The launcher is self-locating, so this works from any clone location.

## Use

    dirtywork run --repo ~/repos/someproject "Add a unit test for X"

- **Watch a run:** `tail -f` the transcript path printed on stderr
  (`~/.dirtywork/runs/<slug>/transcript.jsonl`).
- **Review a run:** `git -C <worktree> diff`, read the transcript (`run_end`
  carries `diff_stat` and `untracked`), run the repo's tests — then commit
  the branch or discard it.
- **Discard a run:**
  `git -C <repo> worktree remove --force <worktree> && git -C <repo> branch -D dirtywork/<slug>`
- **All flags, stdout JSON, exit codes, transcript events:** see
  [Machine contract](#machine-contract).

## How a run works

1. **Preflight** — LM Studio reachable, model loaded, repo valid. Any
   failure exits 2 with nothing created.
2. **Worktree** — a fresh worktree at `<repo>/.worktrees/dw-<slug>` on new
   branch `dirtywork/<slug>`, branched from `--branch-from` (default:
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
   transcript lands at `~/.dirtywork/runs/<slug>/transcript.jsonl`
   (outside the worktree, so it can never pollute the diff).

## Safety model

Guardrails block **accidents, not adversaries** — the post-run review is
the real gate:

- All file tools are path-confined to the worktree (symlink-safe realpath
  checks; `.git/` is write-protected against hook injection).
- `bash` runs cwd-pinned in the worktree with a minimal environment (your
  shell's tokens/keys are not inherited) and a regex denylist: `sudo`,
  `git push`, `git config`/`remote`/`worktree`/`branch -D`/… that would write
  the parent repo's shared state (including through `git -C`/`git -c`/`--flag`
  global options), `rm`/`mv`/`chmod`/`chown` on absolute or `~` paths,
  `cd`/`pushd` escapes, downloads piped to a shell, system-control
  commands, redirects outside the worktree.
- Every denylist rejection is logged to the transcript as a
  `guardrail_block` event, so attempted escapes are visible at review time.
- File tools refuse to operate on anything that isn't a regular file (FIFOs,
  devices, sockets) and refuse to write through a symlink at the final path
  component, even when its target is inside the worktree. `write_file`
  content is capped at 5 MB, `list_dir` output at 2000 entries, and the
  assistant's own text is capped at 64 000 chars in the transcript (the
  full text is still sent to the model).
- Worktree growth is sampled after every tool call against
  `--max-worktree-mb`/`--max-worktree-files`; past either, the run ends with
  status `budget_exceeded`.
- Network is allowed (package restores need it); per-command timeout 120s
  default, 600s max.

Plainly: this is **not a sandbox**. Run it against repos where you'd trust
yourself to review the diff — because that review is the actual gate.

## Development

    python3 -m pytest              # unit suite (no LM Studio needed)
    python3 -m pytest -m live -v   # live suite (requires LM Studio running;
                                   # includes a real end-to-end agent run)

Design docs: `docs/superpowers/specs/2026-08-13-localagent-design.md`
(architecture and contracts) and
`docs/superpowers/plans/2026-08-14-localagent.md` (implementation plan).

## The story

dirtywork was designed, built, reviewed, and shipped in one day — by the
exact orchestrator/worker pattern it implements — and its first production
run surfaced a real cent-level rounding bug in the invoicing app it was
pointed at. The full postmortem, including a build-one-yourself recipe:
[the postmortem](https://github.com/JimboSchneider/dirtywork/blob/main/docs/2026-08-14-building-localagent.md)
(or read [the designed HTML edition](https://dirtywork.run/building-localagent.html)
served via Pages).

In August 2026 the project was renamed **dirtywork** — same tool, a name that says what it does.

## Troubleshooting

- **exit 2, "cannot reach LM Studio"** — server not running; check `lms ps`
  and `curl -s localhost:1234/v1/models`.
- **exit 2, "model not loaded"** — `lms load <model>` (the error names the
  loaded models).
- **status `max_turns` / `timeout`** — the worktree is kept; read the
  transcript to see where it stalled, salvage what's useful, or re-run with
  higher limits.
- **status `context_exhausted`** — the task needed more context than the
  model's window; split the task or use the larger-context model.
- **status `budget_exceeded`** — the worktree grew past
  `--max-worktree-mb`/`--max-worktree-files` during a tool call; the
  worktree and branch are kept for salvage. Raise the limit or investigate
  what wrote so much.

## Machine contract

`dirtywork` is built to be driven by another agent (Claude Code) rather than
read by a human — the primary consumer parses stdout, not the terminal.

**Flags:**

```
dirtywork run --repo <path> "<task>"
    [--model qwen/qwen3-coder-next]   # or mistralai/devstral-small-2-2512
    [--branch-from <ref>]             # default: repo HEAD
    [--max-turns 40]
    [--timeout 1800]                  # whole-run wall clock, seconds
    [--temperature <f>]               # omitted by default → server preset
    [--base-url http://localhost:1234/v1]  # LM Studio's OpenAI-compatible endpoint
    [--max-worktree-mb 2048]          # best-effort worktree size bound
    [--max-worktree-files 200000]     # best-effort worktree entry-count bound
```

**stdout:** on any run that gets past preflight, exactly one JSON object is
printed to stdout (nothing else goes to stdout):

```json
{
  "status": "completed",
  "worktree": "/path/to/repo/.worktrees/dw-<slug>",
  "branch": "dirtywork/<slug>",
  "transcript": "/path/to/transcript.jsonl",
  "turns": 7,
  "usage": {"prompt_tokens": 0, "completion_tokens": 0},
  "final_message": "...",
  "run_dir": "/home/you/.dirtywork/runs/<slug>",
  "base_commit": "abc123def456..."
}
```

`status` is one of: `completed`, `max_turns`, `timeout`,
`context_exhausted`, `model_error`, `interrupted`, `budget_exceeded`. When
the run fails before a `RunResult` exists — the LLM client raises,
post-worktree setup fails (e.g. the transcript can't be created), or any
other exception escapes the run (status `model_error` in every case) —
`turns` is `null` and `usage` is `{}`, but `status`, `worktree`, `branch`,
`transcript`, `run_dir`, and (when it was resolved before the failure)
`base_commit` are still populated so the worktree can be located for
salvage.

**Exit codes:**

- `0` — `completed`.
- `1` — run ended abnormally (`max_turns`, `timeout`, `context_exhausted`,
  `model_error`, `interrupted`, `budget_exceeded`); the worktree and branch
  are kept for salvage/review. `main` catches every `Exception` the run
  raises (not just ones the runner itself converts to a status) and reports
  it as `model_error` via the same JSON contract, so a post-preflight run
  never tracebacks. (Ctrl-C is a `KeyboardInterrupt`, a `BaseException`, not caught
  here — but the run loop itself already converts in-loop Ctrl-C to status
  `interrupted` before it would reach this point.)
- `2` — preflight or environment error (LM Studio unreachable, model not
  loaded, `--repo` not a git repo, etc.); nothing is created.

All progress (transcript path, worktree path, `error:`-prefixed messages) is
written to stderr; watch a live run with `tail -f` on the transcript path.

**Transcript events** (JSONL, one per line): `run_start` (task, repo, model,
config, plus provenance: `base_commit`, `branch`, `branch_from`,
`base_url`, `dirtywork_version`, `temperature`, `sandbox`, `provider`),
`assistant` (text + tool calls — text capped at 64 000 chars in the
transcript only, the full text is still sent to the model), `tool_result`
(truncated), `guardrail_block`, `run_end` (status, turns, duration,
cumulative usage, plus `diff_stat` in host mode — `git diff --stat`
against the base commit, tracked changes only, capped at 64 000 chars —
and `untracked`, `git status --porcelain` `??` entries, capped at
64 000 chars).

## Contributing

Issues and PRs welcome. Ground rules:

- Runtime stays **stdlib-only** — that zero-dependency install is a feature,
  not an accident. Dev-only dependencies (pytest) are fine.
- `python3 -m pytest` must be green; if your change touches the model-facing
  path, run the live suite too (`python3 -m pytest -m live -v`, needs a
  running LM Studio).
- Tool functions never raise; the client raises `LLMError` only; stdout is
  exactly one JSON object post-preflight. These contracts have tests — keep
  them green.

## License

[MIT](https://github.com/JimboSchneider/dirtywork/blob/main/LICENSE) © 2026 Dirt Simple Solutions, LLC
