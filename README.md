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

Developed and benchmarked on macOS/Apple Silicon; CI-tested on Linux and
macOS (Windows unsupported — see below).

## Documentation

- **[Operating guide](https://github.com/JimboSchneider/dirtywork/blob/main/docs/operating.md)** — running a task, resuming,
  reviewing, the `runs` subcommands, benchmarking, troubleshooting.
- **[Machine contract](https://github.com/JimboSchneider/dirtywork/blob/main/dirtywork/contract/machine-contract.md)** — every flag, the stdout
  JSON schema, exit codes, transcript events.
- **[Security](https://github.com/JimboSchneider/dirtywork/blob/main/docs/security.md)** — the Docker containment model, known
  exposures, and `--sandbox none`'s host-mode caveats.
- **[Transcript schema](https://github.com/JimboSchneider/dirtywork/blob/main/docs/transcript-schema.md)** — the full
  field-by-field JSONL/`run.json` reference.
- **[Worker image](https://github.com/JimboSchneider/dirtywork/blob/main/docker/README.md)** — building, publishing, and
  deriving the sandbox's Docker image.

## Security & trust

**Docker is the default sandbox as of 0.4 — a breaking change from 0.2.**
Every tool call (`read_file`/`write_file`/`append_file`/`edit_file`/`apply_edits`/
`insert_before`/`insert_after`/`list_dir`/`grep`/`bash`) runs inside a
locked-down container: `--network none` by default,
`--read-only` root filesystem, `--cap-drop ALL`, kernel-enforced memory/CPU/
process-count/per-file-size limits, and no host path mounted in except the
parent repository's read-only git object store.

Full model, known exposures and the host-mode caveats:
[docs/security.md](https://github.com/JimboSchneider/dirtywork/blob/main/docs/security.md).

## Platform support

| Tier | Platform | What it means |
|---|---|---|
| Developed & benchmarked | macOS on Apple Silicon (M-series, unified memory) with LM Studio | all worker/bench numbers and model-sizing guidance in `docs/superpowers/bench/` were measured here |
| CI-tested | Linux x86_64 (Ubuntu, Python 3.9 + 3.13) and macOS | unit suite on every push; the Docker sandbox live tests run on Linux in CI |
| Unsupported | Windows | the unit suite also runs on `windows-latest` in CI as an advisory (allowed-to-fail) job that publishes a per-file pass/fail/error/skip table; Windows remains unsupported until an integration suite passes (see the note in [Security & trust](https://github.com/JimboSchneider/dirtywork/blob/main/docs/security.md#security--trust)) |

Other OpenAI-compatible servers (vLLM, llama.cpp) should work via
`--base-url`/`--provider`. LM Studio (`--provider openai`) and Ollama
(`--provider ollama`) are both exercised — recorded-fixture contract tests for
each, plus an opt-in live smoke per server; the Anthropic API adapter
(`--provider anthropic`) has recorded-fixture tests and no live tests. Parallel
tool calls are unverified on Ollama.

## Requirements

- macOS/Linux, Python 3.9+ (stdlib only — no venv, no pip deps)
- **Docker Desktop or dockerd** (default sandbox as of 0.4) — `docker
  version` must succeed. Missing/unreachable Docker is a preflight error
  with a hint; pass `--sandbox none` to skip this requirement and run
  unsandboxed on the host instead.
- [LM Studio](https://lmstudio.ai) serving its OpenAI-compatible API at
  `localhost:1234` with a tool-calling-capable model loaded. Verified
  working: `qwen/qwen3-coder-next` (65k context, default) and
  `mistralai/devstral-small-2-2512` (32k context). One slot loaded with the
  largest context your machine holds beats several smaller ones — see
  [Sizing the context window](https://github.com/JimboSchneider/dirtywork/blob/main/docs/operating.md#sizing-the-context-window)
  for the measured numbers
- `--provider anthropic` needs the `ANTHROPIC_API_KEY` environment variable
  set; the default (`--provider openai`, LM Studio or any OpenAI-compatible
  server) and `--provider ollama` need no key.
- `--provider ollama` talks to `http://localhost:11434/v1` and asks
  `GET /api/ps` what context length the model is actually loaded with. Run
  `ollama run <model>` first — Ollama lists *pulled* models, not resident ones,
  so an unloaded model passes preflight and then gets whatever `num_ctx`
  Ollama picks. Model ids include the tag (`gemma4:latest`).
- The target repo must be a git repo with at least one commit

**Other servers:** anything speaking the OpenAI chat-completions API with tool
calling should work via `--base-url`. Ollama has its own `--provider ollama`
as of 0.10 (default base URL `http://localhost:11434/v1`, with a real
loaded-context probe) — see [Platform support](#platform-support) for what's
actually exercised by the test suites. Reports welcome.

## Install

**Recommended — pipx (PyPI):**

    pipx install dirtywork

Update later with `pipx upgrade dirtywork`. pipx keeps dirtywork in its own
venv and puts a `dirtywork` shim on your PATH, so it can't collide with your
projects' dependencies. If you don't have pipx, see the
[pipx install docs](https://pipx.pypa.io/stable/how-to/install-pipx.html) (macOS:
`brew install pipx && pipx ensurepath`).

**pipx (straight from GitHub, unreleased `main`):**

    pipx install git+https://github.com/JimboSchneider/dirtywork

**From source (for hacking on dirtywork itself):**

    git clone https://github.com/JimboSchneider/dirtywork
    cd dirtywork
    chmod +x bin/dirtywork
    ln -sf "$PWD/bin/dirtywork" ~/.local/bin/dirtywork

The launcher is self-locating, so this works from any clone location. Note
that whatever branch or working tree is checked out becomes "the tool" —
prefer the pipx install for day-to-day use and run `bin/dirtywork` from the
clone explicitly when testing unreleased changes.

## Use

    dirtywork run --repo ~/repos/someproject "Add a unit test for X"

- **Watch a run:** `tail -f` the transcript path printed on stderr.
- **Review a run:** `git -C <worktree> diff`, read the transcript, run the
  repo's tests — then commit the branch or discard it.
- **Clean up a run:** `dirtywork runs clean <slug>`.

### The review→fix loop

- `dirtywork resume <slug> --feedback "…"` — send the same worktree back to
  the worker with your review notes.
- `dirtywork run --branch-from @<slug> "…"` — start a fresh run from what an
  earlier run produced.
- `dirtywork runs snapshot <slug>` — commit a run's worktree onto its own
  branch by hand, without a full resume.
- `--verify "<cmd>"` — make your gate the harness's gate; a failing command
  comes back to the worker as a fix round before the run ends.

Everything else — resuming, the `runs` subcommands, benchmarking,
troubleshooting: [docs/operating.md](https://github.com/JimboSchneider/dirtywork/blob/main/docs/operating.md); every flag, the
stdout JSON and exit codes: [the machine contract](https://github.com/JimboSchneider/dirtywork/blob/main/dirtywork/contract/machine-contract.md).

## How a run works

1. **Preflight** — LM Studio reachable, model loaded, repo valid. Any
   failure exits 2 with nothing created.
2. **Worktree** — a fresh worktree at `<repo>/.worktrees/dw-<slug>` on new
   branch `dirtywork/<slug>`, branched from `--branch-from` (default:
   repo HEAD). In docker mode (the default) the worktree stays empty
   (only its `.git` file) for the whole run — the worker's tree lives on a
   Docker volume and reaches the worktree only via the validated export
   after the run ends. `.worktrees/` is added to the repo's local
   `.git/info/exclude` automatically. If the repo has a `CLAUDE.md` or
   `AGENTS.md` at its base commit, its content is injected into the
   worker's system prompt so it inherits your conventions.
3. **The loop** — the model gets eleven tools (`read_file`, `write_file`,
   `append_file`, `edit_file`, `apply_edits`, `insert_before`, `insert_after`,
   `list_dir`, `grep`, `bash`, `finish`) via OpenAI function-calling.
   `append_file` adds text verbatim to the end of an existing file, so a file
   larger than one reply is `write_file` for the first part and `append_file`
   for each part after it — the recovery a truncated `write_file` is now told
   to use by name, in a message that states the `--max-tokens` cap and a
   chunk size to stay under (1.0, #65).
   `insert_before`/`insert_after` add
   whole lines around a unique anchor without touching the anchor's own line
   — the primitive for "add a line here", which `edit_file` could only express
   as a replace. `apply_edits` takes a brief's whole numbered list of exact
   replacements to one file in a single call, applied in order, all-or-nothing:
   if any `old` does not match exactly once at its turn, nothing is written and
   the result names the first failure. Since 0.10 every file write is staged
   in a temp file and promoted atomically, so a run killed mid-write leaves
   the file byte-identical instead of truncated. Every successful
   `edit_file`/`apply_edits`/`write_file`/`append_file`/`insert_*` result
   echoes a capped unified diff of what actually changed, so a replace that
   silently deleted a line is visible to the worker in the same turn — except
   `write_file` on a NEW file, which has nothing to diff against and reports
   its byte and line count instead.
   Context is budgeted per model — dirtywork asks the server what window it
   actually loaded and reports both the value and its source, and the payload's
   `trimmed_turns` says on how many turns the oldest tool results had to be
   dropped to fit. Three consecutive tool failures of one kind (malformed
   call, malformed arguments, unknown tool, bad arguments, empty reply) or
   six in total abort the run, or six cut-off replies at `--max-tokens` (1.0,
   #65). The model ends a run by calling the
   `finish(summary=...)` tool (a plain reply with no tool call also ends it);
   an empty, think-only, or truncated reply, or a tool call written as text,
   is sent back with a one-line nudge instead of being treated as completion.
4. **No auto-commit** — changes stay uncommitted in the worktree; the
   transcript lands at `~/.dirtywork/runs/<slug>/transcript.jsonl`
   (outside the worktree, so it can never pollute the diff).

## Development

    python3 -m pytest                    # unit suite (no LM Studio or Docker needed)
    python3 -m pytest -m live -v         # live suite (requires LM Studio running;
                                          # includes a real end-to-end agent run)
    python3 -m pytest -m docker -v       # docker suite (requires a running Docker
                                          # daemon; host-sentinel and lifecycle tests)
    python3 -m pytest -m ollama -v       # ollama suite (requires a running Ollama
                                          # server; opt-in, same shape as -m live)

Design docs: `docs/superpowers/specs/2026-08-13-localagent-design.md`
(architecture and contracts) and
`docs/superpowers/plans/2026-08-14-localagent.md` (implementation plan).

## The story

dirtywork's first version was designed, built, reviewed, and shipped in a
single day — by the exact orchestrator/worker pattern it implements — and
its first production run surfaced a real cent-level rounding bug in the
invoicing app it was pointed at. That was v0.1. Since then the work has been
the unglamorous kind: hardening the host mode (0.3), putting the worker in a
container (0.4), and running the tool against its own security plans, task
by task, with a frontier model planning and reviewing, a local model doing
the typing, and every decision on the record — reviews, ledgers, a
scoreboard, and a release gate that runs on real Docker. The postmortems:
[building localagent](https://dirtywork.run/building-localagent.html),
[the tool renamed itself](https://dirtywork.run/the-tool-renamed-itself.html),
and the process record for the sandbox work in
[`docs/superpowers/bench/`](https://github.com/JimboSchneider/dirtywork/tree/main/docs/superpowers/bench/).

In August 2026 the project was renamed **dirtywork** — same tool, a name that says what it does.

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
