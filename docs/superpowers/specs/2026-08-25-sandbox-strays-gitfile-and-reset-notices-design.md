# Sandbox resets: kill strays in place, keep the worker's git state, tell the worker (#61)

**Date:** 2026-08-25
**Status:** Design v2 — approach B (stray ladder + gitfile discovery + #60-carrier notices) chosen
by the owner (2026-08-25 09:14 CDT) with **nine required revisions**, each resolved in the section
§0 names. v2 folds a six-lens red-team with two-refuter adversarial verification (42 agents,
09:26–09:56 CDT): 50 findings (3 Blocker, 32 Important, 15 Minor); 18 Blocker/Important verified
(17 kept, 1 refuted — a host-side `GIT_DIR` claim outside this spec's scope), the 17 unverified
ones and the Minors read and folded by the author. The three Blockers were all in §4.4 (nested
repositories at export) and rewrote it (§4.4). §0.1 maps every fold. Not yet approved for
plan → execution.
**Origin:** issue #61 (milestone **1.0.0 — contract freeze**), soak finding S11, plus the two
findings the #64 dogfood carried here: S13 (`GIT_DIR`/`GIT_WORK_TREE` exported into every
command) and the "stray process" resets that followed plain foreground commands. Evidence:
`docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md` (S11, S13, the #64 run rows) and the
live probes of 2026-08-25 recorded in §1.
**Parent specs:** `2026-08-15-review-response-design.md` (§3 Reset, §6 reaping and budgets —
superseded for docker mode; §4 in-container init and §7 export — superseded for the **worker**
container's layout and for the export's `.git`-entry step only; the export container's env-driven
init is unchanged), `2026-08-23-harness-followups-after-tool-results-design.md` (#60: carriers,
`follow_up`, `via`), `2026-08-19-tools-context-timeouts-design.md` (§4.3 timeout nudge),
`2026-08-18-run-evidence-and-review-loop-design.md` (§4 `--verify`).
Ships in **dirtywork 1.0.0**. Stdlib-only, Python 3.9 floor, `schema_version` stays 2 — every
change below is a new sparse field, a new event kind, or a new documented value of an existing
field; never a removed or renamed one, and no new `via` value.

## 0. The owner's nine revisions and where each is resolved

| # | Finding (2026-08-25 09:14 CDT) | Resolved in |
|---|---|---|
| 1 | Worker/export git environment needs an explicit split; export mounts `/work` read-only | §4.1, §4.2 |
| 2 | Kill-in-place can leave git metadata in a bad state (index lock, partial ref, half object) | §3.5 |
| 3 | Watchdog lock scope: must cover top → kill → top → reset, both sampling paths, no deadlock; P7 is a gate | §3.6, §1.2 (P7 confirmed), §10 |
| 4 | `cat` loophole contradiction; kill every non-tether PID or only detector-classified strays; tether PID discovery and refresh | §3.1, §3.2, §3.3 |
| 5 | Notice delivery on verify, mixed `finish` turns, early aborts, turns with no carrier | §5.2 |
| 6 | Nested-repository export: exclude the nested root, robust to spaces/newlines, overlap with tracked paths, committed vs uncommitted | §4.4 (rewritten in v2: files kept, `.git` dropped) |
| 7 | Root `.git` file lifecycle: missing/directory/symlink, tampering, first start/warm/reset | §4.3 |
| 8 | Exact event schemas, limits, ordering, empty-list rule, where counts surface | §6 |
| 9 | .NET wording: four variables, derived-image snippet shows all of them | §7 |

### 0.1 Red-team fold (v2)

| finding | fold |
|---|---|
| B: parent-of-`.git` exclusion silently reverts the worker's work under it; `find` stream is merged stdout+stderr; enumeration unchecked | §4.4 rewritten: stderr kept out of the parsed stream, keep-rule tokens, `-prune`; nested repositories exported as plain files via per-root sub-index splice; gitlink safety net |
| disk-floor kill mid-sample → spurious `budget sample failed` reset | §3.6: `_after_bash` consumes a recorded violation before reaping; `_watchdog_kill` sets `_reset_this_call`; thread-path sample never resets |
| one kill pass; zombie/respawn window before the verifying `top` | §3.3 three-pass loop; §3.4 bounded settle re-check |
| lock-wait bound wrong; disk poll must not wait on a ladder | §3.6: thread-path sample try-acquires and skips; honest bound stated |
| shutdown outside the lock protocol; `note_bash_end` not in a `finally` | §3.6 shutdown flag; §3.3 `bash()` `finally` |
| timed-out `grep` leaves a harness process the ladder blames on the worker | §3.8 `_kill_abandoned_exec()`; invariants 1 and 3 restated |
| shared fixtures answer discovery with garbage → rung silently disabled in every test | §3.2/§3.3 scripts are module constants with positional args; §9 fixtures activate the rung by default |
| `NUDGE_KINDS` ripple: `empty_reply` misclassification, detail cell width, compare claim | §6.2 explicit `EMPTY_REPLY_NUDGE_KINDS`, consequences stated |
| `nudge.via: finish_result` would be a new value; invariant 7 false per carrier | §5.2 notices ride the existing `deliver()` carriers only; §2 invariant 7 per carrier |
| `runs show` seams misnamed; no inline-code escaper exists | §6.2 named against `_timeline_line`, `_md_event_lines`, new `_md_code` |
| redirect order leaks `cannot open` into the parsed stream; sweep output is merged too | §3.2/§3.3 `2>/dev/null <` order; §3.4 sweep `-print0` + full-match filter, never resets |
| timed-out sweep must not reset; OOM check position; `git status` advice conditional; `/tmp` not reclaimed | §3.4, §3.5, §5.3, §2 |
| `drain_notices` as a hard Protocol method breaks duck-typed doubles; blocking fakes for test 6; tasks unsized | §5.1 defensive read; §9 callable responses; §10 task boundaries |
| docs: `dropped_git_entries` rows, nine event names, machine-contract snippet, 2026-08-15 §7, security.md bullet, checklist item 6, bash text overclaims host mode | §8 |

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

## 1. The facts (measured 2026-08-25, live probes on this macOS host / Docker Desktop 29.7.2)

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
`sandbox_error`. Not seen in any recorded run; one lost race away. The same mechanism applies to a
disk-floor `_watchdog_kill` landing during the post-bash sample (red-team, §3.6).

### 1.3 `.NET` daemons (P3, image `dirtywork-worker-dev:issue63`, SDK 8.0.424 + 10.0.400, offline)

Without extra environment, the first `dotnet build` leaves `VBCSCompiler … -pipename:…` running
(same PID reused by later net8.0 and net10.0 builds, still there 70 s later). With
`DOTNET_CLI_USE_MSBUILD_SERVER=0 MSBUILDDISABLENODEREUSE=1 UseSharedCompilation=false DOTNET_NOLOGO=1`
set at `docker run`, `docker top` is tether-only after every one of `new`, `build`, `build` (×2
frameworks). Second-build wall time: 0.459 s vs 0.436 s (net8.0), 0.456 s vs 0.441 s (net10.0)
— no cost on the single-project case tested. `dotnet new xunit` cannot restore offline (NU1301), so
`dotnet test` was not exercised.

### 1.4 Gitfile discovery (P1 + follow-up, `dirtywork-worker-pytest:0.10`, git 2.39.5, `/bin/sh` = dash)

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
is byte-identical whether the gitfile holds garbage or is absent. The gitfile recipe cannot run in
the export container (`/work` is read-only there: `fatal: could not open '/work/.git' for
writing`), and the export's `find /work -mindepth 1 -iname .git` would list `/work/.git` every run.
Nested repositories under `/work` (now creatable with `git init`, and created by scaffolders such
as `cargo new`) with **no commit** make `git add -A` fatal (`error: 'vendor/sub/' does not have a
commit checked out`); with a commit they become gitlinks. Follow-up probe: `git add -A -- .
":(exclude,literal)a b/sub"` turns that fatal into a clean add with the subtree absent from the
tree; `find /work -mindepth 1 -iname .git ! \( -path /work/.git -type f \) -print0` in argv form
lists the nested entry and not the root gitfile; `kill`, `read`, `[` are dash builtins; dash applies
redirections left to right, so `read -r c < missing 2>/dev/null` still prints `cannot open` to
stderr — the order must be `2>/dev/null <`.

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
(reproduced with `setsid cat 0<>fifo &`); killing by PID spares the real tether. Not measured: the
reap latency of a process the containerd shim (not tini) owns — the abandoned process of a
timed-out `docker exec` — hence the settle re-check in §3.4.

## 2. The invariants (the contract after this change)

1. **No process outlives the bash call that started it** — as far as the detector can see (§3.1:
   a stray that renders as exactly `cat` is invisible until that loophole is closed). After every
   `bash` call (tool or `--verify`), the container holds only PID 1 and the tether. In-place kill
   first; a full reset only when the kill cannot be performed or verified.
2. **A stray never costs git state by itself.** `/gitdir` (index, refs, stashes, commits, config)
   survives every stray kill; only a full reset re-initializes it, and a full reset happens only for
   the reasons §3.7 lists. The same in-place kill also leaves `/tmp` and `/home/worker` as they
   were — wanted for `/gitdir`, a documented trade for the other two: a stray that filled `/tmp`
   leaves it full until the worker cleans it or a reset happens (§8).
3. **The harness never mistakes itself for a stray.** No dirtywork-owned exec is in flight while
   `_reap` inspects, kills, or re-inspects the container, and no exec the harness abandoned on a
   timeout is left for the detector to find (§3.8).
4. **The worker is told, in the turn it happened, through the #60 carriers.** Every `stray_kill`
   and every `sandbox_reset` is a `nudge` on the turn's last tool result, or in the next user
   message on a text turn; never a user message after a tool result, never two consecutive user
   messages.
5. **Git inside the sandbox behaves like git anywhere.** Repository discovery walks up from the
   working directory: the worktree is `/work` (through the gitfile), a `git init` elsewhere creates
   a repository there, and nothing the worker runs inherits `GIT_DIR`/`GIT_WORK_TREE`.
6. **The export never depends on the worker's git dir, the gitfile, or a nested repository, and
   never loses the worker's files to one.** It keeps its own explicit environment; a nested
   repository's files are exported as plain files and only its `.git` entry is dropped (§4.4).
7. **Transcript = wire** (#60 §6.4), per carrier: on a tool-result carrier the notice text is that
   record's `follow_up` and the tool's own `result` stays byte-exact; on the `user` carrier the text
   is not transcribed — only `nudge.kind`, `turn` and `via` are, #60's existing carve-out.

## 3. The stray ladder

The scripts, the `docker top` row parser and the notice-text formatters live in a new module
`dirtywork/sandbox/strays.py` (`docker.py` is already 1 000 lines; DRY & SOLID); `DockerSandbox`
calls into it from `_reap()`, `reset()`, `_discover_tether()` and `_kill_abandoned_exec()`. Every
in-container script is a **module-level constant** with dynamic values passed as shell positional
parameters (`"_", value` after the script — the `WRITE_SCRIPT`/`APPEND_GUARD_SCRIPT` convention),
so tests and fixtures can address an exec by its exact, stable argv.

### 3.1 Detector — unchanged, and what "stray" means

`_reap()` keeps its `docker top <name>` detector and its row parser (moved to `strays.py`): every
row whose CMD is not one of `cat`, `/bin/cat`, `… docker-init -- cat`, `… docker-init -- /bin/cat`
is a stray row. The `cat`-named loophole (§1.5) is **not closed here**: `docker top` cannot tell two
`cat` processes apart without `ps` options whose availability varies by daemon, and closing it is a
detector change unrelated to the state loss this issue is about. It is documented (§8) and left for
a follow-up. The kill in §3.3 is **total**, not per-row: whenever the detector fires, every process
except PID 1, the tether and the kill shell dies — a `cat`-named stray that happened to coexist
with a detected one dies too. `strays` in the events (§6) records **the detector's rows** from the
first `docker top`, i.e. what triggered the ladder, not a claim about everything killed.

### 3.2 Tether PID discovery and refresh

Right after every `_wait_ready()` — in `start()` and inside `reset()` — before any worker command
can run, `_discover_tether()` execs `TETHER_DISCOVERY_SCRIPT`:

```
n=0; t=
for p in /proc/[0-9]*; do
  read -r c 2>/dev/null < "$p/comm" || continue
  [ "$c" = cat ] || continue
  n=$((n+1)); t=${p#/proc/}
done
[ "$n" = 1 ] || exit 3
echo "$t"
```

At that moment the container holds tini, the tether and this shell, so exactly one `cat` exists.
`2>/dev/null` precedes the input redirection so a process that exits between the glob and the read
cannot print `cannot open` into the captured stream (`Captured.output` merges stderr). Accepted
result: rc 0 **and** the merged output, stripped, is a single line matching `^[0-9]+$` →
`self._tether_pid: int`. Any other outcome — rc ≠ 0, `DockerError`, anything else in the output —
sets `None`, prints `tether pid unknown; a stray process will reset the container` to stderr once
per container life, and **disables the in-place rung** for that container life: `_reap` then
behaves as today (stray rows → `reset("stray process after bash", strays=…)`). `reset()` re-runs
discovery for the new container. No PID-reuse (ABA) guard: the tether's PID can only be reused
after the tether dies, and the tether dying stops the container (`--init` + `cat` on the held
stdin), which `_reap` sees as unreachable → reset → fresh discovery.

### 3.3 The kill (one exec, fork-free, total, three passes)

When the detector fires and `_tether_pid` is known, `_reap` execs
`/bin/sh -c STRAY_KILL_SCRIPT _ <tether_pid>`:

```
T=$1
read -r c 2>/dev/null < "/proc/$T/comm" || exit 3
[ "$c" = cat ] || exit 3
for pass in 1 2 3; do
  for p in /proc/[0-9]*; do
    p=${p#/proc/}
    [ "$p" = 1 ] && continue
    [ "$p" = "$T" ] && continue
    [ "$p" = "$$" ] && continue
    kill -9 "$p" 2>/dev/null
  done
done
exit 0
```

- Every word is a `dash` builtin (`read`, `[`, `kill`, `for`, `exit`); no `$( )`, pipe, subshell
  or external binary — the property that makes it run inside a pids-saturated container (§1.5).
  A code comment states the property; a unit test asserts the constant contains none of `$(`,
  `|`, `` ` ``, `(`.
- `/proc/[0-9]*` is expanded once per `for`, string-sorted (`/proc/1001` before `/proc/999`), so a
  supervisor's child may die before the supervisor forks again; passes 2 and 3 re-expand and catch
  what was forked in between. `kill` on an already-dead PID just fails into `2>/dev/null`.
- The tether guard (`/proc/$T/comm` must read `cat`) exits 3 if the recorded PID is not the tether
  → escalate. Never kills PID 1, the tether, or itself.
- Exec timeout `docker_cli.T_QUERY` (10 s). Any of rc ≠ 0, `DockerError` (including a timeout) →
  escalate (§3.4).

`bash()` wraps its exec in `try/finally` so `watchdog.note_bash_end()` always runs — today a
`KeyboardInterrupt` during the exec skips it and leaves the watchdog believing a bash call is in
flight for the rest of the run.

### 3.4 Re-check, lock sweep, OOM, escalation

After the kill exec returns 0, `_reap` re-runs `docker top` through the same parser, up to
**three times 50 ms apart** while stray rows remain (a bounded settle window for a process whose
parent — tini, or the containerd shim for an abandoned `docker exec` — has not reaped it yet;
no `<defunct>` string filter anywhere):

- a `top` fails / rc ≠ 0 → `reset("container unreachable after bash")`.
- stray rows remain after the third look → `reset("stray process after bash", strays=…)` with the
  **first** top's rows.
- tether-only → the kill succeeded. Then the **lock sweep** (§3.5): `LOCK_SWEEP_ARGV` =
  `/usr/bin/find /gitdir ( -name *.lock -o -name gc.pid ) -type f -delete -print0` (argv list, no
  shell; `-delete` before `-print0`, so a path is printed only after its unlink succeeded;
  T_QUERY; it may fork — the pid table is free now). The output is split on NUL, the unterminated
  tail dropped, and only tokens full-matching `^/gitdir/.*(\.lock|/gc\.pid)$` become
  `locks_removed` (the merged stream can carry `find` diagnostics). rc ≠ 0 → stderr `lock sweep
  incomplete (rc N)`, the matched paths still count. A `DockerError` on the sweep (timeout or a
  client failure) → stderr `lock sweep incomplete (<error>)`, `locks_removed` omitted, **no reset**
  — a slow `find` over a large `/gitdir` must not cost the state the ladder just saved.
  Then the OOM check (`docker inspect .State.OOMKilled`, as today, now also on this path): `true`
  → `reset("oom")` (a reset restarts the container, which clears the flag; no latch needed).
  Otherwise write `stray_kill` (§6.1), queue the `stray_kill` notice (§5), return.
  `_reset_this_call` stays `False` — nothing was rebuilt, and the budget sample that follows in
  `_after_bash` is meaningful.

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
left alone. The sweep covers `/gitdir` only: a lock inside a repository the worker created under
`/work` is the worker's (the notice's unconditional `git status` advice covers it). A reflog line
truncated by the kill is tolerated by git with a warning. A partially applied **working-tree**
operation (a killed `git checkout`/`stash pop`/`merge`, a leftover `MERGE_HEAD`) is the same
exposure host mode has after `killpg`; the `stray_kill` notice always ends with `git status`
advice (§5.3). Git-touching strays do **not** escalate to a reset — a reset destroys precisely the
state at issue.

### 3.6 Serialization (revision 3) — `_reap_lock`, lock order, flags

New `self._reap_lock = threading.Lock()`, held:

- by `_reap()` for its **entire** body: first `docker top`, kill exec, the settle re-checks, lock
  sweep, OOM inspect, and the reset fallback;
- by `_kill_abandoned_exec()` (§3.8) for its one exec;
- by `_sample_worktree()` around its whole measure → (reset once) → re-measure sequence.

**Two sampling paths, one lock, two acquisition modes.** `_sample_worktree(*, wait)`:
- `wait=True` — the main thread's synchronous post-call sample (`_after_bash` →
  `check_worktree_budget_once()`): blocking acquire; the lock is free (the same thread just
  released it). Semantics as today: measure → on failure, if no reset happened this call, reset
  once and re-measure → second failure raises `SandboxError`.
- `wait=False` — the watchdog thread's periodic sample (`Watchdog.run()` passes it; the
  `sample` callable takes the keyword): `acquire(blocking=False)`; when the lock is held (a ladder
  or the main thread's sample is in progress) the tick is skipped — `check_worktree_budget_once`
  treats `None` as "no sample" and returns `False` — so **the watchdog thread never waits on a
  ladder and its 0.5 s disk poll is never delayed by one**. On this path a failed measurement
  never resets: if `_reset_this_call` is already set (the container was killed or reset during
  this call by whoever recorded why) it returns `None`; otherwise it resets once and re-measures as
  today, and a second failure raises (the thread's handler then fails closed as today).

**Lock order is `_reap_lock` → `_reset_lock` → `_notices_lock`, everywhere.** `reset()` takes
only `_reset_lock` and is called only from code holding `_reap_lock` (`_reap`, `_sample_worktree`)
or from nothing (direct calls, tests). `_watchdog_kill` takes only `_reset_lock` (from the
disk-floor path, never while holding `_reap_lock`); `_after_bash`'s final flag clear takes only
`_reset_lock`; `_notices_lock` is a leaf taken only around append/drain, never across a docker
call. No path acquires `_reap_lock` while holding `_reset_lock`, so no cycle exists.

**The kill-during-sample defect (§1.2) is closed from both ends:**
- `reset()` sets `_reset_this_call = True` as its first statement under `_reset_lock`, before
  `docker kill`; `_watchdog_kill` does the same before its `docker kill`. The flag's meaning is
  unchanged — the container was killed or rebuilt during this bash call — and it is read by
  `_sample_worktree` (above) and by `_after_bash` (skip the post-call sample). `_after_bash` clears
  it in a `finally`, so a reset that raised cannot leave it sticky for the next call.
- `_after_bash` **consumes a recorded violation before it reaps**: its first statement checks
  `watchdog.violation` and, if set, raises `BudgetExceeded`/`SandboxError` exactly as its existing
  end-of-call block does (that block moves into a helper both sites call). Every watchdog kill
  records `violation` before calling `kill()`, so a container the watchdog killed is never reaped,
  sampled, or reset — a disk-floor kill landing during a bash exec, mid-ladder, or mid-sample ends
  the run `budget_exceeded` with the disk-floor reason and no `sandbox_reset` event.

**Shutdown.** `_stop_container()` sets `self._shutting_down = True` by plain assignment as its
first statement (before `watchdog.stop()`/join). `_sample_worktree` returns `None` and `reset()`
returns without acting (no docker call, no event, no notice) once it is set, so a watchdog tick
racing the teardown can neither start a reset nor raise into a stopped run.

**Honest bound.** Because the thread path never blocks, the only waiting is the main thread's
own sequence: a ladder is at most `top` + kill + 3 re-checks + sweep + OOM = 7 × T_QUERY (70 s)
before any reset, and a `reset()` is five separately T_LIFECYCLE-capped steps (`kill`, `wait`,
`close_tether`, `wait_ready`, `init_worker_git`) plus the discovery exec — worst case minutes,
same as today's reset path, and only on a hung daemon.

### 3.7 Full reset — when, and what it records

`reset(reason, *, strays=None, strays_total=None)` is unchanged in mechanism (kill → wait →
tether → ready → discovery → init, under `_reset_lock`) and is reached only for: `container
unreachable after bash`, `stray process after bash` **after** the ladder failed or when
`_tether_pid` is `None`, `oom`, `budget sample failed` (the sample's second failure), and — never
through `reset()` — a watchdog kill, which ends the run. It writes `sandbox_reset` with `reason`
and, when given, `strays`/`strays_total` (§6.1), and queues the `sandbox_reset` notice (§5).
`_init(restart=True)` re-runs the gitfile recipe (§4.2) — the index is rebuilt from the base
commit; the working tree is untouched.

### 3.8 Execs the harness abandons

`grep()` is the one tool exec that swallows a timed-out `DockerError` and lets the run continue
(`grep_timeout_result`); the in-container `rg`/`grep -rn` keeps running and the next bash call's
`_reap` would find it, kill it, and tell the worker *its* command left it behind. So
`grep()`'s timeout branch calls `_kill_abandoned_exec()` before returning: `STRAY_KILL_SCRIPT` (the
same constant) under `_reap_lock`, **no** `docker top`, **no** sweep (the abandoned execs are
read-only), **no** event, **no** notice, **no** escalation — a failure is ignored (the ladder on the
next call is the backstop). Any future tool exec that continues after a timeout must call it too
(the docstring says so); no other current tool path does — `_read_raw`, `_write_raw`, `list_dir`
propagate a `DockerError`.

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
(docs, §8). Host-side dirtywork never read those variables and is out of scope.

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
  is dirtywork's, see §4.3. The `rm` runs first precisely so `git init --separate-git-dir` can
  never take its "reinitialization moves the repository" branch on a directory the worker put
  there.
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

### 4.4 Export: `.git` entries and nested repositories (revision 6; rewritten in v2)

**Principle (invariant 6):** a `.git`-named entry under `/work` is dropped; every other file the
worker left is exported, whatever directory it sits in. A nested repository — a scaffolder's
`cargo new`, a test fixture, a worker `git init` in a sub-project — must never make the export fail
and must never silently revert the worker's files under it to the base commit.

In the export container (`layout="env"`, `/work` read-only, index populated by `read-tree HEAD`):

1. **Enumerate.** `EXPORT_GIT_ENTRIES_SCRIPT` (a constant, run as `/bin/sh -c`; the export
   container is never pids-saturated):
   `exec /usr/bin/find /work -mindepth 1 -iname .git ! ( -path /work/.git -type f ) -prune -print0 2>/dev/null`
   — every `.git`-named entry of any type (directory, file, symlink; `-iname` keeps today's
   case-insensitive match) except the root gitfile itself; `-prune` stops `find` descending into
   a matched directory; stderr is discarded so `find`'s diagnostics cannot land in the parsed
   stream. Parse: split on NUL, drop the unterminated tail, keep only tokens that start with
   `/work/` and whose last path component equals `.git` case-insensitively. rc ≠ 0 → stderr
   `export: .git enumeration incomplete (rc N)` and continue with what was parsed;
   `Captured.truncated` (1 MiB of paths) → `export_failed: could not enumerate .git entries` (the
   volume is kept; `runs export` can retry).
2. **Report.** `dropped_git_entries` = each token relative to `/work`, as today.
3. **Roots.** `nested_roots` = the parent directory of every token at depth ≥ 2 (`/work/a/.git` →
   `a`), deduplicated. A root `.git` directory or symlink (depth 1) contributes no root — `/work`
   is the worktree and git skips its `.git` entry itself (§1.4).
4. **Splice, deepest first.** For each root `R`, in descending depth (component count), with
   `children(R)` = the roots strictly under `R` that have no other root between them:
   ```
   env GIT_DIR=/tmp/nested/<i> GIT_WORK_TREE=/work/<R> GIT_OBJECT_DIRECTORY=/gitdir/objects
       GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1, -w /work/<R>:
   git init -q --template=
   git read-tree --empty
   git add -A -- . :(exclude,literal)<child-rel>…       # one pathspec per child, own argv element
   git read-tree --prefix=<child-rel>/ <tree[child]>    # one per child
   tree[R] = git write-tree
   ```
   `GIT_OBJECT_DIRECTORY` makes every blob land in the export's own object store (`/gitdir`,
   whose alternates already reach the base objects), so the trees are visible to the main index.
   The nested worktree's root `.git` entry is skipped by git itself; the nested repository's own
   ignore rules apply inside it (it is its own worktree root).
5. **Main index.** For each top-level root `R` (no ancestor root):
   `git rm -r -q --cached --ignore-unmatch -- <R>` (drops the base commit's entries under it —
   a path the base tracked as a file and the worker turned into a directory included), then
   `git read-tree --prefix=<R>/ <tree[R]>`. Then `git add -A -- . :(exclude,literal)<R>…` with one
   pathspec per top-level root (each its own argv element: spaces, newlines and a leading `-` are
   inert; `literal` disables glob interpretation; a directory pathspec matches its subtree; the
   spliced entries do not match the pathspec and are left as spliced). Then `write-tree` as today.
   With no roots the argv is exactly today's `git add -A`.
6. **Safety net.** The index listing the export already reads (`ls-files -s`) must contain no
   mode `160000` entry; one → `export_failed: nested repository at <path> was not masked` (the
   enumeration missed a root, or git treated a directory as embedded for a reason this algorithm
   did not foresee). Fail loud, keep the volume.

Effect: the diff shows the worker's edits, additions and deletions under a nested root like
anywhere else; a committed nested repository never becomes a gitlink; the fatal `does not have a
commit checked out` cannot occur because `git add -A` never enters an embedded repository. Each
root is printed to stderr as `nested repository exported as plain files: <root>` and is derivable
from `dropped_git_entries` (dirname of the entry); no new `run_end` field. Real submodules of the
target repository (gitlinks in the base tree, checked out as empty directories with no `.git`) are
untouched by this — pre-existing behaviour, out of scope.

## 5. Telling the worker (revision 5)

### 5.1 The seam

`Sandbox` (the Protocol) documents `drain_notices() -> list[tuple[str, str]]`: returns and clears
the `(kind, text)` notices queued since the last drain, oldest first. `HostSandbox` returns `[]`.
`DockerSandbox` keeps `self._notices` under `_notices_lock` (§3.6; `reset()` may queue from the
watchdog thread); §3.4 queues `("stray_kill", text)`, §3.7 queues `("sandbox_reset", text)`. The
runner reads it defensively — `getattr(self.sandbox, "drain_notices", None)`, no notices when
absent — so the duck-typed sandbox doubles in `tests/test_runner.py` and any embedder's 1.0
`Sandbox` keep working unchanged.

### 5.2 Drain points in `Runner.run()` — every notice drained exactly once, on the turn it is drained

A helper `drain_sandbox()` → `(text, records)`: drains, writes one `nudge` event per notice
(`kind`, `turn` = the current turn; `via` left to the carrier), joins the texts with the
`_join_nudges` separator, and returns both (`("", [])` when nothing is pending). `nudge.turn` is
the turn the notice was **drained** on — for a notice the watchdog thread queued after a turn's
drain, that is the following turn. Notices ride **only the existing `deliver()` carriers**;
nothing is folded into the verify feedback text and `nudge.via` gains no value:

| path (runner.py) | drain | carrier / `via` |
|---|---|---|
| ordinary tool-call turn (bottom of `one_turn`) | before its `deliver(...)` | `deliver(_join_nudges(malformed_text, sandbox_text, timeout_text, stall_text), [malformed_record, *sandbox_records, timeout_record, stall_record])` — order **`malformed_entry`, sandbox notices (occurrence order), `timeout`, `stall`**; `via: tool_result` |
| `finish` turn, verify fails with feedback (`check_verify` returned feedback; `resolve_finish` has already made the feedback the finish call's `result`) | after `check_verify` returns, where the timeout nudge is delivered today | `deliver(_join_nudges(sandbox_text, timeout_text), [*sandbox_records, timeout_record])` when non-empty — the text becomes `follow_up` on the finish `tool_result`; `via: tool_result` |
| prose answer, verify fails with feedback (`kind == "answer"`) | after `check_verify` returns | `deliver(_join_nudges(feedback, sandbox_text), sandbox_records)` — one user message carrying both; `via: user` |
| text-only continuing turn (`truncated`/`empty`/`text_tool_call`) | before its `deliver(...)` | `deliver(_join_nudges(NUDGES[kind], sandbox_text, stall_text), [kind_record, *sandbox_records, stall_record])` — the drain is load-bearing (a watchdog-thread notice can be pending); `via: user` |
| every run end — `check_verify`'s ending branches (`completed`, `verify_failed`, `budget_exceeded`, `sandbox_error`) and all others (`max_turns`, `timeout`, `stalled`, `stuck`, `model_error`, tool-call `budget_exceeded`/`sandbox_error`, `interrupted`) | first statement of `finish(status, final)` — the single run-ending funnel | recorded, `via` absent (#60's sparse rule); nothing delivered |

A notice raised by the **verify command's own bash** therefore lands as `follow_up` on the finish
result (or in the feedback user message) when the run continues, and is recorded silently when it
ends — never a user message after a tool result, never two consecutive user messages (the #60
carriers are the only writers). A notice raised by a tool call earlier in the same `finish` turn is
drained by the same call. `drain_sandbox()` is idempotent, so the `finish()` drain after a
mid-turn drain writes nothing twice. `deliver()` is only called with non-empty text.

`stray_kill` and `sandbox_reset` are **not** `FailureTracker` kinds and do not feed the
consecutive-failure abort (like `timeout`).

### 5.3 Texts (constants in `strays.py`; the wording can change without a schema change)

- `stray_kill`: `The sandbox killed {n} background process{es} your last command left running
  ({cmds}). A process cannot outlive the bash call that started it — start and use anything you
  need within one command.` then, only when `locks_removed` is non-empty, ` Stale git lock files
  they left in the repository were removed.` and always ` Run \`git status\` to confirm the
  repository state before continuing.`
  `{cmds}`: the first three `strays`, each cut to 80 characters, joined by `; `, then `; +N more`.
- `sandbox_reset`: `The sandbox container was reset after your last command ({reason}). Files in
  the worktree are intact, but git metadata was re-initialized: the index, stashes, local commits
  and branches you created inside the sandbox are gone, and the branch is back at the run's base
  commit with your file changes uncommitted. Run \`git status\` before continuing.`

## 6. Transcript and evidence (revision 8)

### 6.1 Events (docker mode only, schema v2, additive)

**`stray_kill`** (new; the ninth event name) — written by the sandbox inside the bash call,
**before** that call's `tool_result` (the same position `sandbox_reset` has today):

| field | type | rule |
|---|---|---|
| `strays` | list of string | the CMD column of every detector row from the **first** `docker top`, in `docker top` order; at most **20** entries; each cut to **200** characters with a trailing `…` when cut; never empty (the detector fired) |
| `strays_total` | integer | **sparse**: present only when more than 20 rows were seen (then it is the full count) |
| `locks_removed` | list of string | **sparse**: present only when the sweep removed at least one file; absolute paths under `/gitdir`, in `find` order, at most 20 entries |

**`sandbox_reset`** gains the same `strays` / `strays_total` fields, **sparse**: present only when
`reason` is `stray process after bash` (rows from the ladder's first `top`, or from the only `top`
when the in-place rung was disabled). Other reasons carry `reason` alone, as today.

**`nudge`** gains two documented `kind` values, `stray_kill` and `sandbox_reset`, with the
existing `turn` and `via` (`tool_result` / `user`, unchanged; sparse on run-ending turns, as #60
defined). One `nudge` per notice.

Ordering within a turn: `[stray_kill | sandbox_reset]` → the bash `tool_result` (its `follow_up`
holds the whole delivered text when the notice rode there) → … → the `nudge` records at turn end.
`verify` still follows the gate's `_after_bash` events. Empty lists are never written: an empty
`strays` cannot occur, and `locks_removed` is omitted rather than `[]`.

### 6.2 Where counts surface (named against the code)

- `dirtywork/bench.py`: `NUDGE_KINDS` (:48) gains `stray_kill`, `sandbox_reset` **appended at
  the end**; `_event_counts` (:200) counts `stray_kill` events beside `sandbox_reset`;
  `run_one_bench_case`'s result row (:285, :348) gains `stray_kills` beside `sandbox_resets`.
  `_harness_failures` (:250) stops subtracting a hard-coded exclusion tuple: a new constant
  `EMPTY_REPLY_NUDGE_KINDS = ("empty", "truncated", "text_tool_call")` — exactly the kinds whose
  nudge path calls `failures.record("empty_reply")` (`runner.NUDGES`'s keys; a test asserts the two
  stay equal) — is what `empty_reply` sums over, so the new kinds (and `timeout`, `stall`,
  `malformed_entry`) can never be reported as model failures. Consequences, stated rather than
  denied: the `summarize` detail `nudges` cell (:431) and its legend (:776) widen from six to
  eight slash-separated numbers (columns appended); the `--compare` harness cell keeps its four
  components and legend, and its `nudges` total (:492) now includes the two kinds, exactly as
  `timeout` and `malformed_entry` were folded in before.
- `tools/soak_harvest.py` follows `bench.NUDGE_KINDS` and gains `stray kills` next to its reset
  count.
- `dirtywork/runs.py`: plain `runs show` prints `_timeline_line` (:289) — it gains a `stray_kill`
  branch (`stray_kill: N killed — <cmd>; <cmd>…`) and `sandbox_reset` gains a ` — strays: …`
  suffix; `runs show --markdown`/`runs export` render `_md_event_lines` (:361) — a `stray_kill`
  callout `> **stray_kill**: N process(es) killed — <code>, <code> …` (+ `, M lock file(s)
  removed` when present) and the same suffix on the `sandbox_reset` callout. Strays render through a
  new `_md_code(value, limit)`: `_md_trim` at `MD_ARGS_CHARS`, newlines collapsed to spaces, a
  backtick run longer than any inside the value as delimiter, space-padded per CommonMark, no
  HTML escaping inside the span (there is no inline-code helper today; `_md_inline` HTML-escapes and
  would break on a backtick).
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
four — in one snippet, in both places that carry the snippet (§8). With §3 in place, a daemon on
an image that lacks them costs one `stray_kill` notice per build call, not the worker's state.

## 8. Docs and contract

- `docs/machine-contract.md`: the bash-tool bullet (in-place kill, reset rungs, what a reset
  loses), the transcript-events paragraph (`stray_kill`, the two nudge kinds, `sandbox_reset.strays`;
  `via` text unchanged), the docker-mode bullet on git discovery (gitfile, no
  `GIT_DIR`/`GIT_WORK_TREE` in commands, the export keeps its own), the `--verify` note that the
  gate runs without `GIT_*` env, the `dropped_git_entries` row (root gitfile excluded; an entry at
  depth ≥ 2 means its parent was exported as plain files), and the `--image` bullet's condensed
  Dockerfile snippet (gains the four variables, keeping parity with docker/README's fuller one).
- `docs/transcript-schema.md`: `stray_kill` section; "eight event names" → nine; `sandbox_reset`
  table gains `strays`, `strays_total`; `nudge.kind` list and the merge-order sentence
  (`malformed_entry`, sandbox notices, `timeout`, `stall`; on a text turn the kind's nudge, sandbox
  notices, `stall`); event-order note; `dropped_git_entries` row; `runs show` rendering line.
- `docs/operating.md`: bash-tool section (processes are killed when the call returns; a reset is
  the fallback and the worker is told; timeouts), a "git inside the sandbox" paragraph (gitfile,
  what survives what, `git stash` is safe across a stray kill and lost across a reset, `/tmp` is
  not reclaimed by a stray kill), the nested-repository export rule.
- `docs/security.md`: the process-lifetime paragraph names both mechanisms (host `killpg`,
  docker in-place kill + reset); a **new** bullet under "Docker mode" states the worker container's
  environment (no `GIT_DIR`/`GIT_WORK_TREE`) and that the export container keeps them.
- `docker/README.md`: §7's five-line `.NET` snippet for `:0.10`-derived images; "do not bake
  `ENV GIT_DIR`/`GIT_WORK_TREE`"; the 1.0 image checklist item 6 covers all five variables.
- `dirtywork/builtin_tools.py` `BASH_SPEC.description` (wire text; `tests/fixtures/tool_schemas.json`
  regenerated): "Backgrounded processes are killed when the command returns. In docker mode you
  are told which; if they cannot be killed, or the container runs out of memory, it is reset: the
  working tree survives, but git state you created inside the sandbox (index changes, stashes,
  local commits) does not, and you are told when that happens." (host mode kills silently, as
  today — the sentence must stay true for both modes).
- `dirtywork/guardrails.py` comment (gitfile, not env); the 2026-08-15 spec gets a note at §3, §4,
  §6 and §7 as scoped in this document's header; the 0.10 `README.md` docker bullet if it mentions
  resets.
- Ledger: S11/S13 rows point here; the `#64` note about `env -u GIT_DIR -u GIT_WORK_TREE` is
  marked obsolete.

## 9. Tests

Unit (fake docker, `tests/docker_fakes.py` per-prefix scripting; `docker top` scripted as a list
so successive calls differ). Prerequisites: `FakeDocker` responses may be **callables**
`(argv) -> Captured` so a test can block a call on a `threading.Event` (for tests 6 and 7); the
shared `started`/`started_with_transcript` fixtures script `TETHER_DISCOVERY_SCRIPT`'s exact argv
with a valid pid so **the in-place rung is active by default** — a discovery answered by the
generic `["exec"]` default would silently disable it across the suite.

1. `_discover_tether`: single integer line → stored; rc 3 / two lines / `cannot open` noise /
   DockerError → `None` + one stderr line; re-run after `reset()`; the script's redirect order.
2. ladder happy path: top (stray rows) → the kill exec's argv is exactly `STRAY_KILL_SCRIPT, "_",
   "<pid>"`; second top clean → sweep exec → OOM inspect false → `stray_kill` event with `strays`
   in order and `locks_removed` from the NUL/full-match-filtered sweep output; notice queued;
   **no** `docker kill`; `_reset_this_call` stays `False`; the post-call budget sample still runs;
   the default fixture takes this path.
3. escalation: kill rc 3 / rc 1 / DockerError / timeout → `reset("stray process after bash")`
   with `strays`; tops `[dirty, dirty, dirty, dirty]` → same after three re-checks; `[dirty, dirty,
   clean]` → `stray_kill`; a re-check rc ≠ 0 → unreachable reset; sweep DockerError → `stray_kill`
   without `locks_removed`, no reset; OOM true after a clean kill → `reset("oom")`;
   `_tether_pid is None` → straight to reset with `strays`.
4. limits: 25 stray rows → 20 entries + `strays_total: 25`; a 300-char CMD → 200 + `…`;
   `locks_removed` absent when the sweep printed nothing or only non-matching lines.
5. the script constants are fork-free (no `$(`, `|`, `` ` ``, `(`); the kill script has three
   passes; the pid arrives as `$1`.
6. serialization: a blocking `docker top` on the main thread while the watchdog thread's
   `_sample_worktree(wait=False)` runs → it returns `None` without any docker call; the main
   thread's `wait=True` sample after the ladder runs normally; a thread inside `reset()` while the
   main thread enters `_reap` → no deadlock (join with timeout), exactly one reset; the timeout
   path through the ladder (`timeout_result` then a stray row → `stray_kill`).
7. flags: `reset()` and `_watchdog_kill` set `_reset_this_call` before `docker kill` (spy order);
   a watchdog kill with a recorded violation while a sample exec is scripted to fail → zero
   `reset()` calls, zero `sandbox_reset` events, `_after_bash` raises `BudgetExceeded` with the
   disk-floor reason; `_after_bash` clears the flag in a `finally` even when `reset()` raises;
   `_shutting_down` makes `_sample_worktree` return `None` and `reset()` a no-op; `bash()` calls
   `note_bash_end()` when the exec raises `KeyboardInterrupt`; `grep()`'s timeout branch execs the
   kill script once under `_reap_lock` with no top/sweep/event/notice.
8. `docker_args`: worker create argv has no `GIT_DIR`/`GIT_WORK_TREE`; export argv has both;
   base env unchanged otherwise.
9. `init_worker_git(layout="gitfile")` script text (rm → init --separate-git-dir → tail, first
   vs restart); `layout="env"` unchanged byte for byte.
10. export: the enumeration argv (`/bin/sh -c EXPORT_GIT_ENTRIES_SCRIPT`); parsing with a
    NUL-terminated name containing a newline and a space, a non-`/work/` token and an unterminated
    tail (dropped); rc ≠ 0 continues, `truncated` fails the export; roots and `children()` for
    `a/.git`, `a/b/.git`, `c/.git`; the exact exec sequence for the splice (env per nested exec,
    depth order, `read-tree --empty`, `add -A` with the child exclusions, `read-tree --prefix`,
    `write-tree`, main `rm --cached` + `read-tree --prefix` + `add -A` exclusions); a root `.git`
    directory listed but no root; no roots → today's argv byte for byte; a `160000` index entry →
    `export_failed: nested repository at … was not masked`.
11. runner drain points (a scripted sandbox whose `drain_notices()` returns queued notices, and
    one without the method): ordinary turn → nudge events + `follow_up` = joined text in the
    documented order, `via: tool_result`; `finish` + failing verify → `follow_up` on the finish
    `tool_result` = notice (+ timeout), `via: tool_result`, the finish `result` = feedback only;
    prose answer + failing verify → one user message = feedback + notice, `via: user`; text-only
    turn with a pending notice → user message, `via: user`; verify passed / `verify_failed` /
    `max_turns` / `model_error` → records written, `via` absent, no delivery; a notice from a call
    before `finish` in the same turn → carried with it; no double writes across `check_verify` +
    `finish()`; `deliver` never called with empty text.
12. `bench`: `stray_kill` counted, the two nudge kinds counted, `stray_kills` in the row,
    `empty_reply == 0` for a run with only sandbox nudges, `EMPTY_REPLY_NUDGE_KINDS ==
    tuple(runner.NUDGES)`, the compare cell's four components; `runs show` plain line and Markdown
    callout, `_md_code` with a value containing a backtick, `|`, `*` and a newline;
    `test_transcript_schema.py`: `EVENT_NAMES` gains `stray_kill`; the doc-token check extended with
    a synthetic `stray_kill`, a `sandbox_reset` carrying `strays`, and the two nudge kinds,
    tokens taken from the `### stray_kill` / `### sandbox_reset` / `### nudge` sections only (a
    whole-document token scan would pass vacuously).

Live (`@pytest.mark.docker`, `tests/test_docker_live.py`, `DIRTYWORK_LIVE_IMAGE` honoured):

13. a `nohup sleep 300 &` stray → `stray_kill` event, no `sandbox_reset`, a `git stash` made
    before the call pops afterwards, the same container ID before and after.
14. a `cat`-named stray alongside a `sleep` stray → both dead after the call.
15. a killed git: a command that creates `/gitdir/index.lock` and backgrounds a `sleep` →
    `locks_removed` names it and the next `git status` succeeds.
16. S13 and export: `cd $(mktemp -d) && git init && git status && git worktree list` inside the
    sandbox stays local; `git -C /work status` works from `/tmp`; a worktree with a nested
    **uncommitted** repository the worker edited (a new file, a modified base-tracked file and a
    deleted one under it) exports those changes as plain files, lists `<root>/.git` in
    `dropped_git_entries`, and has no gitlink; the same with a committed nested repository and a
    two-level nesting.
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
21. a timed-out `grep` (a fixture file the pattern matches slowly, `timeout=1`) leaves no row in
    `docker top` and the next bash call produces no `stray_kill`.

## 10. Acceptance evidence, gates, and the build

- **P7 is satisfied** (§1.2): the race is reproduced on production code and eliminated by design
  (§3.6); test 17 keeps it eliminated.
- Built by the released dirtywork (0.10.1) against this repository per the owner's dogfood rule:
  chained runs off `issue-61-sandbox-resets` (`--branch-from`), qwen3-coder-next, image
  `dirtywork-worker-pytest:0.10`, ≥ 60 turns, sampler on, one ledger row per run; Claude writes
  the prose docs and reviews every branch; a Claude implementer only after a failed
  resume-with-feedback, stated in the PR. The verify gate is `env -u GIT_DIR -u GIT_WORK_TREE
  python3 -m pytest -q -p no:cacheprovider` **until T1 lands**, then the plain command — its
  passing inside the sandbox is itself the S13 acceptance.
- **Task boundaries** (each independently testable, each a bounded file set sized for a 65k
  context; the plan step refines them):
  - **T1 — gitfile layout** (§4.1–§4.3): `docker_args.py` env split, `lifecycle.py` two layouts,
    `docker.py` call sites, `guardrails.py` comment; tests 8, 9, 20.
  - **T2 — export** (§4.4): `export.py` enumeration, roots, splice, safety net; test 10, 16.
  - **T3 — stray ladder** (§3): new `strays.py` (constants, parser, texts), `docker.py`
    discovery/kill/sweep/OOM/locks/flags/shutdown/`grep` timeout, `watchdog.py` `wait=False`
    path, `docker_fakes.py` callable responses, fixtures; tests 1–7, 13–15, 17–19, 21.
  - **T4 — notices and evidence** (§5, §6): `sandbox/__init__.py` Protocol doc, `host.py`,
    `runner.py` drain points, `bench.py`, `runs.py`, `soak_harvest.py`, `builtin_tools.py` text +
    fixture regen, `test_transcript_schema.py`; tests 11, 12.
  - **T5 — image and docs** (§7, §8): Dockerfile, docker/README, the five docs, the 2026-08-15
    notes, ledger — prose by Claude, Dockerfile + checklist by the worker.
- Soak re-runs: `D3-issue97` (the S11 run: `git stash` survives `dotnet test`) and one
  `run-bash-buildsh` (class A/D) on the built branch, rows in the ledger.

## 11. Out of scope

The `cat`-named-stray detector loophole (documented; a follow-up may use `docker top -eo
pid,ppid,etimes,args` and the oldest `cat` child of `docker-init`); a PID-reuse guard on the
tether (impossible while the container lives, §3.2); reclaiming `/tmp`/`/home/worker` after an
in-place kill (documented trade, §2); `pytest-of-unknown` under `/work` (P4: cannot happen with
the sandbox's own `/tmp`); S12 (fractional floats for integer params); real submodules of the
target repository in the export; harness-injected `.NET` environment; host-side inheritance of the
operator's `GIT_DIR` (refuted as out of scope); moving `/gitdir` onto the volume (approach C,
declined: `--gitdir-size`/`run.json` churn before the freeze, objects against the worktree budget,
a real `.git` under `/work` colliding with the export guards).
