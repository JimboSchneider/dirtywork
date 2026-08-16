# SP2 Docker Sandbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace dirtywork's host-mode-only tool execution with a Docker sandbox backend that becomes the default in 0.3: every tool call (`read_file`, `write_file`, `edit_file`, `list_dir`, `grep`, `bash`) runs inside a locked-down container over a Docker volume, the worker's tree reaches the host only through a validated tar export, and `--sandbox none` remains an explicit opt-out to today's host behavior.

**Architecture:** A `Sandbox` Protocol (`start/read_file/write_file/edit_file/list_dir/grep/bash/finalize/stop`) abstracts tool execution; `HostSandbox` wraps the existing `tools.py` functions (host mode, SP1-hardened), `DockerSandbox` implements the same interface via `docker exec` against a container whose worktree lives on a Docker volume (never a bind mount — host git never touches worker content). Every `docker` invocation goes through one timeout-bearing `run()` wrapper; a watchdog thread polls host free space and worktree size; after the worker container is torn down, a **fresh** export container streams a validated tar into the still-empty host worktree, followed by one `git read-tree HEAD` (index only) so host `git status`/`git diff` become meaningful. `ToolExecutor` becomes a thin dispatcher over whichever `Sandbox` the CLI constructs.

**Tech Stack:** Python 3.9 stdlib only (subprocess, tarfile, threading, hashlib, dataclasses, pathlib). No new dependencies. Docker Desktop / dockerd is a required *runtime* dependency for the default mode only — `--sandbox none` needs none of it.

**Spec:** `docs/superpowers/specs/2026-08-15-review-response-design.md` — read the whole "Sub-project 2: Docker sandbox backend" section (decision record + §1–§9), the threat model, and the success criteria before starting. This plan argues from that spec; every task cites the spec section it implements.

## Global Constraints

- Python 3.9 floor: no `match`, no `X | Y` unions at runtime (only under `from __future__ import annotations`), no `tarfile.data_filter`, no `dataclass(slots=)`. `typing.Literal` is fine (present in 3.9).
- Stdlib only. No new dependencies.
- The stdout JSON contract may gain fields but must not lose or rename any (`status, worktree, branch, transcript, turns, usage, final_message`).
- Every existing test stays green after every task. Run `python -m pytest -q` at the end of each task.
- Commit after each task with a conventional message (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`).
- New tests go in the existing module test file for the file touched where one exists; new modules get `tests/test_<module>.py`.
- Tests that need Docker use marker `docker` (new; added to `pyproject.toml` markers and to `addopts` exclusion: `-m 'not live and not docker'`) and skip when Docker is not available. (Deliberate refinement of the spec's "-m live": `live` already means "needs LM Studio".)
- Never leave placeholders in a plan step: every code step shows the actual code; every test step shows the actual test.
- This plan assumes sub-project 1 (hardening) has already been executed and merged: `dirtywork/rundir.py` exists with `ensure_runs_dir`/`create_run_dir`/`RunDirError`; `dirtywork/budget.py` exists with `BudgetExceeded`/`BudgetReport`/`measure_worktree`/`DEFAULT_MAX_WORKTREE_MB`/`DEFAULT_MAX_WORKTREE_FILES`; `workspace.py` has `worktree_base_commit(worktree) -> str`, `host_diff_stat(worktree, cap=64_000) -> str`, and `load_repo_context(repo: Path, base_commit: str) -> str | None` (note the new required second argument); `runner.Runner.__init__` accepts `finalize: Callable[[], dict] | None = None` and `RunResult` has an `.extra: dict` field, merged from `finalize()` into `run_end`; `ToolExecutor` already runs a post-call budget check; `tools._open_regular` and `tools.MAX_WRITE_BYTES`/`tools.MAX_LIST_ENTRIES` exist; `__main__.py` already has `--max-worktree-mb`/`--max-worktree-files` flags and a `budget_exceeded` status. Where this plan modifies those SP1 shapes, the code shown is written against the SP1 shapes exactly as listed here.

---

## File Structure

- Create `dirtywork/procs.py` — `run_capped`, extracted from `tools.bash` (pure refactor, Task 1).
- Create `dirtywork/sandbox/__init__.py` — `Sandbox` Protocol, `RunArtifacts`, `SandboxError` (Task 2).
- Create `dirtywork/sandbox/host.py` — `HostSandbox` (Task 2).
- Create `dirtywork/sandbox/docker_cli.py` — `run`, timeout constants, `DockerError`, `docker_version`, `resolve_image`, `docker_storage_paths`, `validate_objects_dir` (Task 3).
- Create `dirtywork/sandbox/docker_args.py` — `DockerConfig` + pure argv builders + name/label helpers (Task 4).
- Create `dirtywork/sandbox/watchdog.py` — `Watchdog` (Task 9).
- Create `dirtywork/sandbox/export.py` — `extract_validated`, `export_run`, `ExportError`, `ExportReport` (Tasks 10–11).
- Create `dirtywork/sandbox/docker.py` — `DockerSandbox` (Tasks 6–9, 11).
- Create `docker/Dockerfile` and `docker/README.md` (Task 13).
- Modify `dirtywork/tools.py` — `bash` uses `procs.run_capped`; `ToolExecutor(sandbox, transcript)` dispatch (Tasks 1–2).
- Modify `dirtywork/rundir.py` — add `write_run_json`, `read_run_json` (Task 5).
- Modify `dirtywork/workspace.py` — add `create_worktree(..., no_checkout=False)` and `host_read_tree(worktree)` (Task 5).
- Modify `dirtywork/runner.py` — catch `SandboxError` → `sandbox_error`; `schema_version: 2` on `run_start` (Task 2).
- Modify `dirtywork/__main__.py` — new flags, docker-default preflight with exit-2 hint, `run.json` lifecycle, sandbox construction, `finally: sandbox.stop()`, stdout JSON `schema_version`/`run_dir` (Tasks 2, 12).
- Modify `pyproject.toml` — `docker` marker + addopts exclusion (Task 13).
- Modify `README.md`, `SECURITY.md` — §SP2.8 rewrite (Task 14).
- Tests: `tests/test_procs.py`, `tests/test_sandbox_host.py`, `tests/test_docker_cli.py`, `tests/test_docker_args.py`, `tests/test_export_validator.py`, `tests/test_export_flow.py`, `tests/test_watchdog.py`, `tests/test_docker_sandbox.py`, additions to `tests/test_main.py`, `tests/test_docker_live.py` (marker `docker`), `tests/test_docker_lifecycle.py` (marker `docker`).

---

### Task 1: Extract `procs.run_capped` from `tools.bash`

Pure refactor: `tools.bash`'s subprocess-draining machinery (thread-drained capture with a byte cap, timeout with process-group kill) becomes a reusable primitive that Docker-mode tool execs will also use (Task 7). Behavior must be bit-for-bit identical — every existing `test_tools_bash.py` test stays green unmodified.

**Files:**
- Create: `dirtywork/procs.py`
- Modify: `dirtywork/tools.py:157-235` (the `MAX_BASH_CHARS`/`MAX_BASH_CAPTURE_BYTES`/`_kill_group`/`bash` block)
- Test: `tests/test_procs.py`

**Interfaces:**
- Produces: `MAX_CAPTURE_BYTES = 1024 * 1024`; `@dataclass class Captured: returncode: int | None; output: bytes; truncated: bool; timed_out: bool`; `run_capped(argv: list[str], *, timeout: float, cwd=None, env=None, stdin: bytes | None = None, cap: int = MAX_CAPTURE_BYTES, kill_group: bool = True) -> Captured`.
- Consumes: nothing new (stdlib `subprocess`, `threading`, `os`, `signal`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_procs.py
from __future__ import annotations

import time

from dirtywork.procs import Captured, MAX_CAPTURE_BYTES, run_capped


def test_run_capped_returns_output_and_returncode():
    result = run_capped(["bash", "-c", "echo hi; exit 3"], timeout=5)
    assert isinstance(result, Captured)
    assert result.returncode == 3
    assert result.output.strip() == b"hi"
    assert result.truncated is False
    assert result.timed_out is False


def test_run_capped_caps_output():
    result = run_capped(
        ["python3", "-c", "import sys; sys.stdout.write('A' * 2_000_000)"],
        timeout=10, cap=1024,
    )
    assert len(result.output) <= 1024
    assert result.truncated is True
    assert result.returncode == 0


def test_run_capped_timeout_kills_group():
    start = time.monotonic()
    result = run_capped(
        ["bash", "-c", "(sleep 2 && touch /tmp/dirtywork_procs_survived) & wait"],
        timeout=1,
    )
    assert result.timed_out is True
    assert result.returncode is None
    elapsed = time.monotonic() - start
    assert elapsed < 3.0


def test_run_capped_passes_stdin_bytes():
    result = run_capped(["cat"], timeout=5, stdin=b"from stdin\n")
    assert result.output == b"from stdin\n"


def test_run_capped_respects_cwd_and_env():
    result = run_capped(["bash", "-c", "pwd && echo $MY_VAR"], timeout=5,
                         cwd="/tmp", env={"MY_VAR": "hello", "PATH": "/usr/bin:/bin"})
    assert b"/tmp" in result.output
    assert b"hello" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_procs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dirtywork.procs'`

- [ ] **Step 3: Write minimal implementation**

```python
# dirtywork/procs.py
from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass

MAX_CAPTURE_BYTES = 1024 * 1024


@dataclass
class Captured:
    returncode: int | None
    output: bytes
    truncated: bool
    timed_out: bool


def _kill_group(pid: int) -> None:
    """SIGKILL the whole process group led by pid (a no-op if already gone).

    On the clean-exit path pid is already reaped, so there is a negligible
    PID-reuse window; it would only signal an unrelated process that had both
    become a group leader AND reclaimed this exact pgid, which we accept.
    """
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def run_capped(argv: list, *, timeout: float, cwd=None, env=None,
               stdin: bytes | None = None, cap: int = MAX_CAPTURE_BYTES,
               kill_group: bool = True) -> Captured:
    """Run argv, capturing merged stdout+stderr up to `cap` bytes without
    buffering the whole stream in memory (a drain thread keeps the child's
    pipe from filling, even past the cap), and enforce `timeout` by killing
    the whole process group so backgrounded children cannot outlive the call.
    """
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=kill_group,
        )
    except OSError as e:
        return Captured(returncode=None, output=str(e).encode(), truncated=False,
                         timed_out=False)

    captured = bytearray()
    truncated = False
    lock = threading.Lock()

    def _drain() -> None:
        nonlocal truncated
        with proc.stdout:  # type: ignore[union-attr]
            for chunk in iter(lambda: proc.stdout.read(65536), b""):  # type: ignore[union-attr]
                with lock:
                    room = cap - len(captured)
                    if room > 0:
                        captured.extend(chunk[:room])
                    if len(chunk) > room:
                        truncated = True  # keep draining so the child never blocks

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()

    if stdin is not None:
        def _feed() -> None:
            try:
                proc.stdin.write(stdin)  # type: ignore[union-attr]
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    proc.stdin.close()  # type: ignore[union-attr]
                except OSError:
                    pass

        writer = threading.Thread(target=_feed, daemon=True)
        writer.start()
    else:
        writer = None

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True

    if kill_group:
        _kill_group(proc.pid)
    else:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    reader.join(timeout=5)
    if writer is not None:
        writer.join(timeout=5)

    with lock:
        out = bytes(captured)
        trunc = truncated
    returncode = None if timed_out else proc.returncode
    return Captured(returncode=returncode, output=out, truncated=trunc, timed_out=timed_out)
```

- [ ] **Step 4: Run the new tests**

Run: `python -m pytest tests/test_procs.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Rewire `tools.bash` onto `run_capped`, keeping the model-facing shape identical**

Open `dirtywork/tools.py`. Replace the block from `MAX_BASH_CHARS = 10000` (line 157) through the end of `bash()` (line 235) with:

```python
MAX_BASH_CHARS = 10000


def bash(worktree: Path, command: str, timeout: int = 120) -> str:
    reason = check_bash_command(command)
    if reason:
        return reason  # starts with "BLOCKED:"
    timeout = max(1, min(int(timeout), 600))
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
    return _cap(f"exit code: {captured.returncode}\n{out}", cap=MAX_BASH_CHARS, note=note)
```

Add `MAX_BASH_CAPTURE_BYTES = 1024 * 1024` where the old constant lived (keep the name — `ToolExecutor`/tests may still reference `tools.MAX_BASH_CAPTURE_BYTES`), and add the import at the top of `dirtywork/tools.py`:

```python
from .procs import run_capped
```

Remove the now-unused `signal`, `threading`, `time` imports only if nothing else in the file uses them — `time` is still used by `ToolExecutor.execute`'s deadline math, so keep it; `signal` and `threading` and `subprocess.Popen`/`PIPE`/`STDOUT` machinery for `bash` are no longer needed directly in `tools.py`, but `subprocess` itself is still used by `grep`, so keep the `import subprocess` line. Delete the old `_kill_group` function from `tools.py` (it now lives in `procs.py`).

- [ ] **Step 6: Run the full existing bash test suite**

Run: `python -m pytest tests/test_tools_bash.py -v`
Expected: PASS — all 11 pre-existing tests green, unmodified.

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS, same count as before this task plus the 5 new `test_procs.py` tests.

- [ ] **Step 8: Commit**

```bash
git add dirtywork/procs.py dirtywork/tools.py tests/test_procs.py
git commit -m "refactor: extract procs.run_capped from tools.bash"
```

---

### Task 2: `Sandbox` Protocol, `HostSandbox`, `ToolExecutor(sandbox)` dispatch, runner catches `SandboxError`

Introduces the abstraction every later task builds on: a `Sandbox` Protocol (spec §1) with exactly the six tool methods plus `start`/`finalize`/`stop`; `HostSandbox`, which wraps today's `tools.py` functions and moves the post-call worktree-budget check out of `ToolExecutor` and into itself; and a slimmed `ToolExecutor` that just dispatches `execute(name, args) -> str` to whichever sandbox it holds. `__main__.py` is updated to construct a `HostSandbox` — this task does **not** add Docker yet, it only proves the abstraction holds with the existing host behavior unchanged. All 185+ pre-existing tests stay green (some are edited in place, per spec, to construct through the new shapes — this is the one task in this plan where existing test files change rather than only gaining new tests, because the constructor signature they exercise is changing).

**Files:**
- Create: `dirtywork/sandbox/__init__.py`
- Create: `dirtywork/sandbox/host.py`
- Modify: `dirtywork/tools.py` (remove `ToolExecutor`, keep the plain functions and `TOOL_SCHEMAS`)
- Modify: `dirtywork/runner.py:1-10,89-131` (catch `SandboxError`, add `schema_version: 2`)
- Modify: `dirtywork/__main__.py` (construct `HostSandbox`, pass it to `ToolExecutor`)
- Modify: `tests/test_tools_bash.py`, `tests/test_runner.py` (`parts` fixture), `tests/test_main.py` (construction sites)
- Test: `tests/test_sandbox_host.py`

**Interfaces:**
- Consumes: `dirtywork.tools.{read_file,write_file,edit_file,list_dir,grep,bash,TOOL_SCHEMAS}` (unchanged functions); `dirtywork.budget.{measure_worktree,BudgetExceeded,BudgetReport,DEFAULT_MAX_WORKTREE_MB,DEFAULT_MAX_WORKTREE_FILES}` (SP1); `dirtywork.workspace.host_diff_stat(worktree, cap=64_000) -> str` (SP1).
- Produces: `dirtywork.sandbox.SandboxError(Exception)`; `dirtywork.sandbox.RunArtifacts` dataclass (`diff_stat: str = ""`, `patch_path: str | None = None`, `worktree_bytes: int | None = None`, `worktree_files: int | None = None`, `escaping_symlinks: list = field(default_factory=list)`, `dropped_git_entries: list = field(default_factory=list)`, `export_status: str = "ok"`); `dirtywork.sandbox.Sandbox` Protocol; `dirtywork.sandbox.host.HostSandbox` with `__init__(self, worktree: Path, *, max_worktree_mb=DEFAULT_MAX_WORKTREE_MB, max_worktree_files=DEFAULT_MAX_WORKTREE_FILES)`; `dirtywork.tools.ToolExecutor.__init__(self, sandbox, transcript=None)` and unchanged `execute(name, args) -> str` contract (still raises `KeyError` for unknown tool names, still logs `guardrail_block` for `BLOCKED:` results, still clamps `bash`/`grep` timeouts to the deadline).

- [ ] **Step 1: Write the failing test for the Sandbox scaffolding**

```python
# tests/test_sandbox_host.py
from __future__ import annotations

from pathlib import Path

import pytest

from dirtywork.sandbox import RunArtifacts, SandboxError
from dirtywork.sandbox.host import HostSandbox


@pytest.fixture()
def wt(tmp_path: Path) -> Path:
    (tmp_path / "hello.txt").write_text("hi\n")
    return tmp_path


def test_run_artifacts_defaults():
    ra = RunArtifacts()
    assert ra.diff_stat == ""
    assert ra.patch_path is None
    assert ra.worktree_bytes is None
    assert ra.worktree_files is None
    assert ra.escaping_symlinks == []
    assert ra.dropped_git_entries == []
    assert ra.export_status == "ok"


def test_sandbox_error_is_exception():
    assert issubclass(SandboxError, Exception)


def test_host_sandbox_start_is_noop_and_read_file_works(wt: Path):
    sb = HostSandbox(wt)
    sb.start(wt, wt, "slug", "deadbeef")
    assert "hi" in sb.read_file("hello.txt")


def test_host_sandbox_write_edit_list_grep_bash(wt: Path):
    sb = HostSandbox(wt)
    sb.start(wt, wt, "slug", "deadbeef")
    assert "Wrote" in sb.write_file("new.txt", "content")
    assert "Edited" in sb.edit_file("new.txt", "content", "changed")
    assert "new.txt" in sb.list_dir(".")
    assert "hello.txt" in sb.grep("hi")
    out = sb.bash("echo hi")
    assert "exit code: 0" in out
    assert "hi" in out


def test_host_sandbox_finalize_returns_run_artifacts(wt: Path):
    sb = HostSandbox(wt)
    sb.start(wt, wt, "slug", "deadbeef")
    artifacts = sb.finalize()
    assert isinstance(artifacts, RunArtifacts)
    assert artifacts.worktree_bytes is not None
    assert artifacts.worktree_files is not None


def test_host_sandbox_stop_is_noop(wt: Path):
    sb = HostSandbox(wt)
    sb.start(wt, wt, "slug", "deadbeef")
    sb.stop()  # must not raise


def test_host_sandbox_bash_raises_budget_exceeded_over_cap(wt: Path):
    from dirtywork.budget import BudgetExceeded
    sb = HostSandbox(wt, max_worktree_mb=1, max_worktree_files=1)
    sb.start(wt, wt, "slug", "deadbeef")
    big = wt / "big.bin"
    with pytest.raises(BudgetExceeded):
        sb.bash("dd if=/dev/zero of=big2.bin bs=1M count=5 2>/dev/null")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sandbox_host.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dirtywork.sandbox'`

- [ ] **Step 3: Write `dirtywork/sandbox/__init__.py`**

```python
# dirtywork/sandbox/__init__.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class SandboxError(Exception):
    """Raised when a sandbox backend cannot complete an operation the runner
    depends on (container lifecycle failure, docker CLI timeout/expiry, export
    failure that must abort the run). Caught by Runner and turned into status
    'sandbox_error'."""


@dataclass
class RunArtifacts:
    """What a Sandbox reports at the end of a run. export_status is one of
    "ok", f"export_failed: {reason}", or "n/a" (host mode never exports)."""
    diff_stat: str = ""
    patch_path: str | None = None
    worktree_bytes: int | None = None
    worktree_files: int | None = None
    escaping_symlinks: list = field(default_factory=list)
    dropped_git_entries: list = field(default_factory=list)
    export_status: str = "ok"


class Sandbox(Protocol):
    """Every tool call and the run's start/finalize/stop lifecycle go through
    exactly this surface. HostSandbox (dirtywork.sandbox.host) and
    DockerSandbox (dirtywork.sandbox.docker) both implement it; ToolExecutor
    never knows which one it holds."""

    def start(self, worktree: Path, repo: Path, slug: str, base_commit: str) -> None: ...

    def read_file(self, path: str, offset: int = 0, limit: int = 400) -> str: ...

    def write_file(self, path: str, content: str) -> str: ...

    def edit_file(self, path: str, old_string: str, new_string: str) -> str: ...

    def list_dir(self, path: str = ".") -> str: ...

    def grep(self, pattern: str, path: str = ".", glob: str | None = None,
             timeout: int = 30) -> str: ...

    def bash(self, command: str, timeout: int = 120) -> str: ...

    def finalize(self) -> RunArtifacts: ...

    def stop(self) -> None: ...
```

- [ ] **Step 4: Write `dirtywork/sandbox/host.py`**

```python
# dirtywork/sandbox/host.py
from __future__ import annotations

from pathlib import Path

from .. import tools
from ..budget import (
    DEFAULT_MAX_WORKTREE_FILES,
    DEFAULT_MAX_WORKTREE_MB,
    BudgetExceeded,
    measure_worktree,
)
from ..workspace import host_diff_stat
from . import RunArtifacts


class HostSandbox:
    """Wraps today's tools.py functions unchanged (plus SP1 hardening). The
    worktree-budget check that used to live in ToolExecutor now lives here:
    every mutating call re-measures the worktree afterward and raises
    BudgetExceeded on violation, exactly as the pre-SP2 ToolExecutor did."""

    def __init__(self, worktree: Path, *, max_worktree_mb: int = DEFAULT_MAX_WORKTREE_MB,
                 max_worktree_files: int = DEFAULT_MAX_WORKTREE_FILES):
        self.worktree = worktree
        self.max_worktree_mb = max_worktree_mb
        self.max_worktree_files = max_worktree_files

    def start(self, worktree: Path, repo: Path, slug: str, base_commit: str) -> None:
        self.worktree = worktree  # host mode: no container to create

    def _check_budget(self) -> None:
        report = measure_worktree(self.worktree, max_bytes=self.max_worktree_mb * 1024 * 1024,
                                   max_files=self.max_worktree_files)
        if report.violation:
            raise BudgetExceeded(report.violation)

    def read_file(self, path: str, offset: int = 0, limit: int = 400) -> str:
        return tools.read_file(self.worktree, path, offset, limit)

    def write_file(self, path: str, content: str) -> str:
        result = tools.write_file(self.worktree, path, content)
        self._check_budget()
        return result

    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        result = tools.edit_file(self.worktree, path, old_string, new_string)
        self._check_budget()
        return result

    def list_dir(self, path: str = ".") -> str:
        return tools.list_dir(self.worktree, path)

    def grep(self, pattern: str, path: str = ".", glob: str | None = None,
             timeout: int = 30) -> str:
        return tools.grep(self.worktree, pattern, path, glob, timeout)

    def bash(self, command: str, timeout: int = 120) -> str:
        result = tools.bash(self.worktree, command, timeout)
        self._check_budget()
        return result

    def finalize(self) -> RunArtifacts:
        report = measure_worktree(self.worktree, max_bytes=self.max_worktree_mb * 1024 * 1024,
                                   max_files=self.max_worktree_files)
        return RunArtifacts(
            diff_stat=host_diff_stat(self.worktree),
            worktree_bytes=report.bytes,
            worktree_files=report.files,
            escaping_symlinks=list(report.escaping_symlinks),
            export_status="n/a",
        )

    def stop(self) -> None:
        pass  # no container/volume to tear down in host mode
```

- [ ] **Step 5: Run the new tests**

Run: `python -m pytest tests/test_sandbox_host.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Slim `ToolExecutor` to dispatch onto a `Sandbox`**

In `dirtywork/tools.py`, replace the `ToolExecutor` class (the block starting `class ToolExecutor:` at line 294 through the end of the file) with:

```python
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
```

Keep `import time` at the top of `tools.py` (still needed here). The plain functions (`read_file`, `write_file`, `edit_file`, `list_dir`, `grep`, `bash`), `TOOL_SCHEMAS`, `_cap`, `_guard_readable`, `MAX_RESULT_CHARS`, `MAX_READ_BYTES`, `MAX_BASH_CHARS`, `MAX_BASH_CAPTURE_BYTES` all stay in `tools.py` unchanged — `HostSandbox` calls them directly.

- [ ] **Step 7: Update `tests/test_tools_bash.py`'s executor tests to construct through `HostSandbox`**

Replace the `ToolExecutor` import and the tests that construct `ToolExecutor(wt)` directly:

```python
# tests/test_tools_bash.py — replace the import line
from dirtywork.sandbox.host import HostSandbox
from dirtywork.tools import TOOL_SCHEMAS, ToolExecutor, bash, grep
from dirtywork.transcript import Transcript
```

Replace each of these four tests (keep everything else in the file unchanged):

```python
def test_executor_dispatch_and_unknown(wt: Path):
    ex = ToolExecutor(HostSandbox(wt))
    assert "hi" in ex.execute("read_file", {"path": "hello.txt"})
    with pytest.raises(KeyError):
        ex.execute("format_disk", {})


def test_executor_deadline_exceeded_blocks_execution(wt: Path):
    ex = ToolExecutor(HostSandbox(wt))
    ex.deadline = time.monotonic() - 1
    out = ex.execute("bash", {"command": "touch created.txt"})
    assert "deadline exceeded" in out.lower()
    assert not (wt / "created.txt").exists()


def test_executor_clamps_bash_timeout_to_remaining_deadline(wt: Path):
    captured = {}

    def fake_bash(command, timeout=120):
        captured["timeout"] = timeout
        return "exit code: 0\n"

    ex = ToolExecutor(HostSandbox(wt))
    ex._table["bash"] = fake_bash
    ex.deadline = time.monotonic() + 3

    ex.execute("bash", {"command": "true", "timeout": 600})

    assert captured["timeout"] <= 3
    assert captured["timeout"] >= 1


def test_executor_clamps_grep_timeout_to_remaining_deadline(wt: Path):
    captured = {}

    def fake_grep(pattern, path=".", glob=None, timeout=30):
        captured["timeout"] = timeout
        return "No matches found."

    ex = ToolExecutor(HostSandbox(wt))
    ex._table["grep"] = fake_grep
    ex.deadline = time.monotonic() + 3

    ex.execute("grep", {"pattern": "hi"})

    assert captured["timeout"] <= 3
    assert captured["timeout"] >= 1
```

And:

```python
def test_executor_logs_guardrail_block(wt: Path, tmp_path: Path):
    t = Transcript(tmp_path / "log.jsonl")
    ex = ToolExecutor(HostSandbox(wt), transcript=t)
    out = ex.execute("bash", {"command": "git push"})
    t.close()
    assert out.startswith("BLOCKED:")
    events = [json.loads(l) for l in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert any(e["event"] == "guardrail_block" for e in events)
```

- [ ] **Step 8: Update `tests/test_runner.py`'s `parts` fixture**

Replace the `parts` fixture and its imports:

```python
# tests/test_runner.py — add this import alongside the existing ones
from dirtywork.sandbox.host import HostSandbox
```

```python
@pytest.fixture()
def parts(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "f.txt").write_text("data\n")
    transcript = Transcript(tmp_path / "t.jsonl")
    executor = ToolExecutor(HostSandbox(wt), transcript=transcript)
    return wt, executor, transcript, tmp_path
```

- [ ] **Step 9: Runner catches `SandboxError`, `run_start` gains `schema_version: 2`**

In `dirtywork/runner.py`, add the import at the top:

```python
from .sandbox import SandboxError
```

In `Runner.run`, change the `self.transcript.write("run_start", ...)` call (line 96-98) to include `schema_version`:

```python
        self.transcript.write("run_start", task=task, model=self.model,
                              max_turns=self.max_turns, timeout=self.timeout,
                              schema_version=2, **(self.run_info or {}))
```

Wrap the `self.executor.execute(name, args)` call (inside the `for tc in tool_calls:` loop, currently at line 190) so a `SandboxError` ends the run immediately instead of being counted as a per-tool-call failure — it means the sandbox itself is unusable, not that the model made a mistake:

```python
                for tc in tool_calls:
                    fn_info = tc.get("function") or {}
                    name = fn_info.get("name") or ""
                    raw_args = fn_info.get("arguments") or "{}"
                    call_id = tc.get("id", "")
                    try:
                        args = json.loads(raw_args)
                        if not isinstance(args, dict):
                            raise ValueError("arguments must be a JSON object")
                        result = self.executor.execute(name, args)
                        failures = 0
                    except SandboxError as e:
                        return finish("sandbox_error", str(e))
                    except (json.JSONDecodeError, ValueError) as e:
```

(This replaces just the `try:`/`except (json.JSONDecodeError, ValueError) as e:` pair at the top of that block — the rest of the `except` chain below it, `KeyError`/`TypeError`, is unchanged.)

- [ ] **Step 10: Wire `__main__.py` through `HostSandbox`**

In `dirtywork/__main__.py`, add the import:

```python
from .sandbox.host import HostSandbox
```

Replace the executor construction line inside the `try:` block (currently `executor = ToolExecutor(worktree, transcript=transcript)`):

```python
        sandbox = HostSandbox(worktree, max_worktree_mb=args.max_worktree_mb,
                               max_worktree_files=args.max_worktree_files)
        sandbox.start(worktree, repo, slug, worktree_base_commit(worktree))
        executor = ToolExecutor(sandbox, transcript=transcript)
```

(`args.max_worktree_mb`/`args.max_worktree_files` and `worktree_base_commit` are SP1 additions already present — `--max-worktree-mb` defaults to 2048, `--max-worktree-files` to 200000. Import `worktree_base_commit` alongside the other `workspace` imports at the top of the file.)

Change the line just above the `try:` block from `transcript = None` to also initialize `sandbox`:

```python
    transcript = None
    sandbox = None
    try:
```

Replace the `finally:` clause at the end of `main()` (currently only closing the transcript) so it also stops the sandbox, sandbox first (its teardown may itself write nothing to the transcript, but closing the transcript first would lose any errors `stop()` chose to log):

```python
    finally:
        if sandbox is not None:
            try:
                sandbox.stop()
            except Exception:
                pass
        if transcript is not None:
            try:
                transcript.close()
            except Exception:
                pass
```

- [ ] **Step 11: Update `tests/test_main.py`'s monkeypatches that reach into `__main__`**

`test_transcript_closed_even_on_unexpected_error` and `test_transcript_construction_failure_still_prints_json` both monkeypatch `m.Transcript` and rely on `main()` reaching the `finally` clause cleanly. Since `sandbox = None` is now set before `Transcript` construction can fail, both tests pass unmodified — `sandbox` is `None` when `Transcript()` raises, so `sandbox.stop()` is skipped. No test code changes needed in this file for this task; run it to confirm.

- [ ] **Step 12: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS, all pre-existing tests plus the 7 new `test_sandbox_host.py` tests green.

- [ ] **Step 13: Commit**

```bash
git add dirtywork/sandbox/__init__.py dirtywork/sandbox/host.py dirtywork/tools.py \
        dirtywork/runner.py dirtywork/__main__.py tests/test_sandbox_host.py \
        tests/test_tools_bash.py tests/test_runner.py
git commit -m "feat: introduce Sandbox protocol and HostSandbox, slim ToolExecutor to a dispatcher"
```

---

### Task 3: `docker_cli` — the one timeout-bearing entry point to the `docker` binary

Implements spec §3's "Docker control plane" paragraph: every `docker` invocation goes through one wrapper with an explicit timeout, expiry fails closed with a named command. Also implements spec §2 step 1's object-store bind-source validation (the only host path ever mounted into a container) and the digest-resolution / storage-path helpers preflight needs. No real daemon is touched by any test in this task — every test injects a fake `run`.

**Files:**
- Create: `dirtywork/sandbox/docker_cli.py`
- Test: `tests/test_docker_cli.py`

**Interfaces:**
- Consumes: `dirtywork.procs.{Captured, run_capped}`; `dirtywork.sandbox.SandboxError`; `dirtywork.workspace.WorkspaceError` (SP1).
- Produces: `class DockerError(SandboxError)`; `T_QUERY = 10`, `T_LIFECYCLE = 60`, `T_PULL = 600`, `T_EXPORT_STEP = 300`; `run(argv: list, *, timeout: float, stdin: bytes | None = None) -> Captured`; `docker_version(*, run=run) -> str`; `resolve_image(image: str, *, run=run, pinned_digest: str | None = None) -> str`; `docker_storage_paths(*, run=run) -> list`; `validate_objects_dir(repo: Path) -> Path`.

Note on `resolve_image`'s `pinned_digest` keyword: the spec ties digest pinning to `docker_args.PINNED_DIGEST` (Task 4), but `docker_args.py` does not exist yet in this task and `docker_cli.py` must not depend forward on it. `resolve_image` therefore takes `pinned_digest` as an additive keyword-only parameter (default `None`, meaning "no pin check") — Task 6's `DockerSandbox.start` is the caller that passes `pinned_digest=docker_args.PINNED_DIGEST`. This keeps `resolve_image(image, run=run)` fully valid on its own.

- [ ] **Step 1: Write the failing tests for `run` and `docker_version`**

```python
# tests/test_docker_cli.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dirtywork.procs import Captured
from dirtywork.sandbox.docker_cli import (
    DockerError,
    T_QUERY,
    docker_storage_paths,
    docker_version,
    resolve_image,
    run,
    validate_objects_dir,
)
from dirtywork.workspace import WorkspaceError


def test_run_prefixes_docker(monkeypatch):
    seen = {}

    def fake_run_capped(argv, *, timeout, stdin=None, cap=None, cwd=None, env=None, kill_group=True):
        seen["argv"] = argv
        seen["timeout"] = timeout
        return Captured(returncode=0, output=b"ok", truncated=False, timed_out=False)

    monkeypatch.setattr("dirtywork.sandbox.docker_cli.run_capped", fake_run_capped)
    result = run(["version"], timeout=T_QUERY)
    assert seen["argv"] == ["docker", "version"]
    assert seen["timeout"] == T_QUERY
    assert result.returncode == 0


def test_run_raises_dockererror_on_timeout(monkeypatch):
    def fake_run_capped(argv, **kwargs):
        return Captured(returncode=None, output=b"", truncated=False, timed_out=True)

    monkeypatch.setattr("dirtywork.sandbox.docker_cli.run_capped", fake_run_capped)
    with pytest.raises(DockerError) as exc_info:
        run(["exec", "dw-x", "/bin/true"], timeout=5)
    assert "exec" in str(exc_info.value)
    assert "5" in str(exc_info.value)


def test_docker_version_returns_string_on_success():
    def fake_run(argv, *, timeout, stdin=None):
        assert argv == ["version", "--format", "{{.Server.Version}}"]
        return Captured(returncode=0, output=b"29.7.2\n", truncated=False, timed_out=False)

    assert docker_version(run=fake_run) == "29.7.2"


def test_docker_version_raises_on_nonzero(monkeypatch):
    def fake_run(argv, *, timeout, stdin=None):
        return Captured(returncode=1, output=b"Cannot connect to the Docker daemon",
                         truncated=False, timed_out=False)

    with pytest.raises(DockerError, match="Cannot connect"):
        docker_version(run=fake_run)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_docker_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dirtywork.sandbox.docker_cli'`

- [ ] **Step 3: Write `dirtywork/sandbox/docker_cli.py`, part 1 (`run`, `docker_version`)**

```python
# dirtywork/sandbox/docker_cli.py
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from ..procs import Captured, run_capped
from ..workspace import WorkspaceError
from . import SandboxError

T_QUERY = 10        # version/inspect/top/volume *
T_LIFECYCLE = 60     # create/start/exec true/rm -f/kill
T_PULL = 600         # pull
T_EXPORT_STEP = 300  # each export docker exec


class DockerError(SandboxError):
    """Raised on a nonzero docker CLI exit or an expired timeout. Callers
    turn this into status sandbox_error (via the runner catching
    SandboxError) or, at preflight, into an exit-2 hint."""


def run(argv: list, *, timeout: float, stdin: bytes | None = None) -> Captured:
    """The one entry point to the docker CLI. Prefixes argv with "docker" and
    converts a timeout into a DockerError naming the command, instead of
    silently returning a Captured with timed_out=True — every docker call in
    this codebase must fail loud, not be ignored by an incomplete caller."""
    full = ["docker"] + list(argv)
    captured = run_capped(full, timeout=timeout, stdin=stdin)
    if captured.timed_out:
        raise DockerError(f"docker {' '.join(str(a) for a in argv)} timed out after {timeout}s")
    return captured


def docker_version(*, run=run) -> str:
    captured = run(["version", "--format", "{{.Server.Version}}"], timeout=T_QUERY)
    if captured.returncode != 0:
        raise DockerError(
            f"docker version failed: {captured.output.decode('utf-8', 'replace')[:500]}"
        )
    return captured.output.decode("utf-8", "replace").strip()
```

- [ ] **Step 4: Run the tests so far**

Run: `python -m pytest tests/test_docker_cli.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Write the failing tests for `resolve_image`**

Add to `tests/test_docker_cli.py`:

```python
def test_resolve_image_uses_repodigests_when_present():
    calls = []

    def fake_run(argv, *, timeout, stdin=None):
        calls.append(argv)
        digests = ["dirtywork/worker@sha256:" + "a" * 64]
        return Captured(returncode=0, output=json.dumps(digests).encode(),
                         truncated=False, timed_out=False)

    ref = resolve_image("dirtywork/worker:0.3", run=fake_run)
    assert ref == "dirtywork/worker@sha256:" + "a" * 64
    assert calls == [["image", "inspect", "--format", "{{json .RepoDigests}}",
                       "dirtywork/worker:0.3"]]


def test_resolve_image_pulls_when_absent_then_resolves():
    calls = []

    def fake_run(argv, *, timeout, stdin=None):
        calls.append(argv)
        if argv[0] == "pull":
            return Captured(returncode=0, output=b"", truncated=False, timed_out=False)
        if calls.count(argv) == 1 and argv[0] == "image":
            # first inspect: absent
            if len([c for c in calls if c[0] == "image"]) == 1:
                return Captured(returncode=1, output=b"no such image", truncated=False, timed_out=False)
        digests = ["dirtywork/worker@sha256:" + "b" * 64]
        return Captured(returncode=0, output=json.dumps(digests).encode(),
                         truncated=False, timed_out=False)

    ref = resolve_image("dirtywork/worker:0.3", run=fake_run)
    assert ref == "dirtywork/worker@sha256:" + "b" * 64
    assert ["pull", "dirtywork/worker:0.3"] in calls


def test_resolve_image_pull_failure_raises():
    def fake_run(argv, *, timeout, stdin=None):
        if argv[0] == "pull":
            return Captured(returncode=1, output=b"not found", truncated=False, timed_out=False)
        return Captured(returncode=1, output=b"no such image", truncated=False, timed_out=False)

    with pytest.raises(DockerError, match="pull"):
        resolve_image("dirtywork/worker:0.3", run=fake_run)


def test_resolve_image_falls_back_to_id_when_repodigests_empty():
    def fake_run(argv, *, timeout, stdin=None):
        if argv == ["image", "inspect", "--format", "{{json .RepoDigests}}", "dirtywork/worker:0.3"]:
            return Captured(returncode=0, output=b"[]", truncated=False, timed_out=False)
        if argv == ["image", "inspect", "--format", "{{.Id}}", "dirtywork/worker:0.3"]:
            return Captured(returncode=0, output=b"sha256:" + b"c" * 64,
                             truncated=False, timed_out=False)
        raise AssertionError(f"unexpected argv {argv}")

    ref = resolve_image("dirtywork/worker:0.3", run=fake_run)
    assert ref == "dirtywork/worker@sha256:" + "c" * 64


def test_resolve_image_pinned_digest_mismatch_raises():
    def fake_run(argv, *, timeout, stdin=None):
        digests = ["dirtywork/worker@sha256:" + "a" * 64]
        return Captured(returncode=0, output=json.dumps(digests).encode(),
                         truncated=False, timed_out=False)

    with pytest.raises(DockerError, match="PINNED_DIGEST"):
        resolve_image("dirtywork/worker:0.3", run=fake_run, pinned_digest="sha256:" + "z" * 64)


def test_resolve_image_pinned_digest_match_passes():
    def fake_run(argv, *, timeout, stdin=None):
        digests = ["dirtywork/worker@sha256:" + "a" * 64]
        return Captured(returncode=0, output=json.dumps(digests).encode(),
                         truncated=False, timed_out=False)

    ref = resolve_image("dirtywork/worker:0.3", run=fake_run, pinned_digest="sha256:" + "a" * 64)
    assert ref == "dirtywork/worker@sha256:" + "a" * 64
```

- [ ] **Step 6: Run to verify the new tests fail**

Run: `python -m pytest tests/test_docker_cli.py -v`
Expected: FAIL — `resolve_image` does not exist yet.

- [ ] **Step 7: Implement `resolve_image`**

Append to `dirtywork/sandbox/docker_cli.py`:

```python
def resolve_image(image: str, *, run=run, pinned_digest: str | None = None) -> str:
    """Resolve image to <name>@sha256:<digest>, pulling if absent (the only
    network use at preflight). Falls back to .Id for locally-built images
    with no RepoDigests. If pinned_digest is given, the resolved digest must
    match it exactly or the run refuses to start."""
    name = image.split("@")[0].split(":")[0]

    captured = run(["image", "inspect", "--format", "{{json .RepoDigests}}", image], timeout=T_QUERY)
    if captured.returncode != 0:
        pulled = run(["pull", image], timeout=T_PULL)
        if pulled.returncode != 0:
            raise DockerError(
                f"docker pull {image} failed: {pulled.output.decode('utf-8', 'replace')[:500]}"
            )
        captured = run(["image", "inspect", "--format", "{{json .RepoDigests}}", image], timeout=T_QUERY)
        if captured.returncode != 0:
            raise DockerError(
                f"docker image inspect {image} failed after pull: "
                f"{captured.output.decode('utf-8', 'replace')[:500]}"
            )

    raw = captured.output.decode("utf-8", "replace").strip()
    try:
        digests = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        digests = []

    ref = None
    if isinstance(digests, list):
        for d in digests:
            if isinstance(d, str) and d.startswith(name + "@sha256:"):
                ref = d
                break

    if ref is None:
        id_captured = run(["image", "inspect", "--format", "{{.Id}}", image], timeout=T_QUERY)
        if id_captured.returncode != 0:
            raise DockerError(
                f"cannot resolve a digest for {image}: "
                f"{id_captured.output.decode('utf-8', 'replace')[:500]}"
            )
        image_id = id_captured.output.decode("utf-8", "replace").strip()
        ref = f"{name}@{image_id}"

    if pinned_digest is not None:
        digest_part = ref.split("@", 1)[1] if "@" in ref else ""
        if digest_part != pinned_digest:
            raise DockerError(
                f"resolved image digest {digest_part!r} for {image!r} does not match "
                f"PINNED_DIGEST {pinned_digest!r}; refusing to run an unpinned image"
            )
    return ref
```

- [ ] **Step 8: Run to verify `resolve_image` tests pass**

Run: `python -m pytest tests/test_docker_cli.py -v`
Expected: PASS (10 tests)

- [ ] **Step 9: Write the failing tests for `docker_storage_paths`**

Add to `tests/test_docker_cli.py`:

```python
def test_docker_storage_paths_linux(monkeypatch):
    monkeypatch.setattr("dirtywork.sandbox.docker_cli.sys.platform", "linux")
    monkeypatch.setattr("dirtywork.sandbox.docker_cli.os.name", "posix")

    def fake_run(argv, *, timeout, stdin=None):
        assert argv == ["info", "--format", "{{.DockerRootDir}}"]
        return Captured(returncode=0, output=b"/var/lib/docker\n", truncated=False, timed_out=False)

    paths = docker_storage_paths(run=fake_run)
    assert Path("/var/lib/docker") in paths
    assert Path("/") in paths


def test_docker_storage_paths_darwin_uses_home(monkeypatch):
    monkeypatch.setattr("dirtywork.sandbox.docker_cli.sys.platform", "darwin")
    monkeypatch.setattr("dirtywork.sandbox.docker_cli.os.name", "posix")

    def fake_run(argv, *, timeout, stdin=None):
        raise AssertionError("docker info should not be called on darwin")

    paths = docker_storage_paths(run=fake_run)
    assert Path.home() in paths
    assert Path("/") in paths


def test_docker_storage_paths_dedupes(monkeypatch):
    monkeypatch.setattr("dirtywork.sandbox.docker_cli.sys.platform", "linux")
    monkeypatch.setattr("dirtywork.sandbox.docker_cli.os.name", "posix")

    def fake_run(argv, *, timeout, stdin=None):
        return Captured(returncode=0, output=b"/\n", truncated=False, timed_out=False)

    paths = docker_storage_paths(run=fake_run)
    assert paths.count(Path("/")) == 1
```

- [ ] **Step 10: Run to verify failure, then implement**

Run: `python -m pytest tests/test_docker_cli.py -v`
Expected: FAIL — `docker_storage_paths` does not exist.

Append to `dirtywork/sandbox/docker_cli.py`:

```python
def docker_storage_paths(*, run=run) -> list:
    """Filesystems the watchdog polls for free space (spec §6): Docker's own
    data root on Linux, the user's home volume on macOS/Windows (Docker
    Desktop's VM disk lives there), and always "/" on POSIX (the union mount
    for tmpfs-backed containers can also live on the root filesystem)."""
    paths = []
    if sys.platform.startswith("linux"):
        captured = run(["info", "--format", "{{.DockerRootDir}}"], timeout=T_QUERY)
        if captured.returncode == 0:
            root = captured.output.decode("utf-8", "replace").strip()
            if root:
                paths.append(Path(root))
    else:
        paths.append(Path.home())
    if os.name == "posix":
        paths.append(Path("/"))
    deduped = []
    seen = set()
    for p in paths:
        key = str(p)
        if key not in seen:
            seen.add(key)
            deduped.append(p)
    return deduped
```

- [ ] **Step 11: Run to verify pass**

Run: `python -m pytest tests/test_docker_cli.py -v`
Expected: PASS (13 tests)

- [ ] **Step 12: Write the failing tests for `validate_objects_dir`**

Add to `tests/test_docker_cli.py`:

```python
def _git(repo: Path, *args: str) -> None:
    import subprocess
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "f.txt").write_text("hi")
    _git(r, "add", ".")
    _git(r, "commit", "-m", "init")
    return r


def test_validate_objects_dir_accepts_normal_repo(repo: Path):
    objects = validate_objects_dir(repo)
    assert objects == (repo / ".git" / "objects").resolve()


def test_validate_objects_dir_refuses_symlinked_objects(repo: Path, tmp_path: Path):
    import shutil
    real_objects = repo / ".git" / "objects"
    outside = tmp_path / "outside-objects"
    shutil.move(str(real_objects), str(outside))
    real_objects.symlink_to(outside)
    with pytest.raises(WorkspaceError, match="symlink"):
        validate_objects_dir(repo)


def test_validate_objects_dir_refuses_objects_outside_common_dir(repo: Path, tmp_path: Path, monkeypatch):
    import subprocess
    outside = tmp_path / "outside-objects"
    outside.mkdir()

    real_run = subprocess.run

    def fake_run(argv, **kwargs):
        if "--git-path" in argv and argv[-1] == "objects":
            class R:
                returncode = 0
                stdout = str(outside) + "\n"
            return R()
        return real_run(argv, **kwargs)

    monkeypatch.setattr("dirtywork.sandbox.docker_cli.subprocess.run", fake_run)
    with pytest.raises(WorkspaceError, match="escapes"):
        validate_objects_dir(repo)
```

- [ ] **Step 13: Run to verify failure, then implement**

Run: `python -m pytest tests/test_docker_cli.py -v`
Expected: FAIL — `validate_objects_dir` does not exist.

Append to `dirtywork/sandbox/docker_cli.py`:

```python
def validate_objects_dir(repo: Path) -> Path:
    """Spec §2 step 1: the object store is the ONLY host path ever mounted
    into a container, so it gets its own validation, independent of docker.
    A symlink at the final path component is refused outright (no host
    directory should ever be silently substituted); the resolved path must
    lie inside the resolved git common dir (no ../ escape via a crafted
    .git file or a linked-worktree gitdir)."""
    common = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
                             capture_output=True, text=True)
    if common.returncode != 0:
        raise WorkspaceError(f"cannot locate git common dir for {repo}")
    common_dir = Path(common.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = repo / common_dir
    common_resolved = common_dir.resolve()

    objects = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-path", "objects"],
                              capture_output=True, text=True)
    if objects.returncode != 0:
        raise WorkspaceError(f"cannot locate objects dir for {repo}")
    objects_dir = Path(objects.stdout.strip())
    if not objects_dir.is_absolute():
        objects_dir = repo / objects_dir

    try:
        st = os.lstat(objects_dir)
    except OSError as e:
        raise WorkspaceError(f"cannot stat objects dir {objects_dir}: {e}")
    if not stat.S_ISDIR(st.st_mode):
        raise WorkspaceError(
            f"objects dir {objects_dir} is a symlink or non-directory — refusing to "
            f"mount it into a container"
        )

    objects_resolved = objects_dir.resolve()
    if objects_resolved != common_resolved and common_resolved not in objects_resolved.parents:
        raise WorkspaceError(
            f"objects dir {objects_resolved} escapes git common dir {common_resolved}"
        )
    return objects_resolved
