from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from dirtywork.builtin_tools import default_registry
from dirtywork.runner import Runner
from dirtywork.transcript import Transcript

from .provider_doubles import DictProvider, patch_provider, text_body, tool_call_body

DOC = Path(__file__).parent.parent / "docs" / "transcript-schema.md"

EVENT_NAMES = ["run_start", "assistant", "tool_result", "guardrail_block", "nudge",
               "sandbox_reset", "verify", "run_end"]
NUDGE_KINDS = ["truncated", "empty", "text_tool_call", "stall"]
STATUSES = ["completed", "max_turns", "timeout", "context_exhausted", "model_error",
            "interrupted", "stalled", "stuck", "verify_failed", "budget_exceeded",
            "sandbox_error", "export_failed"]
RUN_END_FIELDS = ["diff_stat", "untracked", "patch_path", "escaping_symlinks",
                  "dropped_git_entries", "worktree_bytes", "worktree_files",
                  "export_status", "watchdog_violation", "watchdog_violation_kind",
                  "finalize_error", "stuck_on", "files_changed",
                  "files_changed_truncated", "last_tool_result", "last_assistant_text",
                  "verify"]


def _doc_tokens():
    return set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", DOC.read_text(encoding="utf-8")))


def test_doc_exists_and_documents_every_event_name():
    assert DOC.exists(), f"{DOC} does not exist"
    tokens = _doc_tokens()
    for name in EVENT_NAMES:
        assert name in tokens, f"event '{name}' is not documented in {DOC.name}"


def test_doc_documents_schema_version_v1_v2_statuses_and_nudge_kinds():
    text = DOC.read_text(encoding="utf-8")
    tokens = _doc_tokens()
    assert "schema_version" in tokens
    assert "v1" in text and "v2" in text
    for status in STATUSES:
        assert status in tokens, f"status '{status}' is not documented"
    for kind in NUDGE_KINDS:
        assert kind in tokens, f"nudge kind '{kind}' is not documented"
    for field in RUN_END_FIELDS:
        assert field in tokens, f"run_end field '{field}' is not documented"


def test_doc_documents_the_finish_tool_and_the_nine_tools():
    tokens = _doc_tokens()
    for name in ("read_file", "write_file", "edit_file", "insert_before", "insert_after",
                 "list_dir", "grep", "bash", "finish"):
        assert name in tokens, f"tool '{name}' is not documented"


class _NudgingProvider(DictProvider):
    """Turn 1 calls a tool, turn 2 replies with nothing (→ `empty` nudge),
    turn 3 answers."""

    def reply(self, model, history, tools):
        if self.calls == 1:
            return tool_call_body("read_file", {"path": "f.txt"})
        if self.calls == 2:
            return text_body("")
        return text_body("done")


def test_a_real_run_emits_the_documented_events(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "f.txt").write_text("data\n")
    from dirtywork.sandbox.host import HostSandbox
    transcript = Transcript(tmp_path / "t.jsonl")
    registry = default_registry(transcript=transcript)
    r = Runner(_NudgingProvider(), registry, HostSandbox(wt), transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    events = [json.loads(l) for l in (tmp_path / "t.jsonl").read_text().splitlines()]
    kinds = [e["event"] for e in events]
    assert kinds[0] == "run_start" and kinds[-1] == "run_end"
    assert set(kinds) == {"run_start", "assistant", "tool_result", "nudge", "run_end"}
    run_start = events[0]
    assert run_start["schema_version"] == 2
    assert run_start["context_window"]
    nudge = next(e for e in events if e["event"] == "nudge")
    assert nudge["kind"] in NUDGE_KINDS and isinstance(nudge["turn"], int)
    assert result.status == "completed"
    # every field emitted by a real run must be documented
    documented = _doc_tokens()
    for e in events:
        for key in e:
            if key in ("ts", "event"):
                continue
            assert key in documented, f"{e['event']}.{key} is not documented in {DOC.name}"


class _FinishingProvider(DictProvider):
    def reply(self, model, history, tools):
        return tool_call_body("finish", {"summary": "all done"}, call_id="f1")


def test_finish_appears_as_an_ordinary_tool_call(tmp_path):
    from dirtywork.sandbox.host import HostSandbox
    wt = tmp_path / "wt"
    wt.mkdir()
    transcript = Transcript(tmp_path / "t.jsonl")
    r = Runner(_FinishingProvider(), default_registry(transcript=transcript),
               HostSandbox(wt), transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    events = [json.loads(l) for l in (tmp_path / "t.jsonl").read_text().splitlines()]
    assistant = next(e for e in events if e["event"] == "assistant")
    assert assistant["tool_calls"][0]["name"] == "finish"
    tool_result = next(e for e in events if e["event"] == "tool_result")
    assert tool_result["result"] == "run finished"
    assert result.final_message == "all done"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "--allow-empty", "-m", "i"], capture_output=True)
    return repo


class _DoneProvider(DictProvider):
    def reply(self, model, history, tools):
        return text_body("done")


def test_stdout_and_run_json_fields_are_all_documented(tmp_path, monkeypatch, capsys):
    import dirtywork.__main__ as m
    repo = _repo(tmp_path)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    patch_provider(monkeypatch, m, lambda base_url=None: _DoneProvider(base_url))

    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "some task"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 2
    assert payload["provider"] == "openai"
    assert "run_dir" in payload
    run_json = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert run_json["provider"] == "openai"

    documented = _doc_tokens()
    for key in payload:
        assert key in documented, f"stdout JSON key '{key}' is not documented in {DOC.name}"
    for key in run_json:
        assert key in documented, f"run.json key '{key}' is not documented in {DOC.name}"


def test_stdout_contract_fields_never_disappear(tmp_path, monkeypatch, capsys):
    import dirtywork.__main__ as m
    repo = _repo(tmp_path)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    patch_provider(monkeypatch, m, lambda base_url=None: _DoneProvider(base_url))
    assert m.main(["run", "--repo", str(repo), "--sandbox", "none", "some task"]) == 0
    payload = json.loads(capsys.readouterr().out)
    for key in ("status", "worktree", "branch", "transcript", "turns", "usage",
                "final_message"):
        assert key in payload


def test_version_is_in_step_with_pyproject():
    import dirtywork
    pyproject = (Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    # No literal here on purpose: the contract is that the two sources AGREE,
    # so a release only bumps pyproject.toml and dirtywork/__init__.py.
    assert dirtywork.__version__.count(".") == 2
    assert f'version = "{dirtywork.__version__}"' in pyproject
