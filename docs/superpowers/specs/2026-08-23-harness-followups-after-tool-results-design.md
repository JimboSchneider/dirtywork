# Harness follow-ups after tool results — carriers that every chat template accepts (#60)

**Date:** 2026-08-23
**Status:** Design v3 — approach B (runner-level carriers) chosen by the owner in chat
(2026-08-23 22:14 CDT) with five required additions: transcript/wire equivalence, an explicit
mixed-turn design, a malformed-only-turn fallback, terminal `finish` results that never read
`run finished` when the run did not finish, and the full contract update. v2 folded in a
three-lens red-team with adversarial verification (25 agents, 2026-08-23 22:33 CDT): 1 Blocker
(§4 — the `finish` result was left provisional on four run-ending paths that leave the tool loop
before verify), 12 Importants and the Minors that survived, each marked *(v2)* where it changed
the design. v3 folds in a two-agent closure/consistency pass on v2 (22:46 CDT): 1 Blocker (§4/§6.1
— the `KeyboardInterrupt` handler sits outside the loop, so the turn would flush the provisional
string before `finish()` resolved it), the true count of delivery/append sites, the `verify.via`
writer, and five Minors, marked *(v3)*. **v4 — approved by the owner (2026-08-23 22:53 CDT)
with four amendments folded, marked *(v4)*:** the equivalence claim narrowed for `trim_messages`
and the preview cap (§6.4); turn close made atomic under the lock (§6.1); safe Markdown rendering
of `follow_up` through the existing fence helper with an adversarial test (§6.3, §9.14); the
`run_start.config.stall_turns` reconstruction claim removed (no such field exists). Placeholder
wording and turn-granularity flushing accepted as stated.
**Origin:** issue #60, milestone **1.0.0 — contract freeze**. Evidence: `#48` soak rows
`F4b-round2-dev` (leg A2), `F5-trunc2048-dev-r1` (leg B2), `F3v2-run-dev` (leg B3);
`docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md`.
**Parent specs:** `2026-08-20-v1rc-large-writes-atomic-ollama-design.md` (§1.3 truncation
recovery, §1.5 `finish_reason`), `2026-08-19-tools-context-timeouts-design.md` (§4.3 timeout
nudge), `2026-08-18-run-evidence-and-review-loop-design.md` (§4 `--verify`).
Ships in **dirtywork 1.0.0**. Stdlib-only, Python 3.9 floor, `schema_version` stays 2 — every
change below is a new sparse field, a new documented enum value, or a new documented string value
of an existing field; never a removed or renamed one.

## Purpose

Every message the harness authors on the model's behalf must land in a position that every chat
template the runner talks to will render. Today five harness follow-ups can land as a `user`
message directly after a `tool` result, and Mistral-family templates (Devstral Small 2 on
LM Studio, the documented second worker) reject that with HTTP 400 → `model_error`. After this
spec the runner's neutral history is legal for strict templates *by construction*; the `finish`
tool result is honest on every path the run can take; and every tool and assistant message is
recorded as the model was sent it on the turn it was produced, within the caps §6.4 states
(user-carried harness text is recorded by kind, as today).

## 1. The failure (facts, measured 2026-08-23)

### 1.1 The template rule

Extracted from the Devstral Small 2 GGUF (`tokenizer.chat_template`). Lines 9-25 slice off a
leading `system` message (`loop_messages = messages[1:]`); lines 44-46 then check:

```
{%- if message.role == 'user' or (message.role == 'assistant' and (tool_calls absent or empty)) %}
    {%- if (message['role'] == 'user') != (ns.index % 2 == 0) %}
        {{- raise_exception('After the optional system message, conversation roles must
            alternate user and assistant roles except for tool calls and results.') }}
```

Only `user` messages and assistant messages **without** tool calls are counted; `tool` messages
and tool-calling assistant turns are invisible to the parity check. A `user` message is therefore
legal only when the last *counted* message is a plain (tool-call-free) assistant reply. Line 82
additionally rejects an assistant message whose content is `''`/none and has no tool calls.

LM Studio preprocesses before rendering: it **drops** an assistant message whose content is empty
and has no tool calls, and **merges** consecutive `user` messages (probe shapes S14/S15). That
preprocessing is LM Studio's, not the template's; a `llama.cpp`/vLLM server rendering the same
template would reject both shapes outright. The runner must not rely on it.

### 1.2 Probe matrix (17 shapes, `max_tokens=1`, live)

