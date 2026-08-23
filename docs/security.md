# Security

dirtywork's containment model — what Docker mode locks down, what it doesn't, and what `--sandbox none` gives up — plus the accident-grade guardrails that back it.

## Security & trust

> **Docker mode protects host integrity and host execution, not
> repository-history confidentiality.** The worker can read the *entire*
> parent object store (all branches, other worktrees' objects, unreachable
> objects). Do not run it on a clone whose history holds secrets you would
> not show the worker.

> **Windows: designed for Docker Desktop on Windows; not supported until a
> Windows integration suite passes.** Since 0.9 the unit suite also runs on
> `windows-latest` in CI as an **advisory (allowed-to-fail) job**: it is a
> separate job that the release gate does not require, and it publishes a
> per-file pass/fail/error/skip table (`tools/junit_summary.py`) plus the raw
> JUnit XML as an artifact. That measures the gap; it does not close it, and no
> production code is changed for Windows. Items that still need real Windows
> testing: Git for Windows paths and `\\?\` handling, `docker` CLI behavior, uid
> `1000:1000`, symlink-as-file export, case-insensitivity, long paths,
> `core.symlinks=false`, `core.longpaths`.

**Docker is the default sandbox as of 0.4 — a breaking change from 0.2.**
Every tool call (`read_file`/`write_file`/`append_file`/`edit_file`/`apply_edits`/
`insert_before`/`insert_after`/`list_dir`/`grep`/`bash`) runs inside a
locked-down container: `--network none` by default,
`--read-only` root filesystem, `--cap-drop ALL`, kernel-enforced memory/CPU/
process-count/per-file-size limits, and no host path mounted in except the
parent repository's read-only git object store. The worker's tree lives on
a Docker volume, never a bind mount, so host git never touches worker
content — a hostile `.gitattributes` plus a local git filter cannot execute
on the host. The tree reaches your worktree only after the run ends,
through a validated tar export (`dirtywork/sandbox/export.py`): file/dir/
symlink members only, path-escape and `.git`-named-entry checks, count and
byte caps, extraction that never calls `tarfile.extract()`/`extractall()`.
Docker missing or the daemon down is a preflight error (exit 2, with a
hint) — there is no silent fallback to unsandboxed execution. The export
step itself (which computes the diff and extracts the final tree, in a
fresh container) always runs with `--network none`, even when
`--allow-network` was passed for the worker.

**What Docker mode does *not* give you:**

- **Confidentiality of repository history** (see the callout above).
- **A portable disk quota.** Total disk is a best-effort bound: worktree
  size is sampled during commands and a host free-space floor is polled,
  but a burst inside one sampling interval (0.5-5 s) can exceed the limit
  before the container is killed. The exported tree is hard-capped by the
  validator regardless.
- **Git-ignored files in the exported worktree.** Build outputs
  (`node_modules`, `bin`/`obj`, `.venv`) stay inside the container's volume
  and are never exported — only the git-visible tree is. `--keep-volume`
  plus `docker run` against the volume recovers them if you need to.
  Non-Windows.

**`--sandbox none`** is the explicit opt-in to pre-0.4 host-mode behavior:
tools are path-confined to the worktree (symlink-safe realpath checks,
`.git/` write-protected), but `bash` is a general shell gated only by a
best-effort regex denylist and a `HOME` redirected into the worktree. A
determined or prompt-injected model can still read absolute host paths
(`cat /etc/...`). Use it only against models and repositories you would
trust with unconfined shell access on your machine — the same caveat 0.2
carried, unchanged.

**Residual exposures (documented, accepted, both modes where relevant):**

- Object-store confidentiality (docker mode) — see the callout above.
- Escaping symlinks (committed in the base tree, or created by the worker)
  are created on the host inside the worktree; dirtywork never follows
  them and lists them in `run_end.escaping_symlinks`. Anything *else* you
  run in that worktree afterward must not follow symlinks blindly.
- Host `git status`/`diff`/`add`/`merge` that *you* run afterward use your
  own git config; a worker-authored `.gitattributes` can trigger a
  configured filter (git-lfs and similar). Review
  `~/.dirtywork/runs/<slug>/diff.patch` instead (the container-computed
  patch — no host git ever touches worker content for that path) or with
  `GIT_CONFIG_GLOBAL=/dev/null`.
- `dirtywork runs snapshot`'s own commit runs no filter or hook (plumbing
  only, `--no-filters`). But two callers that decide *whether* to snapshot —
  `--branch-from @<slug>`'s dirty check and `runs clean`'s dirty-worktree
  guard — run host `git status` on the exported worktree, where a repo-LOCAL
  clean filter (e.g. `git lfs install --local`) still applies even with
  `GIT_CONFIG_GLOBAL=/dev/null`: the same exposure as running `git status`
  there yourself.
- A malicious target repo's `CLAUDE.md`/`AGENTS.md` (read from the base
  commit via git, not the filesystem — symlinks and oversized files are
  rejected) is injected into the worker's prompt; treat untrusted repos'
  documentation as you would untrusted code.

**Practical guidance:** run dirtywork against models and repositories
you'd trust with the equivalent of a locked-down container on your
machine. Read the transcript and diff before you merge — that review is
still the real gate for *what a run produced*, even though docker mode now
also gates *what a run could do to your host while producing it*.

## Safety model

**Docker mode (default):** the container is the real boundary —
`--network none`, `--read-only` root filesystem, `--cap-drop ALL`,
kernel-enforced memory/CPU/process/per-file-size limits, no host path
mounted in except a read-only bind mount of the parent repository's git
object store. The worktree reaches the host only through the validated
tar export. See "Security & trust" above for what this does and does not
cover.
`bash` in docker mode only enforces the mode-independent policy rules (no
`git push`, `sudo`, piping a download into a shell, or system-control
commands) — the host-filesystem/host-repo rules below don't apply, since
the container has no host filesystem or shared parent repo to escape into.

**`--sandbox none` (host mode, pre-0.4 behavior):** guardrails block
**accidents, not adversaries** — the post-run review is the real gate:

- All file tools are path-confined to the worktree (symlink-safe realpath
  checks; `.git/` is write-protected against hook injection).
- `bash` runs cwd-pinned in the worktree with a minimal environment (your
  shell's tokens/keys are not inherited) and a regex denylist: `sudo`,
  `git push`, `git config`/`remote`/`worktree`/`branch -D`/… that would write
  the parent repo's shared state (including through `git -C`/`git -c`/`--flag`
  global options), `rm`/`mv`/`chmod`/`chown` on absolute or `~` paths,
  `cd`/`pushd` escapes (an absolute-path `cd` that lands *inside* the
  worktree is allowed — only paths that leave it are blocked), downloads
  piped to a shell, system-control commands, redirects outside the
  worktree.
- Every denylist rejection is logged to the transcript as a
  `guardrail_block` event, so attempted escapes are visible at review time.
- File tools refuse to operate on anything that isn't a regular file (FIFOs,
  devices, sockets) and refuse to write through a symlink at the final path
  component, even when its target is inside the worktree. As of 0.10 a write is
  staged in a sibling temp and promoted with `rename(2)`; a symlink present at
  call time refuses exactly as before, and one that appears in the gap between
  the check and the promote gets **replaced as a link** — `rename(2)` does not
  follow its destination, so nothing is ever written through it. That is a
  robustness change, not a security change: host tool calls are serial, every
  `bash` call SIGKILLs its process group when it returns, and the realistic
  adversary here is a confused or prompt-injected model, not a racing process.
  The same delta holds in docker mode, where the promote (`mv -fT --`) replaces
  the path itself: a symlink target is replaced by a regular file rather than
  written through, and a hardlinked target is un-linked (the sibling link
  keeps the old content). A promoted file's inode changes, so ownership and
  extended attributes are the new file's, not the old file's. `write_file`
  content is capped at 5 MB, `list_dir` output at 2000 entries, and the
  assistant's own text is capped at 64 000 chars in the transcript (the
  full text is still sent to the model).
- Worktree growth is sampled after every tool call against
  `--max-worktree-mb`/`--max-worktree-files`; past either, the run ends with
  status `budget_exceeded`.
- Network is allowed (package restores need it); per-command timeout 120s
  default, 600s max.

Plainly: this is **not a sandbox**. Run it against repos where you'd trust
yourself to review the diff — because that review is the actual gate.

