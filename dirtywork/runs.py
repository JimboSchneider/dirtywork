"""`dirtywork runs ...` — inspect and clean up finished runs (spec SP3 section 4).

Everything here reads what a run left behind (`~/.dirtywork/runs/<slug>/`:
`run.json`, `transcript.jsonl`, `diff.patch`) plus, best effort, the docker and
git state around it. Nothing in this module ever starts a model run, and no
docker/git failure here is fatal to the command as a whole: a run directory is
the source of truth, the rest is decoration.

RUNS_DIR is read through the `rundir` module (`rundir.RUNS_DIR`) rather than
imported by value, so tests can point it at a tmp_path.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import rundir
from .resume import (ResumeError, find_stashes, load_prior_run, pid_alive,
                      preflight_run_worktree, stash_dir_for, worktree_belongs_to_repo)
from .sandbox import docker_args, docker_cli, export
from .workspace import WorkspaceError, host_worktree_dirty, snapshot_worktree

COLUMN_GAP = "  "
LIST_COLUMNS = ("slug", "status", "started", "resumed", "branch", "worktree",
                "container", "volume")
SHOW_FIELDS = ("slug", "status", "sandbox", "task", "model", "provider",
               "context_window", "turns",
               "resumed_from", "resumed_by", "branch", "worktree", "started", "ended",
               "stuck_on", "files_changed", "verify", "trimmed_turns")
TASK_PREVIEW_CHARS = 200


class RunsError(Exception):
    """A `runs` subcommand refusal that maps to exit 2 (bad slug, unreadable
    run.json, a run this command cannot act on)."""


def format_table(columns, rows) -> str:
    """Fixed-width table: upper-case header, one line per row, every column
    padded to its widest cell. Shared with `dirtywork bench summarize` so both
    CLIs render identically."""
    widths = {c: max([len(c)] + [len(str(r.get(c, ""))) for r in rows]) for c in columns}
    lines = [COLUMN_GAP.join(str(c).upper().ljust(widths[c]) for c in columns).rstrip()]
    for row in rows:
        lines.append(COLUMN_GAP.join(str(row.get(c, "")).ljust(widths[c]) for c in columns).rstrip())
    return "\n".join(lines)


def _iter_run_dirs(runs_dir: Path):
    runs_dir = Path(runs_dir)
    if not runs_dir.is_dir():
        return
    for d in sorted(runs_dir.iterdir()):
        if d.is_dir() and (d / "run.json").exists():
            yield d


_SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_DOCKER_ABSENT_RE = re.compile(r"no such (?:object|container|volume)\b", re.IGNORECASE)


def _run_dir_for(slug: str) -> Path:
    """`<RUNS_DIR>/<slug>` for a plain slug ONLY. A slug is data from the
    command line (or a results file); it must never be able to name a path
    outside RUNS_DIR (`../x`, `/etc`, `.`), so `runs clean --force <slug>`
    can only ever operate on a managed run directory."""
    if not _SLUG_RE.fullmatch(slug) or slug in (".", ".."):
        raise RunsError(f"invalid run slug '{slug}'")
    runs_dir = Path(rundir.RUNS_DIR)
    run_dir = runs_dir / slug
    try:
        if run_dir.resolve().parent != runs_dir.resolve():
            raise RunsError(f"invalid run slug '{slug}'")
    except OSError as e:
        raise RunsError(f"cannot resolve run '{slug}': {e}")
    return run_dir


def _existing_run_dir(slug: str) -> Path:
    """`_run_dir_for(slug)`, but also requires the directory to exist — the
    'no such run' RunsError every single-run subcommand raises, worded
    identically everywhere (including `cmd_snapshot`, which can't use
    `_open_run` below because it needs `resume.load_prior_run`'s stricter
    validation instead of `_open_run`'s bare "is a dict")."""
    run_dir = _run_dir_for(slug)
    if not run_dir.is_dir():
        raise RunsError(f"no such run '{slug}' under {rundir.RUNS_DIR}")
    return run_dir


def _open_run(slug: str):
    """(run_dir, run.json dict) or RunsError — the one lookup every single-run
    subcommand uses, so 'no such run' reads identically everywhere."""
    run_dir = _existing_run_dir(slug)
    try:
        data = rundir.read_run_json(run_dir)
    except (OSError, ValueError) as e:
        raise RunsError(f"cannot read run.json for '{slug}': {e}")
    if not isinstance(data, dict):
        raise RunsError(f"run.json for '{slug}' is not a JSON object")
    return run_dir, data


def _docker_state():
    """(container_states: dict[name, state], volume_names: set[str]), both
    best effort: any docker failure yields empty results so the command still
    prints every run instead of dying on a missing daemon."""
    containers, volumes = {}, set()
    try:
        cp = docker_cli.run(["ps", "-a", "--format", "{{.Names}}\t{{.State}}",
                             "--filter", "label=dirtywork.run"], timeout=docker_cli.T_QUERY)
        if cp.returncode == 0:
            for line in cp.output.decode("utf-8", errors="replace").splitlines():
                if "\t" in line:
                    name, state = line.split("\t", 1)
                    containers[name.strip()] = state.strip()
    except Exception:
        pass
    try:
        cp = docker_cli.run(["volume", "ls", "--format", "{{.Name}}",
                             "--filter", "label=dirtywork.run"], timeout=docker_cli.T_QUERY)
        if cp.returncode == 0:
            volumes = {ln.strip() for ln in cp.output.decode("utf-8", errors="replace").splitlines()
                       if ln.strip()}
    except Exception:
        pass
    return containers, volumes


def _worktree_present(repo, worktree):
    """True/False if git could be asked, None if it could not."""
    if not repo or not worktree:
        return None
    try:
        cp = subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"],
                            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if cp.returncode != 0:
        return None
    paths = [ln.split(" ", 1)[1] for ln in cp.stdout.splitlines() if ln.startswith("worktree ")]
    try:
        resolved = {str(Path(p).resolve()) for p in paths}
        return str(Path(worktree).resolve()) in resolved
    except OSError:
        return None


def _resumed_mark(data: dict) -> str:
    """How `runs list` marks a run that is part of a resume chain: `from <slug>`
    for a resumed run, `by <slug>` for one that was later resumed, both when a
    run sits in the middle of a chain."""
    marks = []
    if data.get("resumed_from"):
        marks.append(f"from {data['resumed_from']}")
    if data.get("resumed_by"):
        marks.append(f"by {data['resumed_by']}")
    return ", ".join(marks) if marks else "-"


def cmd_list(args) -> int:
    containers, volumes = _docker_state()
    rows = []
    for run_dir in _iter_run_dirs(rundir.RUNS_DIR):
        slug = run_dir.name
        try:
            data = rundir.read_run_json(run_dir)
            if not isinstance(data, dict):
                raise ValueError("run.json is not a JSON object")
        except (OSError, ValueError) as e:
            rows.append({"slug": slug, "status": "?", "started": "?", "resumed": "?",
                         "branch": "?", "worktree": "?", "container": "?", "volume": "?",
                         "error": f"unreadable run.json: {e}"})
            continue
        present = _worktree_present(data.get("repo", ""), data.get("worktree", ""))
        container_name = data.get("container")
        volume_name = data.get("volume")
        rows.append({
            "slug": slug,
            "status": data.get("status", "?"),
            "started": data.get("started", "?"),
            "resumed": _resumed_mark(data),
            "resumed_from": data.get("resumed_from"),
            "resumed_by": data.get("resumed_by"),
            "branch": data.get("branch", "?"),
            "worktree": "?" if present is None else ("yes" if present else "no"),
            "container": containers.get(container_name, "-") if container_name else "-",
            "volume": ("present" if volume_name in volumes else "absent") if volume_name else "-",
        })
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("no runs found")
        return 0
    print(format_table(LIST_COLUMNS, rows))
    return 0


def _uid_gid():
    """Same rule DockerSandbox.start uses: the invoking user on POSIX, the
    image's baked-in worker uid elsewhere."""
    return (os.getuid(), os.getgid()) if os.name == "posix" else (1000, 1000)


def _export_status_update(previous: str, export_status: str) -> str:
    """What `status` becomes after a re-export, mirroring `__main__._final_status`:
    an export result only ever replaces a status that was ABOUT the export (or a
    run left marked 'running' by a crash). A run that ended `budget_exceeded` or
    `timeout` keeps that status — the export is not why it ended."""
    if export_status == "ok":
        return "completed" if previous in (None, "", "running", "export_failed") else previous
    return "export_failed" if previous in (None, "", "running", "completed") else previous


def _summary_value(key: str, data: dict) -> str:
    value = data.get(key)
    if value is None or value == "" or value == []:
        return "-"
    # Structured end-of-run evidence: the plain view shows the one thing an
    # operator scans for, not the whole object (the JSON dump below has it all).
    if key == "stuck_on" and isinstance(value, dict):
        return str(value.get("command") or "-")
    if key == "files_changed" and isinstance(value, list):
        head = ", ".join(str(p) for p in value[:3])
        tail = ", ..." if len(value) > 3 else ""
        return f"{len(value)} ({head}{tail})"
    if key == "verify" and isinstance(value, dict):
        state = "passed" if value.get("passed") else "failed"
        return f"{state} (exit {value.get('exit_code')})"
    if key == "context_window":
        # 0.9: the number alone cannot be read -- 32768 may be the model's real
        # window or the fallback nobody chose. The source says which. A run.json
        # written before 0.9 has no source and renders the bare number.
        source = data.get("context_window_source")
        return f"{value} ({source})" if source else str(value)
    text = str(value)
    if key == "task" and len(text) > TASK_PREVIEW_CHARS:
        text = text[:TASK_PREVIEW_CHARS].replace("\n", " ") + " ... (full text below)"
    return text.replace("\n", " ") if key == "task" else text


def read_transcript_events(path) -> tuple:
    """(events, error) -- the one transcript parser in this module. Both
    renderers of `runs show` (the text timeline and the Markdown document) read
    the file through here, so what counts as an event is decided once. A missing
    transcript is not an error (a run that died in preflight never wrote one); an
    unreadable one yields no events plus the message to report."""
    path = Path(path)
    if not path.is_file():
        return [], None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [], str(e)
    events = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events, None


def _tool_result_outcome(result_text) -> str:
    """ERROR / BLOCKED / ok, from the tool result's leading token -- the one
    classification both the text timeline and the Markdown export use."""
    text = str(result_text or "")
    if text.startswith("ERROR"):
        return "ERROR"
    if text.startswith("BLOCKED"):
        return "BLOCKED"
    return "ok"


def _timeline_line(event: dict) -> str:
    ts = event.get("ts", "")
    name = str(event.get("event", ""))
    if name == "tool_result":
        result = str(event.get("result", ""))
        outcome = _tool_result_outcome(result)
        tool = event.get("tool") or "(malformed call)"
        return f"{ts}  {name:<15} {tool:<12} {str(event.get('args', ''))[:80]:<80} [{outcome}]"
    if name == "assistant":
        tools = ",".join(str(tc.get("name")) for tc in (event.get("tool_calls") or [])
                         if isinstance(tc, dict))
        return f"{ts}  {name:<15} " + (f"tools: {tools}" if tools else "text reply")
    if name == "nudge":
        return f"{ts}  {name:<15} kind={event.get('kind', '')} turn={event.get('turn', '')}"
    if name == "guardrail_block":
        return f"{ts}  {name:<15} {event.get('tool', '')}: {str(event.get('reason', ''))[:120]}"
    if name == "sandbox_reset":
        return f"{ts}  {name:<15} {str(event.get('reason', ''))[:120]}"
    if name == "run_end":
        return f"{ts}  {name:<15} status={event.get('status', '')} turns={event.get('turns', '')}"
    return f"{ts}  {name}"


MD_HEADER_FIELDS = ("status", "task", "model", "provider", "context_window", "sandbox",
                    "turns", "base_commit", "branch", "worktree", "resumed_from",
                    "resumed_by")
MD_VERDICT_FIELDS = ("verdict", "note")
# `trimmed_turns` (0.9) is an int that is meaningful at 0, and _md_result's loop
# prints anything not None/"" -- so it renders "0" rather than disappearing,
# which is the point: "nothing was trimmed" is a fact worth reading.
MD_RESULT_FIELDS = ("status", "error", "export_status", "finalize_error",
                    "watchdog_violation", "trimmed_turns")
MD_ARGS_CHARS = 200      # the transcript already caps `args` at 500
MD_RESULT_CHARS = 2000   # the transcript's own `preview` cap for a tool result


def _md_trim(value, limit: int) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + " ... [truncated]"


def _md_fence(text: str) -> str:
    """A fence longer than any backtick run inside `text` -- a tool result (or a
    diff of a Markdown file) may itself contain a fence and would otherwise close
    the block early."""
    longest = max([len(m) for m in re.findall(r"`+", text)] or [0])
    return "`" * max(3, longest + 1)


def _md_block(text: str, lang: str = "") -> list:
    fence = _md_fence(text)
    return [f"{fence}{lang}", text, fence, ""]


def _md_inline(value, limit: int) -> str:
    """Model/tool output that lands in an inline Markdown or HTML context (a
    <summary> line, a blockquote callout): trimmed, then HTML-escaped, because
    tool arguments and guardrail reasons routinely contain `<` and `&`. Text
    that lands inside a fenced block is NOT escaped -- a fence is already
    literal, and escaping there would print `&lt;` to the reader. `quote=False`:
    this is element text, never an attribute value, and JSON arguments are full
    of quotes that would otherwise render as `&quot;` noise. Newlines collapse
    to spaces: a blank line inside a tool's `args` would otherwise open a
    second paragraph inside a <summary>/<details> element and break it."""
    text = _md_trim(value, limit).replace("\r\n", "\n").replace("\n", " ")
    return html.escape(text, quote=False)


def _md_event_lines(event: dict) -> list:
    """One non-assistant timeline event as Markdown: tool results become
    collapsible <details> blocks, harness events become blockquote callouts."""
    name = str(event.get("event", ""))
    if name == "tool_result":
        tool = event.get("tool") or "(malformed call)"
        result = str(event.get("result", ""))
        outcome = _tool_result_outcome(result)
        summary = (f"{html.escape(str(tool), quote=False)}"
                   f"({_md_inline(event.get('args', ''), MD_ARGS_CHARS)}) [{outcome}]")
        lines = ["<details>", f"<summary>{summary}</summary>", ""]
        lines += _md_block(_md_trim(result, MD_RESULT_CHARS))
        lines += ["</details>", ""]
        return lines
    if name == "nudge":
        return [f"> **nudge** `{event.get('kind', '')}` (turn {event.get('turn', '')})", ""]
    if name == "guardrail_block":
        return [f"> **guardrail_block** `{event.get('tool', '')}`: "
                f"{_md_inline(event.get('reason', ''), MD_ARGS_CHARS)}", ""]
    if name == "sandbox_reset":
        return [f"> **sandbox_reset**: {_md_inline(event.get('reason', ''), MD_ARGS_CHARS)}", ""]
    return []


_MD_FENCE_LINE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})", re.MULTILINE)


