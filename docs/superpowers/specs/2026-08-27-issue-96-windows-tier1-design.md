# Windows tier 1: stop crashing on three POSIX-only calls (#96) — design

v3, 2026-08-27. Status: **§8 decisions approved by the owner (§0.2); second-review edits folded
(§0.2); implementation-ready pending the owner's read of v3. Sequenced behind #87 (0.13.0).**
Target: 0.13.x.

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

### 0.2 Second review fold (v3, 2026-08-27 21:43 — five decisions approved, four edits required)

Approved as recommended: (1) `CREATE_SUSPENDED` → assign → resume; (2) no Windows 7 fallback, no
new receipt field; (3) the Windows leg stays advisory until tier 2; (4) `dirtywork/osfs.py`;
(5) POSIX behaviour built and verified by the Linux worker, Win32 execution by the advisory leg,
with every `ctypes` prototype and structure layout in the brief.

Edits, each verified before folding:

1. **§3.1 checked no returns.** v2 claimed every Win32 return is checked and then checked none of
   `CreateJobObjectW`, `SetInformationJobObject`, `CreateToolhelp32Snapshot`, `Thread32First/Next`,
   `OpenThread`, `ResumeThread`, `TerminateProcess`. v3 §3.1 is the full sequence: `dwSize`
   initialised before `Thread32First` (Toolhelp docs: "if you do not initialize dwSize,
   Thread32First fails"), `ResumeThread` must return exactly `1` (docs: previous suspend count;
   `(DWORD)-1` on failure; `1` = "was suspended, now restarted"), `GetLastError()` captured
   before any cleanup call.
2. **Prototype list was promised, not present.** Appendix A: every function with
   `argtypes`/`restype`, every structure with its member order, every constant with its source,
   `WinDLL(..., use_last_error=True)` + `ctypes.get_last_error()`. `subprocess` does **not** expose
   `CREATE_SUSPENDED` (checked the installed `subprocess.py`; it exposes
   `CREATE_BREAKAWAY_FROM_JOB` but not this one) — defined locally from the Process Creation Flags
   table, `0x00000004`.
3. **"Nine sites" was wrong.** A recursive grep finds eleven `os.O_NOFOLLOW` opens: the nine in v2
   plus `sandbox/export.py:255` (`O_CREAT|O_EXCL`) and `:527` (`O_CREAT|O_TRUNC`) — the Docker
   export path, tier 3. v3 §3.2 lists all eleven, converts the two for DRY (the helper exists; a
   raw `os.O_NOFOLLOW` reference is a guaranteed `AttributeError`), and states that their Windows
   behaviour is not exercised until tier 3.
4. **`cloexec=True` broke "byte-identical".** Only the four `tools.py` sites pass `O_CLOEXEC`
   (and three of those `O_NONBLOCK`); `rundir.py:105`, `workspace.py:171,185`, `bench.py:394`,
   `transcript.py:37`, both `export.py` sites pass neither. v3: both keywords default to `False`
   and §3.2's table gives each site's exact arguments; `test_posix_composition_is_identical`
   checks the table against the pre-change flags.

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
| `os.O_NOFOLLOW` (with `O_NONBLOCK`, `O_CLOEXEC` at the `tools.py` sites) | eleven: `tools.py:104,216,256,856`; `rundir.py:105`; `transcript.py:37`; `workspace.py:171,185`; `bench.py:394`; `sandbox/export.py:255,527` (Docker export — tier 3 path) | final-component symlink TOCTOU defence (SP1 §2.1) — a security control, not a convenience |
| `os.kill(pid, 0)` | `resume.py:133` | "is this pid alive" without side effects |

Precedent for platform branches already in the tree: `budget.py:167` (`os.name == "nt"`),
`runs.py:204` (uid fallback), `rundir.py:31` (`hasattr(os, "getuid")`).

## 2. Scope

In:

1. `procs.py`: a Windows process-tree kill with the same guarantee (§3.1).
2. One `open_nofollow()` helper; all eleven sites call it with their existing flags; POSIX
   behaviour byte-identical (§3.2). The two `sandbox/export.py` sites are converted for DRY only —
   Docker export on Windows is tier 3 and is not exercised by tier 1.
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
resumed only after assignment. Every call below is checked; every failure captures
`get_last_error()` **first**, then terminates the suspended child, then closes handles, then
returns the error. Prototypes and constants are in Appendix A.

