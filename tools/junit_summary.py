#!/usr/bin/env python3
"""Summarize a JUnit XML report as a per-file pass/fail/error/skip table.

Written for the advisory Windows CI leg (spec §5, issue #24). The point of that
job is the TABLE -- which test files actually break on Windows -- not a verdict,
so this script never exits non-zero because tests failed; it exits 2 only when
it cannot do its own job (bad arguments, unreadable file, unparseable XML).

Stdlib only, and it imports neither `dirtywork` nor `pytest`: it runs on a bare
runner against the XML pytest already wrote, so a collection error that stops
pytest from importing the package cannot also stop the report.
"""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET

OUTCOMES = ("passed", "failed", "error", "skipped")
HEADING = "## Windows unit suite (advisory)"


def _outcome(case) -> str:
    """One testcase's outcome, from the child element pytest writes for it.
    A case with none of them passed."""
    if case.find("failure") is not None:
        return "failed"
    if case.find("error") is not None:
        return "error"
    if case.find("skipped") is not None:
        return "skipped"
    return "passed"


def _file_of(case) -> str:
    """The test file a case belongs to. pytest sets `file` on every testcase;
    a writer that only sets `classname` (a dotted module path, optionally
    followed by a test class in CapWords) still gets a row. `tests.test_c`
    becomes `tests/test_c.py`; `tests.test_c.TestX` drops the trailing class
    component and becomes the same `tests/test_c.py` -- taking only the FIRST
    segment (the old `classname.split(".")[0]`) would instead collapse the
    whole package to `tests.py`. Backslashes are normalized so a Windows
    run's table reads and sorts like the repository's own paths."""
    path = case.get("file")
    if path:
        return path.replace("\\", "/")
    classname = case.get("classname") or ""
    if not classname:
        return "unknown.py"
    parts = classname.split(".")
    if len(parts) > 1 and parts[-1][:1].isupper():
        parts = parts[:-1]   # a trailing CapWords component is the test class, not a module
    return "/".join(parts) + ".py"


def summarize(xml_text: str) -> dict:
    """{file: {passed, failed, error, skipped}} for one JUnit XML document.
    Accepts either a <testsuites> wrapper or a bare <testsuite> root."""
    # `xml_text` is CI's own pytest-generated JUnit XML, produced and consumed
    # within the same job -- not untrusted input off the network. stdlib
    # ElementTree does not resolve external entities or DTDs by default, so
    # plain ET.fromstring is fine here regardless.
    root = ET.fromstring(xml_text)
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    table = {}
    for suite in suites:
        for case in suite.iter("testcase"):
            counts = table.setdefault(_file_of(case), dict.fromkeys(OUTCOMES, 0))
            counts[_outcome(case)] += 1
    return table


def render(table: dict) -> str:
    """A Markdown table sorted by file, with a total row. Markdown because
    GitHub renders the step summary as Markdown, and it is still perfectly
    readable as plain text in the job log."""
    lines = ["| file | passed | failed | error | skipped |",
             "|---|---:|---:|---:|---:|"]
    totals = dict.fromkeys(OUTCOMES, 0)
    for path in sorted(table):
        counts = table[path]
        for name in OUTCOMES:
            totals[name] += counts[name]
        lines.append("| {} | {} | {} | {} | {} |".format(
            path, counts["passed"], counts["failed"], counts["error"],
            counts["skipped"]))
    lines.append("| **total** | {} | {} | {} | {} |".format(
        totals["passed"], totals["failed"], totals["error"], totals["skipped"]))
    return "\n".join(lines)


def collected_files(text: str) -> set:
    """Test files named by `pytest --collect-only -q`: every line with `::`
    reduced to its path, backslashes normalised like _file_of. Other lines
    (the blank line, the `N tests collected` summary, warnings) are ignored."""
    files = set()
    for line in text.splitlines():
        line = line.strip()
        if "::" in line:
            files.add(line.split("::", 1)[0].replace("\\", "/"))
    return files


def absent(table: dict, collected: set) -> list:
    """Collected files with no testcase in the report, sorted -- the files a
    truncated session never reached."""
    return sorted(f for f in collected if f not in table)


def render_absent(missing: list) -> str:
    if not missing:
        return "absent from this run: 0"
    return "absent from this run: {} test files ({})".format(len(missing), ", ".join(missing))


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    collected_file = None
    if len(args) not in (1, 3):
        print("usage: junit_summary.py <junit.xml> [--collected <collect-only.txt>]", file=sys.stderr)
        return 2
    if len(args) == 3:
        if args[1] != "--collected":
            print("usage: junit_summary.py <junit.xml> [--collected <collect-only.txt>]", file=sys.stderr)
            return 2
        collected_file = args[2]
    try:
        with open(args[0], encoding="utf-8") as fh:
            xml_text = fh.read()
    except OSError as e:
        print(f"error: cannot read '{args[0]}': {e}", file=sys.stderr)
        return 2
    try:
        table = summarize(xml_text)
    except ET.ParseError as e:
        print(f"error: '{args[0]}' is not valid JUnit XML: {e}", file=sys.stderr)
        return 2
    text = render(table)
    if collected_file is not None:
        try:
            with open(collected_file, encoding="utf-8") as fh:
                collected_text = fh.read()
        except OSError as e:
            print(f"error: cannot read '{collected_file}': {e}", file=sys.stderr)
            return 2
        missing = absent(table, collected_files(collected_text))
        line = render_absent(missing)
        text = text + "\n\n" + line
    print(text)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(HEADING + "\n\n" + text + "\n")
        except OSError as e:
            # The table already went to stdout; failing the step over the
            # decoration would be worse than losing it.
            print(f"warning: cannot append to GITHUB_STEP_SUMMARY: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
