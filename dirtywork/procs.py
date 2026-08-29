from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from . import osfs
from .osfs import (CREATE_SUSPENDED, ERROR_NO_MORE_FILES, INVALID_HANDLE_VALUE, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
                   JobObjectExtendedLimitInformation, PROCESS_SET_QUOTA, PROCESS_TERMINATE,
                   TH32CS_SNAPTHREAD, THREAD_SUSPEND_RESUME, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
                   THREADENTRY32)
from ctypes import byref, sizeof

MAX_CAPTURE_BYTES = 1024 * 1024


@dataclass
class Captured:
    returncode: int | None
    output: bytes
    truncated: bool
    timed_out: bool


def _kill_group(pid: int) -> None:
    """SIGKILL the whole process group led by pid (a no-op if already gone).

    On the clean-exit path pid is already reaped, so there is a negligible
    PID-reuse window; it would only signal an unrelated process that had both
    become a group leader AND reclaimed this exact pgid, which we accept.
    """
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


@dataclass
class _Tree:
    """The two handles the Windows branch holds for a child: its job and the
    process handle used to assign it. None on POSIX."""
    job: int
    proc: int


def _fail(win, what: str, *, err=None, kill=None, close=(), job=None) -> Captured:
    """The one failure path of _spawn_windows: read the error code BEFORE any
    cleanup call (CloseHandle clobbers it); kill the JOB when the child is
    already in it (else the child itself); CONFIRM the child is gone with a
    bounded wait; close the handles; return the same shape run_capped returns
    when Popen itself raises OSError. Anything that could not be confirmed
    -- a kill that failed, a wait that timed out, a close that failed -- is
    named in the message. Nothing is ever left silently running."""
    err = win.get_last_error() if err is None else err
    notes = []
    if job is not None and not win.TerminateJobObject(job, 1):
        notes.append(f"TerminateJobObject failed [WinError {win.get_last_error()}]")
    if kill is not None:
        try:
            kill.kill()                                   # TerminateProcess; harmless after the job kill
        except OSError as e:
            notes.append(f"TerminateProcess failed: {e}")
        try:
            kill.wait(timeout=5)
        except subprocess.TimeoutExpired:
            notes.append(f"child pid {kill.pid} NOT confirmed dead after 5s")
    for h in close:
        if not win.CloseHandle(h):
            notes.append(f"CloseHandle({h}) failed [WinError {win.get_last_error()}]")
    msg = f"process-tree containment unavailable: {what}: [WinError {err}] {win.FormatError(err)}"
    if notes:
        msg += "; " + "; ".join(notes)
    return Captured(returncode=None, output=msg.encode(), truncated=False, timed_out=False)


def _spawn_windows(argv, *, cwd, env, stdin, win=None, popen=subprocess.Popen):
    """Create the child suspended, put it in a job that kills every descendant
    when the job dies, then resume its one initial thread -- so containment
    holds from the child's first instruction (spec §3.1). Every Win32 return
    is checked; any failure goes through _fail. Once AssignProcessToJobObject
    has succeeded, every _fail passes job=hJob so the JOB is killed."""
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
    # From here the child is in the job: failures kill the job (job=hJob), not just the child.
    snap = win.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap == INVALID_HANDLE_VALUE:
        return _fail(win, "CreateToolhelp32Snapshot", kill=proc, close=[hProc, hJob], job=hJob)
    te = THREADENTRY32()
    te.dwSize = sizeof(THREADENTRY32)           # required, or Thread32First fails
    tid = None
    ok = win.Thread32First(snap, byref(te))
    while ok:
        if te.th32OwnerProcessID == proc.pid:
            tid = te.th32ThreadID
            break
        ok = win.Thread32Next(snap, byref(te))
    code = win.get_last_error()                 # BEFORE CloseHandle(snap): exhaustion is ERROR_NO_MORE_FILES,
    win.CloseHandle(snap)                       # anything else is a Thread32First/Next failure
    if tid is None:
        what = "thread not found in snapshot" if code == ERROR_NO_MORE_FILES else "Thread32First/Thread32Next"
        return _fail(win, what, err=code, kill=proc, close=[hProc, hJob], job=hJob)
    hThread = win.OpenThread(THREAD_SUSPEND_RESUME, False, tid)
    if not hThread:
        return _fail(win, "OpenThread", kill=proc, close=[hProc, hJob], job=hJob)
    prev = win.ResumeThread(hThread)             # previous suspend count; 0xFFFFFFFF on failure
    err = win.get_last_error()
    win.CloseHandle(hThread)
    if prev != 1:
        return _fail(win, f"ResumeThread returned {prev}", err=err, kill=proc, close=[hProc, hJob], job=hJob)
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


