# Review response: hardening, sandbox, extensibility

**Date:** 2026-08-15
**Status:** Approved design, pre-implementation
**Origin:** An external (ChatGPT) review of v0.2.0. Every claim in it was
verified against the code before this design was written; the verification
outcome is recorded per item so the plan doesn't chase phantoms.

## Purpose

Move dirtywork from "guardrailed harness for a confused model" to "sandboxed
harness that is also safe against a malicious model or repository", without
losing the properties that make it useful: worktree isolation, no auto-commit,
machine-readable output, a complete JSONL transcript, and zero runtime
dependencies. Then make it extensible enough that a new tool, a new provider,
or a benchmark run is one small addition rather than surgery on `tools.py` and
`runner.py`.

**Success criteria**

- The three reproduced denylist bypasses (`git -C ../.. config …`,
  `git -c … push`, `python3 -c 'open("/tmp/…","w")'`) are blocked *by the OS*
  in the default mode, with a live test proving it.
- A repository that ships a symlinked `CLAUDE.md` or `.worktrees` cannot make
  dirtywork read or write outside the repo.
- Every model- or repo-controlled input has an explicit bound.
- `dirtywork run` works unchanged for existing callers (same stdout contract,
  plus new fields).
- Adding a tool is one `ToolSpec`; adding a provider is one adapter class;
  `dirtywork bench` produces per-model completion/regression/token/latency
  numbers.

Three sub-projects, delivered in order as separate PRs, each keeping the
existing 185-test suite green.

---

## Sub-project 1: Hardening

Bounded fixes to existing flows. All verified real; two review items were
overstated and are handled minimally (noted inline).

| # | Finding | Verified | Change |
|---|---------|----------|--------|
| 1 | git rules don't tolerate global options (`git -C x config`, `git -c k=v push`) | Yes — plain forms are blocked; option forms slip past | Git denylist rules accept any run of `-C <arg>`, `-c <k=v>`, `--<flag>[=v]`, `-<x>` tokens between `git` and the subcommand. Both repros become tests. `python3 -c` writing outside stays *unblocked* — it is a shell, and the sandbox is the fix (documented). |
| 2 | `CLAUDE.md`/`AGENTS.md` read through symlinks, unbounded size (`workspace.py:load_repo_context`) | Yes — `is_file()` follows links | `lstat`; refuse symlinks and non-regular files; refuse > `MAX_READ_BYTES` (5 MB); truncate what is injected into the prompt to a `MAX_CONTEXT_CHARS` (32 000) with a marker. |
| 3 | `.worktrees` symlink unchecked (`workspace.py:create_worktree`) | Yes | Refuse if `<repo>/.worktrees` exists and is a symlink (or non-directory). After `git worktree add`, assert `worktree.resolve()` is inside `repo.resolve()/.worktrees`; on failure remove the worktree + branch and raise `WorkspaceError`. |
| 4 | HTTP error body read fully before slicing (`llm.py`) | Yes | `e.read(500)`. |
| 5 | Transcript opened with `open(path, "a")`, default perms | Yes | `os.open(path, O_WRONLY\|O_CREAT\|O_EXCL\|O_APPEND\|O_NOFOLLOW, 0o600)`; create `~/.dirtywork` and `runs/<slug>` with `0o700`. `O_EXCL` also turns a slug collision into a loud failure. |
| 6 | Missing provenance | Yes | `run_start` gains `base_commit` (HEAD of the new worktree), `branch`, `branch_from`, `base_url`, `dirtywork_version`, `temperature`, `argv` (task redacted to length). `run_end` gains `diff_stat` (`git diff --stat` in the worktree, capped). |
| 7 | Unbounded `write_file` content, `list_dir` rows, assistant text | Partly — all transitively bounded by the 64 MB response cap | `write_file` refuses content > `MAX_WRITE_BYTES` (5 MB, mirrors reads); `list_dir` stops after 2 000 entries with a marker; assistant `text` in the transcript is capped at 64 000 chars with a marker (the full text still goes back to the model). |
| 8 | `.git/info/exclude` is modified in the main checkout; docs don't say so | Yes | One sentence in README's Security section and in `SECURITY.md`. |
| 9 | 16-bit slug salt not a uniqueness guarantee | Overstated — `git worktree add -b` already fails on a branch collision | Bump to `token_hex(4)`; rely on #5's `O_EXCL` for the transcript side. |
| 10 | TOCTOU between `resolve()` and write | Real only against a process that survives between tool calls (needs `setsid`); malicious-model territory | Cheap partial: file-tool writes open the final component with `O_NOFOLLOW` (`os.open` + `os.fdopen`) and re-check `S_ISREG`. Full fix is the sandbox. |
| 11 | "Should not market itself as secure" | Not applicable — README/SECURITY.md say "not a sandbox" repeatedly | No change; keep it that way until sub-project 2 lands, then update wording to describe the sandboxed default accurately. |

