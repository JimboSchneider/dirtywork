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


def test_number_lines_matches_read_file_shape(wt: Path):
    from dirtywork import tools
    text = "def main():\n    return 42\n"
    direct = tools._number_lines(text, offset=0, limit=400)
    via_read = tools.read_file(wt, "src/app.py")
    assert direct.splitlines()[0] == via_read.splitlines()[0]
    assert direct.splitlines()[1] == via_read.splitlines()[1]


def test_edit_file_refuses_oversized(wt: Path):
    big = wt / "big.txt"
    with open(big, "wb") as f:
        f.seek(6 * 1024 * 1024)
        f.write(b"x")
    out = tools.edit_file(wt, "big.txt", "x", "y")
    assert "over the" in out


import errno
import os
import signal
from contextlib import contextmanager


@contextmanager
def _hang_guard(seconds=5):
    """Fail loudly instead of hanging the whole suite if a FIFO-hardening
    regression reintroduces a blocking open."""
    def _on_alarm(signum, frame):
        raise TimeoutError(f"operation did not return within {seconds}s — likely hung on a FIFO")
    old = signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs require a POSIX OS")
def test_edit_file_refuses_fifo(wt: Path):
    fifo = wt / "pipe"
    os.mkfifo(fifo)
    with _hang_guard():
        out = tools.edit_file(wt, "pipe", "a", "b")
    assert "not a regular file" in out


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs require a POSIX OS")
def test_write_file_refuses_fifo(wt: Path):
    fifo = wt / "pipe"
    os.mkfifo(fifo)
    with _hang_guard():
        out = tools.write_file(wt, "pipe", "new content")
    assert out.startswith("ERROR:")
    assert "not a regular file" in out


def test_write_file_refuses_symlink_final_component(wt: Path):
    target = wt / "real.txt"
    target.write_text("original")
    link = wt / "link.txt"
    os.symlink(target, link)
    out = tools.write_file(wt, "link.txt", "new content")
    assert out.startswith("ERROR:")
    assert "symlink" in out.lower()
    assert target.read_text() == "original"  # never written through the symlink


def test_edit_file_refuses_symlink_final_component(wt: Path):
    target = wt / "real2.txt"
    target.write_text("aaa")
    link = wt / "link2.txt"
    os.symlink(target, link)
    out = tools.edit_file(wt, "link2.txt", "aaa", "bbb")
    assert out.startswith("ERROR:")
    assert "symlink" in out.lower()
    assert target.read_text() == "aaa"  # never written through the symlink


def test_write_file_refuses_oversized_content(wt: Path):
    huge = "x" * (tools.MAX_WRITE_BYTES + 1)
    out = tools.write_file(wt, "big_write.txt", huge)
    assert out.startswith("ERROR:")
    assert "write limit" in out
    assert not (wt / "big_write.txt").exists()


def test_list_dir_truncates_at_max_entries(wt: Path, monkeypatch):
    # Monkeypatching the constant down (rather than creating 2001 real
    # files) exercises the exact same truncation code path while keeping
    # the rendered listing well under MAX_RESULT_CHARS, so the entry-count
    # marker isn't itself swallowed by the unrelated char-count cap.
    monkeypatch.setattr(tools, "MAX_LIST_ENTRIES", 3)
    many_dir = wt / "many"
    many_dir.mkdir()
    for i in range(5):
        (many_dir / f"f{i}.txt").write_text("x")
    out = tools.list_dir(wt, "many")
    assert "[listing truncated at 3 entries]" in out
    shown = [l for l in out.splitlines() if l.endswith("bytes)")]
    assert len(shown) == 3


def test_describe_change_counts_and_diffs():
    old = "one\ntwo\nthree\nfour\nfive\n"
    new = "one\ntwo\nTWO AND A HALF\nthree\nfour\nfive\n"
    out = tools.describe_change("a/b.py", old, new, verb="Edited")
    lines = out.splitlines()
    assert lines[0] == "Edited a/b.py: +1 -0"      # pure insert: no removal note
    assert "--- a/a/b.py" in out and "+++ b/a/b.py" in out
    assert "+TWO AND A HALF" in out


def test_describe_change_reports_removed_non_blank_lines():
    old = "keep\ndrop me\n\nkeep2\n"
    new = "keep\nkeep2\n"
    out = tools.describe_change("x.txt", old, new, verb="Edited")
    # 'drop me' and the blank line go; only the non-blank one is counted
    assert out.splitlines()[0] == "Edited x.txt: +0 -2 (removed 1 non-blank line)"
    old2 = "a\nb\nc\n"
    new2 = "a\nB\nC\n"
    # a replaced non-blank line counts as removed
    assert tools.describe_change("x.txt", old2, new2, verb="Edited").splitlines()[0] == (
        "Edited x.txt: +2 -2 (removed 2 non-blank lines)")


def test_describe_change_truncates_a_huge_diff():
    old = "".join(f"line {i}\n" for i in range(200))
    new = "".join(f"changed {i}\n" for i in range(200))
    out = tools.describe_change("big.txt", old, new, verb="Edited")
    body = out.split("\n", 1)[1]
    assert len(body.splitlines()) <= tools.MAX_DIFF_LINES + 1     # + the marker line
    assert body.splitlines()[-1].startswith("[diff truncated: ")
    assert body.splitlines()[-1].endswith(" more lines]")


def test_describe_write_new_file_keeps_the_byte_count():
    assert tools.describe_write("new.txt", None, "a\nb\n", 4) == (
        "Wrote 4 bytes to new.txt (new file, 2 lines)")
    assert tools.describe_write("one.txt", None, "solo", 4) == (
        "Wrote 4 bytes to one.txt (new file, 1 line)")


def test_edit_and_write_echo_their_diff(wt: Path):
    out = tools.edit_file(wt, "src/app.py", "return 42", "return 43")
    assert out.startswith("Edited src/app.py: +1 -1 (removed 1 non-blank line)")
    assert "-    return 42" in out and "+    return 43" in out
    over = tools.write_file(wt, "src/app.py", "def main():\n    return 44\n")
    assert over.startswith("Wrote src/app.py: ")
    assert "+    return 44" in over
