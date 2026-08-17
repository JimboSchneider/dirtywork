# FINAL whole-branch review — SP2 Docker sandbox (23a9c22..16d2a7f)

**Reviewer:** Senior Code Reviewer (single reviewer, no subagents dispatched)
**Range:** `23a9c226616062c13c56cf47bbc545b82f43c3d0..16d2a7f` — 44 files, +6361/-315
**Binding authority:** `docs/superpowers/specs/2026-08-15-review-response-design.md` §SP2 + threat model + success criteria
**Plan:** `docs/superpowers/plans/2026-08-16-sp2-docker-sandbox.md`

## How this review was done (passes)

The diff package is ~390 KB, so I did not read it as one blob. Passes:

1. **Spec + plan pass** — read §SP2 (decision record, §1–§9), the threat model,
   and the success criteria; read the plan's task index and Task 16 in full.
2. **Source pass at HEAD** — checked out `16d2a7f` into a throwaway worktree
   (`git worktree add --detach`; the review checkout's HEAD/index/branch were
   never touched) and read the *final* text of every runtime file rather than
   its accumulated hunks: `dirtywork/sandbox/{__init__,host,lifecycle,docker_cli,
   docker_args,docker,export,watchdog}.py`, `dirtywork/{__main__,guardrails,
   procs,tools}.py`, `docker/Dockerfile`, `docker/README.md`, `pyproject.toml`.
3. **Targeted diff pass** — `git diff` restricted to files whose *change* (not
   final state) is what matters: `runner.py`, `rundir.py`, `workspace.py`,
   `tools.py`, `guardrails.py` (vs. main), `tests/test_guardrails_bash.py`.
4. **Test pass** — enumerated every test function, read the security-relevant
   suites in full (`test_export_validator.py`, `test_export_flow.py`,
   `test_docker_live.py`, `test_docker_lifecycle.py`, the docker-mode
   `test_main.py` cases), AST-scanned all suites for assertion-free tests.
5. **Execution pass** — ran the unit suite locally on Python 3.9.6 (the stated
   floor): **448 passed, 14 deselected in 22.85 s**, matching the claim exactly.
   Docker was not invoked, per the brief; the 7 host-sentinel + 3 lifecycle
   live results are taken from the controller.
6. **Differential pass** — executed `main`'s and `HEAD`'s `check_bash_command`
   side by side to get evidence for the guardrail-ordering finding rather than
   asserting it from reading.

---

## Strengths

**The security model is implemented as designed, not approximated.**

- **No repo bind mount anywhere.** `grep 'type=bind\|type=volume\|"-v"'` over
  `dirtywork/` returns exactly five hits: three `type=volume` (prep, worker,
  export) and two `type=bind` — both `objects_dir` with `,readonly`
  (`docker_args.py:73,136,137,166,167`). The spec's central v3 claim ("no host
  path is ever inside the container" except the object store) holds literally.
- **`validate_objects_dir` (docker_cli.py:127-166)** does exactly what §2 step 1
  specifies and in the right order: `lstat` the final component and refuse a
  non-directory (so a symlinked `objects` cannot substitute a host directory),
  *then* require `objects.resolve()` to be inside `git-common-dir.resolve()`.
  Both failure modes have tests (`test_docker_cli.py:201,211`).
- **The tar validator (export.py:65-161) is the strongest code on the branch.**
  It never calls `tarfile.extract()`/`extractall()`; it enforces member type,
  name rules, `.git` case-insensitively, per-member `realpath` containment
  *before* creating anything, `O_EXCL|O_NOFOLLOW` file creation, explicit mode
  normalization, and a `_CountingReader` that bounds bytes actually *read* off
  the stream rather than trusting declared header sizes. The PAX global check
  is done both inside and after the loop as defense in depth. 23 unit tests
  cover every rule in the spec's bullet list, including write-through-symlink,
  hardlink, FIFO, device, duplicate names, and per-member (accepted) vs. global
  (rejected) PAX.
