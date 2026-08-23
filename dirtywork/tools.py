from __future__ import annotations

import difflib
import errno
import os
import re
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

# Read ONCE, at import, before any thread exists: os.umask is process-global
# and changing it is not thread-safe, so it can never be queried lazily inside
# a tool call. A brand-new file staged through _write_atomic is chmod'd to
# `0o644 & ~_UMASK`, which is exactly the mode
# _open_regular(..., O_CREAT, mode=0o644) produced before 0.10 -- masking a
# 0o666 base instead would silently drop the group/other read bits under a
# `umask 0` operator.
_UMASK = os.umask(0)
os.umask(_UMASK)

# Spec §2.2/§2.5: every staged write lands in a sibling temp named
# `.dw-tmp.<basename>.<8 lowercase hex>`. The random suffix is required because
# the WORKER controls sibling names; the sweep matches the full generated shape
# with an anchored regex (never a bare glob), so a worker file literally named
# `.dw-tmp.notes` is left alone.
TMP_PREFIX = ".dw-tmp."
TMP_NAME_RE = re.compile(r"\.dw-tmp\..+\.[0-9a-f]{8}")
# The same shape as TMP_NAME_RE written as a POSIX extended regex (it matches
# the WHOLE path, hence the leading `.*/`). GNU find's DEFAULT regextype is
# Emacs, which treats `{8}` literally and matches nothing -- the consumer MUST
# pass `-regextype posix-extended` alongside `-regex` (as the Task 6 sweep
# exec does). Kept here, beside TMP_NAME_RE, so the host sweep and the
# container sweep can never drift apart.
TMP_FIND_REGEX = r".*/\.dw-tmp\..+\.[0-9a-f]{8}"


def tmp_name(basename: str) -> str:
    """The staging name for a write to `basename`. The ONE generator: the host
    primitive uses it directly and DockerSandbox generates the name host-side
    with it too, passing it into the container as "$2" so worker-controlled
    bytes never reach the script text."""
    return f"{TMP_PREFIX}{basename}.{os.urandom(4).hex()}"


def is_temp_name(name: str) -> bool:
    """True for a name tmp_name() generated (spec §2.5). Anchored on the FULL
    shape: `.dw-tmp.notes` is a worker's file, not ours, and is never swept."""
    return bool(TMP_NAME_RE.fullmatch(name))


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


def _write_all(fd: int, data: bytes) -> None:
    """os.write may write fewer bytes than asked; loop until the buffer is
    gone. Raw os.write is used rather than a buffered file object precisely so
    there is no userspace buffer left to flush -- which is what lets
    _write_atomic surface a deferred write error from its os.close BEFORE it
    promotes the temp over the target."""
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _unlink_quietly(p: Path) -> None:
    """Remove a staging temp, ignoring the case where it is already gone. Never
    raises: it only ever runs on an unwind path that has its own outcome."""
    try:
        os.unlink(str(p))
    except OSError:
        pass


