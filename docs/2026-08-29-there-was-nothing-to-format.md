# There Was Nothing to Format

*dirtywork 0.13.0 and 0.13.1 — `init --agent`, a provider-neutral skill, a Node 22 worker image, issue #87. Written and published 2026-08-29.*

---

Thursday I filed #87. The 0.12 release had given Claude Code a skill —
`dirtywork init` writes a file Claude picks up on its own and drives the
loop from — and everyone else got `dirtywork init --stdout` and a sentence
telling them to put the text somewhere by hand. The issue was the obvious
next step: a `--format` flag, one output shape per tool. Codex, Gemini CLI,
Cursor. The spec from the day before had promised the flag by name.

The issue also carried a worry. Codex's native instruction file is
`AGENTS.md`, and `AGENTS.md` is exactly the file dirtywork hands to the
*worker* at the start of every run. Put orchestrator instructions there and
the local model gets told how to review its own work. So the Codex format
would have to be user-level, and each tool's location would have to be
checked against that tool's current documentation, not remembered.

Saturday morning Claude checked. The issue was wrong in a useful way.

## One file, five directories

Codex CLI, Gemini CLI, Cursor and GitHub Copilot all read the same thing
now: the [Agent Skills](https://agentskills.io) standard — a `SKILL.md` with
a `name` and a `description` in its frontmatter — and all four look in one
shared pair of directories, `~/.agents/skills/` and the project's
`.agents/skills/`. Claude Code's own docs say its skills follow the same
standard. The 0.12 template already was one: `name: dirtywork`, a
description, nothing else required anywhere.

There was nothing to format. There were directories.

So the feature that was going to be a wrapper per tool became a table with
five rows, replacing two literal `".claude"` strings in the function that
computes where `init` writes. The flag is `--agent`, not `--format`, because
a flag called format would have named a thing that doesn't exist. `dirtywork
init --agent codex --repo .` writes the same bytes Claude gets, into the
directory the other four read; `--agent gemini` on the same machine says
`up to date:`, because it's the same two files. Claude Code stays the
default and its paths don't move.

What that means for a 0.12 install is worth saying exactly, because the
issue's acceptance line said "byte-identical" and that turned out to be
false. The destinations and the frontmatter are identical. The body isn't,
for a reason in the next section. So an unmodified 0.12.1 copy prints
`updated: … (v0.12.1 -> v0.13.0)` on the next `init`, which is what the
stamp was built for.

## The skill assumed my machine

While #87 was open, someone on Windows running Ollama read the 0.12 skill
and concluded, reasonably, that Claude would always assume LM Studio on
`localhost:1234` and never pass `--provider` or `--base-url`. They were
right about the skill and wrong about the tool, and that gap is a bug. The
skill mentioned other providers once — "see the contract" — its run
template had no provider line, and its first-run check curled my port.

That's fixed in the same file, so the stamp bumps once instead of twice.
The first-run check now covers LM Studio, Ollama and any other
OpenAI-compatible endpoint, and it says to ask rather than guess when more
than one is running. The run template carries
`[--provider openai|anthropic|ollama] [--base-url <url>]`. And there's a
line I didn't know we needed until Claude checked the code while writing
the spec: `resume` inherits `--provider` from the run it continues, but it
never reads `--base-url` back. Omit it on a resume and you're back on
LM Studio's port. That's precisely the trap the report described, one step
later. The skill says so now.

The spec went through my review before the plan — six items, folded after
Claude verified each against the checkout. One of them was a count of
`:0.12` literals that was short by half, because the grep pattern that
counted them was `dirtywork-worker:0.12` and the contract also says
`my-worker:0.12` and bare `FROM :0.12`. The spec's acceptance check uses the
wide pattern now. Small thing. It's the kind of small thing a plan is for.

## The build

Same rule as the last few releases: dirtywork builds dirtywork. The
released 0.12.1, from PyPI, drove qwen3-coder-next on LM Studio inside the
Docker sandbox against this checkout. Claude wrote the spec, the plan and
the briefs, reviewed every run, and wrote the docs. I reviewed the spec,
said go, reviewed the PR, and merged.

One thing was new this time, learned on the Windows work that morning:
before a brief went to the worker, it was applied literally on the host in
a throwaway worktree and its tests run. That caught six defects in the
briefs — a review grep that could never pass, an unstated import position,
two miscounts, a splice that would have produced 130-column lines, a
one-off in the expected test delta — before a single run started.

| Task | Status | Turns | Wall | Resumes | What came back |
|---|---|---|---|---|---|
| W1 — the table, `--agent`, tests 17 and 24–29 | completed | 32 | 262 s | 1 | three files as briefed; a test name and a docstring comma off by a word |
| W2 — the skill body, the endpoint hint, tests 14 and 30 | completed | 19 | 163 s | 1 | `SKILL.md` byte-identical to the spec's appendix; one 170-column line |
| W3 — the contract entry, the CI wheel-smoke step | **max_turns** | 60 | 231 s | 2 | see below |
| W4 — 0.13.0, the `:0.13` image cycle | completed | 43 | 231 s | 0 | eight files, as briefed |

Four briefs, eight runs counting the resumes, 224 turns, twenty-one minutes
of worker wall clock, about 4.5 million prompt tokens and 35,000 completion
tokens, $0. Every run that reached `--verify` passed it on the first round.
The suite went from 1,662 tests to 1,681. Every deviation in that table
went back through `dirtywork resume` with a feedback file naming the line;
Claude wrote no production code. The four resumes for a test name, a comma,
a long line and a YAML step took between one and two minutes each.

## The loop the meter didn't see

W3 is the row to read. Its brief was two prose edits to the machine
contract and two lines added to a GitHub Actions workflow — a `run: |` block
scalar. The worker did the prose, then spent fifty-one `bash` calls on the
workflow file: `sed -n | cat -A` to look at it, `git checkout` to reset it,
edit, look again. Sixty turns at 3.8 seconds each. Fast, and going nowhere.

The harness has a nudge for a worker that stops making progress. It never
fired, because the worker never stopped editing. It edited and reverted the
same file, which counts as change every time. That's a gap I hadn't seen
until this transcript, and it's on the record now: a nudge that notices the
same file edited *N* times while the test result doesn't move.

The first resume fixed the contract text and then, told in so many words to
leave the workflow file alone, ran `git checkout .github/workflows/ci.yml`
and threw away the correct step from the first run. The second resume put
it back, byte for byte, with the two lines quoted in the feedback and a
sentence forbidding `git checkout`, `git restore` and `git stash`. It
converged. Two resumes is the plan's limit; a third would have meant the
brief was wrong. The lesson that went into the plan file: a prose
replacement wants "replace lines *N*–*M* with these *K* lines," not "replace
from this anchor to that one."

The PR was open at 13:16, thirty-three minutes after I said go. My review
found three documentation items — the homepage still said 0.12; the setup
page's prerequisites were still LM Studio only; the troubleshooting section
only knew `lms` — and the macOS job failed on the docs-only fix that
followed. Not the fix: a test that counts entries in the runner's shared
temp directory before and after the fingerprint script and expects them
equal, which they are unless something else on the runner writes a file in
that second. It had passed twenty minutes earlier on identical code. It
measured the runner, not the script. That's #101, a test-only brief for
another day.

## It worked on Codex the same afternoon

Before that PR had even merged, I'd tried it. I installed the skill from
the branch with `--agent codex`, opened Codex CLI in a different project —
a Vite app — and asked it to delegate a small task: edge-case tests for a
formatting module. The run's slug is stamped 13:45. The worker added only
the tests that were asked for; the typecheck and both test suites passed on
the host, thirty-one of thirty-one focused and 112 of 112 overall; main
untouched; verdict recorded as accepted.

That's the receipt the spec had marked "best-effort, manual": a second
vendor's agent driving the whole loop off a file `init` wrote. One report.
I haven't run Gemini, Cursor or Copilot; the docs say so.

The run found one thing it couldn't do. The project is a Vite app, and the
worker image's Node was Debian bookworm's 18 — end of life in April 2025,
below what Vite 8 and current Vitest require. The worker would have had to
bend the project's dependencies around the sandbox. That's the wrong
direction, so:

## The image said it had no entrypoint

The `:0.13` image is built from `node:22-bookworm-slim` now: Debian 12 plus
Node 22 LTS, npm and corepack from the official image, Debian's
`nodejs`/`npm` gone. The official image ships a `node` user at uid 1000,
which is the uid `worker` has always had, so the Dockerfile removes it
first. Everything else — .NET 8 and 10, the environment, no `WORKDIR` — is
untouched. The published image is about seventeen megabytes larger than `:0.12` — Node 22's own base costs a little more than Debian's did.

My review of that PR found the better thing. The Dockerfile's comment and
the image guide both said the image has no `ENTRYPOINT` and no `CMD` —
dirtywork passes its own `--entrypoint` on every `create`, `run` and `exec`,
so the image's own launch configuration is never trusted. True about
dirtywork. Not true about the image: `node:22-bookworm-slim` sets
`ENTRYPOINT ["docker-entrypoint.sh"]` and `CMD ["node"]`, so a bare
`docker run` of our image started Node. And when Claude checked the `:0.12`
image for comparison, it had been carrying Debian's `CMD ["bash"]` the whole
time. The claim had never been literally true. It is now: `ENTRYPOINT []`,
`CMD []`, a bare run refuses with "no command specified," and CI's sandbox
smoke asserts both `docker inspect` → `null null` and `node --version` →
`v22.` on every build, so the claim can't drift again without failing.

That CI assertion is where the day's second brief defect lived, and it was
Claude's. The brief assumed the sandbox's `bash()` returns the command's
output. It returns the output with an `exit code: 0` line in front of it, so
the assertion saw `'exit code: 0\nv22.23.2'` and failed on a correct image.
The brief had been pre-checked, but as text: the file parsed, the unit tests
passed. Nobody had run the smoke script against a built image. Claude did
that next — built the image locally, reproduced the failure, applied the
fix, watched it pass — and then sent the three-line fix through a fresh
dirtywork run, because the first run's worktree was already cleaned and the
rule doesn't have a clause for three lines. Every brief after that was
checked by running it, not reading it.

## Forty minutes

0.13.0 was cut at 14:53. PyPI had it within a minute. The self-check on my
machine printed every line the spec predicted: `wrote:` for the home copy,
`updated: .claude/skills/dirtywork/SKILL.md (v0.12.1 -> v0.13.0)` for the
project copy, `wrote:` for `--agent codex`, `up to date:` for `--agent
gemini`. The published `:0.13` inspects as `null null`, runs Node 22, and
its digest is the one thing 0.13.1 changes.

Then 0.13.1 was built by 0.13.0. The released wheel, sixteen minutes old,
drove the worker inside a `pytest` image derived from the just-published
`:0.13`, and pinned that image's digest in one run and one resume for a
dropped comment line. The pre-check for that brief was the smoke script
resolving the pulled image against the pin, so a wrong digest would have
refused on the host. It didn't. 0.13.1 was cut at 15:34.

Also in the release, from the morning: native Windows no longer dies on
`os.killpg`, `os.O_NOFOLLOW` or `os.kill(pid, 0)` (#96 tier 1). It stays
unsupported — there's a categorized list of 158 test failures behind those
three — and WSL2 stays the way in.

## Try it, and what's next

    pipx install dirtywork
    cd ~/repos/yourproject
    dirtywork init --repo .                  # Claude Code
    dirtywork init --agent codex --repo .    # Codex, Gemini CLI, Cursor, Copilot

Then open your agent there and ask it to delegate something small. If it's
Gemini, it will ask you to confirm the skill the first time. If it's a Vite
project, the worker can run your tests now.

Next: the edit-and-revert nudge from the W3 transcript, the temp-directory
test, and a real behavior change delegated from Codex rather than a test
file — the loop with review in it, on the new image.

---

*Claude (Fable 5) verified the four tools' documentation, wrote the spec and
the plan and their worker briefs, ran and reviewed every run, wrote the
docs, and drafted this post from the session record. The local model
(qwen3-coder-next via LM Studio) did the typing inside the released
dirtywork 0.12.1, and then 0.13.0. Jim filed the issue, chose `--agent`,
reviewed the spec and both PRs, ran the Codex experiment, and cut both
releases. Same process as the earlier posts.*
