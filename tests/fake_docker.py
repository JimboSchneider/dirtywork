"""Shared FakeCaptured double for tests that stub dirtywork.sandbox.docker_cli.run."""
from __future__ import annotations


class FakeCaptured:
    """Stand-in for dirtywork.procs.Captured: only returncode/output are read."""

    def __init__(self, returncode, output=b""):
        self.returncode = returncode
        self.output = output
