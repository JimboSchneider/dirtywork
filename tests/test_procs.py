from __future__ import annotations

import os
import subprocess
import time

import pytest

from dirtywork import procs
from dirtywork.procs import Captured, MAX_CAPTURE_BYTES, run_capped, _spawn_windows, _kill_tree, _Tree, _fail


def test_run_capped_returns_output_and_returncode():
    result = run_capped(["bash", "-c", "echo hi; exit 3"], timeout=5)
    assert isinstance(result, Captured)
    assert result.returncode == 3
    assert result.output.strip() == b"hi"
    assert result.truncated is False
    assert result.timed_out is False


def test_run_capped_caps_output():
    result = run_capped(
        ["python3", "-c", "import sys; sys.stdout.write('A' * 2_000_000)"],
        timeout=10, cap=1024,
    )
    assert len(result.output) <= 1024
    assert result.truncated is True
    assert result.returncode == 0


def test_run_capped_timeout_kills_group():
    start = time.monotonic()
    result = run_capped(
        ["bash", "-c", "(sleep 2 && touch /tmp/dirtywork_procs_survived) & wait"],
        timeout=1,
    )
    assert result.timed_out is True
    assert result.returncode is None
    elapsed = time.monotonic() - start
    assert elapsed < 3.0


def test_run_capped_passes_stdin_bytes():
    result = run_capped(["cat"], timeout=5, stdin=b"from stdin\n")
    assert result.output == b"from stdin\n"


def test_run_capped_respects_cwd_and_env():
    result = run_capped(["bash", "-c", "pwd && echo $MY_VAR"], timeout=5,
                         cwd="/tmp", env={"MY_VAR": "hello", "PATH": "/usr/bin:/bin"})
    assert b"/tmp" in result.output
    assert b"hello" in result.output


def test_run_capped_stdin_is_devnull_by_default():
    result = run_capped(["bash", "-c", "read x; echo got:$x"], timeout=5)
    assert result.output.strip() == b"got:"
    assert result.timed_out is False


# Windows-only tests below -----------------------------------------------------

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Windows only")


class FakeWin:
    """Stand-in for osfs.win32(). `fail` names the call that returns failure
    (error 5); `resume` is what ResumeThread returns; `pid` is the owner pid the
    snapshot reports (a mismatch = list exhaustion); `thread_fail` makes
    Thread32First fail with that error code; `terminate_fail` / `close_fail`
    make TerminateJobObject / CloseHandle fail with error 5."""
    def __init__(self, fail=None, resume=1, pid=4242, thread_fail=None, terminate_fail=False, close_fail=False):
        self.fail = fail; self.resume = resume; self.pid = pid; self.thread_fail = thread_fail
        self.terminate_fail = terminate_fail; self.close_fail = close_fail
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
    def CreateToolhelp32Snapshot(self, f, p):
        self.calls.append("CreateToolhelp32Snapshot")
        if self.fail == "CreateToolhelp32Snapshot":
            self._err = 5
            return procs.INVALID_HANDLE_VALUE
        return 300
    def Thread32First(self, s, te):
        self.calls.append("Thread32First")
        if self.thread_fail is not None:
            self._err = self.thread_fail; return 0
        te._obj.th32OwnerProcessID = self.pid; te._obj.th32ThreadID = 7; return 1
    def Thread32Next(self, s, te):
        self.calls.append("Thread32Next"); self._err = 18; return 0     # ERROR_NO_MORE_FILES
    def OpenThread(self, a, i, tid): return self._f("OpenThread", 400)
    def ResumeThread(self, h): self.calls.append("ResumeThread"); return self.resume
    def TerminateJobObject(self, j, c):
        self.calls.append("TerminateJobObject")
        if self.terminate_fail:
            self._err = 5; return 0
        return 1
    def CloseHandle(self, h):
        self.closed.append(h)
        if self.close_fail:
            self._err = 5; return 0
        return 1


class FakeProc:
    def __init__(self, pid=4242, kill_raises=False, wait_timeout=False):
        self.pid = pid; self.killed = False; self.waited = False
        self.kill_raises = kill_raises; self.wait_timeout = wait_timeout
    def kill(self):
        if self.kill_raises: raise OSError(5, "access denied")
        self.killed = True
    def wait(self, timeout=None):
        if self.wait_timeout: raise subprocess.TimeoutExpired("x", timeout)
        self.waited = True; return 0


def _popen(pid=4242, **flags):
    made = []
    def popen(argv, **kw):
        assert kw["creationflags"] == procs.CREATE_SUSPENDED
        p = FakeProc(pid, **flags); made.append(p); return p
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
    assert "TerminateJobObject" not in win.calls


