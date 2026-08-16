# Review response: hardening, sandbox, extensibility

**Date:** 2026-08-15 (v2, after a second external review and an empirical
red-team of the sandbox design)
**Status:** Approved direction; v2 design pending user review
**Origin:** Two external (ChatGPT) reviews of v0.2.0 and of the v1 of this
document. Every claim was verified against the code, and the v2 sandbox
design was tested with real Docker/git experiments and attacked from three
lenses (git internals, container/resources, host flow) before being written
down. Where a mechanism below is marked *verified*, a real experiment
demonstrated it on this macOS host with Docker Desktop; *unverified* items
are called out.

## Purpose

Move dirtywork from "guardrailed harness for a confused model" to "sandboxed
harness that is also safe against a malicious model or repository", without
losing the properties that make it useful: worktree isolation, no
auto-commit, machine-readable output, a complete JSONL transcript, and a
Python-stdlib-only runtime. Then make it extensible enough that a new tool,
a new provider, or a benchmark run is one small addition rather than surgery
on `tools.py` and `runner.py`.

**Threat model (docker mode).** A malicious worker model and/or a malicious
target repository must not be able to: (a) modify anything on the host
outside the worktree; (b) modify the parent repository's shared git state
(config, hooks, refs, objects); (c) execute code on the host; (d) exhaust
host disk, CPU, or memory unboundedly; (e) reach the network by default. The
dirtywork process is trusted. The operator's clone is trusted only to the
extent that *read-only* git commands on it are safe (git clone does not
import config or hooks); dirtywork does not try to be safer than `git status`
inside the operator's own clone. Known, accepted exposures are listed under
"Residual exposures" below.

**Success criteria**

- The three reproduced denylist bypasses (`git -C ../.. config …`,
  `git -c … push`, `python3 -c 'open("/tmp/…","w")'`) fail *inside the OS
  boundary* in the default mode, proven by host-sentinel tests.
- A repository that ships a symlinked `CLAUDE.md` or `.worktrees` cannot make
  dirtywork read or write outside the repo.
- Every model- or repo-controlled input has an explicit bound.
- The stdout JSON contract is unchanged (new fields only). **The default
  execution mode changes in 0.3** (Docker); that is a documented breaking
  change with an exit-2 hint, not silent breakage.
- Adding a tool is one `ToolSpec`; adding a provider is one adapter class
  passing the shared contract tests; `dirtywork bench` produces per-model
  completion/regression/token/latency numbers.

Three sub-projects, delivered in order as separate PRs, each keeping the
existing 185-test suite green.

---

## Sub-project 1: Hardening (host mode and shared code)

Bounded fixes to existing flows. All verified real; overstated items are
handled minimally (noted inline). Everything here applies to `--sandbox
none` and to the shared host-side code used by docker mode.

