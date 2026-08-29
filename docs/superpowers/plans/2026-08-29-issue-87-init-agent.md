# #87 `dirtywork init --agent` — one skill file, every orchestrator's directory (0.13.0): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **This repository's execution rule overrides the line above** (repo `CLAUDE.md`, "Dogfood rule").
> Every code task below (`W…`) is built by the **released dirtywork running against this repository**
> with a local worker (`qwen/qwen3-coder-next` via LM Studio) — Claude writes the brief, reviews the
> branch, runs the host suite, feeds back through `dirtywork resume --feedback-file`, and writes the
> prose (`C…`/`D…` tasks). A Claude implementer touches code only after a worker resume-with-feedback
> has failed, and the PR says so. Owner approval is needed for the merge and the release, never assumed.

**Plan v1** (2026-08-29 12:25 CDT) against spec v1.1 (`b5a63a0`,
`docs/superpowers/specs/2026-08-29-issue-87-init-agent-design.md`). Six tasks: C0, W1, W2, W3, W4,
D1, C1 — each W sized well under the #82/#96 calibration (~150–250 changed lines per 60-turn run).

**Goal:** `dirtywork init --agent claude|codex|gemini|cursor|copilot` writes the one skill file to
the directory that agent reads (`.claude/skills` for Claude Code, `.agents/skills` for the other
four); the skill body stops assuming LM Studio; the release is 0.13.0 with the `:0.13` image cycle.

**Architecture:** A five-entry table `AGENT_DIRS` in `dirtywork/contract/__init__.py` replaces the
two `".claude"` literals in `destinations()`; nothing else in `init`'s mechanics (render, stamp,
`decide`, atomic write, `--stdout`) changes, and the rendered bytes are identical for every agent.
The skill template gets three hunks (provider-neutral first-run check, `--provider`/`--base-url`
in the run template, the resume/base-url bullet) and `_ENDPOINT_HINTS["openai"]` names the flags.
The packaged contract documents `--agent`; CI's wheel-smoke proves the non-Claude path from the
wheel; version and image tag roll to 0.13.

**Tech Stack:** Python 3.9+ stdlib (`argparse`, `pathlib`); pytest as in `tests/test_contract.py`
(`home` fixture monkeypatches `HOME`; `m.main([...])` + `capsys`); GitHub Actions `ci.yml`.

**Spec:** `docs/superpowers/specs/2026-08-29-issue-87-init-agent-design.md` v1.1 (`b5a63a0`) —
§0 the owner's decisions, §1.2 the verified landscape (sources and date), §3.1 the table, §3.2 the
three skill hunks and the hint (Appendix A is the whole template), §3.3 test 17, §3.4 the ripple
(every site named), §4 limits, §5 tests 14/17/24–30, §6 acceptance A1–A10, §7 files.

## Global Constraints

- Python floor 3.9; stdlib only in `dirtywork/`.
- The rendered skill is byte-identical for every `--agent` (spec §3.1); `render_skill` is not touched.
- Frontmatter unchanged: `name: dirtywork`, the `description:` line as is (spec §3.2 — the
  standard requires both; `name` must equal the directory `dirtywork`).
- Rendered skill ≤ 200 newlines (`test_skill_frontmatter_and_size`); Appendix A is 135 lines.
- Every `--flag` the skill names must exist in the parser (`test_skill_flags_exist_in_parser`).
- No format's destination is a file `load_repo_context` reads (`CLAUDE.md`, `AGENTS.md` at the
  repo root of the base commit) — test 17 over all five agents.
- `--agent` is single-valued; default `claude`; argparse `choices=AGENTS`.
- Stdout lines and exit codes of `init` unchanged (`wrote:` / `up to date:` / `updated:` /
  `skipped (locally modified):` / `overwrote:`; 0/1/2).
- Image cycle: `DEFAULT_IMAGE` `:0.13`, `PINNED_DIGEST = None` (pinned in 0.13.1); no `:0.12` in
  any spelling outside `docs/superpowers/` and the digest-history comment (A9).
- `docs/superpowers/` is history: never swept.
- Never merge or release without the owner's explicit per-action go.

## Execution model (every W task)

