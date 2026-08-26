# Marker-polluted tool names (#67) — Implementation Plan

> **For agentic workers:** this plan is executed the way #64, #61 and #65/#66 were — each task is a brief handed verbatim to the **released dirtywork** (`pipx run --spec 'dirtywork==0.11.1' dirtywork run …`, qwen3-coder-next on LM Studio, `dirtywork-worker-pytest:0.11`, `--verify` = the unit suite), Claude reviews every branch, gives at most one feedback resume, and finishes a task itself only after that resume fails (stated on the ledger). Steps use checkbox (`- [ ]`) syntax for tracking. This is the first build on a runtime whose change guard (#66) refuses a zero-change completion.

**Goal:** a tool call whose name is stray prose + a tool-call marker + a real tool name is dispatched as that tool with the arguments given, takes no failure strike, and tells the model once per turn to emit clean calls; every name the transcript records is capped head-and-tail; bench, the harvest and the docs learn the new nudge kind and field.

**Architecture:** `ToolRegistry.recover_name` (toolspec) decides; the runner uses it before validation and dispatch, caps the name once for every record, and delivers `NAME_RECOVERED_NUDGE` through the existing `timeout` mechanism at both continuation points; the contract ripple follows the #65/#66 pattern (appended nudge kind, sparse transcript field, harvest feature code and column).