| # | Finding | Verified | Change |
|---|---------|----------|--------|
| 1 | git denylist rules don't tolerate global options (`git -C x config`, `git -c k=v push`) | Yes — plain forms are blocked; option forms slip past | Rules accept any run of `-C <arg>`, `-c <k=v>`, `--<flag>[=v]`, `-<x>` tokens between `git` and the subcommand. Both repros become tests. `python3 -c` writing outside stays unblocked in host mode — documented; the sandbox is the fix. |
| 2 | `CLAUDE.md`/`AGENTS.md` read through symlinks, unbounded size | Yes — `is_file()` follows links | `lstat`; refuse symlinks and non-regular files; refuse > `MAX_READ_BYTES` (5 MB); inject ≤ `MAX_CONTEXT_CHARS` (32 000) with a truncation marker. |
| 3 | `.worktrees` symlink unchecked | Yes — a repo committing `.worktrees -> /outside` places the worktree outside the clone (verified) | `os.lstat(repo/".worktrees")`: proceed only if ENOENT or `S_ISDIR` (never follow). After `git worktree add`, require `(repo.resolve()/".worktrees") in worktree.resolve().parents` (pathlib join of the resolved repo — never `.resolve()` the joined path; that variant passes wrongly, verified). On failure remove worktree + branch, raise `WorkspaceError`. Applies to both modes. |
| 4 | `info/exclude` path not validated | Yes | Resolved `git rev-parse --git-path info/exclude` must be inside resolved `git rev-parse --git-common-dir`; open with `O_NOFOLLOW`; else abort. |
| 5 | HTTP error body read fully before slicing | Yes | `e.read(500)`. |
| 6 | Transcript/run dir created with default perms; `~/.dirtywork` never validated | Yes | `~/.dirtywork` and `runs/`: `os.mkdir(mode=0o700)` ignoring EEXIST, then `lstat` must be `S_ISDIR` and owned by `os.getuid()` (POSIX). Per-run dir: `mkdir(mode=0o700, exist_ok=False)` — a pre-placed symlink raises EEXIST → abort. Transcript: `os.open(O_WRONLY\|O_CREAT\|O_EXCL\|O_APPEND\|O_NOFOLLOW, 0o600)`. `O_EXCL` also turns a slug collision into a loud failure. |
| 7 | Missing provenance | Yes | `run_start` gains `base_commit`, `branch`, `branch_from`, `base_url`, `dirtywork_version`, `temperature`, `sandbox`, `provider`; `run_end` gains `diff_stat` (capped). |
| 8 | Unbounded `write_file` content, `list_dir` rows, assistant text | Partly — transitively bounded by the 64 MB response cap | `write_file` refuses content > `MAX_WRITE_BYTES` (5 MB); `list_dir` stops after 2 000 entries with a marker; assistant `text` in the transcript capped at 64 000 chars (full text still goes to the model). |
| 9 | `write_file` has no regular-file guard; a FIFO in the worktree hangs the process (Linux) | Yes (verified) | All host file tools open with `os.open(... \| O_NOFOLLOW \| O_NONBLOCK \| O_CLOEXEC)`, `fstat`, require `S_ISREG` (and size ≤ cap for reads), then clear `O_NONBLOCK` and use `os.fdopen`. Removes the FIFO hang and closes the final-component symlink TOCTOU. |
| 10 | Worktree disk growth unbounded | Yes | Shared `worktree_budget` walker (used by both modes): `os.fwalk` on POSIX (dir-fd relative, `follow_symlinks=False`; on Windows `\\?\` paths and skip reparse points), sums `st_blocks*512` where available else `st_size`, counts every entry, aborts early when `--max-worktree-mb` (2048) or `--max-worktree-files` (200 000) is exceeded, treats an unreadable directory as a violation (fail closed), and reports symlinks whose target is absolute or escapes the worktree. Host mode runs it after every tool call → status `budget_exceeded`. |
| 11 | `.git/info/exclude` is modified in the main checkout; docs don't say so | Yes | One sentence in README and `SECURITY.md`. |
| 12 | 16-bit slug salt | Overstated — `worktree add -b` already fails on a branch collision | `token_hex(4)`; #6's `O_EXCL` covers the transcript side. |
| 13 | "Should not market itself as secure" | Not applicable — README/SECURITY.md already say "not a sandbox" | No change until sub-project 2 lands; then rewrite to describe the sandboxed default accurately. |

**Testing:** each item gets a unit test in the existing module for its file.
Symlink/FIFO tests use `tmp_path` with real symlinks, `os.mkfifo`, and real
`git init` repos, as the existing workspace tests do.

**Out of scope:** anything that needs a process boundary (sub-project 2).

---

## Sub-project 2: Docker sandbox backend

### Decision record

- **Docker first** (not Seatbelt): cross-platform including Windows, cgroup
  quotas, strongest boundary. `sandbox-exec` verified working on macOS 26.6
  and remains a candidate later backend behind the same interface.
- **Docker is the default in 0.3.** `--sandbox none` is the explicit opt-in
  to today's host behavior. Docker missing or daemon down → preflight error,
  exit 2, hint printed. No silent fallback. This is a breaking change,
  called out in the changelog and README.
- **Image via `--image` with a maintained default** (`dirtywork/worker`,
  built from `docker/Dockerfile` in this repo: Debian-based, `USER worker`
  (uid 1000), git, bash, coreutils, python3, node, .NET SDK, ripgrep). The
  reference in code is pinned by digest. Preflight resolves the digest via
  `docker image inspect --format '{{json .RepoDigests}}'` (fallback `.Id`
  for local builds), pulls explicitly if absent (this is the only network
  use at start), and always runs `<name>@sha256:<digest>`. The digest is
  recorded in `run.json` and `run_start`.
- **All six tools run inside the container.** Host-side file tools over a
  container-writable bind mount were shown to be exploitable (a background
  process in the container swaps a symlink between the host's `resolve()`
  and `open()`; a FIFO blocks the host). Inside the container, symlink
  following is harmless: the mount namespace is the confinement.
- **The runtime stays Python-stdlib-only**, but the secure default requires
  Docker and a maintained image. Said plainly in the README.

### 1. Sandbox interface

```
class Sandbox(Protocol):
    def start(self, worktree: Path, repo: Path, slug: str, base_commit: str) -> None
    def read_file(self, path, offset, limit) -> str
    def write_file(self, path, content) -> str
    def edit_file(self, path, old, new) -> str
    def list_dir(self, path) -> str
    def grep(self, pattern, path, glob, timeout) -> str
    def bash(self, command, timeout) -> str
    def stop(self) -> None
```

`HostSandbox` wraps today's `tools.py` functions unchanged (plus SP1
hardening). `DockerSandbox` implements every operation via `docker exec`
(details below). The runner/`ToolExecutor` (later the registry) dispatches
to the sandbox; model-facing result formats do not change.

### 2. Host-side flow

1. Preflight (read-only on the operator's clone): `git rev-parse
   --is-inside-work-tree`, `rev-parse HEAD`; `docker version`; image
   digest resolution/pull as above.
2. `ensure_worktrees_excluded` with the SP1 path check.
3. SP1 `.worktrees` checks, then
   `git worktree add --no-checkout -b dirtywork/<slug> .worktrees/dw-<slug> <ref>`
   (verified: leaves only the `.git` file; no index; no checkout hooks run).
   Post-check location. Record `base_commit = git -C <wt> rev-parse HEAD`
   and the exact bytes of `<wt>/.git` (compared later byte-for-byte —
   Windows git writes drive-letter forms, so never reconstruct the string).
4. Run dir `~/.dirtywork/runs/<slug>/` (SP1 rules); write `run.json`
   `{status:"running", slug, repo, worktree, branch, base_commit,
   container, image, image_digest, host_pid, started}` **now**; create an
   empty `dotgit` file (0400) for the overmount.
5. Start the container (§3). Wait until `docker exec <c> true` succeeds.
6. In-container init (§4). Any non-zero exit → `docker rm -f`, status
   `sandbox_error`, exit 1.
7. `load_repo_context(worktree)` on the host (files now exist; SP1 rules).
8. Agent loop; every tool call goes through the sandbox (§5); the
   watchdog runs for the container's whole lifetime (§6).
9. Stop: `docker rm -f` (also in the CLI `finally`).
10. Post-run on the host, in this order: (a) run the budget walker once more;
    any entry named `.git` (case-insensitive) at depth ≥ 1, or a root `.git`
    whose bytes differ from step 3 → status `tampered`, list offending paths
    in `run_end`, and **run no host git command in the worktree**;
    (b) otherwise `git -C <wt> read-tree HEAD` (index only; verified it does
    not touch working-tree files) so host `status`/`diff` are meaningful,
    then `git diff --stat` for `run_end.diff_stat`; both run with
    `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 -c core.hooksPath=/dev/null`
    and tolerate non-zero exit (a worker-authored `.gitattributes filter=lfs`
    can otherwise invoke `git-lfs` from the operator's global config —
    verified). Update `run.json`.
11. `run_start` records `sandbox: {backend, image, image_digest, network,
    memory, cpus, pids_limit, tmp_size, gitdir_size, max_worktree_mb,
    max_worktree_files, user}`. `schema_version: 2`.

### 3. Container: creation, mounts, limits, lifetime

```
docker create -i --init --name dw-<slug> \
  --label dirtywork.run=<slug> --label dirtywork.repo=<sha256(resolved repo path)> \
  --network none --memory 4g --memory-swap 4g --cpus 2 --pids-limit 512 \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user <uid>:<gid> \
  --tmpfs /tmp:rw,exec,size=1g,mode=1777 \
  --tmpfs /gitdir:rw,size=512m,mode=0700,uid=<uid>,gid=<gid> \
  --tmpfs /home/worker:rw,size=256m,mode=0700,uid=<uid>,gid=<gid> \
  --mount type=bind,src=<worktree>,dst=/work \
  --mount type=bind,src=<git-common-dir>/objects,dst=/repo.git/objects,readonly \
  --mount type=bind,src=<rundir>/dotgit,dst=/work/.git,readonly \
  -e GIT_DIR=/gitdir -e GIT_WORK_TREE=/work -e HOME=/home/worker -e TMPDIR=/tmp \
  -e LANG=C.UTF-8 -e GIT_AUTHOR_NAME=dirtywork -e GIT_AUTHOR_EMAIL=dirtywork@localhost \
  -e GIT_COMMITTER_NAME=dirtywork -e GIT_COMMITTER_EMAIL=dirtywork@localhost \
  <name>@sha256:<digest> cat
```

- **Lifetime tether.** `--init` makes tini PID 1; its only child is `cat`
  reading the container's stdin. The
  host runs `docker start -ai dw-<slug>` as a `Popen` with `stdin=PIPE`
  (stdout/stderr → devnull) and holds the write end. If dirtywork dies for
  any reason (SIGTERM, SIGHUP, SIGKILL, crash), the pipe closes, `cat` gets
  EOF, `cat` exits, tini exits, the kernel tears down every process in the namespace,
  and the container stops. (Verified for the `docker run -i` form; the
  `create` + `start -ai` form is *unverified* and must be checked first
  thing in implementation, including that a fresh `start -ai` after
  `docker kill` re-attaches stdin.)
- **Reset** (used on timeout, on stragglers, on OOM): `docker kill
  dw-<slug>` (SIGKILL PID 1 → whole namespace dies; tmpfs wiped; bind
  mounts untouched — verified with `restart -t 0`), wait for the `Popen`
  to exit, then a fresh `docker start -ai` and re-run init (§4, restart
  variant). ~150 ms. Emits a `sandbox_reset` transcript event with the
  reason.
- **User.** POSIX hosts: the invoking user's uid:gid (files in the
  worktree stay owned by the operator; verified working on Docker Desktop
  with an arbitrary uid). Windows: `1000:1000`. tmpfs `uid=/gid=` must
  match or the 0700 dirs are unwritable (verified).
- **tmpfs and memory.** tmpfs pages are charged to `--memory` (verified;
  overflow is an OOM kill, not ENOSPC). Sum of tmpfs sizes (1g + 512m +
  256m) stays under half of `--memory`; `--memory-swap` equals `--memory`
  so nothing spills into host swap. `--tmpfs` defaults to `noexec`; `/tmp`
  gets `exec` explicitly (build tools run scripts from it); `/gitdir` and
  `/home/worker` stay `noexec`.
- **`--mount` only, never `-v`** (a missing source is an error, not a
  silently created directory — verified). Before every start (first and
  after reset) the host verifies `<wt>/.git` is a regular non-symlink file
  with the step-3 bytes; the overmount is not attempted otherwise.
- **Name collision.** If `dw-<slug>` exists: `docker inspect` its labels.
  Labels match and container not running → remove and proceed. Labels
  match and running → refuse (exit 2; another live run). Labels absent or
  different → refuse. Never remove a container solely because the name
  matches.
- **Nothing** from `<repo>/.git` other than `objects/` is mounted (no refs,
  config, hooks, `worktrees/`).
- CLI: `--sandbox docker|none`, `--image`, `--allow-network` (default
  bridge network), `--memory`, `--cpus`, `--tmp-size`,
  `--max-worktree-mb`, `--max-worktree-files`.

### 4. In-container git init (idempotent; first start and after every reset)

All commands run with `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1`
so a hostile checked-out `.gitconfig` cannot influence init (verified that
`HOME`-relative config was otherwise honored):

```
git init -q                                   # GIT_DIR=/gitdir
echo /repo.git/objects > /gitdir/objects/info/alternates
git symbolic-ref HEAD refs/heads/dirtywork/<slug>
git update-ref refs/heads/dirtywork/<slug> <base_commit>
first start:   git read-tree -m -u HEAD       # populates /work; refuses if /work is non-empty (verified)
after reset:   git read-tree HEAD             # index only; never touches the working tree (verified)
Windows hosts: add -c core.symlinks=false to the populate (symlinks become plain files)
```

Verified: `status/diff/add/stash/log/commit/gc/repack` all work; new objects
land only in `/gitdir`; the parent object store is byte-identical before and
after, including through `gc`/`repack`; `git config` writes only
`/gitdir/config`. A hostile repo's checkout behavior (symlinks, attributes,
long paths, case collisions) is interpreted inside the container. Anything
the worker does with git metadata (staged index, stashes, commits) is
ephemeral and lost on reset; only the working tree persists — stated in the
`bash` tool description.

### 5. Tool execution inside the container

Every operation is a `docker exec -w /work [-i] dw-<slug> …` with
stdout+stderr merged, drained on a thread with the existing 1 MiB capture
cap, and the per-call timeout. Paths are lexically normalized on the host
(reject absolute paths and `..` escapes — an accident guard, not the
boundary) and used relative to `/work`.

- `bash`: `bash -c 'ulimit -f 524288; exec bash -c "$1"' _ <command>`
  (verified: `RLIMIT_FSIZE` is a hard, unraisable per-file cap even for
  root under `--cap-drop ALL`; the `"$1"` form passes any command string
  intact; exit codes propagate; 153 = file-size cap, documented).
- `read_file`: `head -c <MAX_READ_BYTES> -- <path>` piped through the
  drain; offset/limit windowing and numbering on the host as today.
- `write_file`: content on **stdin** — `docker exec -i … sh -c 'mkdir -p
  "$(dirname -- "$1")" && cat > "$1"' _ <path>` (no quoting of content;
  size cap enforced on the host first).
