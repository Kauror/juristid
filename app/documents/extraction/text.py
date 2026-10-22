"""Plain text, CSV, and images.

Grouped together because they share one property: the bytes are the content,
with no container in between and nothing inside that could execute.

Decoding is where plain text gets interesting. A file that will not decode is
**not** forced through with replacement characters: `õ` silently becoming `?`
across an entire document produces text that indexes, searches and looks fine
while being subtly wrong, and nobody ever finds out. It is reported instead, and
the detected encoding is recorded on every success so a future reader knows what
assumption produced the characters they are reading.
"""

from __future__ import annotations

import csv

from app.documents.enums import DerivativeKind, LocatorKind, TextSource
from app.documents.extraction.base import (
    DerivativePayload,
    Fragment,
    ParseResult,
    SourceFile,
    normalise,
    registry,
)
from app.documents.extraction.errors import ExtractionFailed
from app.documents.extraction.limits import (
    TableBudget,
    current_limits,
    guard_character_budget,
)
from app.documents.extraction.ocr import (
    OcrUnavailable,
    ocr_engine_version,
    ocr_is_available,
    recognise_image,
)
from app.documents.extraction.thumbnails import image_thumbnail

#: Tried in order, and the list is short on purpose.
#:
#: UTF-8 first because it is what everything modern writes. CP1257 after it
#: because Estonian text written on Windows before about 2010 is still arriving
#: in the register, and refusing it would be refusing real evidence.
#:
#: ISO-8859-13 and -15 are deliberately **absent** even though they are also
#: plausible legacy encodings. They map all 256 byte values, so adding either
#: would mean no byte sequence can ever fail to decode — which does not make the
#: parser more capable, it makes the failure silent. A binary file dropped in as
#: `.txt` would decode into mojibake, index cleanly, and search as if it were
#: real text. CP1257 leaves two byte values undefined, which is enough for the
#: obvious garbage to announce itself, and covers 8859-13 content approximately
#: anyway.
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "cp1257")

LINES_PER_FRAGMENT = 200

#: Above this share of C0 control characters, the "text" is not text. The
#: encoding check alone cannot catch this: a single-byte codec will decode
#: anything handed to it, so the second question is whether the result looks
#: like something a person wrote.
MAX_CONTROL_CHARACTER_SHARE = 0.05


def _control_share(text: str) -> float:
    if not text:
        return 0.0
    # Tab, carriage return, line feed and form feed are ordinary in a
    # document; every other C0 code point is a sign the bytes are not text.
    allowed = {chr(9), chr(10), chr(12), chr(13)}
    controls = sum(1 for character in text if ord(character) < 32 and character not in allowed)
    return controls / len(text)


def decode_text(content: bytes) -> tuple[str, str]:
    """Decode, or say honestly that it could not be decoded.

    Never with ``errors="replace"``. A document in which every `õ` has silently
    become `?` indexes, searches and displays exactly like a correct one, and
    the person who eventually quotes it has no way to know. Refusing is
    recoverable; quiet corruption is not (Stage-2B brief 19).
    """
    for encoding in TEXT_ENCODINGS:
        try:
            decoded = content.decode(encoding)
        except UnicodeDecodeError:
            continue
        if _control_share(decoded) > MAX_CONTROL_CHARACTER_SHARE:
            raise ExtractionFailed(
                "not_text",
                "Fail ei näe välja nagu tekst — sisus on ohtralt juhtmärke. "
                "Sisu ei eraldata, et vigast teksti mitte otsingusse kirjutada.",
            )
        return decoded, encoding
    raise ExtractionFailed(
        "undecodable_text",
        "Faili märgistikku ei õnnestunud tuvastada. Sisu ei eraldata, "
        "et vigast teksti mitte otsingusse kirjutada.",
    )