```
def _spawn_windows(argv, *, cwd, env, stdin) -> tuple[Popen, _Tree] | Captured:
    hJob = K.CreateJobObjectW(None, None)
    if not hJob: return _fail("CreateJobObjectW")                       # err = get_last_error()

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not K.SetInformationJobObject(hJob, JobObjectExtendedLimitInformation,
                                     byref(info), sizeof(info)):
        return _fail("SetInformationJobObject", close=[hJob])

    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=..., stdout=PIPE, stderr=STDOUT,
                                creationflags=CREATE_SUSPENDED)
    except OSError as e:
        K.CloseHandle(hJob); return Captured(returncode=None, output=str(e).encode(), ...)   # today's path

    hProc = K.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, proc.pid)
    if not hProc: return _fail("OpenProcess", kill=proc, close=[hJob])
    if not K.AssignProcessToJobObject(hJob, hProc):
        return _fail("AssignProcessToJobObject", kill=proc, close=[hProc, hJob])   # Win7: ERROR_ACCESS_DENIED

    # resume the single initial thread; Popen closed its thread handle at creation
    snap = K.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap == INVALID_HANDLE_VALUE: return _fail("CreateToolhelp32Snapshot", kill=proc, close=[hProc, hJob])
    te = THREADENTRY32(); te.dwSize = sizeof(THREADENTRY32)            # required, or Thread32First fails
    tid = None
    ok = K.Thread32First(snap, byref(te))
    while ok:
        if te.th32OwnerProcessID == proc.pid: tid = te.th32ThreadID; break
        ok = K.Thread32Next(snap, byref(te))                             # FALSE + ERROR_NO_MORE_FILES at end
    K.CloseHandle(snap)
    if tid is None: return _fail("thread not found in snapshot", kill=proc, close=[hProc, hJob])

    hThread = K.OpenThread(THREAD_SUSPEND_RESUME, False, tid)
    if not hThread: return _fail("OpenThread", kill=proc, close=[hProc, hJob])
    prev = K.ResumeThread(hThread)                                       # previous suspend count; (DWORD)-1 on failure
    err = get_last_error(); K.CloseHandle(hThread)
    if prev != 1: return _fail(f"ResumeThread returned {prev}", err=err, kill=proc, close=[hProc, hJob])
    return proc, _Tree(hJob, hProc)

def _fail(what, *, err=None, kill=None, close=()):
    err = get_last_error() if err is None else err                       # BEFORE any cleanup call
    if kill is not None:
        try: kill.kill()                                                  # TerminateProcess; ignore result — best effort
        except OSError: pass
        try: kill.wait(timeout=5)
        except subprocess.TimeoutExpired: pass
    for h in close: K.CloseHandle(h)                                      # ignore result; nothing to do about it
    return Captured(returncode=None, truncated=False, timed_out=False,
                    output=f"process-tree containment unavailable: {what}: {FormatError(err)}".encode())

def _kill_tree_windows(tree):
    K.TerminateJobObject(tree.job, 1)      # replaces _kill_group(proc.pid); unconditional, as today; result ignored
    K.CloseHandle(tree.job)                # KILL_ON_JOB_CLOSE: belt to TerminateJobObject's braces
    K.CloseHandle(tree.proc)
```

- POSIX branch: unchanged, byte-for-byte (`start_new_session=kill_group`, `_kill_group`).
- Structure: `_spawn(argv, ...) -> (proc, tree) | Captured` and `_kill_tree(proc, tree)`, each with
  two branches; `run_capped`'s body otherwise untouched. A `Captured` from `_spawn` is returned
  as-is — the same shape as today's `except OSError` path. `kill_group=False` on Windows: no job,
  no suspension, `proc.kill()` — as POSIX today.
- **No degraded mode.** Any failure above terminates the suspended child and fails the call with
  the Win32 error text in `output`. The worker sees a failed tool call; the operator sees it in
  the transcript. Nothing runs uncontained. Windows 7 / Server 2008 R2 (no nested jobs) fail at
  `AssignProcessToJobObject` with `ERROR_ACCESS_DENIED`; GitHub's Windows runners are inside a job
  and nested jobs are supported from Windows 8 / Server 2012, so CI exercises the real path.
- The suspended process is never left suspended: every early exit terminates it first
  (`Popen.kill()` is `TerminateProcess` on Windows). `ResumeThread` must return exactly `1`: `0`
  means the thread was not suspended (not ours to have created), `>1` means it is still suspended,
  `0xFFFFFFFF` is failure.
