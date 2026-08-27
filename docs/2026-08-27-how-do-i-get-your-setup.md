# How Do I Get Your Setup?

*dirtywork 0.12.0 — the operator contract in the wheel, issue #82. Written and published 2026-08-27.*

---

This morning I asked Claude a question I should have been able to answer
already: how does someone else get the setup I have? Claude Code doing the
thinking, a local model doing the typing, the harness in between. I've been
running it that way for weeks. I build dirtywork with it.

The honest answer had four layers, and three of them were fine. LM Studio
and Docker: in the README. dirtywork itself: `pipx install`. The loop —
brief, run, review, resume, verdict: in the operating guide and the machine
contract. The fourth layer was the instructions to the orchestrator. How to
write a brief a small model can execute. That stdout is one JSON object and
stderr is everything else. What exit 1 means (kept for review) versus exit 2
(nothing was created). That the worker can't install anything in docker mode.
That a resume mints a new slug. Where to put your verdict.

Those lived in two places: my head, and the auto-memory of one Claude Code
install on one machine. Nothing in the wheel. Someone who installed dirtywork
and opened Claude Code got a tool Claude had never heard of.

That's not a 1.0.

## Where the instructions couldn't go

The obvious fix was the wrong one, and it took the spec's first review to see
why. Every Claude Code user has a `CLAUDE.md`. Append the orchestrator
instructions there — done.

Except dirtywork reads the target repo's `CLAUDE.md` and `AGENTS.md` at the
base commit and injects them into the *worker's* system prompt. That's a
feature: your project conventions reach the local model without you repeating
them in every brief. It also means anything you write there is handed to the
worker on every run. Orchestrator instructions in `CLAUDE.md` would tell the
local model how to review its own work and record a verdict on
itself. Not a security hole, exactly. Just the two roles, briefed as one.

So the instructions had to live somewhere the harness doesn't read. Claude
Code has a place for that: a *skill* — a `SKILL.md` under `~/.claude/skills/`
or the project's `.claude/skills/`, picked up on its own when a task looks
like the description. The spec was verified against the Claude Code docs the
day it was written: those two paths, one required frontmatter field, and no
mechanism for an installed CLI to register instructions without writing a
file. A subcommand that writes the file is the sanctioned path.

## What 0.12.0 ships

Three additions, one moved file.

**`dirtywork contract`** prints the machine contract for the version you
installed — every flag, the stdout schema, exit codes, transcript events. The
file moved out of `docs/` and into the package so that's possible; the old
path is a stub pointing at the new one. The skill's first instruction is to
run this before the first `run` of a session and not guess flags.

**`dirtywork init`** writes the skill. `dirtywork init --repo .` puts one copy
in your home and one in the project; commit the project copy and every clone
gets a Claude that already knows how to drive. The file carries a stamp —
version and a hash of its own contents — so `init` after an upgrade refreshes
a copy you haven't touched, leaves a current one alone, and refuses to
overwrite one you edited unless you say `--force`. It says which, one line per
file. `--stdout` prints the rendered text for any other agent.

**`dirtywork --version`** exists now. It didn't. The version was only ever
emitted inside the run JSON, and the skill's first prerequisite check needed
it.

The skill itself is 127 lines and project-neutral: no paths, no image names,
no model beyond `--model <model>`. It says: check the prerequisites; read the
contract; one run, one task; name the files and the tests; stdout is JSON,
parse it; review the diff before the transcript; two resumes without
convergence means the brief was wrong; record a verdict; never merge
unreviewed. Its first paragraph is addressed to the worker, because a
committed project copy is a file in the tree like any other and a worker
exploring the repo can read it. The paragraph says: this is for the
orchestrator that launched you; ignore it. That's a polite request, not a
wall. Hiding `.claude/` from the worker for real is its own design, filed as
#84.

There's a guide on the site now — [Set Up Claude Code as Your dirtywork
Orchestrator](orchestrator-setup.html) — from LM Studio to the first
delegated task. It ends with the manual process, kept for the record.

## The build

Same rule as the last few releases: dirtywork builds dirtywork. The released
0.11.1, from PyPI, drove qwen3-coder-next on LM Studio inside the Docker
sandbox against this checkout. Claude wrote the spec, the plan and the briefs,
reviewed every run, and wrote the docs. I approved the merge and cut the
release. Every run is a row in the ledger.

The plan had eight tasks. Four were worker tasks; four were Claude's — the
move, the docs, acceptance. The worker's four:

| Task | Turns | Wall | Prompt / completion tokens | Verify | Result |
|---|---|---|---|---|---|
| W1 — `contract`, `--version`, the render, 7 tests | 25 | 211 s | 280,950 / 3,896 | pass, round 1 | exact to brief |
| W2a — `init`: destinations, decision table, atomic write, 12 tests | 16 | 172 s | 181,570 / 4,175 | pass, round 1 | verbatim |
| W2b — 5 edge tests + the worker-prompt injection test | 23 | 225 s | 296,456 / 2,774 | pass, round 1 | verbatim, tests only |
| W3 — 0.12.0, the `:0.12` image cycle, a `wheel-smoke` CI job | 12 | 168 s | 171,489 / 3,106 | pass, round 1 | five files, as briefed |
| W3-fix — three YAML lines | 4 | 63 s | 20,563 / 375 | pass, round 1 | verbatim |