| # | history shape | Devstral | Qwen3-coder |
|---|---|---|---|
| S1 | `assistant(finish)` → `tool` → `user` — verify feedback | **400** | ok |
| S7 | `assistant(text + finish)` → `tool` → `user` | **400** | ok |
| S11 | `assistant(bash, bash)` → `tool` → `tool` → `user` — timeout/stall/malformed nudge | **400** | ok |
| S16 | `assistant(bash)` → `tool` → `assistant("")` → `user` — the F5 truncation shape | **400** | ok |
| S10 | S1 buried earlier in the history | **400** | ok |
| S4 | feedback carried inside the `tool` result | ok | ok |
| S17 | `tool` → `assistant("[placeholder]")` → `user` | ok | ok |
| S2/S8/S9/S14/S15 | empty/whitespace assistant, user→user (LM Studio preprocessing) | ok | ok |

### 1.3 Where the runner produces the rejected shapes

All in `dirtywork/runner.py` (line numbers at `b94dec9`):

| follow-up | site | shape today |
|---|---|---|
| `--verify` feedback after `finish()` | `:830-838` | `tool(finish)` → `user` |
| timeout nudge (`TIMEOUT_NUDGE`) | `:858-863` | `tool` → `user` |
| stall nudge (`STALL_NUDGE`, via `check_progress`) | `:851-863` | `tool` → `user` |
| malformed-entry nudge | `:854-863` | `tool` → `user`, or `assistant("")` → `user` on a malformed-only turn |
| `truncated` / `empty` nudge after an empty reply | `:728-738` | `assistant("")` → `user`; fatal when the prior message is a `tool` result |

The issue names three triggers; the class has five. Two follow-ups are already legal and stay as
they are: verify feedback after a **plain-answer** completion (`:721-728`, `assistant(text)` →
`user`) and the `text_tool_call` nudge (the reply has text, so it is a counted assistant turn).

`providers/anthropic.py:46-104` already normalizes this class for the Anthropic wire (merges user
text into the preceding turn, drops empty replies); `providers/openai_compat.py:87-103` passes
history through verbatim, and the Ollama provider subclasses it. `dirtywork resume` does not
rebuild history from the transcript (it builds a fresh task from a rendered tail), so the runner is
the only producer of chat history.

## 2. The invariant

Two rules, enforced by the runner on the neutral history it hands every adapter:

**R1 — a harness follow-up never directly follows a tool result.** On a turn with at least one
addressable tool call, every harness follow-up rides on a tool result of *that turn* (§3). On a
turn with none, the follow-up is the next `user` message, which is legal because the preceding
assistant entry is a counted, non-empty one (R2).

**R2 — an assistant history entry is never droppable.** An entry with no addressable tool call
and no non-whitespace text is stored with placeholder content (§5), so no template or server
preprocessing can delete it and pull a following `user` message up against a `tool` result.

Corollary the runner already documents at `:582-586` and keeps: history never carries two
consecutive `user` messages.

The adapters do not change. The Anthropic adapter's merge/drop path remains as defence in depth;
it no longer fires on runner-produced histories, and its tests stay.

## 3. Carriers

A **carrier** is the history message whose content a follow-up is appended to. When a follow-up is
attached to a tool message its wire content becomes `result + "\n\n" + follow_up`; the tool's own
result text is never altered, and the joiner is the existing `_join_nudges` separator.

| follow-up | carrier on a turn with addressable calls | carrier otherwise |
|---|---|---|
| verify feedback after `finish()` | the terminal call's **result** — the result *is* the feedback (§4); it is not a `follow_up` | n/a — `finish` is a call |
| timeout nudge | `follow_up` of the turn's **last** addressable tool result | n/a — a timeout is a call |
| stall nudge | `follow_up` of the turn's last addressable tool result | next `user` message (text or malformed-only turn) |
| malformed-entry nudge | `follow_up` of the turn's last addressable tool result | next `user` message |
| `truncated` / `empty` / `text_tool_call` nudge | n/a — these are text-reply kinds | next `user` message |
| verify feedback after a plain answer | n/a | next `user` message (unchanged) |

Composition order within one `follow_up` is the existing order and does not change: on the
verify-feedback path only the timeout nudge can accompany the feedback (stall and malformed are
not evaluated on that path today; `:830-838` returns before `check_progress`); on the ordinary
path `malformed, timeout, stall`. Nothing else about *when* a nudge is issued or counted changes —
only where its text lands and (§6.2) that the transcript now says where.

