from __future__ import annotations

from ..llm import LLMError
from .openai_compat import LOADED_CONTEXT_PROBE_TIMEOUT, OpenAICompatClient, _origin

# Ollama's OpenAI-compatible endpoint. The same string is DEFAULT_BASE_URLS's
# "ollama" entry -- duplicated the way openai_compat.DEFAULT_BASE_URL already
# is, so dirtywork.providers can keep importing its adapters lazily; a test
# pins the two together.
OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434/v1"


class OllamaClient(OpenAICompatClient):
    """Ollama as a first-class provider (spec §3.1).

    The wire format is the parent's for everything dirtywork uses: ids with a
    `:tag`, `tool_calls` with string `arguments` and ids, `finish_reason:
    "tool_calls"`, `role: "tool"` accepted, and an extra `message.reasoning`
    field the parent's parser already ignores. Exactly three things differ, and
    each is overridden below for a stated reason.

    Parallel tool calls are UNVERIFIED on Ollama: the fixtures assert our
    parser, not the server."""

    name = "ollama"

    def __init__(self, base_url: str = OLLAMA_DEFAULT_BASE_URL, timeout: int = 600,
                 **kwargs):
        """The only reason this override exists: the parent assigns
        `self.base_url` from ITS OWN default when `base_url` is None, so a
        class attribute could never change the default. `None` -> the Ollama
        default; an explicit "" is a caller choice and is passed through, same
        as the parent. Everything else -- the transport, the rstrip, the
        timeout -- is the parent's."""
        super().__init__(OLLAMA_DEFAULT_BASE_URL if base_url is None else base_url,
                         timeout, **kwargs)

    def context_window(self, model: str):
        """Always None. The parent's CONTEXT_WINDOWS is LM STUDIO's table,
        keyed by LM Studio's model ids; without this override an Ollama user
        running a same-named model would inherit LM Studio's number and see it
        recorded as `provider:ollama` -- a fabricated answer. Falling through
        to /api/ps, then to the default, is the honest behaviour."""
        return None

    def loaded_context_window(self, model: str):
        """The context length Ollama currently has `model` loaded with, or
        None. `GET {origin}/api/ps` lists ONLY resident models, so no `state`
        check is needed or possible; `context_length` there is the loaded
        `num_ctx` and moves when a chat sets `options.num_ctx`.

        Matching is on the `model` key ONLY: Ollama sets `model` and `name` to
        the same tagged id, so matching both would make one entry matchable
        twice. The FIRST match decides -- an entry that matches but cannot
        answer returns None rather than letting a later entry answer for it.

        Cold start, stated: Ollama's /v1/models lists PULLED models, so
        preflight passes for a model that is not resident. /api/ps then has no
        entry, the window falls to the default (32768), and Ollama loads its
        own smaller num_ctx -- silent server-side truncation, not a visible
        failure. `docs/operating.md` tells Ollama users to `ollama run <model>`
        first or pass --context-window.

        Same swallow-everything contract as the parent: connection error,
        timeout, non-2xx, non-JSON, wrong shape -- all None."""
        url = f"{_origin(self.base_url)}/api/ps"
        try:
            body = self._http_json(url, None, {"Content-Type": "application/json"},
                                   LOADED_CONTEXT_PROBE_TIMEOUT, method="GET")
        except LLMError:
            return None      # LLMTimeout is an LLMError: both mean "no answer"
        if not isinstance(body, dict) or not isinstance(body.get("models"), list):
            return None
        for entry in body["models"]:
            if not isinstance(entry, dict) or entry.get("model") != model:
                continue
            loaded = entry.get("context_length")
            if isinstance(loaded, int) and not isinstance(loaded, bool) and loaded > 0:
                return loaded
            return None
        return None
