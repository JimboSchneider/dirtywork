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


from .test_runner import SCENARIOS, _run_scenario, parts  # noqa: F401  (fixture re-exported)


@pytest.mark.live
@pytest.mark.parametrize("build", SCENARIOS)
def test_devstral_accepts_runner_histories(parts, build):
    """Spec #60 §7: replay the runner-produced histories for the #60 shapes
    against the loaded Devstral; a strict template renders every request.

    Mistral's chat template is documented to require tool-call ids of exactly
    9 alphanumeric characters; the scenario builders use short ids (f1/b1/c1).
    Probed directly against this LM Studio build: it accepted the short
    ids as-is (no 400), so no remapping is applied here -- if a future model
    build starts rejecting them with a 400 naming the id, pad ids to 9 chars
    in the serialized wire messages before posting; the shape under test
    (message role/ordering) is unaffected either way.
    """
    import json
    import urllib.request
    from dirtywork.providers.openai_compat import _to_openai_messages
    base = "http://localhost:1234/v1"
    model = "mistralai/devstral-small-2-2512"
    try:
        with urllib.request.urlopen(f"{base}/models", timeout=5) as r:
            ids = [m["id"] for m in json.load(r)["data"]]
    except Exception as e:                              # noqa: BLE001
        pytest.skip(f"LM Studio not reachable: {e}")
    if model not in ids:
        pytest.skip(f"{model} not loaded")
    provider, _r, _e = _run_scenario(parts, build)
    tools = [{"type": "function", "function": {"name": n, "description": "x",
              "parameters": {"type": "object", "properties": {"summary": {"type": "string"},
                                                              "command": {"type": "string"},
                                                              "path": {"type": "string"}}}}}
             for n in ("finish", "bash", "read_file", "write_file")]
    for history in provider.requests:
        body = json.dumps({"model": model, "messages": _to_openai_messages(history),
                           "tools": tools, "max_tokens": 1, "temperature": 0}).encode()
        req = urllib.request.Request(f"{base}/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            assert r.status == 200
