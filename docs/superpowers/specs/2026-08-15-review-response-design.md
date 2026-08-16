# Review response: hardening, sandbox, extensibility

**Date:** 2026-08-15 (v3, after a third external review; the sandbox's
worktree model changed from bind mount to volume + validated export)
**Status:** Approved direction; v3 design pending user review
**Origin:** Three external (ChatGPT) reviews — of v0.2.0, of v1, and of v2 of
this document. Every claim was verified against the code, and every
mechanism marked *verified* below was demonstrated by a real experiment on
this macOS host (Docker Desktop, daemon 29.7.2, Python 3.9.6, git 2.x).
*Unverified* items are called out individually.

## Purpose

Move dirtywork from "guardrailed harness for a confused model" to "sandboxed
harness that is also safe against a malicious model or repository", without
losing the properties that make it useful: worktree isolation, no
auto-commit, machine-readable output, a complete JSONL transcript, and a
Python-stdlib-only runtime. Then make it extensible enough that a new tool,
a new provider, or a benchmark run is one small addition rather than surgery
on `tools.py` and `runner.py`.

**Threat model (docker mode).** A malicious worker model and/or a malicious
target repository must not be able to:

- (a) write anything on the host except inside the run's worktree and
  `~/.dirtywork/runs/<slug>/` — and the worktree only through dirtywork's
  export validator (§SP2.7);
- (b) modify the parent repository's shared git state (config, hooks, refs,
  objects) beyond dirtywork's own bookkeeping (the worktree entry, the run
  branch, one line in `info/exclude`);
- (c) execute code on the host — no host process consumes worker-produced
  bytes except dirtywork's own tar validator and text/JSON handling; host
  git never reads worker content;
- (d) exhaust host resources. Memory (including tmpfs), CPU, process count,
  and per-file size are **kernel-enforced** limits. Total disk is a
  **best-effort bound**: worktree bytes/entries are sampled during commands
  and a host free-space floor is polled; a burst inside one sampling
  interval (0.5–5 s) can exceed the limit before the container is killed.
  The exported tree is hard-capped by the validator. A portable kernel disk
  quota is not available without root; this is stated in the docs, not
  hidden;
- (e) reach the network (default `--network none`).

The dirtywork process is trusted. The operator's clone is trusted only to
the extent that *read-only* git commands on it are safe (git clone does not
import config or hooks); dirtywork does not try to be safer than
`git status` inside the operator's own clone. Known, accepted exposures are
listed under "Residual exposures".

**Success criteria**

- The three reproduced denylist bypasses (`git -C ../.. config …`,
  `git -c … push`, `python3 -c 'open("/tmp/…","w")'`) fail *inside the OS
  boundary* in the default mode, proven by host-sentinel tests.
- A repository that ships a symlinked `CLAUDE.md` or `.worktrees` cannot make
  dirtywork read or write outside the repo.
- Every model- or repo-controlled input has an explicit bound.
- The worker's tree reaches the host only through the export validator; a
  malicious in-tree `.gitattributes` plus an operator-configured local git
  filter executes nothing on the host (proven by a sentinel test).
- The stdout JSON contract is unchanged (new fields only). **The default
  execution mode changes in 0.3** (Docker); that is a documented breaking
  change with an exit-2 hint, not silent breakage.
