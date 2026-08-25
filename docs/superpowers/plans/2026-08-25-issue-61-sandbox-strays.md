# #61 Sandbox strays — kill in place, gitfile discovery, reset notices: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **This repository's execution rule overrides the line above.** Every code task below is built
> by the **released dirtywork (0.10.1) running against this repository** with a local worker
> (`qwen/qwen3-coder-next` via LM Studio) — Claude writes the brief, reviews the branch, runs the
> host suites, feeds back through `dirtywork resume --feedback-file`, and writes the prose docs.
> A Claude implementer touches code only after a worker resume-with-feedback has failed, and the
> PR says so. Owner approval is needed for the merge and the release, never assumed.

**Goal:** Docker mode kills stray processes in place (falling back to a container reset only when
that fails), keeps the worker's git state through it, replaces the `GIT_DIR`/`GIT_WORK_TREE`
environment with a gitfile so git inside the sandbox behaves normally, exports nested repositories
as plain files, and tells the worker — through the #60 carriers — every time a kill or a reset
happened.

**Architecture:** a new `dirtywork/sandbox/strays.py` holds the in-container script constants, the
`docker top` parser and the notice texts; `DockerSandbox` gains tether discovery, the kill ladder,
a `_reap_lock`/flag protocol with the watchdog, and a notice queue the runner drains into the
existing `deliver()` carriers; `lifecycle.init_worker_git` grows a `layout` (gitfile for the worker
container, env for the export container); `export.py` enumerates `.git` entries NUL-safely and
splices each nested root through a throwaway sub-index; bench/runs/soak render and count the new
`stray_kill` event and nudge kinds.

**Tech Stack:** Python 3.9 stdlib only; Docker CLI; dash (`/bin/sh` in the worker image); git 2.39
in the image; pytest; the fake-docker harness in `tests/docker_fakes.py`.

**Spec:** `docs/superpowers/specs/2026-08-25-sandbox-strays-gitfile-and-reset-notices-design.md`
(v4, owner-approved 2026-08-25 11:17 CDT). Section numbers below refer to it.

## Global Constraints

- Python 3.9 floor, stdlib only; every change is additive under `schema_version` 2 (no field
  renamed or removed, no new `nudge.via` value) — spec header.
- The kill script is **fork-free dash**: builtins only, no `$( )`, pipe, backtick or subshell —
  §3.3. Scripts are module constants; dynamic values arrive as `"_", value` positional args — §3.
- Lock order `_reap_lock → _reset_lock → _notices_lock`; `reset()` never takes `_reap_lock` —
  §3.6.
- The worker container gets **no** `GIT_DIR`/`GIT_WORK_TREE`; the export container keeps both —
  §4.1.
- Redirect order in scripts is `2>/dev/null <` (dash applies redirections left to right) — §3.2.
- `Captured.output` merges stderr: every parsed stream is filtered (single-integer, NUL keep-rule,
  full-match regex) — §3.2, §3.4, §4.4.
