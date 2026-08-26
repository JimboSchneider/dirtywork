"""The change guard's primitives (spec #66 §4.1): the worktree fingerprint
script, its parser, and the texts the runner delivers. The script runs
through `Sandbox.bash` -- the seam `--verify` uses -- in both sandbox modes.
"""
from __future__ import annotations

import re

from .tools import parse_exit_code

FINGERPRINT_TIMEOUT = 60
DEFAULT_NO_CHANGE_TURNS = 10
MAX_REASON_CHARS = 200

# bash-only (both sandboxes run `bash -c`; macOS /bin/bash 3.2 suffices: arrays,
# `read -d ''`, process substitution -- no mapfile). One exec, no recursion.
# Every repository under the worktree -- the root and every nested `.git`
# (file or directory, any depth) -- is snapshotted separately: a scratch
# index, a scratch object directory with the real store as an alternate (the
# real index and store are never written), and each nested root excluded from
# its parent's snapshot by pathspec, so no gitlink is ever recorded and an
# unborn nested repository (no commit yet) works. Probed 2026-08-25 (P4).
FINGERPRINT_SCRIPT = r"""export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
roots=()
while IFS= read -r -d '' g; do roots+=("${g%/.git}"); done < <(find . \( -path ./.git -prune \) -o \( -name .git -prune -print0 \) 2>/dev/null)
dw_snap() (
  d=$1; shift
  cd "$d" || exit 1
  real=$(git rev-parse --git-path objects) || exit 1
  case $real in /*) ;; *) real=$PWD/$real ;; esac
  tmp=$(mktemp -d) || exit 1
  trap 'rm -rf "$tmp"' EXIT
  mkdir "$tmp/objects" || exit 1
  GIT_INDEX_FILE=$tmp/index GIT_OBJECT_DIRECTORY=$tmp/objects GIT_ALTERNATE_OBJECT_DIRECTORIES=$real
  export GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
  git -c core.fsmonitor=false add -A -- . "$@" >/dev/null && git write-tree
)
snap() {
  d=$1; ex=()
  for r in "${roots[@]}"; do
    if [ "$d" = . ]; then ex+=(":(exclude,literal)${r#./}")
    elif [[ $r == "$d/"* ]]; then ex+=(":(exclude,literal)${r#"$d/"}")
    fi
  done
  dw_snap "$d" "${ex[@]}"
}
snap . || exit 1
for r in "${roots[@]}"; do snap "$r" || exit 1; done
git rev-parse HEAD"""

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_CAPPED = "[output truncated at "


def _reason(lines: list[str], fallback: str) -> str:
    for line in lines:
        if line and not _HEX40.match(line):
            return line[:MAX_REASON_CHARS]
    return fallback[:MAX_REASON_CHARS]


def parse_fingerprint(result: str) -> tuple[str | None, str | None]:
    """(fingerprint, reason) from the sandbox's bash result (stdout and
    stderr merged; `exit code: N` first when the command ran to an exit).
    Exit 0 -> the fingerprint is the SORTED 40-hex lines (at least two: a
    tree and HEAD); sorting makes it independent of `find`'s order, so the
    same tree gives the same string on any host or container. Any other
    line under exit 0 (an rc-0 `warning:`/`hint:` from git) is ignored --
    the script exits non-zero on every failure, so rc 0 vouches for every
    hash. Everything else is (None, reason), `reason` capped at 200 chars:
    a non-zero exit (reason = the first non-empty non-hex line, git's own
    diagnostic, else the exit line); a result with no `exit code:` head --
    `timeout_result`'s `ERROR: command timed out after ...`, `ERROR: bash
    failed ...`, a `BLOCKED:` -- (reason = its first line); a CAPPED result,
    whose last line is `[output truncated at 10000 chars -- bash output
    capped]` (MAX_BASH_CHARS; about 240 repositories' worth of lines) --
    (reason = that line): a partial listing must never pass as a
    fingerprint, whatever its exit code says."""
    if not isinstance(result, str) or not result.strip():
        return None, "no output"
    lines = [ln.strip() for ln in result.split("\n")]
    last = next((ln for ln in reversed(lines) if ln), "")
    if last.startswith(_CAPPED):
        return None, last[:MAX_REASON_CHARS]
    code = parse_exit_code(result)
    if code is None:
        return None, lines[0][:MAX_REASON_CHARS]
    body = lines[1:]
    if code != 0:
        return None, _reason(body, lines[0])
    hashes = sorted(ln for ln in body if _HEX40.match(ln))
    if len(hashes) < 2:
        return None, _reason(body, "fewer than two hash lines")
    return "\n".join(hashes), None


def fingerprint(sandbox) -> tuple[str | None, str | None]:
    """Run the script through the sandbox's bash seam. A sandbox without
    `bash` (a test double) is a failed measurement, never an error --
    the drain_notices precedent in the runner."""
    bash = getattr(sandbox, "bash", None)
    if bash is None:
        return None, "sandbox has no bash"
    return parse_fingerprint(bash(FINGERPRINT_SCRIPT, FINGERPRINT_TIMEOUT))


# Texts (spec §4.3, §4.4). Wording is not contract; the numbers and the
# rule that the require_changes text never names `finish` are.
UNCHANGED_REQUIRED = (
    "Not accepted as the end of the run: nothing in the worktree changed since this run started, "
    "but the reviewer's feedback asks for changes. Apply every item of the feedback and run the "
    "check each item names, then call finish(summary=...). A second completion with no change "
    "ends the run as `unchanged`.")
UNCHANGED_PLAIN = (
    "Not accepted as the end of the run: nothing in the worktree changed since this run started. "
    "If the task requires changes, make them now, then call finish(summary=...); if the task is "
    "complete without changes, call finish(summary=...) and say so in the summary.")
NO_CHANGE_SINCE_START_REQUIRED = (
    "Nothing in the worktree has changed since this run started ({k} turns or more) and the "
    "reviewer's feedback is not applied yet. Make the first edit now: stop reading whole files — "
    "grep -n for the line you need to change, then edit it.")
NO_CHANGE_SINCE_START_PLAIN = (
    "Nothing in the worktree has changed since this run started ({k} turns or more). If the task "
    "needs changes, make the first edit now — stop reading whole files: grep -n for the line you "
    "need, then edit it; if the task is complete without changes, call finish(summary=...) and "
    "say so in the summary.")
NO_CHANGE_RECENT = (
    "No file in the worktree has changed in the last {k} turns and nothing was committed. If the "
    "task needs more changes, stop reading whole files — grep -n for the line you need, then edit "
    "it; if the task is complete, call finish(summary=...).")
