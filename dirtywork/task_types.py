"""Conservative task suggestions and evidence for comparing offloaded work.

Suggestions describe the first explicit task intent, not the tools used to verify
it. Saved choices always take precedence. Comparisons count attempts as runs and
also expose distinct full briefs so repeated work cannot imply broad evidence.
"""
from __future__ import annotations

import math
import re


TASK_TYPE_OPTIONS = (
    {"id": "prescribed", "label": "Prescribed edits"},
    {"id": "bugfix", "label": "Bug fixes"},
    {"id": "feature", "label": "Features"},
    {"id": "tests", "label": "Tests"},
    {"id": "docs", "label": "Documentation"},
    {"id": "refactor", "label": "Refactoring"},
    {"id": "maintenance", "label": "Configuration & maintenance"},
    {"id": "other", "label": "Other / unclear"},
    {"id": "demo", "label": "Demos & benchmarks"},
)
_LABELS = {option["id"]: option["label"] for option in TASK_TYPE_OPTIONS}
_DEMO_LEADING = re.compile(
    r"^(?:dashboard live demo|wopr live-effects demo|live diff verification demo)\b",
    re.IGNORECASE,
)
_DEMO_REPO = re.compile(
    r"(?:^|/)(?:dwbench(?:[-_][^/]*)?|dwsoak(?:[-_][^/]*)?|"
    r"pytest-of-[^/]+|pytest-[0-9]+)(?:/|$)", re.IGNORECASE,
)
_PRESCRIBED = re.compile(
    r"\bapply (?:one|an?|the) edit list\b|"
    r"\bapply the same [0-9]+ edits? to\b|"
    r"\bimplement task\b[^.!?]{0,160}\bexactly as written\b|"
    r"\b(?:transcribe|copy)\b[^.!?]{0,80}\bverbatim\b|"
    r"\bapply the [^.!?\n]{0,60}edit_file pairs [^.!?\n]{0,40}exactly\b|"
    r"\bexact edits; nothing else\b", re.IGNORECASE,
)
# More specific rules precede the general verbs at the same position. Choosing
# the earliest explicit action makes "Fix ... Add tests ..." a bug fix, while
# "Add tests for ..." is testing work. Merely running tests is never such intent.
_INTENTS = tuple((category, re.compile(pattern, re.IGNORECASE)) for category, pattern in (
    ("other", r"\b(?:investigate|evaluate|consider|decide|determine)\s+(?:whether|if)\b"),
    ("tests", r"\b(?:add|write|create|extend|expand|update|implement)\s+"
              r"(?:the\s+|new\s+|more\s+|missing\s+)*(?:(?:unit|regression|integration|"
              r"acceptance|end-to-end|e2e)\s+)?tests?\b|\b(?:increase|improve) test coverage\b"),
    ("docs", r"\bdocument\s+|\b(?:write|update|edit|revise|add|create|correct|fix)\s+"
             r"(?:(?:a|an|the)\s+)?(?:docs?\b|documentation\b|readme\b|changelog\b|"
             r"markdown document\b|machine contract\b|user guide\b)"),
    ("refactor", r"\b(?:refactor|rename)\s+|\b(?:extract|reorganize)\s+"
                 r"(?:the\s+)?(?:helpers?|functions?|methods?|modules?|classes?)\b"),
    ("maintenance", r"\b(?:bump|pin)\s+(?:the\s+)?(?:package\s+|worker\s+image\s+)?"
                    r"(?:version|digest|dependencies|dependency|image)\b|"
                    r"\b(?:configure|set up)\s+|\b(?:update|restore|adjust|fix)\s+"
                    r"(?:the\s+)?(?:ci\b|configuration\b|config\b|dependencies\b|"
                    r"workflow\b|release\b)"),
    ("bugfix", r"\b(?:fix|repair|resolve|correct)\s+"),
    ("feature", r"\b(?:implement(?!\s+(?:one\b|task\b|a task\b|the plan\b))|"
                r"add|create|build|introduce|enable|support)\s+"),
))
_NEGATED = re.compile(r"\b(?:do not|don't|never|no need to)\s+(?:\w+\s+){0,2}$",
                      re.IGNORECASE)


def _brief(value):
    return " ".join(value.split()) if isinstance(value, str) else ""


