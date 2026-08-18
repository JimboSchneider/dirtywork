# SP3 plan — pre-flight conflict scan

Plan: `docs/superpowers/plans/2026-08-16-sp3-extensibility.md` (7797 lines, 16 tasks, revision 2).
Shipped-code truth: `d9533c8` (v0.5.1). Measured baseline **585 passed, 16 deselected** (13 `docker` + 3 `live`).
Method: sequential read with a per-task interface ledger; then `grep -F` of every quoted before-text;
then a scratch copy of the branch with Tasks 1–3 materialised verbatim from the plan and the suite run.

> Note: while this scan ran, the worktree HEAD advanced past `d9533c8`+docs to include Task 1 and Task 2
> commits (`06f5c11`). All drift checks below were re-run against `d9533c8` content, which is unaffected.

---

## 1. Findings

### BLOCKER

1. **T5 — empty `--base-url` silently becomes LM Studio.** `openai_compat.py` line ~2525:
   `self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")`. Shipped `test_empty_base_url_raises_llmerror`
   (`tests/test_llm.py:123`) passes `base_url=""` and expects `LLMError`; the new client instead hits
   `http://localhost:1234/v1`, which is **live on this machine** (verified) → `list_models()` succeeds → test fails.
   *Fix:* `self.base_url = (DEFAULT_BASE_URL if base_url is None else base_url).rstrip("/")`.
2. **T6 — `test_length_cutoff_without_tool_calls_is_not_completed` is missed.** `tests/test_runner.py:774-785`
   builds a raw OpenAI dict with `finish_reason: "length"`; after edit 5e/5f the runner does `resp.finish_reason`
   → `AttributeError`. Not in the 12-name delete list (plan 2856) nor the rewrite block (plan 2861-2994).
   *Fix:* add it to the rewrite block as `_resp(content="I will now", finish_reason="length", usage={...})`.
3. **T6 — `test_trim_counts_tool_call_arguments` is missed.** `tests/test_runner.py:156-164` passes wire dicts
   in `tool_calls`; edit 5c (plan 3112-3144) drops the old `if not isinstance(tc, dict): continue` guard and calls
   `tc.raw_arguments` → `AttributeError`. *Fix:* rebuild the test on `ToolCall(..., raw_arguments="a"*1000)`.

### DEFECT

4. **Test-count arithmetic is off by one from T3 onward.** T3 Step 5 says 20 but `test_builtin_tools.py` has **21**
   tests; Step 11 says 621, measured **622**. T5 Step 7 says 30, actual **31** (7 contract + 24 explicit).
   T14 Step 5 says 17, actual **20** (5 from T13 + 15 new); T15's 21 inherits it. *Fix:* restate every count.
5. **T6 Step 2 says "replace these six tests" but the block defines ten** (plan 2861-2994) and never names which
   shipped test each one replaces.
6. **T6 Step 3 renames two context-window tests without saying to delete the originals**: shipped
   `test_runner_context_window_defaults_from_table` / `..._zero_is_not_replaced_by_table` (test_runner.py:967, 1041)
   vs the plan's `..._from_the_provider` / `..._by_the_provider`. Appending leaves two failing tests
   (`CONTEXT_WINDOWS` is no longer importable from `dirtywork.runner`).
7. **T6 Step 12 is a placeholder** — "660 passed … adjust the number to what the suite actually reports" (plan 3713)
   — contradicting the plan's own "Never leave placeholders in a plan step" constraint.
8. **T6 Step 8 contradicts itself**: `provider_doubles.py`'s block puts `DEFAULT_MODEL` at the bottom, then the prose
   says move it above `DictProvider` (plan 3591-3597). Harmless either way, but pick one for a verbatim transcriber.
9. **T5→T6 leaves the CLI runtime-broken between commits**: after T5 `__main__` gets a `ChatResponse`-returning
   client while `Runner.run` still does `resp["choices"][0]`. Only `Runner.run`-patching doubles hide it. Say so.
