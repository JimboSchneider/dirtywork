from __future__ import annotations

import re
import time
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


def test_describe_change_sees_a_trailing_newline_only_change():
    # Fix item 4: splitlines() drops terminal-newline info, so "x" -> "x\n"
    # used to report +0 -0 and no diff. splitlines(keepends=True) sees it,
    # and the missing-newline side is rendered with git's marker line.
    out = tools.describe_change("f.txt", "x", "x\n", verb="Edited")
    lines = out.splitlines()
    assert lines[0] == "Edited f.txt: +1 -1 (removed 1 non-blank line)"
    assert "-x" in lines
    assert "+x" in lines
    assert r"\ No newline at end of file" in lines
    # the marker immediately follows the content line that lacked a newline
    assert lines[lines.index("-x") + 1] == r"\ No newline at end of file"


def test_describe_change_form_feed_is_not_mistaken_for_a_missing_newline():
    # Round 2 fix: splitlines(keepends=True) also splits on \v \f \x1c \x1d
    # \x1e \x85 etc, not just "\n" -- a line containing a form feed used to
    # get a FALSE "no newline at end of file" marker mid-diff even though the
    # file genuinely ends in "\n". Only "\n" is a line separator now.
    old = "a\nb\fc\n"
    new = "a\nb\fC\n"
    out = tools.describe_change("f.txt", old, new, verb="Edited")
    assert r"\ No newline at end of file" not in out
    assert "-b\fc" in out
    assert "+b\fC" in out


def test_describe_change_ordinary_edit_output_is_unchanged():
    # Pin: an ordinary edit where every line (old and new) ends in a newline
    # renders byte-for-byte the same as before the keepends=True switch.
    old = "one\ntwo\nthree\n"
    new = "one\nCHANGED\nthree\n"
    out = tools.describe_change("f.py", old, new, verb="Edited")
    assert out == (
        "Edited f.py: +1 -1 (removed 1 non-blank line)\n"
        "--- a/f.py\n+++ b/f.py\n@@ -1,3 +1,3 @@\n"
        " one\n-two\n+CHANGED\n three"
    )


def test_describe_change_truncates_a_huge_diff():
    old = "".join(f"line {i}\n" for i in range(200))
    new = "".join(f"changed {i}\n" for i in range(200))
    out = tools.describe_change("big.txt", old, new, verb="Edited")
    body = out.split("\n", 1)[1]
    assert len(body.splitlines()) <= tools.MAX_DIFF_LINES + 1     # + the marker line
    assert body.splitlines()[-1].startswith("[diff truncated: ")
    assert body.splitlines()[-1].endswith(" more lines]")


def test_describe_change_header_counts_match_the_diff_body_with_popular_repeated_lines():
    # >200 lines with a "popular" repeated line: with autojunk=False the header's
    # SequenceMatcher pass treats the popular line as an ordinary match and reports
    # far fewer +/- than unified_diff (which always uses the default autojunk=True)
    # actually prints in the body. They must agree.
    old_lines, new_lines = [], []
    for i in range(250):
        if i % 5 == 0:
            old_lines.append(f"unique-old-{i}")
            new_lines.append(f"unique-new-{i}")
        else:
            old_lines.append("popular")
            new_lines.append("popular")
    old = "\n".join(old_lines) + "\n"
    new = "\n".join(new_lines) + "\n"
    out = tools.describe_change("big.txt", old, new, verb="Edited")
    head = out.splitlines()[0]
    m = re.match(r"Edited big\.txt: \+(\d+) -(\d+)", head)
    assert m, head
    added, deleted = int(m.group(1)), int(m.group(2))
    # With autojunk fixed (matching unified_diff's default), the popular
    # repeated line is treated the same way in the header pass as in the
    # diff body: both see it as junk and can't anchor a match around it, so
    # the whole 250-line sequence reads as changed. Before the fix (header
    # pinned to autojunk=False) the header undercounted this as +50 -50
    # while unified_diff's body — always autojunk=True — printed 250/250:
    # the two disagreed. They must not.
    assert added == deleted == 250