- `edit_file`: read via `read_file`'s primitive, apply the single-occurrence
  replacement on the host, write via `write_file`'s primitive. The
  read/write pair is not atomic; the only process that could race it is a
  worker background process, which is reaped after every `bash` call (§6),
  and the race can only affect the worker's own files.
- `list_dir`: `find <path> -mindepth 1 -maxdepth 1 -printf …` when GNU
  find exists, else `ls -1Ap`; capped on the host.
- `grep`: `rg` if present in the image else `grep -rn`; same result
  shaping as today.

### 6. Reaping and budgets

- **After every `bash` call** (normal return or timeout): `docker top
  dw-<slug>` (works even when the container is pid-saturated or an
  in-container process is killing incoming shells — verified). If anything
  beyond the two idle processes (tini + `cat`) is present → reset (§3). Also `docker inspect
  .State.OOMKilled` → reset. This restores the documented contract
  ("backgrounded processes are terminated when the command returns"),
  which plain `docker exec` does not honor (verified).
- **Watchdog thread** for the container's lifetime: every 2 s sample
  `shutil.disk_usage(worktree).free` (O(1), portable) and, while a `bash`
  call is in flight, run the SP1 budget walker. On `free < --min-free-mb`
  (1024) or worktree over budget → kill the container, end the run with
  `budget_exceeded`. Between calls no worker process exists (reaped), so
  growth can only happen during a call. This is a *bound*, not a kernel
  quota — a burst inside the sampling interval is possible; documented.
