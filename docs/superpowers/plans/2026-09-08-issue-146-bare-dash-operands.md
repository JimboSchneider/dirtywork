# Issue #146: bare `-` path operands in the Docker sandbox — implementation plan

**Goal:** A worker-supplied path that is a bare `-` must reach every in-container binary as a file operand, never as stdin (`head`, `rg`, `grep`, `stat`) or `$OLDPWD` (`cd`).

**Spec:** [Issue #146](https://github.com/JimboSchneider/dirtywork/issues/146), filed from the PR #145 review. PR #145 put `--` before the rg/grep path and `./` before GNU find's starting point; `--` does not neutralize a bare `-` operand, and `_rel("-")` returns `-` unchanged.

**Architecture:** Fix once in `_rel` (the issue's second option): return `./` + the normalized path for everything except `.` itself, and drop `list_dir`'s own `"./" + rel` so the prefix is not doubled. The `.git` root check still runs on the unprefixed parts. `.` stays `.` because rg/grep print match paths relative to the operand, and `././x` would defeat `grep()`'s leading-`./` strip (verified in `dirtywork-worker-pytest:0.13`: `rg -- ./.` prints `././-/file.txt`). Anchoring in `_rel` also closes a fourth site the issue did not list: `APPEND_GUARD_SCRIPT`'s `stat -Lc %s -- "$1"` reads stdin for `-` and reports size 0, so `append_file("-")`'s cap check underestimates (verified: prints `0`, exit 0). Write-side scripts (`cat >`, `cp --`, `mv -fT --`, `chmod --reference=`) treat `-` as a file already.

**Blast radius (measured on a scratch clone before briefing):** six existing tests (nine cases) in `tests/test_docker_sandbox.py` assert exact argv with an unprefixed path; nothing else in the suite changes. Full suite on the scratch clone with only the production edit: 9 failed / 1,680 passed / 9 skipped / 38 deselected. With the test edits below: `test_docker_sandbox.py` 176 passed, and the nine new cases fail on the baseline module.

**Tech stack:** Python >=3.9, pytest; no dependencies added.

## Global constraints

- Repository `CLAUDE.md`: latest released dirtywork plus a local worker implements code. PyPI checked 2026-09-08: `0.13.1`. Worker: `qwen/qwen3-coder-next` via LM Studio. Image: `dirtywork-worker-pytest:0.13`, network disabled.
- Worker changes only `dirtywork/sandbox/docker.py` and `tests/test_docker_sandbox.py`; the orchestrator writes docs (this plan, the ledger, the spec line noted in the issue) after the run.
- Preserve host behavior, listing/search output, glob options and fallback behavior.
- No merge or release without the owner's per-action authorization.

## Task W1: anchor every `_rel` operand with `./`

- [x] Verify bare-`-` and `./.` behavior of head/rg/grep/cd/stat/cp in a disposable container; measure the blast radius on a scratch clone.
- [x] Launch the brief below verbatim through released dirtywork with the metrics sampler active.
- [x] Review the exported diff against the dry-run patch; run the full suite on the host against the worktree.
- [x] Update `docs/superpowers/specs/2026-08-15-review-response-design.md` (`list_dir` is `find ./<path> …` since PR #145) and write the ledger row.
- [x] Open a PR that closes issue #146; note the fourth site.

### Worker brief

```text
Issue #146: finish the Docker static-tool operand fix from PR #145. A path that is a bare `-` reaches head/rg/grep/stat as STDIN and `cd -- -` as $OLDPWD; `--` does not change that. Fix it ONCE in `_rel` so every consumer gets a './'-anchored operand ('.' stays '.'), and drop list_dir's own "./" + rel so the prefix is not doubled.
Touch ONLY dirtywork/sandbox/docker.py and tests/test_docker_sandbox.py. NEVER write_file either file. Use edit_file with the exact old/new strings below (each old string occurs exactly once; keep the leading spaces) and ONE append_file for the new tests. Read only the line ranges you need, not whole files. No docs, no commits, nothing else.

PRODUCTION FIRST, dirtywork/sandbox/docker.py, three edit_file calls:
P1 old:
Returns (normalized, None) or (None, error_string).
P1 new:
Returns (normalized and './'-anchored, None) or (None, error_string).
P2 old:
    return normalized, None
P2 new:
    # Issue #146: a bare `-` is stdin to head/rg/grep/stat and $OLDPWD to
    # `cd`, and `--` does not change that. Every consumer puts this value in
    # operand position, so anchor it with `./` here, once. `.` stays `.`:
    # rg/grep print paths relative to the operand, and `././x` would defeat
    # grep()'s leading-`./` strip.
    return ("." if normalized == "." else "./" + normalized), None
P3 old:
            # GNU find still treats -delete, ! and ( as expressions after --.
            # Prefix the normalized path so it is always a starting point.
            out, err = self._list_exec(path, ["/usr/bin/find", "./" + rel, "-mindepth", "1", "-maxdepth", "1",
P3 new:
            # GNU find still treats -delete, ! and ( as expressions after --;
            # _rel's `./` anchor keeps the path a starting point.
            out, err = self._list_exec(path, ["/usr/bin/find", rel, "-mindepth", "1", "-maxdepth", "1",

EXISTING EXPECTATIONS, tests/test_docker_sandbox.py, nine edit_file calls (T2, T5, T6 are multi-line):
T1 old:
        "/usr/bin/head", "-c", str(MAX_READ_BYTES + 1), "--", "src/app.py",
T1 new:
        "/usr/bin/head", "-c", str(MAX_READ_BYTES + 1), "--", "./src/app.py",
T2 old:
        "_", "deep/new/file.txt",
    ]
    assert re.fullmatch(r"deep/new/\.dw-tmp\.file\.txt\.[0-9a-f]{8}", argv[10])
T2 new:
        "_", "./deep/new/file.txt",
    ]
    assert re.fullmatch(r"\./deep/new/\.dw-tmp\.file\.txt\.[0-9a-f]{8}", argv[10])
T3 old:
        "/usr/bin/find", "./.", "-mindepth", "1", "-maxdepth", "1",
T3 new:
        "/usr/bin/find", ".", "-mindepth", "1", "-maxdepth", "1",
T4 old:
    assert argv[9] == "deep/new/file.txt"
T4 new:
    assert argv[9] == "./deep/new/file.txt"
T5 old:
    # bytes never reach the script text.
    assert re.fullmatch(r"deep/new/\.dw-tmp\.file\.txt\.[0-9a-f]{8}", argv[10])
T5 new:
    # bytes never reach the script text.
    assert re.fullmatch(r"\./deep/new/\.dw-tmp\.file\.txt\.[0-9a-f]{8}", argv[10])
T6 old:
    assert argv[8] == "_" and argv[9] == "deep/notes.md"
    assert re.fullmatch(r"deep/\.dw-tmp\.notes\.md\.[0-9a-f]{8}", argv[10])
T6 new:
    assert argv[8] == "_" and argv[9] == "./deep/notes.md"
    assert re.fullmatch(r"\./deep/\.dw-tmp\.notes\.md\.[0-9a-f]{8}", argv[10])
T7 old:
    ("./-delete", "./-delete"),
T7 new:
    ("./-delete", "./-delete"),
    ("-", "./-"),
T8 old:
@pytest.mark.parametrize("path", ["--pre=./helper", "-v"])
T8 new:
@pytest.mark.parametrize("path", ["--pre=./helper", "-v", "-"])
T9 old:
    assert argv[-2:] == ["--", path]
T9 new:
    assert argv[-2:] == ["--", "./" + path]

NEW TESTS: ONE append_file to tests/test_docker_sandbox.py with exactly this text (the file currently ends with `assert out == "file.txt:1:needle"`; start your text with two empty lines):


@pytest.mark.parametrize(("path", "expected"), [
    ("-", "./-"),
    ("./-", "./-"),
    ("-/", "./-"),
    ("src/-", "./src/-"),
    (".", "."),
    ("./", "."),
])
def test_rel_anchors_every_operand_except_the_cwd(path, expected):
    assert docker_mod._rel(path) == (expected, None)


def test_read_file_bare_dash_is_a_file_operand_not_stdin(started):
    sb, fake, _ = started
    fake.script(["exec"], _ok(b"dash\n"))
    assert "dash" in sb.read_file("-")
    assert fake.calls[-1][0][-2:] == ["--", "./-"]


def test_list_dir_fallback_bare_dash_is_a_directory_not_oldpwd(started):
    sb, fake, _ = started
    sb._has_gnu_find = False
    fake.script(["exec"], [_ok(b"file.txt\n"), _ok(b"10 file.txt\n10 total\n")])
    assert sb.list_dir("-") == "file.txt  (10 bytes)"
    assert fake.calls[-2][0][-1] == "./-"
    assert fake.calls[-1][0][-2:] == ["./-", "file.txt"]


def test_append_file_guard_stats_bare_dash_as_a_file_not_stdin(started):
    sb, fake, _ = started
    _script_append_guard(fake, _ok(b"4\n"))
    fake.script(["exec", "-w", "/work", "dw-abc123", "/usr/bin/head"], _ok(b"one\n"))
    fake.script(["exec", "-w", "/work", "-i", "dw-abc123", "/bin/sh", "-c",
                 docker_mod.APPEND_WRITE_SCRIPT], _ok())
    sb.append_file("-", "two\n")
    guard = [c for c in fake.calls if docker_mod.APPEND_GUARD_SCRIPT in c[0]][0][0]
    assert guard[-2:] == ["_", "./-"]

VERIFY: run python3 -m pytest -q -p no:cacheprovider tests/test_docker_sandbox.py and expect 176 passed. Then run python3 -m pytest -q -p no:cacheprovider with timeout=300. Finish when both pass; do not rewrite tests that already pass.
```

### Invocation

Released `dirtywork==0.13.1` via `pipx run --spec`, from repo HEAD `516a3c1` (main), `--provider openai --base-url http://localhost:1234/v1 --model qwen/qwen3-coder-next --sandbox docker --image dirtywork-worker-pytest:0.13 --verify "python3 -m pytest -q -p no:cacheprovider" --verify-rounds 2 --max-turns 60 --timeout 1800`. The brief is passed as one argv element. `tools/soak_sampler.sh` runs for the whole wall time and is stopped on every exit path.

### Review gates

The worker's diff should match the dry-run patch (same three production edits, nine expectation edits, four appended tests). Any other touched file or any rewritten passing test is a finding. Host: `tests/test_docker_sandbox.py` 176 passed, full suite green, `git diff --check` clean. Independently: the nine new cases fail on the baseline module.

## Delivery

[PR #153](https://github.com/JimboSchneider/dirtywork/pull/153) closes issue #146; run ledger in [the bench directory](../bench/2026-09-08-issue-146-bare-dash-operands-ledger.md). Pending the owner's merge go-ahead.
