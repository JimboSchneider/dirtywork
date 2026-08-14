# localagent — local LLM agent loop harness

**Date:** 2026-08-13
**Status:** Approved design, pre-implementation

## Purpose

A CLI that runs one coding task per process against a local LM Studio model
(qwen3-coder-next by default) in an agentic tool-use loop, so Claude Code can
delegate exploratory or implementation work to free local models and review the
result before anything merges. Claude is the orchestrator and review gate; the
local model is the worker.

**Success criteria:** Claude can run
`localagent run --repo <any-git-repo> "<task>"`, watch it via `tail -f` on the
transcript, and afterwards review an isolated worktree diff plus a complete
JSONL record of every tool call. Parallelism comes from launching multiple
processes (LM Studio serves 4 concurrent requests per model), not from the CLI.

## Non-goals (v1)

- No auto-commit, auto-merge, or push — the harness never touches the main
  checkout; Claude commits after review.
- No non-git directories (worktree isolation is the safety model).
- No interactive/pretty CLI UX — machine-first output; humans watch via
  `tail -f`.
- No multi-task orchestration inside the CLI — one process, one task.
- No adversarial-grade sandboxing — guardrails block accidents, not malice.

## Environment facts (verified 2026-08-13)

- LM Studio serves OpenAI-compatible API at `http://localhost:1234/v1`.
- Native OpenAI tool-calling works with `qwen/qwen3-coder-next` (verified by
  live test; a `tools` array produced a well-formed `tool_calls` response).
- Models: `qwen/qwen3-coder-next` (65,536 ctx, default) and
  `mistralai/devstral-small-2-2512` (32,768 ctx; its chat template consumes
  ~500 prompt tokens per call). Devstral tool-calling verified working on
  2026-08-14 (live smoke test).
- System Python is 3.9.6; no `uv`. Target: Python 3.9+, stdlib only, no venv.
- A real `rg` binary may not be on PATH for subprocesses (Claude Code shims it
  as a shell function); `grep` tool must fall back to `grep -rn`.

## Architecture

New repo `~/repos/localagent`. Stdlib only. One module per concern:

```
localagent/
  localagent/
    __main__.py     # CLI: argparse, wiring, exit codes
    runner.py       # agent loop + context management
    llm.py          # HTTP client for POST /v1/chat/completions (urllib)
    tools.py        # 6 tool schemas + implementations
    guardrails.py   # path confinement + bash denylist + env scrubbing
    workspace.py    # git worktree lifecycle, CLAUDE.md/AGENTS.md discovery
    transcript.py   # JSONL event writer
  tests/
  pyproject.toml    # metadata only; entry point symlinked into ~/.local/bin
```

## CLI contract

```
localagent run --repo <path> "<task>"
    [--model qwen/qwen3-coder-next]   # or mistralai/devstral-small-2-2512
    [--branch-from <ref>]             # default: repo HEAD
    [--max-turns 40]
    [--timeout 1800]                  # whole-run wall clock, seconds
    [--temperature <f>]               # omitted by default → server preset
```

**Lifecycle:**

1. Preflight: LM Studio reachable, requested model loaded, `--repo` is a git
   repo with ≥1 commit. Any failure → exit 2, nothing created.
2. Create worktree `<repo>/.worktrees/la-<slug>` on new branch
   `localagent/<slug>` from `--branch-from`. Add `.worktrees/` to the repo's
   `.git/info/exclude` (local-only) if not already ignored.
3. Print transcript path to stderr immediately (for `tail -f`).
4. Run the loop. No auto-commit; changes stay uncommitted in the worktree.
5. Print one JSON object to stdout:
   `{status, worktree, branch, transcript, turns, final_message}`.

**Exit codes:** 0 = completed; 1 = run ended abnormally
(`max_turns` / `timeout` / `context_exhausted` / `model_error` /
`interrupted`) with worktree kept for salvage; 2 = harness/environment error.

**Slug:** first ~5 words of the task, kebab-cased, plus a short timestamp
suffix (`la-add-footer-tests-0813a`), guaranteeing unique worktree/branch
names across runs.

**Statuses:** `completed`, `max_turns`, `timeout`, `context_exhausted`,
`model_error`, `interrupted`.

## System prompt

Assembled per run:

- Role: coding agent working in worktree `<path>`; complete the task, then
  reply with a plain-text summary of what changed and what was run.
