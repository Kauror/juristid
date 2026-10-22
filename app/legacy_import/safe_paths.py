"""Opening a file a historical source *names*, without letting the name decide where.

The OneNote archive and the opinions directory are evidence, and so is every
path inside them: ``captureId``, ``archivePageKey`` and ``originalPath`` are
strings somebody's exporter wrote into JSON, and a directory input is whatever
the folder holds today. Joining such a string to the archive root and calling
``read_bytes`` reads whatever it points at — ``../../../etc/passwd``, or a
symbolic link planted inside the archive pointing out of it — and the bytes
then become a document with the archive's provenance on it (docs/adr/0178).

So every read of a source file goes through here, and three things hold:

**The path is a plain relative POSIX path.** Not absolute, no drive letter or
UNC prefix, no backslash, no empty, ``.`` or ``..`` segment, no control
character, and no segment that only *becomes* one of those under Unicode
compatibility folding or Windows' trailing-dot rule. The string is never
normalised into acceptability: a name is used exactly as written, so the
Estonian names in the real archive — NFC or NFD — open as the bytes they are.

**Nothing under the root is a link or a special file.** Every intermediate
directory and the final file are checked without following links; a symbolic
link (or a Windows junction) anywhere below the root, or a FIFO, socket or
device as the final component, is refused rather than skipped. The root itself
is the operator's choice and may be a link to where the corpus is mounted.

**The file opened is the file checked.** On POSIX the walk is done with
``dir_fd`` and ``O_NOFOLLOW`` one component at a time, so no component can be
swapped for a link between the check and the open; the opened descriptor is
then ``fstat``-ed and must be the same regular file (device and inode) the
check saw. Where the platform has neither (Windows), the same rules are kept
with ``lstat`` before the open, the same identity comparison after it, a
second look at every intermediate directory, and a final containment check of
the resolved path — weaker against a deliberate race, which the production and
CI hosts (Linux) never rely on.
"""

from __future__ import annotations

import errno
import os
import re
import stat
import unicodedata
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from app.legacy_import.limits import SourceLimitExceeded, current_limits

if TYPE_CHECKING:
    from app.legacy_import.limits import WorkBudget

#: A Windows drive prefix — ``C:``, ``C:/x``, ``C:x``.
_DRIVE = re.compile(r"^[A-Za-z]:")

#: The name-surrogate bit of a Windows reparse tag: set for symbolic links and
#: junctions, clear for cloud placeholders and deduplicated files, which are
#: ordinary files as far as their bytes are concerned.
_NAME_SURROGATE = 0x20000000

_CHUNK = 1024 * 1024

#: Whether this host can walk with ``dir_fd`` and refuse links atomically.
_O_NOFOLLOW: int = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY: int = getattr(os, "O_DIRECTORY", 0)
_O_CLOEXEC: int = getattr(os, "O_CLOEXEC", 0)

#: Whether this host can walk with ``dir_fd`` and refuse links atomically.
STRONG_WALK = (
    os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and bool(_O_NOFOLLOW)
    and bool(_O_DIRECTORY)
)


class UnsafeSourcePath(RuntimeError):
    """A source path, or what it names on disk, is refused."""


class Kind(Enum):
    MISSING = "missing"
    FILE = "file"
    DIRECTORY = "directory"


def _refuse(raw: object, reason: str) -> UnsafeSourcePath:
    return UnsafeSourcePath(f"Refusing an unsafe source path {raw!r}: {reason}.")


def check_segment(segment: str, *, raw: object | None = None) -> str:
    """One path component, refused unless it can only mean itself."""
    shown = segment if raw is None else raw
    if segment in ("", ".", ".."):
        raise _refuse(shown, "empty, '.' or '..' segment")
    if "/" in segment or "\\" in segment:
        raise _refuse(shown, "a separator inside a name")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in segment):
        # C0 and DEL only. C1 code points are left alone on purpose: the real
        # opinions archive carries cp437-as-cp1252 mojibake such as ``pr<U+0081>gila``
        # in its names, and those names are evidence, not attacks.
        raise _refuse(shown, "a control character")
    if segment != segment.rstrip(" ."):
        # Windows drops trailing dots and spaces, so `x.` and `x` are one file
        # there and two on Linux: one name, two meanings.
        raise _refuse(shown, "a trailing dot or space")
    if ":" in segment:
        # A drive on Windows when first, an alternate data stream after it.
        raise _refuse(shown, "a colon")
    folded = unicodedata.normalize("NFKC", segment)
    if folded in (".", "..") or "/" in folded or "\\" in folded:
        raise _refuse(shown, "a segment that folds to a separator or a dot segment")
    return segment


