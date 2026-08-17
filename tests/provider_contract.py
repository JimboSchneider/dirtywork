from __future__ import annotations

import json
from pathlib import Path

from dirtywork.providers import ToolCall, assistant_message, tool_message


class RecordingTransport:
    """Fake http_json: returns canned fixture bodies in call order and records
    every (url, payload, headers, timeout, method) it was called with."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, payload, headers, timeout, *, method="POST"):
        self.calls.append({"url": url, "payload": payload, "headers": headers,
                           "timeout": timeout, "method": method})
        return self.responses.pop(0)


class ProviderContract:
    """Shared behavioural contract every Provider adapter must satisfy. Subclass,
    set ``fixtures_dir`` to the adapter's own fixture directory, and implement
    ``make_client``/``_system_text``/``_tool_result_entries``."""

    fixtures_dir: Path

    def make_client(self, transport):
        raise NotImplementedError

    def _system_text(self, payload: dict):
        raise NotImplementedError

    def _tool_result_entries(self, payload: dict) -> list:
        raise NotImplementedError

    def _load(self, name: str) -> dict:
        return json.loads((self.fixtures_dir / name).read_text())

    def test_system_prompt_lands_where_wire_expects(self):
        transport = RecordingTransport([self._load("simple_ok.json")])
        client = self.make_client(transport)
        history = [
            {"role": "system", "content": "SYS PROMPT TEXT"},
            {"role": "user", "content": "hi"},
        ]
        client.chat("model-x", history, [], temperature=None, max_tokens=100, timeout=30)
        assert self._system_text(transport.calls[0]["payload"]) == "SYS PROMPT TEXT"

    def test_parallel_tool_calls_parse_in_order(self):
        transport = RecordingTransport([self._load("parallel_tool_calls.json")])
        client = self.make_client(transport)
        resp = client.chat("model-x", [{"role": "user", "content": "read two files"}], [],
                           temperature=None, max_tokens=100, timeout=30)
        assert len(resp.tool_calls) == 2
        assert resp.tool_calls[0].name == "read_file"
        assert resp.tool_calls[0].arguments == {"path": "a.txt"}
        assert resp.tool_calls[1].name == "read_file"
        assert resp.tool_calls[1].arguments == {"path": "b.txt"}
        assert resp.tool_calls[0].id != resp.tool_calls[1].id
        assert resp.finish_reason == "tool_calls"

    def test_malformed_tool_call_sets_error_others_intact(self):
        transport = RecordingTransport([self._load("malformed_tool_call.json")])
        client = self.make_client(transport)
        resp = client.chat("model-x", [{"role": "user", "content": "do two things"}], [],
                           temperature=None, max_tokens=100, timeout=30)
        assert len(resp.tool_calls) == 2
        ok_calls = [tc for tc in resp.tool_calls if tc.error is None]
        bad_calls = [tc for tc in resp.tool_calls if tc.error is not None]
        assert len(ok_calls) == 1 and ok_calls[0].name == "list_dir"
        assert len(bad_calls) == 1
        assert "malformed" in bad_calls[0].error.lower()
        # An unaddressable entry carries no id: the runner cannot answer it with
        # a tool result, and must count it as a malformed *entry*.
        assert bad_calls[0].id == ""

    def test_tool_results_serialize_in_order_with_ids(self):
        transport = RecordingTransport([self._load("simple_ok.json")])
        client = self.make_client(transport)
        tool_calls = [
            ToolCall(id="call_1", name="read_file", arguments={"path": "a.txt"}, error=None),
            ToolCall(id="call_2", name="read_file", arguments={"path": "b.txt"}, error=None),
        ]
        history = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "read two files"},
            assistant_message(None, tool_calls),
            tool_message("call_1", "contents of a"),
            tool_message("call_2", "contents of b"),
        ]
        client.chat("model-x", history, [], temperature=None, max_tokens=100, timeout=30)
        entries = self._tool_result_entries(transport.calls[0]["payload"])
        assert entries == [("call_1", "contents of a"), ("call_2", "contents of b")]

    def test_finish_reason_mapping(self):
        cases = [
            ("finish_reason_stop.json", "stop"),
            ("parallel_tool_calls.json", "tool_calls"),
            ("finish_reason_length_text.json", "length"),
        ]
        for fixture, expected in cases:
            transport = RecordingTransport([self._load(fixture)])
            client = self.make_client(transport)
            resp = client.chat("model-x", [{"role": "user", "content": "hi"}], [],
                               temperature=None, max_tokens=100, timeout=30)
            assert resp.finish_reason == expected, f"{fixture}: expected {expected}, got {resp.finish_reason}"

    def test_usage_normalization(self):
        transport = RecordingTransport([self._load("usage_missing.json")])
        client = self.make_client(transport)
        resp = client.chat("model-x", [{"role": "user", "content": "hi"}], [],
                           temperature=None, max_tokens=100, timeout=30)
        assert resp.usage == {"prompt_tokens": 0, "completion_tokens": 0}

        transport = RecordingTransport([self._load("usage_nan_negative.json")])
        client = self.make_client(transport)
        resp = client.chat("model-x", [{"role": "user", "content": "hi"}], [],
                           temperature=None, max_tokens=100, timeout=30)
        # NaN/-5 are server-controlled and would emit invalid JSON downstream.
        assert resp.usage == {"prompt_tokens": 0, "completion_tokens": 0}

    def test_max_tokens_cutoff_mid_call(self):
        transport = RecordingTransport([self._load("bad_json_arguments.json")])
        client = self.make_client(transport)
        resp = client.chat("model-x", [{"role": "user", "content": "write a big file"}], [],
                           temperature=None, max_tokens=10, timeout=30)
        assert resp.finish_reason == "length"
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].error is not None
        # Addressable (it has an id), so the runner answers it with an error
        # tool result rather than dropping it.
        assert resp.tool_calls[0].id == "call_badargs"