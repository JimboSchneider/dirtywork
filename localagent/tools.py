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
    p.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
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
        if e.is_dir():
            rows.append(f"{e.name}/")
        else:
            rows.append(f"{e.name}  ({e.stat().st_size} bytes)")
    return _cap("\n".join(rows) or "(empty directory)")


def grep(worktree: Path, pattern: str, path: str = ".", glob: str | None = None) -> str:
    try:
        p = resolve_in_worktree(path, worktree)
    except GuardrailError as e:
        return f"ERROR: {e}"
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
    if res.returncode not in (0, 1):
        return f"ERROR: grep failed: {res.stderr.strip()[:500]}"
    if not res.stdout.strip():
        return "No matches found."
    # strip the worktree prefix so results read as relative paths
    out = res.stdout.replace(str(worktree.resolve()) + "/", "")
    return _cap(out, note=" — narrow the pattern or path for full results")