- DRY & SOLID (owner's standing rule): one parser, one script builder, one text formatter; the
  ladder lives in `strays.py`, not spread through `docker.py`.
- The worker never edits `docs/**`; prose docs are Claude's (task D1).

## Execution model (every W task)

- **Run command** (from `/Users/jimschneider/repos/dirtywork`, brief in the scratchpad):

  ```bash
  pipx run --spec 'dirtywork==0.10.1' dirtywork run "$(cat "$BRIEF")" \
    --repo /Users/jimschneider/repos/dirtywork --branch-from issue-61-sandbox-resets \
    --model qwen/qwen3-coder-next --sandbox docker --image dirtywork-worker-pytest:0.10 \
    --verify "env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider" \
    --verify-rounds 2 --max-turns 60 --timeout 1800
  ```

  The verify gate keeps `env -u …` for **every** run of this build: the worker executes inside the
  *released* 0.10.1 sandbox, which still exports `GIT_DIR` (S13 is fixed in the branch's code, not
  in the runtime that runs the worker). S13's acceptance is task C9.
- **Chaining:** each run branches from `issue-61-sandbox-resets`; after review Claude commits the
  export on the run's branch (`worker export verbatim: run <slug>`), adds its own fix commits,
  fast-forwards `issue-61-sandbox-resets` to it from `.worktrees/issue-61-sandbox-resets`
  (`git merge --ff-only dirtywork/<slug>`), removes the run worktree (`git worktree remove
  .worktrees/dw-<slug>`) and deletes the run branch. The next run then branches from the updated
  integration branch.
- **Review loop:** read `~/.dirtywork/runs/<slug>/run.json` + transcript; diff the run worktree
  against the brief and the spec section; run the host suite in the run worktree
  (`/usr/bin/python3 -m pytest -q -p no:cacheprovider`); gaps → `dirtywork resume <slug>
  --feedback-file <file> --max-turns 40` (verify inherited), at most two resumes; then Claude
  finishes leftovers and says so in the ledger and the PR.
- **Metrics:** `tools/soak_sampler.sh <scratchpad>/metrics-61.csv` started in C0 and stopped in
  C9; one ledger row per run (status, turns, wall, s/turn, prompt/completion tokens, tok/s,
  nudges, guardrail blocks, sandbox resets, tool mix, verify outcome) appended to a `#61` section
  of `docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md`.
- Give qwen ≥ 60 turns (it re-runs the suite every other turn); resumes burn turns on
  `read_file`, so feedback names files and lines.

## File structure

| file | responsibility after this plan |
|---|---|
| `dirtywork/sandbox/strays.py` (new) | script constants, `docker top` row parser, tether-pid / sweep parsers, caps, notice texts |
| `dirtywork/sandbox/docker_args.py` | `_base_env_args()` (both containers) + `_git_env_args()` (export only) |
| `dirtywork/sandbox/lifecycle.py` | `init_worker_git(..., layout)`; `EXPORT_GIT_ENTRIES_SCRIPT` lives in `export.py` |
| `dirtywork/sandbox/docker.py` | tether discovery, ladder, notices, locks/flags, shutdown, abandoned-exec kill |
| `dirtywork/sandbox/watchdog.py` | `check_worktree_budget_once(*, wait=True)`; `run()` samples with `wait=False` |
| `dirtywork/sandbox/export.py` | NUL-safe enumeration, nested-root splice, base-aware safety net |
| `dirtywork/sandbox/__init__.py`, `host.py` | `drain_notices()` documented on the Protocol; host returns `[]` |
| `dirtywork/runner.py` | `drain_sandbox()` and its five call sites |
| `dirtywork/bench.py`, `dirtywork/runs.py`, `tools/soak_harvest.py` | counts, columns, callouts, `_md_code` |
| `dirtywork/builtin_tools.py` + `tests/fixtures/tool_schemas.json` | bash description (wire) |
| `docker/Dockerfile`, `docker/README.md` | four `.NET` `ENV` lines; derived-image snippet; checklist |
| `tests/docker_fakes.py` | callable responses |
| docs (`machine-contract`, `transcript-schema`, `operating`, `security`, `guardrails.py` comment) | Claude |

---

### Task C0: Baseline and instrumentation (Claude)

**Files:** none changed.

- [ ] **Step 1: Baseline suite on the integration branch**

Run: `/usr/bin/python3 -m pytest -q -p no:cacheprovider`
Expected: the same count main has (1387 collected − 27 deselected, all green). Record the number.

- [ ] **Step 2: Image and model are up**

Run: `docker image inspect dirtywork-worker-pytest:0.10 --format '{{.Id}}' && ~/.lmstudio/bin/lms ps`
Expected: an image id; `qwen/qwen3-coder-next` loaded (65 536 ctx, PARALLEL 4).

- [ ] **Step 3: Start the sampler**

Run: `tools/soak_sampler.sh /private/tmp/claude-501/-Users-jimschneider-repos-dirtywork/b8bd6636-8109-4636-90dd-6854fae4b3c8/scratchpad/metrics-61.csv`

- [ ] **Step 4: Open the ledger section**

Append `## #61 — sandbox strays (2026-08-25)` with the run-row table header to
`docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md` (commit with the first run's row).

---

### Task W1: Gitfile layout — env split and two init layouts (spec §4.1–§4.3)

**Files:**
- Modify: `dirtywork/sandbox/docker_args.py:120-134` (`_env_entrypoint_args`), `:159`, `:189`
- Modify: `dirtywork/sandbox/lifecycle.py:35-55` (`init_worker_git`)
- Modify: `dirtywork/sandbox/docker.py:308-309` (`_init`), `dirtywork/sandbox/export.py:245`
- Modify: `dirtywork/guardrails.py:84-88` (comment)
- Test: `tests/test_docker_args.py`, `tests/test_docker_sandbox.py:824-843,1425-1445`, `tests/test_export_flow.py`

**Interfaces:**
- Produces: `docker_args._base_env_args() -> list`, `docker_args._git_env_args() -> list`;
  `lifecycle.init_worker_git(run, name, *, branch, base_commit, restart, layout)` with
  `layout in ("gitfile", "env")`; `lifecycle.GITFILE_INIT_SCRIPT` / `ENV_INIT_SCRIPT` constants
  (format strings taking `branch`, `base_commit`, `populate`).

- [ ] **Step 1: Write the brief** to `$SCRATCH/brief-61-w1.md`:

```
Issue #61, task W1 of 8: git discovery inside the docker sandbox must work through a gitfile, not through GIT_DIR/GIT_WORK_TREE exported into every worker command (finding S13: `git init` in a temp dir inside the sandbox lands in /gitdir). Change dirtywork/sandbox/docker_args.py, dirtywork/sandbox/lifecycle.py, dirtywork/sandbox/docker.py, dirtywork/sandbox/export.py and one comment in dirtywork/guardrails.py, with tests. Do NOT edit docs/. Keep it DRY: one script per layout, no duplicated env lists.

1. dirtywork/sandbox/docker_args.py: split `_env_entrypoint_args()` into two functions and delete the old one:
   - `_base_env_args()` returns exactly the old list WITHOUT the two GIT_DIR/GIT_WORK_TREE pairs (keep HOME, TMPDIR, LANG, the four GIT_AUTHOR_*/GIT_COMMITTER_* pairs, PATH, and `--entrypoint /bin/cat`, in the same order).
   - `_git_env_args()` returns `["-e", "GIT_DIR=/gitdir", "-e", "GIT_WORK_TREE=/work"]`.
   `worker_create_argv` uses `*_base_env_args()` only. `export_create_argv` uses `*_base_env_args(), *_git_env_args()` — the export container keeps both variables because /work is mounted read-only there.

2. dirtywork/sandbox/lifecycle.py: `init_worker_git(run, name, *, branch, base_commit, restart, layout)` — `layout` is a required keyword, either "gitfile" or "env" (raise ValueError otherwise). Two module-level script constants, each a str.format template with {branch}, {base_commit}, {populate}:
   ENV_INIT_SCRIPT = today's script unchanged (used for layout="env").
   GITFILE_INIT_SCRIPT = "set -e; rm -rf -- /work/.git; /usr/bin/git init -q --template= --separate-git-dir=/gitdir; echo /repo.git/objects > /gitdir/objects/info/alternates; /usr/bin/git symbolic-ref HEAD refs/heads/{branch}; /usr/bin/git update-ref refs/heads/{branch} {base_commit}; {populate}"
   `populate` is "/usr/bin/git read-tree HEAD" when restart else "/usr/bin/git read-tree -m -u HEAD" (as today). Both layouts exec with `docker_args.exec_argv(name, ["/bin/sh", "-c", script], env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})` — the gitfile layout must NOT add GIT_DIR/GIT_WORK_TREE to the exec (the container env has none, and `git init --separate-git-dir` must not see them). Same error handling as today. Update the docstring: gitfile layout = worker container (writes /work/.git = "gitdir: /gitdir", idempotent for first start and after a reset; the rm -rf first so a directory the worker put at /work/.git never makes `git init` "move" a repository); env layout = export container.

3. dirtywork/sandbox/docker.py `_init`: pass `layout="gitfile"`. dirtywork/sandbox/export.py's `init_worker_git(...)` call: pass `layout="env"`.

4. dirtywork/guardrails.py lines ~84-88: reword the comment so it says the container's git is found through the gitfile /work/.git -> /gitdir (no GIT_DIR in the environment), not "its own throwaway /gitdir (see lifecycle.init_worker_git)" — keep the rest of that comment.

5. Tests:
   a. tests/test_docker_args.py: the worker create argv contains no "GIT_DIR=/gitdir" and no "GIT_WORK_TREE=/work" (update the existing assertion near line 115); the export create argv still contains both (near line 174); `_base_env_args()` has no GIT_DIR entry and `_git_env_args()` is exactly the four-element list.
   b. tests/test_docker_sandbox.py: extend the existing init-script assertions (around lines 824-843 and 1425-1445) so the worker init script contains "rm -rf -- /work/.git" and "--separate-git-dir=/gitdir" and does NOT contain "GIT_DIR=" as an exec -e argument; keep the read-tree assertions.
   c. tests/test_export_flow.py: in test_export_run_happy_path assert the init exec's script does NOT contain "--separate-git-dir" (env layout) and the export create argv contains "GIT_DIR=/gitdir".
   d. A test that `init_worker_git(..., layout="bogus")` raises ValueError.

6. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` and make the whole suite pass (about 1387 tests before your change). Then call finish with a summary.
```

- [ ] **Step 2: Run it** (execution-model command with `BRIEF=$SCRATCH/brief-61-w1.md`); record the slug.
- [ ] **Step 3: Review** the run worktree: `git diff issue-61-sandbox-resets` — check the four-element `_git_env_args`, the gitfile script text byte-for-byte against §4.2, `layout="env"` at the export call, no `GIT_*` on the gitfile exec, the guardrails comment.
- [ ] **Step 4: Host suite** in the run worktree: `/usr/bin/python3 -m pytest -q -p no:cacheprovider` → all green.
- [ ] **Step 5: Live check (Claude, host):** `DIRTYWORK_LIVE_IMAGE=dirtywork-worker-pytest:0.10 /usr/bin/python3 -m pytest -q -m docker tests/test_docker_live.py -k "backgrounded or flood or reset"` from the run worktree → the existing live tests still pass with the gitfile init.
- [ ] **Step 6: Resume with feedback if needed**, then commit the export, fix-commit, ff-merge into `issue-61-sandbox-resets`, remove the run worktree/branch, ledger row.

---

### Task W2: Export — enumeration, nested-root splice, base-aware safety net (spec §4.4)

**Files:**
- Modify: `dirtywork/sandbox/export.py:245-282`
- Test: `tests/test_export_flow.py`

**Interfaces:**
- Consumes: `lifecycle.init_worker_git(..., layout="env")` (W1).
- Produces: `export.EXPORT_GIT_ENTRIES_SCRIPT: str`, `export.parse_git_entries(output: bytes) -> list[str]`
  (absolute `/work/...` tokens), `export.nested_roots(entries: list[str]) -> list[str]`
  (relative, sorted by descending depth then name), `export.children(root, roots) -> list[str]`
  (relative to `root`), `export.top_level_roots(roots) -> list[str]`.

- [ ] **Step 1: Write the brief** to `$SCRATCH/brief-61-w2.md`:

```
Issue #61, task W2 of 8: the docker export must handle nested git repositories under /work — a `cargo new` or a `git init` in a sub-project. Today `git add -A` aborts on an uncommitted nested repo (`error: 'x/' does not have a commit checked out`) and turns a committed one into a gitlink. New rule: a nested repository's FILES are exported like any other files; only its `.git` entry is dropped. Change dirtywork/sandbox/export.py (the block between `lifecycle.init_worker_git(...)` and `git write-tree` in export_run) with tests in tests/test_export_flow.py. Do NOT edit docs/. Every docker exec is an argv list (never a shell string) except the one constant script below; pathspecs are separate argv elements.

1. Module constant `EXPORT_GIT_ENTRIES_SCRIPT = r"exec /usr/bin/find /work -mindepth 1 -iname .git ! \( -path /work/.git -type f \) -prune -print0 2>/dev/null"` (the parentheses backslash-escaped: it runs under /bin/sh -c). Replace today's find exec with `docker_args.exec_argv(name, ["/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT])`, timeout T_EXPORT_STEP.

2. `parse_git_entries(output: bytes) -> list[str]`: split on b"\0", DROP the last chunk (it is unterminated), decode each with errors="replace", keep only tokens that start with "/work/" and whose last path component lower() == ".git". Policy in export_run: rc != 0 -> print(f"export: .git enumeration incomplete (rc {rc})", file=sys.stderr) and continue with what parsed; `captured.truncated` -> return _fail("could not enumerate .git entries"). `dropped_git_entries` = each token with the "/work/" prefix removed (as today).

3. Roots. `nested_roots(entries)`: for every token whose relative path has depth >= 2 (i.e. "a/.git" -> "a", "a/b/.git" -> "a/b"; "/work/.git" contributes nothing) take the parent directory; deduplicate; sort by (descending number of "/"-separated components, then name). `children(root, roots)`: the roots R2 such that R2 startswith root + "/" and no other root R3 satisfies root + "/" <= R3 < R2 with R2 startswith R3 + "/" (i.e. the immediate nested roots), returned RELATIVE to root (strip root + "/"). `top_level_roots(roots)`: roots with no ancestor in the set. Put these three as module-level pure functions.

4. Splice, deepest first — for the i-th root R in the sorted list, one docker exec per command, each with workdir "/work/" + R and env {"GIT_DIR": f"/tmp/nested-{i}", "GIT_WORK_TREE": "/work/" + R, "GIT_OBJECT_DIRECTORY": "/gitdir/objects", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"} via `docker_args.exec_argv(name, argv, workdir=..., env=...)`, timeout T_EXPORT_STEP:
   a. ["/usr/bin/git", "init", "-q", "--template="]
   b. ["/usr/bin/git", "read-tree", "--empty"]
   c. ["/usr/bin/git", "-c", "core.excludesFile=/work/.gitignore", "add", "-A", "--", "."] + [f":(exclude,literal){c}" for c in children(R, roots)]
   d. for each child c (same order): ["/usr/bin/git", "read-tree", f"--prefix={c}/", tree[c]]
   e. ["/usr/bin/git", "write-tree"] -> tree[R] = output.strip()
   Any rc != 0 or DockerError -> _fail(f"nested repository splice failed at {R}: {output[:500]}").

5. Main index (default env/workdir, as today's commands): for each top-level root R: ["/usr/bin/git", "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", f":(literal){R}"] then ["/usr/bin/git", "read-tree", f"--prefix={R}/", tree[R]]. Then the add: ["/usr/bin/git", "add", "-A", "--", "."] + [f":(exclude,literal){R}" for R in top_level_roots] — when there are no roots this must be exactly today's ["/usr/bin/git", "add", "-A"] (no "--", no "."), so existing tests keep passing. Print one line per root to stderr: f"nested repository exported as plain files: {R}".

6. Safety net, after `git write-tree` succeeds: run ["/usr/bin/git", "ls-files", "-s", "-z"] and ["/usr/bin/git", "ls-tree", "-r", "-z", base_commit]; for either: rc != 0, DockerError or captured.truncated -> _fail(f"could not verify the export index ({which})"). Parse both on b"\0" (drop the unterminated tail); an ls-files record is "<mode> <sha> <stage>\t<path>", an ls-tree record is "<mode> <type> <sha>\t<path>". new_gitlinks = paths with mode "160000" in ls-files whose mode in ls-tree is not "160000" (absent counts as not). Any -> _fail(f"nested repository at {path} was not masked") using the first such path in index order.

7. Tests in tests/test_export_flow.py (FakeDocker; script the find exec by its full argv ["exec", "-w", "/work", "dw-abc123-export", "/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT] and each git exec by full argv; a generic ["exec"] default returns rc 0 empty):
   a. parse_git_entries: input b"/work/a b/.git\0/work/x\ny/.git\0garbage\0/tmp/z/.git\0/work/w/.GIT\0/work/unterminated/.git" -> ["/work/a b/.git", "/work/x\ny/.git", "/work/w/.GIT"] (order kept; the /tmp token, "garbage" and the unterminated tail dropped).
   b. nested_roots(["/work/a/.git", "/work/a/b/.git", "/work/c/.git", "/work/.git"]) == ["a/b", "a", "c"]; children("a", ...) == ["b"]; top_level_roots(...) == ["a", "c"].
   c. export_run with two entries "/work/a/.git\0/work/a/b/.git\0": assert the exact exec sequence and env for the splice (nested-0 for "a/b" then nested-1 for "a" with the child exclusion ":(exclude,literal)b" and "--prefix=b/"), the main "rm -r -q --cached --ignore-unmatch -- :(literal)a", "read-tree --prefix=a/ <tree>", and the final add argv ["/usr/bin/git", "add", "-A", "--", ".", ":(exclude,literal)a"]; dropped_git_entries == ["a/.git", "a/b/.git"].
   d. no entries -> the add argv is exactly ["/usr/bin/git", "add", "-A"] and the ls-files/ls-tree execs run.
   e. ls-files output containing b"160000 <sha> 0\tvendor/x\0" while ls-tree lacks vendor/x -> export_status == "export_failed: nested repository at vendor/x was not masked"; the same path present as 160000 in ls-tree -> export succeeds; ls-files rc 1 -> "export_failed: could not verify the export index (ls-files)"; truncated find output -> "export_failed: could not enumerate .git entries"; find rc 1 with parseable output -> export continues and the stderr line is printed.
   f. test_export_run_parses_dropped_git_entries: update to the NUL format.

8. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` and make the whole suite pass. Then call finish with a summary.
```

- [ ] **Step 2: Run it**; record the slug.
- [ ] **Step 3: Review** against §4.4 step by step (escaped parens in the constant; `-prune`; keep-rule; `GIT_OBJECT_DIRECTORY`; `-w /work/<R>`; `core.excludesFile`; `:(literal)` on `rm`; trailing slash on `--prefix`; the base-aware net; fail-closed listings).
- [ ] **Step 4: Host suite** green in the run worktree.
- [ ] **Step 5: Live splice check (Claude, host):** replay P8's fixture through the branch's `export_run` — a throwaway repo with the six nested roots from §1.4, `python3 -m dirtywork run --sandbox docker …` from the run worktree with a scripted 2-call task (`git init` in a subdir + edits), then assert the diff and `dropped_git_entries` in `run.json`. Keep the script in the scratchpad (`p8-replay.py`) — it becomes live test 16 in W8.
- [ ] **Step 6: Resume if needed**, commit export, fix-commit, ff-merge, cleanup, ledger row.

---

### Task W3: `strays.py` — constants, parsers, texts (spec §3.1–§3.5, §5.3, §6.1)

**Files:**
- Create: `dirtywork/sandbox/strays.py`
- Modify: `dirtywork/sandbox/docker.py:857-895` (`_reap` uses `strays.stray_rows`)
- Test: `tests/test_strays.py` (new)

**Interfaces:**
- Produces (all in `dirtywork.sandbox.strays`): `TETHER_DISCOVERY_SCRIPT`, `STRAY_KILL_SCRIPT`,
  `LOCK_SWEEP_ARGV`, `LOCK_PATH_RE`, `MAX_STRAYS = 20`, `MAX_STRAY_CHARS = 200`, `MAX_LOCKS = 20`,
  `stray_rows(top_output: bytes) -> list[str]`, `parse_tether_pid(output: bytes) -> int | None`,
  `parse_locks(output: bytes) -> list[str]`, `cap_strays(rows) -> tuple[list[str], int | None]`,
  `cap_locks(paths, truncated: bool) -> tuple[list[str], int | None]`,
  `stray_kill_text(strays, total, locks_removed) -> str`, `sandbox_reset_text(reason) -> str`.

- [ ] **Step 1: Write the brief** to `$SCRATCH/brief-61-w3.md`:

```
Issue #61, task W3 of 8: create the module dirtywork/sandbox/strays.py — pure functions and constants for the docker sandbox's stray-process handling — with tests in a new tests/test_strays.py, and make DockerSandbox._reap in dirtywork/sandbox/docker.py use its row parser. No docker calls in this module; no other behaviour change. Do NOT edit docs/.

1. Constants (exact text; use a raw triple-quoted string for the two scripts, newlines are fine — /bin/sh -c takes multi-line scripts):
TETHER_DISCOVERY_SCRIPT = r"""n=0; t=
for p in /proc/[0-9]*; do
  read -r c 2>/dev/null < "$p/comm" || continue
  [ "$c" = cat ] || continue
  n=$((n+1)); t=${p#/proc/}
done
[ "$n" = 1 ] || exit 3
echo "$t"
"""
STRAY_KILL_SCRIPT = r"""T=$1
read -r c 2>/dev/null < "/proc/$T/comm" || exit 3
[ "$c" = cat ] || exit 3
for pass in 1 2 3; do
  for p in /proc/[0-9]*; do
    p=${p#/proc/}
    [ "$p" = 1 ] && continue
    [ "$p" = "$T" ] && continue
    [ "$p" = "$$" ] && continue
    kill -9 "$p" 2>/dev/null
  done
done
exit 0
"""
   Comment above STRAY_KILL_SCRIPT: it must stay fork-free (dash builtins only — read, [, kill, for, exit; no $( ), no pipe, no backtick, no subshell) because it has to run inside a pids-saturated container; the tether pid arrives as "$1"; the glob is re-expanded per pass so a process forked between passes is caught.
LOCK_SWEEP_ARGV = ["/usr/bin/find", "/gitdir", "(", "-name", "*.lock", "-o", "-name", "gc.pid", ")", "-type", "f", "-delete", "-print0"]
LOCK_PATH_RE = re.compile(r"^/gitdir/(?:.+/)?(?:[^/]+\.lock|gc\.pid)$")
MAX_STRAYS = 20; MAX_STRAY_CHARS = 200; MAX_LOCKS = 20; NOTICE_CMDS = 3; NOTICE_CMD_CHARS = 80
TETHER_CMDS = ("cat", "/bin/cat")

2. `stray_rows(top_output: bytes) -> list[str]`: move the row parsing that DockerSandbox._reap does today (header split, `line.split(None, n - 1)`, CMD = last field, skip rows whose CMD is in TETHER_CMDS or ends with "docker-init -- cat" or "docker-init -- /bin/cat") into this function; return the CMD of every other row in order ([] when only the tether is present or the output is empty). In docker.py, `_reap` must call `strays.stray_rows(top.output)` and reset when the list is non-empty — same behaviour as today, one parser.

3. `parse_tether_pid(output: bytes) -> int | None`: decode (errors="replace"), strip; return int(text) if the whole stripped text matches ^[0-9]+$ and is > 0, else None.

4. `parse_locks(output: bytes) -> list[str]`: split on b"\0", drop the last (unterminated) chunk, decode each, keep those that LOCK_PATH_RE.fullmatch — order kept.

5. `cap_strays(rows)` -> (first MAX_STRAYS rows each cut to MAX_STRAY_CHARS with a trailing "…" when cut, len(rows) if len(rows) > MAX_STRAYS else None). `cap_locks(paths, truncated)` -> (first MAX_LOCKS paths uncut, len(paths) if len(paths) > MAX_LOCKS and not truncated else None).

6. Texts. `stray_kill_text(strays, total, locks_removed)`: n = total or len(strays); cmds = "; ".join(s[:NOTICE_CMD_CHARS] for s in strays[:NOTICE_CMDS]) and, when n > NOTICE_CMDS, + f"; +{n - NOTICE_CMDS} more". Returns: f"The sandbox killed {n} background process{'' if n == 1 else 'es'} your last command left running ({cmds}). A process cannot outlive the bash call that started it — start and use anything you need within one command." + (" Stale git lock files they left in the repository were removed." if locks_removed else "") + " Run `git status` to confirm the repository state before continuing."
   `sandbox_reset_text(reason)`: f"The sandbox container was reset after your last command ({reason}). Files in the worktree are intact, but git metadata was re-initialized: the index, stashes, local commits and branches you created inside the sandbox are gone, and the branch is back at the run's base commit with your file changes uncommitted. Run `git status` before continuing."

7. tests/test_strays.py (pytest, parametrize where natural):
   a. STRAY_KILL_SCRIPT contains none of "$(", "|", "`", "("; TETHER_DISCOVERY_SCRIPT contains "2>/dev/null <" and not "< \"$p/comm\" 2>"; STRAY_KILL_SCRIPT has "for pass in 1 2 3"; both scripts: `subprocess.run(["sh", "-n", "-c", SCRIPT])` returns 0 (skip if sh is missing).
   b. stray_rows: a real docker top header + rows: tether-only (docker-init + /bin/cat) -> []; with "sleep 300" and "bash -c (while true; do sleep 2; done)" rows -> those two CMDs in order; a bare "cat" row is treated as tether (documented loophole); empty bytes -> [].
   c. parse_tether_pid: b"7\n" -> 7; b"sh: 1: cannot open /proc/9/comm: No such file\n7\n" -> None; b"" -> None; b"0\n" -> None; b"7\n8\n" -> None.
   d. parse_locks: b"/gitdir/index.lock\0/gitdir/gc.pid\0/gitdir/refs/heads/x.lock\0find: '/gitdir/y': Permission denied\n/gitdir/z.lock\0/gitdir/tail.lock" -> ["/gitdir/index.lock", "/gitdir/gc.pid", "/gitdir/refs/heads/x.lock"] (the diagnostic-glued token fails the full match; the tail is dropped); "/gitdir/objects/tmp_obj_x" is not matched.
   e. cap_strays with 25 rows -> 20 entries and total 25; a 300-char row -> 200 chars + "…"; 3 rows -> total None. cap_locks with 25 paths, truncated=False -> total 25; truncated=True -> total None.
   f. the two texts: n == 1 wording ("process"), the "+2 more" suffix for 5 strays, the lock sentence only when locks_removed is non-empty, and "git status" always present.
   g. tests/test_docker_sandbox.py's existing reap tests still pass (no new ones needed here).

8. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` and make the whole suite pass. Then call finish with a summary.
```

- [ ] **Step 2: Run it**; **Step 3: Review** (script text byte-exact vs §3.2/§3.3; `LOCK_PATH_RE` vs §3.4; text vs §5.3; `_reap` uses the shared parser — no second copy); **Step 4: Host suite** green; **Step 5:** resume if needed, commit, ff-merge, cleanup, ledger row.

---

### Task W4: The ladder — tether discovery, kill, sweep, OOM, `stray_kill`, notices (spec §3.2–§3.5, §3.7, §5.1, §6.1)

**Files:**
- Modify: `dirtywork/sandbox/docker.py` (`__init__`, `start`, `_start_tether`/`_wait_ready` call sites, `reset`, `_reap`), `dirtywork/sandbox/host.py`, `dirtywork/sandbox/__init__.py`
- Test: `tests/test_docker_sandbox.py` (fixtures `docker`, `started`, `started_with_transcript` + new tests)

**Interfaces:**
- Consumes: everything in `strays.py` (W3).
- Produces: `DockerSandbox._tether_pid`, `_discover_tether() -> None`, `_kill_strays() -> bool`,
  `_sweep_locks() -> tuple[list[str], int | None]`, `reset(reason, *, strays=None, strays_total=None)`,
  `_queue_notice(kind, text)`, `drain_notices() -> list[tuple[str, str]]` (also on `HostSandbox`,
  returning `[]`; documented on the `Sandbox` Protocol).

- [ ] **Step 1: Write the brief** to `$SCRATCH/brief-61-w4.md`:

```
Issue #61, task W4 of 8: in docker mode a stray background process after a bash call must be killed IN PLACE, and the container reset only when that fails — today every stray costs a `docker kill` and the worker's /gitdir (index, stashes, commits). Change dirtywork/sandbox/docker.py (DockerSandbox), dirtywork/sandbox/host.py and the Sandbox Protocol docstring in dirtywork/sandbox/__init__.py, with tests in tests/test_docker_sandbox.py. Use the constants/parsers/texts from dirtywork/sandbox/strays.py (read it first; do not duplicate them). Do NOT edit docs/. Locks and the watchdog interplay are the NEXT task — do not add locks here beyond the small _notices_lock below.

1. State in __init__: `self._tether_pid = None`, `self._notices = []`, `self._notices_lock = threading.Lock()`, `self._tether_warned = False`.

2. `_discover_tether(self)`: exec `docker_args.exec_argv(self.container, ["/bin/sh", "-c", strays.TETHER_DISCOVERY_SCRIPT])` with timeout docker_cli.T_QUERY; on DockerError or rc != 0 the pid is None; else `strays.parse_tether_pid(captured.output)`. Store in self._tether_pid. When it ends up None: print "tether pid unknown; a stray process will reset the container" to stderr once per container life (reset the once-flag in reset()). Call it right after every `_wait_ready()` — in start() and in reset() — before `_init`.

3. Notices: `_queue_notice(self, kind, text)` appends (kind, text) under _notices_lock. `drain_notices(self) -> list` returns the list and clears it, under the lock. HostSandbox gets `def drain_notices(self): return []`. Add `def drain_notices(self) -> list: ...` to the Sandbox Protocol with a docstring: "(kind, text) notices the sandbox queued since the last drain, oldest first; kinds 'stray_kill' and 'sandbox_reset'; host mode has none."

4. `reset(self, reason, *, strays=None, strays_total=None)`: as today, plus: the sandbox_reset transcript event gains `strays=strays` and `strays_total=strays_total` ONLY when each is not None (never write null or []); after writing it, `_queue_notice("sandbox_reset", strays.sandbox_reset_text(reason))`; re-run `_discover_tether()` after `_wait_ready()`.

5. `_kill_strays(self) -> bool`: exec `docker_args.exec_argv(self.container, ["/bin/sh", "-c", strays.STRAY_KILL_SCRIPT, "_", str(self._tether_pid)])`, timeout T_QUERY; True iff rc == 0 (DockerError -> False).

6. `_sweep_locks(self) -> tuple[list, int | None]`: exec `docker_args.exec_argv(self.container, strays.LOCK_SWEEP_ARGV)`, timeout T_QUERY. DockerError -> print("lock sweep incomplete (<error>)", file=sys.stderr) and return ([], None). Else paths = strays.parse_locks(captured.output); if rc != 0 print(f"lock sweep incomplete (rc {rc})") to stderr; if captured.truncated print("lock sweep incomplete (output truncated)"); return strays.cap_locks(paths, captured.truncated).

7. `_reap(self) -> bool` — new body, same return meaning (True iff a reset happened):
   a. `docker top` as today; unreachable -> reset("container unreachable after bash"); return True.
   b. rows = strays.stray_rows(top.output). If rows is empty -> go to step g.
   c. capped, total = strays.cap_strays(rows). If self._tether_pid is None -> reset("stray process after bash", strays=capped, strays_total=total); return True.
   d. if not self._kill_strays(): same reset as c; return True.
   e. Settle re-check: up to 3 times: time.sleep(0.05) then `docker top`; unreachable -> reset("container unreachable after bash"); return True; if strays.stray_rows(...) is empty -> break; after the third dirty look -> the reset from c; return True.
   f. OOM inspect (today's code) FIRST: "true" -> reset("oom", strays=capped, strays_total=total); return True. Then locks, locks_total = self._sweep_locks(). Write the transcript event: `self.transcript.write("stray_kill", strays=capped, **({"strays_total": total} if total is not None else {}), **({"locks_removed": locks} if locks else {}), **({"locks_removed_total": locks_total} if locks_total is not None else {}))` (guard `self.transcript is not None`, same try/except as the reset event). `_queue_notice("stray_kill", strays.stray_kill_text(capped, total, locks))`. Return False (no reset; _reset_this_call untouched).
   g. no strays: the OOM inspect as today ("true" -> reset("oom"); return True); return False.
   Use `time.sleep` through a module attribute (`_SETTLE_SLEEP = 0.05` and `time.sleep`) so tests can monkeypatch `docker.time.sleep`.

8. Tests (tests/test_docker_sandbox.py). Fixtures: in `docker` and `started_with_transcript`, script the discovery exec by its full argv `["exec", "-w", "/work", "dw-abc123", "/bin/sh", "-c", strays.TETHER_DISCOVERY_SCRIPT]` -> `_ok(b"7\n")` so the in-place rung is ACTIVE by default; script the kill exec `[..., "/bin/sh", "-c", strays.STRAY_KILL_SCRIPT, "_", "7"]` -> _ok(); the sweep exec `["exec", "-w", "/work", "dw-abc123"] + strays.LOCK_SWEEP_ARGV` -> _ok(). Use the container name the fixtures already produce (check docker_args.container_name("abc123")). Then:
   a. discovery: b"7\n" -> _tether_pid == 7; rc 3 -> None and one stderr line (capsys); b"x\n" -> None; a DockerError -> None; after reset() discovery runs again (spy the exec count).
   b. happy path: `fake.script(["top"], [_ok(TOP_WITH_SLEEP), _ok(TOP_TETHER_ONLY)])` (the list pops per call) -> after `sb.bash("true")`: no ["kill", ...] call; the kill exec argv is exactly the scripted one; the sweep ran; an event {"event": "stray_kill", "strays": ["sleep 300"]} in the transcript, no "strays_total", no "locks_removed" when the sweep printed nothing; `sb.drain_notices() == [("stray_kill", <text starting "The sandbox killed 1 background process ">)]`; a second drain returns []; `_reset_this_call` False after the call; the budget sample exec still ran.
   c. sweep output b"/gitdir/index.lock\0/gitdir/gc.pid\0" -> "locks_removed" == both and the notice contains "Stale git lock files"; sweep DockerError -> stray_kill still written without locks_removed, no reset, stderr mentions "lock sweep incomplete".
   d. escalation: kill exec rc 3 -> ["kill", "dw-abc123"] called and a sandbox_reset event with reason "stray process after bash" and strays ["sleep 300"]; tops [dirty, dirty, dirty, dirty] -> the same reset (exactly 4 top calls); tops [dirty, dirty, clean] -> stray_kill (3 top calls); a re-check whose top returns rc 1 -> reset "container unreachable after bash"; _tether_pid None -> reset with strays and NO kill exec; OOM true after a clean re-check -> sandbox_reset reason "oom" with strays and no sweep exec.
   e. 25 stray rows -> "strays" has 20 entries and "strays_total" 25; a 300-char CMD is cut to 200 + "…".
   f. reset() direct call: event has no "strays" key; notice ("sandbox_reset", <text containing "re-initialized">) queued; HostSandbox().drain_notices() == [] (construct with a tmp worktree).
   g. every existing test still passes — the old reap tests that expected a `docker kill` on a stray row must be updated to expect the in-place path (or to script the kill exec as failing when they mean the reset path).

9. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` and make the whole suite pass. Then call finish with a summary.
```

- [ ] **Step 2: Run it**; **Step 3: Review** (the ladder order top → kill → settle → OOM → sweep → event/notice; sparse fields never null/`[]`; discovery after every `_wait_ready`; no `docker kill` on the happy path; the `stray_kill` event is written *before* the bash `tool_result` — check a transcript from Step 5); **Step 4: Host suite** green.
- [ ] **Step 5: Live check (Claude, host):** from the run worktree, `DIRTYWORK_LIVE_IMAGE=dirtywork-worker-pytest:0.10 /usr/bin/python3 -m pytest -q -m docker tests/test_docker_live.py -k "backgrounded or flood"` → `backgrounded` now leaves no `sandbox_reset` (check the transcript for `stray_kill`); `flood` still resets.
- [ ] **Step 6:** resume if needed, commit, ff-merge, cleanup, ledger row.

---

### Task W5: Serialization — `_reap_lock`, `wait=`, flags, shutdown, abandoned exec (spec §3.6, §3.8)

**Files:**
- Modify: `dirtywork/sandbox/docker.py` (`__init__`, `bash`, `grep`, `_after_bash`, `_reap`, `reset`, `_watchdog_kill`, `_sample_worktree`, `_stop_container`), `dirtywork/sandbox/watchdog.py:92-120`
- Modify: `tests/docker_fakes.py:94-115` (callable responses)
- Test: `tests/test_docker_sandbox.py`, `tests/test_watchdog.py`

**Interfaces:**
- Consumes: W4's `_reap`, `_kill_strays`, `reset(strays=)`.
- Produces: `DockerSandbox._reap_lock`, `_shutting_down`, `_sample_worktree(*, wait=True)`,
  `_kill_abandoned_exec()`, `_raise_violation()`; `Watchdog.check_worktree_budget_once(*, wait=True)`;
  `FakeDocker.script(prefix, callable)`.

- [ ] **Step 1: Write the brief** to `$SCRATCH/brief-61-w5.md`:

```
Issue #61, task W5 of 8: the docker sandbox's reap ladder and the watchdog's worktree sampling must never overlap (the watchdog's own `du/find` exec was being reported as a stray and reset the container — 7 of the 26 resets on record), and a watchdog kill must never trigger a second reset. Change dirtywork/sandbox/docker.py and dirtywork/sandbox/watchdog.py, extend tests/docker_fakes.py, with tests in tests/test_docker_sandbox.py and tests/test_watchdog.py. Do NOT edit docs/. Lock order everywhere: _reap_lock -> _reset_lock -> _notices_lock; reset() never takes _reap_lock.

1. `self._reap_lock = threading.Lock()` and `self._shutting_down = False` in __init__.

2. `_reap`: its WHOLE body (first top, kill, settle re-checks, OOM inspect, sweep, and any reset() it calls) runs inside `with self._reap_lock:`. Before EVERY `self.reset(...)` call inside _reap, check `self.watchdog is not None and self.watchdog.violation is not None` — if so, return True without calling reset (the watchdog already killed the container and recorded why).

3. `_sample_worktree(self, *, wait=True)`: if self._shutting_down: return None. Acquire _reap_lock with `blocking=wait`; if it could not be acquired (wait=False and busy): return None. Inside the lock: result = self._measure_worktree_once(); if result is not None: return it. If a watchdog violation is recorded (as in 2): return None. If self._reset_this_call: with wait=True raise SandboxError("worktree budget sample failed after an earlier reset this call") as today; with wait=False return None. Else reset("budget sample failed") once and re-measure; a second failure: wait=True -> raise SandboxError("worktree budget sample failed twice in a row") as today; wait=False -> return None. Release the lock on every path (use `try/finally`).

4. watchdog.py: `check_worktree_budget_once(self, *, wait=True)` calls `self.sample(wait=wait)`; when it returns None, return False without touching violation. `run()` calls `self.check_worktree_budget_once(wait=False)`. Update tests/test_watchdog.py's `sample=lambda: (1024, 5)` lambdas to accept the keyword (`lambda wait=True: (1024, 5)`), and add: a sample returning None -> False and no kill; run() passes wait=False (record the kwarg).

5. Flags. In reset(): the first statement inside `with self._reset_lock:` is `self._reset_this_call = True` (move it up from the end). In `_watchdog_kill`: set `self._reset_this_call = True` inside its `with self._reset_lock:` BEFORE the docker kill. If self._shutting_down, reset() returns immediately (no docker call, no event, no notice).

6. `_after_bash`: restructure as
   try:
       if self.watchdog is not None and self.watchdog.violation is not None: self._raise_violation()
       self._reap()
       if self.watchdog is not None:
           if not self._reset_this_call: self.watchdog.check_worktree_budget_once()
           if self.watchdog.violation is not None: self._raise_violation()
   finally:
       with self._reset_lock: self._reset_this_call = False
   where `_raise_violation()` is today's consume-and-raise block (reads violation + kind, clears them, raises SandboxError for "sandbox_error" else BudgetExceeded) extracted into a method — one copy.

7. `bash()`: wrap the `self._run(argv, ...)` call so that `self.watchdog.note_bash_end()` runs in a `finally:` (today it is skipped when the exec raises anything but DockerError, e.g. KeyboardInterrupt). Keep `_after_bash()` outside the finally, where it is now.

8. `_stop_container()`: first statement `self._shutting_down = True` (plain assignment, no lock).

9. `_kill_abandoned_exec(self)`: if self._tether_pid is None return; `with self._reap_lock:` run the same kill exec as `_kill_strays()` (call it) and ignore its result — no top, no sweep, no event, no notice. Call it in `grep()`'s `if e.timed_out:` branch before `return grep_timeout_result(timeout)`; docstring: any tool exec that continues after a timed-out DockerError must call this, because the in-container process is still running and the next bash call's reap would blame the worker for it.

10. tests/docker_fakes.py: `FakeDocker.run` — if the selected response is callable, return `response(argv)` (a list may also contain callables; pop as today then call). Document it in the class docstring: a callable lets a test block on a threading.Event to force an interleaving.

11. Tests (tests/test_docker_sandbox.py; use the fixture from task W4 where the rung is active):
   a. `_reap` holds `_reap_lock`: script ["top"] with a callable that sets an Event `entered` and waits on `release`; on a second thread call `sb._sample_worktree(wait=False)` after `entered` -> it returns None without any exec (spy calls); then set `release`; join the main bash call. The main thread's `_after_bash` sample afterwards runs normally (its exec is in fake.calls).
   b. lock order / no deadlock: a thread inside `sb.reset("x")` (scripted `kill` callable that waits on an Event) while the main thread calls `sb._reap()` with a stray row scripted -> both finish within a 5 s join; exactly one sandbox_reset event overall (the main thread's reset must see the violation-or-flag rules — assert the total count of ["kill", ...] calls is 1 or 2 and document which).
   c. flags: with a spy on fake.run, `sb.reset("x")` shows `_reset_this_call` True at the moment the ["kill", ...] call is made (check inside a callable response); same for `_watchdog_kill("disk")`.
   d. watchdog kill with recorded violation: set `sb.watchdog.violation = "host free space below 2048 MB"`, `violation_kind = "budget"`; script the sample exec to fail (rc 1); `sb.bash("true")` raises BudgetExceeded with that reason; NO ["kill", ...] call by reset(), no sandbox_reset event, no top call at all.
   e. `_after_bash` clears `_reset_this_call` in a finally: make reset() raise SandboxError (script ["start"]/ready to fail) -> after the raised bash call the flag is False.
   f. `_shutting_down`: after `sb._stop_container()`, `sb._sample_worktree(wait=True)` returns None and `sb.reset("x")` makes no docker call.
   g. `bash()` finally: a `_run` that raises KeyboardInterrupt -> the watchdog's `_bash_in_flight` is False afterwards (catch the exception in the test).
   h. grep timeout: script the grep exec to raise DockerError(timed_out=True) -> `sb.grep("x")` returns the timeout text AND the kill exec argv (STRAY_KILL_SCRIPT, "_", "7") appears exactly once in fake.calls; no top, no sweep, no transcript event, `drain_notices() == []`; with `_tether_pid = None` -> no kill exec.
   i. the timeout path through the ladder: script the bash exec to raise DockerError(timed_out=True) and tops [dirty, clean] -> the result is the timeout text and a stray_kill event exists.
   j. existing tests in tests/test_docker_sandbox.py and tests/test_watchdog.py keep passing (update lambdas as in 4).

12. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` and make the whole suite pass. Then call finish with a summary.
```

- [ ] **Step 2: Run it**; **Step 3: Review** against §3.6 line by line (lock order, every acquisition site, the violation re-read before every reset, `wait=False` never blocks, early flags in both kill paths, `finally` clear, shutdown, `bash()` finally, `grep` timeout); **Step 4: Host suite** green.
- [ ] **Step 5: Race regression (Claude, host):** run `$SCRATCH/p7/probe.py` against the run worktree's `dirtywork` package (`PYTHONPATH=<run worktree>`) → phase A **0/40** resets, **0** `stray_kill`; phase B′ → 0 resets. Keep the numbers for the ledger.
- [ ] **Step 6:** resume if needed, commit, ff-merge, cleanup, ledger row.

---

### Task W6: Runner — `drain_sandbox()` and its five call sites (spec §5.2)

**Files:**
- Modify: `dirtywork/runner.py:577-596` (near `deliver`), `:664` (`finish`), `:836-990` (`one_turn`), `:738-780` (`check_verify` callers)
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `sandbox.drain_notices()` (W4), read via `getattr(self.sandbox, "drain_notices", None)`.

- [ ] **Step 1: Write the brief** to `$SCRATCH/brief-61-w6.md`:

```
Issue #61, task W6 of 8: the runner must deliver the docker sandbox's notices (kinds "stray_kill" and "sandbox_reset") to the model through the existing #60 carriers, and record each as a `nudge` event. Change dirtywork/runner.py (inside Runner.run) with tests in tests/test_runner.py. Do NOT edit docs/. Read the existing closures first: deliver(), finish(), check_verify(), one_turn(), _join_nudges. Nothing here changes what deliver() does or adds any new `via` value.

1. Add a closure next to deliver():
   def drain_sandbox():
       """(joined text, nudge records) for every notice the sandbox queued since the last drain; ("", []) when none or when the sandbox has no drain_notices (host mode, test doubles)."""
       drain = getattr(self.sandbox, "drain_notices", None)
       notices = drain() if drain is not None else []
       records = [self.transcript.write("nudge", kind=kind, turn=turns) for kind, _text in notices]
       return _join_nudges(*(text for _kind, text in notices)), records
   (`turns` is the enclosing turn counter; `via` is stamped later by deliver(), exactly like the other nudge records.)

2. Call sites — exactly these, and no others:
   a. Ordinary tool-call turn (the last deliver in one_turn): `sandbox_text, sandbox_records = drain_sandbox()` right before that deliver, and the call becomes `deliver(_join_nudges(malformed_text, sandbox_text, timeout_text, stall_text), [malformed_record, *sandbox_records, timeout_record, stall_record])`.
   b. The `finish` turn that CONTINUES with verify feedback (the branch `if pending_finish is not None:` after `check_verify` returned `ended is None`): replace the timeout-only delivery with: `sandbox_text, sandbox_records = drain_sandbox()`; write the timeout nudge record as today when timed_out_this_turn; `text = _join_nudges(sandbox_text, timeout_text)`; `if text: deliver(text, [*sandbox_records, timeout_record])`. The feedback itself is already the finish result (resolve_finish) — do not append notices to it.
   c. The prose-answer path (`if kind == "answer":` after `check_verify(content, via="user")` returned feedback): `sandbox_text, sandbox_records = drain_sandbox()`; `deliver(_join_nudges(feedback, sandbox_text), sandbox_records)`.
   d. The text-only continuing turn (the `deliver(_join_nudges(NUDGES[kind], stall_text), ...)` call): `sandbox_text, sandbox_records = drain_sandbox()` before it; `deliver(_join_nudges(NUDGES[kind], sandbox_text, stall_text), [kind_record, *sandbox_records, stall_record])`.
   e. `finish(status, final)`: as its FIRST statement, `drain_sandbox()` — discard the text (the run is over; the records are written with no `via`).
   deliver() must never be called with empty text (it already returns early on "", keep that).

3. Tests in tests/test_runner.py. Build a sandbox double with a `drain_notices()` that returns a queued list once then [] (a small class next to the existing `_TimeoutSandbox`), and reuse the `parts`/provider-double style of the existing nudge tests (see test_empty_reply_is_nudged_not_completed and test_nudge_via_is_absent_on_the_turn_that_aborts_as_model_error):
   a. tool-call turn with a queued ("stray_kill", "KILLED") and a stall nudge on the same turn -> the last tool_result's follow_up == "KILLED\n\n<stall text>" (order: malformed, sandbox, timeout, stall — test with a malformed entry too: "<malformed text>\n\nKILLED"), nudge events kind="stray_kill" with via="tool_result", and the tool result's `result` field unchanged.
   b. finish + failing verify (verify feedback continues the run) with a notice queued by the verify bash -> the finish tool_result's `result` is the feedback text only and its `follow_up` == "KILLED"; the nudge record has via "tool_result".
   c. prose answer + failing verify -> the next user message == "<feedback>\n\nKILLED"; nudge via "user".
   d. text-only turn (empty reply) with a pending notice -> one user message "<empty nudge>\n\nKILLED"; via "user".
   e. run-ending turns: verify passes -> completed; max_turns; model_error after three empty replies -> a nudge event with kind "sandbox_reset" exists and has NO "via" key; nothing appended to history.
   f. a notice queued before a `finish` call in the same turn and verify passing -> recorded once (exactly one nudge event for it).
   g. a sandbox double WITHOUT drain_notices -> runs exactly as before (no AttributeError).
   h. ExplodingSandbox / BudgetBustingSandbox paths still end the run with their statuses.

4. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` and make the whole suite pass. Then call finish with a summary.
```

- [ ] **Step 2: Run it**; **Step 3: Review** (five sites and no sixth; `finish()` first statement; no text folded into `resolve_finish`; order of `_join_nudges`; `via` never a new value); **Step 4: Host suite** green; **Step 5:** resume if needed, commit, ff-merge, cleanup, ledger row.

---

### Task D1: Docs — transcript schema first, then the contract (Claude)

**Files:**
- Modify: `docs/transcript-schema.md` (line 10 "eight" → nine + forward-compatibility sentence; `### stray_kill` section; `sandbox_reset` table; `nudge` kind list and merge-order sentence; `dropped_git_entries` rows; event-order note; `runs show` line), `docs/machine-contract.md` (bash bullet, events paragraph, docker git-discovery bullet, `--verify` note, `dropped_git_entries`, the `--image` snippet), `docs/operating.md`, `docs/security.md`, `docker/README.md` (derived snippet, no-`GIT_DIR` line, checklist item 6), `docs/superpowers/specs/2026-08-15-review-response-design.md` (superseded notes), the ledger (S11/S13 rows, `env -u` note obsolete).

- [ ] **Step 1:** write `docs/transcript-schema.md` changes exactly per spec §6.1/§8 (the W7 worker's `test_transcript_schema.py` extension reads these tokens: `stray_kill`, `strays`, `strays_total`, `locks_removed`, `locks_removed_total`, kinds `stray_kill`/`sandbox_reset`).
- [ ] **Step 2:** the remaining docs per §8, including the `BASH_SPEC` sentence quoted there (W7 puts the same text on the wire).
- [ ] **Step 3:** `/usr/bin/python3 -m pytest -q tests/test_transcript_schema.py` still green; commit on `issue-61-sandbox-resets`.

---

### Task W7: Evidence — bench, runs show, soak harvest, bash wire text, schema test (spec §6.2, §8)

**Files:**
- Modify: `dirtywork/bench.py:48,200-233,250-266,285,348`, `dirtywork/runs.py:289-324,325-395`, `tools/soak_harvest.py:37,396,460`, `dirtywork/builtin_tools.py:226-237`, `tests/fixtures/tool_schemas.json`
- Test: `tests/test_bench.py`, `tests/test_runs.py`, `tests/test_transcript_schema.py`, `tests/test_soak_tools.py`, the schema-fixture test

**Interfaces:**
- Consumes: the `stray_kill` event and the two nudge kinds (W4/W6); the doc tokens from D1.
- Produces: `bench.EMPTY_REPLY_NUDGE_KINDS`, `runs._md_code(value, limit)`.

- [ ] **Step 1: Write the brief** to `$SCRATCH/brief-61-w7.md`:

```
Issue #61, task W7 of 8: surface the new docker-mode evidence — the `stray_kill` transcript event and the nudge kinds "stray_kill"/"sandbox_reset" — in bench, `runs show`, the soak harvester and the transcript-schema test, and update the bash tool's wire description. Change dirtywork/bench.py, dirtywork/runs.py, tools/soak_harvest.py, dirtywork/builtin_tools.py, tests/fixtures/tool_schemas.json, with tests. Do NOT edit docs/ (docs/transcript-schema.md already documents everything; the test reads it).

1. dirtywork/bench.py:
   a. `NUDGE_KINDS = ("stall", "empty", "truncated", "text_tool_call", "timeout", "malformed_entry", "stray_kill", "sandbox_reset")` — appended at the end.
   b. New constant `EMPTY_REPLY_NUDGE_KINDS = ("truncated", "empty", "text_tool_call")` with a comment: the kinds whose nudge path records a FailureTracker "empty_reply" — must equal tuple(runner.NUDGES). In `_harness_failures` replace the `non_stall = sum(... if kind not in ("stall", "timeout", "malformed_entry"))` with `non_stall = sum(counts[f"nudge_{kind}"] for kind in EMPTY_REPLY_NUDGE_KINDS)`; update the docstring.
   c. `_event_counts`: count "stray_kill" events under counts["stray_kill"] (initialise it beside "sandbox_reset").
   d. `run_one_bench_case`: the result row gains "stray_kills": counts["stray_kill"] right after "sandbox_resets" (both places: the skipped-acceptance default row near line 285 and the real row near line 348).
2. tools/soak_harvest.py: append "stray kills" to PER_RUN_COLUMNS (line ~37) and set it in _per_run_row (line ~460) from the counts' "stray_kill" key (0 when absent); the `nudges` total keeps following bench.NUDGE_KINDS.
3. dirtywork/runs.py:
   a. `_md_code(value, limit)`: text = _md_trim(value, limit) with "\r\n" and "\n" replaced by " "; delimiter = "`" * (longest backtick run inside text + 1) (min 1); return f"{delim} {text} {delim}" when text starts or ends with a backtick or when the delimiter is longer than one character, else f"{delim}{text}{delim}". No html.escape (a code span is literal). Docstring: CommonMark code span; `_md_inline` would HTML-escape and break on a backtick.
   b. `_timeline_line`: `stray_kill` -> f"{ts}  {name:<15} {len(strays)} killed — " + "; ".join(strays)[:120] (strays = event.get("strays") or []); `sandbox_reset` -> today's line plus f" — strays: {'; '.join(strays)[:120]}" when the event has "strays".
   c. `_md_event_lines`: `stray_kill` -> [f"> **stray_kill**: {n} process{'' if n == 1 else 'es'} killed — " + ", ".join(_md_code(s, MD_ARGS_CHARS) for s in strays) + (f", {m} lock file{'' if m == 1 else 's'} removed" if m else ""), ""] with n = event.get("strays_total") or len(strays) and m = event.get("locks_removed_total") or len(event.get("locks_removed") or []); `sandbox_reset` -> today's callout plus " — strays: " + the same _md_code join when present.
4. dirtywork/builtin_tools.py BASH_SPEC description: replace the sentence from "Backgrounded processes are terminated" to the end with: "Backgrounded processes are killed when the command returns. In docker mode you are told which; if they cannot be killed, or the container runs out of memory, it is reset: the working tree survives, but git state you created inside the sandbox (index changes, stashes, local commits) does not, and you are told when that happens." Regenerate tests/fixtures/tool_schemas.json so the golden-schema test passes (find the test that compares it and the way the fixture is produced; keep ensure_ascii=False and the file's existing formatting).
5. Tests:
   a. tests/test_bench.py: `_event_counts` counts a {"event": "stray_kill"} line; a run with only nudge kinds "stray_kill"/"sandbox_reset" reports harness "empty_reply" == 0 and "nudge_stray_kill" == 1; `EMPTY_REPLY_NUDGE_KINDS == tuple(runner.NUDGES)` (import dirtywork.runner); run_one_bench_case's row has "stray_kills"; the summarize detail `nudges` cell now has 8 slash-separated numbers and the legend line lists all 8 kinds; the --compare harness cell still has exactly four components.
   b. tests/test_runs.py: `_timeline_line` for a stray_kill event; `_md_event_lines` for a stray_kill event with strays ["a`b", "c|d *e*"] renders each in a code span that survives the backtick (assert "`` a`b ``" or the equivalent longer delimiter) and no HTML entity; a sandbox_reset event with strays gets the suffix; `_md_code("plain", 200) == "`plain`"`.
   c. tests/test_transcript_schema.py: add "stray_kill" to EVENT_NAMES; add "stray_kill" and "sandbox_reset" to NUDGE_KINDS; add a test that the tokens `strays`, `strays_total`, `locks_removed`, `locks_removed_total` appear in the `### stray_kill` section of the doc and `strays` in the `### sandbox_reset` section (split the doc text on "\n### " and search the matching chunk, not the whole document).
   d. tests/test_soak_tools.py: the per-run row has a "stray kills" value.
6. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` and make the whole suite pass. Then call finish with a summary.
```

- [ ] **Step 2: Run it**; **Step 3: Review** (order of `NUDGE_KINDS`; `EMPTY_REPLY_NUDGE_KINDS` equality; compare cell shape; `_md_code` on a backtick; the wire text byte-identical to D1's quoted sentence); **Step 4: Host suite** green; **Step 5:** resume if needed, commit, ff-merge, cleanup, ledger row.

---

### Task W8: Live docker tests + the image `ENV` (spec §7, §9 tests 13–21, 20a/20b)

**Files:**
- Modify: `tests/test_docker_live.py`, `docker/Dockerfile:24-27` (`ENV`), `docker/README.md` (checklist item 6 only — the prose is D1's)
- Test: the tests themselves (`@pytest.mark.docker`; Claude runs them on the host)

- [ ] **Step 1: Write the brief** to `$SCRATCH/brief-61-w8.md`:

```
Issue #61, task W8 of 8: add the live docker tests for the stray ladder and the gitfile layout to tests/test_docker_live.py, and add four ENV lines to docker/Dockerfile. You cannot run docker-marked tests inside this sandbox — write them carefully following the existing patterns (`_make_live_repo`, `_resp`, `_call`, `_run_docker_main`, `_assert_status`, reading events from payload["transcript"]; see test_docker_live_backgrounded_process_is_dead_after_reap and test_docker_live_process_flood_triggers_reset) and make sure the whole non-docker suite still passes and `python3 -m pytest --co -q -m docker tests/test_docker_live.py` collects them. Do NOT edit docs/ except the single checklist line named in item 10.

Each test: a fresh repo, a scripted response list, `_run_docker_main(monkeypatch, tmp_path, repo, responses)` (honour `DIRTYWORK_LIVE_IMAGE` the way the file already does), then assertions on the transcript events. Helper: `_events(payload)` returning the parsed list; `_of(events, name)`.

1. test_docker_live_stray_is_killed_in_place_and_stash_survives: calls: bash "echo x >> README.md && git stash && nohup sleep 300 >/dev/null 2>&1 & echo started" then bash "git stash pop && git diff --stat && cat /proc/1/comm" then finish. Expect: exactly one stray_kill event and no sandbox_reset; the second result contains "README.md" (the stash popped); the stray_kill event's strays contain a "sleep 300" entry; the tool_result for call 1 has a follow_up containing "The sandbox killed".
2. test_docker_live_cat_named_stray_dies_with_the_others: bash "mkfifo /tmp/f; setsid cat 0<>/tmp/f >/dev/null 2>&1 & sleep 300 >/dev/null 2>&1 & echo ok" then bash "ls /proc | grep -c '^[0-9]' " then finish: exactly one stray_kill; the second result's number is <= 4 (tini, tether, the shell, grep).
3. test_docker_live_killed_git_locks_are_swept: bash "touch /gitdir/index.lock /gitdir/gc.pid; sleep 300 >/dev/null 2>&1 & echo ok" then bash "git status --short; echo rc=$?" then finish: the stray_kill event has locks_removed containing "/gitdir/index.lock" and "/gitdir/gc.pid"; the second result contains "rc=0"; the follow_up mentions "Stale git lock files".
4. test_docker_live_git_init_in_tmp_stays_local (S13): bash "d=$(mktemp -d) && cd $d && git init -q && git status --short && git worktree list && git rev-parse --git-dir" then bash "cd /tmp && git -C /work status --short; echo rc=$?" then finish: the first result contains "/tmp/" in the worktree list line and ".git" as the git-dir; the second contains "rc=0"; no sandbox_reset, no stray_kill.
5. test_docker_live_nested_repos_export_as_plain_files: bash "mkdir -p sub && cd sub && git init -q && echo new > NEW.txt && echo mod >> ../README.md && mkdir -p deep/inner && cd deep/inner && git init -q && echo d > D.txt && mkdir -p /work/sub/__pycache__ && echo x > /work/sub/__pycache__/a.pyc && printf '__pycache__/\n' > /work/.gitignore" then finish: status completed; run.json's dropped_git_entries == ["sub/.git", "sub/deep/inner/.git"] (order as find lists them — sort before comparing); the exported worktree contains sub/NEW.txt and sub/deep/inner/D.txt and README.md with "mod", NOT sub/__pycache__/a.pyc; `git -C <worktree> ls-files -s` has no 160000 entry.
6. test_docker_live_race_loop_no_resets: 40 bash calls "sed -n 1,3p README.md" with the ScriptedClient (the runner adds ~5 s per turn only with a real model — so make each command "sleep 5.2; sed -n 1,3p README.md" to reproduce the timing) then finish: zero sandbox_reset, zero stray_kill. Mark it with a skip if os.environ.get("DIRTYWORK_LIVE_SLOW") is not set (it takes ~4 min).
7. test_docker_live_process_flood_triggers_reset (existing): extend its assertions: the sandbox_reset event has a non-empty "strays" list; the run continues (already asserted).
8. test_docker_live_dotnet_build_leaves_no_stray: skip unless `docker run --rm $IMAGE dotnet --version` succeeds (subprocess, 60 s); bash "dotnet new console -o app --framework net8.0 -o app && dotnet build app" then bash "echo ok" then finish: with the image env containing UseSharedCompilation=false (check `docker run --rm $IMAGE env`) expect no stray_kill and no sandbox_reset; without it expect exactly one stray_kill whose strays contain "VBCSCompiler".
9. test_docker_live_root_gitfile_tampering_20a: bash "rm .git; git status; echo rc=$?" then bash "cat .git 2>/dev/null; echo rc=$?" then bash ":(){ :|:& };:" (forces a reset via the pids flood, as the existing flood test does) then bash "cat .git" then finish: first result "rc=128"; the last result contains "gitdir: /gitdir".
   test_docker_live_root_gitfile_tampering_20b: bash "rm .git && git init -q && echo t > T.txt" then finish: completed, run.json dropped_git_entries == [".git"], the worktree contains T.txt.
10. test_docker_live_timed_out_grep_leaves_no_stray: a grep tool call with {"pattern": "x", "path": ".", "timeout": 1} against a repo where the pattern is slow — create a 200 MB file of "y" lines in a bash call first ("yes y | head -c 200000000 > big.txt") — then bash "echo ok" then finish: the grep result is the timeout text; no stray_kill and no sandbox_reset events.
11. docker/Dockerfile: extend the existing `ENV DOTNET_ROOT=...` instruction with four more lines (backslash continuation): DOTNET_CLI_USE_MSBUILD_SERVER=0, MSBUILDDISABLENODEREUSE=1, UseSharedCompilation=false, DOTNET_NOLOGO=1, with a one-line comment: no build daemon may outlive a bash call (#61). In docker/README.md, extend checklist item 6 under "### 1.0 image checklist" to say the 1.0 image bakes all five .NET variables so a derived FROM :1.0 needs none of them. Nothing else in docs.

12. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` (the docker tests stay deselected) and `python3 -m pytest --co -q -m docker tests/test_docker_live.py` — both must succeed. Then call finish with a summary.
```

- [ ] **Step 2: Run it**; **Step 3: Review** the tests against §9 (13–21, 20a/20b) and the Dockerfile diff; **Step 4: Host suite** green.
- [ ] **Step 5: Run the live suite (Claude, host):** `DIRTYWORK_LIVE_IMAGE=dirtywork-worker-pytest:0.10 DIRTYWORK_LIVE_SLOW=1 /usr/bin/python3 -m pytest -q -m docker tests/test_docker_live.py` from the run worktree; then build the dev image (`docker build -t dirtywork-worker-dev:issue61 docker/`) and run the `.NET` test against it (`DIRTYWORK_LIVE_IMAGE=dirtywork-worker-dev:issue61 … -k dotnet`). Fix wave via resume-with-feedback with the exact failures pasted.
- [ ] **Step 6:** commit, ff-merge, cleanup, ledger row.

---

### Task C9: Acceptance, S13 evidence, soak re-runs, PR (Claude)

- [ ] **Step 1: Full suites on `issue-61-sandbox-resets`:** unit (`/usr/bin/python3 -m pytest -q -p no:cacheprovider`) and live (`-m docker`, both images) green; record counts.
- [ ] **Step 2: S13 acceptance with the branch's own runtime:** from `.worktrees/issue-61-sandbox-resets`, `/usr/bin/python3 -m dirtywork run "run the test suite and finish" --repo /Users/jimschneider/repos/dirtywork --branch-from issue-61-sandbox-resets --model qwen/qwen3-coder-next --sandbox docker --image dirtywork-worker-pytest:0.10 --verify "python3 -m pytest -q -p no:cacheprovider" --max-turns 10` — the **plain** gate passes inside the sandbox (no `env -u`); the transcript shows no `sandbox_reset` and no `stray_kill`. Ledger row.
- [ ] **Step 3: Soak re-runs:** `D3-issue97` (the S11 run: `git stash` around `dotnet test`, `dirtywork-worker-net10:0.10` rebuilt with the four `ENV` lines, or the dev image) and one `run-bash-buildsh` (class A/D) with the branch runtime; assert the stash survives and the deliverable is complete; rows in the ledger, with the P7/P5 numbers alongside.
- [ ] **Step 4: Spec §10 sentence:** replace "until T1 lands, then the plain command" with the rule in this plan's execution model (every dogfood run keeps `env -u`; S13 acceptance = Step 2). Commit.
- [ ] **Step 5: Ledger metrics:** stop the sampler; per-window stats computed at the end (not from a snapshot); run totals (runs, turns, wall, prompt tokens, $0); which items Claude finished after a failed resume, if any.
- [ ] **Step 6: PR** from `issue-61-sandbox-resets`: "Closes #61", milestone 1.0.0, body = spec summary + evidence + the ledger link + the dogfood receipts; CI green (incl. the docker-live leg on amd64); wait for the owner's merge word.

## Self-review

- **Spec coverage:** §3.1–§3.5, §3.7 → W3/W4; §3.6, §3.8 → W5; §4.1–§4.3 → W1; §4.4 → W2; §5 → W4 (queue) + W6 (drain); §6.1 → W4 (events) + D1 (doc); §6.2 → W7; §7 → W8 (Dockerfile) + D1 (README prose); §8 → D1 (+ W7 wire text, W1 guardrails comment); §9 tests 1–7 → W4/W5 (W3 for 5's script checks), 8–9 → W1, 10 → W2, 11 → W6, 12 → W7, 13–21 + 20a/20b → W8 (20a's forced reset uses the pids flood), race regression → W5 step 5 and W8 test 6; §10 → C0/C9.
- **Placeholders:** none — every brief carries the code, argv and assertions the worker needs; the two Claude-only live checks (W2 step 5, W5 step 5) reuse the P8/P7 scripts already in the scratchpad.
- **Type consistency:** `drain_notices() -> list[tuple[str, str]]` (W4) is what W6 reads; `reset(reason, *, strays=None, strays_total=None)` (W4) is what W5's flag rules wrap; `strays.cap_strays/cap_locks` return `(list, int | None)` and W4 writes the `_total` keys only when not `None`; `check_worktree_budget_once(*, wait=True)` (W5) is called with the default by `_after_bash` and `wait=False` by `run()`; `EXPORT_GIT_ENTRIES_SCRIPT` is the only shell-string exec in W2 and is `dash -n`-tested in W3's sibling test style (the parse test in W2 exercises the argv).
