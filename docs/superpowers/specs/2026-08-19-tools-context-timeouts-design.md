# 0.9 — apply_edits, context sizing & server-reported windows, louder timeouts, Windows advisory CI

**Date:** 2026-08-19
**Status:** Approved design, v3.3 (v3.2 + plan-time ruling on the bench cell, additive) (owner approved v3 for planning/execution 2026-08-19 12:37 CDT
with three amendments, folded in here: §4.2 DockerError scope covers `grep` too; §1.4 cap scope;
§1.3 extra-key policy). v2 addressed the owner's conditional-approval review; v3 folded in a
three-lens red-team of v2 against the code.
**Origin:** GitHub issues #20 (`apply_edits`), #21 (brief-size/context guidance), #22 (auto-detect the
loaded model's context length), #23 (louder tool timeouts), #24 (Windows: integration suite before
support). All four harness issues come from the SP3 build record
(`docs/superpowers/bench/2026-08-17-sp3-worker-scoreboard.md`, `-run-split.md`).
**Parent specs:** `2026-08-18-run-evidence-and-review-loop-design.md` (0.8: tool transforms, evidence
fields, `stuck`, `--verify`), `2026-08-17-sp2.5-harness-robustness-design.md` (nudges, stall
detector, `resolve_context_window`), `2026-08-15-review-response-design.md` (security posture).
Ships as **dirtywork 0.9.0**; worker image tag moves to **`:0.9`** (policy: the tag tracks the minor —
`docker/README.md`; Dockerfile unchanged, so `:0.9` is a rebuild of `:0.8`), `PINNED_DIGEST = None`
for 0.9.0, pinned in 0.9.1 as for every minor.

## Purpose

Make the common integration brief a handful of turns for a small model (`apply_edits`), make the
context window honest (server-reported, task-size warning, trimming visible), make a timed-out command
impossible to skate past (text, event flag, nudge, counters, scoreboard class), and learn what Windows
actually breaks (advisory CI leg + automated report) without claiming support. Everything is
stdlib-only, keeps `schema_version` 2 (every stdout / `run_end` / `run.json` / transcript change is
additive), and changes no security property: the container recipe, the export validator, the
guardrails, the file tools' `O_NOFOLLOW` write path and the no-host-git-on-worker-content rule are
untouched.

