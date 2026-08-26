# Marker-polluted tool names: recover the call, nudge the model (#67) — design

*dirtywork 1.0 roadmap item 6 (soak finding S6). Written 2026-08-26 from the #48 soak's leg B and
the #65/#66 build's F5 Devstral rows. Owner decision recorded in §0; v2 after a four-lens
red-team (§0.1); v3 after the owner's review (§0.2). Built by the released dirtywork 0.11.1 — the first build on a runtime that
refuses a zero-change finish.*

## 0. The owner's decision

| # | decision | where |
|---|---|---|
| 1 | When a tool call's name is not a registered tool but ends in one after a tool-call marker, **recover it**: dispatch the suffix tool with the given arguments, take no failure strike, and tell the model on that result to emit clean calls. A marker with a suffix that is not a tool, or a garbage name with no marker, keeps today's `unknown_tool` strike. (Jim, 2026-08-26 12:15; the alternatives — nudge-only per the issue's proposal, or recover-always — were declined.) | §3 |

Everything else here is the designer's proposal for the owner's review: the nudge kind's name (`name_recovered`), the transcript caps, the error-text cap, and the acceptance run.

### 0.1 Red-team fold (v2 — four lenses, 24 findings, 6 confirmed by adversarial verification)

| finding | fold |
|---|---|
| **Major** `deliver` (`runner.py:638-657`) rides the turn's *last* tool result and is called at two continuation sites (`:1150-1167` pending-finish branch, `:1193-1198` end of turn) that v1's "attaches a nudge to its result" and cited range did not cover; the `timeout` nudge v1 said to mirror is a per-turn flag composed after the loop and never written on a turn that ends the run; the merge order in `transcript-schema.md:86, :117-119` had no place for the kind | §3.2 rewritten to the `timeout` pattern exactly; merge position stated; ending-turn rule stated; §3.5 and §5 updated |
| **Major** the marker tuple is built by concatenation on purpose (`runner.py:91-94`; sp2.5 ruling): a local worker cannot emit its own template's control tags literally — v1's §3.1 code and §5.1 test strings spelled them out | §3.1/§3.3/§5: concatenation form kept and required, including `"[" + "TOOL_CALLS]"`; the worker brief says so |
| **Major** `run_end`/`run.json` `last_tool_result.tool` (`note_last_tool_result`) stayed uncapped while the transcript's `tool` was capped | §3.2: the name is capped once, before both writes; schema rows named |
| **Major** head-only caps (80/200) drop the marker and the suffix — the diagnostic tail — so S6's third trigger could never see them | §3.2/§3.4: head + `…` + tail form; S6 defined on `tool_result.tool` |
| **Major** the nudge text's home was unstated; a `NUDGES` entry would break `EMPTY_REPLY_NUDGE_KINDS == tuple(runner.NUDGES)` | §3.2: `NAME_RECOVERED_NUDGE` module constant, never a `NUDGES` key |
| **Major** the acceptance referenced a plan file in the session scratchpad | §6: the six rows committed at `docs/superpowers/bench/2026-08-26-issue-67-f5-devstral-plan.jsonl` |
| Minors (7): evidence counts recounted from the transcripts (4 of 8 aborts, marker/length buckets); the nudge text must not hard-code one marker and `n` needed a definition; the `tool` schema row becomes open; "bench's tool mix" is the harvest's; the pinned counts and enumerations named one by one; a schema test for `tool_raw` named; `runs show` does not read `tool_raw` (consistent with the operating.md entry) | folded in place |

### 0.2 Owner review fold (v3, 2026-08-26 14:44 — four items, all verified against the code)

| item | fold |
|---|---|
| `tool_raw` semantics: v2's "present when a marker was found" contradicted `recover_name`, which returns `marker=None` for a marker followed by an unknown suffix | **`tool_raw` means a successful recovery** — present iff `marker is not None`; an unrecovered polluted name is already the `tool` field itself (capped). §3.1/§3.2/§3.5/§5 aligned |
| the acceptance asked for a `tool_raw` count the harvest cannot produce; the harvest's feature list omits S6 (and S14) | §3.5: `soak_harvest` gains a per-run `recovered` column (`PER_RUN_COLUMNS`, count of `tool_result` events with `tool_raw`), its docstring lists S6 and S14; §6 names the column and the one-line fallback |
| §5 cited `tests/test_runner.py:348` and `tests/test_soak_tools.py:471` as concatenation examples; both are literal (the red-team's verifier was wrong) | §5: a test helper `tests/markers.py` is defined; new tests use it; the two existing lines are converted in the plan's first task and no longer cited as examples |
| `BudgetExceeded`/`SandboxError` return at `runner.py:1108/:1110` before the `tool_result` write, so a recovered call on those paths leaves no `tool_raw` | §3.2/§4: excluded and stated — those two paths write no `tool_result` for any call today; no synthetic record |

## 1. Problem and evidence

### 1.1 What happens

Devstral is a Mistral model; `[TOOL_CALLS]` is its native function-calling token. Through LM
Studio's OpenAI-compatible server the model's reply sometimes arrives as a tool call whose
**name** is prose or echoed prior output followed by the token and the intended tool:

```
name      = "exit code: 0\nid,name,email,plan,…\n4,User4,…,13.32[TOOL_CALLS][TOOL_CALLS]bash"
arguments = {"command": "tail -5 fixtures/rows.csv"}
```

`ToolRegistry.execute` (`dirtywork/toolspec.py:410-416`) finds no such tool, returns
`ERROR: unknown tool '<the whole name>'. Available: …` with `failure="unknown_tool"`, and the
runner (`dirtywork/runner.py:1106-1110`) records a `FailureTracker` strike; three consecutive
strikes end the run `model_error: aborted after 3 consecutive unknown_tool failures`. The error
text echoes the garbage name back to the model verbatim, and the model's next call often
carries *that* text as its name — the names grow (7 markers and 1.4 KB in one run; 5 KB in the
soak) until the third strike.

### 1.2 The evidence

- **Soak leg B** (`~/.dirtywork/bench/soak-B.jsonl`, label `F5-default-dev`, S6 in the ledger):
  the run aborted after its file was complete, 709 s.
- **The #65/#66 build's F5 Devstral rows** (ledger `## #65/#66`, F5 serial pass and tie-breaks,
  2026-08-26 00:0x–01:07, branch runtime at `00ea23d`): 4 of 8 Devstral runs ended
  `aborted after 3 consecutive unknown_tool failures` (1024-r2 at turn 30 after writing all
  401 rows; 2048-r1 turn 9; 2048-r2 turn 24 after writing the file; 4096-r1 turn 18); a fifth
  (1024-r1) aborted on the #65 six-cut-off budget with no polluted call; the three that
  completed carried polluted names in their transcripts too. Every one of the
  **28** polluted calls across those transcripts has the same shape:

  | property | count |
  |---|---|
  | suffix after the last marker is a registered tool | 28 / 28 (`bash` 23, `read_file` 3, `finish` 2) |
  | `arguments` is valid JSON for that tool | 28 / 28 |
  | markers in the name | one: 4 · two: 23 · seven: 1 |
  | name length | < 500 chars: 22 · 500–1 000: 5 · 1 410: 1 |
  | prefix content | echoed prior tool result (`exit code: 0\n…`, `Appended to …`) or the model's own prose, once a previous `ERROR: unknown tool '…'` text |

  Measured over the run dirs' `transcript.jsonl` `assistant.tool_calls` entries (recounted by
  the red-team's verifier; the run slugs are on the ledger rows).

So the call is *recoverable*, not merely nudgeable: the tool and its arguments are intact, only
the name field carries the model's stray prose. Refusing it costs the turn, and three refusals
in a row cost the run — after the work is done.

### 1.3 Why not just add the marker to the text-reply classifier

`classify_text_reply` (`runner.py:246-255`) already turns a *text-only* reply that contains a
marker (`_TEXT_TOOL_MARKERS`, `runner.py:100`: `<tool_call>`, `<function=`, `<function_call>`,
`<|tool_call|>`) into the `text_tool_call` nudge. `[TOOL_CALLS]` is not in that tuple, and the
observed failures are not text replies at all — LM Studio *did* parse a tool call; it just put
the prose in the name. Both halves are needed: the marker joins the tuple (a reply that arrives
as prose gets the existing nudge), and the tool-call path learns to read a polluted name.

## 2. Scope

**In:** name recovery in the registry; the runner's use of it (dispatch, nudge on the result,
no strike); `[TOOL_CALLS]` as a text-tool marker; caps on the name in the transcript and in the
unknown-tool error text; the contract ripple (nudge kind, transcript fields, bench, harvest,
docs); unit tests; an acceptance rerun of the Devstral F5 rows.

