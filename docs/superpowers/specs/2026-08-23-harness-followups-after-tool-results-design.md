# Harness follow-ups after tool results — carriers that every chat template accepts (#60)

**Date:** 2026-08-23
**Status:** Design v1 — approach B (runner-level carriers) chosen by the owner in chat
(2026-08-23 22:14 CDT) with five required additions: transcript/wire equivalence, an explicit
mixed-turn design, a malformed-only-turn fallback, terminal `finish` results that never read
`run finished` when the run did not finish, and the full contract update. Red-team pending.
**Origin:** issue #60, milestone **1.0.0 — contract freeze**. Evidence: `#48` soak rows
`F4b-round2-dev` (leg A2), `F5-trunc2048-dev-r1` (leg B2), `F3v2-run-dev` (leg B3);
`docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md`.
**Parent specs:** `2026-08-20-v1rc-large-writes-atomic-ollama-design.md` (§1.3 truncation
recovery, §1.5 `finish_reason`), `2026-08-19-tools-context-timeouts-design.md` (§4.3 timeout
nudge), `2026-08-18-run-evidence-and-review-loop-design.md` (§4 `--verify`).
Ships in **dirtywork 1.0.0**. Stdlib-only, Python 3.9 floor, `schema_version` stays 2 — every
change below is a new sparse field or a new documented value, never a removed or renamed one.

## Purpose

Every message the harness authors on the model's behalf must land in a position that every chat
template the runner talks to will render. Today five harness follow-ups can land as a `user`
message directly after a `tool` result, and Mistral-family templates (Devstral Small 2 on
LM Studio, the documented second worker) reject that with HTTP 400 → `model_error`. After this
spec the runner's neutral history is legal for strict templates *by construction*, the `finish`
tool result is honest, and the transcript records exactly what the model was sent.

## 1. The failure (facts, measured 2026-08-23)

### 1.1 The template rule

Extracted from the Devstral Small 2 GGUF (`tokenizer.chat_template`, lines 44-46):

```
{%- if message.role == 'user' or (message.role == 'assistant' and (tool_calls absent or empty)) %}
    {%- if (message['role'] == 'user') != (ns.index % 2 == 0) %}
        {{- raise_exception('After the optional system message, conversation roles must
            alternate user and assistant roles except for tool calls and results.') }}
```

Only `user` messages and assistant messages **without** tool calls are counted; `tool` messages
and tool-calling assistant turns are invisible to the parity check. A `user` message is therefore
legal only when the last *counted* message is a plain (tool-call-free) assistant reply. Line 82
additionally rejects an assistant message with empty content and no tool calls.

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

A **carrier** is the history message whose content a follow-up is appended to. The wire content of
a tool message becomes `result + "\n\n" + follow_up` when a follow-up is attached; the tool's own
result text is never altered, and the joiner is the existing `_join_nudges` separator.

| follow-up | carrier on a turn with addressable calls | carrier otherwise |
|---|---|---|
| verify feedback after `finish()` | the `finish` call's result — the result *is* the feedback (§4) | n/a — `finish` is a call |
| timeout nudge | the turn's **last** addressable tool result | n/a — a timeout is a call |
| stall nudge | the turn's last addressable tool result | next `user` message (text or malformed-only turn) |
| malformed-entry nudge | the turn's last addressable tool result | next `user` message |
| `truncated` / `empty` / `text_tool_call` nudge | n/a — these are text-reply kinds | next `user` message |
| verify feedback after a plain answer | n/a | next `user` message (unchanged) |

Composition order within one carrier is the existing order and does not change: on the
verify-feedback path `feedback, timeout`; on the ordinary path `malformed, timeout, stall`. A
verify-feedback turn still delivers only the timeout nudge alongside the feedback (stall and
malformed are not evaluated on that path today; `:830-838` returns before `check_progress`).
Nothing else about *when* a nudge is issued, counted or transcribed changes — only where its
text lands.