Conventions: "both backends" = host (`dirtywork/tools.py` via `HostSandbox`) and docker
(`DockerSandbox`, `dirtywork/sandbox/docker.py`); "every payload" = the stdout JSON from every
`_emit_result` call (normal end, `_fail_run`, `_fail_setup`), the `run_end` transcript event from every
writer (the runner's `finish` closure and the two manual writes in `__main__._fail_setup`/`_fail_run`),
and `run.json` (written at start by `_write_run_json_start`, updated by every `_update_run_json` call
including the two on the failure paths).

## 1. `apply_edits` (issue #20)

### 1.1 Signature and semantics

`apply_edits(path: str, edits: list[{old: str, new: str}]) -> str` — a batch of exact `old → new`
replacements applied to ONE file.

- Edits are applied **in order** on the running text: edit *i* sees the text after edits 1…*i−1*.
  A later edit may therefore depend on an earlier one, which is what a brief's numbered list means.
- Matching uses `str.count(old)` — the same non-overlapping count `edit_file` uses today — and each
  `old` must occur **exactly once** in the text as it stands at its turn; the replacement is
  `text.replace(old, new, 1)`.
- **All-or-nothing before the write:** if any edit fails validation or matching, nothing is written
  and the result is an `ERROR:` naming the first failure (§1.2). If every edit matches, the new text is
  written through the existing `_transform_file` write path (§1.6) and the result is
  `describe_change(path, old_text, new_text, verb=f"Applied {n} edit{'' if n == 1 else 's'} to")` —
  e.g. `Applied 3 edits to src/x.py: +4 -2 …` followed by the capped unified diff. `runs.py`'s result
  classifier keys only on `ERROR:`/`BLOCKED:` prefixes, so this renders as `ok` like every other
  success string.
- The **empty list is rejected** as a bad argument (wire `minItems: 1`; runtime validation raises
  `ToolValidationError`, so the registry reports `ERROR: bad arguments for apply_edits: …` with
  `failure="bad_args"` — the FailureTracker `bad_args` kind — exactly as a missing required parameter
  does today).
- Limits: `MAX_APPLY_EDITS = 100` (wire `maxItems: 100`, runtime check), input cap §1.4, output cap
  §1.5.

### 1.2 Error formats (exact; all 1-based; none writes anything)

| Condition | Result text |
|---|---|
| `edits` missing / not a list / empty / > 100 / an item not an object / `old`/`new` missing or not strings / input over the §1.4 cap | registry `bad_args` (§1.3/§1.4): `ERROR: bad arguments for apply_edits: <validation message>` |
| edit *i* has empty `old` | `ERROR: edit i of N: old text is empty; no edits applied` |
| edit *i*'s `old` occurs 0 times | `ERROR: edit i of N: old text occurs 0 times in <path>; it must occur exactly once (after edits 1..i-1 are applied); no edits applied` — the parenthetical is present only for i ≥ 2 (for the first edit: `…it must occur exactly once; no edits applied`) |
| edit *i*'s `old` occurs k>1 times | `ERROR: edit i of N: old text occurs k times in <path>; it must occur exactly once. Include more surrounding context to make it unique; no edits applied` |
| result over the write cap | `ERROR: result is <n> bytes, over the <MAX_WRITE_BYTES>-byte write limit; nothing was written` (§1.5, both backends, all four in-place tools) |
| file unreadable / not UTF-8 / oversized read / escapes the worktree / symlink / non-regular target | **each backend's existing `_transform_file`/`_read_raw`/path wording, unchanged and deliberately NOT unified here** (host: `ERROR: cannot read '<path>': …`, `ERROR: <path> is not valid UTF-8 text; apply_edits only works on text files`; docker: `ERROR: '<path>' exceeds 5242880 bytes; refusing to read`, `ERROR: '<path>' is not valid UTF-8; refusing to edit`, …). Wording parity for these is issue #41. |
| write failure | the existing `ERROR: cannot write '<path>': …` (host) / `_write_raw` error (docker); see §1.6 for what state the file is in |

*N* is the total number of edits and *i* the first failing one. Shape validation of every edit happens
in the registry before any matching, so shape errors never reach the `edit i of N` rows.

### 1.3 Wire schema and runtime validation (registry)

`ParamSpec` (`dirtywork/toolspec.py`) gains an optional `schema: dict | None`. When set, the registry
emits it as the parameter's JSON schema instead of the flat `{"type": …}` — with `ParamSpec.
description` merged in as `"description"` exactly as for flat params (`ToolRegistry.schemas()`). The
Anthropic adapter passes `parameters` through as `input_schema` unchanged, so both wire renderings get
it for free. `apply_edits.edits` declares:

```json
{"type": "array", "minItems": 1, "maxItems": 100,
 "items": {"type": "object",
           "properties": {"old": {"type": "string"}, "new": {"type": "string"}},
           "required": ["old", "new"], "additionalProperties": false},
 "description": "Replacements in order; each old must occur exactly once in the file as it stands after the previous edits."}
```

`_validate_args` validates a `schema`-bearing parameter **recursively** with a minimal built-in
validator (not a JSON-Schema library): `type` (array/object/string/integer/number/boolean),
`minItems`/`maxItems`, `items`, `properties`, `required`, `additionalProperties`. The nested policy
is **what the wire schema says**: `additionalProperties: false` is enforced at runtime too — an edit
object with an extra key is rejected (`edits[1] has unexpected property 'note'`), consistently with the
wire contract, rather than silently dropped. (The registry's *top-level* drop-unknown-keys policy is
unchanged: it exists because local models attach stray top-level parameters from other harnesses'
schemas; nested objects are authored per call and get the strict rule.) The validated value is
returned as-is; `_apply_edits_once` may rely on every item being exactly `{"old": str, "new": str}`.
Violations raise `ToolValidationError` with a path-qualified message (`edits[2].new must be string,
got int`; `edits must have at least 1 item(s)`; `edits must have at most 100 item(s)`; `edits[0] must
be an object`; `edits[1] is missing required property 'old'`; `edits[1] has unexpected property
'note'`) and surface as `bad_args`. Scalar coercion (`_coerce_numeric_string`) applies to nested
integer/number leaves as it does at the top level.

Fixture: the frozen wire fixture is regenerated. Since it now tracks HEAD rather than 0.5.1, the
file is renamed `tests/fixtures/tool_schemas.json` and the test `test_schemas_match_the_frozen_wire_
fixture`; `test_schemas_shape`'s exact name set and `FakeSandbox` in `tests/test_builtin_tools.py`
gain `apply_edits`.

### 1.4 Input-size accounting: recursive, and an explicit cap for `apply_edits`

`ToolRegistry.execute`'s `max_input_bytes` check (`toolspec.py:242-244`, today `sum(len(v) for
top-level str values)`) becomes a recursive walk over `call_args` counting the UTF-8 length of every
`str` **value** — top-level string parameters (so `path` IS counted, as it is today for any tool that
sets a cap) and strings nested inside lists and dicts (every `old` and `new`); dict **keys** and
non-string scalars are **not** counted. No existing built-in sets `max_input_bytes` (they are all
`None`, so the check is skipped for them — unchanged). `APPLY_EDITS_SPEC` sets `Caps(fs="write",
max_input_bytes=MAX_APPLY_EDITS_INPUT_BYTES, max_output_chars=TOOL_OUTPUT_CAP,
transcript="preview")` with `MAX_APPLY_EDITS_INPUT_BYTES = 2 * 1024 * 1024` (2 MiB of `path` +
`old` + `new` text; the file itself is capped at 5 MB). Over the cap → the
registry's existing `ERROR: bad arguments for apply_edits: input is <n> bytes, over the <cap>-byte
limit.` `edit_file` does not gain a cap (no behaviour change).

### 1.5 Output write cap enforced in the shared path (both backends)

`_transform_file` — host and docker — checks `len(new_text.encode("utf-8")) > MAX_WRITE_BYTES`
before writing and returns the §1.2 string `ERROR: result is <n> bytes, over the <MAX_WRITE_BYTES>-
byte write limit; nothing was written` (one helper in `tools.py`, imported by docker like the
transform factories) — identical for `edit_file`, `insert_before`, `insert_after`, `apply_edits` on
both backends. Today docker enforces its `_oversized` check inside `_write_raw` (`ERROR: content is
N bytes, over the M-byte write limit`) while the host transform path has none; after this change the
transform path's check fires first on both backends with the unified string, and `write_file` keeps
its own existing (backend-specific) oversized wording. `tests/test_tools_files.py` asserts the
substring `write limit` — preserved.

### 1.6 Write semantics — stated, not changed

"Nothing written" in §1.1/§1.2 covers every failure **before the write begins**: validation, matching,
read errors, the write cap. It does **not** cover a failure during the write itself: today's host path
opens the target `O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW` and writes, so an I/O error or a kill mid-write
can leave the file truncated — the same property `edit_file`/`insert_*`/`write_file` have today and
that this spec leaves unchanged. A temp-file-then-`os.replace` primitive was considered for 0.9 and
**deferred**: done naively it re-opens the final-component TOCTOU that `O_NOFOLLOW` closes, must
reproduce three errno-specific refusal messages, changes inode/hardlink/directory-permission
semantics, loses the exec bit under docker `mv`, and leaves crash temps inside the export
(`files_changed`, `diff.patch`). That is a security-adjacent change deserving its own spec; it is
filed as a follow-up issue with those requirements. `apply_edits`'s description (§1.7) says "atomic
before the write" plainly, and `docs/operating.md` states the caveat for all in-place tools.

### 1.7 Plumbing

- One transform factory `_apply_edits_once(path, edits)` in `tools.py`, used by both backends'
  `_transform_file` (docker imports it like `_replace_once`/`_insert_once`). `Sandbox` Protocol,
  `HostSandbox` (with the `_check_budget()` wrap its other mutating calls have), `DockerSandbox` gain
  `apply_edits(path, edits)`.
- `builtin_tools.py`: `APPLY_EDITS_SPEC` placed immediately after `EDIT_FILE_SPEC` in `BUILTIN_SPECS`
  (order is significant and documented there); caps per §1.4; description: "Apply several exact
  old→new replacements to one file in one call, in order: every `old` must occur exactly once (in the
  file as it stands after the edits before it); if any does not, nothing is written and the result
  names the first failure. Prefer this over a run of edit_file calls when a brief lists several edits
  to the same file." The module docstring ("The nine tools dirtywork ships") becomes ten.
- `runner._MUTATING_TOOLS` gains `apply_edits` (stall progress; canonical-args hashing is not
  applied to mutating tools, so the issue's "sorted pairs" note is moot — recorded here so nobody
  re-adds it).
- System prompt rule (`__main__.build_system_prompt`): "Use edit_file, apply_edits (several exact
  replacements in one file at once), insert_before, insert_after or write_file for ALL file
  changes. Never modify files via bash (…)".
- Docs — the real tool enumerations: `README.md:48` (Security & trust tool list) and `README.md:
  156-158` (How a run works, "nine tools" → ten + one sentence on apply_edits), `docs/security.md:21`,
  `docs/transcript-schema.md:69` (`tool` enum) and `:71` (the `<Verb> <path>: +A -D` result-format
  row gains `apply_edits` and the `Applied N edits to` verb), `dirtywork/builtin_tools.py:1` module
  docstring; `docs/machine-contract.md` gains a short **Tools** subsection listing the ten tools with
  one line each (it has none today); `docs/operating.md` gains one paragraph on when to use
  `apply_edits` and the §1.6 write caveat.

### 1.8 Tests

`tests/test_tools_files.py` + `tests/test_sandbox_host.py` (host) and `tests/test_docker_sandbox.py`
(docker, via the existing `FakeDocker` exec scripting): in-order application where edit 2 depends on
edit 1's output; rollback — a file with a matching edit 1 and a non-matching edit 2 is byte-identical
afterwards and the result is the §1.2 text with `i=2`; empty `old`; `k>1` matches; the result diff
text; **host/docker parity scoped to the matching/success/rollback results** (identical `Applied …`
text, identical `ERROR: edit i of N: …` text); output-cap parity (a transform whose output exceeds
`MAX_WRITE_BYTES` returns the identical §1.5 string on both backends, for `edit_file` as well as
`apply_edits`). `tests/test_toolspec.py`: nested-schema rendering incl. description merge, recursive
validation messages (empty list, >100, non-object item, non-string `new`, missing `old`, an
unexpected extra key inside an edit → `bad_args`), recursive `max_input_bytes` (`path` + nested
strings counted, keys not).
`tests/test_builtin_tools.py`: fixture regenerated/renamed and matched; name set; `FakeSandbox`.
`tests/test_transcript_schema.py`: the hand-maintained tool list (`:56-60`) gains `apply_edits`.

## 2. Context sizing and trimming (issue #21)

### 2.1 Task-size warning

In `main()`, **after `ctx` is constructed** (after `_workspace_new`/`_workspace_resume` returns —
one call site valid for both `run` and `resume`; `resume` has no `args.task`, its task is built by
`build_resume_task` inside `_workspace_resume`): `task_tokens = ceil(len(ctx.task) /
CHARS_PER_TOKEN)` (the runner's existing 4-chars-per-token heuristic, `runner.CHARS_PER_TOKEN`). If
`task_tokens > TASK_WARN_FRACTION * ctx.context_window` with `TASK_WARN_FRACTION = 0.20` (module
constant in `__main__`), print to stderr one line:

```
warning: the task text is ~{task_tokens} tokens, {pct}% of the {context_window}-token context window; long briefs thrash the prompt cache and risk context_exhausted — split the task or load the model with a larger context (docs/operating.md#sizing-the-context-window)
```

(`pct` rounded to an integer). Nothing is recorded for this; it is advice. Tests in
`tests/test_main.py`: fires for `run` with a long task and a small `--context-window`; silent under
20%; fires for `resume` whose prior task + tail crosses the line.

### 2.2 `trimmed_turns`

`trim_messages(messages, char_budget)` returns `(fits: bool, newly_trimmed: int)` — the number of
tool results it replaced with `TRIM_MARKER` **on this call** (results already holding the marker are
not counted). Its single call site (`runner.py` top of the turn loop) becomes `fits, newly_trimmed =
trim_messages(...)`; `trimmed_turns += 1` when `newly_trimmed > 0`; the run still ends
`context_exhausted` when `not fits` — and that final call counts if it trimmed something before
giving up (the definition is "turns on which trimming happened"). `trimmed_turns` rides
`RunResult.extra` → every payload, `run_end`, `run.json` (default `0` on the failure paths, §6).
`runs show` plain view lists it (`SHOW_FIELDS`); `--markdown` shows it in `## Result`; `bench
summarize` adds `trimmed_turns` (mean) to the per-model stats and pairs it in `--compare`.

Tests (`tests/test_runner.py`): the three existing `trim_messages` tests (`test_trim_messages`,
`test_trim_cannot_fit`, `test_trim_counts_tool_call_arguments`) are rewritten to unpack the tuple and
assert on `fits` (they would otherwise pass vacuously or fail on `is False`), plus assertions on
`newly_trimmed`; a new test: a run that trims on two turns then ends `context_exhausted` reports
`trimmed_turns == 3` when the final failed call also trimmed, `2` when it trimmed nothing.

### 2.3 Guidance

`docs/operating.md` gains `## Sizing the context window` (anchor `#sizing-the-context-window`): one
slot with the largest context the machine holds beats more slots with smaller ones; the SP3 numbers,
cited: at 65k, `qwen/qwen3-coder-next` ran 15–17 s/turn at ~3k prompt tok/s (the per-turn trim
invalidated the prompt cache) and exhausted its context twice on a 1,084-line brief; at 131k,
2.6–5 s/turn and ~13k prompt tok/s with no exhaustion; two 131k slots crashed LM Studio on a 128 GB
machine (wired 55.9 GB, free 1.2 GB just before) while one 131k slot peaks ~66 GB wired; rule of thumb
≤ ~450-line briefs per dispatch, biased toward whole-file writes; `lms load <model> -c 131072` and
the auto-detection of §3; the `trimmed_turns` field as the per-run signal. README *Requirements*
links to it.

## 3. Server-reported context window (issue #22)

### 3.1 Provider hook (optional at runtime)

The `Provider` protocol (`typing.Protocol`, not `runtime_checkable`; no `isinstance` checks exist)
documents an **optional** method `loaded_context_window(model: str) -> int | None`: the context
length the server currently has the model loaded with, or `None`. `resolve_context_window` obtains
it as `getattr(provider, "loaded_context_window", None)`; a provider without it (third-party
providers, the existing test doubles) is treated as `None`, and a method that raises any `Exception`
is treated as `None`. `AnthropicClient.loaded_context_window` is implemented explicitly and returns
`None`.

### 3.2 `OpenAICompatClient.loaded_context_window`

LM Studio's native endpoint (verified live 2026-08-19 against LM Studio at `localhost:1234`:
`GET /api/v0/models` → `{"data":[{"id":"qwen/qwen3-coder-next","state":"loaded",
"max_context_length":262144,"loaded_context_length":65536,…}]}`; `/v1/models` carries no context
field):

- URL: the **origin** of `self.base_url` (`urllib.parse.urlsplit` → `scheme://netloc`) +
  `/api/v0/models` — never a path under `/v1`: `http://localhost:1234/v1` → `http://localhost:1234/
  api/v0/models`; `http://h:1/prefix/v1` → `http://h:1/api/v0/models` (a proxy path prefix is
  dropped; a 404 there is a normal `None`).
- Request: GET, `LOADED_CONTEXT_PROBE_TIMEOUT = 2` seconds (independent of the client's chat
  timeout), through the same `http_json` transport (`method="GET"`, `payload=None`), so tests use
  the existing `RecordingTransport` and can assert URL + timeout.
- Accept iff the body is a dict with a `data` list containing an entry whose `id` **equals** `model`,
  whose `state` (when present) is `"loaded"`, and whose `loaded_context_length` is an `int` (not
  `bool`) `> 0` → return it. Anything else — connection error, timeout, non-2xx, non-JSON,
  missing/`None`/non-int/≤0 field, `state != "loaded"`, model absent — returns `None`.
- Ollama: not probed in 0.9 (its `/api/show` reports the model's architectural maximum, not the
  loaded `num_ctx`; unverified from here) — documented as a follow-up in `docs/operating.md`.

### 3.3 Precedence and source

`resolve_context_window(model, flag_value, env_value, provider=None) -> (tokens, source)`: flag
(`"flag"`) > env (`"env"`) > **server** (`f"provider:{name}:server"`) > static table
(`f"provider:{name}"`) > `DEFAULT_WINDOW` (`"default"`). The two provider sources are distinct strings
so a run record says which one was used. The existing "assuming 32768 tokens" warning still fires only
for `"default"`. The existing test `test_resolve_context_window_uses_the_real_openai_table` builds a
real `OpenAICompatClient(base_url="http://fake/v1")` — it must inject a stub transport (or a client
whose probe returns `None`) so it stays a pure unit test rather than attempting a real GET.

### 3.4 Recording the source

The source string — today discarded in `__main__._resolve_context_window` — is carried on
`RunContext.context_window_source: str` (a required dataclass field placed **before** the first
defaulted field, `branch_from`), written to `run_start` and `run.json` at start
(`context_window_source`), echoed on every payload and `run_end` (§6), and shown by `runs show`
(plain: `context_window: 65536 (provider:openai:server)`; Markdown header). On `resume` the window is
re-resolved exactly as today and its source recorded the same way.

### 3.5 Tests

`tests/test_runner.py`: precedence with a provider double implementing `loaded_context_window`
(server wins over table; `None` → table; raising → table; a double WITHOUT the method → table;
flag/env still win); the existing exact-tuple tests keep passing (sources `provider:fake` etc. are
unchanged for doubles without the hook). `tests/test_providers.py` / `provider_contract.py`:
`OpenAICompatClient.loaded_context_window` against `RecordingTransport` — exact URL for both base URLs
above, `timeout == 2`, `method == "GET"`, each accept/reject case in §3.2;
`AnthropicClient.loaded_context_window` returns `None`. `tests/test_main.py`: `context_window_source`
in `run.json`, `run_start`, stdout; resume records its own.

## 4. Louder timeouts (issue #23)

### 4.1 One canonical timeout result (both backends)

```
ERROR: command timed out after {timeout}s — it did not finish and its result is unknown. Re-run it with a larger timeout (up to 600) or split it into smaller commands; do not report it as passed.
```

No partial output is appended (host today appends the captured tail; docker cannot — parity wins, and
a partial tail is exactly what a small model misreads as a result). `TIMEOUT_PREFIX = "ERROR: command
timed out after "` and `is_timeout_result(text) -> bool` (prefix match) live in `tools.py` and are the
one predicate everything else uses.

### 4.2 Docker: real timeouts only

`docker_cli.DockerError` gains a keyword-only `timed_out: bool = False` (custom `__init__`; every
existing catcher constructs/reads it positionally, so this is backward compatible); `docker_cli.run`
sets it `True` on its expired-timeout path — the only place it raises for a timeout.
`DockerSandbox.bash` returns the §4.1 text **only** when `e.timed_out`; any other `DockerError` returns
`ERROR: bash failed: {message}` (the host's existing wording for a non-timeout failure), so an
ordinary docker failure is never rendered as a timeout. **The same distinction applies to
`DockerSandbox.grep`** (`docker.py:568-569` today renders every `DockerError` as `ERROR: grep timed
out after {timeout}s — narrow the pattern or path.`): `e.timed_out` → that existing grep-timeout
text (unchanged wording; `grep` timeouts are not `bash` timeouts and do not count toward `timeouts`
or the `timeout` nudge, which are about commands the worker ran); any other `DockerError` → `ERROR:
grep failed: {message}` (the wording the non-zero-exit branch already uses). These are the only two
`DockerSandbox` methods that convert a `DockerError` into a tool result; every other catch site
re-raises/propagates as `sandbox_error` and is unchanged. The existing test
`test_bash_timeout_returns_text_not_raise` (`tests/test_docker_sandbox.py`) must construct
`DockerError(..., timed_out=True)` and assert the **full** canonical text (its current substring
assertion would pass on either branch); a new regression test covers `grep` with a generic
`DockerError` → `ERROR: grep failed: …` and with `timed_out=True` → the grep-timeout text.

### 4.3 Transcript, nudge, counters

- `tool_result` events for the `bash` tool carry `timed_out: true` when `is_timeout_result(result)`;
  the field is **absent** otherwise (sparse, additive). Only worker tool calls count — the `--verify`
  command's `sandbox.bash` call is not a tool call, is not transcribed as a `tool_result`, and is
  excluded (its outcome is in `verify`).
- **Nudge kind `timeout`:** when a turn contained ≥1 timed-out bash result, exactly ONE `nudge` event
  `{"event":"nudge","kind":"timeout","turn":N}` is written and the text below is merged into the next
  user message with any other nudge text through the existing `_join_nudges`. Like the `stall` nudge,
  it is only emitted on turns that continue — a turn that ends the run (`finish` whose verify passes or
  is exhausted, `stuck`, an abort) writes no nudge, while a `finish` that continues into a verify
  fix round does (the nudge text rides in that feedback message); the `timeouts` counter is
  unaffected by that.

  ```
  A command timed out and did not finish; its result is unknown. Re-run it with a larger timeout (up to 600 seconds) or split it into smaller commands. Do not report it as passed.
  ```

  It does not count toward `FailureTracker` (a timeout result carries `failure=None`, so — as today —
  it takes the "successful execution" branch and **resets** the consecutive-failure streak; unchanged
  and intended: a timeout is not a model mistake). `RepeatTracker` already treats it as a failing
  result (first line is not `exit code: 0`), so identical repeated timeouts can end the run `stuck`;
  `ProgressTracker` ignores `ERROR` results — both unchanged.
- `timeouts` (count of timed-out worker bash calls) rides `RunResult.extra` → every payload,
  `run_end`, `run.json` (default `0` on failure paths, §6).
- `runs show`: `_tool_result_outcome` returns the new class string `"timed out"` (checked before the
  generic `ERROR` class) so both the plain timeline and the Markdown tool-call summary render
  `… [timed out]` through the existing `[{outcome}]` composition (no emoji, one rule); the plain view
  lists `timeouts` (`SHOW_FIELDS`) and `--markdown` shows it in `## Result`.
- `bench`: `NUDGE_KINDS` gains `"timeout"`; `_harness_failures` takes `timeouts` from the **payload**
  (`payload.get("timeouts", 0)` — the runner's count is the single source of truth; no re-derivation
  from events) as its own class and **excludes `timeout` from `empty_reply`** (`empty_reply` =
  non-stall, non-timeout nudges); `_harness_counts`/`_harness_cell` (the `--compare` harness
  cell) become the 4-tuple `n/s/m/t` with legend `harness: nudges/stalled/max_turns/timeouts`;
  the plain FAILURES column (`_failure_cell`) stays additive — it gains a `timeouts=N` token and
  its legend line gains `/timeout` (a literal `n/s/m/t` replacement there would delete the
  shipped `abort=`/`sandbox_error` tokens).

### 4.4 Docs

`docs/operating.md` (a short "The bash tool" paragraph: per-call `timeout` parameter, default 120 s,
maximum 600 s, the canonical timeout text and the nudge) and `docs/machine-contract.md` (in the new
Tools subsection); `docs/transcript-schema.md`: `tool_result.timed_out`, nudge kind `timeout`,
`run_end.timeouts`, `run.json.timeouts`; `tests/test_transcript_schema.py`'s hand-maintained lists
(`NUDGE_KINDS`, `RUN_END_FIELDS`) updated.

### 4.5 Tests

`tests/test_tools_bash.py`: the exact canonical text, no partial output. `tests/test_docker_sandbox.py`:
`DockerError(timed_out=True)` → canonical text; a generic `DockerError` → `ERROR: bash failed: …`
(and the rewritten existing test, §4.2); the `grep` pair from §4.2. `tests/test_runner.py`: `timed_out: true` on the event and
absent on a normal bash result; one `timeout` nudge per turn even with two timeouts in the turn;
merged text with another nudge; no nudge when the turn ends the run; `timeouts` count; a `--verify`
timeout not counted. `tests/test_runs.py`: `"timed out"` class and `[timed out]` in plain and
Markdown. `tests/test_bench.py`: `timeouts` from the payload; `empty_reply` unchanged by `timeout`
nudges; the 4-tuple cell and legend.

## 5. Windows advisory CI leg (issue #24)

- `.github/workflows/ci.yml`: a **separate** job `windows-unit` (`runs-on: windows-latest`, Python
  3.13, `continue-on-error: true`), not a matrix entry of the gated `test` job; `gate`'s `needs` are
  unchanged, so it can never block. It runs `python -m pytest --junitxml=junit-windows.xml` (the
  `addopts` deselection works unchanged — pytest reads it, not the shell), then `python
  tools/junit_summary.py junit-windows.xml` (committed stdlib script: per-file pass/fail/error/skip
  table to stdout and, when `GITHUB_STEP_SUMMARY` is set, appended there), and uploads the XML with
  `actions/upload-artifact` **SHA-pinned to the same commit and `# v7` comment `publish.yml` already
  uses** (every action in `ci.yml` is SHA-pinned with a version comment).
- `tests/test_budget.py`: `import sys`; the collection-time crash (`os.getuid()` inside
  `@pytest.mark.skipif`) becomes `getattr(os, "getuid", lambda: -1)() == 0 or sys.platform ==
  "win32"` (the `chmod 000` semantics do not hold on Windows — skipped there with that reason).
- Docs: README platform row and `docs/security.md` callout add "the unit suite also runs on
  `windows-latest` in CI as an advisory (allowed-to-fail) job; Windows remains unsupported until an
  integration suite passes". After the first run on the branch, the per-file table is posted on #24
  (a manual step for the PR author; the artifact and step summary make it reproducible).
- No production code is changed for Windows in 0.9 (`os.O_NOFOLLOW`, process-group kills, …) — the
  spike's deliverable is the measured failure table.

## 6. Cross-cutting

- **Contract:** every payload gains `trimmed_turns` (int, default 0), `timeouts` (int, default 0),
  `context_window_source` (string — always known by the time any payload exists: both failure paths
  run against an already-built `RunContext`; a context-window preflight failure exits 2 with no
  payload, as today); `_emit_result` seeds all three before `payload.update(extra)`; the two manual
  `run_end` writes in `__main__` (`_fail_setup`, `_fail_run`) carry `trimmed_turns: 0, timeouts: 0,
  context_window_source`, and **their `_update_run_json` calls carry the same three** so the plain
  `runs show` (run.json-only) never shows `-` for them; `run.json` gets `context_window_source` at
  start and `trimmed_turns`/`timeouts` at end on the normal path. Transcript: `tool_result.timed_out`
  (sparse), nudge kind `timeout`, `run_start.context_window_source`. Tool list: ten tools; wire
  fixture regenerated and renamed. `schema_version` stays 2.
- **Image/version:** `DEFAULT_IMAGE` → `:0.9`, CI docker-live tag `:0.9`, `PINNED_DIGEST = None`
  (comment updated; pin in 0.9.1), `tests/test_docker_args.py`/`test_docker_image.py`, every `:0.8`
  doc mention (README, `docs/operating.md`, `docs/security.md`, `docs/machine-contract.md`,
  `docker/README.md`); `pyproject.toml` and `dirtywork/__init__.py` → `0.9.0` (both are `0.8.1`).
- **Tests:** TDD; new tests in the existing modules; the full unit suite green on 3.9 after every
  task.
- **Docs touched:** README, `docs/operating.md`, `docs/machine-contract.md`, `docs/security.md`,
  `docs/transcript-schema.md`, `docker/README.md`.
- **Follow-up issues to file with 0.9:** atomic write primitive for the in-place tools (§1.6
  requirements); Ollama loaded-context probe (§3.2).
- **Deviation from the owner's review, stated:** "true atomicity requires a temp-file/replace
  strategy" — agreed in principle, deferred to its own spec (§1.6) because the red-team showed the
  naive version regresses security-adjacent behaviour; 0.9 instead states the write semantics
  precisely and keeps the hardened `O_NOFOLLOW` path unchanged.
