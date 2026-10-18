"""Shared review state transitions for the command line and local dashboard."""
from __future__ import annotations

import math
import os
import stat
import json
from pathlib import Path

from .osfs import open_nofollow
from .resume import pid_alive
from datetime import datetime, timezone


def _terminal_event(run_dir):
    """Read complete transcript records; a partial final write is not evidence."""
    end = None
    try:
        fd = open_nofollow(Path(run_dir) / "transcript.jsonl",
                           os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return None
        with os.fdopen(fd, "rb") as source:
            while True:
                line = source.readline(1_048_577)
                if not line:
                    break
                if len(line) > 1_048_576:
                    while line and not line.endswith(b"\n"):
                        line = source.readline(1_048_577)
                    continue
                if not line.endswith(b"\n"):
                    break
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError, RecursionError):
                    continue
                if isinstance(event, dict) and event.get("event") == "run_end":
                    end = event
    except OSError:
        pass
    return end


def effective_run_status(data, end=None, *, run_dir=None):
    """Return display status and activity without modifying stored metadata."""
    if end is None and run_dir is not None:
        end = _terminal_event(run_dir)
    status = data.get("status")
    status = status if isinstance(status, str) and status else "unknown"
    if end and (status in ("running", "unknown")):
        terminal_status = end.get("status")
        status = (terminal_status if isinstance(terminal_status, str)
                  and terminal_status and terminal_status != "running" else "ended")
    if status == "running":
        pid = data.get("host_pid")
        valid_pid = type(pid) is int and 0 < pid <= 2 ** 31 - 1
        activity = "unconfirmed" if not valid_pid else "running" if pid_alive(pid) else "inactive"
    else:
        activity = "unknown" if status == "unknown" else "ended"
    return status, activity


def review_eligible(status, activity_state):
    return activity_state in ("ended", "inactive") and status != "unknown"


def review_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("review start must be a timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def start_review(data, now, *, run_dir=None, end=None):
    if data.get("resumed_by"):
        raise ValueError("Run is superseded by a resumed run.")
    if data.get("verdict"):
        raise ValueError("Run already has a verdict.")
    status, activity = effective_run_status(data, end, run_dir=run_dir)
    if not review_eligible(status, activity):
        raise ValueError("Wait for the worker to finish before starting review.")
    if data.get("review_started_at") is not None:
        if review_timestamp(data["review_started_at"]) > now:
            raise ValueError("Review start is in the future.")
        return dict(data)
    return {**data, "review_started_at": now.isoformat()}


def record_verdict(data, verdict, note, review_seconds, now):
    data = dict(data)
    source = data.get("review_seconds_source")
    if review_seconds is not None:
        try:
            valid = not isinstance(review_seconds, bool) and review_seconds >= 0 and math.isfinite(review_seconds)
        except (TypeError, OverflowError):
            valid = False
        if not valid:
            raise ValueError("Review seconds must be a finite, non-negative number.")
        source = "manual"
    elif data.get("review_seconds") is not None:
        review_seconds = data["review_seconds"]
    elif data.get("review_started_at") is not None:
        review_seconds = (now - review_timestamp(data["review_started_at"])).total_seconds()
        if review_seconds < 0:
            raise ValueError("Review start is in the future; supply --review-seconds to override.")
        source = "timer"
    data.update(verdict=verdict, note=note, verdict_at=now.isoformat(),
                review_seconds=review_seconds, time_to_verdict_s=None)
    if source:
        data["review_seconds_source"] = source
    try:
        ended = review_timestamp(data.get("ended"))
        data["time_to_verdict_s"] = (now - ended).total_seconds()
    except ValueError:
        pass
    return data