**Testing:** each item gets a unit test in the existing test module for its
file (`test_guardrails_bash.py`, `test_workspace.py`, `test_llm.py`,
`test_transcript.py`, `test_tools_files.py`, `test_runner.py`). Symlink tests
use `tmp_path` fixtures with real symlinks and real `git init` repos, as the
existing workspace tests already do.

**Out of scope here:** anything that needs a process boundary. That's
sub-project 2.

---

## Sub-project 2: Docker sandbox backend

### Decision record

- **Docker first** (not Seatbelt): cross-platform including Windows, cgroup
  quotas for free, strongest boundary. `sandbox-exec` was verified working on
  macOS 26.6 (write and network denial) and remains a candidate later backend
  behind the same interface; it is not built now.
- **Image via `--image` with a maintained default** (`dirtywork/worker`,
  built from `docker/Dockerfile` in this repo: Debian-based, git, python3,
  node, .NET SDK, ripgrep). If the pull fails and the Dockerfile is present
  locally, build it once; otherwise error out with the pull error.
- **Sandboxed is the default; `--sandbox none` is the explicit opt-in** to
  today's host-shell behavior. No silent fallback: Docker missing or daemon
  down → preflight error, exit 2, hint printed.

### 1. What is jailed

Only the `bash` tool runs inside the container. File tools
(`read_file`/`write_file`/`edit_file`/`list_dir`/`grep`) stay host-side under
the existing path confinement (already bounded, and it keeps LM Studio
reachable from the host without `host.docker.internal` plumbing). The LLM
client never enters the container.

### 2. Container lifecycle

One container per run.

- **start** (before the first tool call, after the worktree exists):
  `docker run -d --name dw-<slug> <limits> <mounts> <env> <image> sleep infinity`
  (no `--rm`: removal is always explicit, so a timeout restart can't race
  auto-removal)
- **exec** per `bash` call: `docker exec -w /work dw-<slug> bash -c <command>`
  with stdout+stderr merged, drained on a thread with the same 1 MiB capture
  cap as today. On timeout: `docker restart -t 0 dw-<slug>` — SIGKILLs every process in
  the container and brings `sleep infinity` back (tmpfs `/tmp` is lost, which
  is acceptable on a timeout); the result reports the timeout as today's
  message does.
- **stop** at run end and in the CLI `finally`: `docker rm -f dw-<slug>`.
- A container left over from a crashed prior run with the same name is
  removed at start (names are slug-unique, so this only recovers from crashes).

Runs on any host where the `docker` CLI works — no `killpg`, no
`start_new_session`.

### 3. Mounts and environment

Fixed container paths only; no host path is ever visible inside the container
(this is what makes it work on Windows, where a worktree's `.git` file says
`gitdir: C:/…`).

| Host | Container | Mode |
|------|-----------|------|
| `<worktree>` | `/work` | rw |
| `<repo>/.git` (the common dir, from `git rev-parse --git-common-dir`) | `/repo.git` | **ro** |
| `<repo>/.git/worktrees/<name>` | `/repo.git/worktrees/<name>` | rw (worktree HEAD, index) |
| `<repo>/.git/objects` | `/repo.git/objects` | rw (content-addressed, additive) |
| tmpfs | `/tmp` | rw, `size=1g` |

Env for every exec: `GIT_DIR=/repo.git/worktrees/<name>`,
`GIT_WORK_TREE=/work`, `HOME=/work`, `PATH` from the image, `LANG=C.UTF-8`,
`TMPDIR=/tmp`. Nothing from the operator's environment.

