# dirtywork/sandbox/export.py
from __future__ import annotations

import os
import posixpath
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..workspace import MAX_FILES_CHANGED
from . import RunArtifacts, SandboxError, docker_args, docker_cli, lifecycle


class ExportError(SandboxError):
    """Raised by the export flow or the tar validator. export_run (Task 11)
    catches this and turns it into RunArtifacts(export_status=...)."""


_PAX_GLOBAL_MSG = "export archive contains a PAX global header"

# NUL-safe enumeration of .git entries under /work, excluding the new root
# gitfile (which would be at /work/.git and is type f). Uses -prune to avoid
# descending into .git directories themselves.
EXPORT_GIT_ENTRIES_SCRIPT = r"exec /usr/bin/find /work -mindepth 1 -iname .git ! \( -path /work/.git -type f \) -prune -print0 2>/dev/null"


def parse_git_entries(output: bytes) -> list[str]:
    """Parse NUL-separated git entries from find's output.

    Splits on b'\0', drops the last chunk (it is unterminated), decodes each
    with errors='replace', and keeps only tokens that start with '/work/' and
    whose last path component (case-insensitive) == '.git'. Order is preserved.
    """
    if not output:
        return []
    chunks = output.split(b"\0")
    # drops the last chunk (the text after the final NUL is never a complete record)
    chunks = chunks[:-1]
    entries = []
    for chunk in chunks:
        token = chunk.decode("utf-8", errors="replace")
        if token.startswith("/work/"):
            last_component = token.rsplit("/", 1)[-1]
            if last_component.lower() == ".git":
                entries.append(token)
    return entries


def nested_roots(entries: list[str]) -> list[str]:
    """Extract parent directories from entries with at least two components.

    For every entry with at least two "/"-separated components, take the parent
    directory. Deduplicate and sort by (descending number of components, then name).

    Examples:
        ["a/.git", "a/b/.git", "c/.git", ".git"] -> ["a/b", "a", "c"]
    """
    roots = []
    seen = set()
    for entry in entries:
        # Entry looks like "path/to/.git" - need at least two components
        parts = entry.split("/")
        if len(parts) >= 2:
            # Remove ".git" suffix and get parent path
            parent = "/".join(parts[:-1])  # everything except the last component (.git)
            if parent:  # only include non-empty parents
                if parent not in seen:
                    roots.append(parent)
                    seen.add(parent)

    # Sort by (descending number of components, then name)
    def sort_key(root: str) -> tuple:
        components = root.count("/")
        return (-components, root)

    roots.sort(key=sort_key)
    return roots


def children(root: str, roots: list[str]) -> list[str]:
    """Return immediate nested roots relative to root.

    Every R2 in roots with R2.startswith(root + "/") for which NO other R3
    in roots satisfies both R3.startswith(root + "/") and R2.startswith(R3 + "/").
    Returned relative to root (strip root + "/"), in roots list's order.
    """
    prefix = root + "/"
    # Filter roots that start with root + "/"
    candidates = [r for r in roots if r.startswith(prefix)]

    # For each candidate, check if it has an ancestor in roots
    result = []
    for candidate in candidates:
        # Check if there's any R3 that makes this an intermediate node
        has_ancestor = False
        for r3 in roots:
            if r3.startswith(prefix) and r3 != candidate:
                # Check if R2 starts with R3 + "/"
                if candidate.startswith(r3 + "/"):
                    has_ancestor = True
                    break
        if not has_ancestor:
            # Return relative to root
            result.append(candidate[len(prefix):])

    return result


def top_level_roots(roots: list[str]) -> list[str]:
    """Return roots with no ancestor in the set, in roots list's order."""
    root_set = set(roots)
    result = []
    for root in roots:
        # Check if this root has an ancestor in the set
        has_ancestor = False
        parts = root.split("/")
        # Check all possible ancestors (parent, grandparent, etc.)
        for i in range(1, len(parts)):
            ancestor = "/".join(parts[:i])
            if ancestor in root_set:
                has_ancestor = True
                break
        if not has_ancestor:
            result.append(root)
    return result


@dataclass
class ExportReport:
    files: int
    bytes: int
    escaping_symlinks: list


class _CountingReader:
    """Wraps the raw archive stream and raises ExportError as soon as more
    than max_bytes total have been READ from it — bounds the stream itself,
    not just the sum of each member's declared size (a hostile tar could
    lie about sizes in the header)."""

    def __init__(self, stream, max_bytes: int):
        self._stream = stream
        self._max_bytes = max_bytes
        self._read = 0

    def read(self, n=-1):
        chunk = self._stream.read(n)
        self._read += len(chunk)
        if self._read > self._max_bytes:
            raise ExportError(f"export archive exceeds {self._max_bytes} bytes")
        return chunk


