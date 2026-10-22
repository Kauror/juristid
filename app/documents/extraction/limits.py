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

import io
import math
import struct
import warnings
import zipfile
from dataclasses import dataclass
from typing import IO, Any

from django.conf import settings

from app.documents.extraction.base import ParseResult
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
    max_table_rows: int
    max_table_columns: int
    max_table_cells: int
    max_table_cell_characters: int
    max_csv_bytes: int
    max_csv_record_characters: int
    max_email_body_characters: int
    max_fragments: int
    max_text_bytes: int
    max_text_lines: int
    max_zip_directory_bytes: int
    max_docx_part_bytes: int
    max_docx_xml_bytes: int
    max_docx_paragraphs: int

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
            max_table_rows=settings.EXTRACTION_MAX_TABLE_ROWS,
            max_table_columns=settings.EXTRACTION_MAX_TABLE_COLUMNS,
            max_table_cells=settings.EXTRACTION_MAX_TABLE_CELLS,
            max_table_cell_characters=settings.EXTRACTION_MAX_TABLE_CELL_CHARACTERS,
            max_csv_bytes=settings.EXTRACTION_MAX_CSV_BYTES,
            max_csv_record_characters=settings.EXTRACTION_MAX_CSV_RECORD_CHARACTERS,
            max_email_body_characters=settings.EXTRACTION_MAX_EMAIL_BODY_CHARACTERS,
            max_fragments=settings.EXTRACTION_MAX_FRAGMENTS,
            max_text_bytes=settings.EXTRACTION_MAX_TEXT_BYTES,
            max_text_lines=settings.EXTRACTION_MAX_TEXT_LINES,
            max_zip_directory_bytes=settings.EXTRACTION_MAX_ZIP_DIRECTORY_BYTES,
            max_docx_part_bytes=settings.EXTRACTION_MAX_DOCX_PART_BYTES,
            max_docx_xml_bytes=settings.EXTRACTION_MAX_DOCX_XML_BYTES,
            max_docx_paragraphs=settings.EXTRACTION_MAX_DOCX_PARAGRAPHS,
        )


def current_limits() -> Limits:
    return Limits.from_settings()


#: ZIP structures read by :func:`preflight_zip_directory`, as APPNOTE.TXT
#: (4.3.12, 4.3.14-16) and `zipfile` define them.
_EOCD = struct.Struct("<4s4H2LH")
_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP64_LOCATOR = struct.Struct("<4sLQL")
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_ZIP64_EOCD = struct.Struct("<4sQ2H2L4Q")
_ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
_CENTRAL_HEADER = struct.Struct("<4s4B4HL2L5H2L")
_CENTRAL_SIGNATURE = b"PK\x01\x02"
_MAX_COMMENT = 0xFFFF


def _invalid_container() -> ExtractionFailed:
    return ExtractionFailed(
        "invalid_container",
        "Fail ei ole terve Office-pakett (ZIP-struktuur on vigane).",
    )


def _too_many_members(count: int | None, limits: Limits) -> ExtractionFailed:
    said = f"{count}" if count is not None else f"üle {limits.max_archive_members}"
    return ExtractionFailed(
        "too_many_members",
        f"Pakett sisaldab {said} faili, lubatud on {limits.max_archive_members}.",
    )


