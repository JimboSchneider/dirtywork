# Ship the operator contract in the wheel: `dirtywork contract` + `dirtywork init` (#82) — design

v2.1, 2026-08-26. Status: **owner review folded (six items + five residuals); awaiting owner sign-off.** Next: red-team fold → plan → built the dogfood way (released dirtywork + local worker for the code; Claude writes the skill prose and reviews).

## 0. The owner's decision

Jim, 2026-08-26: "I want to make it so any users of dirtywork are able to seamlessly use it like I do with Claude on install."

Decisions taken in the conversation that this spec encodes:

- The operator contract ships **inside the package** and is exposed from the CLI; the docs checkout is no longer the only copy.
- The orchestrator's instructions are installed as a **Claude Code skill** (`SKILL.md`), never appended to `CLAUDE.md`/`AGENTS.md` — those two files are injected into the *worker's* prompt, and orchestrator instructions must not leak into the worker.
- `dirtywork init` writes to the **user-level** skill directory **and**, when `--repo` is given, to the **project's** — "both by default" (Jim's pick over user-only / project-only).
- A Claude Code plugin marketplace entry and an MCP server are explicitly **later**, not part of this.

### 0.1 Owner review fold (v2, 2026-08-26 16:47 — six items, each verified against the code before folding)

1. **Wheel-smoke job was not self-contained or gated** (`ci.yml:31` installs only `pytest`; `gate.needs` at `ci.yml:108` is `[test, docker-live]`). Fixed in §3.5: `python -m pip install build`, `wheel-smoke` added to `gate.needs`, and assertions written to files + `grep -q` — no `| head -1` (SIGPIPE under `pipefail`, and a truncated stream can mask a failure).
2. **Stamp hash ignored the frontmatter.** v1 hashed only the bytes after the stamp, so an edited `description:`/`name:` survived the check and a version upgrade would clobber it. Fixed in §3.3: the hash covers the **entire file with the stamp line removed**; test 19 edits the frontmatter and expects a skip.
3. **"Invisible to the worker" was false as written.** The worker is told to explore with `list_dir`/`read_file` (`__main__.py:90-100`) and docker populates the base tree from `HEAD` (`git read-tree -m -u HEAD`, `lifecycle.py:76`), so a *committed* project skill is readable like any file. Guarantee narrowed to "never injected into the worker prompt" (§3.4, §4, test 17 renamed); the skill's first paragraph tells a worker that reads it to ignore it — **advisory, not a security boundary**; a real exclusion mechanism (hiding `.claude/` from the worker's tree) is a separate design, filed as #84, not built here.
4. **Reject/cleanup flow did not work.** `runs clean` refuses a dirty worktree without `--force` (`runs.py:1088-1090`) and plain `clean` deletes `run.json` and the transcript — contradicting "keep receipts". Fixed in Appendix A: reject = `runs verdict <slug> reject --note`, then `runs clean <slug> --force --keep-transcript` (`_clean_run_dir` keeps exactly `transcript.jsonl` and `run.json`); plain `clean` is described as receipt-destroying.
5. **Docker dependency guidance missed `--allow-network`** (`__main__.py:464`, bridge networking). Appendix A now says *default* docker mode and states the trade-off.
6. **Documentation ripple missed live references.** Sweep found **eleven** sites, not eight: `docs/transcript-schema.md:350` plus docstrings in `dirtywork/tools.py:192` and `dirtywork/workspace.py:208`. The stub and every `docs/*.md` reference now use the absolute canonical GitHub URL rather than a relative path out of `docs/` (§3.1).

Owner decisions on §8 recorded: **move** (contingent on item 6, now addressed), **exit 1 on skipped copies**, **create `~/.claude/skills/` by default**, names **`init`** and **`contract`** stay.

Verified against current Claude Code docs (2026-08-26): skills are discovered at `~/.claude/skills/<name>/SKILL.md` and `<project>/.claude/skills/<name>/SKILL.md`; `description` is the only required frontmatter field; `AGENTS.md` is *not* read natively by Claude Code; there is **no** documented mechanism for an installed CLI to register instructions without writing files. A subcommand that writes the skill file is therefore the sanctioned path, not a workaround. (Sources: code.claude.com/docs/en/skills.md, memory.md, plugins.md.)

