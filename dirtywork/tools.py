from __future__ import annotations

import difflib
import errno
import os
import shutil
import stat
import subprocess
from pathlib import Path

from .guardrails import GuardrailError, build_env, check_bash_command, resolve_in_worktree
from .procs import run_capped

MAX_RESULT_CHARS = 8000
# Spec §3.1: every successful edit/write echoes the unified diff of what it
# actually changed, so a worker that meant to insert a line and replaced one
# instead sees that in the tool result rather than at review time.
MAX_DIFF_LINES = 40
MAX_DIFF_CHARS = 3000
# describe_change's SequenceMatcher/unified_diff are quadratic-ish on files
# with popular repeated lines; above this many lines (either side) the diff
# is omitted entirely rather than risk a multi-minute, uninterruptible call.
DESCRIBE_DIFF_MAX_LINES = 20000
# Refuse to load a file larger than this into memory. read_file/edit_file read
# the whole file (offset/limit only window the result), so an unbounded read is a
# memory-DoS; a non-regular file (FIFO/device) would also block read_text forever.
MAX_READ_BYTES = 5 * 1024 * 1024
MAX_WRITE_BYTES = 5 * 1024 * 1024
MAX_LIST_ENTRIES = 2000


def _cap(text: str, cap: int = MAX_RESULT_CHARS, note: str = "") -> str:
    if len(text) <= cap:
        return text
    suffix = f"\n[output truncated at {cap} chars{note}]"
    return text[:cap] + suffix


def _open_regular(path: Path, flags: int, *, mode: int = 0o644, max_size: int | None = None):
    """Open `path` as a real file, refusing symlinks/FIFOs/devices/sockets and
    (for reads) oversized files, then return a binary fd-backed file object.

    `flags` is the caller's os.O_* combination (e.g. O_RDONLY, or
    O_WRONLY|O_CREAT|O_TRUNC for writes) WITHOUT O_NOFOLLOW/O_NONBLOCK/
    O_CLOEXEC — this function always adds those three:
    - O_NOFOLLOW closes the final-component symlink TOCTOU: writing through a
      symlink is refused (raises OSError with errno ELOOP) even when its
      target is inside the worktree.
    - O_NONBLOCK makes opening a FIFO return immediately instead of hanging —
      a read with no writer returns a valid fd instantly (caught below by the
      S_ISREG check); a write with no reader raises OSError with errno ENXIO
      immediately. Either way the process never blocks.
    - O_CLOEXEC keeps the fd from leaking into any subprocess this process
      later spawns (e.g. the `bash`/`grep` tools).
    O_NONBLOCK is cleared (via os.set_blocking) once S_ISREG is confirmed, so
    ordinary reads/writes on the returned file object behave normally.
    """
    full_flags = flags | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    fd = os.open(str(path), full_flags, mode)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(f"'{path}' is not a regular file (refusing FIFO/device/socket)")
        if max_size is not None and st.st_size > max_size:
            raise OSError(
                f"'{path}' is {st.st_size} bytes, over the {max_size}-byte "
                f"read limit; use grep to search it instead of reading it whole"
            )
        os.set_blocking(fd, True)
    except Exception:
        os.close(fd)
        raise
    if flags & os.O_WRONLY:
        pymode = "wb"
    elif flags & os.O_RDWR:
        pymode = "r+b"
    else:
        pymode = "rb"
    return os.fdopen(fd, pymode)


def _worktree_candidate(path_str: str, worktree: Path) -> Path:
    """The worktree-joined path BEFORE symlink resolution — the same join
    `resolve_in_worktree` performs internally, but without following a
    symlink at the final component. Call this only AFTER
    `resolve_in_worktree` has already validated containment (it fully
    resolves symlinks, so it proves the effective target — if any — is
    inside the worktree); using its return value directly for a WRITE would
    hand `_open_regular` the already-dereferenced target, defeating
    O_NOFOLLOW. Using this parent-resolved, final-component-unresolved join instead lets O_NOFOLLOW see and refuse a real symlink at the final path component while intermediate components stay dereferenced.
    """
    wt = worktree.resolve()
    raw = Path(path_str)
    candidate = raw if raw.is_absolute() else wt / raw
    # Resolve every PARENT component (so an intermediate symlink is
    # dereferenced now, at check time, like resolve_in_worktree did) but keep
    # the final component unresolved so O_NOFOLLOW can refuse a symlink there.
    return candidate.parent.resolve() / candidate.name


