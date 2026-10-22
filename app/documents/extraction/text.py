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

import codecs
import csv
import re
from collections.abc import Callable, Iterator

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
    Limits,
    TableBudget,
    current_limits,
    guard_character_budget,
    open_bounded_image,
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


#: Raw bytes decoded at a time while a plain-text file streams. The decoded
#: piece, the pending line and the block being built are all that is ever in
#: memory besides the bytes themselves (csf_ea514a0f447d737dfb1bdfb9).
TEXT_CHUNK_BYTES = 1024 * 1024

#: The C0 controls `_control_share` counts: everything below a space except
#: tab, line feed, form feed and carriage return.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0e-\x1f]")

#: Every character ``str.splitlines`` ends a line at ("\r\n" is one break).
_LINE_BREAKS = frozenset("\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")

#: How much of one line is kept raw before its finished words are collapsed.
_LINE_COMPACT_CHARACTERS = 64 * 1024


def _decoded_pieces(content: bytes, encoding: str) -> Iterator[str]:
    """``content.decode(encoding)``, a bounded piece at a time, strictly.

    An incremental decoder fed the whole input gives exactly the text — or the
    `UnicodeDecodeError` — that one ``bytes.decode`` call gives, the BOM
    handling of ``utf-8-sig`` included.
    """
    decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
    for start in range(0, len(content), TEXT_CHUNK_BYTES):
        piece = decoder.decode(content[start : start + TEXT_CHUNK_BYTES], final=False)
        if piece:
            yield piece
    tail = decoder.decode(b"", final=True)
    if tail:
        yield tail


def detect_text_encoding(content: bytes) -> str:
    """:func:`decode_text`'s answer, reached without holding the decoded text.

    The same encodings in the same order, the same refusal for undecodable
    bytes, and the same control-character share over the whole text, counted
    a piece at a time.
    """
    for encoding in TEXT_ENCODINGS:
        length = 0
        controls = 0
        try:
            for piece in _decoded_pieces(content, encoding):
                length += len(piece)
                controls += len(_CONTROL_CHARACTERS.findall(piece))
        except UnicodeDecodeError:
            continue
        if length and controls / length > MAX_CONTROL_CHARACTER_SHARE:
            raise ExtractionFailed(
                "not_text",
                "Fail ei näe välja nagu tekst — sisus on ohtralt juhtmärke. "
                "Sisu ei eraldata, et vigast teksti mitte otsingusse kirjutada.",
            )
        return encoding
    raise ExtractionFailed(
        "undecodable_text",
        "Faili märgistikku ei õnnestunud tuvastada. Sisu ei eraldata, "
        "et vigast teksti mitte otsingusse kirjutada.",
    )


class _PendingLine:
    """One line being read, kept as its whitespace-collapsed form.

    ``" ".join(line.split())`` — what `normalise` makes of every line — built
    incrementally: finished words are joined into ``head`` as they complete,
    and only the word still being read stays raw. A line of a hundred million
    spaces is therefore an empty string, as it always normalised to, and never
    a hundred-megabyte one.
    """

    def __init__(self) -> None:
        self.head = ""
        self.tail = ""
        self.open = False

    def feed(self, text: str) -> None:
        self.open = True
        self.tail += text
        if len(self.tail) > _LINE_COMPACT_CHARACTERS:
            self._compact()

    def _compact(self) -> None:
        words = self.tail.split()
        if words and not self.tail[-1].isspace():
            self.tail = words.pop()
        else:
            self.tail = ""
        if words:
            joined = " ".join(words)
            self.head = f"{self.head} {joined}" if self.head else joined

    def least_length(self) -> int:
        """The collapsed length of what has been read of this line so far.

        A lower bound on the finished line: more input can only add to it.
        """
        rest = len(" ".join(self.tail.split()))
        return len(self.head) + (1 if self.head and rest else 0) + rest

    def finish(self) -> str:
        rest = " ".join(self.tail.split())
        line = f"{self.head} {rest}" if self.head and rest else self.head or rest
        self.head, self.tail, self.open = "", "", False
        return line


