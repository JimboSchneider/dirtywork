from __future__ import annotations

import json
import urllib.error
import urllib.request


class LLMError(Exception):
    """Raised when the LM Studio server is unreachable or returns garbage."""


class LMStudioClient:
    def __init__(self, base_url: str = "http://localhost:1234/v1", timeout: int = 600):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, payload: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise LLMError(f"LM Studio HTTP {e.code} on {path}: {e.read()[:500]!r}")
        except (urllib.error.URLError, OSError) as e:
            raise LLMError(f"cannot reach LM Studio at {self.base_url}: {e}")
        except json.JSONDecodeError as e:
            raise LLMError(f"invalid JSON from LM Studio on {path}: {e}")

    def list_models(self) -> list[str]:
        body = self._request("/models")
        return [m["id"] for m in body.get("data", [])]

    def chat(
        self,
        model: str,
        messages: list,
        tools: list,
        temperature: float | None = None,
        max_tokens: int = 4096,
    ) -> dict:
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if tools:
            payload["tools"] = tools
        if temperature is not None:
            payload["temperature"] = temperature
        return self._request("/chat/completions", payload)