def test_describe_change_omits_the_diff_for_a_huge_file():
    old = "\n".join(f"line {i}" for i in range(tools.DESCRIBE_DIFF_MAX_LINES + 1))
    new = old + "\nextra"
    new_line_count = len(new.splitlines())
    start = time.monotonic()
    out = tools.describe_change("huge.txt", old, new, verb="Edited")
    elapsed = time.monotonic() - start
    assert elapsed < 5, f"describe_change took {elapsed:.1f}s on a huge file"
    assert out == f"Edited huge.txt: {new_line_count} lines (diff omitted: file too large)"
    assert "\n" not in out


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


def test_insert_text_places_whole_lines_around_the_anchor_line():
    text = "alpha\nbeta\ngamma\n"
    assert tools.insert_text(text, "beta", "NEW\n", "before") == "alpha\nNEW\nbeta\ngamma\n"
    assert tools.insert_text(text, "beta", "NEW\n", "after") == "alpha\nbeta\nNEW\ngamma\n"
    # a multi-line anchor: 'before' the first line, 'after' the last
    assert tools.insert_text(text, "beta\ngamma", "NEW\n", "before") == (
        "alpha\nNEW\nbeta\ngamma\n")
    assert tools.insert_text(text, "beta\ngamma", "NEW\n", "after") == (
        "alpha\nbeta\ngamma\nNEW\n")
    # an anchor in the middle of a line never splits that line
    assert tools.insert_text("x = f(1)\ny\n", "f(1", "NEW\n", "before") == (
        "NEW\nx = f(1)\ny\n")


def test_insert_text_adds_the_missing_newlines():
    # insert without a trailing newline gets one
    assert tools.insert_text("a\nb\n", "a", "NEW", "after") == "a\nNEW\nb\n"
    # a file with no final newline gets one before the appended line
    assert tools.insert_text("a\nb", "b", "NEW\n", "after") == "a\nb\nNEW\n"
    # inserting before the first line needs no leading newline
    assert tools.insert_text("a\nb\n", "a", "NEW\n", "before") == "NEW\na\nb\n"


def test_insert_before_and_after_write_the_file_and_echo_a_diff(wt: Path):
    (wt / "cfg.txt").write_text("alpha\nbeta\ngamma\n")
    out = tools.insert_after(wt, "cfg.txt", "beta", "beta-plus")
    assert out.startswith("Inserted into cfg.txt: +1 -0")
    assert "+beta-plus" in out
    assert (wt / "cfg.txt").read_text() == "alpha\nbeta\nbeta-plus\ngamma\n"
    out = tools.insert_before(wt, "cfg.txt", "gamma", "pre-gamma\n")
    assert out.startswith("Inserted into cfg.txt: +1 -0")
    assert (wt / "cfg.txt").read_text() == "alpha\nbeta\nbeta-plus\npre-gamma\ngamma\n"


def test_insert_requires_a_unique_anchor(wt: Path):
    (wt / "dup.txt").write_text("aa\naa\n")
    out = tools.insert_before(wt, "dup.txt", "aa", "x")
    assert out.startswith("ERROR: anchor occurs 2 times in dup.txt")
    assert "it must occur exactly once" in out
    assert (wt / "dup.txt").read_text() == "aa\naa\n"      # nothing written
    missing = tools.insert_after(wt, "dup.txt", "zz", "x")
    assert missing.startswith("ERROR: anchor occurs 0 times in dup.txt")


def test_insert_keeps_the_edit_file_guardrails(wt: Path):
    assert tools.insert_before(wt, "../../etc/passwd", "root", "x").startswith("ERROR:")
    assert tools.insert_before(wt, "nope.py", "x", "y").startswith("ERROR: cannot read")
    (wt / "bin2.dat").write_bytes(b"\xff\xfe\x00\x01")
    binary = tools.insert_after(wt, "bin2.dat", "x", "y")
    assert binary == "ERROR: bin2.dat is not valid UTF-8 text; insert_after only works on text files"
    # edit_file's own message is unchanged
    (wt / "bin3.dat").write_bytes(b"\xff\xfe\x00\x01")
    assert tools.edit_file(wt, "bin3.dat", "x", "y") == (
        "ERROR: bin3.dat is not valid UTF-8 text; edit_file only works on text files")


