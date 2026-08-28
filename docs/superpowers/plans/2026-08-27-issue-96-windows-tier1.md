# #96 Windows tier 1 — stop crashing on three POSIX-only calls: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **This repository's execution rule overrides the line above** (repo `CLAUDE.md`, "Dogfood rule").
> Every code task below (`W…`) is built by the **released dirtywork running against this repository**
> with a local worker (`qwen/qwen3-coder-next` via LM Studio) — Claude writes the brief, reviews the
> branch, runs the host suite, feeds back through `dirtywork resume --feedback-file`, and writes the
> prose (`C…` tasks). A Claude implementer touches code only after a worker resume-with-feedback has
> failed, and the PR says so. Owner approval is needed for the merge and the release, never assumed.
> **Sequenced behind #87 (0.13.0): nothing here runs until #87 has merged and the owner says go.**

**Plan v1** (2026-08-27 22:30 CDT) against spec v3 (`4e27b8c`,
`docs/superpowers/specs/2026-08-27-issue-96-windows-tier1-design.md`). Six tasks: C0, W1, W2, W3,
W4, W5, C1 — sized to the #82 calibration (~150–250 changed lines per 60-turn run, 1–3 resumes).
The worker cannot execute a Windows branch; the POSIX-runnable tests in each brief are its verify,
and the advisory `windows-latest` leg on the PR is the real test (spec §8.5).

**Goal:** dirtywork on native Windows stops dying on `os.killpg`, `os.O_NOFOLLOW` and
`os.kill(pid, 0)`; the advisory Windows CI leg runs to the end of the suite and can say
"incomplete" when it does not; `docs/security.md` states exactly what degrades. Windows stays
"unsupported" in the README (tier 3 is a separate decision).

**Architecture:** One new stdlib-only module, `dirtywork/osfs.py`, owns every `ctypes` binding
(`win32()` — kernel32 prototypes, structures, constants from spec Appendix A) and the
`open_nofollow()` helper (POSIX: `os.open` with `O_NOFOLLOW`; Windows: `CreateFileW` +
`FILE_FLAG_OPEN_REPARSE_POINT`, verify on the handle, `ELOOP` on a reparse point, fail closed).
The eleven `os.O_NOFOLLOW` sites call it with their exact prior flags. `procs.py` splits
`run_capped` into `_spawn`/`_kill_tree` with a Windows branch that creates the child suspended,
assigns it to a Job Object with `KILL_ON_JOB_CLOSE`, resumes its one thread, and terminates the
job unconditionally — failing loudly, never degrading. `resume.py`'s `pid_alive` gets an
`OpenProcess` branch. `tools/junit_summary.py --collected` compares the junit against
`pytest --collect-only`, so a truncated session is named in the table.

**Tech Stack:** Python 3.9+ stdlib only (`ctypes`, `ctypes.wintypes`, `msvcrt`, `subprocess`,
`os`, `errno`); pytest with `monkeypatch`/`tmp_path` as in `tests/test_procs.py`; GitHub Actions
(`ci.yml`, SHA-pinned actions already in the file).

**Spec:** `docs/superpowers/specs/2026-08-27-issue-96-windows-tier1-design.md` v3 (`4e27b8c`) —
§3.1 process tree, §3.2 `open_nofollow`, §3.3 `pid_alive`, §3.4 the receipt, §4 limits, §5 tests,
§6 acceptance, §8 the five owner decisions, Appendix A prototypes (every value confirmed against
Microsoft Learn on 2026-08-27 while writing this plan — the "(confirm)" marks are discharged; one
change: truncation is `SetFileInformationByHandle(FileEndOfFileInfo)` — a single call — instead of
`SetFilePointerEx` + `SetEndOfFile`).

## Global Constraints

- Python floor 3.9; stdlib only — no new dependency (spec §2, §8.2). `pyproject.toml` untouched
  except the version (§6.5).
- **POSIX behaviour byte-identical.** Every `open_nofollow` call composes exactly the `os.open`
  flags its site passed before (spec §3.2 table; W2's `test_posix_composition_is_identical`).
  `procs.py`'s POSIX branch keeps `start_new_session=kill_group` and `_kill_group` unchanged.
  `pid_alive`'s POSIX branch is unchanged.
- **No degraded mode on Windows** (§8.2): a failed job create/assign/resume terminates the
  suspended child and returns `Captured(returncode=None, output=b"process-tree containment
  unavailable: …")`. No new `run_end` field, no contract change, `runner.py` untouched (§7).
- **Fail closed** (§3.2): every Win32 `BOOL`/`HANDLE` return is checked; `get_last_error()` is
  read **before** any cleanup call; every error path closes the handle it holds.
- **`ELOOP` preserved**: a file symlink at the final component raises `OSError` with
  `errno.ELOOP` on both platforms (`tools.py:218`, `tools.py:860` branch on it). Directories,
  junctions and directory symlinks fail with `EACCES` on Windows (no `FILE_FLAG_BACKUP_SEMANTICS`).
- `cloexec` and `nonblock` default `False`; each site passes exactly what §3.2's table says.
- Every `ctypes` function has `argtypes` and `restype`; the kernel32 handle is
  `WinDLL("kernel32", use_last_error=True)`; errors via `ctypes.get_last_error()`.
- `CREATE_SUSPENDED = 0x00000004` is defined in `osfs.py` (not exposed by `subprocess`).
- Windows-only tests are `pytest.mark.skipif(os.name != "nt", reason="Windows only")`; symlink
  tests additionally skip when `os.symlink` raises `OSError` (privilege) — skip with a reason,
  never pass vacuously (§4).
- DRY/SOLID (repo standing rule): all `ctypes` lives in `osfs.py`; `procs.py` and `resume.py`
  import `win32()` from it; no second binding anywhere.
- Windows stays "unsupported" in the README; the support row becomes "unit suite green in CI;
  unsupported pending an integration suite" (§4, §6.6).

## Execution model (every W task)

- **Scratchpad** (absolute; pin it in a new session — this one is the session that wrote the plan;
  a new session gets its own, and `run96.sh` is re-written there):
  `SCRATCH=/private/tmp/claude-501/-Users-jimschneider-repos-dirtywork/71616a0f-41b4-4742-b49a-b4e4aff59c20/scratchpad`
  — holds `run96.sh`, the briefs `brief-96-<task>.md` (extracted verbatim from this plan's fenced
  blocks), `feedback-96-<task>-r<n>.md`, `metrics-96.csv` (+ `.pid`).
- **Run command** (`$SCRATCH/run96.sh $SCRATCH/brief-96-<task>.md`):

  ```bash
  #!/bin/bash
  # run96.sh BRIEF_FILE [extra dirtywork args...] — one #96 dogfood run with the plan's flags.
  # REL is the latest release on PyPI at execution time (0.12.1 on 2026-08-27; 0.13.x after #87);
  # IMG is the derived pytest image for that minor (docker/README.md "Derived images").
  set -u
  REL="${DW_REL:?set DW_REL=<latest pypi version>}"; IMG="${DW_IMG:?set DW_IMG=dirtywork-worker-pytest:<X.Y>}"
  BRIEF="${1:?brief file}"; shift
  cd /Users/jimschneider/repos/dirtywork || exit 2
  pipx run --spec "dirtywork==$REL" dirtywork run "$(cat "$BRIEF")" \
    --repo /Users/jimschneider/repos/dirtywork --branch-from issue-96-windows-tier1 \
    --model qwen/qwen3-coder-next --sandbox docker --image "$IMG" \
    --verify "python3 -m pytest -q -p no:cacheprovider" \
    --verify-rounds 2 --max-turns 60 --timeout 1800 "$@" >"$BRIEF.out" 2>"$BRIEF.err"
  rc=$?
  echo "rc=$rc"; python3 -c "import json; d=json.load(open('$BRIEF.out')); print({k:d.get(k) for k in ('status','turns','final_message','run_dir','transcript','worktree','branch')})"
  exit $rc
  ```

- **Chaining:** each run branches from `issue-96-windows-tier1`; after review Claude commits the
  export on the run's branch (`worker export verbatim: run <slug>`), adds its own fix commits,
  fast-forwards `issue-96-windows-tier1` to it (`git rebase issue-96-windows-tier1` in the run
  worktree first when integration moved, then `git merge --ff-only dirtywork/<slug>` from a
  worktree of the integration branch — **never from the main checkout while the owner's other
  terminal has it checked out**), removes the run worktree and deletes the run branch. Tasks run
  strictly in order: W1 → W2 → W3 → W4 → W5 (W2 needs W1's helper; W3 and W4 need W1's `win32()`).
- **Review loop:** read `~/.dirtywork/runs/<slug>/run.json` + transcript; diff the run worktree
  against the brief and the spec section; grep the tests for the brief's literal cases (#61
  lesson: the model inverts a rule and writes the test to match); run the host suite in the run
  worktree (`python3 -m pytest -q -p no:cacheprovider`); gaps → `dirtywork resume <slug>
  --feedback-file <file> --max-turns 40`, feedback that names a file, a line and a shell check per
  item, at most two resumes; then Claude finishes leftovers and says so in the ledger and the PR.
- **Metrics:** `tools/soak_sampler.sh $SCRATCH/metrics-96.csv` (started in C0, detached with
  `nohup … >/dev/null 2>&1 &`; stopped in C1 with `--stop`); one ledger row per run (status,
  turns, wall, s/turn, prompt/completion tokens, tok/s, nudges, guardrail blocks, tool mix, verify
  outcome) appended to a `## #96` section of
  `docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md`.
