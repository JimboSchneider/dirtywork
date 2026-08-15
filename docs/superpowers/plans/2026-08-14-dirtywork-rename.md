# dirtywork Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename localagent → **dirtywork** across package, CLI, PyPI, GitHub repo, docs, domain, and portfolio, releasing as 0.2.0.

**Architecture:** Pure mechanical rename, no behavior changes. Repo-content changes (Tasks 1–4) land on one branch → one held PR. External-surface changes (repo rename, DNS, PyPI publisher, release, yank, portfolio) happen post-merge, in dependency order: repo rename precedes the PyPI publisher binding, which precedes the release.

**Tech Stack:** Python 3.9+ stdlib-only package, pytest, setuptools via `pyproject.toml`, GitHub Actions (OIDC trusted publishing), GitHub Pages (`main` `/docs`), name.com DNS.

**Spec:** `docs/superpowers/specs/2026-08-14-dirtywork-rename-design.md`

## Global Constraints

- New identity, verbatim everywhere: dist **`dirtywork`**, module **`dirtywork`**, CLI **`dirtywork`**, repo **`JimboSchneider/dirtywork`**, domain **`dirtywork.run`**, version **`0.2.0`**.
- Tagline, verbatim: *Frontier models do the thinking. Local models do the dirty work.*
- Renamed artifacts: branch prefix `localagent/<slug>` → `dirtywork/<slug>`, worktree dir `.worktrees/la-<slug>` → `.worktrees/dw-<slug>`, runs dir `~/.localagent/runs` → `~/.dirtywork/runs`.
- Python 3.9+ stdlib only — the rename must add no dependencies.
- Historical documents (`docs/2026-08-14-building-localagent.md`, `docs/building-localagent.html`, old specs/plans) keep their filenames and their "localagent" narrative; they get a dated addendum, not a rewrite.
- Commits: conventional messages, **no Co-Authored-By / attribution footers**.
- PR is **held for Jimbo's review — never self-merge**. Release creation and PyPI yank need Jimbo's explicit go-ahead.
- All commands run from the repo root `~/repos/localagent` unless stated. macOS BSD sed (`sed -i ''`).

---

### Task 1: Module, CLI, and artifact-name rename

**Files:**
- Rename: `localagent/` → `dirtywork/` (whole package), `bin/localagent` → `bin/dirtywork`
- Modify: `dirtywork/workspace.py:38,40`, `dirtywork/__main__.py:22,47,117,134`, all of `tests/*.py`, `bin/dirtywork`

**Interfaces:**
- Consumes: current package `localagent` (entry `localagent.__main__:main`, `RUNS_DIR`, worktree helpers).
- Produces: package `dirtywork` with `dirtywork.__main__:main`, `RUNS_DIR = ~/.dirtywork/runs`, worktree `.worktrees/dw-<slug>`, branch `dirtywork/<slug>`. Tasks 2–3 rely on exactly these names.

- [ ] **Step 1: Create the work branch**

```bash
git checkout -b rename/dirtywork
```

- [ ] **Step 2: Update test expectations first (the failing "tests")**

```bash
sed -i '' -e 's/localagent/dirtywork/g' -e 's/"la-/"dw-/g' tests/*.py
```

This rewrites imports (`from localagent…` → `from dirtywork…`), the branch-name assertions in `tests/test_workspace.py:91–164` (`localagent/demo-08141109` → `dirtywork/demo-08141109`), and the worktree literals (`"la-demo-08141109"` → `"dw-demo-08141109"`, `"la-dup-08141109"` → `"dw-dup-08141109"`).

- [ ] **Step 3: Run the suite to verify it fails**

Run: `python3 -m pytest -q`
Expected: collection errors — `ModuleNotFoundError: No module named 'dirtywork'` (the module doesn't exist yet; that's the red state).

- [ ] **Step 4: Rename the package and launcher, update internals**

