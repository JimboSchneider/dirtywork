# `dirtywork init --agent`: one skill file, every orchestrator's discovery path (#87, 0.13.0) — design

Status: v1.2, 2026-08-29 (v1.1 owner review folded, §0.1; v1.2 A9's exclusion — see the end of §0.1). Follows #82 (0.12.0, `docs/superpowers/specs/2026-08-26-operator-contract-init-design.md`),
whose §2 scoped other-agent output out and whose §4 promised "a `--format` flag on `init`, not a new command".

## 0. The owner's decision (2026-08-29)

- **`--agent claude|codex|gemini|cursor|copilot`, not `--format`.** The #82 promise assumed each tool
  would need its own wrapper. Verification against every tool's current documentation (§1.2) found
  the opposite: there is one format — the Agent Skills standard, which the 0.12 template already
  satisfies — and several directories. A flag called `format` would name a thing that no longer
  exists; `--agent` names the thing that varies (who is reading). Per-tool choices, not a bare
  `agents` choice: you type the tool you run, `init` prints where it wrote, and the table is the
  only place that knows where each tool looks.
- **The provider-neutral skill body ships here** (the owner's fold-in comment on #87, 2026-08-27):
  run template gains `--provider`/`--base-url`, the first-run check stops assuming LM Studio, and
  `_ENDPOINT_HINTS["openai"]` names the flags a wrong-provider user needs. One hash bump instead of two.
- **Release: 0.13.0**, together with #96 tier 1 (already on main, `d755222`). No 0.12.2.

### 0.1 Owner review fold (v1.1, 2026-08-29 12:10 CDT — six items, each verified before folding)

1. Image-ripple scope: v1 counted four `:0.12` lines in the packaged contract because its grep
   pattern was `dirtywork-worker:0.12`, which misses `my-worker:0.12` (line 86, twice) and bare
   `FROM :0.12` / "the `:0.12` image" (126, 120). Re-counted with `:0\.12\b`: seven contract
   lines, twenty in `docker/README.md`, plus the two release lists there. §3.4 and A9 now use the
   broad pattern so no spelling escapes.
2. The resume bullet is the owner's wording: `resume` inherits `--provider` but does not restore a
   *custom* `--base-url`; the default needs no repeating.
3. The first-run check no longer says "check which one answers": determine the intended provider,
   ask when more than one is running, never pick silently; "available" for a generic server,
   "loaded" for LM Studio/Ollama.
4. §3.1/§3.4/docs say outright that the four non-Claude agents share one destination pair; the
   README example is `--repo .` so it produces a committed project skill, not a home-only one.
5. Evidence: Gemini's getting-started tutorial requires both `name:` and `description:` (verified
   at geminicli.com/docs/cli/tutorials/skills-getting-started); Codex "scans `.agents/skills` in
   every directory from your current working directory up to the repository root" (verified,
   learn.chatgpt.com/docs/build-skills); the Claude source is cited as
   `code.claude.com/docs/en/skills` (the owner's `/docs/en/slash-commands` serves the same page,
   "Extend Claude with skills", checked today).
6. Skills are orchestrator instruction files; what they are not is *worker-context* instruction
   files (§1.2, fact 3).

v1.2 (12:40 CDT): the host pre-check of the plan's W4 brief found `:0.12` twice in
`docs/2026-08-27-how-do-i-get-your-setup.md` — the 0.12.0 build record. History, like
`docs/superpowers/`; A9's exclusion now covers the dated posts under `docs/` (`docs/2026-*`), and
§3.4 says so.

## 1. Problem and evidence

### 1.1 Today

`dirtywork init` (0.12.1) renders `dirtywork/contract/SKILL.md`, stamps it, and writes it to Claude
Code's two discovery paths (`~/.claude/skills/dirtywork/SKILL.md`, `<repo>/.claude/skills/dirtywork/SKILL.md`).
Every other agent gets `dirtywork init --stdout` and a sentence in `docs/orchestrator-setup.md`
telling it to place the text by hand. `destinations()` (`dirtywork/contract/__init__.py:69`) has
`".claude"` as a literal in both paths; that literal is the whole Claude-specificity of `init`.

### 1.2 Verified landscape (2026-08-29, each tool's own documentation)

