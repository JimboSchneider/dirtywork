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
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import rundir
from .resume import find_stashes, pid_alive, stash_dir_for, worktree_belongs_to_repo
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


_SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


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


def _open_run(slug: str):
    """(run_dir, run.json dict) or RunsError — the one lookup every single-run
    subcommand uses, so 'no such run' reads identically everywhere."""
    run_dir = _run_dir_for(slug)
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
        if "no such" in text.lower():
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
    """Fail closed: if git cannot be asked, treat the worktree as dirty."""
    try:
        cp = subprocess.run(["git", "-C", str(worktree), "status", "--porcelain"],
                            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return True
    return cp.returncode != 0 or bool(cp.stdout.strip())


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
        if cp.returncode == 0 and f"branch refs/heads/{branch}\n" in cp.stdout + "\n":
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
