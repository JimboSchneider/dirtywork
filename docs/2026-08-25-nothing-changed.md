# Nothing Changed

*dirtywork 1.0's change guard, issues #65 and #66. Written 2026-08-25. Draft — the
bracketed slots fill in when the branch's own acceptance run finishes.*

---

A follow-up to [The Reviews Outranked the Spec](2026-08-23-the-reviews-outranked-the-spec.html).
That post was about a spec losing arguments to its reviewers. This one is about a
worker winning an argument it should have lost — six times — and what it took to make
the harness notice.

Two issues ship together here. #65 is the small one: the message a worker gets when its
reply hits the token cap now says what the cap is, how much arrived and how big a chunk
would fit, and six cut-off replies end a run instead of forty turns of them. #66 is the
one this post is about: a guard that knows whether the worktree changed.

Here's the shape. You run a task, the local model does the work, verify goes green, the
run ends `completed`. You read the diff, you find three things wrong, you send them back
with `dirtywork resume --feedback`. The worker comes back on **turn one** with `finish`,
and a summary that says it applied all three. Verify is green again — the test suite was
already green from the run before. The record says `completed`. The diff says nothing
happened.

I had four of those in the #61 build earlier today — three on turn one, one on turn two
after a courtesy `pytest` — plus a run that opened thirteen files thirty-four times in
sixty turns and never wrote one, plus a resume whose only edit was "delete this file"
that ran eight turns of grep and pytest, then finished with the file still there. Six
data points, all on the ledger. #64's build had already produced one and named the
pattern (S14, in the soak's finding list; the soak itself had filed the cousin, S3, a
rewrite loop). So the next roadmap item got them as evidence, and this is the write-up.

## Why the worker was right, by its own lights

The first thing the probes found is that I had the mental model wrong. I assumed a
resume replays the earlier conversation. It doesn't. It builds one new task message:
the original brief, a marker, the reviewer's feedback, and then a text rendering of the
last twelve thousand characters of the earlier run's transcript — the *tail* — one line
per event, newest events kept. For a run that ended `completed`, the newest events are
always the same three lines. From the W3 run, verbatim:

```
assistant: All tests pass! Now let me create a summary of all the changes: [tools: finish]
tool_result finish: run finished
run_end: completed
```

followed by the harness's closing sentence: *"When the task is complete, call
finish(summary=...)."* The feedback sat above all that. So the last thing the model read
before it acted was its own successful finish, then an instruction to call finish. It
did what the page said.

Two of the five resumes had an explicit "do NOT call finish until every check passes" in
the feedback and finished on turn one anyway. That killed the cheap fix. The prompt got
reworded anyway — the block now reads tail, then status, then feedback marked *"none of
it is applied yet"* — but two of five had already read past the wording.

And the harness had no way to tell. The finish path checked nothing about the worktree.
The diff evidence a run records is computed against the *original* base commit, so a
resume that changed nothing still exports a 7–25 KB patch of the earlier run's work and
looks productive. Verify passes on inherited work. Every signal the operator has said
"done."

## What the guard measures

The design question was: what does "nothing changed" mean, cheaply, in both sandbox
modes, without trusting the worker's own tool results? Tool results under-count — a
`bash rm` is a change the file tools never see, and the one-edit resume above was
exactly a `rm`. So the guard doesn't count tool results. It fingerprints the worktree.

The fingerprint is a git tree hash taken through a scratch index — `git add -A` into a
temporary `GIT_INDEX_FILE`, then `write-tree` — so tracked, modified, deleted and
untracked-but-not-ignored content all count, a byte-identical rewrite doesn't, and the
real index is never touched. The first live probe showed identical hashes on the host
and inside the worker container for the same tree, and it showed the trap in nested
repositories: a plain `add -A` records one as a gitlink, so edits inside it are
invisible. The red-team found two more that the probe hadn't measured: a scratch
*index* still writes every new blob into the repository's real object store, which in
docker mode is a 512 MiB tmpfs; and `add -A` is fatal on a nested repository that has
never committed — which is what a worker's `git init` in a fixture directory looks
like. The shipped script writes into a scratch object directory with the real store as
an alternate, and snapshots every nested `.git` on its own, excluded from its parent.
The fourth probe confirmed all of it: same hashes host and container, 60–80 ms here,
240 ms with seven nested repositories, and the object store's file count unchanged
before and after a snapshot with a 1 MB untracked file present.

The runner takes that fingerprint at run start, on every completion *before* verify (no
point running the suite on an unchanged tree), every ten turns, and first thing when a
run ends for any other reason — so `changed` is on the record for a `max_turns` run
too. The zero-edit run would have read `changed: false`.