# --- spec §1.1/§1.2/§1.5: apply_edits and the shared write cap.

def test_apply_edits_applies_in_order(wt: Path):
    (wt / "seq.txt").write_text("alpha\nbeta\n")
    out = tools.apply_edits(wt, "seq.txt", [
        {"old": "alpha", "new": "gamma"},
        {"old": "gamma\nbeta", "new": "gamma\ndelta"},   # only matches after edit 1
    ])
    assert out.startswith("Applied 2 edits to seq.txt: ")
    assert (wt / "seq.txt").read_text() == "gamma\ndelta\n"


def test_apply_edits_singular_verb_for_one_edit(wt: Path):
    out = tools.apply_edits(wt, "src/app.py", [{"old": "return 42", "new": "return 43"}])
    assert out.startswith("Applied 1 edit to src/app.py: ")
    assert "return 43" in (wt / "src" / "app.py").read_text()


def test_apply_edits_rolls_back_when_a_later_edit_does_not_match(wt: Path):
    before = (wt / "src" / "app.py").read_text()
    out = tools.apply_edits(wt, "src/app.py", [
        {"old": "return 42", "new": "return 43"},
        {"old": "not here", "new": "x"},
    ])
    assert out == ("ERROR: edit 2 of 2: old text occurs 0 times in src/app.py; it must "
                   "occur exactly once (after edits 1..1 are applied); no edits applied")
    assert (wt / "src" / "app.py").read_text() == before   # byte-identical: nothing written


def test_apply_edits_rejects_an_empty_old(wt: Path):
    before = (wt / "src" / "app.py").read_text()
    out = tools.apply_edits(wt, "src/app.py", [{"old": "", "new": "x"}])
    assert out == "ERROR: edit 1 of 1: old text is empty; no edits applied"
    assert (wt / "src" / "app.py").read_text() == before


def test_apply_edits_rejects_a_malformed_edit_item(wt: Path):
    # Sandbox.apply_edits is public; a caller that reaches it without going
    # through the tool registry (spec §1.3) may pass a shape the registry
    # would have refused. The guard fires before any matching, inside the
    # same all-or-nothing pass, and never raises.
    before = (wt / "src" / "app.py").read_text()
    for edits in (
        [{"old": "return 42"}],               # missing "new"
        [{"new": "return 43"}],                # missing "old"
        ["return 42"],                         # not a dict at all
        [{"old": "return 42", "new": 43}],     # "new" not a string
        [{"old": 42, "new": "return 43"}],     # "old" not a string
    ):
        out = tools.apply_edits(wt, "src/app.py", edits)
        assert out == ("ERROR: edit 1 of 1: each edit must be an object with string "
                       "'old' and 'new'; no edits applied")
        assert (wt / "src" / "app.py").read_text() == before


def test_apply_edits_rejects_a_repeated_old(wt: Path):
    (wt / "dup.txt").write_text("aa\naa\n")
    out = tools.apply_edits(wt, "dup.txt", [{"old": "aa", "new": "bb"}])
    assert out == ("ERROR: edit 1 of 1: old text occurs 2 times in dup.txt; it must occur "
                   "exactly once. Include more surrounding context to make it unique; "
                   "no edits applied")
    assert (wt / "dup.txt").read_text() == "aa\naa\n"


def test_apply_edits_result_carries_the_unified_diff(wt: Path):
    out = tools.apply_edits(wt, "src/app.py", [{"old": "return 42", "new": "return 43"}])
    lines = out.splitlines()
    assert lines[0] == "Applied 1 edit to src/app.py: +1 -1 (removed 1 non-blank line)"
    assert "--- a/src/app.py" in out and "+++ b/src/app.py" in out
    assert "-    return 42" in out and "+    return 43" in out


