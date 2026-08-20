# 0.10 (v1 RC) — large writes recoverable, atomic file writes, Ollama first-class, backlog zero

**Date:** 2026-08-20
**Status:** Draft v1 — design decisions approved in chat (2026-08-20 10:35 CDT: `--max-tokens`
default 8192; new `--provider ollama`; atomic-write race delta accepted and stated); awaiting
red-team + owner approval of the written spec.
**Origin:** milestone **0.10.0 — v1 release candidate**: issues #36 (large `write_file` truncates at
the per-turn output cap → empty replies → `model_error`), #43 (atomic write primitive), #47 (verify
Ollama end-to-end), #40/#41/#42 (known-defect backlog). #48 (the v1 soak) runs after this ships and
is not spec'd here. Survey reports (facts, path:line, live Ollama probes) in the session scratchpad;
key facts restated inline.
**Parent specs:** `2026-08-19-tools-context-timeouts-design.md` (v3.3),
`2026-08-18-run-evidence-and-review-loop-design.md`, `2026-08-15-review-response-design.md`
(security posture).
Ships as **dirtywork 0.10.0**; worker image tag **`:0.10`** (Dockerfile unchanged;
`PINNED_DIGEST = None` → pinned in 0.10.1). Everything stdlib-only, Python 3.9 floor,
`schema_version` 2, additive.

## Purpose

