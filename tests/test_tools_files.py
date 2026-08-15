from __future__ import annotations

from pathlib import Path

import pytest

from dirtywork import tools


@pytest.fixture()
def wt(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def main():\n    return 42\n")
    (tmp_path / "README.md").write_text("# Demo\n")
    return tmp_path


def test_read_file_numbers_lines(wt: Path):
    out = tools.read_file(wt, "src/app.py")
    assert "     1\tdef main():" in out
    assert "     2\t    return 42" in out


def test_read_file_offset_limit(wt: Path):
    out = tools.read_file(wt, "src/app.py", offset=1, limit=1)
    assert "def main" not in out
    assert "return 42" in out


def test_read_file_missing_is_error_string(wt: Path):
    out = tools.read_file(wt, "nope.py")
    assert out.startswith("ERROR:")


def test_read_file_escape_is_error_string(wt: Path):
    out = tools.read_file(wt, "../../etc/passwd")
    assert out.startswith("ERROR:")


def test_write_file_creates_parents(wt: Path):
    out = tools.write_file(wt, "deep/new/file.txt", "hello")
    assert (wt / "deep" / "new" / "file.txt").read_text() == "hello"
    assert "Wrote 5 bytes" in out


def test_write_file_git_blocked(wt: Path):
    (wt / ".git").mkdir()
    out = tools.write_file(wt, ".git/hooks/pre-commit", "#!/bin/sh")
    assert out.startswith("ERROR:")


def test_edit_file_unique_match(wt: Path):
    out = tools.edit_file(wt, "src/app.py", "return 42", "return 43")
    assert "Edited" in out
    assert "return 43" in (wt / "src" / "app.py").read_text()


def test_edit_file_no_match(wt: Path):
    out = tools.edit_file(wt, "src/app.py", "not here", "x")
    assert out.startswith("ERROR:") and "0 times" in out


def test_edit_file_multiple_matches(wt: Path):
    (wt / "dup.txt").write_text("aa\naa\n")
    out = tools.edit_file(wt, "dup.txt", "aa", "bb")
    assert out.startswith("ERROR:") and "2 times" in out


def test_edit_file_binary_not_utf8(wt: Path):
    (wt / "bin.dat").write_bytes(b"\xff\xfe\x00\x01")
    out = tools.edit_file(wt, "bin.dat", "aa", "bb")
    assert out.startswith("ERROR:")
    assert "UTF-8" in out


def test_list_dir(wt: Path):
    out = tools.list_dir(wt, ".")
    assert "src/" in out
    assert "README.md" in out


def test_grep_finds_pattern(wt: Path):
    out = tools.grep(wt, "return 42")
    assert "app.py" in out


def test_grep_no_match(wt: Path):
    out = tools.grep(wt, "zzz_not_present")
    assert "No matches" in out


def test_result_cap(wt: Path):
    (wt / "big.txt").write_text("x" * 50000)
    out = tools.read_file(wt, "big.txt")
    assert len(out) <= tools.MAX_RESULT_CHARS + 200  # cap + truncation note
    assert "truncated" in out.lower()


def test_grep_rg_branch(wt: Path, tmp_path: Path, monkeypatch):
    fake_rg = tmp_path / "fake-rg"
    fake_rg.write_text("#!/bin/bash\necho \"$PWD/src/app.py:2:    return 42\"\n")
    fake_rg.chmod(0o755)
    monkeypatch.setattr(tools.shutil, "which", lambda name: str(fake_rg) if name == "rg" else None)
    out = tools.grep(wt, "return 42")
    assert "app.py" in out


def test_read_file_refuses_oversized(wt: Path):
    # sparse 6 MB file — over the 5 MB read cap
    big = wt / "big.bin"
    with open(big, "wb") as f:
        f.seek(6 * 1024 * 1024)
        f.write(b"x")
    out = tools.read_file(wt, "big.bin")
    assert "over the" in out and "read limit" in out


def test_read_file_refuses_fifo(wt: Path):
    import os
    fifo = wt / "pipe"
    os.mkfifo(fifo)
    out = tools.read_file(wt, "pipe")
    assert "not a regular file" in out


def test_edit_file_refuses_oversized(wt: Path):
    big = wt / "big.txt"
    with open(big, "wb") as f:
        f.seek(6 * 1024 * 1024)
        f.write(b"x")
    out = tools.edit_file(wt, "big.txt", "x", "y")
    assert "over the" in out
