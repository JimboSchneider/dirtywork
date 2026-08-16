# SP1 Hardening Implementation Plan

For agentic workers: work one task at a time, in order; do not start task N+1
until task N's tests are green and committed.

## Goal

Close the 13 findings in the spec's Sub-project 1 table: tolerate git global
options in the bash denylist, read `CLAUDE.md`/`AGENTS.md` from the git object
store instead of the filesystem, validate `.worktrees` and `info/exclude`
against symlink/collision attacks, bound every previously-unbounded
model-or-repo-controlled input (HTTP error bodies, run/transcript
directories, file writes, directory listings, assistant transcript text,
worktree disk growth), add full run provenance, and document the two
findings that stay as documented limitations in host mode (`--sandbox none`)
until the sandbox (SP2) lands. Every task keeps the existing 185-test suite
green; by the end there are ~185 + ~70 new tests, all green.

## Architecture

No new processes, no new runtime dependencies, no change to the six-tool
agent loop's shape. This is a hardening pass over the existing five modules
plus two new small modules (`dirtywork/rundir.py`, `dirtywork/budget.py`)
that SP2 will also depend on. The shape after this plan:

```
dirtywork/
  __init__.py       (unchanged: __version__)
  __main__.py        MODIFIED: rundir wiring, provenance, new flags, finalize
  guardrails.py       MODIFIED: git global-option tolerant denylist rules
  workspace.py        MODIFIED: git-object-store repo context, worktree/
                                 info-exclude validation, host_diff_stat
  llm.py              MODIFIED: bounded HTTP error body read
  transcript.py        MODIFIED: O_EXCL|O_NOFOLLOW open, no mkdir
  tools.py             MODIFIED: _open_regular, MAX_WRITE_BYTES,
                                 MAX_LIST_ENTRIES, ToolExecutor budget check
  runner.py            MODIFIED: finalize hook, MAX_ASSISTANT_TEXT_CHARS,
                                 budget_exceeded status
  rundir.py            NEW: ensure_runs_dir, create_run_dir, RunDirError
  budget.py            NEW: measure_worktree, BudgetReport, BudgetExceeded
```

## Tech Stack

Python >= 3.9, stdlib only (`os`, `stat`, `re`, `secrets`, `subprocess`,
`errno`, `dataclasses`, `pathlib`, `typing`, `json`, `time`, `math`,
`argparse`, `datetime`), pytest for tests. No new third-party dependencies.

## Spec path

`docs/superpowers/specs/2026-08-15-review-response-design.md` — Sub-project 1
("Hardening") section, the 13-row table, plus the shared threat model and
success criteria at the top of the document.

## Global Constraints

- Python 3.9 floor: no `match`, no `X | Y` unions at runtime (only under
  `from __future__ import annotations`), no `tarfile.data_filter`, no
  `typing.Literal` misuse beyond 3.9 (`Literal` exists in 3.9 `typing` —
  fine), `dataclass(slots=)` not available.
- Stdlib only. No new dependencies.
- The stdout JSON contract may gain fields but must not lose or rename any
  (`status, worktree, branch, transcript, turns, usage, final_message`).
- Every existing test stays green after every task. Run
  `python -m pytest -q` at the end of each task.
- Commit after each task with a conventional message (`feat:`, `fix:`,
  `test:`, `docs:`, `refactor:`).
- New tests go in the existing module test file for the file touched where
  one exists; new modules get `tests/test_<module>.py`.
- Tests that need Docker use marker `docker` (new; add to `pyproject.toml`
  markers and to `addopts` exclusion: `-m 'not live and not docker'`) and
  skip when Docker is not available. (Deliberate refinement of the spec's
  "-m live": `live` already means "needs LM Studio". Not exercised by this
  plan — no task here needs Docker — but the marker convention is recorded
  here because SP2 depends on it.)
- Never leave placeholders in a plan step: every code step shows the actual
  code; every test step shows the actual test.

## File Structure

- Modify `dirtywork/guardrails.py` — row 1 (git global options).
- Modify `dirtywork/workspace.py` — rows 2, 3, 4, 12; adds
  `worktree_base_commit`, `host_diff_stat`.
- Modify `dirtywork/llm.py` — row 5 (bounded HTTP error body).
- Create `dirtywork/rundir.py` — row 6 (`ensure_runs_dir`, `create_run_dir`,
  `RunDirError`).
- Modify `dirtywork/transcript.py` — row 6 (`O_EXCL|O_NOFOLLOW` open, no
  `mkdir`).
- Modify `dirtywork/tools.py` — rows 8, 9 (`_open_regular`,
  `MAX_WRITE_BYTES`, `MAX_LIST_ENTRIES`) and row 10 wiring (`ToolExecutor`
  budget check, task 9).
- Create `dirtywork/budget.py` — row 10 walker.
- Modify `dirtywork/runner.py` — rows 7, 8, 10 (finalize hook,
  `MAX_ASSISTANT_TEXT_CHARS`, `budget_exceeded`).
- Modify `dirtywork/__main__.py` — wiring: `base_commit`, run dir,
  provenance, flags, `dirtywork_version`, `diff_stat` finalize, `RunDirError`
  → exit 2.
- Modify `README.md` (Security & trust / Safety model / Machine contract)
  and `SECURITY.md` — row 11 sentence; document that host mode does not
  block `python3 -c` writes (row 1) and that `diff_stat` in host mode is
  `git diff --stat` (tracked changes only).
- Tests: `tests/test_guardrails_bash.py`, `tests/test_workspace.py`,
  `tests/test_llm.py`, `tests/test_rundir.py` (new),
  `tests/test_transcript.py`, `tests/test_tools_files.py`,
  `tests/test_tools_bash.py`, `tests/test_budget.py` (new),
  `tests/test_runner.py`, `tests/test_main.py`.

All commands below assume the working directory is the repo root
`/Users/jimschneider/repos/dirtywork`. Every "run tests" step means
`python3 -m pytest -q` unless a narrower command is given for speed while
iterating; the narrower command is always followed by the full suite before
committing.

---

## Task 1: guardrails — git global options tolerance (row 1)

### Files

- Modify: `dirtywork/guardrails.py` (the `_DENYLIST` list, lines 46–78)
- Modify: `tests/test_guardrails_bash.py` (the `BLOCKED`/`ALLOWED` lists)

### Interfaces

Consumes: nothing new.
Produces: `check_bash_command(command: str) -> str | None` — same signature,
now also blocks git subcommands that appear after global options
(`-C <path>`, `-c <k>=<v>`, `--<flag>[=v]`, `-<x>`).

### Steps

1. Add failing test cases to the `BLOCKED` and `ALLOWED` lists in
   `tests/test_guardrails_bash.py`.

   Open `tests/test_guardrails_bash.py` and make this edit — add three new
   entries right after the existing `"git worktree remove x",` line (currently
   line 53) and before the `# plain download piped into a non-sh
   interpreter` comment:

   Replace:
   ```python
       "git worktree remove x",
       # plain download piped into a non-sh interpreter
   ```
   With:
   ```python
       "git worktree remove x",
       # git subcommands preceded by global options (-C, -c, --flag, -x) —
       # the plain-form denylist rules didn't skip these, so the exact same
       # writes slipped past when prefixed with an option.
       "git -C ../.. config core.hooksPath x",
       "git -c core.hooksPath=x push",
       "git --no-pager config user.name",
       # plain download piped into a non-sh interpreter
   ```

   Then add two new entries to the `ALLOWED` list, right after
   `"git worktree list",` (currently line 79):

   Replace:
   ```python
       "git worktree list",
       "git reflog",                     # viewing history is fine; expire/delete blocked
   ```
   With:
   ```python
       "git worktree list",
       "git reflog",                     # viewing history is fine; expire/delete blocked
       "git -C sub status",              # -C with a read-only subcommand is fine
       "git -c color.ui=false log",      # -c with a read-only subcommand is fine
   ```

2. Run the new tests and confirm they fail for the right reason.

   ```
   python3 -m pytest tests/test_guardrails_bash.py -q -k "test_blocked or test_allowed"
   ```

   Expected failure: the three new `BLOCKED` parametrized cases
   (`git -C ../.. config core.hooksPath x`, `git -c core.hooksPath=x push`,
   `git --no-pager config user.name`) fail with
   `assert check_bash_command(cmd) is not None` → `assert None is not None`
   (the current regexes require `git` to be immediately followed by the
   subcommand, so an inserted global option makes the match fail). The two
   new `ALLOWED` cases pass already (nothing currently blocks them), so only
   3 of the 5 new cases fail — that's expected at this point.

3. Implement the git-global-option-tolerant denylist in
   `dirtywork/guardrails.py`.

   Replace the two git-related entries inside `_DENYLIST` (the exact current
   text, lines 48–66):
   ```python
       ("git push is not allowed — leave changes uncommitted for review",
        r"\bgit\s+push\b"),
       # A linked worktree SHARES refs/config/objects with the parent repo, so these
       # git subcommands mutate the parent's state from inside the worktree.
       # core.hooksPath in particular is a persistent host-code-execution pivot.
       # Read-only forms (config --get/--list, remote -v, worktree list, bare reflog)
       # are intentionally NOT matched.
       ("git command that writes the parent repo's shared refs/config is not allowed",
        # config: allowlist the read forms (--get*/--list/-l) and block everything
        # else. Enumerating write flags is whack-a-mole (--local/--global/--system/
        # --file/--unset/… all write shared config from a linked worktree), so we
        # invert it: block `git config` unless a read flag precedes the next separator.
        r"\bgit\s+config\b(?![^;|&]*\s(?:--get\S*|--list|-l)\b)"
        r"|\bgit\s+remote\s+(add|set-url|remove|rm|rename)\b"
        r"|\bgit\s+(update-ref|gc|filter-branch)\b"
        r"|\bgit\s+reflog\s+(expire|delete)\b"
        r"|\bgit\s+worktree\s+(add|remove|prune|move)\b"
        r"|\bgit\s+branch\s+(-[dDmM]\b|--(delete|move)\b)"
        r"|\bgit\s+tag\s+(-d\b|--delete\b)"),
   ```
   With:
   ```python
       ("git push is not allowed — leave changes uncommitted for review",
        _GIT_OPTS + r"push\b"),
       # A linked worktree SHARES refs/config/objects with the parent repo, so these
       # git subcommands mutate the parent's state from inside the worktree.
       # core.hooksPath in particular is a persistent host-code-execution pivot.
       # Read-only forms (config --get/--list, remote -v, worktree list, bare reflog)
       # are intentionally NOT matched.
       ("git command that writes the parent repo's shared refs/config is not allowed",
        # config: allowlist the read forms (--get*/--list/-l) and block everything
        # else. Enumerating write flags is whack-a-mole (--local/--global/--system/
        # --file/--unset/… all write shared config from a linked worktree), so we
        # invert it: block `git config` unless a read flag precedes the next separator.
        _GIT_OPTS + r"config\b(?![^;|&]*\s(?:--get\S*|--list|-l)\b)"
        r"|" + _GIT_OPTS + r"remote\s+(add|set-url|remove|rm|rename)\b"
        r"|" + _GIT_OPTS + r"(update-ref|gc|filter-branch)\b"
        r"|" + _GIT_OPTS + r"reflog\s+(expire|delete)\b"
        r"|" + _GIT_OPTS + r"worktree\s+(add|remove|prune|move)\b"
        r"|" + _GIT_OPTS + r"branch\s+(-[dDmM]\b|--(delete|move)\b)"
        r"|" + _GIT_OPTS + r"tag\s+(-d\b|--delete\b)"),
   ```

   Then add the `_GIT_OPTS` constant just above `_DENYLIST`. Replace this
   exact current text (lines 36–46):
   ```python
   # (reason, pattern) — case-insensitive. BEST-EFFORT accident guards, NOT a
   # security boundary: bash is a general shell, so a determined or prompt-injected
   # model can still read absolute host paths or obfuscate its way past these. The
   # real containment is OS-level sandboxing (see SECURITY.md); these only raise the
   # bar for a *confused* model. The escape-target rules match the natural accident
   # forms — absolute (/), home (~), and parent-relative (..) — since a worktree at
   # <repo>/.worktrees/dw-<slug> is escaped by `../..`. We deliberately do NOT match
   # a leading `$`: HOME is relocated into the worktree (so $HOME/~ stay confined),
   # and a blanket `$` would reject ordinary idioms like `rm -rf "$BUILD_DIR"`.
   _ESCAPE_TARGET = r"(/|~|\.\.)"
   _DENYLIST: list[tuple[str, str]] = [
   ```
   With:
   ```python
   # (reason, pattern) — case-insensitive. BEST-EFFORT accident guards, NOT a
   # security boundary: bash is a general shell, so a determined or prompt-injected
   # model can still read absolute host paths or obfuscate its way past these. The
   # real containment is OS-level sandboxing (see SECURITY.md); these only raise the
   # bar for a *confused* model. The escape-target rules match the natural accident
   # forms — absolute (/), home (~), and parent-relative (..) — since a worktree at
   # <repo>/.worktrees/dw-<slug> is escaped by `../..`. We deliberately do NOT match
   # a leading `$`: HOME is relocated into the worktree (so $HOME/~ stay confined),
   # and a blanket `$` would reject ordinary idioms like `rm -rf "$BUILD_DIR"`.
   #
   # NOTE ON SCOPE: none of this — including the git-subcommand rules below —
   # blocks a model from writing outside the worktree via an *interpreter*, e.g.
   # `python3 -c "open('/tmp/x','w').write('y')"`. Enumerating every interpreter's
   # write primitive is not a regex-shaped problem. Host mode (`--sandbox none`)
   # does not close that gap; the fix is a real OS process boundary (the Docker
   # sandbox, sub-project 2), not a bigger denylist. Documented in README.md and
   # SECURITY.md.
   _ESCAPE_TARGET = r"(/|~|\.\.)"
   # git accepts global options (-C <path>, -c <key>=<value>, --<flag>[=value],
   # -<x>) before the subcommand. The old \bgit\s+<subcommand> rules didn't skip
   # these, so `git -C ../.. config ...` or `git -c core.hooksPath=x push` had the
   # exact same effect as the plain form but slipped past the denylist. Every
   # git-subcommand rule below is prefixed with this instead of a bare `\bgit\s+`.
   _GIT_OPTS = r"\bgit\s+(?:(?:-C\s+\S+|-c\s+\S+|--\S+|-[A-Za-z]\S*)\s+)*"
   _DENYLIST: list[tuple[str, str]] = [
   ```

4. Run the guardrails tests again, then the full suite.

   ```
   python3 -m pytest tests/test_guardrails_bash.py -q
   python3 -m pytest -q
   ```

   Expected: all pass (185 existing + 5 new = 190).

5. Commit.

   ```
   git add dirtywork/guardrails.py tests/test_guardrails_bash.py
   git commit -m "fix: guardrails tolerate git global options before subcommands"
   ```

---

## Task 2: workspace — git-object-store repo context (row 2)

### Files

- Modify: `dirtywork/workspace.py` (add `MAX_CONTEXT_CHARS`,
  `MAX_CONTEXT_BYTES`, `load_repo_context`, `worktree_base_commit`)
- Modify: `dirtywork/__main__.py` (call site update — done in this task so
  the suite stays green)
- Modify: `tests/test_workspace.py` (replace `test_load_repo_context`, add
  new tests)

### Interfaces

