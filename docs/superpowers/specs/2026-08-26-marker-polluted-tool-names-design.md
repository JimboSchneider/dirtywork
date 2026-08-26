# Marker-polluted tool names: recover the call, nudge the model (#67) — design

*dirtywork 1.0 roadmap item 6 (soak finding S6). Written 2026-08-26 from the #48 soak's leg B and
the #65/#66 build's F5 Devstral rows. Owner decision recorded in §0; v2 after a four-lens
red-team (§0.1). Built by the released dirtywork 0.11.0 — the first build on a runtime that
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

```python
TOOL_CALL_MARKERS = ("[TOOL_CALLS]", "<tool_call>", "<function=", "<function_call>", "<|tool_call|>")   # one list for both paths, see 3.3

class ToolRegistry:
    def recover_name(self, name: str) -> tuple[str, bool]:
        """A registered name is returned as-is. A name that is not registered but,
        after its LAST tool-call marker, ends in a registered name is recovered to
        that name (True). Anything else is returned unchanged (False): the
        existing unknown-tool path decides what to do with it."""
        if name in self._table:
            return name, False
        cut = max((name.rfind(m) + len(m) for m in TOOL_CALL_MARKERS if m in name), default=-1)
        if cut < 0:
            return name, False
        suffix = name[cut:].strip()
        return (suffix, True) if suffix in self._table else (name, False)
```

The last marker wins because the model's own text sits *before* the token and the tool name
*after* it (every observed name; `[TOOL_CALLS][TOOL_CALLS]bash` recovers to `bash`). Whitespace
around the suffix is stripped; nothing else is normalised — `Bash` is not `bash`, and a suffix
that is not a tool is not guessed at.

### 3.2 The runner's tool-call path (`dirtywork/runner.py` ~`:1062-1130`)

Right after `name = tc.name` and before the `tc.error` / `_missing_required` checks:

```python
raw_name = name
name, recovered = self.registry.recover_name(name)
```

Everything downstream — validation, dispatch, `progress.note_call`, the bash repeat tracker,
`note_last_tool_result` — sees the recovered name. Two additions:

- **No strike, one nudge per turn.** A recovered call dispatches normally; only the dispatch's
  own outcome is accounted (a recovered `bash` that fails `bad_args` still strikes `bad_args`).
  The first recovered call on a turn attaches a nudge to its result — `nudge{kind:
  "name_recovered", via: "tool_result"}` and the text as the result's `follow_up`, through the
  same `deliver` carrier #60 built — so the history stays call → result. Further recovered calls
  on the same turn dispatch silently (the nudge is per turn, like `timeout`). Text:

  > Your tool call's name was `{raw_head}…[TOOL_CALLS]{tool}` — {n} characters of text
  > before the tool name. The harness ran `{tool}` with the arguments you gave. Emit tool calls
  > only through the tools API: the name field holds the tool name and nothing else.

  (`raw_head` = the first 40 characters of the raw name, newlines shown as `⏎`; `n` = the
  characters before the recovered suffix.)

- **The transcript records both names.** `tool_result.tool` is the recovered name (so
  `runs show`, bench's tool mix and the harvest see `bash`); a **sparse** `tool_raw` field
  carries the polluted name capped at 200 characters, present only when `recovered`. The
  `assistant` event's `tool_calls[].name` is capped at 200 characters in the transcript (its
  `arguments` were already capped at 2 000); the model still receives what it sent. A
  polluted name that is *not* recovered goes through the unknown-tool path unchanged —
  `tool_result.tool` is the raw name capped at 200, as the harvest already assumed.

### 3.3 The marker tuple and the text path

The marker list moves to `toolspec.py` as `TOOL_CALL_MARKERS` (the four `<…>` markers
`runner.py:100` builds today plus the literal `"[TOOL_CALLS]"`), and `runner.py`'s
`_TEXT_TOOL_MARKERS` becomes an import of it — one list serves both paths, and `toolspec`
never imports `runner` (that would be circular). `classify_text_reply` therefore turns a
prose reply that contains the token into the existing `text_tool_call` nudge.

### 3.4 The unknown-tool error text (`toolspec.py:414`)

`ERROR: unknown tool '{name}'` echoes the name back; the observed names show the model
re-quoting that error as its next name. The echoed name is capped at 80 characters with
`…` and `(name truncated)` after the quote. The `Available:` list and the finish hint stay.

### 3.5 Contract ripple

- **Nudge kinds**: `name_recovered` joins the list — `bench.NUDGE_KINDS` 10 → 11 (appended;
  the summarize legend widens to eleven components; `EMPTY_REPLY_NUDGE_KINDS` unchanged — this
  nudge records no failure), `docs/transcript-schema.md`'s `kind` row, `docs/machine-contract.md`
  `:463` and its nudge paragraph, `README.md`'s nudge sentence if it lists kinds.