```

- [ ] **Step 14: Run the full test file**

Run: `python -m pytest tests/test_docker_cli.py -v`
Expected: PASS (16 tests)

- [ ] **Step 15: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 16: Commit**

```bash
git add dirtywork/sandbox/docker_cli.py tests/test_docker_cli.py
git commit -m "feat: add docker_cli control-plane wrapper (run, resolve_image, storage paths, objects validation)"
```

---

### Task 4: `docker_args` — `DockerConfig` and pure argv builders

Every `docker create`/`run`/`exec` argv dirtywork ever issues is built by a pure function here, tested by exact-list assertion (spec §9: "tests assert the exact ... argv (mounts, limits, env, labels, names, no `-w`, explicit `--entrypoint`/`PATH`, export container always `--network none` even with `--allow-network`"). No process is spawned in this task at all — every function takes data in and returns a `list`.

**Files:**
- Create: `dirtywork/sandbox/docker_args.py`
- Test: `tests/test_docker_args.py`

**Interfaces:**
- Consumes: nothing (pure functions over stdlib `hashlib`/`dataclasses`).
- Produces: `@dataclass class DockerConfig` (fields below); `DEFAULT_IMAGE = "dirtywork/worker:0.3"`; `PINNED_DIGEST: str | None = None`; `PATH_ENV = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"`; `container_name(slug: str) -> str`; `volume_name(slug: str) -> str`; `repo_label(repo: Path) -> str`; `prep_run_argv(cfg, slug, image_ref, uid, gid) -> list`; `worker_create_argv(cfg, slug, image_ref, uid, gid, objects_dir, *, repo_label) -> list`; `export_create_argv(cfg, slug, image_ref, uid, gid, objects_dir, *, repo_label) -> list`; `exec_argv(name, argv, *, workdir="/work", stdin=False, env=None) -> list`.

Note on `worker_create_argv`/`export_create_argv`'s `repo_label` keyword: the spec requires every worker/export container to carry `--label dirtywork.repo=<sha256(resolved repo path)>` (decision record; spec §3's create block; §7 "like §3 but..."). The positional parameters listed in the shared cross-plan contract (`cfg, slug, image_ref, uid, gid, objects_dir`) do not include the repo path, and computing a label inside a "pure argv builder" from a `Path` would make the function do I/O (`.resolve()`) rather than stay pure. Both builders therefore take the already-computed label string as an additive, keyword-only `repo_label` argument — call `docker_args.repo_label(repo)` (the helper function, not the parameter) once in `DockerSandbox.start` and pass its result in. The positional signature the brief specifies is unchanged.

- [ ] **Step 1: Write the failing tests for `DockerConfig`, name/label helpers, and `exec_argv`**

```python
# tests/test_docker_args.py
from __future__ import annotations

from pathlib import Path

from dirtywork.sandbox.docker_args import (
    DEFAULT_IMAGE,
    PATH_ENV,
    PINNED_DIGEST,
    DockerConfig,
    container_name,
    exec_argv,
    export_create_argv,
    prep_run_argv,
    repo_label,
    volume_name,
    worker_create_argv,
)


def test_default_image_and_pinned_digest():
    assert DEFAULT_IMAGE == "dirtywork/worker:0.3"
    assert PINNED_DIGEST is None


def test_path_env_is_standard_unix_path():
    assert PATH_ENV == "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def test_docker_config_defaults():
    cfg = DockerConfig()
    assert cfg.image == DEFAULT_IMAGE
    assert cfg.network == "none"
    assert cfg.memory == "4g"
    assert cfg.cpus == "2"
    assert cfg.pids_limit == 512
    assert cfg.tmp_size == "1g"
    assert cfg.gitdir_size == "512m"
    assert cfg.home_size == "256m"
    assert cfg.max_worktree_mb == 2048
    assert cfg.max_worktree_files == 200_000
    assert cfg.min_free_mb == 2048
    assert cfg.max_patch_mb == 10
    assert cfg.keep_volume is False


def test_container_and_volume_names():
    assert container_name("abc123") == "dw-abc123"
    assert volume_name("abc123") == "dw-abc123-work"


def test_repo_label_is_sha256_of_resolved_path(tmp_path: Path):
    import hashlib
    expected = hashlib.sha256(str(tmp_path.resolve()).encode("utf-8")).hexdigest()
    assert repo_label(tmp_path) == expected


def test_exec_argv_default_workdir_no_stdin():
    argv = exec_argv("dw-slug", ["/usr/bin/git", "status"])
    assert argv == ["exec", "-w", "/work", "dw-slug", "/usr/bin/git", "status"]


def test_exec_argv_with_stdin_flag():
    argv = exec_argv("dw-slug", ["/bin/sh", "-c", "cat"], stdin=True)
    assert argv == ["exec", "-w", "/work", "-i", "dw-slug", "/bin/sh", "-c", "cat"]


def test_exec_argv_with_env_and_custom_workdir():
    argv = exec_argv("dw-slug", ["/bin/true"], workdir="/gitdir",
                      env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
    assert argv == ["exec", "-w", "/gitdir",
                     "-e", "GIT_CONFIG_GLOBAL=/dev/null",
                     "-e", "GIT_CONFIG_NOSYSTEM=1",
                     "dw-slug", "/bin/true"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_docker_args.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dirtywork.sandbox.docker_args'`

- [ ] **Step 3: Write `dirtywork/sandbox/docker_args.py`, part 1 (config, names, `exec_argv`)**

```python
# dirtywork/sandbox/docker_args.py
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_IMAGE = "dirtywork/worker:0.3"
# Set at release, after `docker build`/publish (docker/README.md documents the
# procedure). When set, resolve_image()'s pinned_digest check refuses to run
# any image whose resolved digest does not match.
PINNED_DIGEST: str | None = None
# Always passed explicitly on every docker create/run/exec so an image's own
# ENTRYPOINT/CMD/ENV can never substitute a different PATH for the tether,
# chown, or an export step (spec §3 "Entrypoint and PATH are always explicit").
PATH_ENV = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


@dataclass
class DockerConfig:
    image: str = DEFAULT_IMAGE
    network: str = "none"
    memory: str = "4g"
    cpus: str = "2"
    pids_limit: int = 512
    tmp_size: str = "1g"
    gitdir_size: str = "512m"
    home_size: str = "256m"
    max_worktree_mb: int = 2048
    max_worktree_files: int = 200_000
    min_free_mb: int = 2048
    max_patch_mb: int = 10
    keep_volume: bool = False


def container_name(slug: str) -> str:
    return f"dw-{slug}"


def volume_name(slug: str) -> str:
    return f"dw-{slug}-work"


def repo_label(repo: Path) -> str:
    """sha256 hex of the resolved repo path — the dirtywork.repo label value,
    used by `runs clean`'s collision rule to confirm a container/volume
    belongs to this repo before removing it."""
    return hashlib.sha256(str(repo.resolve()).encode("utf-8")).hexdigest()


def exec_argv(name: str, argv: list, *, workdir: str = "/work", stdin: bool = False,
              env: dict | None = None) -> list:
    out = ["exec", "-w", workdir]
    if stdin:
        out.append("-i")
    if env:
        for k, v in env.items():
            out += ["-e", f"{k}={v}"]
    out.append(name)
    out += list(argv)
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_docker_args.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Write the failing tests for `prep_run_argv`**

Add to `tests/test_docker_args.py`:

```python
def test_prep_run_argv_exact():
    cfg = DockerConfig()
    argv = prep_run_argv(cfg, "abc123", "dirtywork/worker@sha256:" + "a" * 64, 501, 20)
    assert argv == [
        "run", "--rm", "--network", "none", "--user", "0:0",
        "--cap-drop", "ALL", "--cap-add", "CHOWN",
        "--mount", "type=volume,src=dw-abc123-work,dst=/work",
        "-e", f"PATH={PATH_ENV}",
        "--entrypoint", "/bin/chown",
        "dirtywork/worker@sha256:" + "a" * 64,
        "501:20", "/work",
    ]
    assert "-w" not in argv
```

- [ ] **Step 6: Run to verify failure, then implement**

Run: `python -m pytest tests/test_docker_args.py -v`
Expected: FAIL — `prep_run_argv` does not exist.

Append to `dirtywork/sandbox/docker_args.py`:

```python
def prep_run_argv(cfg: DockerConfig, slug: str, image_ref: str, uid: int, gid: int) -> list:
    """A throwaway container that chowns a freshly-created volume's root to
    the run user (a fresh Docker volume's root is root-owned — spec §2 step
    5). --user 0:0 so chown itself has permission; --cap-drop ALL plus
    --cap-add CHOWN is the minimum capability for that one syscall."""
    return [
        "run", "--rm", "--network", "none", "--user", "0:0",
        "--cap-drop", "ALL", "--cap-add", "CHOWN",
        "--mount", f"type=volume,src={volume_name(slug)},dst=/work",
        "-e", f"PATH={PATH_ENV}",
        "--entrypoint", "/bin/chown",
        image_ref,
        f"{uid}:{gid}", "/work",
    ]
```

- [ ] **Step 7: Run to verify pass**

Run: `python -m pytest tests/test_docker_args.py -v`
Expected: PASS (9 tests)

- [ ] **Step 8: Write the failing tests for `worker_create_argv`**

Add to `tests/test_docker_args.py`:

```python
def test_worker_create_argv_exact():
    cfg = DockerConfig()
    image_ref = "dirtywork/worker@sha256:" + "a" * 64
    argv = worker_create_argv(cfg, "abc123", image_ref, 501, 20,
                               Path("/Users/x/repo/.git/objects"),
                               repo_label="deadbeef" * 8)
    assert argv == [
        "create", "-i", "--init", "--name", "dw-abc123",
        "--label", "dirtywork.run=abc123",
        "--label", f"dirtywork.repo={'deadbeef' * 8}",
        "--network", "none",
        "--memory", "4g", "--memory-swap", "4g", "--cpus", "2",
        "--pids-limit", "512",
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", "501:20",
        "--tmpfs", "/tmp:rw,exec,size=1g,mode=1777",
        "--tmpfs", "/gitdir:rw,size=512m,mode=0700,uid=501,gid=20",
        "--tmpfs", "/home/worker:rw,size=256m,mode=0700,uid=501,gid=20",
        "--mount", "type=volume,src=dw-abc123-work,dst=/work",
        "--mount", "type=bind,src=/Users/x/repo/.git/objects,dst=/repo.git/objects,readonly",
        "-e", "GIT_DIR=/gitdir",
        "-e", "GIT_WORK_TREE=/work",
        "-e", "HOME=/home/worker",
        "-e", "TMPDIR=/tmp",
        "-e", "LANG=C.UTF-8",
        "-e", "GIT_AUTHOR_NAME=dirtywork",
        "-e", "GIT_AUTHOR_EMAIL=dirtywork@localhost",
        "-e", "GIT_COMMITTER_NAME=dirtywork",
        "-e", "GIT_COMMITTER_EMAIL=dirtywork@localhost",
        "-e", f"PATH={PATH_ENV}",
        "--entrypoint", "/bin/cat",
        image_ref,
    ]
    assert "-w" not in argv


def test_worker_create_argv_honors_custom_network_and_sizes():
    cfg = DockerConfig(network="bridge", memory="8g", cpus="4", pids_limit=256,
                        tmp_size="2g", gitdir_size="1g", home_size="512m")
    argv = worker_create_argv(cfg, "s", "img@sha256:" + "0" * 64, 1000, 1000,
                               Path("/repo/.git/objects"), repo_label="x")
    assert "--network" in argv and argv[argv.index("--network") + 1] == "bridge"
    assert "--memory" in argv and argv[argv.index("--memory") + 1] == "8g"
    assert "--memory-swap" in argv and argv[argv.index("--memory-swap") + 1] == "8g"
    assert "--cpus" in argv and argv[argv.index("--cpus") + 1] == "4"
    assert "--pids-limit" in argv and argv[argv.index("--pids-limit") + 1] == "256"
    assert "/tmp:rw,exec,size=2g,mode=1777" in argv
    assert "/gitdir:rw,size=1g,mode=0700,uid=1000,gid=1000" in argv
    assert "/home/worker:rw,size=512m,mode=0700,uid=1000,gid=1000" in argv
```

- [ ] **Step 9: Run to verify failure, then implement**

Run: `python -m pytest tests/test_docker_args.py -v`
Expected: FAIL — `worker_create_argv` does not exist.

Append to `dirtywork/sandbox/docker_args.py`:

```python
def worker_create_argv(cfg: DockerConfig, slug: str, image_ref: str, uid: int, gid: int,
                        objects_dir: Path, *, repo_label: str) -> list:
    """Spec §3's exact create argv. Never passes -w/WORKDIR at container
    level (verified on Docker Desktop: it resets the volume root's ownership
    to root:root, persistently) — every tool exec passes -w /work itself.
    --entrypoint /bin/cat plus -i plus --init makes tini PID 1 with cat
    reading stdin as its only child (the lifetime tether, Task 6)."""
    name = container_name(slug)
    return [
        "create", "-i", "--init", "--name", name,
        "--label", f"dirtywork.run={slug}",
        "--label", f"dirtywork.repo={repo_label}",
        "--network", cfg.network,
        "--memory", cfg.memory,
        "--memory-swap", cfg.memory,
        "--cpus", cfg.cpus,
        "--pids-limit", str(cfg.pids_limit),
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", f"{uid}:{gid}",
        "--tmpfs", f"/tmp:rw,exec,size={cfg.tmp_size},mode=1777",
        "--tmpfs", f"/gitdir:rw,size={cfg.gitdir_size},mode=0700,uid={uid},gid={gid}",
        "--tmpfs", f"/home/worker:rw,size={cfg.home_size},mode=0700,uid={uid},gid={gid}",
        "--mount", f"type=volume,src={volume_name(slug)},dst=/work",
        "--mount", f"type=bind,src={objects_dir},dst=/repo.git/objects,readonly",
        "-e", "GIT_DIR=/gitdir",
        "-e", "GIT_WORK_TREE=/work",
        "-e", "HOME=/home/worker",
        "-e", "TMPDIR=/tmp",
        "-e", "LANG=C.UTF-8",
        "-e", "GIT_AUTHOR_NAME=dirtywork",
        "-e", "GIT_AUTHOR_EMAIL=dirtywork@localhost",
        "-e", "GIT_COMMITTER_NAME=dirtywork",
        "-e", "GIT_COMMITTER_EMAIL=dirtywork@localhost",
        "-e", f"PATH={PATH_ENV}",
        "--entrypoint", "/bin/cat",
        image_ref,
    ]
