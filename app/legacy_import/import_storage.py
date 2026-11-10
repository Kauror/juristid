"""Fail-closed storage checks for an import that writes evidence bytes (STORE-GUARD).

Why this exists (10.11.2026): the host's on-demand runner applied a OneNote
capture in a ``docker run --rm`` container that mounted the capture but not the
evidence or legacy-source stores. The importer wrote 40 Documents' bytes and
3 page XMLs into the container's own ``/app/evidence`` and ``/app/legacy-source``
— directories the image creates, so they existed and were writable — and they
vanished with the container. The rows committed, and the request read DONE,
because every check after the write read database rows only.

Three checks, each fail-closed:

1. **Before any write** (`require_import_stores`): each store an import writes to
   must exist and be writable, and — in a process that may hold real data
   (``REQUIRE_PERSISTENT_STORES``, which follows ``REAL_DATA_ALLOWED``) — must
   live on a mount of its own, not on the container's root filesystem and not
   on a memory filesystem. Refused by name (``STORAGE_*``) before a row exists.
2. **Inside each page's transaction** (`verify_stored`): every object a row of
   the page will name is read back through the same storage API and re-hashed;
   a missing or different object rolls the page back.
3. **When a capture is checked** (`onenote_capture.verify`, ``check_bytes``):
   the objects already recorded are read back and counted as
   ``stored_missing`` / ``stored_mismatch``; either makes the check fail.

"A mount of its own" is read from ``/proc/self/mountinfo`` (every Linux
container and host has it): the store's path must fall under a mount point
other than ``/`` whose filesystem type is not a memory one. A bind mount or a
named volume passes; a directory the image baked in, a ``--tmpfs`` mount or an
overlay does not. Where there is no ``mountinfo`` (a developer's Windows or
macOS machine) the nearest ancestor that ``os.path.ismount`` names must not be
the filesystem root. No marker file: the evidence store's integrity check and
``prune_orphaned_evidence`` treat every file in the store as an object, and a
marker would need a deployment step the host does not have today.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.files.storage import storages

logger = logging.getLogger(__name__)

#: Filesystem types that keep nothing across a restart, or are a container's own layer.
# `rootfs`: Unraid's own root lives in RAM, so a store bind-mounted from it keeps nothing.
NOT_PERSISTENT_FSTYPES = frozenset(
    {"tmpfs", "ramfs", "rootfs", "devtmpfs", "overlay", "aufs", "squashfs"}
)

MOUNTINFO = Path("/proc/self/mountinfo")

#: The refusal codes. Each is also a `sync_admin.FAILURE_REASONS` key.
STORAGE_MISSING = "STORAGE_MISSING"
STORAGE_NOT_WRITABLE = "STORAGE_NOT_WRITABLE"
STORAGE_NOT_PERSISTENT = "STORAGE_NOT_PERSISTENT"
STORED_FILES_MISSING = "STORED_FILES_MISSING"

STORAGE_REFUSED = (
    "Refused before writing: the {name} store is {what}. An import writes only to the "
    "deployment's mounted stores (STORE-GUARD)."
)
STORED_FILES_REFUSED = (
    "Stored files failed the read-back: {missing} missing, {mismatch} with other bytes "
    "(of {checked} checked)."
)


class StorageGuardError(Exception):
    """A store an import needs is not one it may write to, or lost what was written.

    ``code`` is the request's outcome code; ``counts`` are integers only (no key,
    no path, no file name), and ``before_writing`` says whether anything was written.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        counts: dict[str, int] | None = None,
        before_writing: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.counts = dict(counts or {})
        self.before_writing = before_writing


# -- 1. before any write ----------------------------------------------------------


def _unescape(field_value: str) -> str:
    """``mountinfo`` escapes space, tab, newline and backslash as ``\\ooo``."""
    for code, char in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        field_value = field_value.replace(code, char)
    return field_value


def parse_mountinfo(text: str) -> list[tuple[str, str]]:
    """``(mount point, filesystem type)`` per line of ``/proc/self/mountinfo``."""
    mounts = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 7 or "-" not in parts[6:]:
            continue
        dash = parts.index("-", 6)
        if dash + 1 >= len(parts):
            continue
        mounts.append((_unescape(parts[4]), parts[dash + 1]))
    return mounts


