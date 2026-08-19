"""RFC 822 messages, read with the standard library.

`email` is used rather than a third-party parser because it is maintained
alongside the language, it has no network behaviour to disable, and MIME is one
of the few formats where the standard library's implementation is the reference
one. A message it cannot parse is a message worth reporting rather than one
worth a second parser.
"""

from __future__ import annotations

import email
import email.policy
from email.message import EmailMessage

from app.documents.extraction.base import (
    ParsedAttachment,
    ParseResult,
    SourceFile,
    normalise,
    registry,
)
from app.documents.extraction.email_common import (
    ParsedEmail,
    address_list,
    decode_mime_header,
    html_body_to_text,
    parse_date,
    split_attachments,
)
from app.documents.extraction.errors import ExtractionFailed
from app.documents.extraction.limits import current_limits

#: MIME types we will store as a nested message Document rather than flatten.
MESSAGE_TYPES = frozenset({"message/rfc822", "application/vnd.ms-outlook"})


class EmlParser:
    name = "eml"
    version = "1"
    mime_types = frozenset({"message/rfc822"})

    def parse(self, source: SourceFile) -> ParseResult:
        limits = current_limits()
        try:
            message = email.message_from_bytes(source.content, policy=email.policy.default)
        except Exception as error:
            raise ExtractionFailed(
                "unreadable_eml", "E-kirja ei õnnestunud lugeda; fail võib olla rikutud."
            ) from error
        if not isinstance(message, EmailMessage):  # pragma: no cover - policy guarantees it
            raise ExtractionFailed("unreadable_eml", "E-kirja struktuur on ootamatu.")

        parsed = ParsedEmail()
        parsed.subject = decode_mime_header(message.get("Subject"))
        senders = address_list(message.get("From"))
        if senders:
            parsed.from_name, parsed.from_email = _split_sender(senders[0])
        parsed.to = address_list(message.get("To"))
        parsed.cc = address_list(message.get("Cc"))
        # Only if it is genuinely present. A Bcc header on a received message is
        # rare and interesting; manufacturing an empty one would suggest we
        # looked and found nobody, which is a different claim.
        parsed.bcc = address_list(message.get("Bcc"))
        parsed.sent_at = parse_date(message.get("Date"))
        parsed.message_id = (message.get("Message-ID") or "").strip()
        parsed.in_reply_to = (message.get("In-Reply-To") or "").strip()
        parsed.references = (message.get("References") or "").split()

        parsed.body_text, parsed.body_was_html = _body(message)
        attachments = _attachments(message, limits.max_email_depth)
        parsed.attachments, notes = split_attachments(attachments, limits.max_email_attachments)
        parsed.notes.extend(notes)

        if not (parsed.body_text or parsed.subject or parsed.attachments):
            raise ExtractionFailed("empty_message", "E-kiri ei sisalda päist, sisu ega manuseid.")

        return ParseResult(
            derivatives=parsed.payloads(),
            attachments=tuple(parsed.attachments),
            note=" ".join(parsed.notes),
        )


def _split_sender(display: str) -> tuple[str, str]:
    if "<" in display and display.endswith(">"):
        name, _, address = display.rpartition("<")
        return name.strip(), address[:-1].strip()
    return ("", display) if "@" in display else (display, "")


def _body(message: EmailMessage) -> tuple[str, bool]:
    """Prefer what the sender wrote as text over what their client rendered.

    `text/plain` is the author's own line breaks and no markup at all. Falling
    back to HTML means running it through the sanitiser and flattening it, which
    is correct but lossier — and is the path that has to be safe, because an
    HTML-only message is exactly the kind a phishing attempt arrives as.
    """
    try:
        plain = message.get_body(preferencelist=("plain",))
    except Exception:
        plain = None
    if plain is not None:
        return normalise(_content(plain)), False

    try:
        html = message.get_body(preferencelist=("html",))
    except Exception:
        html = None
    if html is not None:
        return html_body_to_text(_content(html)), True
    return "", False


def _content(part: object) -> str:
    try:
        content = part.get_content()  # type: ignore[attr-defined]
    except Exception:
        return ""
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return str(content)


def _attachments(message: EmailMessage, max_depth: int) -> list[ParsedAttachment]:
    """Every part somebody attached, plus the inline ones, marked apart.

    Nested messages are preserved whole — a forwarded `.eml` becomes one
    attachment, not its own tree of parts — and the depth limit stops a
    mail-archive attachment from turning one intake into a crawl
    (Stage-2B brief 28).
    """
    found: list[ParsedAttachment] = []
    _walk(message, found, depth=0, max_depth=max_depth)
    return found


def _walk(
    message: EmailMessage, found: list[ParsedAttachment], *, depth: int, max_depth: int
) -> None:
    if depth > max_depth:
        raise ExtractionFailed(
            "nesting_limit",
            f"E-kirjade pesastus ületab lubatud {max_depth} taset.",
        )
    for part in message.iter_attachments():
        content_type = part.get_content_type()
        filename = decode_mime_header(part.get_filename()) or _fallback_name(
            content_type, len(found)
        )
        disposition = (part.get("Content-Disposition") or "").lower()
        content_id = (part.get("Content-ID") or "").strip().strip("<>")
        # Inline means the message draws with it: a signature logo, a bullet
        # image, a tracking pixel. It is recorded and deliberately not promoted
        # to a Document (Stage-2B brief 27).
        inline = "inline" in disposition or bool(content_id)

        try:
            payload = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True)
        if isinstance(payload, str):
            payload = payload.encode("utf-8", errors="replace")
        elif isinstance(payload, EmailMessage):
            payload = payload.as_bytes()
        if not payload:
            continue

        found.append(
            ParsedAttachment(
                content=bytes(payload),
                filename=filename,
                mime_type=content_type,
                content_id=content_id,
                inline=inline,
            )
        )


def _fallback_name(content_type: str, index: int) -> str:
    suffix = {
        "application/pdf": "pdf",
        "image/png": "png",
        "image/jpeg": "jpg",
        "message/rfc822": "eml",
        "text/plain": "txt",
    }.get(content_type, "bin")
    return f"manus-{index + 1}.{suffix}"


registry.register(EmlParser())