```

- [ ] **Step 10: Run to verify pass**

Run: `python -m pytest tests/test_docker_args.py -v`
Expected: PASS (11 tests)

- [ ] **Step 11: Write the failing tests for `export_create_argv`**

Add to `tests/test_docker_args.py`:

```python
def test_export_create_argv_always_network_none_even_with_bridge_cfg():
    cfg = DockerConfig(network="bridge")  # --allow-network was passed
    image_ref = "dirtywork/worker@sha256:" + "a" * 64
    argv = export_create_argv(cfg, "abc123", image_ref, 501, 20,
                               Path("/repo/.git/objects"), repo_label="deadbeef")
    assert "--network" in argv
    assert argv[argv.index("--network") + 1] == "none"


def test_export_create_argv_exact():
    cfg = DockerConfig()
    image_ref = "dirtywork/worker@sha256:" + "a" * 64
    argv = export_create_argv(cfg, "abc123", image_ref, 501, 20,
                               Path("/repo/.git/objects"), repo_label="deadbeef")
    assert argv == [
        "create", "-i", "--init", "--name", "dw-abc123-export",
        "--label", "dirtywork.run=abc123",
        "--label", "dirtywork.repo=deadbeef",
        "--network", "none",
        "--memory", "4g", "--memory-swap", "4g", "--cpus", "2",
        "--pids-limit", "256",
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", "501:20",
        "--tmpfs", "/tmp:rw,exec,size=256m,mode=1777",
        "--tmpfs", "/gitdir:rw,size=2g,mode=0700,uid=501,gid=20",
        "--tmpfs", "/home/worker:rw,size=64m,mode=0700,uid=501,gid=20",
        "--mount", "type=volume,src=dw-abc123-work,dst=/work,readonly",
        "--mount", "type=bind,src=/repo/.git/objects,dst=/repo.git/objects,readonly",
        "-e", "GIT_DIR=/gitdir",
        "-e", "GIT_WORK_TREE=/work",
        "-e", "HOME=/home/worker",
        "-e", "TMPDIR=/tmp",
        "-e", "LANG=C.UTF-8",
        "-e", "GIT_AUTHOR_NAME=dirtywork",
        "-e", "GIT_AUTHOR_EMAIL=dirtywork@localhost",
        "-e", "GIT_COMMITTER_NAME=dirtywork",
        "-e", "GIT_COMMITTER_EMAIL=dirtywork@localhost",
        "-e", f"PATH={PATH_ENV}",
        "--entrypoint", "/bin/cat",
        image_ref,
    ]
    assert "-w" not in argv
```

- [ ] **Step 12: Run to verify failure, then implement**

Run: `python -m pytest tests/test_docker_args.py -v`
Expected: FAIL — `export_create_argv` does not exist.

Append to `dirtywork/sandbox/docker_args.py`:

```python
def export_create_argv(cfg: DockerConfig, slug: str, image_ref: str, uid: int, gid: int,
                        objects_dir: Path, *, repo_label: str) -> list:
    """Spec §7: a FRESH container for export, always --network none
    regardless of cfg.network (export needs no network and gets none — this
    is asserted by test_export_create_argv_always_network_none_even_with_bridge_cfg
    even when the operator passed --allow-network for the worker container).
    Volume mounted readonly: no worker process is alive here, nothing
    should be able to write /work during export. --pids-limit 256 and
    smaller /tmp and /home/worker tmpfs than the worker container — only git
    runs here."""
    name = f"{container_name(slug)}-export"
    return [
        "create", "-i", "--init", "--name", name,
        "--label", f"dirtywork.run={slug}",
        "--label", f"dirtywork.repo={repo_label}",
        "--network", "none",
        "--memory", cfg.memory,
        "--memory-swap", cfg.memory,
        "--cpus", cfg.cpus,
        "--pids-limit", "256",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", f"{uid}:{gid}",
        "--tmpfs", "/tmp:rw,exec,size=256m,mode=1777",
        "--tmpfs", f"/gitdir:rw,size=2g,mode=0700,uid={uid},gid={gid}",
        "--tmpfs", f"/home/worker:rw,size=64m,mode=0700,uid={uid},gid={gid}",
        "--mount", f"type=volume,src={volume_name(slug)},dst=/work,readonly",
        "--mount", f"type=bind,src={objects_dir},dst=/repo.git/objects,readonly",
        "-e", "GIT_DIR=/gitdir",
        "-e", "GIT_WORK_TREE=/work",
        "-e", "HOME=/home/worker",
        "-e", "TMPDIR=/tmp",
        "-e", "LANG=C.UTF-8",
        "-e", "GIT_AUTHOR_NAME=dirtywork",
        "-e", "GIT_AUTHOR_EMAIL=dirtywork@localhost",
        "-e", "GIT_COMMITTER_NAME=dirtywork",
        "-e", "GIT_COMMITTER_EMAIL=dirtywork@localhost",
        "-e", f"PATH={PATH_ENV}",
        "--entrypoint", "/bin/cat",
        image_ref,
    ]
```

- [ ] **Step 13: Run the full test file**

Run: `python -m pytest tests/test_docker_args.py -v`
Expected: PASS (13 tests)

- [ ] **Step 14: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 15: Commit**

```bash
git add dirtywork/sandbox/docker_args.py tests/test_docker_args.py
git commit -m "feat: add DockerConfig and pure docker argv builders"
```

---

### Task 5: `run.json` persistence, `--no-checkout` worktrees, and `host_read_tree`

Three small, independent additions the Docker flow needs before `DockerSandbox` can be built: atomic `run.json` read/write (spec §2 step 4, "write `run.json` ... **now**"), a `create_worktree(..., no_checkout=True)` mode that leaves only the `.git` file (spec §2 step 3, verified: "leaves only the `.git` file; no index; no checkout hooks run"), and `host_read_tree` — the one host git command allowed to run after the worker has produced anything (spec §2 step 11).

**Files:**
- Modify: `dirtywork/rundir.py` (append `write_run_json`, `read_run_json`; SP1 already provides `ensure_runs_dir`, `create_run_dir`, `RunDirError`, and the `Path`/`os` imports)
- Modify: `dirtywork/workspace.py:37-48` (add `no_checkout` to `create_worktree`, matching the SP1-hardened shape described in spec row 3) and append `host_read_tree`
- Test: additions to `tests/test_rundir.py`, additions to `tests/test_workspace.py`

**Interfaces:**
- Consumes: `dirtywork.rundir.{ensure_runs_dir, create_run_dir, RunDirError}` (SP1, unchanged); `dirtywork.workspace.WorkspaceError` (unchanged).
- Produces: `write_run_json(run_dir: Path, data: dict) -> None`; `read_run_json(run_dir: Path) -> dict`; `create_worktree(repo: Path, slug: str, branch_from: str | None, *, no_checkout: bool = False) -> Path`; `host_read_tree(worktree: Path) -> None`.

- [ ] **Step 1: Write the failing tests for `write_run_json`/`read_run_json`**

Add to `tests/test_rundir.py` (SP1 already has this file with tests for `ensure_runs_dir`/`create_run_dir` — add these alongside them and add the import):

```python
# tests/test_rundir.py — add to the existing import line
from dirtywork.rundir import RunDirError, create_run_dir, ensure_runs_dir, read_run_json, write_run_json
```

```python
import os
import stat

import pytest


def test_write_run_json_creates_file_mode_0600(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    write_run_json(run_dir, {"status": "running", "slug": "x"})
    p = run_dir / "run.json"
    assert p.exists()
    mode = stat.S_IMODE(os.stat(p).st_mode)
    assert mode == 0o600


def test_write_run_json_then_read_round_trips(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    data = {"status": "running", "slug": "x", "nested": {"a": 1}}
    write_run_json(run_dir, data)
    assert read_run_json(run_dir) == data


def test_write_run_json_overwrite_is_atomic_and_leaves_no_temp_file(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    write_run_json(run_dir, {"status": "running"})
    write_run_json(run_dir, {"status": "completed", "ended": "later"})
    assert read_run_json(run_dir) == {"status": "completed", "ended": "later"}
    assert not any(p.name.endswith(".tmp") for p in run_dir.iterdir())


def test_read_run_json_missing_raises_oserror(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    with pytest.raises(OSError):
        read_run_json(run_dir)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_rundir.py -v`
Expected: FAIL — `ImportError: cannot import name 'write_run_json'`

- [ ] **Step 3: Implement**

Append to `dirtywork/rundir.py` (add `import json` alongside its existing imports; `Path` and `os` are already imported there by SP1's `ensure_runs_dir`/`create_run_dir`):

```python
def write_run_json(run_dir: Path, data: dict) -> None:
    """Atomic, 0600 write: a temp file in the same directory (same
    filesystem as the final path, so os.replace is atomic) then os.replace
    over run.json. A concurrent reader (e.g. `dirtywork runs list`) never
    sees a partially-written file."""
    tmp_path = run_dir / ".run.json.tmp"
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
    except Exception:
        try:
            os.unlink(str(tmp_path))
        except OSError:
            pass
        raise
    os.replace(str(tmp_path), str(run_dir / "run.json"))


def read_run_json(run_dir: Path) -> dict:
    with open(run_dir / "run.json", "r", encoding="utf-8") as fh:
        return json.load(fh)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_rundir.py -v`
Expected: PASS (all SP1 tests plus the 4 new ones)

- [ ] **Step 5: Write the failing tests for `create_worktree(no_checkout=True)` and `host_read_tree`**

Add to `tests/test_workspace.py` (extend the existing import line to include `host_read_tree`):

```python
# tests/test_workspace.py — replace the import line
from dirtywork.workspace import (
    WorkspaceError,
    create_worktree,
    ensure_worktrees_excluded,
    host_read_tree,
    load_repo_context,
    make_slug,
    preflight_repo,
)
```

```python
def test_create_worktree_no_checkout_leaves_only_dot_git(repo: Path):
    wt = create_worktree(repo, "nc-08141109", None, no_checkout=True)
    entries = list(wt.iterdir())
    assert len(entries) == 1
    assert entries[0].name == ".git"
    assert entries[0].is_file()  # linked worktree: .git is a file pointing at the gitdir


def test_create_worktree_no_checkout_head_matches_repo_head(repo: Path):
    wt = create_worktree(repo, "nc2-08141109", None, no_checkout=True)
    wt_head = _git(wt, "rev-parse", "HEAD").strip()
    repo_head = _git(repo, "rev-parse", "HEAD").strip()
    assert wt_head == repo_head


def test_host_read_tree_populates_index_not_working_tree(repo: Path):
    wt = create_worktree(repo, "hrt-08141109", None, no_checkout=True)
    host_read_tree(wt)
    ls_files = _git(wt, "ls-files")
    assert "f.txt" in ls_files
    assert not (wt / "f.txt").exists()  # index only — no working-tree write


def test_host_read_tree_failure_raises_workspace_error(tmp_path: Path):
    not_a_worktree = tmp_path / "not-a-worktree"
    not_a_worktree.mkdir()
    with pytest.raises(WorkspaceError):
        host_read_tree(not_a_worktree)
```

- [ ] **Step 6: Run to verify it fails**

Run: `python -m pytest tests/test_workspace.py -v`
Expected: FAIL — `TypeError: create_worktree() got an unexpected keyword argument 'no_checkout'` and `ImportError: cannot import name 'host_read_tree'`

- [ ] **Step 7: Implement — extend `create_worktree`, add `host_read_tree`**

In `dirtywork/workspace.py`, add `import os` and `import stat` to the top imports (alongside the existing `re`, `secrets`, `subprocess`). Replace the `create_worktree` function (spec row 3's hardened shape, here extended with `no_checkout`):

```python
def create_worktree(repo: Path, slug: str, branch_from: str | None, *,
                     no_checkout: bool = False) -> Path:
    dot_worktrees = repo / ".worktrees"
    try:
        wt_stat = os.lstat(dot_worktrees)
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISDIR(wt_stat.st_mode):
            raise WorkspaceError(
                f"{dot_worktrees} exists and is not a directory — refusing to follow "
                f"a symlink or write through a file at this path"
            )

    rel = Path(".worktrees") / f"dw-{slug}"
    dest = repo / rel
    try:
        os.lstat(dest)
    except FileNotFoundError:
        pass
    else:
        raise WorkspaceError(
            f"{dest} already exists (file, directory, or symlink) — refusing to "
            f"create a worktree through a pre-existing path"
        )

    ref = branch_from or "HEAD"
    branch = f"dirtywork/{slug}"
    existed = _git(repo, "rev-parse", "--verify", "--quiet",
                    f"refs/heads/{branch}").returncode == 0
    args = ["worktree", "add"]
    if no_checkout:
        args.append("--no-checkout")
    args += ["-b", branch, str(rel), ref]
    res = _git(repo, *args)
    if res.returncode != 0:
        if not existed:
            _git(repo, "branch", "-D", branch)  # best-effort cleanup; ignore result
        raise WorkspaceError(f"git worktree add failed: {res.stderr.strip()}")

    worktree = repo / rel
    repo_worktrees_resolved = repo.resolve() / ".worktrees"
    if repo_worktrees_resolved not in worktree.resolve().parents:
        _git(repo, "worktree", "remove", "--force", str(rel))
        if not existed:
            _git(repo, "branch", "-D", branch)
        raise WorkspaceError(
            f"worktree {worktree} did not land inside {repo_worktrees_resolved} "
            f"after creation — aborting"
        )
    return worktree
```

Append `host_read_tree` at the end of `dirtywork/workspace.py`:

```python
def host_read_tree(worktree: Path) -> None:
    """The only host git command that runs after the worker has produced
    anything (spec §2 step 11): index-only, against the base tree, using the
    operator's own object store — writes no working-tree files (verified).
    Config-neutral env so no checked-out state can influence it, even though
    only objects/ was ever mounted into any container."""
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    res = subprocess.run(
        ["git", "-C", str(worktree),
         "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
         "read-tree", "HEAD"],
        capture_output=True, text=True, env=env,
    )
    if res.returncode != 0:
        raise WorkspaceError(f"git read-tree HEAD failed in {worktree}: {res.stderr.strip()}")
```

- [ ] **Step 8: Run to verify pass**

Run: `python -m pytest tests/test_workspace.py -v`
Expected: PASS (all pre-existing tests plus the 4 new ones)

- [ ] **Step 9: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 10: Commit**

```bash
git add dirtywork/rundir.py dirtywork/workspace.py tests/test_rundir.py tests/test_workspace.py
git commit -m "feat: add run.json persistence, no-checkout worktrees, and host_read_tree"
```

---

### Task 6: `DockerSandbox.start`/`stop` — collision refusal, volume, prep, create, tether, ready-wait, init

The container/volume lifecycle from spec §2 steps 5–7 and §3's "Name collision"/"Lifetime tether" paragraphs. This task also defines `FakeDocker`, the one reusable fake used by every remaining Docker unit test in this plan (Tasks 6–11) — it stands in for both `docker_cli.run` and `subprocess.Popen`.

**Files:**
- Create: `dirtywork/sandbox/docker.py`
- Test: `tests/test_docker_sandbox.py` (defines `FakeDocker`/`FakePopen`, used by this task and every later `DockerSandbox` task)

**Interfaces:**
- Consumes: `dirtywork.sandbox.{SandboxError, RunArtifacts}`; `dirtywork.sandbox.docker_cli` (module — `run`, `DockerError`, `T_QUERY`, `T_LIFECYCLE`, `resolve_image`, `validate_objects_dir`); `dirtywork.sandbox.docker_args` (module — `DockerConfig`, `container_name`, `volume_name`, `repo_label`, `PINNED_DIGEST`, `prep_run_argv`, `worker_create_argv`, `exec_argv`).
- Produces: `class DockerSandbox` with `__init__(self, cfg: DockerConfig, *, run_dir: Path, transcript=None, run=docker_cli.run, popen=subprocess.Popen)`; attributes set by `start()`: `.container`, `.volume`, `.image_ref`, `.uid`, `.gid`; `.start(worktree, repo, slug, base_commit) -> None`; `.stop() -> None` (idempotent).

- [ ] **Step 1: Write `FakeDocker`/`FakePopen` and the failing happy-path test**

```python
# tests/test_docker_sandbox.py
from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest

from dirtywork.procs import Captured
from dirtywork.sandbox import SandboxError
from dirtywork.sandbox.docker import DockerSandbox
from dirtywork.sandbox.docker_args import DockerConfig
from dirtywork.sandbox.docker_cli import DockerError


class FakePopen:
    """Stand-in for subprocess.Popen, used both for the `docker start -ai`
    tether (only .argv and .stdin matter there) and for streamed commands
    like `git diff`/`git archive` whose stdout the caller reads (Task 11)
    — .stdout is a real io.BytesIO pre-loaded with `stdout_data` so callers
    can .read() it exactly like a real pipe. .stdin is a real io.BytesIO so
    callers can .write()/.close() it; .wait()/.poll()/.kill() are scripted
    to look like a clean-running process unless a test overrides
    .returncode directly."""

    def __init__(self, argv, *, stdin=None, stdout=None, stderr=None, stdout_data: bytes = b""):
        self.argv = list(argv)
        self.stdin = io.BytesIO() if stdin == subprocess.PIPE else None
        self.stdout = io.BytesIO(stdout_data) if stdout == subprocess.PIPE else None
        self.returncode = None
        self.killed = False

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def terminate(self):
        self.kill()


class FakeDocker:
    """Scriptable stand-in for docker_cli.run and subprocess.Popen, shared by
    every DockerSandbox/export unit test in this plan.

    `script(prefix, response)` maps an argv-prefix tuple to either a single
    Captured (always returned for matching calls) or a list of Captured
    (popped in call order; the last item repeats once the list is down to
    one element, so a test can script "fails twice then succeeds" with
    [fail, fail, ok] and further matching calls keep returning ok). Any argv
    with no matching prefix gets `.default` (returncode 0, empty output) so
    a test only scripts the calls it cares about. When more than one
    registered prefix matches a call, the LONGEST (most specific) prefix
    wins — this lets a test register a broad default like `["exec"]` for
    "any exec call returns ok" and separately override one specific exec
    call (e.g. Task 9's worktree-size sample, which is also a `docker exec`
    under the same `["exec", ...]` prefix) without the broad default
    shadowing it. Every call is recorded in `.calls` (list of
    (argv, timeout, stdin)) for order/content assertions; every FakePopen
    created is recorded in `.popens`.

    `script_popen_stdout(prefix, data)` maps an argv-prefix tuple (matched
    the same longest-prefix-wins way, against the argv passed to `popen()`
    — which for every real call in this codebase is `["docker", ...]`,
    since `popen` is always called with the full `docker` argv already
    prefixed, unlike `run()` which prefixes it internally) to the bytes a
    FakePopen's `.stdout` should yield. Used by Task 11's export-flow tests
    to feed a real in-memory tar into `git archive`'s simulated stdout.
    """

    def __init__(self):
        self.responses = {}
        self.popen_stdout = {}
        self.calls = []
        self.popens = []
        self.default = Captured(returncode=0, output=b"", truncated=False, timed_out=False)

    def script(self, prefix, response) -> None:
        self.responses[tuple(prefix)] = response

    def script_popen_stdout(self, prefix, data: bytes) -> None:
        self.popen_stdout[tuple(prefix)] = data

    def run(self, argv, *, timeout, stdin=None):
        self.calls.append((list(argv), timeout, stdin))
        best_prefix = None
        best_response = None
        for prefix, response in self.responses.items():
            if tuple(argv[: len(prefix)]) == prefix:
                if best_prefix is None or len(prefix) > len(best_prefix):
                    best_prefix, best_response = prefix, response
        if best_prefix is None:
            return self.default
        if isinstance(best_response, list):
            if len(best_response) > 1:
                return best_response.pop(0)
            return best_response[0]
        return best_response

    def popen(self, argv, *, stdin=None, stdout=None, stderr=None):
        best_prefix = None
        best_data = b""
        for prefix, data in self.popen_stdout.items():
            if tuple(argv[: len(prefix)]) == prefix:
                if best_prefix is None or len(prefix) > len(best_prefix):
                    best_prefix, best_data = prefix, data
        p = FakePopen(argv, stdin=stdin, stdout=stdout, stderr=stderr, stdout_data=best_data)
        self.popens.append(p)
        return p


def _ok(output: bytes = b"") -> Captured:
    return Captured(returncode=0, output=output, truncated=False, timed_out=False)


def _fail(output: bytes = b"error") -> Captured:
    return Captured(returncode=1, output=output, truncated=False, timed_out=False)


@pytest.fixture()
def docker(tmp_path: Path):
    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["image", "inspect", "--format", "{{json .RepoDigests}}"],
                _ok(b'["dirtywork/worker@sha256:' + b"a" * 64 + b'"]'))
    fake.script(["volume", "create"], _ok())
    fake.script(["run"], _ok())   # prep container
    fake.script(["create"], _ok())  # worker create
    fake.script(["exec"], _ok())  # ready-wait /bin/true and init
    cfg = DockerConfig()
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    sb = DockerSandbox(cfg, run_dir=run_dir, run=fake.run, popen=fake.popen)
    return sb, fake, run_dir


def _fake_repo(tmp_path: Path) -> Path:
    import subprocess as sp
    repo = tmp_path / "repo"
    repo.mkdir()
    sp.run(["git", "-C", str(repo), "init", "-b", "main"], capture_output=True, check=True)
    sp.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    sp.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "f.txt").write_text("hi")
    sp.run(["git", "-C", str(repo), "add", "."], check=True)
    sp.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True)
    return repo


def test_start_sets_attributes(docker, tmp_path):
    sb, fake, run_dir = docker
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /somewhere\n")

    sb.start(worktree, repo, "abc123", "deadbeef" * 5)

    assert sb.container == "dw-abc123"
    assert sb.volume == "dw-abc123-work"
    assert sb.image_ref == "dirtywork/worker@sha256:" + "a" * 64
    assert isinstance(sb.uid, int)
    assert isinstance(sb.gid, int)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_docker_sandbox.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dirtywork.sandbox.docker'`

- [ ] **Step 3: Write `dirtywork/sandbox/docker.py`, part 1 (`start`)**

```python
# dirtywork/sandbox/docker.py
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from . import SandboxError
from . import docker_args
from . import docker_cli


class DockerSandbox:
    """Every tool call and the run lifecycle for docker mode. Constructed
    with injectable `run`/`popen` so unit tests never touch a real daemon —
    only tests marked `docker` (Tasks 13, 15, 16) pass real callables."""

    def __init__(self, cfg: docker_args.DockerConfig, *, run_dir: Path, transcript=None,
                 run=docker_cli.run, popen=subprocess.Popen):
        self.cfg = cfg
        self.run_dir = run_dir
        self.transcript = transcript
        self._run = run
        self._popen = popen
        self.container = None
        self.volume = None
        self.image_ref = None
        self.uid = None
        self.gid = None
        self._tether = None
        self._slug = None
        self._repo = None
        self._worktree = None
        self._base_commit = None
        self._stopped = False

    def start(self, worktree: Path, repo: Path, slug: str, base_commit: str) -> None:
        self._worktree = worktree
        self._repo = repo
        self._slug = slug
        self._base_commit = base_commit
        name = docker_args.container_name(slug)
        vol = docker_args.volume_name(slug)

        # Name collision refusal (spec §3): never remove anything this
        # invocation did not create — a collision is either a stale leftover
        # or something else's resource, and both deserve a human.
        c_inspect = self._run(["container", "inspect", name], timeout=docker_cli.T_QUERY)
        if c_inspect.returncode == 0:
            raise SandboxError(
                f"container {name} already exists; run `dirtywork runs clean {slug}`"
            )
        v_inspect = self._run(["volume", "inspect", vol], timeout=docker_cli.T_QUERY)
        if v_inspect.returncode == 0:
            raise SandboxError(
                f"volume {vol} already exists; run `dirtywork runs clean {slug}`"
            )

        self.uid = os.getuid() if os.name == "posix" else 1000
        self.gid = os.getgid() if os.name == "posix" else 1000

        objects_dir = docker_cli.validate_objects_dir(repo)
        self.image_ref = docker_cli.resolve_image(
            self.cfg.image, run=self._run, pinned_digest=docker_args.PINNED_DIGEST
        )
        label = docker_args.repo_label(repo)

        create_vol = self._run(
            ["volume", "create", "--label", f"dirtywork.run={slug}",
             "--label", f"dirtywork.repo={label}", vol],
            timeout=docker_cli.T_QUERY,
        )
        if create_vol.returncode != 0:
            raise SandboxError(
                f"docker volume create {vol} failed: "
                f"{create_vol.output.decode('utf-8', 'replace')[:500]}"
            )
        self.volume = vol

        prep_argv = docker_args.prep_run_argv(self.cfg, slug, self.image_ref, self.uid, self.gid)
        prep = self._run(prep_argv, timeout=docker_cli.T_LIFECYCLE)
        if prep.returncode != 0:
            raise SandboxError(
                f"prep container failed to chown the volume: "
                f"{prep.output.decode('utf-8', 'replace')[:500]}"
            )

        create_argv = docker_args.worker_create_argv(
            self.cfg, slug, self.image_ref, self.uid, self.gid, objects_dir, repo_label=label
        )
        created = self._run(create_argv, timeout=docker_cli.T_LIFECYCLE)
        if created.returncode != 0:
            raise SandboxError(
                f"docker create {name} failed: {created.output.decode('utf-8', 'replace')[:500]}"
            )
        self.container = name

        self._start_tether()
        self._wait_ready()
        self._init(restart=False)

    def _start_tether(self) -> None:
        self._tether = self._popen(
            ["docker", "start", "-ai", self.container],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + docker_cli.T_LIFECYCLE
        last_error = None
        while time.monotonic() < deadline:
            try:
                captured = self._run(["exec", self.container, "/bin/true"],
                                      timeout=docker_cli.T_LIFECYCLE)
            except docker_cli.DockerError as e:
                last_error = e
                time.sleep(0.05)
                continue
            if captured.returncode == 0:
                return
            last_error = SandboxError(
                f"docker exec {self.container} /bin/true returned {captured.returncode}"
            )
            time.sleep(0.05)
        raise SandboxError(
            f"container {self.container} did not become ready within "
            f"{docker_cli.T_LIFECYCLE}s" + (f": {last_error}" if last_error else "")
        )

    def _init(self, *, restart: bool) -> None:
        populate = "/usr/bin/git read-tree HEAD" if restart else "/usr/bin/git read-tree -m -u HEAD"
        script = (
            "set -e; "
            "/usr/bin/git init -q; "
            "echo /repo.git/objects > /gitdir/objects/info/alternates; "
            f"/usr/bin/git symbolic-ref HEAD refs/heads/dirtywork/{self._slug}; "
            f"/usr/bin/git update-ref refs/heads/dirtywork/{self._slug} {self._base_commit}; "
            f"{populate}"
        )
        argv = docker_args.exec_argv(
            self.container, ["/bin/sh", "-c", script],
            env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
        )
        captured = self._run(argv, timeout=docker_cli.T_LIFECYCLE)
        if captured.returncode != 0:
            raise SandboxError(
                f"in-container init failed: {captured.output.decode('utf-8', 'replace')[:500]}"
            )

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self.container is not None:
            try:
                self._run(["rm", "-f", self.container], timeout=docker_cli.T_LIFECYCLE)
            except docker_cli.DockerError:
                pass
        if self._tether is not None:
            try:
                if self._tether.stdin is not None:
                    self._tether.stdin.close()
            except OSError:
                pass
            try:
                self._tether.wait(timeout=docker_cli.T_LIFECYCLE)
            except Exception:
                pass
        if self.volume is not None and not self.cfg.keep_volume:
            try:
                self._run(["volume", "rm", self.volume], timeout=docker_cli.T_QUERY)
            except docker_cli.DockerError:
                pass
```

- [ ] **Step 4: Run to verify the happy-path test passes**

Run: `python -m pytest tests/test_docker_sandbox.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Write the failing tests for collision refusal, ordered calls, tether, and stop**

Add to `tests/test_docker_sandbox.py`:

```python
def test_start_refuses_on_container_collision(docker, tmp_path):
    sb, fake, run_dir = docker
    fake.script(["container", "inspect"], _ok())  # already exists
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    with pytest.raises(SandboxError, match="runs clean abc123"):
        sb.start(worktree, repo, "abc123", "deadbeef" * 5)
    # nothing created after the collision check
    assert not any(c[0][0] == "volume" and c[0][1] == "create" for c in fake.calls)


def test_start_refuses_on_volume_collision(docker, tmp_path):
    sb, fake, run_dir = docker
    fake.script(["volume", "inspect"], _ok())  # already exists
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    with pytest.raises(SandboxError, match="runs clean abc123"):
        sb.start(worktree, repo, "abc123", "deadbeef" * 5)


def test_start_prep_failure_raises_sandboxerror(docker, tmp_path):
    sb, fake, run_dir = docker
    fake.script(["run"], _fail(b"chown: permission denied"))
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    with pytest.raises(SandboxError, match="chown"):
        sb.start(worktree, repo, "abc123", "deadbeef" * 5)


def test_start_create_failure_raises_sandboxerror(docker, tmp_path):
    sb, fake, run_dir = docker
    fake.script(["create"], _fail(b"no such image"))
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    with pytest.raises(SandboxError, match="docker create"):
        sb.start(worktree, repo, "abc123", "deadbeef" * 5)


def test_start_call_order(docker, tmp_path):
    sb, fake, run_dir = docker
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    sb.start(worktree, repo, "abc123", "deadbeef" * 5)

    verbs = [tuple(c[0][:2]) for c in fake.calls]
    assert verbs.index(("container", "inspect")) < verbs.index(("volume", "inspect"))
    assert verbs.index(("volume", "inspect")) < verbs.index(("image", "inspect"))
    volume_create_idx = next(i for i, c in enumerate(fake.calls) if c[0][:2] == ["volume", "create"])
    prep_idx = next(i for i, c in enumerate(fake.calls) if c[0][0] == "run")
    create_idx = next(i for i, c in enumerate(fake.calls) if c[0][0] == "create")
    exec_idxs = [i for i, c in enumerate(fake.calls) if c[0][0] == "exec"]
    assert volume_create_idx < prep_idx < create_idx < min(exec_idxs)


def test_start_creates_tether_after_create(docker, tmp_path):
    sb, fake, run_dir = docker
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    sb.start(worktree, repo, "abc123", "deadbeef" * 5)

    assert len(fake.popens) == 1
    assert fake.popens[0].argv == ["docker", "start", "-ai", "dw-abc123"]


def test_wait_ready_retries_until_success(docker, tmp_path):
    sb, fake, run_dir = docker
    fake.script(["exec"], [_fail(), _fail(), _ok()])
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    sb.start(worktree, repo, "abc123", "deadbeef" * 5)  # must not raise

    assert sb.container == "dw-abc123"


def test_stop_is_idempotent_and_removes_container_and_volume(docker, tmp_path):
    sb, fake, run_dir = docker
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    sb.start(worktree, repo, "abc123", "deadbeef" * 5)
    fake.calls.clear()

    sb.stop()
    sb.stop()  # second call must no-op, not re-issue docker commands

    rm_calls = [c for c in fake.calls if c[0][:2] == ["rm", "-f"]]
    vol_rm_calls = [c for c in fake.calls if c[0][:2] == ["volume", "rm"]]
    assert len(rm_calls) == 1
    assert len(vol_rm_calls) == 1


def test_stop_keeps_volume_when_keep_volume_set(tmp_path):
    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["image", "inspect", "--format", "{{json .RepoDigests}}"],
                _ok(b'["dirtywork/worker@sha256:' + b"a" * 64 + b'"]'))
    fake.script(["volume", "create"], _ok())
    fake.script(["run"], _ok())
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())
    cfg = DockerConfig(keep_volume=True)
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    sb = DockerSandbox(cfg, run_dir=run_dir, run=fake.run, popen=fake.popen)
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    sb.start(worktree, repo, "abc123", "deadbeef" * 5)

    sb.stop()

    assert not any(c[0][:2] == ["volume", "rm"] for c in fake.calls)
