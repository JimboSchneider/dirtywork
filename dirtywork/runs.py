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

import json
import os
import subprocess
import sys
from pathlib import Path

from . import rundir
from .resume import pid_alive
from .sandbox import docker_args, docker_cli, export

COLUMN_GAP = "  "
LIST_COLUMNS = ("slug", "status", "started", "resumed", "branch", "worktree",
                "container", "volume")
SHOW_FIELDS = ("slug", "status", "sandbox", "task", "model", "provider", "turns",
               "resumed_from", "resumed_by", "branch", "worktree", "started", "ended")
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


def _open_run(slug: str):
    """(run_dir, run.json dict) or RunsError — the one lookup every single-run
    subcommand uses, so 'no such run' reads identically everywhere."""
    run_dir = Path(rundir.RUNS_DIR) / slug
    if not run_dir.is_dir():
        raise RunsError(f"no such run '{slug}' under {rundir.RUNS_DIR}")
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
    if value is None or value == "":
        return "-"
    text = str(value)
    if key == "task" and len(text) > TASK_PREVIEW_CHARS:
        text = text[:TASK_PREVIEW_CHARS].replace("\n", " ") + " ... (full text below)"
    return text.replace("\n", " ") if key == "task" else text


def _timeline_line(event: dict) -> str:
    ts = event.get("ts", "")
    name = str(event.get("event", ""))
    if name == "tool_result":
        result = str(event.get("result", ""))
        outcome = ("ERROR" if result.startswith("ERROR")
                   else "BLOCKED" if result.startswith("BLOCKED") else "ok")
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


def cmd_show(args) -> int:
    try:
        run_dir, data = _open_run(args.slug)
    except RunsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    for key in SHOW_FIELDS:
        print(f"{key}: {_summary_value(key, data)}")
    print()
    print(json.dumps(data, indent=2, sort_keys=True))

    transcript_path = run_dir / "transcript.jsonl"
    if transcript_path.is_file():
        print("\ntimeline:")
        try:
            lines = transcript_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            lines = []
            print(f"  (cannot read transcript: {e})")
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                print(_timeline_line(event))

    if getattr(args, "diff", False):
        patch_path = run_dir / "diff.patch"
        if patch_path.is_file():
            print("\ndiff:")
            print(patch_path.read_text(encoding="utf-8", errors="replace"))
        else:
            print("\nno diff.patch for this run (host mode, or the export never ran)")
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
                                   keep_volume=args.keep_volume)
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


def dispatch(args) -> int:
    """`main()` routes `dirtywork runs <sub>` here. Each later task adds one
    entry to this table and one parser block in `__main__._add_runs_parsers`."""
    handlers = {
        "list": cmd_list,
        "show": cmd_show,
        "export": cmd_export,
    }
    return handlers[args.runs_cmd](args)
