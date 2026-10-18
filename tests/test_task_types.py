"""Task suggestions and comparisons retain the limits of their evidence."""
import json

import pytest


def test_manual_category_overrides_a_demo_suggestion():
    from dirtywork.task_types import classify_task

    assert classify_task("Dashboard live demo. Create ONLY dashboard-demo.txt.",
                         stored_type="feature", repo="/tmp/dwbench-example-123") == {
        "id": "feature", "label": "Features", "source": "manual"}


@pytest.mark.parametrize("task, category, label", [
    ("Apply one edit list to one Markdown document with the repository's tool, then stop.",
     "prescribed", "Prescribed edits"),
    ("Fix the parser crash. Add regression tests and run pytest.", "bugfix", "Bug fixes"),
    ("Add a JSON export command. Run pytest and update the docs.", "feature", "Features"),
    ("Write regression tests for the parser. Change no production code.", "tests", "Tests"),
    ("Document the --agent flag in the machine contract and README.", "docs", "Documentation"),
    ("Refactor the parser into smaller helpers without changing behavior.", "refactor", "Refactoring"),
    ("Rename calc_total to compute_total throughout ledger.py and update call sites.",
     "refactor", "Refactoring"),
    ("Bump the package version and pin the worker image digest.",
     "maintenance", "Configuration & maintenance"),
    ("Investigate the available approaches and report findings.", "other", "Other / unclear"),
    ("", "other", "Other / unclear"),
])
def test_suggested_category_follows_primary_intent(task, category, label):
    from dirtywork.task_types import classify_task

    assert classify_task(task) == {"id": category, "label": label, "source": "suggested"}


def test_classifier_reads_full_brief_and_ignores_validation_instructions():
    from dirtywork.task_types import classify_task

    task = ("Background information about the existing implementation. " * 120
            + "\nFix the parser crash.\nRun pytest after making the change.")
    assert classify_task(task)["id"] == "bugfix"
    assert classify_task("Implement a CSV importer. Add tests and run the whole test suite.")["id"] == "feature"
    assert classify_task("Run the test suite and report its results.")["id"] == "other"


@pytest.mark.parametrize("stored", ["invented", "", None, 3, [], {}])
def test_invalid_saved_category_falls_back_to_suggestion(stored):
    from dirtywork.task_types import classify_task

    assert classify_task("Fix the parser crash.", stored_type=stored) == {
        "id": "bugfix", "label": "Bug fixes", "source": "suggested"}


@pytest.mark.parametrize("task,repo", [
    ("Dashboard live demo. Create ONLY dashboard-demo.txt.", "/real/project"),
    ("WOPR live-effects demo. Create ONLY wopr-live-demo.txt.", "/real/project"),
    ("Live diff verification demo. Touch ONLY live-diff-demo.txt.", "/real/project"),
    ("Fix a calculation.", "/tmp/dwbench-py-off-by-one-abcd"),
    ("Add a flag.", "/tmp/dwsoak-F4-py-off-by-one-abcd"),
    ("Add a flag.", "/tmp/pytest-of-alice/pytest-12/test_run0/demo"),
])
def test_demonstrations_are_detected_from_explicit_context(task, repo):
    from dirtywork.task_types import classify_task

    assert classify_task(task, repo=repo)["id"] == "demo"


@pytest.mark.parametrize("task,repo,slug,want", [
    ("Implement a dashboard. See the Dashboard live demo instructions below for verification.",
     "/real/project", "demo-new-dashboard", "feature"),
    ("Fix the benchmark harvester. Run pytest after the change.", "/real/project", "demo", "bugfix"),
    ("Document the dwbench and dwsoak commands and pytest setup.", "/real/project", "demo", "docs"),
    ("Add a dashboard example.", "/real/pytest-tools", "dashboard-live-demo-reference", "feature"),
])
def test_demo_words_and_instructions_do_not_hide_real_work(task, repo, slug, want):
    from dirtywork.task_types import classify_task

    assert classify_task(task, repo=repo, slug=slug)["id"] == want


def run(task, category, *, verdict=None, seconds=None, source="manual"):
    return {"task": task, "task_type": {"id": category, "source": source},
            "verdict": verdict, "review_seconds": seconds}