- Fork bombs are contained by `--pids-limit` (verified) and cleared by the
  reap/reset.

### 7. Docs

README Security section rewritten to describe the sandboxed default
truthfully: what the container blocks, what `--sandbox none` gives up, the
0.3 breaking change, the image requirement, and the residual exposures
below. `SECURITY.md`: escapes from docker mode are in scope; `--sandbox
none` keeps today's caveats. Windows: expected to work by construction (no
host path is meaningful inside the container) but **unverified** until run
on a Windows host with Docker Desktop.

**Residual exposures (documented, accepted):**
- The read-only alternate exposes the *entire* parent object store to the
  worker (all branches, other worktrees' objects, unreachable/not-yet-gc'd
  objects) — read-only, no network, so the only egress is the reviewed
  diff/transcript. Do not run docker mode on a clone whose history holds
  secrets you would not show the worker.
- Escaping symlinks committed in the base tree, or created by the worker,
  are materialized on the host inside the worktree (they point nowhere
  useful inside the container). dirtywork's own tools never follow them
  and the walker reports them in `run_end.escaping_symlinks`; anything
  *else* the operator runs in that worktree must not follow symlinks
  blindly.
- Host `git status/diff/merge` that the *orchestrator* runs afterwards use
  the operator's global git config; a worker-authored `.gitattributes` can
  trigger configured filters (git-lfs). Documented with the recommendation
  to review with `GIT_CONFIG_GLOBAL=/dev/null` or after inspecting
  `.gitattributes` changes.
