from __future__ import annotations

import re

from dirtywork.toolspec import Caps, MISSING, ParamSpec, ToolRegistry, ToolSpec


def _fn_ping(sandbox, **kwargs):
    return "pong"


def _fn_echo(sandbox, text):
    return f"echo:{text}"


PING_SPEC = ToolSpec(
    name="ping",
    description="Reply pong.",
    params={},
    required=(),
    fn=_fn_ping,
    caps=Caps(fs="none"),
)

ECHO_SPEC = ToolSpec(
    name="echo",
    description="Echo the given text back.",
    params={"text": ParamSpec(type="string", description="Text to echo")},
    required=("text",),
    fn=_fn_echo,
    caps=Caps(fs="none"),
)


def test_register_and_schemas_wire_shape():
    registry = ToolRegistry()
    registry.register(PING_SPEC)
    registry.register(ECHO_SPEC)
    assert registry.schemas() == [
        {"type": "function", "function": {
            "name": "ping", "description": "Reply pong.",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "echo", "description": "Echo the given text back.",
            "parameters": {"type": "object", "properties": {
                "text": {"type": "string", "description": "Text to echo"}},
                "required": ["text"]}}},
    ]


def test_schemas_preserve_registration_order():
    registry = ToolRegistry()
    registry.register(ECHO_SPEC)
    registry.register(PING_SPEC)
    assert [s["function"]["name"] for s in registry.schemas()] == ["echo", "ping"]


def test_missing_sentinel_is_falsy_and_distinct_from_none():
    assert not MISSING
    assert MISSING is not None
    assert repr(MISSING) == "MISSING"


def test_param_without_description_omits_key_from_schema():
    registry = ToolRegistry()
    spec = ToolSpec(
        name="bare", description="No param descriptions.",
        params={"n": ParamSpec(type="integer")}, required=(),
        fn=lambda sandbox, n=0: str(n), caps=Caps(fs="none"),
    )
    registry.register(spec)
    props = registry.schemas()[0]["function"]["parameters"]["properties"]
    assert props == {"n": {"type": "integer"}}


def test_spec_lookup_returns_the_registered_object_or_none():
    registry = ToolRegistry()
    registry.register(ECHO_SPEC)
    assert registry.spec("echo") is ECHO_SPEC
    assert registry.spec("nonexistent") is None


def test_terminal_defaults_false_and_can_be_declared():
    assert PING_SPEC.terminal is False
    end = ToolSpec(name="end", description="Ends the run.", params={}, required=(),
                   fn=_fn_ping, caps=Caps(fs="none"), terminal=True)
    assert end.terminal is True


def test_register_duplicate_name_raises():
    r = ToolRegistry()
    r.register(PING_SPEC)
    with pytest.raises(ValueError, match="tool 'ping' is already registered"):
        r.register(PING_SPEC)


import time

import pytest

from dirtywork.toolspec import TOOL_CALL_MARKERS, TRANSCRIPT_PREVIEW_CHARS, ToolResult


class _RecordingTranscript:
    def __init__(self):
        self.events = []

    def write(self, event, **fields):
        self.events.append((event, fields))


def _fn_blocked(sandbox, **kwargs):
    return "BLOCKED: not allowed here"


def _fn_long(sandbox, **kwargs):
    return "x" * 50


def _fn_needs_timeout(sandbox, timeout=10):
    return f"ran with timeout={timeout}"


def _fn_paths(sandbox, path=".", offset=0, limit=400):
    return f"paths:{path}:{offset}:{limit}"


def _fn_cmd(sandbox, command, timeout=120):
    return f"exit code: 0\n{command}"


BLOCKED_SPEC = ToolSpec(
    name="blockme", description="Always blocked.", params={}, required=(),
    fn=_fn_blocked, caps=Caps(fs="none"))

CAPPED_SPEC = ToolSpec(
    name="longtext", description="Returns 50 chars.", params={}, required=(),
    fn=_fn_long, caps=Caps(fs="none", max_output_chars=10))

TIMEOUT_SPEC = ToolSpec(
    name="waitfor", description="Echoes its timeout.",
    params={"timeout": ParamSpec(type="integer", default=10)}, required=(),
    fn=_fn_needs_timeout, caps=Caps(fs="none", timeout_default=10, timeout_max=20))

BYTES_SPEC = ToolSpec(
    name="bytesin", description="Takes a string.",
    params={"text": ParamSpec(type="string")}, required=("text",),
    fn=_fn_echo, caps=Caps(fs="none", max_input_bytes=5))


def _fn_needs_timeout_hidden(sandbox, path=".", timeout=30):
    return f"listed {path} with timeout={timeout}"