- **Transcript**: `tool_result.tool_raw` (sparse, ≤ 200 chars, 1.0/#67) and the 200-char cap on
  `assistant.tool_calls[].name` and on `tool_result.tool` — rows in `transcript-schema.md`;
  `tests/test_transcript_schema.py`'s field lists.
- **soak_harvest**: feature code **S6** fires on `nudge.kind == "name_recovered"`, on a
  `tool_result` with `tool_raw`, or on an `unknown tool` error whose name contains a marker;
  `_tool_mix`'s 30-char cap stays (it now mostly sees clean names).
- **operating.md**: a troubleshooting entry ("`aborted after 3 consecutive unknown_tool
  failures` on Devstral — fixed in 1.0 by #67; the transcript's `tool_raw` shows what the
  model sent").

## 4. Failure modes and limits

- A suffix that names a tool but whose arguments belong to another (the model meant
  `read_file`, the name says `bash`): undetectable and unobserved; the dispatch validates the
  arguments against the suffix tool, so the worst case is a `bad_args` result the model can
  read.
- A marker inside a *legitimate* argument string is untouched — recovery reads the name only.
- A polluted `finish` recovers like any tool (2 of 28): the summary is the arguments' `summary`,
  and the change guard (#66) still decides whether the run may end.
- Nothing here changes provider behaviour; if LM Studio one day fixes the parse, `recover_name`
  simply never fires and `name_recovered` counts stay at zero on the ledger.

## 5. Tests

Unit (`tests/test_toolspec.py`, `tests/test_runner.py`, `tests/test_bench.py`,
`tests/test_soak_tools.py`, `tests/test_transcript_schema.py`):

1. `recover_name`: registered name → unchanged/False; `"…[TOOL_CALLS]bash"` → `bash`/True;
   `"…[TOOL_CALLS][TOOL_CALLS]read_file"` → `read_file`/True (last marker); `"…[TOOL_CALLS]nope"`
   → unchanged/False; `"garbage"` (no marker) → unchanged/False; `"<tool_call>bash"` → `bash`/True;
   surrounding whitespace stripped; `"[TOOL_CALLS]Bash"` → unchanged/False.
2. Runner: a polluted `bash` call dispatches and its result carries the `follow_up` text;
   the `nudge` event is `name_recovered` with `via: tool_result`; `tool_result.tool == "bash"`,
   `tool_raw` is the raw name capped at 200; **three polluted calls in a row do not abort**
   (no strike); two polluted calls on one turn produce one nudge; a polluted name with an
   unknown suffix still strikes `unknown_tool`; the error text's name is capped at 80 chars.
3. `classify_text_reply("…[TOOL_CALLS]bash{…}", "stop") == "text_tool_call"`.
4. Transcript caps: `assistant.tool_calls[].name` at 200; `tool_result.tool` at 200 for an
   unrecovered garbage name.
5. bench: `NUDGE_KINDS[-1] == "name_recovered"`, `len == 11`, the legend and the eleven-wide
   detail cell (exact column value); `EMPTY_REPLY_NUDGE_KINDS == tuple(runner.NUDGES)` holds.
6. harvest: S6 fires for each of its three triggers and not for a clean run.
7. Schema test lists updated with the new kind and field.

## 6. Acceptance

- **F5 Devstral rows rerun serially** on the built branch (`tools/soak_driver.py`, the six
  `F5-*-dev-*` rows of the #65/#66 plan, one driver, nothing else on the GPU): **no run ends
  `aborted after 3 consecutive unknown_tool failures`**; the ledger row per run reports
  `name_recovered` nudge count, status, strict-check verdict and wall. Expected from §1.2:
  the 1024/2048 rows that had written their file before aborting now reach `finish`.
- Unit and live suites green at the final code HEAD (the live suite on the pytest image).
- Built by `pipx run dirtywork==0.11.0`: the ledger's `## #67` section records every run —
  and, for the first time, any zero-change feedback resume is refused by the runtime itself.

## 7. Files

`dirtywork/toolspec.py` (`TOOL_CALL_MARKERS`, `recover_name`, error-text cap), `dirtywork/runner.py`
(recovery call, nudge text + delivery, transcript caps, `_TEXT_TOOL_MARKERS` as the import),
`dirtywork/bench.py` (`NUDGE_KINDS`), `tools/soak_harvest.py` (S6), `docs/transcript-schema.md`,
`docs/machine-contract.md`, `docs/operating.md`, `README.md`, tests as in §5, the ledger.
