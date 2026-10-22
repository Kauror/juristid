"""How much of a historical source the importer will read before refusing it.

The legacy readers open an opinions ZIP, an opinions directory, the register
workbook and the KodaDash workbook. Each is an operator-supplied file, and the
attacks that matter are not clever: a 40 KB ZIP that inflates to 40 GB, a
million empty members, a sheet whose one cell sits at ``XFD1048576`` so that a
row loop asks openpyxl for seventeen billion cells. None executes anything;
each turns an import into an outage (docs/adr/0178).

Three rules, the same ones `app.documents.extraction.limits` keeps for uploads:

* **Claims are checked before work, and facts while it happens.** The central
  directory's member count and declared sizes are checked before a byte is
  inflated; then every member is read in chunks and the bytes *actually*
  produced are counted, because a declared size is only a claim.
* **A limit refuses; it never truncates.** A half-read archive catalogued as
  complete is worse than one refused.
* **A limit is a number somebody can change.** Each value can be set in Django
  settings or the environment under its ``LEGACY_IMPORT_*`` name. The defaults
  are far above the real corpus — measured 2026-10-22: the opinions ZIP is
  110 MB with 767 members of at most 1.8 MB and a worst ratio of 2; the
  register workbook is 0.5 MB, 55 parts, 17 sheets, at most 6,358 loaded cells
  per sheet and 5.1 million *declared* (``A1:WVP313``); the KodaDash workbook
  1.7 MB, 35 sheets, 15.6 MB inflated, worst ratio 15.
"""

from __future__ import annotations

import hashlib
import os
import re
import zipfile
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

MiB = 1024 * 1024
GiB = 1024 * MiB

#: Below this a member's compression ratio is not looked at: a 200 KB run of
#: identical XML can compress a thousandfold and still costs nothing to inflate.
RATIO_FLOOR_BYTES = 1 * MiB

_CHUNK = 1024 * 1024

#: ``A1:WVP313`` — the declared used range at the top of a worksheet part.
_DIMENSION = re.compile(rb'<(?:\w+:)?dimension\s+ref="([^"]*)"')
_CELL = re.compile(r"^\$?([A-Z]{1,3})\$?(\d{1,7})$")
#: How much of a worksheet part is read looking for its dimension element. It
#: comes after `sheetPr` and before any row, in the first few hundred bytes.
_DIMENSION_WINDOW = 64 * 1024


class SourceLimitExceeded(RuntimeError):
    """A historical source is larger, or shaped worse, than the importer reads."""


_DEFAULTS: dict[str, int] = {
    # Opinion archives: a ZIP, or a directory of the same files.
    "LEGACY_IMPORT_MAX_ARCHIVE_BYTES": 4 * GiB,
    "LEGACY_IMPORT_MAX_ARCHIVE_MEMBERS": 20_000,
    "LEGACY_IMPORT_MAX_MEMBER_BYTES": 512 * MiB,
    "LEGACY_IMPORT_MAX_TOTAL_UNCOMPRESSED_BYTES": 8 * GiB,
    "LEGACY_IMPORT_MAX_COMPRESSION_RATIO": 200,
    # Workbooks: the register and the KodaDash artifact.
    "LEGACY_IMPORT_MAX_WORKBOOK_BYTES": 200 * MiB,
    "LEGACY_IMPORT_MAX_WORKBOOK_PARTS": 5_000,
    "LEGACY_IMPORT_MAX_WORKBOOK_UNCOMPRESSED_BYTES": 512 * MiB,
    "LEGACY_IMPORT_MAX_WORKSHEETS": 250,
    "LEGACY_IMPORT_MAX_SHEET_ROWS": 200_000,
    "LEGACY_IMPORT_MAX_SHEET_CELLS": 25_000_000,
    "LEGACY_IMPORT_MAX_WORKBOOK_CELLS": 100_000_000,
}