**Out:** anything provider-side (LM Studio's parser, Devstral's template); recovering a call
whose *arguments* are polluted (none observed; `malformed_args` stays as it is); other models'
markers beyond adding `[TOOL_CALLS]` (the tuple is the place for the next one).

## 3. Design

### 3.1 Recovery (`dirtywork/toolspec.py`)

The marker list moves here from `runner.py:100` and gains Devstral's token. **It is built by
concatenation, on purpose, and stays that way** (the comment at `runner.py:91-94` moves with
it): several local models' chat templates parse these exact tags in their own output, so a
worker model editing this file through its tool channel cannot emit them literally — and
`[TOOL_CALLS]` is Devstral's own function-calling token.

```python
# Built by concatenation ON PURPOSE -- see the comment above (moved from runner.py).
TOOL_CALL_MARKERS = ("[" + "TOOL_CALLS]",) + tuple(
    "<" + m for m in ("tool_call>", "function=", "function_call>", "|tool_call|>"))

class ToolRegistry:
    def recover_name(self, name: str) -> tuple[str, str | None, int]:
        """(name, marker, cut). A registered name is returned as-is with marker None.
        A name that is not registered but, after its LAST tool-call marker, ends in a
        registered name is recovered to that name; `marker` is the marker found and `cut`
        the number of characters before it (the model's stray text). Anything else is
        returned unchanged with marker None: the unknown-tool path decides."""
        if name in self._table:
            return name, None, 0
        best = max(((name.rfind(m), m) for m in TOOL_CALL_MARKERS if m in name), default=(-1, None))
        if best[0] < 0:
            return name, None, 0
        pos, marker = best
        suffix = name[pos + len(marker):].strip()
        return (suffix, marker, pos) if suffix in self._table else (name, None, 0)
```

