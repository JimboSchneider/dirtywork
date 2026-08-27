"""Tests for the operator contract package (#82)."""
import argparse
import hashlib
import re

import pytest

from dirtywork import __main__ as m
from dirtywork import __version__
from dirtywork import contract


def test_contract_prints_packaged_reference_verbatim(capsys):
    rc = m.main(["contract"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert out == contract.read("machine-contract.md")
    assert err == ""


def test_contract_first_line_is_heading(capsys):
    m.main(["contract"])
    assert capsys.readouterr().out.split("\n", 1)[0] == "# Machine contract"


def test_skill_frontmatter_and_size():
    text = contract.render_skill(__version__)
    assert text.startswith("---\n")
    head, _ = contract._split_frontmatter(text)
    assert "\nname: dirtywork\n" in head
    assert re.search(r"\ndescription: \S", head)
    assert text.count("\n") <= 200
    for needle in ("dirtywork contract", "resume", "runs verdict", "--keep-transcript",
                   "--allow-network", "`0`", "`1`", "`2`"):
        assert needle in text, needle


def test_skill_stamp_hash_matches_body():
    text = contract.render_skill(__version__)
    head, rest = contract._split_frontmatter(text)
    stamp, _, body = rest.partition("\n")
    match = contract.STAMP_RE.match(stamp)
    assert match, stamp
    assert match.group("version") == __version__
    assert match.group("hash") == hashlib.sha256((head + body).encode("utf-8")).hexdigest()[:16]
    assert f"(v{__version__})" in text
    assert "{{" not in text and "{VERSION}" not in text


def _option_strings(parser: argparse.ArgumentParser) -> set:
    out = set()
    for action in parser._actions:
        out.update(action.option_strings)
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                out |= _option_strings(sub)
    return out


def test_skill_flags_exist_in_parser():
    known = _option_strings(m._build_parser())
    used = set(re.findall(r"--[a-z][a-z-]*", contract.render_skill(__version__)))
    assert used, "the skill names no flags?"
    assert used <= known, sorted(used - known)


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        m.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out == f"dirtywork {__version__}\n"


def test_skill_first_paragraph_addresses_the_worker():
    text = contract.render_skill(__version__)
    _, rest = contract._split_frontmatter(text)
    lines = rest.split("\n")[1:]  # drop the stamp line
    title = next(i for i, line in enumerate(lines) if line.startswith("# "))
    after = [line for line in lines[title + 1:]]
    while after and not after[0].strip():
        after.pop(0)
    paragraph = []
    while after and after[0].strip():
        paragraph.append(after.pop(0))
    joined = " ".join(paragraph).lower()
    assert "worker" in joined and "ignore" in joined, joined
