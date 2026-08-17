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
    """Raised when a model-serving endpoint is unreachable or returns garbage."""


class LLMTimeout(LLMError):
    """Raised when a request to a model-serving endpoint times out."""


class MalformedResponse(LLMError):
    """The endpoint answered, but the body is not a response we can read.

    Narrower than LLMError on purpose: Runner.run() converts this to
    status='model_error' through its own finish() (so finalize() runs and a
    run_end event is written), while a plain LLMError still escapes the runner
    to __main__._fail_run, which keeps a docker volume for recovery."""


def http_json(url: str, payload, headers: dict, timeout: float, *, method: str = "POST") -> dict:
    """Bounded stdlib HTTP JSON request shared by every Provider adapter:
    a whole-transfer wall-clock deadline (not urllib's per-socket-op timeout),
    a MAX_RESPONSE_BYTES cap, and every failure mode raised as LLMError or
    LLMTimeout. ``payload=None`` sends no request body (for GET); ``method``
    overrides the HTTP verb (default POST)."""
    try:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
    except (ValueError, TypeError) as e:
        raise LLMError(f"invalid request for {url!r}: {e}")
    deadline = time.monotonic() + timeout
    resp = None
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        # urllib's timeout is per-socket-op and resp.read() refills across many
        # recvs, so a drip-fed body could outlast the deadline. Read one recv at
        # a time (read1 returns available bytes) with the socket timeout tightened
        # to the REMAINING wall-clock budget before each read — a hard bound.
        sock = _underlying_socket(resp)
        body = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LLMTimeout(f"request to {url} exceeded {timeout}s")
            if sock is not None:
                try:
                    sock.settimeout(remaining)
                except OSError:  # http.client already closed the socket (body fully buffered)
                    sock = None
            try:
                chunk = resp.read1(65536)
            except socket.timeout:
                raise LLMTimeout(f"request to {url} exceeded {timeout}s")
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise LLMError(f"response from {url} exceeds {MAX_RESPONSE_BYTES} bytes")
        body = bytes(body)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read(500)
        except Exception:
            detail = b"<unreadable error body>"
        raise LLMError(f"HTTP {e.code} on {url}: {detail!r}")
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
        if isinstance(e, socket.timeout) or isinstance(getattr(e, "reason", None), socket.timeout):
            raise LLMTimeout(f"request to {url} timed out after {timeout}s")
        raise LLMError(f"cannot reach {url}: {e}")
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
    try:
        return json.loads(body.decode())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise LLMError(f"invalid JSON from {url}: {e}")


def __getattr__(name):
    """`LMStudioClient` moved to providers.openai_compat.OpenAICompatClient in
    0.6 (SP3). The alias is kept for one release, resolved LAZILY through PEP
    562 rather than an import at the bottom of this module: openai_compat
    imports http_json/LLMError from here, so an eager import here would make
    `import dirtywork.providers.openai_compat` (with llm not yet imported) fail
    against a partially-initialized module. New code should use
    dirtywork.providers.get_provider('openai', ...)."""
    if name == "LMStudioClient":
        from .providers.openai_compat import OpenAICompatClient
        return OpenAICompatClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")