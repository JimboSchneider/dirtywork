# #65/#66 Cap-aware truncation, truncation budget, change guard: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **This repository's execution rule overrides the line above.** Every code task below is built
> by the **released dirtywork (0.10.1) running against this repository** with a local worker
> (`qwen/qwen3-coder-next` via LM Studio) — Claude writes the brief, reviews the branch, runs the
> host suites, feeds back through `dirtywork resume --feedback-file`, and writes the prose docs.
> A Claude implementer touches code only after a worker resume-with-feedback has failed, and the
> PR says so. Owner approval is needed for the merge and the release, never assumed.

**Plan v2** (2026-08-25 19:15 CDT). v1 was reviewed against the spec and the code (three lenses +
one refuter per Blocker/Important, 27 agents: 43 findings — 7 Blocker, 23 Important, 13 Minor;
24 verified, 0 refuted; all folded). The Blockers: W3b scripted non-hex hashes that the parser
would drop (guard off, no nudge possible); `take_fingerprint` kept a stale `changed` on a failed
measurement while the brief's own test expected `null`; the `test_main` two-turn consequence was
assigned two tasks after the runner change that causes it; W4b pointed at a `DockerSandbox`
construction no live test contains. Three tasks were split to the #61 calibration (~150–250 changed
lines per 60-turn run, 1–3 resumes): W1a → W1a-1/W1a-2, W2b → W2b-1/W2b-2, W3a → W3a-1/W3a-2.

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
(v4, `14f6e35`; one sentence of §4.3 aligned with this plan's v2: a failed measurement *anywhere*
sets `changed = None` and stores its reason until the next successful measurement clears both —
that is what keeps §5.1's "`changed_reason` present exactly when `changed` is `null` for a failed
measurement" true on every run end). Section numbers below refer to it. The spec is committed on
the integration branch, so the worker can `read_file` it; every brief still carries the exact code
and text it needs.

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
- **One rule for `changed`:** every successful measurement after the start one sets `changed =
  fp != fp_start` and clears `changed_reason`; every failed or raising measurement — start, K
  check, completion check, `finish()` — sets `changed = None` and stores its reason; so
  `changed_reason` is present exactly when `changed` is `null` for that reason — §4.3, §5.1.
- The CLI (never the `Runner`) prints `dirtywork: change guard off: <reason>` once — §5.2.
- `finish()` measures first (before `drain_sandbox()`), and not for `interrupted`, `timeout`,
  `budget_exceeded`, `sandbox_error`, nor when this turn already measured — §4.1 (4).
- Scripted fingerprints in tests are **40 lowercase hex characters** (`"a" * 40`): they go through
  `parse_fingerprint`, which drops anything else.
- Texts are constants; wording is not contract but the **numbers** are (invariant 1); the
  `no_change` text never names `finish` when `require_changes` and the tree equals `fp_start` —
  §3.2, §4.4.
- DRY & SOLID (owner's standing rule): one target function, one parser, one fingerprint call site
  (`take_fingerprint`), one `net_change` parser next to `describe_change`; nothing duplicated
  between the two truncation texts beyond the numbers.
- The worker never edits `docs/**`; prose docs are Claude's (D1 first, D2 last). The worker
  edits `tests/**`, `dirtywork/**`, `tools/soak_harvest.py`. The worker cannot reach docker from
  inside the sandbox: live tests are written by the worker and run by Claude.
- Host pytest interpreter is `/usr/bin/python3` (3.9, pytest 8.4); Homebrew pythons lack pytest.

## Execution model (every W task)

- **Scratchpad** (absolute; pin it in a new session):
  `SCRATCH=/private/tmp/claude-501/-Users-jimschneider-repos-dirtywork/d9da59a0-7dac-4aaa-9697-28e33a342e2b/scratchpad`
  — holds `run6566.sh`, the briefs `brief-6566-<task>.md` (extracted verbatim from this plan's
  fenced blocks), `feedback-6566-<task>-r<n>.md`, `metrics-6566.csv` (+ `.pid`), `f5-plan.jsonl`
  (C5), `redteam6566.json` / `planreview6566.json` (for reference).
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
- **Metrics:** `tools/soak_sampler.sh $SCRATCH/metrics-6566.csv` (started in C0, detached with
  `nohup … >/dev/null 2>&1 &` — piping its stdout hangs the shell; stopped in C5 with `--stop`);
  one ledger row per run (status, turns, wall, s/turn, prompt/completion tokens, tok/s, nudges,
  guardrail blocks, resets, tool mix, verify outcome) appended to the `## #65/#66` section of
  `docs/superpowers/bench/2026-08-23-v1-soak-sdd-ledger.md`.
- Give qwen ≥ 60 turns; resumes burn turns on `read_file`, so feedback names files and lines;
  escape `.` in grep checks.

## File structure

| file | responsibility after this plan |
|---|---|
| `dirtywork/runner.py` | `reply_size`, `call_size`, `chunk_target`, the truncation texts and dict, `truncations` (W1a-1) + abort (W1b); `take_fingerprint`, the change check in `check_verify`, `finish()` measurement, `require_changes` (W3a-1); `check_no_change`, `no_change_turns` (W3b); `ProgressTracker` net-change rule (W2a) |
| `dirtywork/changes.py` (new) | `FINGERPRINT_SCRIPT`, `FINGERPRINT_TIMEOUT`, `parse_fingerprint`, `fingerprint`, the five guard texts, `DEFAULT_NO_CHANGE_TURNS` (W2b-1) |
| `dirtywork/tools.py` | `parse_exit_code` (moved), `parse_change_head`, `net_change` (W2a) |
| `dirtywork/resume.py` | block order + texts (W3c) |
| `dirtywork/__main__.py` | `truncations` seed/contract field (W1a-1); `require_changes`, `changed`/`changed_reason` seeds, stderr line, `unchanged` resume gate (W3c) |
| `dirtywork/bench.py` | `_abort_kind` cut-off form (W1b); `NUDGE_KINDS`, status tuples (W4a) |
| `tools/soak_harvest.py` | `_TRUNCATED_CALL_RESULT_RE` (W1b); `S14` feature (W4a) |
| `dirtywork/runs.py` | `MD_RESULT_FIELDS` (W4a) |
| `tests/provider_doubles.py`, `tests/test_runner.py` | `FingerprintSandbox`, `git_parts` (W2b-1); pins and doubles (W1a-1, W3a-1) |
| `tests/test_changes.py` (new) | tests 10, 11 (W2b-1), 11b (W2b-2) |
| `tests/test_transcript_schema.py` | `RUN_END_FIELDS` (W1a-1, W3a-1), `STATUSES`, `NUDGE_KINDS` (W3a-1) |
| `tests/test_main.py` | `_DEFAULT_EVIDENCE` (W1a-1, W3c); the two-turn consequence (W3a-1); CLI/resume tests (W3c) |
| `tests/test_resume.py` | the block shapes (W3c) |
| `tests/test_bench.py`, `tests/test_soak_tools.py`, `tests/test_runs.py` | evidence tests (W1a-1, W1b, W4a) |
| `tests/test_docker_live.py` | test 24 (W4b) |
| `docs/transcript-schema.md` | D1 (Claude) — DONE `0092287` |
| `docs/machine-contract.md`, `docs/operating.md`, `README.md`, the ledger | D2 (Claude) |

---

### Task C0: Baseline and instrumentation (Claude) — DONE 2026-08-25 18:55

- [x] Baseline suite in `.worktrees/issue-65-66-change-guard`: **1505 passed, 1 skipped, 37 deselected**.
- [x] `dirtywork-worker-pytest:0.10` present; `qwen/qwen3-coder-next` and Devstral loaded; `pipx run --spec 'dirtywork==0.10.1'` answers.
- [x] `$SCRATCH/run6566.sh` written; sampler running (`metrics-6566.csv.pid` = 44429).
- [x] Ledger section `## #65/#66` opened (`8e34173`) with the run-row header and the S14 A/B line.
- [ ] Extract every brief below into `$SCRATCH/brief-6566-<task>.md` verbatim (after this plan's v2 commit).

---

### Task D1: Docs — the transcript schema first (Claude) — DONE 2026-08-25 18:58 (`0092287`)

- [x] `docs/transcript-schema.md`: `nudge.kind` row (`no_change`, `unchanged_finish`), the `follow_up` row and nudge prose merge orders, the forward-compat sentence, `run_end` rows `truncations` / `changed` / `changed_reason`, the `unchanged` status row, the `run.json` table and stdout key list, the four new `finish` result texts. `tests/test_transcript_schema.py`: 12 passed.

---

### Task W1a-1: #65 — the numbers, the texts, the counter (spec §3.1–§3.3 minus the abort)

**Files:**
- Modify: `dirtywork/runner.py:89-99` (`NUDGES`), `:146-160` (`truncated_call_result`),
  `:546-549` (run-scoped counters), `:671-704` (`finish()` extra), `:841-860` (text path),
  `:877-898` (tool loop cases a/b); `dirtywork/__main__.py:590-591` (`_seed_payload`), `:609`
  (`_contract_fields`); `tests/test_transcript_schema.py:26-31` (`RUN_END_FIELDS`);
  `tests/test_main.py:19-28` (`_DEFAULT_EVIDENCE`); `tests/test_soak_tools.py:939`.
- Test: `tests/test_runner.py` pins at `:283-299`, `:361-373`, `:440-461`, `:473`, `:487`, `:501`,
  `:573-577`, `:667-671`, `:921-930` (rewritten; the five new tests are W1a-2).

**Interfaces:**
- Produces (`dirtywork.runner`): `MIN_CHUNK_CHARS = 200`, `MIN_CHUNK_LINES = 5`,
  `CHUNK_DIVISOR = 4`, `DEFAULT_LINE_CHARS = 60`, `MAX_TRUNCATED_REPLIES = 6`,
  `reply_size(resp) -> tuple[int, int]`, `call_size(tc) -> tuple[int, int]`,
  `chunk_target(max_tokens: int, cut_chars: int, cut_lines: int) -> tuple[int, int]`,
  `truncated_call_result(tool: str, raw_arguments, trunc: dict) -> str`, `NUDGES["truncated"]`
  as a `str.format` template with fields `cap cap_chars received cut_chars cut_lines target_chars
  target_lines n max`; `run_end.truncations` (int, always); `RunResult.extra["truncations"]`.

- [ ] **Step 1: Brief** `$SCRATCH/brief-6566-w1a-1.md`:

```
Issue #65 (cap-aware truncation), task W1a-1 of 12. Make every truncation message carry the --max-tokens cap, what the harness received, a per-call target and a running count, and count truncations per run. No abort yet (that is W1b) and no new tests yet (that is W1a-2) — this task is the code plus the existing pins that the new wording breaks. Spec: docs/superpowers/specs/2026-08-25-cap-aware-truncation-and-change-guard-design.md §3.1-§3.3 (read §3.1 and §3.2 first; the code below is exact).

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
   (`resp` is the local of one_turn assigned just above — the nested function closes over it.)

7. Text path (line ~841-860): after `kind = classify_text_reply(content, finish_reason)` and the `if kind == "answer":` block, before `kind_record = self.transcript.write("nudge", kind=kind, turn=turns)`, add `if kind == "truncated": note_truncation()`. Change the delivery line to format the template: `deliver(_join_nudges(NUDGES[kind].format(**trunc), sandbox_text, stall_text), [kind_record, *sandbox_records, stall_record])` (str.format with an empty dict is a no-op for the two texts without fields).

8. Tool loop (line ~877-898): in case (a) — `if tc.error is not None:` … `if finish_reason == "length":` — call `note_truncation(tc)` then `result = truncated_call_result(name, tc.raw_arguments, trunc)`; in case (b) — `elif finish_reason == "length" and self._missing_required(name, args):` — the same two lines. Nothing else in the loop changes; a `length` turn whose calls all parsed completely is NOT a truncation (tests/test_runner.py:537-554 stays as is).

9. dirtywork/__main__.py: `_seed_payload` (line ~590) adds `"truncations": 0,` after `"timeouts": 0,`; `_contract_fields` (line ~609) returns `"truncations": extra.get("truncations", 0),` beside `"timeouts"`. tests/test_transcript_schema.py: append "truncations" to RUN_END_FIELDS (docs/transcript-schema.md already documents it). tests/test_main.py line ~27: add `"truncations": 0,` after `"timeouts": 0,` in `_DEFAULT_EVIDENCE` (that dict is the only seeded-key assertion in the file).

10. tests/test_soak_tools.py line ~939 calls `truncated_call_result("write_file", raw)`: pass a third argument `dict(cap=1024, cap_chars=4096, received=3000, cut_chars=3000, cut_lines=55, target_chars=750, target_lines=13, n=1, max=6)` (the harvest regex is widened in W1b; this call must only keep the module importable and the write_file branch matching).

11. Rewrite the pins in tests/test_runner.py that compare the old wording: lines ~283-299 and ~921-930 compare `NUDGES["truncated"]` by identity — build the same dict the runner builds and compare against `NUDGES["truncated"].format(**d)` (for a text-only reply of N characters with the default max_tokens 8192: cap=8192, cap_chars=32768, received=N, cut_chars=0, cut_lines=0, target_chars=8192, target_lines=136, n=1, max=6); line ~361-373 (`NUDGES["truncated"] not in …`) → assert "cut off at the --max-tokens cap" not in the message; lines ~440-461 (the write_file hint and `_GENERIC_TRUNCATION`) and ~473/~487/~501 → the new texts formatted with the dict the runner would build (compute cut_chars/cut_lines with call_size on the same ToolCall the test sends, received = len(text) + the raw args length); line ~573-577 (`test_truncated_nudge_names_write_file_and_append_file`) → assert the template contains "{cap}", "{received}", "{target_lines}", "cut-off reply {n} of {max}", "write_file the first part and append_file the rest"; line ~667-671 (exact `result.extra == {...}`) adds `"truncations": 0`.

12. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` until green, then call finish with a summary. Do not add the abort; do not add new tests; do not touch docs/.
```

- [ ] **Step 2: Run** `$SCRATCH/run6566.sh $SCRATCH/brief-6566-w1a-1.md`.
- [ ] **Step 3: Review** (texts byte-exact vs §3.2; `note_truncation` once per turn; both tool-loop
  cases call it with `tc`; the text path with `None`; `truncations` in `extra`; no abort added;
  the pins rewritten as described, not weakened; `_DEFAULT_EVIDENCE` gained the key;
  `test_soak_tools.py:939` still passes).
- [ ] **Step 4: Host suite** green in the run worktree; **Step 5:** commit export verbatim + Claude
  nits, ff-merge, ledger row (+ S14 A/B line for any resume).

---

### Task W1a-2: #65 — the five tests for the numbers (spec §7 tests 1–5)

**Files:**
- Test: `tests/test_runner.py` (five new tests); `tests/test_main.py` (the seeded-key assertion
  already covers `truncations` via `_DEFAULT_EVIDENCE`).

**Interfaces:**
- Consumes: everything W1a-1 produces.

- [ ] **Step 1: Brief** `$SCRATCH/brief-6566-w1a-2.md`:

```
Issue #65, task W1a-2 of 12: five new tests in tests/test_runner.py for the cap-aware truncation numbers that W1a-1 added to dirtywork/runner.py (reply_size, call_size, chunk_target, the NUDGES["truncated"] template, truncated_call_result(tool, raw_arguments, trunc), the per-turn note_truncation() and the `truncations` counter). Read those functions first (grep -n "def chunk_target\|def call_size\|def reply_size\|def note_truncation" dirtywork/runner.py). Spec §3.1-§3.3, §7 tests 1-5. Test helpers that already exist in tests/test_runner.py: `_resp(content, tool_calls, usage, finish_reason)`, `_call(id, name, args)`, `_bad_args(id, name, raw)`, `FakeProvider(responses)`, the `parts` fixture (wt, registry, sandbox, transcript, tmp_path), `_events(tmp_path)`.

   test_chunk_target_cap_basis_and_floors: chunk_target(1024, 0, 0) == (1024, 17); (2048, 0, 0) == (2048, 34); (4096, 0, 0) == (4096, 68); (8192, 0, 0) == (8192, 136); (1024, 3000, 55) == (750, 13); (1024, 100, 2) == (200, 5) (floors); (1024, 300, 10) == (200, 6) (per-line from the call: 300/10 = 30 chars/line → 200/30 = 6).
   test_reply_size_and_call_size: build two calls with `_bad_args("c1", "write_file", raw='{"path":"x","content":"a\\nb"}')` and `_bad_args("c2", "read_file", raw="{}")` and a response `_resp(content="abc", tool_calls=[…])`: reply_size(resp) == (3, len(raw1) + len(raw2)); call_size(first) == (len(raw1), 2) (one escaped newline + 1); call_size(`_bad_args("c3", "write_file", raw="")`) == (0, 0); a call whose raw has no newline → lines == 1.
   test_truncated_text_nudge_carries_the_numbers: FakeProvider([_resp(content="I will now", finish_reason="length"), _resp(content="ok")]), Runner(..., max_tokens=1234): the second request's last user message == NUDGES["truncated"].format(cap=1234, cap_chars=4936, received=10, cut_chars=0, cut_lines=0, target_chars=1234, target_lines=20, n=1, max=6); a second run with `_resp(content="", finish_reason="length")` renders "received only 0 characters".
   test_truncated_call_results_carry_the_cut_calls_numbers: a `length` turn with `_bad_args("c", "write_file", raw='{"path": "x", "content": "a\\nb\\nc')` → the tool message equals truncated_call_result("write_file", raw, d) where d has cap=8192, cap_chars=32768, received=len(raw), cut_chars=len(raw), cut_lines=3 (2 escaped newlines + 1), target_chars=chunk_target(8192, len(raw), 3)[0], target_lines=chunk_target(8192, len(raw), 3)[1], n=1, max=6; the generic form for `_bad_args("c", "read_file", raw="{")`; two cut calls in one turn (two `_bad_args` in one `_resp(..., finish_reason="length")`) both get the text with the same `n` and a target sized by the FIRST call; a turn with one complete call (`_call`) and one cut call sizes the target from the cut one only (received counts both).
   test_truncations_counts_once_per_turn: text path → run_end.truncations == 1 after one truncated reply then "ok"; a turn with two cut calls → +1 only; a `length` turn with a complete valid call (the shape of the existing test at line ~537-554) → +0; result.extra["truncations"] equals the run_end value.

Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` until green, then call finish with a summary. Do not change dirtywork/ (if a test cannot pass without a code change, the test is wrong — re-read the function); do not touch docs/.
```

- [ ] **Step 2: Run**; **Step 3: Review** (every number in the brief appears in an assertion; no
  code change); **Step 4: Host suite**; **Step 5:** merge, ledger row.

---

### Task W1b: #65 — the six-reply abort; bench and harvest read it (spec §3.3, §5.2)

**Files:**
- Modify: `dirtywork/runner.py` (text path `:851-854`, tool loop abort `:945`);
  `dirtywork/bench.py:242-252` (`_abort_kind` + docstring); `tools/soak_harvest.py:92-102`.
- Test: `tests/test_runner.py` (test 6), `tests/test_bench.py` (test 7), `tests/test_soak_tools.py`
  (both generic wordings).

**Interfaces:**
- Consumes: `truncations`, `trunc`, `note_truncation` (W1a-1).
- Produces: `TRUNCATION_ABORT` (`dirtywork.runner`); `bench._abort_kind(...) == "truncated"` for
  the cut-off form; `soak_harvest._TRUNCATED_CALL_RESULT_RE` matching both wordings.

- [ ] **Step 1: Brief** `$SCRATCH/brief-6566-w1b.md`:

```
Issue #65, task W1b of 12. Six cut-off replies end a run; bench and the soak harvester understand the new wording. W1a-1 already added reply_size/call_size/chunk_target, the texts, the per-turn note_truncation() and the `truncations` counter in dirtywork/runner.py. Spec §3.3, §5.2.

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

3. Tool loop: in both places W1a-1 calls note_truncation(tc) — case (a) `if tc.error is not None:` and case (b) `elif finish_reason == "length" and self._missing_required(name, args):` — after `abort_reason = failures.record("malformed_args")` and the note_truncation/truncated_call_result lines, add:
                    if abort_reason is None and truncations >= MAX_TRUNCATED_REPLIES:
                        abort_reason = TRUNCATION_ABORT.format(n=truncations, cap=self.max_tokens)
   The truncated result is still produced, recorded in the transcript and appended to messages; the loop's existing `if abort_reason is not None: return finish("model_error", abort_reason)` ends the run after that.

4. dirtywork/bench.py `_abort_kind` (line ~246): keep `_ABORT_RE`; add a module-level `_CUTOFF_ABORT_RE = re.compile(r"aborted after \d+ cut-off replies")` and, in `_abort_kind`, before returning None when `_ABORT_RE` does not match: `if _CUTOFF_ABORT_RE.search(final_message): return "truncated"`. Extend `_abort_kind`'s docstring with one sentence: "`truncated` is the run-level budget of six cut-off replies (#65), not a consecutive count."

5. tools/soak_harvest.py: replace `_TRUNCATED_CALL_RESULT_RE` (line ~100) with
_TRUNCATED_CALL_RESULT_RE = re.compile(
    r"^ERROR: your (?:write_file for |\S+ call was cut off at the (?:token limit|--max-tokens cap)\b)")
   and update the comment above it (lines ~92-99): the harvester reads historical run dirs whose results carry the 0.10 wording ("…at the token limit before it completed.") as well as 1.0's ("…at the --max-tokens cap of N tokens after about …"), so both must match; the write_file branch is unchanged.

6. Tests:
   tests/test_runner.py test_six_cutoff_replies_end_the_run (the S3 shape): responses alternating `_resp(content="header", finish_reason="length")` and `_resp(tool_calls=[_call(f"w{i}", "write_file", {"path": "rows.csv", "content": f"row{i}\n"})])`, six truncations interleaved with five successful writes, Runner(max_tokens=1024): result.status == "model_error", result.final_message == TRUNCATION_ABORT.format(n=6, cap=1024), result.extra["truncations"] == 6, the run_end event has truncations 6, the last nudge event (kind "truncated") has no "via" key, and the run never hit the consecutive rule (each write reset FailureTracker). Variant: three consecutive `length` text replies as the 4th-6th truncations → final_message == "aborted after 3 consecutive empty_reply failures" (the consecutive rule wins). Variant: the sixth truncation on the tool path (`_bad_args(...)` in a `_resp(finish_reason="length")`) → the transcript has that call's tool_result with the truncated text, then run_end model_error with TRUNCATION_ABORT.
   tests/test_bench.py: `bench._abort_kind(TRUNCATION_ABORT.format(n=6, cap=1024)) == "truncated"`; the existing `_abort_kind` assertions unchanged.
   tests/test_soak_tools.py: add test_f5_fires_on_the_new_generic_truncation_wording — F5 fires only when a later append_file's path equals the truncated call's recovered path or a path an earlier successful write_file wrote, so the fixture must contain that write: events = [assistant(finish_reason="length", tool_calls=[write_file, read_file]), tool_result(tool="write_file", args='{"path": "big.txt", "text": "a"}', result="Wrote 1 bytes to big.txt (new file, 1 line)"), tool_result(tool="read_file", args="{", result=runner.truncated_call_result("read_file", "{", dict(cap=1024, cap_chars=4096, received=10, cut_chars=0, cut_lines=0, target_chars=1024, target_lines=17, n=1, max=6))), assistant(tool_calls=[append_file]), tool_result(tool="append_file", args='{"path": "big.txt", "text": "b"}', result="Appended to big.txt: +1 -0")] (copy the exact event shapes from the existing F5 test at lines ~288-302) → detect_features fires F5; the OLD generic wording "ERROR: your read_file call was cut off at the token limit before it completed." kept verbatim in the existing fixtures at lines ~215/~294/~316 still fires (do not rewrite those fixtures — they are the historical wording).

7. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` until green, then call finish with a summary. Do not touch docs/.
```

- [ ] **Step 2: Run**; **Step 3: Review** (abort placement after the consecutive check on both
  paths; the tool-path result is recorded before the abort; `_ABORT_RE` untouched; the harvest
  regex matches both wordings — `grep -c "cut off at the token limit" ~/.dirtywork/runs/*/transcript.jsonl | head`
  as a sanity check on old data); **Step 4: Host suite**; **Step 5:** merge, ledger row.

---

### Task W2a: `tools.py` — `parse_exit_code` moves, `parse_change_head`/`net_change`; `ProgressTracker` (spec §4.2)

**Files:**
- Modify: `dirtywork/tools.py:421-451` (below `describe_change`), `:994-1000`
  (`is_timeout_result` neighbourhood — `parse_exit_code` lands here); `dirtywork/runner.py:14`
  (the `from .tools import is_timeout_result` line), `:261-275` (remove `parse_exit_code`),
  `:294-310` (`ProgressTracker.note_call`).
- Test: `tests/test_tools_files.py` (test 8), `tests/test_runner.py` (test 9).

**Interfaces:**
- Produces (`dirtywork.tools`): `parse_exit_code(result) -> int | None` (moved, same body),
  `parse_change_head(result: str) -> tuple[str, str, int, int] | None`,
  `net_change(result: str) -> bool | None`.

- [ ] **Step 1: Brief** `$SCRATCH/brief-6566-w2a.md`:

```
Issue #66, task W2a of 12: a byte-identical write is not progress. Spec §4.2. Two small moves in dirtywork/tools.py and dirtywork/runner.py.

1. Move `parse_exit_code` from dirtywork/runner.py (line ~261, the function with the docstring "The integer after 'exit code: ' on a bash result's first line…") to dirtywork/tools.py, directly below `is_timeout_result` (line ~994), body unchanged. In runner.py delete the definition and change line 14 `from .tools import is_timeout_result` to `from .tools import is_timeout_result, net_change, parse_exit_code`; its two call sites (RepeatTracker.note_bash line ~362, run_verify line ~731) are unchanged. `grep -rn "parse_exit_code" tests/ dirtywork/` must then show only tools.py's definition and runner.py's import + two uses.

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
   Update the class docstring sentence about mutating tools accordingly.

4. Tests. tests/test_tools_files.py: test_parse_change_head_and_net_change_round_trip — for every verb the tools produce, build the result through the real producer: describe_change(path, old, new, verb=v) for v in ("Wrote", "Edited", "Appended to", "Inserted into", "Applied 1 edit to", "Applied 2 edits to") with (old="a\nb\n", new="a\nc\n") → parse_change_head returns (v, path, 1, 1) and net_change is True; with old == new → (v, path, 0, 0) and net_change is False; with a removal that yields the "(removed N non-blank line(s))" suffix (old="a\nb\n", new="a\n") → parsed and True; describe_write(path, None, "x\n", 2) (the new-file form) → parse_change_head None, net_change True; the "(diff omitted: file too large)" head (build it through describe_change with a new text of DESCRIBE_DIFF_MAX_LINES + 1 lines — the constant is at tools.py line ~24) → parse_change_head None, net_change None; "ERROR: no such file", "BLOCKED: …", "[output truncated at 10000 chars]" and "hello" → None/None.
   tests/test_runner.py: test_progress_tracker_ignores_noop_writes — t = ProgressTracker(stall_turns=4); t.note_call("write_file", {"path": "a"}, "Wrote a: +0 -0"); t.end_turn() is None and t.idle_turns == 1; t.note_call("write_file", {"path": "a"}, "Wrote a: +1 -0"); t.end_turn() is None and t.idle_turns == 0; the new-file form is progress; a result "weird" (unknown shape) is progress. test_identical_rewrites_stall — a Runner with stall_turns=4 and a provider that rewrites f.txt with its existing content ("data\n") every turn ends status "stalled" (the same call key repeats and the write is +0 -0); the existing ProgressTracker tests at lines ~982-1065 still pass.

5. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` until green, then call finish with a summary. Do not touch docs/.
```

- [ ] **Step 2: Run**; **Step 3: Review** (`parse_exit_code` has exactly one definition; the
  regex covers every verb — grep `verb=` in `tools.py` and compare; `None` keeps progress); **Step
  4: Host suite**; **Step 5:** merge, ledger row.

---

### Task W2b-1: `changes.py` — the fingerprint script, parser, texts; the doubles (spec §4.1, §4.3–§4.4 texts)

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
  hashes=None, **HostSandbox kwargs)` — `hashes=None` means "real bash for everything, just
  record"; the `git_parts` fixture.

- [ ] **Step 1: Brief** `$SCRATCH/brief-6566-w2b-1.md`:

```
Issue #66, task W2b-1 of 12: the worktree fingerprint primitives and the test doubles the guard tasks (W3a/W3b) will use. Spec §4.1 (the script is byte-exact and probed; do not "improve" it), §4.3 and §4.4 for the texts. Nothing in the runner changes in this task; the host test that exercises the real script on nested repositories is W2b-2.

1. Create dirtywork/changes.py with exactly this content:
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

   Self-check before finishing (the script must be byte-exact): `python3 -c 'from dirtywork.changes import FINGERPRINT_SCRIPT; print(FINGERPRINT_SCRIPT)' > /tmp/s1; sed -n '/^FINGERPRINT_SCRIPT = r"""/,/^git rev-parse HEAD"""/p' docs/superpowers/specs/2026-08-25-cap-aware-truncation-and-change-guard-design.md | sed '1s/^FINGERPRINT_SCRIPT = r"""//; $s/"""$//' > /tmp/s2; diff /tmp/s1 /tmp/s2 && echo SCRIPT-OK`.

2. tests/provider_doubles.py: add (import FINGERPRINT_SCRIPT from dirtywork.changes and HostSandbox from dirtywork.sandbox.host at the top of the file):
class FingerprintSandbox(HostSandbox):
    """A HostSandbox whose `bash` answers FINGERPRINT_SCRIPT from a scripted
    list and delegates everything else (verify commands and the file tools
    stay real). Entries: a str hash -> "exit code: 0\n<hash>\n<40 zeros>" (two
    lines, as the real script prints a tree and HEAD) -- str entries MUST be
    40 lowercase hex chars (e.g. "a" * 40): they go through
    parse_fingerprint, which drops anything else; None -> "exit code:
    1\nerror: boom"; an exception instance -> raised. The last entry repeats.
    hashes=None -> the real script runs for the fingerprint too (a recording
    HostSandbox). `commands` records every (command, timeout) the runner sends."""
    def __init__(self, worktree, hashes=None, **kwargs):
        super().__init__(worktree, **kwargs)
        self.hashes = None if hashes is None else list(hashes)
        for h in self.hashes or []:
            assert h is None or isinstance(h, BaseException) or (len(h) == 40 and all(c in "0123456789abcdef" for c in h)), h
        self.commands = []

    def bash(self, command, timeout=120):
        self.commands.append((command, timeout))
        if command != FINGERPRINT_SCRIPT or self.hashes is None:
            return super().bash(command, timeout)
        entry = self.hashes.pop(0) if len(self.hashes) > 1 else self.hashes[0]
        if isinstance(entry, BaseException):
            raise entry
        if entry is None:
            return "exit code: 1\nerror: boom"
        return f"exit code: 0\n{entry}\n{'0' * 40}"

3. tests/test_runner.py: beside the `parts` fixture (line ~95) add a `git_parts` fixture identical to `parts` except that `wt` is a real repository: after writing f.txt run `git -C wt init -q`, `git -C wt -c user.email=t@t -c user.name=t add -A`, `git -C wt -c user.email=t@t -c user.name=t commit -qm init` via subprocess.run(check=True); mark the fixture's tests with pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH"). The sandbox it returns is a FingerprintSandbox(wt) with hashes=None (real fingerprints, recorded commands), imported from tests.provider_doubles.

4. tests/test_changes.py (new; import from dirtywork.changes):
   test_parse_fingerprint_sorts_and_requires_two_hashes: two hex lines → the sorted join; the same four lines given in two different orders → equal fingerprints; "exit code: 0\n" + one hex line → (None, "fewer than two hash lines"); "" → (None, "no output").
   test_parse_fingerprint_ignores_rc0_warnings: "exit code: 0\nwarning: something\n<h1>\n<h2>" → the two hashes joined.
   test_parse_fingerprint_fails_open_with_a_reason: "exit code: 1\nerror: 'vendor/x/' does not have a commit checked out\n<h1>" → (None, "error: 'vendor/x/' does not have a commit checked out"); "exit code: 128\n" → (None, "exit code: 128"); tools.timeout_result(60) → (None, a string starting with "ERROR: command timed out after"); "ERROR: bash failed: no such container" → (None, that line); "BLOCKED: sudo is not allowed…" → (None, that line); "exit code: 0\n" + 240 hex lines + "\n[output truncated at 10000 chars — bash output capped]" → (None, "[output truncated at 10000 chars — bash output capped]"); a 500-char diagnostic → reason of length 200.
   test_fingerprint_without_bash: fingerprint(object()) == (None, "sandbox has no bash").
   test_script_shape_and_guardrails: FINGERPRINT_SCRIPT contains each of "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", ":(exclude,literal)", "trap 'rm -rf \"$tmp\"' EXIT", "git -c core.fsmonitor=false add -A -- .", "git write-tree", "git rev-parse HEAD", "GIT_CONFIG_GLOBAL=/dev/null"; guardrails.check_bash_command(FINGERPRINT_SCRIPT, worktree=tmp_path) is None and check_bash_command(FINGERPRINT_SCRIPT, sandboxed=True) is None.
   test_fingerprint_sandbox_double: FingerprintSandbox(tmp_path, ["a"*40, None, RuntimeError("x")]).bash(FINGERPRINT_SCRIPT, 60) returns "exit code: 0\n" + "a"*40 + "\n" + "0"*40 the first time, "exit code: 1\nerror: boom" the second, raises RuntimeError the third and every time after; a non-hex str entry raises AssertionError at construction; commands records (FINGERPRINT_SCRIPT, 60).

5. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` until green, run the self-check of item 1 (it must print SCRIPT-OK), then call finish with a summary. Do not modify dirtywork/runner.py in this task; do not touch docs/.
```

- [ ] **Step 2: Run**; **Step 3: Review** (script byte-exact — run the item-1 self-check on the host;
  parser cases; the double's hex assertion; `git_parts` returns a recording sandbox); **Step 4: Host
  suite**; **Step 5:** merge, ledger row.

---

### Task W2b-2: The real script on the host — nested, unborn, non-ASCII (spec §7 test 11b)

**Files:**
- Test: `tests/test_changes.py` (one test).

- [ ] **Step 1: Brief** `$SCRATCH/brief-6566-w2b-2.md`:

```
Issue #66, task W2b-2 of 12: one host test that runs the real fingerprint script (dirtywork.changes.FINGERPRINT_SCRIPT, added in W2b-1) on a repository with nested repositories. Spec §7 test 11b. Add to tests/test_changes.py:

test_real_script_on_the_host (skip with pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")):
  1. Build a repo in tmp_path: `git init -q`, write README.md, `git -c user.email=t@t -c user.name=t add -A`, `git … commit -qm init` (use a helper `_git(*args, cwd)` that calls subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True, capture_output=True)).
  2. `from dirtywork.sandbox.host import HostSandbox`; `raw = HostSandbox(tmp_path).bash(FINGERPRINT_SCRIPT, FINGERPRINT_TIMEOUT)`; `fp, reason = parse_fingerprint(raw)`; assert reason is None and fp is not None and the raw result has exactly 2 lines matching ^[0-9a-f]{40}$ (a tree and HEAD).
  3. Create, each with its own commit unless stated: a nested repo vendor/inner (init, a file, commit); an UNBORN nested repo vendor/unborn (init + a file x.txt, NO commit); a nested-in-nested vendor/inner/deeper (init + file + commit); nested roots named "vendor/café" and "vendor/sp ace" (init + file + commit each). Run again: rc 0 (parse_exit_code(raw) == 0), exactly 6 hash lines + HEAD = 7 lines matching the hex regex, fp not None.
  4. Write a new file inside vendor/unborn → run again → exactly ONE hex line differs between the two raw results (compare the sets of hex lines: symmetric difference has 2 elements) and the parsed fingerprints differ.
  5. Rewrite README.md byte-identically (read it, write it back) → the parsed fingerprint equals the previous one.
  6. Count files under the real object store (`git rev-parse --git-path objects` → walk it with os.walk) before and after a run with a new 100 KB untracked file (os.urandom) present at the root → equal counts (the scratch object directory).
  7. Count entries of tempfile.gettempdir() before and after a run → equal (no leaked scratch dir).

Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` until green, then call finish with a summary. Do not change dirtywork/changes.py (if the script misbehaves, say so in the summary rather than editing it); do not touch docs/.
```

- [ ] **Step 2: Run**; **Step 3: Review** (the seven checks present; no script edit); **Step 4: Host
  suite**; **Step 5:** merge, ledger row.

---

### Task W3a-1: The guard in the runner — start, completion paths, `finish()`, `require_changes`; the tests it breaks (spec §4.1 When/Failure, §4.3)

**Files:**
- Modify: `dirtywork/runner.py:474-533` (`Runner.__init__`), `:546-560` (state), `:671-704`
  (`finish()`), `:746-786` (`check_verify`), `:1005-1020` (the loop's `try:`);
  `tests/test_transcript_schema.py:18-31` (`NUDGE_KINDS`, `STATUSES`, `RUN_END_FIELDS`);
  `tests/test_runner.py` doubles (`:667-671`, `:1562`, `:1804`, `:1857-1876`, `:1891`, `:1922`,
  `:1943`); `tests/test_main.py` host runs (`_first_run` `:1365-1369` and the single-turn runs).
- Test: `tests/test_runner.py` (tests 12–14).

**Interfaces:**
- Consumes: `changes.fingerprint`, `UNCHANGED_REQUIRED`, `UNCHANGED_PLAIN` (W2b-1);
  `FingerprintSandbox`, `git_parts` (W2b-1).
- Produces: `Runner(..., require_changes: bool = False)`; run-scoped `fp_start`, `fp_check`,
  `fp_turn`, `fp_value`, `changed`, `changed_reason`, `unchanged_finishes`; `take_fingerprint()`
  closure; status `unchanged`; `run_end.changed` (bool | None, always), `run_end.changed_reason`
  (str, sparse), `RunResult.extra` the same; finish results `run not finished: nothing changed`
  and `run not finished: change check could not run (…)`; nudge kind `unchanged_finish`.

- [ ] **Step 1: Brief** `$SCRATCH/brief-6566-w3a-1.md`:

```
Issue #66, task W3a-1 of 12: the change guard in dirtywork/runner.py — fingerprint at run start, on both completion paths (before verify) and in finish(); reject a completion that changed nothing once; end a feedback resume `unchanged` on the second. Spec §4.1 ("When" and "Failure"), §4.3 — read them; the code below is exact where it is code. The every-K-turns check is W3b (not here); the CLI is W3c (not here); more tests are W3a-2.

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
            None. One rule: a successful measurement sets changed/clears the
            reason; a failed or raising one sets changed = None and stores
            the reason (so changed_reason is present exactly when changed is
            null for that reason). BudgetExceeded/SandboxError are stored,
            then re-raised for the caller to map (finish() catches them)."""
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
                changed, changed_reason = None, reason
                return None
            fp_turn, fp_value = turns, fp
            if fp_start is not None:
                changed, changed_reason = (fp != fp_start), None
            return fp
   Note `fp_start` is read here, not assigned; the start assignment is item 5.

5. Run start: the loop today is (line ~1005)
        try:
            while True:
   Insert, inside that `try:` and immediately before `while True:`, this one block (BudgetExceeded/SandboxError end the run through finish(); a KeyboardInterrupt during the exec reaches the existing outer `except KeyboardInterrupt: return finish("interrupted", "")` with turns == 0 and one run_end):
            try:
                fp_start = take_fingerprint()      # spec #66 §4.1 (1); None = guard off for this run
            except BudgetExceeded as e:
                return finish("budget_exceeded", e.reason)
            except SandboxError as e:
                return finish("sandbox_error", str(e))
            fp_check = fp_start
   (`fp_start` and `fp_check` need `nonlocal` only if assigned inside a nested function — here they are assigned in Runner.run itself, so no declaration is needed; the CLI, not the Runner, reports changed_reason — W3c.)

6. finish(status, final): add `changed` to its nonlocal declaration if it has one (it must read the run's `changed` and `changed_reason`; nothing in finish assigns them — take_fingerprint does). As its FIRST statements (before `drain_sandbox()`):
            if (status not in ("interrupted", "timeout", "budget_exceeded", "sandbox_error")
                    and fp_start is not None and fp_turn != turns):
                try:
                    take_fingerprint()              # §4.1 (4): run_end.changed for max_turns/stalled/stuck/model_error/verify_failed/context_exhausted; a failure sets changed None + reason
                except (BudgetExceeded, SandboxError):
                    pass                            # reason and changed=None already stored by take_fingerprint
   Then, in the `extra` dict, add `"changed": changed,` and, only when changed_reason is not None, `"changed_reason": changed_reason` (sparse — build the dict then `if changed_reason is not None: extra["changed_reason"] = changed_reason`). AFTER the whole `if self.finalize is not None:` block (i.e. after its last line `extra["finalize_error"] = finalize_state["error"]`, dedented to the same level as the `if not run_end_written:` that follows — so it runs with or without a finalize callable), add:
                if changed_reason is not None and changed_reason.startswith("budget: ") \
                        and not extra.get("watchdog_violation"):
                    extra["watchdog_violation"] = changed_reason[len("budget: "):]
                    extra["watchdog_violation_kind"] = "budget"
   (the docker budget sample consumed the violation before raising; never overwrite a value finalize() set — the `not extra.get(...)` guard is that rule).

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
   Everything after (the `if not self.verify:` branch and the verify logic) is unchanged. The two call sites of check_verify (the plain-answer path at line ~843-850 and the finish path at ~958-975) need NO change: a returned text is delivered there exactly as verify feedback is today (the finish path treats it as the already-resolved finish result; the plain path sends it as the user message).

8. tests/test_transcript_schema.py: NUDGE_KINDS += ["no_change", "unchanged_finish"]; STATUSES += ["unchanged"]; RUN_END_FIELDS += ["changed", "changed_reason"] (docs/transcript-schema.md already documents them).

9. Existing tests/test_runner.py tests to adjust — the start fingerprint is one more `bash` call, doubles without `bash` (BudgetBustingSandbox ~726/~1887, _GrepTimeoutSandbox ~1666, ExplodingSandbox ~2613) are guard-off automatically; `parts` (a non-repo tmp dir) runs guard-off with `changed` None and `changed_reason` starting with "fatal: not a git repository" on every run_end:
   a. line ~667-671 (test_finalize_merges_into_run_end_and_result_extra, on `parts`): before the exact comparison add `assert result.extra.pop("changed_reason").startswith("fatal: not a git repository")`, and the exact dict gains `"changed": None` (beside W1a-1's `"truncations": 0`). Do not pin git's full diagnostic — its wording is git-version dependent.
   b. line ~1804 `box.commands == ["npm test"]` → compare `[c for c in box.commands if c != FINGERPRINT_SCRIPT]` (import FINGERPRINT_SCRIPT from dirtywork.changes).
   c. the doubles whose `bash` raises or asserts unconditionally — `Raising` (~1857-1876), `InterruptingSandbox` (~1891, raises KeyboardInterrupt — without this line the interrupt would fire on the start fingerprint before turn 1), `Exploding` (~1943, raises RuntimeError — the test needs it to fire on the turn-1 bash after the finish call), and the ones at ~1562 and ~1922 — gain `if command == FINGERPRINT_SCRIPT: return "exit code: 1\nerror: test double"` as the first line of their `bash`, so the scripted behaviour still fires on the turn the test targets.

10. tests/test_main.py host runs (`--sandbox none` on a real linked worktree, guard ON): a run whose model completes with a single plain "done" answer now takes TWO turns — the first completion is rejected with UNCHANGED_PLAIN, the second completes — so: `_first_run` (line ~1365-1369) passes `--max-turns 2`; the inline `--max-turns 1` host runs (grep -n '"--max-turns", "1"' tests/test_main.py — around lines ~1725, ~1823, ~1850, ~1883, ~2016) pass `--max-turns 2`; `turns == 1` assertions on those runs (e.g. ~1320) become 2, and any text assertion on the prior run's turn count (e.g. ~1410 "after 1 turns") follows; `_resume_responses` (line ~1372-1380, a bash body then a finish body) gets a write_file tool-call body (path "resumed.txt", text "x\n") inserted before its finish body so a resumed run changes something and completes. Keep every assertion's meaning; do not loosen `status == "completed"` checks. (Docker-mode tests in the file use fakes whose bash returns "" or "exit code: 0\n" — they run guard-off and need no change.)

11. New tests (tests/test_runner.py; FingerprintSandbox from tests.provider_doubles with `parts`' wt; `git_parts` for real fingerprints; scripted hashes are 40 lowercase hex chars):
   test_start_fingerprint_before_first_chat (parts wt, require_changes False): FingerprintSandbox(wt, ["a"*40]) with responses [finish, finish] — commands[0] == (FINGERPRINT_SCRIPT, 60) and the provider's first request happened after it (record order with a provider that appends to a shared list); the first finish is rejected (its tool_result == UNCHANGED_PLAIN, since every measurement equals the start), the second → status "completed", run_end.changed is False. The same with hashes ["a"*40, "b"*40] and a single finish response → "completed" on the first finish, run_end.changed is True.
   test_start_fingerprint_failure_turns_guard_off: hashes [None] with responses [finish] → status "completed" on the first finish (no rejection), run_end.changed is None, run_end.changed_reason == "error: boom", and exactly ONE FINGERPRINT_SCRIPT command for the whole run (no completion measurement, no finish() measurement).
   test_zero_change_finish_rejected_once_then_completed (git_parts — its sandbox records commands; require_changes False; verify="test -e f.txt"): responses [finish, finish] → the first finish's transcript tool_result.result == UNCHANGED_PLAIN, a nudge event kind "unchanged_finish" via "tool_result", the verify command does not appear in sandbox.commands before the second finish's request; a bash call that failed twice before the first finish (`_bash_call` with "false", the RepeatTracker shape at ~1308) is not counted on after the rejection (repeats were reset: a third identical failing "false" after the rejection does not end the run "stuck" with stuck_repeats=3); the second finish → status "completed", the verify command appears exactly once in sandbox.commands, run_end.changed is False, result.extra["changed"] is False.
   test_zero_change_finish_ends_unchanged_when_required (git_parts, require_changes=True, verify="test -e f.txt"): [finish, finish] → status "unchanged", final_message == the second summary, the finish results == [UNCHANGED_REQUIRED, "run not finished: nothing changed"], the verify command never appears in sandbox.commands, finalize called (use the existing finalize-recording pattern at ~660), run_end.changed False. Variant with a write_file between the finishes → "completed", changed True. Variant: finalize raises → status still "unchanged", extra["finalize_error"] set.

12. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` until green, then call finish with a summary. Do not implement the every-K check; do not touch dirtywork/__main__.py, dirtywork/resume.py or docs/.
```

- [ ] **Step 2: Run**; **Step 3: Review** (the finish-time block is the first statement of
  `finish()`; the skip set is exactly the four statuses; `check_verify`'s check precedes `if not
  self.verify`; `resolve_finish` texts exact; `changed_reason` sparse; the watchdog block sits
  outside the finalize branch; the doubles discriminate on the command rather than weakening
  assertions; the `test_main` two-turn edits keep every `completed` assertion; schema lists
  updated); **Step 4: Host suite**; **Step 5:** merge, ledger row.

---

### Task W3a-2: The guard's remaining runner tests (spec §7 tests 15–17, 19 minus the K cases)

**Files:**
- Test: `tests/test_runner.py`.

**Interfaces:**
- Consumes: everything W3a-1 produces; `FingerprintSandbox`, `git_parts` (W2b-1).

- [ ] **Step 1: Brief** `$SCRATCH/brief-6566-w3a-2.md`:

```
Issue #66, task W3a-2 of 12: more tests for the change guard W3a-1 added to dirtywork/runner.py (take_fingerprint, the check in check_verify, the finish()-time measurement). Read those first (grep -n "def take_fingerprint\|unchanged_finishes\|def check_verify\|def finish" dirtywork/runner.py). Helpers: FingerprintSandbox(wt, hashes) from tests.provider_doubles (scripted hashes are 40 lowercase hex chars, e.g. "a"*40; None → a failed measurement; an exception instance → raised; the first entry answers the START fingerprint; the last entry repeats), the `git_parts` fixture (real repo, real fingerprints, recorded commands), `_resp`, `_call`, `_bash_call`, FakeProvider, `_events`. Spec §4.1, §4.3, §7 tests 15-17 and 19.

   test_plain_answer_rejection_is_a_user_message (git_parts): responses [_resp(content="all done"), _resp(content="all done")] → the second request's last message is {"role": "user", "content": UNCHANGED_PLAIN}; the nudge event kind "unchanged_finish" has via "user"; then "completed" with run_end.changed False; with Runner(require_changes=True) the second answer → status "unchanged". A rejection on the last allowed turn (max_turns=1, one "all done") → status "max_turns".
   test_mixed_turn_rejection (git_parts): one response with [_call("r", "read_file", {"path": "f.txt"}), _call("f", "finish", {"summary": "s"})] then a second finish → in the second request the read_file tool message keeps index order before the finish tool message, the finish tool message's content == UNCHANGED_PLAIN at its own position; a bash call in the same first turn whose command sleeps past its timeout (the shape of the existing timeout tests, `_bash_call("c", "sleep 999")` with a small timeout) still gets TIMEOUT_NUDGE delivered that turn.
   test_fingerprint_exceptions_map_like_verify (parts wt): FingerprintSandbox(wt, [BudgetExceeded("disk")]) → status "budget_exceeded", one run_end with changed None and changed_reason == "budget: disk"; [SandboxError("gone")] → "sandbox_error", changed_reason "sandbox: gone"; on a completion path — hashes ["a"*40, BudgetExceeded("disk")], responses [finish] → "budget_exceeded" and the finish tool_result == "run not finished: change check could not run (disk)"; a KeyboardInterrupt instance as the START entry → status "interrupted", turns == 0, exactly one run_end event. (Build BudgetExceeded the way the existing tests at ~726 do; SandboxError from dirtywork.sandbox.)
   test_finish_time_fingerprint (parts wt, FingerprintSandbox): (a) a max_turns run (max_turns=2, two read_file turns, hashes ["a"*40, "b"*40]) → run_end.changed is True from the finish-time measurement, and the last FINGERPRINT_SCRIPT command is recorded after the last chat request and before the last drain_notices call (give the double a `drain_notices` that appends a marker to the same `commands` list); (b) a subclass whose finalize() flips a flag after which bash returns "ERROR: bash failed: gone" → changed is not None on max_turns (the measurement precedes finalize); (c) a subclass whose fingerprint bash queues a notice ("stray_kill", "text") returned by drain_notices → that nudge record precedes run_end in the transcript; (d) hashes ["a"*40, "a"*40, None] with responses [finish, read_file] and max_turns=2: the finish on turn 1 is rejected (changed False), turn 2 ends max_turns and the finish-time measurement fails → run_end.changed is None (not the stale False) and changed_reason == "error: boom"; (e) BudgetExceeded("disk") as the finish-time entry of a max_turns run → status "max_turns" preserved, changed None, changed_reason "budget: disk", run_end.watchdog_violation == "disk" and watchdog_violation_kind == "budget" (no finalize on this Runner) — and with a finalize that returns {"watchdog_violation": "other", "watchdog_violation_kind": "sandbox_error"} those two keep finalize's values; (f) a rejection on turn 1 then a KeyboardInterrupt raised by the double's bash on turn 2 (the entry after the two "a"*40s) → "interrupted" with run_end.changed False and no "changed_reason" key; (g) count FINGERPRINT_SCRIPT commands: statuses interrupted / timeout / budget_exceeded / sandbox_error take no finish-time fingerprint (for timeout use Runner(timeout=0) with one read turn; for budget/sandbox raise from a TOOL call via the double's bash, not from the fingerprint).

Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` until green, then call finish with a summary. Do not change dirtywork/ (a failing assertion means the test is wrong — re-read the runner); do not touch docs/.
```

- [ ] **Step 2: Run**; **Step 3: Review** (every sub-case present and asserted on the transcript or
  the sandbox's command list; no code change); **Step 4: Host suite**; **Step 5:** merge, ledger row.

---

### Task W3b: The every-K `no_change` check (spec §4.4)

**Files:**
- Modify: `dirtywork/runner.py` (`Runner.__init__`; `check_progress` neighbourhood `:706-717`;
  the two turn-end sites `:855` and `:985`).
- Test: `tests/test_runner.py` (test 18, the K cases of 19).

**Interfaces:**
- Consumes: `fp_start`, `fp_check`, `take_fingerprint()` (W3a-1); `NO_CHANGE_SINCE_START_REQUIRED`,
  `NO_CHANGE_SINCE_START_PLAIN`, `NO_CHANGE_RECENT`, `DEFAULT_NO_CHANGE_TURNS` (W2b-1).
- Produces: `Runner(..., no_change_turns: int = DEFAULT_NO_CHANGE_TURNS)`; `check_no_change() ->
  (RunResult | None, str | None, dict | None)`; nudge kind `no_change`.

- [ ] **Step 1: Brief** `$SCRATCH/brief-6566-w3b.md`:

```
Issue #66, task W3b of 12: every K turns, if the worktree fingerprint has not changed since the last check, nudge the model (never abort). Spec §4.4; W3a-1 already added the fingerprint state and take_fingerprint() to dirtywork/runner.py.

1. Import `DEFAULT_NO_CHANGE_TURNS, NO_CHANGE_SINCE_START_REQUIRED, NO_CHANGE_SINCE_START_PLAIN, NO_CHANGE_RECENT` from .changes. Runner.__init__ gains `no_change_turns: int = DEFAULT_NO_CHANGE_TURNS` (after require_changes), stored as `self.no_change_turns` with the comment "# Spec #66 §4.4: every K turns a fingerprint; equal to the last check's -> a nudge, never an abort; 0 disables. Not a CLI flag."

2. A closure in Runner.run next to check_progress, with the same return shape (no parameter: a turn with a pending finish never reaches this call — both turn-end sites return from check_verify's path first, so the finish check has already measured that turn):
        def check_no_change():
            """(RunResult to end the run with, or None; nudge text, or None;
            the nudge record, or None) -- spec #66 §4.4. Fires on turns that
            are a multiple of no_change_turns when the guard is on. Equal to
            the last check's fingerprint -> nudge and reset the baseline;
            different -> reset the baseline silently; unmeasurable -> keep
            the baseline (take_fingerprint stored the reason)."""
            nonlocal fp_check
            if (fp_start is None or self.no_change_turns <= 0
                    or turns % self.no_change_turns != 0):
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

3. Call it at both turn-end sites, right after `check_progress()`:
   text path (line ~855): after `stalled, stall_text, stall_record = check_progress()` / `if stalled is not None: return stalled`, add `ended, nc_text, nc_record = check_no_change()` / `if ended is not None: return ended`, and join nc_text after stall_text in the deliver call: `deliver(_join_nudges(NUDGES[kind].format(**trunc), sandbox_text, stall_text, nc_text), [kind_record, *sandbox_records, stall_record, nc_record])` — check whether deliver() skips None records; if it does not, filter them (`[r for r in (...) if r is not None]`).
   tool path (line ~985): the same after that site's check_progress() call — this site is reached only when pending_finish is None (the `if pending_finish is not None:` block above it returns) — with nc_text/nc_record appended after stall_text/stall_record in that site's `_join_nudges`/records lists (text order becomes malformed, sandbox, timeout, stall, no_change; transcript records: stall, no_change, malformed_entry, timeout, sandbox — writing the no_change record right after check_progress wrote the stall record gives that order).

4. Tests (tests/test_runner.py; FingerprintSandbox from tests.provider_doubles with `parts`' wt so hashes are scripted — 40 lowercase hex chars; the double's first entry answers the START fingerprint; the last entry repeats; FakeProvider pops responses and raises IndexError when it runs out, so script every turn):
   test_no_change_nudge_since_start: Runner(no_change_turns=3, require_changes=True), hashes ["a"*40] (every measurement equal), responses [read_file, read_file, read_file, finish, finish] → a nudge event kind "no_change" turn 3 via "tool_result", the third tool result's follow_up == NO_CHANGE_SINCE_START_REQUIRED.format(k=3) (and "finish" not in that text); the finish on turn 4 is rejected (UNCHANGED_REQUIRED), the finish on turn 5 ends the run "unchanged". The same without require_changes → NO_CHANGE_SINCE_START_PLAIN.format(k=3) and the run ends "completed" with changed False.
   test_no_change_nudge_recent_after_a_change: hashes ["a"*40, "b"*40, "b"*40] (start a; the K=3 check measures b → changed, silent; the turn-6 check measures b == fp_check → nudge), six read_file responses then [finish] → no nudge at turn 3, a nudge at turn 6 with NO_CHANGE_RECENT.format(k=3) (it names finish); the finish on turn 7 → "completed", changed True.
   test_no_change_check_skips: (a) hashes ["a"*40, None, "a"*40] with six reads → no nudge at 3 (failed measurement, baseline kept, changed_reason "error: boom" set until turn 6 clears it), a nudge at 6 (a == the start baseline); (b) a finish on turn K → no K measurement that turn (count FINGERPRINT_SCRIPT commands: start + the completion check only); (c) no_change_turns=0 → only the start fingerprint is ever sent; (d) a guard-off run (start None) → no FINGERPRINT_SCRIPT after the start one; (e) BudgetExceeded("disk") as the entry the turn-K check pops → status "budget_exceeded", run_end.changed None, changed_reason "budget: disk"; SandboxError("gone") likewise → "sandbox_error", "sandbox: gone"; (f) stall + no_change on one turn: Runner(stall_turns=4, no_change_turns=3), hashes ["a"*40], responses [read_file f.txt, the identical read_file f.txt (idle 1), _resp(content="") (an empty reply: idle 2 and the stall nudge fires at stall_turns // 2 == 2), finish, finish] → the fourth request's last user message == NUDGES["empty"] + "\n\n" + STALL_NUDGE.format(n=2) + "\n\n" + NO_CHANGE_SINCE_START_PLAIN.format(k=3), and the transcript order on turn 3 is nudge{empty} → nudge{stall} → nudge{no_change}.
   test_run_ending_on_a_k_check_turn_reuses_it: Runner(no_change_turns=4, require_changes=False, max_turns=4), hashes ["a"*40, "a"*40, "c"*40], responses [finish, read_file, write_file (path "g.txt"), read_file] → turn 1's finish is rejected (a == a), turn 4's K check measures "c" (changed, no nudge because c != a), then max_turns ends the run and finish() takes no further fingerprint because fp_turn == turns: exactly THREE FINGERPRINT_SCRIPT commands (start, turn 1's completion check, turn 4's K check), no nudge event with kind "no_change", run_end.changed is True.

5. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` until green, then call finish with a summary. Do not touch dirtywork/__main__.py, dirtywork/resume.py or docs/.
```

- [ ] **Step 2: Run**; **Step 3: Review** (both sites call it; the skip conditions; the three
  texts; baseline handling on `None`; the record order; the response scripts in the tests match
  the turn counts); **Step 4: Host suite**; **Step 5:** merge, ledger row.

---

### Task W3c: CLI wiring, the resume block, the `unchanged` gate, the end-to-end tests (spec §4.5, §5.2 `__main__`)

**Files:**
- Modify: `dirtywork/__main__.py:590-591` (`_seed_payload`), `:609` (`_contract_fields`),
  `:842-847` (resume gate), `:929-941` (`Runner(...)`), `:954` (after `runner.run()`);
  `dirtywork/resume.py:224-256` (`build_resume_task`); `tests/test_main.py:19-28`
  (`_DEFAULT_EVIDENCE`), `:1627-1634` (`test_resume_inherits_the_prior_provider`).
- Test: `tests/test_resume.py:148-156`, `:272-303` (test 20), `tests/test_main.py` (tests 21–22).

**Interfaces:**
- Consumes: `Runner(require_changes=...)`, `run_end.changed` / `changed_reason` (W3a-1).
- Produces: `dirtywork: change guard off: <reason>` on stderr; stdout JSON / `run.json` keys
  `changed`, `changed_reason` (sparse); the resume gate message; the two resume block shapes.

- [ ] **Step 1: Brief** `$SCRATCH/brief-6566-w3c.md`:

```
Issue #66, task W3c of 12: wire the guard into the CLI and reorder the resume block. Spec §4.5, §5.2 (the `dirtywork/__main__.py` bullet). W3a-1/W3a-2/W3b added the runner side; W3a-1 already switched the host runs in tests/test_main.py to `--max-turns 2` and added a write_file body to `_resume_responses`.

1. dirtywork/__main__.py:
   a. `_seed_payload` (line ~590): after `"timeouts": 0,` and `"truncations": 0,` add `"changed": None,`.
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
   tests/test_main.py: (a) `_DEFAULT_EVIDENCE` (line ~19-28) gains `"changed": None,` (not `changed_reason` — it is sparse); there is no other exact-payload assertion in the file. (b) test_resume_inherits_the_prior_provider (~1627-1634) resumes with `--feedback "keep going"` and the default plain-"done" client — with require_changes now wired that run would end "unchanged": before its resume call add `patch_provider(monkeypatch, m2, lambda base_url=None: _ScriptedClient(base_url, _resume_responses()))` (the same pattern as line ~1462) so it writes a file and still completes with rc 0. (c) The resume gate: a prior run.json with "status": "unchanged" and no --feedback → rc 2 and "pass --feedback to tell it what to change" on stderr; with --feedback → runs (use the existing _prior/run.json fixtures of the resume tests). (d) End-to-end on the host (`--sandbox none`, real linked worktrees, so the real fingerprint runs; the `_first_run` helper already uses `--max-turns 2`): a `resume --feedback` whose scripted client finishes twice with no write (two finish bodies) → rc 1, run.json["status"] == "unchanged", run.json["changed"] is False, run.json["truncations"] == 0, "changed_reason" not in run.json; the same with a write_file body between the finishes → "completed", changed True; a fresh run whose client finishes twice → "completed", changed False; the stdout payload and run.json carry "truncations" and "changed" on the failure paths (`_fail_setup`/`_fail_run`: 0 / None) and never "changed_reason" there. (e) stderr: a guard-off run prints exactly one line starting with "dirtywork: change guard off: " (capsys) — use a docker-mode test whose fake bash returns "" (e.g. the shape at ~195) — and a guard-on host run prints no such line.

4. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` until green, then call finish with a summary. Do not touch docs/.
```

- [ ] **Step 2: Run**; **Step 3: Review** (block shapes byte-exact vs §4.5; the gate message; the
  stderr line printed once and only by `__main__`; `_contract_fields` sparse rule; no weakened
  `test_main` assertions); **Step 4: Host suite**; **Step 5:** merge, ledger row.

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
Issue #65/#66, task W4a of 12: bench, the soak harvester and `runs show` learn the two nudge kinds, the status and the three run.json fields. Spec §5.2. Consequences are stated there; do not hide a widened cell behind a looser assertion.

1. dirtywork/bench.py: NUDGE_KINDS (line ~48) gains "no_change", "unchanged_finish" APPENDED AT THE END (order is the column order; EMPTY_REPLY_NUDGE_KINDS is unchanged — these are harness nudges, not model failures). `_harness_failures` (line ~268-275) and `_failure_cell` (line ~425-433): the status tuple ("stalled", "max_turns", "sandbox_error") becomes ("stalled", "max_turns", "sandbox_error", "unchanged") in both (one constant if they share one; introduce `_STATUS_FAILURES` next to NUDGE_KINDS if they do not). The summarize legend (line ~783) derives from NUDGE_KINDS — verify it prints ten names.
   tests/test_bench.py: the pins at ~841-848 → `len(bench.NUDGE_KINDS) == 10`, `bench.NUDGE_KINDS[-2:] == ("no_change", "unchanged_finish")`, EMPTY_REPLY_NUDGE_KINDS == tuple(runner.NUDGES) unchanged; the literal at ~863-864 "0/0/0/0/0/0/3/0" → the full ten-wide cell "0/0/0/0/0/0/3/0/0/0" asserted as an exact column value (not a substring); rename the test whose name says eight kinds to say ten; a new assertion that a run whose run_end status is "unchanged" counts under the new status column in `_harness_failures`.

2. tools/soak_harvest.py detect_features (line ~179-235): add a feature code "S14" that fires when any nudge event has kind in ("unchanged_finish", "no_change"), or the run's status is "unchanged", or run.json["changed"] is False. Do NOT fold it into F8 (the stall detector) — F8 is unchanged. Where status features are detected (line ~242-245: stuck/stalled), treat "unchanged" like "stalled" (a harness-ended run) only if that code path builds a status column; otherwise leave it.
   tests/test_soak_tools.py: detect_features fires S14 for each of the three triggers separately and not for a run with changed True and no guard nudges; F8 unaffected by an unchanged_finish nudge.

3. dirtywork/runs.py MD_RESULT_FIELDS (line ~331) gains "truncations", "changed", "changed_reason" after "timeouts". The markdown renderer skips None values (check the loop at the render site; if it does not, make it skip None for these three so a null `changed` does not print "None").
   tests/test_runs.py: `runs show --markdown` on a run.json with truncations 2, changed False → both rendered; with changed None and no changed_reason → neither line; `_tool_result_outcome(text, tool="finish")` returns "not finished" for the four texts: UNCHANGED_REQUIRED, UNCHANGED_PLAIN (import from dirtywork.changes), "run not finished: nothing changed", "run not finished: change check could not run (x)" (no change to the classifier is needed: it already returns "not finished" for any finish result other than "run finished").

4. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` until green, then call finish with a summary. Do not touch docs/.
```

- [ ] **Step 2: Run**; **Step 3: Review** (order of `NUDGE_KINDS`; the ten-wide literal asserted
  exactly; `S14` separate from F8); **Step 4: Host suite** + `dirtywork bench summarize` on
  `~/.dirtywork/bench/soak-B.jsonl` renders (legend of ten); **Step 5:** merge, ledger row.

---

### Task W4b: Live docker test 24 (spec §7.24)

**Files:**
- Modify: `tests/test_docker_live.py` (docker-marked; helpers `_make_live_repo` in
  `tests/docker_live_helpers.py`, `_image_kwargs()` in the file).

- [ ] **Step 1: Brief** `$SCRATCH/brief-6566-w4b.md`:

```
Issue #66, task W4b of 12: one docker-marked live test in tests/test_docker_live.py. Spec §7 test 24. No live test in the file constructs a DockerSandbox by hand — this one does, modelled on the CLI (dirtywork/__main__.py lines ~466-486) and on the hand-built setup in test_docker_live_export_refused_into_nonempty_worktree (tests/test_docker_live.py ~371-397). You cannot reach docker from inside your sandbox: write the test, make sure the module imports and the non-docker suite is green, and call finish saying the live run is the reviewer's.

@pytest.mark.docker
def test_docker_live_fingerprint_matches_host_and_leaves_the_store_alone(tmp_path):
    import os, subprocess
    from dirtywork.changes import FINGERPRINT_SCRIPT, FINGERPRINT_TIMEOUT, fingerprint
    from dirtywork.sandbox import docker_args
    from dirtywork.sandbox.docker import DockerSandbox
    from dirtywork.sandbox.docker_cli import resolve_image
    from dirtywork.sandbox.host import HostSandbox
    from dirtywork.workspace import create_worktree, ensure_worktrees_excluded, worktree_base_commit

    def git(*args, cwd):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True, capture_output=True)

    repo = _make_live_repo(tmp_path)              # README.md committed on main
    ensure_worktrees_excluded(repo)
    worktree = create_worktree(repo, "livefp", None)   # checked out: README.md present
    base_commit = worktree_base_commit(worktree)
    # nested repositories inside the worktree: a committed one and an unborn one
    inner = worktree / "vendor" / "inner"; inner.mkdir(parents=True)
    git("init", "-q", cwd=inner); (inner / "a.txt").write_text("a\n"); git("add", "-A", cwd=inner); git("commit", "-qm", "init", cwd=inner)
    unborn = worktree / "vendor" / "unborn"; unborn.mkdir()
    git("init", "-q", cwd=unborn); (unborn / "x.txt").write_text("x\n")

    fp_host, reason = fingerprint(HostSandbox(worktree))
    assert reason is None and fp_host is not None

    cfg = docker_args.DockerConfig(**_image_kwargs())   # the file's helper: image override via DIRTYWORK_LIVE_IMAGE if set; check its return shape and adapt (it may return {"image": ...} or {})
    run_dir = tmp_path / "rundir"; run_dir.mkdir()
    sb = DockerSandbox(cfg, run_dir=run_dir, image_ref=resolve_image(cfg.image))
    try:
        sb.start(worktree, repo, "livefp", base_commit, branch=None, seed_from_worktree=True)
        fp_docker, reason = fingerprint(sb)
        assert reason is None
        assert fp_docker == fp_host              # content-addressed, sorted: identical on host and in the container
        count_cmd = "find /gitdir/objects -type f | wc -l"
        before = sb.bash(count_cmd, 60).split("\n")[1].strip()
        sb.bash("head -c 102400 /dev/urandom > big.bin", 60)     # a new untracked 100 KB file
        fp2, reason = fingerprint(sb)
        assert reason is None and fp2 != fp_docker
        after = sb.bash(count_cmd, 60).split("\n")[1].strip()
        assert before == after                    # the scratch object directory: the real store did not grow
        sb.bash("cp README.md /tmp/r && cp /tmp/r README.md", 60)   # byte-identical rewrite
        fp3, reason = fingerprint(sb)
        assert reason is None and fp3 == fp2
    finally:
        sb.stop()

Notes: `sb.bash` returns "exit code: N\n<output>", hence the split; keep the `import os` if `_image_kwargs` needs os.environ; if `_image_kwargs()` returns keys DockerConfig does not accept, pass only `image=`. Run `env -u GIT_DIR -u GIT_WORK_TREE python3 -m pytest -q -p no:cacheprovider` (the docker-marked test is deselected there) until green, then call finish with a summary saying the live run is the reviewer's. Do not touch docs/.
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
  `fixtures/rows.csv`; 1024 may instead abort `truncated` with `truncations == 6` in ≤ 16 turns
  (a mixed pair passes there); fail = 60 turns / 1800 s / `stalled` / `stuck` / `verify_failed` /
  `abort_kind == empty_reply` / any truncation text with `target_lines` below 13 / 25 / 50 at
  1024 / 2048 / 4096 (grep the transcripts); other abort kinds → one tie-break rerun, a second
  inconclusive = fail. Table in the ledger with per-run `truncations` and the first
  `target_lines`.
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

- **Spec coverage:** §3.1–§3.2 → W1a-1 (+ tests W1a-2); §3.3 → W1b (abort, bench, harvest) +
  W1a-1 (counter, field); §4.1 script/parser/texts → W2b-1, the real-script test → W2b-2,
  When/Failure → W3a-1; §4.2 → W2a; §4.3 → W3a-1 (+ W3c for `require_changes` wiring); §4.4 →
  W3b; §4.5 → W3c; §5.1 → D1 (doc) + W1a-1/W3a-1 (fields, kinds, status emitted; schema-test
  lists); §5.2 → W1a-1/W1b/W3c (`__main__`, bench abort, harvest regex) + W4a (kinds, status
  tuples, `S14`, `runs show`); §6 → D1, D2; §7 tests 1–5 → W1a-1/W1a-2, 6–7 → W1b, 8–9 → W2a,
  10–11 → W2b-1, 11b → W2b-2, 12–14 → W3a-1, 15–17 + 19 → W3a-2 (K cases → W3b), 18 → W3b,
  20–22 → W3c, 23a → W1a-1/W3a-1, 23b/23c → W4a, 24 → W4b; §8 C1 → C1, F5/suites/PR → C5; §9
  nothing.
- **Placeholders:** none — every brief carries the code, texts, regexes, response scripts and
  assertions; the only "as today" references point at code the worker can read at the cited
  lines.
- **Type consistency:** `chunk_target(max_tokens, cut_chars, cut_lines)` (W1a-1) is what the
  brief's `note_truncation` calls and W1a-2 asserts; `truncated_call_result(tool, raw_arguments,
  trunc)` (W1a-1) is what W1b's tests and `test_soak_tools.py:939` call; `parse_fingerprint ->
  (str | None, str | None)` and `fingerprint(sandbox)` (W2b-1) are what `take_fingerprint` (W3a-1)
  unpacks; `check_no_change() -> (ended, text, record)` (W3b) mirrors `check_progress`;
  `FingerprintSandbox(worktree, hashes=None, **kw)` (W2b-1) is what `git_parts`, W3a-1, W3a-2
  and W3b construct — hex hashes everywhere; `net_change` (W2a) is what `ProgressTracker.note_call`
  reads; the nudge kinds and status strings are spelled identically in W3a-1, W3b, W4a and D1.
