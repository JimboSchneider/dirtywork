# Issues #147, #148, #149: one listing script for the Docker sandbox — implementation plan

**Goal:** `DockerSandbox.list_dir` must survive newlines in names (issue #147), render symlinks the way the host does (issue #148), and stop zeroing sizes or dropping a file named `total` in the fallback (issue #149).

**Spec:** the three issues, all filed from the PR #145 review. They share one cause: two listing pipelines (GNU `find -printf` parsed line by line; `ls -1Ap` plus one batched `wc -c`) that each parse tool output shaped for humans.

**Architecture:** replace both with ONE `/bin/sh` script, `LIST_SCRIPT`, run as `sh -c LIST_SCRIPT sh <rel>`: `cd -- "$1"`, then a `for f in .[!.]* ..?* *` loop that prints one NUL-terminated `kind<TAB>size<TAB>name` record per entry. `[ -d "./$f" ]` follows symlinks (link-to-dir lists as a directory, like the host's `is_dir()`); `stat -Lc %s -- "./$f"` sizes the link's target (like the host's `stat()`); a dangling link is kind `l` and renders as `name  (broken symlink)`, the host's wording. `./$f` keeps a name like `-` an operand. One parser splits on NUL, then tab with maxsplit 2, so tabs and newlines in names survive. `find -L` was rejected: on `ln -s . self` it prints a loop warning, drops the entry and exits 1 (verified). Verified byte-identical output on dash (`dirtywork-worker-pytest:0.13`) and BusyBox (`alpine:3.20`) for dotfiles, names with spaces/tabs/newlines/leading `-`, a file named `-`, link-to-dir, link-to-file, absolute link, dangling link, self and parent loops, an empty file, an empty directory, a missing directory (exit 1), and 300 entries (about 0.13 s more than an empty directory, inside one exec). The `_has_gnu_find` probe goes away; `_probe` stays for `rg`.

**Blast radius (measured on a scratch clone at the PR #154 head):** two production edits (insert the constant, replace the `list_dir` body; net 30 lines fewer) and six test edits: two expectation updates, two fallback tests replaced by one-exec/operand tests, the expression-like-paths test re-pointed at the script operand, the fallback bare-dash test replaced by a symlink-kinds test, and the issue #150 `list_dir` probe test replaced by the newline/tab test (the grep probe test still covers `_probe`). Red on baseline: 11 failed / 167 passed; green: 178 passed. The shipped `LIST_SCRIPT` constant, imported from the module, was executed in both images.

**Tech stack:** Python >=3.9, pytest, POSIX sh + `stat -L`; no dependencies added.

## Global constraints

- Repository `CLAUDE.md`: latest released dirtywork plus a local worker implements code. PyPI checked 2026-09-08: `0.13.1`. Worker: `qwen/qwen3-coder-next` via LM Studio. Image: `dirtywork-worker-pytest:0.13`, network disabled.
- Worker changes only `dirtywork/sandbox/docker.py` and `tests/test_docker_sandbox.py`; the orchestrator writes this plan, the ledger and the spec update after the run.
- Brief shape: fix-first, exact `edit_file` pairs with line numbers, generated from the dry-run script so nothing is retyped; never `write_file`.
- Runs from main only after PR #154 merges (its issue #150 test is one of the edits).
- No merge or release without the owner's per-action authorization.

## Task W1: one listing script, one parser

- [x] Verify the script in dash and BusyBox; dry-run the exact edits on a scratch clone (red on baseline, green with the fix).
- [x] After PR #154 merges, launch the brief below verbatim from main through released dirtywork with the metrics sampler active.
- [x] Review the exported diff against the dry-run patch; run the full suite on the host; execute the shipped `LIST_SCRIPT` from the worktree's module in both images.
- [x] Update the spec's `list_dir` bullet; write the ledger row; open a PR that closes issues #147, #148 and #149.

### Worker brief

```text
Issues #147, #148, #149: replace DockerSandbox.list_dir's GNU-find branch and its ls/wc fallback with ONE portable /bin/sh script (new module constant LIST_SCRIPT) that prints NUL-terminated `kind<TAB>size<TAB>name` records, and ONE parser. Newlines in names no longer crash the parse (#147); symlinks render like the host: link-to-dir as `name/`, link-to-file with the target size, dangling as `name  (broken symlink)` (#148); the wc-zeroing and `total` defects go away with the fallback (#149). list_dir makes exactly one docker exec and never probes for find.
Touch ONLY dirtywork/sandbox/docker.py and tests/test_docker_sandbox.py. NEVER write_file either file. Use edit_file with the exact old/new strings below (keep every leading space; each old string occurs exactly once; line numbers are given so you do not need to search). No append_file is needed. No docs, no commits, nothing else.

P1 edit_file on dirtywork/sandbox/docker.py (insert LIST_SCRIPT: old is lines 172-173, the last line of APPEND_WRITE_SCRIPT and its closing paren). P1 old:
    'cp -- "$1" "$2" && cat >> "$2" && ' + _PROMOTE
)
P1 new:
    'cp -- "$1" "$2" && cat >> "$2" && ' + _PROMOTE
)

# list_dir's ONE exec (issues #147, #148, #149): a portable /bin/sh loop that
# prints one NUL-terminated `kind<TAB>size<TAB>name` record per entry,
# dotfiles included. `[ -d ]` follows symlinks, so a link to a directory
# lists as a directory like the host's is_dir(); `stat -L` sizes the link's
# target like the host's stat(); a dangling link is kind `l`. NUL records
# survive a newline in a name, and `./$f` keeps a name like `-` an operand.
# Verified byte-identical on dash and BusyBox, so there is no GNU-find
# branch and no ls/wc fallback to keep in step.
LIST_SCRIPT = (
    'cd -- "$1" || exit 1; '
    'for f in .[!.]* ..?* *; do '
    '[ -e "./$f" ] || [ -L "./$f" ] || continue; '
    'if [ -d "./$f" ]; then printf "d\\t0\\t%s\\0" "$f"; '
    'elif [ -L "./$f" ] && ! [ -e "./$f" ]; then printf "l\\t0\\t%s\\0" "$f"; '
    'else printf "f\\t%s\\t%s\\0" "$(stat -Lc %s -- "./$f" 2>/dev/null || echo 0)" "$f"; fi; '
    'done'
)

P2 edit_file on dirtywork/sandbox/docker.py (replace the body of list_dir: old is lines 795-833, from `rows = []` through the `formatted = [...]` line). P2 old:
        rows = []  # (name, is_dir, size)
        if self._probe("_has_gnu_find", ["/usr/bin/find", "--version"]):
            # GNU find still treats -delete, ! and ( as expressions after --;
            # _rel's `./` anchor keeps the path a starting point.
            out, err = self._list_exec(path, ["/usr/bin/find", rel, "-mindepth", "1", "-maxdepth", "1",
                                              "-printf", "%y\t%s\t%f\n"])
            if err:
                return err
            for line in out.splitlines():
                if line:
                    kind, size, name = line.split("\t", 2)
                    rows.append((name, kind == "d", int(size)))
        else:
            # Spec fallback for images without GNU find: `ls -1Ap` inside the target
            # directory (trailing `/` marks directories), then ONE batched `wc -c`
            # for the file sizes — never one exec per entry.
            out, err = self._list_exec(path, ["/bin/sh", "-c", 'cd -- "$1" && ls -1Ap', "sh", rel])
            if err:
                return err
            names = [line for line in out.splitlines() if line]
            files = [n for n in names if not n.endswith("/")]
            sizes = {}
            if files:
                wc_out, wc_err = self._list_exec(path, ["/bin/sh", "-c", 'cd -- "$1" && shift && wc -c -- "$@"', "sh", rel, *files])
                if wc_err is None:
                    for line in wc_out.splitlines():
                        parts = line.strip().split(None, 1)
                        if len(parts) == 2 and parts[1] != "total":
                            try:
                                sizes[parts[1]] = int(parts[0])
                            except ValueError:
                                pass
            for n in names:
                if n.endswith("/"):
                    rows.append((n[:-1], True, 0))
                else:
                    rows.append((n, False, sizes.get(n, 0)))
        rows.sort(key=lambda r: r[0])  # raw-name sort BEFORE formatting (host parity)
        formatted = [f"{name}/" if is_dir else f"{name}  ({size} bytes)" for name, is_dir, size in rows]
P2 new:
        out, err = self._list_exec(path, ["/bin/sh", "-c", LIST_SCRIPT, "sh", rel])
        if err:
            return err
        rows = []  # (name, kind, size); kind is d, f or l (broken symlink)
        for record in out.split("\0"):
            if record:
                kind, size, name = record.split("\t", 2)
                rows.append((name, kind, int(size)))
        rows.sort(key=lambda r: r[0])  # raw-name sort BEFORE formatting (host parity)
        formatted = [f"{name}/" if kind == "d"
                     else f"{name}  (broken symlink)" if kind == "l"
                     else f"{name}  ({size} bytes)" for name, kind, size in rows]

T1 edit_file on tests/test_docker_sandbox.py (lines 582-590, inside test_list_dir_shapes_output). T1 old:
    fake.script(["exec"], _ok(b"d\t96\tsrc\nf\t18\tREADME.md\n"))
    out = sb.list_dir(".")
    assert "src/" in out
    assert "README.md  (18 bytes)" in out
    assert fake.calls[-1][0] == [
        "exec", "-w", "/work", "dw-abc123",
        "/usr/bin/find", ".", "-mindepth", "1", "-maxdepth", "1",
        "-printf", "%y\t%s\t%f\n",
    ]
T1 new:
    fake.script(["exec"], _ok(b"d\t0\tsrc\0f\t18\tREADME.md\0"))
    out = sb.list_dir(".")
    assert "src/" in out
    assert "README.md  (18 bytes)" in out
    assert fake.calls[-1][0] == [
        "exec", "-w", "/work", "dw-abc123",
        "/bin/sh", "-c", docker_mod.LIST_SCRIPT, "sh", ".",
    ]

T2 edit_file on tests/test_docker_sandbox.py (line 596, inside test_list_dir_caps_entries). T2 old:
    lines = "".join(f"f\t1\tfile{i}\n" for i in range(MAX_LIST_ENTRIES + 50))
T2 new:
    lines = "".join(f"f\t1\tfile{i}\0" for i in range(MAX_LIST_ENTRIES + 50))

T3 edit_file on tests/test_docker_sandbox.py (lines 769-792, the two whole tests test_list_dir_falls_back_to_ls_when_no_gnu_find and test_list_dir_fallback_passes_target_dir). T3 old:
def test_list_dir_falls_back_to_ls_when_no_gnu_find(started):
    sb, fake, run_dir = started
    # Script find --version to fail (no GNU find), then ls -1Ap, then wc -c
    # The _probe makes a call first to check for GNU find (fails)
    # Then list_dir uses the fallback which needs ls -1Ap and wc -c (2 more calls)
    fake.script(["exec"], [_fail(b"find: not found"), _ok(b"b.txt\nsub/\na.txt\n"), _ok(b"3 b.txt\n5 a.txt\n8 total\n")])
    out = sb.list_dir(".")
    assert out == "a.txt  (5 bytes)\nb.txt  (3 bytes)\nsub/"
    # Verify exactly three exec calls: one for find probe, one for ls -1Ap, one for wc -c
    # No per-file stat calls should exist
    assert len(fake.calls) == 3
    for call in fake.calls:
        assert "/usr/bin/stat" not in call[0]


def test_list_dir_fallback_passes_target_dir(started):
    sb, fake, run_dir = started
    # Script find --version to fail (no GNU find), then ls with "src", then wc -c
    fake.script(["exec"], [_fail(b""), _ok(b"file.txt\n"), _ok(b"10 file.txt\n10 total\n")])
    out = sb.list_dir("src")
    assert "file.txt  (10 bytes)" in out
    # Verify the ls exec's argv contains "src" (the target dir is passed)
    assert any("src" in str(call[0]) for call in fake.calls)
T3 new:
def test_list_dir_is_one_exec_and_never_probes(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b"f\t3\tb.txt\0d\t0\tsub\0f\t5\ta.txt\0"))
    assert sb.list_dir(".") == "a.txt  (5 bytes)\nb.txt  (3 bytes)\nsub/"
    assert len(fake.calls) == 1
    assert getattr(sb, "_has_gnu_find", None) is None


def test_list_dir_passes_the_target_dir_as_the_script_operand(started):
    sb, fake, run_dir = started
    fake.script(["exec"], _ok(b"f\t10\tfile.txt\0"))
    assert sb.list_dir("src") == "file.txt  (10 bytes)"
    assert fake.calls[-1][0][-2:] == ["sh", "./src"]

T4 edit_file on tests/test_docker_sandbox.py (lines 3039-3045, the body of test_list_dir_treats_expression_like_paths_as_paths). T4 old:
    sb, fake, _ = started
    sb._has_gnu_find = True
    fake.script(["exec"], _ok(b"f\t18\tREADME.md\n"))
    out = sb.list_dir(path)
    argv = fake.calls[-1][0][4:]
    assert argv[:2] == ["/usr/bin/find", expected_path]
    assert out == "README.md  (18 bytes)"
T4 new:
    sb, fake, _ = started
    fake.script(["exec"], _ok(b"f\t18\tREADME.md\0"))
    out = sb.list_dir(path)
    assert fake.calls[-1][0][-2:] == ["sh", expected_path]
    assert out == "README.md  (18 bytes)"

T5 edit_file on tests/test_docker_sandbox.py (lines 3084-3090, the whole test test_list_dir_fallback_bare_dash_is_a_directory_not_oldpwd, replaced by a new test). T5 old:
def test_list_dir_fallback_bare_dash_is_a_directory_not_oldpwd(started):
    sb, fake, _ = started
    sb._has_gnu_find = False
    fake.script(["exec"], [_ok(b"file.txt\n"), _ok(b"10 file.txt\n10 total\n")])
    assert sb.list_dir("-") == "file.txt  (10 bytes)"
    assert fake.calls[-2][0][-1] == "./-"
    assert fake.calls[-1][0][-2:] == ["./-", "file.txt"]
T5 new:
def test_list_dir_renders_symlink_kinds_like_the_host(started):
    sb, fake, _ = started
    fake.script(["exec"], _ok(b"d\t0\tdirlink\0f\t5\tfilelink\0l\t0\tdangling\0"))
    assert sb.list_dir(".") == "dangling  (broken symlink)\ndirlink/\nfilelink  (5 bytes)"

T6 edit_file on tests/test_docker_sandbox.py (lines 3109-3117, the whole test test_probe_docker_error_is_not_cached_for_list_dir, replaced by a new test because list_dir no longer probes). T6 old:
def test_probe_docker_error_is_not_cached_for_list_dir(started):
    sb, fake, _ = started
    fake.script(["exec"], [_probe_timeout, _ok(b"a.txt\n"), _ok(b"5 a.txt\n5 total\n"),
                           _ok(b"find (GNU findutils) 4.9.0\n"), _ok(b"f\t18\tREADME.md\n")])
    assert sb.list_dir(".") == "a.txt  (5 bytes)"
    assert getattr(sb, "_has_gnu_find", None) is None
    assert sb.list_dir(".") == "README.md  (18 bytes)"
    assert sb._has_gnu_find is True
    assert fake.calls[-1][0][4:6] == ["/usr/bin/find", "."]
T6 new:
def test_list_dir_survives_newline_and_tab_in_filenames(started):
    sb, fake, _ = started
    fake.script(["exec"], _ok(b"f\t1\ta\nb\0f\t5\tt\tab\0d\t0\tsrc\0"))
    assert sb.list_dir(".") == "a\nb  (1 bytes)\nsrc/\nt\tab  (5 bytes)"

VERIFY: run python3 -m pytest -q -p no:cacheprovider tests/test_docker_sandbox.py and expect 178 passed. Then run python3 -m pytest -q -p no:cacheprovider with timeout=300. Finish when both pass.
```

### Invocation

Released `dirtywork==0.13.1` via `pipx run --spec`, from repo HEAD on main after PR #154, `--provider openai --base-url http://localhost:1234/v1 --model qwen/qwen3-coder-next --sandbox docker --image dirtywork-worker-pytest:0.13 --verify "python3 -m pytest -q -p no:cacheprovider" --verify-rounds 2 --max-turns 60 --timeout 1800`. The brief is passed as one argv element. `tools/soak_sampler.sh` runs for the whole wall time and is stopped on every exit path.

### Review gates

The worker's diff should match the dry-run patch. Any other touched file or any rewritten passing test is a finding. Host: `tests/test_docker_sandbox.py` 178 passed, full suite green, `git diff --check` clean; the module's `LIST_SCRIPT` runs in `dirtywork-worker-pytest:0.13` and `alpine:3.20` with the expected records. Independently: the six updated or new tests fail on the baseline module.

## Delivery

Run ledger: [issues #147/#148/#149 ledger](../bench/2026-09-08-issues-147-148-149-one-listing-script-ledger.md). [PR #155](https://github.com/JimboSchneider/dirtywork/pull/155) closes issues #147, #148 and #149; pending the owner's merge go-ahead.