**"Last addressable tool result"** means the tool message appended for the last element of
`tool_calls` (the id-bearing calls, in the provider's order) on the current turn. A carrier is
always chosen from the current turn's own tool messages, which the runner tracks in a per-turn
list; a follow-up can never touch a message from an earlier turn.

## 4. The `finish` result

`finish` is executed by verifying. Its result is resolved when the turn's verify outcome is
known, and it reads `run finished` only when the run finishes:

| outcome | `finish` result (wire and transcript) | run |
|---|---|---|
| no `--verify`, or verify passed | `run finished` | ends `completed` |
| verify failed, a fix round remains | `VERIFY_FEEDBACK` text, exactly as today — followed by the timeout nudge only when `finish` is also the turn's last addressable call (§3) | continues |
| verify failed, no fix round remains (`--verify-rounds 0`, or the last round) | `run not finished: verify failed (exit <code>); no fix rounds remain` | ends `verify_failed` |
| verify raised `BudgetExceeded` / `SandboxError` | `run not finished: verify could not run (<reason>)` | ends `budget_exceeded` / `sandbox_error` |

`<code>` is `verify_state["exit_code"]`, rendered `unknown` when it is `None`. The three terminal
strings are never sent to a model (the run ends); they exist so the transcript never claims a
finish that did not happen. `VERIFY_FEEDBACK` keeps its wording and its "Fix the problem, then
call finish(summary=...) again" closing line; `verify_state`, the `verify` transcript event and
`run_end.verify` are unchanged.

**Order of operations in a turn** (unchanged from today, now stated): every addressable call
executes in order, including calls after `finish` (`tests/test_runner.py:623`); the `finish`
tool message is appended in its original position with provisional content; after the loop the
verify outcome resolves it in place. Wire order of tool messages therefore equals call order, as
the OpenAI and Anthropic protocols require.

**Multiple `finish` calls in one turn:** the last one's summary wins (as today); *every* terminal
call's result is resolved to the same string, so no `run finished` survives on a run that
continues.

## 5. Placeholder for a droppable reply

`EMPTY_REPLY_PLACEHOLDER = "[empty reply]"`. Applied when the assistant entry being appended has
no addressable tool call and its text is empty after `.strip()`. That covers the `empty` kind, the
`truncated` kind with empty text, and a malformed-only turn (`tool_calls == []`,
`malformed_entries` non-empty, no text). Think-only replies (`<think>…</think>` text) and
`text_tool_call` replies have text and get no placeholder.

The placeholder is the entry's `content` in history (so every adapter sends it) and is recorded on
the `assistant` transcript event (§6). The nudge that follows it is a `user` message, as today.
The placeholder is bracketed harness text a model could imitate; the soak rerun (§10) is where
that is observed, and the wording is a single constant so it can change without a schema change.

## 6. Transcript: what was sent is what is recorded

The transcript is append-only and tool results are written before follow-ups are composed
(`:809-815` vs `:819-863`), so recording the carrier's final content requires the turn's events to
be written when the turn's wire messages are final.

**6.1 Turn-scoped write buffer.** `Transcript` gains a context manager, `turn()`. Inside it,
`write()` appends the record to an in-memory list (in write order) and returns the record; the
runner may amend a returned record until the block exits. On exit — normal or by exception — every
buffered record is written in order, then the buffer is cleared. Outside a turn `write()` behaves
as today. The runner wraps each iteration of its main loop in `turn()`, so `guardrail_block`,
`sandbox_reset`, `tool_result`, `nudge`, `verify` and a `run_end` written from inside the turn keep
their relative order. Amending a record after its turn has flushed is a programming error and
raises.

Trade-off, stated: events reach disk at turn granularity rather than per line. `tail -f` still
works, one turn at a time; an orderly end of any kind (`finish()`, `KeyboardInterrupt`, an
exception) flushes; a hard kill (`SIGKILL`, OOM) loses at most the current turn's events, where
today it loses at most the current line. `dirtywork resume` reads the transcript only after the
run has ended, so its view is unchanged.

**6.2 New sparse fields (schema v2, additive).**

| event | field | type | when present |
|---|---|---|---|
| `tool_result` | `follow_up` | string | this result carried harness text on the wire; the exact text appended (uncapped — it is harness-authored and bounded) |
| `tool_result` (`tool: "finish"`) | `result` | string | unchanged field, new values: the four strings of §4 |
| `assistant` | `placeholder` | string | the entry was stored with placeholder content (§5); the value sent, currently `[empty reply]`. `text` stays the model's actual text (`""`) |
| `nudge` | `via` | string | always, from 1.0: `tool_result` or `user` — which carrier this nudge rode on |
| `verify` | `via` | string | only when feedback was delivered for another round: `finish_result` or `user` |

Wire reconstruction for a tool message is `result + "\n\n" + follow_up` — subject to the
existing, documented `result` preview cap (2000 chars), which this spec does not change.

**6.3 Evidence fields.** `run_end.last_tool_result` / `run.json` keep recording the tool's own
result (the same value `note_last_tool_result` receives today, before any follow-up is attached);
harness text is accounted for in `nudge` events and the per-kind nudge counts, not in tool
evidence. `dirtywork runs show` renders `follow_up` under its tool result as a blockquote in the
existing nudge-callout style (`> harness → model: …`), and renders `placeholder` after the
assistant line as `(sent as: [empty reply])`.

## 7. Providers

No serializer changes. Legality is asserted on the neutral history by tests (§9), through both
serializers:

- `_to_openai_messages` output must satisfy a **strict-template oracle** — a pure function in the
  test tree that implements §1.1 literally: counted-message parity starting with `user`; every
  assistant message has non-whitespace content or a non-empty `tool_calls`; no `user` immediately
  after `tool`. It is the offline stand-in for the Mistral template and is checked on **every**
  request the fake provider receives, in every runner test, not only the new ones.
- The Anthropic serializer's output must satisfy the existing `_assert_alternating` helper
  (`tests/test_provider_anthropic.py:140`).
- The Ollama provider shares `_to_openai_messages`; its contract test asserts the same oracle.

A `live`-marked test replays the runner-produced histories for the three #60 shapes (verify
feedback after `finish`, timeout nudge on a tool turn, empty reply after a tool turn) against the
loaded Devstral with `max_tokens=1` and expects HTTP 200; it skips when the model is not listed.

