# Issue #150: `_probe` must not cache a DockerError — implementation plan

**Goal:** One transient Docker failure on the first `list_dir` or `grep` must not downgrade the rest of the run to the `ls`/`wc` or `grep -rn` fallbacks.

**Spec:** [Issue #150](https://github.com/JimboSchneider/dirtywork/issues/150), filed from the PR #145 review. `DockerSandbox._probe` caches `False` on `docker_cli.DockerError` (an expired timeout included) and nothing ever resets `_has_gnu_find` / `_has_rg`.

**Architecture:** Cache only a definitive answer (an observed return code). On `DockerError`, return `False` for this call only and leave the attribute unset, so the next call probes again. No retry loop and no timeout accounting: spec §4.2 already keeps harness-side execs (grep, and by the same reasoning a capability probe) out of the worker's `timeouts` count. Two regressions using `FakeDocker`'s callable responses: the first probe exec raises a timed-out `DockerError`, the call falls back, nothing is cached, and the second call probes again and takes the GNU-find / rg branch.

**Tech stack:** Python >=3.9, pytest; no dependencies added.

## Global constraints

- Repository `CLAUDE.md`: latest released dirtywork plus a local worker implements code. PyPI checked 2026-09-08: `0.13.1`. Worker: `qwen/qwen3-coder-next` via LM Studio. Image: `dirtywork-worker-pytest:0.13`, network disabled.
- Worker changes only `dirtywork/sandbox/docker.py` and `tests/test_docker_sandbox.py`; the orchestrator writes this plan and the ledger after the run.
- Brief experiment from the issue #146 ledger: quote line numbers next to the anchors, so the worker does not spend turns re-locating text the brief already contains.
- No merge or release without the owner's per-action authorization.

## Task W1: cache only definitive probe answers

- [x] Dry-run the exact edit and tests on a scratch clone: red on baseline, green with the fix.
- [x] Launch the brief below verbatim through released dirtywork with the metrics sampler active.
- [x] Review the exported diff against the dry-run patch; run the full suite on the host against the worktree.
- [x] Write the ledger row; open a PR that closes issue #150.

### Worker brief

```text
Issue #150: DockerSandbox._probe caches False forever after a transient DockerError, so one Docker stall on the first list_dir or grep downgrades the whole run to the ls/wc or grep -rn fallbacks. Fix: cache only a definitive answer (an observed return code); on DockerError return False for THIS call only and leave the cache unset so the next call probes again.
Touch ONLY dirtywork/sandbox/docker.py and tests/test_docker_sandbox.py. NEVER write_file either file. Use ONE edit_file with the exact old/new below (keep the leading spaces) and ONE append_file for the tests. Do not read whole files: the old string is dirtywork/sandbox/docker.py lines 769-778 (the body of `def _probe`, which starts at line 768); tests/test_docker_sandbox.py has 3101 lines and you only need to append. No docs, no commits, nothing else.

P1 edit_file on dirtywork/sandbox/docker.py, old:
        """Probe once per sandbox instance for an optional in-image tool; cached on self."""
        cached = getattr(self, attr, None)
        if cached is None:
            try:
                captured = self._run(docker_args.exec_argv(self.container, argv), timeout=LIST_EXEC_TIMEOUT)
                cached = captured.returncode == 0
            except docker_cli.DockerError:
                cached = False
            setattr(self, attr, cached)
        return cached
P1 new:
        """Probe once per sandbox instance for an optional in-image tool; cached on
        self. Only a definitive answer (an observed return code) is cached: a
        DockerError -- an expired timeout included -- falls back for THIS call
        only and leaves the cache unset, so the next call probes again instead
        of downgrading the whole run to the fallback branches (issue #150)."""
        cached = getattr(self, attr, None)
        if cached is None:
            try:
                captured = self._run(docker_args.exec_argv(self.container, argv), timeout=LIST_EXEC_TIMEOUT)
            except docker_cli.DockerError:
                return False
            cached = captured.returncode == 0
            setattr(self, attr, cached)
        return cached

NEW TESTS: ONE append_file to tests/test_docker_sandbox.py with exactly this text (the file currently ends with `    assert guard[-2:] == ["_", "./-"]`; start your text with two empty lines). DockerError, _ok and the started fixture already exist in that module.


def _probe_timeout(argv):
    """FakeDocker callable response: the capability probe's docker exec timed out."""
    raise DockerError("docker exec ... timed out after 10s", timed_out=True)


def test_probe_docker_error_is_not_cached_for_list_dir(started):
    sb, fake, _ = started
    fake.script(["exec"], [_probe_timeout, _ok(b"a.txt\n"), _ok(b"5 a.txt\n5 total\n"),
                           _ok(b"find (GNU findutils) 4.9.0\n"), _ok(b"f\t18\tREADME.md\n")])
    assert sb.list_dir(".") == "a.txt  (5 bytes)"
    assert getattr(sb, "_has_gnu_find", None) is None
    assert sb.list_dir(".") == "README.md  (18 bytes)"
    assert sb._has_gnu_find is True
    assert fake.calls[-1][0][4:6] == ["/usr/bin/find", "."]


def test_probe_docker_error_is_not_cached_for_grep(started):
    sb, fake, _ = started
    fake.script(["exec"], [_probe_timeout, _ok(b"src/app.py:2:hello\n"),
                           _ok(b"ripgrep 13.0.0\n"), _ok(b"./src/app.py:2:hello\n")])
    assert sb.grep("hello") == "src/app.py:2:hello"
    assert getattr(sb, "_has_rg", None) is None
    assert fake.calls[-1][0][4] == "/usr/bin/grep"
    assert sb.grep("hello") == "src/app.py:2:hello"
    assert sb._has_rg is True
    assert fake.calls[-1][0][4] == "/usr/bin/rg"

VERIFY: run python3 -m pytest -q -p no:cacheprovider tests/test_docker_sandbox.py and expect 178 passed. Then run python3 -m pytest -q -p no:cacheprovider with timeout=300. Finish when both pass.
```

### Invocation

Released `dirtywork==0.13.1` via `pipx run --spec`, from repo HEAD `e916b55` (main), `--provider openai --base-url http://localhost:1234/v1 --model qwen/qwen3-coder-next --sandbox docker --image dirtywork-worker-pytest:0.13 --verify "python3 -m pytest -q -p no:cacheprovider" --verify-rounds 2 --max-turns 60 --timeout 1800`. The brief is passed as one argv element. `tools/soak_sampler.sh` runs for the whole wall time and is stopped on every exit path.

### Review gates

The worker's diff should match the dry-run patch (one production edit, one appended block of two tests plus a helper). Any other touched file or any rewritten passing test is a finding. Host: `tests/test_docker_sandbox.py` 178 passed, full suite green, `git diff --check` clean. Independently: the two new tests fail on the baseline module.

## Delivery

Run ledger: [issue #150 ledger](../bench/2026-09-08-issue-150-probe-docker-error-ledger.md). PR link added below once opened.