**"Last addressable tool result"** means the tool message appended for the last element of
`tool_calls` (the id-bearing calls, in the provider's order) on the current turn — whatever tool it
was, including a terminal call and including a *non-terminal* `finish` (§4). A carrier is always
chosen from the current turn's own tool messages, which the runner tracks in a per-turn list; a
follow-up can never touch a message from an earlier turn. *(v2)* Worked examples:

| turn | terminal result | `follow_up` |
|---|---|---|
| `[finish]`, verify fails, round left, no timeout | feedback | — |
| `[bash(timeout), finish]` | feedback | on `finish`: `TIMEOUT_NUDGE` |
| `[finish, bash(timeout)]` | feedback | on `bash`: `TIMEOUT_NUDGE` |
| `[bash(timeout), finish, bash(timeout)]` | feedback | on the second `bash` only |
| `[finish, bash(timeout)]`, verify **passes** | `run finished` | none — the run ends; a nudge is emitted only on a turn that continues (unchanged) |

*(v2, v3)* **One delivery function.** Every site that appends harness text to history calls one
closure, `deliver(text, nudge_records)`: it picks the carrier (the per-turn list's last tool
message, else a new `user` message), sets `follow_up` on the carrier's buffered `tool_result`
record (§6.1), and stamps `via` on every nudge record it was handed. There are **four** callers
*(v3)*: the text-reply nudge path (`:737` — `truncated`/`empty`/`text_tool_call` plus a stall
record; the per-turn list is empty, so `deliver` picks the `user` carrier and stamps
`via="user"`), the plain-answer verify feedback (`:728`, `user` carrier), the finish-path timeout
nudge (`:834-837`, the feedback itself being the `result` per §4), and the ordinary tool-turn
path (`:853-863`). `check_progress` returns its buffered stall-nudge record alongside the text so
the caller can hand it to `deliver`; `check_verify` returns the buffered `verify` record so the two
feedback sites can stamp `verify.via` (`finish_result` / `user`) *(v3)*. Nothing else in `run()`
appends to or rewrites history content, except `resolve_finish` (§4).

## 4. The `finish` result

*(v2)* **Terminal call** means a call that reached the `spec.terminal` branch with parsed
arguments (`:779-782` today). A `finish` whose arguments were malformed or truncated takes the
ordinary `ERROR:`/truncated path, is not terminal, produces an ordinary tool result that is never
rewritten, and can be a carrier like any other call.

`finish` is executed by verifying. Its tool message is appended in its original position with the
**provisional** content `run not finished: verify did not run`, and its buffered `tool_result`
record holds the same string. One resolver, `resolve_finish(text)`, rewrites *every* terminal
record and history message of the current turn to `text`; it is called from the three places
listed under *Where resolution happens* below, and the provisional string can therefore only
reach disk when none of them runs (an exception the runner does not handle):

| when | resolved `result` (wire and transcript) | run |
|---|---|---|
| no `--verify`, or verify passed | `run finished` | ends `completed` |
| verify failed, a fix round remains | `VERIFY_FEEDBACK` text, exactly as today (the timeout nudge, when one applies, is a `follow_up` per §3, never part of `result`) | continues |
| verify failed, no fix round remains (`--verify-rounds 0`, or the last round) | `run not finished: verify failed (exit <code>); no fix rounds remain` | ends `verify_failed` |
| verify raised `BudgetExceeded` / `SandboxError` | `run not finished: verify could not run (<reason>)` | ends `budget_exceeded` / `sandbox_error` |
| *(v2)* the turn ended before verify ran — a later call raised `BudgetExceeded`/`SandboxError`, a later call's failure tripped the `FailureTracker` abort, or `KeyboardInterrupt` (including inside `run_verify`'s `sandbox.bash`) | `run not finished: <status>` (e.g. `run not finished: interrupted`) | ends with that status |
| *(v2)* an exception the runner does not handle propagates out of the turn | provisional string stands (`run not finished: verify did not run`) — true, and the only case it is written | no runner-written `run_end`; `turn()`'s `finally` flushes the turn and the CLI's `_fail_run` (`__main__.py:633-660`) then writes its failure `run_end` immediately, as today *(v3)* |

`<code>` is `verify_state["exit_code"]`, rendered `unknown` when it is `None`. The four terminal
strings are never sent to a model (the run ends); they exist so the transcript never claims a
finish that did not happen. `VERIFY_FEEDBACK` keeps its wording and its "Fix the problem, then
call finish(summary=...) again" closing line; `verify_state`, the `verify` transcript event's
existing fields and `run_end.verify` are unchanged.

*(v2)* **Where resolution happens.** (a) The feedback path (`:830-838`) calls
`resolve_finish(feedback)` before `deliver`. (b) `check_verify`'s terminal branches call it with
the "no fix rounds remain" / "could not run" strings. (c) The `finish(status, final)` closure
(`:554`, the single exit point for every ended run) resolves any *still-unresolved* terminal
record of the current turn to `run finished` when `status == "completed"` and to
`run not finished: <status>` otherwise — so paths that never reached `check_verify` are covered
without enumerating them. A record is "unresolved" while its content is the provisional string.

*(v3)* **Resolution must precede the flush.** Today's `KeyboardInterrupt` handler wraps the whole
`while` loop (`:678`, `:864-865`), outside any turn; with `turn()` wrapping each iteration, an
interrupt would flush the provisional string before `finish("interrupted")` ran. So the runner
catches `KeyboardInterrupt` **inside** the per-iteration turn block —
`with transcript.turn(): try: <iteration> except KeyboardInterrupt: return finish("interrupted", "")`
— and `finish()` therefore resolves and flushes (§6.1) before the block exits. The outer handler
stays only for an interrupt that lands between iterations, where no terminal record can be
pending. Residual, stated: an interrupt inside `finalize()` or the `run_end` write propagates as
today.

