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
    """Drive one `dirtywork run` through the real CLI. Imported lazily to
    avoid an import cycle (__main__ imports bench); under `python3 -m
    dirtywork bench` __main__.py aliases its running module as
    `dirtywork.__main__` before this import can happen, so it resolves to the
    module already running instead of executing the file a second time
    (pinned by tests/test_bench.py::test_python_m_dirtywork_bench_does_not_double_load_main).
    Kept as a module attribute so tests can monkeypatch `bench.run_once`."""
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


def _mean(values: list):
    """None for an empty sample -- a bench_error row records no turns, no
    tokens and no verdict, and a mean of nothing is not 0."""
    return (sum(values) / len(values)) if values else None


def _numbers(rows: list, key: str) -> list:
    return [r[key] for r in rows if isinstance(r.get(key), (int, float))]


def _summarize_model(rows: list, verdicts: list, review_seconds: list) -> dict:
    """The one aggregation in this module. `cmd_summarize` calls it per model;
    `--compare` calls it per (model, task) -- same function, different key, so
    the two blocks can never disagree about what a rate means."""
    n = len(rows)
    completed = sum(1 for r in rows if r.get("status") == "completed")
    accepted = sum(1 for r in rows if r.get("acceptance") == "pass")
    gamed = sum(1 for r in rows if r.get("acceptance") == "gamed")
    tokens = [(r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0)
              for r in rows if r.get("prompt_tokens") is not None
              or r.get("completion_tokens") is not None]
    # P2-2: a bench_error row (docker/staging failure before the harness ever
    # ran) carries an empty `harness` dict -- it must not be counted as zero
    # nudges/stalls/max_turns among rows that DID run the harness.
    harness_dicts = [r.get("harness") for r in rows
                     if isinstance(r.get("harness"), dict) and r.get("harness")]
    outcomes = {"pass": 0, "fail": 0, "gamed": 0, "skipped": 0}
    for r in rows:
        acceptance = r.get("acceptance")
        outcomes[acceptance if acceptance in outcomes else "skipped"] += 1
    return {
        "runs": n,
        "completion_rate": completed / n if n else 0.0,
        "acceptance_rate": accepted / n if n else 0.0,
        "gamed": gamed,
        "outcomes": outcomes,
        "mean_tokens": _mean(tokens),
        "mean_wall_s": _mean(_numbers(rows, "wall_s")),
        "verdict_rate": (verdicts.count("accept") / len(verdicts)) if verdicts else None,
        "median_review_seconds": statistics.median(review_seconds) if review_seconds else None,
        # added for `--compare`; the per-model block above ignores them
        "mean_turns": _mean(_numbers(rows, "turns")),
        "mean_prompt_tokens": _mean(_numbers(rows, "prompt_tokens")),
        "mean_completion_tokens": _mean(_numbers(rows, "completion_tokens")),
        "harness_rows": len(harness_dicts),
        "nudges": sum((h.get(f"nudge_{kind}") or 0) for h in harness_dicts for kind in NUDGE_KINDS),
        "stalled": sum(1 for h in harness_dicts if h.get("stalled")),
        "max_turns": sum(1 for h in harness_dicts if h.get("max_turns")),
    }


MISSING = "-"
COMPARE_COLUMNS = ("model", "task", "runs", "turns", "wall_s", "prompt", "completion",
                   "accept", "outcomes", "verdict", "harness")
COMPARE_MODEL_COLUMNS = ("model", "runs", "completion", "accept", "gamed", "tokens",
                         "wall_s", "verdict", "review_s")


def _load_results(path: Path) -> list:
    """Every JSON object in a results JSONL file. Blank and malformed lines are
    skipped: a sweep killed mid-write must still summarize."""
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
    return rows


def _model_key(row: dict) -> str:
    return row.get("model", "?")


def _model_task_key(row: dict) -> tuple:
    return (row.get("model", "?"), row.get("task", "?"))


def _aggregate(rows: list, key) -> dict:
    """{key: _summarize_model(...)} for one results file. Verdicts are re-read
    per row through `_verdict_for` (the operator records them after the sweep),
    exactly as the per-model block does."""
    grouped, verdicts, reviews = {}, {}, {}
    for row in rows:
        k = key(row)
        grouped.setdefault(k, []).append(row)
        verdict, review = _verdict_for(row)
        if verdict:
            verdicts.setdefault(k, []).append(verdict)
        if isinstance(review, (int, float)):
            reviews.setdefault(k, []).append(review)
    return {k: _summarize_model(v, verdicts.get(k, []), reviews.get(k, []))
            for k, v in grouped.items()}


