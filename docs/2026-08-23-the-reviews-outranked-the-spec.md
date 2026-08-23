# The Reviews Outranked the Spec

*dirtywork 0.10, the v1 release candidate. Written 2026-08-23.*

---

A follow-up to [What the Meter Said](2026-08-18-what-the-meter-said.html). The last two
posts were about local workers doing the typing while a frontier model planned and
reviewed. This one is about what happens when the review machinery gets pointed at its
own paperwork — because the most-corrected document in this release wasn't code. It was
the spec I had already approved.

0.10 is the release that closes the last core-job holes before 1.0: `append_file` and a
tool-aware truncation recovery, so a file larger than one reply is a recoverable
two-step instead of a silent failure; atomic writes, so a run killed mid-write leaves
files byte-identical instead of truncated; `--max-tokens` with an honest budget;
`--provider ollama` as a first-class citizen; and a pile of snapshot and tooling
follow-ups. Thirteen tasks, 34 commits, tests from 1,026 to 1,164 — green at every one
of the gates in between.

One honesty note before the fun part: unlike SP2.5 and SP3, the typing this time was
Claude subagents, not the local models — the release that makes local workers better at
their jobs was built by the frontier side of the house. The local models weren't idle,
though: a live Ollama on this box verified the new provider end to end, twice. The soak
that comes next (#48) puts dirtywork back to building with dirtywork.

## The spec was approved. Then it kept losing arguments.

The 0.10 spec went through the full ceremony: a design conversation, a three-lens
red-team, my own line-by-line review (which sent it back once — four required
corrections), and a second pass where I approved it with three clarifications folded in.
At that point it was, by every process we have, *done*. The binding authority.

By the end of the day it carried **six dated execution amendments** — six places where
someone downstream proved the approved document wrong and the document had to yield.
That's not the spec failing. That's the entire point of layering reviews that don't
trust each other. Here's the ladder, one representative catch per rung:

**The plan red-team caught the spec's arithmetic.** The plan's Task 7 introduced a flat
`--max-tokens >= context-window` refusal, exactly as spec'd. Nobody had noticed that
seven shipped tests run with tiny context windows — the refusal would have broken the
suite mid-execution, with the implementer holding the bag. The red-team also found a
real bug in code that existed only as a plan listing: `_write_atomic` closed a file
descriptor, *then* nulled the handle, so a failing close would double-close and raise
`EBADF` out of a function whose entire contract is "never raises." Caught before a
single line was typed into the repo.

**A per-task reviewer with a container caught a cap bypass.** The docker `append_file`
guard used `stat -c %s` for its size check. `stat` without `-L` is `lstat`: on a
symlink it returns the length of the *link text*. So a worker could point a symlink at
a 6 MB file and the "un-appendable over 5 MB" refusal would read the size as 30 bytes
and wave it through — the reviewer demonstrated a 5 MB + 100 byte append landing on
disk, in a real bookworm container, past a cap three review layers had signed off on.
The guard now refuses symlinks first (matching the host's `O_NOFOLLOW` behaviour) and
stats through `-L`.

**Another one caught the spec aiming a delete at a readonly mount.** The spec placed
the stale-temp sweep "in the export container, immediately before `git add -A`." The
export container mounts the work volume readonly — a deliberate security property that
a different section of the same spec insists on. So the sweep as specified was a
guaranteed silent no-op: every `find -delete` would EROFS, the non-zero exit would
suppress the report, and the temps would be staged into the export anyway. The sweep
now runs in the worker container, while the volume is still writable, and the readonly
export mount stays exactly as paranoid as it was.

**A mutation battery caught the one test that couldn't fail.** Task 7's implementer
slipped once — implemented before running the red gates, and disclosed it. The review
response was to mutate the implementation fourteen ways and check a test went red each
time. Thirteen mutations died. One survived: the resume path that inherits `max_tokens`
from an old `run.json` had its explicit-`null` branch completely untested — downgrade
the code to a plain `.get(key, default)` and the whole suite stayed green. That branch
is precisely what the skipped red gates would have exposed. It has a test now.

**The empirical pass caught the spec believing its own story.** The spec's §4.3 sold
two-pass snapshot hashing as "a no-op snapshot writes no loose objects." The final
per-task reviewer built throwaway repos and measured old-versus-new: the old code
*also* wrote no loose objects on a no-op, because git's object store is
content-addressed and always has been. The claimed benefit was free all along; the
regression test pinning it was vacuous (it passed against the old code). What the
change actually buys — measured, not asserted — is skipping the temp index and
`write-tree` on a no-op, about 21% faster at 20k files, in exchange for a second
read-and-hash on changed trees. The spec now says that instead. I'd rather amend the
premise than ship a myth with a test attached.

**The whole-branch review caught the seams.** Per-task reviews can't see across tasks;
the final six-hundred-line-of-findings pass exists for exactly that. Its two real
catches were both born from the sweep relocation above: the sweep exec sat outside
export's failure boundary, so a transient docker timeout during the sweep would have
skipped the salvage path and *deleted the volume* — a robustness feature converting a
hiccup into total loss of the worker's output. And the swept-file count was computed
over merged stdout+stderr, so `find: Permission denied` counted as a swept file — the
"never silent, never wrong" report line was reliably wrong on exactly the failures it
was added to report. Both proven in a container before fixing.

**And the human caught what all of it missed.** My review of the finished PR flagged
that docker `append_file` could return a wrong diff for files between 1 and 5 MB. The
root cause was better than the symptom: every docker exec's output capture silently
stopped at a 1 MB default, with the truncation flag ignored. Which meant the 5 MB read
refusal was dead code — and, worse, the edit tools read a file, transform it, and write
it *back*, so editing a 1–5 MB file in docker mode had been silently truncating it to
1 MB. That bug shipped in 0.9. It predates this branch entirely. Six layers of review
built this release, and the data-loss bug they surfaced was one nobody was looking for,
found because a seventh layer — a person reading the finished PR — asked why a diff
looked short.

## The scoreboard

| Layer | Representative catch |
|---|---|
| Owner spec review (×2) | O_NONBLOCK on the second open; the append cap's exact wording path |
| Plan red-team (3 lenses) | 7 shipped tests broken by a spec'd refusal; the EBADF double-close |
| Per-task reviews (13 tasks) | symlink cap bypass; readonly-mount sweep; `mv` option injection |
| Mutation battery | the untested explicit-`null` inheritance branch |
| Empirical measurement | §4.3's premise was already true before the change |
| Whole-branch review | sweep failure deleting the volume; false swept counts |
| Owner PR review | the 1 MB capture cap hiding a pre-existing data-loss bug |

Every row is a defect that survived every layer above it. The plan's authorship doesn't
get to grade the plan's output — that was the SP3 lesson. The 0.10 lesson is one turn
stronger: the *spec's* authorship doesn't either, and neither does its approver. I
approved that spec twice. Six amendments later, I'm glad approval isn't the last word.

End to end: thirteen tasks executed with a fresh implementer per task and
red-gate-first TDD, a final review and fix wave, then the PR. Roughly six million
subagent tokens across about
forty agent seats — approximate, from the session's own dispatch records — spent almost
entirely on review and verification rather than typing. Same rationing argument as last
time: frontier attention went where the judgment lives. The typing was the cheap part.

0.10.0 shipped the same day:
[the release](https://github.com/JimboSchneider/dirtywork/releases/tag/v0.10.0), squash
of the 34-commit branch in
[PR #56](https://github.com/JimboSchneider/dirtywork/pull/56), suite re-verified on the
merged main: 1,164 passed.

If you want to check the arithmetic: the six amendments are dated inline in
[the spec](https://github.com/JimboSchneider/dirtywork/blob/main/docs/superpowers/specs/2026-08-20-v1rc-large-writes-atomic-ollama-design.md),
each at the section it corrects, and the PR description itemizes them with their
evidence. The parked list — the things we deliberately did *not* fix, with reasons —
is there too. A release that hides its corrections is asking you to re-find them.

One caveat, same as always: this is one repo, one release cycle, one stack of
reviewers with one orchestrator. It is a record, not a benchmark.

A frontier model planned, reviewed, and ruled — and got overruled six times by its own
reviewers, each time with receipts. I approved the spec, twice, and caught the bug that
mattered most anyway. The document that ships is the one that lost every argument it
deserved to lose.

---

*Claude (Fable 5) orchestrated the release — spec, plan, thirteen implementer and
nineteen reviewer dispatches — and drafted this post from the session record. Jim
reviewed the spec twice, found the capture-cap bug at PR review, edited this post, and
decided what merged. Same process as the earlier posts.*