def _balance_fences(text: str) -> str:
    """An assistant reply is emitted into the Markdown document raw -- it is
    already the reader's own Markdown. An odd number of fence-opening lines
    means the last one was never closed, and everything the exporter appends
    after it (later turns, the `## Result` section) would be swallowed into
    that code block. Close it explicitly instead, with a fence of the same
    char/length as the unclosed opener."""
    openers = _MD_FENCE_LINE_RE.findall(text)
    if len(openers) % 2 == 0:
        return text
    closer = openers[-1]
    return f"{text}\n{closer}\n\n_[fence auto-closed by the exporter]_"


def _md_timeline(events: list) -> list:
    """`## Timeline`, one `### Turn N` per assistant event; every other event is
    rendered under the turn it followed."""
    lines = ["## Timeline", ""]
    turn = 0
    for event in events:
        name = str(event.get("event", ""))
        if name in ("run_start", "run_end"):
            continue
        if name == "assistant":
            turn += 1
            lines += [f"### Turn {turn}", ""]
            text = str(event.get("text") or "").strip()
            if text:
                lines += [_balance_fences(text), ""]
            tool_calls = [tc for tc in (event.get("tool_calls") or []) if isinstance(tc, dict)]
            if tool_calls:
                # One bullet per call, independent of whether a matching
                # tool_result was ever recorded: a run that aborts mid-call
                # (budget/sandbox error, model_error after a malformed call)
                # loses the <details> block that would normally carry the
                # command -- this is the only place it survives.
                for tc in tool_calls:
                    tool = tc.get("name") or "(unnamed tool)"
                    arguments = tc.get("arguments")
                    args_text = "-" if arguments is None else _md_inline(arguments, MD_ARGS_CHARS)
                    lines.append(f"- `{tool}` — {args_text}")
                lines.append("")
            else:
                lines += ["_text reply, no tool calls_", ""]
            continue
        lines += _md_event_lines(event)
    if len(lines) == 2:   # nothing was appended after the '## Timeline' heading
        lines += ["_(no timeline events recorded)_", ""]
    return lines


