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

## Security & trust

> **Docker mode protects host integrity and host execution, not
> repository-history confidentiality.** The worker can read the *entire*
> parent object store (all branches, other worktrees' objects, unreachable
> objects). Do not run it on a clone whose history holds secrets you would
> not show the worker.

> **Windows: designed for Docker Desktop on Windows; not supported until a
> Windows integration suite passes.** Items that need real Windows testing:
> Git for Windows paths and `\\?\` handling, `docker` CLI behavior, uid
> `1000:1000`, symlink-as-file export, case-insensitivity, long paths,
> `core.symlinks=false`, `core.longpaths`.

**Docker is the default sandbox as of 0.4 — a breaking change from 0.2.**
Every tool call (`read_file`/`write_file`/`edit_file`/`insert_before`/
`insert_after`/`list_dir`/`grep`/`bash`) runs inside a locked-down
container: `--network none` by default,
`--read-only` root filesystem, `--cap-drop ALL`, kernel-enforced memory/CPU/
process-count/per-file-size limits, and no host path mounted in except the
parent repository's read-only git object store. The worker's tree lives on
a Docker volume, never a bind mount, so host git never touches worker
content — a hostile `.gitattributes` plus a local git filter cannot execute
on the host. The tree reaches your worktree only after the run ends,
through a validated tar export (`dirtywork/sandbox/export.py`): file/dir/
symlink members only, path-escape and `.git`-named-entry checks, count and
byte caps, extraction that never calls `tarfile.extract()`/`extractall()`.
Docker missing or the daemon down is a preflight error (exit 2, with a
hint) — there is no silent fallback to unsandboxed execution. The export
step itself (which computes the diff and extracts the final tree, in a
fresh container) always runs with `--network none`, even when
`--allow-network` was passed for the worker.

**What Docker mode does *not* give you:**

- **Confidentiality of repository history** (see the callout above).
- **A portable disk quota.** Total disk is a best-effort bound: worktree
  size is sampled during commands and a host free-space floor is polled,
  but a burst inside one sampling interval (0.5-5 s) can exceed the limit
  before the container is killed. The exported tree is hard-capped by the
  validator regardless.
- **Git-ignored files in the exported worktree.** Build outputs
  (`node_modules`, `bin`/`obj`, `.venv`) stay inside the container's volume
  and are never exported — only the git-visible tree is. `--keep-volume`
  plus `docker run` against the volume recovers them if you need to.
  Non-Windows.

**`--sandbox none`** is the explicit opt-in to pre-0.4 host-mode behavior:
tools are path-confined to the worktree (symlink-safe realpath checks,
`.git/` write-protected), but `bash` is a general shell gated only by a
best-effort regex denylist and a `HOME` redirected into the worktree. A
determined or prompt-injected model can still read absolute host paths
(`cat /etc/...`). Use it only against models and repositories you would
trust with unconfined shell access on your machine — the same caveat 0.2
carried, unchanged.

**Residual exposures (documented, accepted, both modes where relevant):**

- Object-store confidentiality (docker mode) — see the callout above.
- Escaping symlinks (committed in the base tree, or created by the worker)
  are created on the host inside the worktree; dirtywork never follows
  them and lists them in `run_end.escaping_symlinks`. Anything *else* you
  run in that worktree afterward must not follow symlinks blindly.
- Host `git status`/`diff`/`add`/`merge` that *you* run afterward use your
  own git config; a worker-authored `.gitattributes` can trigger a
  configured filter (git-lfs and similar). Review
  `~/.dirtywork/runs/<slug>/diff.patch` instead (the container-computed
  patch — no host git ever touches worker content for that path) or with
  `GIT_CONFIG_GLOBAL=/dev/null`.
- `dirtywork runs snapshot`'s own commit runs no filter or hook (plumbing
  only, `--no-filters`). But two callers that decide *whether* to snapshot —
  `--branch-from @<slug>`'s dirty check and `runs clean`'s dirty-worktree
  guard — run host `git status` on the exported worktree, where a repo-LOCAL
  clean filter (e.g. `git lfs install --local`) still applies even with
  `GIT_CONFIG_GLOBAL=/dev/null`: the same exposure as running `git status`
  there yourself.
- A malicious target repo's `CLAUDE.md`/`AGENTS.md` (read from the base
  commit via git, not the filesystem — symlinks and oversized files are
  rejected) is injected into the worker's prompt; treat untrusted repos'
  documentation as you would untrusted code.

**Practical guidance:** run dirtywork against models and repositories
you'd trust with the equivalent of a locked-down container on your
machine. Read the transcript and diff before you merge — that review is
still the real gate for *what a run produced*, even though docker mode now
also gates *what a run could do to your host while producing it*.

## Platform support

| Tier | Platform | What it means |
|---|---|---|
| Developed & benchmarked | macOS on Apple Silicon (M-series, unified memory) with LM Studio | all worker/bench numbers and model-sizing guidance in `docs/superpowers/bench/` were measured here |
| CI-tested | Linux x86_64 (Ubuntu, Python 3.9 + 3.13) and macOS | unit suite on every push; the Docker sandbox live tests run on Linux in CI |
| Unsupported | Windows | until a Windows integration suite passes (see the note in [Security & trust](#security--trust)) |

Other OpenAI-compatible servers (Ollama, vLLM, llama.cpp) should work via
`--base-url`/`--provider`; only LM Studio and the Anthropic API adapter
(`--provider anthropic`, recorded-fixture tests, no live tests) are
exercised by the test suites.

## Requirements

- macOS/Linux, Python 3.9+ (stdlib only — no venv, no pip deps)
- **Docker Desktop or dockerd** (default sandbox as of 0.4) — `docker
  version` must succeed. Missing/unreachable Docker is a preflight error
  with a hint; pass `--sandbox none` to skip this requirement and run
  unsandboxed on the host instead.
- [LM Studio](https://lmstudio.ai) serving its OpenAI-compatible API at
  `localhost:1234` with a tool-calling-capable model loaded. Verified
  working: `qwen/qwen3-coder-next` (65k context, default) and
  `mistralai/devstral-small-2-2512` (32k context)
- `--provider anthropic` needs the `ANTHROPIC_API_KEY` environment variable
  set; the default (`--provider openai`, LM Studio or any OpenAI-compatible
  server) needs no key.
- The target repo must be a git repo with at least one commit

**Other servers:** anything speaking the OpenAI chat-completions API with tool
calling should work via `--base-url` (e.g. Ollama at
`http://localhost:11434/v1`) — see [Platform support](#platform-support) for
what's actually exercised by the test suites. Reports welcome.

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