**Order of operations in a turn** (unchanged from today, now stated): every addressable call
executes in order, including calls after `finish` (`tests/test_runner.py:623`); the `finish` tool
message keeps its original position; resolution rewrites it in place. Wire order of tool messages
equals call order, as the OpenAI and Anthropic protocols require.

**Multiple terminal calls in one turn:** the last one's summary wins (as today); every terminal
call's `result` is resolved to the same string. A `follow_up` attaches only to the turn's last
addressable call, which may or may not be one of them.

*(v2)* **Transcript cap.** `FINISH_SPEC` moves to `Caps(transcript="full")` — the mode exists
(`toolspec.py:330-331`) and is unused. The finish result is always harness-authored and bounded
(`VERIFY_OUTPUT_CHARS` 4000 + the template + the operator's command), so the transcript records it
byte-for-byte; under the default `preview` cap a real test-suite failure (~4200 chars) would have
been truncated in exactly the F4b shape this spec exists for. Model/tool-authored results keep the
2000-char preview.

## 5. Placeholder for a droppable reply

`EMPTY_REPLY_PLACEHOLDER = "[empty reply]"`. Applied when the assistant entry being appended has
no addressable tool call and its text is empty after `.strip()`. That covers the `empty` kind, the
`truncated` kind with empty text, and a malformed-only turn (`tool_calls == []`,
`malformed_entries` non-empty, no text — this turn takes the tool path at `:715` because
`resp.tool_calls` is non-empty, so it gets the placeholder and the malformed nudge, never a
`truncated` nudge). Think-only replies (`<think>…</think>` text) and `text_tool_call` replies have
text and get no placeholder.

*(v2, v3)* **One append function.** All three assistant-append sites (`:718` tool path, `:723`
answer, `:729` nudged text reply) call one closure, `append_assistant(text, tool_calls)`: it
applies the placeholder rule, writes the `assistant` event (with `placeholder` when applied, §6.2)
and appends the history entry.

The placeholder is the entry's `content` in history (so every adapter sends it). The nudge that
follows it is a `user` message, as today. The placeholder is bracketed harness text a model could
imitate; the soak rerun (§10) is where that is observed, and the wording is a single constant so
it can change without a schema change. *(v2)* Think-only replies rely on the template rendering
prior-turn reasoning as content; Devstral's does (template lines 82-96, verbatim), and a template
that strips it from history would turn such an entry into an empty counted turn — a residual
outside this spec's target, recorded here so it is not mistaken for a gap.

## 6. Transcript: what was sent is what is recorded

The transcript is append-only and tool results are written before follow-ups are composed
(`:809-815` vs `:819-863`), so recording the carrier's final content requires the turn's events to
be written when the turn's wire messages are final.

### 6.1 Turn-scoped write buffer

`Transcript` gains three members and nothing else:

- `turn()` — a context manager. Inside it, `write()` builds the record exactly as today — **`ts`
  is stamped at `write()` time** *(v2)*; only the disk write is deferred — appends it to an
  in-memory list in write order, and **returns the record dict** so the runner may amend it until
  the block exits. Outside a turn `write()` writes immediately and returns `None`, as today.
  Exit — normal or by *any* exception, `KeyboardInterrupt` included (`try/finally`) *(v2)* —
  flushes every buffered record in order and clears the buffer. Amending a dict after its turn
  has flushed changes nothing on disk *(v2: the v1 "raises" clause is dropped; a plain dict cannot
  raise, and the equivalence tests in §9 are the guard)*.
- `flush()` — writes whatever is buffered now, in order, and leaves the turn open *(v2)*.
- `close()` — flushes, then closes *(v2)*.

*(v2)* **Threads.** The docker sandbox's watchdog thread writes `sandbox_reset`
(`sandbox/docker.py:849-853`, called from `watchdog.py:117-119` every 5 s while a `bash` call is in
flight). `write()` and `flush()` share one `threading.Lock` that covers both the buffer and the
physical `_fh.write`/`_fh.flush` *(v3: flushes are small and nothing long runs under it, so one
lock also rules out two threads interleaving partial lines on the file handle — an exposure that
exists today and would otherwise survive)*. A write from any thread while a turn is open is
buffered (it lands in order with the turn); a write while no turn is open goes straight to disk
under the same lock. No record can be lost between the flush loop and the clear.

*(v4)* **Atomic close — a `turn()` invariant.** Leaving a turn is one critical section under the
lock: flush every buffered record, then mark the turn closed. A write from another thread
therefore either takes the lock before the close (it is buffered and flushed with the turn) or
after it (it sees no open turn and writes directly). There is no instant at which a record can
enter the buffer and then be cleared unflushed. Every record written is on disk exactly once.

*(v2)* **`finish()` flushes before the export.** The `finish(status, final)` closure resolves the
turn's terminal records (§4c), calls `transcript.flush()`, *then* `finalize()` (in docker mode the
full export, minutes at worst), then writes `run_end`. The turn's evidence is therefore on disk
before the export starts, and `run_end` is still the last record (it is the only thing in the
buffer when the turn exits).