HIDDEN_TIMEOUT_SPEC = ToolSpec(
    name="listwait",
    description="grep-shaped: caps.timeout_default with no exposed timeout param.",
    params={"path": ParamSpec(type="string", default=".")}, required=(),
    fn=_fn_needs_timeout_hidden, caps=Caps(fs="read", timeout_default=30))

PATHS_SPEC = ToolSpec(
    name="paths", description="read_file-shaped.",
    params={"path": ParamSpec(type="string"),
            "offset": ParamSpec(type="integer", default=0),
            "limit": ParamSpec(type="integer", default=400)},
    required=("path",), fn=_fn_paths, caps=Caps(fs="read"))

CMD_SPEC = ToolSpec(
    name="cmd", description="bash-shaped.",
    params={"command": ParamSpec(type="string"),
            "timeout": ParamSpec(type="integer", default=120)},
    required=("command",), fn=_fn_cmd,
    caps=Caps(fs="write", timeout_default=120, timeout_max=600))

FULL_SPEC = ToolSpec(
    name="verbose", description="Transcribed in full.", params={}, required=(),
    fn=lambda sandbox: "y" * 5000, caps=Caps(fs="none", max_output_chars=100000,
                                             transcript="full"))

SILENT_SPEC = ToolSpec(
    name="silent", description="Never transcribed.", params={}, required=(),
    fn=lambda sandbox: "secret", caps=Caps(fs="none", transcript="none"))


def _registry():
    r = ToolRegistry()
    for spec in (PING_SPEC, ECHO_SPEC, BLOCKED_SPEC, CAPPED_SPEC, TIMEOUT_SPEC,
                 BYTES_SPEC, HIDDEN_TIMEOUT_SPEC, PATHS_SPEC, CMD_SPEC,
                 FULL_SPEC, SILENT_SPEC):
        r.register(spec)
    return r


def test_execute_unknown_tool():
    r = _registry()
    result = r.execute("nope", {}, sandbox=None, deadline=None)
    assert result.kind == "error"
    assert result.failure == "unknown_tool"
    assert result.text.startswith("ERROR: unknown tool 'nope'. Available:")
    assert "ping" in result.text and "echo" in result.text
    assert "To end the run call finish(summary=...)." in result.text


def test_execute_dispatches_and_fills_defaults():
    r = _registry()
    result = r.execute("ping", {}, sandbox=object(), deadline=None)
    assert result == ToolResult(text="pong", kind="ok", failure=None)


def test_execute_drops_unknown_parameters():
    # qwen and friends attach another harness's parameters (Claude Code's
    # `description` on bash). Dropping them keeps a habit from becoming three
    # bad_args strikes and an aborted run (SP1, commit 23a9c22).
    r = _registry()
    result = r.execute("paths", {"path": "a.txt", "description": "look"},
                       sandbox=object(), deadline=None)
    assert result.kind == "ok"
    assert result.text == "paths:a.txt:0:400"


def test_execute_missing_required_is_bad_args():
    r = _registry()
    result = r.execute("echo", {}, sandbox=object(), deadline=None)
    assert result.kind == "error"
    assert result.failure == "bad_args"
    assert result.text.startswith("ERROR: bad arguments for echo:")
    assert "text" in result.text


def test_execute_type_mismatch_is_bad_args():
    r = _registry()
    result = r.execute("echo", {"text": 123}, sandbox=object(), deadline=None)
    assert result.kind == "error"
    assert result.failure == "bad_args"
    assert "must be string" in result.text


def test_execute_bool_is_not_integer():
    r = ToolRegistry()
    spec = ToolSpec(name="takesint", description="d",
                    params={"n": ParamSpec(type="integer")}, required=("n",),
                    fn=lambda sandbox, n: f"n={n}", caps=Caps(fs="none"))
    r.register(spec)
    result = r.execute("takesint", {"n": True}, sandbox=object(), deadline=None)
    assert result.kind == "error" and result.failure == "bad_args"
    assert "must be integer" in result.text


def test_execute_int_accepted_for_number_param():
    r = ToolRegistry()
    spec = ToolSpec(name="takesnum", description="d",
                    params={"n": ParamSpec(type="number")}, required=("n",),
                    fn=lambda sandbox, n: f"n={n}", caps=Caps(fs="none"))
    r.register(spec)
    result = r.execute("takesnum", {"n": 3}, sandbox=object(), deadline=None)
    assert result.kind == "ok" and result.text == "n=3"