- **The fail-closed discipline around `docker` is real.** `docker_cli.run()`
  converts a timeout into `DockerError` instead of returning a `Captured` an
  incomplete caller could ignore, and every `_run` call site carries an explicit
  timeout. The two streamed steps that *can't* use that timeout (`git diff`,
  `git archive` read from a pipe) are bounded by a `threading.Timer` kill at
  `T_EXPORT_STEP` (export.py:264, 300) — a genuinely thoughtful gap-closure.
- **Export always `--network none` regardless of `--allow-network`**
  (`docker_args.py:157`), pinned by a test named for that exact property.
- **`host_read_tree` runs only after `export_status == "ok"`**
  (docker.py:225-232), with an explicit comment on why read-tree after a failed
  export would lie to `git status`. Tested three ways.
- **The live suite earns its keep.** `test_docker_live.py` captures real host
  sentinels (`.git/config` bytes, `for-each-ref`, per-object SHA-256 map, an
  outside file, and an operator-configured `filter.evil` clean/smudge sentinel)
  and compares them after a real run in which the worker writes
  `.gitattributes * filter=evil`. That is the spec's headline success criterion
  proven, not asserted.

**Engineering quality beyond security:**

- Clean layering: pure argv builders (`docker_args`) / one CLI entry point
  (`docker_cli`) / stateful backend (`docker`) / shared lifecycle steps
  (`lifecycle`) / export flow (`export`) / watchdog. `DockerSandbox` takes
  injectable `run`/`popen`, so the unit suite is fake-backed by construction
  and the `docker`-marked suites are the only ones touching a daemon.
- `ToolExecutor` slimmed to a real dispatcher (tools.py:354-390) — the budget
  check moved into `HostSandbox`, where it belongs, and `Sandbox` is a genuine
  Protocol both backends satisfy.
- `procs.run_capped` extraction is a clean DRY win: one drain-thread +
  process-group-kill implementation now serves host `bash` and every `docker`
  call, and the `stdin=DEVNULL` default is the right primitive.
- The stdout JSON contract is **strictly additive**: base emitted `status,
  worktree, branch, transcript, turns, usage, final_message, run_dir,
  base_commit`; HEAD emits all nine plus `schema_version`, `finalize_error`
  (and `export_status` on the `_fail_run` path). `_emit_result` is the single
  shaping point for every path, which is exactly how you keep it from drifting.
- Python 3.9 floor genuinely respected: every runtime module carries
  `from __future__ import annotations`, no `match`, no `dataclass(slots=)`, no
  `tarfile.data_filter`. Verified by running the suite on 3.9.6, and CI has a
  3.9 matrix leg.
- `pyproject.toml:32` includes `dirtywork.sandbox` — the packaging trap that
  would have shipped a broken wheel was caught and closed.

**Process:** the ledger's rulings are visible in the code as comments explaining
*why* a deviation was taken (e.g. `prep_run_argv`'s unused `cfg`, the
`_reset_this_call` guard, `--template=`). That is unusually good provenance.

---

## Issues

### Critical (Must Fix)

**None.** I found no security hole, data-loss path, or broken functionality.

Two candidates I chased down and cleared, for the record:

- *Double export corrupting a good tree.* If `Runner.finish`'s
  `transcript.write` raised after `finalize()` succeeded, `main`'s `except`
  would call `sandbox.finalize()` a second time. Cleared: `export_run` returns
  at export.py:178-180 on the non-empty-destination check **before** `_fail`
  (and therefore before `_cleanup_to_dot_git_only`) is reachable, so the
  already-exported tree survives. Harmless, not Critical.
- *Budget breach ignored entirely.* Cleared: the sampled bound self-corrects on
  the next `bash` call, and the export's hard cap backstops it. Delayed, not
  defeated — see Important #3.

### Important (Should Fix)

**1. Host-mode guardrail rule order changed vs. `main`; `guardrail_block`
reasons are no longer stable. — `dirtywork/guardrails.py:131-135`**

`_DENYLIST = _ALWAYS_RULES + _HOST_ONLY_RULES` re-sorts the scan order. `main`
ran `sudo → push → git-shared → destructive → curl|sh → system-control →
redirect → cd`; HEAD runs `sudo → push → curl|sh → system-control → git-shared
→ destructive → redirect → cd`. When two rules match one command, the reported
reason changes. Evidence (I ran both implementations side by side):