def _last_event(events: list, name: str) -> dict:
    for event in reversed(events):
        if event.get("event") == name:
            return event
    return {}


def _final_message(events: list) -> str:
    """The run's final message, reconstructed from the transcript: run.json does
    not record it. A `finish(summary=...)` call is what the runner turned into
    `final_message`; otherwise (or when the 500-char `args` cap truncated the
    JSON) it is the last non-empty assistant reply."""
    for event in reversed(events):
        if event.get("event") == "tool_result" and event.get("tool") == "finish":
            try:
                args = json.loads(str(event.get("args") or "{}"))
            except ValueError:
                args = {}
            if isinstance(args, dict) and args.get("summary"):
                return str(args["summary"])
            break
    for event in reversed(events):
        if event.get("event") == "assistant":
            text = str(event.get("text") or "").strip()
            if text:
                return text
    return ""


def _md_result(data: dict, events: list) -> list:
    end = _last_event(events, "run_end")
    lines = ["## Result", ""]
    for key in MD_RESULT_FIELDS:
        value = data.get(key) or end.get(key)
        if value not in (None, ""):
            lines.append(f"- **{key}:** {str(value).splitlines()[0]}")
    lines.append("")
    verify = data.get("verify") or end.get("verify")
    if isinstance(verify, dict):
        state = "passed" if verify.get("passed") else "failed"
        lines += [f"**verify** — {state} (exit {verify.get('exit_code')}) after "
                  f"{verify.get('rounds')} round(s)", ""]
        lines += _md_block(str(verify.get("command") or ""))
        lines += _md_block(str(verify.get("output_tail") or ""))
    stuck_on = data.get("stuck_on") or end.get("stuck_on")
    if isinstance(stuck_on, dict):
        lines += [f"**stuck on** — the same failing command ran "
                  f"{stuck_on.get('repeats')} times in a row", ""]
        lines += _md_block(str(stuck_on.get("command") or ""))
        lines += _md_block(str(stuck_on.get("output") or ""))
    files_changed = data.get("files_changed") or end.get("files_changed")
    if isinstance(files_changed, list) and files_changed:
        truncated = data.get("files_changed_truncated") or end.get("files_changed_truncated")
        note = " — list truncated" if truncated else ""
        lines += [f"**files changed ({len(files_changed)}){note}**", ""]
        lines += [f"- `{_md_inline(path, MD_ARGS_CHARS)}`" for path in files_changed]
        lines.append("")
    last_tool = data.get("last_tool_result") or end.get("last_tool_result")
    if isinstance(last_tool, dict):
        lines.append(f"<details><summary>last tool result: "
                     f"{_md_inline(last_tool.get('tool'), MD_ARGS_CHARS)}"
                     f"({_md_inline(last_tool.get('args'), MD_ARGS_CHARS)})</summary>")
        lines.append("")
        lines += _md_block(str(last_tool.get("result") or ""))
        lines += ["</details>", ""]
    last_text = data.get("last_assistant_text") or end.get("last_assistant_text")
    if last_text:
        lines += ["**last assistant text**", ""]
        lines += [f"> {line}" for line in str(last_text).splitlines()] + [""]
    diff_stat = data.get("diff_stat") or end.get("diff_stat")
    if diff_stat:
        lines += ["**diff_stat**", ""] + _md_block(str(diff_stat))
    final = _final_message(events)
    lines += ["**final message**", ""]
    lines += _md_block(final) if final else ["_(none recorded)_", ""]
    return lines


