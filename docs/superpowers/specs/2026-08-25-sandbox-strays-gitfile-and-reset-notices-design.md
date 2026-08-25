# Sandbox resets: kill strays in place, keep the worker's git state, tell the worker (#61)

**Date:** 2026-08-25
**Status:** Design v1 — approach B (stray ladder + gitfile discovery + #60-carrier notices) chosen
by the owner (2026-08-25 09:14 CDT) with **nine required revisions**, each resolved in the section
the table in §0 names. Not yet red-teamed; not yet approved for plan → execution.
**Origin:** issue #61 (milestone **1.0.0 — contract freeze**), soak finding S11, plus the two
findings the #64 dogfood carried here: S13 (`GIT_DIR`/`GIT_WORK_TREE` exported into every
command) and the "stray process" resets that followed plain foreground commands. Evidence:
`docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md` (S11, S13, the #64 run rows) and the
seven live probes of 2026-08-25 recorded in §1.
**Parent specs:** `2026-08-15-review-response-design.md` (§3 Reset, §4 in-container init, §6
reaping and budgets — this document supersedes those three sections for docker mode),
`2026-08-23-harness-followups-after-tool-results-design.md` (#60: carriers, `follow_up`, `via`),
`2026-08-19-tools-context-timeouts-design.md` (§4.3 timeout nudge), `2026-08-18-run-evidence-and-review-loop-design.md`
(§4 `--verify`).
Ships in **dirtywork 1.0.0**. Stdlib-only, Python 3.9 floor, `schema_version` stays 2 — every
change below is a new sparse field, a new event kind, a new documented enum value, or a new
documented string value of an existing field; never a removed or renamed one.

## 0. The owner's nine revisions and where each is resolved

| # | Finding (2026-08-25 09:14 CDT) | Resolved in |
|---|---|---|
| 1 | Worker/export git environment needs an explicit split; export mounts `/work` read-only | §4.1, §4.2 |
| 2 | Kill-in-place can leave git metadata in a bad state (index lock, partial ref, half object) | §3.5 |
| 3 | Watchdog lock scope: must cover top → kill → top → reset, both sampling paths, no deadlock; P7 is a gate | §3.6, §1.2 (P7 confirmed), §10 |
| 4 | `cat` loophole contradiction; kill every non-tether PID or only detector-classified strays; tether PID discovery and refresh | §3.1, §3.2, §3.3 |
| 5 | Notice delivery on verify, mixed `finish` turns, early aborts, turns with no carrier | §5.2 |
| 6 | Nested-repository export: exclude the nested root, robust to spaces/newlines, overlap with tracked paths, committed vs uncommitted | §4.4 |
| 7 | Root `.git` file lifecycle: missing/directory/symlink, tampering, first start/warm/reset | §4.3 |
| 8 | Exact event schemas, limits, ordering, empty-list rule, where counts surface | §6 |
| 9 | .NET wording: four variables, derived-image snippet shows all of them | §7 |

## Purpose

In docker mode, "backgrounded processes are terminated when the command returns" (the `bash`
tool's own description) is enforced by `docker kill` — the whole container namespace dies and its
tmpfs `/gitdir` with it. The worker's index, stashes, local commits and branches vanish and the
worker is not told. The soak lost real work this way (S11: a `git stash` … `dotnet test` …
`git stash pop` → "No stash entries found"; the run finished `completed` with the deliverable
incomplete). Two more facts make it worse than a rare edge:

- `.NET` leaves a Roslyn compiler server (`VBCSCompiler`) behind after every `dotnet build/test`,
  so every build call resets the container (10 of the 26 resets on record).
- dirtywork's own watchdog sample (`docker exec … du -sk /work; find /work | wc -l`) is visible to
  `docker top`; when the worker's command ends while that sample is in flight, the harness calls
  its own exec a stray and resets (7 of 26 — after plain `sed`/`grep`/`cat`; reproduced live, §1.2).

Host mode already honours the contract by SIGKILLing the command's process group in place
(`procs.py`, `security.md` §"process group"). This spec makes docker mode do the equivalent — kill
strays in place, escalate to a reset only when that fails — keeps the worker's git state through it,
fixes the root cause of S13 while it is in the same code (the `GIT_DIR`/`GIT_WORK_TREE` environment,
replaced by a gitfile), and tells the worker exactly what happened through the carrier #60 built.

## 1. The facts (measured 2026-08-25, seven live probes, all on this macOS host / Docker Desktop 29.7.2)

### 1.1 What caused the 26 resets on record (P5: every `~/.dirtywork/runs/*/transcript.jsonl`)

All 26 have reason `stray process after bash`; 25 are written between a bash `tool_result`'s call
and its result (the reset happens inside that call's `_after_bash`). By the command that triggered
them:

| class | n | trigger | after this spec |
|---|---|---|---|
| B | 10 | bare `dotnet build`/`dotnet test`, normal exit; `VBCSCompiler` resident afterwards (P3) | none on the 1.0 image (§7); a `stray_kill` on older images (§3) |
| E | 7 | plain foreground `sed -n`, `grep … \| head`, `cat … \| python3 -m json.tool`, `python3 acceptance/check_sum_range.py`, exit 0 | the watchdog race (§1.2) — eliminated (§3.6) |
| A | 5 | model backgrounding: `bash build.sh &`, `timeout 25 bash ./build.sh &`, a `python3 … http.server &` | `stray_kill`, state kept, worker told (§3, §5) |
| D | 4 | `bash build.sh` hit the 120 s bash timeout (`timed_out: true`); the abandoned exec's process is the stray | `stray_kill` (§3.4) + the existing timeout nudge |
| C | 0 | test suites spawning children — dirtywork's own suite leaves nothing (P4: 3 full runs + per-file runs, `docker top` tether-only every time) | — |

The one reset not tied to a worker call (`reportsh-…-1226d9e4`) belongs to the `--verify` command's
own `sandbox.bash` (the `verify` event is written after `_after_bash` ran). No run ended
`sandbox_error` or `budget_exceeded` (223 runs). No transcript shows the model noticing lost
state in words — S11 was found by reading the diff.

### 1.2 The watchdog race — confirmed (P7, production code, `DockerSandbox` + a started `Watchdog`)

`watchdog.py` `run()`: every 0.5 s tick, if a bash call is in flight and ≥ 5 s have passed since the
last worktree sample, it calls `check_worktree_budget_once()` → `DockerSandbox._sample_worktree()`
→ one `docker exec <c> /bin/sh -c "du -sk /work; find /work | wc -l"`. Between a model's bash calls
more than 5 s always pass, so a sample is due on essentially every call. `bash()` →
`_after_bash()` → `_reap()` → `docker top` runs the moment the command returns; if the sample exec
is still alive, its rows are strays:

```
UID  PID    PPID   C  STIME  TTY  TIME      CMD
501  6223   6200   0  14:03  ?    00:00:00  /sbin/docker-init -- /bin/cat
501  6236   6223   0  14:03  ?    00:00:00  /bin/cat
501  12895  6200   0  14:04  ?    00:00:00  /bin/sh -c du -sk /work; find /work | wc -l
501  12901  12895  50 14:04  ?    00:00:00  du -sk /work
```

Measured: 40 × (5.2 s idle, then `bash("sed -n 1,3p README.md")`, ~75 ms) → **4 resets**;
the same 40 with the watchdog thread stopped → **0**; a forced overlap (sample slowed by 2 s,
bash stretched to 1 s) → **5/5**. The hit rate rises with a slower `/work` sample and longer
commands (pytest, builds), so production sees more than the measured 10 %.

Second defect, same probe: `reset()` sets `self._reset_this_call = True` only after kill → wait →
tether → init. The `docker kill` aborts the in-flight sample; `_sample_worktree` then reads the flag
still `False`, takes its "reset once and retry" branch, waits on `_reset_lock` and performs a
**second** full reset (`budget sample failed`) — every forced-overlap iteration produced two
`sandbox_reset` events. Had the flag been read a moment later, `_sample_worktree` raises inside the
watchdog thread → `violation_kind = "sandbox_error"` → the next tool call ends the run
`sandbox_error`. Not seen in any recorded run; one lost race away.

### 1.3 `.NET` daemons (P3, image `dirtywork-worker-dev:issue63`, SDK 8.0.424 + 10.0.400, offline)

Without extra environment, the first `dotnet build` leaves `VBCSCompiler … -pipename:…` running
(same PID reused by later net8.0 and net10.0 builds, still there 70 s later). With
`DOTNET_CLI_USE_MSBUILD_SERVER=0 MSBUILDDISABLENODEREUSE=1 UseSharedCompilation=false DOTNET_NOLOGO=1`
set at `docker run`, `docker top` is tether-only after every one of `new`, `build`, `build` (×2
frameworks). Second-build wall time: 0.459 s vs 0.436 s (net8.0), 0.456 s vs 0.441 s (net10.0)
— no cost on the single-project case tested. `dotnet new xunit` cannot restore offline (NU1301), so
`dotnet test` was not exercised.

### 1.4 Gitfile discovery (P1, `dirtywork-worker-pytest:0.10`, git 2.39.5)

With no `GIT_DIR`/`GIT_WORK_TREE` on the container, from `/work`:
`git init -q --template= --separate-git-dir=/gitdir` writes `/work/.git` = `gitdir: /gitdir`,
`/gitdir/config` has `bare = false` and no `core.worktree`; the existing tail (alternates,
`symbolic-ref`, `update-ref`, `read-tree -m -u HEAD`) populates `/work`, `git status` is clean,
`--show-toplevel` = `/work` and `--git-dir` = `/gitdir` from `/work` and from `/work/dirtywork`.
S13 is gone: `git init` in `/tmp/x`, and in Python's `tempfile.mkdtemp()` via `subprocess`, stays
local (`--git-dir` `.git`, `worktree list` `/tmp/x`, add/commit work, `/gitdir` untouched); the
same commands with `GIT_DIR` exported reproduce the bug exactly. Stash push/list/pop and commits
work; new objects land only in `/gitdir/objects`; the read-only `/repo.git/objects` bind rejects
writes; no `safe.directory` warnings (uid matches everywhere).

Post-reset (tmpfs wiped, stale gitfile left): plain `git init --separate-git-dir=/gitdir` fails
(`fatal: not a git repository: /gitdir`); `GIT_DIR=/gitdir git init` sets `bare = true` and breaks
discovery. **`rm -rf -- /work/.git; git init -q --template= --separate-git-dir=/gitdir` is
idempotent for first start, warm re-init (refs and alternates preserved) and post-reset** (`git
status` afterwards shows exactly the prior working-tree modifications). Export: with
`GIT_DIR=/tmp/exp GIT_WORK_TREE=/work` set on the exec, `git add -A` + `write-tree` never include
the gitfile (`ls-tree -r --name-only | grep -E '(^|/)\.git($|/)'` → 0 of 205 entries) and the tree
is byte-identical whether the gitfile holds garbage or is absent. Two consequences: the export's
`find /work -mindepth 1 -iname .git` would list `/work/.git` as a dropped entry on every run, and
the gitfile recipe cannot run in the export container (`/work` is mounted read-only there:
`fatal: could not open '/work/.git' for writing`). New hazard: a nested repository under `/work`
(now creatable with `git init`) with **no commit** makes the export's `git add -A` fatal
(`error: 'vendor/sub/' does not have a commit checked out`); with a commit it becomes a gitlink.

### 1.5 Kill in place (P2, `--pids-limit 64`, image has no `ps`/`pgrep`/`pkill`)

`docker top` reports VM-host PIDs (tether host PID 81196 = in-container PID 7), so the kill must
enumerate `/proc` inside the container. One fork-free `sh` loop (builtins only — `for`, `[`,
`read`, `kill`; no `$( )`, pipes or subshells) that skips PID 1, the recorded tether PID and `$$`
cleared every class tried — `nohup sleep &`, `setsid sleep &`, a respawning `while true` supervisor
with a live child, and a pipeline member literally named `cat` — leaving `docker top` tether-only,
no `<defunct>`/`Z` rows in 30 samples at 50 ms (tini reaps first), `.State.Status=running` with an
unchanged `StartedAt`, and a following `docker exec /bin/true` rc 0. Median 0.050 s (exec + top)
against 0.144 s for kill → wait → `start -ai` → ready, before the git re-init a real reset adds.
`/tmp` survives the in-place kill and is wiped by `docker kill` — the difference that keeps
`/gitdir`. Under a saturated pids limit (`:(){ :|:& };:`), `docker top` still answers, an exec that
forks an external binary dies with `Cannot fork` (3/3), the fork-free loop succeeds (3/3), and
`docker kill` recovers the container — so the ladder in §3 is required, and its last rung works.
A stray whose `docker top` CMD is exactly `cat` is treated as the tether by today's detector
(reproduced with `setsid cat 0<>fifo &`); killing by PID spares the real tether.

## 2. The invariants (the contract after this change)

1. **No process outlives the bash call that started it.** After every `bash` call (tool or
   `--verify`), the container holds only PID 1 and the tether. In-place kill first; a full reset only
   when the kill cannot be performed or verified.
2. **A stray never costs git state by itself.** `/gitdir` (index, refs, stashes, commits, config)
   survives every stray kill; only a full reset re-initializes it, and a full reset happens only for
   the reasons §3.7 lists.
3. **The harness never mistakes itself for a stray.** No dirtywork-owned exec is in flight while
   `_reap` inspects, kills, or re-inspects the container.
4. **The worker is told, in the turn it happened, through the #60 carrier.** Every `stray_kill`
   and every `sandbox_reset` is a `nudge` on the turn's last tool result (or the verify-feedback
   carrier, or the next user message on a text turn); never a user message after a tool result.
5. **Git inside the sandbox behaves like git anywhere.** Repository discovery walks up from the
   working directory: the worktree is `/work` (through the gitfile), a `git init` elsewhere creates
   a repository there, and nothing the worker runs inherits `GIT_DIR`/`GIT_WORK_TREE`.
6. **The export never depends on the worker's git dir, the gitfile, or a nested repository.** It
   keeps its own explicit environment and cannot fail because of what the worker did to `.git`
   entries under `/work`.
7. **Transcript = wire** (#60 §6): the notice text the model received is the `follow_up` on the
   record it rode on; the tool's own `result` stays byte-exact.

## 3. The stray ladder (`dirtywork/sandbox/docker.py`)

### 3.1 Detector — unchanged, and what "stray" means

`_reap()` keeps its `docker top <name>` detector and its row parser: every row whose CMD is not
one of `cat`, `/bin/cat`, `… docker-init -- cat`, `… docker-init -- /bin/cat` is a stray row. The
`cat`-named loophole (§1.5) is **not closed here**: `docker top` cannot tell two `cat` processes
apart without `ps` options whose availability varies by daemon, and closing it is a detector
change unrelated to the state-loss this issue is about. It is documented (§8) and left for a
follow-up. The kill in §3.3 is **total**, not per-row: whenever the detector fires, every process
except PID 1, the tether and the kill shell dies — a `cat`-named stray that happened to coexist
with a detected one dies too. `strays` in the events (§6) records **the detector's rows**, i.e.
what triggered the ladder, not a claim about everything killed.

### 3.2 Tether PID discovery and refresh

Right after every `_wait_ready()` — in `start()` and inside `reset()` — before any worker command
can run, `_discover_tether()` execs:

```
/bin/sh -c 'n=0; t=; for p in /proc/[0-9]*; do read -r c < "$p/comm" 2>/dev/null || continue;
[ "$c" = cat ] || continue; n=$((n+1)); t=${p#/proc/}; done; [ "$n" = 1 ] || exit 3; echo "$t"'
```

At that moment the container holds tini, the tether and this shell, so exactly one `cat` exists.
Result: `self._tether_pid: int | None`. Any other outcome — rc ≠ 0, `DockerError`, output not a
single positive integer — sets `None`, prints `tether pid unknown; a stray process will reset the
container` to stderr once per container life, and **disables the in-place rung** for that container
life: `_reap` then behaves exactly as today (stray row → `reset("stray process after bash")`).
`reset()` re-runs discovery for the new container. No PID-reuse (ABA) guard: the tether's PID can
only be reused after the tether dies, and the tether dying stops the container (`--init` + `cat`
on the held stdin), which `_reap` sees as unreachable → reset → fresh discovery.

### 3.3 The kill (one exec, fork-free, total)

When the detector fires and `_tether_pid` is known:

```
/bin/sh -c 'T=<pid>; read -r c < /proc/$T/comm 2>/dev/null || exit 3; [ "$c" = cat ] || exit 3;
for p in /proc/[0-9]*; do p=${p#/proc/}; [ "$p" = 1 ] && continue; [ "$p" = "$T" ] && continue;
[ "$p" = "$$" ] && continue; kill -9 "$p" 2>/dev/null; done; exit 0'
```

- Every word is a `dash` builtin (`read`, `[`, `kill`, `for`, `exit`); no `$( )`, pipe, subshell
  or external binary — the property that makes it run inside a pids-saturated container (§1.5).
  A code comment states the property; the unit test asserts the script text contains none of
  `$(`, `|`, `` ` ``, `(`.
- The tether guard (`/proc/$T/comm` must read `cat`) exits 3 if the recorded PID is not the tether
  → escalate. Never kills PID 1, the tether, or itself.
- Exec timeout `docker_cli.T_QUERY` (10 s). Any of rc ≠ 0, `DockerError` (including a timeout) →
  escalate (§3.4).

### 3.4 Re-check, lock sweep, escalation

After the kill exec returns 0, `_reap` runs `docker top` again through the same parser:

- top fails / rc ≠ 0 → `reset("container unreachable after bash")` (as today's unreachable path).
- stray rows remain → `reset("stray process after bash")` with `strays` = the **first** top's rows.
- tether-only → the kill succeeded. Then the **lock sweep** (§3.5): one exec
  `/usr/bin/find /gitdir \( -name '*.lock' -o -name gc.pid \) -type f -print -delete`
  (T_QUERY; it may fork — the pid table is free now). rc 0 or ≠ 0 alike: the printed paths are
  `locks_removed`; a non-zero rc additionally prints `lock sweep incomplete (rc N)` to stderr. A
  `DockerError` on the sweep → `reset("container unreachable after bash")` (an exec that cannot
  start right after a clean `top` means the container is not usable).
  Then: write `stray_kill` (§6.1), queue the `stray_kill` notice (§5), return. `_reset_this_call`
  stays `False` — nothing was rebuilt, and the budget sample that follows in `_after_bash` is
  meaningful.

The OOM check (`docker inspect .State.OOMKilled`) runs after the ladder, unchanged.

**Timeouts take the same ladder.** When `bash()` returns `timeout_result` (the docker exec
client was killed but the in-container process runs on), `_after_bash` → `_reap` finds it as a
stray row and kills it in place. The timeout nudge (§4.3 of the timeouts spec) and
`tool_result.timed_out` are unchanged; the abandoned process is dead, and `/gitdir` is intact.

### 3.5 Git-state safety after an in-place kill (revision 2)

A SIGKILLed `git` (a timed-out `git gc`, a backgrounded `git fetch`, a `git stash` inside a
killed script) cannot tear git's on-disk state: refs, `HEAD`, `packed-refs` and the index are
written to `<name>.lock` and renamed into place; loose objects and packs are written under
temporary names and renamed. What a kill leaves is (a) stale `*.lock` files, which make every later
git command fail (`Unable to create '/gitdir/index.lock': File exists`), (b) `gc.pid`, and (c)
`tmp_obj_*`/`tmp_pack_*`/`tmp_idx_*` files git ignores and `gc` prunes. After a successful kill
**no process exists**, so every lock under `/gitdir` is stale by definition — the sweep in §3.4
removes exactly (a) and (b) and reports them (`locks_removed`, and a clause in the notice). (c) is
left alone (harmless, and deleting object-directory files is not the harness's business). A reflog
line truncated by the kill is tolerated by git with a warning. A partially applied **working-tree**
operation (a killed `git checkout`/`stash pop`/`merge`) is the same exposure host mode has after
`killpg`; the notice tells the worker to `git status`. Git-touching strays do **not** escalate to a
reset — a reset destroys precisely the state at issue.

### 3.6 Serialization (revision 3) — `_reap_lock`, lock order, early `_reset_this_call`

New `self._reap_lock = threading.Lock()`, held:

- by `_reap()` for its **entire** body: first `docker top`, kill exec, second `docker top`, lock
  sweep, the reset fallback, and the OOM check;
- by `_sample_worktree()` around its whole measure → (reset once) → re-measure sequence — both
  callers: the watchdog thread's `check_worktree_budget_once()` and `_after_bash`'s synchronous one.

`_after_bash` therefore runs `_reap()` (acquire/release) and then `check_worktree_budget_once()`
(acquire/release again, same thread, never nested). The watchdog thread's sample blocks while a
ladder is in progress and then measures the settled container, so no harness exec is ever visible
to `_reap`'s `docker top` and the kill loop never kills a harness exec (invariant 3).

**Lock order is `_reap_lock` → `_reset_lock`, everywhere.** `reset()` takes only `_reset_lock` and
is called only from code holding `_reap_lock` (`_reap`, `_sample_worktree`) or from nothing
(`reset()` called directly, tests). `_watchdog_kill` takes only `_reset_lock` (from the disk-floor
path, never while holding `_reap_lock`); `_after_bash`'s final `_reset_this_call = False` takes only
`_reset_lock`. No path acquires `_reap_lock` while holding `_reset_lock`, so no cycle exists. A
disk-floor kill landing mid-ladder makes the second `docker top` fail → unreachable → reset →
`_after_bash` consumes the violation and ends the run `budget_exceeded`, as today.

**`_reset_this_call = True` moves to the first statement inside `reset()`'s `with
self._reset_lock:`**, before `docker kill`. With the lock above, a sample can no longer be in flight
when `_reap` resets; this closes the remaining window (a reset from `_sample_worktree` itself, or
a direct `reset()`), so an aborted measurement never triggers a second reset (§1.2). The flag's
meaning (a reset happened during this bash call → skip the post-call sample) is unchanged, and
`_after_bash` still clears it at the end.

The watchdog's 0.5 s disk poll waits behind `_reap_lock` for at most one ladder (bounded by the
exec timeouts: ≤ 3 × T_QUERY + one reset ≤ T_LIFECYCLE); the docs note the delay.

### 3.7 Full reset — when, and what it records

`reset(reason)` is unchanged in mechanism (kill → wait → tether → ready → discovery → init, under
`_reset_lock`) and is reached only for: `container unreachable after bash`, `stray process after
bash` **after** the ladder failed or when `_tether_pid` is `None`, `oom`, `budget sample failed`
(the watchdog's second failure), and a watchdog kill. It writes `sandbox_reset` with `reason` and,
for the stray reason, `strays`/`strays_total` (§6.1), and queues the `sandbox_reset` notice (§5).
`_init(restart=True)` re-runs the gitfile recipe (§4.2) — the index is rebuilt from the base
commit; the working tree is untouched.

## 4. Worker git layout: gitfile discovery (revisions 1, 6, 7)

### 4.1 Environment split (revision 1)

`docker_args._env_entrypoint_args()` splits into `_base_env_args()` — `HOME=/home/worker`,
`TMPDIR=/tmp`, `LANG=C.UTF-8`, the four `GIT_AUTHOR_*`/`GIT_COMMITTER_*`, `PATH`, `--entrypoint
/bin/cat` — and `_git_env_args()` — `-e GIT_DIR=/gitdir -e GIT_WORK_TREE=/work`.
`worker_create_argv` uses the base only; `export_create_argv` uses base + git env, **unchanged in
effect**: the export container keeps its explicit environment because `/work` is read-only there
and its init must not write a gitfile. Nothing the worker runs — tool `bash`, `--verify`, the
file-tool execs (`head`, `cp`, `find`, `rg`) — inherits `GIT_DIR`/`GIT_WORK_TREE`. `runs export`
uses the export path and is unchanged. A derived image must not bake `ENV GIT_DIR`/`GIT_WORK_TREE`
(docs, §8).

### 4.2 Init: two layouts

`lifecycle.init_worker_git(run, name, *, branch, base_commit, restart, layout)`:

- `layout="gitfile"` (worker container, from `start()` and `reset()`), exec'd `-w /work` with
  `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1` and no `GIT_*` env:

  ```
  set -e
  rm -rf -- /work/.git
  /usr/bin/git init -q --template= --separate-git-dir=/gitdir
  echo /repo.git/objects > /gitdir/objects/info/alternates
  /usr/bin/git symbolic-ref HEAD refs/heads/<branch>
  /usr/bin/git update-ref refs/heads/<branch> <base_commit>
  /usr/bin/git read-tree -m -u HEAD        # first start
  /usr/bin/git read-tree HEAD              # restart (index only)
  ```

  Verified idempotent for first start, warm re-init and post-reset (§1.4). `rm -rf --` handles a
  regular file, a directory and a symlink (removes the link, never follows it) — the root `.git`
  is dirtywork's, see §4.3.
- `layout="env"` (export container): today's script, unchanged, driven by the container's
  `GIT_DIR`/`GIT_WORK_TREE`.

### 4.3 The root `.git` lifecycle (revision 7)

| state of `/work/.git` when init runs | first start | reset (`restart=True`) |
|---|---|---|
| absent | created (gitfile) | created |
| the gitfile (regular file) | n/a | replaced |
| regular file with other content (worker wrote it) | n/a | replaced |
| directory (worker ran `rm .git; git init` in `/work`) | n/a | **removed** and replaced — the worker's root repository dies with the reset, like everything else in the worker's git metadata; the notice (§5.3) says so in general terms |
| symlink | n/a | link removed (target untouched), replaced |

Between inits the file is the worker's to keep: deleting it makes git in the sandbox fail with
`not a git repository` until the next reset; replacing it with a directory makes `/work` an
ordinary repository until the next reset. The harness does **not** repair it mid-run (no hook
runs between commands), and nothing dirtywork does depends on it: the export uses the env layout
(§4.1), the budget sample counts it as one entry / 4 KiB, the stale-temp sweep's regex does not
match it, `git clean -xdff` does not remove it (P1). `check_bash_command(sandboxed=True)` is
unchanged — the "host" rules that guard `.git` in host mode still do not apply, by design
(`guardrails.py` comment updated to describe the gitfile). Resume's `_seed_from_worktree` tar
excludes the **host** worktree's `./.git` and extracts over `/work` after init wrote the gitfile,
which it does not touch.

### 4.4 Export: dropped `.git` report and nested repositories (revision 6)

In the export container (`layout="env"`, `/work` read-only), `export_run` replaces its
line-parsed `find /work -mindepth 1 -iname .git` with:

1. `find /work -mindepth 1 -iname .git ! \( -path /work/.git -type f \) -print0` — every
   `.git`-named entry of any type (directory, file, symlink; `-iname` keeps today's
   case-insensitive match), **except the root gitfile itself**. A root `.git` that is a directory or
   symlink is still listed. Output is split on NUL, so names with spaces or newlines are exact.
2. `dropped_git_entries` = each path relative to `/work`, as today (now NUL-safe).
3. `nested_roots` = the parent directory of every listed entry at depth ≥ 2 (`/work/a/.git` →
   `a`), deduplicated and **minimized** (a root with an ancestor also in the set is dropped: the
   ancestor's exclusion already covers it). The root entry (depth 1) contributes no root — `/work`
   is the worktree, and git skips a `.git` entry at the worktree root by itself.
4. `git add -A -- . :(exclude,literal)<root>` … one `:(exclude,literal)` pathspec per root, each
   its own argv element of the `docker exec` (no shell; spaces, newlines and leading `-` are
   inert); `literal` disables glob interpretation of `*`, `?`, `[`; a directory pathspec matches
   the whole subtree.
5. `write-tree` as today.

Effect: a nested repository's files are **not exported** and its `.git` never becomes a gitlink,
committed or not. Paths under the root that the base commit tracks stay at the base version in the
tree (the export index came from `read-tree HEAD`; `-A` stages neither modifications nor deletions
outside the pathspec) — the run's diff shows no change there. Each root is printed to stderr as
`nested repository excluded from export: <root>` and is derivable from `dropped_git_entries`
(dirname of the entry); no new `run_end` field. The fatal `does not have a commit checked out` can
no longer occur, because `add -A` never enters an embedded repository. Real submodules of the
target repository (gitlinks in the base tree, checked out as empty directories with no `.git`)
are untouched by this — pre-existing behaviour, out of scope.

## 5. Telling the worker (revision 5)

### 5.1 The seam

`Sandbox` (the Protocol) gains `drain_notices() -> list[tuple[str, str]]`: returns and clears the
`(kind, text)` notices queued since the last drain, oldest first. `HostSandbox` returns `[]`.
`DockerSandbox` keeps `self._notices` under a small lock (`reset()` may queue from the watchdog
thread); §3.4 queues `("stray_kill", text)`, §3.7 queues `("sandbox_reset", text)`.

### 5.2 Drain points in `Runner.run()` — exactly one drain per notice, on the turn it arose

A helper `drain_sandbox()` → `(text, records)`: calls `self.sandbox.drain_notices()`, writes one
`nudge` event per notice (`kind`, `turn`; `via` left to the carrier), joins the texts with the
`_join_nudges` separator, and returns both (empty when there is nothing). It is called at:

| path (runner.py) | where | carrier / `via` |
|---|---|---|
| ordinary tool-call turn (bottom of `one_turn`) | before `deliver(_join_nudges(...))` | joined in order **`malformed_entry`, sandbox notices (occurrence order), `timeout`, `stall`**; `deliver` stamps `tool_result` (or `user` — impossible here, the turn had tool calls) |
| `finish` turn, verify **fails with feedback** (`check_verify` → `run_verify` returned feedback) | inside `check_verify`, after `run_verify()` returns, before `resolve_finish(...)` | `feedback = _join_nudges(feedback, sandbox_text)`; the finish result carries both; records get `via` = the verify `via` (`finish_result`) explicitly, like the verify record; the timeout nudge that follows keeps its own `deliver` |
| prose answer + verify feedback (`kind == "answer"`, `via="user"`) | same `check_verify` code | the next user message carries feedback + notices; records `via: user` |
| `check_verify` **run-ending** branches (verify passed → `completed`; `verify_failed`; `budget_exceeded`/`sandbox_error` from the gate) | via `finish()` below | recorded, `via` absent |
| every other run end (`max_turns`, `timeout`, `stalled`, `stuck`, `model_error`, `budget_exceeded`/`sandbox_error` raised by a tool call, `interrupted`) | first statement of `finish(status, final)` — the single run-ending funnel | recorded, `via` absent — the run is over, nothing is delivered |
| text-only continuing turn (`truncated`/`empty`/`text_tool_call` nudges) | before its `deliver(...)` | nothing can be pending (no bash ran this turn and the previous turn drained), but the call is there so the invariant holds without reasoning: joined **after** the kind's nudge, before `stall` |

A notice raised by the **verify command's own bash** (§1.1's outlier) therefore lands on the
verify feedback when the run continues and is recorded silently when it ends — never as a user
message after a tool result, never as two consecutive user messages (the #60 carriers are the only
writers). A notice raised by a tool call earlier in the same `finish` turn is drained by the same
`check_verify` call (the queue is per turn, not per call). `drain_sandbox()` is idempotent, so the
`finish()` drain after a `check_verify` drain writes nothing twice.

`stray_kill` and `sandbox_reset` are **not** `FailureTracker` kinds and do not feed the
consecutive-failure abort (like `timeout`).

### 5.3 Texts (constants in `docker.py`; the wording can change without a schema change)

- `stray_kill`: `The sandbox killed {n} background process{es} your last command left running
  ({cmds}). A process cannot outlive the bash call that started it — start and use anything you
  need within one command.` followed, when `locks_removed` is non-empty, by ` Stale git lock files
  they left in the repository were removed; run \`git status\` to confirm the state.`
  `{cmds}`: the first three `strays`, each cut to 80 characters, joined by `; `, then `; +N more`.
- `sandbox_reset`: `The sandbox container was reset after your last command ({reason}). Files in
  the worktree are intact, but git metadata was re-initialized: the index, stashes, local commits
  and branches you created inside the sandbox are gone, and the branch is back at the run's base
  commit with your file changes uncommitted. Run \`git status\` before continuing.`

## 6. Transcript and evidence (revision 8)

### 6.1 Events (docker mode only, schema v2, additive)

**`stray_kill`** (new) — written by the sandbox inside the bash call, **before** that call's
`tool_result` (the same position `sandbox_reset` has today):

| field | type | rule |
|---|---|---|
| `strays` | list of string | the CMD column of every detector row from the **first** `docker top`, in `docker top` order; at most **20** entries; each cut to **200** characters with a trailing `…` when cut; never empty (the detector fired) |
| `strays_total` | integer | **sparse**: present only when more than 20 rows were seen (then it is the full count) |
| `locks_removed` | list of string | **sparse**: present only when the sweep removed at least one file; absolute paths under `/gitdir`, in `find` order, at most 20 entries |

**`sandbox_reset`** gains the same `strays` / `strays_total` fields, **sparse**: present only when
`reason` is `stray process after bash` (rows from the ladder's first `top`, or from the only `top`
when the in-place rung was disabled). Other reasons carry `reason` alone, as today.

**`nudge`** gains two documented `kind` values, `stray_kill` and `sandbox_reset`, with the
existing `turn` and `via` (`tool_result` / `user` / `finish_result`; sparse on run-ending turns,
as #60 defined). One `nudge` per notice.

Ordering within a turn: `[stray_kill | sandbox_reset]` → the bash `tool_result` (its `follow_up`
holds the whole delivered text when the notice rode there) → … → the `nudge` records at turn end.
`verify` still follows the gate's `_after_bash` events. Empty lists are never written: an empty
`strays` cannot occur, and `locks_removed` is omitted rather than `[]`.

### 6.2 Where counts surface

- `dirtywork bench summarize`: `NUDGE_KINDS` += `stray_kill`, `sandbox_reset` (counted among
  non-failure nudges, like `timeout`); `_event_counts` counts `stray_kill` events; the summary row
  gains a `stray_kills` column beside `sandbox_resets`. The `--compare` harness cell is unchanged
  (it is part of the frozen output).
- `tools/soak_harvest.py` follows `bench.NUDGE_KINDS` and gains `stray kills` next to its reset
  count.
- `dirtywork runs show`: `stray_kill` renders as a callout `> **stray_kill**: N process(es) killed
  — \`cmd\`, \`cmd\` …` (strays through the existing inline-code escaper, cut at `MD_ARGS_CHARS`;
  `locks_removed` count appended when present); `sandbox_reset` adds `— strays: …` when present.
- `run.json` / `run_end`: nothing new.

## 7. `.NET` images (revision 9)

`docker/Dockerfile` (the 1.0 image) adds to its existing `ENV`:

```
DOTNET_CLI_USE_MSBUILD_SERVER=0 \
MSBUILDDISABLENODEREUSE=1 \
UseSharedCompilation=false \
DOTNET_NOLOGO=1
```

— four variables: the three that stop the MSBuild server, MSBuild node reuse and the Roslyn
compiler server (§1.3: no process survives a build call with them set), plus `DOTNET_NOLOGO=1`,
which removes the banner lines from every tool result the model reads. The harness injects none of
them (`-e` at `docker create` stays toolchain-agnostic); the docs make a derived image from `:0.10`
set **all five** `.NET` lines — `DOTNET_EnableWriteXorExecute=0` (the #63/#70 fix) and these
four — in one snippet. With §3 in place, a daemon on an image that lacks them costs one
`stray_kill` notice per build call, not the worker's state.

## 8. Docs and contract

- `docs/machine-contract.md`: the bash-tool bullet (in-place kill, reset rungs, what a reset
  loses), the transcript-events paragraph (`stray_kill`, the two nudge kinds, `sandbox_reset.strays`),
  the docker-mode bullet on git discovery (gitfile, no `GIT_DIR`/`GIT_WORK_TREE` in commands, the
  export keeps its own), the `--verify` note that the gate runs without `GIT_*` env.
- `docs/transcript-schema.md`: `stray_kill` section; `sandbox_reset` table gains `strays`,
  `strays_total`; `nudge.kind` list; event-order note; `runs show` rendering line.
- `docs/operating.md`: bash-tool section (processes are killed when the call returns; a reset is
  the fallback and the worker is told; timeouts), a "git inside the sandbox" paragraph (gitfile,
  what survives what, `git stash` is safe across a stray kill and lost across a reset), the
  nested-repository export rule, the watchdog-poll delay note.
- `docs/security.md`: the process-lifetime paragraph names both mechanisms (host `killpg`,
  docker in-place kill + reset); the container-environment bullet drops `GIT_DIR`/`GIT_WORK_TREE`
  from the worker's env and keeps them for the export container.
- `docker/README.md`: §7's five-line `.NET` snippet for `:0.10`-derived images; "do not bake
  `ENV GIT_DIR`/`GIT_WORK_TREE`"; the 1.0 image checklist gains the four variables.
- `dirtywork/builtin_tools.py` `BASH_SPEC.description` (wire text; `tests/fixtures/tool_schemas.json`
  regenerated): "Backgrounded processes are killed when the command returns and you are told
  which. In docker mode, if they cannot be killed, or on out-of-memory, the container is reset:
  the working tree survives, but git state you created inside the sandbox (index changes,
  stashes, local commits) does not; you are told when that happens."
- `dirtywork/guardrails.py` comment (gitfile, not env); the 2026-08-15 spec's §3/§4/§6 get a
  one-line "superseded by 2026-08-25 #61 for docker mode" note; the 0.10 `README.md` docker
  bullet if it mentions resets.
- Ledger: S11/S13 rows point here; the `#64` note about `env -u GIT_DIR -u GIT_WORK_TREE` is
  marked obsolete.

## 9. Tests

Unit (fake docker, `tests/docker_fakes.py` per-prefix scripting; `docker top` scripted as a
list so the second call differs from the first):

1. `_discover_tether`: single-line integer → stored; rc 3 / two lines / garbage / DockerError →
   `None` + one stderr line; re-run after `reset()`.
2. ladder happy path: top (stray rows) → kill exec argv contains `T=<pid>` and the script; second
   top (clean) → sweep exec → `stray_kill` event with `strays` in order, `locks_removed` from the
   sweep output; notice queued; **no** `docker kill`; `_reset_this_call` stays `False`; the
   post-call budget sample still runs.
3. escalation: kill rc 3 / rc 1 / DockerError / timeout → `reset("stray process after bash")`
   with `strays`; second top still dirty → same; second top rc ≠ 0 → unreachable reset; sweep
   DockerError → unreachable reset; `_tether_pid is None` → straight to reset (today's path).
4. limits: 25 stray rows → 20 entries + `strays_total: 25`; a 300-char CMD → 200 + `…`;
   `locks_removed` absent when the sweep printed nothing.
5. the kill script text is fork-free (no `$(`, `|`, `` ` ``, `(`); `T=` is the discovered pid.
6. serialization: a scripted slow `docker top` on the main thread while a second thread calls
   `_sample_worktree()` — the sample's exec happens after the ladder's last docker call (spy
   order); lock order test: `_sample_worktree` → `reset` from the watchdog thread while the main
   thread runs `_reap` → no deadlock (join with timeout), exactly one reset.
7. `reset()` sets `_reset_this_call` before `docker kill` (spy on call order and flag).
8. `docker_args`: worker create argv has no `GIT_DIR`/`GIT_WORK_TREE`; export argv has both;
   base env unchanged otherwise.
9. `init_worker_git(layout="gitfile")` script text (rm → init --separate-git-dir → tail, first
   vs restart); `layout="env"` unchanged byte for byte.
10. export: `find … -print0` argv; NUL parsing with a name containing a newline and a space;
    roots minimized (`a` and `a/b/.git` → `a` only); `add -A` argv = `["git","add","-A","--",".",
    ":(exclude,literal)a", …]`; root `.git` directory listed but produces no root; no roots → the
    argv is exactly today's.
11. runner drain points (a scripted sandbox whose `drain_notices()` returns queued notices):
    ordinary turn → nudge events + `follow_up` = joined text in the documented order, `via:
    tool_result`; `finish` + failing verify → the finish result = feedback + notice, records `via:
    finish_result`; prose answer + failing verify → user message = feedback + notice, `via: user`;
    verify passed / `verify_failed` / `max_turns` / `model_error` → records written, `via` absent,
    no delivery; a notice from a call before `finish` in the same turn → carried with the feedback;
    no double writes across `check_verify` + `finish()`.
12. `bench` counts (`stray_kill`, the two nudge kinds, `stray_kills` column, `--compare` cell
    byte-identical to before); `runs show` callouts incl. escaping of a stray command containing
    backticks and `|`; `test_transcript_schema.py`: every field of a synthetic `stray_kill` /
    `sandbox_reset`-with-strays / the two nudge kinds appears as a documented token in
    `transcript-schema.md` (the existing doc-token check, extended to docker-only events).

Live (`@pytest.mark.docker`, `tests/test_docker_live.py`, `DIRTYWORK_LIVE_IMAGE` honoured):

13. a `nohup sleep 300 &` stray → `stray_kill` event, no `sandbox_reset`, a `git stash` made
    before the call pops afterwards, the same container ID before and after.
14. a `cat`-named stray alongside a `sleep` stray → both dead after the call.
15. a killed git: `git stash` with an artificial `index.lock` created by the killed command →
    `locks_removed` names it and the next `git status` succeeds.
16. S13: `cd $(mktemp -d) && git init && git status && git worktree list` inside the sandbox stays
    local; `git -C /work status` works from `/tmp`; the export of a worktree with a nested
    **uncommitted** repository succeeds and reports the root in `dropped_git_entries`, with the
    nested files absent from the diff.
17. race loop: 40 × (5.2 s idle + `sed`) with the watchdog started → **0** `sandbox_reset`,
    **0** `stray_kill` (P7's phase A as a regression test; skip-with-reason above 10 min).
18. escalation: the pids flood (`test_docker_live_process_flood_triggers_reset`) still ends in a
    `sandbox_reset` with `strays` and the run continues.
19. `.NET` (skip-with-reason unless the image has the SDK): `dotnet new console` + `dotnet build`
    → no `stray_kill`, no `sandbox_reset` on the 1.0/dev image; the same on a `:0.10`-style image
    without the variables → exactly one `stray_kill` naming `VBCSCompiler`.
20. root `.git` tampering: `rm .git` → next `git status` in the sandbox fails, export still
    succeeds; `rm .git && git init` (directory) → export succeeds and lists `.git` in
    `dropped_git_entries`; after a forced reset both are back to the gitfile.

## 10. Acceptance evidence and gates

- **P7 is satisfied** (§1.2): the race is reproduced on production code and eliminated by design
  (§3.6); test 17 keeps it eliminated.
- Built by the released dirtywork (0.10.1) against this repository per the owner's dogfood rule:
  chained runs off `issue-61-sandbox-resets` (`--branch-from`), qwen3-coder-next, image
  `dirtywork-worker-pytest:0.10`, ≥ 60 turns, sampler on, one ledger row per run. The verify gate
  is `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` **until §4 lands**
  in the first task, then the plain command — its passing inside the sandbox is itself the S13
  acceptance. Claude writes the prose docs and reviews every branch; a Claude implementer only after
  a failed resume-with-feedback, stated in the PR.
- Soak re-runs: `D3-issue97` (the S11 run: `git stash` survives `dotnet test`) and one
  `run-bash-buildsh` (class A/D) on the built branch, rows in the ledger.

## 11. Out of scope

The `cat`-named-stray detector loophole (documented; a follow-up may use `docker top -eo
pid,ppid,etimes,args` and the oldest `cat` child of `docker-init`); a PID-reuse guard on the
tether (impossible while the container lives, §3.2); `pytest-of-unknown` under `/work` (P4: cannot
happen with the sandbox's own `/tmp`); S12 (fractional floats for integer params); real submodules
of the target repository in the export; harness-injected `.NET` environment; moving `/gitdir`
onto the volume (approach C, declined: `--gitdir-size`/`run.json` churn before the freeze, objects
against the worktree budget, a real `.git` under `/work` colliding with the export guards).
