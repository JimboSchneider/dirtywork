# tests/docker_fakes.py - shared test fakes for DockerSandbox and export flow tests
from __future__ import annotations

import io
import subprocess
from pathlib import Path

from dirtywork.procs import Captured


class FakePopen:
    """Stand-in for subprocess.Popen, used both for the `docker start -ai`
    tether (only .argv and .stdin matter there) and for streamed commands
    like `git diff`/`git archive` whose stdout the caller reads (Task 11)
    — .stdout is a real io.BytesIO pre-loaded with `stdout_data` so callers
    can .read() it exactly like a real pipe. .stdin is a real io.BytesIO so
    callers can .write()/.close(); .wait()/.poll()/.kill() are scripted
    to look like a clean-running process unless a test overrides
    .returncode directly."""

    def __init__(self, argv, *, stdin=None, stdout=None, stderr=None, stdout_data: bytes = b"", env=None):
        self.argv = list(argv)
        self.stdin = io.BytesIO() if stdin == subprocess.PIPE else None
        self.stdout = io.BytesIO(stdout_data) if stdout == subprocess.PIPE else None
        self.returncode = None
        self.killed = False
        self.env = env

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def terminate(self):
        self.kill()


_TOP_HEADER = b"UID  PID  PPID  C  STIME  TTY  TIME  CMD\n"

_SAMPLE_ARGV = ["exec", "-w", "/work", "dw-abc123", "/bin/sh", "-c",
                "du -sk /work; find /work | wc -l"]


class FakeDocker:
    """Scriptable stand-in for docker_cli.run and subprocess.Popen, shared by
    every DockerSandbox/export unit test in this plan.

    `script(prefix, response)` maps an argv-prefix tuple to either a single
    Captured (always returned for matching calls) or a list of Captured
    (popped in call order; the last item repeats once the list is down to
    one element, so a test can script "fails twice then succeeds" with
    [fail, fail, ok] and further matching calls keep returning ok). Any argv
    with no matching prefix gets `.default` (returncode 0, empty output) so
    a test only scripts the calls it cares about. When more than one
    registered prefix matches a call, the LONGEST (most specific) prefix
    wins — this lets a test register a broad default like `["exec"]` for
    "any exec call returns ok" and separately override one specific exec
    call (e.g. Task 9's worktree-size sample, which is also a `docker exec`
    under the same `["exec", ...]` prefix) without the broad default
    shadowing it. Every call is recorded in `.calls` (list of
    (argv, timeout, stdin)) for order/content assertions; every FakePopen
    created is recorded in `.popens`.

    `script_popen_stdout(prefix, data)` maps an argv-prefix tuple (matched
    the same longest-prefix-wins way, against the argv passed to `popen()`
    — which for every real call in this codebase is `["docker", ...]`,
    since `popen` is always called with the full `docker` argv already
    prefixed, unlike `run()` which prefixes it internally) to the bytes a
    FakePopen's `.stdout` should yield. Used by Task 11's export-flow tests
    to feed a real in-memory tar into `git archive`'s simulated stdout.
    """

    def __init__(self):
        self.responses = {}
        self.popen_stdout = {}
        self.calls = []
        self.popens = []
        self.default = Captured(returncode=0, output=b"", truncated=False, timed_out=False)

    def script(self, prefix, response) -> None:
        self.responses[tuple(prefix)] = response

    def script_popen_stdout(self, prefix, data: bytes) -> None:
        self.popen_stdout[tuple(prefix)] = data

    def run(self, argv, *, timeout, stdin=None):
        self.calls.append((list(argv), timeout, stdin))
        best_prefix = None
        best_response = None
        for prefix, response in self.responses.items():
            if tuple(argv[: len(prefix)]) == prefix:
                if best_prefix is None or len(prefix) > len(best_prefix):
                    best_prefix, best_response = prefix, response
        if best_prefix is None:
            return self.default
        if isinstance(best_response, list):
            if len(best_response) > 1:
                return best_response.pop(0)
            return best_response[0]
        return best_response

    def popen(self, argv, *, stdin=None, stdout=None, stderr=None, env=None):
        best_prefix = None
        best_data = b""
        for prefix, data in self.popen_stdout.items():
            if tuple(argv[: len(prefix)]) == prefix:
                if best_prefix is None or len(prefix) > len(best_prefix):
                    best_prefix, best_data = prefix, data
        p = FakePopen(argv, stdin=stdin, stdout=stdout, stderr=stderr, stdout_data=best_data, env=env)
        self.popens.append(p)
        return p


def _ok(output: bytes = b"") -> Captured:
    return Captured(returncode=0, output=output, truncated=False, timed_out=False)


def _fail(output: bytes = b"error") -> Captured:
    return Captured(returncode=1, output=output, truncated=False, timed_out=False)