def test_apply_edits_result_over_the_write_cap_is_refused(wt: Path):
    # The file holds "seed\n"; replacing "seed" leaves the trailing newline, so
    # the result is exactly MAX_WRITE_BYTES + 2 bytes.
    (wt / "grow.txt").write_text("seed\n")
    huge = "x" * (tools.MAX_WRITE_BYTES + 1)
    expected = tools.MAX_WRITE_BYTES + 2
    out = tools.apply_edits(wt, "grow.txt", [{"old": "seed", "new": huge}])
    assert out == (f"ERROR: result is {expected} bytes, over the "
                   f"{tools.MAX_WRITE_BYTES}-byte write limit; nothing was written")
    assert (wt / "grow.txt").read_text() == "seed\n"


def test_edit_file_result_over_the_write_cap_is_refused(wt: Path):
    # Spec §1.5: the cap lives in the SHARED transform path, so edit_file gets
    # the identical refusal -- it had none at all before 0.9.
    (wt / "grow.txt").write_text("seed\n")
    huge = "x" * (tools.MAX_WRITE_BYTES + 1)
    expected = tools.MAX_WRITE_BYTES + 2
    out = tools.edit_file(wt, "grow.txt", "seed", huge)
    assert out == (f"ERROR: result is {expected} bytes, over the "
                   f"{tools.MAX_WRITE_BYTES}-byte write limit; nothing was written")
    assert (wt / "grow.txt").read_text() == "seed\n"


# --- spec §2.2/§2.5/§1.2: the shared write primitive and the shared strings.
# --- Nothing in dirtywork calls _write_atomic yet (Tasks 2, 3, 4 and 6 wire it
# --- up); these tests are the primitive's own contract.

import stat as _stat


def _temp_leftovers(directory: Path) -> list:
    return sorted(p.name for p in directory.iterdir() if p.name.startswith(tools.TMP_PREFIX))


def test_tmp_name_has_the_generated_shape_and_is_random(wt: Path):
    first = tools.tmp_name("app.py")
    second = tools.tmp_name("app.py")
    assert re.fullmatch(r"\.dw-tmp\.app\.py\.[0-9a-f]{8}", first)
    assert first != second          # the worker controls sibling names
    assert tools.is_temp_name(first) and tools.is_temp_name(second)


def test_is_temp_name_ignores_a_worker_file_that_only_starts_like_one(wt: Path):
    # Spec §2.5: the sweep matches the FULL generated shape, never a bare glob.
    assert not tools.is_temp_name(".dw-tmp.notes")
    assert not tools.is_temp_name(".dw-tmp.notes.txt")
    assert not tools.is_temp_name(".dw-tmp.app.py.DEADBEEF")   # we only ever emit lowercase
    assert not tools.is_temp_name("app.py")


def test_write_atomic_creates_a_new_file_with_umask_default_mode(wt: Path):
    target = wt / "new.txt"
    assert tools._write_atomic(target, b"hello\n", path="new.txt") is None
    assert target.read_bytes() == b"hello\n"
    # Exactly what _open_regular(..., O_CREAT, mode=0o644) produced before 0.10.
    assert _stat.S_IMODE(target.stat().st_mode) == 0o644 & ~tools._UMASK
    assert _temp_leftovers(wt) == []


def test_write_atomic_preserves_an_existing_files_mode(wt: Path):
    target = wt / "script.sh"
    target.write_text("#!/bin/sh\n")
    target.chmod(0o755)
    assert tools._write_atomic(target, b"#!/bin/sh\necho hi\n", path="script.sh") is None
    assert target.read_bytes() == b"#!/bin/sh\necho hi\n"
    assert _stat.S_IMODE(target.stat().st_mode) == 0o755
    assert _temp_leftovers(wt) == []


def test_write_atomic_promotes_by_rename_so_the_inode_changes(wt: Path):
    target = wt / "swap.txt"
    target.write_text("old\n")
    before = target.stat().st_ino
    assert tools._write_atomic(target, b"new\n", path="swap.txt") is None
    assert target.read_bytes() == b"new\n"
    assert target.stat().st_ino != before   # spec §2.3: os.replace changes the inode