10. **DRY: `_uid_gid()` is duplicated verbatim** in `runs.py` (T10, plan ~5389) and `bench.py` (T14, plan ~6843);
    `class _FakeCaptured` is duplicated in `tests/test_runs.py` (T9) and `tests/test_bench.py` (T14).
11. **Dead parameter**: `_clean_worktree_and_branch(data, slug, force, log)` never uses `slug` (T11, plan ~5849).
12. **Part A's self-review / type-consistency tables do not exist**, yet plan 7727 and 7764 cite them
    ("Tasks 1-8 are covered by part A's table"). The A↔B seam has no coverage matrix for Tasks 1–8.
13. **`File Structure` (plan line 91) claims README is modified by Tasks "9–15"** — no step in 9–15 touches it.

### NOTE

14. `ToolRegistry.names()` is implemented in T1 Step 3 but absent from T1's **Produces** line (plan 123); T3 consumes it.
15. T3 Step 12 `git add`s `tests/test_toolspec.py`, which T3 does not modify (no-op).
16. Plan says "585 unit tests plus 12 `-m docker` live tests"; actual marker split is 13 `docker` + 3 `live`.
17. `_preflight_llm`'s anthropic hint is an f-string with no placeholders (plan ~2896) — F541 lint noise.
18. `run_once` does not catch `SystemExit` (argparse raises it inside `main()`), and `run_one_bench_case` catches only
    `Exception` — an argparse refusal aborts the whole sweep instead of writing a `bench_error` row.
19. `tests/test_runs.py` imports `os` in T9 but first uses it in T11.
20. T16 Step 4(a)/(c)/(f) give "after" text with no quoted "before"; the shipped call is one line (`__main__.py:549`).
21. `usage_nan_negative.json` (both providers) contains bare `NaN` — accepted by `json.loads`, rejected by strict JSON tooling.
22. `AnthropicClient.list_models` discloses that the `/v1/models` envelope is unverified — keep the comment, verify before use.

---

## 2. Pair table

53 unique task pairs sharing a file or an interface.

### 2a. `dirtywork/__main__.py` edit ordering (36 pairs)

Regions: **T3** = `from .tools import ToolExecutor`, `_execute` executor line, `Runner(` arg line ·
**T6** = llm import, `RunContext`, `_preflight_llm`, `_resolve_context_window` (+ its `main()` call), both
`RunContext(...)` ctors, `_write_run_json_start` dict, `_emit_result` sig/payload/3 call sites, `run_info`,
`_add_run_flags` `--base-url`, `_load_resume_target` · **T9** = new `_add_runs_parsers`, `_parse_args` return,
`main()` head · **T10/11/12** = append to `_add_runs_parsers` · **T14** = stdlib import block, `run_once`,
`_add_bench_parsers`, `_parse_args` return, `main()` head · **T15** = append to `_add_bench_parsers` ·
**T16** = `build_system_prompt`, `_add_run_flags`, `_resolve_allow_commit`, `_load_resume_target`,
`_write_run_json_start`, `_execute` prompt call, `main()` after `preflight_repo`.