def _cleanup_to_dot_git_only(dest: Path) -> None:
    for entry in dest.iterdir():
        if entry.name == ".git" and entry.is_file() and not entry.is_symlink():
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            try:
                entry.unlink()
            except OSError:
                pass


def worktree_is_pristine(path: Path) -> bool:
    """True when `path` holds exactly one entry, the linked-worktree `.git` FILE —
    the only state the export may extract into (anything else is work on disk we
    must not overwrite). Raises OSError if the directory cannot be listed."""
    existing = list(Path(path).iterdir())
    return len(existing) == 1 and existing[0].name == ".git" and existing[0].is_file()


def extract_validated(stream, dest: Path, *, max_files: int, max_bytes: int) -> ExportReport:
    dest = Path(dest)
    if not worktree_is_pristine(dest):
        raise ExportError("worktree not empty")

    dest_real = os.path.realpath(str(dest))
    counting = _CountingReader(stream, max_bytes)
    files = 0
    total_bytes = 0
    escaping_symlinks = []

    try:
        with tarfile.open(fileobj=counting, mode="r|") as tar:
            for member in tar:
                if tar.pax_headers:
                    raise ExportError(_PAX_GLOBAL_MSG)

                files += 1
                if files > max_files:
                    raise ExportError(f"export archive exceeds {max_files} files")
                total_bytes += max(member.size, 0)
                if total_bytes > max_bytes:
                    raise ExportError(f"export archive exceeds {max_bytes} bytes")

                if not (member.isreg() or member.isdir() or member.issym()):
                    raise ExportError(
                        f"export archive contains a disallowed member type at "
                        f"'{member.name}' (only regular files, directories, and "
                        f"symlinks are allowed)"
                    )

                name = member.name
                if posixpath.isabs(name):
                    raise ExportError(f"export archive contains an absolute path '{name}'")
                parts = name.split("/")
                if any(p in ("", ".", "..") for p in parts):
                    raise ExportError(
                        f"export archive contains an invalid path component in '{name}'"
                    )
                if any(p.lower() == ".git" for p in parts):
                    raise ExportError(f"export archive contains a .git-named entry '{name}'")

                target_path = dest / name
                target_real = os.path.realpath(str(target_path))
                if not (target_real == dest_real or target_real.startswith(dest_real + os.sep)):
                    raise ExportError(
                        f"export archive member '{name}' escapes the destination "
                        f"via a symlink created by an earlier member"
                    )

                if member.isdir():
                    os.makedirs(str(target_path), exist_ok=True)
                    os.chmod(str(target_path), 0o755)
                elif member.isreg():
                    os.makedirs(str(target_path.parent), exist_ok=True)
                    fh = tar.extractfile(member)
                    if fh is None:
                        raise ExportError(f"export archive member '{name}' has no content stream")
                    fd = os.open(str(target_path),
                                 os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
                    try:
                        with os.fdopen(fd, "wb") as out:
                            while True:
                                chunk = fh.read(65536)
                                if not chunk:
                                    break
                                out.write(chunk)
                    finally:
                        fh.close()
                    mode = 0o755 if (member.mode & 0o111) else 0o644
                    os.chmod(str(target_path), mode)
                elif member.issym():
                    os.makedirs(str(target_path.parent), exist_ok=True)
                    if sys.platform == "win32":
                        with open(target_path, "w", encoding="utf-8") as fh:
                            fh.write(member.linkname)
                    else:
                        os.symlink(member.linkname, str(target_path))
                        link_target = member.linkname
                        normalized = posixpath.normpath(
                            posixpath.join(posixpath.dirname(name), link_target)
                        )
                        if (posixpath.isabs(link_target) or normalized == ".."
                                or normalized.startswith("../")):
                            escaping_symlinks.append(name)
            if tar.pax_headers:  # defense in depth: a trailing global header (tarfile itself normally refuses one)
                raise ExportError(_PAX_GLOBAL_MSG)

    except ExportError:
        _cleanup_to_dot_git_only(dest)
        raise
    except (tarfile.TarError, OSError) as e:
        _cleanup_to_dot_git_only(dest)
        raise ExportError(f"export extraction failed: {e}")

    return ExportReport(files=files, bytes=total_bytes, escaping_symlinks=escaping_symlinks)


def export_run(cfg, *, slug, base_commit, worktree: Path, run_dir: Path, objects_dir: Path,
               image_ref: str, uid: int, gid: int, repo_label: str, run=docker_cli.run,
               popen=subprocess.Popen) -> RunArtifacts:
    """Spec §7: the whole export flow, run against a FRESH container (never
    the worker's own). Any ExportError leaves the worktree cleaned back to
    just the .git file and the volume intact (`runs export <slug>` can
    retry after the operator raises a limit)."""
    diff_stat = ""
    patch_path = None
    dropped_git_entries: list = []
    escaping_symlinks: list = []
    files_changed: list = []
    files_changed_truncated = False
    worktree_bytes = None
    worktree_files = None

    if not worktree_is_pristine(worktree):
        return RunArtifacts(export_status="export_failed: worktree not empty")

    name = f"{docker_args.container_name(slug)}-export"

    create_argv = docker_args.export_create_argv(cfg, slug, image_ref, uid, gid, objects_dir,
                                                  repo_label=repo_label)
    try:
        created = run(create_argv, timeout=docker_cli.T_LIFECYCLE)
    except docker_cli.DockerError as e:
        return RunArtifacts(export_status=f"export_failed: docker create {name} failed: {e}")
    if created.returncode != 0:
        return RunArtifacts(
            export_status=f"export_failed: docker create {name} failed: "
                           f"{created.output.decode('utf-8', 'replace')[:500]}"
        )

    try:
        tether = popen(["docker", "start", "-ai", name],
                        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        # _cleanup/_fail don't exist yet (they close over `tether`) — this
        # is the one export step that can fail before there is a tether to
        # close, so it gets its own best-effort teardown: just the export
        # container this function itself created.
        try:
            run(["rm", "-f", name], timeout=docker_cli.T_LIFECYCLE)
        except docker_cli.DockerError:
            pass
        return RunArtifacts(export_status=f"export_failed: cannot start export tether: {e}")

    def _cleanup(keep_volume: bool) -> None:
        try:
            run(["rm", "-f", name], timeout=docker_cli.T_LIFECYCLE)
        except docker_cli.DockerError:
            pass
        lifecycle.close_tether(tether)
        if not keep_volume:
            try:
                run(["volume", "rm", docker_args.volume_name(slug)], timeout=docker_cli.T_QUERY)
            except docker_cli.DockerError:
                pass

    def _fail(reason: str) -> RunArtifacts:
        _cleanup(keep_volume=True)  # export_failed always keeps the volume for retry
        _cleanup_to_dot_git_only(worktree)
        return RunArtifacts(
            diff_stat=diff_stat, patch_path=patch_path,
            worktree_bytes=worktree_bytes, worktree_files=worktree_files,
            escaping_symlinks=escaping_symlinks, dropped_git_entries=dropped_git_entries,
            files_changed=files_changed, files_changed_truncated=files_changed_truncated,
            export_status=f"export_failed: {reason}",
        )

    try:
        lifecycle.wait_ready(run, name)

        lifecycle.init_worker_git(run, name, branch=f"dirtywork/{slug}", base_commit=base_commit, restart=True, layout="env")

        find_argv = docker_args.exec_argv(
            name, ["/bin/sh", "-c", EXPORT_GIT_ENTRIES_SCRIPT]
        )
        find_captured = run(find_argv, timeout=docker_cli.T_EXPORT_STEP)

        # Parse the NUL-separated entries
        if find_captured.truncated:
            return _fail("could not enumerate .git entries")

        parsed_entries = parse_git_entries(find_captured.output)

        if find_captured.returncode != 0:
            print(f"export: .git enumeration incomplete (rc {find_captured.returncode})", file=sys.stderr)

        # dropped_git_entries = each token with the "/work/" prefix removed, in find order
        dropped_git_entries = [e[len("/work/"):] for e in parsed_entries if e.startswith("/work/")]

        roots = nested_roots(dropped_git_entries)

        add_argv = docker_args.exec_argv(name, ["/usr/bin/git", "add", "-A"])
        add_captured = run(add_argv, timeout=docker_cli.T_EXPORT_STEP)
        if add_captured.returncode != 0:
            return _fail(f"git add -A failed: {add_captured.output.decode('utf-8', 'replace')[:500]}")

        # Spec §2: the file list, read from the index the `git add -A` above just
        # built, INSIDE the container — the same rule diff_stat follows, so no
        # host git ever touches worker content. A failure here is not fatal:
        # this is evidence for the orchestrator, not a correctness gate.
        names_argv = docker_args.exec_argv(
            name, ["/usr/bin/git", "diff", "--cached", "--name-only", base_commit])
        names_captured = run(names_argv, timeout=docker_cli.T_EXPORT_STEP)
        if names_captured.returncode == 0:
            ordered = sorted({
                line.strip()
                for line in names_captured.output.decode("utf-8", errors="replace").splitlines()
                if line.strip()
            })
            files_changed = ordered[:MAX_FILES_CHANGED]
            files_changed_truncated = len(ordered) > MAX_FILES_CHANGED

        wt_argv = docker_args.exec_argv(name, ["/usr/bin/git", "write-tree"])
        wt_captured = run(wt_argv, timeout=docker_cli.T_EXPORT_STEP)
        if wt_captured.returncode != 0:
            return _fail(f"git write-tree failed: {wt_captured.output.decode('utf-8', 'replace')[:500]}")
        tree = wt_captured.output.decode("utf-8", errors="replace").strip()

        stat_argv = docker_args.exec_argv(name, ["/usr/bin/git", "diff", "--stat", base_commit, tree])
        stat_captured = run(stat_argv, timeout=docker_cli.T_EXPORT_STEP)
        if stat_captured.returncode != 0:
            return _fail(
                f"git diff --stat failed: {stat_captured.output.decode('utf-8', 'replace')[:500]}"
            )
        raw_stat = stat_captured.output.decode("utf-8", errors="replace")
        diff_stat = raw_stat if len(raw_stat) <= 64_000 else raw_stat[:64_000] + "\n[diff_stat truncated at 64000 chars]"

        patch_target = run_dir / "diff.patch"
        diff_argv = ["docker"] + docker_args.exec_argv(name, ["/usr/bin/git", "diff", base_commit, tree])
        max_patch_bytes = cfg.max_patch_mb * 1024 * 1024
        diff_proc = popen(diff_argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        # Streamed steps cannot rely on run()'s timeout: a hung `docker exec` would
        # block .read() forever. A kill-timer bounds each streamed step at
        # T_EXPORT_STEP so the export fails closed like every other docker call.
        diff_timer = threading.Timer(docker_cli.T_EXPORT_STEP, diff_proc.kill)
        diff_timer.start()
        fd = os.open(str(patch_target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as out:
                written = 0
                truncated_patch = False
                while True:
                    chunk = diff_proc.stdout.read(65536)
                    if not chunk:
                        break
                    if written < max_patch_bytes:
                        room = max_patch_bytes - written
                        piece = chunk[:room]
                        out.write(piece)
                        written += len(piece)
                        if len(chunk) > room:
                            truncated_patch = True
                    else:
                        truncated_patch = True
                if truncated_patch:
                    out.write(f"\n[patch truncated at {cfg.max_patch_mb} MB]\n".encode("utf-8"))
        finally:
            diff_timer.cancel()
            try:
                diff_proc.wait(timeout=10)
            except Exception:
                diff_proc.kill()
        if diff_proc.returncode != 0:
            return _fail(f"git diff failed or timed out (rc {diff_proc.returncode})")
        patch_path = str(patch_target)

        archive_argv = ["docker"] + docker_args.exec_argv(
            name, ["/usr/bin/git", "archive", "--format=tar", tree]
        )
        archive_proc = popen(archive_argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        archive_timer = threading.Timer(docker_cli.T_EXPORT_STEP, archive_proc.kill)
        archive_timer.start()
        try:
            report = extract_validated(archive_proc.stdout, worktree,
                                        max_files=cfg.max_worktree_files,
                                        max_bytes=cfg.max_worktree_mb * 1024 * 1024)
        except ExportError as e:
            archive_timer.cancel()
            archive_proc.kill()  # never wait on a process that may still be streaming
            try:
                archive_proc.wait(timeout=10)
            except Exception:
                pass
            return _fail(str(e))
        finally:
            archive_timer.cancel()
        try:
            archive_proc.wait(timeout=10)
        except Exception:
            archive_proc.kill()
        if archive_proc.returncode != 0:
            # the stream ended cleanly from tarfile's point of view but git archive
            # itself failed or was killed by the timer: the tree may be incomplete
            return _fail(f"git archive failed or timed out (rc {archive_proc.returncode})")
        worktree_bytes = report.bytes
        worktree_files = report.files
        escaping_symlinks = report.escaping_symlinks

        _cleanup(keep_volume=cfg.keep_volume)

    except (SandboxError, OSError) as e:
        # OSError alongside SandboxError: popen()/os.open()/file writes in
        # the steps above are raw OS calls with no docker_cli wrapper to
        # turn a failure into a DockerError — an unwrapped OSError here
        # must still route through _fail (container removed, volume kept
        # for retry, worktree cleaned back to .git only) rather than
        # propagate and skip cleanup entirely.
        return _fail(f"docker step failed: {e}")

    return RunArtifacts(
        diff_stat=diff_stat, patch_path=patch_path,
        worktree_bytes=worktree_bytes, worktree_files=worktree_files,
        escaping_symlinks=escaping_symlinks, dropped_git_entries=dropped_git_entries,
        files_changed=files_changed, files_changed_truncated=files_changed_truncated,
        export_status="ok",
    )
