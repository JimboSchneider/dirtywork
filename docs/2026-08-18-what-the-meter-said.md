# What the Meter Said

*SP3, the extensibility sub-project, built while instrumented. Written 2026-08-18.*

---

A follow-up to [Every Failure Was the Harness](every-failure-was-the-harness.html).
Same bet, one sub-project later: a frontier model planned, reviewed, and ruled; a free
local model did the typing; I decided what merges. The difference this time is that we
measured the run *while it was running* — per-turn speed, memory pressure, model time
versus tool time — and several of the day's biggest wins came out of the instrumentation
rather than out of anybody's judgment.

SP3 is the extensibility pass: a tool registry, provider adapters (including the
Anthropic API), the `runs` command family, `dirtywork bench`, and `--allow-commit`.
Sixteen tasks, 39 commits, built with dirtywork itself. Everything below comes from the
run's own record, linked at the bottom.

## First: the plan was stale

The SP3 plan was written on the 16th, against pre-SP2.5 code. Then SP2.5 shipped as
0.5.0/0.5.1 and rewrote `runner.py` and `__main__.py` underneath it. Two tasks in the
plan mandated "full rewrites" of exactly those files. Executing the plan as written
would have deleted shipped behaviour.

So the plan got re-baselined before Task 1: two Opus revision agents (one on the header
and Tasks 1–8, one on Tasks 9–15 plus a new Task 16 for `--allow-commit`), spliced and
committed as docs. Then a pre-flight scan over the revised plan: 53 plan-vs-code pairs
across 16 tasks, **0 shipped-code drift misses, 3 blockers, 10 defects, 9 notes**. The
blockers were small — an empty `base_url` handled as falsy instead of `None`, two shipped
tests the rewrite block didn't know existed. Cheap to fix in a document. Not cheap to fix
after a local model has faithfully typed the wrong thing.

## The scoreboard

Twenty qwen runs with a surviving `run.json` — 13 completed, 5 max_turns, 1
context_exhausted, 1 model_error — plus the crashed first attempt at task-6c, whose
record its own retry overwrote. It's in the table below anyway. 981 turns, 167.3 minutes
of wall clock, 33.7M prompt tokens, 233,795 completion tokens, all local, all free.

| Run | Status | Turns | Wall |
|---|---|---|---|
| smoke | completed | 2 | 0.1m |
| task-1 | completed | 6 | 0.9m |
| task-2 | completed | 11 | 1.8m |
| task-3 | max_turns | 80 | 4.5m |
| task-4 | completed | 24 | 2.3m |
| task-5 | completed | 29 | 3.1m |
| task-6 (run 1) | max_turns | 120 | 29.4m |
| task-6a | context_exhausted | 119 | 32.9m |
| task-6b | max_turns | 120 | 5.3m |
| task-7 | completed | 27 | 2.4m |
| task-6c (run 1) | model_error | 13 | 0.9m |
| task-13 (run 1) | model_error | 29 | 7.7m |
| task-6c (run 2) | max_turns | 100 | 6.8m |
| task-8 | completed | 64 | 7.1m |
| task-9 | completed | 20 | 3.9m |
| task-10 | completed | 30 | 6.9m |
| task-11 | completed | 37 | 7.9m |
| task-12 | completed | 28 | 6.0m |
| task-14 | completed | 41 | 13.7m |
| task-15 | max_turns | 60 | 18.4m |
| task-16 | completed | 63 | 6.3m |

Two of those statuses are less alarming than they look: `max_turns` on task-3 and
task-15 meant the work was done in substance and the turn cap was too tight. Nine of the
twenty runs were landed by controller commit rather than by the worker committing its
own. The two `model_error` rows are the same failure, and I'll get to it.

## Task 6 ate the context window twice

Task 6 was the runner's switch to a provider-neutral history. Its brief was 1,084 lines.

qwen never finished it. Run 1: 120 turns, 29.4 minutes, 5.6M prompt tokens — and at the
end, `runner.py` was syntactically broken and there were eight scratch patch scripts
lying around. The worker had made 66 bash calls and 48 `read_file` calls and had not
called `edit_file` or `write_file` once. The brief plus its own re-reads exceeded its
working context; it reset and retried and ran out of room again. I split the task into
6a/6b/6c. 6a came back `context_exhausted` after 119 turns and 32.9 minutes.

That's two failed runs and about an hour of wall clock spent proving the brief was too
big, not that the model was too small.

