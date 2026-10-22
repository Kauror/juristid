"""What an operator approves is what gets applied: bytes, decisions and plan, as one digest.

A reviewed import is only as strong as the thing the reviewer's digest covers.
The evidence pilot's package digest covered ``package.json`` and nothing else;
the historical import compared a manifest *file* with a claim *about* that file;
the register import printed the workbook's hash and then rebuilt a fresh plan
from whatever the disk and the database held at apply time (docs/adr/0184).

Three pieces live here, and every reviewed importer uses the same three:

**A source manifest.** Every file of a source tree — normalised relative path,
size, SHA-256 — read through `safe_paths` (no links, no special files), bounded
by `limits` (files, directory members, bytes), and refusing two names that are
one file on a case-insensitive or normalising file system. Its digest is over
canonical JSON, so it does not depend on the order the disk lists files in.

**Verified reads.** A reader handed a manifest checks every file it opens
against it: the bytes it parses are the bytes that were hashed, a file the
manifest does not name is refused, and one it names that has gone is refused.
That closes the gap between "the tree was hashed" and "the tree was read".

**A canonical plan digest.** `canonical_sha256` over the resolved plan — what
will be written, against which database rows — with sorted keys and no display
formatting. Model instances enter as their label and primary key, so the
digest changes when the database a plan resolved against changes.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import decimal
import enum
import hashlib
import json
import re
import unicodedata
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any

from app.legacy_import import safe_paths
from app.legacy_import.limits import SourceLimitExceeded, SourceLimits, WorkBudget, current_limits

MANIFEST_VERSION = "juristid.source-manifest/1"

_SHA256 = re.compile(r"[0-9a-f]{64}")


class IntegrityError(RuntimeError):
    """A source is not the one that was approved, or cannot be identified as one."""


# ---------------------------------------------------------------------------
# Canonical JSON
# ---------------------------------------------------------------------------


def _plain(value: Any) -> Any:
    """The JSON value a digest is taken over. Anything unexpected is an error."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, enum.Enum):
        return _plain(value.value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (decimal.Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, PurePath):
        return value.as_posix()
    meta = getattr(value, "_meta", None)
    if meta is not None and hasattr(value, "pk"):
        # A database row enters as its identity: the plan resolved against
        # *this* row, and a different row (or none) is a different plan.
        return {"model": meta.label, "pk": None if value.pk is None else str(value.pk)}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((_plain(item) for item in value), key=canonical_text)
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    raise TypeError(f"{type(value).__name__} has no canonical form for a plan digest.")


def canonical_text(value: Any) -> str:
    """Canonical JSON: sorted keys, no whitespace, UTF-8, NaN refused."""
    return json.dumps(
        _plain(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_text(value).encode("utf-8")).hexdigest()


def require_digest(actual: str, expected: str, *, flag: str, what: str) -> str:
    """Refuse unless `expected` is exactly `actual`. Empty is refused, never defaulted."""
    expected = (expected or "").strip().lower()
    if not expected:
        raise IntegrityError(
            f"{what} needs {flag} from a reviewed plan run. The current digest is {actual}."
        )
    if expected != actual:
        raise IntegrityError(
            f"{what} changed since it was approved ({expected[:16]}… → {actual[:16]}…). "
            "Nothing was written; plan again and review it."
        )
    return actual


def hold_tables(*models: Any) -> None:
    """Keep other writers off these tables until the current transaction ends.

    ``SHARE ROW EXCLUSIVE``: readers pass, other writers wait, and this
    transaction may still write. A reviewed apply takes it before rebuilding
    its plan, so the rows the digest names are the rows it writes against.
    """
    from django.db import connection

    with connection.cursor() as cursor:
        for model in models:
            cursor.execute(f'LOCK TABLE "{model._meta.db_table}" IN SHARE ROW EXCLUSIVE MODE')


# ---------------------------------------------------------------------------
# Manifests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    size: int
    sha256: str


def alias_key(relative: str) -> str:
    """The name a case-insensitive, normalising file system would see."""
    return unicodedata.normalize("NFC", relative).casefold()


class SourceManifest:
    """Every file of one source tree, by its relative POSIX path."""

    def __init__(self, entries: Iterable[ManifestEntry]) -> None:
        self.entries: dict[str, ManifestEntry] = {}
        aliases: dict[str, str] = {}
        for entry in entries:
            safe_paths.check_relative(entry.path)
            other = aliases.setdefault(alias_key(entry.path), entry.path)
            if other != entry.path or entry.path in self.entries:
                raise IntegrityError(
                    f"Two source files are one name on a case-insensitive or normalising "
                    f"file system: {other!r} and {entry.path!r}."
                )
            self.entries[entry.path] = entry

    def __len__(self) -> int:
        return len(self.entries)

    def __contains__(self, relative: object) -> bool:
        return relative in self.entries

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries.values())

    def rows(self) -> list[list[Any]]:
        return [[e.path, e.size, e.sha256] for _, e in sorted(self.entries.items())]

    def digest(self) -> str:
        return canonical_sha256({"version": MANIFEST_VERSION, "files": self.rows()})

    def as_tsv(self) -> str:
        """``path<TAB>size<TAB>sha256`` per file, sorted, LF, for an operator's record."""
        return "".join(f"{path}\t{size}\t{sha}\n" for path, size, sha in self.rows())

    def subtree(self, prefix: str) -> SourceManifest:
        """The entries under `prefix`, with the prefix removed."""
        head = prefix.rstrip("/") + "/"
        return SourceManifest(
            ManifestEntry(path[len(head) :], entry.size, entry.sha256)
            for path, entry in self.entries.items()
            if path.startswith(head)
        )

    def verify(self, relative: str, data: bytes) -> bytes:
        """`data` if it is exactly the approved bytes of `relative`; refused otherwise."""
        entry = self.entries.get(relative)
        if entry is None:
            raise IntegrityError(f"{relative} is not in the approved source manifest.")
        if len(data) != entry.size or hashlib.sha256(data).hexdigest() != entry.sha256:
            raise IntegrityError(f"{relative} changed after the source manifest was taken.")
        return data

    def refuse_absence(self, relative: str) -> None:
        """Called when `relative` is not on disk: refused if the approval listed it."""
        if relative in self.entries:
            raise IntegrityError(f"{relative} is in the approved manifest but no longer on disk.")


def build_manifest(
    root: Path,
    *,
    limits: SourceLimits | None = None,
    budget: WorkBudget | None = None,
) -> SourceManifest:
    """Hash every file under `root`, within the run's limits. Reads, never writes."""
    limits = limits or current_limits()
    root = Path(root)
    entries: list[ManifestEntry] = []
    total = 0
    for path in safe_paths.iter_files(
        root,
        max_files=limits.max_source_files,
        max_directory_members=limits.max_directory_members,
    ):
        relative = path.relative_to(root).as_posix()
        digest = hashlib.sha256()
        size = 0
        with safe_paths.open_file(root, relative) as handle:
            if budget is not None:
                budget.open_file()
            for block in safe_paths.stream_chunks(handle):
                size += len(block)
                if size > limits.max_member_bytes:
                    raise SourceLimitExceeded(
                        f"{relative} is larger than {limits.max_member_bytes} bytes; "
                        f"the limit is {limits.max_member_bytes}."
                    )
                if budget is not None:
                    budget.charge(len(block))
                digest.update(block)
        total += size
        if total > limits.max_total_source_bytes:
            raise SourceLimitExceeded(
                f"{root.name} holds more than {limits.max_total_source_bytes} bytes; "
                f"the limit is {limits.max_total_source_bytes}."
            )
        entries.append(ManifestEntry(relative, size, digest.hexdigest()))
    return SourceManifest(entries)


def parse_manifest_tsv(text: str, *, what: str) -> SourceManifest:
    """A ``path<TAB>size<TAB>sha256`` manifest someone else wrote, read strictly.

    Backslashes become separators (the audit ran on Windows); the path is then
    held to the same rule as every other source path, so a claimed ``..``
    cannot make an unlisted file look listed.
    """
    entries: list[ManifestEntry] = []
    for number, line in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise IntegrityError(f"{what} line {number} is not path, size and SHA-256.")
        path, size, sha = parts
        sha = sha.strip().lower()
        if not size.strip().isdigit() or not _SHA256.fullmatch(sha):
            raise IntegrityError(f"{what} line {number} is not path, size and SHA-256.")
        try:
            relative = safe_paths.check_relative(path.replace("\\", "/"))
        except safe_paths.UnsafeSourcePath as error:
            raise IntegrityError(f"{what} line {number}: {error}") from error
        entries.append(ManifestEntry(relative, int(size), sha))
    return SourceManifest(entries)


def manifest_differences(actual: SourceManifest, claimed: SourceManifest) -> list[str]:
    """Where the tree on disk and a claimed manifest disagree, file by file."""
    problems: list[str] = []
    for path in sorted(set(claimed.entries) - set(actual.entries)):
        problems.append(f"missing: {path}")
    for path in sorted(set(actual.entries) - set(claimed.entries)):
        problems.append(f"not in the manifest: {path}")
    for path in sorted(set(actual.entries) & set(claimed.entries)):
        if actual.entries[path] != claimed.entries[path]:
            problems.append(f"changed: {path}")
    return problems