def test_comparison_uses_reviewed_runs_and_keeps_repeat_counts_visible():
    from dirtywork.task_types import offloading_summary

    result = offloading_summary([
        run("Fix one parser bug", "bugfix", verdict="accept", seconds=0),
        run("  Fix one\n parser bug  ", "bugfix", verdict="reject", seconds=40,
            source="suggested"),
        run("Fix a different bug", "bugfix", verdict="accept", seconds=100),
        run("Pending fix", "bugfix", seconds=1),
        run("Abandoned fix", "bugfix", verdict="cleanup", seconds=2),
        run("Demo", "demo", verdict="accept", seconds=3),
        run("Unclear task", "other", verdict="reject"),
    ])
    assert result == {
        "groups": [
            {"id": "bugfix", "label": "Bug fixes", "total": 5, "reviewed": 3,
             "accepted": 2, "acceptance_rate": 2 / 3, "median_review_s": 40,
             "review_samples": 3, "distinct_tasks": 4, "distinct_reviewed_tasks": 2,
             "distinct_timed_tasks": 2, "suggested_runs": 1, "evidence": "limited"},
            {"id": "other", "label": "Other / unclear", "total": 1, "reviewed": 1,
             "accepted": 0, "acceptance_rate": 0, "median_review_s": None,
             "review_samples": 0, "distinct_tasks": 1, "distinct_reviewed_tasks": 1,
             "distinct_timed_tasks": 0, "suggested_runs": 0, "evidence": "no_timing"},
        ],
        "excluded_demos": 1, "reviewed": 4, "review_samples": 3, "unclassified": 1,
    }


def test_comparison_unknown_metrics_are_missing_and_invalid_times_are_excluded():
    from dirtywork.task_types import offloading_summary

    bad_times = [None, -1, float("inf"), float("nan"), True, "30", 10 ** 400]
    result = offloading_summary([
        run(str(i), "tests", verdict="accept", seconds=value)
        for i, value in enumerate(bad_times)
    ] + [run("Pending", "feature"), run("Cleanup", "feature", verdict="cleanup", seconds=20)])
    tests, feature = result["groups"]
    assert tests["id"] == "tests"
    assert tests["reviewed"] == 7
    assert tests["review_samples"] == 0
    assert tests["median_review_s"] is None
    assert tests["evidence"] == "no_timing"
    assert feature["acceptance_rate"] is None
    assert feature["median_review_s"] is None
    assert result["review_samples"] == 0
    json.dumps(result, allow_nan=False)


def test_evidence_requires_five_distinct_reviewed_and_timed_full_briefs():
    from dirtywork.task_types import offloading_summary

    common = "Identical introductory text " * 30
    repeated = [run(common + "tail A", "feature", verdict="accept", seconds=10) for _ in range(20)]
    row = offloading_summary(repeated)["groups"][0]
    assert row["distinct_tasks"] == row["distinct_timed_tasks"] == 1
    assert row["evidence"] == "limited"
    diverse = [run(common + "tail " + str(i), "feature", verdict="accept", seconds=10)
               for i in range(5)]
    row = offloading_summary(diverse)["groups"][0]
    assert row["distinct_tasks"] == 5
    assert row["distinct_reviewed_tasks"] == 5
    assert row["distinct_timed_tasks"] == 5
    assert row["evidence"] == "ready"
    diverse[-1]["review_seconds"] = None
    assert offloading_summary(diverse)["groups"][0]["evidence"] == "limited"


def test_distinct_briefs_normalize_only_whitespace_and_ignore_empty_briefs():
    from dirtywork.task_types import offloading_summary

    row = offloading_summary([
        run("Add  Flag", "feature", verdict="accept", seconds=10),
        run("Add\nFlag", "feature", verdict="accept", seconds=20),
        run("add Flag", "feature", verdict="accept", seconds=30),
        run("", "feature", verdict="accept", seconds=40),
        run(" \n ", "feature", verdict="accept", seconds=50),
    ])["groups"][0]
    assert row["total"] == 5
    assert row["distinct_tasks"] == row["distinct_reviewed_tasks"] == row["distinct_timed_tasks"] == 2
    assert row["review_samples"] == 5
    assert row["evidence"] == "limited"