def test_write_atomic_refuses_a_symlink_with_the_shipped_wording(wt: Path):
    real = wt / "real.txt"
    real.write_text("original")
    link = wt / "link.txt"
    os.symlink(real, link)
    out = tools._write_atomic(link, b"new", path="link.txt")
    assert out == ("ERROR: 'link.txt' is a symlink; writing through a symlink is not "
                   "allowed even when its target is inside the worktree")
    assert real.read_text() == "original"
    assert _temp_leftovers(wt) == []


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs require a POSIX OS")
def test_write_atomic_refuses_a_fifo_with_the_shipped_wording(wt: Path):
    fifo = wt / "pipe"
    os.mkfifo(fifo)
    with _hang_guard():
        out = tools._write_atomic(fifo, b"new", path="pipe")
    assert out == "ERROR: 'pipe' is not a regular file (refusing FIFO/device/socket)"
    assert _temp_leftovers(wt) == []


def test_write_atomic_writes_through_a_hardlinked_target(wt: Path):
    # Spec §2.2 step 2: a hardlink is MEANT to see the write, so shared-inode
    # semantics (and today's non-atomicity) are preserved on purpose.
    a = wt / "a.txt"
    a.write_text("old\n")
    b = wt / "b.txt"
    os.link(a, b)
    before = a.stat().st_ino
    assert tools._write_atomic(a, b"new\n", path="a.txt") is None
    assert a.read_bytes() == b"new\n"
    assert b.read_bytes() == b"new\n"          # the link sees it
    assert a.stat().st_ino == before           # no rename happened
    assert _temp_leftovers(wt) == []


# `os.geteuid` does not exist on Windows and this decorator runs at COLLECTION
# time, so it is guarded exactly the way tests/test_budget.py:79 guards its own.
@pytest.mark.skipif(getattr(os, "geteuid", lambda: -1)() == 0 or os.name == "nt",
                    reason="root (and Windows) ignore directory permissions")
def test_write_atomic_falls_back_to_the_fd_in_an_unwritable_directory(wt: Path):
    # Spec §2.2 step 5: a writable file in a 0555 directory cannot be renamed
    # into place, so the probe fd is used -- today's semantics, preserved.
    sub = wt / "locked"
    sub.mkdir()
    target = sub / "f.txt"
    target.write_text("old\n")
    sub.chmod(0o555)
    try:
        assert tools._write_atomic(target, b"new\n", path="locked/f.txt") is None
        assert target.read_bytes() == b"new\n"
    finally:
        sub.chmod(0o755)


@pytest.mark.skipif(getattr(os, "geteuid", lambda: -1)() == 0 or os.name == "nt",
                    reason="root (and Windows) ignore directory permissions")
def test_write_atomic_refuses_a_new_file_in_an_unwritable_directory(wt: Path):
    # No probe fd exists (ENOENT), so there is nothing to fall back to: the
    # temp-creation errno is reported, preserving today's EACCES refusal.
    sub = wt / "locked2"
    sub.mkdir()
    sub.chmod(0o555)
    try:
        out = tools._write_atomic(sub / "new.txt", b"x", path="locked2/new.txt")
    finally:
        sub.chmod(0o755)
    assert out.startswith("ERROR: cannot write 'locked2/new.txt': ")
    assert "Permission denied" in out
    assert not (sub / "new.txt").exists()


def test_write_atomic_creates_parents_only_when_asked(wt: Path):
    made = wt / "deep" / "new" / "f.txt"
    assert tools._write_atomic(made, b"hi", path="deep/new/f.txt",
                               create_parents=True) is None
    assert made.read_bytes() == b"hi"
    missing = wt / "other" / "f.txt"
    out = tools._write_atomic(missing, b"hi", path="other/f.txt")
    assert out.startswith("ERROR: cannot write 'other/f.txt': ")
    assert not (wt / "other").exists()


def test_write_atomic_append_verb_changes_only_the_generic_tail(wt: Path, monkeypatch):
    target = wt / "f.txt"
    target.write_text("old\n")

    def _boom(fd, data):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(tools, "_write_all", _boom)
    out = tools._write_atomic(target, b"new\n", path="f.txt", verb="append")
    assert out.startswith("ERROR: cannot append to 'f.txt': ")
    assert "No space left on device" in out


