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
import io

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
from app.documents.extraction.limits import current_limits, guard_character_budget
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
        limits = current_limits()
        text, encoding = decode_text(source.content)

        # Sniffed rather than assumed. Estonian exports are semicolon-delimited
        # about as often as comma-delimited, because the decimal separator is a
        # comma, and getting it wrong turns every row into one long cell.
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ","

        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        fragments: list[Fragment] = []
        total = 0
        for start in range(0, len(rows), LINES_PER_FRAGMENT):
            chunk = rows[start : start + LINES_PER_FRAGMENT]
            body = normalise(
                "\n".join(
                    " | ".join(cell.strip() for cell in row if cell.strip())
                    for row in chunk
                    if any(cell.strip() for cell in row)
                )
            )
            if not body:
                continue
            total += len(body)
            guard_character_budget(total, limits)
            first, last = start + 1, start + len(chunk)
            fragments.append(
                Fragment(
                    text=body,
                    locator_kind=LocatorKind.LINE_RANGE,
                    locator={"row_from": first, "row_to": last},
                    locator_label=f"read {first}–{last}",
                )
            )

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
                        "row_count": len(rows),
                    },
                ),
            )
        )


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