def relative_parts(raw: object) -> tuple[str, ...]:
    """Validate a relative POSIX path and return its segments, unchanged."""
    if not isinstance(raw, str) or not raw:
        raise _refuse(raw, "empty")
    if "\x00" in raw:
        raise _refuse(raw, "a NUL character")
    if "\\" in raw:
        raise _refuse(raw, "a backslash")
    if raw.startswith("/"):
        raise _refuse(raw, "an absolute path")
    if _DRIVE.match(raw):
        raise _refuse(raw, "a drive prefix")
    return tuple(check_segment(part, raw=raw) for part in raw.split("/"))


def check_relative(raw: object) -> str:
    """`relative_parts`, returning the path itself."""
    return "/".join(relative_parts(raw))


# ---------------------------------------------------------------------------
# What the file system holds
# ---------------------------------------------------------------------------


def _is_link(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_reparse_tag", 0) & _NAME_SURROGATE)


def _same(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _kind_of(info: os.stat_result, raw: str) -> Kind:
    if _is_link(info):
        raise _refuse(raw, "a symbolic link inside the source")
    if stat.S_ISREG(info.st_mode):
        return Kind.FILE
    if stat.S_ISDIR(info.st_mode):
        return Kind.DIRECTORY
    raise _refuse(raw, "not a regular file (FIFO, socket or device)")


def _open_root(root: Path) -> int:
    flags = os.O_RDONLY | _O_DIRECTORY | _O_CLOEXEC
    return os.open(root, flags)


def _descend(directory: int, segment: str, raw: str) -> int | None:
    """The next directory down, opened without following a link; None if absent."""
    flags = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
    try:
        return os.open(segment, flags, dir_fd=directory)
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno not in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
            raise
    info = os.stat(segment, dir_fd=directory, follow_symlinks=False)
    if _is_link(info):
        raise _refuse(raw, "a symbolic link inside the source")
    return None  # a file where a directory was expected: nothing is there


def _walk_strong(root: Path, parts: tuple[str, ...], raw: str) -> tuple[int, os.stat_result] | None:
    """(fd of the parent directory, lstat of the final name), or None if absent."""
    directory = _open_root(root)
    try:
        for segment in parts[:-1]:
            child = _descend(directory, segment, raw)
            if child is None:
                os.close(directory)
                return None
            os.close(directory)
            directory = child
        try:
            info = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        except (FileNotFoundError, NotADirectoryError):
            os.close(directory)
            return None
    except BaseException:
        os.close(directory)
        raise
    return directory, info


def _walk_fallback(
    root: Path, parts: tuple[str, ...], raw: str
) -> tuple[Path, os.stat_result, list[tuple[Path, os.stat_result]]] | None:
    """The lstat walk for hosts without ``dir_fd``. None if anything is absent."""
    current = Path(root)
    seen: list[tuple[Path, os.stat_result]] = []
    for segment in parts[:-1]:
        current = current / segment
        try:
            info = os.lstat(current)
        except (FileNotFoundError, NotADirectoryError):
            return None
        if _is_link(info):
            raise _refuse(raw, "a symbolic link inside the source")
        if not stat.S_ISDIR(info.st_mode):
            return None
        seen.append((current, info))
    final = current / parts[-1]
    try:
        info = os.lstat(final)
    except (FileNotFoundError, NotADirectoryError):
        return None
    return final, info, seen


def lookup(root: Path, relative: str) -> Kind:
    """What `relative` names under `root`: a file, a directory, or nothing.

    Refuses — never reports MISSING — when a link or a special file is in the
    way, so a caller that skips missing files cannot be steered past one.
    """
    parts = relative_parts(relative)
    if STRONG_WALK:
        walked = _walk_strong(root, parts, relative)
        if walked is None:
            return Kind.MISSING
        directory, info = walked
        os.close(directory)
        return _kind_of(info, relative)
    found = _walk_fallback(root, parts, relative)
    if found is None:
        return Kind.MISSING
    return _kind_of(found[1], relative)


def _changed(raw: str) -> UnsafeSourcePath:
    return _refuse(raw, "the file changed between the check and the open")


def open_file(root: Path, relative: str) -> BinaryIO:
    """Open a regular file under `root` for reading. FileNotFoundError if absent."""
    parts = relative_parts(relative)
    nonblock = getattr(os, "O_NONBLOCK", 0)  # a FIFO swapped in must not block the open
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | _O_CLOEXEC | nonblock
    if STRONG_WALK:
        walked = _walk_strong(root, parts, relative)
        if walked is None:
            raise FileNotFoundError(errno.ENOENT, "No such source file", relative)
        directory, before = walked
        try:
            if _kind_of(before, relative) is not Kind.FILE:
                raise _refuse(relative, "a directory, not a file")
            try:
                fd = os.open(parts[-1], flags | _O_NOFOLLOW, dir_fd=directory)
            except OSError as error:
                if error.errno in (errno.ELOOP, errno.EMLINK):
                    raise _changed(relative) from error
                raise
        finally:
            os.close(directory)
    else:
        found = _walk_fallback(root, parts, relative)
        if found is None:
            raise FileNotFoundError(errno.ENOENT, "No such source file", relative)
        final, before, seen = found
        if _kind_of(before, relative) is not Kind.FILE:
            raise _refuse(relative, "a directory, not a file")
        try:
            fd = os.open(final, flags | _O_NOFOLLOW)
        except OSError as error:
            if error.errno in (errno.ELOOP, errno.EMLINK):
                raise _changed(relative) from error
            raise
    try:
        after = os.fstat(fd)
        if not stat.S_ISREG(after.st_mode) or not _same(before, after):
            raise _changed(relative)
        if not STRONG_WALK:
            # Without dir_fd an intermediate directory could have been swapped
            # for a link after it was checked: look again, and confirm the
            # resolved file still lives under the root.
            for path, info in seen:
                again = os.lstat(path)
                if _is_link(again) or not _same(info, again):
                    raise _changed(relative)
            resolved = os.path.realpath(final)
            base = os.path.realpath(root)
            if os.path.commonpath([os.path.normcase(resolved), os.path.normcase(base)]) != (
                os.path.normcase(base)
            ):
                raise _refuse(relative, "resolves outside the source root")
        if nonblock:
            os.set_blocking(fd, True)
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def read_bytes(
    root: Path,
    relative: str,
    *,
    max_bytes: int | None = None,
    budget: WorkBudget | None = None,
) -> bytes:
    """The whole file, through `open_file`. Refuses past `max_bytes`, never truncates.

    Every byte read is charged to `budget` when one is given, so a run made of
    many acceptable files still stops at the run's own limit (docs/adr/0184).
    """
    parts: list[bytes] = []
    size = 0
    with open_file(root, relative) as handle:
        if budget is not None:
            budget.open_file()
        # Chunked rather than ``read(max_bytes + 1)``, which would allocate the
        # whole limit up front for every file.
        for block in stream_chunks(handle):
            size += len(block)
            if max_bytes is not None and size > max_bytes:
                raise _refuse(relative, f"larger than the {max_bytes}-byte limit")
            if budget is not None:
                budget.charge(len(block))
            parts.append(block)
    return b"".join(parts)


def iter_directory(
    root: Path, directory: Path, *, max_members: int
) -> Iterator[tuple[str, Path, Kind]]:
    """One directory's members as (name, path, kind), refusing at ``max_members + 1``.

    Counted as `os.scandir` yields them and handed on one at a time, so a
    directory of a million entries costs one more entry than the limit — never
    a list of a million to hold or sort. A link or a special file is refused,
    as everywhere below the root.
    """
    with os.scandir(directory) as entries:
        for count, entry in enumerate(entries, start=1):
            if count > max_members:
                raise SourceLimitExceeded(
                    f"{Path(directory).name or directory} holds more than {max_members} "
                    f"entries; the limit is {max_members}."
                )
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            info = entry.stat(follow_symlinks=False)
            yield entry.name, path, _kind_of(info, relative)


def scan_directory(
    root: Path, directory: Path, *, max_members: int
) -> list[tuple[str, Path, Kind]]:
    """`iter_directory` as a list: bounded by `max_members`, so safe to sort."""
    return list(iter_directory(root, directory, max_members=max_members))


def iter_files(
    root: Path,
    *,
    max_files: int | None = None,
    max_directory_members: int | None = None,
) -> Iterator[Path]:
    """Every regular file below `root`, refusing links and special files.

    Absolute paths, each one under `root`; the caller decides the order. A link
    is refused rather than skipped: a directory archive that holds one is not
    the archive that was approved.

    Bounded while it walks (docs/adr/0184): no directory may hold more than
    `max_directory_members` entries and the walk refuses at the
    ``max_files + 1``-th file it meets, so a caller can sort what it receives
    without ever sorting an unbounded list.
    """
    limits = current_limits()
    files_allowed = limits.max_source_files if max_files is None else max_files
    members_allowed = (
        limits.max_directory_members if max_directory_members is None else max_directory_members
    )
    files = 0
    directories = 0
    pending = [Path(root)]
    while pending:
        directory = pending.pop()
        for _name, path, kind in iter_directory(root, directory, max_members=members_allowed):
            if kind is Kind.DIRECTORY:
                # Directories are counted too, against the same number: a tree
                # of empty folders is as much work to walk as one of files.
                directories += 1
                if directories > files_allowed:
                    raise SourceLimitExceeded(
                        f"{Path(root).name or root} holds more than {files_allowed} "
                        f"directories; the limit is {files_allowed}."
                    )
                pending.append(path)
                continue
            files += 1
            if files > files_allowed:
                raise SourceLimitExceeded(
                    f"{Path(root).name or root} holds more than {files_allowed} files; "
                    f"the limit is {files_allowed}."
                )
            yield path


def stream_chunks(handle: BinaryIO, chunk: int = _CHUNK) -> Iterator[bytes]:
    while block := handle.read(chunk):
        yield block