```

- [ ] **Step 6: Run to verify pass**

Run: `python -m pytest tests/test_docker_sandbox.py -v`
Expected: PASS (10 tests)

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 8: Commit**

```bash
git add dirtywork/sandbox/docker.py tests/test_docker_sandbox.py
git commit -m "feat: add DockerSandbox lifecycle (collision refusal, volume, prep, create, tether, init)"
```

---

### Task 7: `DockerSandbox` tool methods — `read_file`/`write_file`/`edit_file`/`list_dir`/`grep`/`bash`

Spec §5, "Tool execution inside the container": every operation is a `docker exec -w /work [-i]` with host-side path normalization (an accident guard, not the boundary — the container itself is the boundary) and result shaping identical to host mode. This task also factors `tools._number_lines` out of `tools.read_file` so both `HostSandbox` (via `tools.read_file`) and `DockerSandbox.read_file` number/window text the same way.

**Design note on tool-exec timeouts.** `docker_cli.run` (Task 3) always converts a `run_capped` timeout into a raised `DockerError` — correct for lifecycle commands (`create`/`start`/`rm`/`kill`/`volume *`/`inspect`), where a hang means the daemon itself is stuck and the run should fail closed as `sandbox_error`. But `bash` and `grep` are the two tool methods whose *model-facing contract already promises a graceful timeout result* (spec §9's live-suite case "timeout kills `sleep 600` and the run continues"; host-mode `tools.bash`/`tools.grep` both return `"ERROR: ... timed out ..."` text rather than raising). `DockerSandbox.bash`/`.grep` therefore catch `DockerError` from `self._run` and convert it into that same text, matching host mode's contract exactly; `read_file`/`write_file`/`edit_file`/`list_dir` have no user-facing timeout knob, so a `DockerError` there is unexpected and is allowed to propagate — the runner already catches `SandboxError` (of which `DockerError` is a subclass) and ends the run as `sandbox_error`, which is the correct "fail closed" behavior for those.

**Files:**
- Modify: `dirtywork/tools.py` (factor `_number_lines` out of `read_file`)
- Modify: `dirtywork/sandbox/docker.py` (add `_rel`, `read_file`, `write_file`, `edit_file`, `list_dir`, `grep`, `bash`)
- Test: additions to `tests/test_tools_files.py` (the `_number_lines` refactor), additions to `tests/test_docker_sandbox.py`

**Interfaces:**
- Consumes: `dirtywork.tools.{MAX_READ_BYTES, MAX_WRITE_BYTES, MAX_LIST_ENTRIES, MAX_BASH_CHARS, _cap, _number_lines}`; `dirtywork.guardrails.check_bash_command`; `dirtywork.sandbox.docker_cli.DockerError`; `dirtywork.sandbox.docker_args.exec_argv`.
- Produces: `DockerSandbox.read_file/write_file/edit_file/list_dir/grep/bash` matching the `Sandbox` Protocol signatures exactly; private `_rel(path: str, *, writing: bool = False) -> tuple` (returns `(normalized_path, None)` or `(None, error_string)`).

- [ ] **Step 1: Write the failing test for the `_number_lines` refactor**

Add to `tests/test_tools_files.py`:

```python
def test_number_lines_matches_read_file_shape(wt: Path):
    from dirtywork import tools
    text = "def main():\n    return 42\n"
    direct = tools._number_lines(text, offset=0, limit=400)
    via_read = tools.read_file(wt, "src/app.py")
    assert direct.splitlines()[0] == via_read.splitlines()[0]
    assert direct.splitlines()[1] == via_read.splitlines()[1]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_tools_files.py -v`
Expected: FAIL — `AttributeError: module 'dirtywork.tools' has no attribute '_number_lines'`

- [ ] **Step 3: Factor `_number_lines` out of `tools.read_file`**

In `dirtywork/tools.py`, replace the `read_file` function (lines 42-61) with:

```python
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


def read_file(worktree: Path, path: str, offset: int = 0, limit: int = 400) -> str:
    try:
        p = resolve_in_worktree(path, worktree)
    except GuardrailError as e:
        return f"ERROR: {e}"
    err = _guard_readable(p, path)
    if err:
        return err
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"ERROR: cannot read '{path}': {e}"
    return _number_lines(text, offset, limit)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_tools_files.py -v`
Expected: PASS (all pre-existing tests plus the new one)

- [ ] **Step 5: Write the failing tests for `read_file`/`write_file`/`edit_file`**

Add to `tests/test_docker_sandbox.py` (uses the `docker`/`started` fixtures from Task 6; add the `started` fixture first, right after `_fake_repo`):

```python
@pytest.fixture()
def started(docker, tmp_path: Path):
    sb, fake, run_dir = docker
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    sb.start(worktree, repo, "abc123", "deadbeef" * 5)
    fake.calls.clear()
    return sb, fake, run_dir


def test_read_file_exec_argv_and_shaping(started):
    from dirtywork.tools import MAX_READ_BYTES
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b"line one\nline two\n"))
    out = sb.read_file("src/app.py")
    assert fake.calls[-1][0] == [
        "exec", "-w", "/work", "dw-abc123",
        "/usr/bin/head", "-c", str(MAX_READ_BYTES), "--", "src/app.py",
    ]
    assert "     1\tline one" in out
    assert "     2\tline two" in out


def test_read_file_rejects_absolute_path(started):
    sb, fake, run_dir = started
    out = sb.read_file("/etc/passwd")
    assert out.startswith("ERROR:")
    assert not fake.calls


def test_read_file_rejects_dotdot_escape(started):
    sb, fake, run_dir = started
    out = sb.read_file("../../etc/passwd")
    assert out.startswith("ERROR:")
    assert not fake.calls


def test_write_file_sends_content_on_stdin(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok())
    out = sb.write_file("deep/new/file.txt", "hello")
    assert "Wrote 5 bytes" in out
    argv, timeout, stdin = fake.calls[-1]
    assert argv == [
        "exec", "-w", "/work", "-i", "dw-abc123",
        "/bin/sh", "-c", 'mkdir -p "$(dirname -- "$1")" && cat > "$1"',
        "_", "deep/new/file.txt",
    ]
    assert stdin == b"hello"


def test_write_file_refuses_dot_git(started):
    sb, fake, run_dir = started
    out = sb.write_file(".git/hooks/pre-commit", "#!/bin/sh")
    assert out.startswith("ERROR:")
    assert not fake.calls


def test_write_file_refuses_oversized_content(started):
    from dirtywork.tools import MAX_WRITE_BYTES
    sb, fake, run_dir = started
    out = sb.write_file("big.txt", "x" * (MAX_WRITE_BYTES + 1))
    assert out.startswith("ERROR:")
    assert not fake.calls


def test_edit_file_reads_then_writes(started):
    sb, fake, run_dir = started
    fake.script(["exec"], [_ok(b"def main():\n    return 42\n"), _ok()])
    out = sb.edit_file("src/app.py", "return 42", "return 43")
    assert "Edited" in out
    heads = [c for c in fake.calls if "/usr/bin/head" in c[0]]
    writes = [c for c in fake.calls if "cat > \"$1\"" in " ".join(c[0])]
    assert len(heads) == 1
    assert len(writes) == 1


def test_edit_file_no_match(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b"nothing matches here\n"))
    out = sb.edit_file("src/app.py", "not here", "x")
    assert out.startswith("ERROR:") and "0 times" in out


def test_edit_file_multiple_matches(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b"aa\naa\n"))
    out = sb.edit_file("dup.txt", "aa", "bb")
    assert out.startswith("ERROR:") and "2 times" in out
```

- [ ] **Step 6: Run to verify failure, then implement `_rel`, `read_file`, `write_file`, `edit_file`**

Run: `python -m pytest tests/test_docker_sandbox.py -v`
Expected: FAIL — `AttributeError: 'DockerSandbox' object has no attribute 'read_file'`

Add to `dirtywork/sandbox/docker.py`. First, extend the imports at the top of the file:

```python
import posixpath

from ..guardrails import check_bash_command
from ..tools import MAX_BASH_CHARS, MAX_LIST_ENTRIES, MAX_READ_BYTES, MAX_WRITE_BYTES, _cap, _number_lines
```

Add the module-level constants and `_rel` helper (below the imports, above `class DockerSandbox`):

```python
# Fixed exec timeouts for tools with no user-facing timeout knob — these
# operations should complete near-instantly; a hang means the sandbox
# itself is broken, so DockerError is allowed to propagate as sandbox_error
# rather than being caught and turned into text (unlike bash/grep, whose
# Sandbox signatures accept a caller timeout and whose contract already
# promises a graceful "timed out" text result).
READ_EXEC_TIMEOUT = 30
WRITE_EXEC_TIMEOUT = 30
LIST_EXEC_TIMEOUT = 30


def _rel(path: str, *, writing: bool = False):
    """Host-side path normalization — an accident guard, not the security
    boundary (the container's read-only rootfs and its own filesystem are
    the boundary). Returns (normalized, None) or (None, error_string).
    Rejects absolute paths, '..' escapes, and — when writing — a first path
    component of '.git' (mirrors resolve_in_worktree's writing=True guard in
    host mode)."""
    normalized = posixpath.normpath(path)
    if posixpath.isabs(normalized):
        return None, (
            f"ERROR: path '{path}' resolves outside the worktree "
            f"(absolute paths are not allowed)"
        )
    parts = [] if normalized == "." else normalized.split("/")
    if any(part == ".." for part in parts):
        return None, (
            f"ERROR: path '{path}' resolves outside the worktree "
            f"('..' escapes are not allowed)"
        )
    if writing and parts and parts[0] == ".git":
        return None, f"ERROR: writing inside .git/ is not allowed (got '{path}')"
    return normalized, None
```

Add these methods to `class DockerSandbox` (after `stop`):

```python
    def _read_raw(self, path: str):
        rel, err = _rel(path)
        if err:
            return None, err
        argv = docker_args.exec_argv(
            self.container, ["/usr/bin/head", "-c", str(MAX_READ_BYTES), "--", rel]
        )
        captured = self._run(argv, timeout=READ_EXEC_TIMEOUT)
        if captured.returncode != 0:
            return None, (
                f"ERROR: cannot read '{path}': "
                f"{captured.output.decode('utf-8', 'replace')[:500]}"
            )
        return captured.output.decode("utf-8", errors="replace"), None

    def read_file(self, path: str, offset: int = 0, limit: int = 400) -> str:
        text, err = self._read_raw(path)
        if err:
            return err
        return _number_lines(text, offset, limit)

    def write_file(self, path: str, content: str) -> str:
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_WRITE_BYTES:
            return (
                f"ERROR: content is {len(encoded)} bytes, over the "
                f"{MAX_WRITE_BYTES}-byte write limit"
            )
        rel, err = _rel(path, writing=True)
        if err:
            return err
        argv = docker_args.exec_argv(
            self.container,
            ["/bin/sh", "-c", 'mkdir -p "$(dirname -- "$1")" && cat > "$1"', "_", rel],
            stdin=True,
        )
        captured = self._run(argv, timeout=WRITE_EXEC_TIMEOUT, stdin=encoded)
        if captured.returncode != 0:
            return (
                f"ERROR: cannot write '{path}': "
                f"{captured.output.decode('utf-8', 'replace')[:500]}"
            )
        return f"Wrote {len(encoded)} bytes to {path}"

    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        text, err = self._read_raw(path)
        if err:
            return err
        count = text.count(old_string)
        if count != 1:
            return (
                f"ERROR: old_string occurs {count} times in {path}; it must occur "
                f"exactly once. Include more surrounding context to make it unique."
            )
        return self.write_file(path, text.replace(old_string, new_string, 1))
```

- [ ] **Step 7: Run to verify pass**

Run: `python -m pytest tests/test_docker_sandbox.py tests/test_tools_files.py -v`
Expected: PASS

- [ ] **Step 8: Write the failing tests for `list_dir` and `grep`**

Add to `tests/test_docker_sandbox.py`:

```python
def test_list_dir_shapes_output(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b"d\t96\tsrc\nf\t18\tREADME.md\n"))
    out = sb.list_dir(".")
    assert "src/" in out
    assert "README.md  (18 bytes)" in out
    assert fake.calls[-1][0] == [
        "exec", "-w", "/work", "dw-abc123",
        "/usr/bin/find", ".", "-mindepth", "1", "-maxdepth", "1",
        "-printf", "%y\t%s\t%f\n",
    ]


def test_list_dir_caps_entries(started):
    from dirtywork.tools import MAX_LIST_ENTRIES
    sb, fake, run_dir = started
    lines = "".join(f"f\t1\tfile{i}\n" for i in range(MAX_LIST_ENTRIES + 50))
    fake.script(["exec"], _ok(lines.encode()))
    out = sb.list_dir(".")
    assert "capped" in out
    assert out.count("(1 bytes)") == MAX_LIST_ENTRIES


def test_grep_exec_argv_and_strips_leading_dot_slash(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b"./src/app.py:2:    return 42\n"))
    out = sb.grep("return 42")
    assert "src/app.py:2" in out
    assert "./" not in out
    assert fake.calls[-1][0] == [
        "exec", "-w", "/work", "dw-abc123",
        "/usr/bin/rg", "-n", "--no-heading", "-M", "300", "-e", "return 42", ".",
    ]


def test_grep_glob_appends_dash_g_flag(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b""))
    sb.grep("foo", glob="*.py")
    argv = fake.calls[-1][0]
    assert "-g" in argv and argv[argv.index("-g") + 1] == "*.py"


def test_grep_no_match(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b""))
    out = sb.grep("zzz_not_present")
    assert "No matches" in out


def test_grep_timeout_returns_error_text(started):
    sb, fake, run_dir = started

    def raise_timeout(argv, *, timeout, stdin=None):
        fake.calls.append((list(argv), timeout, stdin))
        raise DockerError("docker exec ... timed out after 40s")

    sb._run = raise_timeout
    out = sb.grep("foo", timeout=30)
    assert "timed out" in out.lower()
```

- [ ] **Step 9: Run to verify failure, then implement `list_dir` and `grep`**

Run: `python -m pytest tests/test_docker_sandbox.py -v`
Expected: FAIL — `AttributeError: 'DockerSandbox' object has no attribute 'list_dir'`

Add to `class DockerSandbox`:

```python
    def list_dir(self, path: str = ".") -> str:
        rel, err = _rel(path)
        if err:
            return err
        argv = docker_args.exec_argv(
            self.container,
            ["/usr/bin/find", rel, "-mindepth", "1", "-maxdepth", "1", "-printf", "%y\t%s\t%f\n"],
        )
        captured = self._run(argv, timeout=LIST_EXEC_TIMEOUT)
        if captured.returncode != 0:
            return f"ERROR: cannot list '{path}': {captured.output.decode('utf-8', 'replace')[:500]}"
        rows = []
        for line in captured.output.decode("utf-8", errors="replace").splitlines():
            if not line:
                continue
            kind, size, name = line.split("\t", 2)
            rows.append(f"{name}/" if kind == "d" else f"{name}  ({size} bytes)")
        rows.sort()
        note = ""
        if len(rows) > MAX_LIST_ENTRIES:
            rows = rows[:MAX_LIST_ENTRIES]
            note = f"\n[list capped at {MAX_LIST_ENTRIES} entries]"
        return ("\n".join(rows) or "(empty directory)") + note

    def grep(self, pattern: str, path: str = ".", glob: str | None = None,
             timeout: int = 30) -> str:
        rel, err = _rel(path)
        if err:
            return err
        cmd = ["/usr/bin/rg", "-n", "--no-heading", "-M", "300", "-e", pattern]
        if glob:
            cmd += ["-g", glob]
        cmd.append(rel)
        argv = docker_args.exec_argv(self.container, cmd)
        try:
            captured = self._run(argv, timeout=timeout + 10)
        except docker_cli.DockerError:
            return f"ERROR: grep timed out after {timeout}s — narrow the pattern or path."
        if captured.returncode not in (0, 1):
            return f"ERROR: grep failed: {captured.output.decode('utf-8', 'replace')[:500]}"
        text = captured.output.decode("utf-8", errors="replace")
        if not text.strip():
            return "No matches found."
        lines = [(l[2:] if l.startswith("./") else l) for l in text.splitlines()]
        return _cap("\n".join(lines), note=" — narrow the pattern or path for full results")
```

- [ ] **Step 10: Run to verify pass**

Run: `python -m pytest tests/test_docker_sandbox.py -v`
Expected: PASS

- [ ] **Step 11: Write the failing tests for `bash`**

Add to `tests/test_docker_sandbox.py`:

```python
def test_bash_exec_argv_and_shaping(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b"hi\n"))
    out = sb.bash("echo hi")
    assert "exit code: 0" in out
    assert "hi" in out
    argv, timeout, stdin = fake.calls[-1]
    assert argv == [
        "exec", "-w", "/work", "dw-abc123",
        "/bin/bash", "-c", 'ulimit -f 524288; exec bash -c "$1"', "_", "echo hi",
    ]
    assert timeout == 130  # 120s default + 10


def test_bash_blocked_command_never_execs(started):
    sb, fake, run_dir = started
    out = sb.bash("sudo ls")
    assert out.startswith("BLOCKED:")
    assert not fake.calls


def test_bash_timeout_returns_text_not_raise(started):
    sb, fake, run_dir = started

    def raise_timeout(argv, *, timeout, stdin=None):
        fake.calls.append((list(argv), timeout, stdin))
        raise DockerError("docker exec ... timed out after 11s")

    sb._run = raise_timeout
    out = sb.bash("sleep 600", timeout=1)
    assert "timed out after 1s" in out


def test_bash_nonzero_exit_reported(started):
    sb, fake, run_dir = started
    fake.script(["exec"], Captured(returncode=3, output=b"", truncated=False, timed_out=False))
    out = sb.bash("exit 3")
    assert "exit code: 3" in out


def test_bash_output_capped_note(started):
    sb, fake, run_dir = started
    fake.script(["exec"], Captured(returncode=0, output=b"x" * 100, truncated=True, timed_out=False))
    out = sb.bash("big output")
    assert "capped" in out
```

- [ ] **Step 12: Run to verify failure, then implement `bash`**

Run: `python -m pytest tests/test_docker_sandbox.py -v`
Expected: FAIL — `AttributeError: 'DockerSandbox' object has no attribute 'bash'`

Add to `class DockerSandbox`:

```python
    def bash(self, command: str, timeout: int = 120) -> str:
        reason = check_bash_command(command)
        if reason:
            return reason  # starts with "BLOCKED:"; ToolExecutor logs guardrail_block
        timeout = max(1, min(int(timeout), 600))
        argv = docker_args.exec_argv(
            self.container,
            ["/bin/bash", "-c", 'ulimit -f 524288; exec bash -c "$1"', "_", command],
        )
        try:
            captured = self._run(argv, timeout=timeout + 10)
        except docker_cli.DockerError:
            return _cap(f"ERROR: command timed out after {timeout}s.", cap=MAX_BASH_CHARS)
        out = captured.output.decode("utf-8", errors="replace").strip()
        note = " — bash output capped" if captured.truncated else ""
        return _cap(f"exit code: {captured.returncode}\n{out}", cap=MAX_BASH_CHARS, note=note)
```

- [ ] **Step 13: Run to verify pass**

Run: `python -m pytest tests/test_docker_sandbox.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 14: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 15: Commit**

```bash
git add dirtywork/tools.py dirtywork/sandbox/docker.py tests/test_tools_files.py tests/test_docker_sandbox.py
git commit -m "feat: implement DockerSandbox tool methods (read/write/edit/list/grep/bash)"
```

---

### Task 8: Reap and reset — restoring "backgrounded processes die when the command returns"

Spec §6: plain `docker exec` does not honor dirtywork's documented bash contract ("backgrounded processes are terminated when the command returns") — a `nohup sleep 300 &` inside a `bash` call survives the call. This task adds the after-every-`bash`-call reap (`docker top` parsing, OOM check) and the `reset` sequence it triggers (kill, fresh tether, ready-wait, restart-variant init, `sandbox_reset` transcript event).

**Files:**
- Modify: `dirtywork/sandbox/docker.py` (add `reset`, `_reap`, `_after_bash`; wire `_after_bash()` into `bash`)
- Test: additions to `tests/test_docker_sandbox.py`

**Interfaces:**
- Consumes: `docker_cli.T_QUERY`, `docker_cli.T_LIFECYCLE`, `docker_cli.DockerError` (Task 3); `self.transcript.write(event, **fields)` (existing `Transcript` contract).
- Produces: `DockerSandbox.reset(reason: str) -> None`; private `_reap() -> None`, `_after_bash() -> None`.

- [ ] **Step 1: Write the failing tests for reset on stray process / OOM**

Add to `tests/test_docker_sandbox.py`. First add a transcript-carrying fixture, next to `started`:

```python
@pytest.fixture()
def started_with_transcript(tmp_path: Path):
    from dirtywork.transcript import Transcript
    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["image", "inspect", "--format", "{{json .RepoDigests}}"],
                _ok(b'["dirtywork/worker@sha256:' + b"a" * 64 + b'"]'))
    fake.script(["volume", "create"], _ok())
    fake.script(["run"], _ok())
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())
    cfg = DockerConfig()
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    transcript = Transcript(run_dir / "transcript.jsonl")
    sb = DockerSandbox(cfg, run_dir=run_dir, transcript=transcript, run=fake.run, popen=fake.popen)
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    sb.start(worktree, repo, "abc123", "deadbeef" * 5)
    fake.calls.clear()
    return sb, fake, run_dir, transcript


_TOP_HEADER = b"UID  PID  PPID  C  STIME  TTY  TIME  CMD\n"


def test_reap_resets_and_writes_sandbox_reset_event_on_stray_process(started_with_transcript):
    import json
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["top"], _ok(
        _TOP_HEADER
        + b"501  1  0  0  10:00  ?  00:00:00  cat\n"
        + b"501  42  1  0  10:00  ?  00:00:00  sleep 300\n"
    ))
    fake.script(["exec"], _ok(b"ok\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))

    sb.bash("echo ok")
    transcript.close()

    events = [json.loads(l) for l in (run_dir / "transcript.jsonl").read_text().splitlines()]
    reset_events = [e for e in events if e["event"] == "sandbox_reset"]
    assert reset_events and reset_events[0]["reason"] == "stray process after bash"
    assert any(c[0][0] == "kill" for c in fake.calls)


def test_reap_resets_on_oom(started_with_transcript):
    import json
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["exec"], _ok(b"ok\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"true\n"))

    sb.bash("echo ok")
    transcript.close()

    events = [json.loads(l) for l in (run_dir / "transcript.jsonl").read_text().splitlines()]
    reset_events = [e for e in events if e["event"] == "sandbox_reset"]
    assert reset_events and reset_events[0]["reason"] == "oom"


def test_reset_uses_restart_variant_init(started_with_transcript):
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["top"], _ok(
        _TOP_HEADER
        + b"501  1  0  0  10:00  ?  00:00:00  cat\n"
        + b"501  42  1  0  10:00  ?  00:00:00  sleep 300\n"
    ))
    fake.script(["exec"], _ok(b"ok\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))

    sb.bash("echo ok")

    init_calls = [c for c in fake.calls if c[0][0] == "exec" and "/bin/sh" in c[0]]
    assert init_calls
    last_init_script = init_calls[-1][0][-1]
    assert "git read-tree HEAD" in last_init_script
    assert "read-tree -m -u HEAD" not in last_init_script


def test_reset_creates_a_fresh_tether(started_with_transcript):
    sb, fake, run_dir, transcript = started_with_transcript
    popens_before = len(fake.popens)
    fake.script(["top"], _ok(
        _TOP_HEADER
        + b"501  1  0  0  10:00  ?  00:00:00  cat\n"
        + b"501  42  1  0  10:00  ?  00:00:00  sleep 300\n"
    ))
    fake.script(["exec"], _ok(b"ok\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))

    sb.bash("echo ok")

    assert len(fake.popens) == popens_before + 1


def test_reset_can_be_called_directly(started_with_transcript):
    import json
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["exec"], _ok())

    sb.reset("manual test reset")
    transcript.close()

    events = [json.loads(l) for l in (run_dir / "transcript.jsonl").read_text().splitlines()]
    assert any(e["event"] == "sandbox_reset" and e["reason"] == "manual test reset" for e in events)


def test_reap_resets_when_docker_top_itself_fails(started_with_transcript):
    # A container killed while a docker exec was in flight (Task 16's live
    # lifecycle case) makes the SUBSEQUENT `docker top` call fail outright —
    # not "succeeds but shows a stray row". That must ALSO trigger a reset.
    import json
    sb, fake, run_dir, transcript = started_with_transcript
    fake.script(["top"], _fail(b"Error: No such container: dw-abc123"))
    fake.script(["exec"], _ok(b"ok\n"))

    sb.bash("echo ok")  # must not raise — reap recovers via reset
    transcript.close()

    events = [json.loads(l) for l in (run_dir / "transcript.jsonl").read_text().splitlines()]
    reset_events = [e for e in events if e["event"] == "sandbox_reset"]
    assert reset_events and reset_events[0]["reason"] == "container unreachable after bash"
    assert any(c[0][0] == "kill" for c in fake.calls)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_docker_sandbox.py -k "reap or reset" -v`
Expected: FAIL — `AttributeError: 'DockerSandbox' object has no attribute 'reset'`

- [ ] **Step 3: Implement `reset`, `_reap`, `_after_bash`, and wire into `bash`**

Add to `class DockerSandbox` in `dirtywork/sandbox/docker.py`:

```python
    def reset(self, reason: str) -> None:
        """Spec §3 "Reset" (used on a stray process, OOM, or a watchdog
        kill): docker kill SIGKILLs PID 1, so the whole container namespace
        dies and its tmpfs is wiped — but the volume and its contents
        persist (verified). Fresh tether, ready-wait, then init's restart
        variant (index only — never touches /work, so the working tree
        survives a reset even though the worker's git metadata in /gitdir
        does not)."""
        try:
            self._run(["kill", self.container], timeout=docker_cli.T_LIFECYCLE)
        except docker_cli.DockerError:
            pass
        if self._tether is not None:
            try:
                if self._tether.stdin is not None:
                    self._tether.stdin.close()
            except OSError:
                pass
            try:
                self._tether.wait(timeout=docker_cli.T_LIFECYCLE)
            except Exception:
                pass
        self._start_tether()
        self._wait_ready()
        self._init(restart=True)
        if self.transcript is not None:
            self.transcript.write("sandbox_reset", reason=reason)

    def _reap(self) -> None:
        """After every bash call (spec §6): docker top should show at most
        the lifetime tether (bare "cat", or "/sbin/docker-init -- cat" while
        tini is still attached). Any other row means a backgrounded process
        outlived the call — reset restores the documented contract. A
        nonzero `docker top` itself (the container is stopped, killed, or
        otherwise unreachable — e.g. `docker kill` fired while a `docker
        exec` was in flight) is ALSO a reset trigger: whatever state the
        container is in, a fresh one via reset() is the safe recovery."""
        top = self._run(["top", self.container], timeout=docker_cli.T_QUERY)
        if top.returncode != 0:
            self.reset("container unreachable after bash")
            return
        lines = top.output.decode("utf-8", errors="replace").splitlines()
        if lines:
            header_cols = lines[0].split()
            n = max(len(header_cols), 1)
            for line in lines[1:]:
                if not line.strip():
                    continue
                fields = line.split(None, n - 1)
                cmd = fields[-1] if fields else ""
                # --entrypoint /bin/cat means the tether row reads "/bin/cat"
                # (and tini's row "/sbin/docker-init -- /bin/cat"); a bare
                # "cat" is what the spec's experiment showed — accept both.
                if cmd in ("cat", "/bin/cat") or cmd.endswith("docker-init -- cat") \
                        or cmd.endswith("docker-init -- /bin/cat"):
                    continue
                self.reset("stray process after bash")
                return
        oom = self._run(
            ["inspect", "--format", "{{.State.OOMKilled}}", self.container],
            timeout=docker_cli.T_QUERY,
        )
        if oom.returncode == 0 and oom.output.decode("utf-8", errors="replace").strip() == "true":
            self.reset("oom")

    def _after_bash(self) -> None:
        self._reap()
```

Replace `bash`'s body to call `_after_bash()` on both the timeout path and the normal path:

```python
    def bash(self, command: str, timeout: int = 120) -> str:
        reason = check_bash_command(command)
        if reason:
            return reason  # starts with "BLOCKED:"; ToolExecutor logs guardrail_block
        timeout = max(1, min(int(timeout), 600))
        argv = docker_args.exec_argv(
            self.container,
            ["/bin/bash", "-c", 'ulimit -f 524288; exec bash -c "$1"', "_", command],
        )
        try:
            captured = self._run(argv, timeout=timeout + 10)
        except docker_cli.DockerError:
            result = _cap(f"ERROR: command timed out after {timeout}s.", cap=MAX_BASH_CHARS)
            self._after_bash()
            return result
        out = captured.output.decode("utf-8", errors="replace").strip()
        note = " — bash output capped" if captured.truncated else ""
        result = _cap(f"exit code: {captured.returncode}\n{out}", cap=MAX_BASH_CHARS, note=note)
        self._after_bash()
        return result
```

- [ ] **Step 4: Run to verify the stray-process/OOM/reset tests pass**

Run: `python -m pytest tests/test_docker_sandbox.py -k "reap or reset" -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Write the tests proving reap does NOT reset an innocuous tether**

Add to `tests/test_docker_sandbox.py`:

```python
def test_reap_allows_bare_cat_tether(started):
    sb, fake, run_dir = started
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    fake.script(["exec"], _ok(b"ok\n"))

    out = sb.bash("echo ok")

    assert "ok" in out
    assert not any(c[0][0] == "kill" for c in fake.calls)


def test_reap_allows_docker_init_tether(started):
    sb, fake, run_dir = started
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  /sbin/docker-init -- cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    fake.script(["exec"], _ok(b"ok\n"))

    out = sb.bash("echo ok")

    assert not any(c[0][0] == "kill" for c in fake.calls)