| Tasks | produces vs consumes | finding |
|---|---|---|
| 3 ↔ 6 | T3 rewrites `Runner(client, executor, transcript,` → `client, registry, sandbox, transcript,`; T6 renames only `Runner.__init__`'s 1st param | ✓ T6 never re-quotes the call site; `client` is a Provider by then |
| 3 ↔ 9 | disjoint (`_execute` vs `_parse_args`/`main()` head) | ✓ |
| 3 ↔ 10 | disjoint | ✓ |
| 3 ↔ 11 | disjoint | ✓ |
| 3 ↔ 12 | disjoint | ✓ |
| 3 ↔ 14 | T3 edits an import line inside the `from .` block; T14 rewrites the stdlib block above it | ✓ different blocks |
| 3 ↔ 15 | disjoint | ✓ |
| 3 ↔ 16 | T3 edits `_execute`'s executor/Runner lines; T16 edits `_execute`'s `build_system_prompt` line (549) | ✓ different lines |
| 6 ↔ 9 | T6 edits inside `main()`'s `try:`; T9's before-text is `main()`'s first 3 lines | ✓ T9 quote survives T6 |
| 6 ↔ 10 | disjoint | ✓ |
| 6 ↔ 11 | disjoint | ✓ |
| 6 ↔ 12 | disjoint | ✓ |
| 6 ↔ 14 | `main()` head (T14) vs `main()` body (T6); import blocks differ | ✓ |
| 6 ↔ 15 | disjoint | ✓ |
| 6 ↔ 16 | both touch `_add_run_flags`, `_load_resume_target`, `_write_run_json_start` | ✓ T16 is purely additive and quotes no before-text there; must run after T6 (it does) |
| 9 ↔ 10 | T9 produces `_add_runs_parsers`/`dispatch`; T10 appends `export_p` + `"export": cmd_export` | ✓ |
| 9 ↔ 11 | same; T11 also inserts validation at the top of `dispatch` | ✓ |
| 9 ↔ 12 | same; T12 appends `verdict_p` + handler | ✓ |
| 9 ↔ 14 | T14's `_parse_args` before-text is `_add_runs_parsers(sub)` + `return parser.parse_args(argv)` | ✓ requires T9 first — correct order |
| 9 ↔ 15 | `runs.format_table` produced by T9, consumed by T15 `cmd_summarize` | ✓ signature `(columns, rows)` matches |
| 9 ↔ 16 | disjoint (`main()` head vs `main()` try-body) | ✓ |
| 10 ↔ 11 | both append to `_add_runs_parsers` and `dispatch`'s handler dict | ✓ additive |
| 10 ↔ 12 | same | ✓ |
| 10 ↔ 14 | `_uid_gid()` defined in both `runs.py` and `bench.py` | ✗ DRY duplicate (D10) — identical bodies |
| 10 ↔ 15 | disjoint | ✓ |
| 10 ↔ 16 | disjoint | ✓ |
| 11 ↔ 12 | both append to `_add_runs_parsers`/`dispatch` | ✓ |
| 11 ↔ 14 | disjoint | ✓ |
| 11 ↔ 15 | disjoint | ✓ |
| 11 ↔ 16 | disjoint | ✓ |
| 12 ↔ 14 | disjoint | ✓ |
| 12 ↔ 15 | T12 writes `verdict`/`review_seconds` to run.json; T15 `_verdict_for` re-reads them | ✓ key names agree |
| 12 ↔ 16 | both append keys to run.json (`verdict…` vs `allow_commit`) | ✓ |
| 14 ↔ 15 | T15 appends `bench_sub`/`summarize_p` to `_add_bench_parsers`; replaces `bench.dispatch` | ✓ ordered |
| 14 ↔ 16 | disjoint | ✓ |
| 15 ↔ 16 | disjoint | ✓ |

### 2b. Other shared files / interfaces (17 pairs)