def _fmt_cell(value, kind: str) -> str:
    """One side of a paired cell. None means the file has no rows for this key
    (or no sample for this statistic), and prints as a dash."""
    if value is None:
        return MISSING
    if kind == "pct":
        return f"{value:.0%}"
    if kind == "int":
        return f"{int(value)}"
    return f"{value:.1f}"


def _fmt_delta(a, b, kind: str) -> str:
    """`+N` / `-N` (`0` for no change), or "" when either side is missing --
    there is nothing to subtract from a key one file never ran. The delta is
    computed from the DISPLAYED quantities -- each side snapped to the same
    precision `_fmt_cell` prints -- so a paired cell can never contradict
    itself: 33% -> 67% must show (+34%), the difference of the two displayed
    numbers, not some unrounded value in between."""
    if a is None or b is None:
        return ""
    if kind == "pct":
        diff = round(b * 100) - round(a * 100)
    elif kind == "int":
        diff = int(b) - int(a)
    else:
        diff = round(b, 1) - round(a, 1)
    if diff == 0:
        return "0"
    if kind == "pct":
        text = f"{abs(diff)}%"
    elif kind == "int":
        text = f"{abs(diff)}"
    else:
        text = f"{abs(diff):.1f}"
    return ("+" if diff > 0 else "-") + text


def _compare_cell(a, b, kind: str = "float") -> str:
    """`A -> B (delta)` for one statistic."""
    delta = _fmt_delta(a, b, kind)
    text = f"{_fmt_cell(a, kind)} -> {_fmt_cell(b, kind)}"
    return f"{text} ({delta})" if delta else text


def _stat(summary, key: str):
    """One statistic out of a summary, or None when that file has no such key."""
    return summary.get(key) if summary else None


def _harness_cell(summary) -> str:
    """nudges/stalled/max_turns for one side, compact. MISSING when none of
    this side's rows carried a harness dict (bench_error rows only -- P2-2);
    suffixed with `*` when only SOME of them did, so partial knowledge is
    visible instead of silently reading like a clean zero."""
    if summary is None or summary["harness_rows"] == 0:
        return MISSING
    cell = "/".join(str(n) for n in _harness_counts(summary))
    if summary["harness_rows"] < summary["runs"]:
        cell += "*"
    return cell


def _harness_counts(summary) -> tuple:
    return (summary["nudges"], summary["stalled"], summary["max_turns"])


def _harness_known(summary) -> bool:
    return summary is not None and summary["harness_rows"] > 0


def _outcome_counts(summary) -> tuple:
    o = summary["outcomes"]
    return (o["pass"], o["fail"], o["gamed"], o["skipped"])


def _fmt_component_delta(a: tuple, b: tuple) -> str:
    """Component-wise `+N/-N/0` for count tuples (B - A), same sign convention
    as _fmt_delta -- the legend promises a delta on every paired cell."""
    parts = []
    for x, y in zip(a, b):
        d = y - x
        parts.append("0" if d == 0 else f"{d:+d}")
    return "/".join(parts)


def _paired_counts_cell(left: str, right: str, a: tuple, b: tuple) -> str:
    """`A -> B (dA/dB/...)`; no delta when either side is missing (nothing to
    subtract), like _compare_cell."""
    text = f"{left} -> {right}"
    if a is None or b is None:
        return text
    return f"{text} ({_fmt_component_delta(a, b)})"


def _outcomes_cell(summary) -> str:
    """pass/fail/gamed/skipped for one side, compact (P2-3)."""
    if summary is None:
        return MISSING
    o = summary["outcomes"]
    return f"{o['pass']}/{o['fail']}/{o['gamed']}/{o['skipped']}"


def _harness_partial_footnote(agg_a: dict, agg_b: dict):
    """`* harness data present for N of M runs`, or None when no side of any
    key in the table has partial harness coverage. N/M are summed across
    every summary (either file) that has SOME but not all rows carrying a
    harness dict -- one line covering every `*` in the table rather than one
    line per cell."""
    partial = [s for s in list(agg_a.values()) + list(agg_b.values())
              if 0 < s["harness_rows"] < s["runs"]]
    if not partial:
        return None
    n = sum(s["harness_rows"] for s in partial)
    m = sum(s["runs"] for s in partial)
    return f"* harness data present for {n} of {m} runs"


