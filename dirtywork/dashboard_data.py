"""Dashboard projections and confined review metadata updates; no tool execution."""
from __future__ import annotations

import json
import math
import os
import re
import stat
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from . import rundir
from .osfs import open_nofollow
from .review import start_review, record_verdict, effective_run_status, review_eligible

MAX_LINE = 1024 * 1024
MAX_LIVE_RECORD = 4 * 1024 * 1024
ACTIVITY_LIMIT = 60
TIMELINE_LIMIT = 2000
TIMELINE_LANES = {"run_start": "lifecycle", "run_end": "lifecycle",
                  "assistant": "model", "tool_result": "tools", "verify": "verify",
                  "nudge": "harness", "guardrail_block": "harness",
                  "firewall_denial": "harness", "sandbox_reset": "harness", "stray_kill": "harness"}


def number(value):
    return value if (isinstance(value, (int, float)) and not isinstance(value, bool)
                     and 0 <= value <= 2 ** 53 - 1 and math.isfinite(value)) else None


def text(value, limit=4000):
    return value[:limit] if isinstance(value, str) else ""


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def _open_regular(path):
    fd = open_nofollow(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError("not a regular file")
    return os.fdopen(fd, "rb")


def _diff_rows(patch):
    rows, old, new = [], None, None
    for line in patch.split("\n"):
        match = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$", line)
        if match:
            old, new = int(match[1]), int(match[2])
            rows.append({"kind": "hunk", "text": match[3].strip(), "old": None, "new": None})
        elif old is not None and line[:1] in ("+", "-", " "):
            kind = {"+": "add", "-": "remove", " ": "context"}[line[0]]
            rows.append({"kind": kind, "text": line[1:],
                         "old": old if kind != "add" else None,
                         "new": new if kind != "remove" else None})
            old += kind != "add"
            new += kind != "remove"
        elif line.startswith("\\ No newline"):
            rows.append({"kind": "notice", "text": line[2:], "old": None, "new": None})
    return rows


def _patch_files(patch):
    files = []
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            path = line.partition(" b/")[2] or line[11:]
            files.append({"path": path[:4000], "diff": line + "\n"})
        elif files:
            files[-1]["diff"] += line + "\n"
    for file in files:
        file["rows"] = _diff_rows(file["diff"])
        file["additions"] = sum(row["kind"] == "add" for row in file["rows"])
        file["deletions"] = sum(row["kind"] == "remove" for row in file["rows"])
        file["change_type"] = ("New file" if "\nnew file mode " in file["diff"] else
                               "Deleted file" if "\ndeleted file mode " in file["diff"] else "Modified")
    return files[:1000]


def _live_changes(directory, active):
    """Return a confined snapshot projection, or None when no capture exists."""
    unavailable = {"files": [], "message": "Live preview unavailable: capture could not be read safely.",
                   "source": None, "live": False, "captured_at": None}
    try:
        with _open_regular(directory / "live_changes.json") as source:
            raw = source.read(MAX_LIVE_RECORD + 1)
        if len(raw) > MAX_LIVE_RECORD:
            raise ValueError("live record exceeds preview limit")
        capture = json.loads(raw)
        if (not isinstance(capture, dict) or type(capture.get("version")) is not int
                or capture["version"] != 1 or capture.get("state") not in ("ready", "too_large", "unavailable")
                or not isinstance(capture.get("patch"), str) or not isinstance(capture.get("message"), str)
                or not isinstance(capture.get("captured_at"), str)):
            raise ValueError("invalid live capture")
        captured = datetime.fromisoformat(capture["captured_at"].replace("Z", "+00:00"))
        if captured.tzinfo is None or captured.utcoffset().total_seconds() != 0:
            raise ValueError("capture timestamp is not UTC")
        if len(capture["patch"].encode("utf-8")) > MAX_LINE:
            raise ValueError("live patch exceeds preview limit")
    except FileNotFoundError:
        return None
    except (OSError, ValueError, UnicodeError, RecursionError, OverflowError):
        return unavailable
    result = {"files": [], "message": "", "source": "live", "live": False,
              "captured_at": capture["captured_at"][:80]}
    if capture["state"] != "ready":
        result["message"] = ("Live preview unavailable. " + text(capture["message"]) if capture["state"] == "unavailable"
                             else text(capture["message"]) or "Changes exceed the 1 MiB preview limit.")
        return result
    result["files"] = _patch_files(capture["patch"])
    result["live"] = active
    result["message"] = (("Live file changes · updates after worker actions." if result["files"] else
                          "Live file changes · no changes captured yet.") if active else
                         "Last live snapshot; no saved patch is available for this run.")
    return result


def _signature(path):
    try:
        s = path.lstat()
        return s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns
    except OSError:
        return None


def _verify(value):
    if not isinstance(value, dict):
        return None
    passed = value.get("passed")
    return {"passed": passed if isinstance(passed, bool) else None,
            "exit_code": value.get("exit_code") if type(value.get("exit_code")) is int else None,
            "command": text(value.get("command")),
            "output_tail": text(value.get("output_tail"))}


def _event(event, sequence):
    """Bound browser-visible evidence, excluding assistant prose and file bodies."""
    kind = text(event.get("event"), 80)
    tool = text(event.get("tool"), 201)
    args = event.get("args")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (ValueError, RecursionError):
            pass  # Truncated argument previews remain useful as plain text.
    if isinstance(args, dict):
        target = text(args.get("path")) or text(args.get("command"))
    else:
        target = text(args, 500)
    detail = text(event.get("result"), 2000)
    if kind == "assistant":
        calls = event.get("tool_calls")
        detail = ", ".join(text(c.get("name"), 201) for c in calls[:12]
                           if isinstance(c, dict)) if isinstance(calls, list) else ""
    elif kind == "verify":
        detail = ("Passed" if event.get("passed") is True else "Failed") + \
                 " · exit " + str(event.get("exit_code"))
    elif kind == "run_end":
        detail = text(event.get("status"))
    elif kind == "nudge":
        detail = text(event.get("kind"))
    return {"event": kind, "sequence": sequence, "ts": text(event.get("ts"), 80),
            "tool": tool, "target": target, "detail": detail}


def _timeline(transcript, metadata, started, ended, now, activity_state):
    events = list(transcript["timeline"])
    if metadata.get("review_started_at"):
        events.append({"sequence": "review-start", "ts": metadata["review_started_at"],
                       "lane": "review", "label": "Review started", "outcome": "recorded"})
    if metadata.get("verdict") in ("accept", "reject", "cleanup"):
        events.append({"sequence": "review", "ts": metadata.get("verdict_at"),
                       "lane": "review", "label": "Review: " + metadata["verdict"],
                       "outcome": "failed" if metadata["verdict"] == "reject" else "recorded"})
    timed = [(event, timestamp(event.get("ts"))) for event in events]
    origin = started or min((ts for _, ts in timed if ts), default=None)
    points = []
    untimed = 0
    for event, ts in timed:
        if not ts or not origin or ts < origin:
            untimed += 1
            continue
        points.append({**{k: v for k, v in event.items() if k != "ts"},
                       "at_s": (ts - origin).total_seconds()})
    points.sort(key=lambda point: point["at_s"])
    end = now if activity_state == "running" else ended
    extent = max([point["at_s"] for point in points if point["lane"] != "review"] +
                 ([(end - origin).total_seconds()] if end and origin and end >= origin else []), default=None)
    return {"points": points, "elapsed_s": extent, "untimed_events": untimed,
            "omitted_events": max(0, transcript["timeline_total"] - TIMELINE_LIMIT),
            "origin": "run start" if started else "first recorded event" if origin else None}


class RunStore:
    def __init__(self, runs_dir):
        self.root = Path(runs_dir).expanduser().resolve()
        self._transcripts = {}
        self._lock = threading.RLock()

    def review_action(self, slug, action, note=""):
        """Serialize browser updates against fresh metadata, using CLI transitions."""
        with self._lock:
            try:
                directory = rundir.run_dir_for(slug, self.root)
            except rundir.RunDirError as e:
                raise ValueError("Invalid run slug.") from e
            if directory.is_symlink():
                raise ValueError("Symlinked run directories cannot be reviewed.")
            if not directory.is_dir():
                raise FileNotFoundError("Run no longer exists.")
            try:
                with _open_regular(directory / "run.json") as source:
                    raw = source.read(MAX_LINE + 1)
                if len(raw) > MAX_LINE:
                    raise ValueError("Run metadata is too large.")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("Run metadata is not an object.")
            except (OSError, ValueError, RecursionError) as e:
                raise RuntimeError("Run metadata cannot be safely updated.") from e
            end = self._transcript(directory)["end"]
            status, activity = effective_run_status(data, end)
            if not review_eligible(status, activity) or data.get("resumed_by"):
                raise RuntimeError("This run is still running, unknown, or superseded; refresh to see its state.")
            now = datetime.now(timezone.utc)
            if action == "start":
                try:
                    updated = start_review(data, now, end=end)
                except ValueError as e:
                    raise RuntimeError(str(e)) from e
            else:
                if data.get("verdict"):
                    if data["verdict"] == action:
                        return self._detail(slug, now)
                    raise RuntimeError("This run already has a verdict; refresh to see it.")
                if not data.get("review_started_at"):
                    raise RuntimeError("Start review before accepting or rejecting the run.")
                try:
                    updated = record_verdict(data, action, note or None, None, now)
                except ValueError as e:
                    raise RuntimeError(str(e)) from e
            if updated != data:
                try:
                    rundir.write_run_json(directory, updated, expected_signature=rundir.run_json_signature(data))
                except rundir.RunJsonConflict as e:
                    raise RuntimeError("Run changed; refresh before saving your review.") from e
            return self._detail(slug, now)

    def _transcript(self, directory):
        path = directory / "transcript.jsonl"
        signature = _signature(path)
        cached = self._transcripts.get(directory.name)
        if cached and cached[0] == signature:
            return cached[1]
        result = {"turns": 0, "tool_calls": 0, "activity": [], "start": {}, "end": {},
                  "verify": None, "warnings": [], "available": False,
                  "timeline": [], "timeline_total": 0}
        activity = deque(maxlen=ACTIVITY_LIMIT)
        timeline = deque(maxlen=TIMELINE_LIMIT)
        invalid = 0
        sequence = 0
        try:
            with _open_regular(path) as source:
                result["available"] = True
                while True:
                    line = source.readline(MAX_LINE + 1)
                    if not line:
                        break
                    if len(line) > MAX_LINE:
                        while line and not line.endswith(b"\n"):
                            line = source.readline(MAX_LINE + 1)
                        invalid += 1
                        continue
                    if not line.endswith(b"\n"):
                        result["warnings"].append("Transcript has an incomplete final line; waiting for its flush.")
                        break
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
                            raise ValueError("invalid event")
                    except (ValueError, UnicodeError, RecursionError):
                        invalid += 1
                        continue
                    kind = event["event"]
                    if kind == "run_start":
                        result["start"] = event
                    result["turns"] += kind == "assistant"
                    result["tool_calls"] += kind == "tool_result"
                    if kind == "run_end":
                        result["end"] = event
                    if kind == "verify":
                        result["verify"] = _verify(event)
                    activity.append(_event(event, sequence))
                    if kind in TIMELINE_LANES:
                        timeline.append({"sequence": sequence, "ts": text(event.get("ts"), 80),
                                         "lane": TIMELINE_LANES[kind],
                                         "label": text(event.get("tool"), 201) or kind.replace("_", " "),
                                         "outcome": "failed" if (kind == "verify" and event.get("passed") is False)
                                         or kind in ("guardrail_block", "firewall_denial") else "recorded"})
                        result["timeline_total"] += 1
                    sequence += 1
        except (OSError, ValueError) as e:
            result["warnings"].append("Transcript unavailable: " + str(e))
        if invalid:
            result["warnings"].append(f"Skipped {invalid} invalid or oversized transcript records.")
        result["activity"] = list(activity)
        result["timeline"] = list(timeline)
        self._transcripts[directory.name] = signature, result
        return result

    def changes(self, slug):
        """Read bounded regular captures and exports; never execute git."""
        with self._lock:
            run = self._detail(slug, datetime.now(timezone.utc))
            directory = rundir.run_dir_for(slug, self.root)
            if run["activity_state"] == "running":
                return _live_changes(directory, True) or {
                    "files": [], "message": "Waiting for the next capture of live file changes.",
                    "source": None, "live": False, "captured_at": None}
            try:
                with _open_regular(directory / "diff.patch") as source:
                    raw = source.read(MAX_LINE + 1)
                if len(raw) > MAX_LINE:
                    return {"files": [], "message": "Patch exceeds the 1 MiB preview limit.",
                            "source": "saved", "live": False, "captured_at": None}
                patch = raw.decode("utf-8", errors="replace")
            except (OSError, ValueError):
                return _live_changes(directory, False) or {
                    "files": [], "message": ("Host-mode run has no saved patch; inspect git diff in the worktree."
                                              if run["sandbox"] == "none" else
                                              "No saved patch is available for this run."),
                    "source": None, "live": False, "captured_at": None}
            files = _patch_files(patch)
            return {"files": files, "message": "Saved changes from this run." if files else "No file changes in the saved patch.",
                    "source": "saved", "live": False, "captured_at": None}

    def detail(self, slug, *, now=None):
        with self._lock:
            return self._detail(slug, now or datetime.now(timezone.utc))

    def _detail(self, slug, now):
        try:
            directory = rundir.run_dir_for(slug, self.root)
        except rundir.RunDirError as e:
            raise ValueError(str(e)) from e
        if directory.is_symlink():
            raise ValueError("symlinked run directory")
        if not directory.is_dir():
            raise FileNotFoundError(slug)
        warnings = []
        try:
            with _open_regular(directory / "run.json") as source:
                raw = source.read(MAX_LINE + 1)
            if len(raw) > MAX_LINE:
                raise ValueError("run.json exceeds 1 MiB")
            metadata = json.loads(raw)
            if not isinstance(metadata, dict):
                raise ValueError("run.json is not an object")
        except (OSError, ValueError, UnicodeError, RecursionError) as e:
            metadata = {}
            warnings.append("Run metadata unavailable: " + str(e))
        transcript = self._transcript(directory)
        start = transcript["start"]
        metadata = {**start, **metadata}
        end = transcript["end"]
        status, activity_state = effective_run_status(metadata, end)
        started = timestamp(metadata.get("started")) or timestamp(start.get("ts"))
        ended = timestamp(metadata.get("ended")) or timestamp(end.get("ts"))
        duration = number(end.get("duration_s"))
        if duration is None and started and ended:
            duration = number((ended - started).total_seconds())
        if duration is None and started and activity_state == "running":
            duration = number((now - started).total_seconds())
        usage = end.get("usage")
        usage = ({k: number(usage.get(k)) for k in ("prompt_tokens", "completion_tokens")}
                 if isinstance(usage, dict) else None)
        turns = number(metadata.get("turns"))
        if turns is None:
            turns = number(end.get("turns"))
        if turns is None and transcript["available"]:
            turns = transcript["turns"]
        files = metadata.get("files_changed", end.get("files_changed"))
        verdict = metadata.get("verdict")
        review_started = timestamp(metadata.get("review_started_at"))
        reviewing = bool(review_started and review_started <= now and verdict is None and not metadata.get("resumed_by"))
        run = {k: text(metadata.get(k)) for k in
               ("task", "model", "provider", "repo", "branch", "sandbox", "resumed_from", "resumed_by", "note")}
        run["task"] = text(metadata.get("task"), 64000)
        if isinstance(metadata.get("sandbox"), dict):
            run["sandbox"] = text(metadata["sandbox"].get("backend"))
        run.update({"slug": slug, "status": status, "activity_state": activity_state,
                    "started": started.isoformat() if started else None,
                    "ended": ended.isoformat() if ended else None,
                    "duration_s": duration, "turns": turns,
                    "tool_calls": transcript["tool_calls"] if transcript["available"] else None,
                    "usage": usage, "verdict": verdict if verdict in ("accept", "reject", "cleanup") else None,
                    "review_seconds": number(metadata.get("review_seconds")),
                    "review_started_at": review_started.isoformat() if review_started else None,
                    "review_in_progress": reviewing,
                    "review_eligible": review_eligible(status, activity_state) and not metadata.get("resumed_by"),
                    "review_elapsed_s": (now - review_started).total_seconds() if reviewing else None,
                    "verify": _verify(metadata.get("verify")) or transcript["verify"] or _verify(end.get("verify")),
                    "files_changed": [text(p) for p in files[:1000] if isinstance(p, str)] if isinstance(files, list) else None,
                    "files_changed_truncated": metadata.get("files_changed_truncated") is True,
                    "activity": transcript["activity"],
                    "timeline": _timeline(transcript, metadata, started, ended, now, activity_state),
                    "warnings": warnings + transcript["warnings"]})
        return run

    def snapshot(self, *, now=None):
        now = now or datetime.now(timezone.utc)
        with self._lock:
            runs, warnings = [], []
            try:
                directories = list(self.root.iterdir()) if self.root.exists() else []
            except OSError as e:
                directories = []
                warnings.append("Cannot list runs: " + str(e))
            for directory in directories:
                try:
                    if directory.is_symlink() or not directory.is_dir():
                        continue
                    try:
                        (directory / "run.json").stat()
                    except FileNotFoundError:
                        continue
                    run = self._detail(directory.name, now)
                except (OSError, ValueError) as e:
                    warnings.append(f"{directory.name}: {e}")
                    continue
                if run["warnings"]:
                    warnings.append(directory.name + ": " + "; ".join(run["warnings"]))
                runs.append({**{k: v for k, v in run.items() if k in (
                    "slug", "status", "activity_state", "model", "repo", "started", "ended",
                    "duration_s", "verdict", "review_seconds", "review_in_progress", "resumed_by", "usage")}, "task": run["task"][:240]})
            runs.sort(key=lambda r: (r["started"] or "", r["slug"]), reverse=True)
            present = {r["slug"] for r in runs}
            self._transcripts = {k: v for k, v in self._transcripts.items() if k in present}
            models = []
            for model in sorted({r["model"] or "Unknown model" for r in runs}):
                group = [r for r in runs if (r["model"] or "Unknown model") == model]
                models.append({"model": model, **aggregate(group)})
            return {"runs": runs, "summary": aggregate(runs), "models": models,
                    "warnings": warnings, "runs_dir": str(self.root), "updated_at": now.isoformat()}


def _usage_totals(runs):
    pairs = []
    for run in runs:
        usage = run.get("usage")
        if not isinstance(usage, dict):
            continue
        prompt, completion = number(usage.get("prompt_tokens")), number(usage.get("completion_tokens"))
        if prompt is not None and completion is not None:
            pairs.append((prompt, completion))
    if not pairs:
        return None, None, None, 0
    prompt = sum(pair[0] for pair in pairs)
    completion = sum(pair[1] for pair in pairs)
    return prompt + completion, prompt, completion, len(pairs)


def aggregate(runs):
    reviewed = [r for r in runs if r["verdict"] in ("accept", "reject")]
    accepted = sum(r["verdict"] == "accept" for r in reviewed)
    durations = [r["duration_s"] for r in runs
                 if r["activity_state"] == "ended" and r["duration_s"] is not None]
    reviews = [r["review_seconds"] for r in reviewed if r["review_seconds"] is not None]
    tokens, prompt, completion, samples = _usage_totals(runs)
    accepted_tokens, _, _, accepted_samples = _usage_totals([r for r in reviewed if r["verdict"] == "accept"])
    return {"total": len(runs), "active": sum(r["activity_state"] == "running" for r in runs),
            "reviewing": sum(r.get("review_in_progress") is True for r in runs),
            "completed": sum(r["status"] == "completed" for r in runs),
            "reviewed": len(reviewed), "accepted": accepted,
            "rejected": len(reviewed) - accepted,
            "cleanup": sum(r["verdict"] == "cleanup" for r in runs),
            "awaiting_review": sum(review_eligible(r["status"], r["activity_state"]) and r["verdict"] is None and not r.get("resumed_by") for r in runs),
            "acceptance_rate": accepted / len(reviewed) if reviewed else None,
            "median_duration_s": median(durations) if durations else None,
            "duration_samples": len(durations),
            "median_review_s": median(reviews) if reviews else None, "review_samples": len(reviews),
            "offloaded_tokens": tokens, "offloaded_prompt_tokens": prompt,
            "offloaded_completion_tokens": completion, "token_usage_samples": samples,
            "accepted_offloaded_tokens": accepted_tokens, "accepted_token_usage_samples": accepted_samples}
