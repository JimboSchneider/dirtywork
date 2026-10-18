"""Shared review state transitions for the command line and local dashboard."""
from __future__ import annotations

import math
from datetime import datetime, timezone


def review_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("review start must be a timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def start_review(data, now):
    if data.get("verdict"):
        raise ValueError("Run already has a verdict.")
    if data.get("status") == "running":
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