Close the last core-job holes before v1: a file larger than one turn's output becomes a recoverable
two-step instead of a silent `model_error` (#36); an interrupted write can no longer leave a
truncated file (#43); Ollama — the largest local-server audience — becomes a verified first-class
provider instead of "should work" (#47); and the self-reported defect backlog goes to zero
(#40–#42). No security property changes: the container recipe, guardrails, export validator and
no-host-git rule are untouched; §2 strengthens the host write path and §2.6 states its one
behavioural delta honestly.

## 1. Large writes: `append_file`, a targeted recovery, `--max-tokens`, `finish_reason` (#36)

### 1.1 The failure today (facts)

`max_tokens` is never set by the runner; both adapters default it to **4096**
(`openai_compat.py:192`, `anthropic.py:163`) and nothing makes it configurable. A tool call whose
JSON was cut off arrives with `tc.error` set; when `finish_reason == "length"` the runner records
failure kind `malformed_args` and returns the generic hint at `runner.py:680-686` ("Emit smaller
tool calls — e.g. write the file in pieces using multiple write_file/edit_file calls"). The model
then tends to go silent; three blank replies share the `empty_reply` kind and abort the run. No
tool can actually append, so "write it in pieces" is not honest advice for a new large file.

### 1.2 `append_file(path, text)` — the eleventh tool

- **Semantics:** appends `text` verbatim to an existing regular file. The file **must exist**
  (`ENOENT` → `ERROR: cannot append to '<path>': it does not exist; create it with write_file
  first`) — an append that silently creates would hide path typos exactly where the model is
  already confused. No newline is inserted or required: `append_file` writes bytes; the model owns
  line discipline (the tool description says so).
- **Caps:** the `text` argument is capped like any write argument; additionally the **resulting
  file size** may not exceed `MAX_WRITE_BYTES` (5 MB): pre-check `current_size +
  len(text.encode()) > MAX_WRITE_BYTES` → `ERROR: result is <n> bytes, over the <MAX_WRITE_BYTES>-
  byte write limit; nothing was written` (the §1.5-of-0.9 string, reused).
- **Host:** containment via `resolve_in_worktree(writing=True)` then the §2 atomic path in append
  mode — read the current content through the probe fd, write `old + text` to the temp, replace.
  (Atomicity makes append crash-safe for free; the probe reproduces every existing refusal:
  symlink → ELOOP text, FIFO → ENXIO text, directory/other → generic tail.)
- **Docker:** `_append_raw` mirroring `_write_raw` with the §2.7 script in append form (temp =
  copy of current file, `cat >> tmp`, promote). Same `_oversized`-on-result and `_rel` checks.
- **Result string:** `describe_change(path, old_text, new_text, verb="Appended to")` — header
  `Appended to <path>: +A -0` plus the capped diff (the shared renderer handles it; no new format).
- **Registration:** `APPEND_FILE_SPEC` immediately after `WRITE_FILE_SPEC` in `BUILTIN_SPECS`;
  params `path`, `text` (both required, strings); caps as `write_file`; description: "Append text
  verbatim to the END of an existing file (create the file with write_file first). Nothing is
  inserted between the old content and your text — include a leading newline if the file does not
  end with one. Use write_file + append_file to produce a file too large for one reply."
  `_MUTATING_TOOLS` gains it; the Sandbox Protocol and both backends gain the method; the wire
  fixture regenerates; the system-prompt file rule now names it; tool count ten → **eleven** in
  every enumeration (README ×2, `docs/security.md`, `docs/transcript-schema.md` enum + result row,
  `docs/machine-contract.md` Tools subsection, `builtin_tools.py` module docstring,
  `tests/test_transcript_schema.py` hand list, `test_schemas_shape` name set, `FakeSandbox`).

### 1.3 Targeted recovery for a length-truncated tool call

The `finish_reason == "length"` branch (`runner.py:680-686`) becomes tool-aware:

- If `tc.name == "write_file"` and a `path` can be recovered from the truncated raw JSON — on the
  OpenAI-compat adapter `tc.raw_arguments` holds the model's raw string and `path` is normally
  emitted before the large `content`; recover it with a bounded regex
  (`"path"\s*:\s*"((?:[^"\\]|\\.)*)"`, first match, JSON-unescaped); on Anthropic
  `raw_arguments` is empty and this branch degrades to the generic form — the result becomes:

  ```
  ERROR: your write_file for '<path>' was cut off at the token limit — nothing was written. Write the file in chunks: write_file with the first part, then append_file for each following part.
  ```

- Any other tool, or no recoverable path:

  ```
  ERROR: your <tool> call was cut off at the token limit before it completed. Emit smaller tool calls — for a large file, write_file the first part and append_file the rest.
  ```

  (The existing test `test_length_finish_reason_gives_helpful_hint` is rewritten to the new
  generic string; a new test covers the path-recovered form and the Anthropic no-raw degradation.)
- Failure accounting is unchanged (`malformed_args`, same thresholds). The #36 death-spiral is
  addressed by making the *advice* actionable, not by exempting the failure from counting.

### 1.4 `--max-tokens` (default 8192)

- New flag on `run` and `resume`: `--max-tokens N` (`_positive_int`), default
  `DEFAULT_MAX_TOKENS = 8192` (module constant in `runner.py`). Threaded
  `__main__` → `Runner.__init__(max_tokens=…)` → every `provider.chat(…, max_tokens=self.max_tokens)`
  call; both adapters' own defaults stay 4096 for direct/library callers but the runner always
  passes an explicit value. `resume` inherits the prior run's value from `run.json`
  (`max_tokens` recorded at start by `_write_run_json_start`) unless the flag is given —
  the same inheritance shape as `verify_*`.
- Recorded: `run_start.max_tokens`, `run.json.max_tokens` (start). Not echoed on the stdout
  payload (it is configuration, not evidence; `run.json` carries it).
- Docs: Machine contract flag list + `docs/operating.md` (one paragraph under the bash/tools
  section: what the cap is, the 8192 default, when to raise it, decode-speed cost —
  at ~50–75 tok/s a full 8192-token reply is ~2 min).

### 1.5 `finish_reason` in the transcript

The `assistant` transcript event gains `finish_reason: <string|null>` (additive; whatever the
adapter reported — `"stop"`, `"length"`, `"tool_calls"`, or `null` when absent). Documented in
`docs/transcript-schema.md`; `tests/test_transcript_schema.py` doc-token lists updated. This makes
#36-shaped failures diagnosable from the transcript alone.

## 2. Atomic writes (#43)

### 2.1 Scope and honest threat model

Applies to every host in-place write (`write_file`, `_transform_file` — i.e. `edit_file`,
`apply_edits`, `insert_before`, `insert_after` — and §1.2's `append_file`) and to docker's
`_write_raw`/`_append_raw`. **This is robustness, not a security fix**: in host mode tool calls
are serial and `run_capped` SIGKILLs the whole process group after every bash call, so the
realistic adversary is a confused or prompt-injected model using the file tools — the existing
`O_NOFOLLOW` refusals are an accident guard and stay exactly as deterministic as today. In docker
mode the container is the boundary (read-only rootfs, no caps, tmpfs mounts, export validator);
docker needs atomicity and exec-bit preservation only, not new refusal semantics.

### 2.2 Host primitive: `_write_atomic(worktree, path, data, *, probe_existing=True)`

One function in `tools.py`, used by `write_file`, `_transform_file` and `append_file`:

1. **Probe-open the target** `O_WRONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC` — **no `O_CREAT`**
   (side-effect-free). Outcomes reproduce today's refusals byte-for-byte: `ELOOP` → the existing
   symlink text; `ENXIO` → the existing FIFO text; any other `OSError` except `ENOENT` → the
   existing generic tail (`ERROR: cannot write '<path>': {e}`); `ENOENT` → the new-file branch
   (no mode to preserve; skip to step 3 with default mode `0o644`). On success `fstat` the fd:
   not `S_ISREG` → the existing errno-less "not a regular file" refusal; capture
   `st.st_mode`/`st.st_nlink`.
2. **Hardlink fallback:** if `st_nlink > 1`, write **through the already-open fd**
   (`ftruncate(fd, 0)` + write) — same shared-inode semantics as today, non-atomic only in the
   case that is non-atomic today. (Close the fd in every other branch.)
3. **Temp:** same directory as the target (same filesystem → atomic rename), name
   `.dw-tmp.<basename>.<8 hex from os.urandom>`, opened
   `O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC`, mode `0o600`; write `data`;
   `fchmod(tmp_fd, stat.S_IMODE(st.st_mode))` when the target existed (new file: leave
   `0o644 & ~umask`); `os.replace(tmp, target)`.
4. **Unwind:** from temp creation on, `try/except BaseException: unlink(tmp); raise` — a failure,
   timeout or Ctrl-C leaves the target untouched and no temp behind on that path.
5. **`EACCES`/`EROFS` at temp creation** (writable file in an unwritable directory — works today
   via `O_TRUNC`): fall back to writing through the probe fd as in step 2, preserving today's
   behaviour for `0555` directories.

The in-tree precedent is `rundir.write_run_json` (temp + `O_NOFOLLOW` + `os.replace` +
unlink-on-failure); the primitive follows its shape with a per-call random suffix (a fixed name
would be a worker-controllable collision target).

### 2.3 What "nothing was written" now means

All pre-write refusals are unchanged. NEW: a failure **during** the write (I/O error, kill,
`BudgetExceeded` mid-call) leaves the target byte-identical instead of possibly truncated — except
the two documented fd-fallback branches (hardlinked target; unwritable directory), which keep
today's in-place semantics. `apply_edits`' description drops the "before the write begins"
qualifier; `docs/operating.md`'s §1.6-of-0.9 caveat is replaced with the new guarantee + the two
named exceptions; issue #43 is closed by this section.

### 2.4 The accepted race delta (owner decision 2026-08-20)

If the target becomes a symlink between the probe and the `os.replace`, today's code would refuse;
the new code replaces **the symlink itself** — `rename(2)` does not dereference its destination,
so the link's target (inside or outside the worktree) is never written through. Deterministic
refusal of any symlink present at call time is unchanged. Stated in `docs/security.md`'s host-mode
notes in exactly these terms.

### 2.5 Sweep

`HostSandbox.finalize` and the docker export path sweep `.dw-tmp.*` leftovers (crash remnants from
a killed run) **before** the budget measure / `git add -A`, so they can never appear in
`files_changed`, `diff_stat`, `diff.patch`, a snapshot, or the worktree budget. Count swept files
into a `finalize` stderr note (`swept N stale temp file(s)`) — never silently.

### 2.6 Docker script

`_write_raw` becomes (host-generated temp name passed as `$2`; worker data never in the script):

```sh
mkdir -p "$(dirname -- "$1")" && [ ! -d "$1" ] && cat > "$2" && { chmod --reference="$1" "$2" 2>/dev/null || chmod 644 "$2"; } && mv -fT "$2" "$1" || { rm -f -- "$2"; exit 1; }
```

- `&&`-chained throughout (a failed `cat` never promotes the temp); `mv -fT` (never move *into* a
  directory); exec bit preserved via `chmod --reference` with the `chmod 644` fallback for a new
  file (image is bookworm coreutils — both exist); `rm -f` on any failure.
- `_append_raw`: same shape with `cp -- "$1" "$2" && cat >> "$2"` before the promote (and the
  ENOENT check `[ -f "$1" ]` producing the §1.2 does-not-exist error).
- The parity test's `'cat > "$1"'` matcher in `tests/test_docker_sandbox.py` is updated to the new
  script; every existing behaviour test (write to new deep path, oversized refusal, no-write-on-
  refusal) keeps passing with the new argv shape.

## 3. Ollama first-class (#47)

### 3.1 `--provider ollama`

`OllamaClient(OpenAICompatClient)` in `dirtywork/providers/ollama.py`: `name = "ollama"`, default
`base_url = "http://localhost:11434/v1"`, registered in `PROVIDER_NAMES`/`get_provider` and
`DEFAULT_BASE_URLS`. Differences from the parent, all verified against a live Ollama v0.32.x:

- **Hints** (`_ENDPOINT_HINTS` + the model-not-loaded hint): `Is Ollama running? Try: ollama ps` /
  `pull or run it first: ollama run <model> — note Ollama model ids include the tag, e.g.
  'gemma4:latest'`.
- **`loaded_context_window(model)`**: `GET {origin}/api/ps` (2 s, same probe timeout constant,
  same swallow-everything contract); accept iff `models[]` has an entry whose `model` (or `name`)
  equals `model` and whose `context_length` is a positive non-bool int — verified live: this is
  the **loaded** `num_ctx`, moving when a chat sets `options.num_ctx`. Source string
  `provider:ollama:server`; the static table has no Ollama entries, so the fallback below server
  is `default` + the existing warning.
- **Wire shape**: identical to the parent for everything dirtywork uses (verified live:
  `/v1/models` ids-with-tag; `tool_calls` with string arguments + ids; `finish_reason:
  "tool_calls"`; `role: "tool"` history accepted; an extra `message.reasoning` field is ignored by
  the parser already). No parser changes. **Parallel tool calls are unverified on Ollama** — noted
  in the README row; the contract tests cover the single-call shape from captured fixtures.

### 3.2 Tests and docs

- **Recorded fixtures** captured from the live probes (`tests/fixtures/providers/ollama/…`):
  models list, single tool call, tool-role round-trip, `/api/ps` shapes (loaded, empty, missing
  field) — driven through the existing `ProviderContract` base + `RecordingTransport`.
- **Live smoke** (`@pytest.mark.live`-style, new marker `ollama` excluded by default addopts):
  one end-to-end `dirtywork run --provider ollama --sandbox none` against a small local model,
  mirroring the LM Studio live test.
- README Platform support: Ollama moves to **verified** (models tested named; parallel-tool-calls
  caveat); Requirements/machine contract mention `--provider ollama`; `docs/operating.md` gets an
  Ollama quickstart line (`ollama run qwen3.6 …`, full model tag required). Issue #22's
  Ollama-probe follow-up closes with this section.

## 4. Snapshot follow-ups (#40)

1. `--branch-from @<slug>` resolves through the same slug validation as `runs snapshot`
   (`_run_dir_for`-equivalent: `_SLUG_RE` + parent containment); the refusal message for a
   non-slug is the path-form message, not "unknown run".
2. `EMPTY_TREE_SHA` replaced by a per-repo `git hash-object -t tree /dev/null` (config-neutral
   env, cached per call) so SHA-256 repos get the correct no-op behaviour.
3. `snapshot_worktree` hashes without `-w` first and writes blobs only when a commit will be made
   (no loose objects from a no-op snapshot).
4. `_walk_worktree` prunes ignored directories during the walk: batch `check-ignore` the
   directory paths per level (one extra batch call), skipping descent into ignored dirs —
   `runs snapshot`/`@slug` become O(tracked files) on repos with big `node_modules`/`.venv`.

## 5. Tools wording/perf nits (#41)

1. Host/docker UTF-8 refusal parity: docker `_read_raw(strict=True)` adopts the host wording
   (`ERROR: <path> is not valid UTF-8 text; <tool> only works on text files`) — `_transform_file`
   passes the tool name through (docker's transform already knows it from the caller); docker-mode
   tests for the non-UTF-8 case on `insert_*`/`apply_edits` added.
2. `describe_change` computes opcodes once (a single `SequenceMatcher` pass feeding both the
   `+A -D` counts and hand-rendered hunks with the same `n=2` context and `@@` headers
   `unified_diff` produces — golden-tested against `difflib.unified_diff` output on a corpus of
   cases so the rendering is byte-identical).
3. CRLF rendering documented in `docs/machine-contract.md`'s tool-results paragraph (carriage
   returns show as-is, like git).
4. Doc nits: `docs/transcript-schema.md` result row names all mutating tools;
   README's "insert_* echoes a diff" sentence gains the new-file `write_file` exception.

## 6. Test-coverage gaps (#42)

Exactly the issue's list, as tests only (no behaviour changes): docker `files_changed`
truncation + the Markdown "list truncated" note; `_MUTATING_TOOLS` counts `insert_*`/
`apply_edits`/`append_file` as stall progress; docker `write_file` pre-read failure modes
(oversized/non-UTF-8 old file still writes, renders "new file"); `--feedback ""` treated as
absent (docstring note + test); non-UTF-8 `--feedback-file` → exit 2 test; explicit-`null`
`verify_rounds`/`verify_timeout` in a hand-edited `run.json` hardened
(`prior.get(k) if … is not None else default`) + test.

## 7. Cross-cutting

- **Contract (all additive, `schema_version` 2):** new tool `append_file` (eleven tools); flag
  `--max-tokens` (run + resume, inherited on resume); `run_start`/`run.json` gain `max_tokens`;
  `assistant` events gain `finish_reason`; the two new §1.3 recovery strings; `--provider ollama`.
  Nothing removed or renamed; stdout payload unchanged except via existing seams.
- **Image/version:** `DEFAULT_IMAGE` → `:0.10`, CI docker-live tag, docs mentions;
  `PINNED_DIGEST = None` (pin in 0.10.1); version `0.10.0` in both files.
- **Tests:** TDD throughout; new tests in existing modules except the sanctioned
  `tests/fixtures/providers/ollama/` fixtures and the Ollama live-smoke addition to the live
  suite; full unit suite green on 3.9 after every task; docker parity suites updated for the new
  write scripts.
- **Docs:** README, `docs/operating.md`, `docs/machine-contract.md`, `docs/security.md` (§2.4
  delta), `docs/transcript-schema.md`, `docker/README.md`.
- **Issue hygiene at ship:** #36, #40, #41, #42, #43, #47 close; #22's Ollama follow-up resolved;
  #48 (soak) starts on the 0.10.0 release.
