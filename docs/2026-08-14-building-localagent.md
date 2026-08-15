# Building localagent: teaching an expensive AI to delegate to a free one

*A session log / postmortem, written 2026-08-14. Draft for a possible blog post.*

---

## The pitch

I run Claude Code on frontier models. They're excellent — and expensive. Meanwhile
my Mac Studio sits there with 128 GB of RAM running LM Studio, serving
qwen3-coder-next (65k context) and Devstral Small (32k context) for free, four
concurrent requests each.

The question that started this: **can the expensive model delegate to the free
ones?**

Claude Code's own subagent machinery only spawns Claude models — there's no
"point this subagent at localhost." But nothing stops Claude from driving a local
model *as a tool*: build the prompt, POST it, check the work. The problem is that
a bare chat completion can't explore a repo, edit files, or run tests. What was
missing was a harness — an **agent loop** around the local model.

So in one session, Claude and I designed, built, reviewed, and shipped one:
**localagent**, a stdlib-only Python CLI that runs one coding task per process
against a local model, inside a git worktree it can't escape, producing a
reviewable diff and a full JSONL transcript.

The same day, its first production run wrote unit tests for my invoicing app —
and in doing so surfaced a real cent-level rounding bug in invoice math that had
been sitting in production. Free tokens found a paid-tier bug.

## The division of labor

The design premise is a two-tier system:

| Tier | Who | Does |
|---|---|---|
| Orchestrator | Frontier model (Claude) | Picks the task, writes a precise prompt, audits the transcript, reviews the diff, re-runs the gates, commits/PRs what survives |
| Worker | Local model (qwen3-coder-next) | Explores the repo, writes code, runs builds and tests — inside the sandbox |

Two rules make this safe and economical:

1. **Nothing the worker does touches your real checkout.** Every run gets a fresh
   git worktree on its own branch. No auto-commit, ever. The deliverable is an
   uncommitted diff plus a transcript.
2. **The review gate is the real safety boundary.** Runtime guardrails
   (path confinement, a bash denylist, a scrubbed environment) block *accidents*,
   not adversaries — the spec says so explicitly. What actually protects you is
   that a competent reviewer reads the diff and re-runs the tests before anything
   merges.

The economics only work when the review surface is small. If the worker emits 400
lines you must read line-by-line, you've paid most of the cost anyway. Bounded
tasks — "write tests for this file, here's the style reference, verify with this
command" — are the sweet spot.

## What localagent is

~600 lines of Python 3.9, standard library only (the system Python on macOS runs
it with zero setup). One module per concern:

```
localagent/
  __main__.py     # CLI: preflight → worktree → run → one JSON object on stdout
  runner.py       # the agent loop: tool dispatch, context trimming, strike limits
  llm.py          # urllib client for LM Studio's OpenAI-compatible API
  tools.py        # read_file / write_file / edit_file / list_dir / grep / bash
  guardrails.py   # path confinement + bash denylist + minimal env
  workspace.py    # worktree lifecycle, CLAUDE.md injection
  transcript.py   # JSONL event log, flushed per line (tail -f friendly)
```

Key mechanics:

- **Native tool calling.** LM Studio speaks the OpenAI `tools` API — send a
  schema array, get structured `tool_calls` back. No fragile "parse XML out of
  prose" protocol. (Verify this for your model with one curl before building
  anything; it's the load-bearing assumption.)
- **The loop** is ~100 lines: POST messages → if the reply has tool calls,
  execute them and append results → repeat until the model replies in plain
  text. Bound it three ways: max turns, a wall-clock deadline propagated into
  every model request *and* subprocess, and a three-consecutive-malformed-calls
  abort.
- **Context budgeting.** Estimate ~4 chars/token, count tool-call *arguments*
  as well as message content (file writes are the biggest payloads!), and trim
  oldest tool results first when nearing the window.
- **Machine-first output.** Exactly one JSON object on stdout —
  `{status, worktree, branch, transcript, turns, usage, final_message}` — with
  exit codes 0/1/2. The consumer is another AI parsing stdout, so this contract
  is load-bearing; humans watch via `tail -f` on the transcript instead.
- **Convention injection.** If the target repo has a `CLAUDE.md`/`AGENTS.md`,
  its content goes into the worker's system prompt — read from the *worktree's
  checked-out ref*, not the caller's possibly-dirty working tree.

## How it was built (the meta-story)

The build itself was an exercise in the same delegation philosophy, one tier up:

1. **Brainstorm → spec → plan.** The design conversation settled the safety
   model (full tool powers + review gate), the UX (machine-first, watchable),
   and the containment strategy before any code. The plan then specified all ten
   tasks *including the actual code and tests* — bite-sized TDD cycles.
2. **Cheap models transcribed, mid models reviewed.** Each task went to a
   fresh small-model subagent (the plan text was complete enough that
   implementation was transcription plus testing), and every diff got an
   independent review against the spec before the next task started.
3. **The reviews earned their keep immediately.** Six fix rounds before the PR
   even opened: two "never raises" contract violations reproduced with a
   read-only file and a dangling symlink, exception-type holes in the HTTP
   client (`IncompleteRead` is not an `OSError`!), a stale-branch leak on failed
   worktree creation, crash paths on malformed server responses.
4. **A final whole-branch review** on the strongest model caught what
   task-scoped reviews structurally can't: cross-module seams — the timeout that
   nothing actually enforced, an inconsistent stdout contract, context
   accounting blind to tool-call arguments.

Then the pull request went through **seven rounds of human review** — sixteen
findings, every one verified against the code before being fixed (several
reproduced empirically first), every fix re-gated and replied to in-thread.
Highlights worth stealing as review heuristics:

- *"Does the advertised timeout actually bound every blocking call?"* (It
  didn't: the HTTP client had its own 600s timeout; grep had a fixed 30s.)
- *"What happens on `{"choices":[{"message":null}]}`?"* — malformed-but-valid
  JSON is a whole bug class distinct from invalid JSON.
- *"Is the recovery path protocol-valid?"* A synthesized tool result with an
  empty `tool_call_id` passes your fake-client tests and gets rejected by a
  strict real server. The terminal fix was canonicalization: rebuild every
  accepted tool call into the exact wire shape before resending.
- *"Can cleanup destroy pre-existing state?"* Our best-effort
  `git branch -D` after a failed worktree add would delete a branch that
  existed *before* the call. Record provenance; only delete what you created.

The convergence pattern was striking: 5 findings, then 3, then 1, 1, 1, 2, 3-then-clean —
each round narrower than the last, ending at "no additional actionable issues."

## The first production run

Task: *"Add Vitest unit tests for the six functions in `web/src/lib/format.ts`,
colocated, following the style of `version.test.ts`, verify with `npm run test`."*

The numbers:

- **12 turns, 38 seconds** of loop time
- **89,294 prompt / 1,824 completion tokens — all local, $0**
- 25 tests, all passing; typecheck clean; **zero guardrail events**
- The transcript showed genuinely good behavior: it read the source and the
  style reference before writing, and when its negative-rounding expectation
  failed, it probed real `Math.round` semantics with `node -e` and corrected
  itself rather than guessing.

Review cost on my side: one 125-line test file to read, two gate commands to
re-run, one stray `package-lock.json` touch (from the worktree's `npm install`)
to revert. That's the economics working as designed.

**And then the kicker.** The tests faithfully pinned *existing* behavior — which
put a wrong behavior in plain sight. Two review rounds later, `round2` (which
computes invoice line amounts, subtotals, tax, totals) had two real defects fixed:

1. **Asymmetric negative rounding** — `Math.round` resolves ties toward +∞, so
   a credit didn't mirror its charge (`round2(0.125) → 0.13` but
   `round2(-0.125) → -0.12`).
2. **The epsilon trick silently fails past ~$10** — `Number.EPSILON` (2.2e-16)
   is *smaller than the ULP at magnitude 10* (~1.8e-15), so the nudge vanishes
   in float addition: `round2(10.075) → 10.07`. The fix rounds on the decimal
   the user sees, via a decimal shift: `Number(Math.abs(n) + "e2")` reparses
   the shortest decimal representation — `"10.075e2"` is exactly `1007.5`.

A test-writing delegation turned into a shipped `fix:` for invoice-money
correctness. The delegation didn't create the bug — it dragged it into the light.

## If you want to build one yourself

**Prerequisites.** LM Studio (or any OpenAI-compatible server) with a
tool-calling-capable coding model; git; any language with an HTTP client. Prove
the load-bearing assumption first: send a `tools` array via curl and confirm you
get structured `tool_calls` back, per model you plan to use.

**The recipe, in build order:**

1. **Transcript first** (JSONL, flush per line). Observability before behavior —
   you will debug the loop by reading this file.
2. **Guardrails second, as pure functions.** Path resolution that follows
   symlinks and compares path components (not string prefixes — `/repo-evil`
   starts with `/repo`). A bash denylist for accidents: `sudo`, `git push`,
   absolute-path `rm`, `cd ..` escapes, pipe-to-shell. Scrub the environment —
   your shell's tokens should never reach the worker's subprocesses.
3. **Six tools are enough:** read (numbered lines, windowed), write, exact-match
   edit (require uniqueness, return instructive errors), list, grep, bash. Tools
   *never raise* — every failure comes back as an `ERROR:` string the model can
   read and self-correct from. This one contract eliminates a whole class of
   crashes; enforce it with hostile tests (binary files, broken symlinks,
   read-only files).
4. **Worktree lifecycle:** create from a named ref on a salted-slug branch,
   record what you created so failed cleanup never deletes pre-existing state,
   use `git rev-parse --git-path info/exclude` (correct even in linked
   worktrees) to hide your worktree dir.
5. **The loop**, with all three bounds from day one, and defensive parsing of
   *every* field of the server response — assume `message` can be null,
   `tool_calls` can contain garbage, `usage` can be missing. Canonicalize
   accepted tool calls before resending them.
6. **A machine contract**: one JSON object on stdout, statuses as an enum, exit
   codes, everything else on stderr. Wrap the whole post-setup section in one
   exception boundary so the contract survives even your own bugs.
7. **Two test suites**: a fast one with a scripted fake client (no server
   needed), and a `live`-marked one against the real thing — including one true
   end-to-end run that creates a file in a throwaway repo.

**Prompting the worker:** name the file, name the style reference, name the
verification command. Inject the repo's conventions doc. Tell it to use the edit
tools (never `sed`), verify before finishing, and end with a plain-text summary —
the plain reply is the loop's termination signal.

**Reviewing the work:** read the transcript summary (guardrail events? weird
loops?), read the diff, re-run the gates yourself, revert incidental artifacts,
and only then commit — to a PR you review like anyone else's.

## Honest limitations

- A 30B local model is a *worker*, not an architect. Bounded, well-specified,
  verifiable tasks succeed; open-ended design does not.
- The guardrails are heuristic by design. The threat model is a confused model,
  not a malicious one — the review gate carries the real weight, so never skip it.
- Review cost is the true price. Delegate tasks whose output you can verify
  cheaply (tests, conversions, boilerplate, scaffolding), not ones you'd have to
  re-derive line by line.
- One session in, sample size is one. The first real run was a clear win; the
  pattern needs more reps before I'd call it proven at scale.

## The scoreboard

One day, end to end:

- Stale config cleaned up → design → spec → 10-task TDD plan
- 10 tasks implemented by small-model subagents, each independently reviewed;
  6 pre-merge fix rounds
- 1 final whole-branch review; 7-round human review cycle; 16 findings — all
  verified, fixed, re-gated, and replied to in-thread
- **130 unit tests + 3 live tests** green at merge
- First production delegation: 28 tests shipped to invoicr **plus a real
  rounding bugfix in invoice math**, for roughly 91k local tokens and 38 seconds
  of model time

The expensive model spent its tokens where judgment lives — design, review,
verification — and the free model did the typing. That's the whole idea, and for
one day at least, it worked exactly as drawn.

## Update — the same evening

The "sample size is one" caveat aged fast. By evening, four production runs:

- **The tool refactored its own source.** Asked for four hygiene changes, it
  delivered three and *declined the fourth* — correctly judging it couldn't be
  done without touching tested error strings, exactly as instructed. Along the
  way its own `cd`-escape guardrail blocked it seven times; it recovered each
  time and kept its own 130-test suite green. The tool policed its own
  developer.
- **Devstral's first production task, run concurrently** with a qwen task in a
  different repo — validating the parallel workflow, the salted-slug collision
  design, and the 32k-context path in one shot. (Observed cost: devstral takes
  roughly 1.5× qwen's turns and tokens for similar-sized tasks.)
- Every deliverable survived independent review plus a human review round, and
  merged behind CI gates.

Also since publishing: the project is MIT-licensed, CI-gated on three
platform/Python legs, and pip-installable — `pipx install dirtsimple-agent`
(every obvious name was taken or tripped PyPI's similarity filter; the command
installed is still `localagent`).

---

**Addendum (August 14, 2026, later still):** localagent is now **dirtywork**
([github.com/JimboSchneider/dirtywork](https://github.com/JimboSchneider/dirtywork),
`pipx install dirtywork`, [dirtywork.run](https://dirtywork.run)). A competitive
survey found the pattern this post describes — a frontier model orchestrating
and auditing cheap local workers — is a lane nobody's driving in; the ecosystem
mostly bets on *replacing* the frontier model instead. New name, same bet:
frontier models do the thinking, local models do the dirty work.
