"""Bounds on what a parser is allowed to do with a file it does not trust.

Every uploaded document is hostile until proven otherwise, and the interesting
attacks here are not clever — they are a 40 KB zip that decompresses to 40 GB, a
PNG whose header claims 60,000 × 60,000 pixels, or a spreadsheet with a used
range of two million empty cells. All three turn a parser into an outage
without executing a single byte of anything.

Two rules govern this module.

**A limit refuses; it does not truncate.** A 400-page draft silently cut to 500
pages and marked complete is worse than one marked failed, because only one of
those ever gets looked at again, and the missing part is exactly the part
nobody knows to miss (Stage-2B brief 72, 102).

**A limit is a number somebody can change.** Every value comes from settings, so
a corpus that legitimately contains 900-page annexes is a configuration problem
rather than a code change.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from typing import IO

from django.conf import settings

from app.documents.extraction.errors import ExtractionFailed


@dataclass(frozen=True)
class Limits:
    """The numbers in force for one extraction run."""

    max_pdf_pages: int
    max_characters: int
    max_archive_members: int
    max_uncompressed_bytes: int
    max_compression_ratio: int
    max_image_pixels: int
    max_email_attachments: int
    max_email_depth: int
    max_xlsx_fragments_per_sheet: int

    @classmethod
    def from_settings(cls) -> Limits:
        return cls(
            max_pdf_pages=settings.EXTRACTION_MAX_PDF_PAGES,
            max_characters=settings.EXTRACTION_MAX_CHARACTERS,
            max_archive_members=settings.EXTRACTION_MAX_ARCHIVE_MEMBERS,
            max_uncompressed_bytes=settings.EXTRACTION_MAX_UNCOMPRESSED_BYTES,
            max_compression_ratio=settings.EXTRACTION_MAX_COMPRESSION_RATIO,
            max_image_pixels=settings.EXTRACTION_MAX_IMAGE_PIXELS,
            max_email_attachments=settings.EXTRACTION_MAX_EMAIL_ATTACHMENTS,
            max_email_depth=settings.EXTRACTION_MAX_EMAIL_DEPTH,
            max_xlsx_fragments_per_sheet=settings.EXTRACTION_MAX_XLSX_FRAGMENTS_PER_SHEET,
        )


def current_limits() -> Limits:
    return Limits.from_settings()


def guard_office_container(handle: IO[bytes], limits: Limits) -> None:
    """Refuse an OOXML package that is shaped like a decompression attack.

    DOCX, XLSX and PPTX are all ZIP containers, and every one of the parsers
    downstream will happily inflate whatever the central directory promises.
    Checking the directory *before* opening the document costs one pass over a
    few kilobytes of headers and is the only point at which the numbers are
    still claims rather than allocations.

    Three separate things are checked, because each catches a bomb the others
    miss: total inflated size (one enormous member), member count (a million
    tiny ones), and per-member compression ratio (a member that is modest in
    total but absurd in ratio, which is what nested bombs look like).
    """
    try:
        with zipfile.ZipFile(handle) as archive:
            infos = archive.infolist()
    except zipfile.BadZipFile as error:
        raise ExtractionFailed(
            "invalid_container",
            "Fail ei ole terve Office-pakett (ZIP-struktuur on vigane).",
        ) from error

    if len(infos) > limits.max_archive_members:
        raise ExtractionFailed(
            "too_many_members",
            f"Pakett sisaldab {len(infos)} faili, lubatud on {limits.max_archive_members}.",
        )

    total = 0
    for info in infos:
        # A directory traversal cannot hurt us — nothing here writes members to
        # disk — but a member naming its way out of the package is a strong
        # signal the file was built to attack something, and refusing is free.
        if info.filename.startswith("/") or ".." in info.filename.split("/"):
            raise ExtractionFailed(
                "unsafe_member_path",
                "Pakett sisaldab kahtlase teekonnaga liiget.",
            )
        total += info.file_size
        if total > limits.max_uncompressed_bytes:
            raise ExtractionFailed(
                "decompression_limit",
                "Paketi lahtipakitud maht ületab lubatud piiri.",
            )
        if info.compress_size > 0:
            ratio = info.file_size / info.compress_size
            if ratio > limits.max_compression_ratio:
                raise ExtractionFailed(
                    "compression_ratio",
                    "Paketi liikme pakkimissuhe on ebausutav.",
                )
    handle.seek(0)


def guard_character_budget(total: int, limits: Limits) -> None:
    if total > limits.max_characters:
        raise ExtractionFailed(
            "character_limit",
            f"Eraldatud tekst ületab {limits.max_characters} märgi piiri.",
        )


def configure_image_safety(limits: Limits) -> None:
    """Point Pillow's decompression-bomb guard at our own number.

    Pillow already refuses images past ``Image.MAX_IMAGE_PIXELS`` — but it
    *warns* by default rather than raising, and a warning in a worker loop is a
    log line nobody reads attached to a process that is already allocating.
    """
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = limits.max_image_pixels