Five runs, five first-try completions, zero resumes, zero second verify
rounds. Eighty turns, fourteen minutes of worker wall clock, about 951,000
prompt tokens and 14,000 completion tokens, $0. The four runs of the second
session went from first launch to a pull request with its ledger row in
twenty-nine minutes, including review. Two-thirds of every run's wall clock
is the test suite running inside the container — fifty-two seconds, every
time — which is the price of `--verify` and worth every second of it. The
suite went from 1,579 tests to 1,603.

"Verbatim" in that table is checked, not felt. After each run Claude ran the
plan's shell checks and then a stricter one: every line the worker added has
to appear in the brief. It did, all five times. On issue #61 the local model
inverted a rule and wrote the test to match; that's why the check exists.

## The defect was mine

The fifth row is the one I want on the record.

W3's brief included the new CI job, written out in full so the worker could
copy it. One of its steps was:

    - run: smoke/bin/dirtywork init --stdout > skill.md && grep -q '^name: dirtywork' skill.md

That is not YAML. A plain scalar can't contain a colon followed by a space —
that's the mapping indicator — and `'^name: dirtywork'` has one. Single
quotes don't help; they're just characters inside a plain scalar. The worker
copied my line exactly, as briefed, and the plan's own review step — parse the
workflow file with Ruby's Psych before integrating — refused it: *mapping
values are not allowed in this context, line 120, column 76*. The main
branch's copy parsed fine. Mine didn't.

The fix was three lines: `run: |` and the command on the next line. I could
have typed it in nine seconds. The rule in this repo's `CLAUDE.md` says code
changes go through dirtywork, and it doesn't have a clause for nine seconds.
So a follow-up brief went out — the three replacements, one `apply_edits`
call, touch nothing else — and came back in four turns and sixty-three
seconds. The receipt says who introduced the bug, who caught it and who fixed
it, and none of those is the model.

Review found one more thing, after the PR was open: the machine contract's
three relative links to `operating.md` and `transcript-schema.md` worked when
it lived in `docs/` and broke when it moved into the package. That was
Claude's task, not the worker's, and Claude fixed it by hand — it's
documentation, and the rule is about code. Absolute links now; the only kind
that works in `dirtywork contract` output anyway.

## One line from a transcript

W2a's last tool call, inside the sandbox, after it had smoke-tested its own
`init` implementation by running it against the container's home directory:

    rm -rf ~/.claude

Harmless. The container's `~` lives on the run's own volume, which `runs clean`
deletes. But read it again with the sandbox off — `--sandbox none`, on your host —
and that's your Claude Code configuration gone, because a local model was
tidying up after a unit test. The worker did nothing wrong. It cleaned up its
mess, which is what the brief asked. That's the argument for the default
being a container, made by the model, in one line, on the record.

## Also in 0.12.0

**Marker-polluted tool names are recovered (#67).** Devstral sometimes puts
its `[TOOL_CALLS]` token inside the tool *name* — prose, the marker, then
`bash`. Two releases counted each as an unknown tool and aborted after three,
after the file was already written. Now the harness finds the tool after the
last marker, dispatches the call, and nudges the model once per turn. On the
six Devstral rows that used to abort, none abort. That one was built the
same way, last night, and merged before this build started.

**The `:0.12` worker image.** Same Dockerfile as `:0.11`, published by this
release, unpinned on first publish; 0.12.1 pins the digest, as 0.11.1
did.

**The repo's `CLAUDE.md` now says the dogfood rule out loud** (#81), which is
why the nine-second fix above went through a run.

## Try it, and what's next

    pipx install dirtywork
    cd ~/repos/yourproject
    dirtywork init --repo .

Then open Claude Code there and ask it to delegate something small. It will
run `dirtywork contract` before it runs anything else. I checked: a fresh
Claude Code session in a throwaway repo, asked to delegate a task, named the
skill, said where it found it, and gave `dirtywork contract` as its first
command, quoting "do not guess flags" back at me.

Next is the same thing for orchestrators that aren't Claude — Codex, Gemini
CLI, Cursor. The skill body is already agent-neutral; what's Claude-shaped is
the frontmatter and the install path. The catch is the one from the top of
this post: Codex's native instruction file is `AGENTS.md`, which is exactly
the file dirtywork hands to the worker. So each target needs a location the
harness doesn't read, verified against that tool's current docs, not from
memory. That's #87, and it's 0.13.0.

---

*Claude (Fable 5) wrote the plan and its worker briefs, ran and reviewed every
run, wrote the guide and the docs, and drafted this post from the session
record. The local model (qwen3-coder-next via LM Studio) did the typing inside
the released dirtywork 0.11.1. Jim asked the question, chose the approach,
reviewed the spec, asked for the guide to be rewritten for humans, approved
the merge and cut the release. Same process as the earlier posts.*
