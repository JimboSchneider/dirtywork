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
Every tool call (`read_file`/`write_file`/`edit_file`/`list_dir`/`grep`/
`bash`) runs inside a locked-down container: `--network none` by default,
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
- A malicious target repo's `CLAUDE.md`/`AGENTS.md` (read from the base
  commit via git, not the filesystem — symlinks and oversized files are
  rejected) is injected into the worker's prompt; treat untrusted repos'
  documentation as you would untrusted code.

**Practical guidance:** run dirtywork against models and repositories
you'd trust with the equivalent of a locked-down container on your
machine. Read the transcript and diff before you merge — that review is
still the real gate for *what a run produced*, even though docker mode now
also gates *what a run could do to your host while producing it*.

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

> See [Security & trust](#security--trust) — docker mode does not protect
> repository-history confidentiality.

- **Watch a run:** `tail -f` the transcript path printed on stderr.
- **Review a run:** `git -C <worktree> diff`, read the transcript, run the
  repo's tests — then commit the branch or discard it. (The worktree is
  only populated after the run ends, once the export step completes.)
- **Discard a run:**
  `git -C <repo> worktree remove --force <worktree> && git -C <repo> branch -D dirtywork/<slug>`

- **All flags, stdout JSON, exit codes, transcript events:** see
  [Machine contract](#machine-contract).

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

Docker-mode limit: export stores files, not the worker's in-container
commits, so a resumed docker worker sees the earlier work as uncommitted
changes against the base commit — not as its old commit history. Host mode
(`--sandbox none`) keeps the real commits.

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
3. **The loop** — the model gets seven tools (`read_file`, `write_file`,
   `edit_file`, `list_dir`, `grep`, `bash`, `finish`) via OpenAI
   function-calling. Context is budgeted per model (oldest tool results get
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
- **status `max_turns` / `timeout`** — the worktree is kept; read the
  transcript to see where it stalled, salvage what's useful, or re-run with
  higher limits.
- **status `stalled`** — N turns (`--stall-turns`) passed with no file change
  and no new command output; the worktree is kept. Usually the work is
  done but the model never called `finish` — inspect the worktree, or
  `dirtywork resume <slug>`.
- **host mode (`--sandbox none`): "No module named pytest", or a
  `Library/`/`.cache/` directory appears in the worktree** — bash runs with
  `HOME` set to the worktree on purpose (so `~/.ssh` and friends are out of
  reach), which is where `$HOME`-keyed caches and `pip install --user` land.
  The operator's own user-site packages stay importable (they are put on
  `PYTHONPATH`); if a tool still cannot be found, install it system-wide or in
  the project's virtualenv rather than with `pip install --user` from inside a
  run.
- **status `context_exhausted`** — the task needed more context than the
  model's window; split the task or use the larger-context model.
- **status `budget_exceeded`** — the worktree grew past
  `--max-worktree-mb`/`--max-worktree-files` during a tool call; the
  worktree and branch are kept for salvage. Raise the limit or investigate
  what wrote so much.
- **exit 2, "Docker is the default sandbox since 0.4..."** — Docker
  Desktop/dockerd isn't running or isn't reachable. Start it, or pass
  `--sandbox none` to run unsandboxed on the host.
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
  `dw-<slug>-work` is kept (unless it was already going to be removed) —
  there is no automated recovery command in this release. Either inspect
  it directly (`docker run --rm -v dw-<slug>-work:/work <image> ...`) to
  salvage the tree by hand, or discard it with
  `docker volume rm dw-<slug>-work` once you're done, and re-run the task
  with a higher `--max-worktree-mb`/`--max-worktree-files`.

## Machine contract

`dirtywork` is built to be driven by another agent (Claude Code) rather than
read by a human — the primary consumer parses stdout, not the terminal.

**Flags:**

```
dirtywork run --repo <path> "<task>"
    [--model qwen/qwen3-coder-next]   # or mistralai/devstral-small-2-2512
    [--branch-from <ref>]             # default: repo HEAD
    [--max-turns 40]
    [--stall-turns 12]                # end as `stalled` after N no-progress turns; 0 disables
    [--context-window <tokens>]       # default: built-in table, else 32768 (+ stderr warning)
    [--timeout 1800]                  # whole-run wall clock, seconds
    [--temperature <f>]               # omitted by default → server preset
    [--base-url http://localhost:1234/v1]  # LM Studio's OpenAI-compatible endpoint
    [--max-worktree-mb 2048]
    [--max-worktree-files 200000]
    [--sandbox docker|none]           # default: docker
    [--image ghcr.io/jimboschneider/dirtywork-worker:0.4]  # docker mode only
    [--allow-network]                 # docker mode only; default --network none
    [--memory 4g]                     # docker mode only
    [--cpus 2]                        # docker mode only
    [--tmp-size 1g]                   # docker mode only
    [--gitdir-size 512m]              # docker mode only
    [--min-free-mb 2048]              # docker mode only; host free-space floor
    [--keep-volume]                   # docker mode only; skip volume cleanup
    [--max-patch-mb 10]               # docker mode only; diff.patch cap
```

```
dirtywork resume <slug | run-dir>     # same flags as run, minus --repo/--branch-from/--sandbox/<task>;
    [--model <m>]                     # defaults to the earlier run's model; --image defaults to its image
```

- `--stall-turns N` (default 12) — end the run with status `stalled` after N
  consecutive turns that changed no file and produced no new command output;
  the model gets one nudge halfway. `0` disables.
- `--context-window TOKENS` — the model's context window, used to size the
  transcript trimming budget. Precedence: flag, then `DIRTYWORK_CONTEXT_WINDOW`,
  then a built-in table for the known LM Studio models, then 32768 (with a
  warning on stderr).

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
  "run_dir": "/home/you/.dirtywork/runs/<slug>",
  "base_commit": "abc123...",
  "resumed_from": null,
  "finalize_error": null,
  "watchdog_violation": null,
  "watchdog_violation_kind": null
}
```

`status` is one of: `completed`, `max_turns`, `timeout`, `stalled`,
`context_exhausted`, `model_error`, `interrupted`, `budget_exceeded`,
`sandbox_error`, `export_failed`. When the run fails before a `RunResult`
exists — the LLM client raises, post-worktree setup fails (e.g. the
transcript can't be created), or any other exception escapes the run
(status `model_error` in every case) — `turns` is `null` and `usage` is
`{}`, but `status`, `worktree`, `branch`, `transcript`, and `run_dir` are
still populated so the worktree and run directory can be located for
salvage.

`base_commit` is present on every post-preflight payload. `resumed_from` is
the slug of the run this one continued, or `null` if this was a fresh run.
`finalize_error`, `watchdog_violation`, and `watchdog_violation_kind` are added on the normal
end-of-run path — i.e. whenever `runner.run()` returns a result, `completed`
or not — normally `null`; see `run_end` below for what each means. The two
paths where `runner.run()` never returns (sandbox setup fails before it
starts, or an exception escapes the loop and is caught in `main()`) report
`base_commit` and `resumed_from` only, plus `export_status` too if a docker `finalize()` ran
during that exception recovery.

**Exit codes:**

- `0` — `completed`.
- `1` — any non-`completed` status (`max_turns`, `timeout`, `stalled`,
  `context_exhausted`, `model_error`, `interrupted`, `budget_exceeded`,
  `sandbox_error`, `export_failed`); the worktree and branch are kept for
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
