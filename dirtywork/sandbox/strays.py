"""Pure functions and constants for the docker sandbox's stray-process handling.

No docker calls in this module.
"""
from __future__ import annotations

import re


# Constants (exact text; raw triple-quoted strings for the two scripts)

TETHER_DISCOVERY_SCRIPT = r"""n=0; t=
for p in /proc/[0-9]*; do
  read -r c 2>/dev/null < "$p/comm" || continue
  [ "$c" = cat ] || continue
  n=$((n+1)); t=${p#/proc/}
done
[ "$n" = 1 ] || exit 3
echo "$t"
"""

# STRAY_KILL_SCRIPT must stay fork-free (dash builtins only — read, [, kill, for, exit; no $( ), no pipe, no backtick, no subshell; the only "|" characters are the "||" of the two guards) because it has to run inside a pids-saturated container; the tether pid arrives as "$1"; the glob is re-expanded per pass so a process forked between passes is caught.
STRAY_KILL_SCRIPT = r"""T=$1
read -r c 2>/dev/null < "/proc/$T/comm" || exit 3
[ "$c" = cat ] || exit 3
for pass in 1 2 3; do
  for p in /proc/[0-9]*; do
    p=${p#/proc/}
    [ "$p" = 1 ] && continue
    [ "$p" = "$T" ] && continue
    [ "$p" = "$$" ] && continue
    kill -9 "$p" 2>/dev/null
  done
done
exit 0
"""

LOCK_SWEEP_ARGV = ["/usr/bin/find", "/gitdir", "(", "-name", "*.lock", "-o", "-name", "gc.pid", ")", "-type", "f", "-delete", "-print0"]
LOCK_PATH_RE = re.compile(r"^/gitdir/(?:.+/)?(?:[^/]+\.lock|gc\.pid)$")

MAX_STRAYS = 20
MAX_STRAY_CHARS = 200
MAX_LOCKS = 20
NOTICE_CMDS = 3
NOTICE_CMD_CHARS = 80

# --entrypoint /bin/cat means the tether row reads "/bin/cat" (and tini's row "/sbin/docker-init -- /bin/cat"); a bare
# "cat" is what the spec's experiment showed — accept both. A stray that renders as exactly "cat" is
# therefore invisible to the detector (documented loophole).
TETHER_CMDS = ("cat", "/bin/cat")


def stray_rows(top_output: bytes) -> list[str]:
    """Parse docker top output and return CMD of every non-tether row.

    Takes raw docker top stdout, decodes it, strips the header line,
    and filters out tether processes (bare "cat", "/bin/cat", or
    anything ending with "docker-init -- cat" or "docker-init -- /bin/cat").

    Returns a list of CMD strings in order (empty if only tether present
    or output is empty).
    """
    # Decode with error replacement
    text = top_output.decode("utf-8", errors="replace")
    lines = text.splitlines()

    if not lines:
        return []

    # Parse header to determine column count
    header_cols = lines[0].split()
    n = max(len(header_cols), 1)

    result = []
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split(None, n - 1)
        cmd = fields[-1] if fields else ""

        # Skip tether processes
        if cmd in TETHER_CMDS:
            continue
        if cmd.endswith("docker-init -- cat"):
            continue
        if cmd.endswith("docker-init -- /bin/cat"):
            continue

        result.append(cmd)

    return result


def parse_tether_pid(output: bytes) -> int | None:
    """Parse docker exec output for tether pid.

    Decodes with errors="replace" and strips the whole text, then returns int(text)
    if text matches ^[0-9]+$ and value > 0, else None.
    """
    text = output.decode("utf-8", errors="replace").strip()

    # Check if text is a positive integer
    if not re.fullmatch(r"[0-9]+", text):
        return None

    try:
        value = int(text)
        if value > 0:
            return value
    except ValueError:
        pass

    return None


def parse_locks(output: bytes) -> list[str]:
    """Parse lock sweep output and return valid lock file paths.

    Splits on null bytes, unconditionally drops the last chunk (unterminated),
    decodes each remaining chunk, and keeps only those matching LOCK_PATH_RE.
    """
    # Split on null bytes and unconditionally drop the last chunk
    chunks = output.split(b"\0")[:-1]

    result = []
    for chunk in chunks:
        if not chunk:
            continue
        path = chunk.decode("utf-8", errors="replace")
        if LOCK_PATH_RE.fullmatch(path):
            result.append(path)

    return result


def cap_strays(rows: list[str]) -> tuple[list[str], int | None]:
    """Cap stray rows for display.

    Returns (capped list, total if truncated else None).
    - Cuts each row to MAX_STRAY_CHARS with trailing "…" when cut
    - Returns at most MAX_STRAYS rows
    - Returns len(rows) if > MAX_STRAYS, else None
    """
    total = len(rows)

    # Cap to MAX_STRAYS rows
    capped = rows[:MAX_STRAYS]

    # Cut each row to MAX_STRAY_CHARS
    result = []
    for row in capped:
        if len(row) > MAX_STRAY_CHARS:
            result.append(row[:MAX_STRAY_CHARS] + "…")
        else:
            result.append(row)

    total_result = total if total > MAX_STRAYS else None

    return (result, total_result)


def cap_locks(paths: list[str], truncated: bool) -> tuple[list[str], int | None]:
    """Cap lock paths for display.

    Returns (capped list, total if not truncated else None).
    - Returns at most MAX_LOCKS paths uncut
    - Returns len(paths) if > MAX_LOCKS and not truncated, else None
    """
    total = len(paths)

    # Cap to MAX_LOCKS paths
    capped = paths[:MAX_LOCKS]

    # Return total only if not truncated (meaning list was capped but caller indicated no truncation)
    total_result = total if (total > MAX_LOCKS and not truncated) else None

    return (capped, total_result)


def stray_kill_text(strays: list[str], total: int | None, locks_removed: list[str]) -> str:
    """Generate text about killed stray processes.

    Args:
        strays: List of stray command lines (already capped)
        total: Total number of strays if truncated, else None
        locks_removed: List of removed lock paths (message added when non-empty)

    Returns human-readable message about killed processes.
    """
    n = total if total is not None else len(strays)

    # Build the commands summary
    cmds_list = strays[:NOTICE_CMDS]
    cmds = "; ".join(s[:NOTICE_CMD_CHARS] for s in cmds_list)

    if n > NOTICE_CMDS:
        cmds += f"; +{n - NOTICE_CMDS} more"

    # Build the main message
    plural = "es" if n != 1 else ""
    text = f"The sandbox killed {n} background process{plural} your last command left running ({cmds}). A process cannot outlive the bash call that started it — start and use anything you need within one command."

    # Add lock removal note if applicable
    if locks_removed:
        text += " Stale git lock files they left in the repository were removed."

    # Always end with git status instruction
    text += " Run `git status` to confirm the repository state before continuing."

    return text


def sandbox_reset_text(reason: str) -> str:
    """Generate text about sandbox reset.

    Args:
        reason: The reason for reset (e.g., "oom", "stray process after bash")

    Returns human-readable message about sandbox reset.
    """
    return f"The sandbox container was reset after your last command ({reason}). Files in the worktree are intact, but git metadata was re-initialized: the index, stashes, local commits and branches you created inside the sandbox are gone, and the branch is back at the run's base commit with your file changes uncommitted. Run `git status` before continuing."