def test_group_order_compares_acceptance_then_time_with_missing_values_last():
    from dirtywork.task_types import offloading_summary

    result = offloading_summary([
        run("Pending", "maintenance"),
        run("Slow", "docs", verdict="accept", seconds=30),
        run("Unknown timing", "tests", verdict="accept"),
        run("Low rate", "refactor", verdict="reject", seconds=1),
        run("Fast", "feature", verdict="accept", seconds=10),
        run("Tie", "bugfix", verdict="accept", seconds=10),
    ])
    assert [row["id"] for row in result["groups"]] == [
        "bugfix", "feature", "docs", "tests", "refactor", "maintenance"]


def test_empty_and_demo_only_inputs_have_no_comparison_rows():
    from dirtywork.task_types import offloading_summary

    assert offloading_summary([]) == {"groups": [], "excluded_demos": 0,
                                      "reviewed": 0, "review_samples": 0, "unclassified": 0}
    assert offloading_summary([run("Demo", "demo", verdict="accept", seconds=12)]) == {
        "groups": [], "excluded_demos": 1, "reviewed": 0, "review_samples": 0, "unclassified": 0}


def test_median_remains_finite_for_large_valid_times():
    from dirtywork.task_types import offloading_summary

    row = offloading_summary([
        run("A", "docs", verdict="accept", seconds=1e308),
        run("B", "docs", verdict="reject", seconds=1.2e308),
    ])["groups"][0]
    assert row["median_review_s"] == pytest.approx(1.1e308)
    json.dumps(row, allow_nan=False)


@pytest.mark.parametrize("task", [
    "Implement Task 6 of the relay plan exactly as written; do not redesign anything.\nAdd the command.",
    "Apply the same 8 edits to all seven files: index.html, about.html.\nCreate one new section.",
    "Task 4 adds helper functions and a spec edit.\nEvery code block is the EXACT content to write — transcribe it verbatim.",
])
def test_explicit_transcription_work_is_suggested_as_prescribed(task):
    from dirtywork.task_types import classify_task

    assert classify_task(task)["id"] == "prescribed"


@pytest.mark.parametrize("task", [
    "Write a README for the new command.",
    "Create a Markdown document explaining the setup.",
    "Update the user guide with deployment instructions.",
])
def test_document_creation_is_documentation_work(task):
    from dirtywork.task_types import classify_task

    assert classify_task(task)["id"] == "docs"


def test_invalid_projected_category_cannot_break_the_comparison():
    from dirtywork.task_types import offloading_summary

    result = offloading_summary([{
        "task": "Fix the parser crash.", "task_type": {"id": []},
        "verdict": "accept", "review_seconds": 12,
    }])
    assert result["groups"][0]["id"] == "bugfix"
    assert result["groups"][0]["suggested_runs"] == 1


@pytest.mark.parametrize("task,want", [
    ("Add a CSV importer. Do not copy existing code verbatim.", "feature"),
    ("Do not add tests. Fix the parser crash.", "bugfix"),
    ("Do not fix unrelated bugs. Add a CSV importer.", "feature"),
    ("Investigate whether to fix the parser or add another format.", "other"),
])
def test_negative_and_uncertain_instructions_do_not_become_primary_work(task, want):
    from dirtywork.task_types import classify_task

    assert classify_task(task)["id"] == want


def test_broad_reviewed_categories_precede_perfect_oneoffs():
    from dirtywork.task_types import offloading_summary

    broad = [run("Prescribed task " + str(i), "prescribed",
                 verdict="accept" if i < 17 else "reject", seconds=30)
             for i in range(20)]
    result = offloading_summary(broad + [
        run("One documentation task", "docs", verdict="accept", seconds=1),
        run("One pending task", "feature"),
    ])
    assert [row["id"] for row in result["groups"]] == ["prescribed", "docs", "feature"]
    assert result["groups"][0]["acceptance_rate"] == 0.85
    assert result["groups"][1]["acceptance_rate"] == 1


def test_other_category_remains_last_despite_broad_perfect_reviews():
    from dirtywork.task_types import offloading_summary

    unclear = [run("Unclear task " + str(i), "other", verdict="accept", seconds=1)
               for i in range(5)]
    result = offloading_summary(unclear + [run("Pending maintenance", "maintenance")])
    assert [row["id"] for row in result["groups"]] == ["maintenance", "other"]
