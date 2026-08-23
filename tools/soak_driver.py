#!/usr/bin/env python3
"""Run a soak plan (JSONL, one row per `dirtywork run`) serially, issue #48
(docs/superpowers/bench/2026-08-23-v1-soak-matrix.md). Two plan row shapes,
distinguished by exactly one of `task`/`repo` being present:

    {"task": "<bench task dir name or absolute repo path>", "model": "...",
     "provider": "openai|ollama", "base_url": "...",
     "flags": ["--verify", "...", "--max-tokens", "1024"], "label": "F5-qwen-r1"}

    {"repo": "<absolute path to an existing git repo>",
     "task_text": "..." or "task_file": "<path to a file with the task text>",
     "model": "...", "provider": "...", "base_url": "...", "flags": [...],
     "label": "D-invoicr-94"}

`task` names either a fixture under `dirtywork.bench.BENCH_REPOS` or an
absolute path laid out the same way (a `bench.json` at its root, giving the
task text and -- optionally -- an `acceptance/` command+hashes): the leg-B
provokers in the soak matrix are this shape. `repo` is the leg-D shape --
a real, already-existing repo (e.g. invoicr) with its task text given inline
or read from a file; there is no bench.json, so it is never staged (the row
is run against `repo` in place -- `dirtywork run` makes its own worktree
inside it, exactly as an operator invoking the CLI by hand would) and never
scored (`acceptance_passed` is always null for these rows).

A `task` row is staged into a fresh temp dir first; either shape is then
driven through `dirtywork run` as a real subprocess (`python -m dirtywork
run ...`, run from this checkout) -- not the in-process `run_once()`
`dirtywork.bench` uses for its own sweep speed, because this driver's own
wall-clock/exit-code numbers are part of what the soak is measuring. The
invocation shape (`--repo ... --sandbox docker --keep-volume
--max-turns/--timeout`, then the row's own `--provider`/`--base-url`/extra
flags) mirrors `dirtywork.bench.run_one_bench_case`.

Resumable: a row whose `label` already has a terminal-status result in
`--out` is skipped, so a killed sweep restarts where it left off -- a row
whose spawn itself failed before any `dirtywork run` happened is NOT counted
as done and will retry. Every label in a plan file must be unique (checked
upfront; a duplicate is a plan error, not two rows sharing one result).
`--dry-run` prints the command each row would run without executing
anything.

Stdlib only, run from a source checkout (same constraint as dirtywork.bench).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import soak_common  # noqa: E402
from dirtywork import bench, rundir  # noqa: E402
from dirtywork.runs import _uid_gid  # noqa: E402
from dirtywork.sandbox import docker_args, docker_cli  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MAX_TURNS = 40
DEFAULT_TIMEOUT = 1800


def _resolve_task_source(task_field: str) -> tuple:
    """A plan row's `task` field -> (source_dir, bench_data). `task` is
    either a name under `dirtywork.bench.BENCH_REPOS` or an absolute path to
    a directory laid out the same way. A `bench.json` is required either way
    -- it is the only place a plan row's task TEXT lives (the plan schema
    carries no separate task-text field)."""
    path = Path(task_field)
    source_dir = path if path.is_absolute() else bench.BENCH_REPOS / task_field
    bench_json_path = source_dir / "bench.json"
    if not bench_json_path.is_file():
        raise ValueError(f"no bench.json under {source_dir} (task={task_field!r})")
    bench_data = json.loads(bench_json_path.read_text())
    if not isinstance(bench_data.get("task"), str) or not bench_data["task"]:
        raise ValueError(f"{bench_json_path}: missing/empty 'task' string")
    return source_dir, bench_data


def _resolve_task_text(row: dict) -> str:
    """A `repo`-shaped row's task text -- exactly one of `task_text` (used
    verbatim) or `task_file` (read as UTF-8 and stripped, mirroring how
    `dirtywork run`'s own task positional is just given as text)."""
    has_text = bool(row.get("task_text"))
    has_file = bool(row.get("task_file"))
    if has_text == has_file:
        raise ValueError(
            "a 'repo' row must have exactly one of 'task_text' or 'task_file'; got "
            f"task_text={row.get('task_text')!r} task_file={row.get('task_file')!r}")
    if has_text:
        return row["task_text"]
    path = Path(row["task_file"])
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise ValueError(f"cannot read task_file '{path}': {e}") from e
    if not text:
        raise ValueError(f"task_file '{path}' is empty")
    return text


def _resolve_row(row: dict) -> dict:
    """Normalize either plan row shape to one dict: {'repo_path': Path|None,
    'source_dir': Path|None, 'task_text': str, 'stage': bool,
    'acceptance_dir': Path|None, 'bench_data': dict|None}. `stage` is True
    only for the bench-task shape (`repo_path` is None, a fresh copy of
    `source_dir` is made by the caller); for the `repo` shape `repo_path` is
    used directly and nothing is staged or scored.

    Exactly one of `task`/`repo` must be given on a row -- naming both, or
    neither, is a plan error to be reported clearly, not guessed at."""
    has_task = bool(row.get("task"))
    has_repo = bool(row.get("repo"))
    if has_task == has_repo:
        raise ValueError(
            "a plan row must have exactly one of 'task' (bench task name or "
            "bench-shaped absolute path) or 'repo' (an existing git repo path); got "
            f"task={row.get('task')!r} repo={row.get('repo')!r}")

    if has_task:
        source_dir, bench_data = _resolve_task_source(row["task"])
        return {"repo_path": None, "source_dir": source_dir, "task_text": bench_data["task"],
                "stage": True, "acceptance_dir": source_dir / "acceptance",
                "bench_data": bench_data}

    repo_path = Path(row["repo"])
    if not repo_path.is_absolute():
        raise ValueError(f"'repo' must be an absolute path, got {row['repo']!r}")
    if not repo_path.is_dir():
        raise ValueError(f"no such repo directory: {repo_path}")
    return {"repo_path": repo_path, "source_dir": None, "task_text": _resolve_task_text(row),
            "stage": False, "acceptance_dir": None, "bench_data": None}


def _stage_repo(source_dir: Path, tag: str) -> Path:
    """Copy `source_dir` into a fresh temp dir and commit it. Mirrors
    `dirtywork.bench._stage_repo`, generalized to take any source directory
    since a plan row's task may be an absolute path, not just a name under
    BENCH_REPOS. Docker Desktop caches deleted bind-mount source paths (spec
    SP2 section 8), so a fresh mkdtemp path is used every call, never reused."""
    safe_tag = re.sub(r"[^A-Za-z0-9._-]", "_", tag)[:40] or "task"
    dest = Path(tempfile.mkdtemp(prefix=f"dwsoak-{safe_tag}-"))
    shutil.rmtree(dest)                     # mkdtemp created it; copytree needs it absent
    try:
        shutil.copytree(source_dir, dest)
        git_id = ["-c", "user.email=soak@dirtywork.local", "-c", "user.name=dirtywork-soak"]
        subprocess.run(["git", "-C", str(dest), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(dest), *git_id, "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(dest), *git_id, "commit", "-q", "-m", "soak fixture"],
                       check=True)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)   # never leak a half-staged tree
        raise
    return dest


def _build_argv(model, provider, base_url, task_text, repo_dir, flags,
                max_turns=DEFAULT_MAX_TURNS, timeout=DEFAULT_TIMEOUT) -> list:
    """Same argv shape as `dirtywork.bench.run_one_bench_case`, plus the
    row's own extra `flags` appended last (so e.g. a row's own `--max-turns`
    in `flags` wins -- argparse keeps the last value for a repeated flag)."""
    argv = ["run", task_text, "--repo", str(repo_dir), "--model", model,
            "--sandbox", "docker", "--keep-volume",
            "--max-turns", str(max_turns), "--timeout", str(timeout)]
    if provider:
        argv += ["--provider", provider]
    if base_url:
        argv += ["--base-url", base_url]
    argv += [str(f) for f in (flags or [])]
    return argv


def _invoke_dirtywork(argv: list) -> tuple:
    """`python -m dirtywork <argv>` as a real subprocess, from this checkout.
    Returns (payload_or_None, exit_code, wall_seconds, stderr_text). Unlike
    `dirtywork.bench.run_once`, a stdout parse failure is not fatal here --
    it is folded into the result row as `status: None` so one bad row does
    not abort the sweep."""
    start = time.monotonic()
    proc = subprocess.run([sys.executable, "-m", "dirtywork"] + argv,
                          cwd=str(REPO_ROOT), capture_output=True, text=True)
    wall_s = round(time.monotonic() - start, 1)
    payload = None
    text = proc.stdout.strip()
    if text:
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
    return payload, proc.returncode, wall_s, proc.stderr


def _run_acceptance(acceptance_dir: Path, bench_data: dict, volume: str) -> str:
    """'pass' | 'fail' | 'gamed' | 'skipped'. Mirrors
    `dirtywork.bench._run_acceptance` step for step, generalized to take the
    acceptance/ directory directly instead of deriving it from
    `BENCH_REPOS/<task>` -- a plan row's task may point at an absolute path
    outside bench/repos/ (leg D style). Reuses bench.py's own argv builders
    (`_hash_check_argv`/`_acceptance_run_argv`), which already take the
    acceptance dir as a parameter; only the orchestration around them (which
    bench.py hardcodes to BENCH_REPOS) is duplicated here. Never raises."""
    acceptance = bench_data.get("acceptance")
    hashes = acceptance.get("hashes") if isinstance(acceptance, dict) else None
    command = acceptance.get("command") if isinstance(acceptance, dict) else None
    if not isinstance(hashes, dict) or not isinstance(command, str):
        return "skipped"
    image = docker_args.DEFAULT_IMAGE
    try:
        image_ref = docker_cli.resolve_image(image, pinned_digest=docker_args.pin_for(image))
    except Exception:
        return "skipped"
    uid, gid = _uid_gid()
    try:
        cp = docker_cli.run(bench._hash_check_argv(volume, image_ref, uid, gid,
                                                    [f"/work/{p}" for p in hashes]),
                            timeout=docker_cli.T_EXPORT_STEP)
    except Exception:
        return "skipped"
    if cp.returncode not in (0, 1):
        return "skipped"      # docker itself failed (125/126/127), not the worker's doing
    actual = {}
    for line in cp.output.decode("utf-8", errors="replace").splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            digest, path = parts[0], parts[1].strip().lstrip("*")
            if path.startswith("/work/"):
                path = path[len("/work/"):]
            actual[path] = digest
    for rel_path, expected in hashes.items():
        if actual.get(rel_path) != expected:
            return "gamed"
    try:
        cp = docker_cli.run(bench._acceptance_run_argv(volume, image_ref, uid, gid,
                                                        acceptance_dir, command),
                            timeout=docker_cli.T_EXPORT_STEP)
    except Exception:
        return "skipped"
    return "pass" if cp.returncode == 0 else "fail"


def _run_one(row: dict) -> dict:
    """Run (and, for the bench-task row shape, stage first and score) one
    plan row. Never raises `Exception` -- a bad row becomes a result row
    carrying `error`, same tolerance as `dirtywork.bench.run_one_bench_case`.
    A `repo`-shaped row is run in place (no staging, no acceptance -- see
    the module docstring) so `repo_dir` is only ever cleaned up when it was
    this function's own staged copy.

    Cleanup (review item 6): everything from the moment `repo_dir` might
    exist onward runs inside ONE outer try/finally, so a staged temp dir is
    removed even when the row is interrupted (Ctrl-C/KeyboardInterrupt)
    while `_invoke_dirtywork`'s subprocess is running -- the longest step,
    and the one most likely to catch a Ctrl-C. A per-step `except Exception`
    does NOT catch `KeyboardInterrupt` (it is a BaseException), so relying on
    those alone to clean up (the previous shape of this function) could leak
    a staged copy on every interrupted row; `finally` runs regardless of
    exception type. The `--keep-volume` docker volume is handled
    differently, and deliberately NOT covered by that same guarantee: it is
    removed only in the ordinary (no exception) path below, once a row's
    acceptance check (or the decision to skip one) is settled. If a row
    raises or is interrupted before reaching that point, its volume is left
    behind on purpose -- it is the one piece of forensic evidence for what
    the sandbox actually did, and an operator can `docker run -v
    <volume>:/work ... /bin/sh` into it. Only a row that reaches the end
    without incident has its volume cleaned up automatically."""
    result = {
        "label": row.get("label"), "task": row.get("task") or row.get("repo"),
        "model": row.get("model"), "provider": row.get("provider"),
        "base_url": row.get("base_url"), "flags": row.get("flags") or [],
        "run_dir": None, "exit_code": None, "wall_s": None, "status": None,
        "final_message": None, "acceptance_passed": None,
    }
    try:
        resolved = _resolve_row(row)
    except Exception as e:
        result["error"] = str(e)
        return result

    cleanup_repo_dir = resolved["stage"]
    repo_dir = None
    try:
        try:
            repo_dir = (_stage_repo(resolved["source_dir"],
                                    row.get("label") or row.get("task", "task"))
                       if resolved["stage"] else resolved["repo_path"])
        except Exception as e:
            result["error"] = f"staging failed: {e}"
            return result

        argv = _build_argv(row.get("model"), row.get("provider"), row.get("base_url"),
                           resolved["task_text"], repo_dir, row.get("flags") or [])
        try:
            payload, exit_code, wall_s, stderr_text = _invoke_dirtywork(argv)
        except Exception as e:
            result["error"] = f"subprocess failed: {e}"
            return result
        result["exit_code"], result["wall_s"] = exit_code, wall_s
        # Only available at run time, off this row's OWN subprocess's stdout
        # -- see tools/soak_harvest.py's comment on `_harness_failures` for
        # why it can never be recovered later from run_dir alone (review item 1).
        result["final_message"] = (payload or {}).get("final_message")

        run_dir = Path(payload["run_dir"]) if payload and payload.get("run_dir") else None
        run_json = soak_common.read_run_json(run_dir) if run_dir else {}
        # "terminal status from run.json" -- fall back to the stdout payload
        # only if run.json itself could not be read (e.g. finalize crashed hard).
        status = run_json.get("status") or (payload or {}).get("status")
        result["run_dir"] = str(run_dir) if run_dir else None
        result["status"] = status
        if payload is None:
            result["error"] = f"no stdout JSON (exit {exit_code}): {stderr_text.strip()[:500]}"

        volume = run_json.get("volume")
        # bench_data is None for a `repo` row -- never scored, acceptance_passed stays null.
        if resolved["bench_data"] is not None and volume and status == "completed":
            acceptance = _run_acceptance(resolved["acceptance_dir"], resolved["bench_data"], volume)
            result["acceptance_passed"] = {"pass": True, "fail": False,
                                           "gamed": False}.get(acceptance)  # skipped -> None
        if volume:
            try:
                docker_cli.run(["volume", "rm", volume], timeout=docker_cli.T_LIFECYCLE)
            except Exception:
                pass
        return result
    finally:
        if cleanup_repo_dir and repo_dir is not None:
            shutil.rmtree(repo_dir, ignore_errors=True)


def _dry_run_line(row: dict) -> str:
    label = row.get("label", "?")
    try:
        resolved = _resolve_row(row)
    except Exception as e:
        return f"{label}: ERROR resolving task: {e}"
    repo_display = (f"<fresh copy of {resolved['source_dir']}>" if resolved["stage"]
                    else str(resolved["repo_path"]))
    argv = _build_argv(row.get("model"), row.get("provider"), row.get("base_url"),
                       resolved["task_text"], repo_display, row.get("flags") or [])
    cmd = shlex.join([sys.executable, "-m", "dirtywork"] + argv)
    return f"{label}: {cmd}"


def _existing_labels(out_path: Path) -> set:
    """Labels a prior sweep already resolved -- ONLY rows that reached a real
    `dirtywork run` and got a terminal `status` back (whatever it was:
    `completed`, `max_turns`, `stuck`, ...). `_run_one` writes `status: None`
    for a row whose spawn itself failed (`_resolve_row`/staging/the
    subprocess call raised, or produced no stdout JSON to read a status
    from) -- such a row never got as far as `dirtywork run` doing anything,
    so it must NOT count as done: a fixed plan (a typo, a missing
    bench.json) should be able to resume and retry exactly that row, not
    skip it forever (review item 7)."""
    return {row.get("label") for row in soak_common.load_jsonl(out_path)
            if row.get("label") and row.get("status") is not None}


def _duplicate_labels(rows: list) -> list:
    """Labels that appear more than once among a plan's rows (a row with no
    label is ignored here -- `main`'s loop already skips and reports those
    separately). Sorted for a deterministic error message. Checked upfront
    so two rows can never share a label within one sweep -- the resumability
    check above only looks at the OUT file, computed once before the loop
    starts, so without this a plan containing the same label twice would run
    both copies (review item 7)."""
    seen, dupes = set(), set()
    for row in rows:
        label = row.get("label")
        if not label:
            continue
        if label in seen:
            dupes.add(label)
        seen.add(label)
    return sorted(dupes)


def _append_result(out_path: Path, row: dict) -> None:
    """Same append-only, 0600, symlink-refusing write `dirtywork.bench.cmd_bench`
    uses for its own results file."""
    fd = os.open(str(out_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
        fh.flush()


def _default_out_path(plan_path: Path) -> Path:
    return rundir.BENCH_HOME / f"soak-{plan_path.stem}.jsonl"


def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="soak_driver.py",
        description="Run a soak plan (JSONL) serially through `dirtywork run`.")
    p.add_argument("plan", help="plan file: one JSON row per run (see module docstring)")
    p.add_argument("--out", default=None, metavar="FILE",
                  help="results JSONL path (default: "
                       "~/.dirtywork/bench/soak-<plan file stem>.jsonl)")
    p.add_argument("--dry-run", action="store_true", default=False,
                  help="print the command each row would run, without running anything")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    plan_path = Path(args.plan)
    if not plan_path.is_file():
        print(f"error: no such plan file '{plan_path}'", file=sys.stderr)
        return 2
    rows = soak_common.load_jsonl(plan_path)
    if not rows:
        print(f"error: '{plan_path}' has no rows", file=sys.stderr)
        return 2
    dupes = _duplicate_labels(rows)
    if dupes:
        print(f"error: '{plan_path}' has duplicate labels: {', '.join(dupes)}", file=sys.stderr)
        return 2

    out_path = Path(args.out) if args.out else _default_out_path(plan_path)
    done = _existing_labels(out_path)

    if not args.dry_run:
        rundir.ensure_bench_dir()
        out_path.parent.mkdir(parents=True, exist_ok=True)

    for row in rows:
        label = row.get("label")
        if not label:
            print(f"skip row with no 'label': {row}", file=sys.stderr)
            continue
        if label in done:
            print(f"skip {label}: already in {out_path}", file=sys.stderr)
            continue
        if args.dry_run:
            print(_dry_run_line(row))
            continue
        result = _run_one(row)
        _append_result(out_path, result)
        print(f"{label}: status={result.get('status')} exit={result.get('exit_code')} "
              f"wall_s={result.get('wall_s')} acceptance_passed={result.get('acceptance_passed')}"
              + (f" error={result['error']}" if result.get("error") else ""),
              file=sys.stderr)

    if not args.dry_run:
        print(f"results: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