- `signal.SIGKILL` is referenced only inside the POSIX branch; it is never evaluated on Windows.
- `proc._handle` is not used. `hProc` from `OpenProcess` is the terminate/assign target.

### 3.2 `open_nofollow(path, flags, mode=0o600, *, cloexec=False, nonblock=False) -> int`

New module `dirtywork/osfs.py` (one helper, two branches, no other logic). Each of the eleven
sites calls it with the flags it passes today minus the three platform flags, which become
keyword arguments **per site** — so the POSIX composition is provably identical:

| site | `flags` | `cloexec` | `nonblock` | tier |
|---|---|---|---|---|
| `tools.py:104` (`write_file`, caller-supplied `flags` = `O_WRONLY\|O_CREAT\|O_TRUNC`) | as passed | True | True | 1 |
| `tools.py:216` (write probe) | `O_WRONLY` | True | True | 1 |
| `tools.py:256` (create-exclusive) | `O_WRONLY\|O_CREAT\|O_EXCL` | True | False | 1 |
| `tools.py:856` (write probe) | `O_WRONLY` | True | True | 1 |
| `rundir.py:105` (run.json temp) | `O_WRONLY\|O_CREAT\|O_TRUNC` | False | False | 1 |
| `transcript.py:37` | `O_WRONLY\|O_CREAT\|O_EXCL\|O_APPEND` | False | False | 1 |
| `workspace.py:171` (read) | `O_RDONLY` | False | False | 1 |
| `workspace.py:185` | `O_WRONLY\|O_CREAT\|O_APPEND` | False | False | 1 |
| `bench.py:394` | `O_WRONLY\|O_CREAT\|O_APPEND` | False | False | 1 |
| `sandbox/export.py:255` | `O_WRONLY\|O_CREAT\|O_EXCL` | False | False | 3 — converted for DRY; not exercised on Windows until tier 3 |
| `sandbox/export.py:527` | `O_WRONLY\|O_CREAT\|O_TRUNC` | False | False | 3 — same |

(`mode` is whatever each site passes today; `0o600` is the helper's default because eight of the
eleven use it.)

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
| `O_CREAT\|O_EXCL[\|O_APPEND]` — `tools.py:256`, `transcript.py:37`, `export.py:255` | `CREATE_NEW`. Anything existing at the path, symlink included → `ERROR_FILE_EXISTS` (80) → `EEXIST`, as POSIX. |
| `O_CREAT\|O_TRUNC` — `rundir.py:105`, `tools.py:104`, `export.py:527` | (1) `CREATE_NEW`; on `ERROR_FILE_EXISTS` → (2) `OPEN_EXISTING`, **verify**, then truncate through the verified handle: `SetFilePointerEx(h, 0, NULL, FILE_BEGIN)` + `SetEndOfFile(h)`. A symlink swapped in between (1) and (2) is caught by verify. |
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
    return msvcrt.open_osfhandle(h, (os.O_APPEND if flags & os.O_APPEND else 0)
                                    | (os.O_NOINHERIT if cloexec else 0))
except OSError:
    CloseHandle(h); raise
