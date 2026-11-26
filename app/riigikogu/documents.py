"""A Riigikogu file's text, through the application's own parsers (ADR 0225 §5).

A committee's letter is filed as the Word-generated HTML of the e-mail it was
(``Kaasamiskiri_(eelnõu_1082_SE).html``, Windows-1252), or as a PDF. HTML goes
through the same sanitiser e-mail bodies do (`html_body_to_text`: scripts and
styles dropped, text only, never marked safe); PDF and Word through the parsers
uploads use. The text is used and dropped: only its digest and the sentences that
carry a date are kept (no copy of the letter is stored).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.riigikogu.enums import TextStatus

_CHARSET = re.compile(rb"charset\s*=\s*[\"']?([A-Za-z0-9_\-]+)", re.IGNORECASE)
#: The single-byte encodings a Riigikogu letter is written in, tried after its declared one.
_FALLBACKS = ("utf-8", "cp1257", "cp1252")
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
            from app.documents.extraction import parsers  # noqa: F401  (registers the parsers)
            from app.documents.extraction.base import SourceFile, registry
            from app.documents.extraction.isolation import parse_isolated
            from app.documents.extraction.limits import current_limits, guard_parse_result

            parser = registry.for_mime_type(mime)
            if parser is None:
                return Text(TextStatus.UNREADABLE, digest=digest(raw), why="failitüüpi ei loeta")
            # Behind the parser boundary like every other untrusted file: this
            # runs inside the nightly `docker exec`, whose environment carries
            # the AI keys as well as the database password (docs/adr/0230).
            result = parse_isolated(
                parser, SourceFile(content=raw, filename=file_name, mime_type=mime)
            )
            # The postcondition `parse_source` applies to every parser's output;
            # this path calls the parser directly, so it states it (ADR 0176).
            guard_parse_result(result, current_limits())
            text = "\n".join(
                fragment.text for payload in result.derivatives for fragment in payload.fragments
            )
    except Exception as error:  # a parser's refusal is this document's, not the night's
        return Text(TextStatus.UNREADABLE, digest=digest(raw), why=type(error).__name__)
    if not text.strip():
        return Text(TextStatus.UNREADABLE, digest=digest(raw), why="tekstikiht puudub")
    return Text(TextStatus.READ, text=text, digest=digest(raw))