class TextParser:
    name = "text"
    version = "1"
    mime_types = frozenset({"text/plain"})

    def parse(self, source: SourceFile) -> ParseResult:
        limits = current_limits()
        text, encoding = decode_text(source.content)
        lines = text.splitlines()

        fragments: list[Fragment] = []
        total = 0
        for start in range(0, len(lines), LINES_PER_FRAGMENT):
            chunk = lines[start : start + LINES_PER_FRAGMENT]
            body = normalise("\n".join(chunk))
            if not body:
                continue
            total += len(body)
            guard_character_budget(total, limits)
            first, last = start + 1, start + len(chunk)
            fragments.append(
                Fragment(
                    text=body,
                    locator_kind=LocatorKind.LINE_RANGE,
                    locator={"line_from": first, "line_to": last},
                    locator_label=f"read {first}–{last}",
                )
            )

        if not fragments:
            raise ExtractionFailed("no_text", "Fail on tühi.")

        return ParseResult(
            derivatives=(
                DerivativePayload(
                    fragments=tuple(fragments),
                    metadata={"encoding": encoding, "line_count": len(lines)},
                ),
            )
        )


class CsvParser:
    name = "csv"
    version = "1"
    mime_types = frozenset({"text/csv"})

    def parse(self, source: SourceFile) -> ParseResult:
        """Read the rows as a stream, charging every limit as each row arrives.

        This used to be ``list(csv.reader(...))``: the whole row graph built
        first and the character budget applied afterwards, by which point a
        file of one enormous row, or of millions of empty ones, had already
        had its allocation (csf_5fa01ed3, docs/adr/0176). Now the size is
        checked before decoding, the raw length of each record before the csv
        module builds it, and rows, cells and cell length as they are read.
        The output — fragments, locators, metadata — is what it always was.
        """
        limits = current_limits()
        if len(source.content) > limits.max_csv_bytes:
            raise ExtractionFailed(
                "csv_size_limit",
                f"CSV-fail on suurem kui {limits.max_csv_bytes} baiti. Originaal on alles.",
            )
        text, encoding = decode_text(source.content)

        # Sniffed rather than assumed. Estonian exports are semicolon-delimited
        # about as often as comma-delimited, because the decimal separator is a
        # comma, and getting it wrong turns every row into one long cell.
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ","

        lines = _RecordLines(text, limits.max_csv_record_characters)
        budget = TableBudget(limits)
        fragments: list[Fragment] = []
        total = 0
        row_count = 0
        chunk: list[str] = []
        chunk_rows = 0

        def flush() -> None:
            nonlocal total
            body = normalise("\n".join(chunk))
            if not body:
                return
            total += len(body)
            guard_character_budget(total, limits)
            first, last = row_count - chunk_rows + 1, row_count
            fragments.append(
                Fragment(
                    text=body,
                    locator_kind=LocatorKind.LINE_RANGE,
                    locator={"row_from": first, "row_to": last},
                    locator_label=f"read {first}–{last}",
                )
            )

        try:
            for row in csv.reader(lines, delimiter=delimiter):
                lines.record_read()
                row_count += 1
                budget.visit(row_count, len(row), "CSV-fail")
                cells = [
                    stripped
                    for stripped in (budget.cell(cell, "CSV-faili").strip() for cell in row)
                    if stripped
                ]
                if cells:
                    chunk.append(" | ".join(cells))
                chunk_rows += 1
                if chunk_rows == LINES_PER_FRAGMENT:
                    flush()
                    chunk, chunk_rows = [], 0
        except csv.Error as error:
            # The module's own message names the defect and never the content.
            # Its field limit (131 072 by default) is the cell-length limit
            # arriving first, and is reported as that.
            if "field larger than field limit" in str(error):
                raise ExtractionFailed(
                    "cell_character_limit",
                    f"CSV-faili lahter on pikem kui {csv.field_size_limit()} märki.",
                ) from error
            raise ExtractionFailed(
                "unreadable_csv", f"CSV-faili ei õnnestunud lugeda ({error})."
            ) from error
        if chunk_rows:
            flush()

        if not fragments:
            raise ExtractionFailed("no_text", "CSV ei sisalda ridu.")

        return ParseResult(
            derivatives=(
                DerivativePayload(
                    fragments=tuple(fragments),
                    # No column typing, no header inference, no "this looks like
                    # a date". A CSV is a text file with separators; inferring
                    # spreadsheet semantics from it is how an importer starts
                    # inventing data (Stage-2B brief 19).
                    metadata={
                        "encoding": encoding,
                        "delimiter": delimiter,
                        "row_count": row_count,
                    },
                ),
            )
        )


