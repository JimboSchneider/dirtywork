# 0.10 (v1 RC) — large writes recoverable, atomic file writes, Ollama first-class, backlog zero

**Date:** 2026-08-20
**Status:** Revised design, v3 — v1's decisions were approved in chat (2026-08-20 10:35 CDT:
`--max-tokens` default 8192; new `--provider ollama`; atomic-write race delta accepted); v2 folded in
a three-lens Opus red-team (7 Blockers, 27 Importants); v3 folds in the owner's review of v2
(2026-08-20 12:54 CDT: read-open O_NONBLOCK, new-file mode 0o644, the _write_atomic catch boundary,
the docker append size mechanism, tools.py in the §1.2 list, docker append FIFO guard). Approved
2026-08-23 (09:51 CDT) with three clarifications folded below: an append-specific argument-cap path
shared by both backends with exact parity tests (never `_oversized`'s write-file wording); docker's
append read is tool-aware (`tool="append_file"`); the temp fd is closed before `os.replace`.
**Origin:** milestone **0.10.0 — v1 release candidate**: issues #36, #43, #47, #40, #41, #42.
#48 (the v1 soak) runs after this ships and is not spec'd here.
**Parent specs:** `2026-08-19-tools-context-timeouts-design.md` (v3.3),
`2026-08-18-run-evidence-and-review-loop-design.md`, `2026-08-15-review-response-design.md`.
Ships as **dirtywork 0.10.0**; worker image tag **`:0.10`** (Dockerfile unchanged;
`PINNED_DIGEST = None` → pinned in 0.10.1). Stdlib-only, Python 3.9 floor, `schema_version` 2,
additive.

## Purpose

Close the last core-job holes before v1: a file larger than one turn's output becomes a recoverable
two-step instead of a silent `model_error` (#36); an interrupted write can no longer leave a
truncated file (#43); Ollama becomes a verified first-class provider (#47); the self-reported defect
backlog goes to zero (#40–#42). No security property changes; §2.4 states the one behavioural delta
honestly.

## 1. Large writes (#36)

### 1.1 The failure today (facts)

`max_tokens` is never set by the runner; both adapters default it to **4096**
(`openai_compat.py:192`, `anthropic.py:163`) and nothing makes it configurable. A tool call whose
JSON was cut off arrives with `tc.error` set; when `finish_reason == "length"` the branch at
`runner.py:678-688` records failure kind `malformed_args` and returns a generic "cut off at the
token limit" hint. The model then tends to go silent (three blank replies share `empty_reply` →
abort). No tool can append, so "write it in pieces" is not honest advice for a new large file.

### 1.2 `append_file(path, text)` — the eleventh tool

- **Semantics:** appends `text` verbatim to an existing regular file. The file **must exist**:
  a missing target refuses with `ERROR: cannot append to '<path>': it does not exist; create it
  with write_file first`. No newline is inserted or required — `append_file` writes bytes and the
  model owns line discipline (the tool description says so).
- **Caps, in this order on both backends** (so neither mode ever surfaces the read-limit wording
  from an append):
  1. the `text` **argument**: `len(text.encode()) > MAX_WRITE_BYTES` → `ERROR: text is <n> bytes,
     over the <MAX_WRITE_BYTES>-byte write limit; append in smaller pieces` — emitted by a shared
     `_append_oversized(encoded)` helper in `tools.py`, imported by `docker.py` the way
     `MAX_WRITE_BYTES` already is, so both backends produce the byte-identical string. The shared
     `_oversized`'s write-file wording (`content is …; write the file in smaller pieces`) must
     never surface from an append. **Parity tests:** host-mode and docker-mode tests assert the
     byte-identical string for each of the three caps;
  2. the **current file** must be readable under `MAX_READ_BYTES` (5 MB) — a larger file is
     un-appendable in both modes (host: `os.stat` size pre-check; docker: the existing
     `_read_raw` cap) and refuses with the **result**-cap string;
  3. the **result**: `len(old) + len(text.encode()) > MAX_WRITE_BYTES` → the shared
     `_check_write_size` string `ERROR: result is <n> bytes, over the <MAX_WRITE_BYTES>-byte write
     limit; nothing was written`.
- **Host:** containment via `resolve_in_worktree(writing=True)`; the §2.2 `O_WRONLY` probe runs
  **unchanged** (so the ELOOP / FIFO / non-regular refusals are byte-identical, re-worded with the
  append verb per §2.2's `verb` parameter; `ENOENT` on the probe is the does-not-exist error above,
  never §2.2's new-file branch). The current content is read through a **second** open of the
  `_worktree_candidate` path via the existing `_open_regular(p, os.O_RDONLY,
  max_size=MAX_READ_BYTES)` — which already adds `O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC` (the
  `O_NONBLOCK` matters: a FIFO swapped in between the two opens must not block the read open),
  verifies `S_ISREG` via `fstat`, and only then restores blocking mode with
  `os.set_blocking(fd, True)`. Strict UTF-8 decode — non-UTF-8 → `ERROR: <path> is not valid UTF-8
  text; append_file only works on text files`, matching the host transform wording. The read fd's
  `fstat` must additionally match the probe fd's `st_ino`/`st_dev` — a swap between the two opens
  refuses with the generic tail. Then `_write_atomic(target, old_bytes + text.encode(), verb="append")`. `append_file`
  never creates parent directories.
- **Docker:** `_append_raw` reuses `_rel(writing=True)` and takes **three execs**: (1) a guard +
  size exec `sh -c '[ ! -h "$1" ] || exit 3; [ -e "$1" ] || exit 2; [ -f "$1" ] || exit 3; stat -Lc
  %s -- "$1"'` — rc 2 →
  the exact does-not-exist string; rc 3 (a symlink — dangling included — or a FIFO/device/directory,
  **checked before any read so a FIFO can never block a reader exec**) → `ERROR: cannot append to
  '<path>': not a regular file`;
  rc 0 → the exact byte size on stdout, which lets docker produce the **exact** result-cap string
  even for a file too large to read (`_read_raw` alone discards the size — `head -c N+1` only
  proves "exceeds"): `size > MAX_READ_BYTES` or `size + len(text) > MAX_WRITE_BYTES` → `ERROR:
  result is <size + len(text)> bytes, over the <MAX_WRITE_BYTES>-byte write limit; nothing was
  written`; (2) the existing `_read_raw(path, strict=True,
  tool="append_file")` — the §5.1 `tool` parameter, so invalid UTF-8 refuses with `ERROR: <path>
  is not valid UTF-8 text; append_file only works on text files`, matching the host append
  wording, never the legacy `refusing to edit`; a race between the guard exec and this one (the
  file grew past `MAX_READ_BYTES` in between) is trapped by matching `_read_raw`'s "exceeds"
  refusal and remapping it to the result-cap string built from the guard's last-known size, so
  `_read_raw`'s own read-limit wording can never surface from an append; (3) the §2.6 append write
  script. Before it, the text just read is re-summed against `MAX_WRITE_BYTES` (the guard's size
  is a moment old — the file may have grown in place since the guard exec) and refused with the
  result-cap string on the recomputed sum if it now exceeds the cap. Host mirrors the same order
  with `os.stat` on the probe fd, so both modes emit identical strings from identical conditions.
  The `text` argument is capped by `_append_oversized` **before any exec**; `_append_raw` never
  routes the payload through `_oversized`, whose write-file wording must not surface from an
  append. The write script's missing-target guard exits
  **2** (mapped to the does-not-exist string — a delete between execs still refuses correctly);
  the write script also re-checks writability (`[ -w "$1" ]`, WRITE_SCRIPT's own guard's
  counterpart) so an unwritable target refuses `EACCES` instead of either leaking a temp path in a
  `cp`/`cat` stderr wrap or, run as root, silently succeeding; any other failure exits 1 and wraps
  stderr as `ERROR: cannot append to '<path>': {stderr}`.

  (Execution amendment, 2026-08-23: the guard's original `stat -c %s` was an **lstat** — a symlink
  to an oversized file dodged both size caps, since the guard never dereferenced it to see the real
  target size. Fix round 1 for Task 4 adds the `[ ! -h "$1" ] || exit 3` check FIRST (restoring
  parity with the host's `O_NOFOLLOW` probe, which also refuses symlinks, and closing the
  cap-bypass) and switches the final `stat` to `-Lc` — belt-and-suspenders for a race after the
  `-h` check, now that the guard is trusted to dereference. The same review found the append write
  script missing WRITE_SCRIPT's writability guard, and the read/write execs trusting the guard's
  snapshot with no re-check against a file that changed size in between — both are fixed above and
  mirror `tools.append_file`'s own probe-then-read handling, which already re-checks after the
  read (`len(raw) + len(encoded) > MAX_WRITE_BYTES`, tools.py ~:830).)

  (Execution amendment, 2026-08-23: the owner's PR review of `append_file` (PR #56) found that
  "the existing `_read_raw` cap" above was never actually `MAX_READ_BYTES` in practice —
  `docker_cli.run` called `run_capped` with no explicit `cap` at all, so EVERY docker exec's
  capture, `_read_raw`'s `head -c MAX_READ_BYTES+1` read included, silently stopped at
  `procs.MAX_CAPTURE_BYTES` (1 MiB) with `Captured.truncated` set and ignored. A 1–5 MiB file
  therefore "read" as its first 1 MiB: `append_file`'s diff was built from a truncated copy, and
  worse, `_transform_file` (edit_file/apply_edits/insert_before/insert_after) would write that
  truncated copy back — silent data loss on any in-place edit of a 1–5 MiB file. The
  `len(captured.output) > MAX_READ_BYTES` refusal a few paragraphs up was consequently dead code:
  a 6 MiB file was never refused, since the capture never got that far. Fix: `docker_cli.run` gains
  an explicit `cap` keyword (default `procs.MAX_CAPTURE_BYTES`, forwarded to `run_capped`) so a
  caller reading a whole file back can ask for a cap above what it expects to receive; `_read_raw`
  now requests `cap=MAX_READ_BYTES + 1` — exactly what `head` can ever emit — so its capture is
  never cut short and the exceeds-refusal is live again. `Captured.truncated` is also refused
  defensively (`if captured.truncated or len(captured.output) > MAX_READ_BYTES`), even though the
  exec itself should never produce one under the new cap. Export's own patch-streaming path has its
  own cap and was untouched.)
- **Result string:** whatever `describe_change(path, old_text, new_text, verb="Appended to")`
  computes. It is `+A -0` only when the file already ended in a newline; when it did not, the final
  line is a replace and the header reads `+A -1 (removed 1 non-blank line)` — the visible
  consequence of not including a leading newline, which the tool description warns about. No new
  counting rule.
- **Registration:** `APPEND_FILE_SPEC` immediately after `WRITE_FILE_SPEC` in `BUILTIN_SPECS`;
  params `path`, `text` (required strings); caps as `write_file`; description: "Append text
  verbatim to the END of an existing file (create the file with write_file first). Nothing is
  inserted between the old content and your text — include a leading newline if the file does not
  end with one. Use write_file + append_file to produce a file too large for one reply."
  Updates, exhaustively: `dirtywork/tools.py` (the `append_file` function + `_write_atomic`'s
  `verb="append"` wording), `Sandbox` Protocol (method + the tool enumeration in its docstring,
  `sandbox/__init__.py:51`), `HostSandbox` (+`_check_budget` wrap), `DockerSandbox`,
  `runner._MUTATING_TOOLS`, the system-prompt file rule (`__main__.py:71`,
  `build_system_prompt`), the wire fixture (regenerated — any `ToolSpec.description` change
  requires this), ten → **eleven** in: README ×2, `docs/security.md`, `docs/transcript-schema.md`
  (enum + the result-format row), `docs/machine-contract.md` Tools subsection,
  `builtin_tools.py` module docstring ("The ten tools…"), `tests/test_transcript_schema.py`'s
  hand list **and** the test rename `test_doc_documents_the_finish_tool_and_the_ten_tools` →
  `…_eleven_tools`, `test_schemas_shape`'s name set, `FakeSandbox` (`tests/test_builtin_tools.py`).

### 1.3 Targeted recovery for a length-truncated tool call

The `finish_reason == "length"` branch (`runner.py:678-688`) becomes tool-aware. A runner-side
helper (adapters stay dumb; `raw_arguments` is a neutral `ToolCall` field) recovers the path:

- **When it fires:** (a) as today, `tc.error is not None` with `finish_reason == "length"`; and
  (b) NEW — `finish_reason == "length"`, `tc.error is None`, and the parsed arguments dict is
  missing a required parameter of `tc.name` (the Anthropic shape: a truncated `tool_use` whose
  `input` came back `{}` parses "successfully") — same strings, same `malformed_args` accounting.
  Case (b) is checked before dispatch so the registry's `bad_args` path never swallows it.
- **Path recovery:** `re.search(r'"path"\s*:\s*"((?:[^"\\]|\\.)*)"', tc.raw_arguments[:8192])`;
  unescape with `json.loads('"' + m.group(1) + '"')` inside `try/except ValueError` → generic
  form on any failure; the recovered path is truncated to 200 chars and rendered with `!r`. On
  Anthropic `raw_arguments` is `""` (its error branches never set it) and recovery degrades to the
  generic form.
- **Strings:** recovered `write_file` path →

  ```
  ERROR: your write_file for <path!r> was cut off at the token limit — nothing was written. Write the file in chunks: write_file with the first part, then append_file for each following part.
  ```

  any other tool / no recoverable path →

  ```
  ERROR: your <tool> call was cut off at the token limit before it completed. Emit smaller tool calls — for a large file, write_file the first part and append_file the rest.
  ```

- **The text-side nudge too:** `NUDGES["truncated"]` (`runner.py:77-78`) is reworded to name the
  tools: "… emit one tool call at a time; for a large file, write_file the first part and
  append_file the rest." (`tests/test_runner.py:682` compares against the constant — no change.)
- **Tests:** the existing `test_length_finish_reason_gives_helpful_hint` (`tests/test_runner.py:
  320-333`) becomes the **path-recovered** case — its fixture already carries a recoverable
  `path`, and its current assertion (`"cut off at the token limit" in …`) pins nothing; it now
  asserts the full recovered sentence. New tests: a non-`write_file` tool → generic; Anthropic
  shape (`raw_arguments=""`) → generic; case (b) (empty-dict args, `length`) → `malformed_args`
  not `bad_args`; a raw fragment whose escape sequence is invalid JSON → generic (no exception).
- Failure accounting is otherwise unchanged.

### 1.4 `--max-tokens` (default 8192)

- Added by the shared `_add_run_flags` with `type=_positive_int, default=None if resume else
  DEFAULT_MAX_TOKENS` (`DEFAULT_MAX_TOKENS = 8192`, `runner.py`), so a resume with no flag falls
  through to inheritance: `args.max_tokens = prior.get("max_tokens") if prior.get("max_tokens")
  is not None else DEFAULT_MAX_TOKENS` — the §6 hardened shape, which also covers pre-0.10
  `run.json` files. **Stated consequence:** resuming a 0.9 run silently moves the effective cap
  4096 → 8192 (the new default; the resume transcript records the value used).

  (Execution amendment, 2026-08-23: fix round 1 for Task 7 hardens the inherited value itself, not
  just its presence — a non-`int` (bool excluded), zero, or negative `prior.get("max_tokens")` now
  also falls back to `DEFAULT_MAX_TOKENS`, since a hand-edited `run.json` can carry anything JSON
  allows for this key and would otherwise traceback preflight's `>=` comparison or bypass
  `_positive_int` into a cap-blind or inflated budget.)
- Threaded `__main__` → `Runner.__init__(max_tokens=…)` → the single `provider.chat` call site
  (`runner.py:590-592`) as an explicit kwarg. Both adapters already accept it; their own defaults
  stay 4096 for direct callers (`tests/test_llm.py`'s `payload["max_tokens"] == 4096` stays
  green via a direct-adapter test path).
- **Budget interaction (the window is shared):** `char_budget = int(max(0, context_window -
  max_tokens) * BUDGET_FRACTION * CHARS_PER_TOKEN)` — at the 32768 default window and 8192 cap the
  prompt budget is ~18.4k tokens' worth of chars, leaving real slack instead of today's
  cap-blind 24.5k. Preflight refuses `--max-tokens >= context_window` with `--max-tokens <N> must
  be smaller than the <W>-token context window` (exit 2).
- Recorded in `run_start` and `run.json` **by the runner's own `run_start` kwargs** (it already
  emits `max_turns`/`timeout`/`context_window` there) and `_write_run_json_start`. Not echoed on
  the stdout payload (configuration, not evidence — consistent with the `_contract_fields` seam;
  nothing auto-echoes start fields).
- **Anthropic note:** the default rises for `--provider anthropic` too; some older Claude models
  cap output at 4096 and reject a larger `max_tokens` — the machine-contract flag row says "pass
  `--max-tokens 4096` for models that cap output there".
- Docs: machine-contract flag list, `docs/operating.md` (cap + decode-cost paragraph),
  **`docs/transcript-schema.md`** `run_start` and `run.json` tables (doc-token test covers them).

### 1.5 `finish_reason` in the transcript

The `assistant` event gains `finish_reason` — written as `finish_reason if
isinstance(finish_reason, str) else None` (adapters do not guarantee a string; Anthropic passes
unknown stop reasons through raw). Documented in `docs/transcript-schema.md` as `string | null`,
common values `stop`/`length`/`tool_calls`, **not a closed enum**.
`tests/test_transcript_schema.py` gains `ASSISTANT_FIELDS = ["text", "tool_calls",
"finish_reason"]` with a doc-token assertion mirroring `RUN_END_FIELDS`. (The write site is
`runner.py:627-631`, where `finish_reason` is already in scope.)

## 2. Atomic writes (#43)

### 2.1 Scope and honest threat model

Applies to every host in-place write (`write_file`, `_transform_file`, `append_file`) and docker's
`_write_raw`/`_append_raw`. **Robustness, not a security fix**: host tool calls are serial and
`run_capped` SIGKILLs the process group after every bash call; the realistic adversary is a
confused or prompt-injected model using the file tools. The `O_NOFOLLOW` refusals remain exactly
as deterministic as today. Docker's container is the boundary; it needs atomicity plus
mode-preservation under the new `mv` (today's `cat >` writes through the inode and preserves mode
for free — the temp+`mv` shape is what creates the need).

### 2.2 Host primitive

`_write_atomic(target: Path, data: bytes, *, verb: str = "write", create_parents: bool = False)
-> str | None` — returns an `ERROR:` string or `None`. `target` is the already-computed
`_worktree_candidate` path (callers keep their existing `resolve_in_worktree` calls). `verb`
selects the refusal wording (`write` → `cannot write '<path>'`; `append` → `cannot append to
'<path>'`); the ELOOP and non-regular-file strings are shared verbatim.

0. **Parents:** when `create_parents` (i.e. from `write_file` only): `target.parent.mkdir(
   parents=True, exist_ok=True)` before the probe, exactly as today; `OSError` → the generic
   tail. `_transform_file` and `append_file` never mkdir.
1. **Probe:** open `O_WRONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC` — no `O_CREAT`
   (side-effect-free). `ELOOP` → the existing symlink text; `ENXIO` → the existing FIFO text;
   any other `OSError` except `ENOENT` → the generic tail; `ENOENT` → the new-file branch
   (skip to 3, no mode to preserve). On success `fstat`: not `S_ISREG` → the existing errno-less
   refusal, which lands in the generic branch and today renders with the absolute path doubled —
   `ERROR: cannot write '<path>': '<abs>' is not a regular file (refusing FIFO/device/socket)` —
   **preserved verbatim**; capture `st_mode`/`st_nlink`/`st_ino`/`st_dev`.
2. **Hardlink fallback:** `st_nlink > 1` → write through the probe fd (`ftruncate(fd, 0)` +
   write) — shared-inode semantics preserved on purpose (a hardlink is *meant* to see the write);
   non-atomic exactly where today is. The probe fd stays **open until `os.replace` succeeds or a
   fallback is decided**, closed in `finally`.
3. **Temp:** same directory (same filesystem → atomic rename), name
   `.dw-tmp.<basename>.<8 lowercase hex from os.urandom>`, `O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|
   O_CLOEXEC` mode `0o600`; write `data`; then `fchmod(tmp_fd, stat.S_IMODE(st.st_mode))` when
   the target existed, **else** `fchmod(tmp_fd, 0o644 & ~_UMASK)` where `_UMASK` is read once at
   import (`os.umask(0)`/restore, before any thread) — matching today's `_open_regular(...,
   O_CREAT, mode=0o644)` exactly (`0o644 & ~umask`; with `umask 0` today yields `0o644`, so
   `0o666`-based masking would diverge there). Then **`os.close(tmp_fd)`** — writes go through
   raw `os.write`, so there is no userspace buffer to flush, and the close surfaces any deferred
   write error **before** the promote — then `os.replace(tmp, target)`.
4. **Catch boundary and unwind:** from temp creation on, every step (write, `fchmod`,
   the temp-fd `os.close`, `os.replace`) runs inside one `try`. An **`OSError`** there unlinks the temp and **returns**
   the generic tail (`ERROR: cannot <verb> '<path>': {e}`) — honouring the `str | None`
   signature; tool functions never raise. Any **other `BaseException`** (KeyboardInterrupt,
   `BudgetExceeded`, `SandboxError` from a budget hook) unlinks the temp and **re-raises** — those
   are run-level signals the runner owns, not tool results. The probe fd closes in `finally`
   either way.
5. **Temp-creation failure:** `EACCES`/`EROFS` with a live probe fd (writable file, unwritable
   directory) → fall back to writing through the probe fd (today's semantics for `0555`
   directories). `ENOENT` from the probe means there is no fd — return the generic tail with the
   temp-creation errno (preserves today's `EACCES` refusal for a new file in an unwritable
   directory). Any other temp error → generic tail.

Precedent: `rundir.write_run_json` (same shape); the random suffix is required here because the
worker controls sibling names.

### 2.3 What "nothing was written" now means

Pre-write refusals unchanged. NEW: a failure during the write leaves the target byte-identical —
except the two fd-fallback branches (hardlinked target: shared-inode semantics are the *point* of
a hardlink; unwritable directory: rename is impossible there), which keep today's in-place
behaviour and are named in the docs. `os.replace` changes the inode: a worker-held open fd on the
old file keeps seeing old content — stated in the same doc paragraph. Doc anchors: the "before the
write begins" phrasing lives at `docs/machine-contract.md:132` and `docs/operating.md:43` (not in
any `ToolSpec.description`) — both replaced with the new guarantee + named exceptions.

### 2.4 The accepted race delta (owner decision 2026-08-20)

A symlink present at call time refuses exactly as today. If the target becomes a symlink between
probe and `os.replace`, the new code replaces the link itself — `rename(2)` does not dereference
its destination, so nothing is ever written through it. Stated in `docs/security.md`'s host-mode
notes in these terms.

### 2.5 Sweep

The sweep matches the full generated shape `.dw-tmp.<name>.<8 lowercase hex>` (anchored regex,
never a bare glob — a worker file named `.dw-tmp.notes` is left alone). Because temps are unlinked
in-call, the only producers of leftovers are kills — so the sweep runs where it can actually help:
`HostSandbox.start()` (resumed runs) and, folded into `measure_worktree`'s existing walk (no
second traversal), at `finalize` before `_measure()`/`host_files_changed`; docker: one
`find /work -regextype posix-extended -regex … -delete` exec against the still-alive WORKER
container, in `DockerSandbox.finalize()` immediately before it stops the container — ahead of
export entirely. Swept count → stderr note (`swept N stale temp file(s)`), never silent.

(Execution amendment, 2026-08-23: fix round 1 relocated the docker sweep and tightened its regex.
Originally specified as one exec inside `export_run`, immediately before the export's `git add -A`
alongside the existing `.git`-entry sweep — but the export container's `/work` volume mount is
readonly by design (`docker_args.export_create_argv`), so a `find … -delete` there gets EROFS on
every match, exits non-zero, and the sweep silently does nothing while `git add -A` still stages
`.dw-tmp.…` into the export. The sweep instead runs against the still-alive WORKER container: one
exec in `DockerSandbox.finalize()`, immediately before `_stop_container()` — before export starts
at all, not alongside it. Reporting is never silent on a partial failure: the swept-N note fires
whenever the sweep printed any lines, regardless of exit code, and a non-zero rc additionally notes
`sweep incomplete (rc N)`. The same review found `TMP_FIND_REGEX` over-matching across a directory
boundary — `find -regex` matches the WHOLE path and POSIX ERE `.` crosses `/`, so a greedy `.+`
basename component would match INTO a `.dw-tmp.`-named directory and delete a worker's own file
underneath it; the basename component is now `[^/]+`. `is_temp_name`'s name-only match was never
affected — a single path component can never contain `/`.)

### 2.6 Docker scripts

`_write_raw` (host-generated temp name passed as `$2`; worker data never inside the script; each
guard echoes its own diagnostic so `_write_raw`'s stderr wrap never renders empty):

```sh
mkdir -p "$(dirname -- "$1")" && { [ ! -d "$1" ] || { echo "cannot write $1: Is a directory" >&2; exit 1; }; } && { [ -w "$1" ] || [ ! -e "$1" ] || { echo "cannot write $1: Permission denied" >&2; exit 1; }; } && cat > "$2" && { chmod --reference="$1" "$2" 2>/dev/null || chmod 644 "$2"; } && mv -fT -- "$2" "$1" || { rm -f -- "$2"; exit 1; }
```

(`mv -fT -- ` — the `--` was added during execution: Task 3's review proved in-container that a top-level target named like `-f` is otherwise taken as an option. Execution amendment, 2026-08-23.)

- `&&`-chained (a failed `cat` never promotes); `mv -fT` (never move *into* a directory); the
  writability guard keeps host parity (today an unwritable file refuses `EACCES`; without it the
  temp+`mv` would silently overwrite a `0444` file, since rename needs only directory write);
  `chmod --reference` + `chmod 644` fallback (new file; GNU coreutils on bookworm); `rm -f` on
  any failure is harmless when the temp never existed.
- `_append_raw` write script: `[ -f "$1" ] || exit 2` (re-checked at write time; also excludes
  directories and FIFOs — the §1.2 guard exec already refused them before any read), then
  `[ -w "$1" ] || { echo "cannot append to $1: Permission denied" >&2; exit 1; }` (WRITE_SCRIPT's
  own writability guard's counterpart — without it a `0444` target either leaks a temp path in a
  `cp`/`cat` stderr wrap or, run as root, silently succeeds), then
  `cp -- "$1" "$2" && cat >> "$2"` and the shared chmod/`mv -fT` promote (`cp` without `-p` is
  fine — the `chmod --reference` step runs after). rc 2 → the §1.2 does-not-exist string; rc 1 →
  `ERROR: cannot append to '<path>': {stderr}`.

  (Execution amendment, 2026-08-23: the writability guard line was added in Task 4's fix round 1,
  the same review that added the guard exec's symlink refusal — see the §1.2 amendment note above.)
- **Test churn, counted:** the `'cat > "$1"'` matcher appears 12× — an exact-argv assertion at
  `tests/test_docker_sandbox.py:380`, ten substring matchers in that file, one in
  `tests/test_docker_runs.py`. Replace the substring matchers with one shared
  `_is_write_exec(call)` helper so the next script change touches one place.

## 3. Ollama first-class (#47)

### 3.1 `--provider ollama`

`OllamaClient(OpenAICompatClient)` in `dirtywork/providers/ollama.py`; `name = "ollama"`;
`OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434/v1"` — the subclass overrides `__init__` only
to change that default (delegating to `super()`; a class attribute would not survive the parent's
unconditional assignment), and the same string joins `DEFAULT_BASE_URLS`. Registered in
`PROVIDER_NAMES`/`get_provider`; the CLI picks it up via `choices=list(PROVIDER_NAMES)`. All
verified against a live Ollama v0.32.x:

- **`context_window()` overridden to return `None`** — the parent's `CONTEXT_WINDOWS` is LM
  Studio's table; without the override an Ollama user with a same-named model would inherit LM
  Studio's number under source `provider:ollama`.
- **`loaded_context_window(model)`**: `GET {origin}/api/ps` (same 2 s probe timeout constant, same
  swallow-everything contract). Guard `isinstance(body.get("models"), list)`; iterate matching on
  the **`model` key only** (Ollama sets `model` and `name` to the same tagged id; matching both
  would make two entries matchable); on the **first** match return `context_length` iff it is a
  positive non-bool `int`, else `None` — do not continue scanning. No `state` check (`/api/ps`
  lists only resident models). Verified live: this field is the loaded `num_ctx` and moves when a
  chat sets `options.num_ctx`. Source `provider:ollama:server`; below it the chain is `default` +
  the existing warning.
- **Cold start (the common case, stated):** Ollama's `/v1/models` lists **pulled** models, so
  preflight passes for a model that is not resident; `/api/ps` then has no entry and the window
  falls to `default` (32768) while Ollama will load its own smaller `num_ctx` — silent server-side
  truncation, not a visible failure. The §3.2 docs tell Ollama users to `ollama run <model>`
  **before** the run or pass `--context-window` explicitly.
- **Hints:** `_ENDPOINT_HINTS["ollama"] = "Is Ollama running? Try: ollama ps"`; a new
  `_MODEL_HINTS` dict keyed by provider replaces the two-branch ternary at `__main__.py:189-192`
  (`openai` → `lms load <model>`; `ollama` → `Pull or run it first: ollama run <model> — Ollama
  model ids include the tag, e.g. 'gemma4:latest'`). For Ollama the preflight message itself says
  "not available" rather than "not loaded" (its `/v1/models` lists pulled models).
- **Resume:** the provider-switch refusal is unchanged with **no** `openai`↔`ollama` carve-out —
  the wire format is identical but the inherited `--model` (Ollama ids carry tags) and the
  un-recorded `base_url` make the run non-portable; the refusal comment is amended to say so.
  Pre-0.10 users who reached Ollama via `--provider openai --base-url …:11434/v1` keep resuming
  that way.
- **Wire shape:** identical to the parent for everything dirtywork uses (verified live: ids with
  `:tag`; `tool_calls` with string arguments + ids; `finish_reason: "tool_calls"`; `role: "tool"`
  accepted; the extra `message.reasoning` field is already ignored). Parallel tool calls are
  **unverified on Ollama** — the fixtures assert our parser, not the server; noted in the docs.

### 3.2 Tests and docs

- **Fixtures:** `tests/fixtures/providers/ollama/` carries the **eight filenames
  `ProviderContract` hard-codes** (`simple_ok`, `parallel_tool_calls`, `malformed_tool_call`,
  `finish_reason_stop`, `finish_reason_length_text`, `usage_missing`, `usage_nan_negative`,
  `bad_json_arguments`) — live-captured where Ollama produced them; `parallel_tool_calls`,
  `malformed_tool_call`, `usage_nan_negative`, `bad_json_arguments` hand-built from the live
  single-call shape. `/api/ps` cases (loaded, empty `models`, missing/invalid `context_length`,
  unreachable, non-list `models`) are standalone tests in **`tests/test_provider_ollama.py`** with
  inline bodies through `RecordingTransport` (the `test_provider_openai.py` precedent) — no
  fixture files for those.
- **Markers/CI:** `pyproject.toml` gains marker `"ollama: requires a running Ollama server"`
  **and** `addopts` becomes `-m 'not live and not docker and not ollama'` (the docker CI job
  passes its own `-m docker`, unaffected). The live smoke is a **new `tests/test_live_ollama.py`**
  with its own `pytestmark = [pytest.mark.ollama, pytest.mark.skipif(not _ollama_up(), …)]` —
  `tests/test_live.py` (whose module-level skipif probes LM Studio) is untouched.
- **Existing tests named:** `tests/test_providers.py:17-22` (exact-equality `PROVIDER_NAMES` /
  `DEFAULT_BASE_URLS`) gains the ollama entries; `:103` covers `get_provider("ollama").name`
  automatically; the explicit-`""` base_url invariant at `:93-97` extends to ollama (the subclass
  keeps `None`-only defaulting).
- **Docs:** the "Ollama is not probed in 0.9 … pass `--context-window`" paragraphs at
  `docs/machine-contract.md:106-109` and `docs/operating.md:258-260` are **replaced** with the
  `/api/ps` probe, its `provider:ollama:server` source, and the cold-start advice. README edit
  sites, named: the "only LM Studio and the Anthropic API adapter … are exercised" prose
  (`README.md:65-68` — now false), the provider bullet (`:83-85`), the other-servers paragraph
  (`:88-91`); `docs/operating.md` gains an Ollama quickstart line (full model tag required).
  Issue #22's Ollama follow-up closes with this section.

## 4. Snapshot follow-ups (#40)

1. **Slug validation parity:** `--branch-from @<slug>` resolves through the same rule as
   `runs snapshot` — `_SLUG_RE` + runs-dir containment, lifted into one shared helper (today's
   `runs._run_dir_for` is private; `_resolve_branch_from` currently routes through the
   path-permissive `resume.resolve_run_dir`). A syntactically invalid slug refuses with the shared
   helper's message; a **valid but missing** slug keeps `unknown run '<slug>' (no run dir under
   <RUNS_DIR>)` (pinned by `tests/test_main.py:1941`).
2. **SHA-256 repos:** `EMPTY_TREE_SHA` (two uses, no test pins it) is replaced by a per-invocation
   empty-tree id computed once per `snapshot_worktree` call (after the `symbolic-ref` check) via
   `printf '' | git hash-object -t tree --stdin` (stdin discipline — no `/dev/null` path).
3. **No loose objects on a no-op:** two-pass hashing. Pass 1: `hash-object --no-filters
   --stdin-paths` **without `-w`** for files, and `hash-object --stdin` without `-w` for symlink
   targets, building the (mode, sha, path) entry list; compare it against `git ls-tree -r -z
   <head_tree>` (same normalization) — equal → return `None`, nothing written. Different → re-run
   the identical hashing **with `-w`** and rebuild the entries from the second pass's shas (a file
   changed between passes must not leave the tree pointing at an unwritten blob), then
   update-index/write-tree/commit-tree as today. (Verified: `write-tree` fails on absent blobs, so
   the no-op decision cannot come after a `-w`-less update-index.)

   (Execution amendment, 2026-08-23: the "no loose objects on a no-op" framing above was imprecise
   — git's object store is content-addressed, so `-w` on unchanged content was ALREADY a no-op at
   the object-count level on the old single-pass code; that half of the claim held before this
   change too. Task 10's fix round 1 rewrote the pinning test
   (`test_snapshot_worktree_makes_a_no_op_decision_without_writing`) to check the command
   transcript instead of the object count, since the count alone doesn't discriminate old code from
   new. The two-pass change's real, measured effect is elsewhere: a genuine no-op now returns
   before the temp index + `write-tree` ever run and stops freshening blob mtimes (~21% faster at
   20k files), at the cost of a second full read+hash pass on the changed path (+9-19%).)
4. **Walk cost:** replace the walk with an explicit BFS: collect each level's candidate directory
   relpaths, run **one** batched index-aware `_ignored_relpaths` call per tree *depth* (never
   `--no-index` — index-awareness is what keeps a tracked `build/keep.txt` in the snapshot when
   `build/` is ignored; regression test for exactly that), drop ignored dirs, descend. Every
   directory path passes the control-character/UTF-8 guard (factored out of `snapshot_worktree`
   into a helper both call) **before** it reaches `check-ignore`, preserving the module's
   `WorkspaceError`-not-`UnicodeEncodeError` promise.

## 5. Tools wording/perf nits (#41)

1. **UTF-8 refusal parity:** `DockerSandbox._read_raw` gains `tool: str | None = None` and
   `_transform_file` gains a `tool` parameter passed by all four callers (`edit_file`,
   `apply_edits`, `insert_before`, `insert_after`); `write_file`'s discarded pre-read passes
   `tool="write_file"`. Docker's wording becomes the host's (`ERROR: <path> is not valid UTF-8
   text; <tool> only works on text files`); the docker docstring sentence claiming no tool name is
   needed is removed. Docker-mode tests for the non-UTF-8 case on the insert/apply tools **and `append_file`** added.
2. **Single-pass `describe_change`:** reuse the one `SequenceMatcher` via
   `get_grouped_opcodes(n)` and mirror `difflib._format_range_unified`'s two special cases
   (length 1 → bare number; length 0 → start−1). Verified by prototype: 3000 randomized pairs,
   0 mismatches vs `unified_diff(..., n=2, lineterm='')`. Test: a randomized, **seeded** property
   test (≥1000 generated pairs incl. no-trailing-newline, duplicate-line and empty sides)
   asserting byte-equality with `difflib.unified_diff`.
3. **CRLF rendering** documented in `docs/machine-contract.md`'s tool-results paragraph.
4. **Doc nits:** `docs/transcript-schema.md` result row names all mutating tools; README's
   "insert_* echoes a diff" sentence gains the new-file `write_file` exception.

## 6. Test-coverage gaps (#42)

Tests (and two one-line hardenings) only: docker `files_changed` truncation + the Markdown
"— list truncated" note; `_MUTATING_TOOLS` counts `insert_*`/`apply_edits`/`append_file` as stall
progress; docker `write_file` pre-read failure modes (oversized/non-UTF-8 old file still writes,
renders "new file"); **`--feedback ""` (or whitespace-only) is normalized to `None` at parse** —
treated as absent by the completed-run gate and the resume prompt AND recorded as `null` (making
`docs/transcript-schema.md:52`'s "null means a resume without feedback" true) — docstring + test;
non-UTF-8 `--feedback-file` → exit 2 test (behaviour already correct); explicit-`null`
`verify_rounds`/`verify_timeout` in a hand-edited `run.json` hardened at `__main__.py:700-702`
(`prior.get(k) if prior.get(k) is not None else default`) + test.

## 7. Cross-cutting

- **Contract (additive, `schema_version` 2):** tool `append_file` (eleven tools); flag
  `--max-tokens` (run + resume, inherited on resume, refused at ≥ context window);
  `run_start`/`run.json` gain `max_tokens`; `assistant` events gain `finish_reason`
  (`string | null`, open enum); the §1.3 recovery strings; `--provider ollama`. Nothing removed
  or renamed; the stdout payload is unchanged.
- **Image/version:** `DEFAULT_IMAGE` → `:0.10`; CI docker-live tag; every doc mention;
  `PINNED_DIGEST = None` (comment updated; pin in 0.10.1);
  `tests/test_docker_args.py::test_default_image_and_pinned_digest` **pins both literals** —
  update the tag and change the digest assertion to `PINNED_DIGEST is None` with a comment
  pointing at 0.10.1; version `0.10.0` in `pyproject.toml` + `dirtywork/__init__.py`.
- **Tests:** TDD; new tests in existing modules except the sanctioned new files
  (`tests/fixtures/providers/ollama/`, `tests/test_provider_ollama.py`,
  `tests/test_live_ollama.py`); full unit suite green on 3.9 after every task; docker parity
  suites updated per §2.6's counted churn.
- **Docs:** README, `docs/operating.md`, `docs/machine-contract.md`, `docs/security.md` (§2.4),
  `docs/transcript-schema.md`, `docker/README.md`.
- **Issue hygiene at ship:** #36, #40, #41, #42, #43, #47 close; #22's follow-up resolved; #48
  starts on the 0.10.0 release.