```

Every `if not ...:` above reads `get_last_error()` **before** `CloseHandle`, exactly as §3.1.
`CreateFileW` failure is reported through `ctypes.WinError(get_last_error())`, which maps
`ERROR_FILE_EXISTS` → `EEXIST`, `ERROR_ACCESS_DENIED` → `EACCES`, `ERROR_FILE_NOT_FOUND` →
`ENOENT` — the errnos the callers already branch on.

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
- `test_posix_composition_is_identical` — for each of the eleven rows of §3.2's table, the
  `os.open` flags `open_nofollow` composes on POSIX equal the literal flags the site used before
  (captured as constants in the test, including which sites had `O_CLOEXEC`/`O_NONBLOCK`).
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
- `dirtywork/tools.py`, `rundir.py`, `transcript.py`, `workspace.py`, `bench.py`,
  `sandbox/export.py` — eleven call sites → `open_nofollow`.
- `dirtywork/resume.py` — `pid_alive` Windows branch.
- `tools/junit_summary.py` (`--collected`), `.github/workflows/ci.yml` (collect step, `-rE`,
  summary args).
- `tests/test_procs.py`, `tests/test_osfs.py` (new), `tests/test_resume.py`,
  `tests/test_junit_summary.py`.
- `docs/security.md`, `README.md` (support row), release notes.

Not touched, on purpose: `runner.py`, `transcript.py`'s event schema, `contract/machine-contract.md`.

## 8. Owner decisions (taken 2026-08-27 21:43)

1. **Suspended create** — `CREATE_SUSPENDED` → assign → resume; the containment guarantee stands.
2. **No Windows 7 fallback** — fail loudly; no new receipt or schema field.
3. **Windows leg stays advisory** until the tier-2 long tail is cleared.
4. **`dirtywork/osfs.py`** as a separate helper module.
5. **Build process** — POSIX behaviour built and verified in the Linux worker; the advisory leg is
   the Win32 execution test; the brief carries Appendix A verbatim. A wrong Windows branch is a
   resume-with-feedback with the CI table pasted in.

## Appendix A — `ctypes` prototypes, structures, constants

Everything binds through `K = ctypes.WinDLL("kernel32", use_last_error=True)`; errors are read
with `ctypes.get_last_error()` and rendered with `ctypes.FormatError(err)` / `ctypes.WinError(err)`.
Every function below gets `argtypes` and `restype` set exactly as listed — a `HANDLE` left to the
default `int` conversion is truncated on 64-bit and fails silently. Types are `ctypes.wintypes`:
`HANDLE, DWORD, BOOL, LPVOID, LPCWSTR, LARGE_INTEGER, ULONG` plus `ctypes.c_size_t` (`SIZE_T`),
`ctypes.c_ulonglong` (`ULONGLONG`), `ctypes.c_long` (`LONG`).

Sources: Microsoft Learn pages for each function/structure, read 2026-08-27. Items marked
**(confirm)** were not re-read today and the brief-writer confirms them against the linked page
before the brief is issued.

### Functions

| function | argtypes | restype | failure |
|---|---|---|---|
| `CreateJobObjectW` | `LPVOID, LPCWSTR` | `HANDLE` | `NULL` |
| `SetInformationJobObject` | `HANDLE, c_int, LPVOID, DWORD` | `BOOL` | `0` |
| `AssignProcessToJobObject` | `HANDLE, HANDLE` | `BOOL` | `0` (`ERROR_ACCESS_DENIED` when nested jobs unsupported) |
| `TerminateJobObject` | `HANDLE, c_uint` | `BOOL` | `0` |
| `OpenProcess` | `DWORD, BOOL, DWORD` | `HANDLE` | `NULL` |
| `TerminateProcess` | `HANDLE, c_uint` | `BOOL` | `0` |
| `GetExitCodeProcess` | `HANDLE, POINTER(DWORD)` | `BOOL` | `0` |
| `CreateToolhelp32Snapshot` | `DWORD, DWORD` | `HANDLE` | `INVALID_HANDLE_VALUE` |
| `Thread32First` / `Thread32Next` | `HANDLE, POINTER(THREADENTRY32)` | `BOOL` | `0`; end of list = `ERROR_NO_MORE_FILES` |
| `OpenThread` | `DWORD, BOOL, DWORD` | `HANDLE` | `NULL` |
| `ResumeThread` | `HANDLE` | `DWORD` | `0xFFFFFFFF`; success = previous suspend count, must be `1` |
| `CloseHandle` | `HANDLE` | `BOOL` | `0` (ignored on cleanup paths) |
| `CreateFileW` | `LPCWSTR, DWORD, DWORD, LPVOID, DWORD, DWORD, HANDLE` | `HANDLE` | `INVALID_HANDLE_VALUE` |
| `GetFileInformationByHandleEx` | `HANDLE, c_int, LPVOID, DWORD` | `BOOL` | `0` |
| `SetFilePointerEx` | `HANDLE, LARGE_INTEGER, POINTER(LARGE_INTEGER), DWORD` | `BOOL` | `0` |
| `SetEndOfFile` | `HANDLE` | `BOOL` | `0` |

`msvcrt.open_osfhandle(handle, flags)` is stdlib Python (`msvcrt` module), raises `OSError`.

### Structures (member order is the ABI; do not reorder)

```
class IO_COUNTERS(Structure):                       # winnt.h (confirm)
    _fields_ = [("ReadOperationCount", c_ulonglong), ("WriteOperationCount", c_ulonglong),
                ("OtherOperationCount", c_ulonglong), ("ReadTransferCount", c_ulonglong),
                ("WriteTransferCount", c_ulonglong), ("OtherTransferCount", c_ulonglong)]