Consequence: `git status/diff/log/add/stash-list` work; `git commit`,
`git config` (writes), `git push`, `core.hooksPath`, ref/tag/branch writes all
fail structurally (read-only), and there is no network. The denylist stops
being a security boundary; its rules stay as free accident guards and to keep
`guardrail_block` transcript events useful for review.

### 4. Limits and network

Defaults: `--network none`, `--memory 4g`, `--cpus 2`, `--pids-limit 512`,
`--read-only` root filesystem, `--cap-drop ALL`,
`--security-opt no-new-privileges`, `--user <uid>:<gid>` of the invoking user
on Linux hosts (Docker Desktop's mount layer handles ownership on macOS and
Windows; on those hosts the container user is the image's non-root default — the
`docker/Dockerfile` sets `USER worker` (uid 1000)).

CLI: `--sandbox docker|none` (default `docker`), `--image`, `--allow-network`
(switches to the default bridge network — needed for package restores),
`--memory`, `--cpus`. `run_start` records
`sandbox: {backend, image, network, memory, cpus, pids_limit}`.

### 5. Interface

```
class Sandbox(Protocol):
    def start(self, worktree: Path, repo: Path, slug: str) -> None: ...
    def exec(self, command: str, timeout: int) -> ExecResult: ...   # (exit_code|None, output: bytes, truncated: bool, timed_out: bool)
    def stop(self) -> None: ...
```