```bash
git mv localagent dirtywork
git mv bin/localagent bin/dirtywork
sed -i '' 's/localagent/dirtywork/g' dirtywork/*.py bin/dirtywork
sed -i '' 's/f"la-{slug}"/f"dw-{slug}"/' dirtywork/workspace.py
sed -i '' 's/\.localagent/.dirtywork/' dirtywork/__main__.py 2>/dev/null; true
rm -rf local_agent.egg-info
```

The first sed covers: package-internal imports, `workspace.py:40` branch prefix, `__main__.py` `prog="dirtywork"` + both `"branch": f"dirtywork/{slug}"` strings, and the launcher's `python3 -m dirtywork`. The `.localagent` sed is belt-and-braces — the first sed already turned `".localagent"` into `".dirtywork"` (verify in Step 5). Also update the two comment lines in `bin/dirtywork` that still say `pipx install dirtsimple-agent` → `pipx install dirtywork`.

- [ ] **Step 5: Verify no stale references and the suite passes**

```bash
grep -rn "localagent\|\"la-" dirtywork/ tests/ bin/ ; echo "grep exit: $?"
python3 -m pytest -q
```

Expected: grep exit 1 (no matches); full unit suite passes (133 tests; the live suite is excluded by default addopts).

- [ ] **Step 6: Smoke-test the launcher**

Run: `./bin/dirtywork --help`
Expected: usage text with `prog` shown as `dirtywork`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat!: rename package and CLI to dirtywork"
```

---

### Task 2: Packaging metadata and publish workflow

**Files:**
- Modify: `pyproject.toml:6,7,25,28,31`, `.github/workflows/publish.yml:29`

**Interfaces:**
- Consumes: package `dirtywork` from Task 1.
- Produces: dist name `dirtywork` 0.2.0, entry point `dirtywork = "dirtywork.__main__:main"`. Task 8 (release) relies on these exact values.

- [ ] **Step 1: Update `pyproject.toml`**

Change these lines (current → new):

```toml
name = "dirtywork"            # was: dirtsimple-agent   (line 6)
version = "0.2.0"             # was: 0.1.0              (line 7)
Repository = "https://github.com/JimboSchneider/dirtywork"   # line 25
[project.scripts]
dirtywork = "dirtywork.__main__:main"                        # line 28
[tool.setuptools]
packages = ["dirtywork"]                                     # line 31
```

Also add under `[project.urls]` (same block as Repository):

```toml
Homepage = "https://dirtywork.run"
```

If the `description` field mentions localagent, rewrite it to: `Frontier models do the thinking. Local models do the dirty work. Delegate coding tasks to local LLMs in isolated git worktrees.`

- [ ] **Step 2: Update `publish.yml` environment URL**

Line 29: `url: https://pypi.org/p/dirtsimple-agent` → `url: https://pypi.org/p/dirtywork`

- [ ] **Step 3: Verify the build produces the new dist**

```bash
pipx run build
ls dist/
```

Expected: `dirtywork-0.2.0.tar.gz` and `dirtywork-0.2.0-py3-none-any.whl`. Then clean up: `rm -rf dist/ dirtywork.egg-info/`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml .github/workflows/publish.yml
git commit -m "feat: publish as dirtywork 0.2.0"
```

---

### Task 3: README, SECURITY, and docs content

**Files:**
- Modify: `README.md`, `SECURITY.md:6,11`, `docs/index.html`
- Modify (addendum only): `docs/building-localagent.html`, `docs/2026-08-14-building-localagent.md`

**Interfaces:**
- Consumes: names from Tasks 1–2.
- Produces: user-facing docs matching the new identity; canonical URL `https://dirtywork.run/building-localagent.html`.

- [ ] **Step 1: Mechanical README/SECURITY sweep**

```bash
sed -i '' \
  -e 's|JimboSchneider/localagent|JimboSchneider/dirtywork|g' \
  -e 's|jimboschneider.github.io/localagent/|dirtywork.run/|g' \
  -e 's/dirtsimple-agent/dirtywork/g' \
  -e 's/localagent/dirtywork/g' \
  README.md SECURITY.md
```

(Order matters: URL forms first, then the bare word.)

- [ ] **Step 2: Hand-fix the README spots seds can't judge**