def test_execute_coerces_numeric_string_for_integer_param():
    # F3: local models routinely send "60" instead of 60 -- the old ToolExecutor
    # did int(timeout) itself; the coerced value must reach spec.fn as a real int.
    r = ToolRegistry()
    spec = ToolSpec(name="takesint", description="d",
                    params={"n": ParamSpec(type="integer")}, required=("n",),
                    fn=lambda sandbox, n: f"n={n}:{type(n).__name__}", caps=Caps(fs="none"))
    r.register(spec)
    result = r.execute("takesint", {"n": "60"}, sandbox=object(), deadline=None)
    assert result.kind == "ok"
    assert result.text == "n=60:int"


def test_execute_coerces_numeric_string_for_number_param():
    r = ToolRegistry()
    spec = ToolSpec(name="takesnum", description="d",
                    params={"n": ParamSpec(type="number")}, required=("n",),
                    fn=lambda sandbox, n: f"n={n}:{type(n).__name__}", caps=Caps(fs="none"))
    r.register(spec)
    result = r.execute("takesnum", {"n": "1.5"}, sandbox=object(), deadline=None)
    assert result.kind == "ok"
    assert result.text == "n=1.5:float"


def test_execute_non_numeric_string_stays_bad_args():
    r = ToolRegistry()
    spec = ToolSpec(name="takesint", description="d",
                    params={"n": ParamSpec(type="integer")}, required=("n",),
                    fn=lambda sandbox, n: f"n={n}", caps=Caps(fs="none"))
    r.register(spec)
    result = r.execute("takesint", {"n": "abc"}, sandbox=object(), deadline=None)
    assert result.kind == "error" and result.failure == "bad_args"


def test_execute_non_integer_numeric_string_for_integer_param_stays_bad_args():
    # "1.5" coerces for "number" but not for "integer".
    r = ToolRegistry()
    spec = ToolSpec(name="takesint", description="d",
                    params={"n": ParamSpec(type="integer")}, required=("n",),
                    fn=lambda sandbox, n: f"n={n}", caps=Caps(fs="none"))
    r.register(spec)
    result = r.execute("takesint", {"n": "1.5"}, sandbox=object(), deadline=None)
    assert result.kind == "error" and result.failure == "bad_args"


def test_execute_explicit_null_allowed_for_none_defaulted_param():
    # `grep(glob=None)`: a model that spells the default out explicitly must not
    # take a bad_args strike for it.
    r = ToolRegistry()
    spec = ToolSpec(name="g", description="d",
                    params={"pattern": ParamSpec(type="string"),
                            "glob": ParamSpec(type="string", default=None)},
                    required=("pattern",),
                    fn=lambda sandbox, pattern, glob=None: f"{pattern}:{glob}",
                    caps=Caps(fs="read"))
    r.register(spec)
    result = r.execute("g", {"pattern": "x", "glob": None}, sandbox=object(), deadline=None)
    assert result.kind == "ok" and result.text == "x:None"


def test_execute_fn_type_error_is_bad_args():
    r = ToolRegistry()
    def _picky(sandbox, n):
        raise TypeError("n must be positive")
    r.register(ToolSpec(name="picky", description="d",
                        params={"n": ParamSpec(type="integer")}, required=("n",),
                        fn=_picky, caps=Caps(fs="none")))
    result = r.execute("picky", {"n": 1}, sandbox=object(), deadline=None)
    assert result.kind == "error" and result.failure == "bad_args"
    assert result.text == "ERROR: bad arguments for picky: n must be positive"


def test_execute_caps_max_output_chars_truncates():
    r = _registry()
    result = r.execute("longtext", {}, sandbox=object(), deadline=None)
    assert result.kind == "ok"
    assert result.text.startswith("x" * 10)
    assert "truncated at 10 chars" in result.text


def test_execute_caps_max_input_bytes_rejects_oversized():
    r = _registry()
    result = r.execute("bytesin", {"text": "toolong"}, sandbox=object(), deadline=None)
    assert result.kind == "error"
    assert result.failure == "bad_args"
    assert "byte limit" in result.text


def test_execute_timeout_clamped_to_timeout_max():
    r = _registry()
    result = r.execute("waitfor", {"timeout": 999}, sandbox=object(), deadline=None)
    assert result.text == "ran with timeout=20"


def test_execute_timeout_clamped_to_remaining_deadline():
    r = _registry()
    deadline = time.monotonic() + 3
    result = r.execute("waitfor", {"timeout": 999}, sandbox=object(), deadline=deadline)
    assert result.text in ("ran with timeout=1", "ran with timeout=2", "ran with timeout=3")


def test_execute_injects_timeout_even_when_not_a_schema_param():
    r = _registry()
    deadline = time.monotonic() + 2
    result = r.execute("listwait", {}, sandbox=object(), deadline=deadline)
    assert result.text in ("listed . with timeout=1", "listed . with timeout=2")