def render_markdown(slug: str, data: dict, events: list, *, diff=None, error=None) -> str:
    """The whole run as one Markdown document: run.json for the header and the
    result, the transcript for the turns. Token counts come from the transcript's
    `run_end.usage` -- run.json has never carried them."""
    lines = [f"# {slug}", ""]
    for key in MD_HEADER_FIELDS:
        if key == "task":
            # The full task text gets its own section below; the header keeps a
            # one-line preview (no "(full text below)" -- there is no JSON dump here).
            # Missing/empty falls back to "-" like every other header field.
            raw_task = data.get("task")
            if not raw_task:
                lines.append("- **task:** -")
                continue
            preview = str(raw_task).replace("\n", " ")
            if len(preview) > TASK_PREVIEW_CHARS:
                preview = preview[:TASK_PREVIEW_CHARS] + " ..."
            lines.append(f"- **task:** {preview}")
            continue
        lines.append(f"- **{key}:** {_summary_value(key, data)}")
    usage = _last_event(events, "run_end").get("usage")
    usage = usage if isinstance(usage, dict) else {}
    for key in ("prompt_tokens", "completion_tokens"):
        value = usage.get(key)
        lines.append(f"- **{key}:** {'-' if value is None else value}")
    for key in MD_VERDICT_FIELDS:
        if data.get(key):
            lines.append(f"- **{key}:** {_summary_value(key, data)}")
    lines.append("")
    if error:
        lines += [f"> **transcript unreadable:** {error}", ""]
    task_text = data.get("task")
    if task_text:
        lines += ["## Task", ""] + _md_block(str(task_text))
    lines += _md_timeline(events)
    lines += _md_result(data, events)
    if diff is not None:
        # The "no diff.patch" fallback sentence is prose, not a patch -- it must
        # not be wrapped in a ```diff fence like a real patch would be.
        lines += ["## Diff", ""]
        lines += [diff, ""] if diff == NO_PATCH_NOTE else _md_block(diff, "diff")
    return "\n".join(lines).rstrip() + "\n"


NO_PATCH_NOTE = "no diff.patch for this run (host mode, or the export never ran)"


def cmd_show(args) -> int:
    try:
        run_dir, data = _open_run(args.slug)
    except RunsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # `getattr`, not `args.x`: existing callers build a Namespace with
    # slug/diff only (tests/test_runs.py) and must keep working.
    want_diff = getattr(args, "diff", False)
    markdown = getattr(args, "markdown", False)
    out = getattr(args, "out", None)
    if out and not markdown:
        print("error: --out requires --markdown", file=sys.stderr)
        return 2

    transcript_path = run_dir / "transcript.jsonl"
    patch_path = run_dir / "diff.patch"

    if markdown:
        events, read_error = read_transcript_events(transcript_path)
        diff_text = None
        if want_diff:
            diff_text = (patch_path.read_text(encoding="utf-8", errors="replace")
                         if patch_path.is_file() else NO_PATCH_NOTE)
        document = render_markdown(args.slug, data, events, diff=diff_text, error=read_error)
        if out:
            try:
                Path(out).write_text(document, encoding="utf-8")
            except OSError as e:
                print(f"error: cannot write '{out}': {e}", file=sys.stderr)
                return 2
            print(f"wrote {out}")
        else:
            print(document, end="")
        return 0

    for key in SHOW_FIELDS:
        print(f"{key}: {_summary_value(key, data)}")
    print()
    print(json.dumps(data, indent=2, sort_keys=True))

    if transcript_path.is_file():
        print("\ntimeline:")
        events, read_error = read_transcript_events(transcript_path)
        if read_error:
            print(f"  (cannot read transcript: {read_error})")
        for event in events:
            print(_timeline_line(event))

    if want_diff:
        if patch_path.is_file():
            print("\ndiff:")
            print(patch_path.read_text(encoding="utf-8", errors="replace"))
        else:
            print(f"\n{NO_PATCH_NOTE}")
    return 0