| Tasks | produces vs consumes | finding |
|---|---|---|
| 1 ↔ 2 | `toolspec.py` + `test_toolspec.py`; T2 inserts module pieces after `class ToolValidationError` and 3 methods after `schemas` | ✓ materialised and run: **27 pass** |
| 1 ↔ 3 | `ToolSpec/ParamSpec/Caps/ToolRegistry` → `builtin_tools.py`; `registry.names()`/`.spec()` | ✓ (names() undocumented in T1 Produces — N14) |
| 1 ↔ 6 | `registry.spec(name).terminal`, `registry.schemas()` in the refactored runner | ✓ |
| 2 ↔ 3 | `execute(name,args,*,sandbox,deadline) -> ToolResult`, `canonical_args`, `transcript_preview` | ✓ all three consumed with the declared signatures |
| 2 ↔ 6 | same three, plus `ToolResult.failure` → `failures.record(...)` (`"unknown_tool"`/`"bad_args"` ∈ `FAILURE_KINDS`) | ✓ |
| 2 ↔ 8 | `Caps.transcript` / `TRANSCRIPT_PREVIEW_CHARS` documented in `transcript-schema.md` (`tool_result.result` row) | ✓ |
| 3 ↔ 8 | `default_registry` imported by `tests/test_transcript_schema.py`; `finish` documented as an ordinary tool | ✓ |
| 4 ↔ 5 | `ChatResponse`, `ToolCall(raw_arguments=)`, `assistant_message`, `tool_message` | ✓ field names/defaults agree |
| 4 ↔ 6 | `DEFAULT_BASE_URLS`, `PROVIDER_NAMES`, `get_provider`, `assistant_message`, `tool_message` | ✓ `get_provider(name, base_url)` called positionally, matches |
| 4 ↔ 7 | `ToolCall(id="",…)` unaddressable convention; `raw_arguments = json.dumps(input)` | ✓ |
| 5 ↔ 6 | `MalformedResponse` caught by the runner, plain `LLMError` escapes; `CONTEXT_WINDOWS` moved to the adapter | ✓ except D6 (old test names still import `CONTEXT_WINDOWS` from `runner`) |
| 5 ↔ 7 | `tests/provider_contract.py` `ProviderContract`/`RecordingTransport` reused | ✓ all 7 contract cases satisfiable by the Anthropic fixtures (stop_reason mapping checked) |
| 5 ↔ 8 | `parse_chat_response` reached via `provider_doubles.DictProvider.chat` | ✓ |
| 6 ↔ 7 | `tests/test_providers.py` appended by both (1 test then 4) → 14 total | ✓ count consistent |
| 6 ↔ 8 | `provider_doubles.{DictProvider,patch_provider,text_body,tool_call_body}` consumed by `test_transcript_schema.py`; `provider` on run_start/run.json/stdout | ✓ |
| 8 ↔ 16 | T16 appends the `allow_commit` line to `docs/transcript-schema.md`'s `run.json` section | ✓ section exists after T8 |
| 13 ↔ 14 | `bench/repos/<task>/bench.json` (`task`, `acceptance.command`, `acceptance.hashes`) → `_bench_json`, `_run_acceptance`, `_stage_repo` | ✓ every `command` names `/acceptance/…`; hash map keys are `acceptance/<rel>` and `_hash_check_argv` prefixes `/work/` |

---

## 3. Self-consistency table