def classify_task(task, *, stored_type=None, repo="", slug=""):
    """Return a saved category or a deterministic suggestion from a full brief.

    A slug is not evidence of intent: a real task may have "demo" in its name.
    Demo exclusions require a known leading description or a fixture repo path.
    """
    if isinstance(stored_type, str) and stored_type in _LABELS:
        return {"id": stored_type, "label": _LABELS[stored_type], "source": "manual"}
    brief = _brief(task)
    repo_path = repo.replace("\\", "/") if isinstance(repo, str) else ""
    if _DEMO_LEADING.search(brief) or _DEMO_REPO.search(repo_path):
        category = "demo"
    elif any(not _NEGATED.search(brief[max(0, match.start() - 40):match.start()])
             for match in _PRESCRIBED.finditer(brief)):
        category = "prescribed"
    else:
        # Code examples are supporting instructions, not a task's primary prose.
        prose = re.sub(r"```[^\n]*\n.*?```", " ", task, flags=re.DOTALL) if isinstance(task, str) else ""
        candidates = []
        for priority, (intent, pattern) in enumerate(_INTENTS):
            for match in pattern.finditer(prose):
                if not _NEGATED.search(prose[max(0, match.start() - 40):match.start()]):
                    candidates.append((match.start(), priority, intent))
                    break
        category = min(candidates)[2] if candidates else "other"
    return {"id": category, "label": _LABELS[category], "source": "suggested"}


def _review_time(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    low, high = ordered[middle - 1:middle + 1]
    # All values are nonnegative; this midpoint cannot overflow like (a+b)/2.
    return low + (high - low) / 2


def offloading_summary(runs):
    """Compare categories using verdicts and measured human review time only.

    Cleanup and pending runs remain in run counts but do not enter acceptance
    rates or timing samples. Demo runs are excluded from every comparison count.
    Empty briefs count as runs but cannot add distinct evidence.
    """
    groups = {}
    excluded_demos = 0
    for run in runs:
        task_type = run.get("task_type")
        if (not isinstance(task_type, dict) or not isinstance(task_type.get("id"), str)
                or task_type["id"] not in _LABELS):
            task_type = classify_task(run.get("task"), repo=run.get("repo", ""))
        category = task_type["id"]
        if category == "demo":
            excluded_demos += 1
            continue
        if category not in groups:
            groups[category] = {
                "id": category, "label": _LABELS[category], "total": 0, "reviewed": 0,
                "accepted": 0, "suggested_runs": 0, "_times": [], "_tasks": set(),
                "_reviewed_tasks": set(), "_timed_tasks": set(),
            }
        row = groups[category]
        row["total"] += 1
        row["suggested_runs"] += task_type.get("source") == "suggested"
        brief = _brief(run.get("task"))
        if brief:
            row["_tasks"].add(brief)
        if run.get("verdict") not in ("accept", "reject"):
            continue
        row["reviewed"] += 1
        row["accepted"] += run.get("verdict") == "accept"
        if brief:
            row["_reviewed_tasks"].add(brief)
        seconds = _review_time(run.get("review_seconds"))
        if seconds is not None:
            row["_times"].append(seconds)
            if brief:
                row["_timed_tasks"].add(brief)
    rows = []
    for row in groups.values():
        times = row.pop("_times")
        row["distinct_tasks"] = len(row.pop("_tasks"))
        row["distinct_reviewed_tasks"] = len(row.pop("_reviewed_tasks"))
        row["distinct_timed_tasks"] = len(row.pop("_timed_tasks"))
        row["review_samples"] = len(times)
        row["acceptance_rate"] = row["accepted"] / row["reviewed"] if row["reviewed"] else None
        row["median_review_s"] = _median(times)
        row["evidence"] = ("no_timing" if not times else "limited"
                           if min(row["distinct_reviewed_tasks"], row["distinct_timed_tasks"]) < 5
                           else "ready")
        rows.append(row)
    rows.sort(key=lambda row: (
        row["id"] == "other",
        row["distinct_reviewed_tasks"] < 5,
        row["acceptance_rate"] is None,
        -row["acceptance_rate"] if row["acceptance_rate"] is not None else 0,
        row["median_review_s"] is None,
        row["median_review_s"] if row["median_review_s"] is not None else 0,
        row["id"],
    ))
    return {"groups": rows, "excluded_demos": excluded_demos,
            "reviewed": sum(row["reviewed"] for row in rows),
            "review_samples": sum(row["review_samples"] for row in rows),
            "unclassified": sum(row["total"] for row in rows if row["id"] == "other")}