```

- [ ] **Step 6: Run to verify pass**

Run: `python -m pytest tests/test_docker_sandbox.py -v`
Expected: PASS (all tests in the file, including the 8 new reap/reset ones)

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 8: Commit**

```bash
git add dirtywork/sandbox/docker.py tests/test_docker_sandbox.py
git commit -m "feat: reap stray processes and OOM after every bash call, add DockerSandbox.reset"
```

---

### Task 9: `Watchdog` and its `DockerSandbox` integration

Spec §6's watchdog thread: a host free-space floor polled every 0.5 s for the container's whole lifetime, and a worktree-size sample every 5 s while a `bash` call is in flight (best-effort disk-exhaustion bound, threat model (d)). This task also adds the *synchronous* sample the spec requires "once right after each bash call" — done by the sandbox directly, not the background thread, so it happens exactly when a call returns rather than on the thread's own timing. `DockerSandbox.start()` constructs the `Watchdog` but does **not** start its background thread — that thread does real `time.sleep`/`shutil.disk_usage` work with no injectable clock in production use, which is wrong to run inside a unit test suite. `dirtywork/__main__.py` (Task 12) is the only place that calls `sandbox.watchdog.start()`, right after a real `sandbox.start()` succeeds; `DockerSandbox.stop()` always calls `.stop()`/`.join()` on it (both safe to call on a never-started thread).

**Files:**
- Create: `dirtywork/sandbox/watchdog.py`
- Modify: `dirtywork/sandbox/docker.py` (`__init__` gains `self.watchdog = None`; `start()` constructs it; `stop()` tears it down; add `_sample_worktree`, `_watchdog_kill`; `_after_bash` surfaces `BudgetExceeded`; `bash` brackets the exec with `note_bash_start`/`note_bash_end`)
- Test: `tests/test_watchdog.py`; additions to `tests/test_docker_sandbox.py` (including updates to the `started`/`started_with_transcript` fixtures)

**Interfaces:**
- Consumes: `dirtywork.budget.BudgetExceeded` (SP1); `dirtywork.sandbox.docker_cli.{T_QUERY, DockerError}`; `dirtywork.sandbox.docker_args.exec_argv`.
- Produces: `class Watchdog(threading.Thread)` with `__init__(self, kill: Callable[[str], None], sample: Callable[[], tuple], storage_paths: list, *, min_free_mb: int, max_worktree_mb: int, max_worktree_files: int, clock=time.monotonic, sleep=time.sleep)`; `.start()`/`.stop()` (inherited/overridden `Thread` lifecycle); `.note_bash_start()`/`.note_bash_end()`; `.violation: str | None`; `.check_worktree_budget_once() -> bool` (additive — see the note below); `DockerSandbox.watchdog` attribute (set by `start()`), `DockerSandbox._sample_worktree() -> tuple`, `DockerSandbox._watchdog_kill(reason: str) -> None`.

Note on `check_worktree_budget_once`: the shared cross-plan contract lists `Watchdog`'s public surface as `start()/stop()`, `note_bash_start()/note_bash_end()`, and `.violation`. The spec's synchronous post-bash sample ("called synchronously by the sandbox, not the thread") and the thread's own periodic 5 s sample need to run the *exact same* sample-then-compare-then-kill logic, so it is factored into one additional public method both call — the background loop calls it on its own timer, `DockerSandbox._after_bash` calls it once right after every `bash` return. This is additive, not a rename or removal of anything in the shared contract.

- [ ] **Step 1: Write the failing unit tests for `Watchdog`**

```python
# tests/test_watchdog.py
from __future__ import annotations

import time
from pathlib import Path

from dirtywork.sandbox.watchdog import Watchdog


def test_note_bash_start_and_end_toggle_in_flight():
    wdg = Watchdog(kill=lambda r: None, sample=lambda: (0, 0), storage_paths=[],
                    min_free_mb=1, max_worktree_mb=1, max_worktree_files=1)
    assert wdg._bash_in_flight is False
    wdg.note_bash_start()
    assert wdg._bash_in_flight is True
    wdg.note_bash_end()
    assert wdg._bash_in_flight is False


def test_check_worktree_budget_once_under_caps_no_kill():
    kills = []
    wdg = Watchdog(kill=lambda r: kills.append(r), sample=lambda: (1024, 5),
                    storage_paths=[], min_free_mb=1, max_worktree_mb=2048,
                    max_worktree_files=200_000)
    result = wdg.check_worktree_budget_once()
    assert result is False
    assert kills == []
    assert wdg.violation is None


def test_check_worktree_budget_once_over_mb_cap_kills():
    kills = []
    wdg = Watchdog(kill=lambda r: kills.append(r), sample=lambda: (3 * 1024 * 1024, 10),
                    storage_paths=[], min_free_mb=1, max_worktree_mb=2048,
                    max_worktree_files=200_000)
    result = wdg.check_worktree_budget_once()
    assert result is True
    assert kills and "worktree exceeds" in kills[0]
    assert wdg.violation == kills[0]


def test_check_worktree_budget_once_over_file_cap_kills():
    kills = []
    wdg = Watchdog(kill=lambda r: kills.append(r), sample=lambda: (10, 500_000),
                    storage_paths=[], min_free_mb=1, max_worktree_mb=2048,
                    max_worktree_files=200_000)
    result = wdg.check_worktree_budget_once()
    assert result is True
    assert kills and "worktree exceeds" in kills[0]


def test_run_loop_kills_on_disk_floor_breach(tmp_path, monkeypatch):
    import dirtywork.sandbox.watchdog as wd

    class FakeUsage:
        free = 100 * 1024 * 1024  # 100 MB, below the 2048 MB floor

    monkeypatch.setattr(wd.shutil, "disk_usage", lambda path: FakeUsage())

    kills = []
    wdg = wd.Watchdog(
        kill=lambda reason: kills.append(reason), sample=lambda: (0, 0),
        storage_paths=[tmp_path], min_free_mb=2048, max_worktree_mb=2048,
        max_worktree_files=200_000, clock=lambda: 0.0, sleep=lambda s: None,
    )

    wdg.run()  # call directly (not .start()) for deterministic single-thread testing

    assert kills == ["host free space below 2048 MB"]
    assert wdg.violation == "host free space below 2048 MB"


def test_run_loop_kills_on_worktree_over_cap_while_bash_in_flight(tmp_path, monkeypatch):
    import dirtywork.sandbox.watchdog as wd

    class FakeUsage:
        free = 10 * 1024 * 1024 * 1024  # 10 GB, plenty

    monkeypatch.setattr(wd.shutil, "disk_usage", lambda path: FakeUsage())

    kills = []
    clock = {"t": 0.0}
    wdg = wd.Watchdog(
        kill=lambda reason: kills.append(reason),
        sample=lambda: (3 * 1024 * 1024, 10),  # 3 GB, over the 2048 MB cap
        storage_paths=[tmp_path], min_free_mb=2048, max_worktree_mb=2048,
        max_worktree_files=200_000,
        clock=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
    )
    wdg.note_bash_start()

    wdg.run()

    assert kills and "worktree exceeds" in kills[0]


def test_run_loop_does_not_sample_worktree_when_no_bash_in_flight(tmp_path, monkeypatch):
    import dirtywork.sandbox.watchdog as wd

    class FakeUsage:
        free = 10 * 1024 * 1024 * 1024

    monkeypatch.setattr(wd.shutil, "disk_usage", lambda path: FakeUsage())

    sample_calls = []
    clock = {"t": 0.0}

    def fake_sample():
        sample_calls.append(1)
        return (3 * 1024 * 1024, 10)  # would violate if ever sampled

    stop_after = {"n": 20}

    def fake_sleep(s):
        clock["t"] += s
        stop_after["n"] -= 1
        if stop_after["n"] <= 0:
            wdg.stop()

    wdg = wd.Watchdog(
        kill=lambda reason: None, sample=fake_sample,
        storage_paths=[tmp_path], min_free_mb=2048, max_worktree_mb=2048,
        max_worktree_files=200_000, clock=lambda: clock["t"], sleep=fake_sleep,
    )
    # note_bash_start() is never called — no bash call in flight

    wdg.run()

    assert sample_calls == []


def test_stop_sets_stop_event_and_thread_exits(tmp_path, monkeypatch):
    import dirtywork.sandbox.watchdog as wd

    class FakeUsage:
        free = 10 * 1024 * 1024 * 1024

    monkeypatch.setattr(wd.shutil, "disk_usage", lambda path: FakeUsage())
    wdg = wd.Watchdog(kill=lambda r: None, sample=lambda: (0, 0), storage_paths=[tmp_path],
                       min_free_mb=1, max_worktree_mb=1, max_worktree_files=1,
                       sleep=lambda s: time.sleep(0.01))

    wdg.start()
    wdg.stop()
    wdg.join(timeout=2)

    assert not wdg.is_alive()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_watchdog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dirtywork.sandbox.watchdog'`

- [ ] **Step 3: Write `dirtywork/sandbox/watchdog.py`**

```python
# dirtywork/sandbox/watchdog.py
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from typing import Callable


class Watchdog(threading.Thread):
    """Background thread for the container's whole lifetime (spec §6):
    every 0.5s, the smaller of the host free-space across `storage_paths`
    is compared to `min_free_mb`; every 5s while a bash call is in flight,
    `sample()` (worktree kbytes, entry count) is compared to
    `max_worktree_mb`/`max_worktree_files`. A breach calls `kill(reason)`
    and records `.violation`, then the loop returns (the container is dead;
    nothing more to watch until the sandbox resets or stops it).

    The synchronous post-bash-call sample the spec also requires is NOT run
    by this thread — DockerSandbox calls `check_worktree_budget_once()`
    directly right after each `bash` return, so that check happens exactly
    when the spec says to, independent of this thread's own timer.
    """

    DISK_POLL_INTERVAL = 0.5
    WORKTREE_POLL_INTERVAL = 5.0

    def __init__(self, kill: Callable, sample: Callable, storage_paths: list, *,
                 min_free_mb: int, max_worktree_mb: int, max_worktree_files: int,
                 clock=time.monotonic, sleep=time.sleep):
        super().__init__(daemon=True)
        self.kill = kill
        self.sample = sample
        self.storage_paths = list(storage_paths)
        self.min_free_mb = min_free_mb
        self.max_worktree_mb = max_worktree_mb
        self.max_worktree_files = max_worktree_files
        self.clock = clock
        self.sleep = sleep
        self.violation: str | None = None
        self._bash_in_flight = False
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def note_bash_start(self) -> None:
        with self._lock:
            self._bash_in_flight = True

    def note_bash_end(self) -> None:
        with self._lock:
            self._bash_in_flight = False

    def stop(self) -> None:
        self._stop_event.set()

    def _check_disk(self) -> bool:
        try:
            free_mb = min(shutil.disk_usage(str(p)).free for p in self.storage_paths) / (1024 * 1024)
        except (OSError, ValueError):
            return False
        if free_mb < self.min_free_mb:
            reason = f"host free space below {self.min_free_mb} MB"
            self.violation = reason
            self.kill(reason)
            return True
        return False

    def check_worktree_budget_once(self) -> bool:
        """One worktree-size sample-and-check. Called by this thread's own
        loop (every 5s while a bash call is in flight) AND, synchronously,
        by DockerSandbox right after every bash call returns."""
        kbytes, entries = self.sample()
        mb = kbytes / 1024
        if mb > self.max_worktree_mb or entries > self.max_worktree_files:
            reason = (
                f"worktree exceeds {self.max_worktree_mb} MB or "
                f"{self.max_worktree_files} files (sampled {mb:.1f} MB, {entries} files)"
            )
            self.violation = reason
            self.kill(reason)
            return True
        return False

    def run(self) -> None:
        last_worktree_check = self.clock()
        while not self._stop_event.is_set():
            if self._check_disk():
                return
            with self._lock:
                in_flight = self._bash_in_flight
            if in_flight and self.clock() - last_worktree_check >= self.WORKTREE_POLL_INTERVAL:
                last_worktree_check = self.clock()
                if self.check_worktree_budget_once():
                    return
            self.sleep(self.DISK_POLL_INTERVAL)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_watchdog.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Wire `Watchdog` into `DockerSandbox` — write the failing tests first**

Modify the `started` and `started_with_transcript` fixtures in `tests/test_docker_sandbox.py` to pre-script the watchdog's worktree-sample exec call (once `_after_bash` starts sampling after every `bash` call, the generic `["exec"]` default — which returns plain `"ok\n"` — is not shaped like `du`/`find` output, so it must be overridden with a safe, under-the-caps response for these two fixtures' tests to keep passing). Add this constant near `_TOP_HEADER`:

```python
_SAMPLE_ARGV = ["exec", "-w", "/work", "dw-abc123", "/bin/sh", "-c",
                "du -sk /work; find /work | wc -l"]
```

Replace the `started` fixture:

```python
@pytest.fixture()
def started(docker, tmp_path: Path):
    sb, fake, run_dir = docker
    fake.script(_SAMPLE_ARGV, _ok(b"1024\t/work\n5\n"))  # 1 MB, 5 files: safely under caps
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    sb.start(worktree, repo, "abc123", "deadbeef" * 5)
    fake.calls.clear()
    return sb, fake, run_dir
```

Replace the `started_with_transcript` fixture:

```python
@pytest.fixture()
def started_with_transcript(tmp_path: Path):
    from dirtywork.transcript import Transcript
    fake = FakeDocker()
    fake.script(["container", "inspect"], _fail())
    fake.script(["volume", "inspect"], _fail())
    fake.script(["image", "inspect", "--format", "{{json .RepoDigests}}"],
                _ok(b'["dirtywork/worker@sha256:' + b"a" * 64 + b'"]'))
    fake.script(["volume", "create"], _ok())
    fake.script(["run"], _ok())
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())
    fake.script(_SAMPLE_ARGV, _ok(b"1024\t/work\n5\n"))
    cfg = DockerConfig()
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    transcript = Transcript(run_dir / "transcript.jsonl")
    sb = DockerSandbox(cfg, run_dir=run_dir, transcript=transcript, run=fake.run, popen=fake.popen)
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    sb.start(worktree, repo, "abc123", "deadbeef" * 5)
    fake.calls.clear()
    return sb, fake, run_dir, transcript
```

Now add the new tests:

```python
def test_start_creates_watchdog_with_configured_caps_but_does_not_start_thread(docker, tmp_path):
    sb, fake, run_dir = docker
    repo = _fake_repo(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    sb.start(worktree, repo, "abc123", "deadbeef" * 5)

    assert sb.watchdog is not None
    assert sb.watchdog.min_free_mb == sb.cfg.min_free_mb
    assert sb.watchdog.max_worktree_mb == sb.cfg.max_worktree_mb
    assert sb.watchdog.max_worktree_files == sb.cfg.max_worktree_files
    assert not sb.watchdog.is_alive()


def test_bash_calls_watchdog_note_start_and_end(started):
    sb, fake, run_dir = started
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    fake.script(["exec"], _ok(b"ok\n"))
    events = []
    sb.watchdog.note_bash_start = lambda: events.append("start")
    sb.watchdog.note_bash_end = lambda: events.append("end")

    sb.bash("echo ok")

    assert events == ["start", "end"]


def test_bash_raises_budget_exceeded_when_watchdog_violation_already_set(started):
    from dirtywork.budget import BudgetExceeded
    sb, fake, run_dir = started
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    fake.script(["exec"], _ok(b"ok\n"))
    sb.watchdog.violation = "pre-existing violation for this test"

    with pytest.raises(BudgetExceeded, match="pre-existing violation"):
        sb.bash("echo ok")


def test_bash_watchdog_detects_over_cap_sample_and_raises(started):
    from dirtywork.budget import BudgetExceeded
    sb, fake, run_dir = started
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    fake.script(["exec"], _ok(b"ok\n"))
    fake.script(_SAMPLE_ARGV, _ok(b"3145728\t/work\n10\n"))  # 3 GB, over the 2048 MB default

    with pytest.raises(BudgetExceeded, match="worktree exceeds"):
        sb.bash("echo ok")

    assert any(c[0][0] == "kill" for c in fake.calls)


def test_sample_worktree_failure_then_success_after_reset(started):
    sb, fake, run_dir = started
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    fake.script(["exec"], _ok(b"ok\n"))
    fake.script(_SAMPLE_ARGV, [_fail(b"exec failed: pid saturation"), _ok(b"1024\t/work\n5\n")])

    out = sb.bash("echo ok")  # must not raise

    assert "ok" in out
    assert any(c[0][0] == "kill" for c in fake.calls)  # the sample-failure reset


def test_sample_worktree_failure_twice_raises_sandboxerror(started):
    sb, fake, run_dir = started
    fake.script(["top"], _ok(_TOP_HEADER + b"501  1  0  0  10:00  ?  00:00:00  cat\n"))
    fake.script(["inspect", "--format", "{{.State.OOMKilled}}"], _ok(b"false\n"))
    fake.script(["exec"], _ok(b"ok\n"))
    fake.script(_SAMPLE_ARGV, _fail(b"exec failed: pid saturation"))  # always fails

    with pytest.raises(SandboxError, match="sample failed twice"):
        sb.bash("echo ok")
```

- [ ] **Step 6: Run to verify failure**

Run: `python -m pytest tests/test_docker_sandbox.py -v`
Expected: FAIL — `AttributeError: 'DockerSandbox' object has no attribute 'watchdog'` (and the pre-existing bash tests from Tasks 7-8 now fail too, since `_SAMPLE_ARGV` scripting was added but `_after_bash` doesn't sample yet — this is expected mid-step; Step 7 makes everything pass together).

- [ ] **Step 7: Wire `Watchdog` into `DockerSandbox`**

In `dirtywork/sandbox/docker.py`, add to the imports:

```python
from ..budget import BudgetExceeded
from .watchdog import Watchdog
```

In `DockerSandbox.__init__`, add `self.watchdog = None` alongside the other attributes (after `self._stopped = False`):

```python
        self._stopped = False
        self.watchdog = None
```

In `start()`, after the `self._init(restart=False)` call, add:

```python
        self.watchdog = Watchdog(
            kill=self._watchdog_kill,
            sample=self._sample_worktree,
            storage_paths=docker_cli.docker_storage_paths(run=self._run),
            min_free_mb=self.cfg.min_free_mb,
            max_worktree_mb=self.cfg.max_worktree_mb,
            max_worktree_files=self.cfg.max_worktree_files,
        )
        # Constructed, not started: the background thread does real
        # time.sleep/shutil.disk_usage work with no injectable clock in
        # production use. dirtywork/__main__.py starts it explicitly right
        # after a real sandbox.start() succeeds (Task 12).
```

Add these two methods to `class DockerSandbox`:

```python
    def _watchdog_kill(self, reason: str) -> None:
        try:
            self._run(["kill", self.container], timeout=docker_cli.T_LIFECYCLE)
        except docker_cli.DockerError:
            pass

    def _sample_worktree(self) -> tuple:
        """(kbytes, entries) for /work, sampled inside the container. On
        exec failure, resets once and retries; a second failure raises
        SandboxError (spec §6: "If the exec itself fails ... → reset, then
        re-measure; a second failure → sandbox_error")."""
        for attempt in range(2):
            argv = docker_args.exec_argv(
                self.container, ["/bin/sh", "-c", "du -sk /work; find /work | wc -l"]
            )
            try:
                captured = self._run(argv, timeout=docker_cli.T_QUERY)
            except docker_cli.DockerError:
                captured = None
            if captured is not None and captured.returncode == 0:
                lines = captured.output.decode("utf-8", errors="replace").splitlines()
                try:
                    kbytes = int(lines[0].split()[0])
                    entries = int(lines[-1].strip())
                except (IndexError, ValueError):
                    pass
                else:
                    return kbytes, entries
            if attempt == 0:
                self.reset("budget sample failed")
        raise SandboxError("worktree budget sample failed twice in a row")
```

Replace `_after_bash`:

```python
    def _after_bash(self) -> None:
        self._reap()
        if self.watchdog is not None:
            self.watchdog.check_worktree_budget_once()
            if self.watchdog.violation is not None:
                violation = self.watchdog.violation
                self.watchdog.violation = None
                raise BudgetExceeded(violation)
```

Replace `bash` to bracket the exec with `note_bash_start`/`note_bash_end`:

```python
    def bash(self, command: str, timeout: int = 120) -> str:
        reason = check_bash_command(command)
        if reason:
            return reason  # starts with "BLOCKED:"; ToolExecutor logs guardrail_block
        timeout = max(1, min(int(timeout), 600))
        argv = docker_args.exec_argv(
            self.container,
            ["/bin/bash", "-c", 'ulimit -f 524288; exec bash -c "$1"', "_", command],
        )
        if self.watchdog is not None:
            self.watchdog.note_bash_start()
        try:
            captured = self._run(argv, timeout=timeout + 10)
        except docker_cli.DockerError:
            if self.watchdog is not None:
                self.watchdog.note_bash_end()
            result = _cap(f"ERROR: command timed out after {timeout}s.", cap=MAX_BASH_CHARS)
            self._after_bash()
            return result
        if self.watchdog is not None:
            self.watchdog.note_bash_end()
        out = captured.output.decode("utf-8", errors="replace").strip()
        note = " — bash output capped" if captured.truncated else ""
        result = _cap(f"exit code: {captured.returncode}\n{out}", cap=MAX_BASH_CHARS, note=note)
        self._after_bash()
        return result
```

Finally, update `stop()` to tear down the watchdog (add this at the top of the method body, before the container `rm -f`):

```python
    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self.watchdog is not None:
            self.watchdog.stop()
            if self.watchdog.is_alive():
                self.watchdog.join(timeout=docker_cli.T_LIFECYCLE)
        if self.container is not None:
```

(the rest of `stop()` — the `rm -f`, tether teardown, `volume rm` — is unchanged from Task 8).

- [ ] **Step 8: Run to verify pass**

Run: `python -m pytest tests/test_docker_sandbox.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 9: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 10: Commit**

```bash
git add dirtywork/sandbox/watchdog.py dirtywork/sandbox/docker.py tests/test_watchdog.py tests/test_docker_sandbox.py
git commit -m "feat: add Watchdog (disk floor + worktree sampling) and wire into DockerSandbox"
```

---

### Task 10: `extract_validated` — the export tar validator

Spec §7's "Validator" paragraph, verbatim: this is the code that stands between an attacker-controlled tar stream (the worker's whole tree, archived by `git archive` inside a container the worker fully controlled) and the host filesystem. No `tarfile.extract()`/`extractall()` is ever called — this project's Python 3.9 floor has no `tarfile.data_filter` (added in 3.12), so every member is inspected and written by hand: directories via `os.makedirs`, files via `tarfile.extractfile()` piped into `os.open(O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW)`, symlinks via `os.symlink()` — this is strictly safer than trusting `tarfile`'s own path-joining on an unpatched floor.

**Files:**
- Create: `dirtywork/sandbox/export.py` (this task adds `ExportError`, `ExportReport`, `extract_validated`; Task 11 adds `export_run`)
- Test: `tests/test_export_validator.py`

**Interfaces:**
- Consumes: `dirtywork.sandbox.SandboxError`; stdlib `tarfile`, `os`, `posixpath`, `shutil`.
- Produces: `class ExportError(SandboxError)`; `@dataclass class ExportReport: files: int; bytes: int; escaping_symlinks: list`; `extract_validated(stream, dest: Path, *, max_files: int, max_bytes: int) -> ExportReport`.

- [ ] **Step 1: Write the `_make_tar` test helper and the first three failing tests**

```python
# tests/test_export_validator.py
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_export_validator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dirtywork.sandbox.export'`