| Tool | Personal skills | Project skills | Frontmatter | Source |
|---|---|---|---|---|
| Claude Code | `~/.claude/skills/<name>/SKILL.md` | `.claude/skills/<name>/SKILL.md` | `description` required; `name` optional (display only for personal/project skills) | code.claude.com/docs/en/skills — "Claude Code skills follow the Agent Skills open standard" |
| Codex CLI | `$HOME/.agents/skills` | `.agents/skills` in **every directory from `$CWD` up to the repository root** (also `/etc/codex/skills`) | `name`, `description` required | learn.chatgpt.com/docs/build-skills (redirect target of developers.openai.com/codex/skills) — invoked as `$dirtywork` or implicitly by description |
| Gemini CLI | `~/.gemini/skills/` **or the `~/.agents/skills/` alias** (alias wins within the tier) | `.gemini/skills/` or `.agents/skills/` (workspace skills need workspace trust) | `name`, `description` both required, first thing in the file | geminicli.com/docs/cli/skills and …/cli/tutorials/skills-getting-started — activation calls `activate_skill` and **asks the user for consent**; `/skills list`, `gemini skills list --all` |
| Cursor | `~/.agents/skills/`, `~/.cursor/skills/` (legacy: `~/.claude/skills/`, `~/.codex/skills/`) | `.agents/skills/`, `.cursor/skills/` (legacy: `.claude/skills/`, `.codex/skills/`) | `name`, `description` required | cursor.com/docs/skills — `/` menu or automatic |
| GitHub Copilot (CLI, VS Code, cloud agent) | `~/.copilot/skills`, `~/.agents/skills` (VS Code also `~/.claude/skills`) | `.github/skills`, `.claude/skills`, `.agents/skills` | `name`, `description` required (VS Code doc) | docs.github.com/en/copilot/concepts/agents/about-agent-skills; code.visualstudio.com/docs/agent-customization/agent-skills |
| Agent Skills standard | — | — | `name` ≤ 64 chars, `[a-z0-9-]`, **must match the directory name**; `description` ≤ 1024 chars | agentskills.io/specification |

Three facts fall out:

1. **One directory pair — `~/.agents/skills/dirtywork/SKILL.md` and `<repo>/.agents/skills/dirtywork/SKILL.md` — is read by Codex, Gemini CLI, Cursor and Copilot.** Cursor and Copilot additionally read the `.claude/skills` copies a Claude install already wrote.
2. **The 0.12 template is already a valid standard skill**: `name: dirtywork` matches the directory `dirtywork`, `description` is 273 characters (limit 1024), nothing else is required anywhere. The same bytes are correct for every tool. There is no wrapper to write.
3. **None of these paths is a file the worker is fed.** `workspace.load_repo_context` (`workspace.py:242-270`) reads exactly `CLAUDE.md` and `AGENTS.md` at the repository root of the base commit, by `git ls-tree`. `.agents/skills/…` is not `AGENTS.md`. The issue's worry — that Codex's only target is `AGENTS.md` — is moot: Codex reads skills, and a skill is an orchestrator instruction file, not a
   *worker-context* one. `~/.codex/AGENTS.md` (which does exist, with `AGENTS.override.md`) is not used.

What the issue got wrong, recorded so it is not re-derived: Codex has had skills since well before this
spec; Gemini CLI too; Cursor's rules (`.cursor/rules/*.mdc`) and Copilot's instructions
(`.github/instructions/*.instructions.md`, `~/.copilot/instructions/`) are the *always-on* mechanism,
which is the wrong shape for an on-demand playbook and, for Copilot, includes `AGENTS.md`/`CLAUDE.md`.
Skills are the on-demand mechanism in all five tools.

### 1.3 The provider assumption (owner's report, 2026-08-27)

A Windows/Ollama user read the 0.12.1 skill and concluded the orchestrator "will never pass the
provider nor a base-url". The skill mentions providers once ("Other providers: see the contract"),
its run template has no `--provider`/`--base-url` line, and its first-run check is `curl
localhost:1234/v1/models` naming LM Studio. `run` has `--provider {openai,anthropic,ollama}` and
`--base-url` (`__main__.py:1033,1036`). The exit-2 hint an Ollama user then sees is
`_ENDPOINT_HINTS["openai"]` = "Is the OpenAI-compatible server running? Try: lms ps" — LM Studio again.

## 2. Scope

In:

- `init --agent {claude,codex,gemini,cursor,copilot}` (default `claude`), a destination table, and
  nothing else in `init`'s mechanics changed.
- The provider-neutral skill body (§3.2) and the `_ENDPOINT_HINTS["openai"]` string.
- Tests (§5), the contract/CLI/CI ripple (§3.4), docs (§3.4), version 0.13.0 and the per-minor
  worker-image cycle `docker/README.md` prescribes.
- Built the dogfood way: released 0.12.1 + local worker; receipts in the ledger.

Out (each with the reason):

- `--format generic` / a body without frontmatter — `--stdout` is that; the frontmatter is part of
  the standard and harmless to a tool that ignores it.
- Per-tool wrappers, `~/.codex/AGENTS.md`, `.cursor/rules/*.mdc`, `.github/instructions/`,
  `GEMINI.md` — wrong mechanism (§1.2), and two of them are files the worker is fed.
- `--agent all` or a repeatable `--agent` — run `init` once per tool; revisit if anyone asks.
- Hiding `.claude/` / `.agents/` from the worker's tree — #84.
- Windows tier 2 (#96) — separate PRs on request.
- Refreshing this repo's committed `.claude/skills/dirtywork/SKILL.md` — after the release, with the
  released 0.13.0, as #89 did for 0.12.

## 3. Design

### 3.1 `--agent` and the destination table

```
dirtywork init [--agent {claude,codex,gemini,cursor,copilot}] [--repo PATH] [--no-user] [--force] [--stdout]
```

In `dirtywork/contract/__init__.py`:

```python
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
```

`destinations(args)` replaces its two `".claude"` literals with `AGENT_DIRS[args.agent]`; every
other line of the module is unchanged. Destinations are `<Path.home()>/<dir>/skills/dirtywork/SKILL.md`
and `<repo>/<dir>/skills/dirtywork/SKILL.md`. The parser gains
`init_p.add_argument("--agent", choices=AGENTS, default="claude", help=...)`; argparse rejects
anything else with its usual usage error (rc 2).

Everything in #82 §3.3 applies per destination unchanged: the stamp, `decide()`'s five outcomes and
their stdout lines, `--force`, `--no-user` (`--no-user` without `--repo` is still "nothing to
write"), `--repo` validation before any write, resolve-based de-duplication, atomic writes,
`~/<dir>/` created if absent (`--no-user` opts out, as for `~/.claude/` in 0.12).
`render_skill` is untouched. `--stdout` prints the same bytes whatever `--agent` says; `--agent` is
accepted with `--stdout` and has no effect on the output (test 26).

The four non-Claude agents share one destination pair: `init --agent codex` and `init --agent
gemini` write the same two files, and the second reports `up to date:` — one install serves all
four. Claude Code plus any of the others is two independent pairs, each with its own stamp. A
Claude user who also runs Cursor or Copilot already has a copy those tools read (§1.2); the docs
say so, `init` does not special-case it.

### 3.2 The skill body: provider-neutral

Three edits to `dirtywork/contract/SKILL.md` (full template in Appendix A; the plan's brief points
the worker at it verbatim). Nothing else in the body changes.

(a) **Before the first run** — the LM Studio bullet becomes:

```
- The endpoint you will pass is serving and the model you will pass as `--model`
  is loaded (LM Studio `curl -s http://localhost:1234/v1/models`; Ollama
  `ollama ps`) or available (another OpenAI-compatible server:
  `curl -s <base-url>/models`). If the request does not say which provider,
  find out; if more than one is running, ask — never pick one silently, and
  never assume LM Studio. `--provider anthropic` needs `ANTHROPIC_API_KEY`.
```

(b) **Run template** — one line added between `--model` and `--verify`, and one bullet:

```
    dirtywork run --repo <path> "<brief>" \
      --model <model> \
      [--provider openai|anthropic|ollama] [--base-url <url>] \
      --verify "<test command>" --verify-rounds 2 \
      --max-turns 60 --timeout 1800
```

```
- `--provider`/`--base-url`: omit both for LM Studio on `localhost:1234` (the
  default). Use `--provider ollama` for Ollama; for another OpenAI-compatible
  server, pass `--base-url <url>`. `resume` inherits `--provider`, but it does
  not restore a custom `--base-url` — repeat that custom URL on every resume.
```

(c) `dirtywork/__main__.py` `_ENDPOINT_HINTS["openai"]` becomes

```
"Is the OpenAI-compatible server running? Try: lms ps (LM Studio). Another server: --base-url <url>; Ollama: --provider ollama"
```

No test pins the old string (`grep -rn "lms ps" tests/` is empty). The last sentence of (b) is
verified against `__main__.py:776-789` and `:244-245`: `resume` inherits `--provider` from the run
it continues (an explicit *different* one is a preflight error), but `--base-url` is never read
back from the prior run — omitted, it falls back to the provider's default, which for `openai` is
LM Studio's port. A run started against another server therefore needs `--base-url` on every
`resume`. That is precisely the trap the 2026-08-27 report describes, one step later.

Constraints the edits respect: the drift guard (`test_skill_flags_exist_in_parser`) collects every
`--[a-z][a-z-]*` token; `--provider`, `--base-url` and `--model` exist in the parser, and
`openai|anthropic|ollama`, `<url>` are not option-shaped. Line count 126 → 135 (Appendix A, counted) against the cap of
200. The "Parallelism is processes … (LM Studio serves several requests per loaded model)" line is
left alone: true, and I have not verified Ollama's parallelism default, so the skill does not claim it.

**Consequence for existing installs, stated so the tests assert the right thing.** The issue's
acceptance sketch said `init` with no flag is "byte-identical" to 0.12's output. With the body
edited that is false and should be: *destinations and frontmatter identical; body differs only by
(a) and (b)*. An unmodified 0.12.1 install therefore prints `updated: <path> (v0.12.1 -> v0.13.0)`
on the first `init` after upgrade (the 0.12.0 → 0.12.1 path via #91 did exactly this); an edited one
prints `skipped (locally modified)` and rc 1, as before. A user who hand-added a `--provider` line
per the 0.12 docs can drop the edit after upgrading: the template carries the flags now.

### 3.3 The constraint every agent must respect — test 17, extended

`test_worker_prompt_does_not_inject_skill_file` is parametrized over `AGENTS`: a repo whose base
commit contains `<dir>/skills/dirtywork/SKILL.md` (rendered) and a plain file, and no `CLAUDE.md`
or `AGENTS.md`; `load_repo_context(repo, base) is None` for every agent. The reader lists two exact
root paths by `ls-tree`, so no skill directory can match today; the test pins that against a future
reader that walks or globs.

### 3.4 Ripple (each site named)

- `dirtywork/contract/__init__.py`: `AGENT_DIRS`, `AGENTS`, `destinations()`; module and
  `destinations` docstrings say "orchestrator skill", not "Claude Code skill".
- `dirtywork/__main__.py`: `from .contract import AGENTS` at module top (the lazy `from . import
  contract as contract_mod` in `main()` stays; `contract/__init__.py` imports only
  `dirtywork.__version__` from a one-line `dirtywork/__init__.py`, so there is no cycle); `--agent`
  on `init_p` with `choices=AGENTS, default="claude"`; `init`'s help → "install the skill that teaches an
  orchestrating agent to drive dirtywork (Claude Code by default; --agent for Codex, Gemini CLI,
  Cursor, Copilot)"; `--repo`/`--no-user` help name `<dir>/skills/dirtywork/SKILL.md` generically
  ("that agent's skills directory"); the top-level parser's description sentence (`:1169`) →
  "`dirtywork init` installs the orchestrator skill (Claude Code by default, `--agent` for others)";
  `_ENDPOINT_HINTS["openai"]` (§3.2c).
- `dirtywork/contract/machine-contract.md`: synopsis lines 41-42 gain `[--agent
  claude|codex|gemini|cursor|copilot]` and the comment "install the orchestrator skill"; the
  **init** entry (lines 233-246) gains: default `claude` → `~/.claude/skills/dirtywork/SKILL.md`
  (+ `<PATH>/.claude/…`); `codex`, `gemini`, `cursor`, `copilot` → `~/.agents/skills/dirtywork/SKILL.md`
  (+ `<PATH>/.agents/…`), "the file is the same for every agent (the Agent Skills standard); only
  the directory differs, and the four non-Claude agents share it — one `init` covers all four"; the
  last sentence extends to
  "a project copy committed to the target repo — under `.claude/` or `.agents/` — is visible to the
  worker like any other file". Everything else in the entry stands.
- `.github/workflows/ci.yml` `wheel-smoke`: one added step after the existing `init --stdout`
  check — `mkdir smoke-repo && smoke/bin/dirtywork init --agent codex --no-user --repo smoke-repo
  && test -f smoke-repo/.agents/skills/dirtywork/SKILL.md && smoke/bin/dirtywork init --agent
  gemini --stdout > skill2.md && cmp skill.md skill2.md`. Files and `test -f`/`cmp`, no pipes
  (same reasoning as #82 §3.5).
- **Version 0.13.0** in `pyproject.toml` and `dirtywork/__init__.py`, **plus the per-minor image
  cycle** (#82 §3.5, owner decision C2, unchanged policy): `dirtywork/sandbox/docker_args.py`
  `DEFAULT_IMAGE` → `ghcr.io/jimboschneider/dirtywork-worker:0.13`, `PINNED_DIGEST = None` with the
  0.12 digest (`sha256:edcf3a47…fe3c`) added to the history comment; `tests/test_docker_args.py:22`;
  `.github/workflows/ci.yml:98` docker-live tag → `:0.13`; **every** `:0.12` in the packaged
  contract — seven lines: 29, 74, 79, 86 (`my-worker:0.12`, twice), 109, 120 ("the `:0.12` image"),
  126 (`FROM :0.12`) — the unqualified and custom-image spellings included; every `:0.12` in
  `docker/README.md` (lines 28, 40, 44-47, 105, 118, 125-126, 141-142, 154, 161-162, 197, 212-213,
  the `my-worker:0.12` custom-image examples included), plus `0.13.0` appended to the first-release
  list (line 115) and `0.13.1 for 0.13` to the pin list (line 119); `tests/test_docker_args.py:22-24`
  (assert and its pin comment); `docs/operating.md:494` ("the `:0.13` image, the default since
  0.13.0"). The digest is pinned in 0.13.1, as 0.12.1 did. Nothing under `docs/superpowers/` or in the dated posts `docs/2026-*.md`
  is swept (history — the 0.12.0 build record mentions `:0.12` twice), and the digest-history
  comment in `docker_args.py` keeps its `:0.12` lines.
- `README.md` **Use it from Claude Code** (lines 146-157): "Any other agent can read the same
  instructions with `dirtywork init --stdout`" → "`dirtywork init --agent codex --repo .` installs
  the same file where Codex looks — and Gemini CLI, Cursor and Copilot, which share that
  directory (`--agent gemini|cursor|copilot` write the same files)"; the two sentences "The skill
  assumes LM Studio on `localhost:1234` … says how" are deleted (the template carries
  `--provider`/`--base-url`); the "put your own tweaks (a `--provider` line, say)" example in the
  next paragraph loses its parenthetical.
- `docs/orchestrator-setup.md` and its hand-maintained twin `docs/orchestrator-setup.html`:
  "You need dirtywork 0.12.0 or later" → "0.12.0 or later; 0.13.0 for anything but Claude Code";
  a new section **Other orchestrators** after "Teach Claude the loop" — one command
  (`dirtywork init --agent codex --repo .`; `gemini`, `cursor` and `copilot` land in the same two
  files, so one install serves all four), where it lands, how each tool surfaces it (Codex:
  `$dirtywork` or by description; Gemini CLI: asks you to confirm the first time it activates it,
  `/skills list` shows it; Cursor and Copilot: the `/` menu, or on its own), which Copilot surfaces
  can actually run a local model (CLI and VS Code agent mode; the cloud agent cannot reach your
  machine), and the one rule in one sentence: never into `AGENTS.md`/`CLAUDE.md`, because the worker
  is fed those. In **Good to know**: the **Other providers** bullet shrinks to "the skill's run
  template carries `--provider`/`--base-url` and its first-run check covers LM Studio, Ollama and
  any other endpoint; if you added a `--provider` line by hand under 0.12, `init` will report your
  copy as locally modified — `--force` takes the new template, which has the line"; the **Other
  agents** bullet is deleted (superseded by the section); the "project skill is a file in the repo"
  bullet says `.claude/` or `.agents/`.
- `docs/operating.md:375-378` **Setting up an orchestrator**: one added sentence — "`--agent codex`
  (or `gemini`, `cursor`, `copilot`) writes the same file to `.agents/skills/` instead".
- Site check after the docs merge: Pages build `built`, all sitemap URLs 200 (standing rule).

### 3.5 Self-dogfood (after release, not part of the build)

Released 0.13.0 on the maintainer's machine: `dirtywork init` → `updated: … (v0.12.1 -> v0.13.0)`
for the unmodified home copy; `dirtywork init --agent codex` → `wrote: ~/.agents/skills/dirtywork/SKILL.md`.
A session in a non-Claude tool listing the skill is best-effort — it depends on which of those tools
the maintainer has installed — and is recorded as done or not done, not assumed.

## 4. Failure modes and limits

- **A tool moves its directory.** One table entry. The file is the standard's, so what moves is the
  path, never the content. The verification date and sources in §1.2 are the record to re-check against.
- **Gemini CLI asks for consent** when the model first activates the skill, showing `name`,
  `description` and the path. Expected, documented; the description already says what the skill does.
- **Copilot's cloud agent** runs on GitHub's infrastructure and cannot reach LM Studio or Docker on
  the user's machine. The skill is still harmless there (it will fail at `dirtywork --version`).
  The docs name the surfaces that work: Copilot CLI, VS Code agent mode.
- **A repo copy under `.agents/skills/` is visible to the worker** exactly like `.claude/skills/`
  is today — readable in the tree, never injected. The first paragraph's "worker: ignore this" holds;
  hiding it for real is #84.
- **`~/.agents/` created on a machine without any of those tools** — same trade as `~/.claude/` in
  0.12: accepted for "one command", `--no-user` opts out.
- **`--agent` is single-valued.** Two tools, two commands. Each destination pair stamps independently,
  so `--force` on one never touches the other.
- **Edited 0.12 copies** (the docs told users to add `--provider` by hand) print `skipped (locally
  modified)` after the upgrade, rc 1, file untouched — the safe direction. The docs say `--force` is
  now the right answer because the template has the line.
- **Codex scans `.agents/skills` in every directory from `$CWD` up to the repository root**, so a
  project copy at the root is found from any subdirectory; `init --repo` writes at the path given,
  which the docs tell users to make the repo root. Not enforced (`--repo` need not be a git repo —
  #82 §3.3).

## 5. Tests

`tests/test_contract.py`; the 0.12 tests keep their numbers and pass unchanged except where noted.
Same fixtures (`home` monkeypatches `HOME`; `_git`).

- 14 `test_skill_frontmatter_and_size` (extended): needles gain the exact template line
  `[--provider openai|anthropic|ollama] [--base-url <url>]`, `ollama ps`, `--base-url <url>`;
  asserts `"Other providers: see the contract"` is **absent**; the ≤ 200-line cap stands.
- 17 `test_worker_prompt_does_not_inject_skill_file` (extended): `@pytest.mark.parametrize("agent", contract.AGENTS)`;
  the committed file is `<repo>/<AGENT_DIRS[agent]>/skills/dirtywork/SKILL.md`.
- 24 `test_init_agent_writes_that_agents_paths` — parametrized over `AGENTS`: `init --agent <a> --repo <tmp>`
  writes `~/<dir>/skills/dirtywork/SKILL.md` and `<tmp>/<dir>/skills/dirtywork/SKILL.md`, two
  `wrote:` lines user-then-project, both files byte-equal to `render_skill(__version__)`, rc 0.
- 25 `test_init_default_agent_is_claude` — `init` and `init --agent claude` write the same path
  (`~/.claude/skills/dirtywork/SKILL.md`); the second run prints `up to date:`.
- 26 `test_init_stdout_is_identical_across_agents` — for every agent, `init --agent <a> --stdout`
  stdout == `render_skill(__version__)` and no file is written.
- 27 `test_init_unknown_agent_is_a_usage_error` — `init --agent emacs` → `SystemExit` with code 2,
  nothing written.
- 28 `test_init_agents_share_or_split_destinations` — in one `HOME`: `init --agent claude` then
  `init --agent codex` → both files exist, the second prints `wrote:`; then `init --agent gemini`
  → `up to date:` for the same `~/.agents/…` file (the four share it); a local edit to the
  `.claude` copy, then `init --agent cursor` → still `up to date:` and the edited copy is untouched.
- 29 `test_agent_dirs_table` — `AGENT_DIRS["claude"] == ".claude"`; every other value is
  `".agents"`; `AGENTS == tuple(AGENT_DIRS)`; the parser's `--agent` choices equal `AGENTS`
  (walk `m._build_parser()` as `_option_strings` does, find the `init` subparser's `--agent` action).
- 30 `test_endpoint_hint_names_the_flags` — `m._ENDPOINT_HINTS["openai"]` contains `--base-url`
  and `--provider ollama` and still `lms ps`.
- `tests/test_docker_args.py:22` → `:0.13`; `PINNED_DIGEST is None` asserted the way 0.12.0 did.

CI: the `wheel-smoke` step in §3.4.

## 6. Acceptance

| # | Check | How |
|---|---|---|
| A1 | Every agent's documented paths | Tests 24, 29 green; for `codex`, `gemini`, `cursor`, `copilot` the written path is `~/.agents/skills/dirtywork/SKILL.md` — the path in each tool's docs (§1.2) |
| A2 | Default output unchanged in shape | `dirtywork init --stdout` frontmatter is byte-identical to 0.12.1's (`name:`, `description:` lines unchanged); the body differs only by §3.2 (a) and (b) (diff on the maintainer's machine against `pipx run --spec dirtywork==0.12.1 dirtywork init --stdout`, recorded in the PR) |
| A3 | Existing installs take the normal upgrade path | Released 0.13.0 on an unmodified 0.12.1 home copy prints `updated: … (v0.12.1 -> v0.13.0)`, rc 0 (manual, §3.5) |
| A4 | One file, every agent | Test 26; CI `cmp` step |
| A5 | Nothing is injected into the worker prompt, for any agent | Test 17 × 5 green |
| A6 | The skill names only real flags | Drift guard (test 16) green with `--provider`/`--base-url` present |
| A7 | Suite and CI | Full suite on the host; `wheel-smoke` (with the new step) green on the PR |
| A8 | Docs | `docs/orchestrator-setup.md` has **Other orchestrators**; `grep -n "init --stdout" README.md docs/orchestrator-setup.md` finds no "other agents use --stdout" sentence; after merge, Pages `built` and sitemap URLs 200 |
| A9 | Image cycle consistent — no `:0.12` in any spelling | `grep -rn ':0\.12\b' --include='*.py' --include='*.md' --include='*.yml' --include='*.toml' --include='Dockerfile*' . \| grep -v 'docs/superpowers\|docs/2026-'` returns **only** lines of the digest-history comment in `dirtywork/sandbox/docker_args.py` (the dated posts under `docs/` are build records, history like `docs/superpowers/`) (the pattern catches `dirtywork-worker:0.12`, `my-worker:0.12` and bare `FROM :0.12` alike; v1's `dirtywork-worker:0.12` pattern let the other two escape); `PINNED_DIGEST is None`; `docker/README.md` lists `0.13.0` among first releases and `0.13.1 for 0.13` among pins; `DEFAULT_IMAGE`, `ci.yml`, the contract and `docker/README.md` all say `:0.13` |
| A10 | Built the dogfood way | Each brief's run has a ledger row (status, turns, wall, tokens, tok/s, nudges, verdict); a Claude fallback, if any, is named in the PR |

## 7. Files

Modified: `dirtywork/contract/__init__.py`, `dirtywork/contract/SKILL.md`,
`dirtywork/contract/machine-contract.md`, `dirtywork/__main__.py`, `dirtywork/__init__.py`,
`dirtywork/sandbox/docker_args.py`, `pyproject.toml`, `tests/test_contract.py`,
`tests/test_docker_args.py`, `.github/workflows/ci.yml`, `docker/README.md`, `README.md`,
`docs/orchestrator-setup.md`, `docs/orchestrator-setup.html`, `docs/operating.md`.

New: none.

## 8. Open questions

None. The option's shape and name were the owner's call (§0). Everything else follows from §1.2.

## Appendix A — `dirtywork/contract/SKILL.md` (the 0.13 template; `{{VERSION}}` is the only placeholder; the stamp line is inserted by `render_skill`)

Edits against 0.12.1 are the three in §3.2; every other line is verbatim.

````markdown
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
- The endpoint you will pass is serving and the model you will pass as `--model`
  is loaded (LM Studio `curl -s http://localhost:1234/v1/models`; Ollama
  `ollama ps`) or available (another OpenAI-compatible server:
  `curl -s <base-url>/models`). If the request does not say which provider,
  find out; if more than one is running, ask — never pick one silently, and
  never assume LM Studio. `--provider anthropic` needs `ANTHROPIC_API_KEY`.