def _configured(name: str) -> int:
    try:
        from django.conf import settings

        if settings.configured and hasattr(settings, name):
            return int(getattr(settings, name))
    except ImportError:  # pragma: no cover - Django is always installed here
        pass
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else _DEFAULTS[name]


@dataclass(frozen=True)
class SourceLimits:
    max_archive_bytes: int
    max_archive_members: int
    max_member_bytes: int
    max_total_uncompressed_bytes: int
    max_compression_ratio: int
    max_workbook_bytes: int
    max_workbook_parts: int
    max_workbook_uncompressed_bytes: int
    max_worksheets: int
    max_sheet_rows: int
    max_sheet_cells: int
    max_workbook_cells: int


def current_limits() -> SourceLimits:
    """The limits in force now. Read per call, so a test or an operator can change them."""
    return SourceLimits(
        max_archive_bytes=_configured("LEGACY_IMPORT_MAX_ARCHIVE_BYTES"),
        max_archive_members=_configured("LEGACY_IMPORT_MAX_ARCHIVE_MEMBERS"),
        max_member_bytes=_configured("LEGACY_IMPORT_MAX_MEMBER_BYTES"),
        max_total_uncompressed_bytes=_configured("LEGACY_IMPORT_MAX_TOTAL_UNCOMPRESSED_BYTES"),
        max_compression_ratio=_configured("LEGACY_IMPORT_MAX_COMPRESSION_RATIO"),
        max_workbook_bytes=_configured("LEGACY_IMPORT_MAX_WORKBOOK_BYTES"),
        max_workbook_parts=_configured("LEGACY_IMPORT_MAX_WORKBOOK_PARTS"),
        max_workbook_uncompressed_bytes=_configured(
            "LEGACY_IMPORT_MAX_WORKBOOK_UNCOMPRESSED_BYTES"
        ),
        max_worksheets=_configured("LEGACY_IMPORT_MAX_WORKSHEETS"),
        max_sheet_rows=_configured("LEGACY_IMPORT_MAX_SHEET_ROWS"),
        max_sheet_cells=_configured("LEGACY_IMPORT_MAX_SHEET_CELLS"),
        max_workbook_cells=_configured("LEGACY_IMPORT_MAX_WORKBOOK_CELLS"),
    )


# ---------------------------------------------------------------------------
# Containers
# ---------------------------------------------------------------------------


def check_file_size(path: Path, maximum: int, *, what: str) -> int:
    size = Path(path).stat().st_size
    if size > maximum:
        raise SourceLimitExceeded(
            f"{Path(path).name}: the {what} is {size} bytes; the limit is {maximum}."
        )
    return size


def check_zip_directory(
    infos: Iterable[zipfile.ZipInfo],
    *,
    max_members: int,
    max_member_bytes: int,
    max_total_bytes: int,
    max_ratio: int,
    what: str,
) -> None:
    """The central directory's claims, checked before anything is inflated."""
    infos = list(infos)
    if len(infos) > max_members:
        raise SourceLimitExceeded(
            f"The {what} has {len(infos)} members; the limit is {max_members}."
        )
    total = 0
    for info in infos:
        if info.file_size > max_member_bytes:
            raise SourceLimitExceeded(
                f"The {what} member {info.filename!r} declares {info.file_size} bytes; "
                f"the limit is {max_member_bytes}."
            )
        total += info.file_size
        if total > max_total_bytes:
            raise SourceLimitExceeded(
                f"The {what} declares more than {max_total_bytes} bytes once inflated."
            )
        if info.file_size > RATIO_FLOOR_BYTES and (
            info.compress_size <= 0 or info.file_size / info.compress_size > max_ratio
        ):
            raise SourceLimitExceeded(
                f"The {what} member {info.filename!r} has an implausible compression ratio "
                f"({info.file_size} from {info.compress_size} bytes; the limit is {max_ratio})."
            )