def cmd_export(args) -> int:
    """Spec SP3 section 4: re-run the SP2 section 7 export for a run whose volume
    still exists (a crash, or `export_failed` after the operator raised a limit).
    Refuses a non-empty worktree, a still-running run, and anything that is not a
    docker-sandbox run."""
    try:
        run_dir, data = _open_run(args.slug)
    except RunsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if data.get("sandbox") != "docker":
        print(f"error: run '{args.slug}' is not a docker-sandbox run; nothing to export",
              file=sys.stderr)
        return 2
    if data.get("status") == "running" and pid_alive(data.get("host_pid")):
        print(f"error: run '{args.slug}' is still running (pid {data.get('host_pid')}); "
              f"wait for it to finish before exporting", file=sys.stderr)
        return 2

    volume = data.get("volume") or ""
    if not volume:
        print(f"error: run.json for '{args.slug}' records no volume", file=sys.stderr)
        return 2
    worktree = Path(data.get("worktree", ""))
    if not worktree.is_dir():
        print(f"error: worktree {worktree} is missing; nothing to export into", file=sys.stderr)
        return 2
    try:
        pristine = export.worktree_is_pristine(worktree)
    except OSError as e:
        print(f"error: cannot read worktree {worktree}: {e}", file=sys.stderr)
        return 2
    if not pristine:
        print(f"error: worktree {worktree} is not empty (it holds more than the .git file); "
              f"the export refuses to overwrite work already on disk", file=sys.stderr)
        return 2

    try:
        cp = docker_cli.run(["volume", "inspect", volume], timeout=docker_cli.T_QUERY)
    except Exception as e:
        print(f"error: cannot query docker: {e}", file=sys.stderr)
        return 2
    if cp.returncode != 0:
        print(f"error: volume '{volume}' does not exist -- nothing to export "
              f"(it may already have been removed by 'runs clean')", file=sys.stderr)
        return 2

    repo = Path(data["repo"])
    try:
        objects_dir = docker_cli.validate_objects_dir(repo)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    image = data.get("image") or docker_args.DEFAULT_IMAGE
    try:
        image_ref = docker_cli.resolve_image(image, pinned_digest=docker_args.pin_for(image))
    except Exception as e:
        print(f"error: cannot resolve image '{image}': {e}", file=sys.stderr)
        return 2

    cfg = docker_args.DockerConfig(image=image, max_patch_mb=args.max_patch_mb,
                                   keep_volume=args.keep_volume,
                                   max_worktree_mb=args.max_worktree_mb,
                                   max_worktree_files=args.max_worktree_files)
    uid, gid = _uid_gid()
    artifacts = export.export_run(
        cfg, slug=args.slug, base_commit=data["base_commit"], worktree=worktree,
        run_dir=run_dir, objects_dir=objects_dir, image_ref=image_ref, uid=uid, gid=gid,
        repo_label=docker_args.repo_label(repo),
    )

    data["status"] = _export_status_update(data.get("status"), artifacts.export_status)
    data["export_status"] = artifacts.export_status
    data["diff_stat"] = artifacts.diff_stat
    data["patch_path"] = artifacts.patch_path
    data["worktree_bytes"] = artifacts.worktree_bytes
    data["worktree_files"] = artifacts.worktree_files
    data["escaping_symlinks"] = artifacts.escaping_symlinks
    data["dropped_git_entries"] = artifacts.dropped_git_entries
    data["files_changed"] = artifacts.files_changed
    data["files_changed_truncated"] = artifacts.files_changed_truncated
    rundir.write_run_json(run_dir, data)

    if artifacts.export_status != "ok":
        print(f"error: export failed: {artifacts.export_status}\n"
              f"the volume was kept, so this command can be retried after raising a limit",
              file=sys.stderr)
        return 1
    print(f"exported '{args.slug}' into {worktree}")
    if artifacts.diff_stat:
        print(artifacts.diff_stat)
    return 0


def cmd_verdict(args) -> int:
    """Spec SP3 section 4: append the operator's verdict to run.json.
    `time_to_verdict_s` is measured from the run's `ended` timestamp (the key
    `__main__._update_run_json` writes) and is deliberately noisy -- it includes
    idle time. `--review-seconds` is the operator's explicit measure."""
    try:
        run_dir, data = _open_run(args.slug)
    except RunsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    verdict_at = datetime.now(timezone.utc).isoformat()
    data["verdict"] = args.verdict
    data["note"] = args.note
    data["verdict_at"] = verdict_at
    data["review_seconds"] = args.review_seconds
    data["time_to_verdict_s"] = None
    ended = data.get("ended")
    if ended:
        try:
            ended_dt = datetime.fromisoformat(str(ended).replace("Z", "+00:00"))
            if ended_dt.tzinfo is None:
                # A naive timestamp (no tz offset recorded): assume UTC rather
                # than crash subtracting an aware datetime from a naive one.
                ended_dt = ended_dt.replace(tzinfo=timezone.utc)
            data["time_to_verdict_s"] = (
                datetime.fromisoformat(verdict_at) - ended_dt).total_seconds()
        except (ValueError, TypeError):
            pass

    rundir.write_run_json(run_dir, data)
    print(f"recorded verdict '{args.verdict}' for '{args.slug}'")
    return 0


