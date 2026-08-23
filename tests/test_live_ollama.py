"""Live smoke against a real Ollama server. Marked `ollama`, which
pyproject.toml's addopts deselects, AND skipped when no server answers -- so it
never runs by accident and never fails a normal suite. tests/test_live.py is
untouched: its module-level skipif probes LM Studio, a different server."""
from __future__ import annotations

import os

import pytest

from dirtywork.llm import LLMError
from dirtywork.providers import get_provider
from dirtywork.providers.ollama import OLLAMA_DEFAULT_BASE_URL

# Ollama model ids carry a tag; override for a machine with a different model.
MODEL = os.environ.get("DIRTYWORK_OLLAMA_MODEL", "gemma4:latest")

PROBE_TOOL = [{"type": "function", "function": {
    "name": "list_dir",
    "description": "List files in a directory",
    "parameters": {"type": "object",
                   "properties": {"path": {"type": "string"}},
                   "required": ["path"]}}}]


def _ollama_up() -> bool:
    try:
        get_provider("ollama", timeout=5).list_models()
        return True
    except LLMError:
        return False


pytestmark = [pytest.mark.ollama,
              pytest.mark.skipif(not _ollama_up(), reason="Ollama not running")]


def test_live_models_list_includes_the_tagged_id():
    models = get_provider("ollama").list_models()
    assert models, "Ollama reports no pulled models"
    assert all(isinstance(m, str) for m in models)
    assert MODEL in models, f"{MODEL} is not pulled; set DIRTYWORK_OLLAMA_MODEL"


def test_live_tool_call_and_loaded_window():
    client = get_provider("ollama")
    assert client.base_url == OLLAMA_DEFAULT_BASE_URL
    resp = client.chat(MODEL, [{"role": "user", "content": "What files are in src?"}],
                       tools=PROBE_TOOL, max_tokens=200, temperature=0)
    assert resp.tool_calls, f"{MODEL} returned no tool_calls: {resp.text!r:.200}"
    assert resp.tool_calls[0].name == "list_dir"
    assert resp.tool_calls[0].id
    assert resp.finish_reason in ("tool_calls", "stop", "length")
    # The model is resident now (the chat above loaded it), so /api/ps answers.
    window = client.loaded_context_window(MODEL)
    assert window is None or (isinstance(window, int) and window > 0)