class InflationBudget:
    """Counts the bytes members *actually* inflate to, across one archive read."""

    def __init__(self, *, max_member_bytes: int, max_total_bytes: int, what: str) -> None:
        self.max_member_bytes = max_member_bytes
        self.max_total_bytes = max_total_bytes
        self.what = what
        self.total = 0

    def _charge(self, info: zipfile.ZipInfo, member: int, more: int) -> int:
        member += more
        self.total += more
        if member > self.max_member_bytes or member > info.file_size:
            raise SourceLimitExceeded(
                f"The {self.what} member {info.filename!r} inflates past its limit "
                f"(declared {info.file_size} bytes)."
            )
        if self.total > self.max_total_bytes:
            raise SourceLimitExceeded(
                f"The {self.what} inflates to more than {self.max_total_bytes} bytes."
            )
        return member

    def read(self, archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
        """One member's bytes, inflated in chunks and counted as they come."""
        parts: list[bytes] = []
        size = 0
        with _member(archive, info, self.what) as handle:
            while block := _chunk(handle, info, self.what):
                size = self._charge(info, size, len(block))
                parts.append(block)
        return b"".join(parts)

    def digest(self, archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> tuple[str, int, bytes]:
        """(SHA-256, size, first 8 bytes) of one member, never holding it whole."""
        digest = hashlib.sha256()
        size = 0
        head = b""
        with _member(archive, info, self.what) as handle:
            while block := _chunk(handle, info, self.what):
                size = self._charge(info, size, len(block))
                if len(head) < 8:
                    head = (head + block)[:8]
                digest.update(block)
        return digest.hexdigest(), size, head


def _member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, what: str) -> Any:
    try:
        return archive.open(info)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as error:
        raise SourceLimitExceeded(
            f"The {what} member {info.filename!r} cannot be read: {error}"
        ) from error


def _chunk(handle: BinaryIO, info: zipfile.ZipInfo, what: str) -> bytes:
    try:
        return handle.read(_CHUNK)
    except (zipfile.BadZipFile, zlib.error, EOFError) as error:
        # zipfile stops at the declared size and then fails the CRC: a member
        # that under-declares its size ends up here, refused.
        raise SourceLimitExceeded(f"The {what} member {info.filename!r} is damaged: {error}") from (
            error
        )


def archive_budget(limits: SourceLimits) -> InflationBudget:
    return InflationBudget(
        max_member_bytes=limits.max_member_bytes,
        max_total_bytes=limits.max_total_uncompressed_bytes,
        what="archive",
    )


def check_opinion_zip(path: Path, archive: zipfile.ZipFile, limits: SourceLimits) -> None:
    check_file_size(path, limits.max_archive_bytes, what="archive")
    check_zip_directory(
        archive.infolist(),
        max_members=limits.max_archive_members,
        max_member_bytes=limits.max_member_bytes,
        max_total_bytes=limits.max_total_uncompressed_bytes,
        max_ratio=limits.max_compression_ratio,
        what="archive",
    )


# ---------------------------------------------------------------------------
# Workbooks
# ---------------------------------------------------------------------------


def _column_number(letters: str) -> int:
    number = 0
    for letter in letters:
        number = number * 26 + (ord(letter) - 64)
    return number


def declared_extent(ref: str) -> tuple[int, int] | None:
    """(rows, columns) a ``dimension ref`` claims, or None if it is not a range."""
    corners = ref.strip().upper().split(":")
    parsed = [_CELL.match(corner) for corner in corners]
    if not corners or len(corners) > 2 or not all(parsed):
        return None
    rows = [int(match.group(2)) for match in parsed if match]
    columns = [_column_number(match.group(1)) for match in parsed if match]
    return max(rows) - min(rows) + 1, max(columns) - min(columns) + 1


def _worksheet_parts(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    return [
        info
        for info in archive.infolist()
        if info.filename.lower().startswith("xl/worksheets/") and info.filename.endswith(".xml")
    ]


def _dimension_of(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str | None:
    with _member(archive, info, "workbook") as handle:
        try:
            head = handle.read(_DIMENSION_WINDOW)
        except (zipfile.BadZipFile, zlib.error, EOFError) as error:
            raise SourceLimitExceeded(f"The workbook part {info.filename!r} is damaged.") from error
    match = _DIMENSION.search(head)
    return match.group(1).decode("ascii", errors="replace") if match else None


def check_workbook_file(path: Path, limits: SourceLimits | None = None) -> None:
    """Refuse a workbook shaped like a decompression or dimension attack, before parsing.

    An XLSX is a ZIP of XML parts. Its size, its parts' declared sizes and
    ratios, its worksheet count and each worksheet's declared used range are
    all read from the central directory and the first bytes of each sheet —
    a few kilobytes — before openpyxl allocates anything.
    """
    limits = limits or current_limits()
    path = Path(path)
    check_file_size(path, limits.max_workbook_bytes, what="workbook")
    try:
        with zipfile.ZipFile(path) as archive:
            check_zip_directory(
                archive.infolist(),
                max_members=limits.max_workbook_parts,
                max_member_bytes=limits.max_workbook_uncompressed_bytes,
                max_total_bytes=limits.max_workbook_uncompressed_bytes,
                max_ratio=limits.max_compression_ratio,
                what="workbook",
            )
            sheets = _worksheet_parts(archive)
            if len(sheets) > limits.max_worksheets:
                raise SourceLimitExceeded(
                    f"{path.name} has {len(sheets)} worksheets; the limit is "
                    f"{limits.max_worksheets}."
                )
            total = 0
            for info in sheets:
                ref = _dimension_of(archive, info)
                extent = declared_extent(ref) if ref else None
                if extent is None:
                    continue
                total += _check_extent(path.name, info.filename, *extent, limits=limits)
                if total > limits.max_workbook_cells:
                    raise SourceLimitExceeded(
                        f"{path.name} declares more than {limits.max_workbook_cells} cells."
                    )
    except zipfile.BadZipFile as error:
        raise SourceLimitExceeded(f"{path.name} is not a readable workbook: {error}") from error


def _check_extent(name: str, sheet: str, rows: int, columns: int, *, limits: SourceLimits) -> int:
    if rows > limits.max_sheet_rows:
        raise SourceLimitExceeded(
            f"{name}: sheet {sheet!r} spans {rows} rows; the limit is {limits.max_sheet_rows}."
        )
    cells = rows * columns
    if cells > limits.max_sheet_cells:
        raise SourceLimitExceeded(
            f"{name}: sheet {sheet!r} spans {rows} × {columns} cells; the limit is "
            f"{limits.max_sheet_cells}."
        )
    return cells


def check_loaded_workbook(name: str, workbook: Any, limits: SourceLimits | None = None) -> None:
    """The extent openpyxl actually computed, before any row loop walks it.

    In normal mode ``max_row`` and ``max_column`` come from the cells present,
    not from the declared range, and a reader that loops over that rectangle
    calling ``cell()`` *creates* every cell in it. One stray cell far out is
    enough, so the rectangle is what is bounded.
    """
    limits = limits or current_limits()
    if len(workbook.worksheets) > limits.max_worksheets:
        raise SourceLimitExceeded(
            f"{name} has {len(workbook.worksheets)} worksheets; the limit is "
            f"{limits.max_worksheets}."
        )
    total = 0
    for worksheet in workbook.worksheets:
        rows = int(getattr(worksheet, "max_row", 0) or 0)
        columns = int(getattr(worksheet, "max_column", 0) or 0)
        total += _check_extent(name, worksheet.title, rows, columns, limits=limits)
        if total > limits.max_workbook_cells:
            raise SourceLimitExceeded(f"{name} spans more than {limits.max_workbook_cells} cells.")