## 8. Docs and contract

Every passage that states the old shape is rewritten; nothing is left implying "the next user
message" unconditionally:

- `docs/operating.md:74-89` (verify): the failure comes back "as the `finish` call's result, or as
  a message when the worker answered in prose"; `:100-106` (timeout): the nudge "is appended to
  the turn's last tool result".
- `docs/transcript-schema.md`: `assistant` table (+`placeholder`); `tool_result` table
  (+`follow_up`); the `finish` paragraph at `:77-80` (the four results of §4); the `nudge` section
  at `:82-93` (carrier rule, `via`, and the kinds list gains `timeout`, which it already omits);
  `verify` table (+`via`); a short **"Wire shape"** subsection stating R1/R2 and the
  reconstruction rule.
- `docs/machine-contract.md:339` (`nudge` line: kinds incl. `timeout`, `via`); the `--verify`
  bullet at `:90-96` (delivery form); a one-line statement of R1/R2 under the transcript-events
  paragraph.
- `README.md:196` already says "sent back with a one-line nudge" — accurate, unchanged.
- 1.0.0 release notes (GitHub release body; the repo keeps no CHANGELOG file): the message-shape
  change, the `finish` result values, the new fields, the turn-granularity flush.

## 9. Tests

Baseline `1237 passed, 1 skipped, 20 deselected` (`/usr/bin/python3 -m pytest -q`, `b94dec9`);
the count only rises. Beyond the invariant check in §7 (which runs inside every existing runner
test), new tests cover:

1. **Verify feedback rides on `finish`** — `assistant(finish)` → `tool(finish, content ==
   VERIFY_FEEDBACK…)`; no `user` message follows; `verify.via == "finish_result"`;
   `tool_result.result` equals the wire content.
2. **Finish-first mixed turn** — `[finish, bash]` where bash times out: `finish` result is the
   feedback, bash result carries `follow_up == TIMEOUT_NUDGE`, wire order `[finish, bash]`, bash
   executed. And `[bash(timeout), finish]`: the `finish` result carries feedback *and* the timeout
   nudge, joined feedback-first.
3. **Multiple `finish` in one turn** — both results resolved to the same string.
4. **Malformed-only turn** — no addressable call, empty text: the assistant entry carries the
   placeholder, the malformed nudge is a `user` message, and no tool message from the previous
   turn changed (assert the prior turn's tool content byte-equal before/after).
5. **Malformed-only turn with text** — no placeholder, nudge as `user`.
6. **Stall / malformed nudge on a tool turn** — appended to the last result; `nudge.via ==
   "tool_result"`; one `follow_up` per turn holding the joined text.
7. **Empty reply after a tool turn (F5 shape)** — placeholder on the assistant entry,
   `assistant.placeholder` recorded, nudge as `user`, oracle passes.
8. **Verify failure with `--verify-rounds 0`** — status `verify_failed`, `finish` result is the
   §4 "no fix rounds remain" string, no feedback delivered, no `via` on `verify`.
9. **Verify failure on the last round** — same string; and **verify `SandboxError` /
   `BudgetExceeded`** — the "could not run" string, statuses unchanged.
10. **Transcript/wire equivalence** — for every turn of a multi-turn scenario, every wire tool
    message content equals `event.result + ("\n\n" + event.follow_up if present)` (results kept
    short of the preview cap), and every wire assistant content equals `event.placeholder or
    event.text`.
11. **Turn buffer** — `Transcript.turn()` preserves order across `guardrail_block`/`tool_result`/
    `nudge`/`verify`; flushes on exception; a record amended inside the block is written amended;
    amending after flush raises; `write()` outside a turn is immediate.
12. **All three providers** — the histories captured from tests 1, 2, 4 and 7 serialized through
    `_to_openai_messages` (OpenAI and Ollama) pass the oracle, and through the Anthropic
    serializer pass `_assert_alternating`.
13. **Existing tests updated, not deleted** — `test_finish_tool_ends_run_after_other_calls_in_turn`
    still asserts `run finished` (verify absent); the verify-feedback tests at `:1321`, `:1345`,
    `:1589` assert the new carrier; `test_timeout_nudge_merges_with_the_stall_nudge` asserts the
    joined `follow_up`.
14. **`runs show`** — renders `follow_up` and `placeholder`.
15. **Live** — the §7 replay against Devstral.

## 10. Acceptance evidence (necessary, not sufficient)

With §9 green, rerun through the #48 soak tooling: `F4b-round2-dev`, `F5-trunc2048-dev-r1`,
`F3v2-run-dev` on Devstral — expected: no `model_error`, `run_end.error` empty, and F4/F3/F5
detectors fire as designed; plus their Qwen counterparts (`F4b-round2-qwen` must still show
`F4(passed=True,rounds=2)`) to show the tool-result carrier did not cost Qwen the retry. Rows are
appended to the ledger with the soak's usual columns.

## 11. Out of scope

- Adapter-level normalization for OpenAI-compatible backends (approach A/C) — rejected in chat.
- Changing the `result` preview cap, the transcript schema version, or resume's history rebuild.
- Per-model template sniffing: the runner produces one shape for every backend.
- `trim_messages` behaviour: a trimmed tool result loses its `follow_up` along with its result
  (both replaced by `TRIM_MARKER`), which is the existing rule for old tool content.