- Adding a tool is one `ToolSpec`; adding a provider is one adapter class
  passing the shared contract tests; `dirtywork bench` produces per-model
  completion/acceptance/token/latency numbers.

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
| 2 | `CLAUDE.md`/`AGENTS.md` read through symlinks, unbounded size | Yes — `is_file()` follows links | Read from the **base commit**, not the filesystem: `git -C <repo> ls-tree <base_commit> -- CLAUDE.md AGENTS.md`; accept only mode `100644`/`100755` blobs; `cat-file -s` must be ≤ `MAX_READ_BYTES` (5 MB); `cat-file -p` gives raw bytes (no smudge filters); inject ≤ `MAX_CONTEXT_CHARS` (32 000) with a truncation marker. Symlink entries (mode `120000`) and gitlinks are ignored. One code path for both modes; no filesystem race is possible. |
| 3 | `.worktrees` symlink unchecked; final destination unchecked | Yes — a repo committing `.worktrees -> /outside` places the worktree outside the clone (verified) | (i) `os.lstat(repo/".worktrees")`: proceed only if ENOENT or `S_ISDIR` (never follow). (ii) `os.lstat(repo/".worktrees"/f"dw-{slug}")` **must be ENOENT** — a pre-existing directory, file, or symlink at the exact destination aborts before `git worktree add` (otherwise git would create through a symlink and a later `worktree remove` would clean an outside directory). (iii) After `git worktree add`, require `(repo.resolve()/".worktrees") in worktree.resolve().parents` (pathlib join of the resolved repo — never `.resolve()` the joined path; that variant passes wrongly, verified). On (iii) failure remove worktree + branch, raise `WorkspaceError`. Applies to both modes. |
| 4 | `info/exclude` path not validated | Yes | Resolved `git rev-parse --git-path info/exclude` must be inside resolved `git rev-parse --git-common-dir`; open with `O_NOFOLLOW`; else abort. |
| 5 | HTTP error body read fully before slicing | Yes | `e.read(500)`. |
| 6 | Transcript/run dir created with default perms; `~/.dirtywork` never validated | Yes | `~/.dirtywork` and `runs/`: `os.mkdir(mode=0o700)` ignoring EEXIST, then `lstat` must be `S_ISDIR` and owned by `os.getuid()` (POSIX). Per-run dir: `mkdir(mode=0o700, exist_ok=False)` — a pre-placed symlink raises EEXIST → abort. Transcript: `os.open(O_WRONLY\|O_CREAT\|O_EXCL\|O_APPEND\|O_NOFOLLOW, 0o600)`. `O_EXCL` also turns a slug collision into a loud failure. |
| 7 | Missing provenance | Yes | `run_start` gains `base_commit`, `branch`, `branch_from`, `base_url`, `dirtywork_version`, `temperature`, `sandbox`, `provider`; `run_end` gains `diff_stat` (capped). |
| 8 | Unbounded `write_file` content, `list_dir` rows, assistant text | Partly — transitively bounded by the 64 MB response cap | `write_file` refuses content > `MAX_WRITE_BYTES` (5 MB); `list_dir` stops after 2 000 entries with a marker; assistant `text` in the transcript capped at 64 000 chars (full text still goes to the model). |
| 9 | `write_file` has no regular-file guard; a FIFO in the worktree hangs the process (Linux) | Yes (verified) | Host-mode file tools open with `os.open(... \| O_NOFOLLOW \| O_NONBLOCK \| O_CLOEXEC)`, `fstat`, require `S_ISREG` (and size ≤ cap for reads), then clear `O_NONBLOCK` and use `os.fdopen`. Removes the FIFO hang and closes the final-component symlink TOCTOU. (Docker mode runs tools inside the container; §SP2.5.) |
| 10 | Worktree disk growth unbounded | Yes | Host-mode `worktree_budget` walker: `os.fwalk` on POSIX (dir-fd relative, `follow_symlinks=False`; on Windows `\\?\` paths and skip reparse points), sums `st_blocks*512` where available else `st_size`, counts every entry, aborts early when `--max-worktree-mb` (2048) or `--max-worktree-files` (200 000) is exceeded, treats an unreadable directory as a violation (fail closed), and reports symlinks whose target is absolute or escapes the worktree. Host mode runs it after every tool call → status `budget_exceeded`. The same two caps bound the docker export (§SP2.7). |
| 11 | `.git/info/exclude` is modified in the main checkout; docs don't say so | Yes | One sentence in README and `SECURITY.md`. |
| 12 | 16-bit slug salt | Overstated — `worktree add -b` already fails on a branch collision | `token_hex(4)`; #6's `O_EXCL` covers the transcript side. |
| 13 | "Should not market itself as secure" | Not applicable — README/SECURITY.md already say "not a sandbox" | No change until sub-project 2 lands; then rewrite to describe the sandboxed default accurately. |

**Testing:** each item gets a unit test in the existing module for its file.
Symlink/FIFO tests use `tmp_path` with real symlinks, `os.mkfifo`, and real
`git init` repos, as the existing workspace tests do. Row 3(ii) gets a test
with a pre-placed symlink at the exact destination.

**Out of scope:** anything that needs a process boundary (sub-project 2).

---

## Sub-project 2: Docker sandbox backend

### Decision record

- **Docker first** (not Seatbelt): a real OS boundary with cgroup quotas,
  and the only backend that can plausibly serve Windows hosts.
  `sandbox-exec` verified working on macOS 26.6 and remains a candidate
  later backend behind the same interface.
- **Docker is the default in 0.3.** `--sandbox none` is the explicit opt-in
  to today's host behavior. Docker missing or daemon down → preflight error,
  exit 2, hint printed. No silent fallback. This is a breaking change,
  called out in the changelog and README.
- **The worktree lives on a Docker volume, not a bind mount.** The host
  worktree directory stays empty (only its `.git` file) for the whole run;
  the worker's tree is exported to it afterwards through a validated tar
  stream (§7). Reasons, in order: (1) host git never touches worker
  content, so a hostile `.gitattributes`/local filter cannot execute on the
  host; (2) no host path is ever inside the container, so the bind-mount
  attack surface of v2 (overmounted `.git`, case-insensitive `.Git`
  overwrite, TOCTOU on host-side tools, uid mapping) disappears; (3)
  volumes run at native speed on Docker Desktop, where bind mounts are
  5–10× slower for build-style workloads on macOS and worse on Windows;
  (4) the Windows surface shrinks to "run `docker` and extract a tar".
  Costs, stated plainly: an export step; **git-ignored files (build
  outputs, `node_modules`, `bin/obj`) are not exported**; the host worktree
  is empty until the run ends; the exported tree must fit the export
  container's object store. Kernel disk quota is *not* gained by this
  choice — see the threat model.
- **Image via `--image` with a maintained default** (`dirtywork/worker`,
  built from `docker/Dockerfile` in this repo: Debian-based, `USER worker`
  (uid 1000), git, bash, coreutils, findutils, python3, node, .NET SDK,
  ripgrep). The reference in code is pinned by digest. Preflight resolves
  the digest via `docker image inspect --format '{{json .RepoDigests}}'`
  (fallback `.Id` for local builds), pulls explicitly if absent (the only
  network use at start), and always runs `<name>@sha256:<digest>`. The
  digest is recorded in `run.json` and `run_start`.
- **All six tools run inside the container** via `docker exec`.
- **Every export step runs in a fresh container**, never in the worker's
  container: no worker process is alive, `/gitdir` (and any `git config`
  the worker wrote there) is gone, `/tmp` is clean, and the volume is
  mounted read-only.
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
    def finalize(self) -> RunArtifacts   # diff_stat, patch_path, worktree_bytes,
                                         # worktree_files, escaping_symlinks,
                                         # dropped_git_entries, export_status
    def stop(self) -> None
```

`HostSandbox` wraps today's `tools.py` functions unchanged (plus SP1
hardening); its `finalize` runs the walker and `git diff --stat` on the host
(host mode is the mode where the operator accepted host-side git on worker
content). `DockerSandbox` implements every operation via `docker exec` and
`finalize` via the export flow (§7). The runner/`ToolExecutor` (later the
registry) dispatches to the sandbox; model-facing result formats do not
change.