Consumes: `subprocess.run` (stdlib), the module's existing `_git(repo,
*args)` helper.
Produces:
- `MAX_CONTEXT_CHARS = 32_000`
- `MAX_CONTEXT_BYTES = 5 * 1024 * 1024`
- `load_repo_context(repo: Path, base_commit: str) -> str | None` (breaking
  signature change: second arg now required)
- `worktree_base_commit(worktree: Path) -> str`

### Steps

1. Write the failing tests. Replace the entire existing
   `test_load_repo_context` function in `tests/test_workspace.py` (the exact
   current text, lines 150–155):
   ```python
   def test_load_repo_context(repo: Path):
       assert load_repo_context(repo) is None
       (repo / "AGENTS.md").write_text("agents rules")
       assert load_repo_context(repo) == "agents rules"
       (repo / "CLAUDE.md").write_text("claude rules")  # CLAUDE.md wins
       assert load_repo_context(repo) == "claude rules"
   ```
   With:
   ```python
   def _commit_file(repo: Path, name: str, content: str) -> str:
       (repo / name).write_text(content)
       _git(repo, "add", name)
       _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", f"add {name}")
       return _git(repo, "rev-parse", "HEAD").strip()


   def test_load_repo_context_none_when_absent(repo: Path):
       base = _git(repo, "rev-parse", "HEAD").strip()
       assert load_repo_context(repo, base) is None


   def test_load_repo_context_reads_from_base_commit(repo: Path):
       base = _commit_file(repo, "CLAUDE.md", "claude rules")
       assert load_repo_context(repo, base) == "claude rules"


   def test_load_repo_context_agents_md_fallback(repo: Path):
       base = _commit_file(repo, "AGENTS.md", "agents rules")
       assert load_repo_context(repo, base) == "agents rules"


   def test_load_repo_context_claude_md_preferred_over_agents_md(repo: Path):
       _commit_file(repo, "AGENTS.md", "agents rules")
       base = _commit_file(repo, "CLAUDE.md", "claude rules")
       assert load_repo_context(repo, base) == "claude rules"


   def test_load_repo_context_mode_100755_accepted(repo: Path):
       (repo / "CLAUDE.md").write_text("exec rules")
       (repo / "CLAUDE.md").chmod(0o755)
       _git(repo, "add", "CLAUDE.md")
       _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "exec claude")
       base = _git(repo, "rev-parse", "HEAD").strip()
       assert load_repo_context(repo, base) == "exec rules"


   def test_load_repo_context_ignores_uncommitted_file(repo: Path):
       # File exists on disk but was never committed at base_commit — must be
       # invisible. This is the whole point of reading from the object store
       # instead of the filesystem.
       base = _git(repo, "rev-parse", "HEAD").strip()
       (repo / "CLAUDE.md").write_text("not committed")
       assert load_repo_context(repo, base) is None


   def test_load_repo_context_ignores_symlink(repo: Path):
       import os
       os.symlink("/etc/passwd", repo / "CLAUDE.md")
       _git(repo, "add", "CLAUDE.md")
       _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "symlinked claude md")
       base = _git(repo, "rev-parse", "HEAD").strip()
       assert load_repo_context(repo, base) is None


   def test_load_repo_context_skips_oversized_blob(repo: Path, monkeypatch):
       import dirtywork.workspace as workspace_mod
       monkeypatch.setattr(workspace_mod, "MAX_CONTEXT_BYTES", 10)
       base = _commit_file(repo, "CLAUDE.md", "this content is over ten bytes")
       assert load_repo_context(repo, base) is None


   def test_load_repo_context_truncates_long_content(repo: Path):
       base = _commit_file(repo, "CLAUDE.md", "x" * 40000)
       result = load_repo_context(repo, base)
       assert result is not None
       marker = "\n[truncated at 32000 chars]"
       assert result.endswith(marker)
       assert len(result) == 32000 + len(marker)


   def test_worktree_base_commit(repo: Path):
       wt = create_worktree(repo, "ctx-08141109", None)
       expected = _git(repo, "rev-parse", "HEAD").strip()
       assert worktree_base_commit(wt) == expected
   ```

2. Run the new tests and confirm they fail (import/attribute errors and
   `TypeError: load_repo_context() missing 1 required positional argument`).

   ```
   python3 -m pytest tests/test_workspace.py -q -k "load_repo_context or worktree_base_commit"
   ```

   Expected: `AttributeError`/`ImportError`-shaped failures for
   `worktree_base_commit` (doesn't exist yet) and `TypeError` for every
   `load_repo_context(repo, base)` call (current signature takes one arg).

3. Implement `load_repo_context` and `worktree_base_commit` in
   `dirtywork/workspace.py`.

   Replace the module's constant-free top (the exact current text, lines
   1–8):
   ```python
   from __future__ import annotations

   import re
   import secrets
   import subprocess
   from datetime import datetime
   from pathlib import Path


   class WorkspaceError(Exception):
   ```
   With:
   ```python
   from __future__ import annotations

   import re
   import secrets
   import subprocess
   from datetime import datetime
   from pathlib import Path

   MAX_CONTEXT_CHARS = 32_000
   # Separate from tools.MAX_READ_BYTES (also 5 MB) even though the value is the
   # same today — this bounds a git blob size, not a filesystem read.
   MAX_CONTEXT_BYTES = 5 * 1024 * 1024


   class WorkspaceError(Exception):
   ```

   Then replace the entire current `load_repo_context` function (the exact
   current text, lines 71–76):
   ```python
   def load_repo_context(repo: Path) -> str | None:
       for name in ("CLAUDE.md", "AGENTS.md"):
           p = repo / name
           if p.is_file():
               return p.read_text(encoding="utf-8", errors="replace")
       return None
   ```
   With:
   ```python
   def worktree_base_commit(worktree: Path) -> str:
       res = _git(worktree, "rev-parse", "HEAD")
       if res.returncode != 0:
           raise WorkspaceError(f"cannot resolve HEAD in {worktree}: {res.stderr.strip()}")
       return res.stdout.strip()


   def load_repo_context(repo: Path, base_commit: str) -> str | None:
       """Read CLAUDE.md/AGENTS.md from the base commit's git object store, not
       the filesystem. This closes two problems with a filesystem read: a
       symlinked CLAUDE.md pointing outside the repo (Path.is_file() follows
       links) and an unbounded read of whatever happens to be on disk right now
       (which could be dirty/uncommitted content unrelated to the commit the
       worktree was branched from). `cat-file -p` on a blob never runs a smudge
       filter, so this is also immune to a hostile .gitattributes.
       """
       res = _git(repo, "ls-tree", base_commit, "--", "CLAUDE.md", "AGENTS.md")
       if res.returncode != 0:
           return None
       entries = {}
       for line in res.stdout.splitlines():
           if not line.strip():
               continue
           meta, _, name = line.partition("\t")
           parts = meta.split()
           if len(parts) != 3:
               continue
           mode, obj_type, sha = parts
           # Only plain blobs at mode 100644 (file) or 100755 (executable) are
           # eligible. Symlink entries (mode 120000) and gitlinks/submodules
           # (mode 160000) are ignored — a symlinked CLAUDE.md in the commit
           # itself must not be followed either.
           if obj_type != "blob" or mode not in ("100644", "100755"):
               continue
           entries[name] = sha
       for name in ("CLAUDE.md", "AGENTS.md"):
           sha = entries.get(name)
           if sha is None:
               continue
           size_res = _git(repo, "cat-file", "-s", sha)
           if size_res.returncode != 0:
               continue
           try:
               size = int(size_res.stdout.strip())
           except ValueError:
               continue
           if size > MAX_CONTEXT_BYTES:
               continue
           content_res = subprocess.run(
               ["git", "-C", str(repo), "cat-file", "-p", sha],
               capture_output=True,
           )
           if content_res.returncode != 0:
               continue
           text = content_res.stdout.decode("utf-8", errors="replace")
           if len(text) > MAX_CONTEXT_CHARS:
               text = text[:MAX_CONTEXT_CHARS] + "\n[truncated at 32000 chars]"
           return text
       return None
   ```

4. Update the `__main__.py` call site so the suite stays green (the old
   single-argument call would now raise `TypeError`).

   Replace the exact current import block in `dirtywork/__main__.py` (lines
   13–20):
   ```python
   from .workspace import (
       WorkspaceError,
       create_worktree,
       ensure_worktrees_excluded,
       load_repo_context,
       make_slug,
       preflight_repo,
   )
   ```
   With:
   ```python
   from .workspace import (
       WorkspaceError,
       create_worktree,
       ensure_worktrees_excluded,
       load_repo_context,
       make_slug,
       preflight_repo,
       worktree_base_commit,
   )
   ```

   Then replace the exact current line in `main()` (line 104):
   ```python
           system_prompt = build_system_prompt(worktree, load_repo_context(worktree))
   ```
   With:
   ```python
           base_commit = worktree_base_commit(worktree)
           system_prompt = build_system_prompt(worktree, load_repo_context(repo, base_commit))
   ```

   (`base_commit` here is a new local variable; task 10 will reuse it for
   provenance and the finalize hook — no further edit needed at this call
   site later.)

5. Run the workspace tests, then the full suite.

   ```
   python3 -m pytest tests/test_workspace.py tests/test_main.py -q
   python3 -m pytest -q
   ```

   Expected: all pass. `tests/test_main.py::test_load_repo_context_uses_worktree_not_caller_checkout`
   requires no edit — it already exercises exactly the new behavior (it
   dirties the *filesystem* copy of `CLAUDE.md` after the commit, then
   asserts the dirty content never reaches the prompt; the git-object-store
   read makes that true for a structural reason now, not an accidental one).

6. Commit.

   ```
   git add dirtywork/workspace.py dirtywork/__main__.py tests/test_workspace.py
   git commit -m "feat: read CLAUDE.md/AGENTS.md from the base commit's git object store"
   ```

---

## Task 3: workspace — `.worktrees` and destination checks, 4-byte salt (rows 3, 12)

### Files

- Modify: `dirtywork/workspace.py` (`create_worktree`, `make_slug`)
- Modify: `tests/test_workspace.py` (new tests)

### Interfaces

Consumes: `os.lstat`, `stat.S_ISDIR` (stdlib).
Produces: `create_worktree(repo: Path, slug: str, branch_from: str | None) ->
Path` — same signature, hardened internals. `make_slug(...)` — same
signature, default salt now `secrets.token_hex(4)` (8 hex chars, was
`token_hex(2)`/4 chars).

### Steps

1. Write the failing tests. Add these to `tests/test_workspace.py`, right
   after the existing `test_create_worktree_existing_dir_no_stale_branch`
   function (the last function in the file):

   ```python
   def test_create_worktree_worktrees_symlink_rejected(repo: Path, tmp_path: Path):
       outside = tmp_path / "outside"
       outside.mkdir()
       (repo / ".worktrees").symlink_to(outside)
       with pytest.raises(WorkspaceError):
           create_worktree(repo, "sym-08141109", None)
       assert list(outside.iterdir()) == []  # nothing created through the symlink


   def test_create_worktree_destination_symlink_rejected(repo: Path, tmp_path: Path):
       (repo / ".worktrees").mkdir()
       elsewhere = tmp_path / "elsewhere"
       elsewhere.mkdir()
       (repo / ".worktrees" / "dw-pre-08141109").symlink_to(elsewhere)
       with pytest.raises(WorkspaceError):
           create_worktree(repo, "pre-08141109", None)
       porcelain = subprocess.run(
           ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
           capture_output=True, text=True, check=True,
       ).stdout
       assert porcelain.count("worktree ") == 1  # only the main worktree


   def test_create_worktree_destination_empty_dir_rejected(repo: Path):
       (repo / ".worktrees" / "dw-emptydir-08141109").mkdir(parents=True)
       with pytest.raises(WorkspaceError):
           create_worktree(repo, "emptydir-08141109", None)


   def test_make_slug_salt_is_8_hex_chars():
       now = datetime(2026, 8, 14, 11, 9)
       slug = make_slug("same task", now)
       salt = slug.rsplit("-", 1)[-1]
       assert len(salt) == 8
       int(salt, 16)  # raises ValueError if not valid hex
   ```

2. Run the new tests and confirm they fail.

   ```
   python3 -m pytest tests/test_workspace.py -q -k "worktrees_symlink or destination_symlink or destination_empty_dir or salt_is_8_hex"
   ```

   Expected: the three symlink/collision tests fail because
   `create_worktree` currently has no pre-checks (git itself may create the
   worktree through the symlink, or fail with a different error that isn't
   `WorkspaceError`, or the empty-dir case fails differently than expected —
   any of these is an acceptable "fails for the right reason" signal). The
   salt test fails: `assert len(salt) == 8` → `assert 4 == 8`.

3. Implement the hardening in `dirtywork/workspace.py`.

   Replace the module's import block (the exact current text after task 2's
   edit, lines 1–12):
   ```python
   from __future__ import annotations

   import re
   import secrets
   import subprocess
   from datetime import datetime
   from pathlib import Path

   MAX_CONTEXT_CHARS = 32_000
   # Separate from tools.MAX_READ_BYTES (also 5 MB) even though the value is the
   # same today — this bounds a git blob size, not a filesystem read.
   MAX_CONTEXT_BYTES = 5 * 1024 * 1024
   ```
   With:
   ```python
   from __future__ import annotations

   import os
   import re
   import secrets
   import stat
   import subprocess
   from datetime import datetime
   from pathlib import Path

   MAX_CONTEXT_CHARS = 32_000
   # Separate from tools.MAX_READ_BYTES (also 5 MB) even though the value is the
   # same today — this bounds a git blob size, not a filesystem read.
   MAX_CONTEXT_BYTES = 5 * 1024 * 1024
   ```

   Replace `make_slug`'s salt line (the exact current text):
   ```python
       if salt is None:
           salt = secrets.token_hex(2)
   ```
   With:
   ```python
       if salt is None:
           salt = secrets.token_hex(4)
   ```

   Replace the entire current `create_worktree` function:
   ```python
   def create_worktree(repo: Path, slug: str, branch_from: str | None) -> Path:
       rel = Path(".worktrees") / f"dw-{slug}"
       ref = branch_from or "HEAD"
       branch = f"dirtywork/{slug}"
       existed = _git(repo, "rev-parse", "--verify", "--quiet",
                       f"refs/heads/{branch}").returncode == 0
       res = _git(repo, "worktree", "add", "-b", branch, str(rel), ref)
       if res.returncode != 0:
           if not existed:
               _git(repo, "branch", "-D", branch)  # best-effort cleanup; ignore result
           raise WorkspaceError(f"git worktree add failed: {res.stderr.strip()}")
       return repo / rel
   ```
   With:
   ```python
   def create_worktree(repo: Path, slug: str, branch_from: str | None) -> Path:
       worktrees_dir = repo / ".worktrees"
       try:
           wd_st = os.lstat(worktrees_dir)
       except FileNotFoundError:
           pass
       else:
           if not stat.S_ISDIR(wd_st.st_mode):
               raise WorkspaceError(
                   f"{worktrees_dir} exists and is not a directory — refusing to "
                   f"create a worktree through a symlink or other non-directory here"
               )

       rel = Path(".worktrees") / f"dw-{slug}"
       dest = repo / rel
       try:
           os.lstat(dest)
       except FileNotFoundError:
           pass
       else:
           # A pre-existing file, directory, or symlink at the EXACT destination
           # must abort before `git worktree add` runs: git would create through
           # a symlink, and a later `worktree remove` would then clean an
           # unrelated outside directory.
           raise WorkspaceError(
               f"{dest} already exists; refusing to create a worktree through a "
               f"pre-existing file, directory, or symlink at the exact destination"
           )

       ref = branch_from or "HEAD"
       branch = f"dirtywork/{slug}"
       existed = _git(repo, "rev-parse", "--verify", "--quiet",
                       f"refs/heads/{branch}").returncode == 0
       res = _git(repo, "worktree", "add", "-b", branch, str(rel), ref)
       if res.returncode != 0:
           if not existed:
               _git(repo, "branch", "-D", branch)  # best-effort cleanup; ignore result
           raise WorkspaceError(f"git worktree add failed: {res.stderr.strip()}")

       worktree = repo / rel
       # Never `.resolve()` the joined path and compare — that variant passes
       # wrongly when a component is a symlink. Resolve each side separately.
       expected_parent = repo.resolve() / ".worktrees"
       if expected_parent not in worktree.resolve().parents:
           _git(repo, "worktree", "remove", "--force", str(rel))
           _git(repo, "branch", "-D", branch)
           raise WorkspaceError(
               f"worktree resolved to {worktree.resolve()}, outside the expected "
               f"{expected_parent} — refusing (a symlinked .worktrees or ref "
               f"could redirect git worktree add outside the repo)"
           )
       return worktree
   ```

4. Run the workspace tests, then the full suite.

   ```
   python3 -m pytest tests/test_workspace.py -q
   python3 -m pytest -q
   ```

   Expected: all pass.

5. Commit.

   ```
   git add dirtywork/workspace.py tests/test_workspace.py
   git commit -m "fix: validate .worktrees and worktree destination against symlinks/collisions, widen slug salt"
   ```

---

## Task 4: workspace — `info/exclude` path validation (row 4)

### Files

- Modify: `dirtywork/workspace.py` (`ensure_worktrees_excluded`)
- Modify: `tests/test_workspace.py` (new test)

### Interfaces

Consumes: `os.open`/`os.fdopen` (stdlib, already imported `os` from task 3).
Produces: `ensure_worktrees_excluded(repo: Path) -> None` — same signature,
hardened internals.

### Steps

1. Write the failing test. Add to `tests/test_workspace.py`, right after
   `test_ensure_worktrees_excluded_from_linked_worktree`:

   ```python
   def test_ensure_worktrees_excluded_rejects_symlinked_exclude(repo: Path, tmp_path: Path):
       exclude = repo / ".git" / "info" / "exclude"
       outside = tmp_path / "outside-exclude.txt"
       outside.write_text("original content\n")
       exclude.unlink()
       exclude.symlink_to(outside)

       with pytest.raises(WorkspaceError):
           ensure_worktrees_excluded(repo)

       assert outside.read_text() == "original content\n"  # untouched
   ```

2. Run the new test and confirm it fails.

   ```
   python3 -m pytest tests/test_workspace.py -q -k rejects_symlinked_exclude
   ```

   Expected failure: no `WorkspaceError` is raised — the current code
   happily follows the symlink and appends `.worktrees/` to
   `outside-exclude.txt`, so `pytest.raises(WorkspaceError)` fails with
   `DID NOT RAISE`.

3. Implement the hardening. Replace the entire current
   `ensure_worktrees_excluded` function in `dirtywork/workspace.py`:
   ```python
   def ensure_worktrees_excluded(repo: Path) -> None:
       # Use --git-path (not --git-dir) so this resolves to the shared repository's
       # info/exclude even when `repo` is itself a linked worktree — a linked
       # worktree's --git-dir is its private .git/worktrees/<name> dir, but git only
       # ever consults the common/shared info/exclude for status/ignore purposes.
       res = _git(repo, "rev-parse", "--git-path", "info/exclude")
       if res.returncode != 0:
           raise WorkspaceError(f"cannot locate git dir for {repo}")
       exclude = Path(res.stdout.strip())
       if not exclude.is_absolute():
           exclude = repo / exclude
       exclude.parent.mkdir(parents=True, exist_ok=True)
       existing = exclude.read_text() if exclude.exists() else ""
       if ".worktrees/" not in existing:
           with open(exclude, "a", encoding="utf-8") as fh:
               if existing and not existing.endswith("\n"):
                   fh.write("\n")
               fh.write(".worktrees/\n")
   ```
   With:
   ```python
   def ensure_worktrees_excluded(repo: Path) -> None:
       # Use --git-path (not --git-dir) so this resolves to the shared repository's
       # info/exclude even when `repo` is itself a linked worktree — a linked
       # worktree's --git-dir is its private .git/worktrees/<name> dir, but git only
       # ever consults the common/shared info/exclude for status/ignore purposes.
       common_res = _git(repo, "rev-parse", "--git-common-dir")
       if common_res.returncode != 0:
           raise WorkspaceError(f"cannot locate git common dir for {repo}")
       common = Path(common_res.stdout.strip())
       if not common.is_absolute():
           common = repo / common
       common = common.resolve()

       exclude_res = _git(repo, "rev-parse", "--git-path", "info/exclude")
       if exclude_res.returncode != 0:
           raise WorkspaceError(f"cannot locate git dir for {repo}")
       exclude = Path(exclude_res.stdout.strip())
       if not exclude.is_absolute():
           exclude = repo / exclude
       exclude.parent.mkdir(parents=True, exist_ok=True)

       # A replaced info/exclude (symlink to a file outside the repo, planted by a
       # hostile committed tree or a prior compromised run) must not redirect this
       # write outside the git dir. Require the resolved path inside the resolved
       # common dir before opening it at all.
       resolved_exclude = exclude.resolve()
       if resolved_exclude.parent != common and common not in resolved_exclude.parents:
           raise WorkspaceError(
               f"info/exclude resolved to {resolved_exclude}, outside the git "
               f"common dir {common} — refusing to write"
           )

       try:
           read_fd = os.open(str(exclude), os.O_RDONLY | os.O_NOFOLLOW)
       except FileNotFoundError:
           existing = ""
       except OSError as e:
           raise WorkspaceError(f"cannot read {exclude}: {e}")
       else:
           with os.fdopen(read_fd, "r", encoding="utf-8") as fh:
               existing = fh.read()

       if ".worktrees/" in existing:
           return

       try:
           write_fd = os.open(
               str(exclude), os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o644
           )
       except OSError as e:
           raise WorkspaceError(f"cannot open {exclude} for writing: {e}")
       with os.fdopen(write_fd, "a", encoding="utf-8") as fh:
           if existing and not existing.endswith("\n"):
               fh.write("\n")
           fh.write(".worktrees/\n")
   ```

4. Run the workspace tests, then the full suite.

   ```
   python3 -m pytest tests/test_workspace.py -q
   python3 -m pytest -q
   ```

   Expected: all pass, including the two pre-existing
   `test_ensure_worktrees_excluded_idempotent` and
   `test_ensure_worktrees_excluded_from_linked_worktree` tests (verified by
   design: both call the function on a real `.git/info/exclude` with no
   symlink involved, so the new resolved-path check passes and the
   read/append logic is behaviorally identical to before).

5. Commit.

   ```
   git add dirtywork/workspace.py tests/test_workspace.py
   git commit -m "fix: validate info/exclude resolves inside the git common dir before writing"
   ```

---

## Task 5: llm — bounded HTTP error body (row 5)

### Files

- Modify: `dirtywork/llm.py` (line 77)
- Modify: `tests/test_llm.py` (new test)

### Interfaces

Consumes: `urllib.error.HTTPError` (stdlib).
Produces: no signature change — `LMStudioClient._request` reads at most 500
bytes from a hostile/slow error body instead of reading the whole thing
before slicing.

### Steps

1. Write the failing test. Add to `tests/test_llm.py`, after
   `test_oversized_response_raises_llmerror` (the last test in the file):

   ```python
   def test_http_error_body_read_is_bounded(monkeypatch):
       import urllib.error
       import urllib.request

       calls = []

       class BoundedFP:
           def read(self, n=-1):
               calls.append(n)
               return b"x" * 10

           def close(self):
               pass

       def fake_urlopen(req, timeout=None):
           raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, BoundedFP())

       monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
       client = LMStudioClient(base_url="http://127.0.0.1:9/v1")
       with pytest.raises(LLMError):
           client.list_models()
       assert calls == [500]
   ```

2. Run the new test and confirm it fails.

   ```
   python3 -m pytest tests/test_llm.py -q -k http_error_body_read_is_bounded
   ```

   Expected failure: `assert calls == [500]` → `assert [-1] == [500]` (the
   current code calls `e.read()` with no argument, i.e. `n=-1`, then slices
   the result in Python — `BoundedFP.read` is called with `n=-1`, not 500).

3. Implement the fix. Replace the exact current text in `dirtywork/llm.py`
   (lines 75–80):
   ```python
           except urllib.error.HTTPError as e:
               try:
                   detail = e.read()[:500]
               except Exception:
                   detail = b"<unreadable error body>"
               raise LLMError(f"LM Studio HTTP {e.code} on {path}: {detail!r}")
   ```
   With:
   ```python
           except urllib.error.HTTPError as e:
               try:
                   detail = e.read(500)
               except Exception:
                   detail = b"<unreadable error body>"
               raise LLMError(f"LM Studio HTTP {e.code} on {path}: {detail!r}")
   ```

4. Run the llm tests, then the full suite.

   ```
   python3 -m pytest tests/test_llm.py -q
   python3 -m pytest -q
   ```

   Expected: all pass.

5. Commit.

   ```
   git add dirtywork/llm.py tests/test_llm.py
   git commit -m "fix: bound HTTP error body read to 500 bytes instead of reading fully first"
   ```

---

## Task 6: rundir + transcript — validated run directories, `O_EXCL` transcript (row 6)

### Files

- Create: `dirtywork/rundir.py`
- Create: `tests/test_rundir.py`
- Modify: `dirtywork/transcript.py`
- Modify: `tests/test_transcript.py`
- Modify: `dirtywork/__main__.py` (wire `ensure_runs_dir`/`create_run_dir`;
  full provenance wiring is task 10, this task only wires the run
  directory itself)

### Interfaces

Consumes: `os.mkdir`, `os.lstat`, `os.getuid` (POSIX), `os.open` (stdlib).
Produces:
- `dirtywork/rundir.py`: `DIRTYWORK_HOME = Path.home() / ".dirtywork"`,
  `RUNS_DIR = DIRTYWORK_HOME / "runs"`, `class RunDirError(Exception)`,
  `ensure_runs_dir(runs_dir: Path = RUNS_DIR) -> Path`,
  `create_run_dir(runs_dir: Path, slug: str) -> Path`.
- `dirtywork/transcript.py`: `Transcript.__init__(self, path: Path)` — same
  signature, opens with
  `O_WRONLY|O_CREAT|O_EXCL|O_APPEND|O_NOFOLLOW`, mode `0o600`; no more
  `mkdir(parents=True)` (the caller — `rundir` — must have already created
  the parent directory).

### Steps

1. Write the failing tests for `dirtywork/rundir.py`. Create
   `tests/test_rundir.py`:

   ```python
   from __future__ import annotations

   import os
   import stat
   from pathlib import Path

   import pytest

   from dirtywork.rundir import RunDirError, create_run_dir, ensure_runs_dir


   def test_ensure_runs_dir_creates_0700_dirs(tmp_path: Path):
       # ensure_runs_dir only creates its two direct levels (mirroring the real
       # ~/.dirtywork/runs, where ~ always already exists) — the stand-in "home"
       # directory itself must exist before the call, same as a real $HOME.
       home = tmp_path / "home"
       home.mkdir()
       runs = home / ".dirtywork" / "runs"
       result = ensure_runs_dir(runs)
       assert result == runs
       assert stat.S_IMODE((home / ".dirtywork").stat().st_mode) == 0o700
       assert stat.S_IMODE(runs.stat().st_mode) == 0o700


   def test_ensure_runs_dir_idempotent(tmp_path: Path):
       home = tmp_path / "home"
       home.mkdir()
       runs = home / ".dirtywork" / "runs"
       ensure_runs_dir(runs)
       ensure_runs_dir(runs)  # second call must not raise


   def test_ensure_runs_dir_symlink_raises(tmp_path: Path):
       home = tmp_path / "home"
       home.mkdir()
       outside = tmp_path / "outside"
       outside.mkdir()
       (home / ".dirtywork").mkdir()
       (home / ".dirtywork" / "runs").symlink_to(outside)
       with pytest.raises(RunDirError):
           ensure_runs_dir(home / ".dirtywork" / "runs")


   def test_ensure_runs_dir_wrong_owner_raises(tmp_path: Path, monkeypatch):
       home = tmp_path / "home"
       home.mkdir()
       runs = home / ".dirtywork" / "runs"
       ensure_runs_dir(runs)  # create it as the real user first
       real_getuid = os.getuid()
       monkeypatch.setattr(os, "getuid", lambda: real_getuid + 1)
       with pytest.raises(RunDirError):
           ensure_runs_dir(runs)


   def test_create_run_dir(tmp_path: Path):
       runs = tmp_path / "runs"
       runs.mkdir()
       run_dir = create_run_dir(runs, "some-slug")
       assert run_dir == runs / "some-slug"
       assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700


   def test_create_run_dir_refuses_existing_dir(tmp_path: Path):
       runs = tmp_path / "runs"
       runs.mkdir()
       (runs / "dup-slug").mkdir()
       with pytest.raises(RunDirError):
           create_run_dir(runs, "dup-slug")


   def test_create_run_dir_refuses_existing_symlink(tmp_path: Path):
       runs = tmp_path / "runs"
       runs.mkdir()
       outside = tmp_path / "outside"
       outside.mkdir()
       (runs / "dup-slug").symlink_to(outside)
       with pytest.raises(RunDirError):
           create_run_dir(runs, "dup-slug")
   ```

2. Run the new tests and confirm they fail with `ModuleNotFoundError: No
   module named 'dirtywork.rundir'`.

   ```
   python3 -m pytest tests/test_rundir.py -q
   ```

3. Implement `dirtywork/rundir.py`:

   ```python
   from __future__ import annotations

   import os
   import stat
   from pathlib import Path

   DIRTYWORK_HOME = Path.home() / ".dirtywork"
   RUNS_DIR = DIRTYWORK_HOME / "runs"


   class RunDirError(Exception):
       """Raised when ~/.dirtywork or a per-run directory cannot be trusted."""


   def _ensure_owned_dir(path: Path) -> None:
       try:
           os.mkdir(path, mode=0o700)
       except FileExistsError:
           pass
       except OSError as e:
           raise RunDirError(f"cannot create {path}: {e}")
       try:
           st = os.lstat(path)
       except OSError as e:
           raise RunDirError(f"cannot stat {path}: {e}")
       if not stat.S_ISDIR(st.st_mode):
           raise RunDirError(f"{path} exists and is not a directory")
       if hasattr(os, "getuid") and st.st_uid != os.getuid():
           raise RunDirError(f"{path} is not owned by the current user")


   def ensure_runs_dir(runs_dir: Path = RUNS_DIR) -> Path:
       runs_dir = Path(runs_dir)
       _ensure_owned_dir(runs_dir.parent)
       _ensure_owned_dir(runs_dir)
       return runs_dir


   def create_run_dir(runs_dir: Path, slug: str) -> Path:
       run_dir = Path(runs_dir) / slug
       try:
           os.mkdir(run_dir, mode=0o700)
       except FileExistsError:
           raise RunDirError(f"{run_dir} already exists — slug collision")
       except OSError as e:
           raise RunDirError(f"cannot create {run_dir}: {e}")
       return run_dir
   ```

4. Run the rundir tests.

   ```
   python3 -m pytest tests/test_rundir.py -q
   ```

   Expected: all pass.

5. Write the failing tests for `dirtywork/transcript.py`. First, the new
   tests below use `pytest.raises`, but `tests/test_transcript.py` does not
   import `pytest` today — add it. Replace the exact current top of the file:
   ```python
   from __future__ import annotations

   import json
   from pathlib import Path

   from dirtywork.transcript import Transcript
   ```
   With:
   ```python
   from __future__ import annotations

   import json
   from pathlib import Path

   import pytest

   from dirtywork.transcript import Transcript
   ```

   Then fix the existing test that relies on the old auto-mkdir behavior —
   replace the exact current text:
   ```python
   def test_writes_jsonl_events_with_ts(tmp_path: Path):
       t = Transcript(tmp_path / "sub" / "transcript.jsonl")  # parent dirs auto-created
       t.write("run_start", task="do a thing", model="m")
   ```
   With:
   ```python
   def test_writes_jsonl_events_with_ts(tmp_path: Path):
       (tmp_path / "sub").mkdir()  # the run dir now exists before Transcript is built
       t = Transcript(tmp_path / "sub" / "transcript.jsonl")
       t.write("run_start", task="do a thing", model="m")
   ```

   Then add three new tests at the end of `tests/test_transcript.py`:
   ```python
   def test_refuses_preexisting_file(tmp_path: Path):
       path = tmp_path / "transcript.jsonl"
       path.write_text("stale content from a slug collision\n")
       with pytest.raises(FileExistsError):
           Transcript(path)


   def test_refuses_symlink(tmp_path: Path):
       real = tmp_path / "elsewhere.jsonl"
       link = tmp_path / "transcript.jsonl"
       link.symlink_to(real)
       with pytest.raises(OSError):
           Transcript(link)
       assert not real.exists()  # nothing was ever written through the symlink


   def test_file_mode_is_0600(tmp_path: Path):
       import stat
       path = tmp_path / "transcript.jsonl"
       t = Transcript(path)
       t.close()
       assert stat.S_IMODE(path.stat().st_mode) == 0o600
   ```

6. Run the transcript tests and confirm the new/edited ones fail.

   ```
   python3 -m pytest tests/test_transcript.py -q
   ```

   Expected: `test_writes_jsonl_events_with_ts` still passes (mkdir still
   works today); `test_refuses_preexisting_file` and `test_refuses_symlink`
   fail with `Failed: DID NOT RAISE` (current code silently appends to an
   existing file and happily follows a symlink); `test_file_mode_is_0600`
   fails because `open(path, "a")` uses the default mode (`0o666` minus
   umask, typically `0o644`), not `0o600`.

7. Implement the hardening. Replace the entire current
   `dirtywork/transcript.py`:
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
   ```
   With (only the class body above `write`/`close` changes; the rest of the
   file is unchanged):
   ```python
   from __future__ import annotations

   import json
   import os
   from datetime import datetime, timezone
   from pathlib import Path


   class Transcript:
       """Append-only JSONL event log, flushed per line so `tail -f` works.

       The parent directory must already exist (created by
       `dirtywork.rundir.create_run_dir` before this is constructed) — this
       class no longer creates it. Opened with O_EXCL so a slug collision (or a
       symlink planted at the transcript path) is a loud failure instead of a
       silent append/overwrite, and O_NOFOLLOW so a symlink at the exact path
       is refused rather than followed.
       """

       def __init__(self, path: Path):
           self.path = Path(path)
           flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | os.O_NOFOLLOW
           fd = os.open(str(self.path), flags, 0o600)
           self._fh = os.fdopen(fd, "a", encoding="utf-8")
   ```