def _compare_rows(agg_a: dict, agg_b: dict) -> list:
    """One row per (model, task) key present in either file, sorted."""
    rows = []
    for model, task in sorted(set(agg_a) | set(agg_b)):
        a, b = agg_a.get((model, task)), agg_b.get((model, task))
        rows.append({
            "model": model,
            "task": task,
            "runs": _compare_cell(_stat(a, "runs"), _stat(b, "runs"), "int"),
            "turns": _compare_cell(_stat(a, "mean_turns"), _stat(b, "mean_turns")),
            "wall_s": _compare_cell(_stat(a, "mean_wall_s"), _stat(b, "mean_wall_s")),
            "prompt": _compare_cell(_stat(a, "mean_prompt_tokens"),
                                    _stat(b, "mean_prompt_tokens")),
            "completion": _compare_cell(_stat(a, "mean_completion_tokens"),
                                        _stat(b, "mean_completion_tokens")),
            "accept": _compare_cell(_stat(a, "acceptance_rate"),
                                    _stat(b, "acceptance_rate"), "pct"),
            "outcomes": _paired_counts_cell(
                _outcomes_cell(a), _outcomes_cell(b),
                _outcome_counts(a) if a else None, _outcome_counts(b) if b else None),
            "verdict": _compare_cell(_stat(a, "verdict_rate"),
                                     _stat(b, "verdict_rate"), "pct"),
            "harness": _paired_counts_cell(
                _harness_cell(a), _harness_cell(b),
                _harness_counts(a) if _harness_known(a) else None,
                _harness_counts(b) if _harness_known(b) else None),
        })
    return rows


def _compare_model_rows(agg_a: dict, agg_b: dict) -> list:
    """The per-model stats `bench summarize` already prints, paired A -> B."""
    rows = []
    for model in sorted(set(agg_a) | set(agg_b)):
        a, b = agg_a.get(model), agg_b.get(model)
        rows.append({
            "model": model,
            "runs": _compare_cell(_stat(a, "runs"), _stat(b, "runs"), "int"),
            "completion": _compare_cell(_stat(a, "completion_rate"),
                                        _stat(b, "completion_rate"), "pct"),
            "accept": _compare_cell(_stat(a, "acceptance_rate"),
                                    _stat(b, "acceptance_rate"), "pct"),
            "gamed": _compare_cell(_stat(a, "gamed"), _stat(b, "gamed"), "int"),
            "tokens": _compare_cell(_stat(a, "mean_tokens"), _stat(b, "mean_tokens")),
            "wall_s": _compare_cell(_stat(a, "mean_wall_s"), _stat(b, "mean_wall_s")),
            "verdict": _compare_cell(_stat(a, "verdict_rate"),
                                     _stat(b, "verdict_rate"), "pct"),
            "review_s": _compare_cell(_stat(a, "median_review_seconds"),
                                      _stat(b, "median_review_seconds")),
        })
    return rows


def _print_comparison(path_a: Path, rows_a: list, path_b: Path, rows_b: list) -> int:
    agg_a = _aggregate(rows_a, _model_task_key)
    agg_b = _aggregate(rows_b, _model_task_key)
    print(f"A = {path_a}")
    print(f"B = {path_b}")
    print("cells: A -> B (Δ); Δ = B - A (component-wise for count cells); "
          f"'{MISSING}' = no rows for that key in that file; "
          "outcomes = pass/fail/gamed/skipped")
    print("harness: nudges/stalled/max_turns")
    print()
    print(format_table(COMPARE_COLUMNS, _compare_rows(agg_a, agg_b)))
    footnote = _harness_partial_footnote(agg_a, agg_b)
    if footnote:
        print(footnote)
    print()
    print("per-model (A -> B):")
    print(format_table(COMPARE_MODEL_COLUMNS,
                       _compare_model_rows(_aggregate(rows_a, _model_key),
                                           _aggregate(rows_b, _model_key))))
    return 0


def cmd_summarize(args) -> int:
    path = Path(args.file)
    if not path.is_file():
        print(f"error: no such file '{path}'", file=sys.stderr)
        return 2
    rows = _load_results(path)

    # `getattr`, not `args.compare`: existing callers build a Namespace with
    # `file` only (tests/test_bench.py) and must keep working.
    compare = getattr(args, "compare", None)
    if compare:
        other = Path(compare)
        if not other.is_file():
            print(f"error: no such file '{other}'", file=sys.stderr)
            return 2
        return _print_comparison(path, rows, other, _load_results(other))

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