- **Windows verification happens once, in C1:** the PR's advisory leg. If its table shows a
  Windows branch wrong, the fix is a resume of the relevant W task with the CI table pasted into
  the feedback file (spec §8.5).
- Give qwen ≥ 60 turns; resumes burn turns on `read_file`, so feedback names files and lines;
  escape `.` in grep checks.

## File structure

- Create `dirtywork/osfs.py` — `win32()` (cached kernel32 binding: prototypes, structures,
  constants), `_win_open_params(flags)` (pure), `_win_open(path, flags, cloexec, win)`,
  `_winerror(win, code, what, path)`, `open_nofollow(...)`. One module, one responsibility each.
- Create `tests/test_osfs.py` — spec §5 `test_osfs` cases (W1) + `test_posix_composition_is_identical` (W2).
- Modify `dirtywork/tools.py:104-115`, `:216`, `:256`, `:856`; `dirtywork/rundir.py:105`;
  `dirtywork/transcript.py:37-38`; `dirtywork/workspace.py:171`, `:184-186`;
  `dirtywork/bench.py:394`; `dirtywork/sandbox/export.py:255-256`, `:527` — eleven sites → `open_nofollow` (W2).
- Modify `dirtywork/procs.py` — `_spawn`, `_kill_tree`, `_spawn_windows`, `_fail`, `_Tree` (W3);
  `tests/test_procs.py` — five new tests (W3).
- Modify `dirtywork/resume.py:128-140` — `pid_alive` Windows branch (W4); `tests/test_resume.py` — two new tests (W4).
- Modify `tools/junit_summary.py` — `--collected FILE`, `absent()` (W5); `tests/test_junit_summary.py` — three new tests (W5).
- Modify `.github/workflows/ci.yml:36-66` (windows job: collect step, `-rE`, summary args),
  `docs/security.md` (Windows paragraph), `README.md` (support row), release notes (C1).

Not touched, on purpose: `runner.py`, `transcript.py`'s event schema, `contract/machine-contract.md`, `pyproject.toml` (except version at release).

---

### Task C0: Baseline and instrumentation (Claude)

**Files:** none in the repo except the ledger section.