1. Title block (lines 1–6): heading becomes `# dirtywork`, and directly under the badges add the tagline as a standalone italic line: `*Frontier models do the thinking. Local models do the dirty work.*`
2. Install section (~lines 42–45): now reads `pipx install dirtywork`; **delete** the parenthetical "(The distribution name is `dirtsimple-agent`; the installed command is still `localagent`.)" — dist and command finally match.
3. Cleanup/branch references (~lines 68, 75, 86, 165): confirm they now read `dirtywork/<slug>`, and change worktree examples from `la-` to `dw-` and `~/.localagent/runs` to `~/.dirtywork/runs` (the seds handled `.localagent` via the bare-word rule — verify).
4. History section (~lines 115–126): the sed updated spec/plan/postmortem link *paths* — revert the two doc filenames back to their real on-disk names (`2026-08-13-localagent-design.md`, `2026-08-14-localagent.md`, `building-localagent.*`), since files keep historical names. Easiest: `git diff README.md`, and for any changed line that is a `docs/…localagent…` file path, restore the filename while keeping any repo-URL change.
5. Add one sentence at the end of the History section: `In August 2026 the project was renamed **dirtywork** — same tool, a name that says what it does.`

- [ ] **Step 3: Replace `docs/index.html`**

Full new content:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="0; url=building-localagent.html">
<link rel="canonical" href="https://dirtywork.run/building-localagent.html">
<title>dirtywork</title>
</head>
<body>
<p><a href="building-localagent.html">Building dirtywork</a> — the story of the harness (written under its original name, localagent).
Source: <a href="https://github.com/JimboSchneider/dirtywork">github.com/JimboSchneider/dirtywork</a></p>
</body>
</html>
```

- [ ] **Step 4: Add the rename addendum to the blog post (both editions)**

Append to `docs/2026-08-14-building-localagent.md` (bottom, after the existing evening addendum), and mirror the same text as a styled paragraph/aside at the bottom of `docs/building-localagent.html` matching its existing addendum markup:

```markdown
---