| Task | checked | finding |
|---|---|---|
| 1 | tests vs code, imports, commit list, counts | ✓ materialised the plan's `toolspec.py` + tests and ran them: 6 pass; "591 = 585+6" correct |
| 2 | 21 appended tests vs `execute`/`canonical_args`/`transcript_preview`; exact asserted strings | ✓ all 27 pass as written (`"byte limit"`, `"must be string"`, `"truncated at 10 chars"`, timeout clamps, `"paths:a.txt:0:400"` all verified); "612 = 585+27" correct |
| 3 | frozen-schema fixture, 21 tests, 3 patch scripts, commit list | ✗ D4: Step 5 says 20 passed (file has 21); Step 11 says 621, measured **622**. Everything else ✓ — `default_registry().schemas()` equals shipped `TOOL_SCHEMAS` **byte for byte** (diffed), all before-texts exist, patch scripts print exactly `rewrote 56 destructuring site(s), 54 Runner(...) call site(s)`, `git add` list complete (N15 no-op) |
| 4 | 9 tests vs dataclasses/`get_provider`; `pyproject.toml` before-text | ✓ `packages = ["dirtywork", "dirtywork.sandbox"]` exists verbatim; 9 tests match the code; counts internally consistent |
| 5 | 8 fixtures, contract suite, 24 explicit tests, `llm.py` rewrite, 4 test_llm patch pairs | ✗ **BLOCKER 1** (empty base_url). ✗ D4: Step 7 says 30, file has **31**. Otherwise ✓ — all 4 patch pairs occur exactly once; `_underlying_socket`/`settimeout` `OSError` guard/`MAX_RESPONSE_BYTES`/`e.read(500)` preserved verbatim; module `__getattr__` alias works with `from … import` |
| 6 | 13 deletions, 10 rewrites, 8 runner edits, 10 `__main__` edits, 2 patch scripts, 5 new CLI tests | ✗ **BLOCKERS 2 & 3**; ✗ D5, D6, D7, D8. Verified ✓: all 12 `__main__`/`test_main` before-texts exist once; the 9a script counts are exactly **10 preflight / 16 installation** sites; **14** in-test fakes match the plan's list; `run_start["base_url"]` exists; `RunContext` insertion point is legal (no defaults before it) |
| 7 | 8 fixtures, 12 explicit + 7 contract tests, adapter code | ✓ 19 is correct; stop-reason map satisfies all 3 contract cases; `_to_anthropic_messages` merges consecutive tool results into one user turn as the contract's `_tool_result_entries` expects; test_providers 14 correct |
| 8 | 7 tests, doc token coverage, README anchor | ✓ every `EVENT_NAMES`/`STATUSES`/`NUDGE_KINDS`/`RUN_END_FIELDS`/tool token appears backticked in the doc; run.json key list matches `_write_run_json_start` + `_update_run_json` exactly; stdout key list matches `_emit_result`'s success call; README anchor `**Transcript events** (JSONL, one per line):` exists |
| 9 | 12 tests, `runs.py`, 3 `__main__` edits | ✓ 12 correct; `_parse_args`/`main()` before-texts exist verbatim; `rundir.read_run_json` raises `ValueError`/`OSError` (both caught); `docker_cli.T_QUERY = 10` exists; fake `run(argv, timeout=None)` matches `run(argv, *, timeout)`; N19 unused `os` |
| 10 | 7 tests, `cmd_export`, parser block | ✓ 19 correct; `export_run(cfg, *, slug, base_commit, worktree, run_dir, objects_dir, image_ref, uid, gid, repo_label, …)` matches exactly; `resolve_image(image, *, pinned_digest=)` matches; `DockerConfig(image, max_patch_mb, keep_volume)` valid; `RunArtifacts(export_status="ok")` constructible (all fields defaulted) |
| 11 | 13 tests, 6 helpers, parser + dispatch validation | ✓ 32 correct; `stash_dir_for`/`find_stashes`/`pid_alive` exist with the used signatures; fake docker `argv[:1]`/`argv[:2]` prefixes match every emitted argv; ✗ D11 dead `slug` param |
| 12 | 3 tests, `cmd_verdict`, parser block | ✓ 35 correct; reads `ended` (the key `_update_run_json` actually writes); `datetime.fromisoformat` handles the `+00:00` form on 3.9 |
| 13 | 5 tests, 3 fixtures, 4 recorded hashes | ✓ **all four sha256 hashes recomputed and matched exactly**; all three fixtures verified failing in their unsolved state by inspection (`range(low,high)`, no `--loud`, missing trailing newline vs `cmp`); file counts ≤ 5 |
| 14 | 15 tests, `bench.py`, 5 `__main__` edits | ✗ D4: Step 5 says 17, actual **20**. ✗ N18 `SystemExit`. Otherwise ✓ — `docker_args.PATH_ENV`, `T_LIFECYCLE`, `T_EXPORT_STEP` all exist; `_ABORT_RE` correctly yields `bad_args` and `mixed`; `parse_model_spec` verified against all 4 test cases; argv assertions match `_acceptance_base_argv` exactly |
| 15 | 4 tests, `cmd_summarize`, sub-subparser | ✓ arithmetic verified: m1 rates 100%/50%/50% and `median([30,90]) = 60` → `f"{60.0:g}"` = `"60"`; `"2/1/0/0"` matches `NUDGE_KINDS` order; `from .runs import format_table` introduces no import cycle |
| 16 | 5 + 1 tests, prompt switch, 6 `__main__` edits, 3 doc edits | ✓ shipped rule line matches `NO_COMMIT_RULE` byte for byte; **ran** `check_bash_command` for all 6 cases in the new guardrail test — passes as written; `_first_run`/`_read_only_run_json`/`_host_repo`/`_install_host_harness` all exist; both README anchors exist; `args.sandbox` is set for `resume` before `_resolve_allow_commit` runs; ✗ N20 no before-text |

---

## 4. Shipped-code drift

Every place the plan quotes shipped code as before-text was checked with `grep -F` (exact-string count == 1)
against `d9533c8`. **Drift misses: 0.**