## 1. Problem and evidence

The README's first paragraph says dirtywork is "built to be driven by an orchestrating agent (Claude Code, in our case)". The two documents that make that possible — `docs/machine-contract.md` (525 lines: every flag, the stdout JSON, exit codes, transcript events) and `docs/operating.md` (530 lines: the human playbook) — exist only in the git checkout:

- `pyproject.toml` packages `dirtywork`, `dirtywork.providers`, `dirtywork.sandbox`; there is no `package-data` and no `MANIFEST.in`. Nothing under `docs/` reaches the wheel.
- The README's "Documentation" links (`README.md:35`, `:152`) are GitHub blob URLs; `docs/.nojekyll` means the Pages site serves no rendered copy either.
- A `pipx install dirtywork` user gets the CLI, `--help`, and nothing that tells their agent *how to orchestrate* — what a brief is, that stdout is one JSON object, what exit 1 means, that `resume --feedback` exists.

The maintainer's own Claude drives dirtywork well only because of per-user memory on one machine plus the repo `CLAUDE.md` (#81) — and that file is repo-development policy ("dirtywork builds dirtywork"), not the operator contract.

## 2. Scope

**In**

1. `dirtywork/contract/` package with two shipped files: the machine contract (moved from `docs/`) and a skill template.
2. `dirtywork contract` — print the machine contract to stdout.
3. `dirtywork init` — render and install the skill; idempotent, version-stamped, edit-preserving. Plus `dirtywork --version`, which does not exist today (`__version__` is only emitted inside the run JSON) and is the skill's first prerequisite check.
4. The skill text itself (Appendix A) — ≤ 200 lines, agent-facing, project-neutral.
5. Contract ripple: the contract's own **Flags** section documents both subcommands; docs stub + inbound links; README / operating.md sections; `--help`.
6. Tests, a drift guard (every `--flag` the skill names must exist in the parser), and a CI wheel-install smoke.
7. Version 0.12.0 (new CLI surface → minor).

**Out**

- Plugin marketplace entry; MCP server; `AGENTS.md`/other-agent output formats (extension point noted in §4).
- Any change to what is injected into the worker prompt.
- A `run`-time warning that an installed skill is stale.
- Rewriting `docs/operating.md`; the skill *condenses* it, the guide stays the human reference.

## 3. Design

### 3.1 The packaged contract (`dirtywork/contract/`)

```
dirtywork/contract/
  __init__.py            # read(name) -> str ; render_skill(version) -> str
  machine-contract.md    # git mv docs/machine-contract.md — verbatim, no templating
  SKILL.md               # template: exactly one placeholder, {{VERSION}}
```

- `read(name)` uses `importlib.resources.files(__package__).joinpath(name).read_text(encoding="utf-8")` (available on 3.9, the floor).
- `render_skill(version)` substitutes `{{VERSION}}`, then computes the stamp (§3.3) and inserts it as the first line after the frontmatter. The returned text is byte-for-byte what `init` writes.
- `pyproject.toml`: `packages` gains `"dirtywork.contract"`; add `[tool.setuptools.package-data] "dirtywork.contract" = ["*.md"]`.
- Single source of truth: the contract file **moves**. `docs/machine-contract.md` becomes a three-line stub that keeps the `# Machine contract` heading (so existing `#machine-contract` anchors still resolve) and links to the canonical copy by **absolute URL** — `https://github.com/JimboSchneider/dirtywork/blob/main/dirtywork/contract/machine-contract.md` — and to `dirtywork contract`. Absolute, not relative: `docs/` is the Pages root, and a `../dirtywork/...` link only works in GitHub's renderer.
- Inbound references — **eleven** sites, every one updated to the absolute canonical URL (or, in Python docstrings, the new path): `README.md:35`, `README.md:152`, `docker/README.md:235`, `docker/README.md:253`, `docs/operating.md:28`, `:34`, `:478`, `docs/transcript-schema.md:277`, `docs/transcript-schema.md:350`, `dirtywork/tools.py:192`, `dirtywork/workspace.py:208`. Acceptance A8 is a repo-wide grep proving no stale `docs/machine-contract.md` reference remains outside the stub and `docs/superpowers/`.

