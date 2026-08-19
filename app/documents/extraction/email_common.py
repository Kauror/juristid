"""What EML and MSG have in common once the container is open.

Both formats end up as the same thing: a set of headers, one or two body
representations, and a list of parts. The two parsers differ only in how they
get there, so everything after that point lives here and is tested once.

**The message binary is the evidence. This is a derivative.** Sender, date,
subject and body are what a parser made of the file, and if the parser is wrong
the original is still there to be re-read. That ordering is why nothing in this
module is allowed to write back over a Matter's fields (Stage-2B brief 22, 29).

**Nothing here touches the network.** An HTML body is sanitised, never rendered
and never fetched from: remote images are tracking pixels as often as they are
logos, external CSS is code, and a URL in an untrusted message is a thing to
display as text rather than to resolve (Stage-2B brief 23).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime

from app.core.richtext import plain_text
from app.documents.enums import DerivativeKind, LocatorKind
from app.documents.extraction.base import (
    DerivativePayload,
    Fragment,
    ParsedAttachment,
    normalise,
)

#: Headers worth keeping verbatim. Message-ID, In-Reply-To and References are
#: here despite Stage 2B building no thread model: they are the only evidence
#: that survives of how a conversation actually hung together, they cost three
#: strings, and they cannot be reconstructed later from anything else
#: (Stage-2B brief 30).
THREAD_HEADERS = ("message-id", "in-reply-to", "references")


@dataclass
class ParsedEmail:
    """A message, reduced to the parts that are worth indexing."""

    subject: str = ""
    from_name: str = ""
    from_email: str = ""
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    sent_at: str = ""
    message_id: str = ""
    in_reply_to: str = ""
    references: list[str] = field(default_factory=list)
    body_text: str = ""
    body_was_html: bool = False
    attachments: list[ParsedAttachment] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def metadata(self) -> dict[str, object]:
        """The structured record stored on the EMAIL_METADATA derivative.

        Absent headers are absent, not empty strings pretending to be data. A
        message with no `Date:` is a real thing and inventing one would put a
        confident wrong timestamp on the file's first day (Stage-2B brief 22).
        """
        record: dict[str, object] = {}
        for key, value in (
            ("subject", self.subject),
            ("from_name", self.from_name),
            ("from_email", self.from_email),
            ("sent_at", self.sent_at),
            ("message_id", self.message_id),
            ("in_reply_to", self.in_reply_to),
            ("body_format", "html" if self.body_was_html else "text"),
        ):
            if value:
                record[key] = value
        for key, values in (
            ("to", self.to),
            ("cc", self.cc),
            ("bcc", self.bcc),
            ("references", self.references),
        ):
            if values:
                record[key] = values
        record["attachment_count"] = sum(1 for a in self.attachments if not a.inline)
        record["inline_resource_count"] = sum(1 for a in self.attachments if a.inline)
        if self.notes:
            record["notes"] = self.notes
        return record

    def header_summary(self) -> str:
        """The headers as searchable text.

        Indexed alongside the body because "who sent that" and "what was it
        called" are how people actually look for a message, and neither is in
        the body.
        """
        lines = []
        if self.subject:
            lines.append(self.subject)
        sender = " ".join(part for part in (self.from_name, self.from_email) if part)
        if sender:
            lines.append(f"Saatja: {sender}")
        for label, values in (("Saajad", self.to), ("Koopia", self.cc)):
            if values:
                lines.append(f"{label}: {', '.join(values)}")
        return "\n".join(lines)

    def payloads(self) -> tuple[DerivativePayload, ...]:
        fragments: list[Fragment] = []
        header_text = self.header_summary()
        if header_text:
            fragments.append(
                Fragment(
                    text=header_text,
                    locator_kind=LocatorKind.SECTION,
                    locator={"part": "headers"},
                    locator_label="kirja päis",
                )
            )
        if self.body_text:
            fragments.append(
                Fragment(
                    text=self.body_text,
                    locator_kind=LocatorKind.BODY,
                    locator={"part": "body"},
                    locator_label="kirja sisu",
                )
            )
        return (
            DerivativePayload(
                kind=DerivativeKind.EMAIL_METADATA,
                metadata=self.metadata(),
            ),
            DerivativePayload(
                kind=DerivativeKind.EXTRACTED_TEXT,
                fragments=tuple(fragments),
                metadata={"body_format": "html" if self.body_was_html else "text"},
            ),
        )


def decode_mime_header(raw: str | None) -> str:
    """`=?UTF-8?B?...?=` back into readable Estonian.

    A subject line left encoded is a subject line nobody can search for.
    """
    if not raw:
        return ""
    try:
        return normalise(str(make_header(decode_header(raw))))
    except Exception:
        return normalise(raw)


def address_list(raw: str | None) -> list[str]:
    """`Name <a@b>, c@d` into display strings, keeping both halves.

    Both are kept because both get searched: people remember the name, systems
    record the address, and which one a lawyer types depends on which they had
    in front of them.
    """
    if not raw:
        return []
    out: list[str] = []
    for name, address in getaddresses([raw]):
        display = decode_mime_header(name)
        if display and address:
            out.append(f"{display} <{address}>")
        elif address:
            out.append(address)
        elif display:
            out.append(display)
    return out


def parse_date(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError):
        # A malformed Date: header is recorded as absent rather than guessed.
        return ""


_STYLE_AND_SCRIPT = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


def html_body_to_text(html: str) -> str:
    """An HTML body reduced to text, safely.

    Two passes and both matter. Script and style *elements* go first, contents
    included: a sanitiser that strips tags but keeps text would otherwise fold
    a stylesheet into the indexed body. Then the existing allowlist sanitiser
    runs — the same one that guards authored entry bodies — and the result is
    flattened to text.

    The output of this function is text. It is never marked safe, never
    inserted as markup, and never used to render the message
    (Stage-2B brief 23, 70).
    """
    stripped = _STYLE_AND_SCRIPT.sub(" ", html or "")
    return normalise(plain_text(stripped))


def split_attachments(
    parts: list[ParsedAttachment], limit: int
) -> tuple[list[ParsedAttachment], list[str]]:
    """Enforce the attachment ceiling, honestly.

    Refuses rather than trims. A message quietly reduced to its first fifty
    attachments looks identical to one that had fifty, and the annexes that got
    dropped are the ones nobody knows to ask about.
    """
    real = [part for part in parts if not part.inline]
    if len(real) > limit:
        from app.documents.extraction.errors import ExtractionFailed

        raise ExtractionFailed(
            "attachment_limit",
            f"Kirjas on {len(real)} manust, lubatud on {limit}.",
        )
    notes: list[str] = []
    inline = len(parts) - len(real)
    if inline:
        notes.append(f"{inline} sisest ressurssi (allkirjapildid vms) ei salvestatud dokumendina.")
    return parts, notes