class JOBOBJECT_BASIC_LIMIT_INFORMATION(Structure):  # winnt.h (confirm member order)
    _fields_ = [("PerProcessUserTimeLimit", LARGE_INTEGER), ("PerJobUserTimeLimit", LARGE_INTEGER),
                ("LimitFlags", DWORD), ("MinimumWorkingSetSize", c_size_t),
                ("MaximumWorkingSetSize", c_size_t), ("ActiveProcessLimit", DWORD),
                ("Affinity", c_size_t),              # ULONG_PTR
                ("PriorityClass", DWORD), ("SchedulingClass", DWORD)]

class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(Structure):   # winnt.h — read today
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", c_size_t), ("JobMemoryLimit", c_size_t),
                ("PeakProcessMemoryUsed", c_size_t), ("PeakJobMemoryUsed", c_size_t)]

class THREADENTRY32(Structure):                     # tlhelp32.h — read today
    _fields_ = [("dwSize", DWORD), ("cntUsage", DWORD), ("th32ThreadID", DWORD),
                ("th32OwnerProcessID", DWORD), ("tpBasePri", c_long), ("tpDeltaPri", c_long),
                ("dwFlags", DWORD)]

class FILE_ATTRIBUTE_TAG_INFO(Structure):           # winbase.h — read today
    _fields_ = [("FileAttributes", DWORD), ("ReparseTag", DWORD)]
```

`sizeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION)` must be 144 on 64-bit (112 for the basic struct
with padding + 48 for `IO_COUNTERS` — **confirm** by asserting in a test on the Windows leg; a
wrong `_fields_` makes `SetInformationJobObject` fail with `ERROR_INVALID_PARAMETER`, which is at
least loud).

### Constants

| name | value | source |
|---|---|---|
| `CREATE_SUSPENDED` | `0x00000004` | Process Creation Flags — read today; **not** exposed by `subprocess` |
| `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` | `0x00002000` | `JOBOBJECT_BASIC_LIMIT_INFORMATION` — read today |
| `JobObjectExtendedLimitInformation` | `9` | `SetInformationJobObject` class table — read today |
| `PROCESS_SET_QUOTA` / `PROCESS_TERMINATE` / `PROCESS_QUERY_LIMITED_INFORMATION` | `0x0100` / `0x0001` / `0x1000` | Process Security and Access Rights (confirm) |
| `TH32CS_SNAPTHREAD` | `0x00000004` | `CreateToolhelp32Snapshot` (confirm) |
| `THREAD_SUSPEND_RESUME` | `0x0002` | Thread Security and Access Rights (confirm) |
| `STILL_ACTIVE` | `259` | `GetExitCodeProcess` (confirm) |
| `INVALID_HANDLE_VALUE` | `HANDLE(-1).value` (all bits set) | handleapi.h |
| `ERROR_FILE_NOT_FOUND` / `ERROR_ACCESS_DENIED` / `ERROR_NO_MORE_FILES` / `ERROR_FILE_EXISTS` / `ERROR_INVALID_PARAMETER` / `ERROR_ALREADY_EXISTS` | `2` / `5` / `18` / `80` / `87` / `183` | System Error Codes (confirm) |
| `GENERIC_READ` / `GENERIC_WRITE` / `FILE_APPEND_DATA` | `0x80000000` / `0x40000000` / `0x0004` | Generic Access Rights; File Access Rights (confirm) |
| `FILE_SHARE_READ` / `_WRITE` / `_DELETE` | `1` / `2` / `4` | `CreateFileW` (confirm) |
| `CREATE_NEW` / `OPEN_EXISTING` / `OPEN_ALWAYS` | `1` / `3` / `4` | `CreateFileW` (confirm) — no `CREATE_ALWAYS` (2), no `TRUNCATE_EXISTING` (5), by design |
| `FILE_ATTRIBUTE_NORMAL` / `FILE_ATTRIBUTE_DIRECTORY` / `FILE_ATTRIBUTE_REPARSE_POINT` | `0x80` / `0x10` / `0x400` | File Attribute Constants (confirm) |
| `FILE_FLAG_OPEN_REPARSE_POINT` | `0x00200000` | `CreateFileW` — read today |
| `FileAttributeTagInfo` | `9` | `FILE_INFO_BY_HANDLE_CLASS` (confirm) |
| `FILE_BEGIN` | `0` | `SetFilePointerEx` (confirm) |
| `os.O_NOINHERIT` | from `os` on Windows | CPython |