def test_execute_deadline_exceeded_short_circuits_without_running_the_tool():
    r = _registry()
    deadline = time.monotonic() - 1
    result = r.execute("cmd", {"command": "touch created.txt"}, sandbox=object(),
                       deadline=deadline)
    assert result.kind == "error"
    assert result.failure is None          # today's executor resets the strike counter here
    assert "deadline exceeded" in result.text.lower()


def test_execute_blocked_writes_guardrail_block_and_kind():
    transcript = _RecordingTranscript()
    r = ToolRegistry(transcript=transcript)
    r.register(BLOCKED_SPEC)
    result = r.execute("blockme", {}, sandbox=object(), deadline=None)
    assert result.kind == "blocked"
    assert result.failure is None
    assert result.text.startswith("BLOCKED:")
    assert transcript.events and transcript.events[0][0] == "guardrail_block"
    assert transcript.events[0][1]["tool"] == "blockme"
    assert transcript.events[0][1]["reason"].startswith("BLOCKED:")


def test_execute_fn_exception_propagates():
    r = ToolRegistry()
    def _boom(sandbox, **kwargs):
        raise RuntimeError("kaboom")
    r.register(ToolSpec(name="boom", description="d", params={}, required=(),
                        fn=_boom, caps=Caps(fs="none")))
    with pytest.raises(RuntimeError, match="kaboom"):
        r.execute("boom", {}, sandbox=object(), deadline=None)


def test_transcript_preview_modes():
    r = _registry()
    assert r.transcript_preview("paths", "z" * 5000) == "z" * TRANSCRIPT_PREVIEW_CHARS
    assert r.transcript_preview("verbose", "y" * 5000) == "y" * 5000
    assert r.transcript_preview("silent", "secret") == ""
    assert r.transcript_preview("nonexistent", "z" * 5000) == "z" * TRANSCRIPT_PREVIEW_CHARS


# --- canonical_args: moved here verbatim in intent from tests/test_runner.py's
# --- test_canonical_args_normalizes_effective_arguments (R3). ProgressTracker
# --- depends on these exact semantics; two calls that do the same thing must
# --- look the same, or a stuck model could dodge `stalled` by varying noise.

def test_canonical_args_normalizes_effective_arguments():
    r = _registry()
    a = r.canonical_args("paths", {"path": "./f.txt", "description": "x"})
    b = r.canonical_args("paths", {"path": "f.txt", "offset": 0, "limit": 400})
    assert a == b == {"path": "f.txt", "offset": 0, "limit": 400}
    assert r.canonical_args("cmd", {"command": " ls \n", "timeout": 5}) == {"command": "ls"}
    assert r.canonical_args("cmd", {"command": "ls"}) == {"command": "ls"}
    assert r.canonical_args("listwait", {}) == {"path": "."}
    assert r.canonical_args("no_such_tool", {"x": 1}) == {"x": 1}
    assert r.canonical_args("paths", "not a dict") == {}


def test_canonical_args_normalizes_trailing_slash_and_empty_path():
    r = _registry()
    assert r.canonical_args("paths", {"path": "f.txt/"})["path"] == "f.txt"
    assert r.canonical_args("paths", {"path": "  "})["path"] == "."


def test_canonical_args_omits_params_without_defaults():
    r = _registry()
    assert r.canonical_args("paths", {}) == {"offset": 0, "limit": 400}


def test_canonical_args_coerces_numeric_strings_like_execute():
    # F3: a stuck model alternating "5" and 5 for the same call must not dodge
    # stall detection -- canonical_args has to see the same coercion execute() does.
    r = ToolRegistry()
    spec = ToolSpec(name="takesint", description="d",
                    params={"n": ParamSpec(type="integer")}, required=("n",),
                    fn=lambda sandbox, n: f"n={n}", caps=Caps(fs="none"))
    r.register(spec)
    assert r.canonical_args("takesint", {"n": "5"}) == {"n": 5}
    assert r.canonical_args("takesint", {"n": 5}) == {"n": 5}


# --- spec §1.3/§1.4: nested parameter schemas, recursive validation, and
# --- recursive input-size accounting. No shipped tool uses these until Task 2.

_EDITS_SCHEMA = {
    "type": "array", "minItems": 1, "maxItems": 100,
    "items": {"type": "object",
              "properties": {"old": {"type": "string"}, "new": {"type": "string"}},
              "required": ["old", "new"], "additionalProperties": False},
}


