import errno
import os
import pytest
from dirtywork import osfs
from dirtywork.osfs import open_nofollow, _win_open_params, _win_open, INVALID_HANDLE_VALUE, ERROR_FILE_EXISTS, CREATE_NEW, OPEN_EXISTING, OPEN_ALWAYS, GENERIC_READ, GENERIC_WRITE, FILE_APPEND_DATA, FILE_ATTRIBUTE_REPARSE_POINT

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Windows only")


class FakeWin:
    """A stand-in for osfs.win32(): records calls, returns what the test says.
    `tag_attrs` is what GetFileInformationByHandleEx reports; `fail` names the
    call that should return failure; `exists` makes CREATE_NEW fail with
    ERROR_FILE_EXISTS once."""
    def __init__(self, tag_attrs=0, fail=None, exists=False, fail_code=5):
        self.calls = []
        self.tag_attrs = tag_attrs
        self.fail = fail
        self.exists = exists
        self.fail_code = fail_code
        self.closed = []
        self._err = 0
        self.handle = 1234

    def get_last_error(self):
        return self._err

    def FormatError(self, code):
        return f"fake error {code}"

    def CreateFileW(self, path, access, share, sa, disposition, attrs, tmpl):
        self.calls.append(("CreateFileW", disposition, access))
        if self.exists and disposition == CREATE_NEW:
            self.exists = False
            self._err = ERROR_FILE_EXISTS
            return INVALID_HANDLE_VALUE
        if self.fail == "CreateFileW":
            self._err = self.fail_code
            return INVALID_HANDLE_VALUE
        return self.handle

    def GetFileInformationByHandleEx(self, h, cls, buf, size):
        self.calls.append(("GetFileInformationByHandleEx", h))
        if self.fail == "GetFileInformationByHandleEx":
            self._err = 87
            return 0
        buf._obj.FileAttributes = self.tag_attrs
        return 1

    def SetFileInformationByHandle(self, h, cls, buf, size):
        self.calls.append(("SetFileInformationByHandle", h, cls))
        if self.fail == "SetFileInformationByHandle":
            self._err = 5
            return 0
        return 1

    def CloseHandle(self, h):
        self.closed.append(h)
        return 1


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
    def boom(h, f):
        raise OSError(errno.EBADF, "bad handle")
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


def test_path_not_found_maps_enoent_as_filenotfounderror(monkeypatch):
    import types, sys
    monkeypatch.setitem(sys.modules, "msvcrt", types.SimpleNamespace(open_osfhandle=lambda h, f: 7))
    win = FakeWin(fail="CreateFileW", fail_code=3)          # ERROR_PATH_NOT_FOUND: the parent directory is missing
    with pytest.raises(FileNotFoundError) as ei:
        _win_open("nodir/p", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, False, win)
    assert ei.value.errno == errno.ENOENT


def test_create_trunc_on_symlink_is_refused_before_any_truncate(monkeypatch):
    """O_CREAT|O_TRUNC where a FILE SYMLINK already exists: CREATE_NEW fails (exists),
    OPEN_EXISTING opens the link itself, verify sees the reparse point -> ELOOP, and
    SetFileInformationByHandle never runs -- the target is untouched."""
    import types, sys
    monkeypatch.setitem(sys.modules, "msvcrt", types.SimpleNamespace(open_osfhandle=lambda h, f: 7))
    win = FakeWin(exists=True, tag_attrs=FILE_ATTRIBUTE_REPARSE_POINT)
    with pytest.raises(OSError) as ei:
        _win_open("p", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, False, win)
    assert ei.value.errno == errno.ELOOP
    assert not any(c[0] == "SetFileInformationByHandle" for c in win.calls)
    assert win.closed == [win.handle]


@pytest.mark.skipif(os.name == "nt", reason="POSIX composition")
def test_posix_refuses_file_symlink(tmp_path):
    target = tmp_path / "t"
    target.write_text("x")
    link = tmp_path / "l"
    os.symlink(target, link)
    with pytest.raises(OSError) as ei:
        open_nofollow(link, os.O_WRONLY)
    assert ei.value.errno == errno.ELOOP
    fd = open_nofollow(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.close(fd)
    assert target.read_text() == ""


@pytest.mark.skipif(os.name == "nt", reason="POSIX composition")
def test_posix_keywords_add_only_the_requested_flags(monkeypatch):
    seen = {}

    def fake_open(path, flags, mode):
        seen["flags"] = flags
        seen["mode"] = mode
        return 3

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
    target = tmp_path / "t"
    target.write_text("x")
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
def test_windows_create_trunc_on_file_symlink_leaves_target(tmp_path):
    target = tmp_path / "t"
    target.write_text("keep")
    link = tmp_path / "l"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink privilege not available")
    with pytest.raises(OSError) as ei:
        open_nofollow(link, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    assert ei.value.errno == errno.ELOOP
    assert target.read_text() == "keep"


@WINDOWS_ONLY
def test_windows_missing_parent_is_filenotfounderror(tmp_path):
    with pytest.raises(FileNotFoundError):
        open_nofollow(tmp_path / "nodir" / "f", os.O_WRONLY | os.O_CREAT | os.O_TRUNC)


@WINDOWS_ONLY
def test_windows_refuses_junction_with_eacces(tmp_path):
    import _winapi
    d = tmp_path / "d"
    d.mkdir()
    j = tmp_path / "j"
    _winapi.CreateJunction(str(d), str(j))
    with pytest.raises(OSError) as ei:
        open_nofollow(j, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    assert ei.value.errno == errno.EACCES


@WINDOWS_ONLY
def test_windows_create_trunc_truncates_through_verified_handle(tmp_path):
    p = tmp_path / "f"
    p.write_text("hello")
    fd = open_nofollow(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    os.close(fd)
    assert p.stat().st_size == 0
    fd = open_nofollow(tmp_path / "new", os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    os.write(fd, b"a")
    os.close(fd)
    assert (tmp_path / "new").read_bytes() == b"a"
