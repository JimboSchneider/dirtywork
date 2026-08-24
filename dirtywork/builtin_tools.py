"""The eleven tools dirtywork ships, declared as ToolSpecs.

Each `fn` receives the Sandbox as its first argument and forwards to the
matching Sandbox method, so a tool never knows whether it is running on the
host or inside a container. Adding a tool means adding one ToolSpec here and
one method to the Sandbox protocol -- nothing in runner.py or __main__.py
changes.
"""
from __future__ import annotations

from .toolspec import Caps, ParamSpec, ToolRegistry, ToolSpec
from .tools import MAX_BASH_CHARS, MAX_RESULT_CHARS

# Caps.max_output_chars is an OUTER safety net. Every tool already truncates
# its own result (tools._cap) at MAX_RESULT_CHARS / MAX_BASH_CHARS and appends
# an explanatory note ("... — re-run with offset/limit to see more"). If the
# registry's cap were equal to the tool's own cap it would chop that note off
# and change shipped output, so it sits one note-length above it.
TOOL_OUTPUT_CAP = MAX_RESULT_CHARS + 512
BASH_OUTPUT_CAP = MAX_BASH_CHARS + 512
# Spec §1.1/§1.4: apply_edits' own limits. MAX_APPLY_EDITS is enforced entirely
# by the wire schema's `maxItems` -- the registry's recursive validator honours
# it, so there is no second runtime check to keep in step. The input cap bounds
# `path` plus every `old`/`new` the model sent (the FILE is separately capped at
# tools.MAX_READ_BYTES, 5 MB); it is the only Caps.max_input_bytes any built-in
# sets, so no other tool's behaviour changes.
MAX_APPLY_EDITS = 100
MAX_APPLY_EDITS_INPUT_BYTES = 2 * 1024 * 1024


def _read_file(sandbox, path, offset=0, limit=400):
    return sandbox.read_file(path, offset, limit)


def _write_file(sandbox, path, content):
    return sandbox.write_file(path, content)


def _append_file(sandbox, path, text):
    return sandbox.append_file(path, text)


def _edit_file(sandbox, path, old_string, new_string):
    return sandbox.edit_file(path, old_string, new_string)


def _apply_edits(sandbox, path, edits):
    return sandbox.apply_edits(path, edits)


def _insert_before(sandbox, path, anchor, text):
    return sandbox.insert_before(path, anchor, text)


def _insert_after(sandbox, path, anchor, text):
    return sandbox.insert_after(path, anchor, text)


def _list_dir(sandbox, path="."):
    return sandbox.list_dir(path)


def _grep(sandbox, pattern, path=".", glob=None, timeout=30):
    return sandbox.grep(pattern, path, glob, timeout)


def _bash(sandbox, command, timeout=120):
    return sandbox.bash(command, timeout)


def _finish(sandbox, summary=""):
    """Never executed: the runner sees ToolSpec.terminal and ends the run
    itself, reading `summary` straight off the tool call (so a `finish` with no
    summary completes the run with an empty final message instead of taking a
    validation strike). Present only so the spec is a complete ToolSpec."""
    return "run finished"


READ_FILE_SPEC = ToolSpec(
    name="read_file",
    description="Read a file, returning numbered lines. Use offset/limit to "
                "page through; files over ~5 MB or non-regular files are refused.",
    params={
        "path": ParamSpec(type="string", description="Path relative to worktree root"),
        "offset": ParamSpec(type="integer", description="0-based first line, default 0", default=0),
        "limit": ParamSpec(type="integer", description="Max lines, default 400", default=400),
    },
    required=("path",),
    fn=_read_file,
    caps=Caps(fs="read", max_output_chars=TOOL_OUTPUT_CAP, transcript="preview"),
)

WRITE_FILE_SPEC = ToolSpec(
    name="write_file",
    description="Create or overwrite a file. Parent directories are created.",
    params={
        "path": ParamSpec(type="string"),
        "content": ParamSpec(type="string"),
    },
    required=("path", "content"),
    fn=_write_file,
    caps=Caps(fs="write", max_output_chars=TOOL_OUTPUT_CAP, transcript="preview"),
)

APPEND_FILE_SPEC = ToolSpec(
    name="append_file",
    description="Append text verbatim to the END of an existing file (create "
                "the file with write_file first). Nothing is inserted between "
                "the old content and your text — include a leading newline if "
                "the file does not end with one. Use write_file + append_file "
                "to produce a file too large for one reply.",
    params={
        "path": ParamSpec(type="string"),
        "text": ParamSpec(type="string"),
    },
    required=("path", "text"),
    fn=_append_file,
    caps=Caps(fs="write", max_output_chars=TOOL_OUTPUT_CAP, transcript="preview"),
)

EDIT_FILE_SPEC = ToolSpec(
    name="edit_file",
    description="Replace old_string with new_string in a file. old_string "
                "must occur exactly once — include surrounding context.",
    params={
        "path": ParamSpec(type="string"),
        "old_string": ParamSpec(type="string"),
        "new_string": ParamSpec(type="string"),
    },
    required=("path", "old_string", "new_string"),
    fn=_edit_file,
    caps=Caps(fs="write", max_output_chars=TOOL_OUTPUT_CAP, transcript="preview"),
)

