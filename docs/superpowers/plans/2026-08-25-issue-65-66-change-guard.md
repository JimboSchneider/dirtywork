# #65/#66 Cap-aware truncation, truncation budget, change guard: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **This repository's execution rule overrides the line above.** Every code task below is built
> by the **released dirtywork (0.10.1) running against this repository** with a local worker
> (`qwen/qwen3-coder-next` via LM Studio) — Claude writes the brief, reviews the branch, runs the
> host suites, feeds back through `dirtywork resume --feedback-file`, and writes the prose docs.
> A Claude implementer touches code only after a worker resume-with-feedback has failed, and the
> PR says so. Owner approval is needed for the merge and the release, never assumed.

**Plan v1** (2026-08-25 18:45 CDT), written from spec v4 (owner-reviewed 18:31 CDT, approved for
this step). Calibration from #61: a ~190-line change took one 60-turn run plus one to three
resumes; a feedback resume that reads a `completed` tail finishes with zero change about one time
in three (the S14 shape this build removes — every such resume is an A/B data point, §8 of the
spec). Tasks are therefore sized for one run each, and the two largest (`runner.py`'s guard, the
CLI/resume half) are split.

**Goal:** Every truncation message tells the model the cap, what arrived and a per-call target;
six cut-off replies end a run; a byte-identical write is not progress; and a completion that
changed nothing in the worktree is rejected once and, on a feedback resume, ends the run
`unchanged` instead of `completed`.

**Architecture:** `runner.py` gains `reply_size`/`call_size`/`chunk_target`, a run-scoped
`truncations` counter with a six-reply abort, a `take_fingerprint` closure used at run start, on
both completion paths (before verify), every K turns and first thing in `finish()`, and
`require_changes` / `no_change_turns` constructor arguments. A new `dirtywork/changes.py` holds the
bash-only fingerprint script (scratch index + scratch object store with the real store as an
alternate; every nested `.git` excluded from its parent and snapshotted separately), its parser
(sorted hash lines; fail-open with a reason), the guard texts and `DEFAULT_NO_CHANGE_TURNS`.
`tools.py` gains `parse_change_head`/`net_change` (and inherits `parse_exit_code`); `resume.py`
reorders the resume block (tail → status → feedback → finish sentence); `__main__.py` wires
`require_changes`, the three new contract fields, one stderr line and the `unchanged` resume gate;
bench/harvest/runs count and render the two nudge kinds, the status and the fields.

**Tech Stack:** Python 3.9 stdlib only; bash (both sandboxes run `bash -c`; macOS 3.2 on the
host, 5.x in the image); git 2.39 in `dirtywork-worker-pytest:0.10`; pytest; the runner doubles in
`tests/test_runner.py` / `tests/provider_doubles.py`; `tools/soak_driver.py` for the F5 reruns.

**Spec:** `docs/superpowers/specs/2026-08-25-cap-aware-truncation-and-change-guard-design.md`
(v4, `14f6e35`). Section numbers below refer to it. The spec is committed on the integration
branch, so the worker can `read_file` it; every brief still carries the exact code and text it
needs.

## Global Constraints

- Python 3.9 floor, stdlib only; `from __future__ import annotations` in every new module;
  every change is additive under `schema_version` 2 — no field renamed or removed, no new event
  name, no new `nudge.via` value — spec header, §2 (8).
- `FINGERPRINT_SCRIPT` is **bash-only** and byte-exact as §4.1 prints it (P4 validated it; the
  `EXIT` trap is the only post-probe addition). It runs through `sandbox.bash` — the seam
  `--verify` uses — never through a new sandbox method — §4.1.
- `parse_exit_code` lives in `dirtywork/tools.py` after W2a; `runner.py` and `changes.py` import
  it from there (no cycle) — §4.1.
- The truncation counter increments **once per turn**; `MAX_TRUNCATED_REPLIES = 6`; the
  consecutive-failure reason wins when both fire on the same turn — §3.3.