**Trade-off, stated.** Events reach disk at turn granularity rather than per line. `tail -f`
still works, one turn at a time; every orderly end flushes; a hard kill (`SIGKILL`, OOM, and
`SIGTERM` — no handler is installed, so it behaves the same) loses at most the current turn's
events, where today it loses at most the current line. `dirtywork resume` reads the transcript
only after the run has ended, so its view is unchanged. The runner wraps each iteration of its
main loop in `turn()`; `run_start` (before the loop) and the CLI's own failure `run_end` are
immediate.

### 6.2 New sparse fields and values (schema v2, additive)

| event | field | type | when present |
|---|---|---|---|
| `tool_result` | `follow_up` | string | this result carried harness text on the wire; the exact joined text appended (uncapped — harness-authored and bounded) |
| `tool_result` (`tool: "finish"`) | `result` | string | unchanged field, new values: the strings of §4, recorded in full (`transcript="full"`) |
| `assistant` | `placeholder` | string | the entry was stored with placeholder content (§5); the value sent, currently `[empty reply]`. `text` stays the model's actual text (`""`) |
| `nudge` | `via` | string | from 1.0: `tool_result` or `user` — which carrier this nudge rode on. **Sparse** *(implementation ruling, 2026-08-24)*: absent when the run ended on that same turn before the text was delivered (the third empty-reply strike → `model_error`; a stall verdict on a text turn → `stalled`) — the event is still written, as in 0.9, so nudge counts stay comparable with the #48 soak ledger |
| `nudge` | `kind` | string | *(v2)* new value `malformed_entry`: the "N of your tool calls were malformed" text, which today is delivered but never transcribed (`:853-857`). Written where the text is composed, on both carriers. `bench.NUDGE_KINDS`, `tests/test_transcript_schema.py`'s kinds list and the soak `nudges` column (which sums over `NUDGE_KINDS`) gain it; `bench.py:230-232` already buckets unknown kinds, so old readers are safe |
| `verify` | `via` | string | only when feedback was delivered for another round: `finish_result` or `user` |

Wire reconstruction for a tool message is `result + "\n\n" + follow_up` (`result` alone when no
`follow_up`), exact for `finish` and for any result under the 2000-char preview cap; for an
assistant message it is `placeholder` when present, else `text` (exact under the 64 000-char cap).

### 6.3 Evidence fields and `runs show`

`run_end.last_tool_result` / `run.json` keep recording the tool's own result (the same value
`note_last_tool_result` receives today, before any follow-up is attached); harness text is
accounted for in `nudge` events (now including `malformed_entry`) and the per-kind nudge counts,
not in tool evidence.

*(v2)* Both renderers in `dirtywork/runs.py` change, and both tolerate absent keys (old
transcripts):

- plain timeline (`_timeline_line`, `:285-306`): a `tool_result` line gains the suffix
  ` +follow_up` when the key is present; an `assistant` line gains ` (sent as: [empty reply])`
  when `placeholder` is present.
- Markdown export (`_md_event_lines`, `:354-375`): after a `tool_result`'s `<details>` block,
  a `> **harness → model:**` callout line followed by the follow-up text as a fenced block through
  the existing `_md_block`/`_md_fence` helpers (`:327-337` — the fence is longer than any backtick
  run inside the text), **never** as raw blockquoted text *(v4: verify output and the operator's
  command can contain newlines, backticks, `>`, headings or `</details>`, any of which would
  corrupt a raw blockquote)*; after an assistant line, `(sent as: <placeholder>)` through
  `_md_inline`. *(v3)* A `finish` result is exempt from `_md_trim`'s
  `MD_RESULT_CHARS` (`:319`, `:366`) for the same reason it is exempt from the transcript preview
  cap — otherwise the review artifact would reproduce the F4b truncation the transcript no longer
  has.
- `_tool_result_outcome` (`:266-282`): a `finish` result other than `run finished` renders
  `[not finished]` instead of `[ok]` in both renderers.

### 6.4 Scope of the equivalence claim *(v2, v4)*

The transcript records each tool and assistant message **as the model was sent it on the turn it
was produced**, by the §6.2 rule, with three stated limits:

- **Preview cap.** A non-`finish` tool result is recorded through the 2000-char preview
  (`Caps.transcript="preview"`, unchanged); its `follow_up` is exact, its `result` is exact only
  under the cap. `finish` results are exact (`full`).