The last marker wins because the model's own text sits *before* the token and the tool name
*after* it (every observed name; `[TOOL_CALLS][TOOL_CALLS]bash` recovers to `bash`). Whitespace
around the suffix is stripped; nothing else is normalised — `Bash` is not `bash`, and a suffix
that is not a tool is not guessed at.

### 3.2 The runner's tool-call path (`dirtywork/runner.py`)

**In the per-call loop** (`:1062-1130`), right after `name = tc.name` and before the
`tc.error` / `_missing_required` checks:

```python
raw_name = name
name, marker, cut = self.registry.recover_name(name)
if marker is not None and first_recovered is None:      # per-turn, like timed_out_this_turn
    first_recovered = (raw_name, marker, name, cut)
```

Everything downstream — validation, dispatch, `progress.note_call`, the bash repeat tracker,
`note_last_tool_result`, the `pending_finish`/terminal branch for a recovered `finish` — sees
the recovered name. A recovered call takes **no failure strike**: only the dispatch's own
outcome is accounted (a recovered `bash` that fails `bad_args` still strikes `bad_args`).

**The name is capped once.** `transcript_name = cap_name(name)` is computed before the
`tool_result` write (`:1125`) and passed to both that write and `note_last_tool_result`, so the
transcript's `tool_result.tool`, `run_end.last_tool_result.tool` and `run.json.last_tool_result.tool`
carry the same value. `cap_name` (one constant, `TOOL_NAME_TRANSCRIPT_CHARS = 200`) keeps
**head and tail**: `name[:120] + "…" + name[-80:]` when longer than 200 (so the marker and the
suffix — the diagnostic part — survive; the field can then be 201 characters). The same cap
applies to `assistant.tool_calls[].name` in the transcript (`arguments` were already capped at
2 000). The model still receives what it sent.

