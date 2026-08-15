# The Tool Renamed Itself

*A follow-up to [Building localagent](2026-08-14-building-localagent.md), written the
same evening. The tool in that post no longer exists. It has a better name now — and it
did a good share of the renaming.*

---

## The survey

Before announcing localagent anywhere, I wanted to know the neighborhood. So the
orchestrator did what orchestrators do: fanned out three research agents, each sweeping a
different angle — established coding CLIs, worktree-isolation tools, and
orchestrator/worker patterns.

The findings compressed to four lines:

1. **Local-model support is table stakes.** Every surviving coding agent speaks
   Ollama or LM Studio. That alone differentiates nothing.
2. **Worktree isolation is commoditized.** By mid-2026 nearly every serious tool ships
   it; Claude Code has it as a first-party feature.
3. **Nobody with traction ships the combination** — a local model running a real tool
   loop in an isolated worktree, leaving a full JSONL transcript that a *stronger*
   frontier model audits before anything merges. Aider's architect/editor mode hands
   work down but never audits it afterward. Qwen Code reviews itself, same model
   grading its own homework. The dozen tiny MCP servers with the "Claude thinks, your
   local model grunts" tagline forward single prompts and get text back — no tool loop,
   no transcript, nothing to audit.
4. **The ecosystem's big bet is replacement.** Ollama and LM Studio both shipped
   Anthropic-compatible endpoints in January 2026 so that Claude Code can run entirely
   on local models. Swap the frontier model out; keep the workflow.

Our bet is the inverse of point four. Keep the frontier model — but only where judgment
lives, as planner and auditor. Hand everything else to the models already running for
free on the Mac in the corner.

Did "localagent" say any of that? It did not. It was three names for one tool — repo
`localagent`, command `localagent`, PyPI dist `dirtsimple-agent` (every obvious name had
tripped PyPI's similarity filter) — and none of the three said anything at all.

## The name

**dirtywork.** As in: frontier models do the thinking, local models do the dirty work.
The whole architecture in one phrase, with exactly the right amount of henchman energy.

The domain hunt was brief. `dirty.work` — registered, of course. `dirtywork.com` — a
premium listing at $57,500, which is a lot for a tool whose entire pitch is not paying
for things. `dirtywork.run` — available, $6.99 for the first year. Bought for three
years on the spot.

Best part: the domain is an imperative. *dirtywork, run.* It's also literally the
command.

## The rename renames itself

A rename touches everything — package, CLI, launcher, tests, packaging metadata, README,
blog, DNS, PyPI. So it got the full treatment: spec, implementation plan, subagent-driven
execution with a reviewer gating every task.

Two of those tasks were the mechanical heart of the job: rename the Python package
(imports, branch prefixes, test assertions, launcher) and sweep the docs. Bounded,
well-specified, verifiable — exactly the shape of task the tool exists for. So the tool
got assigned its own rename. A qwen3-coder worker, in an isolated worktree, renaming the
harness it was running inside of.

Task one went cleanly: 24 turns, tests updated first so the suite went red for the right
reason, then the package rename brought all 133 back to green. The reviewer approved it with
nothing to fix.

## Exit 127

Then task three — the docs sweep — refused to start. `command not found`.

What happened is the kind of thing you'd reject in fiction. The `localagent` command on
my PATH was a symlink to `bin/localagent` in the repo. Task one had just renamed that
file to `bin/dirtywork`. The launcher I was using to launch the rename had been renamed
out from under me by the rename. The first casualty of the rename was the renamer.

One relaunch through the new launcher later, the worker ran fine. And the run's own
receipt is the part I'd frame: it came back reporting a worktree at `.worktrees/dw-…`,
on a branch named `dirtywork/…`, with its transcript under `~/.dirtywork/runs/`. Every
one of those names was minutes old. Task one hadn't even merged — the worker was running
inside the renamed harness on its own feature branch, which means the rename was
validated in production *by the rename itself*, before its own pull request existed.

That run: 47 turns, about 1.1 million prompt tokens, $0 in API fees.

## What review caught

The honest section, because a story where delegation just works is a story you should
distrust.

The worker missed one plan line item — a couple of README examples still showed the old
worktree prefix. Half of that is on the orchestrator: the line existed in the plan and
didn't survive into the worker's prompt. Specs don't delegate themselves.

More interesting: the worker *added* things. Twice, in one docs task, it invented
decorative HTML nobody asked for — a styled stats box here, an extra flourish there.
Reviewers caught both. That's the sharpened lesson of the day: when you audit a local
worker's diff, you're not just looking for what's missing. You're looking for what's
extra. Small models are eager.

And the final whole-branch review caught a defect that wasn't the worker's at all: the
module's `__version__` still said 0.1.0 because the *plan* never listed that file. The
review gate doesn't just catch the cheap model. It catches the orchestrator writing the
plan. That's the point of gates.

## The scoreboard

From "we need a new name" to shipped, in one evening:

- Competitive survey → name → domain bought: about half an hour
- Spec, plan, and a six-commit PR (two commits authored by the tool itself), 133 tests
  green, one worker fix round, one final-review fix
- Repo renamed, DNS live, HTTPS enforced, `dirtywork 0.2.0` on PyPI:
  `pipx install dirtywork`
- Total elapsed, name decision to installable package: about an hour and a half

The first post ended by saying the pattern needed more reps before I'd call it proven.
It got its reps the same evening, on itself. The tool that delegates dirty work spent
the night doing its own — in a sandbox, on a transcript, behind a review gate, exactly
as drawn.

Frontier models do the thinking. Local models do the dirty work. Now the name says so.