- **Trimming.** On later turns `trim_messages` (`runner.py:428-446`) replaces the oldest tool
  results in *history* with `TRIM_MARKER`; the transcript keeps the original event and does not
  record per-message which results were trimmed on which turn — only `run_end.trimmed_turns`
  (turns on which trimming happened) says that it occurred. A trimmed result loses its
  `follow_up` along with its text on the wire (§11). So the transcript is exact for what the
  model saw *when the result was produced*, not for what a later request re-sent.
- **User-carried text.** Recorded by `nudge.kind` (+`via`) only; the stall count `n` and the
  malformed count are not transcribed, as today. Plain-answer verify feedback remains summarized
  by the `verify` event and `run_end.verify` (last round's tail only), as today. No `user` event
  is added.

The "Wire shape" subsection §8 adds to `docs/transcript-schema.md` states these three limits in
the same words.

## 7. Providers

No serializer changes. Legality is asserted on the neutral history by tests, through both
serializers, with **one** helper *(v2)*: `assert_strict_template_legal(history)` in
`tests/provider_doubles.py`, called from every double that drives the runner — `FakeProvider.chat`
(`tests/test_runner.py:61`, and its `_ServerProvider` subclass) and `DictProvider.chat`
(`tests/provider_doubles.py`, used by `test_main.py` and `test_transcript_schema.py`). It
serializes via `_to_openai_messages`, drops a leading `system` message (template lines 9-25), and
checks the §1.1 rule literally: counted-message parity starting with `user`; every assistant
message has non-whitespace content or a non-empty `tool_calls` (stricter than template line 82,
which only rejects `''`/none — intentional, it is the R2 rule); no `user` immediately after `tool`.
Every existing runner-driven test thereby becomes an invariant test; no existing test constructs a
shape that fails it after the change (`test_transcript_schema.py`'s `_NudgingProvider` produces the
S16 shape today and becomes legal). The Anthropic merge-path tests
(`tests/test_provider_anthropic.py:150-191`) call the client directly and are untouched.

- The Anthropic serializer's output must satisfy the existing `_assert_alternating` helper
  (`tests/test_provider_anthropic.py:140`), and for a mixed turn its single `user` message must
  carry the `tool_result` blocks in call order.
- The Ollama provider shares `_to_openai_messages`; its contract test asserts the same helper.

A `live`-marked test replays the runner-produced histories for the three #60 shapes (verify
feedback after `finish`, timeout nudge on a tool turn, empty reply after a tool turn) against the
loaded Devstral with `max_tokens=1` and expects HTTP 200; it skips when the model is not listed.

## 8. Docs and contract

Every passage that states the old shape, the old `finish` result, or per-line flushing is
rewritten; nothing is left implying "the next user message" or `run finished` unconditionally:

- `docs/operating.md:74-89` (verify): the failure comes back "as the `finish` call's result, or as
  a message when the worker answered in prose"; `:100-106` (timeout): the nudge "is appended to
  the turn's last tool result"; `:179-183` (`runs show --markdown`): the new callouts.
