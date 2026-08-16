from __future__ import annotations

import io
import stat as st
import tarfile
import time as time_mod
from pathlib import Path

import pytest

from dirtywork.sandbox.export import ExportError, ExportReport, extract_validated


def _make_tar(entries: list) -> io.BytesIO:
    """entries: list of dicts with key "name" and "type" ("file"|"dir"|
    "symlink"|"hardlink"|"fifo"|"chardev"), plus "content" (bytes, for
    files), "linkname" (for symlink/hardlink), and "mode" (int, default
    0o644 for files / 0o755 for dirs). Returns a BytesIO positioned at 0."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for e in entries:
            info = tarfile.TarInfo(e["name"])
            kind = e["type"]
            if kind == "file":
                data = e.get("content", b"")
                info.mode = e.get("mode", 0o644)
                info.size = len(data)
                info.type = tarfile.REGTYPE
                tar.addfile(info, io.BytesIO(data))
            elif kind == "dir":
                info.mode = e.get("mode", 0o755)
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = e["linkname"]
                tar.addfile(info)
            elif kind == "hardlink":
                info.type = tarfile.LNKTYPE
                info.linkname = e["linkname"]
                tar.addfile(info)
            elif kind == "fifo":
                info.type = tarfile.FIFOTYPE
                tar.addfile(info)
            elif kind == "chardev":
                info.type = tarfile.CHRTYPE
                info.devmajor = 1
                info.devminor = 3
                tar.addfile(info)
            else:
                raise ValueError(f"unknown type {kind!r}")
    buf.seek(0)
    return buf


@pytest.fixture()
def empty_worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /somewhere\n")
    return wt


def test_extract_validated_refuses_when_dest_not_empty(empty_worktree):
    (empty_worktree / "leftover.txt").write_text("stray")
    stream = _make_tar([{"name": "a.txt", "type": "file", "content": b"hi"}])
    with pytest.raises(ExportError, match="not empty"):
        extract_validated(stream, empty_worktree, max_files=100, max_bytes=1_000_000)


def test_extract_validated_refuses_when_dot_git_missing(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    stream = _make_tar([{"name": "a.txt", "type": "file", "content": b"hi"}])
    with pytest.raises(ExportError, match="not empty"):
        extract_validated(stream, wt, max_files=100, max_bytes=1_000_000)


def test_extract_validated_normal_files_and_dirs(empty_worktree):
    stream = _make_tar([
        {"name": "src", "type": "dir"},
        {"name": "src/app.py", "type": "file", "content": b"print(1)\n"},
        {"name": "README.md", "type": "file", "content": b"# hi\n"},
    ])
    report = extract_validated(stream, empty_worktree, max_files=100, max_bytes=1_000_000)
    assert report.files == 3
    assert (empty_worktree / "src" / "app.py").read_bytes() == b"print(1)\n"
    assert (empty_worktree / "README.md").read_bytes() == b"# hi\n"
    assert report.escaping_symlinks == []


def test_extract_validated_reports_escaping_absolute_symlink(empty_worktree):
    stream = _make_tar([{"name": "esc", "type": "symlink", "linkname": "/etc/passwd"}])
    report = extract_validated(stream, empty_worktree, max_files=100, max_bytes=1_000_000)
    assert (empty_worktree / "esc").is_symlink()
    assert "esc" in report.escaping_symlinks


def test_extract_validated_reports_escaping_relative_symlink(empty_worktree):
    stream = _make_tar([{"name": "rel", "type": "symlink", "linkname": "../../../outside"}])
    report = extract_validated(stream, empty_worktree, max_files=100, max_bytes=1_000_000)
    assert (empty_worktree / "rel").is_symlink()
    assert "rel" in report.escaping_symlinks


def test_extract_validated_does_not_report_non_escaping_symlink(empty_worktree):
    stream = _make_tar([
        {"name": "a.txt", "type": "file", "content": b"hi"},
        {"name": "link", "type": "symlink", "linkname": "a.txt"},
    ])
    report = extract_validated(stream, empty_worktree, max_files=100, max_bytes=1_000_000)
    assert report.escaping_symlinks == []


def test_extract_validated_dotdot_prefixed_name_is_not_escaping(empty_worktree):
    # "..hidden" is a legal file name, not a parent reference
    stream = _make_tar([
        {"name": "..hidden", "type": "file", "content": b"x"},
        {"name": "link", "type": "symlink", "linkname": "..hidden"},
    ])
    report = extract_validated(stream, empty_worktree, max_files=100, max_bytes=1_000_000)
    assert report.escaping_symlinks == []


def test_extract_validated_refuses_write_through_earlier_symlink(empty_worktree):
    stream = _make_tar([
        {"name": "a", "type": "symlink", "linkname": "/etc"},
        {"name": "a/x", "type": "file", "content": b"pwned"},
    ])
    with pytest.raises(ExportError, match="escapes"):
        extract_validated(stream, empty_worktree, max_files=100, max_bytes=1_000_000)
    remaining = list(empty_worktree.iterdir())
    assert len(remaining) == 1 and remaining[0].name == ".git"


def test_extract_validated_refuses_dot_git_component_case_insensitive(empty_worktree):
    stream = _make_tar([{"name": "sub/.Git/h", "type": "file", "content": b"x"}])
    with pytest.raises(ExportError, match=r"\.git"):
        extract_validated(stream, empty_worktree, max_files=100, max_bytes=1_000_000)


def test_extract_validated_refuses_hardlink(empty_worktree):
    stream = _make_tar([
        {"name": "a.txt", "type": "file", "content": b"hi"},
        {"name": "b.txt", "type": "hardlink", "linkname": "a.txt"},
    ])
    with pytest.raises(ExportError, match="disallowed member type"):
        extract_validated(stream, empty_worktree, max_files=100, max_bytes=1_000_000)


def test_extract_validated_refuses_fifo(empty_worktree):
    stream = _make_tar([{"name": "pipe", "type": "fifo"}])
    with pytest.raises(ExportError, match="disallowed member type"):
        extract_validated(stream, empty_worktree, max_files=100, max_bytes=1_000_000)


def test_extract_validated_refuses_device(empty_worktree):
    stream = _make_tar([{"name": "dev0", "type": "chardev"}])
    with pytest.raises(ExportError, match="disallowed member type"):
        extract_validated(stream, empty_worktree, max_files=100, max_bytes=1_000_000)


def test_extract_validated_refuses_over_file_count_cap(empty_worktree):
    entries = [{"name": f"f{i}.txt", "type": "file", "content": b"x"} for i in range(5)]
    stream = _make_tar(entries)
    with pytest.raises(ExportError, match="files"):
        extract_validated(stream, empty_worktree, max_files=3, max_bytes=1_000_000)


def test_extract_validated_refuses_over_byte_cap(empty_worktree):
    stream = _make_tar([{"name": "big.bin", "type": "file", "content": b"x" * 10_000}])
    with pytest.raises(ExportError, match="bytes"):
        extract_validated(stream, empty_worktree, max_files=100, max_bytes=1000)


def test_extract_validated_refuses_absolute_path(empty_worktree):
    stream = _make_tar([{"name": "/etc/passwd", "type": "file", "content": b"x"}])
    with pytest.raises(ExportError, match="absolute"):
        extract_validated(stream, empty_worktree, max_files=100, max_bytes=1_000_000)


def test_extract_validated_refuses_dotdot_component(empty_worktree):
    stream = _make_tar([{"name": "../outside.txt", "type": "file", "content": b"x"}])
    with pytest.raises(ExportError, match="invalid path component"):
        extract_validated(stream, empty_worktree, max_files=100, max_bytes=1_000_000)


def test_extract_validated_refuses_pax_global_header(empty_worktree):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT,
                       pax_headers={"comment": "hostile global header"}) as tar:
        info = tarfile.TarInfo("normal.txt")
        data = b"hello"
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    with pytest.raises(ExportError, match="PAX global header"):
        extract_validated(buf, empty_worktree, max_files=100, max_bytes=1_000_000)


def test_extract_validated_accepts_per_member_pax_headers_for_long_paths(empty_worktree):
    # Test for binding addition R5: per-member PAX headers should be accepted
    buf = io.BytesIO()
    long_path = "a" * 60 + "/" + "b" * 60 + ".txt"
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        info = tarfile.TarInfo(long_path)
        data = b"ok"
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    report = extract_validated(buf, empty_worktree, max_files=100, max_bytes=1_000_000)
    assert report.files == 1
    assert (empty_worktree / long_path).exists()
    assert (empty_worktree / long_path).read_bytes() == b"ok"


def test_extract_validated_normalizes_modes(empty_worktree):
    stream = _make_tar([
        {"name": "dir1", "type": "dir", "mode": 0o777},
        {"name": "plain.txt", "type": "file", "content": b"x", "mode": 0o600},
        {"name": "script.sh", "type": "file", "content": b"#!/bin/sh\n", "mode": 0o755},
    ])
    extract_validated(stream, empty_worktree, max_files=100, max_bytes=1_000_000)
    assert st.S_IMODE((empty_worktree / "dir1").stat().st_mode) == 0o755
    assert st.S_IMODE((empty_worktree / "plain.txt").stat().st_mode) == 0o644
    assert st.S_IMODE((empty_worktree / "script.sh").stat().st_mode) == 0o755


def test_extract_validated_ignores_archive_mtime(empty_worktree):
    stream = _make_tar([{"name": "old.txt", "type": "file", "content": b"x"}])
    before = time_mod.time()
    extract_validated(stream, empty_worktree, max_files=100, max_bytes=1_000_000)
    after = time_mod.time()
    mtime = (empty_worktree / "old.txt").stat().st_mtime
    assert before - 5 <= mtime <= after + 5  # extraction time, not an archive-supplied mtime


def test_extract_validated_cleanup_on_failure_leaves_only_dot_git(empty_worktree):
    stream = _make_tar([
        {"name": "good.txt", "type": "file", "content": b"fine"},
        {"name": "pipe", "type": "fifo"},
    ])
    with pytest.raises(ExportError):
        extract_validated(stream, empty_worktree, max_files=100, max_bytes=1_000_000)
    remaining = list(empty_worktree.iterdir())
    assert len(remaining) == 1
    assert remaining[0].name == ".git"
