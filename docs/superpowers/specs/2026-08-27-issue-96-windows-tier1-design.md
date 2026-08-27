# Windows tier 1: stop crashing on three POSIX-only calls (#96) — design

v1, 2026-08-27. Status: **draft for owner review; sequenced behind #87 (0.13.0).** Target: 0.13.x.

## 0. The owner's decision

Jim, 2026-08-27: file the Windows work as a feature, write the spec now, build it after #87. Windows
stays "unsupported" in the README until a Windows integration suite passes (`docs/security.md`);
tier 1 is the difference between *crashes on an `AttributeError`* and *runs, degraded and
documented*. Tiers 2 (long tail) and 3 (Docker live on Windows) are separate decisions.

Interim guidance for Windows users — WSL2 — is already in the README (#97, #98) and has one
receipt.

## 1. Problem and evidence

A Windows user hit `AttributeError: module 'os' has no attribute 'killpg'` on the first run
(`--sandbox docker` and `--sandbox none` alike). The advisory `windows-latest` CI leg — the
unit suite, allowed to fail, per-file table published — measures the gap on every push. Baseline
on main `ad70e60` (run 33100954477, artifact `junit-windows`):

| | |
|---|---|
| collected / ran | 817 of 1,375 |
| pass / fail / error | 635 / 150 / 29 |
| `os.O_NOFOLLOW` missing | 146 (117 failures + 29 setup errors) |
| `os.killpg` missing | 8 |
| long tail (`\` in expected paths, symlink semantics, docker argv, `cd`-prefix boundary) | ~25 |

**The leg has been blind to 558 tests.** The session ended with a `KeyboardInterrupt` at 99.9 s
(`_pytest/python.py:168`). Cause: `tests/test_resume.py::test_pid_alive` calls
`pid_alive(os.getpid())`, which does `os.kill(pid, 0)` (`dirtywork/resume.py:133`) — the POSIX
liveness probe. On Windows signal `0` **is** `signal.CTRL_C_EVENT`, and `os.kill` with it calls
`GenerateConsoleCtrlEvent`, which delivers Ctrl-C to every process sharing the console — pytest
included. Every test file that sorts after `test_resume` never ran: `test_rundir`, `test_runner`,
`test_runs`, `test_sandbox_host`, `test_soak_tools`, `test_strays`, `test_tools_bash`,
`test_tools_files`, `test_toolspec`, `test_transcript`, `test_transcript_schema`,
`test_watchdog`, `test_workspace` (13 files; the other four absent files are `docker`/`live`
marked and deselected by design). The per-file table did not say so — it only lists files it saw.

`pid_alive` is product code with product callers: `runs.py:688` (`runs list`/`show` status),
`runs.py:893` (`runs clean`/`verdict` preflight), `resume.py:156,187` (resume preflights). On
native Windows, `dirtywork runs list` with a `running` run would Ctrl-C the operator's terminal.

So tier 1 is **three** POSIX-only calls, not two:

| call | sites | what it guarantees today |
|---|---|---|
| `os.killpg(pid, signal.SIGKILL)` after `start_new_session=True` | `procs.py:28,49` | "backgrounded children cannot outlive the call" (`run_capped` docstring) — applied unconditionally, clean exit included |
| `os.O_NOFOLLOW` (with `O_NONBLOCK`, `O_CLOEXEC` at some sites) | `tools.py:104,216,256,856`; `rundir.py:105`; `transcript.py:37`; `workspace.py:171,185`; `bench.py:394` | final-component symlink TOCTOU defence (SP1 §2.1) — a security control, not a convenience |
| `os.kill(pid, 0)` | `resume.py:133` | "is this pid alive" without side effects |

Precedent for platform branches already in the tree: `budget.py:167` (`os.name == "nt"`),
`runs.py:204` (uid fallback), `rundir.py:31` (`hasattr(os, "getuid")`).

## 2. Scope

In:

1. `procs.py`: a Windows process-tree kill with the same guarantee (§3.1).
2. One `open_nofollow()` helper; the nine sites call it; POSIX behaviour byte-identical (§3.2).
3. `pid_alive()` Windows branch with no console side effects (§3.3).
4. The advisory CI leg's summary reports **absent test files**, so a truncated session can never
   again read as "measured" (§3.4).
5. `docs/security.md`: a Windows paragraph stating exactly what degrades (§4).

Out (tier 2 / tier 3 / owner decisions): the ~25 long-tail failures; Docker mode on Windows;
making the Windows leg required; any README claim beyond "unit suite green in CI, still
unsupported"; pywin32 or any new dependency (everything below is `ctypes` + `msvcrt`, stdlib).

## 3. Design

### 3.1 Process tree kill — a Job Object, not `taskkill /T`

`taskkill /T` walks the parent→child tree *at kill time*; a grandchild whose parent already
exited is orphaned and missed — the exact leak `killpg` was chosen to prevent. A **Job Object**
makes every descendant inherit membership; killing the job kills all of them, no tree walk.

Windows branch of `run_capped`:

```
hJob = CreateJobObjectW(None, None)
info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE   # 0x2000
SetInformationJobObject(hJob, JobObjectExtendedLimitInformation (=9), byref(info), sizeof(info))
proc = subprocess.Popen(argv, ..., creationflags=CREATE_NEW_PROCESS_GROUP)  # no start_new_session
AssignProcessToJobObject(hJob, int(proc._handle))
... drain / wait exactly as today ...
TerminateJobObject(hJob, 1)      # replaces _kill_group(proc.pid); unconditional, as today
CloseHandle(hJob)                # KILL_ON_JOB_CLOSE is the belt to TerminateJobObject's braces
```

- POSIX branch: unchanged, byte-for-byte (`start_new_session=kill_group`, `_kill_group`).
- Structure: `_spawn(argv, ...) -> (proc, tree_handle)` and `_kill_tree(proc, tree_handle)`,
  each with the two branches; `run_capped`'s body otherwise untouched. `kill_group=False` on
  Windows: no job, `proc.kill()` — same as POSIX today.
- `proc._handle` is private API but stable across 3.9–3.14 (it is what `Popen.wait` uses);
  the alternative, `OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, proc.pid)`, is
  public and costs one more call. **Use `OpenProcess`**; the plan carries both signatures.
- Residual window: between `Popen` returning and `AssignProcessToJobObject`, the child could
  spawn a grandchild that escapes the job. Microseconds; documented next to the accepted
  PID-reuse window in `_kill_group`'s docstring. `CREATE_SUSPENDED` would close it but `Popen`
  does not expose the thread handle to resume; not worth a raw `CreateProcessW`.
- Nested jobs: GitHub's Windows runners already run inside a job. Nested jobs are supported on
  Windows 8 / Server 2012 and later; on older systems `AssignProcessToJobObject` fails with
  `ERROR_ACCESS_DENIED` — fall back to `taskkill /T /F /PID` and record `tree_kill: "taskkill"`
  in the run's `run_end` event so the degraded path is visible in receipts.
- `signal.SIGKILL` is referenced only inside the POSIX branch; it is never evaluated on Windows.

### 3.2 `open_nofollow(path, flags, mode=0o600, *, cloexec=True, nonblock=False) -> int`

New module `dirtywork/osfs.py` (one helper, two branches, no other logic). The nine sites call
it with the flags they pass today minus the three platform flags, which become keyword
arguments — so the POSIX composition is provably identical:

```
# POSIX
extra = os.O_NOFOLLOW | (os.O_CLOEXEC if cloexec else 0) | (os.O_NONBLOCK if nonblock else 0)
return os.open(path, flags | extra, mode)
```

Windows branch — the faithful equivalent, no check-then-open race:

```
access      = FILE_APPEND_DATA if flags & O_APPEND else (GENERIC_WRITE if flags & O_WRONLY else GENERIC_READ)
disposition = CREATE_NEW        if flags & O_CREAT and flags & O_EXCL
            = CREATE_ALWAYS     if flags & O_CREAT and flags & O_TRUNC
            = OPEN_ALWAYS       if flags & O_CREAT
            = TRUNCATE_EXISTING if flags & O_TRUNC
            = OPEN_EXISTING     otherwise
h = CreateFileW(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, None,
                disposition, FILE_FLAG_OPEN_REPARSE_POINT | FILE_ATTRIBUTE_NORMAL, None)
if h == INVALID_HANDLE_VALUE: raise OSError(winerror=GetLastError())   # via ctypes.WinError()
GetFileInformationByHandle(h, byref(bhfi))
if bhfi.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
    CloseHandle(h); raise OSError(errno.ELOOP, "symlink at final component", path)
return msvcrt.open_osfhandle(h, (O_APPEND if flags & O_APPEND else 0) | (O_NOINHERIT if cloexec else 0))
```

- `FILE_FLAG_OPEN_REPARSE_POINT` opens the link itself, never its target; the attribute is read
  from the **handle we hold**, so there is no window. Refusal raises `OSError(ELOOP)` — the same
  errno POSIX `O_NOFOLLOW` produces — so every caller's `except OSError` and every error string
  that mentions the symlink case stays as it is. (Plan item: confirm each site's except clause.)
- A file with `FILE_ATTRIBUTE_REPARSE_POINT` that is not a symlink (a OneDrive placeholder, a
  dedup point) is also refused. Correct for a worktree write path; documented.
- `O_NONBLOCK` (the FIFO-hang defence at `tools.py:213`): Windows has no filesystem FIFOs a
  worktree path can name; keyword accepted, no-op, documented.
- `mode=0o600`: Windows honours only the read-only bit; the file inherits the directory ACL.
  Documented in `security.md` as the one real degradation of tier 1.
- Long paths: `CreateFileW` takes the path as given; `\\?\` prefixing is tier 2 (already on
  `security.md`'s untested list). Not addressed here.

### 3.3 `pid_alive` on Windows

```
h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION (=0x1000), False, pid)
if not h:
    err = GetLastError()
    return err == ERROR_ACCESS_DENIED (=5)      # exists, not ours — POSIX PermissionError → True
