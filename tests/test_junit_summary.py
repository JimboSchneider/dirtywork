from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "junit_summary.py"


def _load():
    """tools/ is deliberately not a package (adding __init__.py would put a CI
    helper into the installable surface), so the script is loaded by path."""
    spec = importlib.util.spec_from_file_location("junit_summary", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# SAMPLE is a plain (non-raw) string, so `\\` is ONE backslash in the XML --
# exactly what a Windows pytest run writes into `file=`. Doubling it here would
# make _file_of produce "tests//test_b.py" and every assertion below miss.
SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="1" failures="1" skipped="1" tests="5">
    <testcase classname="tests.test_a" name="test_ok" file="tests/test_a.py"/>
    <testcase classname="tests.test_a" name="test_bad" file="tests/test_a.py">
      <failure message="boom">trace</failure>
    </testcase>
    <testcase classname="tests.test_b" name="test_err" file="tests\\test_b.py">
      <error message="kaboom">trace</error>
    </testcase>
    <testcase classname="tests.test_b" name="test_skip" file="tests\\test_b.py">
      <skipped message="no fifo"/>
    </testcase>
    <testcase classname="tests.test_c" name="test_ok2"/>
  </testsuite>
</testsuites>
"""


def test_summarize_counts_outcomes_per_file():
    module = _load()
    table = module.summarize(SAMPLE)
    assert table["tests/test_a.py"] == {"passed": 1, "failed": 1, "error": 0, "skipped": 0}
    # Windows backslashes are normalized so the table sorts and reads like the repo
    assert table["tests/test_b.py"] == {"passed": 0, "failed": 0, "error": 1, "skipped": 1}
    # a writer that emits only classname still gets a row, under its full
    # module path -- not collapsed to the top-level package alone
    assert table["tests/test_c.py"] == {"passed": 1, "failed": 0, "error": 0, "skipped": 0}


def test_render_is_a_sorted_markdown_table_with_a_total_row():
    module = _load()
    text = module.render(module.summarize(SAMPLE))
    lines = text.splitlines()
    assert lines[0] == "| file | passed | failed | error | skipped |"
    assert lines[1] == "|---|---:|---:|---:|---:|"
    assert lines[2].startswith("| tests/test_a.py |")
    assert lines[3].startswith("| tests/test_b.py |")
    assert lines[4].startswith("| tests/test_c.py |")
    assert lines[-1] == "| **total** | 2 | 1 | 1 | 1 |"


@pytest.mark.parametrize("classname,expected", [
    ("tests.test_c", "tests/test_c.py"),            # module path, no class
    ("tests.test_c.TestX", "tests/test_c.py"),       # trailing CapWords class dropped
    ("test_c", "test_c.py"),                         # single segment, no package
    ("", "unknown.py"),                              # no classname at all
])
def test_file_of_classname_fallback(classname, expected):
    module = _load()
    import xml.etree.ElementTree as ET
    attr = f' classname="{classname}"' if classname else ""
    case = ET.fromstring(f'<testcase{attr} name="t"/>')
    assert module._file_of(case) == expected


def test_summarize_and_render_on_zero_testcases():
    module = _load()
    empty = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="0" skipped="0" tests="0"/>
</testsuites>
"""
    table = module.summarize(empty)
    assert table == {}
    text = module.render(table)
    lines = text.splitlines()
    assert lines[0] == "| file | passed | failed | error | skipped |"
    assert lines[1] == "|---|---:|---:|---:|---:|"
    assert lines[-1] == "| **total** | 0 | 0 | 0 | 0 |"


def test_main_prints_the_table_and_appends_to_the_step_summary(tmp_path, monkeypatch, capsys):
    module = _load()
    xml = tmp_path / "junit.xml"
    xml.write_text(SAMPLE, encoding="utf-8")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert module.main([str(xml)]) == 0
    out = capsys.readouterr().out
    assert "| **total** | 2 | 1 | 1 | 1 |" in out
    written = summary.read_text(encoding="utf-8")
    assert written.startswith("## Windows unit suite (advisory)")
    assert "| **total** | 2 | 1 | 1 | 1 |" in written


def test_main_without_a_step_summary_still_prints(tmp_path, monkeypatch, capsys):
    module = _load()
    xml = tmp_path / "junit.xml"
    xml.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert module.main([str(xml)]) == 0
    assert "| **total** | 2 | 1 | 1 | 1 |" in capsys.readouterr().out


@pytest.mark.parametrize("argv,message", [
    ([], "usage:"),
    (["a.xml", "b.xml"], "usage:"),
    (["/nonexistent/junit.xml"], "cannot read"),
])
def test_main_refuses_bad_input_with_exit_2(argv, message, capsys):
    module = _load()
    assert module.main(argv) == 2
    assert message in capsys.readouterr().err


def test_main_reports_unparseable_xml(tmp_path, capsys):
    module = _load()
    xml = tmp_path / "junit.xml"
    xml.write_text("<not-xml", encoding="utf-8")
    assert module.main([str(xml)]) == 2
    assert "not valid JUnit XML" in capsys.readouterr().err
