# Machine contract

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
    [--context-window <tokens>]       # default: the server's loaded window, else the
                                      # built-in table, else 32768 (+ stderr warning)
    [--timeout 1800]                  # whole-run wall clock, seconds
    [--temperature <f>]               # omitted by default → server preset
    [--max-tokens 8192]               # per-reply output cap; must be < the context window
    [--provider openai|anthropic|ollama]  # default: openai; anthropic needs ANTHROPIC_API_KEY
    [--base-url <url>]                # default depends on --provider (LM Studio for openai,
                                      # https://api.anthropic.com for anthropic)
    [--max-worktree-mb 2048]
    [--max-worktree-files 200000]
    [--sandbox docker|none]           # default: docker
    [--image ghcr.io/jimboschneider/dirtywork-worker:0.11]  # docker mode only
    [--allow-network]                 # docker mode only; default --network none
    [--memory 4g]                     # docker mode only
    [--cpus 2]                        # docker mode only
    [--tmp-size 1g]                   # docker mode only
    [--gitdir-size 512m]              # docker mode only
    [--home-size 256m]                # docker mode only
    [--min-free-mb 2048]              # docker mode only; host free-space floor
    [--keep-volume]                   # docker mode only; skip volume cleanup
    [--max-patch-mb 10]               # docker mode only; diff.patch cap
    [--allow-commit]                  # host mode only; worker commits its own work
dirtywork contract                          # print this document (the installed version's) to stdout; exit 0
dirtywork init [--repo <path>] [--no-user] [--force] [--stdout]
                                            # install the Claude Code skill — see "init" below
dirtywork --version                         # prints `dirtywork X.Y.Z`
```

```
dirtywork resume <slug | run-dir>     # same flags as run, minus --repo/--branch-from/--sandbox/<task>;
    [--model <m>]                     # defaults to the earlier run's model; --image defaults to its image
    [--feedback "<text>"]             # reviewer instructions; REQUIRED to resume a `completed` run
    [--feedback-file <path>]          # same, read from a UTF-8 file (max 64000 chars)
