from __future__ import annotations

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
