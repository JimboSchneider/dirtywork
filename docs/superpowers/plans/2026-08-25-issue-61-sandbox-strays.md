# #61 Sandbox strays — kill in place, gitfile discovery, reset notices: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **This repository's execution rule overrides the line above.** Every code task below is built
> by the **released dirtywork (0.10.1) running against this repository** with a local worker
> (`qwen/qwen3-coder-next` via LM Studio) — Claude writes the brief, reviews the branch, runs the
> host suites, feeds back through `dirtywork resume --feedback-file`, and writes the prose docs.
> A Claude implementer touches code only after a worker resume-with-feedback has failed, and the
> PR says so. Owner approval is needed for the merge and the release, never assumed.

**Plan v2** (2026-08-25 11:45 CDT): v1 reviewed against the spec and the code (26 findings: 3
brief-level Blockers — an assertion no script could satisfy, a parameter shadowing the `strays`
module, a `tree[...]` key mismatch — 14 Important, 9 Minor); all folded, and W2/W4/W5/W8 split
into halves sized for one 60-turn run each (calibration: #64's ~190-line change took 1 run + 3
resumes).

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
(v4, owner-approved 2026-08-25 11:17 CDT; wording of §3.3/§3.6/§9 aligned with this plan's v2).
Section numbers below refer to it.

## Global Constraints

- Python 3.9 floor, stdlib only; every change is additive under `schema_version` 2 (no field
  renamed or removed, no new `nudge.via` value) — spec header.
- The kill script is **fork-free dash**: builtins only, no `$( )`, no pipe, no backtick, no
  subshell (`|` occurs only inside the `||` guards) — §3.3. Scripts are module constants; dynamic
  values arrive as `"_", value` positional args — §3.
- Lock order `_reap_lock → _reset_lock → _notices_lock`; `reset()` never takes `_reap_lock` —
  §3.6.
- The worker container gets **no** `GIT_DIR`/`GIT_WORK_TREE`; the export container keeps both —
  §4.1.
- Redirect order in scripts is `2>/dev/null <` (dash applies redirections left to right) — §3.2.
- `Captured.output` merges stderr: every parsed stream is filtered (single-integer, NUL keep-rule,
  full-match regex) — §3.2, §3.4, §4.4.
- DRY & SOLID (owner's standing rule): one parser, one script builder, one text formatter; the
  ladder lives in `strays.py`, not spread through `docker.py`.
- The worker never edits `docs/**`; prose docs are Claude's (task D1). `docker/Dockerfile` and
  the one checklist line in `docker/README.md` are the worker's (W8a).

## Execution model (every W task)

- **Scratchpad** (absolute; pin it in a new session):
  `SCRATCH=/private/tmp/claude-501/-Users-jimschneider-repos-dirtywork/b8bd6636-8109-4636-90dd-6854fae4b3c8/scratchpad`
  — holds `run61.sh`, the briefs `brief-61-<task>.md` (extracted verbatim from this plan's fenced
  blocks), `metrics-61.csv`, `p7/probe.py` (race loop), `p8/export.log` + `p8/splice.py` (nested-repo
  fixture).
- **Run command** (`$SCRATCH/run61.sh $SCRATCH/brief-61-<task>.md`), which is:

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
  integration branch. Tasks run strictly in the order listed.
- **Review loop:** read `~/.dirtywork/runs/<slug>/run.json` + transcript; diff the run worktree
  against the brief and the spec section; run the host suite in the run worktree
  (`/usr/bin/python3 -m pytest -q -p no:cacheprovider`); gaps → `dirtywork resume <slug>
  --feedback-file <file> --max-turns 40` (verify inherited), at most two resumes; then Claude
  finishes leftovers and says so in the ledger and the PR.
- **Metrics:** `tools/soak_sampler.sh $SCRATCH/metrics-61.csv` started in C0 and stopped in C9;
  one ledger row per run (status, turns, wall, s/turn, prompt/completion tokens, tok/s, nudges,
  guardrail blocks, sandbox resets, tool mix, verify outcome) appended to the `#61` section of
  `docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md`.
- Give qwen ≥ 60 turns (it re-runs the suite every other turn); resumes burn turns on
  `read_file`, so feedback names files and lines.

## File structure

| file | responsibility after this plan |
|---|---|
| `dirtywork/sandbox/strays.py` (new) | script constants, `docker top` row parser, tether-pid / sweep parsers, caps, notice texts |
| `dirtywork/sandbox/docker_args.py` | `_base_env_args()` (both containers) + `_git_env_args()` (export only) |
| `dirtywork/sandbox/lifecycle.py` | `init_worker_git(..., layout)` |
| `dirtywork/sandbox/docker.py` | tether discovery, ladder, notices, locks/flags, shutdown, abandoned-exec kill |
| `dirtywork/sandbox/watchdog.py` | `check_worktree_budget_once(*, wait=True)`; `run()` samples with `wait=False` |
| `dirtywork/sandbox/export.py` | NUL-safe enumeration, nested-root splice, base-aware safety net; `EXPORT_GIT_ENTRIES_SCRIPT` |
| `dirtywork/sandbox/__init__.py`, `host.py` | `drain_notices()` documented on the Protocol; host returns `[]` |
| `dirtywork/runner.py` | `drain_sandbox()` and its five call sites |
| `dirtywork/bench.py`, `dirtywork/runs.py`, `tools/soak_harvest.py` | counts, columns, callouts, `_md_code` |
| `dirtywork/builtin_tools.py` + `tests/fixtures/tool_schemas.json` | bash description (wire) |
| `docker/Dockerfile`, `docker/README.md` | four `.NET` `ENV` lines (W8a); derived-image snippet + no-`GIT_DIR` line (D1); checklist item 6 (W8a) |
| `tests/docker_fakes.py` | callable responses (W3) |
| docs (`machine-contract`, `transcript-schema`, `operating`, `security`, `guardrails.py` comment) | Claude (D1), except the `guardrails.py` comment (W1) |

---

### Task C0: Baseline and instrumentation (Claude) — DONE 2026-08-25 11:30

- [x] Baseline suite on the integration branch: `1386 passed, 1 skipped, 27 deselected` (1387 collected).
- [x] `dirtywork-worker-pytest:0.10` present; `qwen/qwen3-coder-next` (65 536 ctx, PARALLEL 4) and Devstral loaded.
- [x] Sampler running: `tools/soak_sampler.sh $SCRATCH/metrics-61.csv` (pid file beside it).
- [x] Ledger section `## #61` opened with the run-row header.

---

### Task W1: Gitfile layout — env split and two init layouts (spec §4.1–§4.3)

**Files:**
- Modify: `dirtywork/sandbox/docker_args.py:120-134` (`_env_entrypoint_args`), `:159`, `:189`
- Modify: `dirtywork/sandbox/lifecycle.py:35-55` (`init_worker_git`)
- Modify: `dirtywork/sandbox/docker.py:308-309` (`_init`), `dirtywork/sandbox/export.py:245`
- Modify: `dirtywork/guardrails.py:85-87` (comment)
- Test: `tests/test_docker_args.py:115,160-186`, `tests/test_docker_sandbox.py:824-843,1425-1445`, `tests/test_export_flow.py`

**Interfaces:**
- Produces: `docker_args._base_env_args() -> list`, `docker_args._git_env_args() -> list`;
  `lifecycle.init_worker_git(run, name, *, branch, base_commit, restart, layout)` with
  `layout in ("gitfile", "env")`; `lifecycle.GITFILE_INIT_SCRIPT` / `ENV_INIT_SCRIPT` constants
  (format strings taking `branch`, `base_commit`, `populate`).

- [ ] **Step 1: Brief** `$SCRATCH/brief-61-w1.md`:

```
Issue #61, task W1 of 12: git discovery inside the docker sandbox must work through a gitfile, not through GIT_DIR/GIT_WORK_TREE exported into every worker command (finding S13: `git init` in a temp dir inside the sandbox lands in /gitdir). Change dirtywork/sandbox/docker_args.py, dirtywork/sandbox/lifecycle.py, dirtywork/sandbox/docker.py, dirtywork/sandbox/export.py and one comment in dirtywork/guardrails.py, with tests. Do NOT edit docs/. Keep it DRY: one script per layout, no duplicated env lists.

1. dirtywork/sandbox/docker_args.py: split `_env_entrypoint_args()` into two functions and delete the old one:
   - `_base_env_args()` returns exactly the old list WITHOUT the two GIT_DIR/GIT_WORK_TREE pairs (keep HOME, TMPDIR, LANG, the four GIT_AUTHOR_*/GIT_COMMITTER_* pairs, PATH, and `--entrypoint /bin/cat`, in the same order).
   - `_git_env_args()` returns `["-e", "GIT_DIR=/gitdir", "-e", "GIT_WORK_TREE=/work"]`.
   `worker_create_argv` uses `*_base_env_args()` only. `export_create_argv` uses `*_git_env_args(), *_base_env_args()` — in that order, so the export create argv stays byte-identical to today's (the export container keeps both variables because /work is mounted read-only there).

2. dirtywork/sandbox/lifecycle.py: `init_worker_git(run, name, *, branch, base_commit, restart, layout)` — `layout` is a required keyword, either "gitfile" or "env" (raise ValueError otherwise). Two module-level script constants, each a str.format template with {branch}, {base_commit}, {populate}:
   ENV_INIT_SCRIPT = today's script unchanged (used for layout="env").
   GITFILE_INIT_SCRIPT = "set -e; rm -rf -- /work/.git; /usr/bin/git init -q --template= --separate-git-dir=/gitdir; echo /repo.git/objects > /gitdir/objects/info/alternates; /usr/bin/git symbolic-ref HEAD refs/heads/{branch}; /usr/bin/git update-ref refs/heads/{branch} {base_commit}; {populate}"
   `populate` is "/usr/bin/git read-tree HEAD" when restart else "/usr/bin/git read-tree -m -u HEAD" (as today). Both layouts exec with `docker_args.exec_argv(name, ["/bin/sh", "-c", script], env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})` — the gitfile layout must NOT add GIT_DIR/GIT_WORK_TREE to the exec (the container env has none, and `git init --separate-git-dir` must not see them). Same error handling as today. Update the docstring: gitfile layout = worker container (writes /work/.git = "gitdir: /gitdir", idempotent for first start and after a reset; the rm -rf first so a directory the worker put at /work/.git never makes `git init` "move" a repository); env layout = export container.

3. dirtywork/sandbox/docker.py `_init`: pass `layout="gitfile"`. dirtywork/sandbox/export.py's `init_worker_git(...)` call: pass `layout="env"`.

4. dirtywork/guardrails.py lines ~85-87: reword the comment so it says the container's git is found through the gitfile /work/.git -> /gitdir (no GIT_DIR in the environment), not "its own throwaway /gitdir (see lifecycle.init_worker_git)" — keep the rest of that comment.

5. Tests:
   a. tests/test_docker_args.py: the worker create argv contains no "GIT_DIR=/gitdir" and no "GIT_WORK_TREE=/work" (update the existing assertion near line 115); the export create argv still contains both in the same positions as today (the full-list assertion near lines 160-186 must keep passing unchanged); `_base_env_args()` has no GIT_DIR entry and `_git_env_args()` is exactly the four-element list.
   b. tests/test_docker_sandbox.py: extend the existing init-script assertions (around lines 824-843 and 1425-1445) so the worker init script contains "rm -rf -- /work/.git" and "--separate-git-dir=/gitdir" and does NOT contain "GIT_DIR=" as an exec -e argument; keep the read-tree assertions.
   c. tests/test_export_flow.py: in test_export_run_happy_path assert the init exec's script does NOT contain "--separate-git-dir" (env layout) and the export create argv contains "GIT_DIR=/gitdir".
   d. A test that `init_worker_git(..., layout="bogus")` raises ValueError.

6. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` and make the whole suite pass (about 1387 tests before your change). Then call finish with a summary.
```

- [ ] **Step 2: Run** `$SCRATCH/run61.sh $SCRATCH/brief-61-w1.md`; record the slug.
- [ ] **Step 3: Review** the run worktree: `git diff issue-61-sandbox-resets` — the four-element `_git_env_args`, the gitfile script text byte-for-byte against §4.2, `layout="env"` at the export call, no `GIT_*` on the gitfile exec, the export create argv byte-identical, the guardrails comment.
- [ ] **Step 4: Host suite** in the run worktree: `/usr/bin/python3 -m pytest -q -p no:cacheprovider` → all green.
- [ ] **Step 5: Live check (Claude, host):** from the run worktree,
  `DIRTYWORK_LIVE_IMAGE=dirtywork-worker-pytest:0.10 /usr/bin/python3 -m pytest -q -m docker tests/test_docker_live.py -k "backgrounded or flood or reset"` — note the two named tests do not pass `_image_kwargs()` yet (W8a adds it), so they run on the default `:0.10` image, which is fine for this check.
- [ ] **Step 6:** resume if needed, commit the export, fix-commit, ff-merge, cleanup, ledger row.

---

### Task W2a: Export — enumeration, parsing, roots (spec §4.4 steps 1–3)

**Files:**
- Modify: `dirtywork/sandbox/export.py:245-256`
- Test: `tests/test_export_flow.py`

**Interfaces:**
- Consumes: `lifecycle.init_worker_git(..., layout="env")` (W1).
- Produces: `export.EXPORT_GIT_ENTRIES_SCRIPT: str`, `export.parse_git_entries(output: bytes) -> list[str]`
  (absolute `/work/...` tokens, find order), `export.nested_roots(entries) -> list[str]` (relative,
  descending depth then name), `export.children(root, roots) -> list[str]` (relative to `root`),
  `export.top_level_roots(roots) -> list[str]`. `git add -A` is still today's argv after W2a.

- [ ] **Step 1: Brief** `$SCRATCH/brief-61-w2a.md`:

```
Issue #61, task W2a of 12: the docker export enumerates `.git`-named entries under /work with a line-parsed `find` that would list the new root gitfile every run and cannot cope with newlines in names. Replace it with a NUL-safe enumeration plus three pure functions that later work (W2b) uses to export nested repositories as plain files. Change dirtywork/sandbox/export.py (only the find block right after `lifecycle.init_worker_git(...)` in export_run) with tests in tests/test_export_flow.py. Do NOT edit docs/. Leave `git add -A`, the `git diff --cached --name-only` step and everything after them unchanged.

1. Module constant EXPORT_GIT_ENTRIES_SCRIPT = r"exec /usr/bin/find /work -mindepth 1 -iname .git ! \( -path /work/.git -type f \) -prune -print0 2>/dev/null" — note the backslash-escaped parentheses: it runs under /bin/sh -c and unescaped parentheses are a shell syntax error. Replace today's find exec with `docker_args.exec_argv(name, ["/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT])`, timeout T_EXPORT_STEP.

2. `parse_git_entries(output: bytes) -> list[str]` (module-level): split on b"\0", DROP the last chunk (it is unterminated), decode each with errors="replace", keep only tokens that start with "/work/" and whose last path component .lower() == ".git"; order kept. In export_run: rc != 0 -> print(f"export: .git enumeration incomplete (rc {rc})", file=sys.stderr) and continue with what parsed; `captured.truncated` -> return _fail("could not enumerate .git entries"). `dropped_git_entries` = each token with the "/work/" prefix removed, in find order (not sorted).

3. Three module-level pure functions over RELATIVE paths (the tokens minus "/work/"):
   - `nested_roots(entries)`: for every entry with at least two "/"-separated components ("a/.git" -> "a", "a/b/.git" -> "a/b"; a bare ".git" contributes nothing) take the parent directory; deduplicate; sort by (descending number of components, then name).
   - `children(root, roots)`: every R2 in roots with R2.startswith(root + "/") for which NO other R3 in roots satisfies both R3.startswith(root + "/") and R2.startswith(R3 + "/") — i.e. the immediate nested roots — returned RELATIVE to root (strip root + "/"), in the roots list's order.
   - `top_level_roots(roots)`: the roots with no ancestor in the set, in the roots list's order.
   Compute `roots = nested_roots(dropped_git_entries)` in export_run and keep it in a local variable (W2b uses it); nothing else changes yet.

4. Tests in tests/test_export_flow.py (FakeDocker; script the find exec by its full argv ["exec", "-w", "/work", "dw-abc123-export", "/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT]; the generic ["exec"] default returns rc 0 empty):
   a. parse_git_entries(b"/work/a b/.git\0/work/x\ny/.git\0garbage\0/tmp/z/.git\0/work/w/.GIT\0/work/unterminated/.git") == ["/work/a b/.git", "/work/x\ny/.git", "/work/w/.GIT"].
   b. nested_roots(["a/.git", "a/b/.git", "c/.git", ".git"]) == ["a/b", "a", "c"]; children("a", ["a/b/c", "a/b", "a", "c"]) == ["b"]; children("a/b", ["a/b/c", "a/b", "a", "c"]) == ["c"]; children("c", [...]) == []; top_level_roots(["a/b/c", "a/b", "a", "c"]) == ["a", "c"].
   c. test_export_run_parses_dropped_git_entries: switch its scripted find output to the NUL format and assert dropped_git_entries keeps find order.
   d. find rc 1 with parseable output -> the export continues and "export: .git enumeration incomplete (rc 1)" is on stderr (capsys); find output with truncated=True -> export_status == "export_failed: could not enumerate .git entries".
   e. `subprocess.run(["sh", "-n", "-c", EXPORT_GIT_ENTRIES_SCRIPT])` returns 0 (skip if sh is missing) — the escaped parentheses must parse.
   f. test_export_run_happy_path still passes with the new find argv.

5. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` and make the whole suite pass. Then call finish with a summary.
```

- [ ] **Step 2: Run**; **Step 3: Review** (escaped parens; `-prune`; keep-rule; find order preserved; `add -A` untouched; `files_changed` step untouched); **Step 4: Host suite** green; **Step 5:** resume if needed, commit, ff-merge, cleanup, ledger row.

---

### Task W2b: Export — the splice and the base-aware safety net (spec §4.4 steps 4–6)

**Files:**
- Modify: `dirtywork/sandbox/export.py` (between the enumeration and `git write-tree`, and after it)
- Test: `tests/test_export_flow.py`

**Interfaces:**
- Consumes: `roots`, `children()`, `top_level_roots()` (W2a).
- Produces: the splice; `export_failed: nested repository at <path> was not masked`,
  `export_failed: could not verify the export index (<which>)`.

- [ ] **Step 1: Brief** `$SCRATCH/brief-61-w2b.md`:

```
Issue #61, task W2b of 12: the docker export must export a nested git repository's FILES like any other files and drop only its `.git` entry — today `git add -A` aborts on an uncommitted nested repo (`error: 'x/' does not have a commit checked out`) and turns a committed one into a gitlink. export_run in dirtywork/sandbox/export.py already computes `roots` (nested_roots) and has children()/top_level_roots() from the previous task — read them first. Change only the stretch from after the enumeration to just after `git write-tree`, with tests in tests/test_export_flow.py. Do NOT edit docs/. Every docker exec is an argv list; pathspecs are separate argv elements. Keep the existing `git diff --cached --name-only <base_commit>` step (files_changed / files_changed_truncated) exactly where it is — after the add, before write-tree — unchanged.

1. Splice, deepest first. `tree = {}` keyed by the FULL relative root. For the i-th root R in `roots` (already sorted deepest first), one docker exec per command, each built with `docker_args.exec_argv(name, argv, workdir="/work/" + R, env={"GIT_DIR": f"/tmp/nested-{i}", "GIT_WORK_TREE": "/work/" + R, "GIT_OBJECT_DIRECTORY": "/gitdir/objects", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})`, timeout T_EXPORT_STEP:
   a. ["/usr/bin/git", "init", "-q", "--template="]
   b. ["/usr/bin/git", "read-tree", "--empty"]
   c. ["/usr/bin/git", "-c", "core.excludesFile=/work/.gitignore", "add", "-A", "--", "."] + [f":(exclude,literal){c}" for c in children(R, roots)]
   d. for each child c (same order; c is RELATIVE to R): ["/usr/bin/git", "read-tree", f"--prefix={c}/", tree[f"{R}/{c}"]]
   e. ["/usr/bin/git", "write-tree"] -> tree[R] = output.decode().strip()
   Any rc != 0 or DockerError -> return _fail(f"nested repository splice failed at {R}: {output[:500]}").

2. Main index (default workdir/env as today's git execs): for each R in top_level_roots(roots): ["/usr/bin/git", "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", f":(literal){R}"] then ["/usr/bin/git", "read-tree", f"--prefix={R}/", tree[R]]. Then the add: ["/usr/bin/git", "add", "-A", "--", "."] + [f":(exclude,literal){R}" for R in top_level_roots(roots)] — when there are no roots this must be exactly today's ["/usr/bin/git", "add", "-A"] (no "--", no "."), so existing tests keep passing. Print one line per top-level root to stderr: f"nested repository exported as plain files: {R}". Any rc != 0 -> _fail as today's add failure does.

3. Safety net, right after `git write-tree` succeeds (before the diff --stat step): run ["/usr/bin/git", "ls-files", "-s", "-z"] and ["/usr/bin/git", "ls-tree", "-r", "-z", base_commit] (T_EXPORT_STEP each). For either: rc != 0, DockerError, or captured.truncated -> return _fail(f"could not verify the export index ({which})") with which = "ls-files" or "ls-tree". Parse both on b"\0" (drop the unterminated tail): an ls-files record is "<mode> <sha> <stage>\t<path>", an ls-tree record is "<mode> <type> <sha>\t<path>". new_gitlinks = paths whose ls-files mode is "160000" and whose ls-tree mode is not "160000" (absent counts as not). Any -> return _fail(f"nested repository at {path} was not masked") for the first such path in index order.

4. Tests in tests/test_export_flow.py (script each git exec by its full argv; the export container is "dw-abc123-export"; nested execs carry "-w", "/work/<R>" and the five "-e" pairs in the order GIT_DIR, GIT_WORK_TREE, GIT_OBJECT_DIRECTORY, GIT_CONFIG_GLOBAL, GIT_CONFIG_NOSYSTEM — check docker_args.exec_argv for the exact positions):
   a. entries "/work/a/.git\0/work/a/b/.git\0": assert the exact exec sequence — nested-0 for "a/b" (init, read-tree --empty, add with no exclusion, write-tree), nested-1 for "a" (add with ":(exclude,literal)b", then read-tree "--prefix=b/" with nested-0's tree, write-tree), then main "rm -r -q --cached --ignore-unmatch -- :(literal)a", "read-tree --prefix=a/ <tree of a>", and the final add ["/usr/bin/git", "add", "-A", "--", ".", ":(exclude,literal)a"]; dropped_git_entries == ["a/.git", "a/b/.git"]; stderr has "nested repository exported as plain files: a".
   b. no entries -> the add argv is exactly ["/usr/bin/git", "add", "-A"], and the ls-files/ls-tree execs still run after write-tree.
   c. ls-files output b"160000 <40 hex> 0\tvendor/x\0100644 <40 hex> 0\tREADME.md\0" with ls-tree lacking vendor/x -> export_status == "export_failed: nested repository at vendor/x was not masked"; the same path present as "160000 commit <sha>\tvendor/x" in ls-tree -> export succeeds; ls-files rc 1 -> "export_failed: could not verify the export index (ls-files)"; ls-tree truncated -> "... (ls-tree)".
   d. a nested splice exec returning rc 128 -> export_status starts with "export_failed: nested repository splice failed at a".
   e. test_export_run_happy_path and test_export_run_reports_files_changed still pass (the files_changed step is untouched).

5. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` and make the whole suite pass. Then call finish with a summary.
```

- [ ] **Step 2: Run**; **Step 3: Review** against §4.4 steps 4–6 (`/tmp/nested-<i>` flat; `GIT_OBJECT_DIRECTORY`; `-w /work/<R>`; `core.excludesFile`; `tree[f"{R}/{c}"]`; `:(literal)` on `rm`; trailing slash on `--prefix`; the `files_changed` step still between add and write-tree; base-aware, fail-closed net; `dropped_git_entries` in find order); **Step 4: Host suite** green.
- [ ] **Step 5: Live splice check (Claude, host):** replay the P8 fixture through the branch's runtime — from the run worktree, `PYTHONPATH=. /usr/bin/python3 $SCRATCH/p8/splice.py --via-dirtywork` is not available; instead run a scripted two-call task with `python3 -m dirtywork run --sandbox docker --image dirtywork-worker-pytest:0.10 …` whose first bash call recreates P8's six nested roots and the ordinary edits, then assert `run.json`'s `dropped_git_entries` (six entries) and the exported worktree's diff (12 entries, no gitlink) as P8 did. Keep the driver as `$SCRATCH/p8-replay.py`; it becomes live test 16 in W8a.
- [ ] **Step 6:** resume if needed, commit, ff-merge, cleanup, ledger row.

---

### Task W3: `strays.py` — constants, parsers, texts; callable fakes (spec §3.1–§3.5, §5.3, §6.1)

**Files:**
- Create: `dirtywork/sandbox/strays.py`
- Modify: `dirtywork/sandbox/docker.py:857-907` (`_reap` uses `strays.stray_rows`), `tests/docker_fakes.py:94-115`
- Test: `tests/test_strays.py` (new)

**Interfaces:**
- Produces (all in `dirtywork.sandbox.strays`): `TETHER_DISCOVERY_SCRIPT`, `STRAY_KILL_SCRIPT`,
  `LOCK_SWEEP_ARGV`, `LOCK_PATH_RE`, `TETHER_CMDS`, `MAX_STRAYS = 20`, `MAX_STRAY_CHARS = 200`,
  `MAX_LOCKS = 20`, `NOTICE_CMDS = 3`, `NOTICE_CMD_CHARS = 80`,
  `stray_rows(top_output: bytes) -> list[str]`, `parse_tether_pid(output: bytes) -> int | None`,
  `parse_locks(output: bytes) -> list[str]`, `cap_strays(rows) -> tuple[list[str], int | None]`,
  `cap_locks(paths, truncated: bool) -> tuple[list[str], int | None]`,
  `stray_kill_text(strays, total, locks_removed) -> str`, `sandbox_reset_text(reason) -> str`;
  `FakeDocker.script(prefix, callable)` where the callable takes `argv` and may return a
  `Captured` or raise.

- [ ] **Step 1: Brief** `$SCRATCH/brief-61-w3.md`:

```
Issue #61, task W3 of 12: create the module dirtywork/sandbox/strays.py — pure functions and constants for the docker sandbox's stray-process handling — with tests in a new tests/test_strays.py; make DockerSandbox._reap in dirtywork/sandbox/docker.py use its row parser; and let tests/docker_fakes.py script a callable response. No docker calls in the new module; no other behaviour change. Do NOT edit docs/.

1. Constants (exact text; raw triple-quoted strings for the two scripts — /bin/sh -c takes multi-line scripts):
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
   Comment above STRAY_KILL_SCRIPT: it must stay fork-free (dash builtins only — read, [, kill, for, exit; no $( ), no pipe, no backtick, no subshell; the only "|" characters are the "||" of the two guards) because it has to run inside a pids-saturated container; the tether pid arrives as "$1"; the glob is re-expanded per pass so a process forked between passes is caught.
LOCK_SWEEP_ARGV = ["/usr/bin/find", "/gitdir", "(", "-name", "*.lock", "-o", "-name", "gc.pid", ")", "-type", "f", "-delete", "-print0"]
LOCK_PATH_RE = re.compile(r"^/gitdir/(?:.+/)?(?:[^/]+\.lock|gc\.pid)$")
MAX_STRAYS = 20; MAX_STRAY_CHARS = 200; MAX_LOCKS = 20; NOTICE_CMDS = 3; NOTICE_CMD_CHARS = 80
TETHER_CMDS = ("cat", "/bin/cat")

2. `stray_rows(top_output: bytes) -> list[str]`: move the row parsing DockerSandbox._reap does today (decode, header split, `line.split(None, n - 1)`, CMD = last field, skip rows whose CMD is in TETHER_CMDS or ends with "docker-init -- cat" or "docker-init -- /bin/cat") into this function; return the CMD of every other row in order ([] when only the tether is present or the output is empty). In docker.py, `_reap` must `from . import strays` and call `strays.stray_rows(top.output)`, resetting when the list is non-empty — same behaviour as today, one parser.

3. `parse_tether_pid(output: bytes) -> int | None`: decode (errors="replace"), strip; return int(text) if the whole stripped text matches ^[0-9]+$ and the value is > 0, else None.

4. `parse_locks(output: bytes) -> list[str]`: split on b"\0", drop the last (unterminated) chunk, decode each, keep those that LOCK_PATH_RE.fullmatch — order kept.

5. `cap_strays(rows)` -> (the first MAX_STRAYS rows, each cut to MAX_STRAY_CHARS with a trailing "…" when cut, and len(rows) if len(rows) > MAX_STRAYS else None). `cap_locks(paths, truncated)` -> (the first MAX_LOCKS paths uncut, and len(paths) if len(paths) > MAX_LOCKS and not truncated else None).

6. Texts. `stray_kill_text(strays, total, locks_removed)`: n = total or len(strays); cmds = "; ".join(s[:NOTICE_CMD_CHARS] for s in strays[:NOTICE_CMDS]) plus, when n > NOTICE_CMDS, f"; +{n - NOTICE_CMDS} more". Returns f"The sandbox killed {n} background process{'' if n == 1 else 'es'} your last command left running ({cmds}). A process cannot outlive the bash call that started it — start and use anything you need within one command." + (" Stale git lock files they left in the repository were removed." if locks_removed else "") + " Run `git status` to confirm the repository state before continuing."
   `sandbox_reset_text(reason)`: f"The sandbox container was reset after your last command ({reason}). Files in the worktree are intact, but git metadata was re-initialized: the index, stashes, local commits and branches you created inside the sandbox are gone, and the branch is back at the run's base commit with your file changes uncommitted. Run `git status` before continuing."

7. tests/docker_fakes.py: in `FakeDocker.run`, after selecting the response (and popping from a list as today), if it is callable return `response(argv)` — the callable may return a Captured or raise (e.g. docker_cli.DockerError) so a test can script an exec failure, or block on a threading.Event to force an interleaving. Document that in the class docstring.

8. tests/test_strays.py (pytest, parametrize where natural):
   a. STRAY_KILL_SCRIPT contains none of "$(", "`", "(" and no pipe — assert "|" not in STRAY_KILL_SCRIPT.replace("||", "") (the two "|| exit 3" guards are the only bars); TETHER_DISCOVERY_SCRIPT contains "2>/dev/null <" and not '< "$p/comm" 2>'; STRAY_KILL_SCRIPT has "for pass in 1 2 3"; both scripts: subprocess.run(["sh", "-n", "-c", SCRIPT]).returncode == 0 (skip if sh is missing).
   b. stray_rows: a real docker top header ("UID PID PPID C STIME TTY TIME CMD") + rows: tether-only (a "/sbin/docker-init -- /bin/cat" row and a "/bin/cat" row) -> []; with "sleep 300" and "bash -c (while true; do sleep 2; done) >/dev/null 2>&1 &" rows -> those two CMDs in order; a bare "cat" row is treated as tether (documented loophole); b"" -> [].
   c. parse_tether_pid: b"7\n" -> 7; b"sh: 1: cannot open /proc/9/comm: No such file\n7\n" -> None; b"" -> None; b"0\n" -> None; b"7\n8\n" -> None.
   d. parse_locks: b"/gitdir/index.lock\0/gitdir/gc.pid\0/gitdir/refs/heads/x.lock\0find: '/gitdir/y': Permission denied\n/gitdir/z.lock\0/gitdir/tail.lock" -> ["/gitdir/index.lock", "/gitdir/gc.pid", "/gitdir/refs/heads/x.lock"]; "/gitdir/objects/tmp_obj_x\0" -> [].
   e. cap_strays with 25 rows -> 20 entries and total 25; a 300-char row -> 200 chars + "…"; 3 rows -> total None. cap_locks with 25 paths, truncated=False -> total 25; truncated=True -> total None.
   f. the two texts: n == 1 wording ("process "), the "; +2 more" suffix for 5 strays, the lock sentence only when locks_removed is non-empty, "git status" always present; sandbox_reset_text("oom") contains "(oom)".
   g. FakeDocker: a scripted callable that returns a Captured is returned; one that raises DockerError propagates; a list containing a callable pops it.
   h. tests/test_docker_sandbox.py's existing reap tests still pass (no new ones needed here).

9. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` and make the whole suite pass. Then call finish with a summary.
```

- [ ] **Step 2: Run**; **Step 3: Review** (script text byte-exact vs §3.2/§3.3; `LOCK_PATH_RE` vs §3.4; texts vs §5.3; `_reap` uses the shared parser and no second copy remains; `FakeDocker` callables); **Step 4: Host suite** green; **Step 5:** resume if needed, commit, ff-merge, cleanup, ledger row.

---

### Task W4a: Sandbox state — tether discovery, notices, `reset(strays=)` (spec §3.2, §3.7, §5.1, §6.1)

**Files:**
- Modify: `dirtywork/sandbox/docker.py` (`__init__`, `start`, `reset`), `dirtywork/sandbox/host.py`, `dirtywork/sandbox/__init__.py`
- Test: `tests/test_docker_sandbox.py` (fixtures `docker`, `started`, `started_with_transcript` + new tests)

**Interfaces:**
- Consumes: `strays.py` (W3).
- Produces: `DockerSandbox._tether_pid`, `_discover_tether() -> None`, `_queue_notice(kind, text)`,
  `drain_notices() -> list[tuple[str, str]]` (also on `HostSandbox` → `[]`; on the Protocol),
  `reset(reason, *, strays=None, strays_total=None)`; `docker.py` imports
  `from . import strays` **and** `from .strays import sandbox_reset_text, stray_kill_text`;
  the shared fixtures script the discovery exec with pid 7.

- [ ] **Step 1: Brief** `$SCRATCH/brief-61-w4a.md`:

```
Issue #61, task W4a of 12: give DockerSandbox (dirtywork/sandbox/docker.py) the state the in-place stray kill (next task) needs — the tether's in-container pid, a notice queue the runner drains, and a reset() that records which strays caused it — plus the Protocol/host side of the queue. Tests in tests/test_docker_sandbox.py. Use dirtywork/sandbox/strays.py (read it first; do not duplicate). Do NOT edit docs/. No locks beyond the small _notices_lock; _reap is NOT changed in this task.

IMPORTANT naming rule: reset()'s new keyword parameter is called `strays` (spec-mandated), which shadows the module `strays` inside that method. So docker.py imports BOTH `from . import strays` (used everywhere else) AND `from .strays import sandbox_reset_text, stray_kill_text, parse_tether_pid`, and uses those bare names inside reset() (and later inside _reap).

1. State in __init__: `self._tether_pid = None`, `self._tether_warned = False`, `self._notices = []`, `self._notices_lock = threading.Lock()`.

2. `_discover_tether(self)`: exec `docker_args.exec_argv(self.container, ["/bin/sh", "-c", strays.TETHER_DISCOVERY_SCRIPT])` with timeout docker_cli.T_QUERY; DockerError or rc != 0 -> pid None; else `parse_tether_pid(captured.output)`. Store in self._tether_pid. When it ends up None and not self._tether_warned: print("tether pid unknown; a stray process will reset the container", file=sys.stderr) and set the flag; reset() clears the flag (new container life). Call `_discover_tether()` right after every `_wait_ready()` — in start() and in reset() — before `_init`.

3. Notices: `_queue_notice(self, kind, text)` appends (kind, text) under _notices_lock. `drain_notices(self) -> list` returns the queued list and clears it, under the lock. HostSandbox gets `def drain_notices(self) -> list: return []`. Add `def drain_notices(self) -> list: ...` to the Sandbox Protocol in dirtywork/sandbox/__init__.py with a docstring: "(kind, text) notices the sandbox queued since the last drain, oldest first; kinds 'stray_kill' and 'sandbox_reset'; host mode has none."

4. `reset(self, reason, *, strays=None, strays_total=None)`: as today (kill, wait, tether, ready, init, event, flag), plus: after `_wait_ready()` call `_discover_tether()`; the sandbox_reset transcript event includes `strays=strays` only when strays is not None and `strays_total=strays_total` only when strays_total is not None (never write null or []); after writing the event, `_queue_notice("sandbox_reset", sandbox_reset_text(reason))`. Keep the existing try/except around the transcript write.

5. Tests (tests/test_docker_sandbox.py). Fixtures: in `docker` and `started_with_transcript`, script the discovery exec by its full argv `["exec", "-w", "/work", "dw-abc123", "/bin/sh", "-c", strays.TETHER_DISCOVERY_SCRIPT]` -> `_ok(b"7\n")` (the fixtures' slug is "abc123", container "dw-abc123"; check docker_args.exec_argv for the exact shape). Then:
   a. after the fixture's start(), `sb._tether_pid == 7`; scripting the discovery exec to rc 3 -> None and exactly one stderr line (capsys) even after two failed discoveries; output b"x\n" -> None; a scripted callable raising DockerError -> None; after `sb.reset("x")` the discovery exec ran again (count its argv in fake.calls) and a warned flag is reset.
   b. `sb.reset("x")` direct: the sandbox_reset event has no "strays"/"strays_total" keys; `sb.drain_notices() == [("sandbox_reset", <text containing "re-initialized">)]`; a second drain returns [].
   c. `sb.reset("stray process after bash", strays=["sleep 300"], strays_total=None)` -> the event has "strays": ["sleep 300"] and no "strays_total"; with strays_total=25 -> "strays_total": 25.
   d. HostSandbox(tmp_path).drain_notices() == [] (construct it the way tests/test_sandbox_host.py does).
   e. every existing test still passes (the fixtures' generic ["exec"] scripting is now overridden for the discovery argv only).

6. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` and make the whole suite pass. Then call finish with a summary.
```

- [ ] **Step 2: Run**; **Step 3: Review** (both imports present; bare `sandbox_reset_text` inside `reset`; discovery after every `_wait_ready`; sparse fields never null/`[]`; `_notices_lock` never held across a docker call); **Step 4: Host suite** green; **Step 5:** resume if needed, commit, ff-merge, cleanup, ledger row.

---

### Task W4b: The ladder — kill, settle, OOM, sweep, `stray_kill` (spec §3.3–§3.5, §6.1)

**Files:**
- Modify: `dirtywork/sandbox/docker.py:857-907` (`_reap`) + two new methods
- Test: `tests/test_docker_sandbox.py:789-1030` (existing reap/reset tests) + new tests

**Interfaces:**
- Consumes: W3 + W4a (`_tether_pid`, `_queue_notice`, `reset(strays=)`, the fixture scripting).
- Produces: `_kill_strays() -> bool`, `_sweep_locks() -> tuple[list[str], int | None]`,
  `docker._SETTLE_SLEEP = 0.05`, the `stray_kill` transcript event.

- [ ] **Step 1: Brief** `$SCRATCH/brief-61-w4b.md`:

```
Issue #61, task W4b of 12: in docker mode a stray background process after a bash call must be killed IN PLACE, and the container reset only when that fails — today every stray costs a `docker kill` and the worker's /gitdir (index, stashes, commits). Rewrite DockerSandbox._reap in dirtywork/sandbox/docker.py and add two helpers, with tests in tests/test_docker_sandbox.py. The previous tasks gave you dirtywork/sandbox/strays.py (constants, parsers, texts), `self._tether_pid`, `_queue_notice`, `reset(reason, *, strays=None, strays_total=None)` and fixtures where the discovery exec answers pid 7 — read them first. Use the bare names `stray_kill_text` (already imported) and `strays.<name>` for the rest. Do NOT edit docs/. Locks and the watchdog interplay are the NEXT tasks — do not add any lock here.

1. `_kill_strays(self) -> bool`: exec `docker_args.exec_argv(self.container, ["/bin/sh", "-c", strays.STRAY_KILL_SCRIPT, "_", str(self._tether_pid)])`, timeout docker_cli.T_QUERY; True iff rc == 0 (DockerError -> False).

2. `_sweep_locks(self) -> tuple[list, int | None]`: exec `docker_args.exec_argv(self.container, strays.LOCK_SWEEP_ARGV)`, timeout T_QUERY. DockerError -> print(f"lock sweep incomplete ({e})", file=sys.stderr) and return ([], None). Else paths = strays.parse_locks(captured.output); if rc != 0: print(f"lock sweep incomplete (rc {rc})", file=sys.stderr); if captured.truncated: print("lock sweep incomplete (output truncated)", file=sys.stderr); return strays.cap_locks(paths, captured.truncated).

3. Module-level `_SETTLE_SLEEP = 0.05`; the re-check below calls `time.sleep(_SETTLE_SLEEP)` through the module's `time` so tests can monkeypatch `docker.time.sleep`.

4. `_reap(self) -> bool` — new body; keep today's FIRST statement verbatim (`if self._reset_this_call: return False` — at most one reset per bash call) and today's return meaning (True iff a reset happened). Update the docstring. Then:
   a. `docker top` as today; unreachable (DockerError or rc != 0) -> self.reset("container unreachable after bash"); return True.
   b. rows = strays.stray_rows(top.output). If rows is empty -> go to g.
   c. capped, total = strays.cap_strays(rows). If self._tether_pid is None -> self.reset("stray process after bash", strays=capped, strays_total=total); return True.
   d. if not self._kill_strays(): the same reset as c; return True.
   e. Settle re-check, up to 3 times: time.sleep(_SETTLE_SLEEP) then `docker top`; unreachable -> self.reset("container unreachable after bash"); return True; if strays.stray_rows(...) is empty -> break; after the third dirty look -> the reset from c; return True.
   f. OOM inspect (today's `docker inspect --format {{.State.OOMKilled}}`) FIRST: "true" -> self.reset("oom", strays=capped, strays_total=total); return True. Then locks, locks_total = self._sweep_locks(). Write the event (inside the same `if self.transcript is not None:` + try/except pattern reset() uses): fields = {"strays": capped}; add "strays_total": total when total is not None; add "locks_removed": locks when locks is non-empty; add "locks_removed_total": locks_total when it is not None; `self.transcript.write("stray_kill", **fields)`. Then `self._queue_notice("stray_kill", stray_kill_text(capped, total, locks))`. Return False (no reset; _reset_this_call untouched).
   g. no strays: the OOM inspect as today ("true" -> self.reset("oom"); return True); return False.

5. Tests (tests/test_docker_sandbox.py). Add to the shared fixtures `docker` and `started_with_transcript`: the kill exec `["exec", "-w", "/work", "dw-abc123", "/bin/sh", "-c", strays.STRAY_KILL_SCRIPT, "_", "7"]` -> _ok(), and the sweep exec `["exec", "-w", "/work", "dw-abc123"] + strays.LOCK_SWEEP_ARGV` -> _ok(). Define two top outputs: TOP_TETHER_ONLY (header + docker-init row + /bin/cat row) and TOP_WITH_SLEEP (the same plus a "sleep 300" row). Then:
   a. happy path: `fake.script(["top"], [_ok(TOP_WITH_SLEEP), _ok(TOP_TETHER_ONLY)])` -> after `sb.bash("true")`: no ["kill", ...] call; the kill exec argv above appears once; the sweep ran; the transcript has {"event": "stray_kill", "strays": ["sleep 300"]} with no "strays_total" and no "locks_removed"; `sb.drain_notices() == [("stray_kill", <text starting "The sandbox killed 1 background process ">)]`; `sb._reset_this_call` is False after the call; the budget sample exec (_SAMPLE_ARGV) still ran; exactly two ["top", ...] calls; the OOM inspect ran between the second top and the sweep (check call order).
   b. sweep output b"/gitdir/index.lock\0/gitdir/gc.pid\0" -> "locks_removed" == both and the notice contains "Stale git lock files"; sweep scripted as a callable raising DockerError -> stray_kill still written without "locks_removed", no ["kill", ...], stderr contains "lock sweep incomplete".
   c. escalation: kill exec rc 3 -> a ["kill", "dw-abc123"] call and a sandbox_reset event with reason "stray process after bash" and "strays": ["sleep 300"]; tops [dirty, dirty, dirty, dirty] -> the same reset and exactly 4 top calls; tops [dirty, dirty, clean] -> stray_kill and 3 top calls; a re-check whose top returns rc 1 -> reset "container unreachable after bash"; `sb._tether_pid = None` -> reset with strays and NO kill exec; OOM "true" after a clean re-check -> sandbox_reset reason "oom" carrying "strays" and NO sweep exec; no stray_kill event on any of these paths.
   d. 25 stray rows -> "strays" has 20 entries and "strays_total" 25; a 300-char CMD is cut to 200 + "…".
   e. Three existing tests drive a reset from a stray "sleep 300" top row and must be updated by name: test_reap_resets_and_writes_sandbox_reset_event_on_stray_process (~line 789) becomes the escalation case — script the kill exec to rc 3 so the reset still happens and also assert the event's "strays"; test_reset_uses_restart_variant_init (~824) and test_reset_creates_a_fresh_tether (~845) keep their subject — script the kill exec to rc 3 there too. Every other existing test must pass unchanged.

6. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` and make the whole suite pass. Then call finish with a summary.
```

- [ ] **Step 2: Run**; **Step 3: Review** (the guard kept as the first statement; ladder order top → kill → settle ≤3 → OOM → sweep → event/notice; no `stray_kill` written on any path that resets — grep the escalation test's transcript; sparse fields never null/`[]`; no `docker kill` on the happy path); **Step 4: Host suite** green.
- [ ] **Step 5: Live check (Claude, host):** from the run worktree, `DIRTYWORK_LIVE_IMAGE=dirtywork-worker-pytest:0.10 /usr/bin/python3 -m pytest -q -m docker tests/test_docker_live.py -k "backgrounded or flood"` → `backgrounded` leaves no `sandbox_reset` (its transcript has a `stray_kill`); `flood` still resets. Both run on the default image until W8a adds `_image_kwargs()`.
- [ ] **Step 6:** resume if needed, commit, ff-merge, cleanup, ledger row.

---

### Task W5a: Serialization — `_reap_lock`, `_sample_worktree(wait=)`, watchdog `wait=` (spec §3.6 first half)

**Files:**
- Modify: `dirtywork/sandbox/docker.py` (`__init__`, `_reap`, `_sample_worktree`), `dirtywork/sandbox/watchdog.py:92-120`
- Test: `tests/test_docker_sandbox.py`, `tests/test_watchdog.py:19-60,96-130,190-200`

**Interfaces:**
- Consumes: W4b's `_reap`.
- Produces: `DockerSandbox._reap_lock`, `_sample_worktree(*, wait=True)`;
  `Watchdog.check_worktree_budget_once(*, wait=True)` forwarding `sample(wait=wait)`.

- [ ] **Step 1: Brief** `$SCRATCH/brief-61-w5a.md`:

```
Issue #61, task W5a of 12: the docker sandbox's reap ladder and the watchdog's worktree sampling must never overlap — the watchdog's own `du/find` exec was being reported as a stray and reset the container (7 of the 26 resets on record). Add a lock in dirtywork/sandbox/docker.py and a non-blocking sampling mode in dirtywork/sandbox/watchdog.py, with tests in tests/test_docker_sandbox.py and tests/test_watchdog.py. Do NOT edit docs/. Lock order everywhere: _reap_lock -> _reset_lock -> _notices_lock; reset() never takes _reap_lock. Flags, shutdown and the grep timeout are the NEXT task.

1. `self._reap_lock = threading.Lock()` in __init__.

2. `_reap`: its WHOLE body after the first-statement guard (first top, kill, settle re-checks, OOM inspect, sweep, and any reset() it calls) runs inside `with self._reap_lock:`. Update the docstring: True means "a reset happened" (the next task widens it).

3. `_sample_worktree(self, *, wait=True)` — acquire _reap_lock with `self._reap_lock.acquire(blocking=wait)`; if that returns False (wait=False and the lock is busy): return None immediately. Inside a try/finally that releases the lock on every path:
   - result = self._measure_worktree_once(); if result is not None: return it.
   - wait=True: today's behaviour exactly — if not self._reset_this_call: reset("budget sample failed") once, re-measure, return the result or raise SandboxError("worktree budget sample failed twice in a row"); else raise SandboxError("worktree budget sample failed after an earlier reset this call").
   - wait=False (the watchdog thread): if self._reset_this_call: return None; else reset("budget sample failed") once, re-measure, and return the result or None — this path resets at most once and never raises (the main thread's wait=True sample after the call is the one that escalates).
   Keep the D2 docstring and extend it with the two modes.

4. dirtywork/sandbox/watchdog.py: `check_worktree_budget_once(self, *, wait=True)` calls `self.sample(wait=wait)`; when the sample returns None, return False without touching violation/kill. `run()` calls `self.check_worktree_budget_once(wait=False)`. In tests/test_watchdog.py update every sample callback to accept the keyword — the lambdas `sample=lambda: (1024, 5)` become `lambda wait=True: (1024, 5)` and the two def-style callbacks (`def fake_sample():` near line 106 and `def raising_sample():` near line 195) gain `wait=True` — and add: a sample returning None -> check_worktree_budget_once() is False, no kill, violation None; run() passes wait=False (record the kwarg in a fake sample and assert it).

5. Tests in tests/test_docker_sandbox.py (FakeDocker responses may be callables — see tests/docker_fakes.py):
   a. `_reap` holds the lock: script ["top"] with a callable that sets a threading.Event `entered`, waits (with timeout) on an Event `release`, then returns _ok(TOP_TETHER_ONLY); run `sb.bash("true")` on a thread; after `entered`, on the main thread call `sb._sample_worktree(wait=False)` -> it returns None and made NO docker call (compare fake.calls before/after); set `release`; join the bash thread (timeout 5 s). Afterwards `sb._sample_worktree(wait=True)` runs the sample exec and returns (1024, 5).
   b. wait=False with a failing measure and `_reset_this_call` False -> exactly one reset() (spy) and the re-measure result returned; failing twice -> None returned, no exception; with `_reset_this_call` True -> None and no reset.
   c. wait=True keeps today's semantics: the three existing _sample_worktree tests (test_sample_worktree_failure_then_success_after_reset, ..._no_retry_when_reset_already_happened_this_call, ..._still_retries_once_with_reset_when_no_reset_yet_this_call) pass unchanged.
   d. a reset() started on another thread (its ["kill", ...] scripted as a callable that waits on an Event) while the main thread calls `sb._reap()` with a stray row scripted -> both finish within 5 s (no deadlock); assert the main thread's `_reap` did not start its docker calls before the other thread's reset released `_reset_lock` (use the call order).

6. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` and make the whole suite pass. Then call finish with a summary.
```

- [ ] **Step 2: Run**; **Step 3: Review** — mechanical checks: `grep -n "with self._reset_lock" dirtywork/sandbox/docker.py` and confirm none of those blocks calls `_reap`, `_sample_worktree` or (later) `_kill_abandoned_exec`; read `_sample_worktree` end to end and confirm every return/raise path is inside the `try/finally` that releases `_reap_lock`, including the `wait=False` early return; `run()` passes `wait=False`; **Step 4: Host suite** green; **Step 5:** resume if needed, commit, ff-merge, cleanup, ledger row.

---

### Task W5b: Flags, violation consumption, shutdown, `bash()` finally, abandoned exec (spec §3.6 second half, §3.8)

**Files:**
- Modify: `dirtywork/sandbox/docker.py` (`bash`, `grep`, `_after_bash`, `_reap`, `reset`, `_watchdog_kill`, `_sample_worktree`, `_stop_container`)
- Test: `tests/test_docker_sandbox.py:967-1000` + new tests

**Interfaces:**
- Consumes: W5a's lock and modes.
- Produces: `_raise_violation()`, `_shutting_down`, `_kill_abandoned_exec()`.

- [ ] **Step 1: Brief** `$SCRATCH/brief-61-w5b.md`:

```
Issue #61, task W5b of 12: close the remaining self-inflicted-reset windows in dirtywork/sandbox/docker.py — a watchdog kill must never lead to a second reset, a reset must never start after shutdown began, a KeyboardInterrupt during a bash exec must not leave the watchdog thinking a call is in flight, and a timed-out grep must not leave a harness process for the next reap to blame on the worker. Tests in tests/test_docker_sandbox.py. The previous task added `_reap_lock` and `_sample_worktree(*, wait)`; read them first. Do NOT edit docs/.

1. `_raise_violation(self)`: extract today's consume-and-raise block from the end of `_after_bash` (read violation + kind, clear them, raise SandboxError for kind "sandbox_error" else BudgetExceeded) into this method — one copy.

2. `_after_bash(self)`:
   try:
       if self.watchdog is not None and self.watchdog.violation is not None: self._raise_violation()
       self._reap()
       if self.watchdog is not None:
           if not self._reset_this_call: self.watchdog.check_worktree_budget_once()
           if self.watchdog.violation is not None: self._raise_violation()
   finally:
       with self._reset_lock: self._reset_this_call = False
   Docstring: a violation is consumed BEFORE the reap (the watchdog killed the container and recorded why — nothing to reap, sample or reset) and again after the sample.

3. Violation re-read before any reset: in `_reap`, immediately before EACH `self.reset(...)` call, `if self.watchdog is not None and self.watchdog.violation is not None: return True` (the container is already dead by a watchdog kill; do not sample). Extend `_reap`'s docstring: True means "a reset happened OR the container is already dead by a watchdog kill — do not sample". In `_sample_worktree`, before its reset() call and before raising SandboxError, the same check -> return None.

4. Flags: in reset(), the FIRST statement inside `with self._reset_lock:` is `self._reset_this_call = True` (move it up from the end). In `_watchdog_kill`, set `self._reset_this_call = True` inside its `with self._reset_lock:` BEFORE the docker kill.

5. Shutdown: `self._shutting_down = False` in __init__; `_stop_container()` sets it True as its first statement (plain assignment, no lock). When it is set: `_sample_worktree` returns None immediately (both modes) and `reset()` returns immediately without any docker call, event or notice.

6. `bash()`: move `self.watchdog.note_bash_end()` into a `finally:` around the `self._run(argv, ...)` call so it always runs (today it is skipped when the exec raises anything but DockerError, e.g. KeyboardInterrupt). Keep `_after_bash()` outside that finally, where it is now, on both paths.

7. `_kill_abandoned_exec(self)`: if self._tether_pid is None: return; `with self._reap_lock:` call `self._kill_strays()` and ignore the result — no top, no sweep, no event, no notice. Call it in `grep()`'s `if e.timed_out:` branch before `return grep_timeout_result(timeout)`. Docstring: any tool exec that continues after a timed-out DockerError must call this — the in-container process is still running and the next bash call's reap would otherwise blame the worker's command for it.

8. Tests (tests/test_docker_sandbox.py):
   a. watchdog kill with a recorded violation: set `sb.watchdog.violation = "host free space below 2048 MB"`, `sb.watchdog.violation_kind = "budget"`; `sb.bash("true")` raises BudgetExceeded with that reason; NO ["top", ...] call, NO ["kill", ...] call, no sandbox_reset event, and the violation is cleared afterwards.
   b. a violation recorded DURING the reap (a ["top"] callable that sets watchdog.violation then returns a dirty top) -> `_reap` returns True without calling reset (no ["kill", ...]), and `_after_bash` raises BudgetExceeded.
   c. flags: with a ["kill", ...] response scripted as a callable that records `sb._reset_this_call` at call time: `sb.reset("x")` -> True at that moment; `sb._watchdog_kill("disk")` -> True at that moment.
   d. `_after_bash` clears the flag in the finally: script reset() to raise SandboxError (make `["start"]` / the ready exec fail) -> after the raising bash call `sb._reset_this_call` is False.
   e. shutdown: after `sb._stop_container()`, `sb._sample_worktree(wait=True)` is None, `sb._sample_worktree(wait=False)` is None, and `sb.reset("x")` makes no docker call and writes no event.
   f. `bash()` finally: a `_run` (fake) that raises KeyboardInterrupt -> the exception propagates and `sb.watchdog._bash_in_flight` is False afterwards.
   g. grep timeout: script the grep exec (see grep()'s argv: rg or grep -rn — use the existing grep tests as the pattern) as a callable raising docker_cli.DockerError(..., timed_out=True) -> `sb.grep("x")` returns the timeout text AND the kill exec argv (STRAY_KILL_SCRIPT, "_", "7") appears exactly once in fake.calls; no top, no sweep, no transcript event, `sb.drain_notices() == []`; with `sb._tether_pid = None` -> no kill exec.
   h. the timeout path through the ladder: script the bash exec as a callable raising DockerError(timed_out=True) and tops [dirty, clean] -> the result is the timeout text and a stray_kill event exists.
   i. test_after_bash_raises_budget_exceeded_even_when_reap_reset_this_call (~line 985) inverts under item 2: a violation recorded BEFORE the call is now consumed before the reap — rewrite it as test_after_bash_consumes_a_recorded_violation_before_the_reap asserting BudgetExceeded, ZERO ["kill", ...] calls, no ["top", ...] call and no sandbox_reset event. test_after_bash_skips_budget_sample_when_reap_already_reset (~967) is unchanged. Every other test in tests/test_docker_sandbox.py and tests/test_watchdog.py keeps passing.

9. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` and make the whole suite pass. Then call finish with a summary.
```

- [ ] **Step 2: Run**; **Step 3: Review** against §3.6/§3.8 (the pre-check; re-read before every reset and before the sample's raise; early flags in both kill paths; `finally` clear; shutdown; `bash()` finally with `_after_bash` outside it; `grep` timeout); repeat W5a's mechanical lock checks including `_kill_abandoned_exec`; **Step 4: Host suite** green.
- [ ] **Step 5: Race regression (Claude, host):** `PYTHONPATH=<run worktree> /usr/bin/python3 $SCRATCH/p7/probe.py` → phase A **0/40** resets, **0** `stray_kill`; `probe2.py` (forced overlap) → 0 resets. Numbers to the ledger.
- [ ] **Step 6:** resume if needed, commit, ff-merge, cleanup, ledger row.

---

### Task W6: Runner — `drain_sandbox()` and its five call sites (spec §5.2)

**Files:**
- Modify: `dirtywork/runner.py:577-596` (near `deliver`), `:664` (`finish`), `:780-990` (`one_turn`; its `deliver` sites at ~:849 and ~:983; `check_verify` callers at ~:838 and ~:949)
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `sandbox.drain_notices()` (W4a), read via `getattr(self.sandbox, "drain_notices", None)`.

- [ ] **Step 1: Brief** `$SCRATCH/brief-61-w6.md`:

```
Issue #61, task W6 of 12: the runner must deliver the docker sandbox's notices (kinds "stray_kill" and "sandbox_reset") to the model through the existing #60 carriers, and record each as a `nudge` event. Change dirtywork/runner.py (inside Runner.run) with tests in tests/test_runner.py. Do NOT edit docs/. Read the existing closures first: deliver(), finish(), check_verify(), one_turn(), _join_nudges. Nothing here changes what deliver() does or adds any new `via` value; nothing is appended to the verify feedback text.

1. Add a closure next to deliver():
   def drain_sandbox():
       """(joined text, nudge records) for every notice the sandbox queued since the last drain; ("", []) when none or when the sandbox has no drain_notices (host mode, test doubles)."""
       drain = getattr(self.sandbox, "drain_notices", None)
       notices = drain() if drain is not None else []
       records = [self.transcript.write("nudge", kind=kind, turn=turns) for kind, _text in notices]
       return _join_nudges(*(text for _kind, text in notices)), records
   (`turns` is the enclosing turn counter; `via` is stamped later by deliver(), exactly like the other nudge records.)

2. Call sites — exactly these five, and no others (grep -c 'drain_sandbox()' must be 6: the definition plus five calls; none inside check_verify or run_verify):
   a. Ordinary tool-call turn (the last deliver in one_turn): `sandbox_text, sandbox_records = drain_sandbox()` right before that deliver, and the call becomes `deliver(_join_nudges(malformed_text, sandbox_text, timeout_text, stall_text), [malformed_record, *sandbox_records, timeout_record, stall_record])`.
   b. The `finish` turn that CONTINUES with verify feedback (the branch `if pending_finish is not None:` after `check_verify` returned `ended is None`): replace the timeout-only delivery with: `sandbox_text, sandbox_records = drain_sandbox()`; write the timeout nudge record as today when timed_out_this_turn (else None); `text = _join_nudges(sandbox_text, timeout_text)`; `if text: deliver(text, [*sandbox_records, timeout_record])`. The feedback itself is already the finish result (resolve_finish) — do not append notices to it.
   c. The prose-answer path (`if kind == "answer":` after `check_verify(content, via="user")` returned feedback): `sandbox_text, sandbox_records = drain_sandbox()`; `deliver(_join_nudges(feedback, sandbox_text), sandbox_records)`.
   d. The text-only continuing turn (the `deliver(_join_nudges(NUDGES[kind], stall_text), ...)` call): `sandbox_text, sandbox_records = drain_sandbox()` before it; `deliver(_join_nudges(NUDGES[kind], sandbox_text, stall_text), [kind_record, *sandbox_records, stall_record])`.
   e. `finish(status, final)`: as its FIRST statement, `drain_sandbox()` — discard the text (the run is over; the records are written with no `via`).
   deliver() must never be called with empty text (it already returns early on "", keep that).

3. Tests in tests/test_runner.py. Build a sandbox double with a `drain_notices()` that returns a queued list once then [] (a small class next to the existing `_TimeoutSandbox`, wrapping whatever base double the file's runner tests use), and reuse the `parts`/provider-double style of the existing nudge tests (see test_empty_reply_is_nudged_not_completed and test_nudge_via_is_absent_on_the_turn_that_aborts_as_model_error):
   a. tool-call turn with a queued ("stray_kill", "KILLED") and a stall nudge on the same turn -> the last tool_result's follow_up == "KILLED\n\n<stall text>"; with a malformed entry as well -> "<malformed text>\n\nKILLED\n\n<stall text>"; nudge events kind="stray_kill" with via="tool_result"; the tool result's `result` field unchanged.
   b. finish + failing verify (feedback continues the run) with a notice queued by the verify bash -> the finish tool_result's `result` is the feedback text only and its `follow_up` == "KILLED"; the nudge record has via "tool_result".
   c. prose answer + failing verify -> the next user message == "<feedback>\n\nKILLED"; nudge via "user".
   d. text-only turn (empty reply) with a pending notice -> one user message "<empty nudge>\n\nKILLED"; via "user".
   e. run-ending turns: verify passes -> completed; max_turns; model_error after three empty replies -> a nudge event with kind "sandbox_reset" exists and has NO "via" key; nothing appended to history.
   f. a notice queued before a `finish` call in the same turn and verify passing -> recorded exactly once.
   g. a sandbox double WITHOUT drain_notices -> runs exactly as before (no AttributeError).
   h. ExplodingSandbox / BudgetBustingSandbox paths still end the run with their statuses.

4. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` and make the whole suite pass. Then call finish with a summary.
```

- [ ] **Step 2: Run**; **Step 3: Review** (`grep -c 'drain_sandbox()' dirtywork/runner.py` → 6; none inside `check_verify`/`run_verify`; `finish()` first statement; no text folded into `resolve_finish`; `_join_nudges` order; `via` never a new value); **Step 4: Host suite** green; **Step 5:** resume if needed, commit, ff-merge, cleanup, ledger row.

---

### Task D1: Docs — transcript schema first, then the contract (Claude)

**Files:**
- Modify: `docs/transcript-schema.md` (line 10 "eight" → nine + forward-compatibility sentence; `### stray_kill` section; `sandbox_reset` table; `nudge` kind list and merge-order sentence; `dropped_git_entries` rows; event-order note; `runs show` line), `docs/machine-contract.md` (bash bullet, events paragraph, docker git-discovery bullet, `--verify` note, `dropped_git_entries`, the `--image` snippet's four variables), `docs/operating.md`, `docs/security.md`, `docker/README.md` (derived snippet and the no-`GIT_DIR`/`GIT_WORK_TREE` line only — checklist item 6 is W8a's), `docs/superpowers/specs/2026-08-15-review-response-design.md` (superseded notes), the ledger (S11/S13 rows, `env -u` note obsolete).

- [ ] **Step 1:** write `docs/transcript-schema.md` exactly per spec §6.1/§8 (W7's schema test reads the tokens `stray_kill`, `strays`, `strays_total`, `locks_removed`, `locks_removed_total`, kinds `stray_kill`/`sandbox_reset`).
- [ ] **Step 2:** the remaining docs per §8, including the `BASH_SPEC` sentence quoted there (W7 puts the same text on the wire).
- [ ] **Step 3:** `/usr/bin/python3 -m pytest -q tests/test_transcript_schema.py` still green; commit on `issue-61-sandbox-resets`.

---

### Task W7: Evidence — bench, runs show, soak harvest, bash wire text, schema test (spec §6.2, §8)

**Files:**
- Modify: `dirtywork/bench.py:48,200-233,250-266,285,348`, `dirtywork/runs.py:289-324,325-395`, `tools/soak_harvest.py:37,396,460`, `dirtywork/builtin_tools.py:226-237`, `tests/fixtures/tool_schemas.json`
- Test: `tests/test_bench.py`, `tests/test_runs.py`, `tests/test_transcript_schema.py`, `tests/test_soak_tools.py`, `tests/test_builtin_tools.py` (frozen-schema test)

**Interfaces:**
- Consumes: the `stray_kill` event and the two nudge kinds (W4b/W6); the doc tokens from D1.
- Produces: `bench.EMPTY_REPLY_NUDGE_KINDS`, `runs._md_code(value, limit)`.

- [ ] **Step 1: Brief** `$SCRATCH/brief-61-w7.md`:

```
Issue #61, task W7 of 12: surface the new docker-mode evidence — the `stray_kill` transcript event and the nudge kinds "stray_kill"/"sandbox_reset" — in bench, `runs show`, the soak harvester and the transcript-schema test, and update the bash tool's wire description. Change dirtywork/bench.py, dirtywork/runs.py, tools/soak_harvest.py, dirtywork/builtin_tools.py, tests/fixtures/tool_schemas.json, with tests. Do NOT edit docs/ (docs/transcript-schema.md already documents everything; the test reads it).

1. dirtywork/bench.py:
   a. `NUDGE_KINDS = ("stall", "empty", "truncated", "text_tool_call", "timeout", "malformed_entry", "stray_kill", "sandbox_reset")` — the two appended at the end.
   b. New constant `EMPTY_REPLY_NUDGE_KINDS = ("truncated", "empty", "text_tool_call")` with a comment: the kinds whose nudge path records a FailureTracker "empty_reply" — must equal tuple(runner.NUDGES) (same order). In `_harness_failures` replace `non_stall = sum(... if kind not in ("stall", "timeout", "malformed_entry"))` with `non_stall = sum(counts[f"nudge_{kind}"] for kind in EMPTY_REPLY_NUDGE_KINDS)`; update the docstring.
   c. `_event_counts`: count "stray_kill" events under counts["stray_kill"] (initialise it beside "sandbox_reset").
   d. `run_one_bench_case`: the result row gains "stray_kills": counts["stray_kill"] right after "sandbox_resets" (both places: the skipped-acceptance default row near line 285 with 0, and the real row near line 348).
2. tools/soak_harvest.py: append "stray kills" to PER_RUN_COLUMNS (line ~37) and set it in _per_run_row (line ~460) from the counts' "stray_kill" key (0 when absent); the `nudges` total keeps following bench.NUDGE_KINDS.
3. dirtywork/runs.py:
   a. `_md_code(value, limit)`: text = _md_trim(value, limit) with "\r\n" and "\n" replaced by " "; delim = "`" * (longest backtick run inside text + 1); return f"{delim} {text} {delim}" when len(delim) > 1 or text starts/ends with a backtick, else f"{delim}{text}{delim}". No html.escape (a code span is literal). Docstring: CommonMark code span; `_md_inline` would HTML-escape and break on a backtick.
   b. `_timeline_line`: `stray_kill` -> f"{ts}  {name:<15} {n} killed — " + "; ".join(strays)[:120] where strays = event.get("strays") or [] and n = event.get("strays_total") or len(strays); `sandbox_reset` -> today's line plus f" — strays: {'; '.join(strays)[:120]}" when the event has "strays".
   c. `_md_event_lines`: `stray_kill` -> [f"> **stray_kill**: {n} process{'' if n == 1 else 'es'} killed — " + ", ".join(_md_code(s, MD_ARGS_CHARS) for s in strays) + (f", {m} lock file{'' if m == 1 else 's'} removed" if m else ""), ""] with m = event.get("locks_removed_total") or len(event.get("locks_removed") or []); `sandbox_reset` -> today's callout plus " — strays: " + the same _md_code join when "strays" is present.
4. dirtywork/builtin_tools.py BASH_SPEC description: replace everything from "Backgrounded processes are terminated" to the end of the description with: "Backgrounded processes are killed when the command returns. In docker mode you are told which; if they cannot be killed, or the container runs out of memory, it is reset: the working tree survives, but git state you created inside the sandbox (index changes, stashes, local commits) does not, and you are told when that happens." Then regenerate tests/fixtures/tool_schemas.json with the one-liner quoted in the comment above tests/test_builtin_tools.py's frozen-schema test (python3 -c "import json; from dirtywork.builtin_tools import default_registry; open('tests/fixtures/tool_schemas.json','w',encoding='utf-8').write(json.dumps(default_registry().schemas(), indent=2, ensure_ascii=False) + '\n')") and check the diff is only the bash description.
5. Tests:
   a. tests/test_bench.py: `_event_counts` counts a {"event": "stray_kill"} line; a run whose only nudges are kinds "stray_kill" and "sandbox_reset" reports harness "empty_reply" == 0 and "nudge_stray_kill" == 1; `EMPTY_REPLY_NUDGE_KINDS == tuple(runner.NUDGES)` (import dirtywork.runner); run_one_bench_case's row has "stray_kills"; the summarize detail `nudges` cell has 8 slash-separated numbers and the legend line lists all 8 kinds; the --compare harness cell still has exactly four components.
   b. tests/test_runs.py: `_timeline_line` for a stray_kill event; `_md_event_lines` for a stray_kill event with strays ["a`b", "c|d *e*"] renders each in a code span that survives the backtick (assert "`` a`b ``" is present) and contains no "&lt;"/"&amp;" entity; a sandbox_reset event with strays gets the suffix; `_md_code("plain", 200) == "`plain`"`.
   c. tests/test_transcript_schema.py: add "stray_kill" to EVENT_NAMES; add "stray_kill" and "sandbox_reset" to NUDGE_KINDS; add a test that the tokens `strays`, `strays_total`, `locks_removed`, `locks_removed_total` appear in the `### stray_kill` section of the doc and `strays` in the `### sandbox_reset` section (split the doc text on "\n### " and search the matching chunk, not the whole document).
   d. tests/test_soak_tools.py: the per-run row has a "stray kills" value.
6. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` and make the whole suite pass. Then call finish with a summary.
```

- [ ] **Step 2: Run**; **Step 3: Review** (order of `NUDGE_KINDS`; `EMPTY_REPLY_NUDGE_KINDS` order = `runner.NUDGES`; compare cell shape; `_md_code` on a backtick; the wire text byte-identical to D1's quoted sentence; the fixture diff is only the description); **Step 4: Host suite** green; **Step 5:** resume if needed, commit, ff-merge, cleanup, ledger row.

---

### Task W8a: Live docker tests 13–16, 20a/20b + the image `ENV` (spec §7, §9)

**Files:**
- Modify: `tests/test_docker_live.py` (new tests + `_image_kwargs()` on the two existing tests at ~:206 and ~:224), `docker/Dockerfile:54` (`ENV`), `docker/README.md` (checklist item 6 only)

- [ ] **Step 1: Brief** `$SCRATCH/brief-61-w8a.md`:

```
Issue #61, task W8a of 12: add live docker tests for the stray ladder and the gitfile layout to tests/test_docker_live.py, and add four ENV lines to docker/Dockerfile. You cannot run docker-marked tests inside this sandbox — write them carefully following the existing patterns (`_make_live_repo`, `_resp`, `_call`, `_run_docker_main`, `_assert_status`, `_image_kwargs`, reading events from payload["transcript"]; see test_docker_live_backgrounded_process_is_dead_after_reap and test_docker_live_process_flood_triggers_reset) and make sure the whole non-docker suite still passes and `python3 -m pytest --co -q -m docker tests/test_docker_live.py` collects them. Do NOT edit anything under docs/. The only non-test files you may touch are docker/Dockerfile and the one checklist line in docker/README.md named in item 7.

Each test: a fresh repo, a scripted response list, `_run_docker_main(monkeypatch, tmp_path, repo, responses, **_image_kwargs())` — EVERY new test passes `**_image_kwargs()` so DIRTYWORK_LIVE_IMAGE selects the image, and add `**_image_kwargs()` to the two existing tests that lack it (the backgrounded-process test near line 206 and the flood test near line 224). Helpers: `_events(payload)` returning the parsed list of transcript events; `_of(events, name)` filtering by event name. All tests are @pytest.mark.docker.

1. test_docker_live_stray_is_killed_in_place_and_stash_survives: calls: bash "echo x >> README.md && git stash && (nohup sleep 300 >/dev/null 2>&1 &) && echo started" then bash "git stash pop && git diff --stat" then finish. Expect: exactly one stray_kill event and no sandbox_reset; the second bash result contains "README.md" (the stash popped); the stray_kill event's strays contain an entry containing "sleep 300"; the first bash call's tool_result has a follow_up containing "The sandbox killed".
2. test_docker_live_cat_named_stray_dies_with_the_others: bash "mkfifo /tmp/f; (setsid cat 0<>/tmp/f >/dev/null 2>&1 &); (sleep 300 >/dev/null 2>&1 &); echo ok" then bash "ls /proc | grep -c '^[0-9]'" then finish: exactly one stray_kill; the second result's number is <= 5 (tini, tether, the bash wrapper, the inner bash, grep).
3. test_docker_live_killed_git_locks_are_swept: bash "touch /gitdir/index.lock /gitdir/gc.pid; (sleep 300 >/dev/null 2>&1 &); echo ok" then bash "git status --short; echo rc=$?" then finish: the stray_kill event's locks_removed contains "/gitdir/index.lock" and "/gitdir/gc.pid"; the second result contains "rc=0"; the first result's follow_up mentions "Stale git lock files".
4. test_docker_live_git_init_in_tmp_stays_local (S13): bash "d=$(mktemp -d) && cd $d && git init -q && git status --short && git worktree list && git rev-parse --git-dir" then bash "cd /tmp && git -C /work status --short; echo rc=$?" then finish: the first result contains "/tmp/" in the worktree-list line and a line that is exactly ".git"; the second contains "rc=0"; no sandbox_reset, no stray_kill.
5. test_docker_live_nested_repos_export_as_plain_files: bash "printf '__pycache__/\n' > .gitignore && mkdir -p sub && cd sub && git init -q && echo new > NEW.txt && echo mod >> ../README.md && mkdir -p deep/inner && cd deep/inner && git init -q && echo d > D.txt && mkdir -p /work/sub/__pycache__ && echo x > /work/sub/__pycache__/a.pyc" then finish: status completed; sorted(run.json's dropped_git_entries) == ["sub/.git", "sub/deep/inner/.git"] (read run.json from the run dir the payload names); the exported worktree contains sub/NEW.txt and sub/deep/inner/D.txt and a README.md ending in "mod", and NOT sub/__pycache__/a.pyc; `git -C <worktree> ls-files -s` has no line starting with "160000".
6. test_docker_live_root_gitfile_tampering_20a: bash "rm .git; git status >/dev/null 2>&1; echo rc=$?" then bash ":(){ :|:& };:" (forces a full reset through the pids flood, as the existing flood test does) then bash "cat .git" then finish: the first result contains "rc=128"; a sandbox_reset event exists; the last result contains "gitdir: /gitdir".
   test_docker_live_root_gitfile_tampering_20b: bash "rm .git && git init -q && echo t > T.txt" then finish: completed, run.json dropped_git_entries == [".git"], the exported worktree contains T.txt.
7. docker/Dockerfile: extend the existing `ENV DOTNET_ROOT=...` instruction (line ~54) with four more continuation lines: DOTNET_CLI_USE_MSBUILD_SERVER=0, MSBUILDDISABLENODEREUSE=1, UseSharedCompilation=false, DOTNET_NOLOGO=1, preceded by a one-line comment: no build daemon may outlive a bash call (#61). In docker/README.md, under "### 1.0 image checklist", extend item 6 so it says the 1.0 image bakes DOTNET_EnableWriteXorExecute=0 AND the four .NET stray-process variables, so a derived FROM :1.0 needs none of the five. Nothing else outside tests/.

8. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` (the docker tests stay deselected) and `python3 -m pytest --co -q -m docker tests/test_docker_live.py` — both must succeed. Then call finish with a summary.
```

- [ ] **Step 2: Run**; **Step 3: Review** the tests against §9 (13–16, 20a/20b), `_image_kwargs()` everywhere, the Dockerfile diff; **Step 4: Host suite** green.
- [ ] **Step 5: Live run (Claude, host):** `DIRTYWORK_LIVE_IMAGE=dirtywork-worker-pytest:0.10 /usr/bin/python3 -m pytest -q -m docker tests/test_docker_live.py` from the run worktree; failures → resume with the exact output pasted (at most two resumes, then Claude fixes and says so).
- [ ] **Step 6:** commit, ff-merge, cleanup, ledger row.

---

### Task W8b: Live docker tests 17–19, 21 (spec §9)

**Files:**
- Modify: `tests/test_docker_live.py`

- [ ] **Step 1: Brief** `$SCRATCH/brief-61-w8b.md`:

```
Issue #61, task W8b of 12: add four more live docker tests to tests/test_docker_live.py (same conventions as the tests added in the previous task: `_run_docker_main(..., **_image_kwargs())`, `_events`, `_of`, @pytest.mark.docker; you cannot run them here — make `python3 -m pytest --co -q -m docker tests/test_docker_live.py` collect them and keep the non-docker suite green). Do NOT edit anything under docs/ or outside tests/.

1. test_docker_live_race_loop_no_resets: skip unless os.environ.get("DIRTYWORK_LIVE_SLOW") (it takes ~4 min). The 5.2 s idle must be BETWEEN turns, not inside the command — that is what makes the watchdog's 5 s worktree sample come due at the start of a short call and still be in flight when it returns. Subclass the scripted provider: `class _SlowClient(ScriptedClient):` overriding `reply(self, model, messages, tools)` to `time.sleep(5.2)` then `return super().reply(model, messages, tools)`; build the run the way `_run_main` does but with `_SlowClient(responses)` (add an optional `client_cls=ScriptedClient` parameter to `_run_main`/`_run_docker_main` and pass it through). Responses: 40 × bash "sed -n 1,3p README.md" then finish. Expect zero sandbox_reset and zero stray_kill events, status completed.
2. test_docker_live_process_flood_triggers_reset (existing): extend its assertions — the sandbox_reset event has a non-empty "strays" list of strings.
3. test_docker_live_dotnet_build_leaves_no_stray: image = LIVE_IMAGE or DEFAULT_IMAGE; skip with a reason unless `_dotnet_list_sdks(image)` (existing helper) lists an SDK. Read the image's env with `docker run --rm --entrypoint /usr/bin/env <image>` (subprocess, 60 s timeout); `daemons_off = "UseSharedCompilation=false" in env_out`. Calls: bash "dotnet new console --framework net8.0 -o app && dotnet build app" (timeout 300) then bash "echo ok" then finish. Expect no sandbox_reset either way; when daemons_off: no stray_kill event; otherwise exactly one stray_kill whose strays contain an entry containing "VBCSCompiler".
4. test_docker_live_timed_out_grep_leaves_no_stray: bash "yes y | head -c 200000000 > big.txt; echo made" (timeout 120) then a grep tool call {"pattern": "^zzz", "path": ".", "timeout": 1} then bash "echo ok" then finish: the grep tool_result contains "timed out"; no stray_kill and no sandbox_reset events; status completed.

5. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` and `python3 -m pytest --co -q -m docker tests/test_docker_live.py` — both must succeed. Then call finish with a summary.
```

- [ ] **Step 2: Run**; **Step 3: Review** (the idle is between turns; the flood assertion; the `.NET` probe uses the live image; the grep test's timeout path); **Step 4: Host suite** green.
- [ ] **Step 5: Live run (Claude, host):** `DIRTYWORK_LIVE_IMAGE=dirtywork-worker-pytest:0.10 DIRTYWORK_LIVE_SLOW=1 /usr/bin/python3 -m pytest -q -m docker tests/test_docker_live.py`; then build the dev image (`docker build -t dirtywork-worker-dev:issue61 docker/`) and run the `.NET` test against it (`DIRTYWORK_LIVE_IMAGE=dirtywork-worker-dev:issue61 … -k dotnet`) and against `dirtywork-worker-dev:issue63` (no daemon variables → exactly one `stray_kill`). Fix wave via resume with the exact failures pasted.
- [ ] **Step 6:** commit, ff-merge, cleanup, ledger row.

---

### Task C9: Acceptance, S13 evidence, soak re-runs, PR (Claude)

- [ ] **Step 1: Full suites on `issue-61-sandbox-resets`:** unit (`/usr/bin/python3 -m pytest -q -p no:cacheprovider`) and live (`-m docker`, both images, `DIRTYWORK_LIVE_SLOW=1`) green; record counts.
- [ ] **Step 2: S13 acceptance with the branch's own runtime:** from `.worktrees/issue-61-sandbox-resets`, `/usr/bin/python3 -m dirtywork run "run the test suite with python3 -m pytest -q -p no:cacheprovider and call finish" --repo /Users/jimschneider/repos/dirtywork --branch-from issue-61-sandbox-resets --model qwen/qwen3-coder-next --sandbox docker --image dirtywork-worker-pytest:0.10 --verify "python3 -m pytest -q -p no:cacheprovider" --max-turns 10` — the **plain** gate passes inside the sandbox (no `env -u`); the transcript shows no `sandbox_reset` and no `stray_kill`. Ledger row.
- [ ] **Step 3: Soak re-runs:** `D3-issue97` (the S11 run: `git stash` around `dotnet test`, on `dirtywork-worker-dev:issue61`) and one `run-bash-buildsh` (class A/D) with the branch runtime; assert the stash survives and the deliverable is complete; rows in the ledger, with the P7/P5 numbers alongside.
- [ ] **Step 4: Ledger metrics:** stop the sampler (`tools/soak_sampler.sh $SCRATCH/metrics-61.csv --stop`); per-window stats computed at the end (not from a snapshot); run totals (runs, turns, wall, prompt tokens, $0); which items Claude finished after a failed resume, if any.
- [ ] **Step 5: PR** from `issue-61-sandbox-resets`: "Closes #61", milestone 1.0.0, body = spec summary + evidence + the ledger link + the dogfood receipts; CI green (incl. the docker-live leg on amd64); wait for the owner's merge word.

## Self-review

- **Spec coverage:** §3.1–§3.5 → W3/W4b; §3.2, §3.7, §5.1 → W4a; §3.6 → W5a/W5b; §3.8 → W5b; §4.1–§4.3 → W1; §4.4 → W2a/W2b; §5.2 → W6; §6.1 → W4a/W4b (events) + D1 (doc); §6.2 → W7; §7 → W8a (Dockerfile) + D1 (README prose); §8 → D1 (+ W7 wire text, W1 guardrails comment, W8a checklist line); §9 tests 1–7 → W4a/W4b/W5a/W5b (W3 for test 5's script checks and W2a for the `dash -n` check), 8–9 → W1, 10 → W2a/W2b, 11 → W6, 12 → W7, 13–16 + 20a/20b → W8a, 17–19 + 21 → W8b; race regression → W5b step 5 and W8b test 1; §10 → C0/C9.
- **Placeholders:** none — every brief carries the code, argv and assertions the worker needs; the Claude-only live checks reuse `$SCRATCH/p7/probe.py` and the P8 fixture.
- **Type consistency:** `drain_notices() -> list[tuple[str, str]]` (W4a) is what W6 reads; `reset(reason, *, strays=None, strays_total=None)` (W4a) is what W4b calls and W5b's flag rules wrap; `strays.cap_strays/cap_locks` return `(list, int | None)` and W4b writes the `_total` keys only when not `None`; `tree` is keyed by the full relative root and `children()` returns names relative to the root, so W2b looks up `tree[f"{R}/{c}"]`; `check_worktree_budget_once(*, wait=True)` (W5a) is called with the default by `_after_bash` and `wait=False` by `run()`; `FakeDocker` callables (W3) are what W4a/W4b/W5a/W5b tests use; `_image_kwargs()` (existing) is what every W8 test passes.
