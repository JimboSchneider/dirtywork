# Set up Claude Code as your dirtywork orchestrator

Here is the idea in one breath: Claude Code does the thinking — picks the
task, writes the brief, reviews the result — and a local model on your own
machine does the typing, inside a sandbox, for free. You review the diff and
merge what survives. This page gets you there. It is the setup I use to build
dirtywork itself.

You need dirtywork 0.12.0 or later. Not counting the model download, the
whole thing takes a few minutes.

## You need three things

**A local model.** Install [LM Studio](https://lmstudio.ai), download a model
that can call tools, load it, and start LM Studio's local server (it listens
on port 1234 by default). I use `qwen/qwen3-coder-next`; `mistralai/devstral-small-2-2512`
is the other one I test with. Load one model with as much context as your
machine can hold rather than two small ones — the difference is measured in
[the operating guide](operating.md#sizing-the-context-window).

**Docker.** Docker Desktop or `dockerd`, running. That is where the worker
lives: no network (unless you pass `--allow-network` on purpose), no access
to your home directory, just the repo. The
worker image downloads itself the first time you run.

**dirtywork.**

    pipx install dirtywork
    dirtywork --version

If the second line says `command not found`, run `pipx ensurepath` and open a
new terminal.

Quick check that all three are awake:

    curl -s http://localhost:1234/v1/models
    docker info
    dirtywork --version

## Teach Claude the loop

Go to a project and run:

    cd ~/repos/yourproject
    dirtywork init --repo .

You'll see two lines:

    wrote: /Users/you/.claude/skills/dirtywork/SKILL.md
    wrote: /Users/you/repos/yourproject/.claude/skills/dirtywork/SKILL.md

That file is a Claude Code *skill* — a short set of instructions Claude picks
up on its own when a task looks like "hand this to dirtywork". The home copy
follows you into every project; the project copy travels with the repo.
Commit the project copy, and everyone who clones the repo gets a Claude that
already knows how to drive.

The skill tells Claude to check that LM Studio and Docker are up, read
`dirtywork contract` (the full flag reference for the version you installed)
instead of guessing, write a brief that names files and tests, run, read the
result, review the diff, send the worker back with feedback if it's close,
and record a verdict. It's project-neutral — no paths, no model names, nothing
about my machine.

One thing to know about where instructions go: your project's `CLAUDE.md`
(or `AGENTS.md`) is handed to the *worker* at the start of every run so it
inherits your conventions. That's the place for "we use pytest, four-space
indents, no new dependencies" — not for orchestrator instructions. Leave the
driving to the skill.

## Try it

Open Claude Code in the repo and ask for something small:

> Use dirtywork to add a unit test for `parse_duration` in `tests/test_config.py`.

Here is what should happen. Claude checks the prerequisites, writes a brief
that names the file and the test, and calls `dirtywork run`. You'll see a
transcript path on the terminal; `tail -f` it if you want to watch the local
model work. A few minutes later Claude reads the result, looks at the diff,
runs your tests, and either sends the worker back with a note ("the test
doesn't cover the negative case") or tells you it's done and there's a
`dirtywork/<slug>` branch to look at.

Then you do the part that doesn't change: read the diff like it came from a
contributor, and merge only what you'd merge from a person. Every run leaves a
transcript and a verdict; `dirtywork runs list` shows them all.

## How I use it

None of this is required, but it's what makes the setup stick for me.

- dirtywork's own [`CLAUDE.md`](https://github.com/JimboSchneider/dirtywork/blob/main/CLAUDE.md)
  says every code change is made by running the released dirtywork against
  the checkout, with Claude as planner and reviewer, never implementer. The
  worker sees that rule, which is fine — it's project policy. The mechanics
  stay in the skill.
- I spend the frontier model's tokens on the spec, the plan and the review.
  The briefs go into the plan word for word before a single run starts, so a
  cheaper session can execute them later.
- I keep receipts: turns, wall time, tokens per second, whether the tests
  passed, one row per run. That's where the numbers in the
  [essays](https://dirtywork.run) come from.
- Independent briefs run at the same time. LM Studio serves several requests
  per loaded model. Two tasks in one brief is the mistake I keep making anyway.

## Good to know

- **Upgrades.** After `pipx upgrade dirtywork`, run `dirtywork init` again.
  The upgrade replaces the wheel, not the skill file it wrote, so until you
  do, Claude is driving the new release with the old release's instructions.
  `init` refreshes a copy you haven't touched, leaves a current one alone, and
  won't overwrite one you edited unless you pass `--force`. It tells you
  which happened, one line per file.
- **Other providers.** The skill assumes LM Studio on `localhost:1234`: its
  run template passes only `--model`, and its first-run check curls that
  port. dirtywork itself takes more — `--provider ollama` for Ollama,
  `--base-url <url>` for any other OpenAI-compatible server, `--provider
  anthropic` (needs `ANTHROPIC_API_KEY`) — but the skill doesn't say so, so
  Claude won't pass those flags unless you make it: say it in the request
  ("delegate this with `--provider ollama`"), or add the flag to the run
  template in your `SKILL.md` (`init` preserves an edited copy). A
  provider-neutral skill is part of
  [#87](https://github.com/JimboSchneider/dirtywork/issues/87).
- **Other agents.** `dirtywork init --stdout` prints the skill without
  writing anything. Together with `dirtywork contract`, anything that can run
  a shell command has what Claude has.
- **The worker can't install things.** The sandbox is offline by default
  (`--allow-network` is the deliberate exception). If
  your tests need a tool the image lacks — pytest, say — build a derived image
  once (a five-line Dockerfile in the
  [worker image guide](https://github.com/JimboSchneider/dirtywork/blob/main/docker/README.md#derived-images-extra-packages))
  and pass it with `--image`.
- **The project skill is a file in the repo**, so a worker can read it like
  any other file. Its first paragraph tells a worker to ignore it. That's a
  polite request, not a wall; hiding `.claude/` from the worker for real is
  [#84](https://github.com/JimboSchneider/dirtywork/issues/84).
- **Claude doesn't seem to know about dirtywork.** Check that
  `~/.claude/skills/dirtywork/SKILL.md` exists, then start a fresh Claude Code
  session and ask it to delegate something.
- Everything else: the [operating guide](operating.md).

## Before 0.12.0

Kept for the record. Until 0.12.0 there was no `dirtywork init` and no
skill in the wheel. To get Claude Code driving dirtywork you had to write
the instructions yourself — condense the operating guide and the machine
contract into a user-level file (`~/.claude/CLAUDE.md`, or a hand-made
`~/.claude/skills/dirtywork/SKILL.md`), keep it in step with each release by
hand, and remember not to put it in the project's `CLAUDE.md`, where the
worker would read it on every run. That is how I ran it for the first
eleven minor versions, with the details living in one machine's memory. None of
it is needed any more; `dirtywork init` is the product's copy of that file.
