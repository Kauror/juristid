"""A Riigikogu file's text, through the application's own parsers (ADR 0225 §5).

A committee's letter is filed as the Word-generated HTML of the e-mail it was
(``Kaasamiskiri_(eelnõu_1082_SE).html``, Windows-1252), or as a PDF. HTML goes
through the same sanitiser e-mail bodies do (`html_body_to_text`: scripts and
styles dropped, text only, never marked safe); PDF and Word through the parsers
uploads use. The text is used and dropped: only its digest and the sentences that
carry a date are kept (no copy of the letter is stored).

**Time** (r96 integration: SEC-008 x SEC-010). A PDF or Word file is parsed behind
the parser boundary (docs/adr/0230), whose batch budget allows 20 minutes a file.
Inside the nightly Riigikogu step that wall is narrowed to the time the stage has
left (`client.time_left`: the 90-minute limit and the step's deadline, ADR 0231),
less `PARSE_MARGIN`, so one parse never carries the step past its budget. With no
time left the file is not parsed, and a parse stopped by that narrowed wall is
`client.OutOfTime` — the stage's ordinary «the rest next night» — never an
unreadable letter.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.riigikogu import client
from app.riigikogu.enums import TextStatus

_CHARSET = re.compile(rb"charset\s*=\s*[\"']?([A-Za-z0-9_\-]+)", re.IGNORECASE)
#: The single-byte encodings a Riigikogu letter is written in, tried after its declared one.
_FALLBACKS = ("utf-8", "cp1257", "cp1252")
#: Seconds kept back from the stage's time when a parse is bounded by it: what is
#: left to settle the document and close the run before the step's deadline.
PARSE_MARGIN = 30.0
MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}


@dataclass(frozen=True)
class Text:
    status: str
    text: str = ""
    digest: str = ""
    why: str = ""


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _decode_html(raw: bytes) -> str | None:
    declared = _CHARSET.search(raw[:4096])
    names = ([declared.group(1).decode("ascii", "ignore")] if declared else []) + list(_FALLBACKS)
    for name in names:
        try:
            return raw.decode(name)
        except (LookupError, UnicodeDecodeError):
            continue
    return None


def _parse_wall() -> float | None:
    """The wall a parse may take inside the stage's time, or ``None`` outside one
    (the boundary's own batch budget then applies). Raises `client.OutOfTime` when
    not even `PARSE_MARGIN` is left."""
    left = client.time_left()
    if left is None:
        return None
    if left <= PARSE_MARGIN + 1:
        raise client.OutOfTime("ajapiir täis; ülejäänu loetakse järgmisel ööl")
    return left - PARSE_MARGIN


def read(raw: bytes, file_name: str) -> Text:
    """The file's text, or why it could not be read. Never raises for a bad file."""
    if not raw:
        return Text(TextStatus.UNREADABLE, why="fail on tühi")
    name = (file_name or "").casefold()
    head = raw[:512].lstrip().lower()
    try:
        if name.endswith((".html", ".htm")) or head.startswith((b"<html", b"<!doctype html")):
            from app.documents.extraction.email_common import html_body_to_text

            decoded = _decode_html(raw)
            if decoded is None:
                return Text(TextStatus.UNREADABLE, digest=digest(raw), why="märgistik tundmatu")
            text = html_body_to_text(decoded)
        else:
            suffix = next((s for s in MIME_BY_SUFFIX if name.endswith(s)), "")
            mime = MIME_BY_SUFFIX.get(suffix, "application/pdf" if raw[:5] == b"%PDF-" else "")
            if not mime:
                return Text(TextStatus.UNREADABLE, digest=digest(raw), why="tundmatu failitüüp")
            from app.documents.extraction import (
                isolation,
                parsers,  # noqa: F401  (registers the parsers)
            )
            from app.documents.extraction.base import SourceFile, registry
            from app.documents.extraction.errors import ExtractionFailed
            from app.documents.extraction.isolation import parse_isolated
            from app.documents.extraction.limits import current_limits, guard_parse_result

            parser = registry.for_mime_type(mime)
            if parser is None:
                return Text(TextStatus.UNREADABLE, digest=digest(raw), why="failitüüpi ei loeta")
            wall = _parse_wall()
            # Behind the parser boundary like every other untrusted file: this
            # runs inside the nightly `docker exec`, whose environment carries
            # the AI keys as well as the database password (docs/adr/0230).
            try:
                result = parse_isolated(
                    parser,
                    SourceFile(content=raw, filename=file_name, mime_type=mime),
                    wall_seconds=wall,
                )
            except ExtractionFailed as error:
                if wall is not None and error.code == isolation.TIMEOUT:
                    # Stopped by the stage's time, not by the file: read next night.
                    raise client.OutOfTime(
                        "ajapiir täis; ülejäänu loetakse järgmisel ööl"
                    ) from None
                raise
            # The postcondition `parse_source` applies to every parser's output;
            # this path calls the parser directly, so it states it (ADR 0176).
            guard_parse_result(result, current_limits())
            text = "\n".join(
                fragment.text for payload in result.derivatives for fragment in payload.fragments
            )
    except client.OutOfTime:
        raise  # the stage's time, not this document's fault (module docstring)
    except Exception as error:  # a parser's refusal is this document's, not the night's
        return Text(TextStatus.UNREADABLE, digest=digest(raw), why=type(error).__name__)
    if not text.strip():
        return Text(TextStatus.UNREADABLE, digest=digest(raw), why="tekstikiht puudub")
    return Text(TextStatus.READ, text=text, digest=digest(raw))