- The change check runs **before** verify on both completion paths; a rejected completion never
  runs the verify command; the finish record keeps its call position and only its `result` text is
  rewritten (#60) — §4.3, §5.1.
- Every `changed: null` produced by an attempted-and-failed or raising measurement carries
  `changed_reason`; the CLI (never the `Runner`) prints `dirtywork: change guard off: <reason>` —
  §4.1, §5.2.
- `finish()` measures first (before `drain_sandbox()`), and not for `interrupted`, `timeout`,
  `budget_exceeded`, `sandbox_error`, nor when this turn already measured — §4.1 (4).
- Texts are constants; wording is not contract but the **numbers** are (invariant 1); the
  `no_change` text never names `finish` when `require_changes` and the tree equals `fp_start` —
  §3.2, §4.4.
- DRY & SOLID (owner's standing rule): one target function, one parser, one fingerprint call site
  (`take_fingerprint`), one `net_change` parser next to `describe_change`; nothing duplicated
  between the two truncation texts beyond the numbers.
- The worker never edits `docs/**`; prose docs are Claude's (D1 first, D2 last). The worker
  edits `tests/**`, `dirtywork/**`, `tools/soak_harvest.py`.
- Host pytest interpreter is `/usr/bin/python3` (3.9, pytest 8.4); Homebrew pythons lack pytest.

## Execution model (every W task)

- **Scratchpad** (absolute; pin it in a new session):
  `SCRATCH=/private/tmp/claude-501/-Users-jimschneider-repos-dirtywork/d9da59a0-7dac-4aaa-9697-28e33a342e2b/scratchpad`
  — holds `run6566.sh`, the briefs `brief-6566-<task>.md` (extracted verbatim from this plan's
  fenced blocks), `feedback-6566-<task>-r<n>.md`, `metrics-6566.csv`, `f5-plan.jsonl` (C5),
  `redteam6566.json` (the spec's red-team, for reference).
- **Run command** (`$SCRATCH/run6566.sh $SCRATCH/brief-6566-<task>.md`), which is:

  ```bash
  #!/bin/bash
  # run6566.sh BRIEF_FILE [extra dirtywork args...] — one #65/#66 dogfood run with the plan's flags.
  set -u
  BRIEF="${1:?brief file}"; shift
  cd /Users/jimschneider/repos/dirtywork || exit 2
  pipx run --spec 'dirtywork==0.10.1' dirtywork run "$(cat "$BRIEF")" \
    --repo /Users/jimschneider/repos/dirtywork --branch-from issue-65-66-change-guard \
    --model qwen/qwen3-coder-next --sandbox docker --image dirtywork-worker-pytest:0.10 \
    --verify "env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider" \
    --verify-rounds 2 --max-turns 60 --timeout 1800 "$@" >"$BRIEF.out" 2>"$BRIEF.err"
  rc=$?
  echo "rc=$rc"; python3 -c "import json,sys; d=json.load(open('$BRIEF.out')); print({k:d.get(k) for k in ('status','turns','final_message','slug','transcript','worktree')})" 2>/dev/null || tail -5 "$BRIEF.err"
  exit $rc
  ```

  The verify gate keeps `env -u …` for **every** run of this build: the worker executes inside
  the *released* 0.10.1 sandbox (S13 is fixed on `main` since #74 but not in the released runtime).
  The released runtime also has **no change guard**: a zero-change feedback resume still ends
  `completed` there — record each one in the ledger's S14 A/B row (§8) and re-send the feedback.
- **Chaining:** each run branches from `issue-65-66-change-guard`; after review Claude commits
  the export on the run's branch (`worker export verbatim: run <slug>`), adds its own fix commits,
  fast-forwards `issue-65-66-change-guard` to it from `.worktrees/issue-65-66-change-guard`
  (`git rebase issue-65-66-change-guard` in the run worktree first when integration moved, then
  `git merge --ff-only dirtywork/<slug>`), removes the run worktree (`git worktree remove
  .worktrees/dw-<slug>`) and deletes the run branch **from the integration worktree**. Tasks run
  strictly in the order listed.
- **Review loop:** read `~/.dirtywork/runs/<slug>/run.json` + transcript; diff the run worktree
  against the brief and the spec section; grep the tests for the brief's literal cases (#61
  lesson: the model inverts a rule and writes the test to match); run the host suite in the run
  worktree (`/usr/bin/python3 -m pytest -q -p no:cacheprovider`); gaps → `dirtywork resume <slug>
  --feedback-file <file> --max-turns 40` (verify inherited), feedback that names a file, a line
  and a shell check per item, at most two resumes; then Claude finishes leftovers and says so in
  the ledger and the PR.
- **Metrics:** `tools/soak_sampler.sh $SCRATCH/metrics-6566.csv` started in C0 and stopped in
  C5; one ledger row per run (status, turns, wall, s/turn, prompt/completion tokens, tok/s,
  nudges, guardrail blocks, resets, tool mix, verify outcome) appended to a new `## #65/#66`
  section of `docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md`.
- Give qwen ≥ 60 turns; resumes burn turns on `read_file`, so feedback names files and lines;
  escape `.` in grep checks.

## File structure

| file | responsibility after this plan |
|---|---|
| `dirtywork/runner.py` | `reply_size`, `call_size`, `chunk_target`, the truncation texts and dict, `truncations` + abort (W1a/W1b); `take_fingerprint`, the change check in `check_verify`, `check_no_change`, `finish()` measurement, `require_changes` / `no_change_turns` (W3a/W3b); `ProgressTracker` net-change rule (W2a) |
| `dirtywork/changes.py` (new) | `FINGERPRINT_SCRIPT`, `FINGERPRINT_TIMEOUT`, `parse_fingerprint`, `fingerprint`, the four guard texts, `DEFAULT_NO_CHANGE_TURNS` (W2b) |
| `dirtywork/tools.py` | `parse_exit_code` (moved), `parse_change_head`, `net_change` (W2a) |
| `dirtywork/resume.py` | block order + texts (W3c) |
| `dirtywork/__main__.py` | `truncations` seed/contract field (W1a); `require_changes`, `changed`/`changed_reason` seeds, stderr line, `unchanged` resume gate (W3c) |
| `dirtywork/bench.py` | `_abort_kind` cut-off form (W1b); `NUDGE_KINDS`, status tuples (W4a) |
| `tools/soak_harvest.py` | `_TRUNCATED_CALL_RESULT_RE` (W1b); `S14` feature (W4a) |
| `dirtywork/runs.py` | `MD_RESULT_FIELDS` (W4a) |
| `tests/provider_doubles.py`, `tests/test_runner.py` | `FingerprintSandbox`, `git_parts` (W2b); pins and doubles (W1a, W3a) |
| `tests/test_changes.py` (new) | tests 10, 11, 11b (W2b) |
| `tests/test_transcript_schema.py` | `RUN_END_FIELDS` (W1a, W3a), `STATUSES`, `NUDGE_KINDS` (W3a) |
| `tests/test_main.py`, `tests/test_resume.py` | CLI/resume tests and the two-turn consequence (W3c) |
| `tests/test_bench.py`, `tests/test_soak_tools.py`, `tests/test_runs.py` | evidence tests (W1b, W4a) |
| `tests/test_docker_live.py` | test 24 (W4b) |
| `docs/transcript-schema.md` | D1 (Claude, before any code lands) |
| `docs/machine-contract.md`, `docs/operating.md`, `README.md`, the ledger | D2 (Claude) |

---

### Task C0: Baseline and instrumentation (Claude)

- [ ] Baseline suite in `.worktrees/issue-65-66-change-guard` (`/usr/bin/python3 -m pytest -q -p
  no:cacheprovider`); record the count in the ledger header.
- [ ] `dirtywork-worker-pytest:0.10` present (`docker images`); `qwen/qwen3-coder-next` loaded
  (`curl -s http://localhost:1234/v1/models`); `pipx run --spec 'dirtywork==0.10.1' dirtywork
  --version` answers.
- [ ] Write `$SCRATCH/run6566.sh` (the script above), `chmod +x`; start the sampler
  (`tools/soak_sampler.sh $SCRATCH/metrics-6566.csv`, pid file beside it).
- [ ] Open the ledger section `## #65/#66 — cap-aware truncation, truncation budget, change guard
  (2026-08-25)` with the same run-row table header as the `#61` section, plus an "S14 A/B" line
  (feedback resumes: first completion zero-change yes/no, counted as they happen).
- [ ] Extract every brief below into `$SCRATCH/brief-6566-<task>.md` verbatim.

---

### Task D1: Docs — the transcript schema first (Claude)

**Files:**
- Modify: `docs/transcript-schema.md` — the `nudge.kind` row (`:101-119`): `no_change`,
  `unchanged_finish` with §5.1's carrier note; the `follow_up` row (`:84`) and the nudge prose
  (`:104-111`): `no_change` inserted in the joined-text orders of §5.1; the forward-compat
  paragraph (`:22-31`): "#65/#66 add two `nudge.kind` values, one status and three `run_end`
  fields"; `run_end` rows `truncations` (integer, always), `changed` (boolean or null, always),
  `changed_reason` (string, sparse) beside `timeouts` (`:239-241`) with §5.1's definitions; the
  Statuses table (`:243-258`): `unchanged`; the `run.json` field table (`:325-332`) and the
  stdout-JSON key list (`:269`): the three fields; the `tool_result` prose for `finish`: the four
  new `result` texts (`Not accepted as the end of the run: …` ×2, `run not finished: nothing
  changed`, `run not finished: change check could not run (…)`).

- [ ] **Step 1:** write the rows exactly per spec §5.1/§6; every new token backticked (the
  doc-token tests read backticked identifiers).
- [ ] **Step 2:** `/usr/bin/python3 -m pytest -q tests/test_transcript_schema.py` still green (the
  lists are unchanged until W1a/W3a; documenting a field before it is emitted breaks nothing).
- [ ] **Step 3:** commit on `issue-65-66-change-guard`: `docs(schema): #65/#66 fields, kinds and
  status (spec §5.1)`.

---

### Task W1a: #65 — the numbers, the texts, the counter (spec §3.1–§3.3 minus the abort)

**Files:**
- Modify: `dirtywork/runner.py:89-99` (`NUDGES`), `:146-160` (`truncated_call_result`),
  `:546-549` (run-scoped counters), `:671-704` (`finish()` extra), `:841-860` (text path),
  `:877-898` (tool loop cases a/b); `dirtywork/__main__.py:590-591` (`_seed_payload`), `:609`
  (`_contract_fields`); `tests/test_transcript_schema.py:26-31` (`RUN_END_FIELDS`);
  `tests/test_soak_tools.py:939`.
- Test: `tests/test_runner.py` (pins at `:283-299`, `:361-373`, `:440-461`, `:473`, `:487`,
  `:501`, `:573-577`, `:667-671`, `:921-930`; new tests 1–5).

**Interfaces:**
- Produces (`dirtywork.runner`): `MIN_CHUNK_CHARS = 200`, `MIN_CHUNK_LINES = 5`,
  `CHUNK_DIVISOR = 4`, `DEFAULT_LINE_CHARS = 60`, `MAX_TRUNCATED_REPLIES = 6`,
  `reply_size(resp) -> tuple[int, int]`, `call_size(tc) -> tuple[int, int]`,
  `chunk_target(max_tokens: int, cut_chars: int, cut_lines: int) -> tuple[int, int]`,
  `truncated_call_result(tool: str, raw_arguments, trunc: dict) -> str`, `NUDGES["truncated"]`
  as a `str.format` template with fields `cap cap_chars received cut_chars cut_lines target_chars
  target_lines n max`; `run_end.truncations` (int, always); `RunResult.extra["truncations"]`.

- [ ] **Step 1: Brief** `$SCRATCH/brief-6566-w1a.md`:

```
Issue #65 (cap-aware truncation), task W1a of 9. Make every truncation message carry the --max-tokens cap, what the harness received, a per-call target and a running count, and count truncations per run. No abort yet (that is W1b). Spec: docs/superpowers/specs/2026-08-25-cap-aware-truncation-and-change-guard-design.md §3.1-§3.3 (read §3.1 and §3.2 first; the code below is exact).

1. dirtywork/runner.py, next to CHARS_PER_TOKEN (line ~46), add:
MIN_CHUNK_CHARS = 200
MIN_CHUNK_LINES = 5
CHUNK_DIVISOR = 4
DEFAULT_LINE_CHARS = 60
MAX_TRUNCATED_REPLIES = 6

2. dirtywork/runner.py, next to truncated_call_result (line ~146), add these three functions exactly (docstrings included; `_tool_call_arg_chars` already exists at line ~426):
def reply_size(resp) -> tuple[int, int]:
    """(text_chars, raw_chars): what the harness RECEIVED -- the reply's prose
    and the raw argument strings of every addressable tool call
    (`_tool_call_arg_chars`). Reported to the model as `received`; never the
    target's basis."""
    return (len(resp.text or ""),
            sum(_tool_call_arg_chars(tc) for tc in resp.tool_calls))


def call_size(tc) -> tuple[int, int]:
    """(chars, lines) of ONE tool call's raw arguments -- the call that was
    cut (cases a/b). Newlines inside JSON string arguments arrive escaped
    (`\\n`), so both forms are counted. (0, 0) when the adapter kept nothing
    (Anthropic's error branch); the text path has no call and passes (0, 0)."""
    raw = tc.raw_arguments or ""
    chars = _tool_call_arg_chars(tc)
    return chars, (raw.count("\\n") + raw.count("\n") + 1) if chars else 0


def chunk_target(max_tokens: int, cut_chars: int, cut_lines: int) -> tuple[int, int]:
    """(characters, lines) a single tool call's content must stay under.
    Basis: the cap's character capacity (max_tokens * CHARS_PER_TOKEN), or the
    smaller of that and what the CUT CALL actually got out when its raw
    arguments are present (cut_chars > 0) -- that call's own ratio reflects
    how densely THIS model tokenizes THIS content. A quarter of the basis
    leaves room for JSON escaping, the call's other fields and any prose
    around it. Lines come from the cut call's own characters-per-line when it
    had enough lines to measure, else DEFAULT_LINE_CHARS."""
    cap_chars = max_tokens * CHARS_PER_TOKEN
    basis = min(cap_chars, cut_chars) if cut_chars > 0 else cap_chars
    chars = max(MIN_CHUNK_CHARS, basis // CHUNK_DIVISOR)
    per_line = (cut_chars / cut_lines
                if cut_chars > 0 and cut_lines >= 3 else DEFAULT_LINE_CHARS)
    return chars, max(MIN_CHUNK_LINES, int(chars / per_line))

3. Replace NUDGES["truncated"] (line ~90) with this template (the other two NUDGES entries are unchanged; they contain no braces):
    "truncated": ("Your reply was cut off at the --max-tokens cap of {cap} tokens (about "
                  "{cap_chars} characters); the harness received only {received} characters of "
                  "it — cut-off reply {n} of {max} (the run ends at {max}; three in a row also "
                  "end it). Keep each tool call's content under about {target_chars} characters "
                  "(about {target_lines} lines; split long lines if you must) and emit one tool "
                  "call at a time; for a large file, write_file the first part and append_file "
                  "the rest."),

4. Replace truncated_call_result with (signature gains `trunc: dict`; `_recovered_path` unchanged):
def truncated_call_result(tool: str, raw_arguments, trunc: dict) -> str:
    if tool == "write_file":
        path = _recovered_path(raw_arguments)
        if path is not None:
            return (f"ERROR: your write_file for {path!r} was cut off at the --max-tokens cap of "
                    f"{trunc['cap']} tokens after about {trunc['cut_chars']} characters "
                    f"(~{trunc['cut_lines']} lines) — nothing was written; cut-off reply "
                    f"{trunc['n']} of {trunc['max']}. Write the file in chunks of at most about "
                    f"{trunc['target_chars']} characters (about {trunc['target_lines']} lines): "
                    f"write_file with the first part, then append_file for each following part.")
    return (f"ERROR: your {tool} call was cut off at the --max-tokens cap of {trunc['cap']} tokens "
            f"after about {trunc['cut_chars']} characters (~{trunc['cut_lines']} lines) before it "
            f"completed — cut-off reply {trunc['n']} of {trunc['max']}. Keep each tool call under "
            f"about {trunc['target_chars']} characters (about {trunc['target_lines']} lines); for "
            f"a large file, write_file the first part and append_file the rest.")

5. In Runner.run: beside `timeouts = 0` (line ~549) add `truncations = 0   # spec #65 §3.3: turns with a truncation nudge or truncated_call_result; never reset`. Add `truncations` to the `nonlocal` line of one_turn. In finish()'s `extra` dict add `"truncations": truncations,` right after `"timeouts": timeouts,`.

6. In one_turn, right after `append_assistant(resp.text, tool_calls, finish_reason)`, add:
            trunc: dict = {}
            counted = False

            def note_truncation(tc=None) -> None:
                """Spec #65 §3.1: count this turn's truncation once and build
                the numbers both texts format. `tc` is the cut call (cases
                a/b) or None on the text path."""
                nonlocal truncations, counted
                if counted:
                    return
                truncations += 1
                counted = True
                text_chars, raw_chars = reply_size(resp)
                cut_chars, cut_lines = call_size(tc) if tc is not None else (0, 0)
                tc_chars, tc_lines = chunk_target(self.max_tokens, cut_chars, cut_lines)
                trunc.update({"cap": self.max_tokens,
                              "cap_chars": self.max_tokens * CHARS_PER_TOKEN,
                              "received": text_chars + raw_chars,
                              "cut_chars": cut_chars, "cut_lines": cut_lines,
                              "target_chars": tc_chars, "target_lines": tc_lines,
                              "n": truncations, "max": MAX_TRUNCATED_REPLIES})

7. Text path (line ~841-860): after `kind = classify_text_reply(content, finish_reason)` and the `if kind == "answer":` block, before `kind_record = self.transcript.write("nudge", kind=kind, turn=turns)`, add `if kind == "truncated": note_truncation()`. Change the delivery line to format the template: `deliver(_join_nudges(NUDGES[kind].format(**trunc), sandbox_text, stall_text), [kind_record, *sandbox_records, stall_record])` (str.format with an empty dict is a no-op for the two texts without fields).

8. Tool loop (line ~877-898): in case (a) — `if tc.error is not None:` … `if finish_reason == "length":` — call `note_truncation(tc)` then `result = truncated_call_result(name, tc.raw_arguments, trunc)`; in case (b) — `elif finish_reason == "length" and self._missing_required(name, args):` — the same two lines. Nothing else in the loop changes; a `length` turn whose calls all parsed completely is NOT a truncation (tests/test_runner.py:537-554 stays as is).

9. dirtywork/__main__.py: `_seed_payload` (line ~590) adds `"truncations": 0,` after `"timeouts": 0,`; `_contract_fields` (line ~609) returns `"truncations": extra.get("truncations", 0),` beside `"timeouts"`. tests/test_transcript_schema.py: append "truncations" to RUN_END_FIELDS (docs/transcript-schema.md already documents it).

10. tests/test_soak_tools.py line ~939 calls `truncated_call_result("write_file", raw)`: pass a third argument `dict(cap=1024, cap_chars=4096, received=3000, cut_chars=3000, cut_lines=55, target_chars=750, target_lines=13, n=1, max=6)` (the harvest regex is widened in W1b; this call must only keep the module importable and the write_file branch matching).

11. Rewrite the pins in tests/test_runner.py that compare the old wording: lines ~283-299 and ~921-930 compare `NUDGES["truncated"]` by identity — build the same dict the runner builds and compare against `NUDGES["truncated"].format(**d)` (for a text-only reply of N characters: cap=8192 by default, cap_chars=32768, received=N, cut_chars=0, cut_lines=0, target_chars=8192, target_lines=136, n=1, max=6); line ~361-373 (`NUDGES["truncated"] not in …`) → assert "cut off at the --max-tokens cap" not in the message; lines ~440-461 (the write_file hint and `_GENERIC_TRUNCATION`) and ~473/~487/~501 → the new texts formatted with the dict the runner would build (compute cut_chars/cut_lines with call_size on the same ToolCall the test sends); line ~573-577 (`test_truncated_nudge_names_write_file_and_append_file`) → assert the template contains "{cap}", "{received}", "{target_lines}", "cut-off reply {n} of {max}", "write_file the first part and append_file the rest"; line ~667-671 (exact `result.extra == {...}`) adds `"truncations": 0`.

12. New tests in tests/test_runner.py (names as given):
   test_chunk_target_cap_basis_and_floors: chunk_target(1024, 0, 0) == (1024, 17); (2048, 0, 0) == (2048, 34); (4096, 0, 0) == (4096, 68); (8192, 0, 0) == (8192, 136); (1024, 3000, 55) == (750, 13); (1024, 100, 2) == (200, 5) (floors); (1024, 300, 10) == (200, 6) (per-line from the call: 300/10 = 30 chars/line → 200/30 = 6).
   test_reply_size_and_call_size: reply_size of a response with text "abc" and two calls with raw_arguments '{"path":"x","content":"a\\nb"}' and '{}' == (3, sum of the two lengths); call_size of the first == (its length, 2); call_size of ToolCall(raw_arguments="") == (0, 0); a call with arguments but no newline → lines == 1.
   test_truncated_text_nudge_carries_the_numbers: FakeProvider([_resp(content="I will now", finish_reason="length"), _resp(content="ok")]), Runner(..., max_tokens=1234): the second request's last user message == NUDGES["truncated"].format(cap=1234, cap_chars=4936, received=10, cut_chars=0, cut_lines=0, target_chars=1234, target_lines=20, n=1, max=6); an empty `length` reply renders "received only 0 characters".
   test_truncated_call_results_carry_the_cut_calls_numbers: a `length` turn with `_bad_args("c", "write_file", raw='{"path": "x", "content": "a\\nb\\nc')` → the tool message equals truncated_call_result("write_file", raw, d) where d has cut_chars == len(raw) and cut_lines == 3 (2 escaped newlines + 1); the generic form for `_bad_args(name="read_file")`; two cut calls in one turn get the same `n` and the same dict; a turn with one complete call and one cut call sizes the target from the cut call.
   test_truncations_counts_once_per_turn: text path → truncations 1; a turn with two cut calls → +1 only; a `length` turn with a complete valid call → +0; run_end and result.extra carry `truncations`; `_seed_payload`/`_contract_fields` seed 0 (tests/test_main.py already checks the seeded keys — add "truncations" wherever "timeouts" is asserted there).

13. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` until green, then call finish with a summary. Do not add the abort; do not touch docs/.
```

- [ ] **Step 2: Run** `$SCRATCH/run6566.sh $SCRATCH/brief-6566-w1a.md`.
- [ ] **Step 3: Review** (texts byte-exact vs §3.2; `note_truncation` once per turn; both tool-loop
  cases call it with `tc`; the text path with `None`; `truncations` in `extra`; no abort added;
  the seven pins rewritten as described, not weakened; `test_soak_tools.py:939` still passes).
- [ ] **Step 4: Host suite** green in the run worktree; **Step 5:** commit export verbatim + Claude
  nits, ff-merge, ledger row (+ S14 A/B line for any resume).

---

### Task W1b: #65 — the six-reply abort; bench and harvest read it (spec §3.3, §5.2)

**Files:**
- Modify: `dirtywork/runner.py` (text path `:851-854`, tool loop abort `:945`);
  `dirtywork/bench.py:242-252` (`_abort_kind` + docstring); `tools/soak_harvest.py:92-102`.
- Test: `tests/test_runner.py` (test 6), `tests/test_bench.py` (test 7), `tests/test_soak_tools.py`
  (both generic wordings).

**Interfaces:**
- Consumes: `truncations`, `trunc`, `note_truncation` (W1a).
- Produces: `TRUNCATION_ABORT` (`dirtywork.runner`); `bench._abort_kind(...) == "truncated"` for
  the cut-off form; `soak_harvest._TRUNCATED_CALL_RESULT_RE` matching both wordings.

- [ ] **Step 1: Brief** `$SCRATCH/brief-6566-w1b.md`:

```
Issue #65, task W1b of 9. Six cut-off replies end a run; bench and the soak harvester understand the new wording. W1a already added reply_size/call_size/chunk_target, the texts, the per-turn note_truncation() and the `truncations` counter in dirtywork/runner.py. Spec §3.3, §5.2.

1. dirtywork/runner.py, next to MAX_TRUNCATED_REPLIES, add:
TRUNCATION_ABORT = ("aborted after {n} cut-off replies at --max-tokens {cap}: raise --max-tokens "
                    "or split the writes")

2. Text path (in one_turn, the no-tool-call branch): today it is
                kind_record = self.transcript.write("nudge", kind=kind, turn=turns)
                abort_reason = failures.record("empty_reply")
                if abort_reason is not None:
                    return finish("model_error", abort_reason)
   Add, right after that `if` block (so the consecutive-failure reason still wins):
                if truncations >= MAX_TRUNCATED_REPLIES and kind == "truncated":
                    return finish("model_error",
                                  TRUNCATION_ABORT.format(n=truncations, cap=self.max_tokens))
   (The nudge record was already written; it stays without `via`, exactly like the third strike.)

3. Tool loop: in both places W1a calls note_truncation(tc) — case (a) `if tc.error is not None:` and case (b) `elif finish_reason == "length" and self._missing_required(name, args):` — after `abort_reason = failures.record("malformed_args")` and the note_truncation/truncated_call_result lines, add:
                    if abort_reason is None and truncations >= MAX_TRUNCATED_REPLIES:
                        abort_reason = TRUNCATION_ABORT.format(n=truncations, cap=self.max_tokens)
   The truncated result is still produced, recorded in the transcript and appended to messages; the loop's existing `if abort_reason is not None: return finish("model_error", abort_reason)` ends the run after that.

4. dirtywork/bench.py `_abort_kind` (line ~246): keep `_ABORT_RE`; add a module-level `_CUTOFF_ABORT_RE = re.compile(r"aborted after \d+ cut-off replies")` and, in `_abort_kind`, before returning None when `_ABORT_RE` does not match: `if _CUTOFF_ABORT_RE.search(final_message): return "truncated"`. Extend `_abort_kind`'s docstring with one sentence: "`truncated` is the run-level budget of six cut-off replies (#65), not a consecutive count."

5. tools/soak_harvest.py: replace `_TRUNCATED_CALL_RESULT_RE` (line ~100) with
_TRUNCATED_CALL_RESULT_RE = re.compile(
    r"^ERROR: your (?:write_file for |\S+ call was cut off at the (?:token limit|--max-tokens cap)\b)")
   and update the comment above it (lines ~92-99): the harvester reads historical run dirs whose results carry the 0.10 wording ("…at the token limit before it completed.") as well as 1.0's ("…at the --max-tokens cap of N tokens after about …"), so both must match; the write_file branch is unchanged.

6. Tests:
   tests/test_runner.py test_six_cutoff_replies_end_the_run (the S3 shape): responses alternating `_resp(content="header", finish_reason="length")` and `_resp(tool_calls=[_call(f"w{i}", "write_file", {"path": "rows.csv", "content": f"row{i}\n"})])`, six truncations interleaved with five successful writes, Runner(max_tokens=1024): result.status == "model_error", result.final_message == TRUNCATION_ABORT.format(n=6, cap=1024), result.extra["truncations"] == 6, the run_end event has truncations 6, the last nudge event (kind "truncated") has no "via" key, the FailureTracker never reached 3 (each write reset it). Variant: three consecutive `length` text replies as the 4th-6th truncations → final_message == "aborted after 3 consecutive empty_reply failures" (the consecutive rule wins). Variant: the sixth truncation on the tool path (`_bad_args(...)` with finish_reason="length") → the transcript has that call's tool_result with the truncated text, then run_end model_error with TRUNCATION_ABORT.
   tests/test_bench.py: `bench._abort_kind(TRUNCATION_ABORT.format(n=6, cap=1024)) == "truncated"`; the existing `_abort_kind` assertions unchanged.
   tests/test_soak_tools.py: `detect_features` fires F5 for a run whose tool_result is the NEW generic text (build it with runner.truncated_call_result("read_file", "{", dict(cap=1024, cap_chars=4096, received=10, cut_chars=1, cut_lines=1, target_chars=1024, target_lines=17, n=1, max=6))) followed by a successful append_file, and still fires for the OLD generic text "ERROR: your read_file call was cut off at the token limit before it completed." kept verbatim in the existing fixtures at lines ~215/~294/~316 (do not rewrite those fixtures — they are the historical wording).

7. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` until green, then call finish with a summary. Do not touch docs/.
```

- [ ] **Step 2: Run**; **Step 3: Review** (abort placement after the consecutive check on both
  paths; the tool-path result is recorded before the abort; `_ABORT_RE` untouched; harvest regex
  matches both wordings — run `grep -c "cut off at the token limit" ~/.dirtywork/runs/*/transcript.jsonl | head`
  as a sanity check on old data); **Step 4: Host suite**; **Step 5:** merge, ledger row.

---

### Task W2a: `tools.py` — `parse_exit_code` moves, `parse_change_head`/`net_change`; `ProgressTracker` (spec §4.2)

**Files:**
- Modify: `dirtywork/tools.py:421-451` (below `describe_change`), `:995-1000`
  (`is_timeout_result` neighbourhood — `parse_exit_code` lands here); `dirtywork/runner.py:261-275`
  (remove `parse_exit_code`, import it), `:294-310` (`ProgressTracker.note_call`).
- Test: `tests/test_tools_files.py` (test 8), `tests/test_runner.py` (test 9).

**Interfaces:**
- Produces (`dirtywork.tools`): `parse_exit_code(result) -> int | None` (moved, same body),
  `parse_change_head(result: str) -> tuple[str, str, int, int] | None`,
  `net_change(result: str) -> bool | None`.

- [ ] **Step 1: Brief** `$SCRATCH/brief-6566-w2a.md`:

```
Issue #66, task W2a of 9: a byte-identical write is not progress. Spec §4.2. Two small moves in dirtywork/tools.py and dirtywork/runner.py.

1. Move `parse_exit_code` from dirtywork/runner.py (line ~261, the function with the docstring "The integer after 'exit code: ' on a bash result's first line…") to dirtywork/tools.py, directly below `is_timeout_result` (line ~994), body unchanged. In runner.py delete the definition and add `parse_exit_code` to the existing `from .tools import (...)` import; its two call sites (RepeatTracker.note_bash line ~362, run_verify line ~731) are unchanged. grep -rn "parse_exit_code" tests/ dirtywork/ must show only tools.py's definition and runner.py's import + two uses.

2. dirtywork/tools.py, directly below `describe_change` (line ~421-451; keep them adjacent — the parser must track the producer), add:
_CHANGE_HEAD_RE = re.compile(
    r"^(Wrote|Edited|Appended to|Inserted into|Applied \d+ edits? to) (.+): \+(\d+) -(\d+)"
    r"(?: \(removed \d+ non-blank lines?\))?$")
_NEW_FILE_HEAD_RE = re.compile(r"^Wrote \d+ bytes to (.+) \(new file, \d+ lines?\)$")


def parse_change_head(result: str) -> tuple[str, str, int, int] | None:
    """(verb, path, added, deleted) from the head line of a describe_change /
    describe_write result, or None for any other string (an ERROR:/BLOCKED:
    result, the `(diff omitted: file too large)` head, a capped or foreign
    string). The regex tracks describe_change's head format above."""
    if not isinstance(result, str):
        return None
    head = result.split("\n", 1)[0]
    match = _CHANGE_HEAD_RE.match(head)
    if match is None:
        return None
    verb, path, added, deleted = match.groups()
    return verb, path, int(added), int(deleted)


def net_change(result: str) -> bool | None:
    """Did a mutating tool's result describe a net change? True for a new
    file or a head with added + deleted > 0, False for `+0 -0` (a
    byte-identical rewrite), None when the result is not a describe_change
    head at all -- the runner treats None as 'unknown, count it as progress'."""
    if not isinstance(result, str):
        return None
    if _NEW_FILE_HEAD_RE.match(result.split("\n", 1)[0]):
        return True
    parsed = parse_change_head(result)
    if parsed is None:
        return None
    return parsed[2] + parsed[3] > 0

3. dirtywork/runner.py ProgressTracker.note_call (line ~294): today
        if name in _MUTATING_TOOLS:
            self._progressed = True          # a successful write/edit is always progress
            return
   becomes
        if name in _MUTATING_TOOLS:
            # Spec #66 §4.2: a write that changed nothing (`+0 -0`) is not
            # progress; an unknown result shape still is (fail open).
            if net_change(result) is not False:
                self._progressed = True
            return
   (import net_change from .tools). Update the class docstring sentence about mutating tools accordingly.

4. Tests. tests/test_tools_files.py: test_parse_change_head_and_net_change_round_trip — for every verb the tools produce, build the result through the real producer: describe_change(path, old, new, verb=v) for v in ("Wrote", "Edited", "Appended to", "Inserted into", "Applied 1 edit to", "Applied 2 edits to") with (old="a\nb\n", new="a\nc\n") → parse_change_head returns (v, path, 1, 1) and net_change is True; with old == new → (v, path, 0, 0) and net_change is False; with a removal that yields the "(removed N non-blank line(s))" suffix (old="a\nb\n", new="a\n") → parsed and True; describe_write(path, None, "x\n", 2) (the new-file form) → parse_change_head None, net_change True; the "(diff omitted: file too large)" head (build it through describe_change with a new text longer than the diff cap — find the constant next to describe_change) → parse_change_head None, net_change None; "ERROR: no such file", "BLOCKED: …", "[output truncated at 10000 chars]" and "hello" → None/None.
   tests/test_runner.py: test_progress_tracker_ignores_noop_writes — t = ProgressTracker(stall_turns=4); t.note_call("write_file", {"path": "a"}, "Wrote a: +0 -0"); t.end_turn() is None and t.idle_turns == 1; t.note_call("write_file", {"path": "a"}, "Wrote a: +1 -0"); t.end_turn() is None and t.idle_turns == 0; the new-file form is progress; a result "weird" (unknown shape) is progress. test_identical_rewrites_stall — a Runner with stall_turns=4 and a provider that rewrites f.txt with its existing content ("data\n") every turn ends status "stalled" (the same call key repeats and the write is +0 -0); the existing ProgressTracker tests at lines ~982-1065 still pass.

5. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` until green, then call finish with a summary. Do not touch docs/.
```

- [ ] **Step 2: Run**; **Step 3: Review** (`parse_exit_code` has exactly one definition; the
  regex covers every verb — grep `verb=` in `tools.py` and compare; `None` keeps progress); **Step
  4: Host suite**; **Step 5:** merge, ledger row.

---

### Task W2b: `changes.py` — the fingerprint script, parser, texts; the doubles (spec §4.1, §4.3–§4.4 texts)

**Files:**
- Create: `dirtywork/changes.py`, `tests/test_changes.py`.
- Modify: `tests/provider_doubles.py` (`FingerprintSandbox`), `tests/test_runner.py:95-106`
  (`git_parts` fixture beside `parts`).

**Interfaces:**
- Produces (`dirtywork.changes`): `FINGERPRINT_TIMEOUT = 60`, `FINGERPRINT_SCRIPT` (str),
  `DEFAULT_NO_CHANGE_TURNS = 10`, `parse_fingerprint(result: str) -> tuple[str | None, str | None]`,
  `fingerprint(sandbox) -> tuple[str | None, str | None]`, `UNCHANGED_REQUIRED`, `UNCHANGED_PLAIN`,
  `NO_CHANGE_SINCE_START_REQUIRED`, `NO_CHANGE_SINCE_START_PLAIN`, `NO_CHANGE_RECENT` (the last
  three `str.format` templates with `{k}`); `tests.provider_doubles.FingerprintSandbox(worktree,
  hashes, *, max_worktree_mb=..., ...)`; the `git_parts` fixture.

- [ ] **Step 1: Brief** `$SCRATCH/brief-6566-w2b.md`:

```
Issue #66, task W2b of 9: the worktree fingerprint primitives and the test doubles the guard tasks (W3a/W3b) will use. Spec §4.1 (the script is byte-exact and probed; do not "improve" it), §4.3 and §4.4 for the texts. Nothing in the runner changes in this task.

1. Create dirtywork/changes.py:
"""The change guard's primitives (spec #66 §4.1): the worktree fingerprint
script, its parser, and the texts the runner delivers. The script runs
through `Sandbox.bash` -- the seam `--verify` uses -- in both sandbox modes.
"""
from __future__ import annotations

import re

from .tools import parse_exit_code

FINGERPRINT_TIMEOUT = 60
DEFAULT_NO_CHANGE_TURNS = 10
MAX_REASON_CHARS = 200

# bash-only (both sandboxes run `bash -c`; macOS /bin/bash 3.2 suffices: arrays,
# `read -d ''`, process substitution -- no mapfile). One exec, no recursion.
# Every repository under the worktree -- the root and every nested `.git`
# (file or directory, any depth) -- is snapshotted separately: a scratch
# index, a scratch object directory with the real store as an alternate (the
# real index and store are never written), and each nested root excluded from
# its parent's snapshot by pathspec, so no gitlink is ever recorded and an
# unborn nested repository (no commit yet) works. Probed 2026-08-25 (P4).
FINGERPRINT_SCRIPT = r"""export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
roots=()
while IFS= read -r -d '' g; do roots+=("${g%/.git}"); done < <(find . \( -path ./.git -prune \) -o \( -name .git -prune -print0 \) 2>/dev/null)
dw_snap() (
  d=$1; shift
  cd "$d" || exit 1
  real=$(git rev-parse --git-path objects) || exit 1
  case $real in /*) ;; *) real=$PWD/$real ;; esac
  tmp=$(mktemp -d) || exit 1
  trap 'rm -rf "$tmp"' EXIT
  mkdir "$tmp/objects" || exit 1
  GIT_INDEX_FILE=$tmp/index GIT_OBJECT_DIRECTORY=$tmp/objects GIT_ALTERNATE_OBJECT_DIRECTORIES=$real
  export GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
  git -c core.fsmonitor=false add -A -- . "$@" >/dev/null && git write-tree
)
snap() {
  d=$1; ex=()
  for r in "${roots[@]}"; do
    if [ "$d" = . ]; then ex+=(":(exclude,literal)${r#./}")
    elif [[ $r == "$d/"* ]]; then ex+=(":(exclude,literal)${r#"$d/"}")
    fi
  done
  dw_snap "$d" "${ex[@]}"
}
snap . || exit 1
for r in "${roots[@]}"; do snap "$r" || exit 1; done
git rev-parse HEAD"""

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_CAPPED = "[output truncated at "


def _reason(lines: list[str], fallback: str) -> str:
    for line in lines:
        if line and not _HEX40.match(line):
            return line[:MAX_REASON_CHARS]
    return fallback[:MAX_REASON_CHARS]


def parse_fingerprint(result: str) -> tuple[str | None, str | None]:
    """(fingerprint, reason) from the sandbox's bash result (stdout and
    stderr merged; `exit code: N` first when the command ran to an exit).
    Exit 0 -> the fingerprint is the SORTED 40-hex lines (at least two: a
    tree and HEAD); sorting makes it independent of `find`'s order, so the
    same tree gives the same string on any host or container. Any other
    line under exit 0 (an rc-0 `warning:`/`hint:` from git) is ignored --
    the script exits non-zero on every failure, so rc 0 vouches for every
    hash. Everything else is (None, reason), `reason` capped at 200 chars:
    a non-zero exit (reason = the first non-empty non-hex line, git's own
    diagnostic, else the exit line); a result with no `exit code:` head --
    `timeout_result`'s `ERROR: command timed out after ...`, `ERROR: bash
    failed ...`, a `BLOCKED:` -- (reason = its first line); a CAPPED result,
    whose last line is `[output truncated at 10000 chars -- bash output
    capped]` (MAX_BASH_CHARS; about 240 repositories' worth of lines) --
    (reason = that line): a partial listing must never pass as a
    fingerprint, whatever its exit code says."""
    if not isinstance(result, str) or not result.strip():
        return None, "no output"
    lines = [ln.strip() for ln in result.split("\n")]
    last = next((ln for ln in reversed(lines) if ln), "")
    if last.startswith(_CAPPED):
        return None, last[:MAX_REASON_CHARS]
    code = parse_exit_code(result)
    if code is None:
        return None, lines[0][:MAX_REASON_CHARS]
    body = lines[1:]
    if code != 0:
        return None, _reason(body, lines[0])
    hashes = sorted(ln for ln in body if _HEX40.match(ln))
    if len(hashes) < 2:
        return None, _reason(body, "fewer than two hash lines")
    return "\n".join(hashes), None


def fingerprint(sandbox) -> tuple[str | None, str | None]:
    """Run the script through the sandbox's bash seam. A sandbox without
    `bash` (a test double) is a failed measurement, never an error --
    the drain_notices precedent in the runner."""
    bash = getattr(sandbox, "bash", None)
    if bash is None:
        return None, "sandbox has no bash"
    return parse_fingerprint(bash(FINGERPRINT_SCRIPT, FINGERPRINT_TIMEOUT))


# Texts (spec §4.3, §4.4). Wording is not contract; the numbers and the
# rule that the require_changes text never names `finish` are.
UNCHANGED_REQUIRED = (
    "Not accepted as the end of the run: nothing in the worktree changed since this run started, "
    "but the reviewer's feedback asks for changes. Apply every item of the feedback and run the "
    "check each item names, then call finish(summary=...). A second completion with no change "
    "ends the run as `unchanged`.")
UNCHANGED_PLAIN = (
    "Not accepted as the end of the run: nothing in the worktree changed since this run started. "
    "If the task requires changes, make them now, then call finish(summary=...); if the task is "
    "complete without changes, call finish(summary=...) and say so in the summary.")
NO_CHANGE_SINCE_START_REQUIRED = (
    "Nothing in the worktree has changed since this run started ({k} turns or more) and the "
    "reviewer's feedback is not applied yet. Make the first edit now: stop reading whole files — "
    "grep -n for the line you need to change, then edit it.")
NO_CHANGE_SINCE_START_PLAIN = (
    "Nothing in the worktree has changed since this run started ({k} turns or more). If the task "
    "needs changes, make the first edit now — stop reading whole files: grep -n for the line you "
    "need, then edit it; if the task is complete without changes, call finish(summary=...) and "
    "say so in the summary.")
NO_CHANGE_RECENT = (
    "No file in the worktree has changed in the last {k} turns and nothing was committed. If the "
    "task needs more changes, stop reading whole files — grep -n for the line you need, then edit "
    "it; if the task is complete, call finish(summary=...).")

2. tests/provider_doubles.py: add
class FingerprintSandbox(HostSandbox):
    """A HostSandbox whose `bash` answers FINGERPRINT_SCRIPT from a scripted
    list and delegates everything else (verify commands, file tools stay
    real). Entries: a str hash -> "exit code: 0\n<hash>\n<40 zeros>" (two
    lines, as the real script prints a tree and HEAD); None -> "exit code:
    1\nerror: boom"; an Exception instance -> raised. The last entry repeats.
    `commands` records every (command, timeout) the runner sends."""
    def __init__(self, worktree, hashes, **kwargs):
        super().__init__(worktree, **kwargs)
        self.hashes = list(hashes)
        self.commands = []

    def bash(self, command, timeout=120):
        self.commands.append((command, timeout))
        if command != FINGERPRINT_SCRIPT:
            return super().bash(command, timeout)
        entry = self.hashes.pop(0) if len(self.hashes) > 1 else self.hashes[0]
        if isinstance(entry, BaseException):
            raise entry
        if entry is None:
            return "exit code: 1\nerror: boom"
        return f"exit code: 0\n{entry}\n{'0' * 40}"
   (import FINGERPRINT_SCRIPT from dirtywork.changes and HostSandbox from dirtywork.sandbox.host; the constructor kwargs are HostSandbox's.)

3. tests/test_runner.py: beside the `parts` fixture (line ~95) add a `git_parts` fixture identical to `parts` except that `wt` is a real repository: after writing f.txt run `git -C wt init -q`, `git -C wt -c user.email=t@t -c user.name=t add -A`, `git -C wt -c user.email=t@t -c user.name=t commit -qm init` via subprocess.run(check=True). Skip both new fixtures' tests with pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH").

4. tests/test_changes.py (new; import from dirtywork.changes):
   test_parse_fingerprint_sorts_and_requires_two_hashes: two hex lines → the sorted join; the same four lines given in two different orders → equal fingerprints; "exit code: 0\n" + one hex line → (None, "fewer than two hash lines"); "" → (None, "no output").
   test_parse_fingerprint_ignores_rc0_warnings: "exit code: 0\nwarning: something\n<h1>\n<h2>" → the two hashes joined.
   test_parse_fingerprint_fails_open_with_a_reason: "exit code: 1\nerror: 'vendor/x/' does not have a commit checked out\n<h1>" → (None, "error: 'vendor/x/' does not have a commit checked out"); "exit code: 128\n" → (None, "exit code: 128"); tools.timeout_result(60) → (None, a string starting with "ERROR: command timed out after"); "ERROR: bash failed: no such container" → (None, that line); "BLOCKED: sudo is not allowed…" → (None, that line); "exit code: 0\n" + 240 hex lines + "\n[output truncated at 10000 chars — bash output capped]" → (None, "[output truncated at 10000 chars — bash output capped]"); a 500-char diagnostic → reason of length 200.
   test_fingerprint_without_bash: fingerprint(object()) == (None, "sandbox has no bash").
   test_script_shape_and_guardrails: FINGERPRINT_SCRIPT contains each of "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", ":(exclude,literal)", "trap 'rm -rf \"$tmp\"' EXIT", "git -c core.fsmonitor=false add -A -- .", "git write-tree", "git rev-parse HEAD", "GIT_CONFIG_GLOBAL=/dev/null"; guardrails.check_bash_command(FINGERPRINT_SCRIPT, worktree=tmp_path) is None and check_bash_command(FINGERPRINT_SCRIPT, sandboxed=True) is None.
   test_real_script_on_the_host (skip without git): build a repo in tmp_path with one committed file; HostSandbox(tmp_path).bash(FINGERPRINT_SCRIPT, FINGERPRINT_TIMEOUT) parses to a fingerprint (rc 0, two lines); then create: a committed nested repo vendor/inner (init, a file, commit), an UNBORN nested repo vendor/unborn (init + a file, no commit), a nested-in-nested vendor/inner/deeper (init + commit), nested roots named "vendor/café" and "vendor/sp ace" (init + commit each) — run again: rc 0, exactly 6 hash lines + HEAD (count the 40-hex lines in the raw result: repositories + 1), fingerprint not None; write a file inside vendor/unborn → only that repository's line changes (compare the raw hex line sets: exactly one line differs); rewrite the root file byte-identically → fingerprint unchanged; count files under `git rev-parse --git-path objects` before and after a run with a new 100 KB untracked file present → equal; count entries of tempfile.gettempdir() before and after → equal.

5. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` until green, then call finish with a summary. Do not modify dirtywork/runner.py in this task; do not touch docs/.
```

- [ ] **Step 2: Run**; **Step 3: Review** (script byte-exact vs spec §4.1 — `diff <(sed -n
  '/^FINGERPRINT_SCRIPT = r"""/,/^git rev-parse HEAD"""/p' dirtywork/changes.py) <(the spec's
  block)`; parser cases; the doubles' shapes; the host test actually exercises unborn + non-ASCII);
  **Step 4: Host suite**; **Step 5:** merge, ledger row.

---

### Task W3a: The guard in the runner — start, completion paths, `finish()`, `require_changes` (spec §4.1 When/Failure, §4.3)

**Files:**
- Modify: `dirtywork/runner.py:474-533` (`Runner.__init__`), `:546-560` (state), `:671-704`
  (`finish()`), `:746-786` (`check_verify`), `:1005-1020` (the loop's `try:`);
  `tests/test_transcript_schema.py:18-31` (`NUDGE_KINDS`, `STATUSES`, `RUN_END_FIELDS`);
  `tests/test_runner.py` doubles (`:667-671`, `:1562`, `:1804`, `:1857-1876`, `:1891`, `:1922`).
- Test: `tests/test_runner.py` (tests 12–17 and the non-K parts of 19).

**Interfaces:**
- Consumes: `changes.fingerprint`, `UNCHANGED_REQUIRED`, `UNCHANGED_PLAIN` (W2b);
  `FingerprintSandbox`, `git_parts` (W2b).
- Produces: `Runner(..., require_changes: bool = False)`; run-scoped `fp_start`, `fp_check`,
  `fp_turn`, `fp_value`, `changed`, `changed_reason`, `unchanged_finishes`; `take_fingerprint()`
  closure; status `unchanged`; `run_end.changed` (bool | None, always), `run_end.changed_reason`
  (str, sparse), `RunResult.extra` the same; finish results `run not finished: nothing changed`
  and `run not finished: change check could not run (…)`; nudge kind `unchanged_finish`.

- [ ] **Step 1: Brief** `$SCRATCH/brief-6566-w3a.md`:

```
Issue #66, task W3a of 9: the change guard in dirtywork/runner.py — fingerprint at run start, on both completion paths (before verify) and in finish(); reject a completion that changed nothing once; end a feedback resume `unchanged` on the second. Spec §4.1 ("When" and "Failure"), §4.3 — read them; the code below is exact where it is code. The every-K-turns check is W3b (not here); the CLI is W3c (not here).

1. Imports: `from .changes import fingerprint as _fingerprint, UNCHANGED_REQUIRED, UNCHANGED_PLAIN`.

2. Runner.__init__ gains `require_changes: bool = False` (after `verify_timeout`) and stores `self.require_changes = require_changes` with the comment "# Spec #66 §4.3: a completion that changed nothing ends the run `unchanged` on the second attempt (the CLI sets this for resume --feedback)".

3. Runner.run state, beside `timeouts`/`truncations`:
        fp_start = None         # spec #66 §4.1: the worktree fingerprint at run start (None = guard off)
        fp_check = None         # §4.4: baseline of the every-K check (W3b)
        fp_turn = -1            # turn of the newest measurement
        fp_value = None
        changed = None          # §4.3 one rule: newest measurement != fp_start; null = unknown
        changed_reason = None   # why the newest measurement failed (sparse on run_end)
        unchanged_finishes = 0

4. A closure in Runner.run, defined next to run_verify:
        def take_fingerprint():
            """Spec #66 §4.1/§4.3: one measurement. Returns the fingerprint or
            None; sets the run's `changed`/`changed_reason` by the one rule.
            BudgetExceeded/SandboxError are stored as the reason, then
            re-raised for the caller to map (finish() catches them)."""
            nonlocal fp_turn, fp_value, changed, changed_reason
            try:
                fp, reason = _fingerprint(self.sandbox)
            except BudgetExceeded as e:
                changed, changed_reason = None, f"budget: {e.reason}"
                raise
            except SandboxError as e:
                changed, changed_reason = None, f"sandbox: {e}"
                raise
            if fp is None:
                changed_reason = reason
                return None
            fp_turn, fp_value = turns, fp
            if fp_start is not None:
                changed, changed_reason = (fp != fp_start), None
            return fp
   Note `fp_start` is read here, not assigned; the start assignment is item 5.

5. Run start: the loop today is (line ~1005)
        try:
            while True:
                ...
   Insert, inside that `try:` and immediately before `while True:`:
            fp_start = take_fingerprint()          # spec #66 §4.1 (1); None = guard off for this run
            if fp_start is None and changed_reason is not None:
                pass                                # the CLI reports changed_reason (W3c); the Runner never writes stderr
            fp_check = fp_start
   BudgetExceeded/SandboxError raised there must end the run through finish(): wrap that one call as
            try:
                fp_start = take_fingerprint()
            except BudgetExceeded as e:
                return finish("budget_exceeded", e.reason)
            except SandboxError as e:
                return finish("sandbox_error", str(e))
   and keep it inside the outer `try:` so a KeyboardInterrupt during the exec reaches the existing `except KeyboardInterrupt: return finish("interrupted", "")` (turns == 0, one run_end).

6. finish(status, final): as its FIRST statements (before `drain_sandbox()`):
            if (status not in ("interrupted", "timeout", "budget_exceeded", "sandbox_error")
                    and fp_start is not None and fp_turn != turns):
                try:
                    take_fingerprint()              # §4.1 (4): run_end.changed for max_turns/stalled/stuck/model_error/verify_failed/context_exhausted
                except (BudgetExceeded, SandboxError):
                    pass                            # reason already stored by take_fingerprint
   Then, in the `extra` dict, add `"changed": changed,` and, only when changed_reason is not None, `"changed_reason": changed_reason` (sparse — build the dict then `if changed_reason is not None: extra["changed_reason"] = changed_reason`). After `extra.update(finalize_state["result"])` (the finalize merge that already exists), add:
                if changed_reason is not None and changed_reason.startswith("budget: ") \
                        and not extra.get("watchdog_violation"):
                    extra["watchdog_violation"] = changed_reason[len("budget: "):]
                    extra["watchdog_violation_kind"] = "budget"
   (the docker budget sample consumed the violation before raising; never overwrite a value finalize() set).

7. check_verify(final, via): today it starts with `nonlocal stuck` then `if not self.verify:`. Insert at the top (after nonlocal, which gains `unchanged_finishes`):
            if fp_start is not None:
                try:
                    fp = take_fingerprint()
                except BudgetExceeded as e:
                    resolve_finish(f"run not finished: change check could not run ({e.reason})")
                    return finish("budget_exceeded", e.reason), None
                except SandboxError as e:
                    resolve_finish(f"run not finished: change check could not run ({e})")
                    return finish("sandbox_error", str(e)), None
                if fp is not None and fp == fp_start:
                    if unchanged_finishes == 0:
                        unchanged_finishes = 1
                        stuck = None
                        repeats.reset()             # a rejection round is a fresh episode, as verify feedback
                        record = self.transcript.write("nudge", kind="unchanged_finish", turn=turns)
                        if record is not None:
                            record["via"] = "tool_result" if via == "finish_result" else "user"
                        text = UNCHANGED_REQUIRED if self.require_changes else UNCHANGED_PLAIN
                        resolve_finish(text)
                        return None, text
                    if self.require_changes:
                        resolve_finish("run not finished: nothing changed")
                        return finish("unchanged", final), None
   Everything after (the `if not self.verify:` branch and the verify logic) is unchanged. On the finish-tool path the caller already treats a returned text as the finish result (resolve_finish did it) and only delivers the turn's timeout/sandbox nudges; on the plain-answer path (via="user") the caller delivers the returned text as the user message — check the two call sites (line ~843-850 and ~958-975) and make sure the plain path sends `text` exactly as it sends verify feedback today.

8. tests/test_transcript_schema.py: NUDGE_KINDS += ["no_change", "unchanged_finish"]; STATUSES += ["unchanged"]; RUN_END_FIELDS += ["changed", "changed_reason"] (docs/transcript-schema.md already documents them).

9. Existing tests to adjust (the start fingerprint is one more `bash` call; doubles without `bash` are guard-off automatically): tests/test_runner.py line ~667-671 exact `extra` gains `"changed": None`; line ~1804 `box.commands == ["npm test"]` → compare `[c for c in box.commands if c != FINGERPRINT_SCRIPT]`; the doubles whose `bash` raises or asserts unconditionally — `Raising` (~1857-1876), `InterruptingSandbox` (~1891), the ones at ~1562 and ~1922 — gain `if command == FINGERPRINT_SCRIPT: return "exit code: 1\nerror: test double"` as their first line so the scripted behaviour still fires on the turn the test targets; `ExplodingSandbox` (~2613) has no bash → unchanged. Import FINGERPRINT_SCRIPT from dirtywork.changes where needed. `parts` (a non-repo tmp dir) runs guard-off: `changed` is None and `changed_reason` is set on every run_end there — adjust any test that asserts the exact run_end key set.

10. New tests (tests/test_runner.py; use FingerprintSandbox from tests.provider_doubles with `parts`' wt, and `git_parts` for real fingerprints):
   test_start_fingerprint_before_first_chat: FingerprintSandbox(wt, ["a"*40]) — commands[0] == (FINGERPRINT_SCRIPT, 60) and the provider's first request happened after it (record order with a provider that appends to a shared list); a run that finishes → the finish-time fingerprint equals → run_end.changed is False; hashes ["a"*40, "b"*40] → True.
   test_start_fingerprint_failure_turns_guard_off: hashes [None] → run_end.changed is None, changed_reason == "error: boom", and exactly ONE FINGERPRINT_SCRIPT command for the whole run even though the model calls finish twice (no rejection: both finishes accepted; the first ends the run).
   test_zero_change_finish_rejected_once_then_completed (git_parts, real script, require_changes False, verify="test -e f.txt"): responses [finish, finish] → the first finish's transcript tool_result.result == UNCHANGED_PLAIN, a nudge event kind "unchanged_finish" via "tool_result", the verify command not in the sandbox's bash calls before the second finish; the second finish → status "completed", verify ran once, run_end.changed is False, result.extra["changed"] is False.
   test_zero_change_finish_ends_unchanged_when_required (git_parts, require_changes=True, verify set): [finish, finish] → status "unchanged", final_message == the second summary, the finish results == [UNCHANGED_REQUIRED, "run not finished: nothing changed"], verify never ran, finalize called (use the existing finalize-recording pattern), changed False. Variant with a write_file between the finishes → "completed", changed True. Variant: finalize raises → status still "unchanged", extra["finalize_error"] set.
   test_plain_answer_rejection_is_a_user_message (git_parts): responses [_resp(content="all done"), _resp(content="all done")] → the second request's last message is {"role": "user", "content": UNCHANGED_PLAIN}; nudge via "user"; then "completed" with changed False; with require_changes → "unchanged". A rejection on the last allowed turn (max_turns=1) → status "max_turns".
   test_mixed_turn_rejection (git_parts): a turn with [read_file call, finish call] → the read_file result is a normal tool message, the finish tool message's content == UNCHANGED_PLAIN at its own position (index order preserved), and a timed-out command's TIMEOUT_NUDGE of the same turn is still delivered (reuse the existing timeout test shape).
   test_fingerprint_exceptions_map_like_verify: FingerprintSandbox(wt, [BudgetExceeded("disk")]) → status "budget_exceeded" with a run_end whose changed is None and changed_reason == "budget: disk"; [SandboxError("gone")] → "sandbox_error", changed_reason "sandbox: gone"; on a completion path (hashes ["a"*40, BudgetExceeded("disk")]) → "budget_exceeded" and the finish result == "run not finished: change check could not run (disk)"; a KeyboardInterrupt raised by the double on the START fingerprint → status "interrupted", turns == 0, exactly one run_end.
   test_finish_time_fingerprint (FingerprintSandbox): a max_turns run (max_turns=2, reads only) → run_end.changed set from the finish-time measurement and the FINGERPRINT_SCRIPT command was sent before any `drain_notices` call (give the double a drain_notices that records its call order); a double whose finalize() flips a flag after which bash returns "ERROR: bash failed: gone" → changed is not None on max_turns; a double whose fingerprint bash queues a notice into drain_notices → that nudge record precedes run_end in the transcript; hashes ["a"*40, "a"*40, None] with a rejection on turn 2 then max_turns → changed None and changed_reason "error: boom" (not the stale False); BudgetExceeded("disk") on the finish-time measurement of a max_turns run → status "max_turns" preserved, changed None, changed_reason "budget: disk", run_end.watchdog_violation == "disk" and watchdog_violation_kind == "budget"; a rejection on turn 2 then KeyboardInterrupt on turn 3 → "interrupted" with changed False and no changed_reason key; statuses interrupted/timeout/budget_exceeded/sandbox_error take no finish-time fingerprint (count the commands).

11. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` until green, then call finish with a summary. Do not implement the every-K check; do not touch dirtywork/__main__.py, dirtywork/resume.py or docs/.
```

- [ ] **Step 2: Run**; **Step 3: Review** (the finish-time block is the first statement of
  `finish()`; the skip set is exactly the four statuses; `check_verify`'s check precedes `if not
  self.verify`; `resolve_finish` texts exact; `changed_reason` sparse; the doubles discriminate on
  the command rather than weakening assertions; schema lists updated); **Step 4: Host suite**;
  **Step 5:** merge, ledger row.

---

### Task W3b: The every-K `no_change` check (spec §4.4)

**Files:**
- Modify: `dirtywork/runner.py` (`Runner.__init__`; `check_progress` neighbourhood `:706-717`;
  the two turn-end sites `:855` and `:985`).
- Test: `tests/test_runner.py` (test 18, the K cases of 19).

**Interfaces:**
- Consumes: `fp_start`, `fp_check`, `take_fingerprint()` (W3a); `NO_CHANGE_SINCE_START_REQUIRED`,
  `NO_CHANGE_SINCE_START_PLAIN`, `NO_CHANGE_RECENT`, `DEFAULT_NO_CHANGE_TURNS` (W2b).
- Produces: `Runner(..., no_change_turns: int = DEFAULT_NO_CHANGE_TURNS)`; `check_no_change() ->
  (RunResult | None, str | None, dict | None)`; nudge kind `no_change`.

- [ ] **Step 1: Brief** `$SCRATCH/brief-6566-w3b.md`:

```
Issue #66, task W3b of 9: every K turns, if the worktree fingerprint has not changed since the last check, nudge the model (never abort). Spec §4.4; W3a already added the fingerprint state and take_fingerprint() to dirtywork/runner.py.

1. Import `DEFAULT_NO_CHANGE_TURNS, NO_CHANGE_SINCE_START_REQUIRED, NO_CHANGE_SINCE_START_PLAIN, NO_CHANGE_RECENT` from .changes. Runner.__init__ gains `no_change_turns: int = DEFAULT_NO_CHANGE_TURNS` (after require_changes), stored as `self.no_change_turns` with the comment "# Spec #66 §4.4: every K turns a fingerprint; equal to the last check's -> a nudge, never an abort; 0 disables. Not a CLI flag."

2. A closure in Runner.run next to check_progress, with the same return shape:
        def check_no_change():
            """(RunResult to end the run with, or None; nudge text, or None;
            the nudge record, or None) -- spec #66 §4.4. Fires on turns that
            are a multiple of no_change_turns when the guard is on and the
            turn carried no pending finish (the finish check already
            measured). Equal to the last check's fingerprint -> nudge and
            reset the baseline; different -> reset the baseline silently;
            unmeasurable -> keep the baseline."""
            nonlocal fp_check
            if (fp_start is None or self.no_change_turns <= 0
                    or turns % self.no_change_turns != 0 or pending_finish is not None):
                return None, None, None
            try:
                fp = take_fingerprint()
            except BudgetExceeded as e:
                return finish("budget_exceeded", e.reason), None, None
            except SandboxError as e:
                return finish("sandbox_error", str(e)), None, None
            if fp is None:
                return None, None, None
            same = fp == fp_check
            fp_check = fp
            if not same:
                return None, None, None
            record = self.transcript.write("nudge", kind="no_change", turn=turns)
            if fp == fp_start:
                text = (NO_CHANGE_SINCE_START_REQUIRED if self.require_changes
                        else NO_CHANGE_SINCE_START_PLAIN)
            else:
                text = NO_CHANGE_RECENT
            return None, text.format(k=self.no_change_turns), record
   `pending_finish` must be readable there: it is a local of one_turn today — make it visible by passing it in (`check_no_change(pending_finish)`) rather than widening scope.

3. Call it at both turn-end sites, right after `check_progress()`:
   text path (line ~855): after `stalled, stall_text, stall_record = check_progress()` / `if stalled is not None: return stalled`, add `ended, nc_text, nc_record = check_no_change(None)` / `if ended is not None: return ended`, and join nc_text after stall_text in the deliver call: `deliver(_join_nudges(NUDGES[kind].format(**trunc), sandbox_text, stall_text, nc_text), [kind_record, *sandbox_records, stall_record, nc_record])` (deliver already skips None records — check; if not, filter them).
   tool path (line ~985): the same after the existing check_progress() call, with `check_no_change(pending_finish)` — pending_finish is None at that point on a continuing turn, but pass the local anyway — and nc_text/nc_record appended after stall_text/stall_record in that site's `_join_nudges`/records lists (the order becomes: malformed, sandbox, timeout, stall, no_change for the text; transcript records: stall, no_change, malformed_entry, timeout, sandbox — write the no_change record right after check_progress wrote the stall record, which the call order above gives you).

4. Tests (tests/test_runner.py, FingerprintSandbox with `parts`' wt so hashes are scripted; note the double's first entry answers the START fingerprint):
   test_no_change_nudge_since_start: Runner(no_change_turns=3, require_changes=True), hashes ["s"*40] (every measurement equal), three read-only turns then finish → a nudge event kind "no_change" turn 3 via "tool_result", the third tool result's follow_up == NO_CHANGE_SINCE_START_REQUIRED.format(k=3) (and "finish" not in it); the same without require_changes → NO_CHANGE_SINCE_START_PLAIN.format(k=3).
   test_no_change_nudge_recent_after_a_change: hashes ["s"*40, "t"*40, "t"*40, "t"*40] (start s; K=3 check t → changed, silent; 6 → equal → nudge) → no nudge at turn 3, NO_CHANGE_RECENT.format(k=3) at turn 6 (it names finish).
   test_no_change_check_skips: a None entry at turn K → no nudge, the next window still compares against the old baseline (hashes ["s", None, "s"] → nudge at 6 since start == s); a turn with pending_finish at K → no K measurement (count FINGERPRINT_SCRIPT commands); no_change_turns=0 → only the start fingerprint is ever sent; a guard-off run (start None) → none; on a no-tool turn (an empty reply at turn K) the nudge rides the user message after the empty text and after the stall text when both fire (assert the join order NUDGES["empty"] + "\n\n" + STALL_NUDGE... + "\n\n" + no_change text with stall_turns chosen so both fire on the same turn); transcript order on that turn: nudge{empty} → nudge{stall} → nudge{no_change}.
   test_run_ending_on_a_k_check_turn_reuses_it: no_change_turns=2, require_changes False, hashes ["s"*40, "s"*40, "u"*40]: turn 1 finish (rejected: s == s), turn 3 write_file, max_turns=4 → the K check at turn 4 measures "u" (changed True) and finish() sends no further FINGERPRINT_SCRIPT (exactly three sent: start, turn 1, turn 4... count them against the scripted turns); run_end.changed is True.

5. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` until green, then call finish with a summary. Do not touch dirtywork/__main__.py, dirtywork/resume.py or docs/.
```

- [ ] **Step 2: Run**; **Step 3: Review** (both sites call it; the skip conditions; the three
  texts; baseline handling on `None`; the record order); **Step 4: Host suite**; **Step 5:**
  merge, ledger row.

---

### Task W3c: CLI wiring, the resume block, the `unchanged` gate, the `test_main` consequence (spec §4.5, §5.2 `__main__`)

**Files:**
- Modify: `dirtywork/__main__.py:590-591` (`_seed_payload`), `:609` (`_contract_fields`),
  `:842-847` (resume gate), `:929-941` (`Runner(...)`), `:954` (after `runner.run()`);
  `dirtywork/resume.py:224-256` (`build_resume_task`).
- Test: `tests/test_resume.py:148-156`, `:272-303` (test 20), `tests/test_main.py` (tests 21–22,
  the `--max-turns 2` consequence).

**Interfaces:**
- Consumes: `Runner(require_changes=...)`, `run_end.changed` / `changed_reason` (W3a).
- Produces: `dirtywork: change guard off: <reason>` on stderr; stdout JSON / `run.json` keys
  `changed`, `changed_reason` (sparse); the resume gate message; the two resume block shapes.

- [ ] **Step 1: Brief** `$SCRATCH/brief-6566-w3c.md`:

```
Issue #66, task W3c of 9: wire the guard into the CLI and reorder the resume block. Spec §4.5, §5.2 (the `dirtywork/__main__.py` bullet). W3a/W3b added the runner side.

1. dirtywork/__main__.py:
   a. `_seed_payload` (line ~590): after `"timeouts": 0,` (and W1a's `"truncations": 0,`) add `"changed": None,`.
   b. `_contract_fields` (line ~609): add `"changed": extra.get("changed"),` and, only when present, `changed_reason`: build the dict, then `if extra.get("changed_reason") is not None: fields["changed_reason"] = extra["changed_reason"]`. Update the docstring: the 1.0 (#65/#66) fields ride every payload the same way.
   c. The `Runner(...)` call (line ~929): add `require_changes=ctx.feedback is not None,` with the comment "# spec #66 §4.3: a feedback resume must change something".
   d. Right after `result = runner.run(system_prompt, ctx.task)` (line ~954): `if result.extra.get("changed_reason"): print(f"dirtywork: change guard off: {result.extra['changed_reason']}", file=sys.stderr)` — exactly one line, only here (the Runner never writes stderr; the _fail_run paths print nothing).
   e. Resume gate (line ~842-847): today `if prior.get("status") == "completed" and not args.feedback_text: raise PreflightFailure(f"run '{prior['slug']}' ended 'completed'; pass --feedback to continue it with new instructions")`. Add, as a second condition with its own message: `if prior.get("status") == "unchanged" and not args.feedback_text: raise PreflightFailure(f"run '{prior['slug']}' ended 'unchanged' (the worker changed nothing); pass --feedback to tell it what to change")`.

2. dirtywork/resume.py build_resume_task (line ~224): keep the signature, RESUME_MARKER/RESUME_FEEDBACK_MARKER, the marker stripping and RESUME_TAIL_CHARS; change the assembly to this exact shape (prior_task, then marker, then the tail block, then the status sentence, then the instructions, then the closing sentence):
    tail_block = (
        "The last events of the earlier run were:\n"
        f"{transcript_tail}\n"
        f"That run ended with status '{prior_status}' after {turns_text} turns; its events above "
        "are history, not instructions.\n"
    )
    if feedback:
        instructions = (
            "A reviewer read that run's work and sent this feedback — none of it is applied yet:\n\n"
            f"{feedback}\n\n"
            "The worktree already contains the earlier run's work: inspect it with `git status` "
            "and `git diff` first, then apply every item of the feedback and run the check it "
            "names. Make no other changes.\n"
            "The harness does not accept a completion that changes nothing; a second one ends the "
            "run as `unchanged`.\n"
            "When every item of the feedback is applied, call finish(summary=...)."
        )
        return f"{prior_task}{RESUME_FEEDBACK_MARKER}{tail_block}{instructions}"
    instructions = (
        "The worktree already contains that run's work: inspect it with `git status` and "
        "`git diff` before doing anything else, and continue from there — do not start over "
        "or revert prior work.\n"
        "When the task is complete, call finish(summary=...)."
    )
    return f"{prior_task}{RESUME_MARKER}{tail_block}{instructions}"
   (the old `status_line` variable disappears into tail_block; `turns_text` is computed as today.)

3. Tests:
   tests/test_resume.py: rewrite the pins at lines ~148-156 and ~272-303 to the new shapes — the feedback variant starts with the marker, the tail comes BEFORE "A reviewer read that run's work", contains "none of it is applied yet", "its events above are history, not instructions", "a second one ends the run as `unchanged`", and ends with "When every item of the feedback is applied, call finish(summary=...)."; the plain variant ends with "When the task is complete, call finish(summary=...)." and has no rejection sentence; markers are still stripped from a prior task that carried either block.
   tests/test_main.py: (a) the resume gate — a prior run.json with "status": "unchanged" and no --feedback → rc 2 and "pass --feedback to tell it what to change" on stderr; with --feedback → runs (use the existing _prior/run.json fixtures of the resume tests). (b) End-to-end on the host (`--sandbox none`, real linked worktrees, so the real fingerprint runs): `_first_run` (line ~1365) and the other host runs that complete via a single plain "done" answer (lines ~1463, ~1631, ~1823, ~1830, ~2016) now take TWO turns — the first "done" is rejected, the second completes — so pass `--max-turns 2` there and update `turns == 1` assertions to 2; `_resume_responses` (line ~1372) inserts a `write_file` tool-call body before its `finish` body so the resume tests that assert "completed" still do; add: a `resume --feedback` whose scripted client finishes twice with no write → rc 1, run.json["status"] == "unchanged", run.json["changed"] is False, run.json["truncations"] == 0; the same with a write_file between → "completed", changed True; a fresh run whose client finishes twice → "completed", changed False; the stdout payload and run.json carry "truncations" and "changed" on the failure paths (0 / None) and never "changed_reason" there; (c) stderr: a guard-off run (the docker-mode fakes whose bash returns "" or "exit code: 0\n", or `--sandbox none` on a tmp dir that is not a git repo if such a test exists) prints exactly one line starting with "dirtywork: change guard off: " (capsys) and a guard-on host run prints none. The docker-mode fakes in this file (bash → "" at ~195/~318/~392/~642, "exit code: 0\n" at ~1539) now run guard-off: their payloads have "changed": None plus a "changed_reason" — adjust exact-payload assertions accordingly.

4. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` until green, then call finish with a summary. Do not touch docs/.
```

- [ ] **Step 2: Run**; **Step 3: Review** (block shapes byte-exact vs §4.5; the gate message; the
  stderr line printed once and only by `__main__`; `_contract_fields` sparse rule; no weakened
  `test_main` assertions — the two-turn shape is asserted, not skipped); **Step 4: Host suite**;
  **Step 5:** merge, ledger row.

---

### Task C1: The guard's own acceptance on the branch runtime (Claude; spec §8 C1)

From `.worktrees/issue-65-66-change-guard`, `/usr/bin/python3 -m dirtywork …` (the branch's
runtime), host **and** docker (`--sandbox docker --image dirtywork-worker-pytest:0.10`), qwen:

- [ ] **(a)** `run` a small task to `completed`; `resume <slug> --feedback "No change is needed;
  call finish."` → the first `finish`'s `tool_result.result == UNCHANGED_REQUIRED` with
  `nudge{unchanged_finish}`; the second → status `unchanged`, exit 1, `run.json.changed == false`.
- [ ] **(a′)** `resume` of that `unchanged` run without `--feedback` → exit 2 with the §4.5
  message.
- [ ] **(b)** the same prior with one real one-line item → `completed`, `changed: true`.
- [ ] **(c)** a fresh run whose task is "call finish immediately with the summary 'nothing to
  do'" → `UNCHANGED_PLAIN` first, then `completed` with `changed: false`.
- [ ] **(d)** the read-only task (“read these N files fully with `read_file` and list every public
  function of each in the finish summary; do not edit anything”, N sized for > 20 turns,
  `--max-turns 30`) → `nudge{no_change}` at every tenth turn before the first completion
  (`NO_CHANGE_SINCE_START_PLAIN`), `changed: false`, the first completion rejected with
  `UNCHANGED_PLAIN`, the second `completed`; if the first completion arrives before turn 10,
  double N and rerun once.
- [ ] **(e) observation:** a fresh W7-shaped edit task (the ledger section's harvest columns or
  another item not yet on the branch) — record whether a mutating call came within K turns of the
  first `no_change` nudge.
- [ ] **Gate:** (a)–(d) failing blocks W4a/W4b and D2 and returns the spec to §4.3/§4.4. Rows in
  the ledger.

---

### Task W4a: Evidence surfaces — bench, harvest, `runs show` (spec §5.2, tests 23b/23c)

**Files:**
- Modify: `dirtywork/bench.py:46-51` (`NUDGE_KINDS`), `:268-275` (`_harness_failures`),
  `:425-433` (`_failure_cell`); `tools/soak_harvest.py:179-235` (`detect_features`), `:242-245`;
  `dirtywork/runs.py:331-332` (`MD_RESULT_FIELDS`).
- Test: `tests/test_bench.py:841-848`, `:863-864`; `tests/test_soak_tools.py`; `tests/test_runs.py`.

**Interfaces:**
- Consumes: nudge kinds `no_change`, `unchanged_finish`, status `unchanged`, run.json
  `changed`/`truncations`/`changed_reason` (W3a–W3c).
- Produces: `bench.NUDGE_KINDS` of ten; `S14` feature in `soak_harvest.detect_features`;
  `runs show --markdown` rows for the three fields.

- [ ] **Step 1: Brief** `$SCRATCH/brief-6566-w4a.md`:

```
Issue #65/#66, task W4a of 9: bench, the soak harvester and `runs show` learn the two nudge kinds, the status and the three run.json fields. Spec §5.2. Consequences are stated there; do not hide a widened cell behind a looser assertion.

1. dirtywork/bench.py: NUDGE_KINDS (line ~48) gains "no_change", "unchanged_finish" APPENDED AT THE END (order is the column order; EMPTY_REPLY_NUDGE_KINDS is unchanged — these are harness nudges, not model failures). `_harness_failures` (line ~268-275) and `_failure_cell` (line ~425-433): the status tuple ("stalled", "max_turns", "sandbox_error") becomes ("stalled", "max_turns", "sandbox_error", "unchanged") in both (one constant if they share one; introduce `_STATUS_FAILURES` next to NUDGE_KINDS if they do not). The summarize legend (line ~783) derives from NUDGE_KINDS — verify it prints ten names.
   tests/test_bench.py: the pins at ~841-848 → `len(bench.NUDGE_KINDS) == 10`, `bench.NUDGE_KINDS[-2:] == ("no_change", "unchanged_finish")`, EMPTY_REPLY_NUDGE_KINDS == tuple(runner.NUDGES) unchanged; the literal at ~863-864 "0/0/0/0/0/0/3/0" → the full ten-wide cell "0/0/0/0/0/0/3/0/0/0" asserted as an exact column value (not a substring); rename the test whose name says eight kinds to say ten; a new assertion that a run whose run_end status is "unchanged" counts under the new status column in `_harness_failures`.

2. tools/soak_harvest.py detect_features (line ~179-235): add a feature code "S14" that fires when any nudge event has kind in ("unchanged_finish", "no_change"), or the run's status is "unchanged", or run.json["changed"] is False. Do NOT fold it into F8 (the stall detector) — F8 is unchanged. Where status features are detected (line ~242-245: stuck/stalled), treat "unchanged" like "stalled" (a harness-ended run) only if that code path builds a status column; otherwise leave it.
   tests/test_soak_tools.py: detect_features fires S14 for each of the three triggers separately and not for a run with changed True and no guard nudges; F8 unaffected by an unchanged_finish nudge.

3. dirtywork/runs.py MD_RESULT_FIELDS (line ~331) gains "truncations", "changed", "changed_reason" after "timeouts". The markdown renderer skips None values (check the loop at the render site; if it does not, make it skip None for these three so a null `changed` does not print "None").
   tests/test_runs.py: `runs show --markdown` on a run.json with truncations 2, changed False → both rendered; with changed None and no changed_reason → neither line; `_tool_result_outcome` returns "not finished" for the four texts: UNCHANGED_REQUIRED, UNCHANGED_PLAIN (import from dirtywork.changes), "run not finished: nothing changed", "run not finished: change check could not run (x)".

4. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` until green, then call finish with a summary. Do not touch docs/.
```

- [ ] **Step 2: Run**; **Step 3: Review** (order of `NUDGE_KINDS`; the ten-wide literal asserted
  exactly; `S14` separate from F8); **Step 4: Host suite** + `dirtywork bench summarize` on
  `~/.dirtywork/bench/soak-B.jsonl` renders (legend of ten); **Step 5:** merge, ledger row.

---

### Task W4b: Live docker test 24 (spec §7.24)

**Files:**
- Modify: `tests/test_docker_live.py` (docker-marked; the `_image_kwargs()` / `_events` helpers
  from #61).

- [ ] **Step 1: Brief** `$SCRATCH/brief-6566-w4b.md`:

```
Issue #66, task W4b of 9: one docker-marked live test in tests/test_docker_live.py (follow the file's existing helpers: `_image_kwargs()`, the DockerSandbox construction the other live tests use, `pytest.mark.docker`). Spec §7 test 24.

test_docker_live_fingerprint_matches_host_and_leaves_the_store_alone:
  1. Build a host repo in tmp_path with one committed file (git init/add/commit with -c user.email/-c user.name), plus a nested committed repo `vendor/inner` and an unborn `vendor/unborn` with a file.
  2. Host fingerprint: dirtywork.changes.fingerprint(HostSandbox(tmp_path)) → (fp_host, None).
  3. Start a DockerSandbox for that repo the way the resume-seeding live test does (the worktree seeded into /work with the #61 gitfile layout), then `dirtywork.changes.fingerprint(sandbox)` → (fp_docker, None) and assert fp_docker == fp_host (the parser sorts the lines; hashes are content-addressed).
  4. Count files under /gitdir/objects via sandbox.bash("find /gitdir/objects -type f | wc -l") before and after a fingerprint taken with a new untracked 100 KB file written through the sandbox's write_file tool → equal counts (the scratch object directory), and the second fingerprint differs from the first.
  5. A byte-identical rewrite of the committed file through write_file → the fingerprint equals the previous one.
  6. Stop the sandbox in a finally block as the other live tests do.
Run the docker-marked suite for this test (`env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider -m docker -k fingerprint`) if docker is reachable from the sandbox — it is NOT (the worker runs inside a container without the docker socket), so: write the test, make sure the module still imports and the non-docker suite is green, and call finish saying the live run is the reviewer's. Do not touch docs/.
```

- [ ] **Step 2: Run**; **Step 3: Review + live run on the host** (`DIRTYWORK_LIVE_SLOW=1
  /usr/bin/python3 -m pytest -q -p no:cacheprovider -m docker -k fingerprint`, both the pytest
  image and `dirtywork-worker-dev:issue61`); **Step 4: Host suite**; **Step 5:** merge, ledger row.

---

### Task D2: Docs — the contract, operating, README; the ledger (Claude; spec §6)

**Files:**
- Modify: `docs/machine-contract.md` (`:180`, `:374-376`, `:415` status lists + exit-1 rule;
  `:440-443` nudge shape with ten kinds; `:352-353` example and `:360`/`:365` ("the nine keys
  above" → eleven), `:369-370`, `:391-392`, `:405-406` prose for `truncations`, `changed`,
  `changed_reason`; `:246-252` the `append_file` bullet's two verbatim `truncated_call_result`
  quotes → prose + invariant 1's numbers + the six-cut-off rule; a paragraph under the finish
  rules: rejected once, `unchanged` on a feedback resume, the guard detects change not compliance;
  one sentence in the `--stall-turns` paragraph `:160-169` on the every-ten-turns `no_change`
  nudge), `docs/operating.md` (`:172-187` resume/feedback paragraph: the new order, "none of it is
  applied yet", the rejection, `unchanged`, the widened gate; `:398-482` three troubleshooting
  entries — status `unchanged`; `changed: null` / `changed_reason`; `changed: true` on a run that
  edited nothing (host mode): the `HOME` cache limit with `PIP_CACHE_DIR`, `npm_config_cache`,
  `NUGET_PACKAGES`, `.git/info/exclude`; `:330-341` the `--max-tokens` paragraph: the messages
  state the cap and a per-call target, six cut-off replies end the run `model_error`; `:370-371`
  if it enumerates kinds), `README.md` (`:172`, `:195` truncation sentences; `:191-193` abort
  rule "or six cut-off replies at `--max-tokens`"), the ledger section (run rows, the S14 A/B
  line, C1 results).

- [ ] **Step 1:** write the docs per spec §6, quoting numbers not wording.
- [ ] **Step 2:** `/usr/bin/python3 -m pytest -q tests/test_transcript_schema.py tests/test_main.py`
  green; commit on `issue-65-66-change-guard`: `docs(#65/#66): contract, operating, README`.

---

### Task C5: F5 reruns, full suites, metrics, PR (Claude; spec §8)

- [ ] **Step 1: F5 plan** `$SCRATCH/f5-plan.jsonl` — rows `{"task": "py-big-fixture", "model":
  "qwen/qwen3-coder-next", "provider": "openai", "base_url": "http://localhost:1234/v1",
  "flags": ["--max-tokens", "1024", "--max-turns", "60"], "label": "F5-1024-qwen-r1"}` for caps
  1024/2048/4096 × qwen, devstral (`mistralai/devstral-small-2-2512`) × r1, r2 (12 rows); run
  `tools/soak_driver.py $SCRATCH/f5-plan.jsonl --out $SCRATCH/f5-results.jsonl` from
  `.worktrees/issue-65-66-change-guard` (the branch runtime).
- [ ] **Step 2: Score** per spec §8: recovery = `completed` ∧ `truncations ≥ 1` ∧ 401 lines in
  `fixtures/rows.csv`; 1024 may instead abort `truncated` with `truncations == 6` in ≤ 16 turns;
  fail = 60 turns / 1800 s / `stalled` / `stuck` / `verify_failed` / `abort_kind == empty_reply` /
  any truncation text with `target_lines` below 13 / 25 / 50 at 1024 / 2048 / 4096 (grep the
  transcripts); other abort kinds → one tie-break rerun. Table in the ledger with per-run
  `truncations` and the first `target_lines`.
- [ ] **Step 3: Full suites** on the integration branch: unit (`/usr/bin/python3 -m pytest -q -p
  no:cacheprovider`) and live (`-m docker`, `DIRTYWORK_LIVE_SLOW=1`, both images) green; counts
  recorded.
- [ ] **Step 4: Metrics:** stop the sampler (`tools/soak_sampler.sh $SCRATCH/metrics-6566.csv
  --stop`); per-window stats at the end; run totals (runs, turns, wall, prompt tokens, $0); which
  tasks Claude finished and why; the S14 A/B count.
- [ ] **Step 5: PR** from `issue-65-66-change-guard`: "Closes #65, closes #66", milestone 1.0.0,
  body = spec summary + evidence + the ledger link + the dogfood receipts; CI green (incl. the
  docker-live leg); wait for the owner's merge word.

## Self-review

- **Spec coverage:** §3.1–§3.2 → W1a; §3.3 → W1b (abort, bench, harvest) + W1a (counter, field);
  §4.1 script/parser/texts → W2b, When/Failure → W3a; §4.2 → W2a; §4.3 → W3a (+ W3c for
  `require_changes` wiring); §4.4 → W3b; §4.5 → W3c; §5.1 → D1 (doc) + W1a/W3a (fields, kinds,
  status emitted) + W3a (schema-test lists); §5.2 → W1a/W1b/W3c (`__main__`, bench abort,
  harvest regex) + W4a (kinds, status tuples, `S14`, `runs show`); §6 → D1, D2; §7 tests 1–5 →
  W1a, 6–7 → W1b, 8–9 → W2a, 10–11b → W2b, 12–17 + 19 → W3a, 18 + 19's K cases → W3b, 20–22 →
  W3c, 23a → W1a/W3a, 23b/23c → W4a, 24 → W4b; §8 C1 → C1, F5/suites/PR → C5; §9 nothing.
- **Placeholders:** none — every brief carries the code, texts, regexes and assertions; the only
  "as today" references point at code the worker can read at the cited lines.
- **Type consistency:** `chunk_target(max_tokens, cut_chars, cut_lines)` (W1a) is what the
  brief's `note_truncation` calls; `truncated_call_result(tool, raw_arguments, trunc)` (W1a) is
  what W1b's tests and `test_soak_tools.py:939` call; `parse_fingerprint -> (str | None, str |
  None)` and `fingerprint(sandbox)` (W2b) are what `take_fingerprint` (W3a) unpacks;
  `check_no_change(pending_finish) -> (ended, text, record)` (W3b) mirrors `check_progress`;
  `FingerprintSandbox(worktree, hashes)` (W2b) is what W3a/W3b tests construct; `net_change`
  (W2a) is what `ProgressTracker.note_call` reads; the nudge kinds and status strings are spelled
  identically in W3a, W3b, W4a and D1.
