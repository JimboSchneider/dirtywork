# #101: private TMPDIR for the fingerprint script's temp-litter check: Implementation Plan

> **This repository's execution rule overrides the default plan-execution skills** (repo `CLAUDE.md`,
> "Dogfood rule"). Task W1 below is built by the **released dirtywork** running against this
> repository with a local worker (`qwen/qwen3-coder-next` via LM Studio). Claude writes the brief,
> reviews the branch, runs the host suite, and writes the PR. A Claude implementer touches code only
> after a worker resume-with-feedback has failed, and the PR says so. Owner approval is needed for
> the merge, never assumed.

**Plan v1** (2026-08-29 16:35 CDT). Root cause verified before writing the brief (not assumed):
`tests/test_changes.py::test_real_script_on_the_host` step 7 counts entries in
`tempfile.gettempdir()` before and after one `HostSandbox(...).bash(FINGERPRINT_SCRIPT, ...)` call.
`tempfile.gettempdir()` on CI is the **shared** runner temp dir, so any other process creating or
deleting an entry there in that ~1s window fails the assertion for a reason unrelated to the script
(seen on PR #100's `docker-live` job, `cf98743`, on identical code that had passed 20 minutes
earlier).

**The fix works, verified by tracing the actual code path (not assumed):**
`HostSandbox.bash()` → `dirtywork/tools.py:bash()` → `run_capped(..., env=build_env(home=worktree))`.
`build_env()` (`dirtywork/guardrails.py:210`) explicitly carries `TMPDIR` from `os.environ` into the
subprocess: `keep = ("PATH", "TERM", "LANG", "TMPDIR")`. `FINGERPRINT_SCRIPT`
(`dirtywork/changes.py:31`) does `tmp=$(mktemp -d)`, which honors `$TMPDIR`. So
`monkeypatch.setenv("TMPDIR", <private dir>)` genuinely redirects where the **script itself** writes
its temp dirs, not just where the test's own counter looks — both sides read the same env var, so
they stay in sync. `tempfile.gettempdir()` caches its answer in the module-level `tempfile.tempdir`;
that cache must be cleared (`monkeypatch.setattr(tempfile, "tempdir", None)`) or the Python-side
`gettempdir()` call keeps returning whatever was cached before this test ran.

## Global Constraints

- Only `tests/test_changes.py` changes. No production code.
- `monkeypatch.setattr`, not a direct `tempfile.tempdir = None` assignment, so pytest's fixture
  teardown restores the original module state automatically (no cross-test leakage).
- The private temp dir lives under pytest's own `tmp_path`, so it's cleaned up by pytest same as
  everything else the test creates — no separate cleanup code needed.

## Execution model

Same as prior sessions' plans (`docs/superpowers/plans/2026-08-29-node22-worker-image.md`,
"Execution model"): this session's `$SCRATCH` (`/private/tmp/claude-501/-Users-jimschneider-repos-dirtywork/6d1529ba-f5a6-4b9b-8e8f-c5c06b027d19/scratchpad`), `runN22.sh` copied with
`--branch-from issue-101-flaky-tempdir-test`, released **0.13.1**, image `dirtywork-worker-pytest:0.13`.
Pre-check the brief on the host in a throwaway worktree before launching — it's caught real defects
every time this session used it.

---

### Task W1: Isolate the temp-litter check behind a private TMPDIR (worker)

**Files:** `tests/test_changes.py` — the `test_real_script_on_the_host` function signature (line 185)
and its step-7 block (the 16 lines starting `# 7. Count entries of tempfile.gettempdir() before and
after`, immediately after the step-6 `assert after_count == before_count` line).

**Interfaces:** none — single self-contained test function, no other code depends on it.

- [ ] **Step 1: Pre-check on the host** — apply the brief's edit in a throwaway worktree, run
  `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider tests/test_changes.py::test_real_script_on_the_host -v`
  (needs `bash`, `git`; runs on macOS/Linux hosts), confirm it passes and that a manual check —
  writing a decoy file into the *real* system temp dir mid-test — would have failed the *old*
  assertion but not the new one (proves the isolation is real, not cosmetic). Discard.
- [ ] **Step 2: Brief** `$SCRATCH/brief-101-w1.md`:

```
Issue #101 (flaky test). tests/test_changes.py::test_real_script_on_the_host step 7 counts entries in tempfile.gettempdir() before and after a subprocess call, expecting them equal -- but gettempdir() is the runner's SHARED system temp dir, so any other process touching it in that window fails the assertion for a reason that has nothing to do with the script under test. Fix: give the script and the counter their own private temp directory instead of the shared one. Touch only tests/test_changes.py.

1. Change the function signature on line 185 from:
def test_real_script_on_the_host(tmp_path: Path):
to:
def test_real_script_on_the_host(tmp_path: Path, monkeypatch):

2. Replace this exact block (it starts right after the step-6 assert on the object-store file count, with "# 7. Count entries of tempfile.gettempdir() before and after"):

    # 7. Count entries of tempfile.gettempdir() before and after
    import tempfile

    temp_dir = tempfile.gettempdir()

    def count_temp_entries(path):
        """Count entries in a directory."""
        return len(os.listdir(path))

    before_temp = count_temp_entries(temp_dir)

    raw6 = HostSandbox(tmp_path).bash(FINGERPRINT_SCRIPT, FINGERPRINT_TIMEOUT)

    after_temp = count_temp_entries(temp_dir)
    assert after_temp == before_temp, f"Temp dir entry count should not change: {before_temp} -> {after_temp}"

with exactly this (same indentation, 4 spaces):

    # 7. Count entries of a private temp dir before and after -- tempfile.gettempdir()
    # is the runner's SHARED system temp dir, and any other process creating or
    # removing an entry there during the ~1s window flakes this assertion for a
    # reason that has nothing to do with the script under test (#101). Point the
    # script at a private TMPDIR instead and clear tempfile's module-level cache
    # (tempfile.tempdir) so gettempdir() re-reads the env var. HostSandbox.bash()
    # carries TMPDIR from os.environ into the subprocess (dirtywork/guardrails.py
    # build_env()), and the script's `mktemp -d` honors it, so both sides land on
    # the same private directory.
    import tempfile

    private_tmp = tmp_path / "systmp"
    private_tmp.mkdir()
    monkeypatch.setenv("TMPDIR", str(private_tmp))
    monkeypatch.setattr(tempfile, "tempdir", None)
    temp_dir = tempfile.gettempdir()
    assert temp_dir == str(private_tmp), f"expected the private TMPDIR, got {temp_dir}"

    def count_temp_entries(path):
        """Count entries in a directory."""
        return len(os.listdir(path))

    before_temp = count_temp_entries(temp_dir)

    raw6 = HostSandbox(tmp_path).bash(FINGERPRINT_SCRIPT, FINGERPRINT_TIMEOUT)

    after_temp = count_temp_entries(temp_dir)
    assert after_temp == before_temp, f"Temp dir entry count should not change: {before_temp} -> {after_temp}"

Checks: `grep -c "def test_real_script_on_the_host(tmp_path: Path, monkeypatch):" tests/test_changes.py` prints 1; `grep -c "private_tmp = tmp_path" tests/test_changes.py` prints 1; `grep -c "monkeypatch.setattr(tempfile, \"tempdir\", None)" tests/test_changes.py` prints 1. Verify: python3 -m pytest -q -p no:cacheprovider tests/test_changes.py -v, then the full suite.
```

- [ ] **Step 3: Run** the dogfood build with `$SCRATCH`'s `run0131.sh` (or a fresh copy), branch-from
  `issue-101-flaky-tempdir-test`.
- [ ] **Step 4: Review.** Diff is exactly the two named edits, nothing else touched; run the test
  five times in a row on the host (`for i in 1 2 3 4 5; do ...; done`) to build confidence the flake
  is actually gone, not just quiet once; run the full suite.
- [ ] **Step 5: Resume if needed (≤2); chain** — export commit, rebase onto the branch, ff-merge,
  clean the run. PR, CI, ledger row. **Never merge without Jim's explicit go** (see
  `feedback-merge-approval.md`).

## Self-review

Spec coverage: the issue's proposed fix (private `TMPDIR` via `monkeypatch`) is exactly what the
brief implements, with the "why it actually works" traced through `build_env()` and confirmed rather
than assumed. Placeholders: none. Types: `monkeypatch` is the standard pytest fixture; no new
signature is introduced elsewhere that depends on this function.
