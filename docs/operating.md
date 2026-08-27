# Operating dirtywork

Day-to-day usage once dirtywork is installed: running a task, reviewing and resuming a run, the `runs` subcommands, benchmarking, and troubleshooting.

## Use

    dirtywork run --repo ~/repos/someproject "Add a unit test for X"

> See [Security & trust](security.md#security--trust) — docker mode does not protect
> repository-history confidentiality.

- **Watch a run:** `tail -f` the transcript path printed on stderr.
- **Review a run:** `git -C <worktree> diff`, read the transcript, run the
  repo's tests — then commit the branch or discard it. (The worktree is
  only populated after the run ends, once the export step completes.)

  > **The worker cannot install dependencies in docker mode** (`--network none`,
  > no host directories mounted); it can only run what the image ships — git,
  > bash, coreutils, findutils, python3, node/npm, the .NET SDKs (8.0 and 10.0
  > from the `:0.11` image; the old `:0.10` image had 8.0 only), ripgrep, jq,
  > uuid-runtime, shellcheck and curl. Always run the repo's own gate yourself
  > on the exported worktree, or pass it as [`--verify`](#verifying-a-run). For
  > a Node repo whose gate needs `node_modules`, symlink your own into the
  > exported worktree for the gate and remove it afterwards — a `node_modules/`
  > gitignore pattern does **not** match a symlink, so a forgotten one shows up
  > as an untracked path. If the gate needs a tool the image lacks, build a
  > derived image (see the recipe next to `--image` in [Machine
  > contract](https://github.com/JimboSchneider/dirtywork/blob/main/dirtywork/contract/machine-contract.md#machine-contract)).
- **Clean up a run:** `dirtywork runs clean <slug>` — see
  [Inspecting, cleaning up and re-exporting runs](#inspecting-cleaning-up-and-re-exporting-runs)
  for the safety rules and the rest of the `runs` subcommands.

- **All flags, stdout JSON, exit codes, transcript events:** see
  [Machine contract](https://github.com/JimboSchneider/dirtywork/blob/main/dirtywork/contract/machine-contract.md#machine-contract).

#### Editing files

The worker changes files only through tools — `write_file`, `append_file`,
`edit_file`, `apply_edits`, `insert_before`, `insert_after` — never through
`bash`. A file larger than one reply is `write_file` for the first part and
`append_file` for each part after it; `append_file` adds text verbatim to the
end of an EXISTING file, inserting nothing between the old content and the
new, so `text` needs a leading newline when the file does not end with one.
When a
brief lists several exact replacements in one file, say so and expect one
`apply_edits` call rather than a run of `edit_file` calls: the edits are applied
**in order on the running text** (edit 3 may depend on what edit 1 produced),
each `old` must match exactly once at its turn, and the whole batch is
all-or-nothing before the write — the first failure writes nothing and the
result names it. That is one turn instead of five, and one prompt-cache hit
instead of five, which is most of the difference on a small local model.

> **File writes are atomic as of 0.10.** Every refusal — validation, a
> non-matching `old`, an unreadable or non-UTF-8 file, a result over the 5 MB
> write limit — still happens before the file is opened. And now the write
> itself is staged: `write_file`, `append_file`, `edit_file`, `apply_edits` and
> `insert_*` write into a sibling `.dw-tmp.<name>.<8 hex>` file and promote it
> with an atomic rename, so an I/O error or a kill mid-write leaves the target
> byte-identical instead of truncated. The file's mode is carried across the
> promote (an executable stays executable). Two exceptions keep the old
> behaviour on purpose: a target with more than one hard link is written
> through the shared inode, because that is what a hardlink is *for*; and a
> target in a directory dirtywork cannot write is written in place, because a
> rename is impossible there. In docker mode there is no fd fallback: a
> writable file inside an unwritable directory refuses (Permission denied)
> where host mode writes in place. The promote changes the inode, so a
> background process the worker left holding the file open keeps seeing the
> old content. Recovery for a genuinely bad write is still `git -C <worktree>
> checkout -- <path>` — the worktree is a scratch branch.

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
default hands the first failure back to the worker — as the `finish` call's
own result when it finished through `finish(summary=…)`, or as the next
message when it answered in prose — naming the command, the exit code and the
output tail, and lets it try once more against the ordinary
`--max-turns`/`--timeout` budget (the command may run N+1 times);
`0` verifies once and ends the run either way. `--verify-timeout S` (default 600, clamped to
1–600) bounds each run. In docker mode the command can only use what the image
ships — see the callout under *Review a run*. `dirtywork resume` inherits the
verify command from the run it continues.

#### The bash tool

`bash(command, timeout=120)` runs one shell command in the worktree. The
per-call `timeout` is the model's to set (default 120 s, maximum 600 s, clamped;
`--timeout` is the separate whole-run wall clock). A command that hits its
timeout is killed (host mode kills its whole process group; in docker mode the
`docker exec` is abandoned, which is why the result is *unknown*, not *failed*)
and returns exactly:

    ERROR: command timed out after 120s — it did not finish and its result is unknown. Re-run it with a larger timeout (up to 600) or split it into smaller commands; do not report it as passed.

**No partial output is appended**, deliberately: the host backend could produce
a tail and the container backend cannot, and a tail is exactly what a small
model reads as "the command's result" when the command never finished. The same
turn also carries a one-line nudge — appended to the turn's last tool result,
so no chat template sees a user message after a tool result (1.0, #60) —
telling the worker in words that the result is unknown and must not be
reported as a pass, the `tool_result` transcript event
carries `timed_out: true`, and the run's `timeouts` counter rises — visible on
the stdout JSON, in `run_end`, in `run.json`, in `dirtywork runs show`
(where the call renders `[timed out]` rather than `[ERROR]`), and in
`dirtywork bench summarize` (a `timeouts=N` token in the FAILURES column, and
the fourth number of the `--compare` harness cell).

A timeout is **not** counted as a model failure: it does not feed the
consecutive-failure abort, and it resets that streak like any other tool call
that executed. It does count toward `--stuck-repeats` — the same command timing
out four times in a row ends the run `stuck`, which is the honest outcome.

In docker mode, only a real expired timeout renders that way; any other docker
failure (a killed container, an exec that could not start) returns
`ERROR: bash failed: …` instead, so an ordinary failure is never read as "it
might still be running".

**Background processes, and git inside the sandbox.** A process cannot outlive
the `bash` call that started it. Host mode SIGKILLs the command's process group;
docker mode (since 1.0, #61) kills every process but the container's tether in
place — a timed-out command's abandoned process included — records them in a
`stray_kill` transcript event, sweeps any stale git lock they left, and tells the
worker on the same turn (`The sandbox killed N background process(es) …`). The
container, its `/tmp` and the worker's git metadata in `/gitdir` all survive a
kill — a `git stash` made before the call pops afterwards — but `/tmp` is not
reclaimed either: a stray that filled it leaves it full until the worker cleans
it or a reset happens. Only when the kill cannot be performed or verified (a
process flood at the pids limit, an unreachable container) or the container ran
out of memory is the container **reset**: the working tree survives, the index,
stashes, local commits and branches are re-initialized from the base commit,
and the worker is told exactly that. The one thing the reaper cannot see is a
background process whose `docker top` command is exactly `cat`; it is not
reported, though it dies with any other stray. Git inside the sandbox behaves
like git anywhere: the worktree is found through the gitfile `/work/.git` →
`/gitdir`, nothing the worker runs inherits `GIT_DIR`/`GIT_WORK_TREE`, and a
`git init` in a temp dir stays there. A nested repository the worker creates
under the worktree (`cargo new`, a fixture, a `git init` in a sub-project) is
exported as plain files — its `.git` entry is dropped and listed in
`dropped_git_entries`, its files (with its own ignore rules plus the top-level
`.gitignore`) come out like any others.

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
into a review round. The resumed task keeps the original brief, then (1.0)
appends the tail of the earlier run's transcript, the status it ended with,
the reviewer's feedback — framed as *not yet applied* — and a closing
sentence telling the worker to inspect the worktree with `git status`/`git
diff`, apply every item of the feedback and run the check it names, and
nothing else, then call `finish(summary=...)`.

    dirtywork resume <slug> --feedback "You dropped the null check in api.ts; restore it."

The harness will not accept a completion (`finish`, or a plain answer with no
tool call) that changed nothing in the worktree since the resumed run
started (1.0, #66): the first such completion is refused once, with the
reason as its own result or the next user message, and the run continues; a
second one ends the run `unchanged` — exit 1, nothing verified. Resuming a
run that ended `completed` **or `unchanged`** requires feedback — without it
the command refuses (exit 2, nothing created), because a plain resume strips
the feedback block and would let the same non-work end `completed`/`unchanged`
again with nothing new to apply. Every other status resumes with or without
feedback, as before. The feedback text is recorded in the new run's
`run.json` (`feedback`) and its
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
  results, blockquote callouts for nudges/guardrail blocks/sandbox resets/stray kills,
  the harness text a tool result carried to the model (1.0: a fenced
  "harness → model" block), `(sent as: [empty reply])` on a turn the harness
  had to pad, a `## Result` section, and with `--diff` the patch in a fenced
  block) —
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
  filter, and any hook in your repo, are bypassed rather than executed; the
  repo's ignore rules ARE applied, the same way `git add -A` would apply them
  (`git check-ignore`, config-neutral — a tracked file matching an ignore
  pattern is still kept). Symlinks are recorded by their target string, never
  followed; executable bits are preserved; anything that is not a regular file
  or a symlink is skipped. Refuses (exit 2) a run still going with a live pid, a
  missing worktree, a worktree that is not a linked worktree of the run's repo,
  a worktree with a pre-resume stash beside it, and one the export never
  populated. Mostly you will not call it by hand: `--branch-from @<slug>` calls
  it for you.

## Sizing the context window

**One slot loaded with the largest context your machine holds beats more slots
with smaller ones.** These numbers are from the SP3 build record
(`docs/superpowers/bench/2026-08-17-sp3-worker-scoreboard.md` and
`-run-split.md`), measured on a 128 GB Apple Silicon machine with LM Studio
serving `qwen/qwen3-coder-next`:

| Loaded context | Per turn | Prompt throughput | Outcome on a 1,084-line brief |
|---|---|---|---|
| 65k | 15–17 s | ~3k tok/s | `context_exhausted` twice — the per-turn trim invalidated the prompt cache, so every turn re-read the whole history from scratch |
| 131k | 2.6–5 s | ~13k tok/s | no exhaustion |

The 65k number is not a slow model; it is a *cache-miss* number. Once the
history stops fitting, dirtywork trims the oldest tool results every turn, the
prompt prefix changes every turn, and the server re-processes it every turn.
The fix is a bigger window, not a faster machine.

Two 131k slots do **not** fit on 128 GB: loading the second one crashed LM
Studio (55.9 GB wired, 1.2 GB free just before the crash), while a single 131k
slot peaks around 66 GB wired. Load one:

    lms load qwen/qwen3-coder-next -c 131072

dirtywork asks the server what it actually loaded (LM Studio's
`GET /api/v0/models` reports `loaded_context_length`) and uses that, so you do
not have to repeat the number as `--context-window`. The run records where the
value came from in `context_window_source` — `provider:openai:server` when the
server answered, `provider:openai` when the built-in table did, `flag`/`env`
when you said so, `default` when nothing knew. `--provider ollama` is probed
too, with `GET /api/ps` — the `context_length` it reports is the loaded
`num_ctx` and moves when a chat sets `options.num_ctx` — and shows up as
`provider:ollama:server`. There is no static table for Ollama, so a model that
is not resident goes straight to `default` (32768): Ollama's `/v1/models` lists
models you have *pulled*, not models it has *loaded*, so preflight cannot tell.
Run `ollama run <model>` before the run (or pass `--context-window`) or Ollama
will load its own, usually smaller, `num_ctx` and truncate server-side without
telling anyone.

**Ollama quickstart:**

    ollama run gemma4:latest            # make it resident first
    dirtywork run --provider ollama --model gemma4:latest \
      --repo ~/repos/someproject "Add a unit test for X"

Some Ollama builds load a model with a small default `num_ctx` (4096 or
lower); if the probed window comes back at or below 8192 the flat
`--max-tokens` refusal exits 2 (it defaults to 8192, and the check is
`max_tokens >= window`), so on such setups pass `--max-tokens` below the
loaded window — or raise `num_ctx` before loading.

The full tag is required — `gemma4` and `gemma4:latest` are different ids to
Ollama, and `--model` must match what `/v1/models` lists. Parallel tool calls
are not verified on Ollama; if a model emits them, dirtywork parses them the
same way it parses LM Studio's.

The window is shared between the prompt and the reply, so `--max-tokens`
(default 8192) is subtracted from it before the prompt budget is computed:
`(window - max_tokens) * 0.75 * 4` characters. At the 32768 default that is
about 18.4k tokens' worth of prompt, versus the cap-blind 24.5k before 0.10 —
real slack instead of a reply that runs off the end. Raising `--max-tokens`
buys longer single replies at the cost of prompt room, and decode is the slow
half on a local model: a cap you never reach costs nothing, but a cap you do
reach costs seconds per turn. Lower it when a model rejects it (some older
Claude models cap output at 4096) or when you would rather spend the window on
context than on one long reply. The refusal is flat, with no small-window
exemption: a server-reported context window at or below 8192 refuses every run
until you pass `--max-tokens` and lower it below that window.

When a reply does hit the cap (1.0, #65), the harness no longer just says
content was cut: the message states the `--max-tokens` cap and a per-call
target size (characters and lines) to stay under next time. Truncations are
counted for the whole run and never reset; six cut-off replies end it
`model_error`. A cut-off reply counts only toward that budget: it takes no
empty-reply or malformed-arguments strike, so the three-consecutive-failure
rule is for replies that are genuinely empty and calls that are malformed
without being cut.

**Rules of thumb**

- Keep a dispatched brief under ~450 lines. Past roughly 20% of the window
  dirtywork prints a `warning: the task text is ~N tokens, P% of the …` line on
  stderr; that is the same signal, earlier.
- Bias briefs toward whole-file writes and `apply_edits` batches rather than
  long prose: the model re-reads the task every turn, so a compact brief is
  cheaper on every turn, not just the first.
- Watch `trimmed_turns` on the run's stdout JSON (and in `dirtywork runs show`).
  A run with a non-zero count paid the cache-miss tax on that many turns; a run
  with a large one wanted a bigger window or a smaller brief.

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
- **status `unchanged`** (1.0, #66) — the run was a `resume --feedback` and
  the worker completed twice — `finish` or a plain answer — without changing
  the worktree since the run started; nothing was verified, but the export
  still ran (the evidence is the prior work, unchanged). Read the transcript
  to see what the reviewer's feedback actually asked for, then `dirtywork
  resume <slug> --feedback "..."` again — resuming an `unchanged` run
  requires `--feedback`, like `completed`.
- **`changed: null` / `changed_reason` set** — the change guard could not
  measure the worktree fingerprint; `changed_reason` on `run_end` (and echoed
  once on stderr as `dirtywork: change guard off: <reason>`) names why. One
  common cause on a resume: an over-budget worktree ends the run before the
  first turn — clean the worktree or raise `--max-worktree-mb`.
- **`changed: true` on a run that appears to have edited nothing (host
  mode)** — host mode's `HOME` is the worktree, so a tool's caches
  (`.npm/`, `.cache/`, `.nuget/`, `.dotnet/`, …) written there count as
  changes just like `files_changed` counts them. Redirect the cache per tool
  (`PIP_CACHE_DIR`, `npm_config_cache`, `NUGET_PACKAGES`, …) or list the
  paths in the worktree's `.git/info/exclude` if you want the guard (and
  `files_changed`) to ignore them.
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
- **`No space left on device` during a package restore in docker mode** —
  package-manager caches live under `$HOME`, which is a tmpfs capped at
  `--home-size` (256m by default); a real restore can overflow that. Raise
  `--home-size` (and `--memory` alongside it — tmpfs writes are charged to
  the container's memory cgroup), or redirect the cache per command instead.
  See the `--tmp-size`/`--gitdir-size`/`--home-size` bullet in
  [Machine contract](https://github.com/JimboSchneider/dirtywork/blob/main/dirtywork/contract/machine-contract.md#machine-contract).
- **`File size limit exceeded` / exit 153 from any `dotnet` command on the
  old `:0.10` image** — a 0.10 defect, not a dirtywork bug: that image's
  .NET 8 runtime trips the sandbox's per-command file-size limit
  (`ulimit -f 524288`, 256 MiB) at startup with W^X enabled, the .NET
  default. Use the `:0.11` image (the default since 0.11.0), or a derived
  image based on `:0.10` that adds `ENV DOTNET_EnableWriteXorExecute=0` — see
  [`docker/README.md`](../docker/README.md).
- **`aborted after 3 consecutive unknown_tool failures` on Devstral, with tool names that end in `[TOOL_CALLS]bash`** — 0.10 counted each as an unknown tool. 1.0 (#67) recovers the call: the tool after the last marker runs with the arguments given, the transcript's `tool_result` shows `tool: bash` and the raw name in `tool_raw`, and the model is told once per turn (`nudge` kind `name_recovered`) to emit clean calls. If a name has a marker but no real tool after it, it is still an unknown-tool failure.
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

