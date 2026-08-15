# Self-Rename Post, Landing Page & Beta Announcements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline — the source material for the prose is the orchestrating session's own history, which subagent implementers do not hold; reviewer subagents still gate every piece). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship the "The Tool Renamed Itself" blog post, a real landing page for dirtywork.run with SEO/link-preview hygiene, and three paste-ready beta announcements (r/LocalLLaMA, Show HN, DSS LinkedIn kit).

**Architecture:** All site files are static HTML/MD in `docs/`, served by GitHub Pages at dirtywork.run. Branch `post/the-tool-renamed-itself` (already holds the spec) → one held PR. Announcement texts are session deliverables (scratchpad + chat), never committed. Every prose piece passes a reviewer-subagent gate (facts vs. the spec's ledger, voice vs. the corpus rules) before it counts as done.

**Tech Stack:** Hand-authored HTML/CSS matching `docs/building-localagent.html`'s design system; no JS frameworks, no build step, no analytics. Verification via the chrome-devtools MCP (local rendering screenshots at desktop + 375px) and Python stdlib checks.

**Spec:** `docs/superpowers/specs/2026-08-14-self-rename-post-and-announcements-design.md` — its **fact ledger** and **voice rules** bind every task below; reviewers receive both.

## Global Constraints

- Every factual claim in any deliverable must trace to the spec's fact ledger. No invented numbers, quotes, or events.
- Voice rules (spec §Voice) bind all prose: short declaratives; max one dry aside per piece, placed like a footnote; conversational openers; no corporate jargon; no emoji walls; opinions as self-answered rhetorical questions.
- Beta framing verbatim where versioned: **"dirtywork 0.2 (beta)"**; honesty line: works-on-my-setup, LM Studio + qwen3-coder / devstral verified.
- Links: `https://dirtywork.run`, `https://github.com/JimboSchneider/dirtywork`, `pipx install dirtywork`.
- Tagline verbatim: *Frontier models do the thinking. Local models do the dirty work.*
- Existing URLs must not break: `building-localagent.html` and the CNAME file stay untouched.
- Commits: conventional messages, no attribution footers. PR is **held for Jimbo — never self-merge**. Nothing posts anywhere except by Jimbo's paste/click (LinkedIn page: joint session, Jimbo submits).
- Reviewer subagents: Sonnet, one per piece, prompt carries spec path + the piece + a fact-and-voice checklist; findings fixed before the next task starts.

---

### Task 1: Blog post — markdown edition

**Files:** Create `docs/2026-08-14-the-tool-renamed-itself.md`

- [ ] Write the post (~1,200–1,800 words) following the spec's six beats in order. Title: `The Tool Renamed Itself`. Frontmatter-free plain markdown like the existing post. The exit-127 moment is the centerpiece; the honest-review section names the worker's two invented-HTML incidents and the plan's own `__version__` miss without softening.
- [ ] Self-check: every number/claim present in the spec fact ledger; voice rules pass; read the last paragraph of each section aloud for a landed stop.
- [ ] Dispatch reviewer subagent (facts + voice). Fix findings.
- [ ] Commit: `docs: add "The Tool Renamed Itself" post (markdown)`

### Task 2: Blog post — designed HTML edition

**Files:** Create `docs/the-tool-renamed-itself.html`; Read (reference only) `docs/building-localagent.html`

- [ ] Read `building-localagent.html` in full; extract its head block, CSS custom properties, section markup patterns (`sec-mark` numbered headers, `stats` grids, code styling, footnote pattern).
- [ ] Convert Task 1's markdown into that design system. A `stats` grid is allowed ONLY for genuine numeric metrics (e.g. 24 turns / 47 turns / 133 tests / $0 API fees). Add prev/next post links between the two posts.
- [ ] Head block: `<title>`, meta description (≤160 chars), `link rel=canonical` → `https://dirtywork.run/the-tool-renamed-itself.html`, OpenGraph + Twitter-card tags (og:title, og:description, og:type=article, og:url, og:image → `/og-card.png` from Task 4).
- [ ] Validate: `python3 -c "...count('<')==count('>')..."` bracket balance + no unclosed tags by eyeball of the diff; all hrefs resolve (relative files exist; absolute URLs from Global Constraints only).
- [ ] Render check via chrome-devtools MCP: open `file://` path, screenshot at default desktop and 375×812; text legible, no horizontal scroll.
- [ ] Commit: `docs: designed HTML edition of the self-rename post`

### Task 3: Landing page

**Files:** Replace `docs/index.html` entirely

- [ ] Invoke `frontend-design:frontend-design` skill BEFORE writing — the landing page must read as designed, not templated, while reusing the blog's design tokens so the site is one system.
- [ ] Build the five sections from spec §1a: hero (name, tagline, `pipx install dirtywork` in a copy-friendly code block), the flip in ≤3 sentences, terminal-styled 3-beat "how a run works" (delegate → isolated `dw-` worktree + JSONL transcript → orchestrator audits, only reviewed work merges), honest 0.2-beta strip, footer (GitHub / PyPI / Writing list with both posts newest-first / Dirt Simple Solutions credit).
- [ ] Head block: title `dirtywork`, meta description, canonical `https://dirtywork.run/`, OG/Twitter tags with `/og-card.png`, JSON-LD `SoftwareApplication` (name, url, description, operatingSystem: macOS/Linux, applicationCategory: DeveloperApplication).
- [ ] Five-second test on the desktop screenshot: name, what it is, install command all visible without scroll. Screenshot 375×812 too — no horizontal scroll.
- [ ] Dispatch reviewer subagent (facts + voice + five-second-test judgment on the screenshot).
- [ ] Commit: `feat: landing page for dirtywork.run`

### Task 4: SEO / link-preview hygiene

**Files:** Create `docs/robots.txt`, `docs/sitemap.xml`, `docs/og-card.png` (best effort)

- [ ] `robots.txt`: allow all, `Sitemap: https://dirtywork.run/sitemap.xml`.
- [ ] `sitemap.xml`: the three pages (/, both posts), lastmod 2026-08-14.
- [ ] OG card (1200×630 PNG): dark background matching the site, wordmark `dirtywork`, tagline, `dirtywork.run`. Generate via available local tooling (`sips`/ImageMagick/rsvg from an authored SVG; else render the SVG in the chrome-devtools browser and screenshot at exactly 1200×630). If no path yields a clean PNG, ship without the image, strip og:image tags from Tasks 2–3, and note it in the PR body.
- [ ] Verify Tasks 2–3 og:image paths match reality.
- [ ] Commit: `feat: robots, sitemap, and OG card`

### Task 5: r/LocalLLaMA post + Show HN blurb

**Files (scratchpad, not committed):** `announce-reddit.md`, `announce-hn.md` in the session scratchpad

- [ ] Reddit post: title + body. Flip-first, conversational, honest beta, one dry aside max. Links: dirtywork.run, GitHub, the new blog post. Mention verified models + the it-renamed-itself hook. No marketing cadence — write like the LinkedIn corpus.
- [ ] Show HN: title `Show HN: Dirtywork – frontier models think, local models do the dirty work` (adjust only for HN's 80-char limit) + two paragraphs: what it is/how it works; the honest story (built in a day, renamed itself, 0.2 beta, feedback wanted). No aside jokes — HN register is drier still.
- [ ] Dispatch one reviewer subagent covering both (facts + voice + audience fit).
- [ ] Deliver both texts verbatim in chat for Jimbo.

### Task 6: DSS LinkedIn kit

**Files (scratchpad, not committed):** `linkedin-kit.md`

- [ ] Page fields: name `Dirt Simple Solutions`, tagline (≤120 chars, from the DSS site's "Full-stack software, dirt simple." identity), website `https://dirtsimplesolutions.com`, industry Software Development, size 1 employee, type Privately Held, About (≤2,000 chars: what DSS is, invoicr + dirtywork as shipped proof, the practical-technology register — business-flavored but still Jim).
- [ ] First post: dirtywork announcement for the page — business framing of the flip, link dirtywork.run + blog post, honest beta note.
- [ ] Dispatch reviewer subagent (voice + facts + LinkedIn field limits).
- [ ] Deliver verbatim in chat for Jimbo.

### Task 7: Held PR — then STOP

- [ ] Full-link sweep across the three HTML files (every internal href targets an existing file in `docs/`).
- [ ] Push `post/the-tool-renamed-itself`; open PR titled `feat: self-rename post, landing page, and SEO hygiene` with a body summarizing Tasks 1–4 and screenshots attached (desktop hero + mobile). PRs on this repo: conventional title, no attribution.
- [ ] **STOP.** Jimbo reviews the PR and the Task 5–6 texts. No merge, no post, no LinkedIn session until his word.

### Task 8 (post-approval): Joint LinkedIn page session

- [ ] Preconditions: Jimbo merged the PR (links live) and approved the kit.
- [ ] In the shared Chrome (Jimbo logged in): navigate to `https://www.linkedin.com/company/setup/new/`, fill fields from the approved kit, screenshot each filled state for Jimbo.
- [ ] **Jimbo clicks Create page** and completes any verification. Claude never submits.
- [ ] Offer the first-post text for Jimbo to paste; he publishes. Log out reminder for the automation profile afterward.
