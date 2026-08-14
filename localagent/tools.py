from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .guardrails import GuardrailError, resolve_in_worktree

MAX_RESULT_CHARS = 8000


def _cap(text: str, cap: int = MAX_RESULT_CHARS, note: str = "") -> str:
    if len(text) <= cap:
        return text
    suffix = f"\n[output truncated at {cap} chars{note}]"
    return text[:cap] + suffix


def read_file(worktree: Path, path: str, offset: int = 0, limit: int = 400) -> str:
    try:
        p = resolve_in_worktree(path, worktree)
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except GuardrailError as e:
        return f"ERROR: {e}"
    except OSError as e:
        return f"ERROR: cannot read '{path}': {e}"
    window = lines[offset : offset + limit]
    numbered = "\n".join(f"{i:6}\t{line}" for i, line in enumerate(window, offset + 1))
    if offset + limit < len(lines):
        numbered += (
            f"\n[showing lines {offset + 1}-{offset + len(window)} of {len(lines)}; "
            f"re-run with offset={offset + limit} for more]"
        )
    return _cap(numbered, note=" — re-run with offset/limit to see more")


def write_file(worktree: Path, path: str, content: str) -> str:
    try:
        p = resolve_in_worktree(path, worktree, writing=True)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except GuardrailError as e:
        return f"ERROR: {e}"
    except OSError as e:
        return f"ERROR: cannot write '{path}': {e}"
    return f"Wrote {len(content.encode('utf-8'))} bytes to {path}"


def edit_file(worktree: Path, path: str, old_string: str, new_string: str) -> str:
    try:
        p = resolve_in_worktree(path, worktree, writing=True)
        text = p.read_text(encoding="utf-8")
    except GuardrailError as e:
        return f"ERROR: {e}"
    except OSError as e:
        return f"ERROR: cannot read '{path}': {e}"
    count = text.count(old_string)
    if count != 1:
        return (
            f"ERROR: old_string occurs {count} times in {path}; it must occur exactly "
            f"once. Include more surrounding context to make it unique."
        )
    try:
        p.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
    except OSError as e:
        return f"ERROR: cannot write '{path}': {e}"
    return f"Edited {path}"


def list_dir(worktree: Path, path: str = ".") -> str:
    try:
        p = resolve_in_worktree(path, worktree)
        entries = sorted(p.iterdir(), key=lambda e: e.name)
    except GuardrailError as e:
        return f"ERROR: {e}"
    except OSError as e:
        return f"ERROR: cannot list '{path}': {e}"
    rows = []
    for e in entries:
        try:
            if e.is_dir():
                rows.append(f"{e.name}/")
            else:
                rows.append(f"{e.name}  ({e.stat().st_size} bytes)")
        except OSError:
            rows.append(f"{e.name}  (broken symlink)")
    return _cap("\n".join(rows) or "(empty directory)")


def grep(worktree: Path, pattern: str, path: str = ".", glob: str | None = None) -> str:
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
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return "ERROR: grep timed out after 30s — narrow the pattern or path."
    except OSError as e:
        return f"ERROR: grep failed: {e}"
    if res.returncode not in (0, 1):
        return f"ERROR: grep failed: {res.stderr.strip()[:500]}"
    if not res.stdout.strip():
        return "No matches found."
    # strip the worktree prefix so results read as relative paths
    out = res.stdout.replace(str(worktree.resolve()) + "/", "")
    return _cap(out, note=" — narrow the pattern or path for full results")


from .guardrails import build_env, check_bash_command

MAX_BASH_CHARS = 10000


def bash(worktree: Path, command: str, timeout: int = 120) -> str:
    reason = check_bash_command(command)
    if reason:
        return reason  # starts with "BLOCKED:"
    timeout = max(1, min(int(timeout), 600))
    try:
        res = subprocess.run(
            ["bash", "-c", command],
            cwd=str(worktree),
            env=build_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {timeout}s."
    except OSError as e:
        return f"ERROR: bash failed: {e}"
    combined = (res.stdout + res.stderr).strip()
    return _cap(f"exit code: {res.returncode}\n{combined}", cap=MAX_BASH_CHARS)


def _param(props: dict, required: list) -> dict:
    return {"type": "object", "properties": props, "required": required}


TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a file, returning numbered lines. Large files are "
                       "windowed; use offset/limit to page through.",
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
                       "files. 120s default timeout, 600s max.",
        "parameters": _param({
            "command": {"type": "string"},
            "timeout": {"type": "integer", "description": "Seconds, default 120, max 600"},
        }, ["command"])}},
]


class ToolExecutor:
    """Dispatches validated tool calls. Unknown names raise KeyError."""

    def __init__(self, worktree: Path, transcript=None):
        self.worktree = worktree
        self.transcript = transcript
        self._table = {
            "read_file": read_file,
            "write_file": write_file,
            "edit_file": edit_file,
            "list_dir": list_dir,
            "grep": grep,
            "bash": bash,
        }

    def execute(self, name: str, args: dict) -> str:
        fn = self._table[name]  # KeyError → runner counts a model failure
        result = fn(self.worktree, **args)
        if result.startswith("BLOCKED:") and self.transcript is not None:
            self.transcript.write("guardrail_block", tool=name, args=args, reason=result)
        return result