def _number_lines(text: str, offset: int, limit: int) -> str:
    lines = text.splitlines()
    window = lines[offset : offset + limit]
    numbered = "\n".join(f"{i:6}\t{line}" for i, line in enumerate(window, offset + 1))
    if offset + limit < len(lines):
        numbered += (
            f"\n[showing lines {offset + 1}-{offset + len(window)} of {len(lines)}; "
            f"re-run with offset={offset + limit} for more]"
        )
    return _cap(numbered, note=" — re-run with offset/limit to see more")


def _lines_keep_newlines(text: str) -> list:
    """Like `text.splitlines(keepends=True)`, except ONLY `"\n"` is treated
    as a line separator. `str.splitlines()` also breaks on `\v`, `\f`,
    `\x1c`-`\x1e`, `\x85`, U+2028 and U+2029 -- a line that merely
    CONTAINS one of those characters (e.g. a form feed) would otherwise be
    split into a fragment with no trailing `"\n"`, and describe_change would
    then render a FALSE `\ No newline at end of file` marker mid-diff even
    though the file genuinely ends in a newline. Splitting on `"\n"` alone
    means only the file's true final line can ever lack one."""
    if not text:
        return []
    parts = text.split("\n")
    if parts[-1] == "":
        # text ends in "\n": every remaining piece is a complete line.
        return [p + "\n" for p in parts[:-1]]
    # text does NOT end in "\n": the last piece is the file's final,
    # newline-less line; every other piece is a complete line.
    return [p + "\n" for p in parts[:-1]] + [parts[-1]]


def _line_counts(old_lines: list, new_lines: list) -> tuple:
    """(added, deleted, removed_non_blank) from SequenceMatcher opcodes. A
    REPLACED non-blank line counts as removed: the counter exists to answer
    'did I delete content I did not mean to delete', and a replace deletes
    before it inserts."""
    added = deleted = removed_non_blank = 0
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("delete", "replace"):
            deleted += i2 - i1
            removed_non_blank += sum(1 for line in old_lines[i1:i2] if line.strip())
        if tag in ("insert", "replace"):
            added += j2 - j1
    return added, deleted, removed_non_blank


