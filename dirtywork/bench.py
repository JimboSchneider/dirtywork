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
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import rundir
from .runner import FAILURE_KINDS


def run_once(argv):
    """Drive one `dirtywork run` through the real CLI. Imported lazily: a
    module-level `from .__main__ import run_once` would execute __main__.py as
    a second module object under `python3 -m dirtywork bench`. Kept as a
    module attribute so tests can monkeypatch `bench.run_once`."""
    from .__main__ import run_once as _run_once
    return _run_once(argv)
from .runs import _uid_gid, format_table
from .sandbox import docker_args, docker_cli

BENCH_REPOS = Path(__file__).resolve().parent.parent / "bench" / "repos"
BENCH_HOME = rundir.BENCH_HOME
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
    try:
        shutil.copytree(src, dest)
        git_id = ["-c", "user.email=bench@dirtywork.local", "-c", "user.name=dirtywork-bench"]
        subprocess.run(["git", "-C", str(dest), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(dest), *git_id, "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(dest), *git_id, "commit", "-q", "-m", "bench fixture"],
                       check=True)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)   # never leak a half-staged tree
        raise
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
        *docker_args.security_args(ACCEPTANCE_PIDS),  # --pids-limit/--read-only/--cap-drop ALL/no-new-privileges, shared with worker+export containers
        "--user", f"{uid}:{gid}",
        "--memory", ACCEPTANCE_MEMORY, "--memory-swap", ACCEPTANCE_MEMORY,
        "--cpus", ACCEPTANCE_CPUS,
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
    """'pass' | 'fail' | 'gamed' | 'skipped'. Never raises: a docker failure, OR
    a bench.json missing/malformed `acceptance.hashes`/`acceptance.command`,
    degrades to 'skipped' rather than aborting the whole bench sweep."""
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
                                      BENCH_REPOS / task / "acceptance", command),
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
    wall_start = time.monotonic()

    def bench_error(e: Exception) -> dict:
        # One bad case degrades to a recorded row; the sweep continues (same
        # contract as _run_acceptance's "never raises").
        return {"stamp": stamp, "model": model, "task": task, "repeat": repeat,
                "provider": provider, "base_url": base_url, "slug": None, "run_dir": None,
                "status": "bench_error", "error": str(e), "turns": None,
                "prompt_tokens": None, "completion_tokens": None,
                "wall_s": round(time.monotonic() - wall_start, 1),
                "acceptance": "skipped", "guardrail_blocks": 0, "sandbox_resets": 0,
                "diff_stat": None, "harness": {}, "verdict": None, "review_seconds": None}

    repo_dir = None
    try:
        bench_data = _bench_json(task)
        repo_dir = _stage_repo(task)
    except Exception as e:  # staging: bad bench.json, git missing/misconfigured, disk full
        if repo_dir is not None:
            shutil.rmtree(repo_dir, ignore_errors=True)
        return bench_error(e)
    argv = ["run", bench_data["task"], "--repo", str(repo_dir), "--model", model,
            "--sandbox", "docker", "--keep-volume",
            "--max-turns", str(max_turns), "--timeout", str(timeout)]
    if provider:
        argv += ["--provider", provider]
    if base_url:
        argv += ["--base-url", base_url]

    try:
        payload = run_once(argv)
    except Exception as e:
        shutil.rmtree(repo_dir, ignore_errors=True)
        return bench_error(e)
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
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        out_path = rundir.ensure_bench_dir(BENCH_HOME) / f"{stamp}.jsonl"
    fd = os.open(str(out_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
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


DETAIL_COLUMNS = ("model", "task", "rep", "status", "turns", "wall_s", "prompt",
                  "completion", "accept", "verdict", "review_s", "nudges", "failures")


def _verdict_for(row: dict) -> tuple:
    """(verdict, review_seconds) for a result row, re-read from run.json at
    summarize time -- the operator usually records a verdict long after the bench
    sweep, so the value stored in the row itself is only a fallback."""
    slug = row.get("slug")
    if slug:
        run_dir = Path(rundir.RUNS_DIR) / slug
        if run_dir.is_dir():
            try:
                data = rundir.read_run_json(run_dir)
            except (OSError, ValueError):
                data = None
            if isinstance(data, dict):        # a corrupt/non-object run.json falls back to the row
                return data.get("verdict"), data.get("review_seconds")
    return row.get("verdict"), row.get("review_seconds")


def _failure_cell(harness: dict) -> str:
    parts = [name for name in ("stalled", "max_turns", "sandbox_error") if harness.get(name)]
    if harness.get("empty_reply"):
        parts.append(f"empty_reply={harness['empty_reply']}")
    if harness.get("abort_kind"):
        parts.append(f"abort={harness['abort_kind']}")
    return ",".join(parts) if parts else "-"


def _detail_row(row: dict, verdict, review_seconds) -> dict:
    harness = row.get("harness") or {}
    nudges = "/".join(str(harness.get(f"nudge_{kind}", 0)) for kind in NUDGE_KINDS)
    return {
        "model": row.get("model", "?"), "task": row.get("task", "?"),
        "rep": row.get("repeat", 0), "status": row.get("status", "?"),
        "turns": "-" if row.get("turns") is None else row["turns"],
        "wall_s": "-" if row.get("wall_s") is None else row["wall_s"],
        "prompt": "-" if row.get("prompt_tokens") is None else row["prompt_tokens"],
        "completion": "-" if row.get("completion_tokens") is None else row["completion_tokens"],
        "accept": row.get("acceptance", "-"),
        "verdict": verdict or "-",
        "review_s": "-" if review_seconds is None else review_seconds,
        "nudges": nudges, "failures": _failure_cell(harness),
    }


def _summarize_model(rows: list, verdicts: list, review_seconds: list) -> dict:
    n = len(rows)
    completed = sum(1 for r in rows if r.get("status") == "completed")
    accepted = sum(1 for r in rows if r.get("acceptance") == "pass")
    gamed = sum(1 for r in rows if r.get("acceptance") == "gamed")
    tokens = [(r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0)
              for r in rows if r.get("prompt_tokens") is not None
              or r.get("completion_tokens") is not None]
    walls = [r["wall_s"] for r in rows if isinstance(r.get("wall_s"), (int, float))]
    return {
        "runs": n,
        "completion_rate": completed / n if n else 0.0,
        "acceptance_rate": accepted / n if n else 0.0,
        "gamed": gamed,
        "mean_tokens": (sum(tokens) / len(tokens)) if tokens else None,
        "mean_wall_s": (sum(walls) / len(walls)) if walls else None,
        "verdict_rate": (verdicts.count("accept") / len(verdicts)) if verdicts else None,
        "median_review_seconds": statistics.median(review_seconds) if review_seconds else None,
    }


def cmd_summarize(args) -> int:
    path = Path(args.file)
    if not path.is_file():
        print(f"error: no such file '{path}'", file=sys.stderr)
        return 2
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)

    detail, by_model, verdicts, reviews = [], {}, {}, {}
    for row in rows:
        model = row.get("model", "?")
        verdict, review = _verdict_for(row)
        detail.append(_detail_row(row, verdict, review))
        by_model.setdefault(model, []).append(row)
        if verdict:
            verdicts.setdefault(model, []).append(verdict)
        if isinstance(review, (int, float)):
            reviews.setdefault(model, []).append(review)

    if detail:
        print("nudges: stall/empty/truncated/text_tool_call")
        print(format_table(DETAIL_COLUMNS, detail))
        print()

    for model in sorted(by_model):
        summary = _summarize_model(by_model[model], verdicts.get(model, []),
                                   reviews.get(model, []))
        print(f"model: {model}")
        print(f"  runs: {summary['runs']}")
        print(f"  completion rate: {summary['completion_rate']:.0%}")
        print(f"  acceptance rate: {summary['acceptance_rate']:.0%}")
        print(f"  gamed: {summary['gamed']}")
        print(f"  mean tokens: {summary['mean_tokens']:.1f}" if summary["mean_tokens"] is not None
              else "  mean tokens: n/a")
        print(f"  mean wall_s: {summary['mean_wall_s']:.1f}" if summary["mean_wall_s"] is not None
              else "  mean wall_s: n/a")
        if summary["verdict_rate"] is not None:
            print(f"  verdict rate: {summary['verdict_rate']:.0%}")
            print(f"  median review_seconds: {summary['median_review_seconds']:g}"
                  if summary["median_review_seconds"] is not None
                  else "  median review_seconds: n/a")
        print()
    return 0


def dispatch(args) -> int:
    """`main()` routes `dirtywork bench ...` here."""
    if getattr(args, "bench_cmd", None) == "summarize":
        return cmd_summarize(args)
    return cmd_bench(args)
