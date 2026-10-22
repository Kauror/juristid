"""Outlook `.msg`, via `extract-msg`.

The format is an OLE compound file with no reader in the standard library and
no published specification that is usable without one. The alternative to a
third-party parser is automating Outlook, which would mean a Windows desktop in
the deployment path — for a Linux container serving six lawyers, that is not a
trade worth making (Stage-2B brief 24).

The parser is a dependency like any other: pinned, audited, and confined to
this file, so replacing it later touches one module. Its output is normalised
into the same `ParsedEmail` the EML path produces, which is why the email tests
that matter — provenance, attachment handling, HTML safety — are written once
against that shape rather than twice against two libraries.
"""

from __future__ import annotations

import io
import logging

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
    guard_body_input,
    html_body_to_text,
    parse_date,
    split_attachments,
)
from app.documents.extraction.errors import ExtractionFailed
from app.documents.extraction.limits import CharacterBudget, Limits, current_limits

logger = logging.getLogger(__name__)


class MsgParser:
    name = "msg"
    version = "1"
    mime_types = frozenset({"application/vnd.ms-outlook"})

    def parse(self, source: SourceFile) -> ParseResult:
        import extract_msg

        limits = current_limits()
        try:
            message = extract_msg.openMsg(io.BytesIO(source.content))
        except Exception as error:
            raise ExtractionFailed(
                "unreadable_msg", "Outlooki sõnumit ei õnnestunud lugeda; fail võib olla rikutud."
            ) from error

        try:
            parsed = self._read(message, limits)
        finally:
            # The library holds an OLE handle open. A worker that leaks one per
            # message runs out of descriptors somewhere in the middle of a
            # backlog, which is a failure that looks like a different bug.
            try:
                message.close()
            except Exception:  # pragma: no cover - close of a broken handle
                logger.warning("Could not close an Outlook message handle cleanly")

        if not (parsed.body_text or parsed.subject or parsed.attachments):
            raise ExtractionFailed("empty_message", "Sõnum ei sisalda päist, sisu ega manuseid.")

        return ParseResult(
            derivatives=parsed.payloads(),
            attachments=tuple(parsed.attachments),
            note=" ".join(parsed.notes),
        )

    def _read(self, message: object, limits: Limits) -> ParsedEmail:
        # The same budget as the EML path, charged the same way: each decoded
        # property as it is read, the body measured before it is flattened and
        # charged after (csf_8da83d23).
        budget = CharacterBudget(limits)
        parsed = ParsedEmail()
        parsed.subject = budget.charge(normalise(_text(message, "subject")))
        from_name, from_email = _sender(message)
        parsed.from_name, parsed.from_email = budget.charge(from_name), budget.charge(from_email)
        parsed.to = budget.charge_all(address_list(_text(message, "to")))
        parsed.cc = budget.charge_all(address_list(_text(message, "cc")))
        parsed.bcc = budget.charge_all(address_list(_text(message, "bcc")))
        parsed.sent_at = _sent_at(message)
        parsed.message_id = budget.charge(normalise(_text(message, "messageId")))
        parsed.in_reply_to = budget.charge(normalise(_text(message, "inReplyTo")))
        references = _text(message, "references")
        parsed.references = budget.charge_all(references.split() if references else [])

        body = _text(message, "body")
        if body:
            parsed.body_text = normalise(guard_body_input(body, limits))
        else:
            html = _text(message, "htmlBody")
            if html:
                parsed.body_text = html_body_to_text(guard_body_input(html, limits))
                parsed.body_was_html = True
        budget.charge(parsed.body_text)

        attachments, export_notes = _attachments(message)
        parsed.notes.extend(export_notes)
        parsed.attachments, notes = split_attachments(attachments, limits.max_email_attachments)
        parsed.notes.extend(notes)
        return parsed