| Plan lines | File | Quoted before-text (first line) | Result |
|---|---|---|---|
| 1462 | `dirtywork/tools.py` | `\ndef _param(props: dict, required: list) -> dict:` | ✓ 1 |
| 1470-1486 | `dirtywork/tools.py` | `import inspect` / `import posixpath` / `import time` unused after the trailing-block removal | ✓ all 3 present and provably unused afterwards |
| 1518-1521 | `dirtywork/runner.py` | `    def __init__(self, client, executor, transcript, model,` | ✓ 1 (line 274) |
| 1526-1530 | `dirtywork/runner.py` | `        self.client = client` (3-line block) | ✓ 1 (281-283) |
| 1540-1544 | `dirtywork/runner.py` | `    def run(self, …) -> RunResult:` + `from .tools import TOOL_SCHEMAS` | ✓ 1 (294-297) |
| 1553-1556 | `dirtywork/runner.py` | `        deadline = start + self.timeout` + `self.executor.deadline = deadline` | ✓ 1 (310-311) |
| 1564 | `dirtywork/runner.py` | `                    resp = self.client.chat(self.model, messages, tools=TOOL_SCHEMAS,` | ✓ 1 (355) |
| 1573-1579 | `dirtywork/runner.py` | `                        if name == FINISH_TOOL:` (7-line block) | ✓ 1 (447-453) |
| 1603-1614 | `dirtywork/runner.py` | `                    except KeyError:` (12-line block) | ✓ 1 (469-480) |
| 1641-1646 | `dirtywork/__main__.py` | `from .tools import ToolExecutor`; `        executor = ToolExecutor(sandbox, transcript=transcript)`; `            client, executor, transcript, model=args.model,` | ✓ 1 each (22, 518, 536) |
| 1667-1677 | `tests/test_runner.py` | `from dirtywork.tools import ToolExecutor\n`; the 3-line `parts` fixture tail | ✓ 1 each (30, 64-66) |
| 1681-1686 | `tests/test_runner.py` | regex counts 56 / 54 | ✓ measured exactly 56 and 54 (55 `Runner(` total; the gap is `test_budget_exceeded_from_executor_ends_run`) |
| 1716 | `tests/test_runner.py` | `test_canonical_args_normalizes_effective_arguments` is the last test in the file | ✓ (line 1077 of 1086) |
| 2200 | `pyproject.toml` | `packages = ["dirtywork", "dirtywork.sandbox"]` | ✓ 1 (line 32) |
| 2568-2578 | `tests/test_llm.py` | 3 × `assert resp["choices"][0]["message"]["content"] == "hi"` blocks + `from dirtywork.llm import LLMError, LLMTimeout, LMStudioClient` | ✓ 1 each |
| 3154 / 3157-3159 | `dirtywork/__main__.py` | `from .llm import LLMError, LMStudioClient` | ✓ 1 (14) |
| 3164-3166 | `dirtywork/__main__.py` | `    sandbox_mode: str` | ✓ 1 |
| 3196-3201 | `dirtywork/__main__.py` | `def _resolve_context_window(args) -> int:` (4-line block) | ✓ 1 (118-121) |
| 3210 | `dirtywork/__main__.py` | `context_window = _resolve_context_window(args)` | ✓ 1 (643) |
| 3212 | `dirtywork/__main__.py` | `sandbox_mode=args.sandbox, image_ref=image_ref,` and `base_commit=prior["base_commit"], task=task, sandbox_mode=args.sandbox,` | ✓ 1 each |
| 3214-3217 | `dirtywork/__main__.py` | `        "model": args.model,` | ✓ 1 |
| 3221 | `dirtywork/__main__.py` | `"sandbox": sandbox_info, "provider": "openai",` | ✓ 1 (542) |
| 3225 | `dirtywork/__main__.py` | `    p.add_argument("--base-url", default="http://localhost:1234/v1")` | ✓ 1 |
| 3238-3241 | `dirtywork/__main__.py` | `    args.sandbox = prior["sandbox"]` + `if args.model is None:` + `args.model = prior["model"]` | ✓ 1 |
| 3350 / 3358 / 3362 | `tests/test_main.py` | preflight site ×10, installation site ×16, `from dirtywork.sandbox.docker_cli import DockerError\n` ×1 | ✓ exactly 10 / 16 / 1 |
| 3382 | `tests/test_main.py` | the 14 in-test fake clients | ✓ exactly 14 classes with the quoted `chat(self, model, messages, tools, temperature=None, max_tokens=4096, timeout=None)` shape |
| 3479-3489 | `tests/test_live.py` | `msg = resp["choices"][0]["message"]` 4-line block; `from dirtywork.llm import LLMError, LMStudioClient` | ✓ 1 each (9, 41-44) |
| 3492 | `tests/test_docker_live.py` | `monkeypatch.setattr(m, "LMStudioClient", lambda base_url=None: client)` + one fake client | ✓ 1 each (72, 16) |
| 4435 | `README.md` | `**Transcript events** (JSONL, one per line):` | ✓ 1 (line 445) |
| 5187-5190 | `dirtywork/__main__.py` | `def main(argv: list \| None = None) -> int:` + `args = _parse_args(argv)` + `try:` | ✓ 1 |
| 5535-5539 | `dirtywork/__main__.py` | `resume_p = sub.add_parser("resume", …)` … `return parser.parse_args(argv)` | ✓ 1 |
| 7217 (T16 §16) | `dirtywork/guardrails.py` | "no rule blocks `git commit`" | ✓ executed `check_bash_command` for all 6 cases: commits `None`, push `BLOCKED:` — in both `sandboxed` modes |
| 7241 (T16) | `dirtywork/__main__.py` | `- Do not run git commit or git branch commands; leave all changes uncommitted for review.` | ✓ matches `NO_COMMIT_RULE` byte for byte (line 55) |
| 7259-7262 | `README.md` | `    [--sandbox docker\|none]           # default: docker` and the `` `--stall-turns N` `` bullet | ✓ lines 360, 377 |

