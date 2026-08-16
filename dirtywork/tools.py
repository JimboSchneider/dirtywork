from __future__ import annotations

import errno
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

from .guardrails import GuardrailError, build_env, check_bash_command, resolve_in_worktree
from .procs import run_capped

MAX_RESULT_CHARS = 8000
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
    lines = raw.decode("utf-8", errors="replace").splitlines()
    window = lines[offset : offset + limit]
    numbered = "\n".join(f"{i:6}\t{line}" for i, line in enumerate(window, offset + 1))
    if offset + limit < len(lines):
        numbered += (
            f"\n[showing lines {offset + 1}-{offset + len(window)} of {len(lines)}; "
            f"re-run with offset={offset + limit} for more]"
        )
    return _cap(numbered, note=" — re-run with offset/limit to see more")


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
    return f"Wrote {len(encoded)} bytes to {path}"


def edit_file(worktree: Path, path: str, old_string: str, new_string: str) -> str:
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
        return f"ERROR: {path} is not valid UTF-8 text; edit_file only works on text files"
    count = text.count(old_string)
    if count != 1:
        return (
            f"ERROR: old_string occurs {count} times in {path}; it must occur exactly "
            f"once. Include more surrounding context to make it unique."
        )
    new_text = text.replace(old_string, new_string, 1)
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
    return f"Edited {path}"


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


def _param(props: dict, required: list) -> dict:
    return {"type": "object", "properties": props, "required": required}


TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a file, returning numbered lines. Use offset/limit to "
                       "page through; files over ~5 MB or non-regular files are refused.",
        "parameters": _param({
            "path": {"type": "string", "description": "Path relative to worktree root"},
            "offset": {"type": "integer", "description": "0-based first line, default 0"},
            "limit": {"type": "integer", "description": "Max lines, default 400"},
        }, ["path"])}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Create or overwrite a file. Parent directories are created.",
        "parameters": _param({
            "path": {"type": "string"},
            "content": {"type": "string"},
        }, ["path", "content"])}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Replace old_string with new_string in a file. old_string "
                       "must occur exactly once — include surrounding context.",
        "parameters": _param({
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        }, ["path", "old_string", "new_string"])}},
    {"type": "function", "function": {
        "name": "list_dir",
        "description": "List a directory's entries (dirs end with /).",
        "parameters": _param({"path": {"type": "string", "description": "Default '.'"}}, [])}},
    {"type": "function", "function": {
        "name": "grep",
        "description": "Search file contents with a regex. Optional glob filter "
                       "like '*.cs' or '*.tsx'.",
        "parameters": _param({
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "Default '.'"},
            "glob": {"type": "string"},
        }, ["pattern"])}},
    {"type": "function", "function": {
        "name": "bash",
        "description": "Run a shell command in the worktree (cwd is the worktree "
                       "root). Use for builds/tests/git-status, NEVER for editing "
                       "files. 120s default timeout, 600s max. Backgrounded "
                       "processes are terminated when the command returns.",
        "parameters": _param({
            "command": {"type": "string"},
            "timeout": {"type": "integer", "description": "Seconds, default 120, max 600"},
        }, ["command"])}},
]


_TOOL_PARAMS = {s["function"]["name"]: set(s["function"]["parameters"]["properties"])
                for s in TOOL_SCHEMAS}


class ToolExecutor:
    """Dispatches validated tool calls onto a Sandbox. Unknown names raise
    KeyError. Deadline clamping for bash/grep and guardrail_block transcript
    logging are unchanged from the pre-sandbox executor."""

    def __init__(self, sandbox, transcript=None):
        self.sandbox = sandbox
        self.transcript = transcript
        self.deadline = None
        self._table = {
            "read_file": sandbox.read_file,
            "write_file": sandbox.write_file,
            "edit_file": sandbox.edit_file,
            "list_dir": sandbox.list_dir,
            "grep": sandbox.grep,
            "bash": sandbox.bash,
        }

    def execute(self, name: str, args: dict) -> str:
        fn = self._table[name]  # KeyError → runner counts a model failure
        # Local models routinely attach parameters from other harnesses' tool
        # schemas (e.g. Claude Code's `description` on bash). Dropping unknown
        # keys instead of raising TypeError keeps a habit from becoming three
        # "bad arguments" failures and an aborted run; missing/invalid REQUIRED
        # args still surface as TypeError from the tool function itself.
        allowed = _TOOL_PARAMS[name]
        args = {k: v for k, v in args.items() if k in allowed}
        if self.deadline is not None:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                return ("ERROR: run deadline exceeded; stop calling tools and "
                        "summarize what you have done.")
            if name in ("bash", "grep"):
                args = dict(args)
                default = 120 if name == "bash" else 30
                args["timeout"] = min(int(args.get("timeout", default)), max(1, int(remaining)))
        result = fn(**args)
        if result.startswith("BLOCKED:") and self.transcript is not None:
            self.transcript.write("guardrail_block", tool=name, args=args, reason=result)
        return result