APPLY_EDITS_SPEC = ToolSpec(
    name="apply_edits",
    description="Apply several exact old→new replacements to one file in one "
                "call, in order: every `old` must occur exactly once (in the "
                "file as it stands after the edits before it); if any does "
                "not, nothing is written and the result names the first "
                "failure. Prefer this over a run of edit_file calls when a "
                "brief lists several edits to the same file.",
    params={
        "path": ParamSpec(type="string"),
        "edits": ParamSpec(
            type="array",
            description="Replacements in order; each old must occur exactly once in "
                        "the file as it stands after the previous edits.",
            schema={
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_APPLY_EDITS,
                "items": {
                    "type": "object",
                    "properties": {"old": {"type": "string"},
                                   "new": {"type": "string"}},
                    "required": ["old", "new"],
                    "additionalProperties": False,
                },
            }),
    },
    required=("path", "edits"),
    fn=_apply_edits,
    caps=Caps(fs="write", max_input_bytes=MAX_APPLY_EDITS_INPUT_BYTES,
              max_output_chars=TOOL_OUTPUT_CAP, transcript="preview"),
)

INSERT_BEFORE_SPEC = ToolSpec(
    name="insert_before",
    description="Insert text as whole new line(s) immediately BEFORE the line "
                "containing anchor. anchor must occur exactly once — include "
                "surrounding context. The anchor's own line is never modified; "
                "use this instead of edit_file when you mean to add a line, not "
                "replace one.",
    params={
        "path": ParamSpec(type="string"),
        "anchor": ParamSpec(type="string"),
        "text": ParamSpec(type="string"),
    },
    required=("path", "anchor", "text"),
    fn=_insert_before,
    caps=Caps(fs="write", max_output_chars=TOOL_OUTPUT_CAP, transcript="preview"),
)

INSERT_AFTER_SPEC = ToolSpec(
    name="insert_after",
    description="Insert text as whole new line(s) immediately AFTER the line "
                "containing anchor. anchor must occur exactly once — include "
                "surrounding context. The anchor's own line is never modified; "
                "use this instead of edit_file when you mean to add a line, not "
                "replace one.",
    params={
        "path": ParamSpec(type="string"),
        "anchor": ParamSpec(type="string"),
        "text": ParamSpec(type="string"),
    },
    required=("path", "anchor", "text"),
    fn=_insert_after,
    caps=Caps(fs="write", max_output_chars=TOOL_OUTPUT_CAP, transcript="preview"),
)

LIST_DIR_SPEC = ToolSpec(
    name="list_dir",
    description="List a directory's entries (dirs end with /).",
    params={"path": ParamSpec(type="string", description="Default '.'", default=".")},
    required=(),
    fn=_list_dir,
    caps=Caps(fs="read", max_output_chars=TOOL_OUTPUT_CAP, transcript="preview"),
)

GREP_SPEC = ToolSpec(
    name="grep",
    description="Search file contents with a regex. Optional glob filter "
                "like '*.cs' or '*.tsx'.",
    params={
        "pattern": ParamSpec(type="string"),
        "path": ParamSpec(type="string", description="Default '.'", default="."),
        "glob": ParamSpec(type="string", default=None),
    },
    required=("pattern",),
    fn=_grep,
    caps=Caps(fs="read", max_output_chars=TOOL_OUTPUT_CAP, timeout_default=30,
              transcript="preview"),
)

# this description is also the error text's accepted-form, so the wire contract and the runtime match
BASH_SPEC = ToolSpec(
    name="bash",
    description="Run a shell command in the worktree (cwd is the worktree "
                "root). Use for builds/tests/git-status, NEVER for editing "
                "files. 120s default timeout, 600s max. Backgrounded "
                "processes are terminated when the command returns. In "
                "docker mode, a stray background process or an "
                "out-of-memory container triggers an automatic reset: the "
                "working tree survives, but any git state you created "
                "inside the sandbox (index changes, stashes, local "
                "commits) does not — write_file/edit_file changes and "
                "anything already written to disk are unaffected.",
    params={
        "command": ParamSpec(type="string"),
        "timeout": ParamSpec(type="integer", description='an integer number of seconds (60) or a duration string ("60s", "2m"); default 120, max 600', default=120, unit="seconds"),
    },
    required=("command",),
    fn=_bash,
    caps=Caps(fs="write", network=True, max_output_chars=BASH_OUTPUT_CAP,
              timeout_default=120, timeout_max=600, transcript="preview"),
)

FINISH_SPEC = ToolSpec(
    name="finish",
    description=("End the run. Call this once the task is complete and verified "
                 "(tests/build run, changes committed if the task asked for commits). "
                 "summary: 2-6 sentences on what you did and anything left undone."),
    params={"summary": ParamSpec(type="string")},
    required=("summary",),
    fn=_finish,
    # Spec #60 §4: the finish result is harness-authored and bounded (the verify
    # feedback carries a VERIFY_OUTPUT_CHARS tail), so the transcript keeps it
    # byte-for-byte instead of the 2000-char preview every model/tool-authored
    # result gets.
    caps=Caps(fs="none", max_output_chars=TOOL_OUTPUT_CAP, transcript="full"),
    terminal=True,
)

# Registration order is the order the tools are advertised to the model and the
# order the unknown-tool error lists them in. Do not reorder.
BUILTIN_SPECS = (READ_FILE_SPEC, WRITE_FILE_SPEC, APPEND_FILE_SPEC, EDIT_FILE_SPEC,
                 APPLY_EDITS_SPEC, INSERT_BEFORE_SPEC, INSERT_AFTER_SPEC, LIST_DIR_SPEC,
                 GREP_SPEC, BASH_SPEC, FINISH_SPEC)


def default_registry(transcript=None) -> ToolRegistry:
    registry = ToolRegistry(transcript=transcript)
    for spec in BUILTIN_SPECS:
        registry.register(spec)
    return registry
