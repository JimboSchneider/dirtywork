from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from localagent.llm import LLMError, LLMTimeout, LMStudioClient


class _FakeLMStudio(BaseHTTPRequestHandler):
    last_payload: dict = {}
    get_body: object = {"data": [{"id": "m1"}, {"id": "m2"}]}

    def do_GET(self):
        body = json.dumps(_FakeLMStudio.get_body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        _FakeLMStudio.last_payload = json.loads(self.rfile.read(length))
        body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silence test output
        pass


@pytest.fixture()
def server():
    _FakeLMStudio.get_body = {"data": [{"id": "m1"}, {"id": "m2"}]}
    srv = HTTPServer(("127.0.0.1", 0), _FakeLMStudio)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()
    _FakeLMStudio.get_body = {"data": [{"id": "m1"}, {"id": "m2"}]}


class _SlowLMStudio(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.rfile.read(length)
        time.sleep(2)
        body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silence test output
        pass


@pytest.fixture()
def slow_server():
    srv = HTTPServer(("127.0.0.1", 0), _SlowLMStudio)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def test_list_models(server: str):
    client = LMStudioClient(base_url=server)
    assert client.list_models() == ["m1", "m2"]


@pytest.mark.parametrize("bad_body", [
    [],
    {"data": {}},
    {"data": [{}]},
])
def test_list_models_unexpected_shape_raises_llmerror(server: str, bad_body):
    _FakeLMStudio.get_body = bad_body
    client = LMStudioClient(base_url=server)
    with pytest.raises(LLMError):
        client.list_models()


def test_chat_payload_and_response(server: str):
    client = LMStudioClient(base_url=server)
    resp = client.chat("m1", [{"role": "user", "content": "x"}], tools=[])
    assert resp["choices"][0]["message"]["content"] == "hi"
    payload = _FakeLMStudio.last_payload
    assert payload["model"] == "m1"
    assert payload["max_tokens"] == 4096
    assert "temperature" not in payload  # omitted when None


def test_chat_temperature_included(server: str):
    client = LMStudioClient(base_url=server)
    client.chat("m1", [], tools=[], temperature=0.2)
    assert _FakeLMStudio.last_payload["temperature"] == 0.2


def test_chat_timeout_kwarg_passthrough(server: str):
    client = LMStudioClient(base_url=server)
    resp = client.chat("m1", [{"role": "user", "content": "x"}], tools=[], timeout=5)
    assert resp["choices"][0]["message"]["content"] == "hi"


def test_connection_error_raises_llmerror():
    client = LMStudioClient(base_url="http://127.0.0.1:1/v1", timeout=2)
    with pytest.raises(LLMError):
        client.list_models()


def test_chat_tools_omitted_when_empty(server: str):
    client = LMStudioClient(base_url=server)
    client.chat("m1", [], tools=[])
    assert "tools" not in _FakeLMStudio.last_payload


def test_chat_timeout_raises_llmtimeout(slow_server: str):
    client = LMStudioClient(base_url=slow_server, timeout=0.5)
    with pytest.raises(LLMTimeout) as exc_info:
        client.chat("m1", [{"role": "user", "content": "x"}], tools=[])
    assert isinstance(exc_info.value, LLMError)


def test_chat_tools_included_when_nonempty(server: str):
    client = LMStudioClient(base_url=server)
    tools = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object", "properties": {}}}}]
    client.chat("m1", [], tools=tools)
    assert _FakeLMStudio.last_payload["tools"] == tools