def describe_change(path: str, old_text: str, new_text: str, *, verb: str) -> str:
    """Spec §3.1: '<Verb> <path>: +A -D [(removed N non-blank line(s))]' plus a
    capped unified diff. Lines are compared WITH their line endings, via
    `_lines_keep_newlines` (NOT `str.splitlines(keepends=True)`, which also
    breaks on \\v/\\f/etc — see its docstring), so a change to only the
    file's final newline (e.g. `"x"` -> `"x\\n"`) is seen rather than reading
    as identical. A content line that lacks a trailing newline (only ever
    the diff's very last old/new line) is rendered followed by git's own
    `\\ No newline at end of file` marker, on its own output line. CRLF
    content shows its carriage return as-is, like git does — a line ending
    in `"\\r\\n"` keeps the `"\\r"` once the `"\\n"` is treated as the
    separator. Pure — no filesystem access — so the host backend and the
    container backend produce byte-identical text for identical content."""
    old_lines = _lines_keep_newlines(old_text)
    new_lines = _lines_keep_newlines(new_text)
    if max(len(old_lines), len(new_lines)) > DESCRIBE_DIFF_MAX_LINES:
        # SequenceMatcher/unified_diff are omitted entirely: even the O(n)
        # line-count pass is skipped so this stays cheap regardless of content.
        return f"{verb} {path}: {len(new_lines)} lines (diff omitted: file too large)"
    added, deleted, removed_non_blank = _line_counts(old_lines, new_lines)
    head = f"{verb} {path}: +{added} -{deleted}"
    if removed_non_blank > 0:
        plural = "" if removed_non_blank == 1 else "s"
        head += f" (removed {removed_non_blank} non-blank line{plural})"
    diff_lines = list(difflib.unified_diff(
        old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}", n=2, lineterm=""))
    if not diff_lines:
        return head
    # The first two entries (fromfile/tofile) and every "@@ ... @@" hunk
    # header are already newline-free (lineterm=""); every other entry is a
    # ' '/'+'/'-'-prefixed content line whose text still carries whatever
    # ending its source line had (or didn't). Strip that ending so every
    # entry below joins on a single "\n", inserting git's marker line right
    # after any content line that had no ending of its own.
    rendered = []
    for i, line in enumerate(diff_lines):
        if i < 2 or line.startswith("@@"):
            rendered.append(line)
        elif line.endswith("\n"):
            rendered.append(line[:-1])
        else:
            rendered.append(line)
            rendered.append(r"\ No newline at end of file")
    kept = []
    total = 0
    for line in rendered:
        if len(kept) >= MAX_DIFF_LINES or total + len(line) + 1 > MAX_DIFF_CHARS:
            kept.append(f"[diff truncated: {len(rendered) - len(kept)} more lines]")
            break
        kept.append(line)
        total += len(line) + 1
    return head + "\n" + "\n".join(kept)


def describe_write(path: str, old_text, new_text: str, byte_count: int) -> str:
    """write_file's result string (spec §3.1). `old_text` is the file's previous
    content, or None when there was none to read — a new file, OR an existing
    file the backend could not read back as UTF-8 text (binary, oversized,
    unreadable). Both render as '(new file, M lines)'; that only ever changes
    the wording of a result string, never the write itself. The byte count
    stays in the new-file string so callers matching 'Wrote N bytes' match."""
    if old_text is None:
        lines = len(new_text.splitlines())
        plural = "" if lines == 1 else "s"
        return f"Wrote {byte_count} bytes to {path} (new file, {lines} line{plural})"
    return describe_change(path, old_text, new_text, verb="Wrote")


def insert_text(text: str, anchor: str, insert: str, where: str) -> str:
    """Spec §3.2: place `insert` as WHOLE LINES relative to the line(s) holding
    `anchor`, never modifying the anchor's own line. `where` is 'before' (just
    before the start of the line holding the anchor's first character) or
    'after' (just after the end of the line holding its last character — the
    anchor may span lines). The caller has already proved the anchor occurs
    exactly once. Pure: both backends call this with the text they read."""
    start = text.index(anchor)
    end = start + len(anchor)
    if not insert.endswith("\n"):
        insert = insert + "\n"
    if where == "before":
        line_start = text.rfind("\n", 0, start) + 1
        return text[:line_start] + insert + text[line_start:]
    last = max(start, end - 1)
    newline = text.find("\n", last)
    if newline == -1:
        # the anchor sits on a final line with no trailing newline: give the
        # file one so the inserted text starts on a line of its own
        head = text + "\n" if text and not text.endswith("\n") else text
        return head + insert
    return text[:newline + 1] + insert + text[newline + 1:]


def _read_text_for_diff(path: Path):
    """The file's current text for describe_write, or None when there is none
    to read (missing, a symlink, a FIFO/device, over the read limit, or not
    valid UTF-8). Never raises: this is decoration on a write, and a write must
    never fail because its 'before' picture could not be taken."""
    try:
        fh = _open_regular(path, os.O_RDONLY, max_size=MAX_READ_BYTES)
    except OSError:
        return None
    try:
        raw = fh.read()
    except OSError:
        return None
    finally:
        fh.close()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def read_file(worktree: Path, path: str, offset: int = 0, limit: int = 400) -> str:
    try:
        p = resolve_in_worktree(path, worktree)
    except GuardrailError as e:
        return f"ERROR: {e}"
    try:
        fh = _open_regular(p, os.O_RDONLY, max_size=MAX_READ_BYTES)
    except OSError as e:
        return f"ERROR: cannot read '{path}': {e}"
    try:
        raw = fh.read()
    finally:
        fh.close()
    return _number_lines(raw.decode("utf-8", errors="replace"), offset, limit)


def write_file(worktree: Path, path: str, content: str) -> str:
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_WRITE_BYTES:
        return (
            f"ERROR: content is {len(encoded)} bytes, over the {MAX_WRITE_BYTES}-byte "
            f"write limit; write the file in smaller pieces"
        )
    try:
        resolve_in_worktree(path, worktree, writing=True)  # containment check only
    except GuardrailError as e:
        return f"ERROR: {e}"
    p = _worktree_candidate(path, worktree)
    # Best-effort 'before' picture, taken after the containment check and
    # before the truncating open. None means "nothing to diff against".
    old_text = _read_text_for_diff(p)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"ERROR: cannot write '{path}': {e}"
    try:
        fh = _open_regular(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    except OSError as e:
        if e.errno == errno.ELOOP:
            return (
                f"ERROR: '{path}' is a symlink; writing through a symlink is not "
                f"allowed even when its target is inside the worktree"
            )
        if e.errno == errno.ENXIO:
            return f"ERROR: '{path}' is not a regular file (refusing FIFO/device/socket)"
        return f"ERROR: cannot write '{path}': {e}"
    try:
        fh.write(encoded)
    finally:
        fh.close()
    return describe_write(path, old_text, content, len(encoded))


def _transform_file(worktree: Path, path: str, transform, *, tool: str) -> str:
    """Read → transform → write for every in-place file tool (spec §3.2).
    `transform(text) -> (new_text_or_None, result)`: a None new_text means the
    transform refused and `result` (an 'ERROR: …' string) is returned without
    writing anything. Every check edit_file used to perform itself lives here,
    unchanged: worktree containment, the regular-file/symlink refusals, the
    5 MB read limit, UTF-8 validation, and the O_NOFOLLOW write -- plus, since
    0.9, the shared output cap (_check_write_size, spec §1.5)."""
    try:
        p = resolve_in_worktree(path, worktree, writing=True)
    except GuardrailError as e:
        return f"ERROR: {e}"
    try:
        fh = _open_regular(p, os.O_RDONLY, max_size=MAX_READ_BYTES)
    except OSError as e:
        return f"ERROR: cannot read '{path}': {e}"
    try:
        raw = fh.read()
    finally:
        fh.close()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return f"ERROR: {path} is not valid UTF-8 text; {tool} only works on text files"
    new_text, result = transform(text)
    if new_text is None:
        return result
    too_big = _check_write_size(new_text)
    if too_big:
        return too_big
    write_target = _worktree_candidate(path, worktree)
    try:
        wfh = _open_regular(write_target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    except OSError as e:
        if e.errno == errno.ELOOP:
            return (
                f"ERROR: '{path}' is a symlink; writing through a symlink is not "
                f"allowed even when its target is inside the worktree"
            )
        if e.errno == errno.ENXIO:
            return f"ERROR: '{path}' is not a regular file (refusing FIFO/device/socket)"
        return f"ERROR: cannot write '{path}': {e}"
    try:
        wfh.write(new_text.encode("utf-8"))
    finally:
        wfh.close()
    return result


def _replace_once(path: str, old_string: str, new_string: str):
    """edit_file's transform. Defined here (not in a backend) so the host and
    the container share one uniqueness rule and one error string."""
    def transform(text: str):
        count = text.count(old_string)
        if count != 1:
            return None, (
                f"ERROR: old_string occurs {count} times in {path}; it must occur exactly "
                f"once. Include more surrounding context to make it unique."
            )
        new_text = text.replace(old_string, new_string, 1)
        return new_text, describe_change(path, text, new_text, verb="Edited")
    return transform


def _insert_once(path: str, anchor: str, insert: str, where: str):
    """insert_before/insert_after's transform — the same uniqueness rule and
    the same error shape as _replace_once, with `anchor` in place of
    `old_string`."""
    def transform(text: str):
        count = text.count(anchor)
        if count != 1:
            return None, (
                f"ERROR: anchor occurs {count} times in {path}; it must occur exactly "
                f"once. Include more surrounding context to make it unique."
            )
        new_text = insert_text(text, anchor, insert, where)
        return new_text, describe_change(path, text, new_text, verb="Inserted into")
    return transform


def _check_write_size(new_text: str):
    """Spec §1.5: the one over-the-limit refusal for the shared transform path,
    or None. Both backends' _transform_file call it immediately before the
    write, so edit_file, insert_before, insert_after and apply_edits refuse an
    oversized RESULT with the identical string on the host and in the
    container.

    write_file keeps its own (backend-specific) oversized wording: it refuses
    the model's own `content` before any read, which is a different event with
    a different fix ("write the file in smaller pieces")."""
    size = len(new_text.encode("utf-8"))
    if size > MAX_WRITE_BYTES:
        return (f"ERROR: result is {size} bytes, over the {MAX_WRITE_BYTES}-byte "
                f"write limit; nothing was written")
    return None


def _apply_edits_once(path: str, edits: list):
    """apply_edits' transform (spec §1.1). Defined here, not in a backend, so
    the host and the container share one ordering rule, one uniqueness rule and
    one set of error strings.

    Every edit is applied IN ORDER on the RUNNING text -- edit i sees the text
    after edits 1..i-1 -- because that is what a brief's numbered list means:
    edit 3 may legitimately depend on what edit 1 produced. Each `old` must
    occur exactly once in the text as it stands at its turn, counted with
    str.count (the same non-overlapping count edit_file uses).

    The first failure refuses the WHOLE batch: the transform returns None as
    its new text, which both _transform_file implementations treat as "refused,
    do not write". Registry validation (spec §1.3) has already proved every
    item is exactly {"old": str, "new": str}, so this never re-checks shapes."""
    total = len(edits)

    def transform(text: str):
        new_text = text
        for index, edit in enumerate(edits, 1):
            old = edit["old"]
            if not old:
                return None, (f"ERROR: edit {index} of {total}: old text is empty; "
                              f"no edits applied")
            count = new_text.count(old)
            if count == 0:
                # The "after edits 1..i-1" qualifier only makes sense from the
                # second edit on (spec §1.2, v3.2 ruling).
                applied = (f" (after edits 1..{index - 1} are applied)"
                           if index > 1 else "")
                return None, (
                    f"ERROR: edit {index} of {total}: old text occurs 0 times in {path}; "
                    f"it must occur exactly once{applied}; no edits applied"
                )
            if count > 1:
                return None, (
                    f"ERROR: edit {index} of {total}: old text occurs {count} times in "
                    f"{path}; it must occur exactly once. Include more surrounding "
                    f"context to make it unique; no edits applied"
                )
            new_text = new_text.replace(old, edit["new"], 1)
        verb = f"Applied {total} edit{'' if total == 1 else 's'} to"
        return new_text, describe_change(path, text, new_text, verb=verb)
    return transform


def edit_file(worktree: Path, path: str, old_string: str, new_string: str) -> str:
    return _transform_file(worktree, path, _replace_once(path, old_string, new_string),
                           tool="edit_file")


def insert_before(worktree: Path, path: str, anchor: str, text: str) -> str:
    return _transform_file(worktree, path, _insert_once(path, anchor, text, "before"),
                           tool="insert_before")


def insert_after(worktree: Path, path: str, anchor: str, text: str) -> str:
    return _transform_file(worktree, path, _insert_once(path, anchor, text, "after"),
                           tool="insert_after")


def apply_edits(worktree: Path, path: str, edits: list) -> str:
    return _transform_file(worktree, path, _apply_edits_once(path, edits),
                           tool="apply_edits")


def list_dir(worktree: Path, path: str = ".") -> str:
    try:
        p = resolve_in_worktree(path, worktree)
        entries = sorted(p.iterdir(), key=lambda e: e.name)
    except GuardrailError as e:
        return f"ERROR: {e}"
    except OSError as e:
        return f"ERROR: cannot list '{path}': {e}"
    truncated = len(entries) > MAX_LIST_ENTRIES
    entries = entries[:MAX_LIST_ENTRIES]
    rows = []
    for e in entries:
        try:
            if e.is_dir():
                rows.append(f"{e.name}/")
            else:
                rows.append(f"{e.name}  ({e.stat().st_size} bytes)")
        except OSError:
            rows.append(f"{e.name}  (broken symlink)")
    if truncated:
        rows.append(f"[listing truncated at {MAX_LIST_ENTRIES} entries]")
    return _cap("\n".join(rows) or "(empty directory)")


def grep(worktree: Path, pattern: str, path: str = ".", glob: str | None = None,
         timeout: int = 30) -> str:
    try:
        p = resolve_in_worktree(path, worktree)
    except GuardrailError as e:
        return f"ERROR: {e}"
    except OSError as e:
        return f"ERROR: cannot access '{path}': {e}"
    rg = shutil.which("rg")
    if rg:
        cmd = [rg, "-n", "--no-heading", "-M", "300", "-e", pattern]
        if glob:
            cmd += ["-g", glob]
        cmd.append(str(p))
    else:
        cmd = ["grep", "-rn", "-e", pattern]
        if glob:
            cmd += [f"--include={glob}"]
        cmd.append(str(p))
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"ERROR: grep timed out after {timeout}s — narrow the pattern or path."
    except OSError as e:
        return f"ERROR: grep failed: {e}"
    if res.returncode not in (0, 1):
        return f"ERROR: grep failed: {res.stderr.strip()[:500]}"
    if not res.stdout.strip():
        return "No matches found."
    # strip the worktree prefix so results read as relative paths
    out = res.stdout.replace(str(worktree.resolve()) + "/", "")
    return _cap(out, note=" — narrow the pattern or path for full results")


MAX_BASH_CHARS = 10000
# Hard cap on child output buffered in memory. subprocess.run(capture_output=True)
# buffers the whole stream before we can truncate it, so `cat /dev/zero` would OOM
# the process. We drain the pipe on a thread (so the child never blocks on a full
# pipe) but keep only the first MAX_BASH_CAPTURE_BYTES.
MAX_BASH_CAPTURE_BYTES = 1024 * 1024


def bash(worktree: Path, command: str, timeout: int = 120) -> str:
    reason = check_bash_command(command, worktree)
    if reason:
        return reason  # starts with "BLOCKED:"
    timeout = max(1, min(int(timeout), 600))
    # Note: child stdin is /dev/null (never the operator's stdin); worker commands
    # that read stdin receive EOF immediately instead of blocking.
    captured = run_capped(
        ["bash", "-c", command],
        cwd=str(worktree),
        env=build_env(home=worktree),
        timeout=timeout,
        cap=MAX_BASH_CAPTURE_BYTES,
    )
    out = captured.output.decode("utf-8", errors="replace").strip()
    note = " — bash output capped" if captured.truncated else ""
    if captured.timed_out:
        tail = f"\n{out}" if out else ""
        return _cap(f"ERROR: command timed out after {timeout}s.{tail}",
                    cap=MAX_BASH_CHARS, note=note)
    if captured.returncode is None and not captured.timed_out:
        return _cap(f"ERROR: bash failed: {captured.output.decode('utf-8', 'replace').strip()}", cap=MAX_BASH_CHARS, note=note)
    return _cap(f"exit code: {captured.returncode}\n{out}", cap=MAX_BASH_CHARS, note=note)