8. Run the transcript tests, then the full suite.

   ```
   python3 -m pytest tests/test_transcript.py -q
   python3 -m pytest -q
   ```

   Expected: all pass — including `tests/test_runner.py`'s `parts` fixture
   (`Transcript(tmp_path / "t.jsonl")`) and every `test_main.py` test that
   monkeypatches `RUNS_DIR` to a fresh `tmp_path / "runs"`. Verify this by
   inspection, not by editing: `tmp_path` is created by pytest itself before
   the test runs, so `Transcript`'s parent directory (`tmp_path` in
   `test_runner.py`, or `run_dir` created by the `rundir` wiring below in
   `test_main.py`) always exists by the time `Transcript.__init__` runs — no
   fixture edits are needed in either file for this task.

9. Wire `rundir` into `dirtywork/__main__.py`. Replace the exact current
   import/module-constant block (lines 1–23, i.e. everything from the
   module docstring line through `DEFAULT_MODEL`):
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
       worktree_base_commit,
   )

   RUNS_DIR = Path.home() / ".dirtywork" / "runs"
   DEFAULT_MODEL = "qwen/qwen3-coder-next"
   ```
   With:
   ```python
   from __future__ import annotations

   import argparse
   import json
   import sys
   from datetime import datetime
   from pathlib import Path

   from .llm import LLMError, LMStudioClient
   from .rundir import RUNS_DIR, RunDirError, create_run_dir, ensure_runs_dir
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
       worktree_base_commit,
   )

   DEFAULT_MODEL = "qwen/qwen3-coder-next"
   ```

   Then replace the exact current text in `main()` (the workspace block
   through the start of the run block):
   ```python
       # ---- workspace ----
       slug = make_slug(args.task, datetime.now())
       try:
           ensure_worktrees_excluded(repo)
           worktree = create_worktree(repo, slug, args.branch_from)
       except WorkspaceError as e:
           _err(str(e))
           return 2

       transcript_path = RUNS_DIR / slug / "transcript.jsonl"
       print(f"transcript: {transcript_path}", file=sys.stderr)
       print(f"worktree:   {worktree}", file=sys.stderr)
   ```
   With:
   ```python
       # ---- workspace ----
       slug = make_slug(args.task, datetime.now())
       try:
           ensure_worktrees_excluded(repo)
           worktree = create_worktree(repo, slug, args.branch_from)
       except WorkspaceError as e:
           _err(str(e))
           return 2

       # ---- run directory (exit 2: the worktree exists but no transcript/run
       # bookkeeping has started yet, so this is still a preflight-style failure) ----
       try:
           runs_dir = ensure_runs_dir(RUNS_DIR)
           run_dir = create_run_dir(runs_dir, slug)
       except RunDirError as e:
           _err(str(e))
           return 2

       transcript_path = run_dir / "transcript.jsonl"
       print(f"transcript: {transcript_path}", file=sys.stderr)
       print(f"worktree:   {worktree}", file=sys.stderr)
   ```

   `run_dir` is now a local variable; task 10 will use it to add `run_dir`
   to the stdout JSON payload. No further edit to this block is needed
   later.

10. Run the full suite.

    ```
    python3 -m pytest -q
    ```

    Expected: all pass. (`RUNS_DIR` is still importable as
    `dirtywork.__main__.RUNS_DIR` for existing test monkeypatches — it is
    imported from `rundir` under the same name, matching the module-global
    lookup pattern `monkeypatch.setattr(m, "RUNS_DIR", ...)` already used
    throughout `tests/test_main.py`.)

11. Commit.

    ```
    git add dirtywork/rundir.py tests/test_rundir.py dirtywork/transcript.py \
        tests/test_transcript.py dirtywork/__main__.py
    git commit -m "feat: validate ~/.dirtywork run directories and open the transcript O_EXCL|O_NOFOLLOW"
    ```

---

## Task 7: tools — bounded writes/listings, regular-file-only opens (rows 8, 9)

### Files

- Modify: `dirtywork/tools.py` (`_open_regular`, `MAX_WRITE_BYTES`,
  `MAX_LIST_ENTRIES`, `read_file`, `write_file`, `edit_file`, `list_dir`;
  remove `_guard_readable`)
- Modify: `tests/test_tools_files.py` (new tests, one edited test)

### Interfaces

Consumes: `os.open`, `os.fstat`, `os.set_blocking`, `stat.S_ISREG`, `errno`
(stdlib).
Produces:
- `MAX_WRITE_BYTES = 5 * 1024 * 1024`
- `MAX_LIST_ENTRIES = 2000`
- `_open_regular(path: Path, flags: int, *, mode: int = 0o644, max_size:
  int | None = None)` → an fd-backed binary file object; raises `OSError` on
  any non-regular-file target, oversized read target, or (with the caller's
  chosen flags) a symlink at the final path component.
- `read_file`, `write_file`, `edit_file`, `list_dir` — same signatures,
  hardened internals.

### Steps

1. Write the failing tests. Add to `tests/test_tools_files.py`, after the
   last existing test (`test_edit_file_refuses_oversized`):

   ```python
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
   ```

2. Run the new tests and confirm they fail.

   ```
   python3 -m pytest tests/test_tools_files.py -q -k "fifo or symlink_final or oversized_content or truncates_at_max_entries"
   ```

   Expected failures: the FIFO tests currently pass for `read_file` but
   `edit_file`/`write_file` don't yet call any FIFO guard at all (their
   current code paths would attempt a real read/write against the FIFO —
   for `edit_file` this means `p.read_text()` on a FIFO with no writer,
   which **hangs**; the `_hang_guard` context manager is exactly why this
   test is written with a guard from the start rather than added later).
   The symlink tests fail with `Failed: DID NOT RAISE`-shaped assertions
   (current `write_file`/`edit_file` write straight through the symlink to
   `real.txt`/`real2.txt`, so `target.read_text()` comes back changed and
   there's no `"ERROR:"` output). `test_write_file_refuses_oversized_content`
   fails — `tools.MAX_WRITE_BYTES` doesn't exist yet (`AttributeError`).
   `test_list_dir_truncates_at_max_entries` fails —
   `tools.MAX_LIST_ENTRIES` doesn't exist yet.

3. Implement `_open_regular` and the constants. Replace the exact current
   top of `dirtywork/tools.py` (lines 1–39, module docstring/imports through
   the end of `_guard_readable`):
   ```python
   from __future__ import annotations

   import os
   import shutil
   import signal
   import stat
   import subprocess
   import threading
   import time
   from pathlib import Path

   from .guardrails import GuardrailError, build_env, check_bash_command, resolve_in_worktree

   MAX_RESULT_CHARS = 8000
   # Refuse to load a file larger than this into memory. read_file/edit_file read
   # the whole file (offset/limit only window the result), so an unbounded read is a
   # memory-DoS; a non-regular file (FIFO/device) would also block read_text forever.
   MAX_READ_BYTES = 5 * 1024 * 1024


   def _cap(text: str, cap: int = MAX_RESULT_CHARS, note: str = "") -> str:
       if len(text) <= cap:
           return text
       suffix = f"\n[output truncated at {cap} chars{note}]"
       return text[:cap] + suffix


   def _guard_readable(p: Path, path: str) -> str | None:
       """ERROR string if p is not a bounded, regular file, else None."""
       try:
           st = p.stat()
       except OSError as e:
           return f"ERROR: cannot read '{path}': {e}"
       if not stat.S_ISREG(st.st_mode):
           return f"ERROR: '{path}' is not a regular file (refusing FIFO/device/socket)"
       if st.st_size > MAX_READ_BYTES:
           return (f"ERROR: '{path}' is {st.st_size} bytes, over the {MAX_READ_BYTES}-byte "
                   f"read limit; use grep to search it instead of reading it whole")
       return None
   ```
   With:
   ```python
   from __future__ import annotations

   import errno
   import os
   import shutil
   import signal
   import stat
   import subprocess
   import threading
   import time
   from pathlib import Path

   from .guardrails import GuardrailError, build_env, check_bash_command, resolve_in_worktree

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
       O_NOFOLLOW. Using this unresolved join instead lets O_NOFOLLOW see and
       refuse a real symlink at the final path component.
       """
       wt = worktree.resolve()
       raw = Path(path_str)
       return raw if raw.is_absolute() else wt / raw
   ```

4. Run tests to confirm `_open_regular` alone doesn't yet fix anything
   (`read_file`/`write_file`/`edit_file`/`list_dir` don't call it yet).

   ```
   python3 -m pytest tests/test_tools_files.py -q
   ```

   Expected: same failures as step 2 (the new helpers exist but aren't
   wired in yet).

5. Wire `_open_regular` into `read_file`. Replace the exact current
   function:
   ```python
   def read_file(worktree: Path, path: str, offset: int = 0, limit: int = 400) -> str:
       try:
           p = resolve_in_worktree(path, worktree)
       except GuardrailError as e:
           return f"ERROR: {e}"
       err = _guard_readable(p, path)
       if err:
           return err
       try:
           lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
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
   ```
   With:
   ```python
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
   ```

6. Wire `_open_regular`/`_worktree_candidate` into `write_file`. Replace the
   exact current function:
   ```python
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
   ```
   With:
   ```python
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
   ```

7. Wire `_open_regular`/`_worktree_candidate` into `edit_file`. Replace the
   exact current function:
   ```python
   def edit_file(worktree: Path, path: str, old_string: str, new_string: str) -> str:
       try:
           p = resolve_in_worktree(path, worktree, writing=True)
       except GuardrailError as e:
           return f"ERROR: {e}"
       err = _guard_readable(p, path)
       if err:
           return err
       try:
           text = p.read_text(encoding="utf-8")
       except UnicodeDecodeError:
           return f"ERROR: {path} is not valid UTF-8 text; edit_file only works on text files"
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
   ```
   With:
   ```python
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
   ```

8. Add entry-count truncation to `list_dir`. Replace the exact current
   function:
   ```python
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
   ```
   With:
   ```python
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
   ```

9. Run the tools-files tests, then the full suite.

   ```
   python3 -m pytest tests/test_tools_files.py -q
   python3 -m pytest -q
   ```

   Expected: all pass, including the pre-existing
   `test_read_file_refuses_fifo` (unaffected: `_open_regular` opening a FIFO
   for `O_RDONLY` with `O_NONBLOCK` returns immediately, then the `S_ISREG`
   check refuses it with the same `"not a regular file"` wording as before)
   and `test_read_file_refuses_oversized`/`test_edit_file_refuses_oversized`
   (the `over the ... read limit` wording is preserved verbatim in
   `_open_regular`'s size-check message).

10. Commit.

    ```
    git add dirtywork/tools.py tests/test_tools_files.py
    git commit -m "fix: refuse non-regular-file opens and writes through symlinks; bound write/list sizes"
    ```

---

## Task 8: budget walker (row 10)

### Files

- Create: `dirtywork/budget.py`
- Create: `tests/test_budget.py`

### Interfaces

Consumes: `os.fwalk`, `os.stat(dir_fd=...)`, `os.readlink(dir_fd=...)`
(stdlib, POSIX).
Produces:
- `DEFAULT_MAX_WORKTREE_MB = 2048`
- `DEFAULT_MAX_WORKTREE_FILES = 200_000`
- `class BudgetExceeded(Exception)` with `.reason: str`
- `@dataclass class BudgetReport: bytes: int; files: int;
  escaping_symlinks: list; violation: str | None`
- `measure_worktree(worktree: Path, *, max_bytes: int, max_files: int) ->
  BudgetReport`

### Steps

1. Write the failing tests. Create `tests/test_budget.py`:

   ```python
   from __future__ import annotations

   import os
   from pathlib import Path

   import pytest

   from dirtywork.budget import DEFAULT_MAX_WORKTREE_FILES, DEFAULT_MAX_WORKTREE_MB, measure_worktree


   @pytest.fixture()
   def wt(tmp_path: Path) -> Path:
       d = tmp_path / "wt"
       d.mkdir()
       return d


   def test_measure_small_tree(wt: Path):
       (wt / "a.txt").write_text("hello")
       sub = wt / "sub"
       sub.mkdir()
       (sub / "b.txt").write_text("world")
       report = measure_worktree(wt, max_bytes=10 * 1024 * 1024, max_files=1000)
       assert report.violation is None
       assert report.files == 3  # a.txt, sub/, sub/b.txt
       assert report.bytes > 0
       assert report.escaping_symlinks == []


   def test_measure_over_bytes_violation(wt: Path):
       (wt / "big.bin").write_bytes(b"x" * (2 * 1024 * 1024))
       report = measure_worktree(wt, max_bytes=1024 * 1024, max_files=1000)
       assert report.violation is not None
       assert "MB" in report.violation


   def test_measure_over_files_violation(wt: Path):
       for i in range(5):
           (wt / f"f{i}.txt").write_text("x")
       report = measure_worktree(wt, max_bytes=10 * 1024 * 1024, max_files=3)
       assert report.violation is not None
       assert "entries" in report.violation


   def test_measure_reports_absolute_escaping_symlink(wt: Path):
       outside = wt.parent / "outside.txt"
       outside.write_text("secret")
       (wt / "esc.txt").symlink_to(outside)
       report = measure_worktree(wt, max_bytes=10 * 1024 * 1024, max_files=1000)
       assert "esc.txt" in report.escaping_symlinks


   def test_measure_reports_relative_escaping_symlink(wt: Path):
       outside_dir = wt.parent / "outside_dir"
       outside_dir.mkdir()
       (outside_dir / "x").write_text("secret")
       (wt / "esc_rel.txt").symlink_to(Path("../outside_dir/x"))
       report = measure_worktree(wt, max_bytes=10 * 1024 * 1024, max_files=1000)
       assert "esc_rel.txt" in report.escaping_symlinks


   def test_measure_does_not_report_internal_symlink(wt: Path):
       # An ABSOLUTE target is always reported regardless of what it points at
       # (matching the SP2 export validator's rule) — so this uses a RELATIVE
       # target that stays inside the worktree to exercise the "not escaping"
       # branch specifically.
       (wt / "real.txt").write_text("hi")
       (wt / "link.txt").symlink_to(Path("real.txt"))
       report = measure_worktree(wt, max_bytes=10 * 1024 * 1024, max_files=1000)
       assert report.escaping_symlinks == []


   @pytest.mark.skipif(os.getuid() == 0, reason="root ignores directory permissions")
   def test_measure_unreadable_dir_is_violation(wt: Path):
       locked = wt / "locked"
       locked.mkdir()
       (locked / "secret.txt").write_text("x")
       os.chmod(locked, 0o000)
       try:
           report = measure_worktree(wt, max_bytes=10 * 1024 * 1024, max_files=1000)
           assert report.violation is not None
           assert "unreadable directory" in report.violation
       finally:
           os.chmod(locked, 0o755)


   def test_measure_does_not_descend_into_symlinked_dir(wt: Path):
       outside_dir = wt.parent / "big_outside"
       outside_dir.mkdir()
       for i in range(20):
           (outside_dir / f"f{i}.txt").write_bytes(b"x" * 1000)
       (wt / "link_dir").symlink_to(outside_dir)
       report = measure_worktree(wt, max_bytes=10 * 1024 * 1024, max_files=1000)
       assert report.files == 1  # only the symlink itself, not its 20 targets
       assert "link_dir" in report.escaping_symlinks


   def test_default_constants():
       assert DEFAULT_MAX_WORKTREE_MB == 2048
       assert DEFAULT_MAX_WORKTREE_FILES == 200_000
   ```

2. Run the new tests and confirm they fail with `ModuleNotFoundError: No
   module named 'dirtywork.budget'`.

   ```
   python3 -m pytest tests/test_budget.py -q
   ```

3. Implement `dirtywork/budget.py`:

   ```python
   from __future__ import annotations

   import os
   import stat
   from dataclasses import dataclass
   from pathlib import Path

   DEFAULT_MAX_WORKTREE_MB = 2048
   DEFAULT_MAX_WORKTREE_FILES = 200_000


   class BudgetExceeded(Exception):
       """Raised when a worktree exceeds a configured disk/file-count budget."""

       def __init__(self, reason: str):
           super().__init__(reason)
           self.reason = reason


   @dataclass
   class BudgetReport:
       bytes: int
       files: int
       escaping_symlinks: list
       violation: str | None


   class _UnreadableDir(Exception):
       def __init__(self, path: str):
           self.path = path


   def _is_escaping(dirpath: str, target: str, root: str) -> bool:
       """A symlink target counts as escaping if it is absolute (regardless of
       where it actually points — matching the SP2 export validator's rule) or,
       for a relative target, if normalizing it against the symlink's own
       directory lands outside `root`."""
       if os.path.isabs(target):
           return True
       candidate = os.path.normpath(os.path.join(dirpath, target))
       root_norm = os.path.normpath(root)
       return not (candidate == root_norm or candidate.startswith(root_norm + os.sep))


   def _measure_posix(worktree: Path, max_bytes: int, max_files: int) -> BudgetReport:
       root = str(worktree)
       total_bytes = 0
       total_files = 0
       escaping: list = []

       def _onerror(err: OSError) -> None:
           raise _UnreadableDir(err.filename or str(err))

       try:
           for dirpath, dirnames, filenames, dirfd in os.fwalk(
               root, onerror=_onerror, follow_symlinks=False
           ):
               for name in dirnames + filenames:
                   try:
                       st = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
                   except OSError as e:
                       raise _UnreadableDir(e.filename or str(e))
                   total_files += 1
                   total_bytes += (
                       st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size
                   )
                   if stat.S_ISLNK(st.st_mode):
                       target = os.readlink(name, dir_fd=dirfd)
                       if _is_escaping(dirpath, target, root):
                           rel = os.path.relpath(os.path.join(dirpath, name), root)
                           escaping.append(rel)
                   if total_bytes > max_bytes:
                       return BudgetReport(
                           total_bytes, total_files, escaping,
                           f"worktree exceeds {max_bytes // (1024 * 1024)} MB",
                       )
                   if total_files > max_files:
                       return BudgetReport(
                           total_bytes, total_files, escaping,
                           f"worktree exceeds {max_files} entries",
                       )
       except _UnreadableDir as e:
           return BudgetReport(total_bytes, total_files, escaping,
                                f"unreadable directory: {e.path}")

       return BudgetReport(total_bytes, total_files, escaping, None)


   def _measure_windows(worktree: Path, max_bytes: int, max_files: int) -> BudgetReport:
       # Best-effort; not exercised by this (POSIX-developed) test suite. `\\?\`
       # -prefixed paths avoid MAX_PATH limits; FILE_ATTRIBUTE_REPARSE_POINT
       # entries (symlinks and junctions) are counted but not descended into,
       # matching follow_symlinks=False on POSIX. Escaping-target detection is
       # skipped — reading a Windows reparse point's target needs the reparse
       # API, which this stdlib-only runtime does not attempt here; the export
       # validator (SP2, POSIX today) is the enforced boundary either way.
       import ctypes

       FILE_ATTRIBUTE_REPARSE_POINT = 0x400
       resolved_root = str(worktree.resolve())
       walk_root = resolved_root if resolved_root.startswith("\\\\?\\") else "\\\\?\\" + resolved_root
       total_bytes = 0
       total_files = 0
       escaping: list = []

       for dirpath, dirnames, filenames in os.walk(walk_root):
           reparse_dirs = []
           for name in dirnames:
               full = os.path.join(dirpath, name)
               attrs = ctypes.windll.kernel32.GetFileAttributesW(full)
               if attrs != -1 and attrs & FILE_ATTRIBUTE_REPARSE_POINT:
                   reparse_dirs.append(name)
           for name in reparse_dirs:
               dirnames.remove(name)  # do not descend into reparse points
           for name in dirnames + filenames + reparse_dirs:
               full = os.path.join(dirpath, name)
               try:
                   st = os.stat(full)
               except OSError as e:
                   return BudgetReport(
                       total_bytes, total_files, escaping,
                       f"unreadable directory: {e.filename or e}",
                   )
               total_files += 1
               total_bytes += st.st_size
               if total_bytes > max_bytes:
                   return BudgetReport(
                       total_bytes, total_files, escaping,
                       f"worktree exceeds {max_bytes // (1024 * 1024)} MB",
                   )
               if total_files > max_files:
                   return BudgetReport(
                       total_bytes, total_files, escaping,
                       f"worktree exceeds {max_files} entries",
                   )
       return BudgetReport(total_bytes, total_files, escaping, None)


   def measure_worktree(worktree: Path, *, max_bytes: int, max_files: int) -> BudgetReport:
       if os.name == "nt":
           return _measure_windows(worktree, max_bytes, max_files)
       return _measure_posix(worktree, max_bytes, max_files)
   ```

4. Run the budget tests, then the full suite.

   ```
   python3 -m pytest tests/test_budget.py -q
   python3 -m pytest -q
   ```

   Expected: all pass.

5. Commit.

   ```
   git add dirtywork/budget.py tests/test_budget.py
   git commit -m "feat: add worktree disk/file-count budget walker"
   ```

---

## Task 9: runner — budget status, finalize hook, assistant-text cap (rows 7, 8, 10)

### Files

- Modify: `dirtywork/runner.py` (`RunResult`, `Runner.__init__`,
  `Runner.run`)
- Modify: `dirtywork/tools.py` (`ToolExecutor.__init__`, `ToolExecutor.execute`)
- Modify: `tests/test_runner.py` (new tests)
- Modify: `tests/test_tools_bash.py` (new test)

### Interfaces

Consumes: `dirtywork.budget.{BudgetExceeded, DEFAULT_MAX_WORKTREE_FILES,
DEFAULT_MAX_WORKTREE_MB, measure_worktree}` (task 8).
Produces:
- `MAX_ASSISTANT_TEXT_CHARS = 64_000`
- `RunResult` gains `extra: dict = field(default_factory=dict)`
- `Runner.__init__(..., finalize: Callable[[], dict] | None = None)`
- New status `budget_exceeded`
- `ToolExecutor.__init__(self, worktree, transcript=None, *,
  max_worktree_mb=DEFAULT_MAX_WORKTREE_MB,
  max_worktree_files=DEFAULT_MAX_WORKTREE_FILES)`; `execute(name, args)`
  unchanged return type, now raises `BudgetExceeded` after any call that
  leaves the worktree over budget.

### Steps

1. Write the failing tests for `ToolExecutor`'s budget check. Add to
   `tests/test_tools_bash.py`, after `test_bash_timeout_reaps_process_tree`
   (the last test in the file):

   ```python
   def test_executor_raises_budget_exceeded_over_file_limit(wt: Path):
       from dirtywork.budget import BudgetExceeded
       ex = ToolExecutor(wt, max_worktree_files=3)
       # wt already has 1 entry (hello.txt from the fixture). Each write adds
       # one more; the check runs AFTER the write, so it must succeed through
       # exactly 3 total entries and only raise once a 4th is created.
       ex.execute("write_file", {"path": "a.txt", "content": "x"})
       ex.execute("write_file", {"path": "b.txt", "content": "x"})
       with pytest.raises(BudgetExceeded):
           ex.execute("write_file", {"path": "c.txt", "content": "x"})
   ```

2. Run it and confirm it fails.

   ```
   python3 -m pytest tests/test_tools_bash.py -q -k budget_exceeded_over_file_limit
   ```

   Expected failure: `ToolExecutor(wt, max_worktree_files=3)` raises
   `TypeError: __init__() got an unexpected keyword argument
   'max_worktree_files'`.

3. Implement the `ToolExecutor` budget wiring in `dirtywork/tools.py`.
   Replace the exact current `ToolExecutor` class (the last class in the
   file):
   ```python
   class ToolExecutor:
       """Dispatches validated tool calls. Unknown names raise KeyError."""

       def __init__(self, worktree: Path, transcript=None):
           self.worktree = worktree
           self.transcript = transcript
           self.deadline = None
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
           if self.deadline is not None:
               remaining = self.deadline - time.monotonic()
               if remaining <= 0:
                   return ("ERROR: run deadline exceeded; stop calling tools and "
                           "summarize what you have done.")
               if name in ("bash", "grep"):
                   args = dict(args)
                   default = 120 if name == "bash" else 30
                   args["timeout"] = min(int(args.get("timeout", default)), max(1, int(remaining)))
           result = fn(self.worktree, **args)
           if result.startswith("BLOCKED:") and self.transcript is not None:
               self.transcript.write("guardrail_block", tool=name, args=args, reason=result)
           return result
   ```
   With:
   ```python
   class ToolExecutor:
       """Dispatches validated tool calls. Unknown names raise KeyError."""

       def __init__(self, worktree: Path, transcript=None, *,
                    max_worktree_mb: int = DEFAULT_MAX_WORKTREE_MB,
                    max_worktree_files: int = DEFAULT_MAX_WORKTREE_FILES):
           self.worktree = worktree
           self.transcript = transcript
           self.deadline = None
           self.max_worktree_bytes = max_worktree_mb * 1024 * 1024
           self.max_worktree_files = max_worktree_files
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
           if self.deadline is not None:
               remaining = self.deadline - time.monotonic()
               if remaining <= 0:
                   return ("ERROR: run deadline exceeded; stop calling tools and "
                           "summarize what you have done.")
               if name in ("bash", "grep"):
                   args = dict(args)
                   default = 120 if name == "bash" else 30
                   args["timeout"] = min(int(args.get("timeout", default)), max(1, int(remaining)))
           result = fn(self.worktree, **args)
           if result.startswith("BLOCKED:") and self.transcript is not None:
               self.transcript.write("guardrail_block", tool=name, args=args, reason=result)
           report = measure_worktree(self.worktree, max_bytes=self.max_worktree_bytes,
                                     max_files=self.max_worktree_files)
           if report.violation:
               raise BudgetExceeded(report.violation)
           return result
   ```

   Then add the import. Replace the exact current import line in
   `dirtywork/tools.py`:
   ```python
   from .guardrails import GuardrailError, build_env, check_bash_command, resolve_in_worktree
   ```
   With:
   ```python
   from .budget import BudgetExceeded, DEFAULT_MAX_WORKTREE_FILES, DEFAULT_MAX_WORKTREE_MB, measure_worktree
   from .guardrails import GuardrailError, build_env, check_bash_command, resolve_in_worktree
   ```

4. Run the tools-bash test, then confirm the whole file still passes.

   ```
   python3 -m pytest tests/test_tools_bash.py -q
   ```

   Expected: all pass. (The pre-existing tests all operate on tiny `tmp_path`
   trees, well under the 2048 MB / 200 000-file defaults, so `measure_worktree`
   never raises for them.)

5. Write the failing tests for the `Runner` changes. Add to
   `tests/test_runner.py`, after `test_usage_ignores_non_finite_from_server`
   (the last test in the file):

   ```python
   def test_finalize_merges_into_run_end_and_result_extra(parts):
       wt, executor, transcript, tmp = parts
       client = FakeClient([_resp(content="done")])
       r = Runner(client, executor, transcript, model="m",
                 finalize=lambda: {"diff_stat": " 1 file changed"})
       result = r.run("s", "t")
       transcript.close()
       assert result.extra == {"diff_stat": " 1 file changed"}
       events = _events(tmp)
       run_end = next(e for e in events if e["event"] == "run_end")
       assert run_end["diff_stat"] == " 1 file changed"


   def test_finalize_exception_recorded_status_preserved(parts):
       wt, executor, transcript, tmp = parts
       client = FakeClient([_resp(content="done")])

       def boom():
           raise RuntimeError("disk gone")

       r = Runner(client, executor, transcript, model="m", finalize=boom)
       result = r.run("s", "t")
       transcript.close()
       assert result.status == "completed"
       events = _events(tmp)
       run_end = next(e for e in events if e["event"] == "run_end")
       assert "disk gone" in run_end["finalize_error"]


   def test_assistant_text_capped_in_transcript_full_text_resent(parts):
       wt, executor, transcript, tmp = parts
       # Over MAX_ASSISTANT_TEXT_CHARS (64_000) but comfortably under the
       # default model's char_budget (~98_304 for the fallback DEFAULT_WINDOW),
       # so trim_messages doesn't ALSO trigger context_exhausted — this test is
       # about the transcript-only cap, not the trim path.
       huge_text = "y" * 70_000
       client = FakeClient([
           _resp(tool_calls=[_call("c1", "list_dir", {"path": "."})], content=huge_text),
           _resp(content="done"),
       ])
       r = Runner(client, executor, transcript, model="m")
       result = r.run("s", "t")
       transcript.close()
       assert result.status == "completed"

       events = _events(tmp)
       assistant_event = next(e for e in events if e["event"] == "assistant")
       assert len(assistant_event["text"]) < 70_000
       assert "truncated" in assistant_event["text"]

       # the resent history to the model must keep the FULL text
       second = client.requests[1]
       assistant_msg = next(m for m in second if m["role"] == "assistant" and m.get("tool_calls"))
       assert assistant_msg["content"] == huge_text


   def test_budget_exceeded_from_executor_ends_run(parts):
       wt, executor, transcript, tmp = parts
       from dirtywork.budget import BudgetExceeded

       class BudgetBustingExecutor:
           def execute(self, name, args):
               raise BudgetExceeded("worktree exceeds 2048 MB")

       client = FakeClient([_resp(tool_calls=[_call("c1", "write_file", {"path": "x", "content": "y"})])])
       r = Runner(client, BudgetBustingExecutor(), transcript, model="m")
       result = r.run("s", "t")
       transcript.close()
       assert result.status == "budget_exceeded"
       assert "2048 MB" in result.final_message
       events = _events(tmp)
       run_end = next(e for e in events if e["event"] == "run_end")
       assert run_end["status"] == "budget_exceeded"
   ```

6. Run the new tests and confirm they fail.

   ```
   python3 -m pytest tests/test_runner.py -q -k "finalize or assistant_text_capped or budget_exceeded_from_executor"
   ```

   Expected failures: `TypeError: __init__() got an unexpected keyword
   argument 'finalize'` for the two finalize tests; the assistant-text test
   fails because `assistant_event["text"]` is the full 70 000-char string
   (no truncation exists yet) so `len(...) < 70_000` is false; the
   budget-exceeded test fails because `Runner.run` doesn't catch
   `BudgetExceeded` today — it propagates up out of `r.run("s", "t")`,
   failing the test with an unhandled exception instead of returning a
   `RunResult`.

7. Implement the `Runner` changes in `dirtywork/runner.py`.

   Replace the exact current import block (lines 1–9):
   ```python
   from __future__ import annotations

   import json
   import math
   import time
   from dataclasses import dataclass, field

   from .llm import LLMTimeout
   ```
   With:
   ```python
   from __future__ import annotations

   import json
   import math
   import time
   from dataclasses import dataclass, field
   from typing import Callable

   from .budget import BudgetExceeded
   from .llm import LLMTimeout

   MAX_ASSISTANT_TEXT_CHARS = 64_000
   ```

   Replace the exact current `RunResult` dataclass:
   ```python
   @dataclass
   class RunResult:
       status: str
       turns: int
       final_message: str
       usage: dict = field(default_factory=dict)
   ```
   With:
   ```python
   @dataclass
   class RunResult:
       status: str
       turns: int
       final_message: str
       usage: dict = field(default_factory=dict)
       extra: dict = field(default_factory=dict)
   ```

   Replace the exact current `Runner.__init__`:
   ```python
       def __init__(self, client, executor, transcript, model,
                    max_turns: int = 40, timeout: int = 1800,
                    temperature: float | None = None,
                    run_info: dict | None = None):
           self.client = client
           self.executor = executor
           self.transcript = transcript
           self.model = model
           self.max_turns = max_turns
           self.timeout = timeout
           self.temperature = temperature
           self.run_info = run_info
           window = CONTEXT_WINDOWS.get(model, DEFAULT_WINDOW)
           self.char_budget = int(window * BUDGET_FRACTION * CHARS_PER_TOKEN)
   ```
   With:
   ```python
       def __init__(self, client, executor, transcript, model,
                    max_turns: int = 40, timeout: int = 1800,
                    temperature: float | None = None,
                    run_info: dict | None = None,
                    finalize: Callable[[], dict] | None = None):
           self.client = client
           self.executor = executor
           self.transcript = transcript
           self.model = model
           self.max_turns = max_turns
           self.timeout = timeout
           self.temperature = temperature
           self.run_info = run_info
           self.finalize = finalize
           window = CONTEXT_WINDOWS.get(model, DEFAULT_WINDOW)
           self.char_budget = int(window * BUDGET_FRACTION * CHARS_PER_TOKEN)
   ```

   Replace the exact current `finish` closure inside `run`:
   ```python
           def finish(status: str, final: str) -> RunResult:
               self.transcript.write("run_end", status=status, turns=turns,
                                     duration_s=round(time.monotonic() - start, 1),
                                     usage=usage)
               return RunResult(status, turns, final, usage)
   ```
   With:
   ```python
           def finish(status: str, final: str) -> RunResult:
               extra: dict = {}
               finalize_error = None
               if self.finalize is not None:
                   try:
                       finalize_result = self.finalize()
                       if isinstance(finalize_result, dict):
                           extra.update(finalize_result)
                   except Exception as e:
                       finalize_error = f"{type(e).__name__}: {e}"
               if finalize_error is not None:
                   extra["finalize_error"] = finalize_error
               self.transcript.write("run_end", status=status, turns=turns,
                                     duration_s=round(time.monotonic() - start, 1),
                                     usage=usage, **extra)
               return RunResult(status, turns, final, usage, extra=extra)
   ```

   Replace the exact current assistant-transcript write:
   ```python
                   self.transcript.write(
                       "assistant", text=msg.get("content"),
                       tool_calls=[{"name": (tc.get("function") or {}).get("name"),
                                    "arguments": ((tc.get("function") or {}).get("arguments") or "")[:2000]}
                                   for tc in tool_calls])
   ```
   With:
   ```python
                   transcript_text = msg.get("content")
                   if isinstance(transcript_text, str) and len(transcript_text) > MAX_ASSISTANT_TEXT_CHARS:
                       transcript_text = (
                           transcript_text[:MAX_ASSISTANT_TEXT_CHARS]
                           + f"\n[truncated at {MAX_ASSISTANT_TEXT_CHARS} chars in the transcript "
                             f"only — the full text was sent to the model]"
                       )
                   self.transcript.write(
                       "assistant", text=transcript_text,
                       tool_calls=[{"name": (tc.get("function") or {}).get("name"),
                                    "arguments": ((tc.get("function") or {}).get("arguments") or "")[:2000]}
                                   for tc in tool_calls])
   ```

   Replace the exact current start of the per-tool-call try block:
   ```python
                   try:
                       args = json.loads(raw_args)
                       if not isinstance(args, dict):
                           raise ValueError("arguments must be a JSON object")
                       result = self.executor.execute(name, args)
                       failures = 0
                   except (json.JSONDecodeError, ValueError) as e:
   ```
   With:
   ```python
                   try:
                       args = json.loads(raw_args)
                       if not isinstance(args, dict):
                           raise ValueError("arguments must be a JSON object")
                       result = self.executor.execute(name, args)
                       failures = 0
                   except BudgetExceeded as e:
                       return finish("budget_exceeded", e.reason)
                   except (json.JSONDecodeError, ValueError) as e:
   ```

8. Run the runner tests, then the full suite.

   ```
   python3 -m pytest tests/test_runner.py -q
   python3 -m pytest -q
   ```

   Expected: all pass.

9. Commit.

   ```
   git add dirtywork/runner.py dirtywork/tools.py tests/test_runner.py tests/test_tools_bash.py
   git commit -m "feat: runner catches BudgetExceeded, gains a finalize hook, caps assistant text in the transcript"
   ```

---

## Task 10: `__main__` wiring — provenance, budget flags, `host_diff_stat` (row 7 + flags)

### Files

- Modify: `dirtywork/workspace.py` (add `host_diff_stat`)
- Modify: `tests/test_workspace.py` (new tests)
- Modify: `dirtywork/__main__.py` (flags, provenance, finalize wiring,
  stdout JSON)
- Modify: `tests/test_main.py` (new tests)

### Interfaces

Consumes: `dirtywork.rundir.{RUNS_DIR, RunDirError, create_run_dir,
ensure_runs_dir}` (task 6), `dirtywork.budget.{DEFAULT_MAX_WORKTREE_FILES,
DEFAULT_MAX_WORKTREE_MB}` (task 8), `dirtywork.workspace.worktree_base_commit`
(task 2), `dirtywork.__init__.__version__`.
Produces: `host_diff_stat(worktree: Path, cap: int = 64_000) -> str`; CLI
flags `--max-worktree-mb` (default 2048), `--max-worktree-files` (default
200000); `run_start` transcript event gains `base_commit, branch,
branch_from, base_url, dirtywork_version, temperature, sandbox, provider`;
`run_end` gains `diff_stat` (via `finalize`); stdout JSON gains `run_dir`,
`base_commit` (additive — no existing key removed or renamed).

### Steps

1. Write the failing tests for `host_diff_stat`. Add to
   `tests/test_workspace.py`, after `test_worktree_base_commit`:

   ```python
   def test_host_diff_stat_reports_tracked_changes(repo: Path):
       wt = create_worktree(repo, "diff-08141109", None)
       (wt / "f.txt").write_text("changed content")
       out = host_diff_stat(wt)
       assert "f.txt" in out


   def test_host_diff_stat_no_changes_is_empty(repo: Path):
       wt = create_worktree(repo, "nodiff-08141109", None)
       out = host_diff_stat(wt)
       assert out.strip() == ""


   def test_host_diff_stat_ignores_untracked_new_files(repo: Path):
       # git diff --stat only reports TRACKED changes — a brand-new file that
       # was never `git add`ed does not appear. This is a documented limitation
       # of host-mode diff_stat, not a bug.
       wt = create_worktree(repo, "untracked-08141109", None)
       (wt / "new.txt").write_text("hello")
       out = host_diff_stat(wt)
       assert "new.txt" not in out


   def test_host_diff_stat_truncates(repo: Path):
       # git diff --stat only reports TRACKED changes (see the untracked-files
       # test above) — commit the files first, then modify them, so there is
       # real tracked diff output to truncate.
       for i in range(50):
           (repo / f"file{i}.txt").write_text("x\n")
       _git(repo, "add", ".")
       _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "add files")
       wt = create_worktree(repo, "trunc-08141109", None)
       for i in range(50):
           (wt / f"file{i}.txt").write_text("changed content\n" * 5)
       out = host_diff_stat(wt, cap=200)
       assert len(out) <= 200 + len("\n[truncated at 200 chars]")
       assert "truncated at 200 chars" in out
   ```

   Also add `host_diff_stat` to the existing import block at the top of
   `tests/test_workspace.py`. Replace the exact current text:
   ```python
   from dirtywork.workspace import (
       WorkspaceError,
       create_worktree,
       ensure_worktrees_excluded,
       load_repo_context,
       make_slug,
       preflight_repo,
   )
   ```
   With:
   ```python
   from dirtywork.workspace import (
       WorkspaceError,
       create_worktree,
       ensure_worktrees_excluded,
       host_diff_stat,
       load_repo_context,
       make_slug,
       preflight_repo,
       worktree_base_commit,
   )
   ```

2. Run the new tests and confirm they fail with
   `ImportError: cannot import name 'host_diff_stat'`.

   ```
   python3 -m pytest tests/test_workspace.py -q -k host_diff_stat
   ```

3. Implement `host_diff_stat` in `dirtywork/workspace.py`. Add it at the
   end of the file, after `load_repo_context`:

   ```python
   def host_diff_stat(worktree: Path, cap: int = 64_000) -> str:
       """`git diff --stat` in the worktree, capped. Host mode only — this
       compares the worktree's working tree against its index, so it reports
       TRACKED changes only; a brand-new file the model wrote but never staged
       is invisible here (documented in README.md/SECURITY.md)."""
       res = _git(worktree, "diff", "--stat")
       if res.returncode != 0:
           return f"[diff --stat failed: {res.stderr.strip()[:500]}]"
       out = res.stdout
       if len(out) > cap:
           out = out[:cap] + f"\n[truncated at {cap} chars]"
       return out
   ```

4. Run the workspace tests, then the full suite.

   ```
   python3 -m pytest tests/test_workspace.py -q
   python3 -m pytest -q
   ```

   Expected: all pass.

5. Write the failing tests for `__main__` provenance/flags/diff_stat. Add to
   `tests/test_main.py`, after `test_llm_error_during_run_prints_model_error_json`
   (the last test in the file):

   ```python
   def test_run_start_has_all_provenance_fields(tmp_path, monkeypatch):
       # Runner.run() itself writes the run_start transcript event — replacing
       # Runner.run wholesale (as other tests in this file do to short-circuit
       # the agent loop) would skip that write entirely. Drive a minimal fake
       # LLM client through the REAL Runner.run() instead, so run_start is
       # actually emitted.
       import subprocess
       import dirtywork.__main__ as m
       repo = tmp_path / "r"
       repo.mkdir()
       subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
       subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                       "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                      capture_output=True)
       monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")

       class ImmediateDoneClient:
           def __init__(self, base_url=None):
               pass

           def list_models(self):
               return [m.DEFAULT_MODEL]

           def chat(self, model, messages, tools, temperature=None, max_tokens=4096, timeout=None):
               return {"choices": [{"message": {"role": "assistant", "content": "done"}}],
                       "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

       monkeypatch.setattr(m, "LMStudioClient", ImmediateDoneClient)

       rc = m.main(["run", "--repo", str(repo), "some task"])
       assert rc == 0

       transcript_files = list((tmp_path / "runs").rglob("transcript.jsonl"))
       events = [json.loads(l) for l in transcript_files[0].read_text().splitlines()]
       run_start = next(e for e in events if e["event"] == "run_start")
       for key in ("base_commit", "branch", "branch_from", "base_url",
                   "dirtywork_version", "temperature", "sandbox", "provider"):
           assert key in run_start, key
       assert run_start["sandbox"] == "none"
       assert run_start["provider"] == "openai"


   def test_run_end_has_diff_stat_after_writing_tracked_file(tmp_path, monkeypatch):
       import subprocess
       import dirtywork.__main__ as m
       repo = tmp_path / "r"
       repo.mkdir()
       subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
       (repo / "existing.txt").write_text("original\n")
       subprocess.run(["git", "-C", str(repo), "add", "existing.txt"], capture_output=True)
       subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                       "-c", "user.name=t", "commit", "-m", "init"],
                      capture_output=True)
       monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")

       class WritingFakeClient:
           def __init__(self, base_url=None):
               self.calls = 0

           def list_models(self):
               return [m.DEFAULT_MODEL]

           def chat(self, model, messages, tools, temperature=None, max_tokens=4096, timeout=None):
               self.calls += 1
               if self.calls == 1:
                   return {"choices": [{"message": {
                       "role": "assistant", "content": None,
                       "tool_calls": [{"id": "c1", "type": "function",
                                        "function": {"name": "write_file",
                                                     "arguments": json.dumps(
                                                         {"path": "existing.txt", "content": "changed\n"})}}],
                   }}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
               return {"choices": [{"message": {"role": "assistant", "content": "done"}}],
                       "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

       monkeypatch.setattr(m, "LMStudioClient", WritingFakeClient)

       rc = m.main(["run", "--repo", str(repo), "some task"])
       assert rc == 0

       transcript_files = list((tmp_path / "runs").rglob("transcript.jsonl"))
       events = [json.loads(l) for l in transcript_files[0].read_text().splitlines()]
       run_end = next(e for e in events if e["event"] == "run_end")
       assert "diff_stat" in run_end
       assert "existing.txt" in run_end["diff_stat"]


   def test_rundir_error_exits_2(tmp_path, monkeypatch):
       import subprocess
       import dirtywork.__main__ as m
       repo = tmp_path / "r"
       repo.mkdir()
       subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
       subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                       "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                      capture_output=True)
       runs_dir = tmp_path / "runs"
       monkeypatch.setattr(m, "RUNS_DIR", runs_dir)
       monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])
       monkeypatch.setattr(m, "make_slug", lambda task, now: "fixed-slug")
       runs_dir.mkdir(parents=True)
       (runs_dir / "fixed-slug").mkdir()  # pre-existing run dir collides

       rc = m.main(["run", "--repo", str(repo), "some task"])
       assert rc == 2


   def test_stdout_json_has_run_dir_and_base_commit(tmp_path, monkeypatch, capsys):
       import subprocess
       import dirtywork.__main__ as m
       from dirtywork.runner import RunResult
       repo = tmp_path / "r"
       repo.mkdir()
       subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
       subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                       "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                      capture_output=True)
       monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
       monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])
       monkeypatch.setattr(m.Runner, "run", lambda self, sp, t: RunResult("completed", 1, "ok", {}))

       rc = m.main(["run", "--repo", str(repo), "some task"])
       assert rc == 0
       payload = json.loads(capsys.readouterr().out)
       assert payload["run_dir"].endswith("run_dir_placeholder") is False  # sanity: it's a real path
       assert "runs" in payload["run_dir"]
       assert payload["base_commit"]
       # existing contract fields must still be present and unrenamed
       for key in ("status", "worktree", "branch", "transcript", "turns", "usage", "final_message"):
           assert key in payload
   ```

6. Run the new tests and confirm they fail.

   ```
   python3 -m pytest tests/test_main.py -q -k "provenance_fields or diff_stat_after_writing or rundir_error_exits_2 or run_dir_and_base_commit"
   ```

   Expected failures: `test_run_start_has_all_provenance_fields` fails —
   `run_start` currently only has `repo`/`worktree` from `run_info`, missing
   the new keys. `test_run_end_has_diff_stat_after_writing_tracked_file`
   fails — no `finalize` is wired, so `run_end` has no `diff_stat` key.
   `test_rundir_error_exits_2` fails — `make_slug` in `__main__` is called
   with `(args.task, datetime.now())` but nothing yet forces a collision
   distinctly identifiable as `RunDirError` vs. some other 2/1/0 exit (this
   currently returns something other than 2, since without the fixed slug
   the random salt makes a real collision astronomically unlikely — the test
   fails because `rc != 2`, most likely `rc == 0`). `test_stdout_json_has_run_dir_and_base_commit`
   fails — `KeyError`-shaped: `payload["run_dir"]` raises `KeyError` since
   the field doesn't exist yet.

7. Implement the full wiring in `dirtywork/__main__.py`.

   Replace the exact current import/constant block (after task 6's edit):
   ```python
   from __future__ import annotations

   import argparse
   import json
   import sys
   from datetime import datetime
   from pathlib import Path

   from .llm import LLMError, LMStudioClient
   from .rundir import RUNS_DIR, RunDirError, create_run_dir, ensure_runs_dir
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
       worktree_base_commit,
   )

   DEFAULT_MODEL = "qwen/qwen3-coder-next"
   ```
   With:
   ```python
   from __future__ import annotations

   import argparse
   import json
   import sys
   from datetime import datetime
   from pathlib import Path

   from . import __version__
   from .budget import DEFAULT_MAX_WORKTREE_FILES, DEFAULT_MAX_WORKTREE_MB
   from .llm import LLMError, LMStudioClient
   from .rundir import RUNS_DIR, RunDirError, create_run_dir, ensure_runs_dir
   from .runner import Runner
   from .tools import ToolExecutor
   from .transcript import Transcript
   from .workspace import (
       WorkspaceError,
       create_worktree,
       ensure_worktrees_excluded,
       host_diff_stat,
       load_repo_context,
       make_slug,
       preflight_repo,
       worktree_base_commit,
   )

   DEFAULT_MODEL = "qwen/qwen3-coder-next"
   ```

   Replace the exact current argument-parsing block:
   ```python
       run_p.add_argument("--base-url", default="http://localhost:1234/v1")
       args = parser.parse_args(argv)
   ```
   With:
   ```python
       run_p.add_argument("--base-url", default="http://localhost:1234/v1")
       run_p.add_argument("--max-worktree-mb", type=int, default=DEFAULT_MAX_WORKTREE_MB)
       run_p.add_argument("--max-worktree-files", type=int, default=DEFAULT_MAX_WORKTREE_FILES)
       args = parser.parse_args(argv)
   ```

   Replace the exact current run block (from `# ---- run ----` through the
   end of the function):
   ```python
       # ---- run ----
       # Everything from here on -- Transcript/ToolExecutor/Runner construction,
       # system-prompt assembly, and the run itself -- is wrapped in one boundary so
       # the machine contract (exactly one JSON object on stdout, post-preflight)
       # holds even if a component other than runner.run() blows up.
       transcript = None
       try:
           transcript = Transcript(transcript_path)
           executor = ToolExecutor(worktree, transcript=transcript)
           runner = Runner(client, executor, transcript, model=args.model,
                           max_turns=args.max_turns, timeout=args.timeout,
                           temperature=args.temperature,
                           run_info={"repo": str(repo), "worktree": str(worktree)})
           system_prompt = build_system_prompt(worktree, load_repo_context(worktree))
           result = runner.run(system_prompt, args.task)
       except Exception as e:
           message = str(e) if isinstance(e, LLMError) else f"unexpected error: {e!r}"
           if transcript is not None:
               try:
                   transcript.write("run_end", status="model_error", error=message)
               except Exception:
                   pass
           _err(message)
           print(json.dumps({
               "status": "model_error",
               "worktree": str(worktree),
               "branch": f"dirtywork/{slug}",
               "transcript": str(transcript_path),
               "turns": None,
               "usage": {},
               "final_message": message,
           }, indent=2))
           return 1
       finally:
           if transcript is not None:
               try:
                   transcript.close()
               except Exception:
                   pass

       print(json.dumps({
           "status": result.status,
           "worktree": str(worktree),
           "branch": f"dirtywork/{slug}",
           "transcript": str(transcript_path),
           "turns": result.turns,
           "usage": result.usage,
           "final_message": result.final_message,
       }, indent=2))
       return 0 if result.status == "completed" else 1
   ```
   With:
   ```python
       # ---- run ----
       # Everything from here on -- Transcript/ToolExecutor/Runner construction,
       # system-prompt assembly, and the run itself -- is wrapped in one boundary so
       # the machine contract (exactly one JSON object on stdout, post-preflight)
       # holds even if a component other than runner.run() blows up.
       transcript = None
       base_commit = None
       try:
           base_commit = worktree_base_commit(worktree)
           transcript = Transcript(transcript_path)
           executor = ToolExecutor(worktree, transcript=transcript,
                                   max_worktree_mb=args.max_worktree_mb,
                                   max_worktree_files=args.max_worktree_files)
           run_info = {
               "repo": str(repo),
               "worktree": str(worktree),
               "base_commit": base_commit,
               "branch": f"dirtywork/{slug}",
               "branch_from": args.branch_from,
               "base_url": args.base_url,
               "dirtywork_version": __version__,
               "temperature": args.temperature,
               "sandbox": "none",
               "provider": "openai",
           }
           runner = Runner(client, executor, transcript, model=args.model,
                           max_turns=args.max_turns, timeout=args.timeout,
                           temperature=args.temperature,
                           run_info=run_info,
                           finalize=lambda: {"diff_stat": host_diff_stat(worktree)})
           system_prompt = build_system_prompt(worktree, load_repo_context(repo, base_commit))
           result = runner.run(system_prompt, args.task)
       except Exception as e:
           message = str(e) if isinstance(e, LLMError) else f"unexpected error: {e!r}"
           if transcript is not None:
               try:
                   transcript.write("run_end", status="model_error", error=message)
               except Exception:
                   pass
           _err(message)
           print(json.dumps({
               "status": "model_error",
               "worktree": str(worktree),
               "branch": f"dirtywork/{slug}",
               "transcript": str(transcript_path),
               "turns": None,
               "usage": {},
               "final_message": message,
               "run_dir": str(run_dir),
               "base_commit": base_commit,
           }, indent=2))
           return 1
       finally:
           if transcript is not None:
               try:
                   transcript.close()
               except Exception:
                   pass

       print(json.dumps({
           "status": result.status,
           "worktree": str(worktree),
           "branch": f"dirtywork/{slug}",
           "transcript": str(transcript_path),
           "turns": result.turns,
           "usage": result.usage,
           "final_message": result.final_message,
           "run_dir": str(run_dir),
           "base_commit": base_commit,
       }, indent=2))
       return 0 if result.status == "completed" else 1
   ```

8. Run the main tests, then the full suite.

   ```
   python3 -m pytest tests/test_main.py -q
   python3 -m pytest -q
   ```

   Expected: all pass.

9. Commit.

   ```
   git add dirtywork/workspace.py tests/test_workspace.py dirtywork/__main__.py tests/test_main.py
   git commit -m "feat: wire run provenance, worktree budget flags, and host diff_stat into __main__"
   ```

---

## Task 11: docs — README.md and SECURITY.md (rows 1, 11; row 13 no-op)

### Files

- Modify: `README.md`
- Modify: `SECURITY.md`

### Interfaces

None — documentation only. No test (docs have no executable assertions);
the step is to run the full suite once more and commit.

### Steps

1. Update README.md's "Security & trust" section. Replace the exact current
   bullet list:
   ```markdown
   - **File tools (`read_file`/`write_file`/`edit_file`/`list_dir`/`grep`) are confined
     to the worktree** by real path resolution — symlinks, `..`, and absolute paths that
     escape are rejected.
   - **`bash` is a general shell, not a sandbox.** A denylist blocks common *accidents*
     (destructive commands aimed outside the worktree, shared-git-state writes, piping a
     download into an interpreter), and `HOME` is redirected into the worktree so `~`/
     `$HOME` can't reach your real `~/.ssh` or `~/.aws`. But a determined or
     prompt-injected model can still read absolute host paths (`cat /etc/…`) — the
     denylist raises the bar for a confused model, it does not stop an adversarial one.
   - **Review is the real boundary.** Read the transcript and diff before you merge. Note
     that `bash` side-effects happen at run time, so review catches what lands in the
     diff, not what a command already did.
   ```
   With:
   ```markdown
   - **File tools (`read_file`/`write_file`/`edit_file`/`list_dir`/`grep`) are confined
     to the worktree** by real path resolution — symlinks, `..`, and absolute paths that
     escape are rejected. Writes additionally refuse to go through a symlink at the
     final path component (even one pointing back inside the worktree) and refuse
     any non-regular-file target (FIFO/device/socket) outright.
   - **`bash` is a general shell, not a sandbox.** A denylist blocks common *accidents*
     (destructive commands aimed outside the worktree, shared-git-state writes, piping a
     download into an interpreter), and `HOME` is redirected into the worktree so `~`/
     `$HOME` can't reach your real `~/.ssh` or `~/.aws`. But a determined or
     prompt-injected model can still read absolute host paths (`cat /etc/…`) — the
     denylist raises the bar for a confused model, it does not stop an adversarial one.
     Concretely, host mode (`--sandbox none`, the only mode this version has) does
     **not** block writes made through an interpreter — e.g.
     `python3 -c "open('/tmp/x','w').write('y')"` succeeds. Enumerating every
     interpreter's write primitive is not a regex-shaped problem; the real fix is a
     process boundary (an OS-level sandbox), tracked as the next release.
   - **`.git/info/exclude` gains a line.** The first run against a repo appends
     `.worktrees/` to the shared repository's `.git/info/exclude` (not tracked, not
     committed, idempotent) so worktree directories don't show up as untracked noise
     in `git status`. This is the only host-side git state a run writes outside its
     own worktree.
   - **Worktree growth is checked after every tool call.** Past `--max-worktree-mb`
     (default 2048) or `--max-worktree-files` (default 200000) the run ends with
     status `budget_exceeded`. This is a best-effort, sampled bound, not a kernel
     quota — see SECURITY.md.
   - **Review is the real boundary.** Read the transcript and diff before you merge. Note
     that `bash` side-effects happen at run time, so review catches what lands in the
     diff, not what a command already did. `git diff --stat` in host mode reports
     **tracked changes only** — a new file the model wrote but never `git add`ed
     won't appear in `diff_stat`; `git status` in the worktree shows it.
   ```

2. Update README.md's "Safety model" bullet list. Replace the exact current
   text:
   ```markdown
   - All file tools are path-confined to the worktree (symlink-safe realpath
     checks; `.git/` is write-protected against hook injection).
   - `bash` runs cwd-pinned in the worktree with a minimal environment (your
     shell's tokens/keys are not inherited) and a regex denylist: `sudo`,
     `git push`, `rm`/`mv`/`chmod`/`chown` on absolute or `~` paths,
     `cd`/`pushd` escapes, downloads piped to a shell, system-control
     commands, redirects outside the worktree.
   - Every denylist rejection is logged to the transcript as a
     `guardrail_block` event, so attempted escapes are visible at review time.
   - Network is allowed (package restores need it); per-command timeout 120s
     default, 600s max.
   ```
   With:
   ```markdown
   - All file tools are path-confined to the worktree (symlink-safe realpath
     checks; `.git/` is write-protected against hook injection).
   - `bash` runs cwd-pinned in the worktree with a minimal environment (your
     shell's tokens/keys are not inherited) and a regex denylist: `sudo`,
     `git push`, `git config`/`remote`/`worktree`/`branch -D`/… that would write
     the parent repo's shared state (including through `git -C`/`git -c`/`--flag`
     global options), `rm`/`mv`/`chmod`/`chown` on absolute or `~` paths,
     `cd`/`pushd` escapes, downloads piped to a shell, system-control
     commands, redirects outside the worktree.
   - Every denylist rejection is logged to the transcript as a
     `guardrail_block` event, so attempted escapes are visible at review time.
   - File tools refuse to operate on anything that isn't a regular file (FIFOs,
     devices, sockets) and refuse to write through a symlink at the final path
     component, even when its target is inside the worktree. `write_file`
     content is capped at 5 MB, `list_dir` output at 2000 entries, and the
     assistant's own text is capped at 64 000 chars in the transcript (the
     full text is still sent to the model).
   - Worktree growth is sampled after every tool call against
     `--max-worktree-mb`/`--max-worktree-files`; past either, the run ends with
     status `budget_exceeded`.
   - Network is allowed (package restores need it); per-command timeout 120s
     default, 600s max.
   ```

3. Update README.md's "Machine contract" section. Replace the exact current
   flags block:
   ```markdown
   ```
   dirtywork run --repo <path> "<task>"
       [--model qwen/qwen3-coder-next]   # or mistralai/devstral-small-2-2512
       [--branch-from <ref>]             # default: repo HEAD
       [--max-turns 40]
       [--timeout 1800]                  # whole-run wall clock, seconds
       [--temperature <f>]               # omitted by default → server preset
       [--base-url http://localhost:1234/v1]  # LM Studio's OpenAI-compatible endpoint
   ```
   ```
   With:
   ```markdown
   ```
   dirtywork run --repo <path> "<task>"
       [--model qwen/qwen3-coder-next]   # or mistralai/devstral-small-2-2512
       [--branch-from <ref>]             # default: repo HEAD
       [--max-turns 40]
       [--timeout 1800]                  # whole-run wall clock, seconds
       [--temperature <f>]               # omitted by default → server preset
       [--base-url http://localhost:1234/v1]  # LM Studio's OpenAI-compatible endpoint
       [--max-worktree-mb 2048]          # best-effort worktree size bound
       [--max-worktree-files 200000]     # best-effort worktree entry-count bound
   ```
   ```

   Replace the exact current stdout example and status list:
   ```markdown
   **stdout:** on any run that gets past preflight, exactly one JSON object is
   printed to stdout (nothing else goes to stdout):

   ```json
   {
     "status": "completed",
     "worktree": "/path/to/repo/.worktrees/dw-<slug>",
     "branch": "dirtywork/<slug>",
     "transcript": "/path/to/transcript.jsonl",
     "turns": 7,
     "usage": {"prompt_tokens": 0, "completion_tokens": 0},
     "final_message": "..."
   }
   ```

   `status` is one of: `completed`, `max_turns`, `timeout`,
   `context_exhausted`, `model_error`, `interrupted`. When the run fails before
   a `RunResult` exists — the LLM client raises, post-worktree setup fails (e.g.
   the transcript can't be created), or any other exception escapes the run
   (status `model_error` in every case) — `turns` is `null` and `usage` is `{}`,
   but `status`, `worktree`, `branch`, and `transcript` are still populated so
   the worktree can be located for salvage.
   ```
   With:
   ```markdown
   **stdout:** on any run that gets past preflight, exactly one JSON object is
   printed to stdout (nothing else goes to stdout):

   ```json
   {
     "status": "completed",
     "worktree": "/path/to/repo/.worktrees/dw-<slug>",
     "branch": "dirtywork/<slug>",
     "transcript": "/path/to/transcript.jsonl",
     "turns": 7,
     "usage": {"prompt_tokens": 0, "completion_tokens": 0},
     "final_message": "...",
     "run_dir": "/home/you/.dirtywork/runs/<slug>",
     "base_commit": "abc123def456..."
   }
   ```

   `status` is one of: `completed`, `max_turns`, `timeout`,
   `context_exhausted`, `model_error`, `interrupted`, `budget_exceeded`. When
   the run fails before a `RunResult` exists — the LLM client raises,
   post-worktree setup fails (e.g. the transcript can't be created), or any
   other exception escapes the run (status `model_error` in every case) —
   `turns` is `null` and `usage` is `{}`, but `status`, `worktree`, `branch`,
   `transcript`, `run_dir`, and (when it was resolved before the failure)
   `base_commit` are still populated so the worktree can be located for
   salvage.
   ```

   Replace the exact current exit-codes bullet for `1`:
   ```markdown
   - `1` — run ended abnormally (`max_turns`, `timeout`, `context_exhausted`,
     `model_error`, `interrupted`); the worktree and branch are kept for
     salvage/review. `main` catches every `Exception` the run raises (not just
     ones the runner itself converts to a status) and reports it as
     `model_error` via the same JSON contract, so a post-preflight run never
     tracebacks. (Ctrl-C is a `KeyboardInterrupt`, a `BaseException`, not caught
     here — but the run loop itself already converts in-loop Ctrl-C to status
     `interrupted` before it would reach this point.)
   ```
   With:
   ```markdown
   - `1` — run ended abnormally (`max_turns`, `timeout`, `context_exhausted`,
     `model_error`, `interrupted`, `budget_exceeded`); the worktree and branch
     are kept for salvage/review. `main` catches every `Exception` the run
     raises (not just ones the runner itself converts to a status) and reports
     it as `model_error` via the same JSON contract, so a post-preflight run
     never tracebacks. (Ctrl-C is a `KeyboardInterrupt`, a `BaseException`, not
     caught here — but the run loop itself already converts in-loop Ctrl-C to
     status `interrupted` before it would reach this point.)
   ```

   Replace the exact current transcript-events line:
   ```markdown
   **Transcript events** (JSONL, one per line): `run_start` (task, repo, model,
   config), `assistant` (text + tool calls), `tool_result` (truncated),
   `guardrail_block`, `run_end` (status, turns, duration, cumulative usage).
   ```
   With:
   ```markdown
   **Transcript events** (JSONL, one per line): `run_start` (task, repo, model,
   config, plus provenance: `base_commit`, `branch`, `branch_from`,
   `base_url`, `dirtywork_version`, `temperature`, `sandbox`, `provider`),
   `assistant` (text + tool calls — text capped at 64 000 chars in the
   transcript only, the full text is still sent to the model), `tool_result`
   (truncated), `guardrail_block`, `run_end` (status, turns, duration,
   cumulative usage, plus `diff_stat` in host mode — `git diff --stat`
   against the base commit, tracked changes only, capped at 64 000 chars).
   ```

4. Add a troubleshooting entry for `budget_exceeded`. Replace the exact
   current text:
   ```markdown
   - **status `context_exhausted`** — the task needed more context than the
     model's window; split the task or use the larger-context model.
   ```
   With:
   ```markdown
   - **status `context_exhausted`** — the task needed more context than the
     model's window; split the task or use the larger-context model.
   - **status `budget_exceeded`** — the worktree grew past
     `--max-worktree-mb`/`--max-worktree-files` during a tool call; the
     worktree and branch are kept for salvage. Raise the limit or investigate
     what wrote so much.
   ```

5. Update SECURITY.md. Replace the exact current bullet list:
   ```markdown
   - **File tools are confined** to the worktree by real path resolution (symlink,
     `..`, and absolute-path escapes are rejected).
   - **`bash` is a general shell and is NOT confined** — it is gated only by a
     best-effort regex denylist plus a `HOME` redirected into the worktree (so
     `~`/`$HOME` can't reach `~/.ssh`/`~/.aws`). A determined or prompt-injected
     model can still read absolute host paths. Do not treat `bash` as a sandbox.
   ```
   With:
   ```markdown
   - **File tools are confined** to the worktree by real path resolution (symlink,
     `..`, and absolute-path escapes are rejected); writes additionally refuse to
     go through a symlink at the final path component and refuse any
     non-regular-file target (FIFO/device/socket).
   - **`bash` is a general shell and is NOT confined** — it is gated only by a
     best-effort regex denylist plus a `HOME` redirected into the worktree (so
     `~`/`$HOME` can't reach `~/.ssh`/`~/.aws`). A determined or prompt-injected
     model can still read absolute host paths, and can still WRITE outside the
     worktree through an interpreter (e.g. `python3 -c "open('/tmp/x','w')..."`)
     — the denylist matches shell-command *forms*, not every write primitive
     every interpreter offers. Do not treat `bash` as a sandbox.
   - **`.git/info/exclude` is modified.** Every run appends `.worktrees/` to the
     shared repository's `.git/info/exclude` (idempotent; not committed). This is
     the only host-side git state a run writes outside its own worktree.
   - **Worktree disk/file-count growth is a best-effort, sampled bound**
     (`--max-worktree-mb`/`--max-worktree-files`, checked after every tool
     call), not a kernel quota — a large write within a single tool call can
     exceed the limit before it is caught.
   ```

6. Run the full suite (docs changes don't affect tests, but this confirms
   nothing else regressed).

   ```
   python3 -m pytest -q
   ```

   Expected: all pass.

7. Commit.

   ```
   git add README.md SECURITY.md
   git commit -m "docs: document info/exclude write, python3 -c write gap, budget_exceeded, and new provenance fields"
   ```

---

## Self-review: spec coverage

| Spec row | Finding | Task(s) |
|---|---|---|
| 1 | git denylist doesn't tolerate global options | Task 1 (regex fix), Task 11 (README/guardrails.py doc note added in Task 1 step 3) |
| 2 | `CLAUDE.md`/`AGENTS.md` read through symlinks, unbounded size | Task 2 |
| 3 | `.worktrees` symlink unchecked; final destination unchecked | Task 3 |
| 4 | `info/exclude` path not validated | Task 4 |
| 5 | HTTP error body read fully before slicing | Task 5 |
| 6 | Transcript/run dir created with default perms; `~/.dirtywork` never validated | Task 6 |
| 7 | Missing provenance | Task 9 (`Runner`/`RunResult` plumbing), Task 10 (`__main__` wiring — `run_start` fields, `run_end.diff_stat`) |
| 8 | Unbounded `write_file` content, `list_dir` rows, assistant text | Task 7 (`write_file`/`list_dir`), Task 9 (`MAX_ASSISTANT_TEXT_CHARS`) |
| 9 | `write_file` has no regular-file guard; FIFO hang | Task 7 |
| 10 | Worktree disk growth unbounded | Task 8 (walker), Task 9 (`ToolExecutor` wiring + `budget_exceeded` status) |
| 11 | `.git/info/exclude` modified in the main checkout, undocumented | Task 11 |
| 12 | 16-bit slug salt | Task 3 (`token_hex(4)`) |
| 13 | "Should not market itself as secure" | No change, by spec — deferred until sub-project 2 lands (out of scope for this plan) |

## Type consistency

Confirms every name/signature introduced by this plan matches the shared
brief (`plan-brief-shared.md`, "SP1 introduces" section) verbatim.

- [x] `dirtywork/workspace.py`: `MAX_CONTEXT_CHARS = 32_000` — Task 2.
- [x] `dirtywork/workspace.py`: `MAX_CONTEXT_BYTES = 5 * 1024 * 1024` — Task 2.
- [x] `dirtywork/workspace.py`: `load_repo_context(repo: Path, base_commit: str) -> str | None` — Task 2.
- [x] `dirtywork/workspace.py`: `worktree_base_commit(worktree: Path) -> str` — Task 2.
- [x] `dirtywork/workspace.py`: `create_worktree(repo: Path, slug: str, branch_from: str | None) -> Path` (SP1 adds lstat/ENOENT/post-check rules; the `*, no_checkout: bool = False` kwarg is left for SP2 to add) — Task 3.
- [x] `dirtywork/workspace.py`: `ensure_worktrees_excluded(repo: Path) -> None` (validated path, `O_NOFOLLOW`) — Task 4.
- [x] `dirtywork/workspace.py`: `host_diff_stat(worktree: Path, cap: int = 64_000) -> str` — Task 10.
- [x] `dirtywork/workspace.py`: `make_slug(...)` unchanged signature; salt is `secrets.token_hex(4)` — Task 3.
- [x] `dirtywork/rundir.py` (NEW): `DIRTYWORK_HOME = Path.home() / ".dirtywork"`; `RUNS_DIR = DIRTYWORK_HOME / "runs"` (`__main__.RUNS_DIR` keeps the name by importing this) — Task 6.
- [x] `dirtywork/rundir.py`: `class RunDirError(Exception)` — Task 6.
- [x] `dirtywork/rundir.py`: `ensure_runs_dir(runs_dir: Path = RUNS_DIR) -> Path` — Task 6.
- [x] `dirtywork/rundir.py`: `create_run_dir(runs_dir: Path, slug: str) -> Path` — Task 6. (`write_run_json`/`read_run_json` are explicitly SP2's addition per the shared brief — not implemented here.)
- [x] `dirtywork/transcript.py`: `Transcript.__init__(self, path: Path)` opens with `os.open(path, O_WRONLY|O_CREAT|O_EXCL|O_APPEND|O_NOFOLLOW, 0o600)`; no more `mkdir(parents=True)` — Task 6.
- [x] `dirtywork/budget.py` (NEW): `class BudgetExceeded(Exception)` with `.reason: str` — Task 8.
- [x] `dirtywork/budget.py`: `@dataclass class BudgetReport: bytes: int; files: int; escaping_symlinks: list[str]; violation: str | None` — Task 8.
- [x] `dirtywork/budget.py`: `measure_worktree(worktree: Path, *, max_bytes: int, max_files: int) -> BudgetReport` — Task 8.
- [x] `dirtywork/budget.py`: `DEFAULT_MAX_WORKTREE_MB = 2048`, `DEFAULT_MAX_WORKTREE_FILES = 200_000` — Task 8.
- [x] `dirtywork/tools.py`: `MAX_WRITE_BYTES = 5 * 1024 * 1024`; `MAX_LIST_ENTRIES = 2000` — Task 7.
- [x] `dirtywork/tools.py`: `_open_regular(path: Path, flags: int, *, mode: int = 0o644, max_size: int | None = None)` → fd-backed file object; `O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC`; fstat `S_ISREG`; size cap; clears `O_NONBLOCK` — Task 7.
- [x] `dirtywork/tools.py`: `ToolExecutor.__init__(self, worktree, transcript=None, *, max_worktree_mb=DEFAULT_MAX_WORKTREE_MB, max_worktree_files=DEFAULT_MAX_WORKTREE_FILES)`; runs `measure_worktree` after every `execute`, raises `BudgetExceeded` on violation — Task 9.
- [x] `dirtywork/runner.py`: `MAX_ASSISTANT_TEXT_CHARS = 64_000` — Task 9.
- [x] `dirtywork/runner.py`: `Runner.__init__(..., finalize: Callable[[], dict] | None = None)`; `finish()` merges `finalize()`'s dict into `run_end` and into `RunResult.extra: dict`; a `finalize()` exception is caught and recorded as `run_end.finalize_error` — Task 9.
- [x] `dirtywork/runner.py`: catches `BudgetExceeded` from `executor.execute` → `finish("budget_exceeded", e.reason)` — Task 9.
- [x] `dirtywork/runner.py`: new status `budget_exceeded` — Task 9.
- [x] `dirtywork/__main__.py`: flags `--max-worktree-mb` (2048), `--max-worktree-files` (200000) — Task 10.
- [x] `dirtywork/__main__.py`: `run_start` gains `base_commit, branch, branch_from, base_url, dirtywork_version, temperature, sandbox ("none"), provider ("openai")` — Task 10.
- [x] `dirtywork/__main__.py`: `run_end` gains `diff_stat` (via `finalize`) in host mode — Task 10.
- [x] Stdout JSON contract: no existing key (`status, worktree, branch, transcript, turns, usage, final_message`) lost or renamed; `run_dir` and `base_commit` added — Task 10, verified by `test_stdout_json_has_run_dir_and_base_commit`.