def cmd_snapshot(args) -> int:
    """Spec §6.1: commit the run worktree's current content onto the run's own
    branch, using plumbing that never runs a filter or a hook (the repo's
    ignore rules ARE applied, though, the same way `git add -A` would apply
    them). Plumbing-only by design — the review→fix loop needs a commit to branch from
    (`--branch-from @<slug>`) without asking the operator for a manual wip
    commit that their own git config would have filtered. The live-pid,
    missing/foreign-worktree and pre-resume-stash guards are
    `resume.preflight_run_worktree` — the same guards `dirtywork resume`
    applies before touching a prior run's worktree, and Task 8's
    `--branch-from @<slug>` is the third caller. The empty-tree guard lives
    inside `snapshot_worktree` itself, since that function has callers (Task
    8) that never pass through this CLI guard at all.

    Reads run.json via `resume.load_prior_run` rather than `_open_run` (which
    validates only "is a dict"): `preflight_run_worktree` indexes
    `prior["worktree"]`/`["repo"]`/`["slug"]` directly, so a malformed
    run.json must be refused with a clean RunsError/ResumeError message
    BEFORE that, not surfaced as a bare KeyError traceback. `_existing_run_dir`
    still does the slug-shape/containment/existence validation `_open_run` uses."""
    try:
        run_dir = _existing_run_dir(args.slug)
    except RunsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    try:
        data = load_prior_run(run_dir)
    except ResumeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    try:
        preflight_run_worktree(data, action="snapshot")
    except ResumeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    # load_prior_run already required "branch" to be a str (_REQUIRED_STR_KEYS);
    # only the empty-string case still needs a check here.
    branch = data["branch"]
    if not branch:
        print(f"error: run.json for '{args.slug}' records no branch", file=sys.stderr)
        return 2
    worktree = Path(data.get("worktree", ""))

    report: dict = {}
    try:
        sha = snapshot_worktree(worktree, branch, f"wip: dirtywork run {args.slug}",
                                report=report)
    except WorkspaceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    line = f"snapshot {sha} on {branch}" if sha else "nothing to snapshot"
    skipped = report.get("skipped", 0)
    if skipped:
        noun = "entry" if skipped == 1 else "entries"
        line += f" ({skipped} non-regular {noun} skipped)"
    print(line)
    return 0


def dispatch(args) -> int:
    """`main()` routes `dirtywork runs <sub>` here. Each later task adds one
    entry to this table and one parser block in `__main__._add_runs_parsers`."""
    if args.runs_cmd == "clean":
        if not args.all and not args.slug:
            print("error: 'runs clean' needs a slug or --all", file=sys.stderr)
            return 2
        if args.all and args.slug:
            print("error: 'runs clean' takes a slug or --all, not both", file=sys.stderr)
            return 2
    handlers = {
        "list": cmd_list,
        "show": cmd_show,
        "export": cmd_export,
        "clean": cmd_clean,
        "verdict": cmd_verdict,
        "snapshot": cmd_snapshot,
    }
    return handlers[args.runs_cmd](args)


def _staleness(data: dict, force: bool):
    """(is_stale, why_not) per SP2 section 3: any status other than 'running' is
    stale; 'running' is stale only with a confirmed-dead host_pid AND --force."""
    if data.get("status") != "running":
        return True, None
    host_pid = data.get("host_pid")
    if not isinstance(host_pid, int) or isinstance(host_pid, bool):
        return False, "status is 'running' and no host_pid is recorded to check"
    if pid_alive(host_pid):
        return False, f"status is 'running' and its host process ({host_pid}) is alive"
    if force:
        return True, None
    return False, ("status is 'running' with a dead host process -- pass --force to "
                   "confirm cleanup")


def _run_json_owned_by_current_user(run_dir: Path) -> bool:
    """SP2 section 3's ownership condition. Windows has no uid ownership and no
    integration suite yet, so this fails closed there."""
    if not hasattr(os, "getuid"):
        return False
    try:
        return (run_dir / "run.json").stat().st_uid == os.getuid()
    except OSError:
        return False


def _clean_docker_resource(kind: str, name: str, repo: str, slug: str, log: list) -> None:
    """kind is 'container' or 'volume'. Removes ONLY a resource whose
    dirtywork.run/dirtywork.repo labels match this exact run; anything missing,
    unlabeled, or belonging to another run/repo is reported and left alone."""
    if kind == "container":
        inspect_argv = ["inspect", "--format",
                        '{{index .Config.Labels "dirtywork.run"}}\t'
                        '{{index .Config.Labels "dirtywork.repo"}}', name]
        rm_argv = ["rm", "-f", name]
    else:
        inspect_argv = ["volume", "inspect", "--format",
                        '{{index .Labels "dirtywork.run"}}\t'
                        '{{index .Labels "dirtywork.repo"}}', name]
        rm_argv = ["volume", "rm", name]
    try:
        cp = docker_cli.run(inspect_argv, timeout=docker_cli.T_QUERY)
    except Exception as e:
        log.append((f"skip-{kind}", f"'{name}': cannot inspect: {e}"))
        return
    if cp.returncode != 0:
        text = cp.output.decode("utf-8", errors="replace").strip()
        # Object-level "gone" only: `Error: No such object: <name>` (container),
        # `... no such volume` (volume). A bare "no such" would also match a
        # DAEMON-DOWN message ("dial unix /var/run/docker.sock: connect: no such
        # file or directory") and misfile an outage as "already removed".
        if _DOCKER_ABSENT_RE.search(text):
            # Not a refusal: a completed docker run already removed its container
            # and volume in sandbox.stop(), so this is the normal end state. It must
            # not count as "skipped" (exit 1 / run dir kept / --force needed).
            log.append((f"absent-{kind}", f"'{name}': not found (already removed)"))
        else:
            # Daemon down, permission denied, timeout... -- we could NOT verify the
            # resource is gone, so nothing else may be removed either (see _clean_one).
            first = text.splitlines()[0] if text else "docker inspect failed"
            log.append((f"skip-{kind}", f"'{name}': cannot inspect: {first}"))
        return
    run_label, _, repo_label_value = cp.output.decode("utf-8", errors="replace").strip().partition("\t")
    if run_label != slug or repo_label_value != docker_args.repo_label(Path(repo)):
        log.append((f"skip-{kind}", f"'{name}': labels do not match this run -- never touching it"))
        return
    try:
        rm = docker_cli.run(rm_argv, timeout=docker_cli.T_LIFECYCLE)
    except Exception as e:
        log.append((f"skip-{kind}", f"'{name}': removal failed: {e}"))
        return
    log.append((f"removed-{kind}" if rm.returncode == 0 else f"skip-{kind}", name))