- Disk growth during a single `bash` call is bounded by sampling, not by a
  kernel quota.

### 8. Testing

- Unit: `DockerSandbox` over an injectable `_run(argv, stdin=None)`; tests
  assert the exact `docker create/start/exec/top/kill/rm` argv (mounts,
  limits, env, labels, name), the stdin write path, the reset path, the
  collision logic, and `stop` idempotency. `HostSandbox` reuses
  `test_tools_bash.py`/`test_tools_files.py`.
- Live (`-m live`, needs Docker), against a real temp repo, with **host
  sentinels** captured before and compared after: parent `.git/config`
  bytes, `refs/` listing, object-store file list + hashes, a sentinel file
  outside the worktree, a network sentinel. Cases: write inside `/work`
  appears on the host; `python3 -c 'open("/etc/x","w")'` → permission
  error; `git config core.hooksPath x` writes only `/gitdir/config`;
  `curl` fails; `git status` works with the `GIT_DIR` mapping; a
  `nohup … &` writer is dead after the call returns; timeout kills `sleep
  600` and the run continues; fork bomb → reset; a `.Git` overwrite on a
  case-insensitive host is caught by the post-run gate; nested
  `payload/.git/` is caught; over-budget write ends the run with
  `budget_exceeded`; killing the dirtywork process stops the container.

