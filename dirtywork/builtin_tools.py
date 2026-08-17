"""The seven tools dirtywork ships, declared as ToolSpecs.

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


def _read_file(sandbox, path, offset=0, limit=400):
    return sandbox.read_file(path, offset, limit)


def _write_file(sandbox, path, content):
    return sandbox.write_file(path, content)


def _edit_file(sandbox, path, old_string, new_string):
    return sandbox.edit_file(path, old_string, new_string)


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
        "timeout": ParamSpec(type="integer", description="Seconds, default 120, max 600", default=120),
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
    caps=Caps(fs="none", max_output_chars=TOOL_OUTPUT_CAP, transcript="preview"),
    terminal=True,
)

# Registration order is the order the tools are advertised to the model and the
# order the unknown-tool error lists them in. Do not reorder.
BUILTIN_SPECS = (READ_FILE_SPEC, WRITE_FILE_SPEC, EDIT_FILE_SPEC, LIST_DIR_SPEC,
                 GREP_SPEC, BASH_SPEC, FINISH_SPEC)


def default_registry(transcript=None) -> ToolRegistry:
    registry = ToolRegistry(transcript=transcript)
    for spec in BUILTIN_SPECS:
        registry.register(spec)
    return registry