### 2. Host-side flow

1. Preflight (read-only on the operator's clone): `git rev-parse
   --is-inside-work-tree`, `rev-parse HEAD`; `docker version` (10 s
   timeout); image digest resolution/pull as above.
2. `ensure_worktrees_excluded` with the SP1 path check.
3. SP1 `.worktrees` checks including the exact-destination `lstat`, then
   `git worktree add --no-checkout -b dirtywork/<slug> .worktrees/dw-<slug> <ref>`
   (verified: leaves only the `.git` file; no index; no checkout hooks run).
   Post-check location. Record `base_commit = git -C <wt> rev-parse HEAD`.
4. Run dir `~/.dirtywork/runs/<slug>/` (SP1 rules); write `run.json`
   `{status:"running", slug, repo, worktree, branch, base_commit,
   container, volume, image, image_digest, host_pid, started}` **now**.
5. Volume: `docker volume create --label dirtywork.run=<slug>
   --label dirtywork.repo=<sha256(resolved repo path)> dw-<slug>-work`,
   then a throw-away prep container
   `docker run --rm --network none --user 0:0 --cap-drop ALL --cap-add CHOWN
   --mount type=volume,src=dw-<slug>-work,dst=/work <image>@<digest>
   chown <uid>:<gid> /work` (verified: a fresh volume's root is
   root-owned; after this the run user can write).
6. Start the worker container (§3) and wait until `docker exec <c> true`
   succeeds (60 s).
7. In-container init (§4). Any non-zero exit → teardown, status
   `sandbox_error`, exit 1.
8. `load_repo_context(repo, base_commit)` on the host (SP1 row 2, git-based).
9. Agent loop; every tool call goes through the sandbox (§5); the watchdog
   runs for the container's whole lifetime (§6).
10. Stop the worker container: `docker rm -f` (also in the CLI `finally`).
11. Export (§7) into the still-empty host worktree; then the **only**
    host git command that runs after the worker has produced anything:
    `git -C <wt> read-tree HEAD` (index only, base tree, operator's own
    objects; verified it writes no working-tree files) with
    `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
    -c core.hooksPath=/dev/null -c core.fsmonitor=false`, so host
    `git status`/`git diff` are meaningful. Remove the volume unless
    `--keep-volume`. Update `run.json`.
12. `run_start` records `sandbox: {backend, image, image_digest, network,
    memory, cpus, pids_limit, tmp_size, gitdir_size, max_worktree_mb,
    max_worktree_files, user}`. `schema_version: 2`.

### 3. Worker container: creation, mounts, limits, lifetime

```
docker create -i --init --name dw-<slug> \
  --label dirtywork.run=<slug> --label dirtywork.repo=<sha256(resolved repo path)> \
  --network none --memory 4g --memory-swap 4g --cpus 2 --pids-limit 512 \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user <uid>:<gid> \
  --tmpfs /tmp:rw,exec,size=1g,mode=1777 \
  --tmpfs /gitdir:rw,size=512m,mode=0700,uid=<uid>,gid=<gid> \
  --tmpfs /home/worker:rw,size=256m,mode=0700,uid=<uid>,gid=<gid> \
  --mount type=volume,src=dw-<slug>-work,dst=/work \
  --mount type=bind,src=<git-common-dir>/objects,dst=/repo.git/objects,readonly \
  -e GIT_DIR=/gitdir -e GIT_WORK_TREE=/work -e HOME=/home/worker -e TMPDIR=/tmp \
  -e LANG=C.UTF-8 -e GIT_AUTHOR_NAME=dirtywork -e GIT_AUTHOR_EMAIL=dirtywork@localhost \
  -e GIT_COMMITTER_NAME=dirtywork -e GIT_COMMITTER_EMAIL=dirtywork@localhost \
  <name>@sha256:<digest> cat
```

- **Never pass `-w`/WORKDIR at container level.** Verified: on this daemon
  a container-level `-w /work` over the volume resets the volume root's
  ownership to `root:root`, persistently, and the run user can no longer
  write. Every command uses `docker exec -w /work …` (verified harmless) or
  `cd /work` inside the command.
- **Lifetime tether (verified in all four forms).** `--init` makes tini
  PID 1; its only child is `cat` reading the container's stdin. The host
  runs `docker start -ai dw-<slug>` as a `Popen` with `stdin=PIPE`
  (stdout/stderr → devnull) and holds the write end. Verified: `create` +
  `start -ai` attaches; SIGKILL of the attach process stops the container;
  SIGKILL of the *parent Python process* closes the pipe and stops the
  container; closing stdin stops it; after `docker kill`, the attach exits
  (rc 137, ~90 ms) and a fresh `docker start -ai` re-attaches.
- **Reset** (used on timeout, on stragglers, on OOM): `docker kill
  dw-<slug>` (SIGKILL PID 1 → whole namespace dies; tmpfs wiped; the
  volume and its contents persist — verified), wait for the `Popen` to
  exit, fresh `docker start -ai`, re-run init (§4, restart variant). Emits
  a `sandbox_reset` transcript event with the reason. The working tree
  survives a reset; the worker's git metadata (index, stashes, commits in
  `/gitdir`) does not — stated in the `bash` tool description.
- **User.** POSIX hosts: the invoking user's uid:gid (so the read-only
  parent object store is readable under any umask; verified working on
  Docker Desktop with an arbitrary uid). Windows: `1000:1000`. tmpfs
  `uid=/gid=` must match or the 0700 dirs are unwritable (verified).
- **tmpfs and memory.** tmpfs pages are charged to `--memory` (verified;
  overflow is an OOM kill, not ENOSPC). Sum of tmpfs sizes (1g + 512m +
  256m) stays under half of `--memory`; `--memory-swap` equals `--memory`
  so nothing spills into host swap. `--tmpfs` defaults to `noexec`; `/tmp`
  gets `exec` explicitly (build tools run scripts from it); `/gitdir` and
  `/home/worker` stay `noexec`.
- **`--mount` only, never `-v`** (a missing source is an error, not a
  silently created directory — verified).
- **Name collision.** If `dw-<slug>` (container) or `dw-<slug>-work`
  (volume) exists: inspect labels. Labels match and container not running
  → remove and proceed. Labels match and running → refuse (exit 2; another
  live run). Labels absent or different → refuse. Never remove a container
  or volume solely because the name matches.
- **Nothing** from `<repo>/.git` other than `objects/` is mounted (no refs,
  config, hooks, `worktrees/`), and no host path other than that directory
  is visible inside any container.
- **Docker control plane.** Every `docker` invocation goes through one
  `_run(argv, *, timeout, stdin=None)` with an explicit timeout: `version`
  /`inspect`/`top`/`volume *` 10 s; `create`/`start`/`exec true`/`rm -f`
  /`kill` 60 s; `pull` 600 s; tool `exec`s the tool's own timeout + 10 s;
  export steps 300 s each. Expiry → status `sandbox_error` (`run_end.error`
  says which command), then best-effort teardown (`docker rm -f`, `volume
  rm`) each with its own timeout, never blocking the exit. Whatever is
  left is visible to `runs list` and removable by `runs clean`. A hung
  Docker CLI therefore fails closed instead of hanging the run.
- CLI: `--sandbox docker|none`, `--image`, `--allow-network` (default
  bridge network), `--memory`, `--cpus`, `--tmp-size`, `--gitdir-size`,
  `--max-worktree-mb`, `--max-worktree-files`, `--min-free-mb`,
  `--keep-volume`.

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
```

Verified: `status/diff/add/stash/log/commit/gc/repack` all work; new objects
land only in `/gitdir`; the parent object store is byte-identical before and
after, including through `gc`/`repack`; `git config` writes only
`/gitdir/config`. A hostile repo's checkout behavior (symlinks, attributes,
long paths, case collisions) is interpreted inside the container.

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
  in-container process is killing incoming shells — verified; output is a
  header row plus `/sbin/docker-init -- cat` and `cat` when idle —
  verified). Any other row → reset (§3). Also `docker inspect
  .State.OOMKilled` → reset. This restores the documented contract
  ("backgrounded processes are terminated when the command returns"),
  which plain `docker exec` does not honor (verified).
