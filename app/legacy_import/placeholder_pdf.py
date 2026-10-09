"""The 2026 pilot's placeholder evidence: a PDF that says it is not an opinion.

A canonical SENT `Submission` must carry its exact final evidence — the
database refuses one without (`submissions_sent_requires_timestamp_and_evidence`)
and that rule is not loosened for the pilot. The register says *that* the Chamber
sent an opinion on a date; it does not hold the letter. So the pilot generates
one page per imported send that states, in capitals, what it is:

    PROOVIMPORT — ASENDUSDOKUMENT
    SEE EI OLE TEGELIK KOJA ARVAMUS

and below it the register reference, the `VÄLJA` date and why the file exists.
Nothing in it resembles the content of a legal opinion — no argument, no
summary, no position — because a placeholder that read like one would be the
first thing somebody quoted (docs/adr/0148 §7).

**Deterministic and self-contained.** A hand-written PDF 1.4 with the two
standard Helvetica faces in WinAnsi encoding, which covers every Estonian letter
(õ ä ö ü š ž and their capitals), and no creation date — the same inputs give the
same bytes, so a rehearsal and the hosted run produce the same digests and a
test can pin them. A character WinAnsi cannot carry becomes «?», never a
dropped letter that silently changes a word.
"""

from __future__ import annotations

import datetime as dt
import textwrap
from dataclasses import dataclass

#: Recorded in the PDF and on every placeholder `DocumentVersion`. Bumped when
#: the page's wording or layout changes.
PLACEHOLDER_MARK = "excel-pilot-2026-placeholder/1"

HEADLINE = "PROOVIMPORT — ASENDUSDOKUMENT"
SUBHEADLINE = "SEE EI OLE TEGELIK KOJA ARVAMUS"

EXPLANATION: tuple[str, ...] = (
    "See dokument on loodud ainult Juristidi proovikasutuse testimiseks.",
    "Koda saatis selles teemas arvamuse registris märgitud kuupäeval, kuid arvamuse "
    "teksti ei ole proovikasutusse imporditud. Selle faili sisu ei ole Koja seisukoht "
    "ega selle kokkuvõte ning seda ei tohi kasutada Koja arvamuse allikana.",
    "Kui süsteem päriselt kasutusele võetakse, asendatakse kogu proovikasutuse "
    "andmestik uue impordiga, mis sisaldab tegelikke dokumente.",
)


@dataclass(frozen=True)
class PlaceholderFacts:
    """What one placeholder page states — all of it from the register row."""

    reference: str
    title: str
    sent_on: dt.date
    addressee_raw: str
    workbook_name: str
    workbook_sha256: str
    sheet: str
    row_number: int


def placeholder_filename(sent_on: dt.date) -> str:
    """The original filename: a placeholder at a glance, with no register number."""
    return f"PROOVIMPORT-ASENDUSDOKUMENT-koja-arvamus-{sent_on.isoformat()}.pdf"


def placeholder_title(sent_on: dt.date) -> str:
    """The document's title on the Matter — and so the opinion's, as natively."""
    return f"PROOVIMPORT — asendusdokument, Koja arvamus {sent_on:%d.%m.%Y}"


def placeholder_lines(facts: PlaceholderFacts) -> list[str]:
    """The body text, line by line, before wrapping."""
    lines = [
        f"Teema viide registris: {facts.reference}",
        f"Teema: {' '.join(facts.title.split())}",
        f"Arvamuse saatmise kuupäev registris (VÄLJA): {facts.sent_on:%d.%m.%Y}",
        f"Adressaat registris (KELLELE): {' '.join(facts.addressee_raw.split()) or '—'}",
        "",
        *EXPLANATION,
        "",
        f"Allikas: {facts.workbook_name}, leht {facts.sheet}, rida {facts.row_number}",
        f"Töövihiku SHA-256: {facts.workbook_sha256}",
        f"Tehniline märgis: {PLACEHOLDER_MARK}",
    ]
    return lines


def _pdf_text(value: str) -> bytes:
    """A PDF literal string in WinAnsi, with the three special bytes escaped."""
    encoded = value.encode("cp1252", errors="replace")
    return encoded.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _pdf_info_text(value: str) -> bytes:
    """A document-information string: UTF-16BE with its byte-order mark, as hex.

    The info dictionary is read in PDFDocEncoding, not WinAnsi, so the em dash
    the page body carries would read back as another letter there.
    """
    return b"<" + ("﻿" + value).encode("utf-16-be").hex().upper().encode() + b">"


def render_placeholder_pdf(facts: PlaceholderFacts) -> bytes:
    """One A4 page. Deterministic: no clock, no randomness, no metadata dates."""
    wrapped: list[str] = []
    for line in placeholder_lines(facts):
        wrapped.extend(textwrap.wrap(line, width=92) or [""])

    content = bytearray()
    content += b"BT /F1 18 Tf 56 776 Td (" + _pdf_text(HEADLINE) + b") Tj ET\n"
    content += b"BT /F1 14 Tf 56 752 Td (" + _pdf_text(SUBHEADLINE) + b") Tj ET\n"
    content += b"BT /F2 10 Tf 14 TL 56 716 Td\n"
    for index, line in enumerate(wrapped):
        if index:
            content += b"T* "
        content += b"(" + _pdf_text(line) + b") Tj\n"
    content += b"ET\n"

    info_title = _pdf_info_text(f"{HEADLINE} {facts.reference}")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length "
        + str(len(content)).encode()
        + b" >>\nstream\n"
        + bytes(content)
        + b"endstream",
        b"<< /Title " + info_title + b" /Producer " + _pdf_info_text(PLACEHOLDER_MARK) + b" >>",
    ]

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {len(objects)} 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)