**The transcript records both names.** `tool_result.tool` is the recovered name (so `runs show`,
the harvest's `_tool_mix` and its feature detection see `bash`); a **sparse** `tool_raw` field
carries the raw name through the same `cap_name`, present **iff the name was recovered**
(`recover_name` returned a marker) — never for an unrecovered polluted name, whose raw name is
the `tool` field itself. An
unrecovered garbage name goes through the unknown-tool path unchanged; its `tool` is the raw
name through `cap_name`, and the `tool` schema row becomes open: a registered name, or on an
`unknown_tool` error the name the model sent (capped), or `""` for a discarded malformed entry.

**The nudge follows the `timeout` pattern exactly** (`:1123-1124`, `:1140-1167`, `:1183-1199`):

- The text is a module constant next to `TIMEOUT_NUDGE` — `NAME_RECOVERED_NUDGE`, a
  `str.format` template — and **never a `runner.NUDGES` key** (`bench.EMPTY_REPLY_NUDGE_KINDS ==
  tuple(runner.NUDGES)` pins that dict to the three kinds that strike `empty_reply`; this nudge
  strikes nothing):

  > Your tool call's name was `{raw_head}…{marker}{tool}` — {cut} characters of text before
  > the tool-call marker. The harness ran `{tool}` with the arguments you gave. Emit tool calls
  > only through the tools API: the name field holds the tool name and nothing else.

  `raw_head` = the first 40 characters of the raw name with newlines shown as `⏎`; `marker` and
  `cut` come from `recover_name`; the first recovered call of the turn supplies them.
- After the loop, `name_recovered_text` is composed beside `timeout_text` (`:1148`) when
  `first_recovered` is set, and joined into **both** delivery points: the `pending_finish`
  branch (`:1160`, today `_join_nudges(sandbox_text, timeout_text)`) and the end of the turn
  (`:1198`), **after `timeout` and before `stall`** in the merge order. The `nudge` record
  (`kind: "name_recovered"`, `turn`) is written where `timeout_record` is written (`:1158`,
  `:1195-1196`), so `deliver` stamps `via: "tool_result"` on it and the text rides the turn's
  **last** tool result, exactly as `timeout` does. One nudge per turn however many calls were
  recovered.
- **On a turn that ends the run** — a recovered `finish` whose completion is accepted, or a
  stall/stuck/change-guard verdict on that turn — the nudge is neither delivered nor recorded
  (the `timeout` rule at `:1140-1147`); the record of what happened is the `tool_raw` field on
  that call's `tool_result`, which is what the ledger counts. **Excluded:** a recovered call
  whose dispatch raises `BudgetExceeded` or `SandboxError` returns at `:1108`/`:1110` before
  the `tool_result` write, as any call does today — the run ends `budget_exceeded`/
  `sandbox_error` with no record of that call and no synthetic one is written. A recovered `finish` whose completion is
  *refused* (verify feedback, the change guard's first refusal) continues through the
  `pending_finish` branch and gets the nudge there.

### 3.3 The text path

`runner.py`'s `_TEXT_TOOL_MARKERS` becomes `from .toolspec import TOOL_CALL_MARKERS as
_TEXT_TOOL_MARKERS` (toolspec never imports runner — the reverse would be circular), so
`classify_text_reply` turns a prose reply that contains `[TOOL_CALLS]` into the existing
`text_tool_call` nudge; the other four markers behave as today.

### 3.4 The unknown-tool error text (`toolspec.py:414`)

`ERROR: unknown tool '{name}'` echoes the name back; the observed names show the model
re-quoting that error as its next name. The echoed name is capped **head and tail** — the
first 40 characters, `…`, the last 40, then `(name truncated)` — so the marker and suffix stay
visible to the model without the whole echo. The `Available:` list and the finish hint stay.

### 3.5 Contract ripple (each site named)

- **Nudge kinds** — `name_recovered` is appended: `bench.NUDGE_KINDS` 10 → 11 (the summarize
  legend and detail cell widen to eleven; `tests/test_bench.py:854` becomes
  `NUDGE_KINDS[-3:] == ("no_change", "unchanged_finish", "name_recovered")`, `:857` `len == 11`,
  the "ten kinds" test at `:875` renamed to eleven; `EMPTY_REPLY_NUDGE_KINDS` unchanged);
  `docs/transcript-schema.md` `kind` row (`:126`), the `follow_up` row (`:86`) and the
  merge-order sentences (`:117-119`, including the verify-feedback clause) gain the kind at its
  position; the version sentence at `:27-32` gains "#67 adds one `nudge.kind` (`name_recovered`),
  one sparse `tool_result` field (`tool_raw`) and the name caps"; `docs/machine-contract.md:463`
  kinds string and `:464` "ten kinds" → eleven, `:460-461` the name cap; `README.md`'s nudge
  sentence if it lists kinds; `tests/test_transcript_schema.py`'s lists.
- **Transcript fields** — rows for `tool_raw` (sparse, ≤ 201 chars, 1.0/#67), the cap form on
  `assistant.tool_calls[].name`, `tool_result.tool` (now an open row, see §3.2) and
  `run_end`/`run.json` `last_tool_result.tool` (`transcript-schema.md:245`, `:342`).
- **soak_harvest** — feature code **S6** fires on `nudge.kind == "name_recovered"`, on a
  `tool_result` carrying `tool_raw`, or on a `tool_result` whose `result` starts with
  `ERROR: unknown tool` and whose `tool` contains a `TOOL_CALL_MARKERS` marker (the capped
  field keeps the tail, so the marker survives; a prefix so long that the head+tail cap drops
  every marker is the stated blind spot — none observed). The per-run table gains a
  **`recovered`** column (`PER_RUN_COLUMNS`, `tools/soak_harvest.py:36-37`, after `stray kills`):
  the count of `tool_result` events carrying `tool_raw`; the existing `nudges` column already
  sums every `bench.NUDGE_KINDS` kind (`:403`), so `name_recovered` counts there by
  construction. The module docstring's feature list (`:6-8`) gains **S6** and the #66 **S14** it
  also omits. `_tool_mix`'s 30-char cap stays.
- **operating.md** — a troubleshooting entry: "`aborted after 3 consecutive unknown_tool
  failures` on Devstral — 1.0 (#67) recovers the call; the transcript's `tool_raw` shows what
  the model sent". `runs show --markdown` does not render `tool_raw` (the entry points at the
  transcript).

## 4. Failure modes and limits

- A suffix that names a tool but whose arguments belong to another (the model meant
  `read_file`, the name says `bash`): undetectable and unobserved; the dispatch validates the
  arguments against the suffix tool, so the worst case is a `bad_args` result the model can
  read.
- A marker inside a *legitimate* argument string is untouched — recovery reads the name only.
- A polluted `finish` recovers like any tool (2 of 28): the summary is the arguments' `summary`,
  and the change guard (#66) still decides whether the run may end.
- A recovered call whose dispatch raises `BudgetExceeded`/`SandboxError` leaves no
  `tool_result` (the runner returns before the write, for every call) — the run's status is
  the record; no synthetic event.
- Nothing here changes provider behaviour; if LM Studio one day fixes the parse, `recover_name`
  simply never fires and `name_recovered` counts stay at zero on the ledger.

## 5. Tests

All marker strings in tests are built by concatenation, through one helper module,
`tests/markers.py`:

```python
# Built by concatenation ON PURPOSE: a local worker model editing tests through its tool
# channel cannot emit its own chat template's control tags literally (see toolspec.py).
TOOL_CALLS = "[" + "TOOL_CALLS]"          # Devstral / Mistral
TOOL_CALL_OPEN = "<" + "tool_call>"        # Qwen-style XML
TOOL_CALL_CLOSE = "</" + "tool_call>"
```

New tests import these; the two existing literal occurrences — `tests/test_runner.py:348`
(`"<tool_call>{}</tool_call>"`) and `tests/test_soak_tools.py:471` (`"…[TOOL_CALLS]" + …`) —
are converted to the helper in the plan's first task, so the repo has no literal marker in a
test after this build.

Unit (`tests/test_toolspec.py`, `tests/test_runner.py`, `tests/test_bench.py`,
`tests/test_soak_tools.py`, `tests/test_transcript_schema.py`):

1. `recover_name`: registered name → `(name, None, 0)`; `"…" + M + "bash"` → `("bash", M, cut)`;
   `"…" + M + M + "read_file"` → `read_file` (last marker); `"…" + M + "nope"` → unchanged, marker
   `None`; `"garbage"` (no marker) → unchanged; `"<" + "tool_call>" + "bash"` → `bash` with that
   marker; surrounding whitespace stripped; `M + "Bash"` → unchanged. (`M` = `markers.TOOL_CALLS`.)
2. Runner: a polluted `bash` call dispatches; the turn's **last** tool result carries the
   `follow_up` text and the `nudge` event is `name_recovered` with `via: tool_result`;
   `tool_result.tool == "bash"` and `tool_raw` is the raw name through `cap_name`; an
   unrecovered polluted name has **no** `tool_raw` (its `tool` is the capped raw name);
   **three polluted calls in a row do not abort** (no strike); two polluted calls on one turn →
   one nudge, on the second call's result; a polluted name with an unknown suffix still strikes
   `unknown_tool`; a recovered `finish` on a verify-feedback turn continues and gets the nudge
   through the `pending_finish` branch; a recovered `finish` that completes writes no `nudge`
   record and the run's last `tool_result` has `tool_raw`; `run_end.last_tool_result.tool` equals
   the capped transcript name for a 1 000-character unrecovered garbage name; the error text's
   name is head+tail capped with `(name truncated)`; the merge order on a turn with a timeout
   and a recovery is `timeout` then `name_recovered`.
3. `classify_text_reply("…" + M + "bash{…}", "stop") == "text_tool_call"`.
4. Transcript caps: `assistant.tool_calls[].name` and `tool_result.tool` through `cap_name`
   (201 chars, head 120 + `…` + tail 80) for a long unrecovered name; `tool_raw` absent on a
   clean call.
5. bench: `NUDGE_KINDS[-3:]`, `len == 11`, the legend and the eleven-wide detail cell as an exact
   column value; `EMPTY_REPLY_NUDGE_KINDS == tuple(runner.NUDGES)` holds.
6. harvest: S6 fires for each of its three triggers separately — including an unrecovered name
   whose prefix exceeds 120 characters, where the tail keeps the marker — and not for a clean run.
7. Schema: `tool_raw` and `name_recovered` are documented tokens (`_doc_tokens()`), checked by a
   runner-driven case that emits a recovered call and asserts every emitted `tool_result` key is
   documented.

## 6. Acceptance

- **F5 Devstral rows rerun serially** on the built branch: `tools/soak_driver.py
  docs/superpowers/bench/2026-08-26-issue-67-f5-devstral-plan.jsonl` (the six `F5-*-dev-*` rows
  of the #65/#66 plan, committed with this spec; one driver, nothing else on the GPU): **no run
  ends `aborted after 3 consecutive unknown_tool failures`**; the ledger row per run reports
  the harvest's `nudges` and `recovered` columns (`tools/soak_harvest.py <results.jsonl>`;
  fallback for one run: `grep -c '"tool_raw":' <run_dir>/transcript.jsonl`), status,
  strict-check verdict and wall. Expected
  from §1.2: the 1024/2048 rows that had written their file before aborting now reach `finish`.
- Unit and live suites green at the final code HEAD (the live suite on the pytest image).
- Built by `pipx run dirtywork==0.11.1` with `dirtywork-worker-pytest:0.11`: the ledger's
  `## #67` section records every run — and, for the first time, any zero-change feedback
  resume is refused by the runtime itself.

## 7. Files

`dirtywork/toolspec.py` (`TOOL_CALL_MARKERS` by concatenation, `recover_name`, error-text cap),
`dirtywork/runner.py` (recovery call, `first_recovered`, `NAME_RECOVERED_NUDGE` beside
`TIMEOUT_NUDGE`, both delivery points, `cap_name`/`TOOL_NAME_TRANSCRIPT_CHARS`, `_TEXT_TOOL_MARKERS`
as the import),
`dirtywork/bench.py` (`NUDGE_KINDS`), `tools/soak_harvest.py` (S6), `docs/transcript-schema.md`,
`docs/machine-contract.md`, `docs/operating.md`, `README.md`, tests as in §5,
`tests/markers.py` (new), `docs/superpowers/bench/2026-08-26-issue-67-f5-devstral-plan.jsonl`, the ledger.