def _nested_spec(**caps_kwargs):
    """A ToolSpec shaped exactly like Task 2's apply_edits, so these tests pin
    the registry behaviour the real tool will depend on."""
    def _fn(sandbox, path, edits):
        return f"{path}:{len(edits)}"

    return ToolSpec(
        name="apply_edits",
        description="batch edits",
        params={
            "path": ParamSpec(type="string"),
            "edits": ParamSpec(type="array", description="Replacements in order.",
                               schema=_EDITS_SCHEMA),
        },
        required=("path", "edits"),
        fn=_fn,
        caps=Caps(fs="write", **caps_kwargs),
    )


def test_schema_param_renders_the_nested_schema_with_the_description_merged():
    registry = ToolRegistry()
    registry.register(_nested_spec())
    params = registry.schemas()[0]["function"]["parameters"]
    assert params["properties"]["path"] == {"type": "string"}     # flat rendering unchanged
    assert params["properties"]["edits"] == {
        "type": "array", "minItems": 1, "maxItems": 100,
        "items": {"type": "object",
                  "properties": {"old": {"type": "string"}, "new": {"type": "string"}},
                  "required": ["old", "new"], "additionalProperties": False},
        "description": "Replacements in order.",
    }
    # the ParamSpec's own schema dict is never mutated by the merge
    assert "description" not in _EDITS_SCHEMA


@pytest.mark.parametrize("edits,message", [
    ("not-a-list", "edits must be array, got str"),
    ([], "edits must have at least 1 item(s)"),
    ([{"old": "a", "new": "b"}] * 101, "edits must have at most 100 item(s)"),
    (["nope"], "edits[0] must be an object"),
    ([{"old": "a", "new": "b"}, {"new": "b"}], "edits[1] is missing required property 'old'"),
    ([{"old": "a", "new": "b"}, {"old": "a", "new": "b", "note": "x"}],
     "edits[1] has unexpected property 'note'"),
    ([{"old": "a", "new": "b"}, {"old": "a", "new": "b"}, {"old": "a", "new": 3}],
     "edits[2].new must be string, got int"),
])
def test_nested_validation_messages_are_path_qualified_bad_args(edits, message):
    registry = ToolRegistry()
    registry.register(_nested_spec())
    result = registry.execute("apply_edits", {"path": "a.py", "edits": edits},
                              sandbox=None, deadline=None)
    assert result.failure == "bad_args"
    assert result.text == f"ERROR: bad arguments for apply_edits: {message}"


def test_nested_validation_coerces_a_numeric_string_at_a_nested_leaf():
    # Same rule as the top level (_coerce_numeric_string): local models send
    # "5" where the schema says integer, at every depth.
    spec = ToolSpec(
        name="t", description="", params={
            "rows": ParamSpec(type="array", schema={
                "type": "array",
                "items": {"type": "object", "properties": {"n": {"type": "integer"}},
                          "required": ["n"], "additionalProperties": False}}),
        },
        required=("rows",), fn=lambda sandbox, rows: repr(rows),
        caps=Caps(fs="none"))
    registry = ToolRegistry()
    registry.register(spec)
    result = registry.execute("t", {"rows": [{"n": "5"}]}, sandbox=None, deadline=None)
    assert result.kind == "ok"
    assert result.text == "[{'n': 5}]"


def test_nested_validation_passes_a_valid_batch_through_unchanged():
    registry = ToolRegistry()
    registry.register(_nested_spec())
    result = registry.execute("apply_edits",
                              {"path": "a.py", "edits": [{"old": "a", "new": "b"}]},
                              sandbox=None, deadline=None)
    assert result.kind == "ok" and result.text == "a.py:1"


def test_max_input_bytes_counts_nested_strings_and_the_path_but_not_keys():
    registry = ToolRegistry()
    registry.register(_nested_spec(max_input_bytes=20))
    # path "a.py" (4) + old "x"*10 (10) + new "y"*10 (10) = 24 > 20.
    # The keys "old"/"new" (6 more bytes) are deliberately NOT counted.
    result = registry.execute(
        "apply_edits", {"path": "a.py", "edits": [{"old": "x" * 10, "new": "y" * 10}]},
        sandbox=None, deadline=None)
    assert result.failure == "bad_args"
    assert result.text == ("ERROR: bad arguments for apply_edits: input is 24 bytes, "
                           "over the 20-byte limit.")


def test_max_input_bytes_under_the_cap_runs_the_tool():
    registry = ToolRegistry()
    registry.register(_nested_spec(max_input_bytes=20))
    result = registry.execute("apply_edits",
                              {"path": "a.py", "edits": [{"old": "x", "new": "y"}]},
                              sandbox=None, deadline=None)
    assert result.kind == "ok" and result.text == "a.py:1"


