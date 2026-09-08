# Issue #151: one shared rg/grep argv builder — implementation plan

**Goal:** the host `grep` and `DockerSandbox.grep` must build their search argv in one place, so the flag set and the `--` operand terminator cannot drift between backends again.

**Spec:** [Issue #151](https://github.com/JimboSchneider/dirtywork/issues/151), the DRY item from the PR #145 review. PR #145 put `["--", path]` in the Docker copy only; the host copy appended the path bare, safe only because `resolve_in_worktree` returns an absolute path.

**Architecture:** `tools.grep_argv(executable, pattern, path, glob, *, rg)` returns the full argv, always ending `["--", path]`. The caller resolves the binary (host: `shutil.which("rg")` or `grep`; Docker: `/usr/bin/rg` or `/usr/bin/grep` after the `_has_rg` probe) and says which flag set applies. The Docker argv is byte-for-byte what it was, so `tests/test_docker_sandbox.py` does not change; the host argv gains `--`. Issue #139 (static tools through policy) gets one place to see the search argv.

**Blast radius (measured on a scratch clone at the PR #155 head):** two edits in `tools.py`, two in `docker.py` (import plus the grep body), six new tests appended to `tests/test_tools_files.py` (four builder parametrizations, one host test that fakes `shutil.which` and `subprocess.run` and asserts the argv ends `--` plus the resolved absolute path). Red on baseline: 5 failed; green: 277 passed across `test_tools_files.py` and `test_docker_sandbox.py`.

**Tech stack:** Python >=3.9, pytest; no dependencies added.

## Global constraints

- Repository `CLAUDE.md`: latest released dirtywork plus a local worker implements code. PyPI checked 2026-09-08: `0.13.1`. Worker: `qwen/qwen3-coder-next` via LM Studio. Image: `dirtywork-worker-pytest:0.13`, network disabled.
- Worker changes only the three files named in the brief; the orchestrator writes this plan and the ledger after the run.
- Brief shape: fix-first, exact `edit_file` pairs with line numbers generated from the dry-run script, tests appended verbatim, never `write_file`.
- Runs from main only after PR #155 merges (its line numbers are computed against that head).
- No merge or release without the owner's per-action authorization.

## Task W1: shared argv builder

- [x] Dry-run the exact edits on a scratch clone at the PR #155 head (red on baseline, green with the fix).
- [x] After PR #155 merges, launch the brief below verbatim from main through released dirtywork with the metrics sampler active.
- [x] Review the exported diff against the dry-run patch; run the full suite on the host against the worktree.
- [x] Write the ledger row; open a PR that closes issue #151.

### Worker brief

```text
Issue #151 (DRY): host grep (dirtywork/tools.py) and DockerSandbox.grep (dirtywork/sandbox/docker.py) build the same rg/grep argv independently, and PR #145's `--` before the path exists only in the Docker copy. Add ONE shared builder `grep_argv(executable, pattern, path, glob, *, rg)` in dirtywork/tools.py that always ends the argv with `["--", path]`, and make both backends call it. Emitted argv is unchanged for Docker and gains `--` on the host.
Touch ONLY dirtywork/tools.py, dirtywork/sandbox/docker.py and tests/test_tools_files.py. NEVER write_file any file. Use edit_file with the exact old/new strings below (keep every leading space; each old string occurs exactly once; line numbers are given so you do not need to search) and ONE append_file for the tests. No docs, no commits, nothing else. tests/test_docker_sandbox.py must not change: its grep argv expectations already pass.

P1 edit_file on dirtywork/tools.py (add grep_argv: old is lines 965-967, the whole grep_timeout_result function; new keeps it and adds the builder after it). P1 old:
def grep_timeout_result(timeout: int) -> str:
    """The canonical result for a grep call that hit its timeout."""
    return GREP_TIMEOUT_TEXT.format(timeout=timeout)
P1 new:
def grep_timeout_result(timeout: int) -> str:
    """The canonical result for a grep call that hit its timeout."""
    return GREP_TIMEOUT_TEXT.format(timeout=timeout)


def grep_argv(executable: str, pattern: str, path: str, glob: str | None, *, rg: bool) -> list:
    """The ONE search argv both backends run (issue #151). `executable` is the
    binary the caller resolved (host: shutil.which; docker: the image's
    /usr/bin path) and `rg` says which flag set it takes. Pattern and glob
    travel as option values; the path is always the last operand after `--`,
    so an option-like or bare `-` path (PR #145, issue #146) is never read
    as a flag on either backend."""
    if rg:
        cmd = [executable, "-n", "--no-heading", "-M", "300", "-e", pattern]
        if glob:
            cmd += ["-g", glob]
    else:
        cmd = [executable, "-rn", "-e", pattern]
        if glob:
            cmd += [f"--include={glob}"]
    return cmd + ["--", path]

P2 edit_file on dirtywork/tools.py (lines 978-988, inside def grep). P2 old:
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
P2 new:
    rg = shutil.which("rg")
    cmd = grep_argv(rg or "grep", pattern, str(p), glob, rg=bool(rg))

P3 edit_file on dirtywork/sandbox/docker.py (line 35, inside the `from ..tools import (` block; keep alphabetical order). P3 old:
    grep_timeout_result,
P3 new:
    grep_argv,
    grep_timeout_result,

P4 edit_file on dirtywork/sandbox/docker.py (lines 843-853, inside DockerSandbox.grep). P4 old:
        # Probe for rg once per sandbox instance
        if self._probe("_has_rg", ["/usr/bin/rg", "--version"]):
            cmd = ["/usr/bin/rg", "-n", "--no-heading", "-M", "300", "-e", pattern]
            if glob:
                cmd += ["-g", glob]
        else:
            cmd = ["/usr/bin/grep", "-rn", "-e", pattern]
            if glob:
                cmd += [f"--include={glob}"]
        # Keep option-like paths (for example --pre=./helper) as operands.
        cmd.extend(["--", rel])
P4 new:
        # Probe for rg once per sandbox instance
        has_rg = self._probe("_has_rg", ["/usr/bin/rg", "--version"])
        cmd = grep_argv("/usr/bin/rg" if has_rg else "/usr/bin/grep", pattern, rel, glob, rg=has_rg)

NEW TESTS: ONE append_file to tests/test_tools_files.py (1113 lines; it ends with `    assert tools.net_change("hello") is None`) with exactly this text, starting with two empty lines. `tools`, `pytest`, `Path` and the `wt` fixture already exist in that module.


@pytest.mark.parametrize(("executable", "rg", "glob", "expected"), [
    ("/usr/bin/rg", True, None,
     ["/usr/bin/rg", "-n", "--no-heading", "-M", "300", "-e", "needle", "--", "-"]),
    ("/usr/bin/rg", True, "*.py",
     ["/usr/bin/rg", "-n", "--no-heading", "-M", "300", "-e", "needle", "-g", "*.py", "--", "-"]),
    ("grep", False, None, ["grep", "-rn", "-e", "needle", "--", "-"]),
    ("grep", False, "*.py", ["grep", "-rn", "-e", "needle", "--include=*.py", "--", "-"]),
])
def test_grep_argv_puts_the_path_last_after_double_dash(executable, rg, glob, expected):
    assert tools.grep_argv(executable, "needle", "-", glob, rg=rg) == expected


def test_grep_runs_the_shared_argv_with_the_absolute_path_last(wt: Path, monkeypatch):
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        return tools.subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(tools.shutil, "which", lambda name: "/usr/bin/rg" if name == "rg" else None)
    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    assert tools.grep(wt, "needle", path="src", glob="*.py") == "No matches found."
    assert seen == [["/usr/bin/rg", "-n", "--no-heading", "-M", "300", "-e", "needle", "-g", "*.py",
                     "--", str(tools.resolve_in_worktree("src", wt))]]

VERIFY: run python3 -m pytest -q -p no:cacheprovider tests/test_tools_files.py tests/test_docker_sandbox.py and expect 277 passed. Then run python3 -m pytest -q -p no:cacheprovider with timeout=300. Finish when both pass.
```

### Invocation

Released `dirtywork==0.13.1` via `pipx run --spec`, from repo HEAD on main after PR #155, `--provider openai --base-url http://localhost:1234/v1 --model qwen/qwen3-coder-next --sandbox docker --image dirtywork-worker-pytest:0.13 --verify "python3 -m pytest -q -p no:cacheprovider" --verify-rounds 2 --max-turns 60 --timeout 1800`. The brief is passed as one argv element. `tools/soak_sampler.sh` runs for the whole wall time and is stopped on every exit path.

### Review gates

The worker's diff should match the dry-run patch. `tests/test_docker_sandbox.py` untouched. Host: `test_tools_files.py` + `test_docker_sandbox.py` 277 passed, full suite green, `git diff --check` clean. Independently: the five new host cases fail on the baseline module.

## Delivery

Run ledger: [issue #151 ledger](../bench/2026-09-08-issue-151-shared-grep-argv-ledger.md). [PR #156](https://github.com/JimboSchneider/dirtywork/pull/156) closes issue #151; pending the owner's merge go-ahead.
