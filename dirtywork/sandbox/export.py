from __future__ import annotations

import os
import posixpath
import shutil
import sys
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

from . import SandboxError


class ExportError(SandboxError):
    """Raised by the export flow or the tar validator. export_run (Task 11)
    catches this and turns it into RunArtifacts(export_status=...)."""


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


def extract_validated(stream, dest: Path, *, max_files: int, max_bytes: int) -> ExportReport:
    dest = Path(dest)
    existing = list(dest.iterdir())
    if len(existing) != 1 or existing[0].name != ".git" or not existing[0].is_file():
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
                    raise ExportError("export archive contains a PAX global header")

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
    except ExportError:
        _cleanup_to_dot_git_only(dest)
        raise
    except tarfile.TarError as e:
        _cleanup_to_dot_git_only(dest)
        raise ExportError(f"malformed export archive: {e}")

    return ExportReport(files=files, bytes=total_bytes, escaping_symlinks=escaping_symlinks)
