"""Activity results expose bounded, trustworthy file-change evidence."""
import json

import pytest

from dirtywork.dashboard_data import RunStore, _event


EDIT_RESULT = (
    "Edited src/app.py: +1 -1 (removed 1 non-blank line)\n"
    "--- a/src/app.py\n"
    "+++ b/src/app.py\n"
    "@@ -14,3 +14,3 @@\n"
    " def greet():\n"
    '-    return "Hello"\n'
    '+    return "Welcome"\n'
    " \n"
)


def activity(result, tool="edit_file", kind="tool_result"):
    return _event({"event": kind, "tool": tool, "args": {"path": "src/app.py"},
                   "result": result}, 7)


def test_edit_result_exposes_readable_summary_and_numbered_diff_rows():
    projected = activity(EDIT_RESULT)
    assert projected["detail"] == EDIT_RESULT
    assert projected["sequence"] == 7
    assert projected["change_preview"] == {
        "summary": "1 line added · 1 line removed",
        "additions": 1,
        "deletions": 1,
        "truncated": False,
        "rows": [
            {"kind": "hunk", "text": "", "old": None, "new": None},
            {"kind": "context", "text": "def greet():", "old": 14, "new": 14},
            {"kind": "remove", "text": '    return "Hello"', "old": 15, "new": None},
            {"kind": "add", "text": '    return "Welcome"', "old": None, "new": 15},
            {"kind": "context", "text": "", "old": 16, "new": 16},
        ],
    }


@pytest.mark.parametrize("tool,verb", [
    ("write_file", "Wrote"), ("append_file", "Appended to"),
    ("edit_file", "Edited"), ("apply_edits", "Applied 2 edits to"),
    ("insert_before", "Inserted into"), ("insert_after", "Inserted into"),
])
def test_each_known_mutating_tool_can_project_its_successful_diff(tool, verb):
    result = f"{verb} f: +2 -0\n--- a/f\n+++ b/f\n@@ -0,0 +1,2 @@\n+a\n+b"
    preview = activity(result, tool)["change_preview"]
    assert preview["summary"] == "2 lines added · 0 lines removed"
    assert preview["additions"] == 2
    assert preview["deletions"] == 0
    assert preview["rows"][-1] == {"kind": "add", "text": "b", "old": None, "new": 2}


def test_multiple_hunks_preserve_context_and_missing_newline_notices():
    result = (
        "Applied 2 edits to f: +2 -2 (removed 2 non-blank lines)\n"
        "--- a/f\n+++ b/f\n@@ -2,2 +2,2 @@ first\n same\n-old\n+new\n"
        "@@ -20 +20 @@ second\n-last\n\\ No newline at end of file\n"
        "+next\n\\ No newline at end of file"
    )
    preview = activity(result, "apply_edits")["change_preview"]
    assert preview["truncated"] is False
    assert preview["rows"] == [
        {"kind": "hunk", "text": "first", "old": None, "new": None},
        {"kind": "context", "text": "same", "old": 2, "new": 2},
        {"kind": "remove", "text": "old", "old": 3, "new": None},
        {"kind": "add", "text": "new", "old": None, "new": 3},
        {"kind": "hunk", "text": "second", "old": None, "new": None},
        {"kind": "remove", "text": "last", "old": 20, "new": None},
        {"kind": "notice", "text": "No newline at end of file", "old": None, "new": None},
        {"kind": "add", "text": "next", "old": None, "new": 20},
        {"kind": "notice", "text": "No newline at end of file", "old": None, "new": None},
    ]


@pytest.mark.parametrize("tool,kind,result", [
    ("read_file", "tool_result", EDIT_RESULT),
    ("bash", "tool_result", EDIT_RESULT),
    ("unknown_tool", "tool_result", EDIT_RESULT),
    ("edit_file", "guardrail_block", EDIT_RESULT),
    ("edit_file", "tool_result", "ERROR: old text occurs 0 times; no edits applied"),
    ("edit_file", "tool_result", "BLOCKED: path is outside the worktree"),
    ("write_file", "tool_result", "Wrote 42 bytes to f (new file, 3 lines)"),
    ("write_file", "tool_result", "Wrote f: +0 -0"),
    ("edit_file", "tool_result", "Edited f: 30000 lines (diff omitted: file too large)"),
    ("edit_file", "tool_result", "Ordinary output\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b"),
    ("edit_file", "tool_result", None),
])
def test_non_diff_results_retain_plain_detail(tool, kind, result):
    projected = activity(result, tool, kind)
    assert "change_preview" not in projected
    assert projected["detail"] == (result or "")