> See [Security & trust](#security--trust) — docker mode does not protect
> repository-history confidentiality.

- **Watch a run:** `tail -f` the transcript path printed on stderr.
- **Review a run:** `git -C <worktree> diff`, read the transcript, run the
  repo's tests — then commit the branch or discard it. (The worktree is
  only populated after the run ends, once the export step completes.)

  > **The worker cannot install dependencies in docker mode** (`--network
  > none`, no host directories mounted); it can only run what the image
  > ships — git, bash, coreutils, findutils, python3, node/npm, the .NET SDK,
  > ripgrep, jq, uuid-runtime, shellcheck and curl. Always run the repo's own
  > gate yourself on the exported worktree, or pass it as
  > [`--verify`](#verifying-a-run). For a Node repo whose gate needs
  > `node_modules`, symlink your own into the exported worktree for the gate
  > and remove it afterwards — a `node_modules/` gitignore pattern does **not**
  > match a symlink, so a forgotten one shows up as an untracked path. If the
  > gate needs a tool the image lacks, build a derived image (see the recipe
  > next to `--image` in [Machine contract](#machine-contract)).
- **Clean up a run:** `dirtywork runs clean <slug>` — see
  [Inspecting, cleaning up and re-exporting runs](#inspecting-cleaning-up-and-re-exporting-runs)
  for the safety rules and the rest of the `runs` subcommands.

- **All flags, stdout JSON, exit codes, transcript events:** see
  [Machine contract](#machine-contract).

#### Verifying a run

    dirtywork run --repo ~/repos/someproject --verify 'npm test' "Add a unit test for X"

`--verify CMD` makes your gate the harness's gate. The moment the worker
declares itself done — `finish(summary=…)` or a plain answer — dirtywork runs
`CMD` inside the sandbox, through the same `bash` path the worker used (same
guardrails, same `--network none`, same budget watchdog), and **before** the
export. A zero exit leaves the run `completed`; anything else ends it
`verify_failed` (exit 1), with the command, its exit code and a 4000-char
output tail in `verify` on the stdout JSON, the `run_end` event and `run.json`.
`--verify-rounds N` (default 1) is how many fix rounds follow a failure: the
default hands the first failure back to the worker as a message naming the
command, the exit code and the output tail, and lets it try once more against
the ordinary `--max-turns`/`--timeout` budget (the command may run N+1 times);
`0` verifies once and ends the run either way. `--verify-timeout S` (default 600, clamped to
1–600) bounds each run. In docker mode the command can only use what the image
ships — see the callout under *Review a run*. `dirtywork resume` inherits the
verify command from the run it continues.

### Resuming a run

A run that ended early (`max_turns`, `stalled`, `timeout`, `interrupted`, a
crash) keeps its worktree. Continue it on the same worktree and branch:

    dirtywork resume <slug>          # slug from the run's stdout JSON / ~/.dirtywork/runs
    dirtywork resume ~/.dirtywork/runs/<slug> --max-turns 60

A resume is a new run (new slug, transcript, run dir, and — in docker mode —
container and volume) whose task is the original task plus a summary of how
the earlier run ended and the tail of its transcript; the model is told to
`git status`/`git diff` first and continue rather than restart. The sandbox
mode is the original run's; `--model` and the other run flags may be
overridden. Refused (exit 2, nothing created) when the earlier run is still
running, its worktree is gone, or its base commit no longer exists. In docker
mode the prior work is moved aside during the final export and put back if that
export fails, so a failed resume leaves the worktree exactly as it was. If a docker
resume is killed mid-export, that stash (`<worktree>.pre-resume-<slug>`, next to the
worktree) still holds the pre-resume content; `resume` refuses to run again until you
move it back or delete it, and never deletes a stash it did not create.

**Sending review feedback.** `--feedback TEXT` (or `--feedback-file PATH`, a
UTF-8 file, max 64 000 chars; the two are mutually exclusive) turns a resume
into a review round: the resumed task keeps the original brief, then tells the
worker that a reviewer read its work and sent *this*, and to inspect the
worktree with `git status`/`git diff` and apply the feedback and nothing else.

    dirtywork resume <slug> --feedback "You dropped the null check in api.ts; restore it."

Resuming a run that ended `completed` **requires** feedback — without it the
command refuses (exit 2, nothing created), because a completed run that is
continued with its own original brief just re-does work it already declared
done. Every other status resumes with or without feedback, as before. The
feedback text is recorded in the new run's `run.json` (`feedback`) and its
`run_start` event; both resume markers (`--- RESUMED RUN ---` and
`--- RESUMED RUN: REVIEW FEEDBACK ---`) are stripped from the prior task before
a new block is built, so resuming a resume never accumulates preambles.

Docker-mode limit: export stores files, not the worker's in-container
commits, so a resumed docker worker sees the earlier work as uncommitted
changes against the base commit — not as its old commit history. Host mode
(`--sandbox none`) keeps the real commits.

## Inspecting, cleaning up and re-exporting runs

Every run leaves a directory under `~/.dirtywork/runs/<slug>/` (`run.json`,
`transcript.jsonl`, and in docker mode `diff.patch`); the `runs` subcommands
work from that directory (plus best-effort docker/git lookups) independently
of whether the run is still going:

- `dirtywork runs list [--json]` — every run under `~/.dirtywork/runs`: slug,
  status, when it started, its place in a resume chain, branch, whether the
  worktree still exists, and container/volume state.
- `dirtywork runs show <slug> [--diff] [--markdown] [--out FILE]` — the run's
  summary fields, its full `run.json`, and a timeline reconstructed from the
  transcript; `--diff` also prints `diff.patch`. `--markdown` renders the same
  run as a Markdown report instead (header block whose `task` field is a
  one-line preview, a `## Task` section with the full task text, one
  `### Turn N` section per assistant turn, collapsible `<details>` tool
  results, blockquote callouts for nudges/guardrail blocks/sandbox resets, a
  `## Result` section, and with `--diff` the patch in a fenced block) —
  paste-ready for a PR or an issue; `--out FILE` writes it to a file instead
  of stdout.
- `dirtywork runs export <slug> [--max-worktree-mb 2048] [--max-worktree-files 200000] [--max-patch-mb 10] [--keep-volume]` —
  re-runs the docker export into the worktree for a run whose volume still
  exists (after `export_failed`, or a crash before the export ran); refuses
  a non-empty worktree, a still-running run, or a non-docker-sandbox run.
  `--max-worktree-mb`/`--max-worktree-files` default to the same limits as
  `dirtywork run` — raise them here to retry an export that failed because
  the tree was too big.
- `dirtywork runs clean <slug>|--all [--force] [--keep-transcript]` —
  remove a run's container, volume, worktree, branch, and run directory.
  Every refusal is printed and makes the command exit 1:
  - refuses a worktree with uncommitted changes, or a branch that has
    commits beyond the run's recorded base commit — both are what make
    `--allow-commit` runs safe to clean; `--force` removes them anyway.
  - refuses a run that's still marked `running` with a live (or
    unconfirmable) host process — not overridable; once the process is
    confirmed dead, `--force` is required to confirm the cleanup.
  - never deletes a branch unless it's still the one actually checked out
    in the worktree (protects against deleting the wrong branch if you
    checked out something else there by hand).
  - a run whose worktree was taken over by a later `resume`
    (`resumed_by` set) keeps its worktree and branch — they belong to the
    newest run in the chain; clean that run instead.
  - only ever removes the worktree dirtywork itself created for the run
    (`<repo>/.worktrees/dw-<slug>`, a linked worktree of the recorded repo)
    — an edited `run.json` cannot point it at another worktree; a worktree
    that is already gone is not a refusal (git bookkeeping is pruned and the
    run's own `dirtywork/<slug>` branch removed if nothing has it checked out).
  - container/volume removal only ever touches a resource whose
    `dirtywork.run`/`dirtywork.repo` labels match this exact run (the SP2
    collision rule); anything else is left alone and reported. A resource
    that is already gone ("no such object") is fine; any other `docker
    inspect` failure (daemon down, permission denied) is a refusal, and then
    the worktree, branch and run directory are left untouched too, so a
    retry can still finish.
  - if anything above was refused, the run directory is kept too, so
    nothing it describes is lost before you can retry with `--force`.
  - slugs are plain names (`[A-Za-z0-9._-]`); paths are rejected.
  - `--keep-transcript` keeps `run.json`/`transcript.jsonl` and removes
    the rest of the run directory.
- `dirtywork runs verdict <slug> accept|reject|cleanup [--note TEXT] [--review-seconds N]` —
  record the operator's verdict on a run into its `run.json`.
- `dirtywork runs snapshot <slug>` — commit the run worktree's current content
  onto the run's own branch as `wip: dirtywork run <slug>`, then print
  `snapshot <sha> on <branch>` (or `nothing to snapshot` when the tree already
  matches the branch head). Built entirely from git plumbing — no `git add`, no
  `git commit` — so a worker-authored `.gitattributes` plus a configured clean
  filter, and any hook in your repo, are bypassed rather than executed; ignore
  rules are deliberately not applied either, because a wip snapshot is a
  snapshot. Symlinks are recorded by their target string, never followed;
  executable bits are preserved; anything that is not a regular file or a
  symlink is skipped. Refuses (exit 2) a run still going with a live pid, a
  missing worktree, a worktree that is not a linked worktree of the run's repo,
  a worktree with a pre-resume stash beside it, and one the export never
  populated. Mostly you will not call it by hand: `--branch-from @<slug>` calls
  it for you.

## Benchmarking

    dirtywork bench --models 'model[@provider][=base_url],...' \
        [--provider openai] [--base-url URL] [--tasks name1,name2] \
        [--repeats N] [--out PATH] [--max-turns 40] [--timeout 1800]
    dirtywork bench summarize <results.jsonl> [--compare <other.jsonl>]

Runs every (model × task × repeat) combination through the normal
`dirtywork run --sandbox docker --keep-volume` path against the fixture
repos under `bench/repos/` (each with a `bench.json` of expected file
hashes plus an acceptance command), then scores the result: a fresh
acceptance container mounts the run's volume at `/work` and the fixture's
own `bench/repos/<task>/acceptance/` read-only at `/acceptance`, hashes the
worker's copy of `acceptance/` to catch tampering (marked `gamed`), and
runs the acceptance command from the read-only mount to get `pass`/`fail`.
Each row also records harness-failure counts (nudges by kind, stalls,
`max_turns`, `sandbox_error`, aborts). `--models` takes comma-separated
`model[@provider][=base_url]` specs — anything a spec omits falls back to
the sweep-wide `--provider`/`--base-url`, which in turn fall back to
`dirtywork run`'s own defaults. Results append to
`~/.dirtywork/bench/<UTC-timestamp>.jsonl` (or `--out`); `dirtywork bench
summarize <file>` prints a per-case table plus a per-model summary
(completion/acceptance/verdict rates, gamed count, mean tokens/wall time,
median review seconds). `--compare <other.jsonl>` prints two paired
`A -> B (Δ)` tables instead — the per-(model, task) table and the paired
per-model summary — deltas are B minus A, a key only one sweep ran shows
`-` on the other side, the per-(model, task) table's `outcomes` column
breaks the acceptance rate down as `pass/fail/gamed/skipped` per side (count
cells carry a component-wise delta, e.g. `0/0/1/0 -> 1/0/0/0 (+1/0/-1/0)`), and
its `harness` column reads `-` for a side whose rows never ran the harness
(bench_error only) or is suffixed `*` when only some of that side's rows did.

`bench` runs from a source checkout only — `bench/` and its fixture repos
are not part of the installed package.

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
3. **The loop** — the model gets nine tools (`read_file`, `write_file`,
   `edit_file`, `insert_before`, `insert_after`, `list_dir`, `grep`, `bash`,
   `finish`) via OpenAI function-calling. `insert_before`/`insert_after` add
   whole lines around a unique anchor without touching the anchor's own line
   — the primitive for "add a line here", which `edit_file` could only express
   as a replace. Every successful `edit_file`/`write_file`/`insert_*` result
   echoes a capped unified diff of what actually changed, so a replace that
   silently deleted a line is visible to the worker in the same turn.
   Context is budgeted per model (oldest tool results get
   trimmed first); three consecutive tool failures of one kind (malformed
   call, malformed arguments, unknown tool, bad arguments, empty reply) or
   six in total abort the run. The model ends a run by calling the
   `finish(summary=...)` tool (a plain reply with no tool call also ends it);
   an empty, think-only, or truncated reply, or a tool call written as text,
   is sent back with a one-line nudge instead of being treated as completion.
4. **No auto-commit** — changes stay uncommitted in the worktree; the
   transcript lands at `~/.dirtywork/runs/<slug>/transcript.jsonl`
   (outside the worktree, so it can never pollute the diff).

## Safety model

**Docker mode (default):** the container is the real boundary —
`--network none`, `--read-only` root filesystem, `--cap-drop ALL`,
kernel-enforced memory/CPU/process/per-file-size limits, no host path
mounted in except a read-only bind mount of the parent repository's git
object store. The worktree reaches the host only through the validated
tar export. See "Security & trust" above for what this does and does not
cover.
`bash` in docker mode only enforces the mode-independent policy rules (no
`git push`, `sudo`, piping a download into a shell, or system-control
commands) — the host-filesystem/host-repo rules below don't apply, since
the container has no host filesystem or shared parent repo to escape into.

**`--sandbox none` (host mode, pre-0.4 behavior):** guardrails block
**accidents, not adversaries** — the post-run review is the real gate:

- All file tools are path-confined to the worktree (symlink-safe realpath
  checks; `.git/` is write-protected against hook injection).
- `bash` runs cwd-pinned in the worktree with a minimal environment (your
  shell's tokens/keys are not inherited) and a regex denylist: `sudo`,
  `git push`, `git config`/`remote`/`worktree`/`branch -D`/… that would write
  the parent repo's shared state (including through `git -C`/`git -c`/`--flag`
  global options), `rm`/`mv`/`chmod`/`chown` on absolute or `~` paths,
  `cd`/`pushd` escapes (an absolute-path `cd` that lands *inside* the
  worktree is allowed — only paths that leave it are blocked), downloads
  piped to a shell, system-control commands, redirects outside the
  worktree.
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

    python3 -m pytest                    # unit suite (no LM Studio or Docker needed)
    python3 -m pytest -m live -v         # live suite (requires LM Studio running;
                                          # includes a real end-to-end agent run)
    python3 -m pytest -m docker -v       # docker suite (requires a running Docker
                                          # daemon; host-sentinel and lifecycle tests)

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
[`docs/superpowers/bench/`](docs/superpowers/bench/).

In August 2026 the project was renamed **dirtywork** — same tool, a name that says what it does.

## Troubleshooting

- **exit 2, "cannot reach LM Studio"** — server not running; check `lms ps`
  and `curl -s localhost:1234/v1/models`.
- **exit 2, "model not loaded"** — `lms load <model>` (the error names the
  loaded models).
- **exit 2, "ANTHROPIC_API_KEY is not set"** — set that environment variable
  before running with `--provider anthropic`.
- **status `max_turns` / `timeout`** — the worktree is kept; read the
  transcript to see where it stalled, salvage what's useful, or re-run with
  higher limits.
- **status `stalled`** — N turns (`--stall-turns`) passed with no file change
  and no new command output; the worktree is kept. Usually the work is
  done but the model never called `finish` — inspect the worktree, or
  `dirtywork resume <slug>`.
- **status `stuck`** — the same failing `bash` command ran `--stuck-repeats`
  times in a row (default 4) with output that differed only in timing; the
  worktree is kept. The payload's `stuck_on` names the command, its output and
  the repeat count. Usually the worker cannot run the repo's gate at all (a
  missing dependency in docker mode) or is re-running a test it has no way to
  pass — read `stuck_on`, fix the environment or the brief, then
  `dirtywork resume <slug> --feedback "..."`. `--stuck-repeats 0` disables it.
- **status `verify_failed`** — the worker declared itself done but the
  `--verify` command exited non-zero on its last allowed run; the worktree is
  kept and the export still ran. Read `verify.output_tail` in the payload, then
  `dirtywork resume <slug> --feedback "<what to fix>"` — the resume inherits
  the same verify command. In docker mode, check first that the command can run
  at all in the image (`--network none`, nothing installed at run time).
- **host mode (`--sandbox none`): "No module named pytest", or a
  `Library/`/`.cache/` directory appears in the worktree** — bash runs with
  `HOME` set to the worktree on purpose (so `~/.ssh` and friends are out of
  reach), which is where `$HOME`-keyed caches and `pip install --user` land.
  The operator's own user-site packages stay importable (they are put on
  `PYTHONPATH`), and the roots of `$HOME`-keyed toolchain managers are carried
  over (`VOLTA_HOME`, `RUSTUP_HOME`, `CARGO_HOME`, `NVM_DIR`, `PYENV_ROOT` —
  kept when set in your shell, else defaulted to `~/.volta`-style directories
  that exist) so `node`/`cargo` shims do not re-download toolchains into the
  worktree; if a tool still cannot be found, install it system-wide or in the
  project's virtualenv rather than with `pip install --user` from inside a run.
- **status `context_exhausted`** — the task needed more context than the
  model's window; split the task or use the larger-context model.
- **status `budget_exceeded`** — the worktree grew past
  `--max-worktree-mb`/`--max-worktree-files` during a tool call; the
  worktree and branch are kept for salvage. Raise the limit or investigate
  what wrote so much.
- **exit 2, "Docker is the default sandbox since 0.4..."** — Docker
  Desktop/dockerd isn't running or isn't reachable. Start it, or pass
  `--sandbox none` to run unsandboxed on the host.
- **exit 2 with "permission denied while trying to connect to the Docker
  daemon socket"** (Linux) — the daemon is up but your user can't talk to
  it. Either add yourself to the `docker` group (`sudo usermod -aG docker
  $USER`, then log out and back in — `newgrp docker` works for the current
  shell), or run rootless Docker (`dockerd-rootless-setuptool.sh install`,
  then `DOCKER_HOST=unix://$XDG_RUNTIME_DIR/docker.sock`). Verify with
  `docker version` before retrying; dirtywork uses the same `docker` CLI
  and socket you do. On macOS/Windows Docker Desktop this doesn't apply
  (Desktop owns the socket for your user); if Desktop shows running but
  `docker version` fails, `DOCKER_HOST` or a stale `~/.docker/config.json`
  context is the usual culprit (`docker context ls`).
- **`docker: command not found` / "Cannot connect to the Docker daemon"
  from inside a run's `bash` tool** — expected in docker mode: the worker
  has no docker socket and no network by design (the container mounts only
  the run's volume and the read-only object store; `--network none`).
  In host mode the worker inherits your PATH, so this means the same
  socket problem as above.
- **exit 2, "Build or pull the worker image..."** — the configured
  `--image` couldn't be resolved (not pullable, or a digest mismatch
  against `PINNED_DIGEST`). Build/pull it per `docker/README.md`, pass a
  different `--image`, or use `--sandbox none`.
- **exit 2, "Check that the repository's git object store is valid..."**
  — `--repo`'s git object store failed validation (a symlinked or missing
  `objects` directory, or one that escapes the git common dir). Verify
  the repo with `git -C <repo> fsck`, or use `--sandbox none`.
- **status `sandbox_error`** — a docker command failed or timed out mid-run
  (daemon hang, container killed unexpectedly twice in a row, etc.); the
  worktree may be partially or not exported. Check `run_end.error` in the
  transcript and `docker ps -a --filter label=dirtywork.run=<slug>`.
- **status `export_failed` (in `run.json`'s `export_status`, and as the
  overall `status` if the agent loop itself otherwise completed)** — the
  worker's tree could not be validated/exported (e.g. it exceeded
  `--max-worktree-mb`/`--max-worktree-files`). The Docker volume
  `dw-<slug>-work` is kept (unless it was already going to be removed).
  Retry the export in place with `dirtywork runs export <slug>
  --max-worktree-mb <n> --max-worktree-files <n>` after raising the limit
  that tripped it (see [Inspecting, cleaning up and re-exporting
  runs](#inspecting-cleaning-up-and-re-exporting-runs)). To salvage the
  tree by hand instead, inspect the volume directly (`docker run --rm -v
  dw-<slug>-work:/work <image> ...`), or discard it with `docker volume rm
  dw-<slug>-work` once you're done.

## Machine contract

`dirtywork` is built to be driven by another agent (Claude Code) rather than
read by a human — the primary consumer parses stdout, not the terminal.

**Flags:**

```
dirtywork run --repo <path> "<task>"
    [--model qwen/qwen3-coder-next]   # or mistralai/devstral-small-2-2512
    [--branch-from <ref>|@<slug>]     # default: repo HEAD; @<slug> = an earlier run's branch
    [--max-turns 40]
    [--stall-turns 12]                # end as `stalled` after N no-progress turns; 0 disables
    [--stuck-repeats 4]               # end as `stuck` after N identical failing bash runs; 0 disables
    [--verify "<cmd>"]                # run this in the sandbox on completion; non-zero → `verify_failed`
    [--verify-rounds 1]               # fix rounds after a failed --verify (0 = verify once, no retry)
    [--verify-timeout 600]            # seconds per --verify run, clamped to 1-600
    [--context-window <tokens>]       # default: built-in table, else 32768 (+ stderr warning)
    [--timeout 1800]                  # whole-run wall clock, seconds
    [--temperature <f>]               # omitted by default → server preset
    [--provider openai|anthropic]     # default: openai; anthropic needs ANTHROPIC_API_KEY
    [--base-url <url>]                # default depends on --provider (LM Studio for openai,
                                       # https://api.anthropic.com for anthropic)
    [--max-worktree-mb 2048]
    [--max-worktree-files 200000]
    [--sandbox docker|none]           # default: docker
    [--image ghcr.io/jimboschneider/dirtywork-worker:0.8]  # docker mode only
    [--allow-network]                 # docker mode only; default --network none
    [--memory 4g]                     # docker mode only
    [--cpus 2]                        # docker mode only
    [--tmp-size 1g]                   # docker mode only
    [--gitdir-size 512m]              # docker mode only
    [--min-free-mb 2048]              # docker mode only; host free-space floor
    [--keep-volume]                   # docker mode only; skip volume cleanup
    [--max-patch-mb 10]               # docker mode only; diff.patch cap
    [--allow-commit]                  # host mode only; worker commits its own work
```

```
dirtywork resume <slug | run-dir>     # same flags as run, minus --repo/--branch-from/--sandbox/<task>;
    [--model <m>]                     # defaults to the earlier run's model; --image defaults to its image
    [--feedback "<text>"]             # reviewer instructions; REQUIRED to resume a `completed` run
    [--feedback-file <path>]          # same, read from a UTF-8 file (max 64000 chars)
```

- `--branch-from @<slug>` — start the new run from the branch an earlier run
  left behind instead of from repo HEAD. If that run's worktree still has
  uncommitted work, dirtywork snapshots it first (`dirtywork runs snapshot`'s
  plumbing-only commit — no filters, no hooks) and prints
  `snapshot <sha> on <branch> (from @<slug>)` on stderr, so the new run starts
  from the work as the reviewer actually saw it, not from the last commit.
  Unknown slug, or a run whose `run.json` records no branch, is a preflight
  refusal (exit 2, nothing created). The resolved branch NAME is what
  `run_start.branch_from` records; `run.json` also records
  `branch_from_run: "<slug>"`. This is the "start a fresh run from what that
  one produced" half of the review→fix loop — the other half is
  `dirtywork resume <slug> --feedback "..."`, which continues the same run on
  the same worktree.

- `--image REF` (docker mode) — the worker image, default
  `ghcr.io/jimboschneider/dirtywork-worker:0.8`. The image is the worker's
  whole toolchain: with `--network none` and no host mounts, nothing can be
  installed during a run. To add a tool, derive an image once:

  ```Dockerfile
  FROM ghcr.io/jimboschneider/dirtywork-worker:0.8
  USER root
  RUN apt-get update && apt-get install -y --no-install-recommends <packages> \
      && rm -rf /var/lib/apt/lists/*
  USER worker
  ```

  then `docker build -t my-worker:0.8 .` and `--image my-worker:0.8`. A custom
  `--image` is never digest-pinned — `PINNED_DIGEST` protects the maintained
  default image only.

- `--stall-turns N` (default 12) — end the run with status `stalled` after N
  consecutive turns that changed no file and produced no new command output;
  the model gets one nudge halfway. `0` disables.
- `--stuck-repeats N` (default 4) — end the run with status `stuck` after the
  same **failing** `bash` command has run N times in a row. "Same" uses the
  stall detector's own fingerprint (command plus output with timings, clock
  times and git shas stripped), so a rerun that differs only in duration
  counts; a passing run (`exit code: 0`) resets the streak to zero. Edits
  between the reruns do **not** reset it — that is the loop `--stall-turns`
  cannot see, since every `edit_file` counts as progress. No nudge is sent:
  the point is to stop paying for turns. `0` disables.
- `--verify CMD` / `--verify-rounds N` / `--verify-timeout S` — see
  [Verifying a run](#verifying-a-run). `--verify-rounds` counts **fix rounds
  after a failed verify** — the command may run N+1 times; `0` verifies once and
  ends the run either way. `dirtywork resume` inherits all three — the command,
  the rounds, and the timeout — from the run it continues (recorded in `run.json`
  at run start, so this works even when the prior run ended before verify ever
  ran, e.g. `max_turns`/`stalled`/`stuck`/`timeout`/`budget_exceeded`); an
  explicit flag on `resume` overrides the inherited value.
- `--context-window TOKENS` — the model's context window, used to size the
  transcript trimming budget. Precedence: flag, then `DIRTYWORK_CONTEXT_WINDOW`,
  then a built-in table for the known LM Studio models, then 32768 (with a
  warning on stderr).

- `--allow-commit` (host mode only) — replaces the prompt's "leave all changes
  uncommitted for review" rule with "commit your work in small conventional
  commits as you go", so the run's branch comes back as real history instead of
  a dirty worktree. Rejected in preflight with `--sandbox docker`: the export
  carries files, not commits (its archive can never contain a `.git` entry), so
  a container's commits could not reach the host anyway. `dirtywork resume`
  inherits the setting from the run it continues.

**stdout:** on any run that gets past preflight, exactly one JSON object is
printed to stdout (nothing else goes to stdout):

```json
{
  "schema_version": 2,
  "status": "completed",
  "worktree": "/path/to/repo/.worktrees/dw-<slug>",
  "branch": "dirtywork/<slug>",
  "transcript": "/path/to/transcript.jsonl",
  "turns": 7,
  "usage": {"prompt_tokens": 0, "completion_tokens": 0},
  "final_message": "...",
  "provider": "openai",
  "run_dir": "/home/you/.dirtywork/runs/<slug>",
  "base_commit": "abc123...",
  "resumed_from": null,
  "finalize_error": null,
  "watchdog_violation": null,
  "watchdog_violation_kind": null,
  "stuck_on": null,
  "files_changed": ["web/src/lib/api.ts", "web/src/lib/api.test.ts"],
  "files_changed_truncated": false,
  "last_tool_result": {
    "tool": "bash",
    "args": "{\"command\": \"npm test\"}",
    "result": "exit code: 0\n12 passing"
  },
  "last_assistant_text": "Added the retry and a test for it.",
  "verify": {
    "command": "npm test",
    "exit_code": 0,
    "output_tail": "exit code: 0\n12 passing",
    "rounds": 1,
    "passed": true
  }
}
```

The last six keys are 0.8 additions (`stuck_on`, `files_changed`,
`files_changed_truncated`, `last_tool_result`, `last_assistant_text`,
`verify`). Every one of them is present on every normal end-of-run payload:
`null` when it does not apply, `[]`/`false` for the list and its flag.

`status` is one of: `completed`, `max_turns`, `timeout`, `stalled`, `stuck`,
`verify_failed`, `context_exhausted`, `model_error`, `interrupted`,
`budget_exceeded`, `sandbox_error`, `export_failed`. When the run fails before a `RunResult`
exists — the LLM client raises, post-worktree setup fails (e.g. the
transcript can't be created), or any other exception escapes the run
(status `model_error` in every case) — `turns` is `null` and `usage` is
`{}`, but `status`, `worktree`, `branch`, `transcript`, and `run_dir` are
still populated so the worktree and run directory can be located for
salvage.

`base_commit` and `provider` (`"openai"` or `"anthropic"`) are present on
every post-preflight payload. `resumed_from` is
the slug of the run this one continued, or `null` if this was a fresh run.
`finalize_error`, `watchdog_violation`, `watchdog_violation_kind`, `stuck_on`,
`files_changed`, `files_changed_truncated`, `last_tool_result` and
`last_assistant_text` are added on the normal
end-of-run path — i.e. whenever `runner.run()` returns a result, `completed`
or not — normally `null` (`[]`/`false` for the two list/flag fields); see
`run_end` below for what each means. The last four are there so a run that
ends with an empty `final_message` is still triageable without opening the
transcript: what it changed, what it last ran and what it last said. On a
`completed` run they are just as useful — "the last thing the worker checked
failed, and it called `finish` anyway" reads straight off `last_tool_result`.
The two
paths where `runner.run()` never returns (sandbox setup fails before it
starts, or an exception escapes the loop and is caught in `main()`) report
`base_commit` and `resumed_from` only, plus `export_status` too if a docker `finalize()` ran
during that exception recovery.

**Exit codes:**

- `0` — `completed`.
- `1` — any non-`completed` status (`max_turns`, `timeout`, `stalled`,
  `stuck`, `verify_failed`, `context_exhausted`, `model_error`, `interrupted`,
  `budget_exceeded`, `sandbox_error`, `export_failed`); the worktree and branch are kept for
  salvage/review. `main` catches every `Exception` the run raises (not
  just ones the runner itself converts to a status) and reports
  it as `model_error` via the same JSON contract, so a post-preflight run
  never tracebacks. (Ctrl-C is a `KeyboardInterrupt`, a `BaseException`, not caught
  here — but the run loop itself already converts in-loop Ctrl-C to status
  `interrupted` before it would reach this point.)
- `2` — preflight or environment error (LM Studio unreachable, model not
  loaded, `--repo` not a git repo, etc.); nothing is created.

All progress (transcript path, worktree path, `error:`-prefixed messages) is
written to stderr; watch a live run with `tail -f` on the transcript path.

Full field-by-field schema, including every v1→v2 addition and the
`run.json` field list: [`docs/transcript-schema.md`](docs/transcript-schema.md).

**Transcript events** (JSONL, one per line): `run_start` (task, repo, model,
config, `schema_version: 2`, plus provenance: `worktree`, `base_commit`,
`branch`, `branch_from`, `base_url`, `dirtywork_version`, `temperature`,
`sandbox` — the docker settings dict, or `"none"` — and `provider`,
`context_window`, `resumed_from`),
`assistant` (text + tool calls — text capped at 64 000 chars in the
transcript only, the full text is still sent to the model), `tool_result`
(truncated), `guardrail_block`, `nudge` (`{"event": "nudge", "kind":
"truncated|empty|text_tool_call|stall", "turn": N}`), `sandbox_reset`
(docker mode: the container was reset — reason), and `run_end` (status, turns,
duration, cumulative
usage, plus the run's artifacts: in host mode `diff_stat` — `git diff
--stat` against the base commit, tracked changes only — and `untracked` —
`git status --porcelain` `??` entries — each capped at 64 000 chars; in
docker mode `diff_stat` (which already includes new files, since the
export stages everything first), `untracked` (always `""`), `patch_path`,
`worktree_bytes`, `worktree_files`, `escaping_symlinks`,
`dropped_git_entries`, `export_status`, `watchdog_violation` (docker mode;
null unless the watchdog killed the container), `watchdog_violation_kind`
(set alongside `watchdog_violation`: `"budget"` for a worktree-size or
host-disk-floor breach, `"sandbox_error"` for the watchdog's own
worktree-sampling exec failing twice; otherwise `null`), and
`finalize_error` (set when the finalize/export step itself raised an
exception after the agent loop otherwise finished; `null` normally)).
A `finish(summary=...)` call appears in the transcript as an ordinary tool call in its `assistant` event followed by a `tool_result` event whose `result` is `run finished`; the summary becomes the run's `final_message`.

The docker settings dict (`run_start`'s `sandbox`, and the same fields in
`run.json`) includes `image` (the `--image` argument as given),
`image_digest` (the registry digest from `RepoDigests`, or `null` for a
locally-built image that was never pushed/pulled) — provenance only — and
`image_pinned` (`true` only when `--image` was left at its default AND
`PINNED_DIGEST` was enforced against a pulled default image; `false` for a
custom `--image` — never pinned — or a locally built/loaded default image,
which only warns). `run.json` also records the run's key fields: `task`,
`model`, `context_window`, `resumed_from`, and `turns` (at the end); when a
run is resumed, the earlier run's `resumed_by` field records the slug of the new run that continued it.
The container itself always runs from the image's local
content-addressed Id, never a registry digest, so a run can never trigger a
network pull.

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