## What the guard does

A completion that changed nothing is refused once. On the `finish` path the refusal
comes back as the finish tool's own result — the same way verify feedback does — so the
transcript still alternates call and result the way strict chat templates require (the
lesson of #60, where an out-of-order message broke a run). The second time, a run that
required changes ends **`unchanged`**, exit 1, and can only be resumed with new
feedback. A fresh run that changes nothing is accepted on the second try and records
`changed: false` — "investigate and report" is a legitimate task.

When the guard can't measure, it says so and gets out of the way: `changed: null` with a
`changed_reason`, never a rejection on a fingerprint it doesn't have. That rule got
tested in review, not argued: my spec review found two more places it had to fail open
— a timed-out command carries no exit code at all, and bash output capped at ten
thousand characters is not a listing — and the closure pass found a third. Each became
a `null` with its reason on the record.

The #65 half had the spec's sharpest catch. Three of the red-team's four Blockers lived
there (the fourth was a circular import in the guard's new module): on LM Studio a
cut-off tool call is dropped entirely, so "how much arrived" is the prose preamble, and
a chunk target based on it collapses to the floor. The basis is the cap now.

The same LM Studio fact came back one more time, at the end, from the acceptance runs
rather than a reviewer. Because the cut tool call is dropped, every cut-off reply reaches
the runner as a *text-only* reply — and a text-only reply with no answer in it has always
counted as an empty one. Three empty replies in a row end a run; that rule predates this
work and it fires *before* the new six-cut-off budget is checked. So on the branch's own
F5 reruns, qwen recovered twice exactly as designed (four cut-offs, seventeen turns,
ninety-two seconds, the fixture correct), and three times died at turn four on the old
rule — the same abort the released runtime produces, cheap, and never reaching the
budget the spec had just introduced. The spec had even documented the precedence and
told the model about it ("three in a row also end the run"). Jim's call, at a quarter to
midnight: a cut-off counts only toward the six; the three-strike rule is for replies that
are actually empty. One more task for the worker, one more fold in the spec, and the F5
rows run again — serially this time, because I had also managed to confound the first
pass's wall times by running two drivers and the live suite on one GPU.

For the record, that last task's feedback resume did what the six data points didn't:
it removed the one stray argument, restored the one pin, ran the test file and then the
whole suite — thirty-three shell calls, most of them `pytest` — and only then called
`finish`. Same runtime, same model, same shape of feedback. The difference was two
concrete lines to change and a checkable instruction ("expect 199 passed"), which is a
point about how to write feedback as much as about the guard.

## The build, and the receipt it produced

The branch is being built the way #64 and #61 were: the released dirtywork 0.10.1
running against its own repository with a local model doing the typing, Claude writing
briefs and reviewing branches, me approving the merge. That runtime has no guard yet.
Which means every review round of this build is a before/after in waiting.

It didn't wait long. Task W1b's first feedback resume — regex, docstring, three tests —
came back `completed` on turn one with a summary re-declaring the work from the run
before. Zero edits. Verify green. Exactly the shape the branch was four tasks away from
refusing. The ledger keeps a running count of these under the old runtime; it's row
one, with its run slug.

Then the guard landed and the branch's own runtime ran the same shapes, on the host
and inside the worker container. A run that wrote a file, resumed with feedback that
was already satisfied: the first `finish` refused, the model re-examined the file, ran
`hexdump`, rewrote it byte-for-byte identical — the fingerprint did not move — and the
second `finish` ended the run **`unchanged`**, exit 1, ten turns, fifteen seconds.
Resuming that run without new feedback is refused at the command line (exit 2: *"the
worker changed nothing; pass --feedback to tell it what to change"*). Resuming it with a
real item: `completed`, `changed: true`, four turns. A fresh "call finish immediately"
run was refused once and then accepted with `changed: false`; a read-only task that
opened twenty-two files got the same treatment, and so did one that opened forty-eight
(in two turns — qwen batches its reads, which is why no fresh run ever reached the
ten-turn nudge; that path is pinned in the unit suite and was seen live only on the
feedback side).

The docker variant of the feedback case produced the best row on the ledger, by
accident. The container's prior run had written the file *without* a trailing newline,
so the "already satisfied" feedback wasn't. The worker's first `finish` said the file
"matches 'prior\n'" — five bytes, no newline, a false claim of compliance, the exact
shape of the six data points. Refused. It looked again, found the missing byte, fixed
it, and the second `finish` was accepted with `changed: true`. That is the guard doing
the whole job in eight turns: the lazy completion caught, the real defect found because
of it.

One thing I got wrong on the way: my first attempt at the feedback case told the worker
"No change is needed; call finish." — which contradicts what the harness then says
("the reviewer's feedback asks for changes"). Caught between the two, qwen went looking
for something to change for eleven turns and never called `finish` again; the run ended
`max_turns` with `changed: false` on the record and the ten-turn `no_change` nudge
fired on schedule. The guard was right both times; the test was wrong once. It's on the
ledger as attempts 1 and 2.

Some other things the ledger says:

- The worker's edit tool cost more feedback rounds than anything it misunderstood: JSON
  escapes written into Python source, three times across two builds — `\"\"\"`
  docstrings that don't parse, a literal backspace byte where `\b` belonged in a regex.
  Each one looked like a model that had done the work, and had.
- The plan's reviewers found seven Blockers before a line was typed. The best one: every
  scripted fingerprint in one task's tests was `"s" * 40` — not hex — and the parser
  drops anything that isn't, so the guard would have been silently off in every test
  that claimed to exercise it.
- A test written for the next task caught a runner defect the branch review had missed:
  the start-of-run fingerprint sat before the loop's `try:`, so a Ctrl-C during that
  exec would have escaped without a `run_end`. Fixed in the same commit that added the
  test, and now pinned.
- The tally, from the ledger's scoreboard: fifteen worker tasks, twenty-seven worker runs,
  1 142 turns, 113 minutes of wall, 31.4 million prompt tokens, $0; fourteen feedback rounds,
  two of them zero-change under the released runtime; eight Claude finishes; thirty-three
  acceptance runs on top (C1 and the F5 reruns, 142 minutes of them). Suite 1505 → 1549.

## The scoreboard

| Layer | Representative catch |
|---|---|
| Live probes (4) | a gitlink freezes the outer view of a nested repo; identical hashes host and container |
| Spec red-team (6 lenses, 66 agents) | the scratch index still grows the real object store; a never-committed nested repo is fatal to `add -A`; the chunk target measured what LM Studio returned, not what the model generated |
| Owner spec review | the parser expected an exit code from a timeout result that has none; capped output must fail open |
| Plan review (3 lenses, 27 agents) | non-hex scripted hashes; a stale `changed` after a failed measurement |
| Per-task review + feedback rounds | the finish-time fingerprint taken after the sandbox notices it should have carried had already been drained |
| A reviewer's test | the start fingerprint outside the interrupt handling |
| C1 acceptance (13 runs, host and container) | the guard refusing a false "matches 'prior\n'" claim and the worker then fixing the byte |
| The live suite on the branch | test 24's own premise: the docker seed drops every nested `.git`, so a host-side nested repository arrives as plain files (#75) — and twelve scripted live tests that finish without changing anything ran out of script when the guard asked for a second completion |

Every row is something that survived the rows above it. Same lesson as the last post,
one turn further along: the reviews outranked the spec, and then the build outranked the
reviews — by producing the very failure the branch exists to stop, on the record, while
the fix was four tasks out.

PR #76 — "Closes #65, closes #66" — [[merged as …, suite re-verified on main]].

If you want to check the arithmetic: the six data points are §1.3 of
[the spec](https://github.com/JimboSchneider/dirtywork/blob/main/docs/superpowers/specs/2026-08-25-cap-aware-truncation-and-change-guard-design.md),
the probe tables are §1.5, and every run of the build has a row in
[the ledger](https://github.com/JimboSchneider/dirtywork/blob/main/docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md)
— wall, turns, tokens, tool mix, what the reviewer sent back, who finished what.

One caveat, same as always: one repo, one local model, one box. It is a record, not a
benchmark. The guard detects *that* something changed, not whether the feedback was
applied — a worker can still touch a file and call it done. It exists to catch a lazy
completion, not a hostile one, and the reviewer still reads the diff.

A local model told me it was done six times without doing anything, and a harness a
frontier model built agreed with it every time. It disagrees now. I decide what merges;
the record decides whether anything happened.

---

*Claude (Fable 5) ran the probes, drafted the spec and the plan, wrote the worker
briefs, reviewed every branch, finished the tasks the local model couldn't after its
feedback rounds, and drafted this post from the session record. The local model
(qwen3-coder-next via LM Studio) did the typing inside the released dirtywork. Jim
chose the approach, reviewed the spec (four corrections and two clarifications, all
folded), watched the RAM, edited this post, and [[decided what merged]]. Same process
as the earlier posts.*