@pytest.mark.parametrize("failing", ["CreateJobObjectW", "SetInformationJobObject", "OpenProcess",
                                     "AssignProcessToJobObject", "CreateToolhelp32Snapshot", "OpenThread"])
def test_spawn_windows_failure_terminates_child_and_fails_loudly(failing):
    win = FakeWin(fail=failing); popen = _popen()
    result = _spawn_windows(["x"], cwd=None, env=None, stdin=None, win=win, popen=popen)
    assert isinstance(result, Captured) and result.returncode is None
    assert b"process-tree containment unavailable" in result.output and failing.encode() in result.output
    assert b"[WinError 5]" in result.output
    if popen.made:                                # child existed -> terminated AND waited, never left suspended
        assert popen.made[0].killed and popen.made[0].waited
    if failing in ("CreateToolhelp32Snapshot", "OpenThread"):   # child was in the job -> the job was killed
        assert "TerminateJobObject" in win.calls
    else:
        assert "TerminateJobObject" not in win.calls
    if failing != "CreateJobObjectW":
        assert 100 in win.closed


def test_spawn_windows_thread_not_found_vs_toolhelp_failure():
    win = FakeWin(pid=1); popen = _popen(pid=4242)                       # snapshot never lists our pid: exhaustion
    r = _spawn_windows(["x"], cwd=None, env=None, stdin=None, win=win, popen=popen)
    assert b"thread not found in snapshot" in r.output and b"[WinError 18]" in r.output
    assert popen.made[0].killed and "TerminateJobObject" in win.calls
    win = FakeWin(thread_fail=5); popen = _popen()                        # Thread32First itself failed
    r = _spawn_windows(["x"], cwd=None, env=None, stdin=None, win=win, popen=popen)
    assert b"Thread32First/Thread32Next" in r.output and b"[WinError 5]" in r.output
    assert popen.made[0].killed


@pytest.mark.parametrize("resume", [0, 2, 0xFFFFFFFF])
def test_spawn_windows_requires_resume_count_exactly_one(resume):
    win = FakeWin(resume=resume); popen = _popen()
    result = _spawn_windows(["x"], cwd=None, env=None, stdin=None, win=win, popen=popen)
    assert isinstance(result, Captured) and b"ResumeThread returned" in result.output
    assert popen.made[0].killed and "TerminateJobObject" in win.calls


def test_fail_reports_unconfirmed_termination():
    win = FakeWin(); p = FakeProc(wait_timeout=True)
    r = _fail(win, "x", err=5, kill=p, close=[100], job=100)
    assert b"NOT confirmed dead" in r.output and win.calls == ["TerminateJobObject"] and win.closed == [100]
    r2 = _fail(FakeWin(), "x", err=5, kill=FakeProc(kill_raises=True))
    assert b"TerminateProcess failed" in r2.output
    r3 = _fail(FakeWin(terminate_fail=True, close_fail=True), "x", err=5, kill=FakeProc(), close=[100], job=100)
    assert b"TerminateJobObject failed" in r3.output and b"CloseHandle(100) failed" in r3.output


def test_kill_tree_windows_terminates_job_then_closes(monkeypatch):
    win = FakeWin(); monkeypatch.setattr(procs.osfs, "win32", lambda: win)
    assert _kill_tree(FakeProc(), _Tree(job=100, proc=200), True) is None
    assert win.calls[-1] == "TerminateJobObject" and win.closed == [100, 200]


def test_kill_tree_windows_reports_terminate_failure(monkeypatch):
    win = FakeWin(terminate_fail=True); monkeypatch.setattr(procs.osfs, "win32", lambda: win)
    p = FakeProc()
    trailer = _kill_tree(p, _Tree(job=100, proc=200), True)
    assert p.killed and "TerminateJobObject failed" in trailer and win.closed == [100, 200]


def test_run_capped_appends_kill_trailer(monkeypatch):
    monkeypatch.setattr(procs, "_kill_tree", lambda proc, tree, kg: "process-tree kill: boom")
    r = procs.run_capped(["bash", "-c", "echo hi"], timeout=5)
    assert r.output.startswith(b"hi") and b"[dirtywork] process-tree kill: boom" in r.output


def test_kill_tree_posix_uses_killpg(monkeypatch):
    seen = {}
    monkeypatch.setattr(procs, "_kill_group", lambda pid: seen.setdefault("pid", pid))
    assert _kill_tree(FakeProc(pid=99), None, True) is None
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
    assert result.timed_out is True and b"[dirtywork]" not in result.output
    grandchild = int(result.output.split()[0])
    deadline = time.monotonic() + 2
    while pid_alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert pid_alive(grandchild) is False