def preflight_zip_directory(handle: IO[bytes], limits: Limits) -> None:
    """Check a ZIP's directory from its own end records before `zipfile` reads it.

    `zipfile.ZipFile()` reads the whole central directory into memory and
    builds one `ZipInfo` per entry before anybody can count them, so the
    member limit below used to run after the allocation it was meant to
    prevent: a small file of hundreds of thousands of empty entries cost every
    one of them first (csf_5fb4e796365c901966387382, docs/adr/0182).

    This reads only the end of the file — the End Of Central Directory record,
    within the 64 KiB comment window, and the ZIP64 locator and record when
    there is one — and refuses before any directory byte is read when:

    * the records are missing, malformed, multi-disk, or say ZIP64 (a
      ``0xFFFF``/``0xFFFFFFFF`` sentinel) without a consistent ZIP64 record;
    * the declared entry count is over ``max_archive_members``;
    * the declared directory is larger than ``max_zip_directory_bytes`` or does
      not fit in the file before its end record.

    It then walks the directory itself, one fixed-size entry header at a time
    and seeking over names and extra fields, so the walk allocates nothing per
    entry and stops at the first entry past the limit. A directory whose
    entries do not add up to exactly its declared size and count is refused as
    malformed. Only a directory that passes all of it is handed to `zipfile`.
    """
    handle.seek(0, io.SEEK_END)
    file_size = handle.tell()
    if file_size < _EOCD.size:
        raise _invalid_container()
    window = min(file_size, _EOCD.size + _MAX_COMMENT)
    handle.seek(file_size - window)
    tail = handle.read(window)
    # Where `zipfile` looks for it: the last 22 bytes when there is no comment,
    # otherwise the last signature within the comment window.
    at = len(tail) - _EOCD.size
    if not (tail.startswith(_EOCD_SIGNATURE, at) and tail.endswith(b"\x00\x00")):
        at = tail.rfind(_EOCD_SIGNATURE)
    if at < 0 or at + _EOCD.size > len(tail):
        raise _invalid_container()
    (_sig, disk, disk_start, entries_here, entries, cd_size, cd_offset, _comment) = (
        _EOCD.unpack_from(tail, at)
    )
    location = file_size - window + at

    zip64 = _read_zip64(handle, location)
    if zip64 is not None:
        disk, disk_start, entries_here, entries, cd_size, cd_offset, location = zip64
    elif 0xFFFF in (entries_here, entries) or 0xFFFFFFFF in (cd_size, cd_offset):
        # A ZIP64 sentinel with no ZIP64 record to resolve it.
        raise _invalid_container()

    if disk != 0 or disk_start != 0 or entries_here != entries:
        raise _invalid_container()
    if entries > limits.max_archive_members:
        raise _too_many_members(entries, limits)
    if cd_size > limits.max_zip_directory_bytes:
        raise ExtractionFailed(
            "zip_directory_limit",
            f"Paketi sisukord on {cd_size} baiti, lubatud on {limits.max_zip_directory_bytes}.",
        )
    # Where `zipfile` itself will read it from: immediately before the end
    # record (prepended data shifts the declared offset, not this).
    start = location - cd_size
    if start < 0:
        raise _invalid_container()
    _walk_central_directory(handle, start, cd_size, entries, limits)
    handle.seek(0)


def _read_zip64(handle: IO[bytes], eocd_location: int) -> tuple[int, ...] | None:
    """The ZIP64 end record's numbers, if a locator precedes the end record."""
    locator_at = eocd_location - _ZIP64_LOCATOR.size
    if locator_at < 0:
        return None
    handle.seek(locator_at)
    data = handle.read(_ZIP64_LOCATOR.size)
    if len(data) != _ZIP64_LOCATOR.size:
        raise _invalid_container()
    signature, locator_disk, record_offset, disks = _ZIP64_LOCATOR.unpack(data)
    if signature != _ZIP64_LOCATOR_SIGNATURE:
        return None
    if locator_disk != 0 or disks > 1:
        raise _invalid_container()
    record_at = locator_at - _ZIP64_EOCD.size
    if record_at < 0 or record_offset > record_at:
        raise _invalid_container()
    handle.seek(record_at)
    data = handle.read(_ZIP64_EOCD.size)
    if len(data) != _ZIP64_EOCD.size:
        raise _invalid_container()
    (
        signature,
        record_size,
        _made,
        _needed,
        disk,
        disk_start,
        entries_here,
        entries,
        cd_size,
        cd_offset,
    ) = _ZIP64_EOCD.unpack(data)
    # A record with "extensible data" is not something an Office writer
    # produces; refusing it keeps the record exactly where it was looked for.
    if signature != _ZIP64_EOCD_SIGNATURE or record_size + 12 != _ZIP64_EOCD.size:
        raise _invalid_container()
    # The directory ends where the record begins, as `zipfile` also requires.
    if cd_offset + cd_size != record_offset:
        raise _invalid_container()
    return disk, disk_start, entries_here, entries, cd_size, cd_offset, record_at