---

## Sub-project 3: Extensibility

Starts only after sub-project 2's live suite is green.

### 1. Tool registry (`dirtywork/toolspec.py`)

```
@dataclass(frozen=True)
class ParamSpec: type: str; description: str = ""; default: Any = MISSING
@dataclass(frozen=True)
class Caps:                       # enforced generically by the registry
    fs: Literal["none", "read", "write"]
    network: bool = False
    max_input_bytes: int | None = None
    max_output_chars: int = 8000
    timeout_default: int | None = None
    timeout_max: int | None = None
    transcript: Literal["full", "preview", "none"] = "preview"
@dataclass(frozen=True)
class ToolSpec:
    name: str; description: str
    params: dict[str, ParamSpec]; required: tuple[str, ...]
    fn: Callable[[Sandbox, ...], str]
    caps: Caps
@dataclass(frozen=True)
class ToolResult: text: str; kind: Literal["ok", "error", "blocked"]
class ToolRegistry:
    def register(self, spec) -> None
    def schemas(self) -> list[dict]            # OpenAI wire shape
    def execute(self, name, args, *, sandbox, deadline) -> ToolResult
```

`execute` validates `required`/types against the spec (hand-rolled, no
`jsonschema`), rejects unknown parameters, enforces `caps` (input size,
output cap, timeout clamp to deadline and `timeout_max`), and returns
`kind="blocked"` for `BLOCKED:` results (writing the `guardrail_block`
event). All tools run in the sandbox (`fn` receives it), so there is no
host/sandbox domain flag. The six tools become specs; `TOOL_SCHEMAS`,
`ToolExecutor`, and the runner's ad-hoc `except TypeError` go away.

### 2. Provider adapters (`dirtywork/providers/`)

Contract first: `tests/provider_contract.py` is a shared suite every adapter
must pass (system prompt handling; a turn with parallel tool calls; a
malformed tool call; tool results in order; `finish_reason` mapping; usage
normalization; a `max_tokens` cut-off mid-call). It runs against recorded
fixtures for both wire formats.