def _read_mountinfo() -> list[tuple[str, str]] | None:
    try:
        return parse_mountinfo(MOUNTINFO.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None


def containing_mount(path: str, mounts: Iterable[tuple[str, str]]) -> tuple[str, str] | None:
    """The mount ``path`` lives on: the longest mount point it is under (the last one
    listed wins, as the kernel stacks them)."""
    best: tuple[str, str] | None = None
    for point, fstype in mounts:
        prefix = point.rstrip("/") + "/"
        if (path == point or path.startswith(prefix) or point == "/") and (
            best is None or len(point) >= len(best[0])
        ):
            best = (point, fstype)
    return best


#: ``persistence_problem``'s default: read this process's ``mountinfo`` at call time.
READ_MOUNTINFO: Any = object()


def persistence_problem(path: Path, *, mounts: Any = READ_MOUNTINFO) -> str:
    """Why ``path`` is not on a persistent store of its own, or ``""``."""
    table: list[tuple[str, str]] | None = _read_mountinfo() if mounts is READ_MOUNTINFO else mounts
    resolved = Path(os.path.realpath(path))
    if table is not None:
        found = containing_mount(resolved.as_posix(), table)
        if found is None:
            return "on no mount this process can see"
        point, fstype = found
        if point == "/":
            return "on the container's own filesystem, not a mounted store"
        if fstype in NOT_PERSISTENT_FSTYPES:
            return f"on a {fstype} mount, which keeps nothing"
        return ""
    probe = resolved
    while not os.path.ismount(probe) and probe.parent != probe:
        probe = probe.parent
    if probe == Path(resolved.anchor) or probe.parent == probe:
        return "on the machine's root filesystem, not a mounted store"
    return ""


def _is_writable(path: Path) -> bool:
    from app.core.deployment import _is_writable as probe

    return probe(path)


#: Storage alias -> the setting that names its root (for messages only).
IMPORT_STORES = (
    ("EVIDENCE_ROOT", "EVIDENCE_STORAGE_ALIAS"),
    ("LEGACY_SOURCE_ROOT", "LEGACY_SOURCE_STORAGE_ALIAS"),
)


def require_import_stores() -> None:
    """Refuse (``StorageGuardError``, ``before_writing``) unless every store is usable.

    The root checked is the storage backend's own ``location`` — what the import
    writes to — not only the setting that named it. A backend with no local
    location cannot be checked here and is refused where persistence is required.
    """
    persistent = bool(getattr(settings, "REQUIRE_PERSISTENT_STORES", False))
    for name, alias_setting in IMPORT_STORES:
        storage = storages[getattr(settings, alias_setting)]
        location = getattr(storage, "location", None)
        if not location:
            if persistent:
                _refuse(name, STORAGE_NOT_PERSISTENT, "not a local store this check can read")
            continue
        root = Path(location)
        if not root.is_dir():
            _refuse(name, STORAGE_MISSING, "missing")
        if not _is_writable(root):
            _refuse(name, STORAGE_NOT_WRITABLE, "not writable by this process")
        if persistent:
            problem = persistence_problem(root)
            if problem:
                _refuse(name, STORAGE_NOT_PERSISTENT, problem)


def _refuse(name: str, code: str, what: str) -> None:
    message = STORAGE_REFUSED.format(name=name, what=what)
    logger.error("import storage refused: %s %s", code, name)
    raise StorageGuardError(code, message, before_writing=True)


# -- 2. after the writes, before the commit ----------------------------------------


@dataclass(frozen=True)
class StoredObject:
    """One object a row about to commit names. ``ours``: this page wrote it."""

    alias: str
    key: str
    sha256: str
    size: int
    ours: bool


@dataclass
class ReadBack:
    checked: int = 0
    missing: int = 0
    mismatch: int = 0
    #: Which objects failed, for the caller to discard — never shown or stored.
    failed: list[StoredObject] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing or self.mismatch)

    def counts(self) -> dict[str, int]:
        return {
            "stored_checked": self.checked,
            "stored_missing": self.missing,
            "stored_mismatch": self.mismatch,
        }


def hash_stored(storage: Any, key: str) -> tuple[str, int] | None:
    """``(sha256, size)`` of the stored object, or ``None`` when it is not there."""
    try:
        with storage.open(key, "rb") as handle:
            digest = hashlib.sha256()
            size = 0
            while chunk := handle.read(1 << 20):
                digest.update(chunk)
                size += len(chunk)
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return None
    return digest.hexdigest(), size


def read_back(objects: Iterable[StoredObject]) -> ReadBack:
    """Re-open every object through its storage and compare it with what the row says."""
    result = ReadBack()
    for obj in objects:
        result.checked += 1
        got = hash_stored(storages[obj.alias], obj.key)
        if got is None:
            result.missing += 1
            result.failed.append(obj)
        elif got != (obj.sha256, obj.size):
            result.mismatch += 1
            result.failed.append(obj)
    return result


def verify_stored(objects: list[StoredObject]) -> ReadBack:
    """`read_back`, raising ``STORED_FILES_MISSING`` when anything failed."""
    result = read_back(objects)
    if not result.ok:
        logger.error(
            "import storage read-back failed: %s missing=%d mismatch=%d checked=%d",
            STORED_FILES_MISSING,
            result.missing,
            result.mismatch,
            result.checked,
        )
        raise StorageGuardError(
            STORED_FILES_MISSING,
            STORED_FILES_REFUSED.format(
                missing=result.missing, mismatch=result.mismatch, checked=result.checked
            ),
            counts=result.counts(),
        )
    return result


def discard_ours(objects: Iterable[StoredObject]) -> int:
    """Remove the objects this page wrote whose rows rolled back. Best effort, logged."""
    removed = 0
    for obj in objects:
        if not obj.ours:
            continue
        try:
            storages[obj.alias].delete(obj.key)
            removed += 1
        except Exception as error:
            logger.error(
                "could not remove an object whose rows rolled back (%s)", type(error).__name__
            )
    return removed