---

## 5. Rubric conflicts

| Rubric | Finding |
|---|---|
| Tests that assert nothing | **None.** AST-scanned every `def test_` in all 119 python blocks: every one contains an `assert` or `pytest.raises`. |
| Verbatim duplicated logic (DRY) | **`_uid_gid()`** identical in `runs.py` (T10) and `bench.py` (T14) — the plan's own type-consistency table records it as dual-owned. **`class _FakeCaptured`** duplicated in `tests/test_runs.py` (T9) and `tests/test_bench.py` (T14). `_sanitize_usage` is near-identical in `openai_compat.py` and `anthropic.py` but the wire keys genuinely differ — acceptable. `_acceptance_base_argv` correctly factors the shared docker argv rather than repeating it (good). |
| Dead code | `_clean_worktree_and_branch(data, slug, force, log)` never uses `slug` (T11). `builtin_tools._finish` is deliberately never executed but is documented as such and keeps the spec table complete — acceptable. `ToolSpec.caps.fs`/`network` are declared but never enforced; the docstring says so explicitly — acceptable, flag only if the reviewer wants enforcement. |
| Python 3.9 violations | **None found.** No `match`/`case` statements (the only hit is a variable named `match`). No `dataclass(slots=)`. No `removeprefix`/`removesuffix`. Every new module opens with `from __future__ import annotations` (19 occurrences), so `int \| None` / `str \| None` / `dict \| None` annotations are strings at runtime. `typing.Protocol` and `typing.Literal` are 3.9-available. `dict.get` / `str.partition` / `str.rpartition` / `posixpath.normpath` / `statistics.median` / `datetime.fromisoformat("…+00:00")` all fine on 3.9. |
| Non-stdlib imports | **None.** Every import across all code blocks is stdlib, `pytest` (existing dev dependency), or `dirtywork`/`tests`. |
| Other | `usage_nan_negative.json` uses bare `NaN` (non-RFC JSON, accepted by `json.loads`). `run_once` swallowing stdout via `redirect_stdout` will also swallow a `SystemExit` traceback path (N18). T6's `os.getuid` monkeypatch in T11's ownership test patches the real `os` module globally for the duration of that test — restored by monkeypatch, but worth knowing. |