### 3.2 `dirtywork contract`

Prints `machine-contract.md` to stdout verbatim. No flags. Nothing on stderr. Exit 0. The doc already says of `run` that "stdout is the contract"; `contract` is the same promise for documentation — any orchestrator, Claude or not, can `dirtywork contract` and read it.

### 3.3 `dirtywork init`

```
dirtywork init [--repo PATH] [--no-user] [--force] [--stdout]
```

**Destinations** (rendered content is identical for every destination):

| flag state | `~/.claude/skills/dirtywork/SKILL.md` | `<repo>/.claude/skills/dirtywork/SKILL.md` |
|---|---|---|
| (none) | write | — |
| `--repo PATH` | write | write |
| `--repo PATH --no-user` | — | write |
| `--no-user` alone | — | — → usage error, exit 2 ("nothing to write") |
| `--stdout` | print rendered skill to stdout, write nowhere, exit 0 (other flags validated but not acted on) |

`~` is `Path.home()`. Parent directories are created (`mkdir -p`); a user without Claude Code gets a `~/.claude/skills/` directory — accepted for "seamless", `--no-user` opts out. `--repo` must be an existing directory (it does **not** have to be a git repo — `init` never touches git); otherwise exit 2. Files are written atomically (temp file in the same directory + `os.replace`; reuse the repo's existing atomic-write helper if one is exported — grep for `os.replace` — rather than adding a second).

**The stamp.** First line of the body, immediately after the frontmatter's closing `---`:

```
<!-- dirtywork-skill v{VERSION} sha256:{HEX16} — generated by `dirtywork init`; your edits are preserved (re-run with --force to overwrite) -->
```

`HEX16` is the first 16 hex characters of the SHA-256 of the **entire file with the stamp line removed** (UTF-8 bytes; the frontmatter is included, so an edited `name:` or `description:` counts as a local edit). It lets `init` distinguish "our unmodified output" from "the user edited this" without storing history.

**Decision per destination:**

| existing file | action | stdout line | rc contribution |
|---|---|---|---|
| absent | write | `wrote: <path>` | 0 |
| stamp parses, hash matches, version equal | none | `up to date: <path>` | 0 |
| stamp parses, hash matches, version differs | overwrite | `updated: <path> (v{old} -> v{new})` | 0 |
| stamp parses, hash differs | skip | `skipped (locally modified): <path>` | 1 |
| no parseable stamp | skip (treated as locally modified) | same | 1 |
| either skip case with `--force` | overwrite | `overwrote: <path>` | 0 |

Exit code is `0` if every destination was written/updated/current, `1` if any was skipped, `2` on usage or environment error (`--repo` not a directory, unwritable path). Messages: one stdout line per destination in the order user → project; errors are `error: ...` on stderr, matching the CLI's existing convention. (`init` is human/agent-facing plain text, like `runs list`; the "one JSON object on stdout" rule is `run`'s and stays `run`'s.)

### 3.4 The skill

Full text in Appendix A. Design rules it must satisfy (tests enforce the mechanical ones):

- Frontmatter: `name: dirtywork`; `description:` one or two sentences naming the triggers (run dirtywork, delegate to a local/worker model, review a dirtywork run). No `disable-model-invocation` — the point is that Claude reaches for it unprompted.
- The first paragraph after the title addresses a **worker** that might read the file (a committed project copy is visible in the worktree like any other file): it says the file is for the orchestrator and to ignore it. This is **advisory, not a security boundary** — a model can read past it; the real exclusion is #84. It is the only mitigation in scope; see §4.
- ≤ 200 lines including frontmatter (Claude Code's general guidance for instruction files).
- Says, early and plainly: run `dirtywork contract` for the reference; don't guess flags.
- Covers the loop the maintainer actually runs: brief → run → parse stdout JSON → review the diff before the transcript → `resume --feedback` or re-brief → `runs verdict` → cleanup. Plus the three things that bite first-time orchestrators: stdout vs stderr, exit 0/1/2 semantics, and "the worker cannot install dependencies in docker mode".
- Project-neutral: no maintainer paths, no image names, no model beyond `--model <model>`; the target repo's own `CLAUDE.md`/`AGENTS.md` carries worker conventions (and the skill says so).
- Every `--flag` it mentions exists in the argparse tree (drift guard, §5).

### 3.5 Contract ripple (each site named)

- `dirtywork/contract/machine-contract.md` **Flags** section: add `dirtywork contract` and `dirtywork init` with the table in §3.3, the stamp format, and `init`'s exit codes. The contract documents its own delivery.
- `docs/machine-contract.md` → stub (§3.1). Inbound references updated (eleven sites, §3.1).
- `README.md`: a short **Use it from Claude Code** subsection — `pipx install dirtywork`, `dirtywork init --repo .`, "Claude now has the `dirtywork` skill; ask it to delegate a task" — and the Documentation list gains `dirtywork contract` beside the contract link.
- `docs/operating.md`: **Setting up an orchestrator** (≤ 15 lines) pointing at `init`, `--no-user`, `--force`, and `contract`.
- `dirtywork --help`: top-level parser description gets one sentence: "Driving it from an agent? `dirtywork init` installs the Claude Code skill; `dirtywork contract` prints the reference."
- `dirtywork --version`: `p.add_argument("--version", action="version", version=f"dirtywork {__version__}")` on the top-level parser (`__main__.py` already imports `__version__` at line 25).
- Version 0.12.0 in `pyproject.toml` and `dirtywork/__init__.py` (the string is hardcoded in both).
- `.github/workflows/ci.yml`: a `wheel-smoke` job (ubuntu, one Python), added to `gate.needs` alongside `test` and `docker-live` so a failure blocks the gate. Steps: `python -m pip install build` (CI installs only `pytest` today) → `python -m build` → `python -m venv smoke && smoke/bin/pip install dist/*.whl` → in the venv, `dirtywork contract > contract.md && grep -q '^# Machine contract' contract.md`, `dirtywork init --stdout > skill.md && grep -q '^name: dirtywork' skill.md && grep -q 'dirtywork-skill v' skill.md`, and `dirtywork --version | grep -q '^dirtywork '`. Outputs go to files and are asserted with `grep -q` — no `| head -1` (SIGPIPE under `pipefail`; a truncated stream can hide a crash after line 1). This is the only check that catches a missing `package-data` entry; unit tests run from the source tree and would pass anyway.

### 3.6 Self-dogfood (after release, not part of the build)

`dirtywork init --repo ~/repos/dirtywork` in this repo, commit `.claude/skills/dirtywork/SKILL.md`; the invocation block in Claude's memory shrinks to a pointer. Issue #82's first user is this repo's own orchestrator. Note the committed copy *is* readable by dirtywork's own worker (§4); the skill's worker-ignore paragraph is advisory; #84 is the exclusion mechanism.

## 4. Failure modes and limits

- **Two copies, one source.** User and project copies are rendered from the same template by the same version, so they are identical at install time. They can drift across upgrades (user copy 0.12, project copy 0.13). Claude Code's precedence between a same-named user and project skill is **not documented** (UNVERIFIED); since both copies say "run `dirtywork contract`", the reference an agent reads is always the installed CLI's, and the drift is confined to playbook prose. Re-running `init --repo` refreshes both.
- **Edited copies stop updating.** By design — the stamp turns an edit into a skip, and the message says so. `--force` is the override; the stamp line itself tells the user this.
- **Claude-shaped output.** `init` writes Claude Code's format. Other agents use `dirtywork contract` or `init --stdout` and place the text themselves. If a second format is ever wanted, it is a `--format` flag on `init`, not a new command.
- **Skill/CLI drift.** The skill quotes flag names; the drift-guard test fails the suite if a named flag disappears from the parser.
- **Worker exposure — narrowed.** `init` never writes `CLAUDE.md`/`AGENTS.md`, and the worker-prompt injection (`workspace.py:243-270`) reads only those two files at the base commit, so the skill is **never injected into the worker prompt** (test 17). It is *not* invisible: a project copy committed to the target repo lands in the worker's tree (`git read-tree -m -u HEAD`, `lifecycle.py:76`) and the worker is told to explore with `list_dir`/`read_file`. Mitigation in scope: the skill's first paragraph tells a worker to ignore it — advisory, not a security boundary. Out of scope, filed as #84: exclude `.claude/` (orchestrator material) from the worker's populated tree — a sandbox-contract change that deserves its own spec. The user-level copy (`~/.claude/skills/`) is never in any repo and has no exposure.
- **`.claude/` gitignored in the target repo.** `init --repo` still writes; whether to commit is the user's call. `init` prints the path and never runs git.
- **Windows.** Already unsupported; `Path.home()` and the atomic write work there regardless.

## 5. Tests

`tests/test_contract.py` (new), driven through `m.main([...])` with `capsys`/`monkeypatch` like `tests/test_main.py`; `init` resolves `~` with `Path.home()` at call time (not at import, unlike `rundir.DIRTYWORK_HOME`), so tests redirect it with `monkeypatch.setenv("HOME", str(tmp_path))`, which `Path.home()` honours on POSIX.

1. `test_contract_prints_packaged_reference_verbatim` — stdout == packaged file bytes, stderr empty, rc 0.
2. `test_contract_first_line_is_heading` — `# Machine contract`.
3. `test_init_writes_user_skill_by_default` — file exists, stdout `wrote: <path>`, rc 0.
4. `test_init_with_repo_writes_both` — two files, identical bytes, two stdout lines user-then-project.
5. `test_init_no_user_writes_project_only`.
6. `test_init_no_user_without_repo_exits_2` — `error:` on stderr, nothing written.
7. `test_init_is_idempotent` — second run: `up to date:`, bytes unchanged, rc 0.
8. `test_init_updates_older_stamped_copy` — plant a file rendered with version `0.0.1`; `updated: ... (v0.0.1 -> v<current>)`, rc 0.
9. `test_init_skips_locally_modified_without_force` — append a line; `skipped (locally modified):`, file untouched, rc 1.
10. `test_init_force_overwrites_modified` — `overwrote:`, rc 0.
11. `test_init_unstamped_existing_file_is_treated_as_modified` — rc 1 without `--force`.
12. `test_init_stdout_prints_and_writes_nothing` — stdout == `render_skill(__version__)`, no files, rc 0.
13. `test_init_repo_not_a_directory_exits_2`.
14. `test_skill_frontmatter_and_size` — starts with `---`, has `name: dirtywork` and a non-empty `description:`, ≤ 200 lines, mentions `dirtywork contract`, `resume`, `runs verdict`, `--keep-transcript`, `--allow-network`, and all three exit codes.
15. `test_skill_stamp_hash_matches_body` — recompute the SHA-256 of the entire UTF-8 file with the stamp line removed; its first 16 hex characters equal the stamp's `HEX16`; the stamp's version equals `__version__`.
16. `test_skill_flags_exist_in_parser` — every `--[a-z][a-z-]*` token in the rendered skill is an option string somewhere in the argparse tree (walk subparsers via `_subparsers._group_actions[*].choices`).
17. `test_worker_prompt_does_not_inject_skill_file` — a repo whose base commit contains `.claude/skills/dirtywork/SKILL.md` and no `CLAUDE.md`: the injected conventions are empty (model it on the `CLAUDE.md`/`AGENTS.md` tests in `tests/test_workspace.py`; the reader is `workspace.py:243-270`). This pins "not injected", nothing more.
18. `test_version_flag` — `dirtywork --version` prints `dirtywork <__version__>` and exits 0 (argparse's `action="version"` raises `SystemExit(0)`; catch it with `pytest.raises`).
19. `test_init_detects_edited_frontmatter` — plant a copy rendered with version `0.0.1`, change only its `description:` line, run `init`: `skipped (locally modified)`, rc 1, file unchanged; `--force` overwrites.
20. `test_skill_first_paragraph_addresses_the_worker` — the first paragraph of the body after the title (the lines up to the first blank line, joined) contains both "worker" and "ignore". The prose wraps, so the test reads the paragraph, not one physical line.

CI: the `wheel-smoke` job in §3.5.

## 6. Acceptance

| # | Check | How |
|---|---|---|
| A1 | The contract is in the wheel | On a machine with no checkout: `pipx run --spec dirtywork==0.12.0 dirtywork contract > contract.md && grep -q '^# Machine contract' contract.md` exits 0 (same file-plus-`grep -q` pattern as CI) |
| A2 | `init` installs a discoverable skill | Fresh `HOME`; `dirtywork init`; open Claude Code in any directory; the `dirtywork` skill appears in its skill list (manual) |
| A3 | The skill fires on intent | In a repo where `dirtywork init --repo .` was run: ask Claude Code to "delegate this to dirtywork" — it invokes the skill and runs `dirtywork contract` before its first `run` (manual, one session) |
| A4 | Edits survive upgrades | Edit the installed skill; `dirtywork init` → `skipped (locally modified)`, rc 1, file unchanged |
| A5 | Suite and CI green | Full suite passes; `wheel-smoke` job green on the PR |
| A6 | Size | Rendered skill ≤ 200 lines (test 14) |
| A7 | Nothing is injected into the worker prompt | Test 17 green (the narrowed claim; see §4) |
| A8 | No stale contract path | `grep -rln 'docs/machine-contract.md' --include='*.md' --include='*.py' --exclude-dir=superpowers --exclude-dir=.worktrees .` prints exactly one path: `./docs/machine-contract.md` (the stub) |

## 7. Files

New: `dirtywork/contract/__init__.py`, `dirtywork/contract/SKILL.md`, `dirtywork/contract/machine-contract.md` (moved), `tests/test_contract.py`.

Modified: `dirtywork/__main__.py` (two subparsers, two dispatch branches, `--version`), `pyproject.toml` (packages, package-data, version), `dirtywork/__init__.py` (version), `docs/machine-contract.md` (stub), `README.md`, `docs/operating.md`, `docker/README.md`, `docs/transcript-schema.md`, `dirtywork/tools.py` and `dirtywork/workspace.py` (docstring paths), `.github/workflows/ci.yml` (`wheel-smoke` job + `gate.needs`).

## 8. Open questions — resolved by the owner (2026-08-26)

1. **Move vs. copy.** Move, contingent on the link sweep (§3.1, item 6 of §0.1 — done).
2. **Exit 1 on skip.** Yes.
3. **Creating `~/.claude/skills/` for non-Claude users.** Yes; `--no-user` opts out.
4. **Naming.** `init` and `contract` stay.

Follow-up, filed as #84 (not part of this spec): exclude `.claude/` from the tree the worker is given, so orchestrator material never reaches the worker at all (§4).

---

## Appendix A — `dirtywork/contract/SKILL.md` (template; `{{VERSION}}` is the only placeholder; the stamp line is inserted by `render_skill`)

```markdown
---
name: dirtywork
description: Drive dirtywork — delegate a coding task to a local model that works in an isolated git worktree, then review the result, resume it with feedback, and record a verdict. Use when asked to run dirtywork, hand implementation to a local/worker model, or review a dirtywork run.
---

# Driving dirtywork (v{{VERSION}})

*If you are the dirtywork **worker** reading this inside the sandbox: this file
is for the orchestrator that launched you. Ignore it and follow your task.*

You are the orchestrator. dirtywork runs a **worker** — a local model — in an
agentic tool-use loop inside an isolated git worktree and hands you the result.
You pick the task, write the brief, review what comes back, and decide. The
worker never merges anything; you do.

**Reference:** `dirtywork contract` prints every flag, the stdout JSON schema,
exit codes and transcript events for the installed version. Run it before your
first `run` in a session. Do not guess flags.

## Before the first run

- `dirtywork --version` — installed and on PATH (`pipx install dirtywork` if not).
- `curl -s http://localhost:1234/v1/models` — LM Studio is serving and the model
  you will pass as `--model` is loaded. Other providers: see the contract.
- `docker info` — docker mode (the default, and the contained one) needs a
  running Docker; the worker image is pulled on the first run if it is
  absent (exit 2 with "Build or pull the worker image" means that failed —
  see the contract's `--image` entry). `--sandbox none` runs the worker on
  your host: read the contract's security section first.

## The loop

### 1. Write the brief

One run = one task. The worker is a small model with a small context window: it
executes, it does not design. Put in the brief:

- the exact files to touch, by path — and any it must not touch;
- the exact names, strings and signatures, quoted;
- the test(s) that must pass and the command that runs them (the worker runs
  them itself);
- decisions already made ("use X, not Y") so it does not re-decide them.

Keep it under ~40 lines. If you cannot name the files, you are not ready to
delegate — explore first, then brief. The target repo's `CLAUDE.md` /
`AGENTS.md` (at the base commit) is injected into the worker's prompt
automatically: put worker-facing conventions there, not in every brief.

### 2. Run

    dirtywork run --repo <path> "<brief>" \
      --model <model> \
      --verify "<test command>" --verify-rounds 2 \
      --max-turns 60 --timeout 1800

- `--verify` runs your gate in the sandbox after the worker finishes and feeds
  failures back for up to `--verify-rounds` further attempts.
- Progress is on **stderr**: transcript path, worktree path, `error:` lines.
  `tail -f` the transcript to watch a run.
- **stdout is exactly one JSON object.** Parse it; do not grep it. Fields you
  will use: `status`, `worktree`, `branch`, `base_commit`, `transcript`,
  `run_dir`, `turns`, `files_changed`, `final_message`, `stuck_on`,
  `last_tool_result`.
- Exit codes: `0` = `completed`. `1` = any other status (`max_turns`,
  `timeout`, `stalled`, `stuck`, `verify_failed`, `context_exhausted`,
  `model_error`, `budget_exceeded`, `unchanged`, …) — the worktree and branch
  are kept for review and salvage. `2` = preflight or environment error;
  nothing was created.
- In the **default** docker mode the container has no network and nothing
  mounted, so the worker **cannot install dependencies**; it has what the image
  ships — git, bash, python3, node/npm, .NET, ripgrep, jq, curl, shellcheck.
  `--allow-network` gives the container bridge networking so installs work, at
  the cost of an offline sandbox — use it deliberately, and for anything
  permanent build a derived image instead (see the contract). Run any richer
  gate yourself, on the host, against the exported worktree.

### 3. Review — always, before anything merges

- `git -C <worktree> status` then `git -C <worktree> diff`: read the diff
  first, the transcript second. Compare `files_changed` with the brief —
  a touched file you did not name is a finding.
- Run the repo's own gate on the host against the worktree.
- Judge the work, not the status: `completed` with a wrong change is a reject;
  `max_turns` with a correct change is salvageable.

### 4. Resume with feedback, or re-brief

If the work is close: `dirtywork resume <slug> --feedback "<exactly what to
change>"` — same worktree, same branch; the worker is told to inspect its
earlier work and apply your feedback. Two resumes without convergence means
the brief was wrong: reject, rewrite the brief, run again from a clean base.

### 5. Verdict and cleanup

- `dirtywork runs verdict <slug> accept|reject --note "<why>"` records your
  decision in the run's `run.json`.
- Accept: commit or PR the `dirtywork/<slug>` branch as you would any
  contributor's work.
- Reject: record it, then discard the work but not the record —
  `dirtywork runs clean <slug> --force --keep-transcript`. `--force` because a
  rejected worktree has uncommitted changes and `clean` refuses to delete
  those otherwise; `--keep-transcript` keeps `run.json` and the transcript and
  removes the container, volume and worktree. Plain `runs clean <slug>`
  deletes the receipts too.
- `dirtywork runs list` and `dirtywork runs show <slug>` inspect earlier runs.

## Rules of thumb

- Never merge unreviewed worker output. The worker ran `bash` in a sandbox;
  a shell is a shell — see the contract's security section.
- Parallelism is processes: run independent briefs concurrently (LM Studio
  serves several requests per loaded model). Never two tasks in one brief.
- On `stuck`/`stalled`, read `stuck_on` and `last_tool_result` before
  resuming — the fix is usually in the brief, not the model.
- Keep receipts: `run.json`, the transcript and the verdict are the record of
  what the worker did and what you decided. `runs clean --keep-transcript`
  preserves them; plain `clean` does not.
```