_OPTS_SCHEMA = {
    "type": "object",
    "properties": {"verbose": {"type": "boolean"}},
    "required": ["verbose"],
    "additionalProperties": False,
}


def test_top_level_object_schema_validates_and_path_qualifies_a_missing_required_key():
    # Every other nested-validation test exercises an object schema nested
    # inside an array (apply_edits' `edits` items); this one pins the object
    # branch of _validate_against_schema when it IS the param's own top-level
    # schema, not a leaf several levels down.
    spec = ToolSpec(
        name="t", description="", params={
            "opts": ParamSpec(type="object", schema=_OPTS_SCHEMA),
        },
        required=("opts",), fn=lambda sandbox, opts: repr(opts),
        caps=Caps(fs="none"))
    registry = ToolRegistry()
    registry.register(spec)

    ok = registry.execute("t", {"opts": {"verbose": True}}, sandbox=None, deadline=None)
    assert ok.kind == "ok" and ok.text == "{'verbose': True}"

    bad = registry.execute("t", {"opts": {}}, sandbox=None, deadline=None)
    assert bad.failure == "bad_args"
    assert bad.text == "ERROR: bad arguments for t: opts is missing required property 'verbose'"


class TestCoerceDuration:
    """Tests for _coerce_duration helper function."""

    @pytest.mark.parametrize("value,expected", [
        ("60s", 60),
        ("60S", 60),           # case-insensitive
        (" 60 sec ", 60),      # whitespace and alternate unit
        ("2m", 120),
        ("2 minutes", 120),    # spaces in unit name
        ("60", 60),            # plain numeric string
        (60, 60),              # plain int
        ("1seconds", 1),       # no space between number and unit
    ])
    def test_coerce_duration_returns_int(self, value, expected):
        from dirtywork.toolspec import _coerce_duration
        assert _coerce_duration(value) == expected

    @pytest.mark.parametrize("value", [
        "60ms",          # milliseconds not supported
        "1.5s",          # float string
        "-5s",           # negative: the regex allows digits only
        "",              # empty string
        "abc",           # non-numeric
        "s",             # just unit, no number
        True,            # bool (even though it's an int subclass)
        1.5,             # float
        None,            # NoneType
        "9" * 5000 + "s",   # digit run beyond the regex bound; int() on it would raise on 3.11+
        "٦٠s",           # Arabic-Indic digits (re.ASCII prevents these)
    ])
    def test_coerce_duration_returns_none(self, value):
        from dirtywork.toolspec import _coerce_duration
        assert _coerce_duration(value) is None


class _MockSandbox:
    """Minimal mock for sandbox to test tool validation without actually running commands."""
    def bash(self, command, timeout=120):
        return f"command executed with timeout={timeout}"
    def read_file(self, path, offset=0, limit=400):
        return "mock file content"
    def write_file(self, path, content):
        return "file written"
    def append_file(self, path, text):
        return "appended"
    def edit_file(self, path, old_string, new_string):
        return "edited"
    def apply_edits(self, path, edits):
        return "edits applied"
    def insert_before(self, path, anchor, text):
        return "inserted before"
    def insert_after(self, path, anchor, text):
        return "inserted after"
    def list_dir(self, path="."):
        return "dir listing"
    def grep(self, pattern, path=".", glob=None, timeout=30):
        return "grep result"


def test_execute_coerces_duration_string_for_timeout_param():
    """Duration strings should be converted to seconds for timeout params."""
    from dirtywork.builtin_tools import BASH_SPEC
    r = ToolRegistry()
    r.register(BASH_SPEC)

    # Test with "60s" - should be converted to 60
    mock_sandbox = _MockSandbox()
    result = r.execute("bash", {"command": "echo test", "timeout": "60s"}, 
                       sandbox=mock_sandbox, deadline=None)
    assert result.kind == "ok"
    # Verify the timeout was coerced to int
    assert "timeout=60" in result.text


def test_execute_timeout_clamps_duration_strings():
    """Duration strings should respect timeout clamping."""
    from dirtywork.builtin_tools import BASH_SPEC
    r = ToolRegistry()
    r.register(BASH_SPEC)

    # Test with "20m" - should be clamped to max (600)
    mock_sandbox = _MockSandbox()
    result = r.execute("bash", {"command": "echo test", "timeout": "20m"}, 
                       sandbox=mock_sandbox, deadline=None)
    assert result.kind == "ok"
    # The timeout should be clamped to 600
    assert "timeout=600" in result.text