- **Watchdog thread** for the container's lifetime:
  - every 0.5 s: host `shutil.disk_usage` on the filesystem holding
    Docker's storage (Linux: the data-root from `docker info`; Docker
    Desktop: the user's home volume, where the VM disk image lives) and on
    `/`; if the smaller `free` drops below `--min-free-mb` (2048) → kill,
    status `budget_exceeded`. Verified that `df` *inside* the container is
    useless for this on Desktop: the VM disk is a sparse image that reports
    ~2 TB free regardless of host free space;
  - after every `bash` call and every 5 s while one is in flight:
    `docker exec dw-<slug> sh -c 'du -sk /work; find /work | wc -l'` (10 s
    timeout). Over `--max-worktree-mb`/`--max-worktree-files` → kill,
    `budget_exceeded`. If the exec itself fails (pid saturation, hostile
    process killing shells) → reset, then re-measure; a second failure →
    `sandbox_error`.
  Between calls no worker process exists (reaped), so growth can only
  happen during a call. This is the best-effort bound the threat model
  describes.
- Fork bombs are contained by `--pids-limit` (verified) and cleared by the
  reap/reset.

### 7. Export: worker tree → host worktree

Runs after the worker container is gone, in a **fresh** container created
like §3 but with `--pids-limit 256`, tmpfs sizes `/gitdir` `2g` (only git
runs here; sized for the whole tree's new objects), `/tmp` `256m`,
`/home/worker` `64m` (sum ≈ 2.3g under `--memory 4g`), the volume mounted
**read-only**, and no `HOME`-relative anything the worker could have
written. Steps, each a separate `docker exec` with its own timeout:

1. Init exactly as §4 (restart variant, index only) — a clean `/gitdir`,
   so no worker-written config, hooks, or filters exist.
2. `find /work -iname .git -mindepth 1` → `dropped_git_entries` (git never
   adds `.git`-named entries — verified: nested `.git` directories and
   `gitdir:` files are silently skipped by `git add -A`; we report them
   instead of silently dropping).
3. `git add -A` (honors the *worker's* `.gitignore` in the tree and the
   clean `info/exclude`; nonzero exit → `export_failed`).
4. `T=$(git write-tree)`; `git diff --stat <base_commit> $T` (capped 64 000
   chars → `run_end.diff_stat`); `git diff <base_commit> $T` streamed to
   `~/.dirtywork/runs/<slug>/diff.patch` with a `--max-patch-mb` cap (10;
   truncated with a marker). This patch is the **git-free review path**
   (`runs show --diff`, SP3).
5. `git archive --format=tar $T` streamed from the exec's stdout straight
   into the validator on the host; bytes read are capped at
   `--max-worktree-mb` (the stream is aborted and the run marked
   `export_failed` beyond it).
6. `docker rm -f` the export container; `docker volume rm` unless
   `--keep-volume` (or on `export_failed`, so `runs export <slug>` can
   retry after the operator raises a limit).

**Validator (host, Python `tarfile` in stream mode; no reliance on
`tarfile.data_filter`, which this project's Python 3.9 floor lacks):**

- The destination must contain exactly one entry, the `.git` file, before
  extraction; otherwise refuse (`export_failed: worktree not empty`).
- Member count ≤ `--max-worktree-files`; running byte total ≤
  `--max-worktree-mb`.
- Allowed member types: regular file, directory, symlink. Anything else
  (hard link, device, FIFO, PAX global/other) → refuse.
- Name rules: relative; no empty, `.`, or `..` components; no component
  equal to `.git` case-insensitively (git cannot emit one; its presence
  means a broken or hostile image → refuse the whole export).
- Before creating each member, `os.path.realpath(dest/name)` must lie
  under `realpath(dest)` — so no member can be written through a symlink
  created by an earlier member (verified: `a -> /etc` then `a/x` is refused;
  a `sub/.Git/h` member is refused).
  Symlink members are created as symlinks on POSIX; targets are not
  validated for creation (creating a link is harmless; nothing in
  dirtywork follows one) but every absolute or escaping target is reported
  in `run_end.escaping_symlinks` (verified: `esc -> /etc/passwd` and
  `rel -> ../../../outside` are created and reported). On Windows a
  symlink member becomes a plain file whose content is the link target —
  git's own `core.symlinks=false` behavior, so host `git status` shows it
  unchanged.
- Modes normalized: directories `0755`, files `0644` or `0755` (exec bit
  only); uid/gid/mtime from the archive are ignored (`extract(...,
  set_attrs=False)` plus an explicit `chmod` — verified `set_attrs=False`
  alone leaves directories `0700`).
- On any violation: stop, remove what was extracted, leave the `.git`
  file, status `export_failed` with the reason. Nothing partial is left
  for review.

Verified end to end on this host: worker populates `/work` from the
read-only parent objects, edits/deletes/adds files, adds escaping
symlinks and an ignored file; the fresh export container produces the
tree, `diff --stat`, and archive; the validator extracts into the empty
worktree; host `git read-tree HEAD` + `git status --porcelain` shows
`M`/`D`/`??` correctly and the ignored file never reaches the host.

Statuses added by SP2: `budget_exceeded`, `sandbox_error`,
`export_failed`. (v2's `tampered` is gone: no host-side surface remains for
the worker to tamper with.)

### 8. Docs

README Security section rewritten to describe the sandboxed default
truthfully: what the container blocks, what `--sandbox none` gives up, the
0.3 breaking change, the image requirement, that build outputs stay in the
container and only the git-visible tree is exported, and the residual
exposures below. Two callouts placed **at the top of the Security section
and in the docker-mode quick start**, not only in a residual list:

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

`SECURITY.md`: escapes from docker mode are in scope; `--sandbox none`
keeps today's caveats.

**Residual exposures (documented, accepted):**
- Object-store confidentiality, as above.
- Escaping symlinks (committed in the base tree or created by the worker)
  are created on the host inside the worktree; dirtywork never follows
  them and lists them in `run_end.escaping_symlinks`; anything *else* the
  operator runs in that worktree must not follow symlinks blindly.
- Host `git status/diff/add/merge` that the *orchestrator* runs afterwards
  use the operator's own config; a worker-authored `.gitattributes` can
  trigger configured filters (git-lfs). Documented with the recommendation
  to review via `runs show --diff` (container-computed patch, no host git
  on worker content) or with `GIT_CONFIG_GLOBAL=/dev/null`.
- Disk: best-effort sampled bound (threat model (d)); Docker Desktop's VM
  disk image is sparse and large (2 TB on this host), so it is not itself a
  bound.
- Docker Desktop caches deleted directories: a bind source that was
  deleted and re-created at the same path can be reported as missing for a
  while (hit during prototyping). dirtywork never reuses paths (slugs are
  salted), but bench fixture repos must use unique temp paths per run.

### 9. Testing

- Unit: `DockerSandbox` over an injectable `_run(argv, stdin=None,
  timeout)`; tests assert the exact `docker volume create/run(prep)/create
  /start/exec/top/kill/rm/volume rm` argv (mounts, limits, env, labels,
  names, no `-w`), the stdin write path, the reset path, the collision
  logic, per-command timeouts and the fail-closed path, and `stop`
  idempotency. The validator gets exhaustive unit tests over hand-built
  tars (every rule above, including write-through-symlink, `.Git`,
  hard link, device, count/byte caps, empty-destination check, cleanup on
  failure). `HostSandbox` reuses `test_tools_bash.py`/`test_tools_files.py`.
- Live (`-m live`, needs Docker), against a real temp repo, with **host
  sentinels** captured before and compared after: parent `.git/config`
  bytes, `refs/` listing, object-store file list + hashes, a sentinel file
  outside the worktree, a network sentinel, and an **operator local-config
  filter sentinel** (`git config --local filter.evil.clean/smudge 'touch
  <sentinel>'`, worker writes `.gitattributes` `* filter=evil` — sentinel
  must be absent after the run). Cases: write inside `/work` appears on the
  host after export; `python3 -c 'open("/etc/x","w")'` → permission error;
  `git config core.hooksPath x` writes only `/gitdir/config`; `curl` fails;
  `git status` works with the `GIT_DIR` mapping; a `nohup … &` writer is
  dead after the call returns; timeout kills `sleep 600` and the run
  continues with the working tree intact; fork bomb → reset; nested
  `payload/.git/` reported and not exported; escaping symlink reported;
  ignored file not exported; over-budget write ends the run with
  `budget_exceeded`; export refused into a non-empty worktree.
- **Lifecycle suite (release gate — docker mode is not the default until
  it passes):** dirtywork killed with SIGKILL → container and attach gone,
  volume present, `runs export` recovers the tree; dirtywork killed while
  `docker start -ai` is attached; Docker daemon unavailable mid-run (a stub
  `docker` on `PATH` that hangs → per-command timeout → `sandbox_error`,
  exit within the timeout, nothing hung); container killed during a
  running `docker exec` (`bash` returns an error, run continues after
  reset); reset followed by a fresh `docker start -ai` and successful
  tool calls; after every case, `docker ps -a`/`volume ls` filtered by
  label show nothing the test did not expect, and no `docker` child
  process of the test remains.

---

## Sub-project 3: Extensibility

Starts only after sub-project 2's live and lifecycle suites are green.

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
new statuses (`budget_exceeded`, `sandbox_error`, `export_failed`) +
`run_end.diff_stat/escaping_symlinks/dropped_git_entries/worktree_bytes/
worktree_files`. `run.json` is written at start and updated at end
(sub-project 2).

### 4. Run inspection and cleanup (`dirtywork runs …`)

- `runs list` — slug, status, started, branch, worktree present, container
  and volume state (from `run.json`, `git worktree list --porcelain`,
  `docker ps -a --filter label=dirtywork.run`, `docker volume ls --filter
  label=dirtywork.run`).
- `runs show <slug> [--diff]` — `run.json` plus a tool-call timeline from
  the transcript; `--diff` prints `diff.patch` (the container-computed
  patch; no host git touches worker content).
- `runs export <slug>` — re-run §SP2.7 for a run whose volume still exists
  (crash, `export_failed` after raising a limit); refuses a non-empty
  worktree.
- `runs clean <slug> | --all [--keep-transcript] [--force]` — `docker rm
  -f` of the labeled container, `docker volume rm` of the labeled volume,
  `git worktree remove --force`, `git branch -D`, run dir; refuses a
  worktree with uncommitted changes unless `--force`. Only ever removes
  containers/volumes whose labels match.
- `runs verdict <slug> accept|reject|cleanup [--note …] [--review-seconds N]`
  — appends `{verdict, note, verdict_at, review_seconds}` to `run.json`.
  `verdict_at − ended` is recorded automatically as `time_to_verdict_s`
  (noisy: includes idle time); `--review-seconds` is the operator's
  explicit measure. Together with the status this makes "% needing human
  cleanup", "orchestrator rejection rate", and "review cost per accepted
  run" measurable — completion and acceptance rates alone do not prove the
  economic thesis.

### 5. Benchmark suite (`bench/`)

- `bench/repos/<name>/` — 3–4 tiny fixture repos committed as plain
  directories (`git init`ed into a **uniquely named** temp copy at bench
  time — Docker Desktop's stale-path cache, §SP2.8), each with a
  `bench.json` (`task`, `acceptance`) and an `acceptance/` directory
  holding the harness (tests, expected outputs, hashes of harness files).
- `dirtywork bench --models <m>[,<m>…] [--provider …] [--repeats N] [--tasks …]`
  runs each (model × task × repeat) through the normal `run` path with
  `--keep-volume`, then runs acceptance in a **fresh** container with the
  run's volume at `/work` (rw; the export already happened) and
  `acceptance/` mounted read-only at `/acceptance`; harness files inside
  `/work` are compared to the recorded hashes and any mismatch marks the
  run `gamed`. Acceptance commands never come from the worktree. The
  volume is removed afterwards.
- Results append to `~/.dirtywork/bench/<stamp>.jsonl`: model, task,
  repeat, status, turns, tokens, wall seconds, guardrail blocks, sandbox
  resets, diff stat, acceptance pass/fail/gamed, run slug, and — when the
  operator later records one — verdict and review seconds (joined from
  `run.json` at `bench summarize` time). `bench summarize <file>` prints
  completion rate, acceptance rate, mean tokens and latency per model, and
  verdict rate / median review seconds where verdicts exist.

### Sequencing

Registry → providers (contract suite, OpenAI, then Anthropic) →
schema/`run.json` docs → `runs` commands → bench.

---

## Non-goals

- Seatbelt / bubblewrap backends (later backends behind `Sandbox`).
- Auto-detecting devcontainer images.
- Kernel-level disk quotas on the worktree (not portably available without
  root; best-effort bound instead, stated in the threat model).
- Exporting git-ignored files from the container (build outputs). Operators
  who need them can `--keep-volume` and `docker run` against the volume.
- Windows support claims before the Windows integration suite exists.
- Any UI. `runs` and `bench` are CLI-only.
- Human-in-the-loop timing beyond `runs verdict`.