def test_write_atomic_returns_an_error_string_on_an_oserror_during_the_write(wt: Path, monkeypatch):
    target = wt / "f.txt"
    target.write_text("old\n")

    def _boom(fd, data):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(tools, "_write_all", _boom)
    out = tools._write_atomic(target, b"new\n", path="f.txt")
    assert out.startswith("ERROR: cannot write 'f.txt': ")
    assert target.read_bytes() == b"old\n"     # spec §2.3: byte-identical
    assert _temp_leftovers(wt) == []           # the temp was unlinked in-call


def test_write_atomic_surfaces_a_close_failure_without_raising(wt: Path, monkeypatch):
    # Spec §2.2: the temp fd is closed BEFORE the promote precisely so a
    # DEFERRED write error surfaces while the target is still untouched. The
    # handle is cleared before that close, so the except arm's own cleanup
    # never closes an already-closed fd -- an EBADF escaping the handler would
    # be a tool function raising, which the contract forbids.
    target = wt / "deferred.txt"
    target.write_text("old\n")
    staged = {}
    real_write_all = tools._write_all
    real_close = os.close

    def _record(fd, data):
        staged["fd"] = fd            # _write_all only ever gets the temp fd
        return real_write_all(fd, data)

    def _closing(fd):
        real_close(fd)
        if fd == staged.get("fd"):
            raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(tools, "_write_all", _record)
    monkeypatch.setattr(os, "close", _closing)
    out = tools._write_atomic(target, b"new\n", path="deferred.txt")
    assert out.startswith("ERROR: cannot write 'deferred.txt': ")
    assert "Input/output error" in out
    assert target.read_bytes() == b"old\n"     # never promoted
    assert _temp_leftovers(wt) == []


def test_write_atomic_reraises_a_non_oserror_and_unlinks_its_temp(wt: Path, monkeypatch):
    # Spec §2.2 step 4: KeyboardInterrupt / BudgetExceeded / SandboxError are
    # run-level signals the runner owns, NOT tool results.
    target = wt / "f.txt"
    target.write_text("old\n")

    def _boom(fd, data):
        raise KeyboardInterrupt

    monkeypatch.setattr(tools, "_write_all", _boom)
    with pytest.raises(KeyboardInterrupt):
        tools._write_atomic(target, b"new\n", path="f.txt")
    assert target.read_bytes() == b"old\n"
    assert _temp_leftovers(wt) == []


def test_append_oversized_wording_is_not_the_write_file_wording(wt: Path):
    # Spec §1.2 cap 1: an append's fix is "append in smaller pieces", never
    # write_file's "write the file in smaller pieces".
    assert tools._append_oversized(b"x" * 10) is None
    out = tools._append_oversized(b"x" * (tools.MAX_WRITE_BYTES + 1))
    assert out == (f"ERROR: text is {tools.MAX_WRITE_BYTES + 1} bytes, over the "
                   f"{tools.MAX_WRITE_BYTES}-byte write limit; append in smaller pieces")
    assert "write the file in smaller pieces" not in out


def test_result_too_big_is_the_shared_transform_string(wt: Path):
    # The same sentence _check_write_size has emitted since 0.9, now built in
    # one place so docker's append (which learns the size from `stat`, not from
    # a buffer) can render it byte-identically.
    assert tools._result_too_big(99) == (
        f"ERROR: result is 99 bytes, over the {tools.MAX_WRITE_BYTES}-byte "
        f"write limit; nothing was written")
    huge = "x" * (tools.MAX_WRITE_BYTES + 1)
    assert tools._check_write_size(huge) == tools._result_too_big(tools.MAX_WRITE_BYTES + 1)
    assert tools._check_write_size("small") is None


def test_append_missing_and_not_utf8_strings(wt: Path):
    assert tools._append_missing("notes.md") == (
        "ERROR: cannot append to 'notes.md': it does not exist; create it with "
        "write_file first")
    assert tools._not_utf8("bin.dat", "append_file") == (
        "ERROR: bin.dat is not valid UTF-8 text; append_file only works on text files")