def _walk_central_directory(
    handle: IO[bytes], start: int, size: int, declared: int, limits: Limits
) -> None:
    handle.seek(start)
    walked = 0
    count = 0
    while walked < size:
        header = handle.read(_CENTRAL_HEADER.size)
        if len(header) != _CENTRAL_HEADER.size or walked + _CENTRAL_HEADER.size > size:
            raise _invalid_container()
        fields = _CENTRAL_HEADER.unpack(header)
        if fields[0] != _CENTRAL_SIGNATURE:
            raise _invalid_container()
        count += 1
        if count > limits.max_archive_members:
            raise _too_many_members(None, limits)
        variable = fields[12] + fields[13] + fields[14]
        walked += _CENTRAL_HEADER.size + variable
        handle.seek(variable, io.SEEK_CUR)
    if walked != size or count != declared:
        raise _invalid_container()


def guard_office_container(handle: IO[bytes], limits: Limits) -> list[zipfile.ZipInfo]:
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

    The directory itself is checked first, from the file's end records, before
    `zipfile` allocates anything for it (:func:`preflight_zip_directory`).
    Returns the members, so a parser can budget the parts it will parse.
    """
    preflight_zip_directory(handle, limits)
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
    return infos


def guard_character_budget(total: int, limits: Limits) -> None:
    if total > limits.max_characters:
        raise ExtractionFailed(
            "character_limit",
            f"Eraldatud tekst ületab {limits.max_characters} märgi piiri.",
        )


class CharacterBudget:
    """One running total for a parser that builds its text from many pieces.

    ``guard_character_budget`` checks a total the caller keeps; this keeps it,
    so a parser assembling a message from a subject, four address lists and a
    body charges each piece as it is made rather than adding them up at the
    end, after the allocation the budget exists to prevent (csf_8da83d23).
    """

    def __init__(self, limits: Limits) -> None:
        self._limits = limits
        self.used = 0

    def charge(self, text: str) -> str:
        self.used += len(text)
        guard_character_budget(self.used, self._limits)
        return text

    def charge_all(self, texts: list[str]) -> list[str]:
        for text in texts:
            self.charge(text)
        return texts


class TableBudget:
    """What reading a spreadsheet or a CSV may *visit*, empty cells included.

    The character budget only counts text that comes out, and a used range of
    a million blank rows produces none — so a workbook declaring
    ``A1:XFD1048576`` around one styled cell cost seventeen billion cell
    visits and no budget at all (csf_a74bfe6e). This counts the walk itself:
    rows per sheet, cells per row, and cells across the whole file.

    ``declared`` is the cheap check, made from the worksheet's own dimension
    record before a single row is read; ``visit`` is the one that holds when
    the record is absent or understates the sheet.
    """

    def __init__(self, limits: Limits) -> None:
        self._limits = limits
        self.cells = 0

    def declared(self, rows: int | None, columns: int | None, where: str) -> None:
        limits = self._limits
        rows = rows or 0
        columns = columns or 0
        if (
            rows > limits.max_table_rows
            or columns > limits.max_table_columns
            or rows * columns > limits.max_table_cells - self.cells
        ):
            raise ExtractionFailed(
                "sheet_dimension_limit",
                f"{where} kasutatud ala on {rows} rida × {columns} veergu, mis ületab "
                f"lubatud piiri ({limits.max_table_rows} rida, {limits.max_table_columns} "
                f"veergu, {limits.max_table_cells} lahtrit). Originaal on alles.",
            )

    def visit(self, row_number: int, width: int, where: str) -> None:
        limits = self._limits
        if row_number > limits.max_table_rows:
            raise ExtractionFailed(
                "table_row_limit",
                f"{where} on üle {limits.max_table_rows} rea. Originaal on alles.",
            )
        if width > limits.max_table_columns:
            raise ExtractionFailed(
                "table_column_limit",
                f"{where} reas {row_number} on {width} lahtrit, lubatud on "
                f"{limits.max_table_columns}. Originaal on alles.",
            )
        # An empty row is still a step of the walk, so it is charged one cell.
        self.cells += max(width, 1)
        if self.cells > limits.max_table_cells:
            raise ExtractionFailed(
                "table_cell_limit",
                f"Tabelis on üle {limits.max_table_cells} lahtri. Originaal on alles.",
            )

    def cell(self, text: str, where: str) -> str:
        if len(text) > self._limits.max_table_cell_characters:
            raise ExtractionFailed(
                "cell_character_limit",
                f"{where} lahter on pikem kui {self._limits.max_table_cell_characters} märki.",
            )
        return text


def guard_parse_result(result: ParseResult, limits: Limits) -> None:
    """The limits every parser's *output* must fit, whichever parser made it.

    Each parser charges its own budgets while it works, because that is where
    an allocation can still be avoided. This is the second line: one check on
    the finished result, in the one path every parse goes through
    (``orchestrator.parse_source``), so a parser that forgets — or one written
    next year — cannot hand the database more text, more fragment rows or
    more metadata than the limits allow. It refuses; it never trims.
    """
    fragments = 0
    characters = 0
    described = 0
    for payload in result.derivatives:
        described += _metadata_characters(payload.metadata)
        for fragment in payload.fragments:
            fragments += 1
            characters += len(fragment.text)
            described += len(fragment.locator_label) + _metadata_characters(fragment.locator)
    if fragments > limits.max_fragments:
        raise ExtractionFailed(
            "fragment_limit",
            f"Eraldatud tekst koosneb {fragments} osast, lubatud on {limits.max_fragments}.",
        )
    guard_character_budget(characters, limits)
    if described > limits.max_characters:
        raise ExtractionFailed(
            "metadata_limit",
            f"Eraldatud metaandmed ületavad {limits.max_characters} märgi piiri.",
        )


def _metadata_characters(value: object) -> int:
    """Characters in a JSON-shaped value, walked without recursion."""
    total = 0
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            total += len(item)
        elif isinstance(item, dict):
            for key, nested in item.items():
                total += len(str(key))
                stack.append(nested)
        elif isinstance(item, list | tuple | set | frozenset):
            stack.extend(item)
    return total


def configure_image_safety(limits: Limits) -> None:
    """Point Pillow's decompression-bomb guard at our own number.

    Pillow only *warns* past ``Image.MAX_IMAGE_PIXELS`` and raises
    ``DecompressionBombError`` past twice that. Setting it to the limit, as
    this used to, let an image of up to twice ``max_image_pixels`` decode with
    nothing but a warning (csf_9b7b703ca586c216499d33bd). Half the limit makes
    Pillow's own hard error fire at the limit, as a backstop for any path that
    opens an image; :func:`open_bounded_image` is the check every extraction
    path makes, explicitly, before decoding. The warning band below the limit
    is ours to ignore: those images are within it.
    """
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = max(1, (limits.max_image_pixels + 1) // 2)
    warnings.filterwarnings("ignore", category=Image.DecompressionBombWarning)


def image_too_large() -> ExtractionFailed:
    return ExtractionFailed("image_too_large", "Pildi mõõtmed ületavad lubatud piiri.")


def open_bounded_image(content: bytes, limits: Limits) -> Any:
    """Open an image by its header and refuse it, *before decoding*, if too large.

    ``Image.open`` reads only the header; ``width × height`` is checked against
    ``max_image_pixels`` there, and only an image within it is ever handed on
    to ``load()``, ``convert()``, ``thumbnail()`` or OCR. One refusal for every
    image path — `image_too_large`, the reason the OCR path always gave — and
    the caller closes what it is given.
    """
    configure_image_safety(limits)
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(content))
    except Image.DecompressionBombError as error:
        raise image_too_large() from error
    except UnidentifiedImageError as error:
        raise ExtractionFailed("unreadable_image", "Pilti ei õnnestunud avada.") from error
    width, height = image.size
    if max(1, width) * max(1, height) > limits.max_image_pixels:
        image.close()
        raise image_too_large()
    return image


def bounded_render_scale(width_pt: float, height_pt: float, wanted: float, limits: Limits) -> float:
    """The largest scale <= ``wanted`` whose raster stays inside the pixel budget.

    PDFium allocates the whole bitmap the moment ``render`` is called; Pillow's
    guard above only ever sees it afterwards, when the memory is already gone. A
    433-byte PDF declaring a 5000 × 5000 pt page is a 7000 × 7000 raster at OCR
    resolution, so the budget has to be applied to the page size *before*
    rendering (AUD-045). A page with no usable size is left at ``wanted`` —
    PDFium refuses those itself, without allocating.
    """
    area = float(width_pt) * float(height_pt)
    if area <= 0:
        return wanted
    scale = min(wanted, (limits.max_image_pixels / area) ** 0.5)
    # PDFium rounds each side up to whole pixels, so the exact square root can
    # still land a few pixels over; shave until the rounded raster fits.
    while (
        scale > 0
        and math.ceil(width_pt * scale) * math.ceil(height_pt * scale) > limits.max_image_pixels
    ):
        scale *= 0.999
    return scale