**Addendum (August 14, 2026, later still):** localagent is now **dirtywork**
([github.com/JimboSchneider/dirtywork](https://github.com/JimboSchneider/dirtywork),
`pipx install dirtywork`, [dirtywork.run](https://dirtywork.run)). A competitive
survey found the pattern this post describes — a frontier model orchestrating
and auditing cheap local workers — is a lane nobody's driving in; the ecosystem
mostly bets on *replacing* the frontier model instead. New name, same bet:
frontier models do the thinking, local models do the dirty work.
```

Do not change any other text in either blog file.

- [ ] **Step 5: Verify remaining references are intentional**

```bash
grep -rn "localagent" README.md SECURITY.md docs/index.html
grep -rln "localagent" docs/
```

Expected: zero hits in README/SECURITY; in `docs/index.html` only the `building-localagent.html` href/filename mentions; blog + old spec/plan files listed (historical, allowed).

- [ ] **Step 6: Commit**

```bash
git add README.md SECURITY.md docs/
git commit -m "docs: rename to dirtywork, add tagline and blog addendum"
```

---

### Task 4: GitHub Pages custom-domain file

**Files:**
- Create: `docs/CNAME`

**Interfaces:**
- Consumes: nothing.
- Produces: Pages custom-domain binding, activated when Task 7 sets DNS. Safe to merge before DNS exists.

- [ ] **Step 1: Confirm Pages serves from `main` `/docs`**

Run: `gh api repos/JimboSchneider/localagent/pages --jq '{source: .source, url: .html_url}'`
Expected: `"branch": "main", "path": "/docs"`. If the source differs, put `CNAME` in whatever directory Pages publishes and adjust the step below.

- [ ] **Step 2: Create the CNAME file**

```bash
printf 'dirtywork.run\n' > docs/CNAME
git add docs/CNAME
git commit -m "feat: bind GitHub Pages to dirtywork.run"
```

---

### Task 5: Open the PR — then STOP

- [ ] **Step 1: Push and open the PR**

```bash
git push -u origin rename/dirtywork
gh pr create --title "feat!: rename localagent to dirtywork" --body "$(cat <<'EOF'
Renames the project to **dirtywork** per docs/superpowers/specs/2026-08-14-dirtywork-rename-design.md.

- Package/CLI/launcher: `localagent` → `dirtywork`; branch prefix `dirtywork/<slug>`, worktrees `.worktrees/dw-<slug>`, runs dir `~/.dirtywork/runs`
- Packaging: dist `dirtywork` 0.2.0, entry point `dirtywork`, publish env URL updated
- Docs: README/SECURITY renamed, tagline added, blog gets a dated rename addendum (history preserved), `docs/CNAME` → dirtywork.run
- No behavior changes; unit suite green (133)

After merge (separate, ordered): repo rename → DNS + Pages domain → PyPI trusted publisher for `dirtywork` → release v0.2.0 → yank dirtsimple-agent 0.1.0.
EOF
)"
```

- [ ] **Step 2: STOP.** Hold for Jimbo's review. Do not merge. Tasks 6–11 only start after Jimbo merges.

---

### Task 6: Rename the GitHub repo (post-merge)

- [ ] **Step 1: Rename and update metadata**

```bash
gh repo rename dirtywork -R JimboSchneider/localagent --yes
gh repo edit JimboSchneider/dirtywork \
  --description "Frontier models do the thinking. Local models do the dirty work. Delegate coding tasks to local LLMs in isolated git worktrees." \
  --homepage "https://dirtywork.run"
```

- [ ] **Step 2: Repoint the local clone**

```bash
git -C ~/repos/localagent remote set-url origin https://github.com/JimboSchneider/dirtywork.git
mv ~/repos/localagent ~/repos/dirtywork
git -C ~/repos/dirtywork pull
```

(Old `github.com/JimboSchneider/localagent` URLs redirect automatically; Pages URLs do not — that's what Tasks 4+7 solve.)

---

### Task 7: DNS and Pages domain activation

- [ ] **Step 1: Create DNS records at name.com**

Preferred: fix the name.com MCP token (name.com → Account → API tokens; the MCP has been 401ing) and Claude applies these. Otherwise Jimbo enters them in the name.com DNS UI. Records for `dirtywork.run`:

| Type | Host | Answer |
| ---- | ---- | ------ |
| A | @ | 185.199.108.153 |
| A | @ | 185.199.109.153 |
| A | @ | 185.199.110.153 |
| A | @ | 185.199.111.153 |
| CNAME | www | jimboschneider.github.io |

(These are GitHub Pages' published apex IPs — verify against the current GitHub Pages docs before entering.)

- [ ] **Step 2: Confirm the custom domain is set on the repo**

Run: `gh api repos/JimboSchneider/dirtywork/pages --jq '{cname: .cname, https: .https_enforced}'`
Expected: `cname: dirtywork.run` (picked up from `docs/CNAME` on the post-merge Pages deploy). If null, set it: `gh api -X PUT repos/JimboSchneider/dirtywork/pages -f cname=dirtywork.run`.

- [ ] **Step 3: Verify serving, then enforce HTTPS**

```bash
curl -sI https://dirtywork.run/ | head -3
```

Expected: `HTTP/2 200` (or 301 → `building-localagent.html`). Certificate provisioning can lag DNS by minutes-to-hours; retry before debugging. Once the cert is live: `gh api -X PUT repos/JimboSchneider/dirtywork/pages -F https_enforced=true`.

---

### Task 8: PyPI trusted publisher + release 0.2.0

- [ ] **Step 1 (Jimbo, manual — needs PyPI login): add a pending trusted publisher**

At pypi.org → Account → Publishing → "Add a new pending publisher" (GitHub):

| Field | Value |
| ----- | ----- |
| PyPI project name | `dirtywork` |
| Owner | `JimboSchneider` |
| Repository name | `dirtywork` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

This must exist **before** the release publishes, or the upload is rejected.

- [ ] **Step 2: Confirm go-ahead with Jimbo, then create the release**

```bash
gh release create v0.2.0 -R JimboSchneider/dirtywork --title "dirtywork 0.2.0" --notes "$(cat <<'EOF'
localagent is now **dirtywork** — same tool, a name that says what it does.

- Install: `pipx install dirtywork` (replaces `dirtsimple-agent`, now yanked)
- CLI renamed: `dirtywork run --repo <repo> "<task>"`
- Worktrees now at `.worktrees/dw-<slug>` on branch `dirtywork/<slug>`; transcripts at `~/.dirtywork/runs/`
- Docs/blog: https://dirtywork.run
- No functional changes from 0.1.0
EOF
)"
```

- [ ] **Step 3: Watch the publish workflow and verify the install**

```bash
gh run watch -R JimboSchneider/dirtywork
pipx install dirtywork
dirtywork --help
pipx uninstall dirtsimple-agent
```

Expected: workflow green; `pipx install` succeeds; help text shows `dirtywork`. If PyPI rejects the name on PEP 541 similarity grounds (unlikely — verified 404 on 2026-08-14): rename dist to `dirty-work` in `pyproject.toml` and the pending publisher, keep module/CLI `dirtywork`, re-tag v0.2.1.

---

### Task 9: Yank `dirtsimple-agent` 0.1.0

- [ ] **Step 1 (Jimbo, manual — needs PyPI login):** pypi.org → project `dirtsimple-agent` → Manage → Releases → 0.1.0 → **Yank**, reason:

```
Renamed: install "dirtywork" instead — https://github.com/JimboSchneider/dirtywork
```

**Noted spec deviation:** the spec said "update description/README to a pointer *and* yank." Changing a PyPI project's description requires uploading a new release, and the old trusted publisher binding breaks once the repo is renamed — for a package with ≈0 installs, a stub upload is pure ceremony. The yank reason (shown by pip/pipx on any install attempt and banner'd on the project page) *is* the pointer. If Jimbo wants the full stub later, it needs a manually-tokened upload of a 0.1.1 whose README is one line.

---

### Task 10: DirtSimpleSolutions portfolio update

**Files:**
- Modify: `/Users/jimschneider/repos/DirtSimpleSolutions/frontend/src/App.tsx:106–110,157–163`

- [ ] **Step 1: Update the portfolio entry (lines 106–110)**

`name: 'localagent'` → `name: 'dirtywork'`; `url` → `'https://github.com/JimboSchneider/dirtywork'`; description text: replace the leading `localagent lets an orchestrating AI…` with `dirtywork lets an orchestrating AI…` (keep the rest of the sentence).

- [ ] **Step 2: Update the writing entry (lines 157–163)**

Keep `title: 'Building localagent'` (the post's real title); `url` → `'https://dirtywork.run/building-localagent.html'`.

- [ ] **Step 3: Build, commit, push**

```bash
cd /Users/jimschneider/repos/DirtSimpleSolutions/frontend && npm run build
cd /Users/jimschneider/repos/DirtSimpleSolutions && git add frontend/src/App.tsx && git commit -m "chore: localagent is now dirtywork" && git push
```

Expected: build green; push to `main` triggers the deploy workflow (established direct-push repo).

---

### Task 11: Final verification sweep and memory

- [ ] **Step 1: Success-criteria checklist (from the spec)**

```bash
pipx list | grep dirtywork && dirtywork --help >/dev/null && echo CLI-OK
python3 -m pytest -q          # in ~/repos/dirtywork — full suite green
curl -sI https://dirtywork.run/ | head -1
grep -rn "localagent" ~/repos/dirtywork --include="*.py" --include="*.toml" --include="*.yml" ; echo "code refs exit: $?"
```

Expected: CLI-OK; suite green; HTTP 200; final grep exits 1 (only historical docs may mention localagent).

- [ ] **Step 2: Update Claude memory**

Rewrite `~/.claude/projects/-Users-jimschneider-repos-invoicr/memory/project_localagent_tool.md`: rename slug/description to dirtywork, new invocation `dirtywork run …`, new paths (`~/.dirtywork/runs`, `.worktrees/dw-`, branch `dirtywork/`), repo path `~/repos/dirtywork`, mark the rename complete with date; update the `MEMORY.md` index line to match.
