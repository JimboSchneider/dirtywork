from __future__ import annotations

import http.client
import json
import socket
import time
import urllib.error
import urllib.request

# urllib's timeout is per-socket-op, not a whole-transfer deadline, and resp.read()
# is unbounded — so a hostile/buggy endpoint could drip-feed a response for far
# longer than the run's timeout, or return a giant body that exhausts memory.
MAX_RESPONSE_BYTES = 64 * 1024 * 1024


def _underlying_socket(resp):
    """Best-effort access to a urlopen response's raw socket (CPython) so its
    timeout can be tightened per read; None if the internals differ."""
    raw = getattr(getattr(resp, "fp", None), "raw", None)
    return getattr(raw, "_sock", None)


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
        deadline = time.monotonic() + effective_timeout
        resp = None
        try:
            resp = urllib.request.urlopen(req, timeout=effective_timeout)
            # urllib's timeout is per-socket-op and resp.read() refills across many
            # recvs, so a drip-fed body could outlast the deadline. Read one recv at
            # a time (read1 returns available bytes) with the socket timeout tightened
            # to the REMAINING wall-clock budget before each read — a hard bound.
            sock = _underlying_socket(resp)
            body = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LLMTimeout(f"request to {path} exceeded {effective_timeout}s")
                if sock is not None:
                    sock.settimeout(remaining)
                try:
                    chunk = resp.read1(65536)
                except socket.timeout:
                    raise LLMTimeout(f"request to {path} exceeded {effective_timeout}s")
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise LLMError(
                        f"response from {path} exceeds {MAX_RESPONSE_BYTES} bytes"
                    )
            body = bytes(body)
        except urllib.error.HTTPError as e:
            try:
                detail = e.read(500)
            except Exception:
                detail = b"<unreadable error body>"
            raise LLMError(f"LM Studio HTTP {e.code} on {path}: {detail!r}")
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
            if isinstance(e, socket.timeout) or isinstance(getattr(e, "reason", None), socket.timeout):
                raise LLMTimeout(f"request to {path} timed out after {effective_timeout}s")
            raise LLMError(f"cannot reach LM Studio at {self.base_url}: {e}")
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass
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