`DockerSandbox` and `HostSandbox` (wraps today's `bash()` body). The `bash`
tool becomes a thin function over `sandbox.exec` that formats `ExecResult` the
way today's output reads (`exit code: N\n…`, timeout message, truncation note),
so the model-facing contract does not change. `ToolExecutor` (and later the
registry, sub-project 3) receives the sandbox instance. `__main__` chooses the
backend in preflight (`docker version` must succeed for `docker`).

### 6. Docs

README Security section is rewritten to describe the sandboxed default
truthfully (what the container blocks, what `--sandbox none` gives up, and
that file tools remain host-side under path confinement). `SECURITY.md`
scope updates: escapes from the Docker sandbox are in scope; `--sandbox none`
keeps today's caveats. Note that Windows support is expected but unverified
until tested on a Windows host with Docker Desktop.

### 7. Testing

- Unit: `DockerSandbox` built on an injectable `_run(argv)`; tests assert the
  exact `docker run` argv (mounts, limits, env, name), the exec argv, the
  timeout kill path, and `stop` idempotency. `HostSandbox` reuses the existing
  `test_tools_bash.py` coverage.
- Live (`-m live`, needs Docker): a real container against a real temp repo:
  write inside `/work` succeeds and appears on the host; the three review
  bypasses fail (`python3 -c 'open("/etc/x","w")'` → permission error,
  `git config core.hooksPath x` → read-only error, `curl` → no network);
  `git status` works with the `GIT_DIR` mapping; timeout kills a `sleep 600`.

---

## Sub-project 3: Extensibility

### 1. Tool registry (`dirtywork/toolspec.py`)

```
@dataclass(frozen=True)
class ParamSpec: type: str; description: str = ""; default: Any = MISSING
@dataclass(frozen=True)
class ToolSpec:
    name: str; description: str
    params: dict[str, ParamSpec]; required: tuple[str, ...]
    fn: Callable[..., str]
    timeout_default: int | None = None; timeout_max: int | None = None
    runs_in_sandbox: bool = False
@dataclass(frozen=True)
class ToolResult: text: str; kind: Literal["ok", "error", "blocked"]
class ToolRegistry:
    def register(self, spec) -> None
    def schemas(self) -> list[dict]            # OpenAI wire shape
    def execute(self, name, args, *, deadline) -> ToolResult
```

`execute` validates `required` and JSON types against the spec (hand-rolled;
no `jsonschema` dependency), rejects unknown parameters, clamps `timeout` to
the run deadline and `timeout_max`, and returns `kind="blocked"` for
`BLOCKED:` results (the registry writes the `guardrail_block` transcript
event). Unknown tool → `KeyError` as today. The six tools become specs in
`tools.py`; `TOOL_SCHEMAS`, `ToolExecutor`, and the runner's ad-hoc
`except TypeError` go away. Adding a tool is one spec plus one function.

### 2. Provider adapters (`dirtywork/providers/`)

The runner keeps a provider-neutral history and never sees wire shapes:

```
@dataclass class ToolCall: id: str; name: str; arguments: dict | None; error: str | None
@dataclass class ChatResponse: text: str; tool_calls: list[ToolCall]; finish_reason: str | None; usage: dict
# history records: {"role": "system"|"user"|"assistant"|"tool", ...}
#   assistant: text + tool_calls; tool: tool_call_id + content
class Provider(Protocol):
    name: str
    def list_models(self) -> list[str]
    def context_window(self, model: str) -> int | None
    def chat(self, model, history, tools: list[ToolSpec-schemas], *, temperature, max_tokens, timeout) -> ChatResponse
```

- `OpenAICompatClient` (rename of `LMStudioClient`; alias kept for one
  release): the current `_valid_tool_call`/`_canonical_tool_call`/usage
  sanitizing moves here as deserialization.
- `AnthropicClient`: urllib, `ANTHROPIC_API_KEY` read host-side (bash env is
  unaffected — it is already stripped), system prompt as top-level `system`,
  assistant `tool_use` / user `tool_result` blocks, `/v1/models` for
  preflight, `usage.input_tokens/output_tokens` mapped to prompt/completion.
- `trim_messages` operates on the neutral history. `CONTEXT_WINDOWS` becomes
  per-provider defaults with `--context-window` as an override.
- CLI: `--provider openai|anthropic` (default `openai`), `--base-url` (default
  per provider). Transcript `run_start` records `provider`.

### 3. Transcript schema versioning

`schema_version: 2` on `run_start` and in the stdout JSON.
`docs/transcript-schema.md` documents each event (`run_start`, `assistant`,
`tool_result`, `guardrail_block`, `run_end`) and every field, with v1 = the
pre-hardening shape and v2 = v1 + provenance + `sandbox` + `provider`. At run
end `~/.dirtywork/runs/<slug>/run.json` is written with the stdout object so
tooling never has to parse the transcript.

### 4. Run inspection and cleanup (`dirtywork runs …`)

- `runs list` — slug, status, started, branch, worktree present (from
  `run.json` + `git worktree list --porcelain`).
- `runs show <slug>` — the `run.json` summary plus a tool-call timeline
  (tool, arg preview, result kind) from the transcript.
- `runs clean <slug> | --all [--keep-transcript]` — `git worktree remove
  --force`, `git branch -D dirtywork/<slug>`, delete the run dir; refuses to
  clean a run whose worktree has uncommitted changes unless `--force`.
- `runs verdict <slug> accept|reject|cleanup [--note …]` — appends
  `{verdict, note, ts}` to `run.json` so "% needing human cleanup" and
  "orchestrator rejection rate" are measurable.

### 5. Benchmark suite (`bench/`)

- `bench/repos/<name>/` — 3–4 tiny fixture repos committed as plain
  directories (each `git init`ed into a temp copy at bench time), each with a
  `bench.json` (`task`, `acceptance` command). Suggested: a Python package
  with a failing test; a Node function to implement against a Jest spec; a
  .NET class library with a failing xUnit test; a "read-only" repo whose task
  is to answer a question about the code.
- `dirtywork bench --models <m>[,<m>…] [--provider …] [--repeats N] [--tasks a,b]`
  runs each (model × task × repeat) through the normal `run` path in the
  sandbox, then runs `acceptance` in the same sandbox against the worktree.
- Results append to `~/.dirtywork/bench/<stamp>.jsonl`: model, task, repeat,
  status, turns, prompt/completion tokens, wall seconds, guardrail blocks,
  diff stat, acceptance pass/fail, run slug. `bench summarize <file>` prints
  completion rate, acceptance (regression) rate, mean tokens and latency per
  model.

### Sequencing

Registry → providers → schema/`run.json` → `runs` commands → bench. Registry
and providers each touch `runner.py`; the sandbox interface from sub-project 2
is consumed by the registry via `runs_in_sandbox`.

---

## Non-goals

- Seatbelt / bubblewrap backends (candidate later backends behind `Sandbox`).
- Auto-detecting devcontainer images.
- Any UI. `runs` and `bench` are CLI-only.
- Human-in-the-loop timing beyond `runs verdict`.
