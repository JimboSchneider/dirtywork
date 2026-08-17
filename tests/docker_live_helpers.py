# tests/docker_live_helpers.py - shared helpers for the real-subprocess/
# real-Docker suites (test_docker_live.py, test_docker_lifecycle.py). Single
# source for the scripted-LLM-response builders and the throwaway git repo
# fixture both suites need.
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _resp(content=None, tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def _call(call_id, name, args: dict):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def _make_live_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README.md").write_text("# demo\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)
    return repo
