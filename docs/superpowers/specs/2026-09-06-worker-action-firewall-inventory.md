# Worker Action Firewall: action and authority inventory

Date: 2026-09-06

Issue: [#134](https://github.com/JimboSchneider/dirtywork/issues/134), part of [#133](https://github.com/JimboSchneider/dirtywork/issues/133)

Audited baseline: `da9094d` (package version `0.13.1`)

Design: [Worker Action Firewall](2026-09-06-worker-action-firewall-design.md)

This is an inventory of current implementation behavior, not a new policy. It covers all eleven tools registered by the CLI, provider adapters, Runner bypasses, backend execution, and the evidence consumers affected by migration. Capability descriptions below are inputs to #135, **not approved enum names or additional permissions**. No runtime behavior changes in this issue.

## Ownership and reading the tables

- **Authority:** whether the worker may request an action. Today this is scattered across shell deny rules and file-target restrictions; there is no common authorization decision.
- **Containment:** where execution can affect state. Host path checks, Docker isolation, subprocess cleanup, and resource limits remain owned by their existing layers.
- **Validation/correctness:** argument shape, edit uniqueness, encoding, atomic write behavior, completion checks, and verification. Passing one of these does not confer authority.
- **Guidance:** instructions in the system prompt. They are not enforced merely because the prompt or a tool description contains them.

Source links are repository-relative; symbol names identify the audited implementation even when later edits move lines. Reproduce the baseline with `git show da9094d:<path>`, or run the commands at the end in a checkout of that revision.

## 1. Worker entry points and dispatch

Sources: [CLI](../../../dirtywork/__main__.py) (`_execute`, `build_system_prompt`), [registry](../../../dirtywork/toolspec.py) (`ToolRegistry`), [built-ins](../../../dirtywork/builtin_tools.py), [Runner](../../../dirtywork/runner.py) (`Runner.run`).

The CLI constructs `default_registry(transcript=...)` for runs and resumes. `BUILTIN_SPECS` supplies the schemas in registration order. Runner calls the selected provider, retains its addressable `ToolCall`s, attempts tool-name recovery, and handles parse/truncation failures before dispatch. Ordinary calls go through `ToolRegistry.execute -> ToolSpec.fn -> Sandbox method`; terminal calls take a separate Runner branch.

| Action surface / dispatch path | Current validation | Current authority rule | Containment owner | Proposed Firewall capability / policy owner | Compatibility notes |
| --- | --- | --- | --- | --- | --- |
| `read_file` | Registry string `path`; integer `offset=0`, `limit=400` (numeric strings accepted). Backend read and output limits. | Worktree path restriction; no blanket prohibition on reading `.git`. No bash guardrails. | Host `resolve_in_worktree` and regular-file open; Docker `_rel` and container execution. | Workspace read, with target classification owned by Firewall; actual file opening stays in backend. | Numbered lines and paging; read decodes invalid UTF-8 with replacement. Host follows an already-resolved in-worktree symlink for reading. Docker uses `head`, not the host regular-file opener. |
| `write_file` | Required string `path`, `content`; backend UTF-8 byte cap of 5 MiB. | File path restriction and root `.git` write prohibition; no bash guardrails. | Host atomic-write helpers; Docker `_write_raw` and `WRITE_SCRIPT`. | Workspace mutation and repository-control target classification, owned by Firewall. | Creates parents; existing write/diff results and backend-specific refusal text survive. Docker `_read_raw` rejects absolute/escaping `..` paths before any pre-read exec; its best-effort old-content read precedes the write-only root-`.git` refusal and size check in `_write_raw`. |
| `append_file` | Required string `path`, `text`; 5 MiB argument/read/result limits; existing regular UTF-8 file required. | File path restriction and root `.git` write prohibition; no bash guardrails. | Host no-follow probes/atomic helper; Docker `_append_guard`/`_append_write`. | Workspace mutation, including the read needed to append; Firewall owns requested authority. | Appends verbatim, adds no newline, creates no file or parents. Argument-size rejection precedes file inspection; existing-size and result-size checks follow. |
| `edit_file` | Required string `path`, `old_string`, `new_string`; backend exact-once match and strict UTF-8/read/result-size checks. | File path restriction and root `.git` write prohibition; no bash guardrails. | Both backends' `_transform_file`, with shared `_replace_once`. | Workspace mutation, with read/modify target represented in #135. | Exact-once matching and echoed change summary/diff are correctness contracts, not policy denials. |
| `apply_edits` | Required string `path`; `edits` array of 1–100 objects with exactly string `old` and `new`; recursive string-value input cap 2 MiB. Backend rejects empty `old`, checks uniqueness and 5 MiB read/result limits. | File path restriction and root `.git` write prohibition; no bash guardrails. | Both `_transform_file` implementations and shared `_apply_edits_once`. | Workspace mutation of one target; Firewall owns bounded request and policy. | Replacements execute in order against evolving text; first failed edit prevents the entire file write. This is one tool action, distinct from a provider batch. Direct Sandbox calls do not receive all registry bounds. |
| `insert_before` | Required string `path`, `anchor`, `text`; backend exact-once anchor, strict UTF-8/read/result limits. | File path restriction and root `.git` write prohibition; no bash guardrails. | Both `_transform_file` implementations and shared `_insert_once`/`insert_text`. | Workspace mutation, with read/modify target represented in #135. | Inserts whole lines before the line containing the anchor's first character, including multi-line anchors, without modifying the anchor text. |
| `insert_after` | Same required shape and checks as `insert_before`. | Same file authority checks; no bash guardrails. | Same transform helpers, with placement `after`. | Workspace mutation; Firewall owns authority and preserves operation identity. | Inserts after the line containing the anchor's last character, including multi-line anchors; must remain distinguishable from `insert_before` in canonical identity. |
| `list_dir` | String `path="."`; backend listing limit 2,000 entries; registry output cap. | Worktree path restriction; no bash guardrails. | Host `Path.iterdir`; Docker GNU `find` or fixed `sh`/`ls`/`wc` fallback commands. | Workspace read/list intent, owned by Firewall; backend option handling must uphold that intent (see section 4). | Name-sorted output, directory suffixes and file sizes; backend cap wording differs. Host materializes/sorts the directory before capping. At the audited baseline, Docker passes expression-like paths to `find` without disambiguation; the `./` starting-point fix is in [PR #145](https://github.com/JimboSchneider/dirtywork/pull/145) (pending merge). |
| `grep` | Required string `pattern`, `path="."`, string `glob=None` (validator accepts null; advertised schema says string); registry injects deadline-clamped hidden `timeout=30`. | Worktree path restriction; no bash guardrails. | Host `subprocess.run` with `rg`/`grep`; Docker fixed argv through `_run`. | Workspace search/read intent, owned by Firewall; current Docker path-to-option confusion can exceed that intent (see section 4). | Pattern passed with `-e`, glob as an argument, but the audited Docker path lacks an option terminator; [PR #145](https://github.com/JimboSchneider/dirtywork/pull/145) adds `--` for rg and fallback grep (pending merge). No model-visible timeout parameter; an extra top-level `timeout` is dropped. Search timeout does not increment the worker bash timeout counter. |
| `bash` | Required string `command`; `timeout=120` accepts integers/numeric strings and duration strings; registry max 600 and remaining deadline; backend clamps 1–600. | Ordered `check_bash_command`: all eight rules in host mode, four `always` rules in Docker. | Host `tools.bash -> run_capped`; Docker `DockerSandbox.bash`, container, watchdog, reaper/reset. | Shell/process authority, plus deterministically recognizable filesystem/network/repository/system-control requirements; vocabulary and uncertainty handling belong to #135–#137. | `Caps(fs="write", network=True)` is descriptive. Accepted command executes unchanged; regex checking is not a proof of shell effects. `exit code:`, timeout and `BLOCKED:` result contracts are observable. |
| `finish` | Adapter must produce a usable parsed object. Runner reads `summary` directly; missing/non-string summary becomes `""` on an ordinary non-truncated call. **No `ToolRegistry.execute` validation/caps/deadline check.** | No per-action policy gate; Runner checks completion/change/verification afterward. | Runner lifecycle and sandbox verify/finalize paths. | Completion request/run control, owned by Firewall before Runner acts; acceptance and verification remain Runner/orchestrator decisions. | Only built-in with `terminal=True`. Registry declares required string summary, but Runner deliberately bypasses it. Pending completion waits for other calls in the batch; all terminal results are later resolved together. |

### Common registry behavior to preserve or deliberately migrate

`_validate_args` drops unknown **top-level** keys, fills declared defaults, coerces numeric strings, rejects booleans for numeric parameters, and accepts explicit `None` only when the default is `None`. Nested `apply_edits` objects reject unknown keys. Duration coercion is enabled by `ParamSpec.unit`, not by a parameter's name. `_validate_against_schema` implements only the repository's small schema subset.

`Caps.max_input_bytes` is set only for `apply_edits`. `_input_bytes` counts UTF-8 string **values**, including `path` and nested replacements; keys and non-string scalars do not count. Shared HTTP transport caps the entire response at 64 MiB before JSON decoding, but there is no general per-action pre-parse payload/depth/count bound. That transport ceiling, backend 5 MiB file limits and provider output-token budgets are not replacements for a bounded action contract.

Registry ordering is: name lookup, validation, optional input cap, deadline refusal, timeout clamping, **executor call**, output truncation, then result-prefix classification. A caught `ToolValidationError` or executor `TypeError` yields `failure="bad_args"`; unknown names yield `failure="unknown_tool"`. Other executor exceptions propagate. A text result beginning `BLOCKED:` produces `kind="blocked", failure=None` and, when the registry has a transcript, a `guardrail_block` event. Other strings, including backend `ERROR:` refusals, are `kind="ok", failure=None`. Thus neither `kind="ok"` nor a reset of the failure counter proves authorization or execution success. The registry's blocked classification is evidence collected **after** executor handoff, not a pre-execution gate.

### Additional dispatch paths and control surfaces

| Surface / caller | Current checks and authority | Containment / correctness owner | Firewall migration consequence |
| --- | --- | --- | --- |
| Marker-polluted tool names | `ToolRegistry.recover_name` recognizes a registered suffix after the last usable raw/sanitized tool-call marker. Unknown suffixes stay unknown. Original `ToolCall` is retained for history. | Runner records capped original/effective names and at most one recovery nudge on a continuing turn. | #136 must deliberately place recovery before tool classification; a recovered `finish` currently takes the terminal bypass too. |
| Malformed or truncated addressable call | Runner returns an error without registry execution. `length` plus malformed args or missing required args gets truncation handling; a complete parseable call on a `length` turn still executes. | Failure/truncation counters and provider history belong to Runner. | Preserve structural responses and truncation-specific guidance; define the adapter-to-Firewall failure seam in #135/#138. |
| Plain-text completion | A nonempty answer with no tool calls can enter `check_verify` and finish. Think-only, empty, or text pretending to be a tool call is classified/nudged instead; it is not executed as a tool. | Runner change checks, verification and finalization. | Account for this completion route alongside `finish`; no worker command arguments originate here. Ownership of a canonical completion event remains a #135/#138 decision. |
| Operator `--verify` | `Runner.run`'s `run_verify` calls `sandbox.bash(self.verify, self.verify_timeout)` directly. Command comes from CLI/resume metadata, not the completion payload. Backend bash guardrails still apply. | Same sandbox environment, budget and process handling as worker bash; pass/fail is correctness. Worker-edited tests/build files can affect what it runs. | Distinguish trusted harness/operator invocation from worker action; do not accidentally require an untrusted dictionary or suppress existing backend controls. No registry `guardrail_block` is emitted here. |
| Workspace fingerprint | `changes.fingerprint` calls sandbox `bash` through `getattr` with fixed `FINGERPRINT_SCRIPT` and 60-second timeout. Runs at start/checkpoints/completion/finalization as Runner requires. | Sandbox executes the harness script; `changes.py` validates hashes/status/output bounds. | Harness measurement, not a worker tool; retain caller provenance. It bypasses registry and uses scratch filesystem/Git state, so it cannot be relabeled as an in-memory Firewall analysis. |
| Custom `ToolSpec` / embedded Runner | Public `register` accepts caller-supplied functions, params, caps and terminal flag; duplicate name rejected. Any spec's `fn` can execute arbitrary Python. The shipped CLI has no user-tool loader. | Trusted embedding application and the function it installs; passing a Sandbox does not force that function to use it. | Map any newly advertised tool through closed capability/policy registration. Do not assume `fs="none"` or `network=False` enforces anything. Custom terminal specs take Runner's terminal branch. |
| Direct `ToolRegistry.execute`, Sandbox methods, or `tools.*` calls | Callable Python APIs; direct calls do not traverse Runner. Sandbox/tools calls also bypass registry validation, caps and transcript classification. | Invoking harness/library and backend. Not separate advertised tools. | Document the trusted executor API seam and how worker requests enter it; no second normalization pass. Interpreter/import tricks launched by worker bash remain effects of that shell action, not extra advertised tools. |
| Start/resume, export, snapshot, cleanup and review CLI operations | Operator paths in `__main__.py`, `resume.py`, `runs.py`, `workspace.py` and sandbox lifecycle/export. Not entries in `BUILTIN_SPECS`. | CLI, export validator, Git/workspace code and orchestrator; shell can still attempt commands within its actual containment. | Do not classify these operator operations as worker-requested tools solely because they execute processes. Protect harness/repository targets when classifying worker effects; retain export/acceptance boundaries. |

## 2. Provider/tool-call adapter contracts

Sources: [neutral types](../../../dirtywork/providers/__init__.py), [HTTP transport](../../../dirtywork/llm.py), [OpenAI-compatible adapter](../../../dirtywork/providers/openai_compat.py), [Ollama adapter](../../../dirtywork/providers/ollama.py), [Anthropic adapter](../../../dirtywork/providers/anthropic.py).

Before adapter parsing, the shared `llm.http_json` enforces `MAX_RESPONSE_BYTES = 64 * 1024 * 1024` and a whole-transfer deadline, then decodes JSON. This is response-level resource protection; direct parser calls/custom provider implementations do not necessarily traverse it. It does not validate individual action authority or bound each nested field.

| Provider / input surface | Parse shape and current validation | Returned history / compatibility |
| --- | --- | --- |
| OpenAI-compatible (including LM Studio) | `choices[0].message.tool_calls[]`: nonempty string `id`, function object with nonempty string `name`; `arguments` absent/null or string. Missing/null/empty argument string becomes `"{}"`. JSON must decode to an object. Other structural failures become unaddressable entries (`id=""`), even if the original object contained an ID. Missing `type` is accepted. | Neutral `ToolCall(id, name, arguments, error, raw_arguments)`. A usable ID plus invalid JSON yields an addressable error. Resend uses `type="function"`, original name and raw argument string where present; one `role="tool"` result references `tool_call_id`. |
| Ollama | Inherits OpenAI-compatible parsing and serialization through `/v1`; native `/api/chat` shapes are not a separate supported action path. Provider-specific context discovery is outside tool dispatch. | Same ID/string-arguments/history contract. Fixtures test parallel parsing; source explicitly says live parallel-tool behavior is unverified. |
| Anthropic | `content[]` blocks with `type="tool_use"`; nonempty string `id`/`name`; `input` must be an object. Invalid ID/name is unaddressable; invalid/missing input with usable identity is an addressable error. Valid input gets `raw_arguments=json.dumps(input)`, not original wire bytes. | Neutral calls serialized back as `tool_use` with object `input`; `tool` messages become `tool_result` blocks in user messages, adjacent results are grouped, following user guidance is merged, empty assistant replies are omitted. Invalid input is resent as `{}`. |

Adapters parse provider syntax, not authority. They do not bound call IDs/names, raw argument length, object depth, extra keys or batch count, nor enforce unique IDs within a batch. OpenAI-compatible JSON decoding also does not establish a finite-number domain for arbitrary argument values. Unknown tool names can parse successfully. #135/#136 must define the bounded domain and deterministic failure handling before these values become policy/Supervisor input. Existing transcript name/argument truncation only limits selected output fields after parsing.

## 3. Ordered shell guardrail reasons

Source: [guardrails.py](../../../dirtywork/guardrails.py), `_RULES`, `_COMPILED`, `_rewrite_worktree_refs`, `check_bash_command`.

The exact first-match reason is observable through `guardrail_block.reason`. Host mode scans all rows in the order below. Docker scans rows **1, 2, 5, 6**, preserving that relative order. Each reason is wrapped exactly as:

```text
BLOCKED: {reason}. Rework the command to stay inside the worktree.
```

| Order | Scope | Exact reason | Matched authority / containment concern |
| --- | --- | --- | --- |
| 1 | always | sudo is not allowed | Privilege escalation policy. |
| 2 | always | git push is not allowed — leave changes uncommitted for review | Publishing repository changes; holds even with network enabled. |
| 3 | host | git command that writes the parent repo's shared refs/config is not allowed | Shared parent-repository control: non-read `config`; remote add/set-url/remove/rm/rename; update-ref/gc/filter-branch; reflog expire/delete; worktree add/remove/prune/move; branch delete/move forms; tag delete forms. |
| 4 | host | destructive command targeting a path outside the worktree | `rm`, `mv`, `chmod`, `chown` with recognized absolute/home/parent escape targets or recognized operator toolchain-root variables. |
| 5 | always | piping a download into an interpreter is not allowed | Recognized curl/wget pipeline into shell/Python/Node/Ruby/Perl; not a general network prohibition. |
| 6 | always | system-control commands are not allowed | osascript/launchctl/shutdown/reboot/killall. |
| 7 | host | redirecting output outside the worktree is not allowed | Recognized `>`/`>>` escape targets, including operator toolchain-root variables, with `/dev/null` exception. |
| 8 | host | changing directory out of the worktree is not allowed | Recognized `cd`/`pushd` escape targets, including operator toolchain-root variables. |

All regexes are case-insensitive; Git patterns account for preceding global options. When `sandboxed=False` **and a worktree is supplied**, absolute references to the supplied/resolved worktree root are rewritten to `.` **only in the checked string**, respecting root boundaries. The original command executes. Docker skips this rewrite entirely. Exact regex behavior, including false positives and escape-target limits, lives in `_RULES`; this table does not broaden the patterns.

The redirected `$HOME` is deliberately not among the operator toolchain roots matched by `_HOME_ESCAPE_TARGET` in rules 4, 7 and 8 (destructive commands, redirects and directory changes). The roots are `VOLTA_HOME`, `RUSTUP_HOME`, `CARGO_HOME`, `NVM_DIR`, `PYENV_ROOT`. Ordinary `$BUILD_DIR`-style variables are not blanket-blocked. Read-only Git config/remote/worktree/reflog forms, relative writes, ordinary downloads, and `git add`/`git commit` may pass. Interpreter/file effects and obfuscated commands are not exhaustively analyzed. Migrate this contract with #140 parity fixtures; do not promote regex coverage into a containment guarantee.

System-prompt rules in `build_system_prompt` separately say to use file tools for edits, avoid bash file writes, and normally avoid commits/branch commands. `--allow-commit` changes prompt guidance (host mode only); it does **not** toggle a `git commit` deny rule. Those guidance-only rules require an explicit later policy decision if they are to become denials.

## 4. Backend ownership and mode differences

Sources: [host tools](../../../dirtywork/tools.py), [HostSandbox](../../../dirtywork/sandbox/host.py), [DockerSandbox](../../../dirtywork/sandbox/docker.py), [Docker arguments](../../../dirtywork/sandbox/docker_args.py), [process handling](../../../dirtywork/procs.py), [OS file handling](../../../dirtywork/osfs.py), [watchdog](../../../dirtywork/sandbox/watchdog.py), [export](../../../dirtywork/sandbox/export.py).

| Boundary | `--sandbox none` | Docker | Consequence for the Firewall |
| --- | --- | --- | --- |
| File-target containment | `resolve_in_worktree` resolves symlinks and requires effective target inside worktree; absolute paths **inside** are accepted. `writing=True` rejects root `.git` paths. Final-component no-follow probes protect writes; actual open/replace/race handling remains in `tools.py`/`osfs.py`. | `_rel` is lexical POSIX normalization: reject absolute paths and surviving `..`, reject root `.git` only for writes. It does not resolve symlinks. Actual commands operate in the container with private `/gitdir` and `/work` volume. | Do not impose identical lexical path behavior on both modes accidentally. File-state containment checks are not a second worker-argument canonicalization. Nested `.git` and symlink targets need explicit authority treatment in later issues. |
| Static execution | Nine file/search tools call host helpers without `check_bash_command`; grep uses argv and inherited process environment, unlike sanitized worker bash. | All nine use Docker execs (including fixed shell scripts), never `DockerSandbox.bash`, so they also bypass `check_bash_command`. `_read_raw` uses bounded `head`; unlike host read it has no identical no-follow/regular-file probe. | All nine must cross the worker-action gate. Capability mapping must account for backend behavior, including the option-confusion gap below; advertised static intent alone does not prove read-only effects. |
| Shell/process boundary | General host `bash -c`; `build_env` relocates HOME, filters env, preserves toolchain roots/user-site support. `run_capped` limits capture and handles child cleanup. This is not OS filesystem/network isolation. | General container bash with file-size ulimit; non-root user, read-only rootfs, dropped capabilities, no-new-privileges, PID/memory/CPU/tmpfs limits. Post-bash process reaping may reset the container; working files survive but private Git state may not. | Shell uncertainty cannot be resolved by inventing permission from the sandbox mode. Preserve independent containment; deterministic policy must describe what it knows and leave `semantic_unknown` semantics to the approved design. |
| Network | Host shell has host networking; no generic network gate. `Caps.network` is metadata. | `--network none` by default; operator `--allow-network` selects `bridge`. Not a per-tool `Caps` enforcement. | Separate requested network authority from network availability. Plain downloads are not rejected by the legacy download-pipeline rule. |
| Resource/size enforcement | Registry deadline gates ordinary dispatch; backend file limits; HostSandbox checks worktree budget **after** mutation/bash, and measures/sweeps at lifecycle points. | Registry deadline plus backend exec timeouts; watchdog disk/worktree/resource checks, bash reaping/reset and export bounds. | Resource failures can follow side effects. Do not relabel them as Firewall DENY, whose contract requires no denied-action executor side effects. |
| Git and export | Linked worktree shares parent refs/config/objects; host-only shell rules protect selected operations. | Worker Git directory is private; host-scoped shell rules skipped. Export validates entries/paths/types/size and drops worker `.git` entries before host materialization. | Preserve mode distinctions and validation/export ownership. A policy allow cannot waive export checks or make worker commits equivalent to accepted host changes. |

**Concrete static-tool gap: option-like paths.** Docker `_rel` accepts `-delete` and `--pre=./helper` as relative paths. `list_dir` passes its path to GNU `find` without a safe path prefix; `grep` appends its path to `rg` without `--`. Capturing `_run` arguments with an inert recorder (no subprocess execution) produces these command tails:

```text
list_dir(path="-delete"):
/usr/bin/find -delete -mindepth 1 -maxdepth 1 -printf <format>

grep(pattern="x", path="--pre=./helper"):
/usr/bin/rg -n --no-heading -M 300 -e x --pre=./helper
```

These are option/expression positions: GNU find's `-delete` can mutate files, and ripgrep's `--pre` selects a preprocessor process. The reproduced fact is the emitted argv; no destructive command was run for this inventory. The same effects are already reachable through Docker bash under its four always-scoped rules, so this is an advertised-intent/evidence-integrity gap (`fs="read"`, `kind="ok"`, no guardrail event), not a new containment escape. The host grep path is resolved to an absolute path first, so it does not have this specific relative-path ambiguity. The concrete operand fix is implemented separately in [PR #145](https://github.com/JimboSchneider/dirtywork/pull/145) (pending merge), with regression coverage: prefix GNU find starting points with `./` and terminate rg/fallback-grep options with `--`. GNU find still recognizes `-delete` after `--`, so the prefix is necessary. #139/#142 still need to reconcile canonical targets, executor argv semantics and policy replay; simply labeling tools read-only or validating `path` as a string is insufficient. This inventory retains the audited-baseline finding and does not change executors or assign new worker permissions.

## 5. Runner results, batches, and evidence contracts

Sources: [Runner](../../../dirtywork/runner.py), [Transcript](../../../dirtywork/transcript.py), [transcript schema](../../transcript-schema.md), [machine contract](../../../dirtywork/contract/machine-contract.md), [run reader](../../../dirtywork/runs.py), [benchmark reader](../../../dirtywork/bench.py).

1. **Ordinary batches are sequential, not atomic.** Every processed addressable call receives `tool_message(tc.id, result)`. A blocked result has no failure strike and resets the failure counter, so a denial normally does not cancel later calls. Effects from earlier calls are not rolled back. A `finish` anywhere in the batch waits for the rest; the last encountered summary becomes the pending final summary.
2. **Early run exits differ from continued history.** Malformed unaddressable entries are counted first; they get transcript error rows with empty tool/args, but no tool message or executor call. Reaching an abort threshold can stop before addressable calls are handled. An addressable failure threshold stops after that call's error result. `BudgetExceeded`/`SandboxError` can exit before a current call's structural result, and later calls are not filled with placeholders. #138 must deliberately handle Firewall failures/results rather than assume these paths already satisfy the new every-addressable-denial invariant.
3. **Finish is provisional until completion is accepted.** Runner stores a provisional result, runs change/verification checks, then replaces all terminal results with the completion/refusal text. `finish` transcript mode is `full`. Verification feedback is delivered through finish results or a user message for plain completion. A warning/nudge delivered after a batch cannot have been seen by later calls within that same batch.
4. **Name/argument history is distinct from policy evidence.** Assistant transcript records cap text at 64,000 chars, each raw argument string at 2,000, and tool names head/tail at 120 + ellipsis + 80 when over 200. Ordinary `tool_result` rows carry effective `tool`, raw `args[:500]`, usually `result[:2000]`, and sparse `tool_raw`, `timed_out`, `follow_up` fields. Original calls remain in provider history. There is no per-call ID/turn field in the existing tool-result row; consumers reconstruct order from assistant/result events.
5. **Guardrail events are neither complete nor hardened policy events.** When a transcript is configured, registry emits `guardrail_block` only for a `BLOCKED:` executor result, recording `tool`, validated `call_args` as `args`, and full blocked result as `reason`. File/path `ERROR:` refusals and direct sandbox verification/fingerprint calls do not get this event. Its args are not a closed bounded Firewall representation. Preserve existing reason prose/order during migration, while introducing separate stable codes.
6. **Current progress identity is not authorization identity.** `canonical_args` is called after handling the tool for `ProgressTracker`; it drops timeout/unknown keys, fills defaults, normalizes stripped paths and strips commands, without proving the nested representation. Bash repetition fingerprints additionally remove volatile output details. #136 must not reuse these as an exact policy identity without reconciling different normalization and execution semantics.
7. **Run evidence remains additive.** Transcript buffering flushes a turn together, including on exceptions; hard kills can lose the active buffered turn. `run_end`, CLI stdout and `run.json` carry status, usage, export/finalization, `files_changed`, `last_tool_result`, `last_assistant_text`, verify, timeout/truncation and change evidence. `last_tool_result` excludes finish and bounds args/result at 500/2,000; `runs show`/Markdown, resume and bench consume these contracts. Existing schema version is 2; consumers ignore unknown additive fields/events. A separate bounded/versioned Firewall event for #141 must not forward these raw dictionaries to Supervisor or create per-ALLOW log spam.

## 6. Explicit gaps handed to downstream issues

These are implementation gaps and decisions to resolve under the approved design, not policies authorized by this inventory.

| Gap | Owner / dependency |
| --- | --- |
| No closed capability/reason/status/event schema or per-action size/depth/call-count/identity bounds; the 64 MiB response ceiling and registry cap fields are not an authority model. | #135, using every row above; adapter protection and payload handling continue in #136. |
| Multiple partial normalizations (provider parse, name recovery, registry coercion, progress identity, backend path handling) are not one authoritative canonical action. Whitespace, timeout and mode-specific path behavior must not silently change. | #136, then #140 parity. |
| Nine static tools and terminal completion have no common policy boundary. Some Docker mutations read before the write-only root-`.git` check (absolute/escaping `..` paths are rejected before read exec); a policy gate inserted only at final write is too late to prevent a denied request reaching an executor. | #137–#139. |
| At the audited baseline, Docker listing/search paths can become `find` expressions or `rg` options, allowing side effects beyond advertised read-only intent without bash guardrails. | Concrete operand fix: [PR #145](https://github.com/JimboSchneider/dirtywork/pull/145), pending merge. #139 must still reconcile canonical target handling with executor argv semantics; #142 needs policy replay cases proving the boundary. |
| Current post-execution `BLOCKED:` classification cannot supply a fail-closed authorization decision. Unexpected exceptions are not converted into a per-call Firewall error; direct library use lacks even CLI's generic failure wrapper. | #137/#138. |
| Guidance-only rules, root versus nested `.git`, symlink-aware authority, dynamic/custom tools, unknown tools and shell semantic uncertainty need explicit decisions. Do not fill these gaps by broadening regexes or claiming static metadata is enforced. | #135–#137/#139/#140. |
| Current ordinary batch behavior is useful but early exits do not guarantee results for every advertised call; finish and plain completion both trigger indirect execution. | #138; preserve invocation provenance for verify/fingerprint. |
| Guardrail reason text/order and mode differences are public evidence; file refusals use different prefixes and do not emit guardrail events. | #140 parity and #141 additive evidence. |
| Supervisor must consume Firewall-owned capability set, reason code, canonical identity and semantic status. Current raw arguments, progress fingerprints and result-prefix interpretation are unsuitable. | #141, unblocking Runtime Supervisor #126; `semantic_unknown` alone never escalates. |
| General host-shell effects and container symlink/file-state behavior are outside deterministic string analysis; resource checks can happen after side effects. | Retain Sandbox/Watchdog/export ownership; #142 tests the actual boundary and #143 handles rollout. |

## 7. Reproduction and verification map

Run from the repository root. This metadata probe executes no tool functions, makes no model/network/Docker calls, and enumerates every advertised tool in registration order:

```bash
python3 - <<'PY'
from dirtywork.builtin_tools import default_registry
from dirtywork.guardrails import _RULES
from dirtywork.toolspec import MISSING

registry = default_registry()
print('| Tool | Parameters (required/default) | fs | network | input bytes | terminal |')
print('| --- | --- | --- | --- | --- | --- |')
for name in registry.names():
    spec = registry.spec(name)
    params = []
    for key, param in spec.params.items():
        suffix = ' required' if key in spec.required else ''
        if param.default is not MISSING:
            suffix += f' default={param.default!r}'
        params.append(f'{key}:{param.type}{suffix}')
    caps = spec.caps
    print(f'| {name} | {"; ".join(params)} | {caps.fs} | {caps.network} | '
          f'{caps.max_input_bytes} | {spec.terminal} |')
print('\nOrdered guardrail reasons:')
for index, (scope, reason, _pattern) in enumerate(_RULES, 1):
    print(index, scope, reason)
PY

rg -n 'default_registry|BUILTIN_SPECS|\.execute\(|spec\.fn|spec\.terminal' dirtywork
rg -n 'check_bash_command|resolve_in_worktree|def _rel|def fingerprint|self\.sandbox\.bash' dirtywork
rg -n 'subprocess\.|run_capped\(|self\._run\(|exec_argv\(' dirtywork
```

The first tool sequence must be `read_file, write_file, append_file, edit_file, apply_edits, insert_before, insert_after, list_dir, grep, bash, finish`, matching the eleven rows in section 1. New registrations require inventory review, not just a count check. The process search also includes operator/harness execution: classify those callers as in the additional-path table rather than counting every exec as a new worker tool.

| Evidence area | Existing executable coverage to preserve |
| --- | --- |
| Advertised tool completeness and dispatch | [test_builtin_tools.py](../../../tests/test_builtin_tools.py): frozen [tool_schemas.json](../../../tests/fixtures/tool_schemas.json), only terminal spec, each wrapper's dispatch, hidden grep timeout, caps, blocked-event recording. |
| Validation/coercion/recovery | [test_toolspec.py](../../../tests/test_toolspec.py): unknown top-level keys, scalar/duration coercion, nested shapes/input bounds, timeout/deadline, name recovery, exceptions and transcript modes. |
| Ordered policy and path rules | [test_guardrails_bash.py](../../../tests/test_guardrails_bash.py), [test_guardrails_paths.py](../../../tests/test_guardrails_paths.py): allow/deny examples, host/Docker scope and first-reason order, worktree rewrites, symlinks and `.git`; #140 adds migration parity without changing the originals. |
| Static file operations and backends | [test_tools_files.py](../../../tests/test_tools_files.py), [test_sandbox_host.py](../../../tests/test_sandbox_host.py), [test_docker_sandbox.py](../../../tests/test_docker_sandbox.py), [test_osfs.py](../../../tests/test_osfs.py): file limits, target restrictions, transform atomicity, append refusal order, backend calls. |
| Provider structures and batches | [test_llm.py](../../../tests/test_llm.py) covers response-size/deadline protection; [provider_contract.py](../../../tests/provider_contract.py), [test_provider_openai.py](../../../tests/test_provider_openai.py), [test_provider_anthropic.py](../../../tests/test_provider_anthropic.py), [test_provider_ollama.py](../../../tests/test_provider_ollama.py), [provider fixtures](../../../tests/fixtures/providers) cover malformed/addressable calls, parallel parse and ordered ID-bearing results. Fixture success is not live-provider certification. |
| Runner completion/history/evidence | [test_runner.py](../../../tests/test_runner.py), [test_transcript.py](../../../tests/test_transcript.py), [test_transcript_schema.py](../../../tests/test_transcript_schema.py), [test_main.py](../../../tests/test_main.py): terminal batches, parse/truncation paths, buffered finish results, follow-up delivery and CLI failure/finalization. |
| Independent containment and evidence consumers | [test_procs.py](../../../tests/test_procs.py), [test_tools_bash.py](../../../tests/test_tools_bash.py), [test_budget.py](../../../tests/test_budget.py), [test_watchdog.py](../../../tests/test_watchdog.py), [test_strays.py](../../../tests/test_strays.py), [test_docker_args.py](../../../tests/test_docker_args.py), [test_export_validator.py](../../../tests/test_export_validator.py), [test_export_flow.py](../../../tests/test_export_flow.py), [test_changes.py](../../../tests/test_changes.py), [test_runs.py](../../../tests/test_runs.py), [test_resume.py](../../../tests/test_resume.py), [test_bench.py](../../../tests/test_bench.py). |

Run the existing default suite in an environment with pytest:

```bash
python3 -m pytest -q -p no:cacheprovider
```

`pyproject.toml` excludes live model, Docker and Ollama tests by default. Passing this suite validates the existing deterministic contracts; it does not claim the Firewall is implemented or that the documented gaps are covered by new security tests. For this documentation-only change, verify the table against registry output, source call sites, ordered reasons and local links, and confirm the diff changes only documentation.