- Tool rules: use `edit_file`/`write_file` for all file changes (never `sed`,
  `echo >`, or heredocs via bash); verify work by running the repo's tests
  before finishing.
- If `CLAUDE.md` or `AGENTS.md` exists at the repo root, its content is
  appended so the agent inherits repo conventions (commands, layout, gates).

## Tools

Exposed via OpenAI function-calling:

| Tool | Behavior |
|---|---|
| `read_file(path, offset?, limit?)` | Numbered lines; output capped ~8k chars with truncation marker instructing re-read with offset |
| `write_file(path, content)` | Create/overwrite; auto-creates parent dirs |
| `edit_file(path, old_string, new_string)` | Exact match, must occur exactly once, else instructive error (mirrors Claude's Edit semantics) |
| `list_dir(path)` | Entries + type + size, sorted |
| `grep(pattern, path?, glob?)` | `rg` binary if found, else `grep -rn`; capped results |
| `bash(command, timeout?)` | Guardrailed subprocess, see below |

## Guardrails

**Path confinement (all file tools):** paths resolve via `realpath` (symlinks
followed) and must land inside the worktree. Absolute paths outside it, `..`
escapes, and symlink escapes are rejected with a readable error. `.git/` is
additionally off-limits to `write_file`/`edit_file` (blocks hook injection).

**Bash:**

- cwd forced to the worktree; run via `bash -c`.
- Minimal environment: `PATH`, `HOME`, `TERM` + language cache vars as needed
  (`DOTNET_CLI_HOME`, `npm_config_cache`). The parent shell env (tokens, keys)
  is not inherited.
- Denylist (case-insensitive regex, instructive rejection): `sudo`; `git push`;
  `rm`/`mv`/`chmod`/`chown` targeting absolute paths or `~`; download piped to
  shell (`curl|wget … | sh`); `osascript`/`launchctl`/`shutdown`/`killall`;
  redirects to absolute paths outside the worktree.
- Per-command timeout default 120s, max 600s. Output capped ~10k chars.
- Network allowed (`npm ci`, NuGet restore need it).
- Every denylist rejection is logged to the transcript as `guardrail_block`.

## Loop & context management

- POST system + task → if reply has `tool_calls`, execute each, append results
  as `tool` messages, repeat; plain content → run complete.
- Sampling params omitted unless `--temperature` given (server preset applies).
- Context budget: ~4 chars/token estimate against the model's window (65k
  qwen / 32k devstral). Past ~75%, oldest tool results (never the task, system
  prompt, or assistant messages) are replaced with
  `[result trimmed — re-run the tool if needed]`. If a request still cannot
  fit, end with `context_exhausted`.
- Per-tool-result cap before appending (~8–10k chars, tool-specific).

## Transcript

JSONL, one event per line, `tail -f`-friendly, stored at
`~/.localagent/runs/<run-id>/transcript.jsonl` (outside the worktree so it can
never pollute the diff). Event types:

- `run_start` — task, repo, model, config
- `assistant` — message text and/or tool calls
- `tool_result` — name, args, truncated output
- `guardrail_block` — tool, attempted action, rule hit
- `run_end` — status, turns, duration, cumulative `usage` from the API

## Error handling

- LM Studio down / model not loaded / bad repo → exit 2 before worktree
  creation.
- Malformed tool-call JSON or unknown tool name → error text returned as the
  tool result so the model self-corrects; 3 consecutive such failures → abort
  with `model_error`.
- Tool-level errors (missing file, non-unique edit match, guardrail block) →
  instructive text as the tool result; normal loop traffic.
- Timeout / max-turns / SIGINT → `run_end` written, worktree kept. Nothing is
  cleaned up automatically.

## Testing

pytest, run locally (no CI in v1):

- Unit: guardrails (denylist cases, path/symlink escapes, `.git` write block),
  `edit_file` uniqueness semantics, context trimming, transcript writer.
- Loop: fake LLM client injected into `runner` (scripted responses) driving a
  2–3 turn run against a temp git repo — no LM Studio required.
- `@pytest.mark.live` smoke test against the real server when it is up
  (includes a devstral tool-calling check).

## Risks / notes

- Devstral tool-calling unverified until implementation (see above).
- Denylists are bypassable by construction; the threat model is accidental
  damage from a weak model, not an adversarial one. The worktree + minimal env
  + review gate carry the real safety weight.
- Local models may loop unproductively; `--max-turns`, `--timeout`, and the
  consecutive-failure abort bound the waste.
