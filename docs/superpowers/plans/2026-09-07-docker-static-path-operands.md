# Docker static-tool path operands: implementation plan

**Goal:** Keep worker-supplied listing/search paths in operand position, so static tools cannot reinterpret them as find expressions or grep options.

**Spec:** The owner's 2026-09-07 review of PR #144 approved a small standalone fix for the observed Docker `list_dir("-delete")` and `grep("x", "--pre=./helper")` argv. This plan records that bounded design; it does not implement Worker Action Firewall #139.

**Architecture:** Leave `_rel` and policy unchanged. Prefix the normalized GNU find path with `./`; terminate rg and fallback grep options with `--` immediately before their path. Existing `head --` and fallback `cd --` paths already separate options from data. This fixes advertised-intent/evidence integrity inside the existing Docker containment; the equivalent effects are already possible via Docker bash.

**Tech stack:** Python >=3.9, pytest, GNU find, ripgrep/GNU grep; no dependencies added.

## Global constraints

- Repository `CLAUDE.md` requires the latest released dirtywork plus a local worker to implement code. PyPI checked 2026-09-07: `0.13.1`. Worker: `qwen/qwen3-coder-next` via LM Studio. Image: existing `dirtywork-worker-pytest:0.13`, network disabled.
- Worker changes only `dirtywork/sandbox/docker.py` and `tests/test_docker_sandbox.py`; orchestrator writes docs and the run ledger.
- Preserve host behavior, lexical path validation, listing/search output, glob options and fallback behavior.
- GNU find's `--` does not disambiguate expression-like starting points. Use a `./` prefix, including for `!` and `(`. For rg/grep, insert `--` after pattern/glob options and before the target path.
- Do not merge or release without the owner's per-action authorization.

## Task W1: separate static-tool path operands

**Consumes:** Existing `DockerSandbox.list_dir`, `DockerSandbox.grep`, `_rel`, and the `started`/`FakeDocker` test fixtures.

**Produces:** Same public signatures/results, with unambiguous path operands; focused regression coverage for option/expression-like paths.

- [x] Verify actual GNU find/rg behavior in an isolated disposable container and the current argv construction.
- [x] Launch the following brief verbatim through released dirtywork with the metrics sampler active.
- [x] Review the exported diff and confirm regression failures against baseline, then passing tests on the fix.
- [x] Run the default suite on the host and exercise actual emitted argv against container binaries with temporary fixtures.
- [x] Record run metrics/verdict, create a standalone PR, and link its status from the baseline inventory in PR #144.

### Worker brief

```text
Fix Docker static-tool path/option confusion found while reviewing PR #144.
Touch only dirtywork/sandbox/docker.py and tests/test_docker_sandbox.py. No docs, releases, commits, or policy changes.
Current _rel accepts '-delete' and '--pre=./helper'. list_dir passes rel to GNU find as an expression; grep passes rel to rg as an option. Host code is unaffected.
1. Write regression tests FIRST in tests/test_docker_sandbox.py, using its started/FakeDocker fixtures to inspect the actual emitted argv without executing dangerous commands.
2. Add test_list_dir_treats_expression_like_paths_as_paths, parameterized with literal input/expected pairs ('-delete','./-delete'), ('!','./!'), ('(','./('), ('./-delete','./-delete'). Call real sb.list_dir, check the actual find starting point equals the expected path, and check ordinary listing output is retained with scripted output.
3. Add test_grep_treats_option_like_paths_as_paths for both rg and fallback grep and paths '--pre=./helper' and '-v'. Exercise real sb.grep with a glob, verify the selected executable and final argv operands ['--', path], ensure pattern/glob flags precede the separator, and check normal search output. Set the existing _has_rg cache on the fixture instance to select the backend.
4. Run python3 -m pytest -q -p no:cacheprovider tests/test_docker_sandbox.py -k 'treats_expression_like_paths or treats_option_like_paths' and confirm the new tests FAIL for the current unsafe argv. Report the failing count; do not skip the red step.
5. Minimal production fix: in the GNU find branch of list_dir replace its path argument rel with './' + rel. In grep replace cmd.append(rel) with cmd.extend(['--', rel]) after rg/fallback/glob construction. Add short comments explaining why find needs a prefix and why grep needs the option terminator. Do not change _rel, fallback ls, or unrelated command sites.
6. Update the two existing exact-argv expectations for list_dir('.') (find path './.') and grep (separator before '.'). Add no other production changes.
7. Run the focused regressions, all tests/test_docker_sandbox.py, and python3 -m pytest -q -p no:cacheprovider. Fix failures caused by this change.
8. Finish with the red/green evidence and summary. Do not run real -delete/preprocessor commands yourself; the orchestrator will independently verify behavior in disposable fixtures.
```

### Invocation