def normalised_text_lines(
    pieces: Iterator[str], check: Callable[[_PendingLine], None]
) -> Iterator[str]:
    """``[" ".join(line.split()) for line in text.splitlines()]``, streamed.

    Line breaks are exactly ``str.splitlines``' — "\r\n" is one break even when
    the two characters arrive in different pieces — and so is the line count,
    a final line without a break included. ``check`` sees the pending line
    whenever it has grown, so a caller can refuse a line before it is whole.
    """
    pending = _PendingLine()
    held_cr = False
    for piece in pieces:
        if held_cr:
            piece = "\r" + piece
            held_cr = False
        if piece.endswith("\r"):
            # Whether it ends the line alone or with a "\n" is in the next piece.
            piece = piece[:-1]
            held_cr = True
        for part in piece.splitlines(keepends=True):
            if part[-1] in _LINE_BREAKS:
                pending.feed(part[:-2] if part.endswith("\r\n") else part[:-1])
                check(pending)
                yield pending.finish()
            else:
                pending.feed(part)
                check(pending)
    if held_cr:
        pending.feed("")
        yield pending.finish()
    elif pending.open:
        yield pending.finish()


class TextParser:
    name = "text"
    version = "1"
    mime_types = frozenset({"text/plain"})

    def parse(self, source: SourceFile) -> ParseResult:
        """Read a plain-text file in bounded pieces; the output is what it always was.

        This used to decode the whole file into one string and split all of it
        into a list of lines before the character budget saw a single
        fragment, so a hundred-megabyte file was a few hundred megabytes of
        text and a list of millions of lines first (csf_ea514a0f447d737dfb1bdfb9,
        docs/adr/0182). Now the raw size is checked before anything is decoded,
        the encoding is decided by a streaming pass that keeps no text, and the
        second pass builds each block of two hundred lines as it arrives —
        charging lines, characters and fragments as it goes, and refusing a
        line before it is whole once it could no longer fit. Fragments, line
        ranges, the encoding and the line count are exactly the old ones; a
        test keeps the old algorithm as the definition of "exactly".
        """
        limits = current_limits()
        if len(source.content) > limits.max_text_bytes:
            raise ExtractionFailed(
                "text_size_limit",
                f"Tekstifail on suurem kui {limits.max_text_bytes} baiti. Originaal on alles.",
            )
        encoding = detect_text_encoding(source.content)

        fragments: list[Fragment] = []
        total = 0
        block: list[str] = []
        block_characters = 0
        line_count = 0

        def check(pending: _PendingLine) -> None:
            # A lower bound on the block this line will end up in: refused as
            # soon as even that cannot fit, which is where the old parser would
            # have refused it once the whole block was built.
            guard_character_budget(total + block_characters + pending.least_length(), limits)

        for line in normalised_text_lines(_decoded_pieces(source.content, encoding), check):
            line_count += 1
            if line_count > limits.max_text_lines:
                raise ExtractionFailed(
                    "text_line_limit",
                    f"Tekstifailis on üle {limits.max_text_lines} rea. Originaal on alles.",
                )
            block.append(line)
            block_characters += len(line)
            if len(block) == LINES_PER_FRAGMENT:
                total = _flush_text_block(block, line_count, fragments, total, limits)
                block, block_characters = [], 0
        if block:
            total = _flush_text_block(block, line_count, fragments, total, limits)

        if not fragments:
            raise ExtractionFailed("no_text", "Fail on tühi.")

        return ParseResult(
            derivatives=(
                DerivativePayload(
                    fragments=tuple(fragments),
                    metadata={"encoding": encoding, "line_count": line_count},
                ),
            )
        )


def _flush_text_block(
    block: list[str], last: int, fragments: list[Fragment], total: int, limits: Limits
) -> int:
    body = normalise("\n".join(block))
    if not body:
        return total
    total += len(body)
    guard_character_budget(total, limits)
    if len(fragments) >= limits.max_fragments:
        # The central postcondition's own refusal, made while reading rather
        # than after every fragment has been built.
        raise ExtractionFailed(
            "fragment_limit",
            f"Eraldatud tekst koosneb üle {limits.max_fragments} osast, "
            f"lubatud on {limits.max_fragments}.",
        )
    first = last - len(block) + 1
    fragments.append(
        Fragment(
            text=body,
            locator_kind=LocatorKind.LINE_RANGE,
            locator={"line_from": first, "line_to": last},
            locator_label=f"read {first}–{last}",
        )
    )
    return total


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

        # The pixel ceiling, from the header and before anything is decoded:
        # one refusal for an oversized image whichever path would have
        # decoded it first (csf_9b7b703ca586c216499d33bd, docs/adr/0182).
        with open_bounded_image(source.content, limits):
            pass

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