def _worktree_is_dirty(worktree: str) -> bool:
    """Fail closed: if git cannot be asked, treat the worktree as dirty.
    Delegates to the one config-neutral dirty check in workspace.py."""
    return host_worktree_dirty(worktree)


def _commits_beyond_base(repo: str, base_commit, branch):
    """Commits `branch` carries past `base_commit`, or None when either value
    is missing or git could not answer. Callers treat None as "unknown,
    assume the worst": an --allow-commit run's real work must never be
    force-deleted just because we couldn't check."""
    if not base_commit or not branch:
        return None
    try:
        cp = subprocess.run(["git", "-C", str(repo), "rev-list", "--count",
                            f"{base_commit}..{branch}"],
                            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if cp.returncode != 0:
        return None
    try:
        return int(cp.stdout.strip())
    except ValueError:
        return None


def _worktree_checked_out_branch(repo: str, worktree):
    """The short branch name `git worktree list --porcelain` records for
    `worktree`, read BEFORE any removal so `git branch -D` only ever targets
    the branch actually checked out there -- run.json is data, not authority.
    None for a detached HEAD or a worktree git does not know about."""
    try:
        cp = subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"],
                            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if cp.returncode != 0:
        return None
    try:
        target = str(Path(worktree).resolve())
    except OSError:
        return None
    current = None
    for line in cp.stdout.splitlines():
        if line.startswith("worktree "):
            try:
                current = str(Path(line[len("worktree "):]).resolve())
            except OSError:
                current = None
        elif line.startswith("branch refs/heads/") and current == target:
            return line[len("branch refs/heads/"):]
    return None


def _is_dirtywork_worktree(worktree: str, repo: str) -> bool:
    """True only for `<repo>/.worktrees/dw-<something>` (resolved), the shape
    workspace.create_worktree produces. Fail closed on any OSError."""
    try:
        wt = Path(worktree).resolve()
        managed = (Path(repo) / ".worktrees").resolve()
    except OSError:
        return False
    return wt.parent == managed and wt.name.startswith("dw-")


def _delete_orphaned_branch(repo: str, branch, log: list) -> None:
    """After an already-gone worktree: delete the run's branch only when it is
    dirtywork's own (`dirtywork/<slug>`) and no worktree has it checked out."""
    if not branch:
        return
    if not str(branch).startswith("dirtywork/"):
        log.append(("skip-branch", f"'{branch}': not a dirtywork/* branch -- left alone"))
        return
    try:
        cp = subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"],
                            capture_output=True, text=True, timeout=10)
        if cp.returncode != 0:      # fail closed: cannot tell where the branch is checked out
            log.append(("skip-branch", f"'{branch}': cannot list worktrees ({cp.stderr.strip() or 'git error'})"))
            return
        if f"branch refs/heads/{branch}\n" in cp.stdout + "\n":
            log.append(("skip-branch", f"'{branch}': still checked out in a worktree"))
            return
        br = subprocess.run(["git", "-C", str(repo), "branch", "-D", str(branch)],
                            capture_output=True, text=True, timeout=10)
        log.append(("removed-branch" if br.returncode == 0 else "skip-branch", str(branch)))
    except (OSError, subprocess.SubprocessError) as e:
        log.append(("skip-branch", f"'{branch}': {e}"))