def test_execute_duration_string_invalid_format_returns_bad_args():
    """Invalid duration format should return bad_args with descriptive message."""
    from dirtywork.builtin_tools import BASH_SPEC
    r = ToolRegistry()
    r.register(BASH_SPEC)

    result = r.execute("bash", {"command": "echo test", "timeout": "60x"}, 
                       sandbox=None, deadline=None)
    assert result.kind == "error"
    assert result.failure == "bad_args"
    assert ('parameter \'timeout\' must be an integer number of seconds (60) or a duration '
            'string ("60s", "2m"); default 120, max 600 — got \'60x\'') in result.text


def test_read_file_limit_still_requires_integer():
    """Non-duration params (like read_file's limit) should still require integers."""
    from dirtywork.builtin_tools import READ_FILE_SPEC
    r = ToolRegistry()
    r.register(READ_FILE_SPEC)

    mock_sandbox = _MockSandbox()
    result = r.execute("read_file", {"path": "test.txt", "limit": "60s"}, 
                       sandbox=mock_sandbox, deadline=None)
    assert result.kind == "error"
    assert result.failure == "bad_args"
    # The error message should mention integer, not duration
    assert "parameter 'limit' must be integer" in result.text


def test_bash_schema_has_description_but_not_unit():
    """The bash tool schema should have the updated description but no unit field."""
    import json
    from dirtywork.builtin_tools import BASH_SPEC
    r = ToolRegistry()
    r.register(BASH_SPEC)

    schemas = r.schemas()
    bash_schema = next(s for s in schemas if s["function"]["name"] == "bash")
    timeout_prop = bash_schema["function"]["parameters"]["properties"]["timeout"]

    # Should have the updated description
    assert timeout_prop["description"] == 'an integer number of seconds (60) or a duration string ("60s", "2m"); default 120, max 600'
    # PR #71 review P1: the wire type must admit the strings the runtime accepts.
    assert timeout_prop["type"] == ["integer", "string"]
    # Should NOT have a unit field (it's internal-only)
    assert "unit" not in json.dumps(bash_schema)


def test_custom_tool_with_unit_seconds_coerces_duration():
    """Custom tool with unit="seconds" should coerce duration strings."""
    from dirtywork.toolspec import Caps, ParamSpec, ToolRegistry, ToolSpec

    def _custom_fn(sandbox, delay):
        return f"delay={delay}"

    custom_spec = ToolSpec(
        name="custom_tool",
        description="A test tool with duration param.",
        params={
            "delay": ParamSpec(type="integer", description="a number of seconds", default=5, unit="seconds"),
        },
        required=(),
        fn=_custom_fn,
        caps=Caps(fs="none"),
    )

    r = ToolRegistry()
    r.register(custom_spec)

    # Test with "2m" - should be converted to 120
    mock_sandbox = _MockSandbox()
    result = r.execute("custom_tool", {"delay": "2m"}, sandbox=mock_sandbox, deadline=None)
    assert result.kind == "ok"
    assert "delay=120" in result.text

    # Test with invalid value
    result = r.execute("custom_tool", {"delay": "abc"}, sandbox=mock_sandbox, deadline=None)
    assert result.kind == "error"
    assert result.failure == "bad_args"
    assert "must be a number of seconds — got 'abc'" in result.text


def test_canonical_args_normalizes_duration_strings_for_unit_params():
    # PR #71 review P2: "2m" and 120 execute identically, so they must
    # canonicalize identically or a stuck model could dodge the stall detector
    # by alternating spellings. Uses its own tool so the rule is proven for any
    # unit="seconds" param, not just bash's (whose timeout canonical_args drops).
    from dirtywork.toolspec import Caps, ParamSpec, ToolRegistry, ToolSpec
    spec = ToolSpec(
        name="waiter", description="wait", required=(),
        params={"delay": ParamSpec(type="integer", description="a number of seconds",
                                   default=5, unit="seconds")},
        fn=lambda sandbox, delay: f"delay={delay}", caps=Caps(fs="none"))
    r = ToolRegistry()
    r.register(spec)
    assert r.canonical_args("waiter", {"delay": "2m"}) == r.canonical_args("waiter", {"delay": 120})
    assert r.canonical_args("waiter", {"delay": "2m"}) == {"delay": 120}
    assert r.canonical_args("waiter", {"delay": "junk"}) == {"delay": "junk"}  # unparseable stays as sent


from dirtywork.builtin_tools import default_registry
from .markers import TOOL_CALLS, TOOL_CALL_OPEN


def test_recover_name_registered_name_is_unchanged():
    r = default_registry()
    assert r.recover_name("bash") == ("bash", None, 0)