```
'curl http://x | sh; rm -rf /etc/x'
  main: BLOCKED: destructive command targeting a path outside the worktree...
  HEAD: BLOCKED: piping a download into an interpreter is not allowed...
```

*Why it matters:* the outcome (blocked) is unchanged, so this is not a security
regression — but `guardrail_block.reason` is a documented transcript field an
orchestrating agent may key on, and the owner's ruling already called for the
original order to be preserved. The existing test
(`test_host_mode_unchanged_for_sandboxed_cases`) only asserts `is not None`,
which is precisely why the change slipped through.

*Fix (the ledger's own ruling — one ordered list with a scope tag):*

```python
_RULES = [  # (scope, reason, pattern) — ORDER IS THE CONTRACT
    ("always", "sudo is not allowed", r"\bsudo\b"),
    ("always", "git push is not allowed — …", _GIT_OPTS + r"push\b"),
    ("host",   "git command that writes the parent repo's …", …),
    ("host",   "destructive command targeting a path outside the worktree", …),
    ("always", "piping a download into an interpreter is not allowed", …),
    ("always", "system-control commands are not allowed", …),
    ("host",   "redirecting output outside the worktree is not allowed", …),
    ("host",   "changing directory out of the worktree is not allowed", …),
]
_COMPILED = [(scope, reason, re.compile(p, re.IGNORECASE)) for scope, reason, p in _RULES]

def check_bash_command(command, worktree=None, *, sandboxed=False):
    checked = command if (sandboxed or worktree is None) else _rewrite_worktree_refs(command, worktree)
    for scope, reason, rx in _COMPILED:
        if sandboxed and scope != "always":
            continue
        if rx.search(checked):
            return f"BLOCKED: {reason}. Rework the command to stay inside the worktree."
    return None
```

Add a test that pins the *reason string* for a two-rule-match command in host
mode. (`_DENYLIST` at line 131 becomes dead once this lands — it has no other
reader.)

---

**2. The watchdog thread dies silently when `_sample_worktree` gives up, and the
disk-floor bound dies with it. — `dirtywork/sandbox/watchdog.py:97-108`,
`dirtywork/sandbox/docker.py:518`**

`Watchdog.run()`'s loop body is unguarded. `check_worktree_budget_once()` →
`self.sample()` → `DockerSandbox._sample_worktree`, which **raises
`SandboxError` after two consecutive exec failures** (docker.py:518). On the
watchdog thread that exception propagates out of `run()`: the thread exits, the
0.5 s `shutil.disk_usage` poll stops for the rest of the run, and the
`SandboxError` never reaches the runner — so the spec's "a second failure →
`sandbox_error`" is honored only on the synchronous `_after_bash` path, not on
the thread's own path.

*Why it matters:* threat-model item (d) says the host free-space floor is polled
"for the container's whole lifetime". After this exception it isn't, and nothing
observable says so. The trigger (repeated `docker exec` failure, e.g. pid
saturation) is exactly the hostile scenario the bound exists for.

*Fix:* wrap the loop body, and make thread-side failure terminal and visible:

```python
def run(self):
    last = self.clock()
    try:
        while not self._stop_event.is_set():
            ...
    except Exception as e:                      # never die silently
        self.violation = f"watchdog failed: {e}"
        try:
            self.kill(self.violation)
        except Exception:
            pass
```

With Important #3's fix, `_after_bash` then surfaces it on the next call.

---

**3. A budget violation recorded by the watchdog thread is discarded when
`_reap()` resets in the same `bash` call. — `dirtywork/sandbox/docker.py:520-529`**

```python
def _after_bash(self) -> None:
    self._reap()
    if self.watchdog is not None and not self._reset_this_call:   # <-- skips BOTH
        self.watchdog.check_worktree_budget_once()
        if self.watchdog.violation is not None: ... raise BudgetExceeded(...)
    self._reset_this_call = False
```

The `not self._reset_this_call` guard is meant to skip *re-sampling* a
just-rebuilt container. It also skips *reading a violation the watchdog thread
already recorded*. Reachable sequence: a `bash` call writes past
`--max-worktree-mb` in >5 s → watchdog thread sets `.violation` and `docker
kill`s → the in-flight exec dies → `_reap()` sees an unreachable container and
resets → `_after_bash` skips the block → the violation is never raised. The run
continues. It self-corrects on the *next* `bash` call (the volume still holds the
oversized tree) or at export, so this delays the bound rather than removing it —
but it contradicts the documented contract, and
`test_after_bash_skips_budget_sample_when_reap_already_reset`
(`tests/test_docker_sandbox.py:701`) currently enshrines the swallow.

*Fix:* skip only the sampling, never the consumption.

```python
def _after_bash(self) -> None:
    self._reap()
    if self.watchdog is not None:
        if not self._reset_this_call:
            self.watchdog.check_worktree_budget_once()
        if self.watchdog.violation is not None:
            violation, self.watchdog.violation = self.watchdog.violation, None
            raise BudgetExceeded(violation)
    self._reset_this_call = False
```

The existing test still passes (it asserts no `du -sk /work` exec, which stays
true). Add one that sets `.violation` *and* forces a reap-reset in the same call.

---

**4. `reset()` can run concurrently on the watchdog thread and the main thread.
— `dirtywork/sandbox/docker.py:400-429`, `498-517`**

`_sample_worktree` calls `self.reset(...)` on its first exec failure, and it is
invoked from `Watchdog.run()` (thread) as well as `_after_bash` (main thread).
`reset()` does `docker kill` → `docker wait` → `close_tether` → `_start_tether()`
→ `_wait_ready()` → `_init()`. Two concurrent entries can leave one
`docker start -ai` Popen orphaned (`self._tether` overwritten), or kill the
container while the other thread's `_init` is running. `_reset_this_call` is a
plain unlocked bool, and `Watchdog._lock` deliberately covers only
`_bash_in_flight` (flagged as a Task 9b ⚠️ and never closed).

*Why it matters:* the lifecycle suite asserts "no leaked `docker start -ai`
child of the test" — a leaked tether is a documented failure mode of this design,
and an orphaned attach keeps a container alive past the run.

*Fix (smallest sufficient):* give `DockerSandbox` a `threading.RLock` and take it
in `reset()`, `_reap()`, and `_sample_worktree`; make `_reset_this_call` reads
and writes happen under it. Alternatively, forbid the thread from resetting at
all — have `_sample_worktree` set a "sample failed" flag the main thread acts on —
which is simpler and matches "the container's lifecycle belongs to the main
thread".

---

**5. `docker/README.md` documents building/publishing `dirtywork/worker:0.3`
while the code defaults to `:0.4`, and `PINNED_DIGEST` is still `None`.
— `docker/README.md:12,16-19,30-31,35`, `dirtywork/sandbox/docker_args.py:8,12`,
`tests/test_docker_image.py:11`**

`DEFAULT_IMAGE = "dirtywork/worker:0.4"` but every command in the image README
says `0.3`, and the build smoke test tags `dirtywork/worker:0.3-test`. An
operator who follows the documented procedure produces an image the default
`--image` will not find.

*Why it matters more than a typo:* docker is now the **default** execution mode.
If `dirtywork/worker:0.4` is not actually pushed to Docker Hub before 0.4.0
tags, the first command every new `pipx install dirtywork` user runs fails at
preflight. That is the single largest ship risk on this branch, and it is not
something the test suite can catch.

*Fix:* s/0.3/0.4/ in `docker/README.md` and `test_docker_image.py`; add to the
release checklist: build → push `dirtywork/worker:0.4` → resolve digest → set
`PINNED_DIGEST` → commit → tag. (The `PINNED_DIGEST` mechanism itself is
implemented and tested — `test_docker_cli.py:122,132` — it just needs a value.)

---

**6. The preflight hint blames the daemon for failures that are not the daemon's.
— `dirtywork/__main__.py:314-320`**

```python
image_ref = _docker_preflight(repo, args.image)
except DockerError as e:
    _err(f"{e}\nDocker is the default sandbox since 0.4. Start Docker Desktop / "
         f"dockerd, or pass --sandbox none to run unsandboxed on the host.")
```

`_docker_preflight` runs three things (`docker_version`, `resolve_image`,
`validate_objects_dir`), and `resolve_image` raises `DockerError` for a *missing
or unpullable image* and for a *pinned-digest mismatch*. All three get the "start
Docker Desktop" hint. Given #5, the most likely real-world failure ("image
`dirtywork/worker:0.4` cannot be pulled") produces a message telling the user to
start a daemon that is already running.

*Fix:* let `resolve_image`'s own message lead, and branch the hint — daemon
failures get the start-Docker hint; image failures get "build or pull the worker
image (see docker/README.md), or pass `--image <ref>`".

---

**7. Docs and error messages point users at `dirtywork runs …` subcommands that
do not exist in this release. — `README.md:89,273`,
`dirtywork/sandbox/docker.py:99,105`**

`_parse_args` (`__main__.py:266-290`) registers exactly one subcommand: `run`.
But:

- README:89 tells the operator to review a worker-authored `.gitattributes`
  safely "via `runs show --diff`" — the *recommended mitigation* for a documented
  residual exposure is a command that does not exist. The patch is really at
  `<run_dir>/diff.patch`; say that.
- README:273 (`export_failed` troubleshooting) says "re-run export after raising
  the limit" with no command, then suggests `--keep-volume on a fresh run`, which
  does not recover the *old* volume.
- The name-collision error the operator will actually see says
  ``run `dirtywork runs clean <slug>` `` — a dead end with no manual fallback.

*Why it matters:* these are the two paths a user hits when something goes wrong
in the new default mode, and both terminate in a nonexistent command. This is
the "honest docs" question the brief asks about, and it is the one place the
answer is no.

*Fix:* until SP3 lands, replace each with the concrete manual equivalent —
`cat ~/.dirtywork/runs/<slug>/diff.patch`, and
`docker rm -f dw-<slug>; docker volume rm dw-<slug>-work` for the collision
message (with the existing "these are not mine to remove" caveat).

---

**8. The lifecycle "release gate" is missing the spec's mid-run daemon-hang case.
— `tests/test_docker_lifecycle.py:195-223` (plan gap, not an implementer defect)**

Spec §9 gates docker-as-default on five cases. Implemented: SIGKILL recovery (1),
container killed during exec + reset + continue (4, 5), and — arguably — killed
while attached (2). The spec's case 3 is *"Docker daemon unavailable **mid-run**
(a stub `docker` on `PATH` that hangs → per-command timeout → `sandbox_error`,
exit within the timeout, nothing hung)"*. `test_docker_lifecycle_daemon_hang_
fails_closed_within_timeout` shadows `docker` from process start, so it exercises
the **preflight** path and asserts `returncode == 2` — it never reaches
`sandbox_error`, and the `finally`-path teardown-with-a-hung-daemon behavior
(which is where a hang would actually strand a container) is untested.

The plan is the cause: Task 16 specifies only three steps, and its Step 3 is the
preflight variant. The commit message is honest about it ("partial: 3 of the
specified tests"). Flagging it because docker is the default *now* and the spec
says the gate is what authorizes that.

*Fix:* one more test — start a run normally, wait for the container, then
prepend a hanging `docker` stub to a `PATH` the child re-reads, or (simpler and
deterministic) drive `DockerSandbox` in-process with a `run` that sleeps past
`T_LIFECYCLE`, and assert status `sandbox_error`, exit 1, bounded wall clock, and
no surviving labelled container/volume. If that is deferred, soften the docs'
"release gate passed" framing to name which cases were run.

### Minor (Nice to Have)

1. **`list_dir`/`grep` pass the model's path as the first argv token —
   `docker.py:330,342,385`.** `_rel` normalizes but does not neutralize a leading
   `-`, so `list_dir("-delete")` becomes `find -delete -mindepth 1 …`, i.e. GNU
   find with no path (defaults to `.`) and `-delete` as the expression. It only
   destroys the worker's own tree inside the container (a model with `bash` can
   already do that), so this is containment-irrelevant — but it is a silent
   footgun. One-token fix: pass `"./" + rel` (and `rel` → `"./"+rel` for grep).
2. **A backgrounded process literally named `cat` survives the reap —
   `docker.py:465-467`.** `_reap` treats a `cat` CMD row as the tether. `cat &`
   with no arguments would be missed. Harmless in practice; tighten by matching
   the tether's PID (1 / its tini child) rather than its command string.
3. **`find` failure during export silently yields an empty
   `dropped_git_entries` — `export.py:229-235`.** `if find_captured.returncode == 0`
   with no `else`. The spec's rationale is "we report them instead of silently
   dropping"; a failed probe should surface as e.g.
   `dropped_git_entries=["<scan failed>"]` or a note in `export_status`.
4. **Export container tmpfs sizes are hardcoded while `--memory` is not —
   `docker_args.py:163-165`.** `/gitdir 2g + /tmp 256m + /home 64m ≈ 2.3 g` is
   sized against the *default* `--memory 4g`; `--memory 1g` makes export
   OOM-kill deterministically. Either scale them from `cfg.memory` or clamp/warn.
5. **`resolve_image` mis-parses `registry:port/name:tag` —
   `docker_cli.py:52`.** `image.split("@")[0].split(":")[0]` yields
   `localhost` for `localhost:5000/img:tag`, so the RepoDigests match fails and
   the `.Id` fallback builds a bogus `localhost@sha256:…` ref. Fails loud at
   `docker create`, so low severity — but it silently defeats digest resolution
   for private registries. Split on the last `:` after the last `/`.
6. **`validate_objects_dir`'s two `subprocess.run` git calls have no timeout —
   `docker_cli.py:134,143`.** Everything else on this branch is timeout-bounded;
   a wedged `git rev-parse` hangs preflight with no message. (Same pre-existing
   pattern in `workspace._git`, which `host_read_tree` inherits.)
7. **Dead imports.** `docker.py:12` (`Captured`, `run_capped` — neither used),
   `export.py:12` (`field`). Also `guardrails.py:131` `_DENYLIST` becomes dead
   under Important #1's fix.
8. **Inaccurate comment — `docker.py:476`:** "If inspect fails, don't reset - it
   would be recursive." `reset()` never calls `_reap()`, so recursion is not the
   reason. State the real one (don't act on a possibly-transient `inspect`).
9. **Docker-mode `BLOCKED` messages end with "Rework the command to stay inside
   the worktree." — `guardrails.py:180`.** Nonsensical advice for `sudo` or
   `git push` inside a container. Give the sandboxed branch its own tail.
10. **`bash` in docker mode can surface `exit code: None` — `docker.py:554`.**
    Host `tools.bash` gained an explicit `returncode is None → "ERROR: bash
    failed: …"` branch; the docker path did not. Mirror it.
11. **Docker-mode `write_file`/`edit_file` never trigger a budget sample.** Host
    mode re-measures after every mutating call (`host.py:47-55`); docker mode
    only checks in `_after_bash`. Spec-conformant (§6 says "after every `bash`
    call"), and the disk floor plus the export cap backstop it — but a
    write_file-only run has no worktree bound until export. Worth one sentence in
    the README's "what Docker mode does not give you".
12. **Stale version references in docs.** `README.md:42` "a breaking change from
    0.2" and `SECURITY.md:35` "keeps 0.2's guardrail-only behavior" — the
    previous release was 0.3.0. `README.md:69` ends a bullet with a dangling
    "Non-Windows." fragment. `README.md:14,186` and `SECURITY.md:14` say
    "a read-only **copy** of the parent object store"; it is a read-only *bind
    mount* of the real directory — the distinction matters for the very
    confidentiality caveat the same paragraph makes.
13. **Machine-contract section drift — `README.md:307-321,336-341`.** The stdout
    example omits `base_commit` (pre-existing) and the new `finalize_error`; the
    exit-1 status list omits `sandbox_error` and `export_failed` even though the
    `status` enum above it includes them.
14. **`extract_validated`'s emptiness check uses `is_file()` —
    `export.py:68`.** `is_file()` follows a symlink, so a `.git` symlink-to-file
    would pass where `_cleanup_to_dot_git_only` (line 54) correctly checks
    `and not entry.is_symlink()`. Unreachable given SP1's pre-`worktree add`
    ENOENT check; align them anyway.
15. **`_write_run_json_start` is unguarded — `__main__.py:357`.** An IO failure
    there escapes `main()` as a traceback with no JSON on stdout, before the
    try-block that guarantees the contract. Move it inside, or wrap it.
16. **Non-reproducible image build — `docker/Dockerfile:23-28`.** `dotnet-install.sh`
    is fetched over the network and executed with no checksum, and `--channel 8.0`
    floats. Runtime digest pinning covers the *run*; the *build* is not
    reproducible. Note it in `docker/README.md`.
17. **`test_docker_image.py` asserts only `git --version`** despite the image
    README listing rg/python3/dotnet/node as contract. Cheap to extend.
18. **Bench docs embed a local absolute path** —
    `docs/superpowers/bench/2026-08-16-sp2-sdd-ledger.md:4` contains
    `/Users/jimschneider/Library/Python/3.9` and the research doc lists host
    hardware. Fine if intentional; worth a conscious decision before it is public.

---

## Triage of the deferred / parked list

`final-deferred-list.md` + `progress.md` "Deferred" entries, with a verdict each.

**Must fix before merge — 1 (from the parked list itself):**

| Item | Verdict |
|---|---|
| T15 re-review: `_DENYLIST = _ALWAYS + _HOST_ONLY` reorders host-mode rule scanning (owner ruled: "carried into the FINAL-REVIEW FIX WAVE") | **Must fix** — Important #1. ~15 lines, plus one reason-pinning test. |

**Can ship — 24:**

| Task | Deferred item | Verdict |
|---|---|---|
| T1 | dead `original_popen` local in test | can ship (cosmetic) |
| T1 | redundant `not captured.timed_out` clause in `tools.bash` | can ship (dead but harmless) |
| T2 | no test that read_file/list_dir/grep skip the budget check | can ship |
| T2 | `_measure() -> dict` should be `BudgetReport` | can ship |
| T3 | `resolve_image` mis-parses `registry:port` refs | can ship — Minor #5; fails loud |
| T3 | `validate_objects_dir` git calls lack timeout | can ship — Minor #6; preflight-only hang |
| T5 | test import block non-alphabetical | can ship |
| T6 | `_wait_ready` per-attempt timeout == overall deadline | can ship (bounded by the same deadline either way) |
| T6 | `_wait_ready` deadline-exceeded path untested | can ship |
| T6 | `start()` ~65 lines | can ship |
| T7a | unused imports in `docker.py` | can ship — Minor #7 |
| T7b | `_probe` "hardcoded 30 s" | can ship (now the named `LIST_EXEC_TIMEOUT`) |
| T7b | stale comment above grep probe | can ship |
| T7b | `wc -c` would misparse a file named `total` | can ship (1-byte cosmetic error) |
| T8 | "it would be recursive" comment inaccurate | can ship — Minor #8 |
| T8 | tether-teardown duplication | **already resolved** by `lifecycle.close_tether` |
| T9b | no test that `.violation` resets after consumption | can ship (but add it with Important #3) |
| T9b | timeout+violation interaction untested | can ship (same) |
| T9b | 4× `if self.watchdog is not None` | can ship (plan idiom) |
| T9b ⚠️ | **Watchdog lock covers only `_bash_in_flight`, not `.violation`/`kill()`** | **escalated** → Important #4 (thread-safety of `reset()`); see below |
| T10 | unused `field` import / Windows-symlink branch / function length | can ship — Minor #7 |
| T11 | `wait_ready` probe timeout == deadline_s | can ship |
| T11 | 5 untested `export_run` return paths | can ship |
| T11 | `reset()`'s `except Exception` around `transcript.write` | can ship |
| T12 | possible double-export if `transcript.write` raises after finalize | can ship — **verified harmless** (export.py:178-180 returns before any cleanup) |
| T12 | `_final_status` overrides `max_turns`/`timeout` | can ship (mandated; live-tested at `test_docker_live.py:212`) |
| T12 | `DOCKER_WORKDIR` duplicates `/work` literal | can ship |
| T12 | docker flag defaults hardcoded vs `DockerConfig` | can ship (drift risk only) |
| T12 | `_write_run_json_start` unguarded | can ship — Minor #15 |
| T13 | none | — |
| T15 | `_reap()` bool return unused | can ship |
| T15 | **watchdog thread's reset path bypasses the unlocked `_reset_this_call` guard** | **escalated** → Important #4 |

**Escalations out of the parked list (2), plus 5 new findings:** the two
watchdog-thread items above were parked as minors; taken together they are one
Important (concurrent `reset()` from two threads). New in this review:
Important #2 (watchdog thread dies on `SandboxError`), #3 (violation swallowed on
reap-reset), #5 (image tag 0.3/0.4 + unpublished image), #6 (misleading preflight
hint), #7 (docs point at nonexistent `runs …` commands), #8 (missing release-gate
case).