- `docs/transcript-schema.md`: `:3-5` — "`tail -f` friendly — each line is flushed immediately"
  becomes "flushed at the end of every turn (`run_start` and a CLI-failure `run_end` immediately);
  `tail -f` shows a turn at a time" *(v2)*; `assistant` table (+`placeholder`); `tool_result`
  table (+`follow_up`) and `:74` "All built-in tools declare `preview`" → "all but `finish`, which
  declares `full`" *(v2)*; the `finish` paragraph at `:77-80` (the §4 values); the `nudge`
  section at `:82-93` (carrier rule, `via`, `malformed_entry`; the kinds list there already has
  `timeout`); `verify` table (+`via`); `:263-273` (`runs show` callouts, and `:269`'s "the same
  2000-char preview the transcript itself applies" → "the transcript's preview cap; `finish`
  results are shown in full" *(v3)*); a short **"Wire shape"** subsection stating R1/R2, the
  §6.2 reconstruction rule and the three §6.4 limits *(v4)*.
- `dirtywork/runs.py:319` comment (`# the transcript's own preview cap`) *(v3)*.
- Dated session logs under `docs/` (`2026-08-14-building-localagent.md:70`, `:181` say "flushed
  per line") are historical record and are left as written *(v3)*.
- `docs/machine-contract.md`: `:326-327` ("watch a live run with `tail -f`" — one turn at a time)
  *(v2)*; `:339-340` (`nudge` line: kinds gain `timeout` — that list omits it today — and
  `malformed_entry`; `via`); `:356` (the finish `tool_result` is `run finished` only when the run
  completed; otherwise one of the §4 strings) *(v2)*; the `--verify` bullet at `:90-96` (delivery
  form); a one-line statement of R1/R2 under the transcript-events paragraph.
- `dirtywork/transcript.py:10` class docstring ("flushed per line so `tail -f` works") *(v2)*.
- `README.md:196` already says "sent back with a one-line nudge" — accurate, unchanged.
- `tests/test_transcript_schema.py`: `placeholder` in the assistant field list, a `tool_result`
  field list including `follow_up`, `malformed_entry` in the kinds list *(v2)*.
- 1.0.0 release notes (GitHub release body; the repo keeps no CHANGELOG file): the message-shape
  change, the `finish` result values and `full` cap, the new fields and kind, the turn-granularity
  flush.

## 9. Tests

Baseline `1237 passed, 1 skipped, 20 deselected` (`/usr/bin/python3 -m pytest -q`, `b94dec9`);
the count only rises. Beyond the invariant helper in §7 (which runs inside every runner-driven
test), new tests cover the following. *(v2)* Scenarios shared between tests are module-level
builders in `tests/test_runner.py` (e.g. `_scenario_verify_feedback_on_finish()` returning the
provider, sandbox and runner kwargs), so test 12 iterates them instead of copying setups.

1. **Verify feedback rides on `finish`** — `assistant(finish)` → `tool(finish, content ==
   VERIFY_FEEDBACK…)`; no `user` message follows; `verify.via == "finish_result"`;
   `tool_result.result` equals the wire content byte-for-byte with a **3000-char verify tail**
   (exercises the `full` cap) *(v2)*.
2. **Mixed turns** — the five rows of the §3 example table, each asserting the terminal `result`,
   which event carries `follow_up`, wire order equal to call order, and that the later call
   executed; plus verify-passing `[finish, bash(timeout)]` → no `follow_up` anywhere,
   `run finished`, `completed` *(v2)*.
3. **Multiple terminal calls in one turn** — both `result`s resolved to the same string; a
   `follow_up` only on the last addressable call.
4. **Malformed-only turn** — no addressable call, empty text: the assistant entry carries the
   placeholder, the `malformed_entry` nudge is a `user` message with `via == "user"`, and the
   previous turn's tool message is byte-equal before/after **and its `tool_result` event has no
   `follow_up` key** *(v2)*. Variants *(v2)*: `finish_reason == "length"` (placeholder + malformed
   nudge, no `truncated` nudge); the third `malformed_entry` strike (run ends `model_error` after
   the placeholder entry was recorded; no nudge, no `user` message follows).
5. **Malformed-only turn with text** — no placeholder, nudge as `user`.
6. **Stall / malformed nudge on a tool turn** — appended to the last result; `nudge.via ==
   "tool_result"` for both kinds; one `follow_up` per turn holding the joined text.
7. **Empty reply after a tool turn (F5 shape)** — placeholder on the assistant entry,
   `assistant.placeholder` recorded, nudge as `user`, helper passes.
8. **Verify failure with `--verify-rounds 0`** — status `verify_failed`, `finish` result is the
   §4 "no fix rounds remain" string, no feedback delivered, no `via` on `verify`.
9. **Verify failure on the last round** — same string; **verify `SandboxError` /
   `BudgetExceeded`** — the "could not run" string, statuses unchanged; *(v2)* **terminal exits
   before verify**: `[finish, write_file]` with `BudgetBustingSandbox` (`:608-614`) →
   `run not finished: budget_exceeded`; `[finish, bash]` with a sandbox that raises
   `KeyboardInterrupt` → `run not finished: interrupted`; `[finish, unknown_tool × 3]` →
   `run not finished: model_error`; `finish` + `--verify` whose `sandbox.bash` raises
   `KeyboardInterrupt` → `run not finished: interrupted`. In every case no `tool_result` in the
   transcript reads `run finished`.
10. **Transcript/wire equivalence** — for every turn of a multi-turn scenario, every wire tool
    message content equals `event.result + ("\n\n" + event.follow_up if present)` (non-finish
    results kept under the 2000-char preview, finish results including a >2000-char feedback),
    and every wire assistant content equals `event.placeholder or event.text` (texts under
    `MAX_ASSISTANT_TEXT_CHARS`); compared against the provider's captured copy of each request.
11. **Turn buffer** — `Transcript.turn()` preserves order across `guardrail_block`/`tool_result`/
    `nudge`/`verify`; a record amended inside the block is written amended; an amendment after
    flush does not reach disk; `ts` is write-time and non-decreasing within a turn; a
    `KeyboardInterrupt` raised inside the block flushes before propagating; a record resolved
    inside an `except` handler within the block is written resolved *(v3)*; `flush()` mid-turn
    writes and keeps the turn open; a write from another thread during `turn()` is on disk after
    exit; `write()` outside a turn is immediate; `close()` flushes *(v2)*; *(v4)* **atomic
    close** — a thread writing in a tight loop while the main thread exits the turn (repeated
    over many iterations): every record it wrote is on disk exactly once, none lost, none
    duplicated, and the buffered ones precede any written after the close.
12. **All three providers** — the shared scenarios of tests 1, 2, 4 and 7 serialized through
    `_to_openai_messages` (OpenAI and Ollama) pass the helper, and through the Anthropic
    serializer pass `_assert_alternating` with `tool_result` block ids in call order
    (e.g. `['f1', 'b1']`) *(v2)*.
13. **Existing tests updated, not deleted** *(v2, complete list)* —
    `test_finish_tool_ends_run_after_other_calls_in_turn` (`:623`) still asserts `run finished`
    (verify absent); `test_empty_reply_is_nudged_not_completed` (`:774`) asserts
    `{"role": "assistant", "content": EMPTY_REPLY_PLACEHOLDER}` and `assistant.placeholder`;
    `test_runner_stalled_status_after_idle_turns` (`:929-945`) asserts the stall text as
    `follow_up` on the `read_file` result, `via == "tool_result"`, no trailing `user`;
    `test_one_timeout_nudge_per_turn_even_with_two_timeouts` (`:1518-1537`) asserts no `user`
    message after the tool messages, `b2`'s content `== timeout_result + "\n\n" + TIMEOUT_NUDGE`,
    `follow_up` on `b2`'s event only; `test_timeout_nudge_merges_with_the_stall_nudge` (`:1540`)
    asserts the joined `follow_up`; the verify-feedback tests at `:1321` and `:1589` assert the new
    carrier (`:1589` asserts `result == VERIFY_FEEDBACK…` and `follow_up == TIMEOUT_NUDGE` on the
    finish event); `:1345` has no shape assertion and is unchanged.
14. **`runs show`** — one test per renderer for `follow_up`, `placeholder` and `[not finished]`,
    and the Markdown export showing a >2000-char `finish` result untrimmed *(v3)*; old-shape
    events without the keys still render. *(v4)* **Adversarial follow-up:** a `follow_up`
    containing a triple-backtick fence, a line starting `> `, a `# heading`, `</details>` and
    CRLF line ends — the export has balanced fences (`_balance_fences` adds nothing), the text
    appears verbatim inside one fenced block, and no line of it is rendered as a heading,
    blockquote or HTML.
15. **Schema coverage** *(v2, v3)* — a second `test_transcript_schema` scenario: a turn whose
    wire body carries a `tool_calls` entry without an `id` (→ `malformed_entry` through
    `parse_chat_response`), then a bash that times out + `finish` + failing verify with
    `--verify-rounds 1`, so `follow_up`, `verify.via`, `nudge.via` and `malformed_entry` are all
    emitted and doc-token-checked (`[not finished]` is a rendering, asserted in test 14). The
    sandbox double `_TimeoutThenFailingVerifySandbox` (`tests/test_runner.py:1574`) moves to
    `tests/provider_doubles.py`, the shared-doubles module, so both test files import it.
16. **Live** — the §7 replay against Devstral.

## 10. Acceptance evidence (necessary, not sufficient)

With §9 green, rerun through the #48 soak tooling: `F4b-round2-dev`, `F5-trunc2048-dev-r1`,
`F3v2-run-dev` on Devstral — expected: no `model_error`, `run_end.error` empty, and F4/F3/F5
detectors fire as designed; plus their Qwen counterparts (`F4b-round2-qwen` must still show
`F4(passed=True,rounds=2)`) to show the tool-result carrier did not cost Qwen the retry. Rows are
appended to the ledger with the soak's usual columns; the placeholder-imitation question of §5 is
answered by reading those transcripts.

*(Implementation ruling, 2026-08-24.)* The bar above conflated "no #60 failure" with "run
succeeds". Measured: zero `Error rendering prompt`/"roles must alternate"/400 across all six
reruns; `F4b-round2-dev` 400 → `completed`; `F3v2-run-dev` 400 → 18 turns past the timeout nudge
(then #67's `[TOOL_CALLS]`-polluted tool names); both F5 rows end on the 2048-token provoker's
"3 consecutive empty_reply failures" — identically to the Qwen control at `b94dec9` (#65/#66).
The #60-specific bar (no template rejection; every follow-up carried legally; Devstral passes each
formerly fatal point) is met; the literal "no `model_error`" bar is not, for causes outside this
spec. The ledger says both.

## 11. Out of scope

- Adapter-level normalization for OpenAI-compatible backends (approach A/C) — rejected in chat.
- Changing the preview cap for model/tool-authored results, the transcript schema version, or
  resume's history rebuild.
- Per-model template sniffing: the runner produces one shape for every backend.
- `trim_messages` behaviour: a trimmed tool result loses its `follow_up` along with its result
  (both replaced by `TRIM_MARKER`), which is the existing rule for old tool content.
- A `SIGTERM` handler (would turn a `docker stop`-style kill into an orderly flush); noted in
  §6.1, not added here.
- Transcribing user-carried harness text verbatim (a `user` event); §6.4 states what is and is
  not reconstructable.