def _write_atomic(target: Path, data: bytes, *, path: str, verb: str = "write",
                  create_parents: bool = False, must_exist: bool = False):
    """Spec §2.2: write `data` to `target` so a failure or a kill during the
    write leaves the file byte-identical instead of truncated. Returns None on
    success or an `ERROR: …` string -- never an OSError, because a tool
    function's contract is to return its failure as text.

    `target` is the caller's already-containment-checked `_worktree_candidate`
    path (callers keep their own `resolve_in_worktree` call). `path` is the
    MODEL-FACING path string every message renders -- the caller's own
    argument -- so the refusals read exactly as they did before 0.10 rather
    than leaking an absolute host path. `verb` picks the generic wording:
    "write" -> `cannot write '<path>'`, "append" -> `cannot append to
    '<path>'`; the ELOOP and non-regular-file strings are shared verbatim
    between the two.

    `must_exist=True` turns off §2.2's new-file branch: an ENOENT probe then
    returns `_append_missing(path)` instead of staging a temp and creating the
    target. Spec §1.2 requires it -- "ENOENT on the probe is the does-not-exist
    error above, never §2.2's new-file branch" -- and it is what makes the host
    refuse the delete-between-read-and-write race the container already refuses
    with the append script's `[ -f "$1" ] || exit 2`. `append_file` is the only
    caller that passes it.

    Two branches deliberately keep today's in-place, non-atomic behaviour and
    are named in docs/machine-contract.md and docs/operating.md:
      * `st_nlink > 1` -- a hardlink is MEANT to see the write, so the shared
        inode is written through rather than replaced;
      * a directory this process cannot create a temp in (0555) -- a rename is
        impossible there, so the probe fd is the only write left.

    Robustness, not a security fix (spec §2.1): the O_NOFOLLOW refusals stay
    exactly as deterministic as today. A symlink present at call time refuses
    below; one that appears between the probe and os.replace is replaced AS A
    LINK, because rename(2) does not dereference its destination -- so nothing
    is ever written through it (spec §2.4)."""
    lead = "cannot write" if verb == "write" else "cannot append to"
    if create_parents:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return f"ERROR: {lead} '{path}': {e}"
    probe_fd = None
    try:
        # Side-effect-free probe: no O_CREAT, so a refusal never leaves a file
        # behind. O_NONBLOCK makes a FIFO with no reader fail with ENXIO
        # instead of hanging; O_NOFOLLOW closes the final-component symlink
        # TOCTOU.
        probe_fd = os.open(str(target),
                           os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as e:
        if e.errno == errno.ELOOP:
            return (f"ERROR: '{path}' is a symlink; writing through a symlink is not "
                    f"allowed even when its target is inside the worktree")
        if e.errno == errno.ENXIO:
            return f"ERROR: '{path}' is not a regular file (refusing FIFO/device/socket)"
        if e.errno != errno.ENOENT:
            return f"ERROR: {lead} '{path}': {e}"
        if must_exist:
            # Spec §1.2: for an append, ENOENT is the does-not-exist refusal,
            # never the new-file branch below -- the target must not be created
            # by a write that was only ever meant to extend it.
            return _append_missing(path)
        # ENOENT: there is no file yet. Fall through to the temp with no mode
        # to preserve and no fd to fall back to.
    try:
        st = None
        if probe_fd is not None:
            st = os.fstat(probe_fd)
            if not stat.S_ISREG(st.st_mode):
                return (f"ERROR: {lead} '{path}': '{target}' is not a regular file "
                        f"(refusing FIFO/device/socket)")
            if st.st_nlink > 1:
                try:
                    os.ftruncate(probe_fd, 0)
                    _write_all(probe_fd, data)
                    # The close IS the write's completion here: a deferred
                    # write error (ENOSPC/EIO) must surface as a returned
                    # string, not escape from the `finally` below. The handle
                    # is cleared first so the `finally` never double-closes.
                    fd, probe_fd = probe_fd, None
                    os.close(fd)
                except OSError as e:
                    return f"ERROR: {lead} '{path}': {e}"
                return None
        tmp = target.parent / tmp_name(target.name)
        try:
            tmp_fd = os.open(
                str(tmp),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600)
        except OSError as e:
            if probe_fd is not None and e.errno in (errno.EACCES, errno.EROFS):
                try:
                    os.ftruncate(probe_fd, 0)
                    _write_all(probe_fd, data)
                    # Same close-is-completion rule as the hardlink branch
                    # above: clear the handle before closing so a deferred
                    # write error returns as a string and the `finally`
                    # never double-closes.
                    fd, probe_fd = probe_fd, None
                    os.close(fd)
                except OSError as e2:
                    return f"ERROR: {lead} '{path}': {e2}"
                return None
            return f"ERROR: {lead} '{path}': {e}"
        # One catch boundary from here on (spec §2.2 step 4).
        try:
            _write_all(tmp_fd, data)
            os.fchmod(tmp_fd, stat.S_IMODE(st.st_mode) if st is not None
                      else 0o644 & ~_UMASK)
            # Closed BEFORE the promote so a deferred write error surfaces
            # while the target is still untouched. The handle is CLEARED
            # first: os.close consumes the fd whether or not it raises, so if
            # this close is the one that reports the deferred error, the
            # handlers below must not try to close it a second time -- an
            # EBADF out of an except arm would be a tool function raising.
            fd, tmp_fd = tmp_fd, None
            os.close(fd)
            os.replace(str(tmp), str(target))
        except OSError as e:
            if tmp_fd is not None:
                # Defensive: cleanup must not replace the real diagnosis with
                # its own errno.
                try:
                    os.close(tmp_fd)
                except OSError:
                    pass
            _unlink_quietly(tmp)
            return f"ERROR: {lead} '{path}': {e}"
        except BaseException:
            if tmp_fd is not None:
                try:
                    os.close(tmp_fd)
                except OSError:
                    pass
            _unlink_quietly(tmp)
            raise
        return None
    finally:
        # Defensive only: on every remaining path the probe fd was merely
        # probed/read (never written through), so a close error here is
        # meaningless -- it must never escape and override the real outcome
        # already being returned.
        if probe_fd is not None:
            try:
                os.close(probe_fd)
            except OSError:
                pass


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
    r"""Like `text.splitlines(keepends=True)`, except ONLY `"\n"` is treated
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
        return _not_utf8(path, tool)
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


def _result_too_big(size: int) -> str:
    """Spec §1.5/§1.2 cap 3: the ONE place the over-the-write-limit RESULT
    sentence is built. `_check_write_size` renders it from a buffer it holds;
    `append_file` renders it from a size it learned some other way (an
    `os.stat` on the host, a `stat -c %s` exec in the container), so both modes
    can refuse a file too large to even read with the same bytes."""
    return (f"ERROR: result is {size} bytes, over the {MAX_WRITE_BYTES}-byte "
            f"write limit; nothing was written")


def _append_oversized(encoded: bytes):
    """Spec §1.2 cap 1: append_file's own refusal for the `text` ARGUMENT, or
    None. Imported by dirtywork.sandbox.docker the way MAX_WRITE_BYTES already
    is, so both backends emit the byte-identical string.

    Deliberately NOT any of the three write-side strings, because an append's
    fix is a different action from a write's. The three, verbatim, are:
    docker._oversized's `ERROR: content is <n> bytes, over the <limit>-byte
    write limit` (no trailing advice); tools.write_file's own inline
    `ERROR: content is <n> bytes, over the <limit>-byte write limit; write the
    file in smaller pieces` (the `; write the file in smaller pieces` tail is
    host-only and exists nowhere in docker.py); and _result_too_big's
    `…; nothing was written`. None of them may ever surface from an append."""
    if len(encoded) > MAX_WRITE_BYTES:
        return (f"ERROR: text is {len(encoded)} bytes, over the {MAX_WRITE_BYTES}-byte "
                f"write limit; append in smaller pieces")
    return None


def _append_missing(path: str) -> str:
    """Spec §1.2: the does-not-exist refusal. Three call sites -- the host
    probe's ENOENT branch, docker's guard exec (rc 2) and docker's write exec
    (rc 2, which is how a delete BETWEEN the two execs still refuses
    correctly) -- so it is built here once."""
    return (f"ERROR: cannot append to '{path}': it does not exist; create it with "
            f"write_file first")


def _not_utf8(path: str, tool: str) -> str:
    """Spec §5.1: the ONE non-UTF-8 refusal. The host transform path has always
    worded it this way; from 0.10 docker's `_read_raw` renders it from here too
    (with the tool it was called for), so a binary file refuses identically in
    both modes and names the tool the model actually called."""
    return f"ERROR: {path} is not valid UTF-8 text; {tool} only works on text files"


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
        return _result_too_big(size)
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
    item is exactly {"old": str, "new": str} for a call routed through the
    registry -- but Sandbox.apply_edits is a public method (see its docstring
    in dirtywork.sandbox.Sandbox), so a malformed item is checked here too,
    before any matching, inside the same all-or-nothing pass."""
    total = len(edits)

    def transform(text: str):
        new_text = text
        for index, edit in enumerate(edits, 1):
            if (not isinstance(edit, dict)
                    or not isinstance(edit.get("old"), str)
                    or not isinstance(edit.get("new"), str)):
                return None, (
                    f"ERROR: edit {index} of {total}: each edit must be an object with "
                    f"string 'old' and 'new'; no edits applied"
                )
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


def append_file(worktree: Path, path: str, text: str) -> str:
    """Spec §1.2: append `text` VERBATIM to the end of an existing regular
    file. Nothing is inserted between the old bytes and the new ones -- the
    model owns line discipline, and APPEND_FILE_SPEC's description says so.

    Three caps fire in a fixed order, mirrored exactly by
    DockerSandbox.append_file, so neither backend can surface the other's
    wording: (1) the `text` ARGUMENT (_append_oversized), before the file is
    touched at all; (2) the current file's size against MAX_READ_BYTES; (3)
    the RESULT size. Caps 2 and 3 share ONE sentence (_result_too_big), so a
    file too large to read reads as un-appendable rather than surfacing
    read_file's "read limit" wording.

    The §2.2 probe runs UNCHANGED, so the symlink and FIFO refusals are
    byte-identical to write_file's -- except ENOENT, which for an append is
    the does-not-exist refusal, never §2.2's new-file branch. That holds for
    BOTH probes: this function's own, and _write_atomic's, which is told so
    with must_exist=True. The content is
    then read through a SECOND open of the same candidate path: O_NONBLOCK
    (which _open_regular always adds) is what keeps a FIFO swapped in between
    the two opens from blocking the read, and the read fd's st_ino/st_dev must
    match the probe's or the append refuses rather than pasting one file's
    bytes onto another inode. append_file NEVER creates parent directories."""
    encoded = text.encode("utf-8")
    too_big = _append_oversized(encoded)
    if too_big:
        return too_big
    try:
        resolve_in_worktree(path, worktree, writing=True)  # containment check only
    except GuardrailError as e:
        return f"ERROR: {e}"
    p = _worktree_candidate(path, worktree)
    try:
        probe_fd = os.open(str(p), os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as e:
        if e.errno == errno.ENOENT:
            return _append_missing(path)
        if e.errno == errno.ELOOP:
            return (f"ERROR: '{path}' is a symlink; writing through a symlink is not "
                    f"allowed even when its target is inside the worktree")
        if e.errno == errno.ENXIO:
            return f"ERROR: '{path}' is not a regular file (refusing FIFO/device/socket)"
        return f"ERROR: cannot append to '{path}': {e}"
    try:
        probe_st = os.fstat(probe_fd)
        if not stat.S_ISREG(probe_st.st_mode):
            return (f"ERROR: cannot append to '{path}': '{p}' is not a regular file "
                    f"(refusing FIFO/device/socket)")
        if probe_st.st_size > MAX_READ_BYTES or probe_st.st_size + len(encoded) > MAX_WRITE_BYTES:
            # Caps 2 and 3, decided before the read so a 6 MB file is never
            # loaded just to be refused. MAX_READ_BYTES == MAX_WRITE_BYTES
            # today, so the first test already implies the second; both are
            # written out so the rule survives the two constants diverging.
            return _result_too_big(probe_st.st_size + len(encoded))
        try:
            fh = _open_regular(p, os.O_RDONLY, max_size=MAX_READ_BYTES)
        except OSError as e:
            if e.errno is None:
                # _open_regular's two errno-less refusals -- the read cap and
                # the non-regular-file check -- which the probe fd disproved a
                # moment ago. Reaching here means the target was replaced
                # between the two opens; refuse with the append's own wording,
                # never read_file's (spec §1.2).
                return (f"ERROR: cannot append to '{path}': the file changed between "
                        f"opening it and reading it")
            return f"ERROR: cannot append to '{path}': {e}"
        try:
            read_st = os.fstat(fh.fileno())
            if (read_st.st_ino, read_st.st_dev) != (probe_st.st_ino, probe_st.st_dev):
                return (f"ERROR: cannot append to '{path}': the file changed between "
                        f"opening it and reading it")
            raw = fh.read()
        except OSError as e:
            return f"ERROR: cannot append to '{path}': {e}"
        finally:
            fh.close()
        try:
            old_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return _not_utf8(path, "append_file")
        if len(raw) + len(encoded) > MAX_WRITE_BYTES:
            # Re-checked against what was actually read: the probe's size is a
            # moment old, and the file may have grown in place since.
            return _result_too_big(len(raw) + len(encoded))
        # must_exist=True: spec §1.2 forbids §2.2's new-file branch here, so a
        # target deleted between the read and this probe refuses rather than
        # being re-created -- the same race docker's append write script
        # refuses with `[ -f "$1" ] || exit 2`.
        err = _write_atomic(p, raw + encoded, path=path, verb="append",
                            must_exist=True)
        if err:
            return err
        return describe_change(path, old_text, old_text + text, verb="Appended to")
    finally:
        os.close(probe_fd)


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


# Spec §4.2: grep's own (unchanged) timeout wording -- distinct from
# TIMEOUT_TEXT because a grep timeout is the harness searching on the
# worker's behalf, not a worker-run command, and does NOT count toward
# `timeouts` or the `timeout` nudge. One string shared by both backends.
GREP_TIMEOUT_TEXT = "ERROR: grep timed out after {timeout}s — narrow the pattern or path."


def grep_timeout_result(timeout: int) -> str:
    """The canonical result for a grep call that hit its timeout."""
    return GREP_TIMEOUT_TEXT.format(timeout=timeout)


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
        return grep_timeout_result(timeout)
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

# Spec §4.1: ONE canonical timed-out-command result, identical on both backends.
# TIMEOUT_PREFIX is the predicate everything downstream keys on -- the
# tool_result flag, the `timeout` nudge, the run's `timeouts` counter,
# `runs show`'s outcome class and the bench scoreboard -- so there is exactly
# one string in this codebase to keep in step.
TIMEOUT_PREFIX = "ERROR: command timed out after "
TIMEOUT_TEXT = (TIMEOUT_PREFIX + "{timeout}s — it did not finish and its result is "
                "unknown. Re-run it with a larger timeout (up to 600) or split it "
                "into smaller commands; do not report it as passed.")


def timeout_result(timeout: int) -> str:
    """The canonical result for a command that hit its timeout. No partial
    output is appended: the host CAN produce a tail (it captured one) and docker
    cannot, and parity wins -- a tail is exactly what a small model reads as
    "the command's result" when the truth is that the command never finished."""
    return TIMEOUT_TEXT.format(timeout=timeout)


def is_timeout_result(text) -> bool:
    """True for a bash result produced by timeout_result(). The ONE predicate --
    never re-derive this from a substring search somewhere else, or the two
    will drift the first time the wording changes."""
    return isinstance(text, str) and text.startswith(TIMEOUT_PREFIX)



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
        # Spec §4.1: no partial output, and no cap -- the canonical text is a
        # couple of hundred characters, far under MAX_BASH_CHARS.
        return timeout_result(timeout)
    if captured.returncode is None and not captured.timed_out:
        return _cap(f"ERROR: bash failed: {captured.output.decode('utf-8', 'replace').strip()}", cap=MAX_BASH_CHARS, note=note)
    return _cap(f"exit code: {captured.returncode}\n{out}", cap=MAX_BASH_CHARS, note=note)
