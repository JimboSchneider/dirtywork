from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from dirtywork.llm import LLMError
from dirtywork.providers import get_provider

QWEN = "qwen/qwen3-coder-next"
DEVSTRAL = "mistralai/devstral-small-2-2512"

PROBE_TOOL = [{"type": "function", "function": {
    "name": "list_dir",
    "description": "List files in a directory",
    "parameters": {"type": "object",
                   "properties": {"path": {"type": "string"}},
                   "required": ["path"]}}}]


def _server_up() -> bool:
    try:
        get_provider("openai", timeout=5).list_models()
        return True
    except LLMError:
        return False


pytestmark = [pytest.mark.live,
              pytest.mark.skipif(not _server_up(), reason="LM Studio not running")]


@pytest.mark.parametrize("model", [QWEN, DEVSTRAL])
def test_model_emits_tool_calls(model):
    """Devstral tool-calling was unverified at design time — this settles it."""
    client = get_provider("openai")
    resp = client.chat(model,
                       [{"role": "user", "content": "What files are in src?"}],
                       tools=PROBE_TOOL, max_tokens=200, temperature=0)
    calls = resp.tool_calls
    assert calls, f"{model} returned no tool_calls: {resp.text!r:.200}"
    assert calls[0].name == "list_dir"


def test_end_to_end_run(tmp_path: Path):
    """Full CLI run against a throwaway repo: create file via the agent."""
    repo = tmp_path / "demo"
    repo.mkdir()
    for cmd in (["init", "-b", "main"],
                ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True,
                       capture_output=True)
    (repo / "README.md").write_text("# demo\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"],
                   check=True, capture_output=True)

    res = subprocess.run(
        ["dirtywork", "run", "--repo", str(repo), "--max-turns", "10",
         "Create a file named hello.txt containing exactly the word: hello"],
        capture_output=True, text=True, timeout=600)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["status"] == "completed"
    created = Path(out["worktree"]) / "hello.txt"
    assert created.exists() and "hello" in created.read_text()
