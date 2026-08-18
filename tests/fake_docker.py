"""Shared FakeCaptured double for tests that stub dirtywork.sandbox.docker_cli.run."""
from __future__ import annotations


class FakeCaptured:
    """Stand-in for dirtywork.procs.Captured (returncode, output, truncated, timed_out);
    callers under test read only returncode/output, the other two default to False."""

    def __init__(self, returncode, output=b"", truncated=False, timed_out=False):
        self.returncode = returncode
        self.output = output
        self.truncated = truncated
        self.timed_out = timed_out