Run the release against this repository with `--branch-from codex/docker-static-paths`, `--provider openai --base-url http://localhost:1234/v1`, `--model qwen/qwen3-coder-next`, `--sandbox docker --image dirtywork-worker-pytest:0.13`, `--verify "python3 -m pytest -q -p no:cacheprovider" --verify-rounds 2 --max-turns 60 --timeout 1800`. Pass the brief as one subprocess argv element, not interpolated shell text. Start `tools/soak_sampler.sh` before the worker and stop it on every exit path. Preserve stdout JSON, stderr, sampler CSV, transcript, run metadata and verdict.

### Review gates

The new tests must fail against the pre-fix module and pass with the fix. A reverted find prefix or missing grep separator must be caught. The host default suite excludes live model/Docker/Ollama tests, so separately run a disposable-container check using the actual command tails generated by `DockerSandbox`: find must list a literal `-delete` directory without deleting sentinels; rg must search an option-like path without invoking a helper; fallback grep must search a literal `-v` file. Check ordinary paths and globs remain usable. No new runtime authority or evidence enums are part of this fix.

## Resume feedback (verbatim)

```text
Your production fix is close. I interrupted because the new tests loop over one cached Sandbox, while assuming every call repeats its capability probe. That assumption is false. Resume with these exact changes only in dirtywork/sandbox/docker.py and tests/test_docker_sandbox.py.

1. In list_dir simplify find_path to "./" + rel (unconditionally); _rel already normalizes away leading ./, so the startswith branch is redundant. Keep the grep separator fix and the two updated existing argv expectations.
2. Replace EVERYTHING from the new def test_list_dir_treats_expression_like_paths_as_paths to EOF with these exact parameterized tests (the original file ended immediately before your additions). No loops, no probe call-count assertions, no further test helpers. Put two blank lines before the first decorator.

@pytest.mark.parametrize(("path", "expected_path"), [
    ("-delete", "./-delete"),
    ("!", "./!"),
    ("(", "./("),
    ("./-delete", "./-delete"),
])
def test_list_dir_treats_expression_like_paths_as_paths(started, path, expected_path):
    sb, fake, _ = started
    sb._has_gnu_find = True
    fake.script(["exec"], _ok(b"f\t18\tREADME.md\n"))
    out = sb.list_dir(path)
    argv = fake.calls[-1][0][4:]
    assert argv[:2] == ["/usr/bin/find", expected_path]
    assert out == "README.md  (18 bytes)"


@pytest.mark.parametrize("has_rg", [True, False])
@pytest.mark.parametrize("path", ["--pre=./helper", "-v"])
def test_grep_treats_option_like_paths_as_paths(started, has_rg, path):
    sb, fake, _ = started
    sb._has_rg = has_rg
    fake.script(["exec"], _ok(b"./file.txt:1:needle\n"))
    out = sb.grep("needle", path=path, glob="*.txt")
    argv = fake.calls[-1][0][4:]
    assert argv[-2:] == ["--", path]
    if has_rg:
        assert argv[:-2] == ["/usr/bin/rg", "-n", "--no-heading", "-M", "300",
                            "-e", "needle", "-g", "*.txt"]
    else:
        assert argv[:-2] == ["/usr/bin/grep", "-rn", "-e", "needle", "--include=*.txt"]
    assert out == "file.txt:1:needle"

3. Run the focused selection; expected 8 passed, no fixture failures:
python3 -m pytest -q -p no:cacheprovider tests/test_docker_sandbox.py -k 'treats_expression_like_paths or treats_option_like_paths'
4. Run all tests/test_docker_sandbox.py, then python3 -m pytest -q -p no:cacheprovider. Use timeout=300 on full-suite bash so it has time to finish. Do not rewrite tests that already pass. Remove trailing whitespace from only your changed lines. Finish when the gates pass.
The orchestrator will rerun these final tests against the baseline production module to verify red, then against your fix to verify green. Do not revert production code for this check yourself.
```

Resume invocation: `dirtywork resume fix-docker-static-tool-pathoption-confus-0906202556-593549d4 --feedback-file <file-containing-the-text-above> --base-url http://localhost:1234/v1 --max-turns 40 --timeout 1800`, through `pipx run --spec dirtywork==0.13.1`, with a separate sampler.

## Fallback decision

Both worker attempts were rejected. The first repeated incorrect capability-probe assumptions in its tests. During the resume the worker rewrote most of the test file, then restored the original file without completing the regressions. The orchestrator interrupted both attempts gracefully and preserved their receipts. Under the repository rule allowing fallback after a failed resume, Codex implemented the bounded final patch and tests on `codex/docker-static-paths`; none of the broad test rewrite was retained. See the [run ledger](../bench/2026-09-07-docker-static-path-operands-ledger.md).

## Delivery

Standalone fix: [PR #145](https://github.com/JimboSchneider/dirtywork/pull/145). The baseline inventory in [PR #144](https://github.com/JimboSchneider/dirtywork/pull/144) links the operand fix from both tool rows and the gap table. Both PRs are pending merge. Host validation and independent review are recorded in the ledger.
