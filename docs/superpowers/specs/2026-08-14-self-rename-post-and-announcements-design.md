# "The Tool Renamed Itself" — blog post & beta announcements design

**Date:** 2026-08-14
**Status:** Approved in conversation (Jimbo, 2026-08-14 evening)

## Deliverables

1. **Blog post** — `docs/2026-08-14-the-tool-renamed-itself.md` (source of truth) +
   `docs/the-tool-renamed-itself.html` (designed edition, same design language as
   `building-localagent.html`). Ships via a **held PR** on this repo.
1a. **Landing page** — `docs/index.html` becomes a real front door for dirtywork.run
   (replacing the redirect): hero (name, tagline, `pipx install dirtywork`), the flip
   thesis in ~3 sentences, a terminal-styled "how a run works" 3-beat (delegate →
   isolated `dw-` worktree + JSONL transcript → orchestrator audits transcript + diff,
   only reviewed work merges), an honest 0.2-beta strip (works-on-my-setup: LM Studio +
   qwen3-coder / devstral verified), footer with GitHub / PyPI / Writing (both posts,
   newest first) / Dirt Simple Solutions credit. One static self-contained HTML file,
   same design tokens as the blog pages, responsive, no JS framework, no analytics.
   Blog posts keep their existing URLs. Same held PR.
   Basic SEO/link-preview hygiene rides along: meta descriptions + canonical URLs on all
   three pages, OpenGraph/Twitter-card tags with a 1200×630 OG card image (best effort),
   robots.txt, sitemap.xml, and JSON-LD SoftwareApplication on the landing page. A full
   multi-page docs/SEO site is explicitly deferred until post-announcement traction.
2. **r/LocalLLaMA post** — text file, delivered for Jimbo to paste. Not committed.
3. **Show HN blurb** — title + two-paragraph text, delivered for Jimbo to paste. Not committed.
4. **DSS LinkedIn kit** — company-page fields (name, tagline, ~2000-char About) plus the
   page's first post announcing dirtywork. Delivered for Jimbo to paste when he creates
   the page. Not committed.

## Voice (from Jimbo's LinkedIn corpus, 2026-08-14)

- Short declaratives that land and stop; punchy imperative closers.
- One dry aside per piece, max, placed like a footnote — never a performance.
- Conversational openers; zero corporate jargon; no emoji walls (a lone `:)` or `lol`
  is in-voice for casual contexts, not for the HN blurb).
- Opinions arrive as a rhetorical question the author immediately answers.
- The blog post keeps the existing postmortem's first-person builder-narrative register,
  tightened by the rules above.

## Blog post — fixed story beats (fact ledger; do not invent beyond it)

1. Competitive survey (research fan-out, 2026-08-14): every neighbor ships a piece of the
   loop (Aider architect/editor, Qwen Code worktrees + self-review, prompt-forwarding MCP
   micro-servers); nobody with traction ships local-worker-in-worktree + frontier-model
   transcript-and-diff audit. The ecosystem's bet is *replacing* the frontier model
   (Ollama & LM Studio both shipped Anthropic-compatible endpoints Jan 2026); ours flips it.
2. The name hunt: "localagent" said nothing; `dirtywork` free on PyPI; `dirty.work`
   registered (confirmed via RDAP), `dirtywork.com` a $57,500 premium; `dirtywork.run`
   available at $6.99 for year one — bought for three years on the spot. The domain is an
   imperative: *dirtywork, run*.
3. The rename executed itself: spec → an eleven-task plan → subagent-driven execution
   where the tool was the implementer for its own package rename (Task 1) and docs
   rename (Task 3), each a qwen3-coder worker in an isolated worktree. Task 1 usage:
158,297 prompt / 1,140 completion tokens (from its run report); combined with Task 3's
~1.1M, the two rename runs total ≈1.25M local prompt tokens, $0. Task 1: 24 turns,
   executed tests-first per its prompt (test expectations updated, suite red with
   ModuleNotFoundError, package renamed, all 133 back to green); its task review
   approved with no code findings. The PR (#5) totaled six commits, two of which carried
   the tool's own work (the package rename and the docs sweep).
4. The exit-127 beat: launching Task 3 failed — `command not found` — because Task 1 had
   renamed `bin/localagent`, orphaning the launcher symlink. The rename's first casualty
   was the renamer. Relaunched via `bin/dirtywork`; the run returned in a
   `.worktrees/dw-…` worktree on a `dirtywork/…` branch with its transcript under
   `~/.dirtywork/runs/` — the rename validating itself in production before the PR opened.
   Task 3: 47 turns, ~1.1M prompt tokens, $0 in API fees.
5. Honest review section: the worker dropped one plan line item (README `la-`→`dw-`
   examples) — that line existed in the plan but was omitted from the worker's task
   prompt, an orchestrator error acknowledged in-session and twice invented decorative HTML nobody asked for; reviewers caught both.
   The final whole-branch review caught the one defect the *plan* missed
   (`__version__` still 0.1.0). Lesson stated plainly: audit local-worker diffs for
   additions beyond the ask, not just misses — and review catches the orchestrator's
   mistakes too.
6. Close on the flip thesis + tagline. Timeline (verified against session timestamps):
   survey → name → domain bought ≈ half an hour; "we need a new name" (18:41 CDT) to
   `dirtywork 0.2.0` installable from PyPI (20:07 CDT) ≈ an hour and a half, same
   evening. Also same evening: repo renamed with redirects, DNS live (4×A + 4×AAAA +
   www CNAME, IPs verified against GitHub's docs), TLS cert issued and HTTPS enforced.

## Announcements

- Framing: **"dirtywork 0.2 (beta)"** — copy-only beta label; APIs may shift; feedback
  wanted; works-on-my-setup honesty (LM Studio + qwen3-coder / devstral verified;
  Claude Code has been the orchestrator for every production run to date, including the
  rename itself — naming it as the verified orchestrator setup is ledger-accurate).
- Guardrail phrasing rule (from SECURITY.md): guardrails stop accidents, not
  adversaries; review is the real boundary. Never describe the worktree as a "sandbox"
  in public copy — "isolated git worktree" is the honest term.
- All three lead with the flip, link `https://dirtywork.run` and the GitHub repo;
  r/LocalLLaMA + blog post may link each other. Install: `pipx install dirtywork`.
- LinkedIn About copy is business-flavored (what DSS is, invoicr + dirtywork as proof of
  work) but keeps the voice; no growth-hack tropes.

## Out of scope

- Posting anything anywhere — every public word ships only via Jimbo's paste, his PR
  merge, or (LinkedIn page creation only) a joint browser session where Claude fills the
  setup form with the approved kit copy in the shared Chrome instance and **Jimbo
  personally clicks create/submit and handles any verification**. The page's first post
  is likewise pasted or submitted by Jimbo.
- Site redesign; the HTML edition reuses the existing post's design system verbatim.

## Success criteria

- Held PR with the two blog files + the landing page; HTML valid and responsive, no
  regression to post #1's page; landing page passes the five-second test (a stranger
  knows what dirtywork is and how to install it without scrolling).
- Three announcement texts delivered and voice-consistent; every factual claim traceable
  to the fact ledger above.
- Jimbo reads the blog post and recognizes his own register — the "would I have written
  this?" test.