- [ ] **Step 3: Write `dirtywork/sandbox/export.py`, part 1 (`extract_validated`'s core)**

```python
# dirtywork/sandbox/export.py
from __future__ import annotations

import os
import posixpath
import shutil
import sys
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

from . import SandboxError


class ExportError(SandboxError):
    """Raised by the export flow or the tar validator. export_run (Task 11)
    catches this and turns it into RunArtifacts(export_status=...)."""


@dataclass
class ExportReport:
    files: int
    bytes: int
    escaping_symlinks: list


class _CountingReader:
    """Wraps the raw archive stream and raises ExportError as soon as more
    than max_bytes total have been READ from it — bounds the stream itself,
    not just the sum of each member's declared size (a hostile tar could
    lie about sizes in the header)."""

    def __init__(self, stream, max_bytes: int):
        self._stream = stream
        self._max_bytes = max_bytes
        self._read = 0

    def read(self, n=-1):
        chunk = self._stream.read(n)
        self._read += len(chunk)
        if self._read > self._max_bytes:
            raise ExportError(f"export archive exceeds {self._max_bytes} bytes")
        return chunk


def _cleanup_to_dot_git_only(dest: Path) -> None:
    for entry in dest.iterdir():
        if entry.name == ".git" and entry.is_file() and not entry.is_symlink():
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            try:
                entry.unlink()
            except OSError:
                pass


def extract_validated(stream, dest: Path, *, max_files: int, max_bytes: int) -> ExportReport:
    dest = Path(dest)
    existing = list(dest.iterdir())
    if len(existing) != 1 or existing[0].name != ".git" or not existing[0].is_file():
        raise ExportError("worktree not empty")

    dest_real = os.path.realpath(str(dest))
    counting = _CountingReader(stream, max_bytes)
    files = 0
    total_bytes = 0
    escaping_symlinks = []

    try:
        with tarfile.open(fileobj=counting, mode="r|") as tar:
            for member in tar:
                if tar.pax_headers:
                    raise ExportError("export archive contains a PAX global header")

                files += 1
                if files > max_files:
                    raise ExportError(f"export archive exceeds {max_files} files")
                total_bytes += max(member.size, 0)
                if total_bytes > max_bytes:
                    raise ExportError(f"export archive exceeds {max_bytes} bytes")

                if not (member.isreg() or member.isdir() or member.issym()):
                    raise ExportError(
                        f"export archive contains a disallowed member type at "
                        f"'{member.name}' (only regular files, directories, and "
                        f"symlinks are allowed)"
                    )

                name = member.name
                if posixpath.isabs(name):
                    raise ExportError(f"export archive contains an absolute path '{name}'")
                parts = name.split("/")
                if any(p in ("", ".", "..") for p in parts):
                    raise ExportError(
                        f"export archive contains an invalid path component in '{name}'"
                    )
                if any(p.lower() == ".git" for p in parts):
                    raise ExportError(f"export archive contains a .git-named entry '{name}'")

                target_path = dest / name
                target_real = os.path.realpath(str(target_path))
                if not (target_real == dest_real or target_real.startswith(dest_real + os.sep)):
                    raise ExportError(
                        f"export archive member '{name}' escapes the destination "
                        f"via a symlink created by an earlier member"
                    )

                if member.isdir():
                    os.makedirs(str(target_path), exist_ok=True)
                    os.chmod(str(target_path), 0o755)
                elif member.isreg():
                    os.makedirs(str(target_path.parent), exist_ok=True)
                    fh = tar.extractfile(member)
                    if fh is None:
                        raise ExportError(f"export archive member '{name}' has no content stream")
                    fd = os.open(str(target_path),
                                 os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
                    try:
                        with os.fdopen(fd, "wb") as out:
                            while True:
                                chunk = fh.read(65536)
                                if not chunk:
                                    break
                                out.write(chunk)
                    finally:
                        fh.close()
                    mode = 0o755 if (member.mode & 0o111) else 0o644
                    os.chmod(str(target_path), mode)
                elif member.issym():
                    os.makedirs(str(target_path.parent), exist_ok=True)
                    if sys.platform == "win32":
                        with open(target_path, "w", encoding="utf-8") as fh:
                            fh.write(member.linkname)
                    else:
                        os.symlink(member.linkname, str(target_path))
                        link_target = member.linkname
                        normalized = posixpath.normpath(
                            posixpath.join(posixpath.dirname(name), link_target)
                        )
                        if (posixpath.isabs(link_target) or normalized == ".."
                                or normalized.startswith("../")):
                            escaping_symlinks.append(name)
    except ExportError:
        _cleanup_to_dot_git_only(dest)
        raise
    except tarfile.TarError as e:
        _cleanup_to_dot_git_only(dest)
        raise ExportError(f"malformed export archive: {e}")

    return ExportReport(files=files, bytes=total_bytes, escaping_symlinks=escaping_symlinks)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_export_validator.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Write the remaining failing tests — symlinks, escapes, disallowed types, caps, name rules**

Add to `tests/test_export_validator.py`:

```python
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
```

- [ ] **Step 6: Run to verify pass**

Run: `python -m pytest tests/test_export_validator.py -v`
Expected: PASS (19 tests)

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 8: Commit**

```bash
git add dirtywork/sandbox/export.py tests/test_export_validator.py
git commit -m "feat: add extract_validated tar validator for the docker export flow"
```

---

### Task 11: `export_run` and `DockerSandbox.finalize`

Spec §7's full export flow, run against a **fresh** container (never the worker's own — the decision record: "no worker process is alive, `/gitdir` ... is gone, `/tmp` is clean, and the volume is mounted read-only"). This task also fixes a lifecycle ordering issue: the worker container must be removed before export, but the **volume must survive** until export finishes with it (export mounts the same volume, read-only) — and if export fails, the volume must survive the run's own `stop()` teardown too, so `runs export <slug>` can retry after the operator raises a limit. `DockerSandbox.stop()` is split into `_stop_container()`/`_stop_volume()` so `finalize()` can stop only the container before exporting, and volume removal after a failed export is suppressed.

**Files:**
- Modify: `dirtywork/sandbox/export.py` (add `export_run`; new imports `subprocess`, `time`, `from . import docker_args, docker_cli, RunArtifacts`)
- Modify: `dirtywork/sandbox/docker.py` (`__init__` gains `self._objects_dir = None` and `self._export_failed = False`; `start()` stores `self._objects_dir = objects_dir`; `stop()` is split into `_stop_container()`/`_stop_volume()`; add `finalize()`; imports gain `from . import export` and `from ..workspace import host_read_tree`)
- Test: `tests/test_export_flow.py` (new — duplicates a trimmed `FakeDocker`/`FakePopen`, per the "repeat code where needed" rule, so this file is independently runnable without cross-importing `tests/test_docker_sandbox.py`); additions to `tests/test_docker_sandbox.py` (`finalize` tests)

**Interfaces:**
- Consumes: `dirtywork.sandbox.docker_args.{export_create_argv, exec_argv, container_name, volume_name, repo_label}`; `dirtywork.sandbox.docker_cli.{run, T_LIFECYCLE, T_QUERY, T_EXPORT_STEP, DockerError, validate_objects_dir}`; `dirtywork.sandbox.export.{ExportError, extract_validated}` (same module, Task 10); `dirtywork.workspace.host_read_tree` (Task 5).
- Produces: `export_run(cfg, *, slug, base_commit, worktree: Path, run_dir: Path, objects_dir: Path, image_ref: str, uid: int, gid: int, repo_label: str, run=docker_cli.run, popen=subprocess.Popen) -> RunArtifacts`; `DockerSandbox.finalize() -> RunArtifacts`.

Note on `export_run`'s `repo_label` keyword: same reasoning as Task 4's `worker_create_argv`/`export_create_argv` — the shared cross-plan contract's positional parameter list (`cfg, slug, base_commit, worktree, run_dir, objects_dir, image_ref, uid, gid`) has no `repo` path to derive a label from, and `export_run` calls `docker_args.export_create_argv(..., repo_label=...)` which requires one. `repo_label` is therefore an additive, keyword-only parameter — `DockerSandbox.finalize()` computes it once via `docker_args.repo_label(self._repo)` and passes it in.

- [ ] **Step 1: Write the trimmed `FakeDocker`/`FakePopen` and the first failing test**

```python
# tests/test_export_flow.py
from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path

import pytest

from dirtywork.procs import Captured
from dirtywork.sandbox.docker_args import DockerConfig
from dirtywork.sandbox.export import export_run


class FakePopen:
    """Trimmed copy of tests/test_docker_sandbox.py's FakePopen (duplicated
    here so this file has no cross-file import dependency): .stdin/.stdout
    are real io.BytesIO objects; .wait()/.poll()/.kill() are scripted."""

    def __init__(self, argv, *, stdin=None, stdout=None, stderr=None, stdout_data: bytes = b""):
        self.argv = list(argv)
        self.stdin = io.BytesIO() if stdin == subprocess.PIPE else None
        self.stdout = io.BytesIO(stdout_data) if stdout == subprocess.PIPE else None
        self.returncode = None

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class FakeDocker:
    """Trimmed copy of tests/test_docker_sandbox.py's FakeDocker: longest-
    prefix-wins scripting for both `run()` and `popen()`'s stdout."""

    def __init__(self):
        self.responses = {}
        self.popen_stdout = {}
        self.calls = []
        self.popens = []
        self.default = Captured(returncode=0, output=b"", truncated=False, timed_out=False)

    def script(self, prefix, response) -> None:
        self.responses[tuple(prefix)] = response

    def script_popen_stdout(self, prefix, data: bytes) -> None:
        self.popen_stdout[tuple(prefix)] = data

    def run(self, argv, *, timeout, stdin=None):
        self.calls.append((list(argv), timeout, stdin))
        best_prefix, best_response = None, None
        for prefix, response in self.responses.items():
            if tuple(argv[: len(prefix)]) == prefix:
                if best_prefix is None or len(prefix) > len(best_prefix):
                    best_prefix, best_response = prefix, response
        if best_prefix is None:
            return self.default
        if isinstance(best_response, list):
            if len(best_response) > 1:
                return best_response.pop(0)
            return best_response[0]
        return best_response

    def popen(self, argv, *, stdin=None, stdout=None, stderr=None):
        best_prefix, best_data = None, b""
        for prefix, data in self.popen_stdout.items():
            if tuple(argv[: len(prefix)]) == prefix:
                if best_prefix is None or len(prefix) > len(best_prefix):
                    best_prefix, best_data = prefix, data
        p = FakePopen(argv, stdin=stdin, stdout=stdout, stderr=stderr, stdout_data=best_data)
        self.popens.append(p)
        return p


def _ok(output: bytes = b"") -> Captured:
    return Captured(returncode=0, output=output, truncated=False, timed_out=False)


def _fail(output: bytes = b"error") -> Captured:
    return Captured(returncode=1, output=output, truncated=False, timed_out=False)


def _make_tar(entries: list) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for e in entries:
            info = tarfile.TarInfo(e["name"])
            data = e.get("content", b"")
            info.size = len(data)
            info.mode = e.get("mode", 0o644)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture()
def empty_worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /somewhere\n")
    return wt


def test_export_run_refuses_when_worktree_not_empty(tmp_path, empty_worktree):
    (empty_worktree / "leftover.txt").write_text("stray")
    fake = FakeDocker()
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig()

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert artifacts.export_status == "export_failed: worktree not empty"
    assert not fake.calls
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_export_flow.py -v`
Expected: FAIL with `ImportError: cannot import name 'export_run' from 'dirtywork.sandbox.export'`

- [ ] **Step 3: Write `export_run` — append to `dirtywork/sandbox/export.py`**

Add to the imports at the top of `dirtywork/sandbox/export.py`:

```python
import subprocess
import threading
import time

from . import RunArtifacts, docker_args, docker_cli
```

Append to the file:

```python
def export_run(cfg, *, slug, base_commit, worktree: Path, run_dir: Path, objects_dir: Path,
                image_ref: str, uid: int, gid: int, repo_label: str, run=docker_cli.run,
                popen=subprocess.Popen) -> RunArtifacts:
    """Spec §7: the whole export flow, run against a FRESH container (never
    the worker's own). Any ExportError leaves the worktree cleaned back to
    just the .git file and the volume intact (`runs export <slug>` can
    retry after the operator raises a limit)."""
    diff_stat = ""
    patch_path = None
    dropped_git_entries: list = []
    escaping_symlinks: list = []
    worktree_bytes = None
    worktree_files = None

    existing = list(worktree.iterdir())
    if len(existing) != 1 or existing[0].name != ".git" or not existing[0].is_file():
        return RunArtifacts(export_status="export_failed: worktree not empty")

    name = f"{docker_args.container_name(slug)}-export"
    create_argv = docker_args.export_create_argv(cfg, slug, image_ref, uid, gid, objects_dir,
                                                  repo_label=repo_label)
    created = run(create_argv, timeout=docker_cli.T_LIFECYCLE)
    if created.returncode != 0:
        return RunArtifacts(
            export_status=f"export_failed: docker create {name} failed: "
                           f"{created.output.decode('utf-8', 'replace')[:500]}"
        )

    tether = popen(["docker", "start", "-ai", name],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _cleanup(keep_volume: bool) -> None:
        try:
            run(["rm", "-f", name], timeout=docker_cli.T_LIFECYCLE)
        except docker_cli.DockerError:
            pass
        try:
            if tether.stdin is not None:
                tether.stdin.close()
        except OSError:
            pass
        try:
            tether.wait(timeout=docker_cli.T_LIFECYCLE)
        except Exception:
            pass
        if not keep_volume:
            try:
                run(["volume", "rm", docker_args.volume_name(slug)], timeout=docker_cli.T_QUERY)
            except docker_cli.DockerError:
                pass

    def _fail(reason: str) -> RunArtifacts:
        _cleanup(keep_volume=True)  # export_failed always keeps the volume for retry
        _cleanup_to_dot_git_only(worktree)
        return RunArtifacts(
            diff_stat=diff_stat, patch_path=patch_path,
            worktree_bytes=worktree_bytes, worktree_files=worktree_files,
            escaping_symlinks=escaping_symlinks, dropped_git_entries=dropped_git_entries,
            export_status=f"export_failed: {reason}",
        )

    deadline = time.monotonic() + docker_cli.T_LIFECYCLE
    ready = False
    while time.monotonic() < deadline:
        try:
            captured = run(["exec", name, "/bin/true"], timeout=docker_cli.T_LIFECYCLE)
        except docker_cli.DockerError:
            time.sleep(0.05)
            continue
        if captured.returncode == 0:
            ready = True
            break
        time.sleep(0.05)
    if not ready:
        return _fail(f"export container {name} did not become ready")

    init_script = (
        "set -e; "
        "/usr/bin/git init -q; "
        "echo /repo.git/objects > /gitdir/objects/info/alternates; "
        f"/usr/bin/git symbolic-ref HEAD refs/heads/dirtywork/{slug}; "
        f"/usr/bin/git update-ref refs/heads/dirtywork/{slug} {base_commit}; "
        "/usr/bin/git read-tree HEAD"
    )
    init_argv = docker_args.exec_argv(
        name, ["/bin/sh", "-c", init_script],
        env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
    )
    init_captured = run(init_argv, timeout=docker_cli.T_LIFECYCLE)
    if init_captured.returncode != 0:
        return _fail(
            f"export container init failed: {init_captured.output.decode('utf-8', 'replace')[:500]}"
        )

    find_argv = docker_args.exec_argv(
        name, ["/usr/bin/find", "/work", "-mindepth", "1", "-iname", ".git"]
    )
    find_captured = run(find_argv, timeout=docker_cli.T_EXPORT_STEP)
    if find_captured.returncode == 0:
        for line in find_captured.output.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            dropped_git_entries.append(line[len("/work/"):] if line.startswith("/work/") else line)

    add_argv = docker_args.exec_argv(name, ["/usr/bin/git", "add", "-A"])
    add_captured = run(add_argv, timeout=docker_cli.T_EXPORT_STEP)
    if add_captured.returncode != 0:
        return _fail(f"git add -A failed: {add_captured.output.decode('utf-8', 'replace')[:500]}")

    wt_argv = docker_args.exec_argv(name, ["/usr/bin/git", "write-tree"])
    wt_captured = run(wt_argv, timeout=docker_cli.T_EXPORT_STEP)
    if wt_captured.returncode != 0:
        return _fail(f"git write-tree failed: {wt_captured.output.decode('utf-8', 'replace')[:500]}")
    tree = wt_captured.output.decode("utf-8", errors="replace").strip()

    stat_argv = docker_args.exec_argv(name, ["/usr/bin/git", "diff", "--stat", base_commit, tree])
    stat_captured = run(stat_argv, timeout=docker_cli.T_EXPORT_STEP)
    if stat_captured.returncode != 0:
        return _fail(
            f"git diff --stat failed: {stat_captured.output.decode('utf-8', 'replace')[:500]}"
        )
    raw_stat = stat_captured.output.decode("utf-8", errors="replace")
    diff_stat = raw_stat if len(raw_stat) <= 64_000 else raw_stat[:64_000] + "\n[diff_stat truncated at 64000 chars]"

    patch_target = run_dir / "diff.patch"
    diff_argv = ["docker"] + docker_args.exec_argv(name, ["/usr/bin/git", "diff", base_commit, tree])
    max_patch_bytes = cfg.max_patch_mb * 1024 * 1024
    diff_proc = popen(diff_argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    # Streamed steps cannot rely on run()'s timeout: a hung `docker exec` would
    # block .read() forever. A kill-timer bounds each streamed step at
    # T_EXPORT_STEP so the export fails closed like every other docker call.
    diff_timer = threading.Timer(docker_cli.T_EXPORT_STEP, diff_proc.kill)
    diff_timer.start()
    fd = os.open(str(patch_target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    written = 0
    truncated_patch = False
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = diff_proc.stdout.read(65536)
                if not chunk:
                    break
                if written < max_patch_bytes:
                    room = max_patch_bytes - written
                    piece = chunk[:room]
                    out.write(piece)
                    written += len(piece)
                    if len(chunk) > room:
                        truncated_patch = True
                else:
                    truncated_patch = True
            if truncated_patch:
                out.write(f"\n[patch truncated at {cfg.max_patch_mb} MB]\n".encode("utf-8"))
    finally:
        diff_timer.cancel()
        try:
            diff_proc.wait(timeout=10)
        except Exception:
            diff_proc.kill()
    if diff_proc.returncode != 0:
        return _fail(f"git diff failed or timed out (rc {diff_proc.returncode})")
    patch_path = str(patch_target)

    archive_argv = ["docker"] + docker_args.exec_argv(
        name, ["/usr/bin/git", "archive", "--format=tar", tree]
    )
    archive_proc = popen(archive_argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    archive_timer = threading.Timer(docker_cli.T_EXPORT_STEP, archive_proc.kill)
    archive_timer.start()
    try:
        report = extract_validated(archive_proc.stdout, worktree,
                                    max_files=cfg.max_worktree_files,
                                    max_bytes=cfg.max_worktree_mb * 1024 * 1024)
    except ExportError as e:
        archive_timer.cancel()
        archive_proc.kill()  # never wait on a process that may still be streaming
        try:
            archive_proc.wait(timeout=10)
        except Exception:
            pass
        return _fail(str(e))
    finally:
        archive_timer.cancel()
    try:
        archive_proc.wait(timeout=10)
    except Exception:
        archive_proc.kill()
    if archive_proc.returncode != 0:
        # the stream ended cleanly from tarfile's point of view but git archive
        # itself failed or was killed by the timer: the tree may be incomplete
        return _fail(f"git archive failed or timed out (rc {archive_proc.returncode})")
    worktree_bytes = report.bytes
    worktree_files = report.files
    escaping_symlinks = report.escaping_symlinks

    _cleanup(keep_volume=cfg.keep_volume)

    return RunArtifacts(
        diff_stat=diff_stat, patch_path=patch_path,
        worktree_bytes=worktree_bytes, worktree_files=worktree_files,
        escaping_symlinks=escaping_symlinks, dropped_git_entries=dropped_git_entries,
        export_status="ok",
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_export_flow.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Write the remaining failing tests for `export_run`**

Add to `tests/test_export_flow.py`:

```python
def test_export_run_happy_path(tmp_path, empty_worktree):
    fake = FakeDocker()
    fake.script(["create"], _ok())
    fake.script(["exec"], _ok())  # ready-wait, init, find, git add -A
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "write-tree"],
                _ok(b"treehash1234\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff", "--stat",
                 "deadbeef" * 5, "treehash1234"],
                _ok(b" 1 file changed, 1 insertion(+)\n"))
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff",
         "deadbeef" * 5, "treehash1234"],
        b"diff --git a/x b/x\n+hi\n")
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "archive",
         "--format=tar", "treehash1234"],
        _make_tar([{"name": "hello.txt", "content": b"hi there"}]))
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig()

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert artifacts.export_status == "ok"
    assert artifacts.diff_stat == " 1 file changed, 1 insertion(+)\n"
    assert artifacts.worktree_files == 1
    assert (empty_worktree / "hello.txt").read_bytes() == b"hi there"
    assert artifacts.patch_path == str(run_dir / "diff.patch")
    assert (run_dir / "diff.patch").read_bytes() == b"diff --git a/x b/x\n+hi\n"
    assert any(c[0][:2] == ["volume", "rm"] for c in fake.calls)

    create_argv = next(c[0] for c in fake.calls if c[0][0] == "create")
    assert create_argv[create_argv.index("--network") + 1] == "none"
    assert any(a.startswith("type=volume") and "readonly" in a and "dw-abc123-work" in a
               for a in create_argv)


def test_export_run_parses_dropped_git_entries(tmp_path, empty_worktree):
    fake = FakeDocker()
    fake.script(["exec"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/find", "/work",
                 "-mindepth", "1", "-iname", ".git"],
                _ok(b"/work/payload/.git\n/work/other/.GIT\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "write-tree"],
                _ok(b"treehash\n"))
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "archive",
         "--format=tar", "treehash"],
        _make_tar([]))
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig()

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert artifacts.dropped_git_entries == ["payload/.git", "other/.GIT"]


def test_export_run_git_add_failure_marks_export_failed_and_keeps_volume(tmp_path, empty_worktree):
    fake = FakeDocker()
    fake.script(["exec"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "add", "-A"],
                _fail(b"fatal: unable to add"))
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig()

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert artifacts.export_status.startswith("export_failed: git add -A failed")
    assert not any(c[0][:2] == ["volume", "rm"] for c in fake.calls)
    remaining = list(empty_worktree.iterdir())
    assert len(remaining) == 1 and remaining[0].name == ".git"


def test_export_run_patch_truncated_with_marker(tmp_path, empty_worktree):
    fake = FakeDocker()
    fake.script(["exec"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "write-tree"],
                _ok(b"treehash\n"))
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "diff",
         "deadbeef" * 5, "treehash"],
        b"x" * 2_000_000)
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "archive",
         "--format=tar", "treehash"],
        _make_tar([]))
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig(max_patch_mb=1)

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert artifacts.export_status == "ok"
    patch_bytes = (run_dir / "diff.patch").read_bytes()
    assert len(patch_bytes) <= 1024 * 1024 + 100
    assert b"[patch truncated at 1 MB]" in patch_bytes


def test_export_run_extract_validation_failure_marks_export_failed(tmp_path, empty_worktree):
    fake = FakeDocker()
    fake.script(["exec"], _ok())
    fake.script(["exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "write-tree"],
                _ok(b"treehash\n"))
    hostile_tar = _make_tar([{"name": "/etc/passwd", "content": b"pwned"}])
    fake.script_popen_stdout(
        ["docker", "exec", "-w", "/work", "dw-abc123-export", "/usr/bin/git", "archive",
         "--format=tar", "treehash"],
        hostile_tar)
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    cfg = DockerConfig()

    artifacts = export_run(
        cfg, slug="abc123", base_commit="deadbeef" * 5, worktree=empty_worktree,
        run_dir=run_dir, objects_dir=Path("/repo/.git/objects"),
        image_ref="dirtywork/worker@sha256:" + "a" * 64, uid=501, gid=20,
        repo_label="deadbeef", run=fake.run, popen=fake.popen,
    )

    assert artifacts.export_status.startswith("export_failed:")
    assert "absolute" in artifacts.export_status
    remaining = list(empty_worktree.iterdir())
    assert len(remaining) == 1 and remaining[0].name == ".git"
    assert not any(c[0][:2] == ["volume", "rm"] for c in fake.calls)
```

- [ ] **Step 6: Run to verify pass**

Run: `python -m pytest tests/test_export_flow.py -v`
Expected: PASS (6 tests)

- [ ] **Step 7: Write the failing tests for `DockerSandbox.finalize`**

Add to `tests/test_docker_sandbox.py`:

```python
def test_finalize_stops_container_calls_export_run_and_host_read_tree(started, monkeypatch):
    from dirtywork.sandbox import RunArtifacts
    sb, fake, run_dir = started
    seen = {}

    def fake_export_run(cfg, **kwargs):
        seen["export_kwargs"] = kwargs
        return RunArtifacts(export_status="ok", diff_stat="stat", worktree_bytes=10, worktree_files=1)

    def fake_host_read_tree(worktree):
        seen["host_read_tree_worktree"] = worktree

    import dirtywork.sandbox.docker as docker_mod
    monkeypatch.setattr(docker_mod.export, "export_run", fake_export_run)
    monkeypatch.setattr(docker_mod, "host_read_tree", fake_host_read_tree)

    artifacts = sb.finalize()

    assert artifacts.export_status == "ok"
    assert seen["export_kwargs"]["slug"] == "abc123"
    assert seen["host_read_tree_worktree"] == sb._worktree
    assert any(c[0][:2] == ["rm", "-f"] for c in fake.calls)  # worker container removed


def test_stop_after_finalize_keeps_volume_when_export_failed(started, monkeypatch):
    from dirtywork.sandbox import RunArtifacts
    sb, fake, run_dir = started

    import dirtywork.sandbox.docker as docker_mod
    monkeypatch.setattr(docker_mod.export, "export_run",
                         lambda cfg, **kw: RunArtifacts(export_status="export_failed: worktree not empty"))
    monkeypatch.setattr(docker_mod, "host_read_tree", lambda worktree: None)

    sb.finalize()
    fake.calls.clear()
    sb.stop()

    assert not any(c[0][:2] == ["volume", "rm"] for c in fake.calls)


def test_stop_after_finalize_removes_volume_when_export_succeeded(started, monkeypatch):
    from dirtywork.sandbox import RunArtifacts
    sb, fake, run_dir = started

    import dirtywork.sandbox.docker as docker_mod
    monkeypatch.setattr(docker_mod.export, "export_run",
                         lambda cfg, **kw: RunArtifacts(export_status="ok"))
    monkeypatch.setattr(docker_mod, "host_read_tree", lambda worktree: None)

    sb.finalize()
    fake.calls.clear()
    sb.stop()

    assert any(c[0][:2] == ["volume", "rm"] for c in fake.calls)
```

- [ ] **Step 8: Run to verify failure**

Run: `python -m pytest tests/test_docker_sandbox.py -k finalize -v`
Expected: FAIL — `AttributeError: 'DockerSandbox' object has no attribute 'finalize'`

- [ ] **Step 9: Implement — split `stop()`, add `finalize()`**

In `dirtywork/sandbox/docker.py`, add to the imports:

```python
from . import export
from ..workspace import host_read_tree
```

In `DockerSandbox.__init__`, add two attributes alongside `self.watchdog = None`:

```python
        self.watchdog = None
        self._objects_dir = None
        self._export_failed = False
```

In `start()`, change the line `objects_dir = docker_cli.validate_objects_dir(repo)` to also store it:

```python
        objects_dir = docker_cli.validate_objects_dir(repo)
        self._objects_dir = objects_dir
```

Replace the `stop()` method entirely with three methods:

```python
    def _stop_container(self) -> None:
        if self.watchdog is not None:
            self.watchdog.stop()
            if self.watchdog.is_alive():
                self.watchdog.join(timeout=docker_cli.T_LIFECYCLE)
        if self.container is not None:
            try:
                self._run(["rm", "-f", self.container], timeout=docker_cli.T_LIFECYCLE)
            except docker_cli.DockerError:
                pass
        if self._tether is not None:
            try:
                if self._tether.stdin is not None:
                    self._tether.stdin.close()
            except OSError:
                pass
            try:
                self._tether.wait(timeout=docker_cli.T_LIFECYCLE)
            except Exception:
                pass

    def _stop_volume(self) -> None:
        if self.volume is not None and not self.cfg.keep_volume and not self._export_failed:
            try:
                self._run(["volume", "rm", self.volume], timeout=docker_cli.T_QUERY)
            except docker_cli.DockerError:
                pass

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._stop_container()
        self._stop_volume()

    def finalize(self) -> RunArtifacts:
        """Spec §2 steps 10-11: stop the worker container (but NOT the
        volume — export needs it), export into the still-empty host
        worktree, then host_read_tree — the one host git command allowed to
        touch anything the worker produced."""
        self._stop_container()
        label = docker_args.repo_label(self._repo)
        artifacts = export.export_run(
            self.cfg, slug=self._slug, base_commit=self._base_commit,
            worktree=self._worktree, run_dir=self.run_dir, objects_dir=self._objects_dir,
            image_ref=self.image_ref, uid=self.uid, gid=self.gid, repo_label=label,
            run=self._run, popen=self._popen,
        )
        if artifacts.export_status.startswith("export_failed"):
            self._export_failed = True
        host_read_tree(self._worktree)
        return artifacts
```

- [ ] **Step 10: Run to verify pass**

Run: `python -m pytest tests/test_docker_sandbox.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 11: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 12: Commit**

```bash
git add dirtywork/sandbox/export.py dirtywork/sandbox/docker.py tests/test_export_flow.py tests/test_docker_sandbox.py
git commit -m "feat: add export_run and DockerSandbox.finalize, preserve volume on export failure"
```

---

### Task 12: CLI wiring — `dirtywork/__main__.py`

Spec §2's full host-side flow, steps 1-12, and the "Name collision"/preflight/`run_start` requirements from §3, assembled into `main()`: docker-mode preflight (`docker_version` → `resolve_image` → `validate_objects_dir`, exit 2 with the documented hint on `DockerError`) before any worktree exists; `run.json` written at step 4 and updated at the end; `DockerSandbox`/`HostSandbox` construction; the watchdog thread started only for real runs (never by unit tests); `run_start`'s `sandbox` dict; `schema_version`/`run_dir` in the stdout JSON; new statuses `sandbox_error` (already surfaced by `Runner`, Task 2) and `export_failed` (computed here, since it depends on the `finalize()` result rather than the agent loop's own outcome).

**Files:**
- Modify: `dirtywork/__main__.py` (full rewrite — shown in full below)
- Modify: `tests/test_main.py` (add `--sandbox none` to every `main([...])` call that isn't itself testing docker preflight, so no unit test depends on a real Docker daemon; add docker-specific tests)

**Interfaces:**
- Consumes: everything built in Tasks 1-11 — `rundir.{RUNS_DIR, ensure_runs_dir, create_run_dir, read_run_json, write_run_json, RunDirError}`; `workspace.{preflight_repo, ensure_worktrees_excluded, create_worktree, worktree_base_commit, load_repo_context, WorkspaceError}`; `sandbox.host.HostSandbox`; `sandbox.docker.DockerSandbox`; `sandbox.docker_args.{DockerConfig, DEFAULT_IMAGE, PINNED_DIGEST, container_name, volume_name}`; `sandbox.docker_cli.{docker_version, resolve_image, validate_objects_dir, DockerError}`; `tools.ToolExecutor`; `runner.Runner` (with SP1's `finalize=` parameter and `RunResult.extra`); `llm.{LLMError, LMStudioClient}`; `dirtywork.__version__`.
- Produces: the CLI contract described in the spec — flags `--sandbox docker|none` (default `docker`), `--image`, `--allow-network`, `--memory`, `--cpus`, `--tmp-size`, `--gitdir-size`, `--min-free-mb`, `--keep-volume`, `--max-patch-mb` (plus the SP1 `--max-worktree-mb`/`--max-worktree-files`); stdout JSON gains `schema_version: 2` and `run_dir`; `run.json` lifecycle per spec §2 steps 4 and 11.

- [ ] **Step 1: Write the failing test for the docker-preflight exit-2 hint**

```python
# tests/test_main.py — add near the top of the file
from dirtywork.sandbox.docker_cli import DockerError
```

```python
def test_main_docker_preflight_failure_exits_2_with_hint(tmp_path, monkeypatch, capsys):
    import subprocess
    import dirtywork.__main__ as m
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])

    def boom(*a, **k):
        raise DockerError("Cannot connect to the Docker daemon")

    monkeypatch.setattr(m, "docker_version", boom)

    rc = m.main(["run", "--repo", str(repo), "some task"])  # --sandbox defaults to docker

    assert rc == 2
    err = capsys.readouterr().err
    assert "Docker" in err
    assert "--sandbox none" in err
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_main.py::test_main_docker_preflight_failure_exits_2_with_hint -v`
Expected: FAIL — `dirtywork.__main__` has no attribute `docker_version` (docker preflight doesn't exist yet)

- [ ] **Step 3: Rewrite `dirtywork/__main__.py` in full**

```python
# dirtywork/__main__.py
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .llm import LLMError, LMStudioClient
from .rundir import RUNS_DIR, RunDirError, create_run_dir, ensure_runs_dir, read_run_json, write_run_json
from .runner import Runner
from .sandbox import SandboxError, docker_args, docker_cli
from .sandbox.docker import DockerSandbox
from .sandbox.docker_args import DEFAULT_IMAGE, DockerConfig
from .sandbox.docker_cli import DockerError, docker_version, resolve_image, validate_objects_dir
from .sandbox.host import HostSandbox
from .tools import ToolExecutor
from .transcript import Transcript
from .workspace import (
    WorkspaceError,
    create_worktree,
    ensure_worktrees_excluded,
    load_repo_context,
    make_slug,
    preflight_repo,
    worktree_base_commit,
)

DEFAULT_MODEL = "qwen/qwen3-coder-next"


def build_system_prompt(worktree: Path, repo_context: str | None) -> str:
    prompt = f"""You are a coding agent working in a git worktree at {worktree}.
Complete the task, then reply with a plain-text summary of what you changed and what commands you ran.

Rules:
- Use edit_file or write_file for ALL file changes. Never modify files via bash (no sed -i, no echo redirects, no heredocs).
- Paths are relative to the worktree root.
- Explore before editing: use list_dir, grep, and read_file to understand the code first.
- Verify your work: run the repo's tests or build via bash before declaring the task complete.
- Do not run git commit or git branch commands; leave all changes uncommitted for review.
- When the task is complete, reply WITHOUT calling any tools — that final plain reply ends the run."""
    if repo_context:
        prompt += f"\n\nRepository conventions (from the repo's own docs):\n\n{repo_context}"
    return prompt


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def _docker_preflight(repo: Path, image: str):
    """Spec §2 step 1: docker_version (daemon reachable) → resolve_image
    (digest, pulling if absent — the only network use at start) →
    validate_objects_dir (the only host path ever mounted). All read-only
    on the operator's clone; nothing is created yet."""
    docker_version()
    image_ref = resolve_image(image, pinned_digest=docker_args.PINNED_DIGEST)
    validate_objects_dir(repo)
    return image_ref


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dirtywork")
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
    run_p.add_argument("--max-worktree-mb", type=int, default=2048)
    run_p.add_argument("--max-worktree-files", type=int, default=200_000)
    run_p.add_argument("--sandbox", choices=["docker", "none"], default="docker")
    run_p.add_argument("--image", default=DEFAULT_IMAGE)
    run_p.add_argument("--allow-network", action="store_true", default=False)
    run_p.add_argument("--memory", default="4g")
    run_p.add_argument("--cpus", default="2")
    run_p.add_argument("--tmp-size", default="1g")
    run_p.add_argument("--gitdir-size", default="512m")
    run_p.add_argument("--min-free-mb", type=int, default=2048)
    run_p.add_argument("--keep-volume", action="store_true", default=False)
    run_p.add_argument("--max-patch-mb", type=int, default=10)
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

    image_ref = None
    if args.sandbox == "docker":
        try:
            image_ref = _docker_preflight(repo, args.image)
        except DockerError as e:
            _err(f"{e}\nDocker is the default sandbox since 0.3. Start Docker Desktop / "
                 f"dockerd, or pass --sandbox none to run unsandboxed on the host.")
            return 2
        except WorkspaceError as e:
            _err(str(e))
            return 2

    # ---- workspace ----
    slug = make_slug(args.task, datetime.now())
    if args.sandbox == "docker":
        # Spec §3 name collision: refuse (exit 2) BEFORE creating anything —
        # DockerSandbox.start() re-checks as defense in depth, but by then the
        # worktree and run dir exist, and "exit 2 creates nothing" must hold.
        for kind, argv in (("container", ["container", "inspect", docker_args.container_name(slug)]),
                           ("volume", ["volume", "inspect", docker_args.volume_name(slug)])):
            try:
                if docker_cli.run(argv, timeout=docker_cli.T_QUERY).returncode == 0:
                    _err(f"{kind} {argv[-1]} already exists; run `dirtywork runs clean {slug}` "
                         f"(dirtywork never removes anything it did not create in this run)")
                    return 2
            except DockerError as e:
                _err(str(e))
                return 2
    try:
        ensure_worktrees_excluded(repo)
        worktree = create_worktree(repo, slug, args.branch_from,
                                    no_checkout=(args.sandbox == "docker"))
    except WorkspaceError as e:
        _err(str(e))
        return 2
    base_commit = worktree_base_commit(worktree)

    try:
        runs_dir = ensure_runs_dir(RUNS_DIR)
        run_dir = create_run_dir(runs_dir, slug)
    except RunDirError as e:
        _err(str(e))
        return 2
    transcript_path = run_dir / "transcript.jsonl"
    print(f"transcript: {transcript_path}", file=sys.stderr)
    print(f"worktree:   {worktree}", file=sys.stderr)

    write_run_json(run_dir, {
        "schema_version": 2,
        "status": "running",
        "slug": slug,
        "repo": str(repo),
        "worktree": str(worktree),
        "branch": f"dirtywork/{slug}",
        "base_commit": base_commit,
        "container": docker_args.container_name(slug) if args.sandbox == "docker" else None,
        "volume": docker_args.volume_name(slug) if args.sandbox == "docker" else None,
        "image": args.image if args.sandbox == "docker" else None,
        "image_digest": image_ref,
        "host_pid": os.getpid(),
        "started": datetime.now(timezone.utc).isoformat(),
        "sandbox": args.sandbox,
    })

    # ---- run ----
    # Everything from here on is wrapped in one boundary so the machine
    # contract (exactly one JSON object on stdout, post-preflight) holds
    # even if a component other than runner.run() blows up.
    transcript = None
    sandbox = None
    try:
        if args.sandbox == "docker":
            cfg = DockerConfig(
                image=args.image,
                network="bridge" if args.allow_network else "none",
                memory=args.memory,
                cpus=args.cpus,
                tmp_size=args.tmp_size,
                gitdir_size=args.gitdir_size,
                max_worktree_mb=args.max_worktree_mb,
                max_worktree_files=args.max_worktree_files,
                min_free_mb=args.min_free_mb,
                max_patch_mb=args.max_patch_mb,
                keep_volume=args.keep_volume,
            )
            sandbox = DockerSandbox(cfg, run_dir=run_dir)
            sandbox.start(worktree, repo, slug, base_commit)
            sandbox.watchdog.start()  # only place a real Watchdog thread is started
            sandbox_info = {
                "backend": "docker", "image": args.image, "image_digest": image_ref,
                "network": cfg.network, "memory": cfg.memory, "cpus": cfg.cpus,
                "pids_limit": cfg.pids_limit, "tmp_size": cfg.tmp_size,
                "gitdir_size": cfg.gitdir_size, "max_worktree_mb": cfg.max_worktree_mb,
                "max_worktree_files": cfg.max_worktree_files,
                "user": f"{sandbox.uid}:{sandbox.gid}",
            }
        else:
            sandbox = HostSandbox(worktree, max_worktree_mb=args.max_worktree_mb,
                                   max_worktree_files=args.max_worktree_files)
            sandbox.start(worktree, repo, slug, base_commit)
            sandbox_info = "none"

        transcript = Transcript(transcript_path)
        executor = ToolExecutor(sandbox, transcript=transcript)

        def finalize():
            artifacts = sandbox.finalize()
            return {
                "diff_stat": artifacts.diff_stat,
                "patch_path": artifacts.patch_path,
                "worktree_bytes": artifacts.worktree_bytes,
                "worktree_files": artifacts.worktree_files,
                "escaping_symlinks": artifacts.escaping_symlinks,
                "dropped_git_entries": artifacts.dropped_git_entries,
                "export_status": artifacts.export_status,
            }

        runner = Runner(
            client, executor, transcript, model=args.model,
            max_turns=args.max_turns, timeout=args.timeout, temperature=args.temperature,
            run_info={
                "repo": str(repo), "worktree": str(worktree), "branch": f"dirtywork/{slug}",
                "branch_from": args.branch_from, "base_commit": base_commit,
                "base_url": args.base_url, "dirtywork_version": __version__,
                "temperature": args.temperature, "sandbox": sandbox_info, "provider": "openai",
            },
            finalize=finalize,
        )
        system_prompt = build_system_prompt(worktree, load_repo_context(repo, base_commit))
        result = runner.run(system_prompt, args.task)
    except Exception as e:
        # A SandboxError here comes from sandbox.start()/init or from a
        # sandbox failure the runner did not itself convert (spec §2 step 7:
        # teardown, status sandbox_error, exit 1). Everything else keeps the
        # existing model_error contract.
        if isinstance(e, SandboxError):
            fail_status, message = "sandbox_error", str(e)
        elif isinstance(e, LLMError):
            fail_status, message = "model_error", str(e)
        else:
            fail_status, message = "model_error", f"unexpected error: {e!r}"
        if transcript is not None:
            try:
                transcript.write("run_end", status=fail_status, error=message)
            except Exception:
                pass
        _err(message)
        try:
            existing = read_run_json(run_dir)
            existing.update(status=fail_status, ended=datetime.now(timezone.utc).isoformat())
            write_run_json(run_dir, existing)
        except Exception:
            pass
        print(json.dumps({
            "schema_version": 2,
            "status": fail_status,
            "worktree": str(worktree),
            "branch": f"dirtywork/{slug}",
            "transcript": str(transcript_path),
            "turns": None,
            "usage": {},
            "final_message": message,
            "run_dir": str(run_dir),
        }, indent=2))
        return 1
    finally:
        if sandbox is not None:
            try:
                sandbox.stop()
            except Exception:
                pass
        if transcript is not None:
            try:
                transcript.close()
            except Exception:
                pass

    extra = result.extra or {}
    export_status = extra.get("export_status", "n/a")
    final_status = result.status
    if isinstance(export_status, str) and export_status.startswith("export_failed") \
            and final_status == "completed":
        final_status = "export_failed"

    try:
        existing = read_run_json(run_dir)
        existing.update(
            status=final_status,
            ended=datetime.now(timezone.utc).isoformat(),
            diff_stat=extra.get("diff_stat"),
            export_status=export_status,
            patch_path=extra.get("patch_path"),
        )
        write_run_json(run_dir, existing)
    except Exception:
        pass

    print(json.dumps({
        "schema_version": 2,
        "status": final_status,
        "worktree": str(worktree),
        "branch": f"dirtywork/{slug}",
        "transcript": str(transcript_path),
        "turns": result.turns,
        "usage": result.usage,
        "final_message": result.final_message,
        "run_dir": str(run_dir),
    }, indent=2))
    return 0 if final_status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the new test to verify pass**

Run: `python -m pytest tests/test_main.py::test_main_docker_preflight_failure_exits_2_with_hint -v`
Expected: PASS

- [ ] **Step 5: Fix pre-existing tests that would otherwise depend on a real Docker daemon**

Docker mode is now the default, so any pre-existing test that reaches past preflight without mocking docker calls would either hang or depend on whatever Docker state happens to exist on the machine running the suite — exactly what the unit suite must never do. `test_main_bad_repo_exits_2` and `test_main_lmstudio_down_exits_2` both return before docker preflight is ever reached (a bad repo fails `preflight_repo`; an unreachable LM Studio fails `client.list_models()`), so neither needs a change. Every other test in this file that calls `main([...])` and gets past those two checks must add `"--sandbox", "none"` to its argv list — it is testing something else (transcript lifecycle, error-JSON shape, `load_repo_context` sourcing) and has no business depending on Docker.

(SP1 already modified some of these tests for its own reasons — the `load_repo_context(repo, base_commit)` signature change, the `--max-worktree-mb`/`--max-worktree-files` flags, `rundir`-based run directories. Keep whatever SP1's version of each test body does; the only change this step requires is adding `"--sandbox", "none"` to each test's `main([...])` call.)

Update these four tests' `main([...])` calls to include `"--sandbox", "none"`:

```python
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "some task"])
```

— apply this exact argv-list change (adding `"--sandbox", "none"` right after `str(repo)`) to `test_transcript_closed_even_on_unexpected_error`, `test_transcript_construction_failure_still_prints_json`, `test_load_repo_context_uses_worktree_not_caller_checkout`, and `test_llm_error_during_run_prints_model_error_json`.

- [ ] **Step 6: Run to verify pass**

Run: `python -m pytest tests/test_main.py -v`
Expected: PASS (all pre-existing tests, plus the new hint test)

- [ ] **Step 7: Write the failing test for a full docker-mode happy path via a fake `DockerSandbox`**

Add to `tests/test_main.py`:

```python
def test_main_docker_mode_happy_path_with_fake_sandbox(tmp_path, monkeypatch, capsys):
    import subprocess
    import dirtywork.__main__ as m
    from dirtywork.runner import RunResult
    from dirtywork.sandbox import RunArtifacts
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])
    monkeypatch.setattr(m, "docker_version", lambda *a, **k: "29.7.2")
    monkeypatch.setattr(m, "resolve_image", lambda *a, **k: "dirtywork/worker@sha256:" + "a" * 64)
    monkeypatch.setattr(m, "validate_objects_dir", lambda repo: repo / ".git" / "objects")
    # the pre-worktree collision check inspects the container/volume names;
    # rc 1 == "no such object" == no collision
    from dirtywork.procs import Captured
    monkeypatch.setattr(m.docker_cli, "run",
                        lambda argv, *, timeout, stdin=None: Captured(1, b"", False, False))

    class FakeWatchdog:
        def start(self):
            pass

    class FakeDockerSandbox:
        def __init__(self, cfg, *, run_dir, transcript=None):
            self.cfg = cfg
            self.run_dir = run_dir
            self.uid = 501
            self.gid = 20
            self.watchdog = FakeWatchdog()

        def start(self, worktree, repo, slug, base_commit):
            pass

        def stop(self):
            pass

        def finalize(self):
            return RunArtifacts(export_status="ok", diff_stat="1 file changed")

    monkeypatch.setattr(m, "DockerSandbox", FakeDockerSandbox)

    def fake_run(self, system_prompt, task):
        return RunResult("completed", 1, "ok", {})

    monkeypatch.setattr(m.Runner, "run", fake_run)

    rc = m.main(["run", "--repo", str(repo), "some task"])  # --sandbox defaults to docker

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 2
    assert "run_dir" in payload
    assert payload["status"] == "completed"


def _docker_mode_scaffold(tmp_path, monkeypatch):
    """Shared setup for the docker-mode CLI tests below: a one-commit repo,
    LM Studio and docker preflight faked, run dir under tmp_path. Returns
    (module, repo)."""
    import subprocess
    import dirtywork.__main__ as m
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])
    monkeypatch.setattr(m, "docker_version", lambda *a, **k: "29.7.2")
    monkeypatch.setattr(m, "resolve_image", lambda *a, **k: "dirtywork/worker@sha256:" + "a" * 64)
    monkeypatch.setattr(m, "validate_objects_dir", lambda repo: repo / ".git" / "objects")
    return m, repo


def test_main_docker_name_collision_exits_2_and_creates_nothing(tmp_path, monkeypatch, capsys):
    import subprocess
    from dirtywork.procs import Captured
    m, repo = _docker_mode_scaffold(tmp_path, monkeypatch)
    # `docker container inspect dw-<slug>` succeeds → the name is taken
    monkeypatch.setattr(m.docker_cli, "run",
                        lambda argv, *, timeout, stdin=None: Captured(0, b"[{}]", False, False))

    rc = m.main(["run", "--repo", str(repo), "some task"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "already exists" in err and "runs clean" in err
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())
    wl = subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"],
                        capture_output=True, text=True).stdout
    assert wl.count("worktree ") == 1  # only the main checkout


def test_main_docker_start_failure_is_sandbox_error_exit_1(tmp_path, monkeypatch, capsys):
    from dirtywork.procs import Captured
    from dirtywork.sandbox import SandboxError
    m, repo = _docker_mode_scaffold(tmp_path, monkeypatch)
    monkeypatch.setattr(m.docker_cli, "run",
                        lambda argv, *, timeout, stdin=None: Captured(1, b"", False, False))

    class BoomSandbox:
        def __init__(self, cfg, *, run_dir, transcript=None):
            self.uid, self.gid = 501, 20
            self.stopped = False

        def start(self, worktree, repo, slug, base_commit):
            raise SandboxError("in-container git init failed: rc 128")

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(m, "DockerSandbox", BoomSandbox)

    rc = m.main(["run", "--repo", str(repo), "some task"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "sandbox_error"
    assert "git init failed" in payload["final_message"]
    run_json = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert run_json["status"] == "sandbox_error"
```

- [ ] **Step 8: Run to verify pass**

Run: `python -m pytest tests/test_main.py -v`
Expected: PASS (all tests, including the three new ones)

- [ ] **Step 9: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 10: Commit**

```bash
git add dirtywork/__main__.py tests/test_main.py
git commit -m "feat: wire docker sandbox into the CLI, docker default with exit-2 preflight hint"
```

---

### Task 13: `docker/Dockerfile`, `docker/README.md`, `docker` pytest marker

Spec decision record's image requirement: "Debian-based, `USER worker` (uid 1000), git, bash, coreutils, findutils, python3, node, .NET SDK, ripgrep." This task also introduces the `docker` marker (skipped automatically when no daemon is reachable) that every remaining task's real-daemon tests use.

**Files:**
- Create: `docker/Dockerfile`
- Create: `docker/README.md`
- Create: `tests/conftest.py` (auto-skip for the `docker` marker)
- Create: `tests/test_docker_image.py` (marker `docker`)
- Modify: `pyproject.toml`

**Interfaces:** None — this is an infrastructure/docs task. It defines the `docker` pytest marker and its auto-skip behavior, which every later docker-marked test file (Tasks 15, 16, and this one) relies on.

- [ ] **Step 1: Write `pyproject.toml`'s marker and addopts change**

Replace the `[tool.pytest.ini_options]` section:

```toml
[tool.pytest.ini_options]
markers = [
    "live: requires a running LM Studio server",
    "docker: requires a running Docker daemon",
]
addopts = "-m 'not live and not docker'"
```

- [ ] **Step 2: Write `tests/conftest.py`**

```python
# tests/conftest.py
from __future__ import annotations

import shutil
import subprocess

import pytest


def _docker_available() -> bool:
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return False
    try:
        result = subprocess.run([docker_bin, "version"], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


@pytest.fixture(scope="session")
def docker_available() -> bool:
    return _docker_available()


def pytest_collection_modifyitems(config, items):
    """Every test marked `docker` is skipped automatically when no daemon
    is reachable — `-m 'not live and not docker'` in addopts already
    excludes them from the default run, but running `pytest -m docker`
    explicitly (or any invocation that overrides addopts) must still not
    hang or fail hard on a machine without Docker."""
    if _docker_available():
        return
    skip_docker = pytest.mark.skip(reason="docker daemon not available")
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(skip_docker)
```

- [ ] **Step 3: Run to verify the marker and skip machinery work**

Run: `python -m pytest -q`
Expected: PASS, same count as before (no `docker`-marked tests exist yet, so `conftest.py` changes nothing observable)

- [ ] **Step 4: Write `docker/Dockerfile`**

```dockerfile
# docker/Dockerfile
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        bash \
        coreutils \
        findutils \
        python3 \
        python3-venv \
        nodejs \
        npm \
        ripgrep \
        ca-certificates \
        wget \
        gnupg \
    && rm -rf /var/lib/apt/lists/*

# .NET SDK via Microsoft's apt package feed — the documented route for
# Debian 12 (bookworm): https://learn.microsoft.com/en-us/dotnet/core/install/linux-debian
RUN wget -q https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
        -O /tmp/packages-microsoft-prod.deb \
    && dpkg -i /tmp/packages-microsoft-prod.deb \
    && rm /tmp/packages-microsoft-prod.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends dotnet-sdk-8.0 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -u 1000 -m worker

USER worker

# No ENTRYPOINT and no CMD: every `docker create`/`docker run` dirtywork
# issues passes an explicit --entrypoint (/bin/cat for the tether,
# /bin/chown for the prep container, /bin/sh for compound export steps) and
# every `docker exec` names its binary by absolute path — nothing in this
# image's own launch configuration is ever trusted to pick the running
# program (spec §3: "Entrypoint and PATH are always explicit").
#
# No WORKDIR either: /work is always a docker volume mounted fresh at
# container-create time. Baking a WORKDIR into the image at that same path
# would reproduce the verified bug where a container-level workdir over the
# volume resets its ownership to root:root, persistently. Every command
# sets -w /work itself via `docker exec` (dirtywork/sandbox/docker_args.py's
# exec_argv), which is verified harmless.
```

- [ ] **Step 5: Write `docker/README.md`**

```markdown
# dirtywork/worker image

Built from `docker/Dockerfile`: Debian bookworm-slim, `USER worker` (uid
1000), git, bash, coreutils, findutils, python3, node, .NET SDK, ripgrep.
No `ENTRYPOINT`/`CMD` — every `docker create`/`run`/`exec` in dirtywork
passes its own explicit `--entrypoint` or absolute binary path.

## Build

    docker build -t dirtywork/worker:0.3 docker/

## Verify locally

    docker run --rm --entrypoint /usr/bin/git dirtywork/worker:0.3 --version
    docker run --rm --entrypoint /usr/bin/rg dirtywork/worker:0.3 --version
    docker run --rm --entrypoint /usr/bin/python3 dirtywork/worker:0.3 --version
    docker run --rm --entrypoint /usr/bin/dotnet dirtywork/worker:0.3 --version

## Publish and pin a digest

`dirtywork/sandbox/docker_args.py`'s `PINNED_DIGEST` (default `None`) is
the supply-chain guarantee for the *default* image: once set,
`resolve_image()` refuses to run any resolved image whose digest doesn't
match, regardless of what `--image` or a mutable tag currently points to.

1. Build and push:

       docker build -t dirtywork/worker:0.3 docker/
       docker push dirtywork/worker:0.3

2. Resolve the pushed digest:

       docker image inspect --format '{{json .RepoDigests}}' dirtywork/worker:0.3

   This prints a JSON array like `["dirtywork/worker@sha256:<64 hex chars>"]`.

3. Set `PINNED_DIGEST` in `dirtywork/sandbox/docker_args.py` to the
   `sha256:<...>` portion only (not the whole `name@sha256:...` string).

4. Commit the `PINNED_DIGEST` change as part of the release. Until it is
   set, `resolve_image()` performs no pin check — every resolved image is
   trusted by whatever `docker image inspect` currently reports for
   `--image`'s value.

## Updating the image

Rebuild, push, verify with the commands above, then repeat the pin
procedure. `--image` lets an operator override the default per run;
`PINNED_DIGEST` only constrains the maintained default.
```

- [ ] **Step 6: Write the docker-marked build smoke test**

```python
# tests/test_docker_image.py
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKER_DIR = REPO_ROOT / "docker"
IMAGE_TAG = "dirtywork/worker:0.3-test"


@pytest.mark.docker
def test_docker_build_succeeds_and_image_has_required_tools():
    build = subprocess.run(
        ["docker", "build", "-t", IMAGE_TAG, str(DOCKER_DIR)],
        capture_output=True, text=True, timeout=600,
    )
    assert build.returncode == 0, build.stderr

    git_version = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "/usr/bin/git", IMAGE_TAG, "--version"],
        capture_output=True, text=True, timeout=30,
    )
    assert git_version.returncode == 0
    assert "git version" in git_version.stdout

    subprocess.run(["docker", "rmi", "-f", IMAGE_TAG], capture_output=True, timeout=60)
```

- [ ] **Step 7: Run the docker-marked test explicitly (requires a real daemon)**

Run: `python -m pytest -m docker -v`
Expected: PASS if Docker is running locally (image builds and `git --version` succeeds through the explicit entrypoint); SKIPPED with reason "docker daemon not available" if not.

- [ ] **Step 8: Run the default (non-docker) suite**

Run: `python -m pytest -q`
Expected: PASS, same count as Task 12 (the new test is excluded by `addopts`).

- [ ] **Step 9: Commit**

```bash
git add docker/Dockerfile docker/README.md tests/conftest.py tests/test_docker_image.py pyproject.toml
git commit -m "feat: add worker Dockerfile, docker pytest marker, and build smoke test"
```

---

### Task 14: Docs — README.md, SECURITY.md, bash tool description

Spec §8: rewrite the Security section to describe the sandboxed default truthfully, with the two required callouts placed at the top of the Security section AND in the docker-mode quick start (not buried in a residual list); document the 0.3 breaking change, the image requirement, that build outputs stay in the container, and the residual exposures. `SECURITY.md`'s scope grows to include docker-mode escapes. The `bash` tool's own description (still living in `tools.py`'s `TOOL_SCHEMAS` until SP3 moves it) gets one sentence about reset losing `/gitdir` state, since that is the one behavior change a *worker model* itself needs to know about, not just the operator.

**Files:**
- Modify: `dirtywork/tools.py` (the `bash` entry in `TOOL_SCHEMAS`)
- Modify: `README.md` (Security & trust, Requirements, Use, How a run works, Safety model, Troubleshooting, Machine contract, Development)
- Modify: `SECURITY.md`
- Test: addition to `tests/test_tools_bash.py`

**Interfaces:** None — docs and description-string changes only; no new code surface.

- [ ] **Step 1: Write the failing test for the updated bash description**

Add to `tests/test_tools_bash.py`:

```python
def test_bash_schema_mentions_reset_behavior():
    schema = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "bash")
    description = schema["function"]["description"]
    assert "reset" in description.lower()
    assert "index" in description.lower() or "git state" in description.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_tools_bash.py::test_bash_schema_mentions_reset_behavior -v`
Expected: FAIL — `StopIteration` avoided (the entry exists) but the assertion on `"reset"` fails against the current description text

- [ ] **Step 3: Update the `bash` entry in `dirtywork/tools.py`'s `TOOL_SCHEMAS`**

Replace the `bash` entry's `"description"` string:

```python
    {"type": "function", "function": {
        "name": "bash",
        "description": "Run a shell command in the worktree (cwd is the worktree "
                       "root). Use for builds/tests/git-status, NEVER for editing "
                       "files. 120s default timeout, 600s max. Backgrounded "
                       "processes are terminated when the command returns. In "
                       "docker mode, a stray background process or an "
                       "out-of-memory container triggers an automatic reset: the "
                       "working tree survives, but any git state you created "
                       "inside the sandbox (index changes, stashes, local "
                       "commits) does not — write_file/edit_file changes and "
                       "anything already written to disk are unaffected.",
        "parameters": _param({
            "command": {"type": "string"},
            "timeout": {"type": "integer", "description": "Seconds, default 120, max 600"},
        }, ["command"])}},
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_tools_bash.py -v`
Expected: PASS (all tests, including the new one)

- [ ] **Step 5: Rewrite README.md's Security & trust section**

Replace the entire `## Security & trust` section (from `## Security & trust` through the paragraph ending `...next step.` just before `## Requirements`) with:

```markdown
## Security & trust

> **Docker mode protects host integrity and host execution, not
> repository-history confidentiality.** The worker can read the *entire*
> parent object store (all branches, other worktrees' objects, unreachable
> objects). Do not run it on a clone whose history holds secrets you would
> not show the worker.

> **Windows: designed for Docker Desktop on Windows; not supported until a
> Windows integration suite passes.** Items that need real Windows testing:
> Git for Windows paths and `\\?\` handling, `docker` CLI behavior, uid
> `1000:1000`, symlink-as-file export, case-insensitivity, long paths,
> `core.symlinks=false`, `core.longpaths`.

**Docker is the default sandbox as of 0.3 — a breaking change from 0.2.**
Every tool call (`read_file`/`write_file`/`edit_file`/`list_dir`/`grep`/
`bash`) runs inside a locked-down container: `--network none` by default,
`--read-only` root filesystem, `--cap-drop ALL`, kernel-enforced memory/CPU/
process-count/per-file-size limits, and no host path mounted in except the
parent repository's read-only git object store. The worker's tree lives on
a Docker volume, never a bind mount, so host git never touches worker
content — a hostile `.gitattributes` plus a local git filter cannot execute
on the host. The tree reaches your worktree only after the run ends,
through a validated tar export (`dirtywork/sandbox/export.py`): file/dir/
symlink members only, path-escape and `.git`-named-entry checks, count and
byte caps, extraction that never calls `tarfile.extract()`/`extractall()`.
Docker missing or the daemon down is a preflight error (exit 2, with a
hint) — there is no silent fallback to unsandboxed execution.

**What Docker mode does *not* give you:**

- **Confidentiality of repository history** (see the callout above).
- **A portable disk quota.** Total disk is a best-effort bound: worktree
  size is sampled during commands and a host free-space floor is polled,
  but a burst inside one sampling interval (0.5-5 s) can exceed the limit
  before the container is killed. The exported tree is hard-capped by the
  validator regardless.
- **Git-ignored files in the exported worktree.** Build outputs
  (`node_modules`, `bin`/`obj`, `.venv`) stay inside the container's volume
  and are never exported — only the git-visible tree is. `--keep-volume`
  plus `docker run` against the volume recovers them if you need to.
  Non-Windows.

**`--sandbox none`** is the explicit opt-in to pre-0.3 host-mode behavior:
tools are path-confined to the worktree (symlink-safe realpath checks,
`.git/` write-protected), but `bash` is a general shell gated only by a
best-effort regex denylist and a `HOME` redirected into the worktree. A
determined or prompt-injected model can still read absolute host paths
(`cat /etc/...`). Use it only against models and repositories you would
trust with unconfined shell access on your machine — the same caveat 0.2
carried, unchanged.

**Residual exposures (documented, accepted, both modes where relevant):**

- Object-store confidentiality (docker mode) — see the callout above.
- Escaping symlinks (committed in the base tree, or created by the worker)
  are created on the host inside the worktree; dirtywork never follows
  them and lists them in `run_end.escaping_symlinks`. Anything *else* you
  run in that worktree afterward must not follow symlinks blindly.
- Host `git status`/`diff`/`add`/`merge` that *you* run afterward use your
  own git config; a worker-authored `.gitattributes` can trigger a
  configured filter (git-lfs and similar). Review via `runs show --diff`
  (the container-computed patch — no host git ever touches worker content
  for that path) or with `GIT_CONFIG_GLOBAL=/dev/null`.
- A malicious target repo's `CLAUDE.md`/`AGENTS.md` (read from the base
  commit via git, not the filesystem — symlinks and oversized files are
  rejected) is injected into the worker's prompt; treat untrusted repos'
  documentation as you would untrusted code.

**Practical guidance:** run dirtywork against models and repositories
you'd trust with the equivalent of a locked-down container on your
machine. Read the transcript and diff before you merge — that review is
still the real gate for *what a run produced*, even though docker mode now
also gates *what a run could do to your host while producing it*.
```

- [ ] **Step 6: Update README.md's Requirements section**

Replace the `## Requirements` bullet list (the `- macOS/Linux, Python 3.9+...` line) to add the Docker requirement:

```markdown
## Requirements

- macOS/Linux, Python 3.9+ (stdlib only — no venv, no pip deps)
- **Docker Desktop or dockerd** (default sandbox as of 0.3) — `docker
  version` must succeed. Missing/unreachable Docker is a preflight error
  with a hint; pass `--sandbox none` to skip this requirement and run
  unsandboxed on the host instead.
- [LM Studio](https://lmstudio.ai) serving its OpenAI-compatible API at
  `localhost:1234` with a tool-calling-capable model loaded. Verified
  working: `qwen/qwen3-coder-next` (65k context, default) and
  `mistralai/devstral-small-2-2512` (32k context)
- The target repo must be a git repo with at least one commit
```

- [ ] **Step 7: Update README.md's quick-start (Use) section with the second callout**

Replace the `## Use` section's opening:

```markdown
## Use

    dirtywork run --repo ~/repos/someproject "Add a unit test for X"

> **Docker mode protects host integrity and host execution, not
> repository-history confidentiality.** The worker can read the *entire*
> parent object store. Do not run it on a clone whose history holds
> secrets you would not show the worker.

- **Watch a run:** `tail -f` the transcript path printed on stderr.
- **Review a run:** `git -C <worktree> diff`, read the transcript, run the
  repo's tests — then commit the branch or discard it. (The worktree is
  only populated after the run ends, once the export step completes.)
- **Discard a run:**
  `git -C <repo> worktree remove --force <worktree> && git -C <repo> branch -D dirtywork/<slug>`
```

- [ ] **Step 8: Update README.md's "How a run works" and "Safety model" sections**

Replace `## How a run works` step 2 (worktree) to describe the docker-mode timing:

```markdown
2. **Worktree** — a fresh worktree at `<repo>/.worktrees/dw-<slug>` on new
   branch `dirtywork/<slug>`, branched from `--branch-from` (default:
   repo HEAD). In docker mode (the default) the worktree stays empty
   (only its `.git` file) for the whole run — the worker's tree lives on a
   Docker volume and reaches the worktree only via the validated export
   after the run ends. `.worktrees/` is added to the repo's local
   `.git/info/exclude` automatically. If the repo has a `CLAUDE.md` or
   `AGENTS.md` at its base commit, its content is injected into the
   worker's system prompt so it inherits your conventions.
```

Replace the `## Safety model` section's opening line ("Guardrails block **accidents, not adversaries**...") with:

```markdown
## Safety model

**Docker mode (default):** the container is the real boundary —
`--network none`, `--read-only` root filesystem, `--cap-drop ALL`,
kernel-enforced memory/CPU/process/per-file-size limits, no host path
mounted in except a read-only copy of the parent object store. The
worktree reaches the host only through the validated tar export. See
"Security & trust" above for what this does and does not cover.

**`--sandbox none` (host mode, pre-0.3 behavior):** guardrails block
**accidents, not adversaries** — the post-run review is the real gate:
```

(the bullet list that follows — path confinement, the bash denylist,
guardrail_block logging, network/timeout notes — is unchanged, since it
describes host-mode behavior exactly as before).

- [ ] **Step 9: Update README.md's Troubleshooting and Machine contract sections**

Add two entries to `## Troubleshooting` (after the existing `context_exhausted` entry):

```markdown
- **exit 2, "Docker is the default sandbox since 0.3..."** — Docker
  Desktop/dockerd isn't running or isn't reachable. Start it, or pass
  `--sandbox none` to run unsandboxed on the host.
- **status `sandbox_error`** — a docker command failed or timed out mid-run
  (daemon hang, container killed unexpectedly twice in a row, etc.); the
  worktree may be partially or not exported. Check `run_end.error` in the
  transcript and `docker ps -a --filter label=dirtywork.run=<slug>`.
- **status `export_failed` (in `run.json`'s `export_status`, and as the
  overall `status` if the agent loop itself otherwise completed)** — the
  worker's tree could not be validated/exported (e.g. it exceeded
  `--max-worktree-mb`/`--max-worktree-files`). The Docker volume is kept
  (unless it was already going to be removed); re-run export after raising
  the limit, or inspect the volume directly with `--keep-volume` on a
  fresh run.
```

Update the `**Flags:**` block in `## Machine contract` to the current flag set:

```markdown
**Flags:**

```
dirtywork run --repo <path> "<task>"
    [--model qwen/qwen3-coder-next]   # or mistralai/devstral-small-2-2512
    [--branch-from <ref>]             # default: repo HEAD
    [--max-turns 40]
    [--timeout 1800]                  # whole-run wall clock, seconds
    [--temperature <f>]               # omitted by default → server preset
    [--base-url http://localhost:1234/v1]  # LM Studio's OpenAI-compatible endpoint
    [--max-worktree-mb 2048]
    [--max-worktree-files 200000]
    [--sandbox docker|none]           # default: docker
    [--image dirtywork/worker:0.3]    # docker mode only
    [--allow-network]                 # docker mode only; default --network none
    [--memory 4g]                     # docker mode only
    [--cpus 2]                        # docker mode only
    [--tmp-size 1g]                   # docker mode only
    [--gitdir-size 512m]              # docker mode only
    [--min-free-mb 2048]              # docker mode only; host free-space floor
    [--keep-volume]                   # docker mode only; skip volume cleanup
    [--max-patch-mb 10]               # docker mode only; diff.patch cap
```
```

Update the stdout example and status list:

```markdown
**stdout:** on any run that gets past preflight, exactly one JSON object is
printed to stdout (nothing else goes to stdout):

```json
{
  "schema_version": 2,
  "status": "completed",
  "worktree": "/path/to/repo/.worktrees/dw-<slug>",
  "branch": "dirtywork/<slug>",
  "transcript": "/path/to/transcript.jsonl",
  "turns": 7,
  "usage": {"prompt_tokens": 0, "completion_tokens": 0},
  "final_message": "...",
  "run_dir": "/home/you/.dirtywork/runs/<slug>"
}
```

`status` is one of: `completed`, `max_turns`, `timeout`,
`context_exhausted`, `model_error`, `interrupted`, `budget_exceeded`,
`sandbox_error`, `export_failed`. When the run fails before a `RunResult`
exists — the LLM client raises, post-worktree setup fails (e.g. the
transcript can't be created), or any other exception escapes the run
(status `model_error` in every case) — `turns` is `null` and `usage` is
`{}`, but `status`, `worktree`, `branch`, `transcript`, and `run_dir` are
still populated so the worktree and run directory can be located for
salvage.
```

- [ ] **Step 10: Update README.md's Development section**

Replace the `## Development` section's command block:

```markdown
## Development

    python3 -m pytest                    # unit suite (no LM Studio or Docker needed)
    python3 -m pytest -m live -v         # live suite (requires LM Studio running;
                                          # includes a real end-to-end agent run)
    python3 -m pytest -m docker -v       # docker suite (requires a running Docker
                                          # daemon; host-sentinel and lifecycle tests)
```

- [ ] **Step 11: Rewrite `SECURITY.md`**

Replace the whole "Scope worth knowing" section (everything from `## Scope worth knowing` through the paragraph ending `...reachable by a *well-intentioned* model...` before `## Supported versions`) with:

```markdown
## Scope worth knowing

As of 0.3, **Docker mode is the default** and is a real containment
boundary: `--network none`, `--read-only` root filesystem,
`--cap-drop ALL`, kernel-enforced memory/CPU/process-count/per-file-size
limits, no host path mounted in except a read-only copy of the parent
repository's git object store, and a validated tar export as the only path
from the worker's tree back to the host. Escapes from docker mode — a
container breakout, a way to write outside the run's worktree or
`~/.dirtywork/runs/<slug>/`, a way to reach the network, a way for the
export validator to write through a symlink or a `.git`-named path — are
in scope and taken seriously.

**Known, accepted exposures in docker mode** (see README's Security &
trust section for the full list): the worker can read the *entire* parent
git object store (all branches, unreachable objects — not a
confidentiality boundary); total disk is a best-effort sampled bound, not
a kernel quota; escaping symlinks are created (not followed) inside the
worktree and reported; host git commands the *operator* runs afterward on
the exported tree use the operator's own config and can trigger a
worker-authored `.gitattributes`' configured filter.

**`--sandbox none`** keeps 0.2's guardrail-only behavior and its caveats
unchanged: file tools are path-confined (symlink-safe), but `bash` is a
general shell gated only by a best-effort regex denylist plus a `HOME`
redirected into the worktree — not confined. A determined or
prompt-injected model can still read absolute host paths. Do not treat
`--sandbox none` as a sandbox; it exists for operators who cannot or do
not want to run Docker.

Reports that DO qualify: any docker-mode escape as described above,
guardrail bypasses reachable by a *well-intentioned* model in
`--sandbox none` mode (accident-grade escapes), anything that lets a run
touch the parent checkout's git state beyond dirtywork's own bookkeeping,
anything that lets a run reach the network in the default (`--network
none`) configuration, and violations of the documented machine contract
that could mislead an orchestrating agent.
```

Note for the implementer: this plan does not bump `dirtywork.__version__` (`"0.2.0"` in `dirtywork/__init__.py`) — that happens at release time, alongside setting `PINNED_DIGEST` (Task 13's `docker/README.md`) and tagging. Leave it as-is here.

- [ ] **Step 12: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS, no regressions (docs-only changes beyond the one `tools.py` description string).

- [ ] **Step 13: Commit**

```bash
git add dirtywork/tools.py README.md SECURITY.md tests/test_tools_bash.py
git commit -m "docs: rewrite README Security section and SECURITY.md for the docker-default 0.3 model"
```

---

### Task 15: Live suite — host sentinels against a real Docker daemon

Spec §9's "Live" bullet, against a real temp repo and a real Docker daemon, marked `docker` (the spec's own text says `-m live`; the SP2 brief deliberately refines this — `live` already means "needs LM Studio" in this codebase, so real-Docker tests get the `docker` marker instead, auto-skipped by Task 13's `conftest.py`). Every case drives the real CLI (`dirtywork.__main__.main`) with a scripted `ScriptedClient` standing in for the LLM, so no LM Studio server is needed either — only Docker.

**Files:**
- Create: `tests/test_docker_live.py` (every test marked `@pytest.mark.docker`)

**Interfaces:** None new — this file only *consumes* the public CLI (`dirtywork.__main__.main`) and, for one case, `dirtywork.sandbox.export.export_run` directly.

- [ ] **Step 1: Write the shared fixtures and helpers**

```python
# tests/test_docker_live.py
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from dirtywork.__main__ import DEFAULT_MODEL


def _resp(content=None, tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def _call(call_id, name, args: dict):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


class ScriptedClient:
    """Stands in for LMStudioClient so these tests need a real Docker
    daemon but NOT a real LM Studio server."""

    def __init__(self, responses):
        self.responses = list(responses)

    def list_models(self):
        return [DEFAULT_MODEL]

    def chat(self, model, messages, tools, temperature=None, max_tokens=4096, timeout=None):
        return self.responses.pop(0)


def _make_live_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README.md").write_text("# demo\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)
    return repo


def _config_bytes(repo: Path) -> bytes:
    return (repo / ".git" / "config").read_bytes()


def _refs_listing(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "for-each-ref"],
                           capture_output=True, text=True, check=True).stdout


def _object_hashes(repo: Path) -> dict:
    objects_dir = repo / ".git" / "objects"
    hashes = {}
    for path in objects_dir.rglob("*"):
        if path.is_file():
            hashes[str(path.relative_to(objects_dir))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _run_docker_main(monkeypatch, tmp_path, repo, responses, **extra_args):
    import dirtywork.__main__ as m
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    client = ScriptedClient(responses)
    monkeypatch.setattr(m, "LMStudioClient", lambda base_url=None: client)
    argv = ["run", "--repo", str(repo), "--sandbox", "docker"]
    for k, v in extra_args.items():
        flag = "--" + k.replace("_", "-")
        if v is True:
            argv.append(flag)
        elif v is not False:
            argv += [flag, str(v)]
    argv.append("do the task")
    return m.main(argv)
```

- [ ] **Step 2: Write the host-sentinel + isolation test**

```python
@pytest.mark.docker
def test_docker_live_full_run_host_sentinels_and_isolation(tmp_path, monkeypatch, capsys):
    repo = _make_live_repo(tmp_path)
    sentinel_path = tmp_path / "filter_sentinel.txt"
    subprocess.run(["git", "-C", str(repo), "config", "--local", "filter.evil.clean",
                     f"touch {sentinel_path}"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "--local", "filter.evil.smudge",
                     f"touch {sentinel_path}"], check=True)
    outside_sentinel = tmp_path / "outside_sentinel.txt"
    outside_sentinel.write_text("do not touch\n")

    config_before = _config_bytes(repo)
    refs_before = _refs_listing(repo)
    objects_before = _object_hashes(repo)

    responses = [
        _resp(tool_calls=[_call("c1", "write_file", {"path": "hello.txt", "content": "from worker\n"})]),
        _resp(tool_calls=[_call("c2", "write_file", {"path": ".gitattributes", "content": "* filter=evil\n"})]),
        _resp(tool_calls=[_call("c3", "bash",
              {"command": "python3 -c \"open('/etc/dirtywork_sentinel','w')\""})]),
        _resp(tool_calls=[_call("c4", "bash", {"command": "git config core.hooksPath x && cat /gitdir/config"})]),
        _resp(tool_calls=[_call("c5", "bash", {"command": "curl -s -m 5 http://example.com/ ; echo curl_exit=$?"})]),
        _resp(tool_calls=[_call("c6", "bash", {"command": "git status"})]),
        _resp(content="done"),
    ]

    _run_docker_main(monkeypatch, tmp_path, repo, responses)
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "completed"
    worktree = Path(payload["worktree"])
    assert (worktree / "hello.txt").read_text() == "from worker\n"

    assert _config_bytes(repo) == config_before
    assert _refs_listing(repo) == refs_before
    assert _object_hashes(repo) == objects_before
    assert outside_sentinel.read_text() == "do not touch\n"
    assert not sentinel_path.exists()  # the operator's local filter never fired on the host

    events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]
    bash_results = [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    assert len(bash_results) == 4
    assert "permission denied" in bash_results[0].lower() or "read-only" in bash_results[0].lower()
    assert "hooksPath" in bash_results[1]  # git config wrote only /gitdir/config, visible via cat
    assert "curl_exit=0" not in bash_results[2]  # curl must fail: --network none
    assert "exit code: 0" in bash_results[3]  # git status works with the GIT_DIR mapping
```

- [ ] **Step 3: Write the timeout-continuation and background-reap tests**

```python
@pytest.mark.docker
def test_docker_live_timeout_kills_command_and_run_continues(tmp_path, monkeypatch, capsys):
    repo = _make_live_repo(tmp_path)
    responses = [
        _resp(tool_calls=[_call("c1", "bash", {"command": "sleep 600", "timeout": 2})]),
        _resp(tool_calls=[_call("c2", "bash", {"command": "echo still-alive"})]),
        _resp(content="done"),
    ]
    _run_docker_main(monkeypatch, tmp_path, repo, responses)
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "completed"
    events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]
    bash_results = [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    assert "timed out" in bash_results[0].lower()
    assert "still-alive" in bash_results[1]


@pytest.mark.docker
def test_docker_live_backgrounded_process_is_dead_after_reap(tmp_path, monkeypatch, capsys):
    repo = _make_live_repo(tmp_path)
    responses = [
        _resp(tool_calls=[_call("c1", "bash",
              {"command": "nohup sh -c 'sleep 3; touch /tmp/dw_bg_marker' >/dev/null 2>&1 & echo started"})]),
        _resp(tool_calls=[_call("c2", "bash",
              {"command": "sleep 4; test -f /tmp/dw_bg_marker && echo FOUND || echo GONE"})]),
        _resp(content="done"),
    ]
    _run_docker_main(monkeypatch, tmp_path, repo, responses)
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "completed"
    events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]
    bash_results = [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    assert "GONE" in bash_results[1]
```

- [ ] **Step 4: Write the process-flood-triggers-reset test**

`--pids-limit 512` (kernel-enforced, threat model (d)) bounds this deliberately, so a real fork bomb is unnecessary and riskier than a bounded flood.

```python
@pytest.mark.docker
def test_docker_live_process_flood_triggers_reset(tmp_path, monkeypatch, capsys):
    repo = _make_live_repo(tmp_path)
    responses = [
        _resp(tool_calls=[_call("c1", "bash", {
            "command": "for i in $(seq 1 600); do sleep 30 & done; echo spawned",
            "timeout": 30,
        })]),
        _resp(tool_calls=[_call("c2", "bash", {"command": "echo alive-after-reset"})]),
        _resp(content="done"),
    ]
    _run_docker_main(monkeypatch, tmp_path, repo, responses)
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "completed"
    events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]
    reset_events = [e for e in events if e["event"] == "sandbox_reset"]
    assert reset_events
    bash_results = [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    assert "alive-after-reset" in bash_results[1]
```

- [ ] **Step 5: Write the export-reporting and over-budget tests**

```python
@pytest.mark.docker
def test_docker_live_export_reports_nested_git_and_escaping_symlink_and_skips_ignored(
        tmp_path, monkeypatch, capsys):
    repo = _make_live_repo(tmp_path)
    responses = [
        _resp(tool_calls=[_call("c1", "bash", {
            "command": "mkdir -p payload && echo fake > payload/.git && "
                       "ln -s /etc/passwd esc && echo ignored.bin > .gitignore && "
                       "echo secret > ignored.bin",
        })]),
        _resp(content="done"),
    ]
    _run_docker_main(monkeypatch, tmp_path, repo, responses)
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "completed"
    worktree = Path(payload["worktree"])
    assert (worktree / "esc").is_symlink()
    assert not (worktree / "ignored.bin").exists()
    assert not (worktree / "payload" / ".git").exists()

    events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]
    run_end = next(e for e in events if e["event"] == "run_end")
    assert "payload/.git" in run_end.get("dropped_git_entries", [])
    assert "esc" in run_end.get("escaping_symlinks", [])


@pytest.mark.docker
def test_docker_live_over_budget_write_ends_run_with_budget_exceeded(tmp_path, monkeypatch, capsys):
    repo = _make_live_repo(tmp_path)
    responses = [
        _resp(tool_calls=[_call("c1", "bash",
              {"command": "dd if=/dev/zero of=big.bin bs=1M count=5 2>/dev/null; echo done"})]),
    ]
    rc = _run_docker_main(monkeypatch, tmp_path, repo, responses, max_worktree_mb=1)
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "budget_exceeded"
    assert rc == 1
```

- [ ] **Step 6: Write the export-refused-into-non-empty-worktree test**

This one drives `export_run` directly (not `main()`) against a real container, since the pre-check it exercises is independent of the agent loop.

```python
@pytest.mark.docker
def test_docker_live_export_refused_into_nonempty_worktree(tmp_path):
    from dirtywork.sandbox import docker_args
    from dirtywork.sandbox.docker_cli import resolve_image, validate_objects_dir
    from dirtywork.sandbox.export import export_run
    from dirtywork.workspace import create_worktree, ensure_worktrees_excluded, worktree_base_commit

    repo = _make_live_repo(tmp_path)
    ensure_worktrees_excluded(repo)
    worktree = create_worktree(repo, "livexp", None, no_checkout=True)
    base_commit = worktree_base_commit(worktree)
    (worktree / "stray.txt").write_text("should not be here")  # makes the worktree non-empty

    objects_dir = validate_objects_dir(repo)
    image_ref = resolve_image(docker_args.DEFAULT_IMAGE)
    cfg = docker_args.DockerConfig()
    label = docker_args.repo_label(repo)
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()

    artifacts = export_run(
        cfg, slug="livexp", base_commit=base_commit, worktree=worktree, run_dir=run_dir,
        objects_dir=objects_dir, image_ref=image_ref, uid=os.getuid(), gid=os.getgid(),
        repo_label=label,
    )

    assert artifacts.export_status == "export_failed: worktree not empty"
```

- [ ] **Step 7: Run the live suite (requires Docker)**

Run: `python -m pytest -m docker tests/test_docker_live.py -v`
Expected: PASS if Docker is running (each case builds/reuses the image and runs a real short-lived container); SKIPPED (all tests) if not.

- [ ] **Step 8: Run the default suite**

Run: `python -m pytest -q`
Expected: PASS, same count as Task 14 (these tests are excluded by `addopts`).

- [ ] **Step 9: Commit**

```bash
git add tests/test_docker_live.py
git commit -m "test: add live docker-daemon suite with host sentinels"
```

---

### Task 16: Lifecycle suite — the release gate

Spec §9's "Lifecycle suite (release gate — docker mode is not the default until it passes)". Every case here spawns `dirtywork` as a real **subprocess** (`python -m dirtywork run ...`), not an in-process `main()` call, because the point is to SIGKILL or otherwise interrupt the actual host process and observe what a real Docker daemon does to the container/volume it created — something an in-process call can never demonstrate. A tiny scripted `http.server` thread stands in for LM Studio so no real LLM is needed, only Docker. `dirtywork runs export <slug>` (SP3's `dirtywork/runs.py`) does not exist yet in this plan — where the spec's release-gate description calls for it, this task calls `export_run` directly against the surviving volume instead, proving the same recoverability property SP3's command will later wrap.

**Files:**
- Create: `tests/test_docker_lifecycle.py` (every test marked `@pytest.mark.docker`)

**Interfaces:** None new — this file drives the real `python -m dirtywork` entry point as a subprocess and, for recovery assertions, `dirtywork.sandbox.export.export_run` directly.

- [ ] **Step 1: Write the shared fixtures and helpers**

```python
# tests/test_docker_lifecycle.py
from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from dirtywork.__main__ import DEFAULT_MODEL


def _resp(content=None, tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def _call(call_id, name, args: dict):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


class _ScriptedHandler(http.server.BaseHTTPRequestHandler):
    """Serves /v1/models and /v1/chat/completions with pre-scripted JSON
    responses, popped in order for each /chat/completions POST."""

    def log_message(self, *a):
        pass  # keep test output quiet

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            body = json.dumps({"data": [{"id": self.server.model}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)  # request body is ignored — responses are pre-scripted
        if self.server.responses:
            resp = self.server.responses.pop(0)
        else:
            resp = {"choices": [{"message": {"role": "assistant", "content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        body = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_fake_llm_server(model: str, responses: list):
    server = http.server.HTTPServer(("127.0.0.1", 0), _ScriptedHandler)
    server.model = model
    server.responses = list(responses)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README.md").write_text("# demo\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)
    return repo


def _spawn_env(tmp_home: Path) -> dict:
    """A subprocess-local $HOME so ~/.dirtywork/runs (and thus the slug the
    subprocess picks) is isolated per test — no CLI flag exists for the
    runs directory, but dirtywork.rundir.RUNS_DIR is Path.home()-derived,
    which $HOME fully controls."""
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "TERM", "LANG")}
    env["HOME"] = str(tmp_home)
    return env


def _dirtywork_argv(repo: Path, base_url: str, task: str = "do the task", extra=None) -> list:
    argv = [sys.executable, "-m", "dirtywork", "run", "--repo", str(repo),
            "--sandbox", "docker", "--base-url", base_url, "--max-turns", "5"]
    if extra:
        argv += extra
    argv.append(task)
    return argv


def _wait_for_slug(tmp_home: Path, timeout: float) -> str:
    runs_root = tmp_home / ".dirtywork" / "runs"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if runs_root.is_dir():
            entries = [p for p in runs_root.iterdir() if p.is_dir()]
            if entries:
                return entries[0].name
        time.sleep(0.2)
    raise TimeoutError("dirtywork subprocess never created a run directory")


def _docker_ps_a(label_filter: str) -> str:
    return subprocess.run(
        ["docker", "ps", "-a", "--filter", label_filter, "--format", "{{.Names}}"],
        capture_output=True, text=True,
    ).stdout


def _docker_volume_ls(label_filter: str) -> str:
    return subprocess.run(
        ["docker", "volume", "ls", "--filter", label_filter, "--format", "{{.Name}}"],
        capture_output=True, text=True,
    ).stdout


def _wait_for_container(slug: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if f"dw-{slug}" in _docker_ps_a(f"label=dirtywork.run={slug}"):
            return
        time.sleep(0.2)
    raise TimeoutError(f"container dw-{slug} never appeared")


def _no_leaked_docker_children(slug: str) -> bool:
    result = subprocess.run(["pgrep", "-f", f"docker start -ai dw-{slug}"], capture_output=True)
    return result.returncode != 0  # pgrep: 0 = found a match, 1 = none found


def _cleanup_labelled(slug: str) -> None:
    subprocess.run(["docker", "rm", "-f", f"dw-{slug}"], capture_output=True)
    subprocess.run(["docker", "rm", "-f", f"dw-{slug}-export"], capture_output=True)
    subprocess.run(["docker", "volume", "rm", f"dw-{slug}-work"], capture_output=True)
```

- [ ] **Step 2: Write the SIGKILL-recovers-via-volume test**

```python
@pytest.mark.docker
def test_docker_lifecycle_sigkill_leaves_container_gone_volume_recoverable(tmp_path):
    repo = _make_repo(tmp_path)
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    responses = [_resp(tool_calls=[_call("c1", "bash", {"command": "sleep 30", "timeout": 60})])]
    server, thread = _start_fake_llm_server(DEFAULT_MODEL, responses)
    base_url = f"http://127.0.0.1:{server.server_port}/v1"
    slug = None
    try:
        proc = subprocess.Popen(_dirtywork_argv(repo, base_url), env=_spawn_env(tmp_home),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            slug = _wait_for_slug(tmp_home, timeout=60)
            _wait_for_container(slug, timeout=60)

            proc.kill()  # SIGKILL the whole dirtywork process
            proc.wait(timeout=15)
            time.sleep(2)  # let the daemon notice the tether's stdin pipe closed

            # The tether Popen died with its parent, so `docker start -ai`'s
            # attach exits and the container stops (verified in the spec's
            # decision record) — it is gone or Exited, never left running.
            status = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Running}}", f"dw-{slug}"],
                capture_output=True, text=True,
            )
            assert status.returncode != 0 or status.stdout.strip() == "false"

            # The volume, with no attached process of its own, survives.
            assert f"dw-{slug}-work" in _docker_volume_ls(f"label=dirtywork.run={slug}")
            assert _no_leaked_docker_children(slug)

            # Recovery: export_run against the surviving volume — this plan
            # predates SP3's `dirtywork runs export <slug>` CLI command, which
            # will wrap exactly this call once it exists.
            from dirtywork.sandbox import docker_args
            from dirtywork.sandbox.docker_cli import resolve_image, validate_objects_dir
            from dirtywork.sandbox.export import export_run
            from dirtywork.workspace import worktree_base_commit

            worktree = repo / ".worktrees" / f"dw-{slug}"
            objects_dir = validate_objects_dir(repo)
            image_ref = resolve_image(docker_args.DEFAULT_IMAGE)
            cfg = docker_args.DockerConfig()
            label = docker_args.repo_label(repo)
            base_commit = worktree_base_commit(worktree)
            run_dir = tmp_home / ".dirtywork" / "runs" / slug
            artifacts = export_run(
                cfg, slug=slug, base_commit=base_commit, worktree=worktree, run_dir=run_dir,
                objects_dir=objects_dir, image_ref=image_ref, uid=os.getuid(), gid=os.getgid(),
                repo_label=label,
            )
            assert artifacts.export_status == "ok"
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=15)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        if slug:
            _cleanup_labelled(slug)
        subprocess.run(["git", "-C", str(repo), "worktree", "prune"], capture_output=True)
```

- [ ] **Step 3: Write the daemon-hang-fails-closed test**

```python
@pytest.mark.docker
def test_docker_lifecycle_daemon_hang_fails_closed_within_timeout(tmp_path):
    repo = _make_repo(tmp_path)
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    stub = fake_bin / "docker"
    stub.write_text("#!/bin/sh\nsleep 1000\n")
    stub.chmod(0o755)

    server, thread = _start_fake_llm_server(DEFAULT_MODEL, [_resp(content="unreachable")])
    base_url = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        env = _spawn_env(tmp_home)
        env["PATH"] = f"{fake_bin}:{env['PATH']}"  # the hanging stub shadows the real docker

        start = time.monotonic()
        result = subprocess.run(_dirtywork_argv(repo, base_url), env=env,
                                 capture_output=True, text=True, timeout=60)
        elapsed = time.monotonic() - start

        # docker_version()'s own T_QUERY=10s timeout must fire well before
        # this test's own 60s ceiling — "fails closed instead of hanging".
        assert elapsed < 30
        assert result.returncode == 2
        assert "Docker" in result.stderr
    finally:
        server.shutdown()
        thread.join(timeout=5)
```

- [ ] **Step 4: Write the container-killed-during-exec-run-continues test**

```python
@pytest.mark.docker
def test_docker_lifecycle_container_killed_during_exec_run_continues_after_reset(tmp_path):
    repo = _make_repo(tmp_path)
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    responses = [
        _resp(tool_calls=[_call("c1", "bash", {"command": "sleep 20", "timeout": 60})]),
        _resp(tool_calls=[_call("c2", "bash", {"command": "echo recovered"})]),
        _resp(content="done"),
    ]
    server, thread = _start_fake_llm_server(DEFAULT_MODEL, responses)
    base_url = f"http://127.0.0.1:{server.server_port}/v1"
    slug = None
    try:
        proc = subprocess.Popen(_dirtywork_argv(repo, base_url), env=_spawn_env(tmp_home),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        slug = _wait_for_slug(tmp_home, timeout=60)
        _wait_for_container(slug, timeout=60)
        time.sleep(2)  # let the "sleep 20" bash exec actually start inside the container
        subprocess.run(["docker", "kill", f"dw-{slug}"], capture_output=True)

        out, _err = proc.communicate(timeout=90)
        payload = json.loads(out.decode())

        assert payload["status"] == "completed"
        events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]
        reset_events = [e for e in events if e["event"] == "sandbox_reset"]
        assert reset_events  # reap detected the killed container and reset it
        bash_results = [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
        assert "recovered" in bash_results[-1]

        assert f"dw-{slug}-work" in _docker_volume_ls(f"label=dirtywork.run={slug}")
        assert _no_leaked_docker_children(slug)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        if slug:
            _cleanup_labelled(slug)
        subprocess.run(["git", "-C", str(repo), "worktree", "prune"], capture_output=True)
```

- [ ] **Step 5: Run the lifecycle suite (requires Docker; slower than the other suites)**

Run: `python -m pytest -m docker tests/test_docker_lifecycle.py -v`
Expected: PASS if Docker is running (each case spawns a real `dirtywork` subprocess and interacts with real containers); SKIPPED (all tests) if not. This suite is the release gate mentioned in the spec: docker mode does not become the *documented* default until it is green on a real daemon (it already IS the CLI's default as of Task 12 — this suite is what proves that default is trustworthy).

- [ ] **Step 6: Run the default suite**

Run: `python -m pytest -q`
Expected: PASS, same count as Task 15 (these tests are excluded by `addopts`).

- [ ] **Step 7: Commit**

```bash
git add tests/test_docker_lifecycle.py
git commit -m "test: add docker lifecycle release-gate suite (sigkill, daemon hang, mid-exec kill)"
```

---

## Self-review: spec coverage

### Decision record → tasks

| Decision record bullet | Tasks |
|---|---|
| Docker first (not Seatbelt): a real OS boundary with cgroup quotas | 3, 4, 6 |
| Docker is the default in 0.3; `--sandbox none` explicit opt-in; missing/down → preflight error exit 2 with hint; no silent fallback; documented breaking change | 12, 14 |
| Worktree lives on a Docker volume, not a bind mount; host worktree stays empty (only `.git`) until export; git-ignored files not exported | 5, 6, 11, 14 |
| Image via `--image` with a maintained default; digest pinned in code, resolved/pulled at preflight, recorded in `run.json`/`run_start` | 3, 4, 12, 13 |
| All six tools run inside the container via `docker exec` | 7 |
| Every export step runs in a fresh container, never the worker's own | 11 |
| Runtime stays Python-stdlib-only | Global Constraints (all tasks); no new dependency introduced anywhere in Tasks 1-16 |

### §1-§9 → tasks

| Spec section | Tasks |
|---|---|
| §1 Sandbox interface (`Sandbox` Protocol, `HostSandbox`, `DockerSandbox`, `RunArtifacts`) | 2 (Protocol/`RunArtifacts`/`HostSandbox`), 6-9, 11 (`DockerSandbox`) |
| §2 step 1 — preflight incl. object-store validation | 3 (`validate_objects_dir`), 12 (`_docker_preflight`) |
| §2 step 2 — `ensure_worktrees_excluded` | 12 (calls the SP1 function) |
| §2 step 3 — `--no-checkout` worktree, base_commit | 5, 12 |
| §2 step 4 — run dir + `run.json` written now | 5 (`write_run_json`), 12 |
| §2 step 5 — volume create + prep container | 6 |
| §2 step 6 — start worker container, wait ready | 6 |
| §2 step 7 — in-container init | 6 |
| §2 step 8 — `load_repo_context(repo, base_commit)` on host | 12 (wiring; function itself is SP1) |
| §2 step 9 — agent loop, tool dispatch, watchdog for container lifetime | 7, 9, 12 |
| §2 step 10 — stop worker container | 11 (`finalize`'s `_stop_container`), 12 (`finally: sandbox.stop()`) |
| §2 step 11 — export, `host_read_tree`, volume removal, `run.json` update | 5 (`host_read_tree`), 10, 11, 12 |
| §2 step 12 — `run_start` sandbox dict, `schema_version: 2` | 2 (`schema_version`), 12 (sandbox dict) |
| §3 worker container creation/mounts/limits/lifetime, entrypoint/PATH always explicit, never `-w`, tether, reset, user, tmpfs/memory, `--mount` only, name collision, nothing but `objects/` mounted, docker control plane, CLI flags | 3 (control plane/timeouts), 4 (argv/labels/names), 6 (create/tether/wait/collision), 8 (reset), 12 (CLI flags) |
| §4 in-container git init (first + restart variants) | 6 (`_init`), 8 (restart variant via `reset`), 11 (export's own restart-variant init) |
| §5 tool execution inside the container | 7 |
| §6 reaping and budgets | 8 (reap/OOM/reset), 9 (watchdog disk floor + worktree sampling, sample-failure escalation) |
| §7 export flow + validator | 10 (`extract_validated`), 11 (`export_run`) |
| §8 docs | 14 |
| §9 testing (unit / live / lifecycle) | Unit: 1, 3, 4, 6, 7, 8, 9, 10, 11 (every `DockerSandbox`/export/validator/argv/watchdog behavior over an injected `run`/`popen`); Live: 15; Lifecycle: 16 |

### Threat model (a)-(e) → tasks

| Threat model item | Tasks |
|---|---|
| (a) no host writes outside the worktree/`~/.dirtywork/runs/<slug>/`, worktree only through the export validator | 5 (`--no-checkout` leaves only `.git` until export), 6 (only `objects/` ever mounted), 10 (validator: name rules, symlink-escape check, byte/count caps, `set_attrs=False`-equivalent manual extraction), 11 (export flow; failure path cleans back to `.git` only) |
| (b) no modification of the parent repo's shared git state beyond dirtywork's own bookkeeping | 5 (`host_read_tree` is index-only, config-neutral env), 6 (only `objects/` bind-mounted, read-only; nothing else from `<repo>/.git`), 11 (export container's own `/gitdir` is fresh and discarded) |
| (c) no host code execution from worker-produced bytes | 10 (validator never calls `tarfile.extract()`/`extractall()`; files are written via `os.open(O_EXCL\|O_NOFOLLOW)`, not tarfile's own path-joining), 5 (`host_read_tree` touches only the index, never runs worker-authored hooks/filters — proven live in Task 15's filter-sentinel test) |
| (d) best-effort resource bounds; kernel-enforced memory/CPU/process-count/per-file-size; sampled disk bound stated honestly | 4 (`--memory`/`--memory-swap`/`--cpus`/`--pids-limit` in every create argv), 7 (`ulimit -f` in the `bash` exec), 8/9 (reap + watchdog for the sampled disk bound), 10/11 (hard cap on the exported tree) |
| (e) network reachability (`--network none` default) | 4 (`worker_create_argv` uses `cfg.network`, default `"none"`; `export_create_argv` is hard-coded `"none"` regardless of `cfg.network`), 12 (`--allow-network` flag), 15 (live `curl` failure case proves it) |

No decision-record bullet, §1-§9 item, or threat-model item was left unmapped.

## Type consistency checklist

Every name below matches the shared brief's "SP2 introduces" contract exactly, with three additive, keyword-only extensions (never changing a documented positional signature), each justified inline where introduced:

- `dirtywork/procs.py`: `MAX_CAPTURE_BYTES`, `Captured(returncode, output, truncated, timed_out)`, `run_capped(argv, *, timeout, cwd=None, env=None, stdin=None, cap=MAX_CAPTURE_BYTES, kill_group=True)` — exact match (Task 1).
- `dirtywork/sandbox/__init__.py`: `SandboxError`, `RunArtifacts` (all seven fields, exact defaults), `Sandbox` Protocol (all nine methods, exact signatures) — exact match (Task 2).
- `dirtywork/sandbox/host.py`: `HostSandbox` — exact match (Task 2).
- `dirtywork/sandbox/docker_cli.py`: `DockerError`, `run(argv, *, timeout, stdin=None)`, `T_QUERY=10`, `T_LIFECYCLE=60`, `T_PULL=600`, `T_EXPORT_STEP=300`, `docker_storage_paths`, `validate_objects_dir` — exact match; `resolve_image(image, run=run)` gains an additive keyword-only `pinned_digest: str | None = None` (Task 3's note: `docker_args.PINNED_DIGEST` doesn't exist until Task 4, and the caller in Task 6 passes it explicitly, so the documented two-argument call form is unchanged).
- `dirtywork/sandbox/docker_args.py`: `DockerConfig` (all twelve fields, exact defaults), `DEFAULT_IMAGE`, `PINNED_DIGEST`, `PATH_ENV`, `container_name`, `volume_name`, `repo_label`, `exec_argv(name, argv, *, workdir="/work", stdin=False, env=None)`, `prep_run_argv(cfg, slug, image_ref, uid, gid)` — exact match; `worker_create_argv`/`export_create_argv` gain an additive keyword-only `repo_label: str` (Task 4's note: the label value has no source path in the documented positional signature, since none of `cfg, slug, image_ref, uid, gid, objects_dir` is a repo path).
- `dirtywork/sandbox/watchdog.py`: `Watchdog(kill, sample, storage_paths, *, min_free_mb, max_worktree_mb, max_worktree_files, clock=time.monotonic, sleep=time.sleep)`, `.start()/.stop()`, `.note_bash_start()/.note_bash_end()`, `.violation` — exact match; gains the additive public method `check_worktree_budget_once()` (Task 9's note: the shared synchronous-sample-and-check logic the thread's own loop and the sandbox's post-bash call both need).
- `dirtywork/sandbox/export.py`: `ExportError`, `ExportReport(files, bytes, escaping_symlinks)`, `extract_validated(stream, dest, *, max_files, max_bytes)` — exact match (Task 10); `export_run(cfg, *, slug, base_commit, worktree, run_dir, objects_dir, image_ref, uid, gid, run=docker_cli.run, popen=subprocess.Popen)` gains the same additive keyword-only `repo_label: str` as the Task 4 builders, for the same reason (Task 11).
- `dirtywork/sandbox/docker.py`: `DockerSandbox(cfg, *, run_dir, transcript=None, run=docker_cli.run, popen=subprocess.Popen)`; post-`start()` attributes `.container`, `.volume`, `.image_ref`, `.uid`, `.gid`; `.reset(reason)`; `.stop()` idempotent — exact match (Tasks 6-9, 11).
- `dirtywork/tools.py`: `ToolExecutor.__init__(self, sandbox, transcript=None)`, `execute(name, args) -> str` — exact match (Task 2).
- `dirtywork/runner.py`: catches `SandboxError` → `finish("sandbox_error", str(e))`; `run_start` includes `schema_version: 2` — exact match (Task 2).
- Statuses added: `sandbox_error` (Task 2, surfaced by `Runner`), `export_failed` (Task 12, computed from `RunResult.extra["export_status"]` since it depends on the `finalize()` result rather than the agent loop's own outcome — `budget_exceeded` is SP1's, reused unchanged here via `HostSandbox`/`DockerSandbox` both raising `dirtywork.budget.BudgetExceeded`).
- CLI flags: `--sandbox docker|none`, `--image`, `--allow-network`, `--memory`, `--cpus`, `--tmp-size`, `--gitdir-size`, `--min-free-mb`, `--keep-volume`, `--max-patch-mb` — exact match (Task 12).
- `run.json` fields at start (`schema_version, status, slug, repo, worktree, branch, base_commit, container, volume, image, image_digest, host_pid, started, sandbox`) and at end (`status, ended, diff_stat, export_status, patch_path`) — exact match (Task 12).

No name from the shared brief's "SP2 introduces" list was renamed, dropped, or given an incompatible signature; every extension is additive and keyword-only, documented at the task that introduces it.
