- **Scratchpad** (absolute; this session's — a new session gets its own and re-writes `run87.sh`):
  `SCRATCH=/private/tmp/claude-501/-Users-jimschneider-repos-dirtywork/6d1529ba-f5a6-4b9b-8e8f-c5c06b027d19/scratchpad`
  — holds `run87.sh`, the briefs `brief-87-<task>.md` (extracted verbatim from this plan's fenced
  blocks), `feedback-87-<task>-r<n>.md`, `metrics-87.csv` (+ `.pid`).
- **Runtime:** `DW_REL=0.12.1` (latest on PyPI, checked 2026-08-29 11:45 CDT — re-check in C0),
  `DW_IMG=dirtywork-worker-pytest:0.12` (present locally; `docker/README.md` "Derived images").
- **Run command** (`$SCRATCH/run87.sh $SCRATCH/brief-87-<task>.md`):

  ```bash
  #!/bin/bash
  # run87.sh BRIEF_FILE [extra dirtywork args...] — one #87 dogfood run with the plan's flags.
  set -u
  REL="${DW_REL:?set DW_REL=<latest pypi version>}"; IMG="${DW_IMG:?set DW_IMG=dirtywork-worker-pytest:<X.Y>}"
  BRIEF="${1:?brief file}"; shift
  cd /Users/jimschneider/repos/dirtywork || exit 2
  pipx run --spec "dirtywork==$REL" dirtywork run "$(cat "$BRIEF")" \
    --repo /Users/jimschneider/repos/dirtywork --branch-from issue-87-init-agent \
    --model qwen/qwen3-coder-next --sandbox docker --image "$IMG" \
    --verify "python3 -m pytest -q -p no:cacheprovider" \
    --verify-rounds 2 --max-turns 60 --timeout 1800 "$@" >"$BRIEF.out" 2>"$BRIEF.err"
  rc=$?
  echo "rc=$rc"; python3 -c "import json; d=json.load(open('$BRIEF.out')); print({k:d.get(k) for k in ('status','turns','final_message','run_dir','transcript','worktree','branch')})"
  exit $rc
  ```

- **Chaining:** each run branches from `issue-87-init-agent`; after review Claude commits the
  export on the run's branch (`worker export verbatim: run <slug>`), adds its own fix commits,
  fast-forwards `issue-87-init-agent` to it (`git rebase issue-87-init-agent` in the run worktree
  first if integration moved, then `git merge --ff-only dirtywork/<slug>` from a worktree of the
  integration branch — **never from the main checkout while the owner's other terminal has it
  checked out**), removes the run worktree and deletes the run branch. Order: W1 → W2 → W3 → W4
  (W3's CI step needs W1's flag; W4's version bump last so every earlier run's stamp/`--version`
  tests see one version).
- **Pre-run check (lesson from #96):** before launching a brief, apply its code on the host in a
  throwaway worktree and run the named tests — a brief with a bug in it costs a full run.
- **Review loop:** read `~/.dirtywork/runs/<slug>/run.json` + transcript; diff the run worktree
  against the brief and the spec section; grep the tests for the brief's literal cases (#61
  lesson: the model inverts a rule and writes the test to match); **check the old lines are gone,
  not only that the new ones are present** (#96 W3 miss); run the host suite in the run worktree
  (`PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider` — the host has no pytest);
  gaps → `dirtywork resume <slug> --feedback-file <file> --max-turns 40`, feedback that names a
  file, a line and a shell check per item, at most two resumes; then Claude finishes leftovers and
  says so in the ledger and the PR.
- **Metrics:** `tools/soak_sampler.sh $SCRATCH/metrics-87.csv` (OUT first; started in C0, detached
  with `nohup … >/dev/null 2>&1 &`; stopped in C1 with `--stop`); one ledger row per run in the
  `## #87` section of `docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md`, columns as the
  `## #96` table: `| Task | Slug | Status | Turns | Wall | s/turn | Prompt tok | Compl tok | tok/s | Nudges | Guardrail | Tool mix | Verify | Resumes | Notes |`.
- Never `cd` into a run worktree in the foreground shell before removing it.

## File structure

| File | Task | Responsibility after this plan |
|---|---|---|
| `dirtywork/contract/__init__.py` | W1 | `AGENT_DIRS`, `AGENTS`; `destinations()` reads `args.agent`; docstrings say "orchestrator skill" |
| `dirtywork/__main__.py` | W1, W2 | `--agent` on `init`; help/description text; `_ENDPOINT_HINTS["openai"]` |
| `dirtywork/contract/SKILL.md` | W2 | the template, three hunks (spec Appendix A) |
| `tests/test_contract.py` | W1, W2 | test 17 parametrized; tests 24–30 |
| `dirtywork/contract/machine-contract.md` | W3, W4 | `--agent` in synopsis + **init** entry; `:0.13` literals |
| `.github/workflows/ci.yml` | W3, W4 | wheel-smoke `--agent` step; docker-live tag `:0.13` |
| `pyproject.toml`, `dirtywork/__init__.py` | W4 | 0.13.0 |
| `dirtywork/sandbox/docker_args.py`, `tests/test_docker_args.py` | W4 | `:0.13`, `PINNED_DIGEST = None`, history comment |
| `docker/README.md`, `docs/operating.md:494` | W4 | `:0.13` sweep, release lists |
| `README.md`, `docs/orchestrator-setup.md` + `.html`, `docs/operating.md:375-378` | D1 | the prose |
| `docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md` | C0, C1 | receipts |

---

### Task C0: Baseline and instrumentation (Claude)

**Files:** none in the repo except the ledger section.

- [ ] **Step 1: Branch state.** On `issue-87-init-agent` (spec `b5a63a0`, this plan): `git fetch origin && git rebase origin/main` — expected clean (main is `d755222`).
- [ ] **Step 2: Confirm the runtime.** `REL=$(curl -s https://pypi.org/pypi/dirtywork/json | python3 -c 'import sys,json; print(json.load(sys.stdin)["info"]["version"])')` — expect `0.12.1`; `pipx run --spec "dirtywork==$REL" dirtywork --version`; `docker image inspect dirtywork-worker-pytest:0.12 >/dev/null && echo ok`; `curl -s http://localhost:1234/v1/models | grep -q qwen3-coder-next && echo loaded`. Export `DW_REL=$REL DW_IMG=dirtywork-worker-pytest:0.12`.
- [ ] **Step 3: Baseline.** `git worktree add .worktrees/issue-87-init-agent issue-87-init-agent`; in it `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider 2>&1 | tail -1` — record the count (expected ≈ 1,650 passed on `d755222`).
- [ ] **Step 4: Sampler + scratch.** `mkdir -p $SCRATCH`; write `run87.sh` (above), `chmod +x`; `nohup tools/soak_sampler.sh $SCRATCH/metrics-87.csv >/dev/null 2>&1 &`.
- [ ] **Step 5: Ledger.** Append `## #87 — init --agent + provider-neutral skill (plan v1, spec v1.1 \`b5a63a0\`)` to the ledger with the runtime line, the host baseline, and the empty per-run table (header above). Commit on `issue-87-init-agent`: `docs(ledger): #87 section, baseline`.

---

### Task W1: `AGENT_DIRS` and `--agent` (spec §3.1, §3.3; tests 17, 24–29)

**Files:**
- Modify: `dirtywork/contract/__init__.py` (`SKILL_DIRNAME` line 17; `destinations()` lines 69–84; two docstrings).
- Modify: `dirtywork/__main__.py` (imports near line 25; `_build_parser` description lines 1167–1170; `init_p` lines 1195–1203).
- Test: `tests/test_contract.py` (test 17 at line 272; new tests after line 270).

**Interfaces:**
- Produces: `contract.AGENT_DIRS: dict[str, str]` (`"claude" → ".claude"`, the other four → `".agents"`), `contract.AGENTS: tuple[str, ...] == tuple(AGENT_DIRS)`, `args.agent: str` (default `"claude"`). `destinations(args)` unchanged in signature; paths are `<base>/<AGENT_DIRS[args.agent]>/skills/dirtywork/SKILL.md`. W3's contract text and D1's docs quote these paths.

- [ ] **Step 1: Pre-run check on the host.** In a throwaway worktree, apply steps 1–4 of the brief by hand, run `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider tests/test_contract.py` — all pass (30 + parametrized). Discard the worktree. (This catches a wrong brief before a 60-turn run.)
- [ ] **Step 2: Brief** `$SCRATCH/brief-87-w1.md`:

```
Issue #87 (dirtywork init --agent), task W1 of 4. Add an --agent option to `dirtywork init` that picks the skills directory; the rendered file is unchanged. Touch only dirtywork/contract/__init__.py, dirtywork/__main__.py (imports, the top-level description, the init subparser) and tests/test_contract.py. Do not edit dirtywork/contract/SKILL.md or dirtywork/contract/machine-contract.md.

1. dirtywork/contract/__init__.py — directly after the line `SKILL_DIRNAME = "dirtywork"` add exactly:

# Where each orchestrator discovers skills, verified 2026-08-29 (spec §1.2). The
# file is the same everywhere -- the Agent Skills standard -- only the directory
# differs. Four of the five read `.agents/skills`; the entry per tool is so a
# user names what they run and a tool that moves later is a one-line change.
AGENT_DIRS = {
    "claude": ".claude",
    "codex": ".agents",
    "gemini": ".agents",
    "cursor": ".agents",
    "copilot": ".agents",
}
AGENTS = tuple(AGENT_DIRS)

2. In destinations(args), replace both `".claude"` literals with `AGENT_DIRS[args.agent]`, so the two paths read `Path.home() / AGENT_DIRS[args.agent] / "skills" / SKILL_DIRNAME / "SKILL.md"` and `Path(args.repo) / AGENT_DIRS[args.agent] / "skills" / SKILL_DIRNAME / "SKILL.md"`. Replace its whole docstring with:
    """User copy unless --no-user, then the project copy if --repo, both under
    the --agent's skills directory; the same path twice (a --repo that resolves
    to $HOME) is one destination."""
In the module docstring replace `the Claude Code skill `dirtywork init` renders` with `the orchestrator skill `dirtywork init` renders (Claude Code by default; --agent picks another tool's directory)`. Nothing else in the module changes.

3. dirtywork/__main__.py — add `from .contract import AGENTS` with the other `from .` imports at the top (keep the lazy `from . import contract as contract_mod` inside main()). In _build_parser(): the parser description becomes "Driving it from an agent? `dirtywork init` installs the orchestrator skill (Claude Code by default; --agent for Codex, Gemini CLI, Cursor, Copilot); `dirtywork contract` prints the reference."; the init subparser's help becomes "install the skill that teaches an orchestrating agent to drive dirtywork (Claude Code by default; --agent for Codex, Gemini CLI, Cursor, Copilot)". Add, before the --repo argument:
    init_p.add_argument("--agent", choices=AGENTS, default="claude",
                        help="whose skills directory to write: claude -> .claude/skills (default); codex, gemini, cursor, copilot -> .agents/skills")
--repo's help becomes "also write the skill into this project (<PATH>/<agent dir>/skills/dirtywork/SKILL.md)"; --no-user's help becomes "do not write the home copy (~/<agent dir>/skills/dirtywork/SKILL.md)".

4. tests/test_contract.py — parametrize test_worker_prompt_does_not_inject_skill_file with `@pytest.mark.parametrize("agent", contract.AGENTS)` (add `agent` to its signature) and build the path as `repo / contract.AGENT_DIRS[agent] / "skills" / "dirtywork" / "SKILL.md"`. Then add these tests directly after test_init_same_version_template_change_updates (the `home` fixture and `_user_skill` helper already exist; `argparse`, `pytest`, `m`, `contract`, `__version__` are already imported):

def _skill_at(base: Path, agent: str) -> Path:
    return base / contract.AGENT_DIRS[agent] / "skills" / "dirtywork" / "SKILL.md"


@pytest.mark.parametrize("agent", contract.AGENTS)
def test_init_agent_writes_that_agents_paths(home, tmp_path, agent, capsys):
    repo = tmp_path / "proj"
    repo.mkdir()
    rc = m.main(["init", "--agent", agent, "--repo", str(repo)])
    out = capsys.readouterr().out
    user, project = _skill_at(home, agent), _skill_at(repo, agent)
    assert rc == 0
    assert out == f"wrote: {user}\nwrote: {project}\n"
    expected = contract.render_skill(__version__).encode("utf-8")
    assert user.read_bytes() == expected and project.read_bytes() == expected


def test_init_default_agent_is_claude(home, capsys):
    assert m.main(["init"]) == 0
    assert m.main(["init", "--agent", "claude"]) == 0
    assert capsys.readouterr().out == f"wrote: {_user_skill(home)}\nup to date: {_user_skill(home)}\n"


@pytest.mark.parametrize("agent", contract.AGENTS)
def test_init_stdout_is_identical_across_agents(home, agent, capsys):
    assert m.main(["init", "--agent", agent, "--stdout"]) == 0
    assert capsys.readouterr().out == contract.render_skill(__version__)
    assert not (home / contract.AGENT_DIRS[agent]).exists()


def test_init_unknown_agent_is_a_usage_error(home, capsys):
    with pytest.raises(SystemExit) as exc:
        m.main(["init", "--agent", "emacs"])
    assert exc.value.code == 2
    assert not (home / ".claude").exists() and not (home / ".agents").exists()


def test_init_agents_share_or_split_destinations(home, capsys):
    assert m.main(["init", "--agent", "claude"]) == 0
    assert m.main(["init", "--agent", "codex"]) == 0
    assert m.main(["init", "--agent", "gemini"]) == 0
    shared = _skill_at(home, "codex")
    assert capsys.readouterr().out == (
        f"wrote: {_user_skill(home)}\nwrote: {shared}\nup to date: {shared}\n")
    with open(_user_skill(home), "a", encoding="utf-8") as fh:
        fh.write("\nlocal edit\n")
    assert m.main(["init", "--agent", "cursor"]) == 0
    assert capsys.readouterr().out == f"up to date: {shared}\n"
    assert _user_skill(home).read_text(encoding="utf-8").endswith("local edit\n")


def test_agent_dirs_table():
    assert contract.AGENT_DIRS["claude"] == ".claude"
    assert {contract.AGENT_DIRS[a] for a in contract.AGENTS if a != "claude"} == {".agents"}
    assert contract.AGENTS == tuple(contract.AGENT_DIRS)
    sub = next(a for a in m._build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    action = next(a for a in sub.choices["init"]._actions if "--agent" in a.option_strings)
    assert tuple(action.choices) == contract.AGENTS and action.default == "claude"

Run: python3 -m pytest -q -p no:cacheprovider tests/test_contract.py — every test must pass, including the existing ones (test_init_with_repo_writes_both still expects .claude paths: the default is claude). Then the full suite.
```

- [ ] **Step 3: Run** `$SCRATCH/run87.sh $SCRATCH/brief-87-w1.md`. Expected: `completed`, verify pass.
- [ ] **Step 4: Review** (Execution model). Specifically: `grep -n '"\.claude"' dirtywork/contract/__init__.py` must print **nothing** (both literals gone); `grep -c "AGENT_DIRS\[args.agent\]" dirtywork/contract/__init__.py` → 2; `grep -n "from .contract import AGENTS" dirtywork/__main__.py` → 1 hit at the top; `dirtywork init --help` (from the worktree: `PYTHONPATH=. python3 -m dirtywork init --help`) shows `--agent {claude,codex,gemini,cursor,copilot}`; test 17 shows 5 parametrized passes; host suite green.
- [ ] **Step 5: Resume if needed** (`--feedback-file`, ≤ 2). Then export commit, fast-forward `issue-87-init-agent`, remove the worktree, delete the run branch, ledger row.

---

### Task W2: The provider-neutral skill body and the endpoint hint (spec §3.2, Appendix A; tests 14, 30)

**Files:**
- Modify: `dirtywork/contract/SKILL.md` (lines 23–24; the run block at lines 52–55; bullets after it).
- Modify: `dirtywork/__main__.py:220` (`_ENDPOINT_HINTS["openai"]`).
- Test: `tests/test_contract.py` (`test_skill_frontmatter_and_size` line 29; new test 30).

**Interfaces:**
- Consumes: nothing from W1 (the template and the table are independent).
- Produces: the 0.13 template — exactly spec Appendix A (135 lines); `m._ENDPOINT_HINTS["openai"]` containing `lms ps`, `--base-url`, `--provider ollama`. D1's docs describe this text.

- [ ] **Step 1: Pre-run check on the host.** Apply the three hunks and the hint in a throwaway worktree; `diff dirtywork/contract/SKILL.md <appendix A extracted from the spec>` must be empty; `tests/test_contract.py` green. Discard.
- [ ] **Step 2: Brief** `$SCRATCH/brief-87-w2.md`:

```
Issue #87 (dirtywork init --agent), task W2 of 4. Make the packaged skill provider-neutral. Touch only dirtywork/contract/SKILL.md, the _ENDPOINT_HINTS dict in dirtywork/__main__.py, and tests/test_contract.py. Do not change the first four lines of SKILL.md (the frontmatter) and do not edit dirtywork/contract/__init__.py. The stamp is computed at render time — there is no stamp in the template; do not add one.

1. dirtywork/contract/SKILL.md, section "## Before the first run": replace the two-line bullet that starts `- `curl -s http://localhost:1234/v1/models` — LM Studio is serving and the model` and ends `Other providers: see the contract.` with exactly these six lines:

- The endpoint you will pass is serving and the model you will pass as `--model`
  is loaded (LM Studio `curl -s http://localhost:1234/v1/models`; Ollama
  `ollama ps`) or available (another OpenAI-compatible server:
  `curl -s <base-url>/models`). If the request does not say which provider,
  find out; if more than one is running, ask — never pick one silently, and
  never assume LM Studio. `--provider anthropic` needs `ANTHROPIC_API_KEY`.

2. Section "### 2. Run": in the indented command block, insert this line between `      --model <model> \` and `      --verify "<test command>" --verify-rounds 2 \` (six spaces of indent, like its neighbours):

      [--provider openai|anthropic|ollama] [--base-url <url>] \

and insert this four-line bullet as the FIRST bullet after that block, i.e. directly before `- `--verify` runs your gate in the sandbox`:

- `--provider`/`--base-url`: omit both for LM Studio on `localhost:1234` (the
  default). Use `--provider ollama` for Ollama; for another OpenAI-compatible
  server, pass `--base-url <url>`. `resume` inherits `--provider`, but it does
  not restore a custom `--base-url` — repeat that custom URL on every resume.

Nothing else in SKILL.md changes: `wc -l dirtywork/contract/SKILL.md` must print 135 (it is 126 now).

3. dirtywork/__main__.py: the "openai" entry of _ENDPOINT_HINTS becomes exactly
    "openai": "Is the OpenAI-compatible server running? Try: lms ps (LM Studio). Another server: --base-url <url>; Ollama: --provider ollama",

4. tests/test_contract.py: in test_skill_frontmatter_and_size, extend the needle tuple with these four strings: "[--provider openai|anthropic|ollama] [--base-url <url>]", "ollama ps", "curl -s <base-url>/models", "not restore a custom `--base-url`"; and add after the for-loop: `assert "Other providers: see the contract" not in text`. Then add, directly after test_skill_first_paragraph_addresses_the_worker:

def test_endpoint_hint_names_the_flags():
    hint = m._ENDPOINT_HINTS["openai"]
    assert "lms ps" in hint and "--base-url" in hint and "--provider ollama" in hint

Run: python3 -m pytest -q -p no:cacheprovider tests/test_contract.py — all pass, including test_skill_flags_exist_in_parser (every --flag the skill names must exist: --provider and --base-url do) and test_skill_stamp_hash_matches_body. Then the full suite.
```

- [ ] **Step 3: Run** `$SCRATCH/run87.sh $SCRATCH/brief-87-w2.md`.
- [ ] **Step 4: Review.** `diff <(awk '/^````markdown$/{f=1;next} /^````$/{f=0} f' docs/superpowers/specs/2026-08-29-issue-87-init-agent-design.md) <run-worktree>/dirtywork/contract/SKILL.md` — **empty**; `grep -n "Other providers\|LM Studio is serving" <run-worktree>/dirtywork/contract/SKILL.md` — nothing; `grep -n 'lms ps' <run-worktree>/dirtywork/__main__.py` shows the new string once; host suite green; `PYTHONPATH=. python3 -m dirtywork init --stdout | wc -l` → 136 (135 + the stamp).
- [ ] **Step 5: Resume if needed; chain** (export commit, ff, cleanup, ledger row).

---

### Task W3: The contract documents `--agent`; CI proves it from the wheel (spec §3.4)

**Files:**
- Modify: `dirtywork/contract/machine-contract.md:41-42` (synopsis), `:233-246` (the **init** entry).
- Modify: `.github/workflows/ci.yml:128-131` (wheel-smoke steps).

**Interfaces:**
- Consumes (W1): `--agent` with choices `claude|codex|gemini|cursor|copilot`, default `claude`; paths `~/.agents/skills/dirtywork/SKILL.md`, `<PATH>/.agents/skills/dirtywork/SKILL.md`.
- Produces: the contract text D1 links to; a CI step C1 watches.

- [ ] **Step 1: Brief** `$SCRATCH/brief-87-w3.md`:

```
Issue #87 (dirtywork init --agent), task W3 of 4. Document --agent in the packaged machine contract and prove it in CI's wheel smoke. Touch only dirtywork/contract/machine-contract.md and .github/workflows/ci.yml. Keep the contract's existing line width (wrap at about 95 columns) and change no sentence that is not named here.

1. machine-contract.md, the synopsis block: replace the two lines
dirtywork init [--repo <path>] [--no-user] [--force] [--stdout]
                                            # install the Claude Code skill — see "init" below
with
dirtywork init [--agent claude|codex|gemini|cursor|copilot] [--repo <path>] [--no-user] [--force] [--stdout]
                                            # install the orchestrator skill — see "init" below

2. machine-contract.md, the paragraph that begins `**init:** writes the Claude Code skill`: replace everything from `**init:**` up to and including `(`--no-user` without `--repo` is a usage error).` with:
**init:** writes the skill that teaches an orchestrating agent to drive dirtywork (the text
`dirtywork init --stdout` prints). `--agent` picks the directory: `claude` (the default) →
`~/.claude/skills/dirtywork/SKILL.md` and, with `--repo PATH`, `<PATH>/.claude/skills/dirtywork/SKILL.md`;
`codex`, `gemini`, `cursor` and `copilot` → `~/.agents/skills/dirtywork/SKILL.md` and
`<PATH>/.agents/skills/dirtywork/SKILL.md`. The file is the same for every agent (the Agent Skills
standard); only the directory differs, and the four non-Claude agents share it — one `init` covers
all four. `--no-user` skips the home copy (`--no-user` without `--repo` is a usage error).
Keep the rest of the paragraph (from `The first line after the frontmatter is a` onward) verbatim, except its final sentence: change `a project copy committed to the target repo is visible to the worker like any other` to `a project copy committed to the target repo — under `.claude/` or `.agents/` — is visible to the worker like any other`.

3. .github/workflows/ci.yml, job wheel-smoke: directly after the step whose run line starts `smoke/bin/dirtywork init --stdout > skill.md`, add a new step with the same indentation as its neighbours:
      - run: |
          mkdir smoke-repo && smoke/bin/dirtywork init --agent codex --no-user --repo smoke-repo && test -f smoke-repo/.agents/skills/dirtywork/SKILL.md && smoke/bin/dirtywork init --agent gemini --stdout > skill2.md && cmp skill.md skill2.md
No pipes (a pipe's exit status is its last command's).

Checks before finishing: `grep -c -- '--agent' dirtywork/contract/machine-contract.md` prints 2; `grep -c 'Claude Code skill' dirtywork/contract/machine-contract.md` prints 0; keep the YAML indentation identical to the neighbouring steps. Verify: python3 -m pytest -q -p no:cacheprovider (test_contract_prints_packaged_reference_verbatim reads the file you edited).
```

- [ ] **Step 2: Run** `$SCRATCH/run87.sh $SCRATCH/brief-87-w3.md`.
- [ ] **Step 3: Review.** Read the whole **init** paragraph in the run worktree against spec §3.4; `git -C <run-worktree> diff --stat` names exactly two files; `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"` on the host (PyYAML is available via `pipx run --spec pyyaml python -c …` if not installed); `grep -n "Claude Code skill" dirtywork/contract/machine-contract.md` — nothing.
- [ ] **Step 4: Resume if needed; chain.**

---

### Task W4: 0.13.0 and the `:0.13` image cycle (spec §3.4, A9)

**Files:**
- Modify: `pyproject.toml:7`, `dirtywork/__init__.py:1`, `dirtywork/sandbox/docker_args.py:8-30`, `tests/test_docker_args.py:20-24`, `.github/workflows/ci.yml:98`, `dirtywork/contract/machine-contract.md` (lines 29, 74, 79, 86, 109, 120, 126), `docker/README.md` (lines 28, 40, 44-47, 105, 115, 118, 119, 125-126, 141-142, 154, 161-162, 197, 212-213), `docs/operating.md:494`.

**Interfaces:**
- Produces: `__version__ == "0.13.0"`; `DEFAULT_IMAGE == "ghcr.io/jimboschneider/dirtywork-worker:0.13"`; `PINNED_DIGEST is None`. C1's A9 grep and the release depend on it.

- [ ] **Step 1: Brief** `$SCRATCH/brief-87-w4.md`:

```
Issue #87, task W4 of 4 — release plumbing for 0.13.0: bump the version and roll the default worker image tag from :0.12 to :0.13 (the v0.13.0 release publishes :0.13; the first release of a minor ships unpinned and 0.13.1 pins the digest). Mechanical — change nothing that is not named here, and nothing under docs/superpowers/.

1. pyproject.toml: `version = "0.13.0"`. dirtywork/__init__.py: `__version__ = "0.13.0"`.

2. dirtywork/sandbox/docker_args.py: `DEFAULT_IMAGE = "ghcr.io/jimboschneider/dirtywork-worker:0.13"`. Replace the comment block between DEFAULT_IMAGE and PINNED_DIGEST (it starts `# Pinned for 0.12.1:` and ends `# sha256:3b8d019a2f20a9df55a72ed51139076f02f2feb597243a69519bc41db1029648.)`) with:
# 0.13.0 is the first release of the minor: unpinned until 0.13.1 pins the
# published :0.13 digest per docker/README.md ("Pin a digest"). This only ever
# pins a REGISTRY digest -- resolve_image() enforces it against a *pulled*
# DEFAULT_IMAGE only; a locally built/loaded image warns instead of refusing,
# and a user-supplied --image is never checked. (0.12.x pinned :0.12 at
# sha256:edcf3a4718392bfe169a078b08ce35cc1e320f2b85231a87439e4ea24d78fe3c;
followed by the existing history lines from `# 0.11.x pinned :0.11 at` through `# sha256:3b8d019a2f20a9df55a72ed51139076f02f2feb597243a69519bc41db1029648.)` verbatim. Then `PINNED_DIGEST: str | None = None`.

3. tests/test_docker_args.py, test_default_image_and_pinned_digest, becomes exactly:
def test_default_image_and_pinned_digest():
    assert DEFAULT_IMAGE == "ghcr.io/jimboschneider/dirtywork-worker:0.13"
    # 0.13.0 is the first release of the minor: unpinned until 0.13.1 pins the
    # published :0.13 digest per docker/README.
    assert PINNED_DIGEST is None

4. .github/workflows/ci.yml: the line `tags: ghcr.io/jimboschneider/dirtywork-worker:0.12` → `:0.13`.

5. dirtywork/contract/machine-contract.md: every `:0.12` → `:0.13` — seven occurrences on seven lines, including `my-worker:0.12` (twice on one line), `The `:0.12` image ships`, and `FROM :0.12`. No other edit.

6. docker/README.md: every `:0.12` → `:0.13` (20 occurrences, including `FROM :0.12` near the top and `my-worker:0.12` in the derived-image examples). Two list edits: in `The first release of a minor (0.4.0, 0.5.0, 0.6.0, 0.7.0, 0.8.0, 0.9.0, 0.10.0, 0.11.0, 0.12.0) ships with` insert `, 0.13.0` after `0.12.0`; in `0.11.1 for 0.11; 0.12.1 for 0.12 — 0.7.x shipped unpinned` insert `; 0.13.1 for 0.13` after `0.12.1 for 0.12`.

7. docs/operating.md: `Use the `:0.12` image (the default since 0.12.0)` → `Use the `:0.13` image (the default since 0.13.0)`.

Check before finishing: `grep -rn ':0\.12\b' --include='*.py' --include='*.md' --include='*.yml' --include='*.toml' . | grep -v docs/superpowers` must print only the `(0.12.x pinned :0.12 at` history line in dirtywork/sandbox/docker_args.py; `grep -rn '0\.12\.0\|0\.12\.1' pyproject.toml dirtywork/__init__.py` prints nothing. Verify: python3 -m pytest -q -p no:cacheprovider (test_docker_args, test_version_flag and the stamp tests read the new version).
```

- [ ] **Step 2: Run** `$SCRATCH/run87.sh $SCRATCH/brief-87-w4.md`.
- [ ] **Step 3: Review.** The A9 grep on the host (spec §6) — only the history line; `git -C <run-worktree> diff --stat` names exactly nine files; `PYTHONPATH=. python3 -m dirtywork --version` → `dirtywork 0.13.0`; `PYTHONPATH=. python3 -m dirtywork init --stdout | head -5` shows `v0.13.0` in the stamp; host suite green.
- [ ] **Step 4: Resume if needed; chain.**

---

### Task D1: The prose — README, orchestrator setup page (+ HTML twin), operating guide (Claude; spec §3.4)

**Files:**
- Modify: `README.md:146-163` (**Use it from Claude Code**).
- Modify: `docs/orchestrator-setup.md` (intro "0.12.0 or later"; new section after "Teach Claude the loop"; **Good to know** bullets) and `docs/orchestrator-setup.html` (same edits, hand-mirrored — `<h2>` sections at lines 119/151).
- Modify: `docs/operating.md:375-378`.

**Interfaces:**
- Consumes: W1's paths and flag; W2's skill text (quote it, don't paraphrase the flags); W3's contract entry.

- [ ] **Step 1: README.** Replace, in **Use it from Claude Code**, the sentences from "Any other agent can read the same instructions" through "says how)." with:

  > Any other agent that reads [Agent Skills](https://agentskills.io) gets the same file where it looks:
  > `dirtywork init --agent codex --repo .` writes it to `.agents/skills/dirtywork/` (home and project) — the
  > directory Codex, Gemini CLI, Cursor and Copilot all read, so `--agent gemini|cursor|copilot` write the
  > same two files. The full reference is `dirtywork contract`. The skill's run template carries
  > `--provider`/`--base-url`, so Ollama and other endpoints need no hand edits.

  In the next paragraph, `(a `--provider` line, say)` → `(a default `--model`, say)`.
- [ ] **Step 2: orchestrator-setup.md.** "You need dirtywork 0.12.0 or later." → "You need dirtywork 0.12.0 or later — 0.13.0 for anything but Claude Code." Insert after the "Teach Claude the loop" section:

  > ## Other orchestrators
  >
  > Codex CLI, Gemini CLI, Cursor and GitHub Copilot read the same kind of file Claude Code does — a
  > `SKILL.md` in the [Agent Skills](https://agentskills.io) layout — from one shared directory. So it
  > is one command, not four:
  >
  >     dirtywork init --agent codex --repo .
  >
  >     wrote: /Users/you/.agents/skills/dirtywork/SKILL.md
  >     wrote: /Users/you/repos/yourproject/.agents/skills/dirtywork/SKILL.md
  >
  > `--agent gemini`, `--agent cursor` and `--agent copilot` write those same two files (`init` will
  > tell you `up to date:`). The file is byte-for-byte what `--agent claude` writes; only the directory
  > differs. What each tool does with it:
  >
  > - **Codex CLI** lists it as `$dirtywork` and also picks it on its own when a request matches the
  >   description.
  > - **Gemini CLI** asks you to confirm the first time it activates the skill (you'll see the name,
  >   the description and the path); `/skills list` shows it.
  > - **Cursor** and **Copilot** show it in the `/` menu and can pick it on their own. For Copilot that
  >   means the CLI or VS Code's agent mode — the cloud agent runs on GitHub's machines and can't reach
  >   your LM Studio or Docker.
  >
  > The one rule is the same everywhere: the skill never goes into `AGENTS.md` or `CLAUDE.md`, because
  > dirtywork hands those to the *worker* on every run. `init` never writes there.

  In **Good to know**: replace the **Other providers** bullet with

  > - **Other providers.** The skill's run template carries `--provider`/`--base-url`, and its
  >   first-run check covers LM Studio, Ollama and any other OpenAI-compatible endpoint — it asks
  >   rather than guessing when more than one is running. If you added a `--provider` line by hand
  >   under 0.12, `init` will now report your copy as locally modified; `--force` takes the new
  >   template, which has the line.

  delete the **Other agents** bullet (the section above replaces it); in **The project skill is a
  file in the repo** change "so a worker can read it like any other file" to "— under `.claude/` or
  `.agents/` — so a worker can read it like any other file".
- [ ] **Step 3: orchestrator-setup.html.** Mirror step 2 exactly in the HTML twin (a new `<h2>Other orchestrators</h2>` section with the same paragraphs, `<pre>` for the commands, `<ul>` for the three bullets; the same **Good to know** edits). Check with `python3 -c "import html.parser"`-free eyes: open it in the browser once (`open docs/orchestrator-setup.html`).
- [ ] **Step 4: operating.md.** In **Setting up an orchestrator** (line 375), after the sentence ending "`--no-user` skips the home copy." add: "`--agent codex` (or `gemini`, `cursor`, `copilot`) writes the same file to `.agents/skills/` instead, the directory those four tools share."
- [ ] **Step 5: Check.** `grep -n "init --stdout" README.md docs/orchestrator-setup.md` — no "other agents use --stdout" sentence remains (A8); `grep -n "assumes LM Studio" README.md docs/orchestrator-setup.md` — nothing; `grep -c "Other orchestrators" docs/orchestrator-setup.md docs/orchestrator-setup.html` → 1 each. Commit on `issue-87-init-agent`: `docs: init --agent — other orchestrators, provider-neutral skill (#87)`.

---

### Task C1: Host verification, acceptance, ledger, PR (Claude; spec §6)

**Files:** the ledger section; the PR.

- [ ] **Step 1: Full suite** in the integration worktree: `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider 2>&1 | tail -1` — green; record the count against C0's baseline (expect +≈16: 5×test 17 −1, 5×24, 25, 5×26, 27, 28, 29, 30).
- [ ] **Step 2: A1–A6.** `PYTHONPATH=. python3 -m dirtywork init --agent codex --stdout | cmp - <(PYTHONPATH=. python3 -m dirtywork init --stdout)` (A4); the `diff` of Appendix A against `SKILL.md` (A2, body); `pipx run --spec dirtywork==0.12.1 dirtywork init --stdout | sed -n 1,4p | cmp - <(PYTHONPATH=. python3 -m dirtywork init --stdout | sed -n 1,4p)` (A2, frontmatter identical); tests 16/17/24/29 named in the suite output (A1, A5, A6).
- [ ] **Step 3: A9.** `grep -rn ':0\.12\b' --include='*.py' --include='*.md' --include='*.yml' --include='*.toml' --include='Dockerfile*' . | grep -v docs/superpowers` → only the history line; `grep -n "0.13.0\|0.13.1 for 0.13" docker/README.md` → both lists.
- [ ] **Step 4: A8.** D1 step 5's greps.
- [ ] **Step 5: Sampler off + ledger.** `tools/soak_sampler.sh $SCRATCH/metrics-87.csv --stop`; fill the `## #87` table (one row per run and resume), the host counts, the RAM/tok-per-s summary from `metrics-87.csv`, and a "Claude touched code?" line (expected: no). Commit `docs(ledger): #87 runs`.
- [ ] **Step 6: PR.** `git push -u origin issue-87-init-agent`; `gh pr create --title "#87: dirtywork init --agent — one skill file, every orchestrator's directory; provider-neutral skill; 0.13.0 — built by the released dirtywork 0.12.1"` with a body that has: the one-paragraph finding (spec §1.2), the flag, the three skill hunks, the `updated:` consequence for existing installs, the image cycle, the ledger link, the test delta, and "Claude implemented: none" (or the exact leftovers). Watch `test`, `docker-live`, `wheel-smoke` (with the new step), `gate`; the advisory Windows leg is informational.
- [ ] **Step 7: Hand to the owner.** Merge is the owner's call. After merge, still the owner's per-action go for each of: cut `v0.13.0` (notes lift from PR #99 + this PR); verify PyPI + `:0.13` image; build `dirtywork-worker-pytest:0.13`; self-dogfood A3 (`dirtywork init` → `updated: … (v0.12.1 -> v0.13.0)`; `init --agent codex` → `wrote:`), recorded as done or not; refresh this repo's `.claude/skills/dirtywork/SKILL.md` with the released 0.13.0 (as #89 did); Pages `built` + sitemap 200 after the docs merge; 0.13.1 pins the digest.

---

## Self-review

**Spec coverage.** §0 (`--agent`, fold-in, 0.13.0) — W1, W2, W4. §1.2 paths — W1's table, W3's contract text, D1's prose. §3.1 (table, `destinations`, choices/default, `--stdout` identical, shared pair) — W1 + tests 24–29. §3.2 (a)(b)(c), the `updated:` consequence — W2, C1 A2/A3, D1's **Other providers** bullet. §3.3 — W1 (test 17 ×5). §3.4: `contract/__init__.py` docstrings — W1; `__main__.py` import/help/description/hint — W1, W2; contract synopsis + entry — W3; wheel-smoke step — W3; version + image cycle (`docker_args.py`, `test_docker_args.py`, `ci.yml:98`, seven contract lines, `docker/README.md` + lists, `operating.md:494`) — W4; README, setup page + HTML twin, `operating.md:375` — D1; Pages smoke — C1 step 7. §3.5 — C1 step 7. §4 limits — carried by the prose in D1 (consent, Copilot surfaces, edited copies, `.agents/` visible to the worker). §5 tests 14, 17, 24–30 and `test_docker_args` — W1, W2, W4. §6 A1–A10 — C1 steps 1–6; A10 is the ledger. §7 files — every file appears in exactly the tasks above; no file outside §7 is touched.

**Placeholder scan.** No TBD/TODO. Every worker step is a brief with the literal code/text; every Claude step is a shell line or the literal prose. `DW_REL`/`DW_IMG` are set in C0 from PyPI at execution time (0.12.1 today).

**Type consistency.** `AGENT_DIRS` / `AGENTS` (W1) are what test 29, W3's prose and the parser's `choices=` use; `args.agent` is the argparse dest `destinations()` reads; `_skill_at(base, agent)` (W1 tests) is defined in the same brief that uses it; `m._ENDPOINT_HINTS["openai"]` (W2) is the existing dict key; `DEFAULT_IMAGE`/`PINNED_DIGEST` names unchanged (W4); the CI step (W3) uses `--agent codex`/`--agent gemini`, both in `AGENTS`.