code = DWORD(); GetExitCodeProcess(h, byref(code)); CloseHandle(h)
return code.value == STILL_ACTIVE (=259)
```

`ERROR_INVALID_PARAMETER` (87) = no such process → `False`. The `isinstance`/`<= 0` guards stay
in front of both branches. No console event is ever generated.

### 3.4 The receipt must be able to say "incomplete"

`tools/junit_summary.py` gains one line under the table: `absent from this run: N test files
(<names>)`, computed from `tests/test_*.py` on disk minus files present in the XML, ignoring
files whose every test carries a deselected marker (`docker`, `live`). A truncated session then
prints thirteen names instead of nothing. Test: a fixture XML missing a file → the line names it.

The advisory step also adds `-p no:cacheprovider -rE` so collection errors, if any appear later,
land in the log; no change to `continue-on-error`.

## 4. Failure modes and limits

- **What degrades on Windows, stated once in `security.md`:** `0o600` create modes are
  advisory (ACL inheritance instead); a reparse point that is not a symlink is refused rather
  than written; the job-assignment window in §3.1; `O_NONBLOCK` is a no-op. Everything else in
  tier 1 keeps the POSIX guarantee.
- **Symlink creation on Windows needs a privilege** (Developer Mode or `SeCreateSymbolicLink`).
  Windows tests that need a symlink `skipif` when `os.symlink` raises `OSError` — skip with the
  reason, never pass vacuously.
- **`ctypes` signatures must be declared** (`argtypes`/`restype`) for every call — 64-bit handles
  truncated through default `int` conversion are the classic silent failure. The plan lists
  every prototype.
- **Windows 7 / Server 2008 R2** (no nested jobs): the `taskkill` fallback, visible in
  `run_end`. Not a target; not silently broken either.
- Nothing here touches the Docker sandbox path; `--sandbox docker` on native Windows remains
  tier 3 and untested. After tier 1 the README row becomes "unit suite green in CI; unsupported
  pending an integration suite" — not "supported".

## 5. Tests

Everything below runs on POSIX too, except the three marked Windows-only, which run in the
advisory leg — that leg is the point.

`tests/test_procs.py` (existing six stay byte-identical):
- `test_kill_tree_posix_uses_killpg` — monkeypatch `os.name`/`os.killpg`; assert called with the
  pgid and `SIGKILL`.
- `test_kill_tree_windows_terminates_job` — monkeypatch `os.name = "nt"` and a fake
  `osfs._win` namespace; assert `TerminateJobObject` then `CloseHandle`, in that order.
- *(Windows only)* `test_run_capped_kills_grandchild_on_timeout` — `python -c` that spawns a
  detached `python -c "time.sleep(60)"` and then sleeps; after `run_capped(timeout=1)` the
  grandchild pid is dead (`pid_alive` → `False` within 2 s).

`tests/test_osfs.py` (new):
- `test_flag_mapping` — table-driven over the nine sites' flag combinations →
  `(access, disposition, osf_flags)`; pure function, runs everywhere.
- `test_posix_composition_is_identical` — for each site's flags, `open_nofollow`'s POSIX call
  equals the literal `os.open` flags the site used before (captured from git history in the test
  as constants).
- `test_refuses_symlink` — POSIX: real symlink → `OSError` with `errno.ELOOP`. *(Windows only,
  skip without symlink privilege)*: same assertion through `CreateFileW`.
- `test_refuses_nonsymlink_reparse_point` — Windows only; skipped unless a junction can be made
  (`mklink /J` needs no privilege): junction at final component → `ELOOP`.

`tests/test_resume.py`:
- `test_pid_alive` stays. Add `test_pid_alive_windows_never_sends_console_events` —
  monkeypatch `os.name = "nt"` and assert `os.kill` is **not** called (a spy that raises).
- *(Windows only)* `test_pid_alive_exited_child` — spawn `python -c "pass"`, wait, assert `False`.

`tests/test_junit_summary.py`: the absent-files line (§3.4), present and absent cases.

## 6. Acceptance

1. Advisory `windows-latest` table on the PR: **every non-deselected test file present**; the
   absent-files line reads `absent from this run: 0`.
2. `tests/test_procs.py` 6+/6+ on Windows; the O_NOFOLLOW failure group is 0; `test_resume`
   runs to the end of the file.
3. Remaining Windows failures ≤ the long-tail count (~25) and each one named in the PR under
   tier 2 — no new categories.
4. POSIX: the full suite (1,375 + new) green on 3.9 / 3.12 / 3.13, macOS and Ubuntu; docker live
   suite green. `dirtywork --version`, `contract`, `init --stdout` unchanged.
5. No new runtime dependency; `pyproject.toml` untouched except the version.
6. `docs/security.md` Windows paragraph landed; README row updated as in §4; `CHANGELOG`/release
   notes name the three calls.
7. The first native-Windows `dirtywork run --sandbox none` receipt from a person, attached to
   #96 — desirable, not gating.

## 7. Files

- `dirtywork/procs.py` — `_spawn`, `_kill_tree`, Windows job branch (+ `taskkill` fallback).
- `dirtywork/osfs.py` — new: `open_nofollow`, `_win_open_params`, the ctypes prototypes.
- `dirtywork/tools.py`, `rundir.py`, `transcript.py`, `workspace.py`, `bench.py` — nine call
  sites → `open_nofollow`.
- `dirtywork/resume.py` — `pid_alive` Windows branch.
- `tools/junit_summary.py`, `.github/workflows/ci.yml` — absent-files line, `-rE`.
- `tests/test_procs.py`, `tests/test_osfs.py` (new), `tests/test_resume.py`,
  `tests/test_junit_summary.py`.
- `docs/security.md`, `README.md` (support row), release notes.

## 8. Open questions for the owner

1. **Job Object vs `taskkill`** — recommendation above is the Job Object with `taskkill` only as
   the no-nested-jobs fallback. Confirm.
2. **Where the Windows leg goes after tier 1** — keep advisory (recommended until tier 2 clears
   the long tail) or make it required for the unit suite only.
3. **Module name** — `dirtywork/osfs.py` for the open helper; or fold into `rundir.py`, which
   already owns the atomic-write path. Recommendation: separate module, it has two importers
   outside `rundir`.
4. **Build process** — the worker runs in the Linux image and cannot execute a Windows branch.
   The plan will carry every `ctypes` prototype verbatim in the brief, the POSIX-runnable tests
   above are the worker's verify, and the advisory leg on the PR is the real test. If the leg
   shows the Windows branch wrong, the fix round is a resume-with-feedback with the CI table
   pasted in — the same loop as any other run.
