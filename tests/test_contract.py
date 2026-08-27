"""Tests for the operator contract package (#82)."""
import argparse
import hashlib
import re
from pathlib import Path

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


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _user_skill(home: Path) -> Path:
    return home / ".claude" / "skills" / "dirtywork" / "SKILL.md"


def test_init_writes_user_skill_by_default(home, capsys):
    rc = m.main(["init"])
    out, err = capsys.readouterr()
    assert rc == 0 and err == ""
    assert out == f"wrote: {_user_skill(home)}\n"
    assert _user_skill(home).read_text(encoding="utf-8") == contract.render_skill(__version__)


def test_init_with_repo_writes_both(home, tmp_path, capsys):
    repo = tmp_path / "proj"
    repo.mkdir()
    rc = m.main(["init", "--repo", str(repo)])
    out = capsys.readouterr().out
    project = repo / ".claude" / "skills" / "dirtywork" / "SKILL.md"
    assert rc == 0
    assert out == f"wrote: {_user_skill(home)}\nwrote: {project}\n"
    assert _user_skill(home).read_bytes() == project.read_bytes()


def test_init_no_user_writes_project_only(home, tmp_path, capsys):
    repo = tmp_path / "proj"
    repo.mkdir()
    rc = m.main(["init", "--repo", str(repo), "--no-user"])
    assert rc == 0
    assert not _user_skill(home).exists()
    assert (repo / ".claude" / "skills" / "dirtywork" / "SKILL.md").is_file()


def test_init_no_user_without_repo_exits_2(home, capsys):
    rc = m.main(["init", "--no-user"])
    out, err = capsys.readouterr()
    assert rc == 2 and out == ""
    assert err.startswith("error: nothing to write")
    assert not _user_skill(home).exists()


def test_init_is_idempotent(home, capsys):
    m.main(["init"])
    first = _user_skill(home).read_bytes()
    capsys.readouterr()
    rc = m.main(["init"])
    assert rc == 0
    assert capsys.readouterr().out == f"up to date: {_user_skill(home)}\n"
    assert _user_skill(home).read_bytes() == first


def test_init_updates_older_stamped_copy(home, capsys):
    path = _user_skill(home)
    path.parent.mkdir(parents=True)
    path.write_text(contract.render_skill("0.0.1"), encoding="utf-8")
    rc = m.main(["init"])
    assert rc == 0
    assert capsys.readouterr().out == f"updated: {path} (v0.0.1 -> v{__version__})\n"
    assert path.read_text(encoding="utf-8") == contract.render_skill(__version__)


def test_init_skips_locally_modified_without_force(home, capsys):
    m.main(["init"])
    path = _user_skill(home)
    edited = path.read_text(encoding="utf-8") + "\nMy own rule.\n"
    path.write_text(edited, encoding="utf-8")
    capsys.readouterr()
    rc = m.main(["init"])
    assert rc == 1
    assert capsys.readouterr().out == f"skipped (locally modified): {path}\n"
    assert path.read_text(encoding="utf-8") == edited


def test_init_force_overwrites_modified(home, capsys):
    m.main(["init"])
    path = _user_skill(home)
    path.write_text(path.read_text(encoding="utf-8") + "\nMy own rule.\n", encoding="utf-8")
    capsys.readouterr()
    rc = m.main(["init", "--force"])
    assert rc == 0
    assert capsys.readouterr().out == f"overwrote: {path}\n"
    assert path.read_text(encoding="utf-8") == contract.render_skill(__version__)


def test_init_unstamped_existing_file_is_treated_as_modified(home, capsys):
    path = _user_skill(home)
    path.parent.mkdir(parents=True)
    path.write_text("hello\n", encoding="utf-8")
    assert m.main(["init"]) == 1
    assert path.read_text(encoding="utf-8") == "hello\n"
    capsys.readouterr()
    assert m.main(["init", "--force"]) == 0
    assert path.read_text(encoding="utf-8") == contract.render_skill(__version__)


def test_init_stdout_prints_and_writes_nothing(home, capsys):
    rc = m.main(["init", "--stdout"])
    out, err = capsys.readouterr()
    assert rc == 0 and err == ""
    assert out == contract.render_skill(__version__)
    assert not (home / ".claude").exists()


def test_init_stdout_with_no_user_still_prints(home, tmp_path, capsys):
    rc = m.main(["init", "--stdout", "--no-user"])
    out, _ = capsys.readouterr()
    assert rc == 0 and out == contract.render_skill(__version__)
    assert not (home / ".claude").exists()
    rc = m.main(["init", "--stdout", "--repo", str(tmp_path / "missing")])
    out, err = capsys.readouterr()
    assert rc == 2 and out == "" and err.startswith("error: --repo")


def test_init_repo_not_a_directory_exits_2(home, tmp_path, capsys):
    rc = m.main(["init", "--repo", str(tmp_path / "missing")])
    out, err = capsys.readouterr()
    assert rc == 2 and out == ""
    assert err.startswith("error: --repo")
    assert not _user_skill(home).exists()