```

`--tmp-size`/`--gitdir-size`/`--home-size` default to the earlier run's
recorded values too (1.0). A run recorded before 1.0 has no `tmp_size`/
`gitdir_size`/`home_size` in its `run.json` to inherit, so it falls back to
the ordinary `--tmp-size`/`--gitdir-size`/`--home-size` defaults (`1g`/
`512m`/`256m`) like a fresh run would.

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
  `ghcr.io/jimboschneider/dirtywork-worker:0.11`. The image is the worker's
  whole toolchain: with `--network none` and no host mounts, nothing can be
  installed during a run. To add a tool, derive an image once:

  ```Dockerfile
  FROM ghcr.io/jimboschneider/dirtywork-worker:0.11
  USER root
  RUN apt-get update && apt-get install -y --no-install-recommends <packages> \
      && rm -rf /var/lib/apt/lists/*
  USER worker
  ```

  then `docker build -t my-worker:0.11 .` and `--image my-worker:0.11`. A custom
  `--image` is never digest-pinned — `PINNED_DIGEST` protects the maintained
  default image only.

  On the supported platforms (macOS/Linux) the worker runs as the **host**
  uid:gid, not the image's `worker` (uid 1000): `dirtywork/sandbox/docker.py`'s
  `DockerSandbox.start()` reads `os.getuid()`/`os.getgid()` and passes
  `--user uid:gid` at `docker create` (every `docker exec` inherits it), and
  the run volume is chowned to that uid — non-posix falls back to `1000:1000`
  (Windows is unsupported; see `docs/security.md`). So anything a
  derived image bakes in for the worker to write or read at run time — a
  pre-restored package cache, a tool's state dir — must be
  world-readable/writable (`chmod -R a+rwX`); the #48 soak's derived image
  failed with `Failed to read NuGet.Config due to unauthorized access` until
  that was done. dirtywork also sets `HOME=/home/worker` with `-e` at
  `docker create`, which every `docker exec` inherits — overriding any `ENV
  HOME` baked into the image. Baked paths are read-only at run time
  (`--read-only` rootfs), so a baked cache must be complete at build time;
  a live restore instead needs `--allow-network` plus a big enough
  `--home-size` (see below). A pre-restored NuGet cache, condensed
  (`docker/README.md`'s Derived images section has the fuller version):

  ```Dockerfile
  FROM ghcr.io/jimboschneider/dirtywork-worker:0.11
  USER root
  ENV DOTNET_EnableWriteXorExecute=0
  # until the 1.0 base image bakes these in — see the 0.10 defect note below;
  # the four below stop the build daemons a stray-process kill would otherwise chase (#61)
  ENV DOTNET_CLI_USE_MSBUILD_SERVER=0 MSBUILDDISABLENODEREUSE=1 UseSharedCompilation=false DOTNET_NOLOGO=1
  ENV NUGET_PACKAGES=/opt/nuget
  RUN dotnet restore <project> --packages /opt/nuget && chmod -R a+rwX /opt/nuget
  USER worker
  ```

  The `:0.11` image ships .NET SDK 8.0 and 10.0 and sets
  `DOTNET_EnableWriteXorExecute=0`, so `dotnet restore`/`build`/`test` work
  for both runtimes under the sandbox's per-command file-size limit (`bash`
  runs everything under `ulimit -f 524288`, 256 MiB; verified offline: new,
  build and run of net8.0 and net10.0 apps under the limit). The old `:0.10`
  image had SDK 8.0 only and its .NET 8 runtime died at startup with `File
  size limit exceeded` (a 0.10 defect); a derived image `FROM :0.11` inherits
  the fix and needs no `ENV` line of its own.
  Restoring anything not vendored this way needs `--allow-network`.

- `--tmp-size` / `--gitdir-size` / `--home-size` (docker mode; default `1g` /
  `512m` / `256m`) — caps on the three tmpfs mounts the **worker** container
  gets: `/tmp` (exec), `/gitdir` (the run's git dir — reached through the
  gitfile `/work/.git` since 1.0 (#61): the worker's commands inherit no
  `GIT_DIR`/`GIT_WORK_TREE`, so a `git init` elsewhere creates a repository
  there; only the export container keeps those variables) and `/home/worker`.
  The separate, short-lived export container (`docker_args.py`'s
  `export_create_argv`, spec §7) is unaffected by these flags — it always
  gets fixed sizes (`/tmp` 256m, `/gitdir` 2g, `/home/worker` 64m), sized for
  the export step's own needs, not the run's. All three worker-container
  flags share one validator (1.0): a non-zero leading digit, more digits,
  then `k`, `m` or `g`, upper-case folded to lower-case — no unit-less
  bytes, no decimals, no `%`, no `mb`/`MiB`, no `t`, no comma-separated mount
  options, no leading zero (Docker itself would accept `1g,exec` on that
  option string, and would read a leading zero as *octal* rather than
  reject it — the harness rejects leading zeros outright instead of relying
  on Docker's own reinterpretation). The validator's error text:
  `expected a size like 256m or 1g (no leading zero; digits then k, m or
  g)`. Package managers cache under `$HOME` by default (NuGet
  `~/.nuget/packages`, npm `~/.npm`, pip `~/.cache/pip`); a real NuGet
  restore overflowed the 256m default home in the #48 soak (`No space left
  on device`) — npm/pip weren't measured but are likely the same. Raise
  `--home-size`, or redirect a cache per command instead
  (`NUGET_PACKAGES=/tmp/nuget dotnet restore …`; npm honours
  `npm_config_cache`, pip honours `PIP_CACHE_DIR`) — environment does not
  carry between `bash` calls: each is its own `docker exec`, which starts
  from the container environment fixed at `docker create` (`HOME=/home/worker`,
  `TMPDIR=/tmp`, `PATH`), so a `HOME=/tmp` set inside one call cannot be
  made to stick for the next.
  All three tmpfs mounts live in RAM and are charged to the container's
  memory cgroup (verified on Docker 29: a 600 MiB write into a 1g tmpfs
  under `--memory 256m` was OOM-killed at ~253 MiB) — raise `--memory`
  alongside them.

- `--stall-turns N` (default 12) — end the run with status `stalled` after N
  consecutive turns that changed no file and produced no new command output;
  the model gets one nudge halfway. `0` disables. Independently (1.0, #66):
  every ten turns the harness fingerprints the worktree, and when it equals
  the previous check's, the model gets a `no_change` nudge — never an abort,
  and not a flag.
- `--stuck-repeats N` (default 4) — end the run with status `stuck` after the
  same **failing** `bash` command has run N times in a row. "Same" uses the
  stall detector's own fingerprint (command plus output with timings, clock
  times and git shas stripped), so a rerun that differs only in duration
  counts; a passing run (`exit code: 0`) resets the streak to zero. Edits
  between the reruns do **not** reset it — that is the loop `--stall-turns`
  cannot see, since every `edit_file` counts as progress. No nudge is sent:
  the point is to stop paying for turns. `0` disables.
- `--verify CMD` / `--verify-rounds N` / `--verify-timeout S` — see
  [Verifying a run](operating.md#verifying-a-run). In docker mode the gate runs
  like a worker `bash` call — without `GIT_DIR`/`GIT_WORK_TREE` in its
  environment since 1.0 (#61), so a suite that shells out to git in temp dirs
  can pass. `--verify-rounds` counts **fix rounds
  after a failed verify** — the command may run N+1 times; `0` verifies once and
  ends the run either way. `dirtywork resume` inherits all three — the command,
  the rounds, and the timeout — from the run it continues (recorded in `run.json`
  at run start, so this works even when the prior run ended before verify ever
  ran, e.g. `max_turns`/`stalled`/`stuck`/`timeout`/`budget_exceeded`/`unchanged`); an
  explicit flag on `resume` overrides the inherited value. Feedback for a fix
  round is delivered as the `finish` call's tool result (or as the next user
  message after a prose answer); the `verify` event records which (`via`).
- `--context-window TOKENS` — the model's context window, used to size the
  transcript trimming budget. Precedence: flag, then `DIRTYWORK_CONTEXT_WINDOW`,
  then **what the server reports it actually loaded the model with** (LM Studio's
  `GET /api/v0/models` → `loaded_context_length`, probed once at preflight with
  a 2-second timeout; any failure is silently no answer), then a built-in table
  for the known LM Studio models, then 32768 (with a warning on stderr — only
  this last step warns). Which step answered is recorded as
  `context_window_source` on `run_start`, `run.json`, every payload and
  `run_end`: `flag`, `env`, `provider:<name>:server`, `provider:<name>`, or
  `default`. `--provider ollama` is probed with `GET /api/ps`, whose
  `context_length` is the loaded `num_ctx` (recorded as
  `provider:ollama:server`); Ollama has no static table in dirtywork, so a
  model that is not resident falls straight through to `default` — its
  `/v1/models` lists PULLED models, so preflight passes for a model Ollama has
  not loaded yet. Run `ollama run <model>` before the run, or pass
  `--context-window`, or Ollama will quietly serve its own smaller `num_ctx`.
  See
  [Sizing the context window](operating.md#sizing-the-context-window).

- `--max-tokens` (default 8192) — the per-reply output cap sent to the provider
  on every request, and subtracted from the context window before the prompt
  budget is computed (`(window - max_tokens) * 0.75 * 4` chars), so a long reply
  can no longer run off the end of a window the prompt already filled. Preflight
  refuses `--max-tokens` at or above the window with
  `--max-tokens <N> must be smaller than the <W>-token context window` (exit 2).
  The rule is flat, with no small-window exemption: a server-reported context
  window at or below 8192 refuses every run until `--max-tokens` is passed and
  lowered below it. Recorded on `run_start` and in `run.json`; **not** echoed on the stdout payload
  (it is configuration, not evidence). `dirtywork resume` inherits it; a run
  recorded before 0.10 has no value to inherit and gets the 8192 default, which
  raises its effective cap from the adapters' old 4096. Pass `--max-tokens 4096`
  for models that cap output there — some older Claude models reject a larger
  value outright.

- `--allow-commit` (host mode only) — replaces the prompt's "leave all changes
  uncommitted for review" rule with "commit your work in small conventional
  commits as you go", so the run's branch comes back as real history instead of
  a dirty worktree. Rejected in preflight with `--sandbox docker`: the export
  carries files, not commits (its archive can never contain a `.git` entry), so
  a container's commits could not reach the host anyway. `dirtywork resume`
  inherits the setting from the run it continues.

**init:** writes the Claude Code skill that teaches an orchestrating agent to drive dirtywork
(the text `dirtywork init --stdout` prints) to `~/.claude/skills/dirtywork/SKILL.md` and, with
`--repo PATH`, to `<PATH>/.claude/skills/dirtywork/SKILL.md` as well; `--no-user` skips the home
copy (`--no-user` without `--repo` is a usage error). The first line after the frontmatter is a
stamp — `<!-- dirtywork-skill vX.Y.Z sha256:<16 hex> … -->` — whose hash covers the rest of the
file, so `init` can tell its own unmodified output from a copy you edited: absent → `wrote:`;
identical to the current render → `up to date:`; stamped, unmodified, but different (a newer
dirtywork, or a changed template) → `updated:`; edited or unstamped → `skipped (locally
modified):` unless `--force` (`overwrote:`). One stdout line per destination, user then
project. Exit 0 when every destination was written or current, 1 when any was skipped, 2 on a
usage or environment error (`error: …` on stderr; nothing further is written). `--stdout`
prints the rendered skill and writes nothing. The skill is never injected into the worker's
prompt; a project copy committed to the target repo is visible to the worker like any other
file (its first paragraph tells a worker to ignore it).

**Security:** Docker mode (the default) protects host integrity and host execution, not
repository-history confidentiality — the worker can read the *entire* parent object store (every
branch, other worktrees' objects, unreachable objects), so do not run it on a clone whose history
holds secrets you would not show the worker. `--sandbox none` runs the worker on the host behind
accident-grade guardrails — path confinement and a bash denylist that block mistakes, not
adversaries. The containment model, the known exposures and the host-mode caveats in full:
https://github.com/JimboSchneider/dirtywork/blob/main/docs/security.md.

**Tools:** the worker is advertised exactly eleven tools, in this order. They are
not configurable; a run's tool surface is the same in host and docker mode.

- `read_file(path, offset=0, limit=400)` — numbered lines; files over ~5 MB and
  non-regular files are refused.
- `write_file(path, content)` — create or overwrite; parent directories are
  created. The result echoes a capped unified diff (a new file reports its byte
  and line count instead).
- `append_file(path, text)` — append `text` **verbatim** to the end of an
  EXISTING file; nothing is inserted between the old content and the new, so a
  file that does not end in a newline needs one at the start of `text`. A
  missing target refuses with `ERROR: cannot append to '<path>': it does not
  exist; create it with write_file first` — `append_file` never creates a file
  or a parent directory. Three caps, in order and identical in both modes: the
  `text` argument (`ERROR: text is <n> bytes, over the <limit>-byte write
  limit; append in smaller pieces`), the current file's size, and the result
  size (both of the latter render `ERROR: result is <n> bytes, over the
  <limit>-byte write limit; nothing was written`). This is the second half of
  the large-file recipe: `write_file` the first part, `append_file` the rest —
  and it is what a truncated call is told to do by name. When a tool call is cut
  off at the token limit (`finish_reason: "length"`), the harness answers with an
  error that states the `--max-tokens` cap, about how much of the call's own
  content actually got out before the cut (characters, and lines when the cut
  call's raw arguments are present), a per-call target size (characters and
  lines) to stay under next time, and which cut-off reply of the run this is
  ("cut-off reply *n* of 6"). A cut `write_file` whose path can be recovered
  from the model's own argument fragment is told nothing was written and to
  resume with `append_file`; any other cut call is told to keep calls under the
  target size, splitting a large file across `write_file` and `append_file`.
  Either way the turn counts as a `malformed_args` failure, including the
  Anthropic shape where the truncated arguments parse as `{}` and a required
  parameter is simply missing. Truncations are counted per run and never reset;
  the sixth cut-off reply of a run ends it `model_error`.
- `edit_file(path, old_string, new_string)` — one exact replacement;
  `old_string` must occur exactly once.
- `apply_edits(path, edits)` — several exact replacements to ONE file in one
  call, applied **in order on the running text** (edit *i* sees the text after
  edits 1…*i−1*), each `old` matching exactly once at its turn. All-or-nothing
  before the write: the first failure writes nothing and the result names it
  (`ERROR: edit i of N: …`). At most 100 edits and 2 MiB of argument text per
  call. This is the tool for a brief's numbered edit list.
- `insert_before(path, anchor, text)` / `insert_after(path, anchor, text)` —
  insert whole line(s) around the line holding a unique `anchor`, never
  modifying the anchor's own line.
- `list_dir(path=".")` — entries, directories suffixed `/`.
- `grep(pattern, path=".", glob=None)` — regex search (ripgrep when the image
  has it, `grep -rn` otherwise).
- `bash(command, timeout=120)` — a shell command in the worktree; 600 s
  maximum. Since 1.0 (#64) `timeout` accepts an integer number of seconds
  (`60`) or a duration string (`"60s"`, `"2m"`); on the wire its schema type
  is `["integer", "string"]` and the description says exactly that, and a
  rejected value's `bad arguments` error repeats the same accepted form
  (`… — got '60x'`) instead of naming only the type. Either spelling
  canonicalizes to the same seconds for the stall detector. A command that
  hits its timeout returns
  `ERROR: command timed out after <n>s — it did not finish and its result is
  unknown. …` with **no partial output**, the `tool_result` event carries
  `timed_out: true`, and the run's `timeouts` counter rises. Backgrounded
  processes are killed when the command returns — host mode kills the process
  group; docker mode, since 1.0 (#61), kills every process but the container's
  tether **in place**: a `stray_kill` transcript event names them and the worker
  is told on the same turn. Only if that kill cannot be performed or verified, or
  the container ran out of memory, is the container reset instead
  (`sandbox_reset`): the working tree survives, the worker's git metadata in
  `/gitdir` (index, stashes, local commits, branches) does not, and the worker is
  told that too. Stale git lock files a killed `git` left behind are swept
  (`locks_removed`).
- `finish(summary)` — ends the run.

The four in-place tools (`edit_file`, `apply_edits`, `insert_before`,
`insert_after`) share one read→transform→write path per backend, so they refuse
an oversized result with the same string
(`ERROR: result is <n> bytes, over the <limit>-byte write limit; nothing was
written`) and produce byte-identical success text on the host and in the
container. Diff bodies use `\n` as the only line separator, so **CRLF content
keeps its carriage return**: a line ending `\r\n` renders as `-foo\r` /
`+foo\r`, exactly as `git diff` shows it, and a line that merely *contains* a
form feed or other vertical whitespace is never split. A final line with no
trailing newline is followed by git's own `\ No newline at end of file` marker
on its own output line.

Since 0.10 "nothing was written" also covers a failure **during**
the write: every host write and every container write is staged in a sibling
temp file and promoted with an atomic rename, so an I/O error or a kill leaves
the target byte-identical. Two branches (host mode only) keep the old in-place
behaviour and are named here rather than hidden: a target with more than one
hard link (a hardlink is *meant* to see the write through the shared inode)
and a target in a directory the process cannot write (a rename is impossible
there). The promote changes the file's inode, so a worker process holding the
old file open keeps reading the old content until it re-opens. In docker mode
there is no fd fallback: a writable file inside an unwritable directory
refuses (Permission denied) where host mode writes in place.

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
  },
  "trimmed_turns": 0,
  "timeouts": 0,
  "context_window_source": "provider:openai:server",
  "truncations": 0,
  "changed": true
}
```

(`changed_reason` is sparse and so is absent here; it rides alongside
`changed` only when `changed` is `null`.)

Six of those keys are 0.8 additions (`stuck_on`, `files_changed`,
`files_changed_truncated`, `last_tool_result`, `last_assistant_text`,
`verify`) and the last three are 0.9's (`trimmed_turns`, `timeouts`,
`context_window_source`). **0.10 adds no stdout key at all**: its additions are
the eleventh tool (`append_file`), the `--max-tokens` flag, `max_tokens` on
`run_start` and `run.json`, `finish_reason` on `assistant` events, and
`--provider ollama` — all additive, `schema_version` still `2`. 1.0 (#65/#66)
adds two more stdout keys: `truncations` (integer, **always** — how many
turns produced a truncation message; `0` when none) and `changed` (boolean \|
null, **always** — whether the newest worktree fingerprint the harness took
differed from the one at run start; `null` when the guard could not measure).
A third field, `changed_reason` (string, **sparse**), rides alongside
`changed` only when it is `null` because a fingerprint was attempted and
failed or raised. Every one of the eleven keys above is present on every
payload —
`null` when it does not apply, `[]`/`false` for the list and its flag, `0` for
the three counters — including the two paths where `runner.run()` never returns
(see below), where they carry those same defaults rather than being omitted.
`trimmed_turns` is how many turns had to drop tool results to fit the context
budget, `timeouts` how many `bash` calls never finished, `truncations` how
many turns produced a truncation message, and
`context_window_source` which precedence step produced `context_window`
(`flag` | `env` | `provider:<name>:server` | `provider:<name>` | `default`).

`status` is one of: `completed`, `max_turns`, `timeout`, `stalled`, `stuck`,
`verify_failed`, `context_exhausted`, `model_error`, `interrupted`,
`budget_exceeded`, `sandbox_error`, `export_failed`, `unchanged` (1.0, #66: a
`resume --feedback` run completed twice without changing the worktree; verify
never ran; exit 1 like every non-`completed` status). When the run fails before a `RunResult`
exists — the LLM client raises, post-worktree setup fails (e.g. the
transcript can't be created), or any other exception escapes the run
(status `model_error` in every case) — `turns` is `null` and `usage` is
`{}`, but `status`, `worktree`, `branch`, `transcript`, and `run_dir` are
still populated so the worktree and run directory can be located for
salvage.

`base_commit` and `provider` (`"openai"`, `"anthropic"`, or `"ollama"`) are present on
every post-preflight payload. `resumed_from` is
the slug of the run this one continued, or `null` if this was a fresh run.
`finalize_error`, `watchdog_violation` and `watchdog_violation_kind` are
added on the normal end-of-run path — i.e. whenever `runner.run()` returns a
result, `completed` or not — normally `null`; see `run_end` below for what
each means. `stuck_on`, `files_changed`, `files_changed_truncated`,
`last_tool_result`, `last_assistant_text`, `verify`, `trimmed_turns`,
`timeouts`, `context_window_source`, `truncations` and `changed` are present on
**every** payload (`null`/`[]`/`false`/`0` when they do not apply) — including
the two paths below where `runner.run()` never returns, where they carry
those same defaults rather than being omitted. Four of those 0.8 keys
are there so a run that ends with an empty `final_message` is still
triageable without opening the transcript: what it changed, what it last ran
and what it last said. On a `completed` run they are just as useful — "the
last thing the worker checked failed, and it called `finish` anyway" reads
straight off `last_tool_result`.
The two
paths where `runner.run()` never returns (sandbox setup fails before it
starts, or an exception escapes the loop and is caught in `main()`) report
`base_commit` and `resumed_from`, the six 0.8 evidence keys above (as their
null/empty defaults) **and** the three 0.9 contract keys — `trimmed_turns` and
`timeouts` as `0`, `context_window_source` as the value preflight actually
resolved — **and** the 1.0 keys, `truncations` as `0` and `changed` as `null`
with no `changed_reason` — plus `export_status` too if a docker `finalize()` ran
during that exception recovery — `finalize_error`, `watchdog_violation` and
`watchdog_violation_kind` are not present on these two paths, since they
never got far enough to know.

**Exit codes:**

- `0` — `completed`.
- `1` — any non-`completed` status (`max_turns`, `timeout`, `stalled`,
  `stuck`, `verify_failed`, `context_exhausted`, `model_error`, `interrupted`,
  `budget_exceeded`, `sandbox_error`, `export_failed`, `unchanged`); the worktree and branch are kept for
  salvage/review. `main` catches every `Exception` the run raises (not
  just ones the runner itself converts to a status) and reports
  it as `model_error` via the same JSON contract, so a post-preflight run
  never tracebacks. (Ctrl-C is a `KeyboardInterrupt`, a `BaseException`, not caught
  here — but the run loop itself already converts in-loop Ctrl-C to status
  `interrupted` before it would reach this point.)
- `2` — preflight or environment error (LM Studio unreachable, model not
  loaded, `--repo` not a git repo, etc.); nothing is created.

All progress (transcript path, worktree path, `error:`-prefixed messages) is
written to stderr; watch a live run with `tail -f` on the transcript path (events land one turn at a time since 1.0).

Full field-by-field schema, including every v1→v2 addition and the
`run.json` field list: [`docs/transcript-schema.md`](transcript-schema.md).

**Transcript events** (JSONL, one per line): `run_start` (task, repo, model,
config, `schema_version: 2`, plus provenance: `worktree`, `base_commit`,
`branch`, `branch_from`, `base_url`, `dirtywork_version`, `temperature`,
`sandbox` — the docker settings dict, or `"none"` — and `provider`,
`context_window`, `context_window_source`, `resumed_from`),
`assistant` (text + tool calls — text capped at 64 000 chars in the
transcript only, the full text is still sent to the model), `tool_result`
(truncated; tool names capped head-and-tail at 200 chars, 1.0/#67), `guardrail_block`, `nudge` (`{"event": "nudge", "kind":
"truncated|empty|text_tool_call|stall|timeout|malformed_entry|stray_kill|sandbox_reset|no_change|unchanged_finish|name_recovered", "turn": N,
"via": "tool_result|user"}` — eleven kinds; `no_change` (every ten turns, the
worktree fingerprint equals the previous check's) and `unchanged_finish` (a
completion rejected because nothing changed, see below) are 1.0 (#66)
additions; `name_recovered` (1.0, #67) is a tool call whose name carried stray text and a marker before a real tool name — the harness ran that tool and says so once per turn. Since 1.0 a nudge on a tool-call turn rides on
the turn's last `tool_result` (its `follow_up` field) and never as a user
message after a tool result; the history never carries two consecutive user
messages), `sandbox_reset`
(docker mode: the container was reset — `reason`, plus `strays` when stray
processes caused it), `stray_kill` (docker mode, 1.0: processes that outlived a
`bash` call were killed in place — `strays`, and `locks_removed` when stale git
locks were swept), and `run_end` (status, turns,
duration, cumulative
usage, plus the run's artifacts: in host mode `diff_stat` — `git diff
--stat` against the base commit, tracked changes only — and `untracked` —
`git status --porcelain` `??` entries — each capped at 64 000 chars; in
docker mode `diff_stat` (which already includes new files, since the
export stages everything first), `untracked` (always `""`), `patch_path`,
`worktree_bytes`, `worktree_files`, `escaping_symlinks`,
`dropped_git_entries` (docker mode: `.git`-named entries the export dropped, the
root gitfile excluded; since 1.0 a nested repository's files are exported as
plain files, so an entry at depth ≥ 2 names one), `export_status`, `watchdog_violation` (docker mode;
null unless the watchdog killed the container), `watchdog_violation_kind`
(set alongside `watchdog_violation`: `"budget"` for a worktree-size or
host-disk-floor breach, `"sandbox_error"` for the watchdog's own
worktree-sampling exec failing twice; otherwise `null`), and
`finalize_error` (set when the finalize/export step itself raised an
exception after the agent loop otherwise finished; `null` normally; `KeyboardInterrupt: interrupted during finalize` when an interrupt landed inside the export — the export is attempted once and never re-run, and `run_end.status` is `interrupted`)).
A `finish(summary=...)` call appears in the transcript as an ordinary tool call in its `assistant` event followed by a `tool_result` event whose `result` is `run finished` when the agent loop ended `completed` (an interrupt or export failure *after* that point is reported in `run_end.status` / the CLI status, not here) — otherwise the verify feedback text (a fix round follows) or a `run not finished: …` reason (see `transcript-schema.md`); the summary becomes the run's `final_message`.

A completion that changed nothing in the worktree since the run started — a
`finish` call or a plain answer with no tool call — is refused once (1.0,
#66): on the finish path the refusal is the finish tool's own `result`; on a
plain answer it is the next user message; verify does not run. On a `resume
--feedback` run a second such completion ends the run `unchanged` (exit 1);
otherwise the run proceeds as if nothing had happened, and a second matching
completion is accepted (`completed`, `run_end.changed: false`). The guard
detects that the worktree changed, not that the feedback was applied — it
catches a lazy completion, not a hostile one.

Since 1.0 the history sent to the model obeys two rules (#60): a harness follow-up never directly follows a tool result — it rides on the turn's last `tool_result` as `follow_up`, or is the `finish` result — and an assistant reply with no tool call and no text is stored as `[empty reply]` (`assistant.placeholder`), so strict chat templates never see a dropped turn or a user message after a tool result.

`run_start`'s `sandbox` is the docker settings dict in docker mode (`"none"`
in host mode) — `{backend, image, image_digest, image_pinned, network,
memory, cpus, pids_limit, tmp_size, gitdir_size, home_size,
max_worktree_mb, max_worktree_files, user}`, `home_size` since 1.0. `image`
is the `--image` argument as given; `image_digest` is the registry digest
from `RepoDigests`, or `null` for a locally-built image that was never
pushed/pulled — provenance only; `image_pinned` is `true` only when
`--image` was left at its default AND `PINNED_DIGEST` was enforced against
a pulled default image (`false` for a custom `--image` — never pinned — or
a locally built/loaded default image, which only warns). `run.json` does
**not** nest that dict: its own `sandbox` field stays the flat string
`"docker"` \| `"none"`, while `image`, `image_digest` and `image_pinned` are
recorded as top-level keys, and — since 1.0 (#63) — so are `tmp_size`,
`gitdir_size` and `home_size` (the canonical lower-cased flag values;
`null` in host mode). `run.json` also records the run's key fields: `task`,
`model`, `context_window`, `resumed_from`, and `turns` (at the end); when a
run is resumed, the earlier run's `resumed_by` field records the slug of the new run that continued it.
The container itself always runs from the image's local
content-addressed Id, never a registry digest, so a run can never trigger a
network pull.