Then we reloaded qwen at 131,072 context instead of 65,536, and the shape of the whole
day changed:

| Context | Model time per turn | Prompt tok/s (model time) | The runs |
|---|---|---|---|
| 65k | 14.4–16.5 s | ~2,700–3,300 | task-6 (120 turns, 29.4m), task-6a (119, 32.9m) |
| 131k | 2.6–3.8 s | ~13,300–13,900 | task-6b (120, 5.3m), task-6c run 2 (100, 6.8m) |

Same model, same machine, same tasks — roughly 3–5× the per-turn speed. The cause is
prompt-cache thrash: at 65k the context trimmer was evicting history nearly every turn,
which invalidates the cache, which means re-processing the prompt from scratch. Task 6b
did 120 turns in 5.3 minutes. Task 6 run 1 did 120 turns in 29.4.

The greed had a limit. Two 131k slots running concurrently (Task 7 alongside Task 13)
worked at first — about 10% per-worker slowdown for ~1.9× aggregate throughput. Then the
sampler caught the memory walking up: 51.9 GB wired and 13.8 GB free a minute in;
55.9 GB wired and 1.2 GB free fifteen seconds before LM Studio crashed and took both
in-flight runs down with it. Those are the two `model_error` rows. One slot at 131k
peaks around 66 GB wired on this box, which is the configuration that survives.

Two pieces of Task 6 still needed Sonnet to finish the last mile: 6b (qwen switched the
runner and got 65 of 71 tests green; the remaining six were SP2.5 reply-classification
semantics) and 6c (36 of 40 `test_main` green). Sonnet closed both, 6.1 and 11.7 minutes.
Two of sixteen tasks needed an escalation. I'd rather write that down than round it off.

## Tool time, not model time

Halfway through the back half, the model-versus-tool split said something I didn't
expect: for tasks 8 through 14, **57–81% of wall-clock was tool time, not model time.**
The slowest individual calls were pytest runs at 121, 183, 276, 277 seconds. The same
suite took about 30 seconds on the host.