- `docker info` — docker mode (the default, and the contained one) needs a
  running Docker; the worker image is pulled on the first run if it is
  absent (exit 2 with "Build or pull the worker image" means that failed —
  see the contract's `--image` entry). `--sandbox none` runs the worker on
  your host: read the contract's **Security** entry first.

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
      [--provider openai|anthropic|ollama] [--base-url <url>] \
      --verify "<test command>" --verify-rounds 2 \
      --max-turns 60 --timeout 1800

- `--provider`/`--base-url`: omit both for LM Studio on `localhost:1234` (the
  default). Use `--provider ollama` for Ollama; for another OpenAI-compatible
  server, pass `--base-url <url>`. `resume` inherits `--provider`, but it does
  not restore a custom `--base-url` — repeat that custom URL on every resume.
- `--verify` runs your gate in the sandbox after the worker finishes and feeds
  failures back for up to `--verify-rounds` further attempts.
- Progress is on **stderr**: transcript path, worktree path, `error:` lines.
  `tail -f` the transcript to watch a run.
- **Past preflight, stdout is exactly one JSON object.** Parse it; do not
  grep it. On exit 2 stdout is empty and the reason is the `error:` line on
  stderr. Fields you will use: `status`, `worktree`, `branch`, `base_commit`,
  `transcript`, `run_dir` (its last path component is the slug),
  `resumed_from`, `turns`, `files_changed`, `final_message`, `stuck_on`,
  `last_tool_result`, `last_assistant_text`.
- Exit codes: `0` = `completed`. `1` = any other status (`max_turns`,
  `timeout`, `stalled`, `stuck`, `verify_failed`, `context_exhausted`,
  `model_error`, `budget_exceeded`, `unchanged`, …) — the worktree and branch
  are kept for review and salvage. `2` = preflight or environment error;
  nothing was created.
- In the **default** docker mode the container has no network and no host
  directories mounted (only the run volume and a read-only view of the repo's
  git objects), so the worker **cannot install dependencies**; it has what the image
  ships — git, bash, python3, node/npm, .NET, ripgrep, jq, curl, shellcheck.
  `--allow-network` gives the container bridge networking so installs work and
  the offline guarantee is gone — use it deliberately, and for anything
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
earlier work and apply your feedback. A resume is a **new run** with its own
slug and `run_dir`; its JSON's `resumed_from` names the run it continued while
`branch` and `worktree` still carry the original slug. From here on use the
newest slug for `runs verdict` and `runs clean` — cleaning an earlier slug in
the chain leaves the worktree and branch in place. Two resumes without
convergence means the brief was wrong: reject, rewrite the brief, run again
from a clean base.

### 5. Verdict and cleanup

- `dirtywork runs verdict <slug> accept|reject --note "<why>"` records your
  decision in the run's `run.json`.
- Accept: commit or PR the `dirtywork/<slug>` branch as you would any
  contributor's work.
- Reject: record it, then discard the work but not the record —
  `dirtywork runs clean <slug> --force --keep-transcript`. `--force` because a
  rejected worktree has uncommitted changes and `clean` refuses to delete
  those otherwise; `--keep-transcript` keeps `run.json` and the transcript and
  removes the container, volume, worktree and branch. Plain `runs clean <slug>`
  deletes the receipts too.
- `dirtywork runs list` and `dirtywork runs show <slug>` inspect earlier runs.

## Rules of thumb

- Never merge unreviewed worker output. The worker ran `bash` in a sandbox;
  a shell is a shell — see the contract's **Security** entry.
- Parallelism is processes: run independent briefs concurrently (LM Studio
  serves several requests per loaded model). Never two tasks in one brief.
- On `stuck`, read `stuck_on` (the repeated failing command); on `stalled` or
  `max_turns`, read `last_tool_result` and `last_assistant_text`. Either way
  the fix is usually in the brief, not the model.
- Keep receipts: `run.json`, the transcript and the verdict are the record of
  what the worker did and what you decided. `runs clean --keep-transcript`
  preserves them; plain `clean` does not.
````