- [ ] **Step 1: Sequence check.** #87 is merged and the owner has said go. `git fetch origin && git rebase origin/main` on `issue-96-windows-tier1` (the spec and this plan are its only commits; a clean rebase is expected).
- [ ] **Step 2: Confirm the runtime.** `REL=$(curl -s https://pypi.org/pypi/dirtywork/json | python3 -c 'import sys,json; print(json.load(sys.stdin)["info"]["version"])')`; `pipx run --spec "dirtywork==$REL" dirtywork --version` prints it; `docker images --format '{{.Repository}}:{{.Tag}}' | grep -x "dirtywork-worker-pytest:${REL%.*}"` prints the image (build it per `docker/README.md` "Derived images" if not); `curl -s http://localhost:1234/v1/models | grep -c qwen3-coder-next` prints ≥ 1. Export `DW_REL` and `DW_IMG` for `run96.sh`.
- [ ] **Step 3: Baseline.** In a worktree of `issue-96-windows-tier1` (`git worktree add .worktrees/issue-96-windows-tier1 issue-96-windows-tier1`), run `python3 -m pytest -q -p no:cacheprovider 2>&1 | tail -1` — record the count in the ledger (1,375 `def test_` on 2026-08-27 at `c476915`; the passed count is whatever main reports after #87).
- [ ] **Step 4: Windows baseline.** Download the latest main run's `junit-windows` artifact (`gh run download <id> -n junit-windows -D $SCRATCH/junit0`) and record: collected, pass/fail/error, and the absent-file list (13 on `ad70e60`) in the ledger. This is the "before" the PR's table is compared against.
- [ ] **Step 5: Sampler + scratch.** `mkdir -p $SCRATCH`; write `run96.sh` (above), `chmod +x`; `nohup tools/soak_sampler.sh $SCRATCH/metrics-96.csv >/dev/null 2>&1 &`.
- [ ] **Step 6: Ledger.** Append `## #96 — Windows tier 1 (plan v1)` to `docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md` with the two baselines and an empty per-run table (`| Task | Slug | Status | Turns | Wall | s/turn | Prompt tok | Compl tok | tok/s | Nudges | Guardrail | Tool mix | Verify | Resumes | Notes |`). Commit on the integration branch: `ledger: #96 section, baselines`.

---

### Task W1: `dirtywork/osfs.py` — the kernel32 binding and `open_nofollow` (spec §3.2, Appendix A; tests: `test_flag_mapping`, `test_verify_fails_closed`, `test_refuses_file_symlink`, the Windows-only three)

**Files:**
- Create: `dirtywork/osfs.py`, `tests/test_osfs.py`.

**Interfaces:**
- Produces (`dirtywork.osfs`): `open_nofollow(path, flags: int, mode: int = 0o600, *, cloexec: bool = False, nonblock: bool = False) -> int`;
  `win32() -> _Win32` (cached; `RuntimeError` off Windows); `_win_open_params(flags: int) -> tuple[int, tuple[int, ...], bool, int]`
  = `(access, dispositions, truncate_after, osf_flags)`; `_win_open(path: str, flags: int, cloexec: bool, win) -> int`;
  `_winerror(win, code: int, what: str, path=None) -> OSError`; constants `CREATE_SUSPENDED`, `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`,
  `JobObjectExtendedLimitInformation`, `PROCESS_SET_QUOTA`, `PROCESS_TERMINATE`, `PROCESS_QUERY_LIMITED_INFORMATION`,
  `TH32CS_SNAPTHREAD`, `THREAD_SUSPEND_RESUME`, `STILL_ACTIVE`, `INVALID_HANDLE_VALUE`, `ERROR_*`, and the structures
  `IO_COUNTERS`, `JOBOBJECT_BASIC_LIMIT_INFORMATION`, `JOBOBJECT_EXTENDED_LIMIT_INFORMATION`, `THREADENTRY32`,
  `FILE_ATTRIBUTE_TAG_INFO`, `FILE_END_OF_FILE_INFO` — all module-level names W3 and W4 import.

- [ ] **Step 1: Brief** `$SCRATCH/brief-96-w1.md`:

```
Issue #96 (Windows tier 1), task W1 of 5. Create the one module that owns every Windows ctypes binding and the symlink-refusing open helper. Do NOT touch any other file in dirtywork/ in this task; the eleven call sites are converted in W2. Standard library only. Python 3.9 compatible (no match statements, no `X | None` at runtime outside annotations — the file uses `from __future__ import annotations`).

1. Create dirtywork/osfs.py with exactly this content:

"""Symlink-safe opens and the Windows process/file bindings (#96 tier 1).

POSIX already has O_NOFOLLOW; Windows does not, and it also lacks os.killpg
and a side-effect-free os.kill(pid, 0). This module is the single home of the
ctypes bindings the Windows branches need (win32()) and of open_nofollow(),
which every O_NOFOLLOW site in the package calls with the flags it always did.
Nothing here imports ctypes.WinDLL unless os.name == "nt".
"""
from __future__ import annotations

import ctypes
import errno
import os
from ctypes import Structure, byref, c_int, c_long, c_size_t, c_uint, c_ulonglong, sizeof

__all__ = ["open_nofollow", "win32"]

_WINDOWS = os.name == "nt"

# --- constants (Microsoft Learn, read 2026-08-27; see spec Appendix A) ---------------------
CREATE_SUSPENDED = 0x00000004                    # Process Creation Flags; not exposed by subprocess
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000  # JOBOBJECT_BASIC_LIMIT_INFORMATION.LimitFlags
JobObjectExtendedLimitInformation = 9            # JOBOBJECTINFOCLASS
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
THREAD_SUSPEND_RESUME = 0x0002
TH32CS_SNAPTHREAD = 0x00000004
STILL_ACTIVE = 259
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value  # all bits set, pointer-sized
ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED = 5
ERROR_NO_MORE_FILES = 18
ERROR_FILE_EXISTS = 80
ERROR_INVALID_PARAMETER = 87
ERROR_ALREADY_EXISTS = 183
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_APPEND_DATA = 0x0004
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
FILE_SHARE_DELETE = 0x4
CREATE_NEW = 1          # CREATE_ALWAYS (2) and TRUNCATE_EXISTING (5) are never used: both are
OPEN_EXISTING = 3       # unsafe with FILE_FLAG_OPEN_REPARSE_POINT (spec §0.1 item 1)
OPEN_ALWAYS = 4
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FileEndOfFileInfo = 6                            # FILE_INFO_BY_HANDLE_CLASS
FileAttributeTagInfo = 9

_WINERRNO = {ERROR_FILE_NOT_FOUND: errno.ENOENT, ERROR_ACCESS_DENIED: errno.EACCES,
             ERROR_FILE_EXISTS: errno.EEXIST, ERROR_ALREADY_EXISTS: errno.EEXIST,
             ERROR_INVALID_PARAMETER: errno.EINVAL}


# --- structures (member order is the ABI; do not reorder) ----------------------------------
class IO_COUNTERS(Structure):
    _fields_ = [("ReadOperationCount", c_ulonglong), ("WriteOperationCount", c_ulonglong),
                ("OtherOperationCount", c_ulonglong), ("ReadTransferCount", c_ulonglong),
                ("WriteTransferCount", c_ulonglong), ("OtherTransferCount", c_ulonglong)]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", c_size_t),
                ("MaximumWorkingSetSize", c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", c_size_t), ("PriorityClass", ctypes.c_uint32), ("SchedulingClass", ctypes.c_uint32)]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", c_size_t), ("JobMemoryLimit", c_size_t),
                ("PeakProcessMemoryUsed", c_size_t), ("PeakJobMemoryUsed", c_size_t)]


class THREADENTRY32(Structure):
    _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32), ("th32ThreadID", ctypes.c_uint32),
                ("th32OwnerProcessID", ctypes.c_uint32), ("tpBasePri", c_long), ("tpDeltaPri", c_long),
                ("dwFlags", ctypes.c_uint32)]


class FILE_ATTRIBUTE_TAG_INFO(Structure):
    _fields_ = [("FileAttributes", ctypes.c_uint32), ("ReparseTag", ctypes.c_uint32)]


class FILE_END_OF_FILE_INFO(Structure):
    _fields_ = [("EndOfFile", ctypes.c_longlong)]


class _Win32:
    """kernel32 with argtypes/restype declared for every call we make. A handle
    left to ctypes' default int conversion is truncated on 64-bit and fails
    silently; every HANDLE below is c_void_p."""

    def __init__(self) -> None:
        from ctypes import wintypes as w  # only importable on Windows
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        HANDLE, DWORD, BOOL, LPVOID, LPCWSTR = w.HANDLE, w.DWORD, w.BOOL, w.LPVOID, w.LPCWSTR
        self.get_last_error = ctypes.get_last_error
        self.FormatError = ctypes.FormatError
        for name, argtypes, restype in (
            ("CreateJobObjectW", [LPVOID, LPCWSTR], HANDLE),
            ("SetInformationJobObject", [HANDLE, c_int, LPVOID, DWORD], BOOL),
            ("AssignProcessToJobObject", [HANDLE, HANDLE], BOOL),
            ("TerminateJobObject", [HANDLE, c_uint], BOOL),
            ("OpenProcess", [DWORD, BOOL, DWORD], HANDLE),
            ("TerminateProcess", [HANDLE, c_uint], BOOL),
            ("GetExitCodeProcess", [HANDLE, ctypes.POINTER(DWORD)], BOOL),
            ("CreateToolhelp32Snapshot", [DWORD, DWORD], HANDLE),
            ("Thread32First", [HANDLE, ctypes.POINTER(THREADENTRY32)], BOOL),
            ("Thread32Next", [HANDLE, ctypes.POINTER(THREADENTRY32)], BOOL),
            ("OpenThread", [DWORD, BOOL, DWORD], HANDLE),
            ("ResumeThread", [HANDLE], DWORD),
            ("CloseHandle", [HANDLE], BOOL),
            ("CreateFileW", [LPCWSTR, DWORD, DWORD, LPVOID, DWORD, DWORD, HANDLE], HANDLE),
            ("GetFileInformationByHandleEx", [HANDLE, c_int, LPVOID, DWORD], BOOL),
            ("SetFileInformationByHandle", [HANDLE, c_int, LPVOID, DWORD], BOOL),
        ):
            fn = getattr(k, name)
            fn.argtypes = argtypes
            fn.restype = restype
            setattr(self, name, fn)


_WIN32 = None


def win32():
    """The process-wide kernel32 binding. RuntimeError off Windows: callers
    branch on os.name first, and the tests pass a fake in its place."""
    global _WIN32
    if not _WINDOWS:
        raise RuntimeError("win32() is only available on Windows")
    if _WIN32 is None:
        _WIN32 = _Win32()
    return _WIN32


def _winerror(win, code: int, what: str, path=None) -> OSError:
    """An OSError whose errno matches what the POSIX call would have raised,
    so callers' `except OSError as e: if e.errno == ...` branches keep working."""
    err = _WINERRNO.get(code, errno.EIO)
    msg = f"{what}: [WinError {code}] {win.FormatError(code)}"
    return OSError(err, msg, path) if path is not None else OSError(err, msg)


def _win_open_params(flags: int) -> tuple:
    """(access, dispositions, truncate_after, osf_flags) for a POSIX flag word.
    Pure; testable everywhere. `dispositions` is tried in order: the second
    entry (only for O_CREAT|O_TRUNC) is used when the first fails with
    ERROR_FILE_EXISTS, and `truncate_after` then applies to it."""
    if flags & os.O_APPEND:
        access = FILE_APPEND_DATA
    elif flags & os.O_RDWR:
        access = GENERIC_READ | GENERIC_WRITE
    elif flags & os.O_WRONLY:
        access = GENERIC_WRITE
    else:
        access = GENERIC_READ
    creat, excl, trunc = flags & os.O_CREAT, flags & os.O_EXCL, flags & os.O_TRUNC
    if creat and excl:
        dispositions, truncate_after = (CREATE_NEW,), False
    elif creat and trunc:
        dispositions, truncate_after = (CREATE_NEW, OPEN_EXISTING), True
    elif creat:
        dispositions, truncate_after = (OPEN_ALWAYS,), False
    else:
        dispositions, truncate_after = (OPEN_EXISTING,), bool(trunc)
    osf_flags = os.O_APPEND if flags & os.O_APPEND else 0
    return access, dispositions, truncate_after, osf_flags


def _win_open(path: str, flags: int, cloexec: bool, win) -> int:
    """CreateFileW with FILE_FLAG_OPEN_REPARSE_POINT (opens the link itself,
    never its target), then verify on the handle we hold -- no check-then-open
    window -- and hand the handle to the CRT. Every failure reads the error
    code BEFORE closing anything and closes the handle it holds."""
    import msvcrt  # Windows only
    access, dispositions, truncate_after, osf_flags = _win_open_params(flags)
    share = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
    attrs = FILE_FLAG_OPEN_REPARSE_POINT | FILE_ATTRIBUTE_NORMAL
    h = INVALID_HANDLE_VALUE
    used = dispositions[0]
    for i, disposition in enumerate(dispositions):
        h = win.CreateFileW(path, access, share, None, disposition, attrs, None)
        if h != INVALID_HANDLE_VALUE:
            used = disposition
            break
        code = win.get_last_error()
        last = i == len(dispositions) - 1
        if last or code != ERROR_FILE_EXISTS:
            raise _winerror(win, code, "CreateFileW", path)
    truncate = truncate_after and used == OPEN_EXISTING

    tag = FILE_ATTRIBUTE_TAG_INFO()
    if not win.GetFileInformationByHandleEx(h, FileAttributeTagInfo, byref(tag), sizeof(tag)):
        code = win.get_last_error()
        win.CloseHandle(h)
        raise _winerror(win, code, "GetFileInformationByHandleEx", path)
    if tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
        win.CloseHandle(h)
        raise OSError(errno.ELOOP, "symlink at final component", path)
    if tag.FileAttributes & FILE_ATTRIBUTE_DIRECTORY:
        win.CloseHandle(h)
        raise OSError(errno.EISDIR, "is a directory", path)
    if truncate:
        eof = FILE_END_OF_FILE_INFO(0)
        if not win.SetFileInformationByHandle(h, FileEndOfFileInfo, byref(eof), sizeof(eof)):
            code = win.get_last_error()
            win.CloseHandle(h)
            raise _winerror(win, code, "SetFileInformationByHandle", path)
    try:
        return msvcrt.open_osfhandle(h, osf_flags | (os.O_NOINHERIT if cloexec else 0))
    except OSError:
        win.CloseHandle(h)
        raise


def open_nofollow(path, flags: int, mode: int = 0o600, *, cloexec: bool = False,
                  nonblock: bool = False) -> int:
    """os.open(path, flags, mode) that refuses a symlink at the final path
    component: POSIX O_NOFOLLOW (plus O_CLOEXEC / O_NONBLOCK when asked);
    Windows CreateFileW + FILE_FLAG_OPEN_REPARSE_POINT with the attribute read
    from the handle we hold, ELOOP on a reparse point. `nonblock` is a no-op on
    Windows (no filesystem FIFOs a worktree path can name); `mode` is ignored
    there beyond what CreateFileW does (ACL inheritance; spec §4)."""
    if not _WINDOWS:
        extra = os.O_NOFOLLOW
        if cloexec:
            extra |= os.O_CLOEXEC
        if nonblock:
            extra |= os.O_NONBLOCK
        return os.open(str(path), flags | extra, mode)
    return _win_open(str(path), flags, cloexec, win32())

2. Create tests/test_osfs.py with exactly these tests (plus the imports they need: errno, os, pytest, from dirtywork import osfs, from dirtywork.osfs import open_nofollow, _win_open_params, _win_open, INVALID_HANDLE_VALUE, ERROR_FILE_EXISTS, CREATE_NEW, OPEN_EXISTING, OPEN_ALWAYS, GENERIC_READ, GENERIC_WRITE, FILE_APPEND_DATA, FILE_ATTRIBUTE_REPARSE_POINT):

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Windows only")

class FakeWin:
    """A stand-in for osfs.win32(): records calls, returns what the test says.
    `tag_attrs` is what GetFileInformationByHandleEx reports; `fail` names the
    call that should return failure; `exists` makes CREATE_NEW fail with
    ERROR_FILE_EXISTS once."""
    def __init__(self, tag_attrs=0, fail=None, exists=False):
        self.calls = []; self.tag_attrs = tag_attrs; self.fail = fail; self.exists = exists
        self.closed = []; self._err = 0; self.handle = 1234
    def get_last_error(self): return self._err
    def FormatError(self, code): return f"fake error {code}"
    def CreateFileW(self, path, access, share, sa, disposition, attrs, tmpl):
        self.calls.append(("CreateFileW", disposition, access))
        if self.exists and disposition == CREATE_NEW:
            self.exists = False; self._err = ERROR_FILE_EXISTS; return INVALID_HANDLE_VALUE
        if self.fail == "CreateFileW":
            self._err = 5; return INVALID_HANDLE_VALUE
        return self.handle
    def GetFileInformationByHandleEx(self, h, cls, buf, size):
        self.calls.append(("GetFileInformationByHandleEx", h))
        if self.fail == "GetFileInformationByHandleEx":
            self._err = 87; return 0
        buf._obj.FileAttributes = self.tag_attrs; return 1
    def SetFileInformationByHandle(self, h, cls, buf, size):
        self.calls.append(("SetFileInformationByHandle", h, cls))
        if self.fail == "SetFileInformationByHandle":
            self._err = 5; return 0
        return 1
    def CloseHandle(self, h):
        self.closed.append(h); return 1

@pytest.mark.parametrize("flags, access, dispositions, truncate_after, osf", [
    (os.O_WRONLY | os.O_CREAT | os.O_TRUNC, GENERIC_WRITE, (CREATE_NEW, OPEN_EXISTING), True, 0),     # tools.py:104, rundir.py:105, export.py:527
    (os.O_WRONLY, GENERIC_WRITE, (OPEN_EXISTING,), False, 0),                                          # tools.py:216, :856
    (os.O_WRONLY | os.O_CREAT | os.O_EXCL, GENERIC_WRITE, (CREATE_NEW,), False, 0),                    # tools.py:256, export.py:255
    (os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, FILE_APPEND_DATA, (CREATE_NEW,), False, os.O_APPEND),  # transcript.py:37
    (os.O_RDONLY, GENERIC_READ, (OPEN_EXISTING,), False, 0),                                           # workspace.py:171
    (os.O_WRONLY | os.O_CREAT | os.O_APPEND, FILE_APPEND_DATA, (OPEN_ALWAYS,), False, os.O_APPEND),    # workspace.py:185, bench.py:394
])
def test_flag_mapping(flags, access, dispositions, truncate_after, osf):
    assert _win_open_params(flags) == (access, dispositions, truncate_after, osf)

def test_create_trunc_falls_back_to_open_existing_and_truncates(monkeypatch):
    """O_CREAT|O_TRUNC: CREATE_NEW first; on ERROR_FILE_EXISTS, OPEN_EXISTING then
    truncate through the verified handle. Never CREATE_ALWAYS, never TRUNCATE_EXISTING."""
    win = FakeWin(exists=True)
    monkeypatch.setattr(osfs, "msvcrt", None, raising=False)
    import types, sys
    fake_msvcrt = types.SimpleNamespace(open_osfhandle=lambda h, f: 7)
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    fd = _win_open("p", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, False, win)
    assert fd == 7
    dispositions = [c[1] for c in win.calls if c[0] == "CreateFileW"]
    assert dispositions == [CREATE_NEW, OPEN_EXISTING]
    assert ("SetFileInformationByHandle", win.handle, osfs.FileEndOfFileInfo) in win.calls
    assert win.closed == []

def test_create_new_does_not_truncate(monkeypatch):
    import types, sys
    monkeypatch.setitem(sys.modules, "msvcrt", types.SimpleNamespace(open_osfhandle=lambda h, f: 7))
    win = FakeWin()
    _win_open("p", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, False, win)
    assert [c[1] for c in win.calls if c[0] == "CreateFileW"] == [CREATE_NEW]
    assert not any(c[0] == "SetFileInformationByHandle" for c in win.calls)

def test_refuses_reparse_point_with_eloop_and_closes(monkeypatch):
    import types, sys
    monkeypatch.setitem(sys.modules, "msvcrt", types.SimpleNamespace(open_osfhandle=lambda h, f: 7))
    win = FakeWin(tag_attrs=FILE_ATTRIBUTE_REPARSE_POINT)
    with pytest.raises(OSError) as ei:
        _win_open("p", os.O_WRONLY, False, win)
    assert ei.value.errno == errno.ELOOP
    assert win.closed == [win.handle]

@pytest.mark.parametrize("failing", ["GetFileInformationByHandleEx", "SetFileInformationByHandle"])
def test_verify_fails_closed(monkeypatch, failing):
    """A failed Win32 call after CreateFileW raises and closes the handle -- never
    returns an fd on a zeroed struct."""
    import types, sys
    monkeypatch.setitem(sys.modules, "msvcrt", types.SimpleNamespace(open_osfhandle=lambda h, f: 7))
    win = FakeWin(fail=failing, exists=(failing == "SetFileInformationByHandle"))
    with pytest.raises(OSError):
        _win_open("p", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, False, win)
    assert win.closed == [win.handle]

def test_open_osfhandle_failure_closes_handle(monkeypatch):
    import types, sys
    def boom(h, f): raise OSError(errno.EBADF, "bad handle")
    monkeypatch.setitem(sys.modules, "msvcrt", types.SimpleNamespace(open_osfhandle=boom))
    win = FakeWin()
    with pytest.raises(OSError):
        _win_open("p", os.O_RDONLY, False, win)
    assert win.closed == [win.handle]

def test_createfile_failure_maps_errno(monkeypatch):
    import types, sys
    monkeypatch.setitem(sys.modules, "msvcrt", types.SimpleNamespace(open_osfhandle=lambda h, f: 7))
    win = FakeWin(fail="CreateFileW")
    with pytest.raises(OSError) as ei:
        _win_open("p", os.O_RDONLY, False, win)
    assert ei.value.errno == errno.EACCES
    assert win.closed == []

@pytest.mark.skipif(os.name == "nt", reason="POSIX composition")
def test_posix_refuses_file_symlink(tmp_path):
    target = tmp_path / "t"; target.write_text("x")
    link = tmp_path / "l"; os.symlink(target, link)
    with pytest.raises(OSError) as ei:
        open_nofollow(link, os.O_WRONLY)
    assert ei.value.errno == errno.ELOOP
    fd = open_nofollow(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.close(fd)
    assert target.read_text() == ""

@pytest.mark.skipif(os.name == "nt", reason="POSIX composition")
def test_posix_keywords_add_only_the_requested_flags(monkeypatch):
    seen = {}
    def fake_open(path, flags, mode): seen["flags"] = flags; seen["mode"] = mode; return 3
    monkeypatch.setattr(os, "open", fake_open)
    open_nofollow("p", os.O_RDONLY)
    assert seen["flags"] == os.O_RDONLY | os.O_NOFOLLOW and seen["mode"] == 0o600
    open_nofollow("p", os.O_WRONLY, 0o644, cloexec=True, nonblock=True)
    assert seen["flags"] == os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK and seen["mode"] == 0o644

def test_win32_raises_off_windows():
    if os.name != "nt":
        with pytest.raises(RuntimeError):
            osfs.win32()

def test_struct_sizes():
    """The ABI sizes the Windows branch depends on; a wrong _fields_ makes
    SetInformationJobObject fail with ERROR_INVALID_PARAMETER on Windows and
    this assertion fail everywhere."""
    from ctypes import sizeof
    assert sizeof(osfs.IO_COUNTERS) == 48
    assert sizeof(osfs.FILE_ATTRIBUTE_TAG_INFO) == 8
    assert sizeof(osfs.THREADENTRY32) == 28
    assert sizeof(osfs.JOBOBJECT_EXTENDED_LIMIT_INFORMATION) == 144 if sizeof(osfs.c_size_t) == 8 else True

@WINDOWS_ONLY
def test_windows_refuses_file_symlink(tmp_path):
    target = tmp_path / "t"; target.write_text("x")
    link = tmp_path / "l"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink privilege not available")
    with pytest.raises(OSError) as ei:
        open_nofollow(link, os.O_WRONLY)
    assert ei.value.errno == errno.ELOOP
    assert target.read_text() == "x"

@WINDOWS_ONLY
def test_windows_refuses_junction_with_eacces(tmp_path):
    import _winapi
    d = tmp_path / "d"; d.mkdir()
    j = tmp_path / "j"; _winapi.CreateJunction(str(d), str(j))
    with pytest.raises(OSError) as ei:
        open_nofollow(j, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    assert ei.value.errno == errno.EACCES

@WINDOWS_ONLY
def test_windows_create_trunc_truncates_through_verified_handle(tmp_path):
    p = tmp_path / "f"; p.write_text("hello")
    fd = open_nofollow(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    os.close(fd)
    assert p.stat().st_size == 0
    fd = open_nofollow(tmp_path / "new", os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    os.write(fd, b"a"); os.close(fd)
    assert (tmp_path / "new").read_bytes() == b"a"

3. Run python3 -m pytest -q -p no:cacheprovider tests/test_osfs.py and then the whole suite; everything must pass. The Windows-only tests skip here. Do not modify any file other than the two named above.
```

- [ ] **Step 2: Run.** `$SCRATCH/run96.sh $SCRATCH/brief-96-w1.md`; ledger row.
- [ ] **Step 3: Review.** In the run worktree: `grep -c 'def test_' tests/test_osfs.py` ≥ 14; `grep -n 'CREATE_ALWAYS\|TRUNCATE_EXISTING' dirtywork/osfs.py` shows only the comment; `grep -n 'get_last_error()' dirtywork/osfs.py` — every one precedes the `CloseHandle` in its block; `python3 -c "import dirtywork.osfs"` on the host succeeds without touching `ctypes.WinDLL`; `python3 -m pytest -q -p no:cacheprovider` green. Compare the module to the brief line by line — the model's habit is to "simplify" `_win_open_params`.
- [ ] **Step 4: Resume if needed** (`feedback-96-w1-r1.md`: file, line, shell check per item), at most twice.
- [ ] **Step 5: Commit** the export on the run branch; fast-forward `issue-96-windows-tier1`; ledger row.

---

### Task W2: The eleven sites call `open_nofollow` (spec §3.2 table; test `test_posix_composition_is_identical`)

**Files:**
- Modify: `dirtywork/tools.py:104-105`, `:115`, `:215-216`, `:254-257`, `:856`; `dirtywork/rundir.py:105`;
  `dirtywork/transcript.py:37-38`; `dirtywork/workspace.py:171`, `:184-186`; `dirtywork/bench.py:394`;
  `dirtywork/sandbox/export.py:255-256`, `:527`.
- Test: `tests/test_osfs.py` (append).

**Interfaces:**
- Consumes: `dirtywork.osfs.open_nofollow(path, flags, mode=0o600, *, cloexec=False, nonblock=False)` (W1).
- Produces: no new names. Behaviour on POSIX byte-identical; on Windows the eleven opens go through `_win_open`.

- [ ] **Step 1: Brief** `$SCRATCH/brief-96-w2.md`:

```
Issue #96 (Windows tier 1), task W2 of 5. Replace every `os.open(..., os.O_NOFOLLOW ...)` in the package with `open_nofollow` from dirtywork/osfs.py (already present). The POSIX flags each site composes must stay byte-identical: the helper always adds O_NOFOLLOW; it adds O_CLOEXEC only when cloexec=True and O_NONBLOCK only when nonblock=True. So each site passes its remaining flags plus exactly the keywords in this table -- no more, no less. Add `from .osfs import open_nofollow` (or `from ..osfs import open_nofollow` in sandbox/export.py) next to the other relative imports of each file.

Exact edits (old -> new; keep everything else on those lines):

a) dirtywork/tools.py line 104-105 (in _open_regular):
   old: full_flags = flags | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        fd = os.open(str(path), full_flags, mode)
   new: fd = open_nofollow(path, flags, mode, cloexec=True, nonblock=True)
   And line 115: `os.set_blocking(fd, True)` becomes
        if os.name != "nt":
            os.set_blocking(fd, True)   # nonblock is a no-op on Windows; set_blocking is pipes-only there
   Update the docstring's "this function always adds those three" sentence to say the helper adds them (POSIX) and that on Windows O_NONBLOCK/O_CLOEXEC have no filesystem equivalent (see osfs.open_nofollow).

b) dirtywork/tools.py line 215-216 (probe):
   old: probe_fd = os.open(str(target), os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
   new: probe_fd = open_nofollow(target, os.O_WRONLY, cloexec=True, nonblock=True)

c) dirtywork/tools.py line 254-257 (tmp create):
   old: tmp_fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
   new: tmp_fd = open_nofollow(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, cloexec=True)

d) dirtywork/tools.py line 856 (probe):
   old: probe_fd = os.open(str(p), os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
   new: probe_fd = open_nofollow(p, os.O_WRONLY, cloexec=True, nonblock=True)

e) dirtywork/rundir.py line 105:
   old: fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
   new: fd = open_nofollow(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)

f) dirtywork/transcript.py lines 37-38:
   old: flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | os.O_NOFOLLOW
        fd = os.open(str(self.path), flags, 0o600)
   new: fd = open_nofollow(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o600)

g) dirtywork/workspace.py line 171:
   old: read_fd = os.open(str(exclude), os.O_RDONLY | os.O_NOFOLLOW)
   new: read_fd = open_nofollow(exclude, os.O_RDONLY)
   (the surrounding `except FileNotFoundError` stays: open_nofollow raises the same OSError subclasses on POSIX and an OSError with errno ENOENT on Windows -- change that except to `except OSError as e: if e.errno != errno.ENOENT: raise` ONLY if FileNotFoundError is not what the Windows branch raises; on POSIX nothing changes. Simplest correct edit: keep `except FileNotFoundError` and add nothing -- _winerror builds a plain OSError, so ALSO change the except to `except OSError as e:` with `if e.errno != errno.ENOENT: raise` as its first line, importing errno if the module does not already.)

h) dirtywork/workspace.py lines 184-186:
   old: write_fd = os.open(str(exclude), os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o644)
   new: write_fd = open_nofollow(exclude, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)

i) dirtywork/bench.py line 394:
   old: fd = os.open(str(out_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
   new: fd = open_nofollow(out_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)

j) dirtywork/sandbox/export.py lines 255-256:
   old: fd = os.open(str(target_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
   new: fd = open_nofollow(target_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)

k) dirtywork/sandbox/export.py line 527:
   old: fd = os.open(str(patch_target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
   new: fd = open_nofollow(patch_target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)

After the edits: `grep -rn 'os\.O_NOFOLLOW' dirtywork --include='*.py'` must print ONLY lines inside dirtywork/osfs.py and comment/docstring lines (no `os.open(` call anywhere else uses it).

Append to tests/test_osfs.py:

SITES = {
    # file:line -> (flags passed to open_nofollow, mode, cloexec, nonblock, the literal flags the site composed before #96)
    "tools.py:104":  (os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644, True, True,  os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC),
    "tools.py:216":  (os.O_WRONLY, 0o600, True, True,   os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC),
    "tools.py:256":  (os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, True, False,  os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC),
    "tools.py:856":  (os.O_WRONLY, 0o600, True, True,   os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC),
    "rundir.py:105": (os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600, False, False, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW),
    "transcript.py:37": (os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o600, False, False, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | os.O_NOFOLLOW),
    "workspace.py:171": (os.O_RDONLY, 0o600, False, False, os.O_RDONLY | os.O_NOFOLLOW),
    "workspace.py:185": (os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644, False, False, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW),
    "bench.py:394":  (os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600, False, False, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW),
    "sandbox/export.py:255": (os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, False, False, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW),
    "sandbox/export.py:527": (os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600, False, False, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW),
}

@pytest.mark.skipif(os.name == "nt", reason="POSIX composition")
@pytest.mark.parametrize("site", sorted(SITES))
def test_posix_composition_is_identical(monkeypatch, site):
    """Each converted site composes exactly the os.open flags it composed before #96."""
    flags, mode, cloexec, nonblock, before = SITES[site]
    seen = {}
    monkeypatch.setattr(os, "open", lambda p, f, m=0o777: seen.update(flags=f) or 3)
    open_nofollow("p", flags, mode, cloexec=cloexec, nonblock=nonblock)
    assert seen["flags"] == before, site

def test_no_raw_nofollow_opens_outside_osfs():
    """The eleven sites are converted; nothing else composes O_NOFOLLOW by hand."""
    import re, pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "dirtywork"
    offenders = []
    for py in root.rglob("*.py"):
        if py.name == "osfs.py":
            continue
        for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if "os.open(" in line and "O_NOFOLLOW" in line:
                offenders.append(f"{py.relative_to(root)}:{n}")
    assert offenders == []

Run python3 -m pytest -q -p no:cacheprovider; the whole suite must pass (existing tests are the proof of byte-identical behaviour). Do not change any behaviour beyond the edits listed.
```

- [ ] **Step 2: Run.** `$SCRATCH/run96.sh $SCRATCH/brief-96-w2.md`; ledger row.
- [ ] **Step 3: Review.** `grep -rn 'os\.O_NOFOLLOW' dirtywork --include='*.py' | grep 'os\.open('` prints nothing; `grep -n 'set_blocking' dirtywork/tools.py` shows the `os.name != "nt"` guard; diff each of the eleven hunks against the table (the model's habit: passing `cloexec=True` everywhere, or dropping a mode); `test_posix_composition_is_identical` has eleven parametrized cases; full suite green (count unchanged + new tests).
- [ ] **Step 4: Resume if needed**, at most twice. **Step 5: Commit**, fast-forward, ledger row.

---

### Task W3: `procs.py` — `_spawn`/`_kill_tree` and the Job Object branch (spec §3.1; tests §5 `test_procs`)

**Files:**
- Modify: `dirtywork/procs.py` (whole `run_capped` prologue/epilogue; new `_Tree`, `_spawn`, `_spawn_windows`, `_fail`, `_kill_tree`).
- Test: `tests/test_procs.py` (existing six unchanged; five added).

**Interfaces:**
- Consumes (`dirtywork.osfs`, W1): `win32()`, `CREATE_SUSPENDED`, `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, `JobObjectExtendedLimitInformation`,
  `PROCESS_SET_QUOTA`, `PROCESS_TERMINATE`, `TH32CS_SNAPTHREAD`, `THREAD_SUSPEND_RESUME`, `INVALID_HANDLE_VALUE`,
  `JOBOBJECT_EXTENDED_LIMIT_INFORMATION`, `THREADENTRY32`.
- Produces (`dirtywork.procs`): `_Tree` (dataclass: `job`, `proc`), `_spawn(argv, *, cwd, env, stdin, kill_group) -> tuple[Popen, _Tree | None] | Captured`,
  `_spawn_windows(argv, *, cwd, env, stdin, win=None, popen=subprocess.Popen) -> tuple[Popen, _Tree] | Captured`,
  `_fail(win, what, *, err=None, kill=None, close=()) -> Captured`, `_kill_tree(proc, tree, kill_group)`. `run_capped`'s signature and `Captured` unchanged.

- [ ] **Step 1: Brief** `$SCRATCH/brief-96-w3.md`:

```
Issue #96 (Windows tier 1), task W3 of 5. In dirtywork/procs.py, split the process start and the process-tree kill out of run_capped so a Windows branch can use a Job Object; the POSIX branch stays byte-for-byte what it is today (start_new_session=kill_group; _kill_group with os.killpg SIGKILL, unconditional after wait). Everything Windows-specific binds through dirtywork.osfs.win32() -- do not call ctypes.WinDLL anywhere in procs.py.

1. Add these imports after `from dataclasses import dataclass`:
from . import osfs
from .osfs import (CREATE_SUSPENDED, INVALID_HANDLE_VALUE, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
                   JobObjectExtendedLimitInformation, PROCESS_SET_QUOTA, PROCESS_TERMINATE,
                   TH32CS_SNAPTHREAD, THREAD_SUSPEND_RESUME, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
                   THREADENTRY32)
from ctypes import byref, sizeof

2. Keep `Captured` and `_kill_group` exactly as they are. Add, after `_kill_group`:

@dataclass
class _Tree:
    """The two handles the Windows branch holds for a child: its job and the
    process handle used to assign it. None on POSIX."""
    job: int
    proc: int


def _fail(win, what: str, *, err=None, kill=None, close=()) -> Captured:
    """The one failure path of _spawn_windows: read the error code BEFORE any
    cleanup call (CloseHandle clobbers it), terminate the suspended child so
    nothing is left frozen, close the handles, and return the same shape
    run_capped returns when Popen itself raises OSError."""
    err = win.get_last_error() if err is None else err
    if kill is not None:
        try:
            kill.kill()
        except OSError:
            pass
        try:
            kill.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    for h in close:
        win.CloseHandle(h)
    msg = f"process-tree containment unavailable: {what}: [WinError {err}] {win.FormatError(err)}"
    return Captured(returncode=None, output=msg.encode(), truncated=False, timed_out=False)


def _spawn_windows(argv, *, cwd, env, stdin, win=None, popen=subprocess.Popen):
    """Create the child suspended, put it in a job that kills every descendant
    when the job dies, then resume its one initial thread -- so containment
    holds from the child's first instruction (spec §3.1). Every Win32 return
    is checked; any failure goes through _fail."""
    win = osfs.win32() if win is None else win
    hJob = win.CreateJobObjectW(None, None)
    if not hJob:
        return _fail(win, "CreateJobObjectW")
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not win.SetInformationJobObject(hJob, JobObjectExtendedLimitInformation, byref(info), sizeof(info)):
        return _fail(win, "SetInformationJobObject", close=[hJob])
    try:
        proc = popen(argv, cwd=cwd, env=env,
                     stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     creationflags=CREATE_SUSPENDED)
    except OSError as e:
        win.CloseHandle(hJob)
        return Captured(returncode=None, output=str(e).encode(), truncated=False, timed_out=False)
    hProc = win.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, proc.pid)
    if not hProc:
        return _fail(win, "OpenProcess", kill=proc, close=[hJob])
    if not win.AssignProcessToJobObject(hJob, hProc):
        return _fail(win, "AssignProcessToJobObject", kill=proc, close=[hProc, hJob])
    snap = win.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap == INVALID_HANDLE_VALUE:
        return _fail(win, "CreateToolhelp32Snapshot", kill=proc, close=[hProc, hJob])
    te = THREADENTRY32()
    te.dwSize = sizeof(THREADENTRY32)           # required, or Thread32First fails
    tid = None
    ok = win.Thread32First(snap, byref(te))
    while ok:
        if te.th32OwnerProcessID == proc.pid:
            tid = te.th32ThreadID
            break
        ok = win.Thread32Next(snap, byref(te))
    win.CloseHandle(snap)
    if tid is None:
        return _fail(win, "thread not found in snapshot", err=0, kill=proc, close=[hProc, hJob])
    hThread = win.OpenThread(THREAD_SUSPEND_RESUME, False, tid)
    if not hThread:
        return _fail(win, "OpenThread", kill=proc, close=[hProc, hJob])
    prev = win.ResumeThread(hThread)             # previous suspend count; 0xFFFFFFFF on failure
    err = win.get_last_error()
    win.CloseHandle(hThread)
    if prev != 1:
        return _fail(win, f"ResumeThread returned {prev}", err=err, kill=proc, close=[hProc, hJob])
    return proc, _Tree(job=hJob, proc=hProc)


def _spawn(argv, *, cwd, env, stdin, kill_group):
    """(proc, tree) or a Captured describing why the child could not be started.
    POSIX: exactly today's Popen call. Windows with kill_group: the job branch."""
    if os.name == "nt" and kill_group:
        return _spawn_windows(argv, cwd=cwd, env=env, stdin=stdin)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=kill_group,
        )
    except OSError as e:
        return Captured(returncode=None, output=str(e).encode(), truncated=False, timed_out=False)
    return proc, None


def _kill_tree(proc, tree, kill_group: bool) -> None:
    """Unconditional, clean exit included -- as today."""
    if tree is not None:
        win = osfs.win32()
        win.TerminateJobObject(tree.job, 1)      # replaces _kill_group(proc.pid)
        win.CloseHandle(tree.job)                # KILL_ON_JOB_CLOSE: belt to TerminateJobObject's braces
        win.CloseHandle(tree.proc)
        return
    if kill_group:
        _kill_group(proc.pid)
    else:
        try:
            proc.kill()
        except OSError:
            pass

3. In run_capped: replace the `try: proc = subprocess.Popen(...) except OSError as e: return Captured(...)` block with
    spawned = _spawn(argv, cwd=cwd, env=env, stdin=stdin, kill_group=kill_group)
    if isinstance(spawned, Captured):
        return spawned
    proc, tree = spawned
and replace the epilogue
    if kill_group:
        _kill_group(proc.pid)
    else:
        try:
            proc.kill()
        except OSError:
            pass
with
    _kill_tree(proc, tree, kill_group)
Everything else in run_capped (drain thread, stdin feeder, wait/timeout, joins, the return) stays byte-identical. Update run_capped's docstring: "...by killing the whole process group (POSIX) or job (Windows) so backgrounded children cannot outlive the call."

4. Append to tests/test_procs.py (keep the existing six untouched; add imports: os, subprocess, pytest, from dirtywork import procs, from dirtywork.procs import _spawn_windows, _kill_tree, _Tree, _fail, Captured):

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Windows only")

class FakeWin:
    """Stand-in for osfs.win32(); `fail` names the call that returns failure;
    `resume` is what ResumeThread returns."""
    def __init__(self, fail=None, resume=1, pid=4242):
        self.fail = fail; self.resume = resume; self.pid = pid
        self.calls = []; self.closed = []; self._err = 0
    def get_last_error(self): return self._err
    def FormatError(self, code): return f"fake {code}"
    def _f(self, name, ok_value):
        self.calls.append(name)
        if self.fail == name:
            self._err = 5; return 0
        return ok_value
    def CreateJobObjectW(self, a, b): return self._f("CreateJobObjectW", 100)
    def SetInformationJobObject(self, h, c, p, s): return self._f("SetInformationJobObject", 1)
    def OpenProcess(self, a, i, pid): return self._f("OpenProcess", 200)
    def AssignProcessToJobObject(self, j, p): return self._f("AssignProcessToJobObject", 1)
    def CreateToolhelp32Snapshot(self, f, p): return self._f("CreateToolhelp32Snapshot", 300)
    def Thread32First(self, s, te):
        self.calls.append("Thread32First"); te._obj.th32OwnerProcessID = self.pid; te._obj.th32ThreadID = 7; return 1
    def Thread32Next(self, s, te): return 0
    def OpenThread(self, a, i, tid): return self._f("OpenThread", 400)
    def ResumeThread(self, h): self.calls.append("ResumeThread"); return self.resume
    def TerminateJobObject(self, j, c): self.calls.append("TerminateJobObject"); return 1
    def CloseHandle(self, h): self.closed.append(h); return 1

class FakeProc:
    def __init__(self, pid=4242): self.pid = pid; self.killed = False; self.waited = False
    def kill(self): self.killed = True
    def wait(self, timeout=None): self.waited = True; return 0

def _popen(pid=4242):
    made = []
    def popen(argv, **kw):
        assert kw["creationflags"] == procs.CREATE_SUSPENDED
        p = FakeProc(pid); made.append(p); return p
    popen.made = made
    return popen

def test_spawn_windows_happy_path_assigns_then_resumes():
    win = FakeWin(); popen = _popen()
    proc, tree = _spawn_windows(["x"], cwd=None, env=None, stdin=None, win=win, popen=popen)
    assert isinstance(tree, _Tree) and tree.job == 100 and tree.proc == 200
    i = win.calls.index
    assert i("AssignProcessToJobObject") < i("ResumeThread")
    assert win.closed == [300, 400]            # snapshot and thread handles; job/proc stay open
    assert proc is popen.made[0] and not proc.killed

@pytest.mark.parametrize("failing", ["CreateJobObjectW", "SetInformationJobObject", "OpenProcess",
                                     "AssignProcessToJobObject", "CreateToolhelp32Snapshot", "OpenThread"])
def test_spawn_windows_failure_terminates_child_and_fails_loudly(failing):
    win = FakeWin(fail=failing); popen = _popen()
    result = _spawn_windows(["x"], cwd=None, env=None, stdin=None, win=win, popen=popen)
    assert isinstance(result, Captured) and result.returncode is None
    assert b"process-tree containment unavailable" in result.output and failing.encode() in result.output
    if popen.made:                                # child existed -> it was terminated, never left suspended
        assert popen.made[0].killed and popen.made[0].waited
    assert 100 in win.closed or failing == "CreateJobObjectW"

@pytest.mark.parametrize("resume", [0, 2, 0xFFFFFFFF])
def test_spawn_windows_requires_resume_count_exactly_one(resume):
    win = FakeWin(resume=resume); popen = _popen()
    result = _spawn_windows(["x"], cwd=None, env=None, stdin=None, win=win, popen=popen)
    assert isinstance(result, Captured) and b"ResumeThread returned" in result.output
    assert popen.made[0].killed

def test_kill_tree_windows_terminates_job_then_closes(monkeypatch):
    win = FakeWin(); monkeypatch.setattr(procs.osfs, "win32", lambda: win)
    _kill_tree(FakeProc(), _Tree(job=100, proc=200), True)
    assert win.calls[-1] == "TerminateJobObject" and win.closed == [100, 200]

def test_kill_tree_posix_uses_killpg(monkeypatch):
    seen = {}
    monkeypatch.setattr(procs, "_kill_group", lambda pid: seen.setdefault("pid", pid))
    _kill_tree(FakeProc(pid=99), None, True)
    assert seen["pid"] == 99

@WINDOWS_ONLY
def test_run_capped_resumes_suspended_child():
    result = procs.run_capped(["python", "-c", "print('hi')"], timeout=20)
    assert result.output.strip() == b"hi" and result.returncode == 0 and result.timed_out is False

@WINDOWS_ONLY
def test_run_capped_kills_grandchild_on_timeout():
    from dirtywork.resume import pid_alive
    import time
    code = ("import subprocess, sys, time; p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
            " print(p.pid, flush=True); time.sleep(60)")
    result = procs.run_capped(["python", "-c", code], timeout=3)
    assert result.timed_out is True
    grandchild = int(result.output.split()[0])
    deadline = time.monotonic() + 2
    while pid_alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert pid_alive(grandchild) is False

Run python3 -m pytest -q -p no:cacheprovider tests/test_procs.py, then the whole suite; all green (the two Windows-only tests skip here). The existing six tests in test_procs.py must be byte-identical to before.
```

- [ ] **Step 2: Run.** `$SCRATCH/run96.sh $SCRATCH/brief-96-w3.md`; ledger row.
- [ ] **Step 3: Review.** `git diff issue-96-windows-tier1 -- dirtywork/procs.py`: the POSIX `Popen` call and `_kill_group` are unchanged; `grep -n 'WinDLL' dirtywork/procs.py` prints nothing; every `_fail(` in `_spawn_windows` lists the handles held at that point (`hJob`, then `hProc, hJob`); `te.dwSize` is set before `Thread32First`; `prev != 1` is the resume check; `test_run_capped_timeout_kills_group` and the other five originals byte-identical (`git diff --stat tests/test_procs.py` shows additions only); full suite green.
- [ ] **Step 4: Resume if needed**, at most twice. **Step 5: Commit**, fast-forward, ledger row.

---

### Task W4: `pid_alive` on Windows (spec §3.3; tests §5 `test_resume`)

**Files:**
- Modify: `dirtywork/resume.py:128-140` (`pid_alive`).
- Test: `tests/test_resume.py` (two tests added after `test_pid_alive`).

**Interfaces:**
- Consumes (`dirtywork.osfs`, W1): `win32()`, `PROCESS_QUERY_LIMITED_INFORMATION`, `ERROR_ACCESS_DENIED`, `STILL_ACTIVE`.
- Produces: `pid_alive(pid, *, win=None) -> bool` — the keyword is for tests; every existing caller passes one positional argument and is unchanged.

- [ ] **Step 1: Brief** `$SCRATCH/brief-96-w4.md`:

```
Issue #96 (Windows tier 1), task W4 of 5. In dirtywork/resume.py, give pid_alive a Windows branch that never sends a console event. Today it does `os.kill(pid, 0)`; on Windows signal 0 IS CTRL_C_EVENT and os.kill broadcasts Ctrl-C to every process on the console -- including the caller. Product callers: runs.py cmd_export and _staleness, resume.py preflight_run_worktree and check_resumable.

1. Add `from . import osfs` and `from .osfs import PROCESS_QUERY_LIMITED_INFORMATION, ERROR_ACCESS_DENIED, STILL_ACTIVE` next to the existing relative import (`from .rundir import read_run_json`). Add `from ctypes import byref, c_uint32`.

2. Replace pid_alive with exactly:

def pid_alive(pid, *, win=None) -> bool:
    """Is `pid` a live process? POSIX: os.kill(pid, 0). Windows: OpenProcess +
    GetExitCodeProcess -- never os.kill, whose signal 0 is CTRL_C_EVENT and
    would Ctrl-C the operator's own console (#96). `win` is for tests."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid, osfs.win32() if win is None else win)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_alive_windows(pid: int, win) -> bool:
    h = win.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return win.get_last_error() == ERROR_ACCESS_DENIED   # exists, not ours -- POSIX PermissionError -> True
    code = c_uint32()
    ok = win.GetExitCodeProcess(h, byref(code))
    win.CloseHandle(h)
    return bool(ok) and code.value == STILL_ACTIVE

3. In tests/test_resume.py, directly after test_pid_alive, add (imports: os, pytest, from dirtywork.resume import _pid_alive_windows, plus what is already imported):

class FakeWin:
    def __init__(self, handle=1, err=0, exit_code=259, ok=1):
        self.handle = handle; self.err = err; self.exit_code = exit_code; self.ok = ok; self.closed = []
    def get_last_error(self): return self.err
    def OpenProcess(self, access, inherit, pid):
        assert access == 0x1000 and inherit is False; return self.handle
    def GetExitCodeProcess(self, h, out):
        out._obj.value = self.exit_code; return self.ok
    def CloseHandle(self, h): self.closed.append(h); return 1

def test_pid_alive_windows_never_sends_console_events(monkeypatch):
    """On Windows os.kill(pid, 0) is a Ctrl-C broadcast; the branch must not call it."""
    monkeypatch.setattr(os, "name", "nt")
    def boom(*a): raise AssertionError("os.kill must not be called on Windows")
    monkeypatch.setattr(os, "kill", boom)
    assert pid_alive(4242, win=FakeWin()) is True
    assert pid_alive(0, win=FakeWin()) is False

@pytest.mark.parametrize("win, expected", [
    (FakeWin(exit_code=259), True),            # STILL_ACTIVE
    (FakeWin(exit_code=0), False),             # exited, handle still openable
    (FakeWin(handle=0, err=5), True),          # ERROR_ACCESS_DENIED: exists, not ours
    (FakeWin(handle=0, err=87), False),        # ERROR_INVALID_PARAMETER: no such process
    (FakeWin(ok=0), False),                    # GetExitCodeProcess failed: fail closed
])
def test_pid_alive_windows_branch(win, expected):
    assert _pid_alive_windows(4242, win) is expected
    if win.handle:
        assert win.closed == [win.handle]

@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_pid_alive_exited_child():
    import subprocess, sys
    p = subprocess.Popen([sys.executable, "-c", "pass"]); p.wait()
    assert pid_alive(p.pid) is False
    assert pid_alive(os.getpid()) is True

Run python3 -m pytest -q -p no:cacheprovider tests/test_resume.py then the whole suite; all green. Do not change any other function.
```

- [ ] **Step 2: Run.** `$SCRATCH/run96.sh $SCRATCH/brief-96-w4.md`; ledger row.
- [ ] **Step 3: Review.** `grep -n 'os\.kill' dirtywork/resume.py` shows only the POSIX branch; the guard order (`isinstance`/`<= 0` before `os.name`) is as briefed; `grep -rn 'pid_alive(' dirtywork/` callers unchanged; full suite green.
- [ ] **Step 4: Resume if needed**, at most twice. **Step 5: Commit**, fast-forward, ledger row.

---

### Task W5: `junit_summary.py --collected` — the receipt can say "incomplete" (spec §3.4; tests §5 `test_junit_summary`)

**Files:**
- Modify: `tools/junit_summary.py` (`main`, new `collected_files`, `absent`, `render_absent`).
- Test: `tests/test_junit_summary.py` (three tests added).

**Interfaces:**
- Produces: `collected_files(text: str) -> set[str]` (from `pytest --collect-only -q` output: `path::name` lines → `path`, backslashes normalised);
  `absent(table: dict, collected: set[str]) -> list[str]` (sorted files in `collected` with no row in `table`);
  `render_absent(missing: list[str]) -> str` (`absent from this run: N test files (a, b)` or `absent from this run: 0`);
  `main` accepts `<junit.xml> [--collected FILE]`.

- [ ] **Step 1: Brief** `$SCRATCH/brief-96-w5.md`:

```
Issue #96 (Windows tier 1), task W5 of 5. tools/junit_summary.py prints a per-file table from a JUnit XML; it cannot say when a pytest session ended early, because it only lists files it saw. Add an optional `--collected FILE` argument: FILE is the output of `python -m pytest --collect-only -q` (lines like `tests/test_a.py::test_x`, then a blank line and a summary line); the script prints, after the table, one line naming every collected test file that has no row in the table. Stdlib only; the script must keep importing neither dirtywork nor pytest; `main()` keeps returning 2 only when it cannot do its own job. Without --collected, output is byte-identical to today.

1. Add after render():

def collected_files(text: str) -> set:
    """Test files named by `pytest --collect-only -q`: every line with `::`
    reduced to its path, backslashes normalised like _file_of. Other lines
    (the blank line, the `N tests collected` summary, warnings) are ignored."""
    files = set()
    for line in text.splitlines():
        line = line.strip()
        if "::" in line:
            files.add(line.split("::", 1)[0].replace("\\", "/"))
    return files


def absent(table: dict, collected: set) -> list:
    """Collected files with no testcase in the report, sorted -- the files a
    truncated session never reached."""
    return sorted(f for f in collected if f not in table)


def render_absent(missing: list) -> str:
    if not missing:
        return "absent from this run: 0"
    return "absent from this run: {} test files ({})".format(len(missing), ", ".join(missing))

2. Change main() to accept `<junit.xml> [--collected FILE]`:
   - args parsing: `if len(args) not in (1, 3) or (len(args) == 3 and args[1] != "--collected"): print usage "usage: junit_summary.py <junit.xml> [--collected <collect-only.txt>]" to stderr; return 2`.
   - after `text = render(table)`: if a collected file was given, read it (OSError -> the same `error: cannot read` message and return 2), compute `line = render_absent(absent(table, collected_files(collected_text)))`, and set `text = text + "\n\n" + line`. Then print and append to GITHUB_STEP_SUMMARY exactly as today (the summary gets the same `text`).

3. Append to tests/test_junit_summary.py (it already has _load() and SAMPLE; add `import os` if missing):

COLLECTED = """tests/test_a.py::test_ok
tests/test_a.py::test_bad
tests\\test_b.py::test_err
tests/test_b.py::test_skip
tests/test_c.py::test_ok2
tests/test_never_ran.py::test_one
tests/test_never_ran.py::test_two

7 tests collected in 0.10s
"""

def test_collected_files_reduces_to_paths():
    m = _load()
    assert m.collected_files(COLLECTED) == {"tests/test_a.py", "tests/test_b.py", "tests/test_c.py", "tests/test_never_ran.py"}

def test_absent_names_collected_files_with_no_rows():
    m = _load()
    table = m.summarize(SAMPLE)
    assert m.absent(table, m.collected_files(COLLECTED)) == ["tests/test_never_ran.py"]
    assert m.render_absent(["tests/test_never_ran.py"]) == "absent from this run: 1 test files (tests/test_never_ran.py)"
    assert m.render_absent([]) == "absent from this run: 0"

def test_main_with_collected_prints_absent_line(tmp_path, capsys, monkeypatch):
    m = _load()
    xml = tmp_path / "j.xml"; xml.write_text(SAMPLE, encoding="utf-8")
    col = tmp_path / "c.txt"; col.write_text(COLLECTED, encoding="utf-8")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert m.main([str(xml), "--collected", str(col)]) == 0
    out = capsys.readouterr().out
    assert out.rstrip().endswith("absent from this run: 1 test files (tests/test_never_ran.py)")
    assert m.main([str(xml)]) == 0
    assert "absent from this run" not in capsys.readouterr().out
    assert m.main([str(xml), "--collected", str(tmp_path / "missing.txt")]) == 2
    assert m.main([str(xml), "--bogus", str(col)]) == 2

Run python3 -m pytest -q -p no:cacheprovider tests/test_junit_summary.py then the whole suite; all green.
```

- [ ] **Step 2: Run.** `$SCRATCH/run96.sh $SCRATCH/brief-96-w5.md`; ledger row.
- [ ] **Step 3: Review.** `python3 tools/junit_summary.py $SCRATCH/junit0/junit-windows.xml --collected <(printf 'tests/test_rundir.py::x\n')` on the host prints the table and `absent from this run: 1 test files (tests/test_rundir.py)`; without `--collected` the output matches `git stash`-ed today's byte for byte; `grep -n 'import' tools/junit_summary.py` shows only `os, sys, xml.etree.ElementTree`; full suite green.
- [ ] **Step 4: Resume if needed**, at most twice. **Step 5: Commit**, fast-forward, ledger row.

---

### Task C1: CI wiring, docs, PR, the Windows receipt (Claude; spec §3.4, §4, §6)

**Files:**
- Modify: `.github/workflows/ci.yml:53-58` (windows job steps), `docs/security.md` (Windows paragraph at the existing note, lines 13-22), `README.md` (support table row), release notes for the release this ships in.

- [ ] **Step 1: ci.yml.** In the `windows-unit` job replace the pytest and summary steps with:

  ```yaml
      - run: python -m pytest --collect-only -q > collected.txt
        continue-on-error: true
      - run: python -m pytest --junitxml=junit-windows.xml -rE
        continue-on-error: true
      - name: Per-file summary
        if: always()
        run: python tools/junit_summary.py junit-windows.xml --collected collected.txt
  ```
  and add `collected.txt` to the artifact's `path` (two lines under `path:`). `gate.needs` is untouched (§8.3). Commit `ci: windows leg records what pytest collected; the summary names absent files`.
- [ ] **Step 2: security.md.** Replace the existing Windows note's last sentence ("no production code is changed for Windows") with a paragraph, in the note's voice: three POSIX-only calls now have Windows branches (`osfs.open_nofollow`, `procs` Job Object, `resume.pid_alive`); what degrades on Windows, one line each — `0o600` create modes advisory (ACL inheritance), any reparse point refused not just symlinks, directories/junctions refused with `EACCES` rather than `EISDIR`/`ELOOP`, `O_NONBLOCK` a no-op, no Windows 7 (job assignment fails loudly); the "items that still need real Windows testing" list stays. Commit `docs(security): what tier 1 changes on Windows and what still degrades`.
- [ ] **Step 3: README.** The `Unsupported | Windows` row: "the unit suite runs on `windows-latest` in CI as an advisory job and, since <version>, runs to completion (the three POSIX-only crashes are fixed — #96); Windows remains unsupported until an integration suite passes (Docker mode there is untested)". Keep the WSL2 note. Commit `docs(README): Windows row after #96 tier 1`.
- [ ] **Step 4: Suites on the host.** In the integration worktree: `python3 -m pytest -q -p no:cacheprovider` (expect baseline + ~30 new); the docker live suite as in #82 C1. Stop the sampler (`tools/soak_sampler.sh --stop`); fill the ledger's metrics, tok/s and RAM lines.
- [ ] **Step 5: PR.** `gh pr create --base main --head issue-96-windows-tier1` titled `#96 tier 1: Windows stops crashing on killpg, O_NOFOLLOW and os.kill(pid, 0); the advisory leg can say "incomplete"`; body lists the receipts (per-run rows, resumes, what Claude finished by hand and why), links the spec and plan. **Do not merge.**
- [ ] **Step 6: The Windows receipt.** Download the PR run's `junit-windows` + `collected.txt` artifacts. Acceptance §6: `absent from this run: 0`; `tests/test_procs.py` all pass; the O_NOFOLLOW group is 0; `test_resume` runs to its end; remaining failures ≤ ~25 and each named under tier 2 in the PR body. **If a Windows branch is wrong** (a W-task test fails on Windows only): write `feedback-96-<task>-r<n>.md` with the CI table and the failing test's traceback pasted in, `dirtywork resume <slug> --feedback-file … --max-turns 40`, fast-forward, push, re-read the table. Two rounds; then Claude fixes by hand and the PR says so.
- [ ] **Step 7: Owner.** Merge and the release that carries it are the owner's call. After the merge: Pages smoke (`docs/security.md` is under `docs/`), and ask the Windows reporter on #96 for the first native `--sandbox none` receipt (§6.7).

---

## Self-review

**Spec coverage.** §3.1 (job, suspended create, resume == 1, fail loudly, no `run_end` field) — W3. §3.2 (`open_nofollow`, two-step create/truncate, verify on the handle, `ELOOP`, `EACCES` for directories, eleven sites, per-site `cloexec`/`nonblock`, `set_blocking` guard) — W1, W2. §3.3 (`pid_alive`, `OpenProcess`, `ERROR_ACCESS_DENIED` → True) — W4. §3.4 (`--collected`, collect step, `-rE`, artifact) — W5, C1. §4 (`security.md` paragraph, README row, symlink-privilege skips) — C1, W1. §5 tests — W1 (`test_flag_mapping`, `test_verify_fails_closed`, symlink/junction/truncate Windows-only, struct sizes), W2 (`test_posix_composition_is_identical`, no-raw-opens), W3 (posix killpg spy, job kill, assign failure, resume count, suspended-resume and grandchild Windows-only), W4 (no console events, branch table, exited child), W5 (three). §6 A1–A7 — C1 steps 4–7. §7 files — every file appears in a task; `runner.py`, `machine-contract.md` untouched. §8 decisions honoured (suspended create; no fallback; advisory; `osfs.py`; worker builds POSIX, CI runs Win32). Appendix A — W1's module, every value confirmed on Microsoft Learn 2026-08-27 (see the spec header note; `SetFileInformationByHandle(FileEndOfFileInfo)` replaces the two-call truncate).

**Placeholder scan.** No TBD/TODO; every code step carries the code; every check is a shell line. `DW_REL`/`DW_IMG` are deliberately unresolved: they are the release current at execution time, set in C0 step 2.

**Type consistency.** `open_nofollow(path, flags, mode=0o600, *, cloexec=False, nonblock=False)` (W1) is what W2's eleven edits and `SITES` call; `_win_open(path, flags, cloexec, win)` (W1) is what `test_osfs` calls with `FakeWin`; `win32()` (W1) is what `procs._spawn_windows` (default `win=None`), `procs._kill_tree` and `resume.pid_alive` call; `_Tree(job, proc)` (W3) is what `_kill_tree` reads; `Captured(returncode, output, truncated, timed_out)` unchanged (W3's `_fail` and `_spawn` build it positionally by keyword); `pid_alive(pid, *, win=None)` (W4) keeps every existing positional call site valid; `collected_files`/`absent`/`render_absent` (W5) are what `main` and the three tests call; the ci.yml step (C1) passes `--collected collected.txt`, the exact form `main` parses.