**Recommended merge gate:** Important #1, #5, #6, #7 (all small, all
mechanical). #2, #3, #4, #8 are correctness/robustness work I would want before
the **0.4.0 tag**, not necessarily before the branch lands on `main`.

---

## Pre-tag checklist for 0.4.0

1. `pyproject.toml:7` — bump `0.3.0` → `0.4.0` (deliberately deferred; confirmed
   still outstanding).
2. **Publish `dirtywork/worker:0.4`** and set
   `docker_args.PINNED_DIGEST` — otherwise the default mode fails preflight for
   every new install (Important #5).
3. Fix `docker/README.md` 0.3→0.4 and `tests/test_docker_image.py:11`.
4. Release notes / GitHub release must call the default-mode change a breaking
   change **from 0.3**, per spec §"success criteria". There is no `CHANGELOG.md`
   in this repo (also absent at base), so the release body is the only place it
   can live — spec says "called out in the changelog and README".
5. Resolve the `runs …` documentation dead ends (Important #7) or ship SP3.4
   first.
6. Decide whether the bench docs' local paths/hardware go public (Minor #18).

---

## Recommendations

- **Pin behavioral contracts, not just outcomes.** The guardrail regression
  survived because the test asserted "blocked" rather than "blocked *for this
  reason*". Any string an orchestrator can parse (`guardrail_block.reason`,
  `export_status` prefixes, status names) deserves at least one exact-match test.
- **Give the watchdog thread the same fail-loud discipline as `docker_cli.run`.**
  The branch's best property is "every docker call fails loud"; the one place
  that isn't true is the one background thread. A bare `except Exception` around
  a thread's loop body that records-and-kills would make it uniform.
- **Prefer "the thread reports, the main thread acts."** Letting the watchdog
  call `reset()` is the root of Important #4. Having it set a flag the main
  thread consumes in `_after_bash` removes the concurrency question entirely and
  is *fewer* lines than adding a lock.
- **Keep the docs' forward references honest.** The README is otherwise
  unusually candid (the "what Docker mode does not give you" section is a model
  of it) — which makes the three references to unshipped `runs` commands stand
  out more, not less.
- **Process note, positive:** the ruling-in-comment pattern (`# cfg is unused
  here and kept for call-site symmetry`, `# never wait on a process that may
  still be streaming`) made this review much faster and should be kept as a norm.

---

## Assessment

**Ready to merge?** **With fixes.**

**Reasoning:** The security-critical code — no repo bind mount, objects-dir
validation, the tar validator, timeout-bounded docker control plane, export-only
`host_read_tree` — matches the spec faithfully and is covered by tests that
exercise real behavior (real tars, real extraction, real host sentinels, 448
green on the stated 3.9 floor). Nothing here is Critical. What blocks a clean
merge is small and mechanical: restore the host-mode denylist order the owner
already ruled on, fix the 0.3/0.4 image-tag split and the misleading preflight
hint, and stop pointing users at `dirtywork runs …` commands that do not exist —
after which the remaining four Importants (watchdog thread robustness,
thread-safe `reset()`, and the missing mid-run daemon-hang gate case) should be
closed before the 0.4.0 tag rather than before the branch lands.
