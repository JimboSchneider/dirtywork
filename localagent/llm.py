from __future__ import annotations

import http.client
import json
import socket
import urllib.error
import urllib.request


class LLMError(Exception):
    """Raised when the LM Studio server is unreachable or returns garbage."""


class LLMTimeout(LLMError):
    """Raised when a request to LM Studio times out."""


class LMStudioClient:
    def __init__(self, base_url: str = "http://localhost:1234/v1", timeout: int = 600):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, payload: dict | None = None,
                 timeout: float | None = None) -> dict:
        url = f"{self.base_url}{path}"
        try:
            data = json.dumps(payload).encode() if payload is not None else None
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )
        except (ValueError, TypeError) as e:
            raise LLMError(f"invalid request for LM Studio at {self.base_url!r}: {e}")
        effective_timeout = timeout if timeout is not None else self.timeout
        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            try:
                detail = e.read()[:500]
            except Exception:
                detail = b"<unreadable error body>"
            raise LLMError(f"LM Studio HTTP {e.code} on {path}: {detail!r}")
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
            if isinstance(e, socket.timeout) or isinstance(getattr(e, "reason", None), socket.timeout):
                raise LLMTimeout(f"request to {path} timed out after {effective_timeout}s")
            raise LLMError(f"cannot reach LM Studio at {self.base_url}: {e}")
        try:
            return json.loads(body.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise LLMError(f"invalid JSON from LM Studio on {path}: {e}")

    def list_models(self) -> list[str]:
        body = self._request("/models")
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise LLMError("unexpected /models response shape from LM Studio")
        ids = []
        for m in body["data"]:
            if not isinstance(m, dict) or not isinstance(m.get("id"), str):
                raise LLMError("unexpected /models entry shape from LM Studio")
            ids.append(m["id"])
        return ids

    def chat(
        self,
        model: str,
        messages: list,
        tools: list,
        temperature: float | None = None,
        max_tokens: int = 4096,
        timeout: float | None = None,
    ) -> dict:
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if tools:
            payload["tools"] = tools
        if temperature is not None:
            payload["temperature"] = temperature
        return self._request("/chat/completions", payload, timeout=timeout)