**Tech Stack:** Python 3.9+ (the repo's floor), stdlib only; pytest; the docker worker image for acceptance.

**Spec:** `docs/superpowers/specs/2026-08-26-marker-polluted-tool-names-design.md` (v3, owner-approved 2026-08-26 14:44). The plan argues from it; executors read both.

## Global Constraints

- **Markers are built by concatenation, everywhere** — code, tests, docs examples in code blocks: `"[" + "TOOL_CALLS]"`, `"<" + "tool_call>"`, never the literal tag (spec §3.1, §5; `runner.py:91-94`'s comment moves to toolspec). A brief that shows a marker shows it concatenated.
- `TOOL_NAME_TRANSCRIPT_CHARS = 200`; `cap_name(s)` = `s` if `len(s) <= 200` else `s[:120] + "…" + s[-80:]` (201 chars) — one function, used for `assistant.tool_calls[].name`, `tool_result.tool`, `tool_result.tool_raw`, `last_tool_result.tool` (spec §3.2).
- The unknown-tool error echoes `name[:40] + "…" + name[-40:]` followed by ` (name truncated)` when the name is longer than 80 (spec §3.4).
- `tool_raw` is present **iff** the call was recovered (spec §0.2).
- `NAME_RECOVERED_NUDGE` is a module constant beside `TIMEOUT_NUDGE`, never a `runner.NUDGES` key (spec §3.2).
- One `name_recovered` nudge per turn, written and delivered where `timeout` is, order `… timeout, name_recovered, stall, no_change`; never on a turn that ends the run (spec §3.2).
- `bench.NUDGE_KINDS` gains `name_recovered` **appended at the end** (10 → 11); `EMPTY_REPLY_NUDGE_KINDS` unchanged (spec §3.5).
- No docs/ edits in code tasks; D1 owns the docs. Every task runs `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` green before `finish`.

---

## Execution model (Claude's checklist, not a worker brief)

- Integration branch `issue-67-tool-name-recovery` (spec v3 at `78d346c`). Launch: `$SCRATCH/run67.sh $SCRATCH/brief-67-<task>.md`, guarded on `$SCRATCH/expected-head` = the integration HEAD. Each run gets its own `dw-*` worktree and branch.
- Review each run's `diff.patch` against its brief; run the touched test files and the full suite on the run worktree; one feedback resume (`pipx run --spec 'dirtywork==0.11.1' dirtywork resume <slug> --feedback-file … --max-turns 40`); a Claude finish only after a failed resume — its commit separate from the worker-verbatim commit (stash → apply `diff.patch` → commit verbatim → checkout Claude files from the stash → commit finish → `git diff HEAD stash@{0}` empty → rebase → ff-merge **from the integration worktree**).
- Ledger: section `## #67` in `docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md`, one row per run (wall, turns, s/turn, tokens, nudges, tool mix, verdict) as they land; sampler `tools/soak_sampler.sh $SCRATCH/metrics-67.csv` running throughout; the first `unchanged` status or `unchanged_finish` nudge under the released runtime is the build's headline row.
- Order: T0 → T1 → T2a → T2b → T3 → D1 → C1 → C2.
- D1's brief necessarily spells Devstral's token in doc prose. It runs on qwen3-coder-next only (that token is inert for Qwen's parser; `<tool_call>` is the one it eats), and if the diff shows the token mangled anywhere, Claude finishes D1 rather than resuming.

---

### Task T0: `tests/markers.py` and the two literal markers

**Files:**
- Create: `tests/markers.py`
- Modify: `tests/test_runner.py:348`, `tests/test_soak_tools.py:471`
- Test: the whole suite (no new test — this task removes literals)

**Interfaces:**
- Produces: `tests.markers.TOOL_CALLS`, `TOOL_CALL_OPEN`, `TOOL_CALL_CLOSE` (str) — every later task's tests import these.

**Brief (verbatim):**

```
Issue #67, task T0 of 8: a helper module for tool-call marker strings in tests, and the two places that spell a marker literally today. Rule (spec §5 and the comment at dirtywork/runner.py lines 91-94): these tags are built by concatenation ON PURPOSE, because local models' chat templates parse them in their own output; never write the literal tag.

1. Create tests/markers.py with exactly this content (note every string is two pieces joined with +):

# Built by concatenation ON PURPOSE: a local worker model editing tests through its tool
# channel cannot emit its own chat template's control tags literally (see the comment in
# dirtywork/toolspec.py next to TOOL_CALL_MARKERS).
TOOL_CALLS = "[" + "TOOL_CALLS]"          # Devstral / Mistral
TOOL_CALL_OPEN = "<" + "tool_call>"        # Qwen-style XML, opening tag
TOOL_CALL_CLOSE = "</" + "tool_call>"      # Qwen-style XML, closing tag

2. tests/test_runner.py line 348 currently passes a content string that is the opening XML tag, then {}, then the closing tag, written literally. Change it to `_resp(content=TOOL_CALL_OPEN + "{}" + TOOL_CALL_CLOSE)` and add `from markers import TOOL_CALL_OPEN, TOOL_CALL_CLOSE` next to the file's other test-helper imports (look at how the file imports from provider_doubles; tests/ is on sys.path the same way).

3. tests/test_soak_tools.py line 471: `long_tool = "resultofpreviouscall" + "..." + ("bash" * 40)` where the middle piece is the Devstral marker written literally. Change it to `long_tool = "resultofpreviouscall" + TOOL_CALLS + ("bash" * 40)` and add `from markers import TOOL_CALLS` next to that file's imports.

4. grep -rn for the literal opening XML tag and the literal Devstral token across tests/*.py — after your change the only hits must be inside tests/markers.py's concatenations (i.e. none as a whole tag). Then run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider tests/test_runner.py tests/test_soak_tools.py` and then the whole suite the same way (drop the file arguments). Call finish(summary=...) naming the three files. Do not touch docs/.
```

- [ ] Launch, review the diff (three files only; both imports resolve; the grep is clean), full suite green on the run worktree, merge, ledger row.

---

### Task T1: `TOOL_CALL_MARKERS`, `recover_name`, the error-text cap, the text path

**Files:**
- Modify: `dirtywork/toolspec.py` (module top; `ToolRegistry`; `execute` at `:410-416`)
- Modify: `dirtywork/runner.py:91-100` (`_TEXT_TOOL_MARKERS` becomes an import)
- Test: `tests/test_toolspec.py`, `tests/test_runner.py`

**Interfaces:**
- Produces: `toolspec.TOOL_CALL_MARKERS: tuple[str, ...]`; `ToolRegistry.recover_name(name: str) -> tuple[str, str | None, int]` (recovered name, marker found or `None`, characters before the marker); the unknown-tool error text `ERROR: unknown tool '<head>…<tail> (name truncated)'. Available: …` for names over 80 chars.
- Consumes: `tests.markers`.

**Brief (verbatim):**

```
Issue #67, task T1 of 8: the tool-name recovery primitive in dirtywork/toolspec.py, the unknown-tool error text cap, and the text-path marker import in dirtywork/runner.py. Spec §3.1, §3.3, §3.4. Rule: tool-call marker strings are built by concatenation ON PURPOSE — never write the literal tag in code or tests (dirtywork/runner.py lines 91-94 explain why; that comment moves with the tuple).

1. dirtywork/toolspec.py, near the top (after the imports, before the first class): move the comment block from dirtywork/runner.py lines 91-94 here verbatim (it starts "These tags are built by concatenation ON PURPOSE") and define, in this exact concatenated form:

TOOL_CALL_MARKERS = ("[" + "TOOL_CALLS]",) + tuple(
    "<" + m for m in ("tool_call>", "function=", "function_call>", "|tool_call|>"))

   The first element is Devstral's (Mistral's) function-calling token; the other four are the ones runner.py builds today at line 100 — keep their exact spelling.

2. dirtywork/runner.py line 100: delete the `_TEXT_TOOL_MARKERS = tuple("<" + m for m in (...))` line and the comment block you moved (lines 91-94), and add `from .toolspec import TOOL_CALL_MARKERS as _TEXT_TOOL_MARKERS` next to the file's other `from .toolspec import ...` line (there is one; extend it or add a second). classify_text_reply (line ~246) keeps using `_TEXT_TOOL_MARKERS` unchanged, so a text-only reply containing Devstral's token now classifies as "text_tool_call". Check there is no import cycle: toolspec must not import runner (it does not today; do not add one).

3. dirtywork/toolspec.py, class ToolRegistry: add this method (place it right before `execute`):

    def recover_name(self, name: str) -> "tuple[str, str | None, int]":
        """(name, marker, cut). A registered name is returned as-is with marker None.
        A name that is not registered but, after its LAST tool-call marker, ends in a
        registered name is recovered to that name; `marker` is the marker found and
        `cut` the number of characters before it (the model's stray text). Anything
        else is returned unchanged with marker None: the unknown-tool path decides."""
        if name in self._table:
            return name, None, 0
        best = max(((name.rfind(m), m) for m in TOOL_CALL_MARKERS if m in name),
                   default=(-1, None))
        if best[0] < 0:
            return name, None, 0
        pos, marker = best
        suffix = name[pos + len(marker):].strip()
        if suffix in self._table:
            return suffix, marker, pos
        return name, None, 0

   Note the annotation is a string so it parses on Python 3.9 (the repo's floor).

4. dirtywork/toolspec.py `execute`, the `if spec is None:` branch (~line 411-416): the error currently embeds the whole `name`. Cap it head-and-tail: shown = name if len(name) <= 80 else name[:40] + "…" + name[-40:] + " (name truncated)"; the text becomes f"ERROR: unknown tool '{shown}'. Available: {available}. To end the run call finish(summary=...)." — everything else (kind="error", failure="unknown_tool") unchanged.

5. tests/test_toolspec.py — add, importing `from markers import TOOL_CALLS, TOOL_CALL_OPEN` and building a registry the way the file's existing tests do (look for how they construct ToolRegistry / the built-in registry; reuse that fixture or helper):
   a. test_recover_name_registered_name_is_unchanged: recover_name("bash") == ("bash", None, 0).
   b. test_recover_name_strips_prose_before_the_last_marker: name = "exit code: 0\n1,User1" + TOOL_CALLS + "bash" → ("bash", TOOL_CALLS, len("exit code: 0\n1,User1")).
   c. test_recover_name_uses_the_last_marker: name = "a" + TOOL_CALLS + "b" + TOOL_CALLS + "read_file" → ("read_file", TOOL_CALLS, len("a" + TOOL_CALLS + "b")).
   d. test_recover_name_leaves_an_unknown_suffix_alone: name = "x" + TOOL_CALLS + "nope" → (name, None, 0).
   e. test_recover_name_leaves_a_name_without_a_marker_alone: recover_name("garbage") == ("garbage", None, 0).
   f. test_recover_name_handles_the_xml_marker: name = "prose " + TOOL_CALL_OPEN + "bash" → ("bash", TOOL_CALL_OPEN, len("prose ")).
   g. test_recover_name_strips_whitespace_around_the_suffix: name = "p" + TOOL_CALLS + "  bash \n" → ("bash", TOOL_CALLS, 1).
   h. test_recover_name_is_case_sensitive: name = TOOL_CALLS + "Bash" → (name, None, 0).
   i. test_unknown_tool_error_caps_the_echoed_name: execute a name of 300 characters ("q" * 150 + TOOL_CALLS + "zz" * 70) with args {} → the result text contains "…", contains "(name truncated)", contains the last 40 characters of the name, and its total length is under 400; a 20-character unknown name is echoed whole with no "(name truncated)". Use the registry's execute signature exactly as other tests in the file call it (it takes sandbox= and deadline=; copy a neighbouring test's call).
   j. In tests/test_runner.py add test_classify_text_reply_treats_the_devstral_marker_as_a_text_tool_call: classify_text_reply("I will run " + TOOL_CALLS + 'bash{"command": "ls"}', "stop") == "text_tool_call" (import classify_text_reply the way the file already does; import TOOL_CALLS from markers).

6. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider tests/test_toolspec.py tests/test_runner.py` until green, then the whole suite the same way (no file arguments), then call finish(summary=...) naming recover_name, the error cap and the import move. Do not touch docs/. Do not add the nudge or any runner dispatch change — that is task T2.
```

- [ ] Launch, review (concatenation preserved; no import cycle; the moved comment; the 3.9-safe annotation), suite green, merge, ledger row.

---

### Task T2a: recovery in the runner's call loop, `cap_name`, `tool_raw`

**Files:**
- Modify: `dirtywork/runner.py` (constants near `:22`; the tool-call loop `:1062-1130`; the `assistant` transcript write `:697`; `note_last_tool_result` `:666`)
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `ToolRegistry.recover_name` (T1).
- Produces: `runner.TOOL_NAME_TRANSCRIPT_CHARS = 200`, `runner.cap_name(s: str) -> str`; the per-turn variable `first_recovered: tuple[str, str, str, int] | None` = `(raw_name, marker, tool, cut)` that T2b reads; transcript `tool_result.tool_raw` (sparse).

**Brief (verbatim):**

```
Issue #67, task T2a of 8: the runner recovers a marker-polluted tool name before validation and dispatch, caps every recorded name once, and records the raw name as a sparse `tool_raw` field. Spec §3.2 (first three paragraphs). NO nudge in this task — T2b adds it; leave a per-turn variable for it as described in step 3. Rule: marker strings only by concatenation ("[" + "TOOL_CALLS]"), in code and tests.

1. dirtywork/runner.py, next to MAX_ASSISTANT_TEXT_CHARS (line ~22), add:

TOOL_NAME_TRANSCRIPT_CHARS = 200


def cap_name(name: str) -> str:
    """Transcript/run.json cap for a tool name, head AND tail so a marker-polluted
    name keeps its diagnostic end (spec #67 §3.2): 200 chars pass through; longer
    names become the first 120 + "…" + the last 80 (201 chars)."""
    if len(name) <= TOOL_NAME_TRANSCRIPT_CHARS:
        return name
    return name[:120] + "…" + name[-80:]

2. The `assistant` transcript write (line ~697): `tool_calls=[{"name": tc.name, "arguments": ...}` → `{"name": cap_name(tc.name), "arguments": ...}`. Only the transcript record changes; the message sent to the model is untouched.

3. In the per-turn code before the `for tc in tool_calls:` loop (line ~1062; next to where `timed_out_this_turn = False` and `pending_finish = None` are set), add `first_recovered = None`. Inside the loop, right after `name = tc.name` and BEFORE the `if tc.error is not None:` check, add:

                raw_name = name
                name, marker, cut = self.registry.recover_name(name)
                if marker is not None and first_recovered is None:
                    first_recovered = (raw_name, marker, name, cut)

   Everything after this uses `name` (the recovered one) exactly as today: the tc.error branch, _missing_required, the registry.execute call, progress.note_call, the bash repeat tracker. A recovered call takes NO failure strike of its own — do not add one; only the dispatch result's own `failure` is recorded, as today.

4. The `tool_result` transcript write (line ~1125, `record = self.transcript.write("tool_result", tool=name, args=raw_args[:500], ...)`): compute `transcript_name = cap_name(name)` on the line before, pass `tool=transcript_name`, and add `tool_raw=cap_name(raw_name)` ONLY when `marker is not None` (build the kwargs so the key is absent otherwise — e.g. `raw_fields = {"tool_raw": cap_name(raw_name)} if marker is not None else {}` and splat it like `**timed_out_fields`). The `note_last_tool_result(name, raw_args, result)` call right below becomes `note_last_tool_result(transcript_name, raw_args, result)` so run_end/run.json `last_tool_result.tool` carries the same capped value.

5. tests/test_runner.py — import TOOL_CALLS from markers; use the file's FakeProvider/_resp/_call/_events helpers and the `parts` fixture exactly as neighbouring tests do. Build a polluted call with `_call("c1", "exit code: 0\\n1,User1" + TOOL_CALLS + "bash", {"command": "echo hi"})` (the name is the polluted string; _call accepts any name).
   a. test_polluted_bash_name_is_recovered_and_dispatched: script [polluted bash call, then a finish call]; after the run the tool_result events contain one with tool == "bash" whose result contains "hi" (the command ran) and whose tool_raw == the polluted name; the run completes (status "completed" — the finish is a plain finish; if the change guard refuses the first finish because nothing changed, script a second finish response too, the way tests written for #66 do — look for "unchanged_finish" in this file for the pattern).
   b. test_three_polluted_calls_in_a_row_do_not_abort: three polluted bash calls on three turns (each its own _resp), then finish → status "completed", and no run_end final_message containing "consecutive".
   c. test_unrecovered_polluted_name_still_strikes_unknown_tool: name = "x" + TOOL_CALLS + "nope"; three such calls → status "model_error" with final_message "aborted after 3 consecutive unknown_tool failures"; the tool_result events for them have NO tool_raw key and their tool == the name (short enough to pass cap_name unchanged).
   d. test_recorded_names_are_capped_head_and_tail: an unrecovered name of 1000 chars ("p" * 950 + TOOL_CALLS + "zzzz") → the assistant event's tool_calls[0]["name"] has length 201 and contains "…"; the tool_result's tool equals cap_name(name); run_end["last_tool_result"]["tool"] == cap_name(name).
   e. test_tool_raw_absent_on_a_clean_call: a normal bash call's tool_result has no tool_raw key.
   f. test_cap_name: cap_name("a" * 200) == "a" * 200; cap_name("a" * 201) == "a" * 120 + "…" + "a" * 80.

6. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider tests/test_runner.py` until green, then the whole suite, then call finish(summary=...) naming the four runner sites and the six tests. Do not touch docs/. Do not add the nudge.
```

- [ ] Launch, review (recovery before `tc.error`; `tool_raw` sparse; one `cap_name` for all four records; no strike added), suite green, merge, ledger row.

---

### Task T2b: `NAME_RECOVERED_NUDGE` at both delivery points

**Files:**
- Modify: `dirtywork/runner.py` (constant beside `TIMEOUT_NUDGE` `:286`; composition beside `timeout_text` `:1148`; the `pending_finish` branch `:1150-1167`; the end-of-turn delivery `:1183-1199`)
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `first_recovered` (T2a).
- Produces: `runner.NAME_RECOVERED_NUDGE` (str.format template with `raw_head`, `marker`, `tool`, `cut`); nudge events `kind="name_recovered"`, `via="tool_result"`.

**Brief (verbatim):**

```
Issue #67, task T2b of 8: the nudge that tells the model a polluted name was recovered — delivered exactly like the existing `timeout` nudge. Spec §3.2 ("The nudge follows the timeout pattern exactly"). Read dirtywork/runner.py lines 1138-1200 first: `timeout_text` is composed after the tool loop, and the `nudge` record is written + delivered at TWO mutually exclusive points — the `pending_finish` branch and the end-of-turn `deliver(...)` — and never on a turn that ends the run. Mirror that, do not invent a third path. Rule: marker strings only by concatenation.

1. dirtywork/runner.py, right after TIMEOUT_NUDGE (line ~286-290), add the constant (a str.format template; it is NOT a key of the NUDGES dict — do not touch NUDGES):

NAME_RECOVERED_NUDGE = (
    "Your tool call's name was `{raw_head}…{marker}{tool}` — {cut} characters of text "
    "before the tool-call marker. The harness ran `{tool}` with the arguments you gave. "
    "Emit tool calls only through the tools API: the name field holds the tool name and "
    "nothing else.")

2. Composition (line ~1148, right after `timeout_text = ...`): add

            name_recovered_text = None
            if first_recovered is not None:
                raw_name, marker, tool, cut = first_recovered
                name_recovered_text = NAME_RECOVERED_NUDGE.format(
                    raw_head=raw_name[:40].replace("\n", "⏎"), marker=marker, tool=tool, cut=cut)

3. The `pending_finish` branch (line ~1150-1167): today it writes a timeout record and joins `_join_nudges(sandbox_text, timeout_text)` when `timed_out_this_turn`, else joins sandbox text alone. Restructure so both nudges are handled: 
   
                sandbox_text, sandbox_records = drain_sandbox()
                extra_records = []
                timeout_record = None
                if timed_out_this_turn:
                    timeout_record = self.transcript.write("nudge", kind="timeout", turn=turns)
                    extra_records.append(timeout_record)
                recovered_record = None
                if name_recovered_text is not None:
                    recovered_record = self.transcript.write("nudge", kind="name_recovered", turn=turns)
                    extra_records.append(recovered_record)
                text = _join_nudges(sandbox_text, timeout_text, name_recovered_text)
                if text:
                    deliver(text, [*sandbox_records, *extra_records])
                return None

   Keep the order sandbox → timeout → name_recovered in both the text and the records.

4. The end-of-turn delivery (line ~1183-1199): after the `timeout_record` block add

            recovered_record = None
            if name_recovered_text is not None:
                recovered_record = self.transcript.write("nudge", kind="name_recovered", turn=turns)

   and change the deliver call to `deliver(_join_nudges(malformed_text, sandbox_text, timeout_text, name_recovered_text, stall_text, nc_text), [r for r in (malformed_record, *sandbox_records, timeout_record, recovered_record, stall_record, nc_record) if r is not None])` — the new nudge sits after timeout and before stall.

5. Nothing is written or delivered on a turn that ends the run (finish accepted, stuck, stall verdict, change-guard end): those paths return before either point, exactly as timeout's do — do not add writes there.

6. tests/test_runner.py (import TOOL_CALLS from markers; polluted call = `_call("c1", "exit code: 0" + TOOL_CALLS + "bash", {"command": "echo hi"})`):
   a. test_recovered_name_nudge_rides_the_turns_last_tool_result: one turn with the polluted bash call and then a clean `_call("c2", "bash", {"command": "echo two"})` in the SAME response, then a finish → the nudge event has kind "name_recovered" and via "tool_result"; the follow_up text (the `follow_up` field of the LAST tool_result of that turn — the clean one) contains "The harness ran `bash`" and the polluted call's own tool_result has no follow_up; exactly one name_recovered nudge for the turn.
   b. test_two_polluted_calls_on_one_turn_produce_one_nudge: two polluted calls in one response → one name_recovered nudge; the follow_up names the FIRST call's head (make the two prefixes differ and assert the first appears in the follow_up).
   c. test_recovered_name_nudge_orders_after_timeout: a turn with a polluted bash call whose command is "sleep 5" with timeout 1 (a timed-out command; copy how the file's timeout tests script that) → the last tool_result's follow_up contains the timeout text before the name_recovered text; the nudge events for the turn are ["timeout", "name_recovered"] in transcript order.
   d. test_recovered_finish_that_completes_writes_no_nudge: a single response whose only call is `_call("f", "prose" + TOOL_CALLS + "finish", {"summary": "done"})` after a turn that changed a file (script a write_file first so the change guard accepts the finish) → status "completed", no nudge event of kind name_recovered, and the finish's tool_result has tool == "finish" and tool_raw == the polluted name.
   e. test_recovered_finish_refused_by_the_change_guard_gets_the_nudge: a fresh run whose first response is the polluted finish with no prior change → the first finish is refused (its result is the #66 plain-mode text), and that same tool_result carries a follow_up containing "The harness ran `finish`" with a name_recovered nudge event via "tool_result"; then a second clean finish completes the run.
   f. test_three_polluted_calls_over_three_turns_nudge_each_turn: three turns each with one polluted call → three name_recovered nudge events, one per turn, and status "completed" after a finish.

7. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider tests/test_runner.py` until green, then the whole suite, then call finish(summary=...) naming the constant, the two delivery points and the six tests. Do not touch docs/. Do not touch bench.py or the harvest.
```

- [ ] Launch, review (two delivery points only; order; no write on ending turns; `NUDGES` untouched — the bench pin still holds), suite green, merge, ledger row.

---

### Task T3: bench `NUDGE_KINDS`, harvest S6 + `recovered` column

**Files:**
- Modify: `dirtywork/bench.py:48`; `tools/soak_harvest.py` (docstring `:6-8`, `PER_RUN_COLUMNS:36-37`, `_per_run_row` `~:460-470`, `detect_features` `~:179-260`)
- Test: `tests/test_bench.py:850-880`, `tests/test_soak_tools.py`

**Interfaces:**
- Consumes: transcript `nudge.kind == "name_recovered"`, `tool_result.tool_raw` (T2a/T2b); `toolspec.TOOL_CALL_MARKERS` (T1).
- Produces: `bench.NUDGE_KINDS[-1] == "name_recovered"` (len 11); harvest per-run column `recovered`; feature code `S6`.

**Brief (verbatim):**

```
Issue #67, task T3 of 8: bench and the soak harvester learn the new nudge kind, and the harvester learns to count recoveries and to flag S6. Spec §3.5. Rule: marker strings only by concatenation ("[" + "TOOL_CALLS]"); in tools/soak_harvest.py import TOOL_CALL_MARKERS from dirtywork.toolspec instead of spelling any marker.

1. dirtywork/bench.py line ~48: NUDGE_KINDS gains "name_recovered" APPENDED AT THE END (10 → 11 entries). EMPTY_REPLY_NUDGE_KINDS is unchanged (this nudge records no failure). The summarize legend derives from NUDGE_KINDS — verify it prints eleven names.
   tests/test_bench.py: the pin at ~line 854 `NUDGE_KINDS[-2:] == ("no_change", "unchanged_finish")` becomes `NUDGE_KINDS[-3:] == ("no_change", "unchanged_finish", "name_recovered")`; ~857 `len == 10` → 11; the test named ..._ten_kinds (~875) is renamed ..._eleven_kinds and its exact-cell assertion widens from "0/0/0/0/0/0/3/0/0/0" to "0/0/0/0/0/0/3/0/0/0/0" (it splits the s1 detail row into cells and compares the nudges cell exactly — keep that form).

2. tools/soak_harvest.py:
   a. Module docstring lines 6-8: the feature list "(F1/F2/F3/F4/F5/F7/F8/F9/F10; ...)" gains S6 and S14: "(F1/F2/F3/F4/F5/F7/F8/F9/F10, S6, S14; ...)". One-line change.
   b. PER_RUN_COLUMNS (line 36-37): insert "recovered" after "stray kills" (before "tool mix").
   c. Wherever the per-run row dict is built (the function that returns "stray kills": h.get("stray_kill_count"...) — find "stray kills" around line 468 and the code that counts stray kills from events): add "recovered": the count of tool_result events that have a "tool_raw" key. Compute it from the events list the same way the stray-kill count is computed (mirror that code path; if counts come from `_event_counts`/`_harness_*`, add the count there). A run with no such events shows 0.
   d. detect_features (line ~179-260): add feature code "S6" that fires when ANY of: a nudge event with kind "name_recovered"; a tool_result event with a "tool_raw" key; a tool_result event whose "result" starts with "ERROR: unknown tool" and whose "tool" contains any marker from TOOL_CALL_MARKERS (from dirtywork.toolspec import TOOL_CALL_MARKERS). Do not fold it into F8 or any other code.

3. tests/test_soak_tools.py (import TOOL_CALLS from markers): 
   a. test_s6_fires_on_each_trigger: three separate event lists — [a name_recovered nudge], [a tool_result with tool "bash" and tool_raw "x" + TOOL_CALLS + "bash"], [a tool_result with tool ("p" * 130 + TOOL_CALLS + "nope") and result "ERROR: unknown tool 'p…'"] — each fires S6 (use BASE_RUN_JSON as the other tests do); a clean run (a bash tool_result, no tool_raw, no nudge) does not.
   b. test_recovered_column_counts_tool_raw: events with two tool_results carrying tool_raw and one without → the per-run row's "recovered" == 2; "recovered" in harvest.PER_RUN_COLUMNS; a row built from a run_json with no events has "recovered" == 0 (mirror the three stray-kill assertions at lines ~1031-1033).

4. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider tests/test_bench.py tests/test_soak_tools.py` until green, then the whole suite, then call finish(summary=...). Do not touch docs/.
```

- [ ] Launch, review (kind appended; the exact-cell test kept exact; S6 not folded into F8; the harvest imports the tuple), suite green, merge, ledger row.

---

### Task D1: the contract docs and the schema test

**Files:**
- Modify: `docs/transcript-schema.md` (`:27-32`, `:72`, `:82`, `:86`, `:114-119`, `:126`, `:245`, `:342`), `docs/machine-contract.md` (`:460-464`), `docs/operating.md` (troubleshooting list, after the `File size limit exceeded` entry), `README.md` (only if it enumerates nudge kinds)
- Test: `tests/test_transcript_schema.py` (`NUDGE_KINDS` list `:20`; `test_a_real_run_emits_the_documented_events` `:78`)

**Interfaces:**
- Consumes: everything above. Produces: nothing code-side.

**Brief (verbatim):**

```
Issue #67, task D1 of 8: the documentation ripple for the recovered tool name — one nudge kind, one sparse field, the name caps — and the schema test that checks it. Spec §3.5. In prose, spell the Devstral token as it is (docs are not model-edited), but in any code example inside the docs build it by concatenation.

1. docs/transcript-schema.md:
   a. Lines 27-32 (the schema_version 2 additive rule): append one sentence: "1.0 (#67) adds one more `nudge.kind` (`name_recovered`), one sparse `tool_result` field (`tool_raw`) and caps every recorded tool name (`assistant.tool_calls[].name`, `tool_result.tool`, `last_tool_result.tool`) at 200 characters head-and-tail (the first 120, `…`, the last 80 — 201 characters when capped)."
   b. Line 72 (`tool_calls` row): after "capped at 2000 chars" add "; `name` is capped head-and-tail at 200 chars (1.0/#67)".
   c. Line 82 (`tool` row): the enumeration becomes open: "tool name — one of <the existing list>; or, on an `unknown_tool` error, the name the model sent, capped head-and-tail at 200 chars (1.0/#67); or `""` for a discarded malformed entry".
   d. A new row right after the `tool` row: | `tool_raw` | | ✓ | string | 1.0 (#67): **sparse** — present only when the name the model sent was recovered: it carried stray text and a tool-call marker before a registered tool name (Devstral's `[TOOL_CALLS]`), the harness dispatched that tool, and this is the raw name, capped head-and-tail at 200 chars. `tool` holds the recovered name. |
   e. Line 86 (`follow_up` row) and lines 114-119 (the merge-order sentences): insert `name_recovered` after `timeout` and before `stall` in the tool-result order ("`malformed_entry`, sandbox notices, `timeout`, `name_recovered`, `stall`, `no_change`"); the text-turn order is unchanged (the nudge only rides tool results); the verify-feedback clause ("or `timeout` alone on a verify-feedback turn") becomes "or `timeout` and/or `name_recovered` on a verify-feedback turn".
   f. Line 126 (`kind` row): add `name_recovered` (1.0, #67: a tool call's name carried stray text and a tool-call marker before a registered tool name; the harness ran that tool — once per turn, on the turn's last tool result, never on a turn that ends the run).
   g. Lines 245 and 342 (`last_tool_result` rows): "`tool` capped head-and-tail at 200 chars (1.0/#67)".
2. docs/machine-contract.md lines ~460-464: the kinds string gains `|name_recovered` at the end; "ten kinds" → "eleven kinds"; add after the #66 clause: "`name_recovered` (1.0, #67) is a tool call whose name carried stray text and a marker before a real tool name — the harness ran that tool and says so once per turn"; where the `assistant`/`tool_result` caps are described (~line 460-461) add "tool names capped head-and-tail at 200 chars".
3. docs/operating.md, the troubleshooting list (find the entry starting "- **`File size limit exceeded` / exit 153"): add a new entry after it: "- **`aborted after 3 consecutive unknown_tool failures` on Devstral, with tool names that end in `[TOOL_CALLS]bash`** — 0.10 counted each as an unknown tool. 1.0 (#67) recovers the call: the tool after the last marker runs with the arguments given, the transcript's `tool_result` shows `tool: bash` and the raw name in `tool_raw`, and the model is told once per turn (`nudge` kind `name_recovered`) to emit clean calls. If a name has a marker but no real tool after it, it is still an unknown-tool failure."
4. README.md: grep for "unchanged_finish" — if the README lists nudge kinds, add `name_recovered` there in the same style; if it does not, leave README alone.
5. tests/test_transcript_schema.py: add "name_recovered" to the NUDGE_KINDS list at line ~20. In test_a_real_run_emits_the_documented_events (line ~78) — it runs a real Runner with a scripted provider and checks every emitted event/field against the doc's tokens — extend its script with one marker-polluted bash call (`_call("cX", "prose " + ("[" + "TOOL_CALLS]") + "bash", {"command": "echo hi"})`, building the marker by concatenation as shown, or import TOOL_CALLS from markers) so the run emits a tool_result with tool_raw and a name_recovered nudge, and add assertions that "tool_raw" and "name_recovered" are in _doc_tokens() and that every key of that tool_result event is a documented token (look at how the test already checks keys for other events and do the same).
6. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider tests/test_transcript_schema.py` until green, then the whole suite, then call finish(summary=...) listing the doc lines you changed.
```

- [ ] Launch, review (every listed line touched; the order sentences; the open `tool` row; the schema test's new assertions), suite green, merge, ledger row.

---

### Task C1: acceptance — the six Devstral F5 rows (Claude)

- [ ] From the integration worktree at the post-D1 HEAD, with nothing else on the GPU: `nohup python3 tools/soak_driver.py docs/superpowers/bench/2026-08-26-issue-67-f5-devstral-plan.jsonl --out $SCRATCH/f5-67-dev.jsonl`; a monitor per row.
- [ ] Score: **pass** = no run ends `aborted after 3 consecutive unknown_tool failures`; per run record status, `python3 tools/soak_harvest.py $SCRATCH/f5-67-dev.jsonl` → the `nudges` and `recovered` columns (fallback `grep -c '"tool_raw":' <run_dir>/transcript.jsonl`), strict-check verdict, wall, turns. A run that still aborts on `unknown_tool` fails C1 and returns to spec §3.1 (what shape did the name have?).
- [ ] Ledger rows + a verdict row.

### Task C2: closure (Claude)

- [ ] Full unit suite and the live docker suite (`DIRTYWORK_LIVE_IMAGE=dirtywork-worker-pytest:0.11 DIRTYWORK_LIVE_SLOW=1 … -m docker`) at the final code HEAD.
- [ ] Stop the sampler; ledger totals + a scoreboard row for `#67` (the first guarded build: count `unchanged`/`unchanged_finish` under the released runtime).
- [ ] PR "Closes #67", milestone `1.0.0 — contract freeze`, body from the ledger; Jim approves the merge.

---

## Self-review

- **Spec coverage**: §3.1 → T1; §3.2 recovery/caps/`tool_raw` → T2a; §3.2 nudge → T2b; §3.3 → T1 step 2; §3.4 → T1 step 4; §3.5 bench/harvest → T3, docs → D1; §5 tests 1 → T1, 2 → T2a/T2b, 3 → T1, 4 → T2a, 5 → T3, 6 → T3, 7 → D1; §6 → C1/C2; §0.2 `tests/markers.py` → T0.
- **Placeholders**: none — the plan carries every brief, test and command in full.
- **Type consistency**: `recover_name -> (str, str | None, int)` in T1 is what T2a unpacks (`name, marker, cut`); `first_recovered = (raw_name, marker, name, cut)` in T2a is what T2b unpacks in that order; `cap_name` defined in T2a is used in T2a only (T2b reads `first_recovered`, not names); `TOOL_CALL_MARKERS` from T1 is what T3's harvest imports.
