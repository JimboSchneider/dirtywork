# Windows tier 1: stop crashing on three POSIX-only calls (#96) — design

v2, 2026-08-27. Status: **owner review folded (§0.1); ready for the owner's §8 answers; sequenced
behind #87 (0.13.0).** Target: 0.13.x.

## 0. The owner's decision

Jim, 2026-08-27: file the Windows work as a feature, write the spec now, build it after #87. Windows
stays "unsupported" in the README until a Windows integration suite passes (`docs/security.md`);
tier 1 is the difference between *crashes on an `AttributeError`* and *runs, degraded and
documented*. Tiers 2 (long tail) and 3 (Docker live on Windows) are separate decisions.

Interim guidance for Windows users — WSL2 — is already in the README (#97, #98) and has one
receipt.

### 0.1 Review fold (v2, 2026-08-27 19:55 — six P1s and one reference, each verified before folding)

1. **`CREATE_ALWAYS` cannot be combined with `FILE_FLAG_OPEN_REPARSE_POINT`** (CreateFileW docs,
   flag table). v1 mapped `O_CREAT|O_TRUNC` — `rundir.py:105` (run.json's atomic write) and the
   worker's `write_file` at `tools.py:104` — to exactly that. Worse, the docs also say that with the
   reparse flag `TRUNCATE_EXISTING` "affects the symbolic link" itself, so no truncating
   disposition is safe. v2 §3.2: create-new first, else open-existing + verify + truncate through
   the verified handle.
2. **Junction test could not produce `ELOOP`.** Opening a directory needs
   `FILE_FLAG_BACKUP_SEMANTICS` (docs, Remarks → Directories); without it a junction fails with
   `ERROR_ACCESS_DENIED` before any attribute is read. That is fail-closed, with a different errno.
   v2 §3.2 says so and §5's test asserts refusal with `EACCES`, reserving `ELOOP` for file symlinks.
3. **Fail-open on an unchecked return.** v1's pseudocode ignored `GetFileInformationByHandle`'s
   `BOOL`; a failed call leaves a zeroed struct that reads as "not a reparse point". v2 §3.2 checks
   every return, closes the handle on every error path including `open_osfhandle`, and switches to
   `GetFileInformationByHandleEx(FileAttributeTagInfo)`.
4. **Assignment race contradicted the guarantee.** v1 kept "children cannot outlive the call"
   while admitting a window between `Popen` and `AssignProcessToJobObject`.
   `PROC_THREAD_ATTRIBUTE_JOB_LIST` would close it atomically, but CPython's
   `subprocess.STARTUPINFO.lpAttributeList` supports only `handle_list` (verified in the installed
   `subprocess.py`), so it is unreachable without a raw `CreateProcessW`. v2 §3.1 uses
   `CREATE_SUSPENDED`: assign, then resume the child's single initial thread. The guarantee stands.
5. **`tree_kill` receipt had no propagation path** — `run_capped` returns `Captured`, tools flatten
   it to text, `Runner` builds `run_end` from its own state (`runner.py:776`), and the file list did
   not include runner/contract changes. v2 removes the degraded mode instead of plumbing a receipt
   for it: if the job cannot be created or assigned, the call fails loudly (§3.1). No new field,
   no contract change. The receipt path is kept in §8 as the alternative if Windows 7 ever matters.
6. **Deselection logic omitted `ollama`.** `pyproject.toml:51`: `-m 'not live and not docker and
   not ollama'`; `tests/test_live_ollama.py:34` is wholly `ollama`-marked. v2 §3.4 stops
   hard-coding markers: the CI step records `pytest --collect-only -q` (which honours `addopts`)
   and the summary compares the junit against that list.
7. Reference: `runs.py:688` is `cmd_export`, not `runs list`/`show`. Fixed in §1; `runs.py:893` is
   `_staleness` (the `clean`/`verdict` preflight) and was cited by role only — now by name.

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
`test_watchdog`, `test_workspace` (13 files; the other four absent files are `docker`/`live`/
`ollama` marked and deselected by design). The per-file table did not say so — it only lists
files it saw.

`pid_alive` is product code with product callers: `runs.py:688` (`cmd_export`), `runs.py:893`
(`_staleness`, the `clean`/`verdict` preflight), `resume.py:156,187` (resume preflights). On native
Windows, `dirtywork runs export` or `runs clean` against a `running` run would Ctrl-C the
operator's terminal.

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
4. The advisory CI leg's summary reports **absent test files** against what pytest collected, so
   a truncated session can never again read as "measured" (§3.4).
5. `docs/security.md`: a Windows paragraph stating exactly what degrades (§4).

Out (tier 2 / tier 3 / owner decisions): the ~25 long-tail failures; Docker mode on Windows;
making the Windows leg required; any README claim beyond "unit suite green in CI, still
unsupported"; Windows 7 / Server 2008 R2 (no nested jobs — see §3.1); pywin32 or any new
dependency (everything below is `ctypes` + `msvcrt`, stdlib).

## 3. Design

### 3.1 Process tree kill — a Job Object, assigned before the child runs

`taskkill /T` walks the parent→child tree *at kill time*; a grandchild whose parent already
exited is orphaned and missed — the exact leak `killpg` was chosen to prevent. A **Job Object**
makes every descendant inherit membership; killing the job kills all of them, no tree walk. To
make membership hold from the child's first instruction, the child is created suspended and
resumed only after assignment:

```
hJob = CreateJobObjectW(None, None)
info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE          # 0x2000
SetInformationJobObject(hJob, JobObjectExtendedLimitInformation (=9), byref(info), sizeof(info))

proc  = subprocess.Popen(argv, ..., creationflags=CREATE_SUSPENDED)                   # 0x4; no start_new_session
hProc = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE (=0x100 | 0x1), False, proc.pid)
if not hProc or not AssignProcessToJobObject(hJob, hProc):
    TerminateProcess(hProc or proc._handle, 1); close everything
    return Captured(returncode=None, output=b"process-tree containment unavailable: " + WinError, ...)

# resume the child's single initial thread (Popen closed its thread handle)
snap = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD (=0x4), 0)
for te in Thread32First/Thread32Next(snap):                                           # THREADENTRY32
    if te.th32OwnerProcessID == proc.pid:
        hThread = OpenThread(THREAD_SUSPEND_RESUME (=0x2), False, te.th32ThreadID)
        prev = ResumeThread(hThread)                                                  # expect 1
        CloseHandle(hThread); break
else: TerminateProcess(hProc, 1); close everything; return the same Captured error
CloseHandle(snap)

... drain / stdin / wait exactly as today ...

TerminateJobObject(hJob, 1)     # replaces _kill_group(proc.pid); unconditional, as today
CloseHandle(hJob)               # KILL_ON_JOB_CLOSE: belt to TerminateJobObject's braces
CloseHandle(hProc)
```

- POSIX branch: unchanged, byte-for-byte (`start_new_session=kill_group`, `_kill_group`).
- Structure: `_spawn(argv, ...) -> (proc, tree)` and `_kill_tree(proc, tree)`, each with two
  branches; `run_capped`'s body otherwise untouched. `kill_group=False` on Windows: no job, no
  suspension, `proc.kill()` — as POSIX today.
- **No degraded mode.** If the job cannot be created, assigned (nested jobs unsupported: Windows
  7 / Server 2008 R2 return `ERROR_ACCESS_DENIED`), or the thread cannot be resumed, the suspended
  child is terminated and the call returns the same shape as today's `except OSError` path —
  `returncode=None`, the Windows error in `output`. The worker sees it as a failed tool call;
  the operator sees it in the transcript. Nothing runs uncontained. GitHub's Windows runners are
  themselves inside a job; nested jobs are supported from Windows 8 / Server 2012, so CI
  exercises the real path.
- The suspended process is never left suspended: every early exit terminates it first.
- `signal.SIGKILL` is referenced only inside the POSIX branch; it is never evaluated on Windows.
- Every `ctypes` call declares `argtypes`/`restype` (`HANDLE`, `DWORD`, `BOOL`, `LPVOID`); the plan
  lists each prototype. `proc._handle` is not used except as the terminate target of last resort.

### 3.2 `open_nofollow(path, flags, mode=0o600, *, cloexec=True, nonblock=False) -> int`

New module `dirtywork/osfs.py` (one helper, two branches, no other logic). The nine sites call
it with the flags they pass today minus the three platform flags, which become keyword
arguments — so the POSIX composition is provably identical:

```
# POSIX
extra = os.O_NOFOLLOW | (os.O_CLOEXEC if cloexec else 0) | (os.O_NONBLOCK if nonblock else 0)
return os.open(path, flags | extra, mode)
```

Windows branch. Access and disposition from `flags`, then one **verify** step on the handle
before it becomes an fd. `FILE_FLAG_OPEN_REPARSE_POINT` is on every open; it opens the link
itself, never the target, and it may not be combined with `CREATE_ALWAYS` — so there is no
`CREATE_ALWAYS` and no `TRUNCATE_EXISTING` anywhere below:

| site flags | sequence |
|---|---|
| `O_CREAT\|O_EXCL[\|O_APPEND]` — `tools.py:256`, `transcript.py:37` | `CREATE_NEW`. Anything existing at the path, symlink included → `ERROR_FILE_EXISTS` (80) → `EEXIST`, as POSIX. |
| `O_CREAT\|O_TRUNC` — `rundir.py:105`, `tools.py:104` | (1) `CREATE_NEW`; on `ERROR_FILE_EXISTS` → (2) `OPEN_EXISTING`, **verify**, then truncate through the verified handle: `SetFilePointerEx(h, 0, NULL, FILE_BEGIN)` + `SetEndOfFile(h)`. A symlink swapped in between (1) and (2) is caught by verify. |
| `O_CREAT\|O_APPEND` — `bench.py:394`, `workspace.py:185` | `OPEN_ALWAYS`, **verify**. (`OPEN_ALWAYS` reports `ERROR_ALREADY_EXISTS` (183) on success — not a failure.) |
| no `O_CREAT` — `workspace.py:171` (read), `tools.py:216,856` (write probes) | `OPEN_EXISTING`, **verify**. |

```
access = FILE_APPEND_DATA if O_APPEND else GENERIC_WRITE if O_WRONLY else GENERIC_READ
h = CreateFileW(path, access, FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE, None,
                disposition, FILE_FLAG_OPEN_REPARSE_POINT | FILE_ATTRIBUTE_NORMAL, None)
if h == INVALID_HANDLE_VALUE: raise ctypes.WinError()          # ERROR_FILE_EXISTS → EEXIST, ERROR_ACCESS_DENIED → EACCES, ...

# verify — fail closed on every path
tag = FILE_ATTRIBUTE_TAG_INFO()
if not GetFileInformationByHandleEx(h, FileAttributeTagInfo (=9), byref(tag), sizeof(tag)):
    err = GetLastError(); CloseHandle(h); raise ctypes.WinError(err)
if tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT (0x400):
    CloseHandle(h); raise OSError(errno.ELOOP, "symlink at final component", path)
if tag.FileAttributes & FILE_ATTRIBUTE_DIRECTORY (0x10):
    CloseHandle(h); raise OSError(errno.EISDIR, ...)           # belt; cannot happen without BACKUP_SEMANTICS
if truncating: if not SetFilePointerEx(...) or not SetEndOfFile(h): err=...; CloseHandle(h); raise ctypes.WinError(err)
try:
    return msvcrt.open_osfhandle(h, (O_APPEND if O_APPEND else 0) | (O_NOINHERIT if cloexec else 0))
except OSError:
    CloseHandle(h); raise
```

- The attribute is read from the **handle we hold**; there is no check-then-open window. A file
  symlink is refused with `errno.ELOOP` — the errno POSIX `O_NOFOLLOW` produces and the one
  `tools.py:218` and `tools.py:860` branch on — so those messages are unchanged.
- **Directories, junctions and directory symlinks** are never opened: without
  `FILE_FLAG_BACKUP_SEMANTICS` (deliberately absent — these are file writes) `CreateFileW` fails
  with `ERROR_ACCESS_DENIED` → `EACCES`, before any attribute is read. Fail-closed, different
  errno from POSIX (`EISDIR` / `ELOOP`); it lands in the callers' generic `OSError` branches.
  Documented; §5 tests assert exactly this.
- A reparse point that is not a symlink (a OneDrive placeholder, a dedup point) is refused too.
  Correct for a worktree write path; documented.
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
    return GetLastError() == ERROR_ACCESS_DENIED (=5)      # exists, not ours — POSIX PermissionError → True
code = DWORD()
ok = GetExitCodeProcess(h, byref(code)); CloseHandle(h)
return bool(ok) and code.value == STILL_ACTIVE (=259)
```

`ERROR_INVALID_PARAMETER` (87) = no such process → `False`. The `isinstance`/`<= 0` guards stay
in front of both branches. No console event is ever generated. (A live process whose exit code
happens to be 259 is the documented ambiguity of `STILL_ACTIVE`; accepted, as everyone accepts it.)

### 3.4 The receipt must be able to say "incomplete"

The advisory step records what pytest itself intends to run, then compares:

```
- run: python -m pytest --collect-only -q > collected.txt       # honours addopts' -m deselection
- run: python -m pytest --junitxml=junit-windows.xml -rE
  continue-on-error: true
- run: python tools/junit_summary.py junit-windows.xml --collected collected.txt
```

`junit_summary.py --collected FILE` prints one line under the table: `absent from this run: N
test files (<names>)` — files named in `collected.txt` (`path::test` lines reduced to their file)
with no testcase in the XML. No marker list is hard-coded anywhere; `addopts` remains the single
source of what is deselected. A truncated session prints thirteen names instead of nothing.
Without `--collected` the tool behaves exactly as today.

## 4. Failure modes and limits

- **What degrades on Windows, stated once in `security.md`:** `0o600` create modes are
  advisory (ACL inheritance instead); a reparse point that is not a symlink is refused rather
  than written; directories/junctions are refused with `EACCES` rather than `EISDIR`/`ELOOP`;
  `O_NONBLOCK` is a no-op. Everything else in tier 1 keeps the POSIX guarantee, including
  process-tree containment (§3.1) — there is no window and no degraded mode.
- **Symlink creation on Windows needs a privilege** (Developer Mode or `SeCreateSymbolicLink`).
  Windows tests that need a symlink `skipif` when `os.symlink` raises `OSError` — skip with the
  reason, never pass vacuously. Junctions (`mklink /J`, `_winapi.CreateJunction`) need no
  privilege and are used where a reparse point of any kind will do.
- **`ctypes` signatures must be declared** (`argtypes`/`restype`) for every call — 64-bit handles
  truncated through default `int` conversion are the classic silent failure. The plan lists
  every prototype.
- **Windows 7 / Server 2008 R2** (no nested jobs): `run_capped` fails loudly (§3.1). Not a target.
- Nothing here touches the Docker sandbox path; `--sandbox docker` on native Windows remains
  tier 3 and untested. After tier 1 the README row becomes "unit suite green in CI; unsupported
  pending an integration suite" — not "supported".

## 5. Tests

Everything below runs on POSIX too, except the ones marked Windows-only, which run in the
advisory leg — that leg is the point.

`tests/test_procs.py` (existing six stay byte-identical):
- `test_kill_tree_posix_uses_killpg` — monkeypatch `os.name`/`os.killpg`; assert called with the
  pgid and `SIGKILL`.
- `test_kill_tree_windows_terminates_job` — monkeypatch `os.name = "nt"` and a fake `_win`
  namespace; assert `TerminateJobObject` then `CloseHandle(job)`, in that order.
- `test_spawn_windows_assign_failure_terminates_child` — fake `AssignProcessToJobObject`
  returning 0; assert `TerminateProcess` called and `Captured.returncode is None` with the error
  text. Runs everywhere.
- *(Windows only)* `test_run_capped_resumes_suspended_child` — `python -c "print('hi')"` returns
  `hi` within the timeout (a stuck-suspended child would time out instead).
- *(Windows only)* `test_run_capped_kills_grandchild_on_timeout` — `python -c` that spawns a
  detached `python -c "time.sleep(60)"` then sleeps; after `run_capped(timeout=1)` the grandchild
  pid is dead (`pid_alive` → `False` within 2 s).

`tests/test_osfs.py` (new):
- `test_flag_mapping` — table-driven over the nine sites' flag combinations →
  `(access, disposition sequence, osf_flags)`; pure function, runs everywhere.
- `test_posix_composition_is_identical` — for each site's flags, `open_nofollow`'s POSIX call
  equals the literal `os.open` flags the site used before (captured as constants in the test).
- `test_verify_fails_closed` — fake `_win` namespace where `GetFileInformationByHandleEx`
  returns 0: `OSError` raised **and** `CloseHandle` called. Same for `open_osfhandle` raising.
  Runs everywhere.
- `test_refuses_file_symlink` — POSIX: `errno.ELOOP`. *(Windows only, skip without symlink
  privilege)*: same assertion through `CreateFileW`.
- *(Windows only)* `test_refuses_junction_with_eacces` — `_winapi.CreateJunction` at the final
  component → `OSError` with `errno.EACCES`; the handle count does not grow.
- *(Windows only)* `test_create_trunc_truncates_existing_through_verified_handle` — existing
  file with content → after `open_nofollow(O_WRONLY|O_CREAT|O_TRUNC)` and close, size is 0; and
  with a file symlink at the path instead → `ELOOP`, target untouched.

`tests/test_resume.py`:
- `test_pid_alive` stays. Add `test_pid_alive_windows_never_sends_console_events` —
  monkeypatch `os.name = "nt"` and a spy `os.kill` that raises; assert it is not called.
- *(Windows only)* `test_pid_alive_exited_child` — spawn `python -c "pass"`, wait, assert `False`.

`tests/test_junit_summary.py`: `--collected` present and absent cases; a collected file with no
testcases is named; a file absent from both lists is not.

## 6. Acceptance

1. Advisory `windows-latest` table on the PR: `absent from this run: 0` — every file pytest
   collected has results.
2. `tests/test_procs.py` all green on Windows; the O_NOFOLLOW failure group is 0; `test_resume`
   runs to the end of the file.
3. Remaining Windows failures ≤ the long-tail count (~25) and each one named in the PR under
   tier 2 — no new categories.
4. POSIX: the full suite (1,375 + new) green on 3.9 / 3.12 / 3.13, macOS and Ubuntu; docker live
   suite green. `dirtywork --version`, `contract`, `init --stdout` unchanged; the machine
   contract is unchanged (no new fields, no new flags).
5. No new runtime dependency; `pyproject.toml` untouched except the version.
6. `docs/security.md` Windows paragraph landed; README row updated as in §4; release notes name
   the three calls.
7. The first native-Windows `dirtywork run --sandbox none` receipt from a person, attached to
   #96 — desirable, not gating.

## 7. Files

- `dirtywork/procs.py` — `_spawn`, `_kill_tree`, Windows job branch (suspended create, resume).
- `dirtywork/osfs.py` — new: `open_nofollow`, `_win_open_params`, the ctypes prototypes.
- `dirtywork/tools.py`, `rundir.py`, `transcript.py`, `workspace.py`, `bench.py` — nine call
  sites → `open_nofollow`.
- `dirtywork/resume.py` — `pid_alive` Windows branch.
- `tools/junit_summary.py` (`--collected`), `.github/workflows/ci.yml` (collect step, `-rE`,
  summary args).
- `tests/test_procs.py`, `tests/test_osfs.py` (new), `tests/test_resume.py`,
  `tests/test_junit_summary.py`.
- `docs/security.md`, `README.md` (support row), release notes.

Not touched, on purpose: `runner.py`, `transcript.py`'s event schema, `contract/machine-contract.md`.

## 8. Open questions for the owner

1. **Suspended create (recommended) vs downgraded guarantee.** §3.1 closes the race with
   `CREATE_SUSPENDED` + Toolhelp resume (~40 lines of ctypes). The alternative is to accept the
   window and rewrite `run_capped`'s docstring and `security.md` to say so. Recommendation: close it.
2. **No Windows 7 fallback.** §3.1 fails loudly where nested jobs are unsupported. If Windows 7 /
   Server 2008 R2 ever matters, the alternative is `taskkill /T` plus a `tree_kill` field on
   `run_end` — which is a `runner.py` + contract + `test_transcript_schema` change (the schema
   test asserts every `run_end` field is documented). Recommendation: no fallback.
3. **Where the Windows leg goes after tier 1** — keep advisory (recommended until tier 2 clears
   the long tail) or make it required for the unit suite only.
4. **Module name** — `dirtywork/osfs.py`; or fold into `rundir.py`, which already owns the
   atomic-write path. Recommendation: separate module, it has four importers outside `rundir`.
5. **Build process** — the worker runs in the Linux image and cannot execute a Windows branch.
   The plan will carry every `ctypes` prototype verbatim in the brief, the POSIX-runnable tests
   above are the worker's verify, and the advisory leg on the PR is the real test. If the leg
   shows the Windows branch wrong, the fix round is a resume-with-feedback with the CI table
   pasted in — the same loop as any other run.