def _clean_worktree_and_branch(data: dict, force: bool, log: list) -> bool:
    """Returns True when the worktree was actually removed. A run whose worktree
    was taken over by a later resume (resumed_by set) keeps both the worktree and
    the branch -- they belong to the newest run in the chain."""
    worktree = data.get("worktree")
    repo = data.get("repo", "")
    if not worktree or not repo:
        return False
    resumed_by = data.get("resumed_by")
    if resumed_by:
        log.append(("kept-worktree",
                    f"'{worktree}': shared with the later resume run '{resumed_by}' -- "
                    f"the worktree and branch belong to the newest run in the chain; "
                    f"run `dirtywork runs clean {resumed_by}` to remove them"))
        return False
    if not Path(worktree).exists():
        # Already gone (removed by hand, or by an earlier partial clean): not a
        # refusal. Prune git's bookkeeping and let the run finish cleaning up.
        subprocess.run(["git", "-C", str(repo), "worktree", "prune"],
                       capture_output=True, text=True, timeout=30)
        log.append(("absent-worktree", f"'{worktree}': already gone"))
        _delete_orphaned_branch(repo, data.get("branch"), log)
        return True
    # Same trust boundary as resume, tightened: run.json is data, not authority.
    # `git worktree remove --force` may only ever target the worktree dirtywork
    # itself created for a run: <repo>/.worktrees/dw-<slug> (create_worktree's
    # naming) that is a linked worktree of the recorded repo (a `.git` FILE whose
    # gitdir resolves under <repo>/.git). Any other linked worktree of the repo
    # (the operator's own, or another tool's) is refused.
    if not _is_dirtywork_worktree(worktree, repo):
        log.append(("skip-worktree",
                    f"'{worktree}': not a dirtywork-managed worktree "
                    f"({repo}/.worktrees/dw-*) -- refusing to remove"))
        return False
    if not worktree_belongs_to_repo(Path(worktree), Path(repo)):
        log.append(("skip-worktree",
                    f"'{worktree}': not a linked worktree of {repo} (refusing to remove)"))
        return False
    if _worktree_is_dirty(worktree) and not force:
        log.append(("skip-worktree",
                    f"'{worktree}': has uncommitted changes (pass --force to remove anyway)"))
        return False
    branch = data.get("branch")
    # A dirty-worktree check alone misses an --allow-commit run: the worker
    # may have committed real work, leaving the worktree clean but the branch
    # ahead of base_commit. That work must survive an un-forced clean too.
    if not force:
        beyond = _commits_beyond_base(repo, data.get("base_commit"), branch)
        if beyond is None:
            log.append(("skip-worktree",
                        f"'{branch}': cannot determine commits beyond base "
                        f"{data.get('base_commit') or '?'} (unknown -- pass --force to "
                        f"remove anyway)"))
            return False
        if beyond > 0:
            short_base = str(data.get("base_commit"))[:7]
            log.append(("skip-worktree",
                        f"'{branch}': has {beyond} commit(s) beyond base {short_base} "
                        f"(pass --force to remove anyway)"))
            return False
    # Read BEFORE removal: once the worktree is gone, git can no longer say
    # which branch it had checked out.
    actual_branch = _worktree_checked_out_branch(repo, worktree)
    try:
        rm = subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
                            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        log.append(("skip-worktree", f"'{worktree}': {e}"))
        return False
    if rm.returncode != 0:
        log.append(("skip-worktree", f"'{worktree}': {rm.stderr.strip() or 'git worktree remove failed'}"))
        return False
    log.append(("removed-worktree", str(worktree)))
    if branch:
        if actual_branch != branch:
            log.append(("skip-branch",
                        f"'{branch}': not the branch checked out in {worktree} "
                        f"(was {actual_branch or 'detached'})"))
        else:
            try:
                br = subprocess.run(["git", "-C", str(repo), "branch", "-D", str(branch)],
                                    capture_output=True, text=True, timeout=10)
                log.append(("removed-branch" if br.returncode == 0 else "skip-branch", str(branch)))
            except (OSError, subprocess.SubprocessError) as e:
                log.append(("skip-branch", f"'{branch}': {e}"))
    return True


def _clean_stashes(data: dict, slug: str, worktree_removed: bool, force: bool, log: list) -> None:
    """A docker resume parks the pre-resume worktree content in
    `<worktree>.pre-resume-<slug>` (resume.stash_dir_for). Cleaning a run removes
    the stash that run created; once the worktree itself is gone, every remaining
    stash beside it is orphaned and goes too. A stash is only ever removed when
    the worktree it belongs beside was actually removed in this invocation, or
    --force was given -- otherwise it is left in place (it may still be needed
    to recover the worktree's pre-resume content)."""
    worktree = data.get("worktree")
    if not worktree:
        return
    worktree = Path(worktree)
    targets = [stash_dir_for(worktree, slug)]
    if worktree_removed or force:
        targets += [p for p in find_stashes(worktree) if p not in targets]
    for stash in targets:
        if not stash.is_dir():
            continue
        if worktree_removed or force:
            shutil.rmtree(stash, ignore_errors=True)
            log.append(("removed-stash", str(stash)))
        else:
            log.append(("kept-stash",
                        f"{stash}: kept -- worktree was not removed (pass --force to "
                        f"remove it too)"))


def _clean_run_dir(run_dir: Path, keep_transcript: bool, log: list) -> None:
    if keep_transcript:
        for child in run_dir.iterdir():
            if child.name in ("transcript.jsonl", "run.json"):
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except OSError:
                    pass
        log.append(("kept-transcript", str(run_dir)))
    else:
        shutil.rmtree(run_dir, ignore_errors=True)
        log.append(("removed-rundir", str(run_dir)))


def _clean_one(slug: str, *, keep_transcript: bool, force: bool) -> list:
    """(action, detail) pairs describing what happened. Any action starting with
    'skip' means something was deliberately left alone -- never a silent no-op."""
    log: list = []
    try:
        run_dir, data = _open_run(slug)
    except RunsError as e:
        log.append(("skip", str(e)))
        return log
    if not _run_json_owned_by_current_user(run_dir):
        log.append(("skip", f"'{slug}': run.json is not owned by the current user"))
        return log
    is_stale, why_not = _staleness(data, force)
    if not is_stale:
        log.append(("skip", f"'{slug}': {why_not}"))
        return log

    repo = data.get("repo", "")
    if data.get("container"):
        _clean_docker_resource("container", data["container"], repo, slug, log)
    if data.get("volume"):
        _clean_docker_resource("volume", data["volume"], repo, slug, log)
    if any(action.startswith("skip") for action, _ in log):
        # A container/volume we could not verify or remove: stop here. Removing
        # the worktree now would leave a run dir that can never be cleaned (its
        # worktree is gone, its docker resources are not) -- keep everything so
        # a retry (daemon back up, or --force) can finish the job.
        log.append(("kept-worktree",
                    f"'{data.get('worktree')}': kept because a docker resource of this "
                    f"run was not removed -- fix that first, then re-run"))
        log.append(("kept-run-dir",
                    f"{run_dir}: kept because a resource it describes was not removed "
                    f"-- re-run with --force"))
        return log

    worktree_removed = _clean_worktree_and_branch(data, force, log)
    _clean_stashes(data, slug, worktree_removed, force, log)
    if any(action.startswith("skip") for action, _ in log):
        log.append(("kept-run-dir",
                    f"{run_dir}: kept because a resource it describes was not removed "
                    f"-- re-run with --force"))
    else:
        _clean_run_dir(run_dir, keep_transcript, log)
    return log


def cmd_clean(args) -> int:
    slugs = ([d.name for d in _iter_run_dirs(rundir.RUNS_DIR)] if args.all else [args.slug])
    any_skipped = False
    for slug in slugs:
        for action, detail in _clean_one(slug, keep_transcript=args.keep_transcript,
                                         force=args.force):
            print(f"{action}: {detail}")
            if action.startswith("skip"):
                any_skipped = True
    return 1 if any_skipped else 0
