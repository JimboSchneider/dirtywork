# dirtywork — rename & repositioning design

**Date:** 2026-08-14
**Status:** Draft for review

## Motivation

Competitive research (2026-08-14) found the tool's core loop — a local model running an
agentic tool loop in an isolated git worktree, leaving a full JSONL transcript that a
stronger frontier model audits before merge — is unoccupied white space. Every neighbor
ships a piece of it (Aider's architect/editor split, Qwen Code's worktrees + self-review,
MCP prompt-forwarding micro-servers), but nobody with traction ships the combination. The
ecosystem's dominant bet is *replacing* the frontier model with local ones; our bet flips
it: keep the frontier model, but only as planner and auditor.

"localagent" says none of that, collides with a dozen generic uses of the phrase, and the
PyPI distribution name (`dirtsimple-agent`) doesn't match the repo or CLI. Rename before
announcing, while the user base is effectively zero.

## New identity

| Surface        | Value                                                        |
| -------------- | ------------------------------------------------------------ |
| Name           | **dirtywork**                                                |
| Tagline        | *Frontier models do the thinking. Local models do the dirty work.* |
| PyPI dist      | `dirtywork` (verified unclaimed 2026-08-14)                  |
| Python package | `dirtywork` (renamed from `localagent`)                      |
| CLI command    | `dirtywork` (renamed from `localagent`)                      |
| GitHub repo    | `JimboSchneider/localagent` → `JimboSchneider/dirtywork`     |
| Domain         | **dirtywork.run** — registered at name.com, 3 years (to ~2029-08), renewal $41.99/yr |

The name ties into the Dirt Simple Solutions brand and the domain doubles as an
imperative: *dirtywork, run*.

## Scope of the rename

### 1. Package & CLI
- `pyproject.toml`: `name = "dirtywork"`, entry point `dirtywork = "dirtywork.__main__:main"`,
  `packages = ["dirtywork"]`, repository URL updated.
- Rename `localagent/` package dir → `dirtywork/`; update all imports and tests.
- `bin/localagent` launcher → `bin/dirtywork`.
- Any user-facing artifacts named after the tool (config paths, env vars, transcript
  headers, worktree branch prefixes) rename with it — inventory during implementation.
- Version: publish as **0.2.0** (minor bump signals succession from `dirtsimple-agent` 0.1.0).

### 2. PyPI transition
- Publish `dirtywork` 0.2.0 fresh.
- `dirtsimple-agent`: update description/README to a "renamed → dirtywork" pointer and
  **yank** 0.1.0 so new installs don't land on it. Do not delete (keeps the pointer live).
- **Trusted publishing must be reconfigured**: the PyPI OIDC publisher is bound to the
  project name + repo (`JimboSchneider/localagent` + `publish.yml`). Add a pending
  publisher for project `dirtywork` pointing at the renamed repo before tagging a release.
- Risk (low): PyPI can reject a name at upload time on PEP 541 similarity grounds even
  though the JSON API 404s. Nothing similar exists; if rejected anyway, fall back to
  `dirty-work` (also unclaimed).

### 3. GitHub repo
- Rename repo to `dirtywork`. Git remotes and web URLs auto-redirect; local clones keep
  working but update remotes anyway.
- **GitHub Pages URLs do not redirect.** Mitigation: put the docs/blog on the custom
  domain — `dirtywork.run` becomes the canonical docs/blog host (CNAME in the Pages
  config + A/ALIAS records at name.com). Old `jimboschneider.github.io/localagent/...`
  links break; acceptable, nothing announced yet.
- Update: README (title, badges, install command, tagline), CI badge URLs, repo About
  description + website field (→ https://dirtywork.run), SECURITY.md references.

### 4. Content ripples
- Blog post `building-localagent.html`: keep as historical record; add a dated note that
  the tool is now **dirtywork** (same pattern as the existing same-evening addendum).
  New posts live under dirtywork.run.
- DirtSimpleSolutions portfolio entry: update name, description, link.
- Claude memory file for the tool: update after execution.

### 5. Announcement (downstream, separate task)
- The rename unblocks the r/LocalLLaMA / HN announcement. Lead with the flip:
  the ecosystem races to replace frontier models; dirtywork keeps one as the quality
  gate and hands the grunt work to models you already run for free.

## Out of scope
- No behavior/feature changes ride along. The rename release is mechanical.
- No new docs site build — Pages + custom domain serving the existing HTML is enough.

## Risks & mitigations
| Risk | Mitigation |
| ---- | ---------- |
| PyPI similarity rejection of `dirtywork` | Fall back to `dirty-work`; both verified 404 today |
| OIDC publish breaks after rename | Reconfigure trusted publisher before tagging; test with a release candidate if needed |
| Stale `pipx install dirtsimple-agent` users | Yanked release + README pointer; user base ≈ 0 pre-announcement |
| Pages custom-domain DNS propagation lag | Set DNS immediately after purchase; verify before announcing |

## Success criteria
- `pipx install dirtywork` installs; `dirtywork --help` runs; full test suite (133) green
  under the renamed package.
- `https://dirtywork.run` serves the docs/blog over HTTPS.
- `dirtsimple-agent` on PyPI is yanked with a visible pointer.
- Repo, README, badges, portfolio all reflect the new name with no stale `localagent`
  references (except intentional historical mentions in the blog post).
