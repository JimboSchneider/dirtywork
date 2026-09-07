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

- [ ] Verify actual GNU find/rg behavior in an isolated disposable container and the current argv construction.
- [ ] Launch the following brief verbatim through released dirtywork with the metrics sampler active.
- [ ] Review the exported diff and confirm regression failures against baseline, then passing tests on the fix.
- [ ] Run the default suite on the host and exercise actual emitted argv against container binaries with temporary fixtures.
- [ ] Record run metrics/verdict, create a standalone PR, and link its status from the baseline inventory in PR #144.

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
