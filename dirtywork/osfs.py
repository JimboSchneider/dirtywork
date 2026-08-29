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
from ctypes import Structure, byref, c_int, c_size_t, c_uint, c_ulonglong, sizeof

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
ERROR_PATH_NOT_FOUND = 3
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

_WINERRNO = {ERROR_FILE_NOT_FOUND: errno.ENOENT, ERROR_PATH_NOT_FOUND: errno.ENOENT,
             ERROR_ACCESS_DENIED: errno.EACCES,
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
                ("th32OwnerProcessID", ctypes.c_uint32), ("tpBasePri", ctypes.c_int32), ("tpDeltaPri", ctypes.c_int32),   # LONG is 4 bytes on Windows; c_long is 8 on LP64 POSIX, where these tests also run
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
    so callers' `except OSError as e: if e.errno == ...` branches keep working.
    OSError(errno, ...) picks the subclass itself (ENOENT -> FileNotFoundError,
    EEXIST -> FileExistsError, EACCES -> PermissionError), so a call site's
    `except FileNotFoundError` keeps working too."""
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