```
@dataclass class ToolCall: id: str; name: str; arguments: dict | None; error: str | None
@dataclass class ChatResponse: text: str; tool_calls: list[ToolCall]; finish_reason: str | None; usage: dict
class Provider(Protocol):
    name: str
    def list_models(self) -> list[str]
    def context_window(self, model: str) -> int | None
    def chat(self, model, history, tools, *, temperature, max_tokens, timeout) -> ChatResponse
```

The runner keeps a provider-neutral history and never sees wire shapes.
`OpenAICompatClient` (rename of `LMStudioClient`, alias kept one release)
absorbs `_valid_tool_call`/`_canonical_tool_call`/usage sanitizing as
deserialization. `AnthropicClient`: urllib, `ANTHROPIC_API_KEY` read
host-side, top-level `system`, `tool_use`/`tool_result` blocks, `/v1/models`
for preflight, `input_tokens/output_tokens` → prompt/completion. The
Anthropic adapter is written *after* the contract suite passes for the
OpenAI adapter, so the neutral history is shaped by two real formats.
`trim_messages` operates on the neutral history. `CONTEXT_WINDOWS` becomes
per-provider defaults with `--context-window` as an override. CLI:
`--provider openai|anthropic` (default `openai`), `--base-url` (default per
provider). `run_start` records `provider`.

### 3. Transcript schema versioning

`schema_version: 2` on `run_start` and in the stdout JSON.
`docs/transcript-schema.md` documents every event (`run_start`, `assistant`,
`tool_result`, `guardrail_block`, `sandbox_reset`, `run_end`) and field; v1
= the pre-hardening shape, v2 = v1 + provenance + `sandbox` + `provider` +
new statuses (`budget_exceeded`, `tampered`, `sandbox_error`). `run.json`
is written at start and updated at end (sub-project 2).

### 4. Run inspection and cleanup (`dirtywork runs …`)

- `runs list` — slug, status, started, branch, worktree present, container
  state (from `run.json`, `git worktree list --porcelain`, and `docker ps
  -a --filter label=dirtywork.run`).
- `runs show <slug>` — `run.json` plus a tool-call timeline from the
  transcript.
- `runs clean <slug> | --all [--keep-transcript] [--force]` — `docker rm
  -f` of the labeled container, `git worktree remove --force`, `git branch
  -D`, run dir; refuses a worktree with uncommitted changes unless
  `--force`. Only ever removes containers whose labels match.
- `runs verdict <slug> accept|reject|cleanup [--note …]` — appends to
  `run.json` so "% needing human cleanup" and "orchestrator rejection rate"
  are measurable.

### 5. Benchmark suite (`bench/`)

- `bench/repos/<name>/` — 3–4 tiny fixture repos committed as plain
  directories (`git init`ed into a temp copy at bench time), each with a
  `bench.json` (`task`, `acceptance`) and an `acceptance/` directory
  holding the harness (tests, expected outputs, hashes of harness files).
- `dirtywork bench --models <m>[,<m>…] [--provider …] [--repeats N] [--tasks …]`
  runs each (model × task × repeat) through the normal `run` path, then
  runs acceptance in a **fresh** container with the worktree at `/work`
  and `acceptance/` mounted read-only at `/acceptance`; harness files
  inside `/work` are compared to the recorded hashes and any mismatch marks
  the run `gamed`. Acceptance commands never come from the worktree.
- Results append to `~/.dirtywork/bench/<stamp>.jsonl`: model, task,
  repeat, status, turns, tokens, wall seconds, guardrail blocks, sandbox
  resets, diff stat, acceptance pass/fail/gamed, run slug. `bench summarize
  <file>` prints completion rate, acceptance rate, mean tokens and latency
  per model.

### Sequencing

Registry → providers (contract suite, OpenAI, then Anthropic) →
schema/`run.json` docs → `runs` commands → bench.

---

## Non-goals

- Seatbelt / bubblewrap backends (later backends behind `Sandbox`).
- Auto-detecting devcontainer images.
- Kernel-level disk quotas on the worktree (not portable; documented bound
  instead).
- Any UI. `runs` and `bench` are CLI-only.
- Human-in-the-loop timing beyond `runs verdict`.