def _kill_tree(proc, tree, kill_group: bool):
    """Unconditional, clean exit included -- as today. Returns None, or a
    string run_capped appends to the output describing a kill or close that
    failed, so a Windows kill that did not happen is never silent. POSIX
    always returns None (byte-identical behaviour)."""
    if tree is not None:
        win = osfs.win32()
        notes = []
        if not win.TerminateJobObject(tree.job, 1):      # replaces _kill_group(proc.pid)
            notes.append(f"TerminateJobObject failed [WinError {win.get_last_error()}]")
            try:
                proc.kill()                                # fall back to the child alone
            except OSError as e:
                notes.append(f"TerminateProcess failed: {e}")
        for h in (tree.job, tree.proc):                    # KILL_ON_JOB_CLOSE: belt to TerminateJobObject's braces
            if not win.CloseHandle(h):
                notes.append(f"CloseHandle({h}) failed [WinError {win.get_last_error()}]")
        return "process-tree kill: " + "; ".join(notes) if notes else None
    if kill_group:
        _kill_group(proc.pid)
    else:
        try:
            proc.kill()
        except OSError:
            pass
    return None


def run_capped(argv: list[str], *, timeout: float, cwd=None, env=None,
               stdin: bytes | None = None, cap: int = MAX_CAPTURE_BYTES,
               kill_group: bool = True) -> Captured:
    """Run argv, capturing merged stdout+stderr up to `cap` bytes without
    buffering the whole stream in memory (a drain thread keeps the child's
    pipe from filling, even past the cap), and enforce `timeout` by killing
    the whole process group (POSIX) or job (Windows) so backgrounded children cannot outlive the call.
    """
    spawned = _spawn(argv, cwd=cwd, env=env, stdin=stdin, kill_group=kill_group)
    if isinstance(spawned, Captured):
        return spawned
    proc, tree = spawned

    captured = bytearray()
    truncated = False
    lock = threading.Lock()

    def _drain() -> None:
        nonlocal truncated
        with proc.stdout:  # type: ignore[union-attr]
            for chunk in iter(lambda: proc.stdout.read(65536), b""):  # type: ignore[union-attr]
                with lock:
                    room = cap - len(captured)
                    if room > 0:
                        captured.extend(chunk[:room])
                    if len(chunk) > room:
                        truncated = True  # keep draining so the child never blocks

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()

    if stdin is not None:
        def _feed() -> None:
            try:
                proc.stdin.write(stdin)  # type: ignore[union-attr]
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    proc.stdin.close()  # type: ignore[union-attr]
                except OSError:
                    pass

        writer = threading.Thread(target=_feed, daemon=True)
        writer.start()
    else:
        writer = None

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True

    trailer = _kill_tree(proc, tree, kill_group)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if tree is not None:                                   # POSIX: unchanged (pass)
            note = f"child pid {proc.pid} NOT confirmed dead after 5s"
            trailer = f"{trailer}; {note}" if trailer else f"process-tree kill: {note}"
    reader.join(timeout=5)
    if writer is not None:
        writer.join(timeout=5)

    with lock:
        out = bytes(captured)
        trunc = truncated
    if trailer:
        out = out + b"\n[dirtywork] " + trailer.encode()
    returncode = None if timed_out else proc.returncode
    return Captured(returncode=returncode, output=out, truncated=trunc, timed_out=timed_out)
