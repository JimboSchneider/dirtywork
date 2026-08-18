"""`dirtywork bench` -- run every (model x task x repeat) through the normal
`dirtywork run` path and score the result (spec SP3 section 5).

Runs from a source checkout: `bench/` is not part of the installed package.

Two containers are involved per case and neither is the worker's own:
the run itself (created by `dirtywork run --sandbox docker --keep-volume`) and,
afterwards, a fresh acceptance container with the run's volume at /work and this
checkout's `bench/repos/<task>/acceptance/` mounted read-only at /acceptance.
The acceptance COMMAND always comes from /acceptance; the worker's own copy under
/work/acceptance is only ever hashed, so tampering with it marks the run `gamed`.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import rundir
from .__main__ import run_once
from .runner import FAILURE_KINDS
from .runs import _uid_gid
from .sandbox import docker_args, docker_cli

BENCH_REPOS = Path(__file__).resolve().parent.parent / "bench" / "repos"
BENCH_HOME = Path.home() / ".dirtywork" / "bench"
NUDGE_KINDS = ("stall", "empty", "truncated", "text_tool_call")
ACCEPTANCE_MEMORY = "2g"
ACCEPTANCE_CPUS = "2"
ACCEPTANCE_PIDS = 256
_ABORT_RE = re.compile(r"aborted after \d+ consecutive (\S+) (?:tool )?failures")


def _bench_json(task: str) -> dict:
    return json.loads((BENCH_REPOS / task / "bench.json").read_text())


def available_tasks() -> list:
    if not BENCH_REPOS.is_dir():
        return []
    return sorted(d.name for d in BENCH_REPOS.iterdir() if (d / "bench.json").is_file())


def parse_model_spec(spec: str, default_provider=None, default_base_url=None):
    """`model[@provider][=base_url]` -> (model, provider|None, base_url|None).

    A model name may contain `/`, `-`, `.` and `:` (`qwen/qwen3-coder-next`,
    `mistralai/devstral-small-2-2512`) but never `@` or `=`, so splitting on
    those two characters is unambiguous. Anything omitted falls back to the
    bench-wide `--provider`/`--base-url`, and if those are unset the flag is not
    passed to `dirtywork run` at all -- `run`'s own defaults then apply, so bench
    never has to hardcode a provider name."""
    rest, sep, base_url = spec.partition("=")
    base_url = base_url.strip() if sep else (default_base_url or "")
    head, at, tail = rest.rpartition("@")
    if at:
        model, provider = head.strip(), tail.strip()
    else:
        model, provider = rest.strip(), (default_provider or "")
    return model, (provider or None), (base_url or None)


def _stage_repo(task: str) -> Path:
    """Copy bench/repos/<task> into a uniquely named temp dir and commit it.
    Docker Desktop caches deleted bind-mount source paths (spec SP2 section 8),
    so bench must never reuse a path -- mkdtemp's random suffix guarantees a
    fresh one every call."""
    src = BENCH_REPOS / task
    dest = Path(tempfile.mkdtemp(prefix=f"dwbench-{task}-"))
    shutil.rmtree(dest)                     # mkdtemp created it; copytree needs it absent
    shutil.copytree(src, dest)
    git_id = ["-c", "user.email=bench@dirtywork.local", "-c", "user.name=dirtywork-bench"]
    subprocess.run(["git", "-C", str(dest), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(dest), *git_id, "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(dest), *git_id, "commit", "-q", "-m", "bench fixture"],
                   check=True)
    return dest


def _acceptance_base_argv(volume: str, uid: int, gid: int, extra_mounts=()) -> list:
    """Shared shape of both post-run containers: no network, no capabilities,
    read-only rootfs with a single writable /tmp tmpfs, the run's volume at /work,
    and an explicit PATH (nothing in the image's own launch config is trusted).
    No `-w`: a container-level workdir over the volume is the verified ownership
    bug called out in docker/Dockerfile, so the command cd's instead."""
    argv = [
        "run", "--rm",
        "--network", "none",
        "--user", f"{uid}:{gid}",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--memory", ACCEPTANCE_MEMORY, "--memory-swap", ACCEPTANCE_MEMORY,
        "--cpus", ACCEPTANCE_CPUS,
        "--pids-limit", str(ACCEPTANCE_PIDS),
        "--tmpfs", "/tmp:rw,exec,size=256m,mode=1777",
        "--mount", f"type=volume,src={volume},dst=/work",
        "-e", f"PATH={docker_args.PATH_ENV}",
        "-e", "HOME=/tmp",
        "-e", "TMPDIR=/tmp",
        "-e", "LANG=C.UTF-8",
    ]
    for mount in extra_mounts:
        argv += ["--mount", mount]
    return argv


def _hash_check_argv(volume: str, image_ref: str, uid: int, gid: int, paths) -> list:
    return (_acceptance_base_argv(volume, uid, gid)
            + ["--entrypoint", "/usr/bin/sha256sum", image_ref] + list(paths))


def _acceptance_run_argv(volume: str, image_ref: str, uid: int, gid: int,
                         acceptance_dir: Path, command: str) -> list:
    mounts = [f"type=bind,src={Path(acceptance_dir).resolve()},dst=/acceptance,readonly"]
    return (_acceptance_base_argv(volume, uid, gid, extra_mounts=mounts)
            + ["--entrypoint", "/bin/sh", image_ref, "-c", f"cd /work && {command}"])


def _run_acceptance(task: str, bench_data: dict, volume: str, *, run=docker_cli.run) -> str:
    """'pass' | 'fail' | 'gamed' | 'skipped'. Never raises: a docker failure
    degrades to 'skipped' rather than aborting the whole bench sweep."""
    image = docker_args.DEFAULT_IMAGE
    try:
        image_ref = docker_cli.resolve_image(image, pinned_digest=docker_args.pin_for(image))
    except Exception:
        return "skipped"
    uid, gid = _uid_gid()
    hashes = bench_data["acceptance"]["hashes"]

    try:
        cp = run(_hash_check_argv(volume, image_ref, uid, gid,
                                  [f"/work/{p}" for p in hashes]),
                 timeout=docker_cli.T_EXPORT_STEP)
    except Exception:
        return "skipped"
    if cp.returncode not in (0, 1):
        return "skipped"      # docker itself failed (125/126/127), not the worker's doing
    # rc 1 = sha256sum could not read a harness file (the worker deleted or moved
    # it) -- that is a mismatch and falls through to "gamed" below.
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
        cp = run(_acceptance_run_argv(volume, image_ref, uid, gid,
                                      BENCH_REPOS / task / "acceptance",
                                      bench_data["acceptance"]["command"]),
                 timeout=docker_cli.T_EXPORT_STEP)
    except Exception:
        return "skipped"
    return "pass" if cp.returncode == 0 else "fail"


def _event_counts(run_dir) -> dict:
    """One pass over the transcript: nudges by kind plus the two whole-run
    event counters."""
    counts = {"guardrail_block": 0, "sandbox_reset": 0, "nudge_other": 0}
    counts.update({f"nudge_{kind}": 0 for kind in NUDGE_KINDS})
    if run_dir is None:
        return counts
    transcript_path = Path(run_dir) / "transcript.jsonl"
    if not transcript_path.is_file():
        return counts
    for line in transcript_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        name = event.get("event")
        if name in ("guardrail_block", "sandbox_reset"):
            counts[name] += 1
        elif name == "nudge":
            key = f"nudge_{event.get('kind')}"
            counts[key if key in counts else "nudge_other"] += 1
    return counts


def _abort_kind(final_message):
    """Which FailureTracker abort ended a `model_error` run, read back out of the
    final message the runner produced (`FailureTracker.record`)."""
    if not isinstance(final_message, str):
        return None
    match = _ABORT_RE.search(final_message)
    if match is None:
        return None
    kind = match.group(1)
    if kind in FAILURE_KINDS:
        return kind
    return "mixed" if kind == "tool" else None


def _harness_failures(counts: dict, status, final_message) -> dict:
    """The harness-failure classes the scoreboard reports. `empty_reply` is the
    FailureTracker kind: the runner records exactly one per non-stall nudge."""
    non_stall = sum(counts[f"nudge_{kind}"] for kind in NUDGE_KINDS if kind != "stall")
    failures = {f"nudge_{kind}": counts[f"nudge_{kind}"] for kind in NUDGE_KINDS}
    failures["nudge_other"] = counts["nudge_other"]
    failures["empty_reply"] = non_stall
    for name in ("stalled", "max_turns", "sandbox_error"):
        failures[name] = 1 if status == name else 0
    failures["abort_kind"] = _abort_kind(final_message)
    return failures


def run_one_bench_case(model: str, task: str, repeat: int, *, provider, base_url, stamp,
                       max_turns: int, timeout: int) -> dict:
    bench_data = _bench_json(task)
    repo_dir = _stage_repo(task)
    argv = ["run", bench_data["task"], "--repo", str(repo_dir), "--model", model,
            "--sandbox", "docker", "--keep-volume",
            "--max-turns", str(max_turns), "--timeout", str(timeout)]
    if provider:
        argv += ["--provider", provider]
    if base_url:
        argv += ["--base-url", base_url]

    wall_start = time.monotonic()
    try:
        payload = run_once(argv)
    except Exception as e:
        shutil.rmtree(repo_dir, ignore_errors=True)
        return {"stamp": stamp, "model": model, "task": task, "repeat": repeat,
                "provider": provider, "base_url": base_url, "slug": None, "run_dir": None,
                "status": "bench_error", "error": str(e), "turns": None,
                "prompt_tokens": None, "completion_tokens": None,
                "wall_s": round(time.monotonic() - wall_start, 1),
                "acceptance": "skipped", "guardrail_blocks": 0, "sandbox_resets": 0,
                "diff_stat": None, "harness": {}, "verdict": None, "review_seconds": None}
    wall_s = round(time.monotonic() - wall_start, 1)

    run_dir = Path(payload["run_dir"]) if payload.get("run_dir") else None
    slug = run_dir.name if run_dir is not None else None
    run_json = {}
    if run_dir is not None and run_dir.is_dir():
        try:
            run_json = rundir.read_run_json(run_dir)
        except (OSError, ValueError):
            run_json = {}

    status = payload.get("status")
    volume = run_json.get("volume")
    acceptance = "skipped"
    try:
        if volume and status == "completed":
            acceptance = _run_acceptance(task, bench_data, volume)
        if volume:
            try:
                docker_cli.run(["volume", "rm", volume], timeout=docker_cli.T_LIFECYCLE)
            except Exception:
                pass
    finally:
        shutil.rmtree(repo_dir, ignore_errors=True)

    usage = payload.get("usage") or {}
    counts = _event_counts(run_dir)
    return {
        "stamp": stamp, "model": model, "task": task, "repeat": repeat,
        "provider": payload.get("provider", provider), "base_url": base_url,
        "slug": slug, "run_dir": str(run_dir) if run_dir else None,
        "status": status, "turns": payload.get("turns"), "wall_s": wall_s,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "acceptance": acceptance,
        "guardrail_blocks": counts["guardrail_block"],
        "sandbox_resets": counts["sandbox_reset"],
        "diff_stat": run_json.get("diff_stat"),
        "harness": _harness_failures(counts, status, payload.get("final_message")),
        "verdict": run_json.get("verdict"),
        "review_seconds": run_json.get("review_seconds"),
    }


def cmd_bench(args) -> int:
    if not args.models:
        print("error: --models is required (e.g. --models qwen/qwen3-coder-next,"
              "other-model@anthropic)", file=sys.stderr)
        return 2
    specs = [s.strip() for s in args.models.split(",") if s.strip()]
    tasks = ([t.strip() for t in args.tasks.split(",") if t.strip()] if args.tasks
             else available_tasks())
    if not tasks:
        print(f"error: no bench fixtures found under {BENCH_REPOS}", file=sys.stderr)
        return 2
    unknown = [t for t in tasks if not (BENCH_REPOS / t / "bench.json").is_file()]
    if unknown:
        print(f"error: unknown bench task(s): {', '.join(unknown)}; available: "
              f"{', '.join(available_tasks())}", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out) if args.out else (BENCH_HOME / f"{stamp}.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a", encoding="utf-8") as fh:
        for spec in specs:
            model, provider, base_url = parse_model_spec(spec, args.provider, args.base_url)
            for task in tasks:
                for repeat in range(args.repeats):
                    row = run_one_bench_case(model, task, repeat, provider=provider,
                                             base_url=base_url, stamp=stamp,
                                             max_turns=args.max_turns, timeout=args.timeout)
                    fh.write(json.dumps(row) + "\n")
                    fh.flush()
                    print(f"{model}  {task}  repeat {repeat}: {row.get('status')} / "
                          f"acceptance {row.get('acceptance')}", file=sys.stderr)
    print(f"results: {out_path}")
    return 0


def dispatch(args) -> int:
    """`main()` routes `dirtywork bench ...` here."""
    return cmd_bench(args)
