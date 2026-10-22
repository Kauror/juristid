"""RFC 822 messages, read with the standard library.

`email` is used rather than a third-party parser because it is maintained
alongside the language, it has no network behaviour to disable, and MIME is one
of the few formats where the standard library's implementation is the reference
one. A message it cannot parse is a message worth reporting rather than one
worth a second parser.
"""

from __future__ import annotations

import email
import email.parser
import email.policy
from email.message import EmailMessage
from typing import Any

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
    guard_body_input,
    html_body_to_text,
    parse_date,
    split_attachments,
)
from app.documents.extraction.errors import ExtractionFailed
from app.documents.extraction.limits import CharacterBudget, Limits, current_limits


def parse_bounded_message(content: bytes, limits: Limits) -> EmailMessage:
    """``email.message_from_bytes`` that stops at ``max_email_parts`` MIME parts.

    The standard parser builds one message object per MIME part, nested
    attached messages included, before anything here can count them, so a
    message of a few megabytes made of empty parts was hundreds of thousands
    of objects first. The parser is the same; its message factory counts every
    object it is asked for and refuses the message the moment the count passes
    the limit, while the parse is still running (docs/adr/0182).
    """
    created = 0
    # The feed parser calls its factory once on construction to probe its
    # signature; that object is never part of the message.
    allowed = limits.max_email_parts + 1

    def factory(*args: Any, **kwargs: Any) -> EmailMessage:
        nonlocal created
        created += 1
        if created > allowed:
            raise ExtractionFailed(
                "email_part_limit",
                f"E-kirjas on üle {limits.max_email_parts} MIME-osa. Originaal on alles.",
            )
        return EmailMessage(*args, **kwargs)

    parser = email.parser.BytesParser(_class=factory, policy=email.policy.default)
    message = parser.parsebytes(content)
    if not isinstance(message, EmailMessage):  # pragma: no cover - the factory makes them
        raise ExtractionFailed("unreadable_eml", "E-kirja struktuur on ootamatu.")
    return message


class EmlParser:
    name = "eml"
    version = "1"
    mime_types = frozenset({"message/rfc822"})

    def parse(self, source: SourceFile) -> ParseResult:
        limits = current_limits()
        try:
            message = parse_bounded_message(source.content, limits)
        except ExtractionFailed:
            raise
        except Exception as error:
            raise ExtractionFailed(
                "unreadable_eml", "E-kirja ei õnnestunud lugeda; fail võib olla rikutud."
            ) from error
        if not isinstance(message, EmailMessage):  # pragma: no cover - policy guarantees it
            raise ExtractionFailed("unreadable_eml", "E-kirja struktuur on ootamatu.")

        # Every decoded header and the flattened body are charged to one
        # budget as they are made, so an oversized message is refused while
        # its text is being assembled rather than after (csf_8da83d23).
        budget = CharacterBudget(limits)
        parsed = ParsedEmail()
        parsed.subject = budget.charge(decode_mime_header(message.get("Subject")))
        senders = address_list(message.get("From"))
        if senders:
            parsed.from_name, parsed.from_email = _split_sender(budget.charge(senders[0]))
        parsed.to = budget.charge_all(address_list(message.get("To")))
        parsed.cc = budget.charge_all(address_list(message.get("Cc")))
        # Only if it is genuinely present. A Bcc header on a received message is
        # rare and interesting; manufacturing an empty one would suggest we
        # looked and found nobody, which is a different claim.
        parsed.bcc = budget.charge_all(address_list(message.get("Bcc")))
        parsed.sent_at = parse_date(message.get("Date"))
        parsed.message_id = budget.charge((message.get("Message-ID") or "").strip())
        parsed.in_reply_to = budget.charge((message.get("In-Reply-To") or "").strip())
        parsed.references = budget.charge_all((message.get("References") or "").split())

        parsed.body_text, parsed.body_was_html = _body(message, limits)
        budget.charge(parsed.body_text)
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


def _body(message: EmailMessage, limits: Limits) -> tuple[str, bool]:
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
        return normalise(guard_body_input(_content(plain), limits)), False

    try:
        html = message.get_body(preferencelist=("html",))
    except Exception:
        html = None
    if html is not None:
        return html_body_to_text(guard_body_input(_content(html), limits)), True
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
        content_id = (part.get("Content-ID") or "").strip().strip("<>")
        # Inline means the message draws with it: a signature logo, a bullet
        # image, a tracking pixel. It is recorded and deliberately not promoted
        # to a Document (Stage-2B brief 27).
        #
        # The sender's explicit disposition wins. Mail clients put a Content-ID
        # on ordinary attachments too, and reading every one of those as a logo
        # meant a real annex never became evidence (AUD-047). Only a part that
        # says nothing either way falls back to the Content-ID, which is what an
        # image referenced from an HTML body usually looks like. The parsed
        # disposition type, not a substring: `filename="inline.pdf"` is not one.
        disposition = part.get_content_disposition()
        if disposition == "attachment":
            inline = False
        elif disposition == "inline":
            inline = True
        else:
            inline = bool(content_id)

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