Reproducing it took one command. Host mode sets `HOME` to the worktree by design, and
that dropped `VOLTA_HOME` — so the `node` shim on `PATH` resolved to volta, volta found
no toolchain under the new `HOME`, and re-downloaded Node into the worktree. Every single
run. (That also explained the `.volta/` junk that had been appearing in worker trees
since Task 13, which I'd noticed and not chased.)

The fix is on this branch: `VOLTA_HOME`, `RUSTUP_HOME`, `CARGO_HOME`, `NVM_DIR` and
`PYENV_ROOT` are carried into the worker environment when set. The worker-env full suite
went from 276 seconds to 36. It's the same lesson as SP2.5's `PYTHONPATH` fix, one
category up: HOME-keyed toolchain managers have to be pointed home. Workers still
launched from the released 0.5.1 for the rest of the run, so Tasks 15 and 16 paid the
tax anyway — that's why their walls look the way they do in the table.

A new macOS update was also lurking in the shadows, mocking us. I'll let that one stand
without further investigation.

None of this was found by thinking hard. It was found because the run was instrumented
and somebody read the numbers in the same hour they were produced.

## What review caught

Each task was reviewed by a two-lens workflow with skeptic agents verifying every
Critical and Important finding, plus scoped Haiku re-reviews after fixes. The largest
category of Important findings, by a distance, was **defects the plan mandated**:

- `runs export` and `runs clean` each re-implemented a "is this worktree pristine?"
  check that already existed — three copies before it became one predicate.
- `runs clean` removed a worktree and branch without the `worktree_belongs_to_repo`
  guard that its sibling code path already had.
- `bench` duplicated the security flags that `docker_args` already assembles, and staged
  repos outside the per-case `try`, so one git failure would abort the whole sweep and
  leak a temp directory.

In each case the worker typed exactly what the plan told it to type. The review gate
doesn't know who wrote the flawed instruction, which is the entire reason it works: the
plan's authorship doesn't get to grade the plan's output.

The final whole-branch review was six lenses — spec, behaviour and security on Opus;
quality, docs and tests on Sonnet — with two skeptics per finding. 48 agents, 21 findings
confirmed, 0 refuted, 18 Minor. What's notable is that the confirmed set was *entirely*
cross-task seams, the kind no per-task review can see:

- The new Anthropic serializer broke the runner's message-alternation invariant on both
  nudge paths — consecutive `user` turns after tool results, and empty assistant content.
  Task 7 built a correct adapter; SP2.5 built a correct nudge; together they were wrong.
- `runs clean` would happily delete a branch full of committed work from an
  `--allow-commit` run without `--force` — two features that shipped in the same branch
  and had never been considered in the same sentence.
- The toolchain-root passthrough from the section above — the fix I'm most pleased
  with — reopened `rm -rf "$CARGO_HOME"` past the guardrail denylist. The fix opened
  the hole.

One fix wave (two Sonnet implementers on disjoint files) closed all 23 items. The
re-review then caught a regression *in the fix wave*: the new run-directory keep rule
fired on the benign "container already removed" outcome, which would have made every
routine cleanup demand `--force`. Fixed. Final gates: 797 unit tests, 14 docker tests,
3 live.

## The meter

Here's the part I keep having to explain: **the frontier tokens did not go down.**

The SP3 day was the largest frontier day in these logs — about 3.4M output tokens, 23.3M
cache writes, 909M cache reads on 08-17. The Opus plan re-baseline plus the pre-flight
scan was roughly 27% of it by API-list-equivalent weighting. Against that, about 47M
prompt tokens went to the local model on the same day — that figure covers the tail of
SP2.5 as well as SP3, which between them account for 44 local runs on the 17th.

What changed isn't the volume. It's what the volume buys. Frontier tokens moved out of
implementation and into planning, orchestration and review — six-lens reviews and skeptic
verification are not cheap, and they're where the 21 confirmed findings came from. The
local model absorbed the typing.

On the subscription side: the meter at 19:41 CDT read 27% of the 5-hour session
window, 10% weekly across all models, 11% weekly on Fable — after about 27 hours covering
SP2.5 and most of SP3. I'm not going to convert that into a dollar saving, because I don't
pay per token and I'd be making the number up. The honest framing is the same one as last
time: what gets rationed is frontier attention, and this is a tool for spending it
carefully.

## What changed, and what's next

Things this run put into the harness, mostly because the run found them:

- Toolchain roots carried into the worker environment (and then guarded, after the final
  review noticed what that opened).
- `--allow-commit`, host mode only, so a run's branch comes back as real history instead
  of a dirty worktree — plus the `runs clean` refusals that make it safe to clean up.
- Provider adapters and `--provider`, with the Anthropic API as the second implementation
  that proved the seam was real.
- `dirtywork runs list/show/export/clean/verdict`, and `dirtywork bench` — which means
  the next version of the table above can build itself.

The branch is complete: 39 commits, gates green, 0.6.0 proposed and not yet applied. The
PR and the release are mine to call, and I haven't called them yet.

If you want to check the arithmetic, it's all in the repo. The per-run scoreboard and
metrics are in
[superpowers/bench/2026-08-17-sp3-worker-scoreboard.md](https://github.com/JimboSchneider/dirtywork/blob/main/docs/superpowers/bench/2026-08-17-sp3-worker-scoreboard.md),
the decision-by-decision ledger in
[superpowers/bench/2026-08-17-sp3-sdd-ledger.md](https://github.com/JimboSchneider/dirtywork/blob/main/docs/superpowers/bench/2026-08-17-sp3-sdd-ledger.md),
the model-versus-tool split in
[superpowers/bench/2026-08-17-sp3-run-split.md](https://github.com/JimboSchneider/dirtywork/blob/main/docs/superpowers/bench/2026-08-17-sp3-run-split.md),
and the previous sub-project's numbers — same model, same box, at 65k — in
[superpowers/bench/2026-08-17-sp2.5-worker-scoreboard.md](https://github.com/JimboSchneider/dirtywork/blob/main/docs/superpowers/bench/2026-08-17-sp2.5-worker-scoreboard.md).
Or skip the reading and run `dirtywork bench` against your own models on your own box.
That's what it's for.


One caveat, same as always: this is one machine — an Apple M5 Max with 128 GB of unified
memory — running one plan with one local model family. It is a record, not a benchmark.
The 65k-versus-131k numbers in particular are a property of this box's memory, and yours
will differ.

A frontier model planned, reviewed, and ruled. A free local model did 981 turns of
typing. I decided what merges. Every number here came from that run's own record, which
is still the only way I know to ask you to believe it.

---

*Claude (Fable 5) planned, reviewed, and ruled on this session, and drafted this post
from the session record linked above. Jim edited it. Same process as the three earlier
posts.*