class _RecordLines:
    """The decoded text, one line at a time, refusing a record that runs on.

    The csv module asks for a line whenever a record needs more input and
    builds the whole record before handing it over, so a record of thirty
    million separators is thirty million cells before anything here sees it.
    Counting the raw characters each record has consumed, and refusing once
    one record has consumed too many, is the only point at which that row is
    still a length rather than an allocation. ``record_read`` is called after
    each row, so the count is per record — quoted line breaks included.
    """

    def __init__(self, text: str, limit: int) -> None:
        self._text = text
        self._position = 0
        self._limit = limit
        self._pending = 0

    def __iter__(self) -> _RecordLines:
        return self

    def __next__(self) -> str:
        # The lines ``iter(io.StringIO(text))`` would give — split after each
        # "\n" and nowhere else — measured before the slice is made, so an
        # over-long line is refused without being copied.
        start = self._position
        if start >= len(self._text):
            raise StopIteration
        end = self._text.find("\n", start)
        end = len(self._text) if end == -1 else end + 1
        self._pending += end - start
        if self._pending > self._limit:
            raise ExtractionFailed(
                "csv_record_limit",
                f"CSV-faili üks kirje on pikem kui {self._limit} märki. Originaal on alles.",
            )
        self._position = end
        return self._text[start:end]

    def record_read(self) -> None:
        self._pending = 0


class ImageParser:
    """PNG and JPEG. The text in an image is whatever OCR can read.

    An image with no legible text is a successful extraction with no fragments,
    not a failure — a photograph of a building is a perfectly valid piece of
    evidence and there is nothing wrong with it. It is recorded as NOT
    APPLICABLE by the orchestrator when no fragment survives, with the reason
    said out loud.
    """

    name = "image"
    version = "1"
    mime_types = frozenset({"image/png", "image/jpeg"})

    def parse(self, source: SourceFile) -> ParseResult:
        limits = current_limits()

        # Built first and kept whatever happens next. A photograph of a building
        # is a perfectly valid piece of evidence with no text in it, and the
        # useful thing to show for it is the picture.
        thumbnail = image_thumbnail(source.content, limits)

        if not ocr_is_available():
            raise ExtractionFailed(
                "ocr_unavailable",
                "OCR ei ole selles keskkonnas saadaval, seega pildilt teksti ei loeta.",
            )
        try:
            engine = ocr_engine_version()
        except OcrUnavailable as error:  # pragma: no cover - probed above
            raise ExtractionFailed("ocr_unavailable", str(error)) from error

        text = normalise(recognise_image(source.content, limits))
        if not text:
            raise ExtractionFailed(
                "no_recognisable_text",
                "Pildilt ei tuvastatud teksti. Originaal on alles ja avatav.",
            )
        guard_character_budget(len(text), limits)

        payloads = [
            DerivativePayload(
                kind=DerivativeKind.OCR_TEXT,
                fragments=(
                    Fragment(
                        text=text,
                        locator_kind=LocatorKind.BODY,
                        locator={},
                        locator_label="pildilt loetud tekst",
                        text_source=TextSource.OCR,
                    ),
                ),
                metadata={"ocr_engine": engine},
            )
        ]
        if thumbnail is not None:
            payloads.append(thumbnail)
        return ParseResult(derivatives=tuple(payloads))


registry.register(TextParser())
registry.register(CsvParser())
registry.register(ImageParser())