@pytest.mark.parametrize("patch", [
    "@@ -1 +1 @@\n-old\n+new",  # Missing file headers.
    "--- a/other\n+++ b/f\n@@ -1 +1 @@\n-old\n+new",
    "--- a/f\n+++ b/f\n-old\n+new",  # Missing hunk header.
    "--- a/f\n+++ b/f\n@@ invalid @@\n-old\n+new",
    "--- a/f\n+++ b/f\n@@ -1 +1 @@\nold\n+new",
    "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old",  # Unmarked incomplete hunk.
    "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+new\n+extra",
    "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+new\nERROR: write failed",
    "--- a/f\n+++ b/f\n@@ -0 +1 @@\n-old\n+new",
    "--- a/f\n+++ b/f\n@@ -1,0 +1,0 @@",  # No actual changed rows.
])
def test_malformed_or_incomplete_unmarked_diffs_use_plain_detail(patch):
    assert "change_preview" not in activity("Edited f: +1 -1\n" + patch)


@pytest.mark.parametrize("marker", [
    "[diff truncated: 80 more lines]", "[output truncated at 100 chars]",
])
def test_upstream_truncation_uses_header_totals_and_labels_partial_preview(marker):
    result = "Edited f: +9 -3\n--- a/f\n+++ b/f\n@@ -1,3 +1,9 @@\n-old\n+new\n" + marker
    preview = activity(result)["change_preview"]
    assert preview["summary"] == "9 lines added · 3 lines removed"
    assert (preview["additions"], preview["deletions"]) == (9, 3)
    assert preview["truncated"] is True
    assert preview["rows"][1] == {"kind": "remove", "text": "old", "old": 1, "new": None}
    assert all(row["text"] != marker for row in preview["rows"])


@pytest.mark.parametrize("already_capped", [False, True])
def test_local_and_transcript_caps_drop_the_unfinished_last_row(already_capped):
    prefix = "Edited f: +2 -1\n--- a/f\n+++ b/f\n@@ -1 +1,2 @@\n-old\n+new\n"
    result = prefix + "+" + "unfinished" * 1000
    if already_capped:
        result = result[:2000]
    projected = activity(result)
    preview = projected["change_preview"]
    assert len(projected["detail"]) == 2000
    assert preview["truncated"] is True
    assert preview["rows"] == [
        {"kind": "hunk", "text": "", "old": None, "new": None},
        {"kind": "remove", "text": "old", "old": 1, "new": None},
        {"kind": "add", "text": "new", "old": None, "new": 1},
    ]


def test_local_cap_keeps_a_row_when_next_character_is_its_newline():
    prefix = "Edited f: +2 -0\n--- a/f\n+++ b/f\n@@ -0,0 +1,2 @@\n+"
    first = "x" * (2000 - len(prefix))
    preview = activity(prefix + first + "\n+hidden")["change_preview"]
    assert preview["truncated"] is True
    assert preview["rows"][-1] == {"kind": "add", "text": first, "old": None, "new": 1}


def test_html_and_non_newline_controls_remain_literal_row_data():
    old = '<script>alert("old")</script>\r\v\f\x85\u2028\u2029'
    new = '<img src=x onerror="alert(1)">\r\v\f\x85\u2028\u2029'
    result = f"Edited f: +1 -1\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-{old}\n+{new}"
    rows = activity(result)["change_preview"]["rows"]
    assert rows[1] == {"kind": "remove", "text": old, "old": 1, "new": None}
    assert rows[2] == {"kind": "add", "text": new, "old": None, "new": 1}
    assert json.loads(json.dumps(rows)) == rows


@pytest.mark.parametrize("result", [
    "Edited f: +9007199254740992 -1\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b",
    "Edited f: +1 -1\n--- a/f\n+++ b/f\n@@ -9007199254740992 +1 @@\n-a\n+b",
    "Edited f: +1 -1\n--- a/f\n+++ b/f\n@@ -1,9007199254740992 +1 @@\n-a\n+b",
    "Edited f: +" + "9" * 100000 + " -1\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b",
    "Edited f: +1 -1\n--- a/f\n+++ b/f\n@@ -" + "9" * 100000 + " +1 @@\n-a\n+b",
])
def test_unsafe_or_huge_numbers_cannot_crash_run_detail(tmp_path, result):
    directory = tmp_path / "run"
    directory.mkdir()
    (directory / "run.json").write_text(json.dumps({"status": "completed"}))
    (directory / "transcript.jsonl").write_text(json.dumps({
        "event": "tool_result", "tool": "edit_file", "result": result,
    }) + "\n")
    run = RunStore(tmp_path).detail("run")
    assert len(run["activity"]) == 1
    assert "change_preview" not in run["activity"][0]
    assert len(run["activity"][0]["detail"]) <= 2000
    json.dumps(run, allow_nan=False)


def test_large_result_is_sliced_before_line_parsing():
    class UnsplittableResult(str):
        def split(self, *args, **kwargs):
            raise AssertionError("the full result must not be split")

        def splitlines(self, *args, **kwargs):
            raise AssertionError("the full result must not be split")

    result = UnsplittableResult(
        "Edited f: +100000 -0\n--- a/f\n+++ b/f\n@@ -0,0 +1,100000 @@\n" + "+x\n" * 100000)
    projected = activity(result)
    assert projected["change_preview"]["truncated"] is True
    assert len(projected["change_preview"]["rows"]) < 700
    assert len(json.dumps(projected)) < 60000