def _text(message: object, attribute: str) -> str:
    """Read one field, tolerating a parser that has no opinion about it.

    `extract-msg` raises rather than returning None for properties a particular
    message does not carry, and "this message has no Cc" must not end the
    extraction.
    """
    try:
        value = getattr(message, attribute, None)
    except Exception:
        return ""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _sender(message: object) -> tuple[str, str]:
    raw = _text(message, "sender")
    addresses = address_list(raw)
    if not addresses:
        return normalise(raw), ""
    display = addresses[0]
    if "<" in display and display.endswith(">"):
        name, _, address = display.rpartition("<")
        return name.strip(), address[:-1].strip()
    return ("", display) if "@" in display else (display, "")


def _sent_at(message: object) -> str:
    for attribute in ("date", "sentDate"):
        raw = getattr(message, attribute, None)
        if raw is None:
            continue
        isoformat = getattr(raw, "isoformat", None)
        if callable(isoformat):
            return str(isoformat())
        parsed = parse_date(str(raw))
        if parsed:
            return parsed
    return ""


def _attachments(message: object) -> tuple[list[ParsedAttachment], list[str]]:
    """The message's attachments, and a note for each one that could not be kept."""
    found: list[ParsedAttachment] = []
    lost = 0
    for index, attachment in enumerate(getattr(message, "attachments", []) or []):
        data = getattr(attachment, "data", None)
        if data is None or data == b"":
            # `extract-msg` answers `b""` for an empty stream where the `.eml`
            # parser skips the part. An empty attachment is nothing anybody
            # sent, and passing it on failed the whole message (ENG-033).
            continue
        embedded = False
        if not isinstance(data, bytes | bytearray):
            # A nested `.msg` arrives as a parsed message object rather than
            # bytes. Preserved whole as its own file, not expanded here: Stage
            # 2B keeps an attached message as evidence, and does not become a
            # recursive mail-archive crawler (Stage-2B brief 28).
            #
            # `exportBytes()`, not `export()`: in extract-msg the latter writes
            # to a path it requires, so calling it bare raised a TypeError that
            # was logged at INFO and every forwarded message silently vanished
            # from its parent (AUD-046).
            export = getattr(data, "exportBytes", None)
            if not callable(export):
                lost += 1
                logger.warning("Nested attachment #%d has no exportable form", index + 1)
                continue
            try:
                data = export()
            except Exception:
                lost += 1
                logger.warning("Nested message attachment #%d could not be exported", index + 1)
                continue
            if not isinstance(data, bytes | bytearray) or not data:
                lost += 1
                logger.warning("Nested message attachment #%d exported nothing", index + 1)
                continue
            embedded = True

        name = _text(attachment, "longFilename") or _text(attachment, "shortFilename")
        mime_type = _text(attachment, "mimetype") or "application/octet-stream"
        if embedded:
            # Outlook names an attached message after its subject, with no
            # extension, and the evidence store decides a type by extension:
            # without `.msg` the message would be dropped as an unknown format.
            name = name or _text(attachment, "displayName") or f"manus-{index + 1}"
            if not name.lower().endswith(".msg"):
                name += ".msg"
            mime_type = "application/vnd.ms-outlook"
        name = name or f"manus-{index + 1}.bin"
        content_id = _text(attachment, "cid")
        found.append(
            ParsedAttachment(
                content=bytes(data),
                filename=normalise(name),
                mime_type=mime_type,
                content_id=content_id,
                inline=_is_inline(attachment, content_id),
            )
        )
    notes = []
    if lost:
        notes.append(f"{lost} manust ei õnnestunud kirjast eraldada; originaalkiri on alles.")
    return found, notes


def _is_inline(attachment: object, content_id: str) -> bool:
    """Whether an attachment is part of how the message draws itself.

    A Content-ID alone does not say so: Outlook gives one to ordinary attached
    files too, and treating every such file as a signature logo meant a real
    annex never became a Document (AUD-047). Only an attachment Outlook itself
    hides from the attachment list — which is what it does with images the body
    references — and that carries a Content-ID is taken as inline. Erring the
    other way costs a logo stored as a Document; erring this way cost evidence.
    """
    if not content_id:
        return False
    try:
        return bool(getattr(attachment, "hidden", False))
    except Exception:
        return False


registry.register(MsgParser())