def test_recover_name_strips_prose_before_the_last_marker():
    r = default_registry()
    name = "exit code: 0\n1,User1" + TOOL_CALLS + "bash"
    assert r.recover_name(name) == ("bash", TOOL_CALLS, len("exit code: 0\n1,User1"))


def test_recover_name_uses_the_last_marker():
    r = default_registry()
    name = "a" + TOOL_CALLS + "b" + TOOL_CALLS + "read_file"
    assert r.recover_name(name) == ("read_file", TOOL_CALLS, len("a" + TOOL_CALLS + "b"))


def test_recover_name_leaves_an_unknown_suffix_alone():
    r = default_registry()
    name = "x" + TOOL_CALLS + "nope"
    assert r.recover_name(name) == (name, None, 0)


def test_recover_name_leaves_a_name_without_a_marker_alone():
    r = default_registry()
    assert r.recover_name("garbage") == ("garbage", None, 0)


def test_recover_name_handles_the_xml_marker():
    r = default_registry()
    name = "prose " + TOOL_CALL_OPEN + "bash"
    assert r.recover_name(name) == ("bash", TOOL_CALL_OPEN, len("prose "))


def test_recover_name_strips_whitespace_around_the_suffix():
    r = default_registry()
    name = "p" + TOOL_CALLS + "  bash \n"
    assert r.recover_name(name) == ("bash", TOOL_CALLS, 1)


def test_recover_name_is_case_sensitive():
    r = default_registry()
    name = TOOL_CALLS + "Bash"
    assert r.recover_name(name) == (name, None, 0)


# Spec #67 §0.3: sanitised markers (non-alphanumeric chars turned to "_")
_SANITISED = re.sub(r"[^A-Za-z0-9_-]", "_", TOOL_CALLS)
_TOOL_CALL_OPEN_SANITISED = re.sub(r"[^A-Za-z0-9_-]", "_", TOOL_CALL_OPEN)


def test_tool_call_markers_include_the_sanitised_forms():
    """Assert len(TOOL_CALL_MARKERS) == 10 and sanitised forms are present."""
    from dirtywork.toolspec import TOOL_CALL_MARKERS as _markers
    assert len(_markers) == 10

    # Sanitised forms should be present
    assert _SANITISED in _markers
    assert re.sub(r"[^A-Za-z0-9_-]", "_", TOOL_CALL_OPEN) in _markers

    # Verify the tuple equals raw five followed by their sanitised twins
    from dirtywork.toolspec import _RAW_MARKERS as _raw_markers
    expected = _raw_markers + tuple(re.sub(r"[^A-Za-z0-9_-]", "_", m) for m in _raw_markers)
    assert _markers == expected


def test_recover_name_handles_the_sanitised_marker():
    """Test recover_name with a sanitised marker in the tool name."""
    r = default_registry()
    # Build sanitised marker: "exit_code_0_17285_fixtures_rows_csv" + SANITISED + "bash"
    name = "exit_code_0_17285_fixtures_rows_csv" + _SANITISED + "bash"
    assert r.recover_name(name) == ("bash", _SANITISED, len("exit_code_0_17285_fixtures_rows_csv"))


def test_recover_name_sanitised_and_raw_markers_in_one_name_uses_the_last():
    """Test that recover_name uses the LAST marker when both sanitised and raw appear."""
    r = default_registry()
    # name = "a" + SANITISED + "bash" + TOOL_CALLS + '{"command": "x"}' + "_Total_rows_400" + SANITISED + "bash"
    name = "a" + _SANITISED + "bash" + TOOL_CALLS + '{"command": "x"}' + "_Total_rows_400" + _SANITISED + "bash"
    # recovered name "bash", marker SANITISED, cut == len(name) - len(SANITISED) - len("bash")
    expected_cut = len(name) - len(_SANITISED) - len("bash")
    assert r.recover_name(name) == ("bash", _SANITISED, expected_cut)


def test_recover_name_sanitised_marker_with_unknown_suffix_stays_unknown():
    """Test that a sanitised marker with unknown suffix is not recovered."""
    r = default_registry()
    name = "x" + _SANITISED + "nope"
    assert r.recover_name(name) == (name, None, 0)


def test_unknown_tool_error_caps_the_echoed_name():
    r = default_registry()
    name = "q" * 150 + TOOL_CALLS + "zz" * 70
    result = r.execute(name, {}, sandbox=None, deadline=None)
    assert "…" in result.text
    assert "(name truncated)" in result.text
    assert name[-40:] in result.text
    assert len(result.text) < 400

    # 20-char unknown name should echo whole
    result = r.execute("unknown_short", {}, sandbox=None, deadline=None)
    assert "(name truncated)" not in result.text
