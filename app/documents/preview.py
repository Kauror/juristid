"""What a lawyer may safely be shown of a file they have not downloaded.

A preview is derived content and is labelled as such everywhere it appears. That
labelling is not decoration: OCR text presented as if it were the document's own
characters is a provenance defect, and somebody quoting a recognition engine's
guess into a legal opinion is entitled to know which one they are reading
(Stage-2B brief 33, 102).

Nothing here renders the source format. There is no path by which an uploaded
HTML file, an SVG, or an email's HTML body becomes markup in a Juristid page:
* Office formats are shown as extracted text.
* Emails are shown as parsed fields and a plain-text body.
* Images are shown through the authorized evidence route with a
  ``Content-Disposition: attachment``, so even a crafted image cannot execute in
  the application's origin.
* Everything a template receives from this module is text, escaped like any
  other text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.documents.enums import (
    DerivativeKind,
    DerivativeStatus,
    ExtractionState,
    TextSource,
)
from app.documents.models import DocumentDerivative, DocumentVersion

#: How much extracted text a preview shows before it stops. The original is one
#: click away and the full text is in the index, so this is a page-weight
#: decision rather than an information one.
PREVIEW_CHARACTER_BUDGET = 40_000
PREVIEW_FRAGMENT_LIMIT = 40

#: What a lawyer is told, in words that describe the situation rather than the
#: enum. "NOT_APPLICABLE" is not a sentence anybody should have to read
#: (Stage-2B brief 35).
STATE_LABELS: dict[str, str] = {
    ExtractionState.PENDING: "Teksti töötlemine ootel",
    ExtractionState.PROCESSING: "Töötlemisel",
    ExtractionState.DONE: "Tekst olemas",
    ExtractionState.FAILED: "Teksti ei õnnestunud eraldada",
    ExtractionState.NOT_APPLICABLE: "Teksti eraldamine ei kohaldu",
}

STATE_TONES: dict[str, str] = {
    ExtractionState.PENDING: "waiting",
    ExtractionState.PROCESSING: "waiting",
    ExtractionState.DONE: "ok",
    ExtractionState.FAILED: "problem",
    ExtractionState.NOT_APPLICABLE: "quiet",
}


@dataclass(frozen=True)
class PreviewFragment:
    locator_label: str
    text: str
    from_ocr: bool


@dataclass(frozen=True)
class EmailField:
    label: str
    value: str


@dataclass(frozen=True)
class Preview:
    """Everything the preview surface needs, already decided.

    Assembled in one place so the template makes no judgements. A template that
    decides whether something is safe to show is a template somebody edits
    without noticing they changed a security property.
    """

    state: str
    state_label: str
    state_tone: str
    note: str = ""
    fragments: tuple[PreviewFragment, ...] = ()
    truncated: bool = False
    total_fragments: int = 0
    email_fields: tuple[EmailField, ...] = ()
    is_image: bool = False
    ocr_used: bool = False
    thumbnail_id: Any = None
    generator: str = ""
    generator_version: str = ""
    built_at: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_text(self) -> bool:
        return bool(self.fragments)

    @property
    def has_anything(self) -> bool:
        return bool(self.fragments or self.email_fields or self.is_image)


EMAIL_FIELD_ORDER: tuple[tuple[str, str], ...] = (
    ("subject", "Teema"),
    ("from_name", "Saatja"),
    ("from_email", "Saatja aadress"),
    ("to", "Saajad"),
    ("cc", "Koopia"),
    ("bcc", "Pimekoopia"),
    ("sent_at", "Saadetud"),
    ("message_id", "Message-ID"),
    ("in_reply_to", "Vastus kirjale"),
)


def active_derivative(version: DocumentVersion, kind: str) -> DocumentDerivative | None:
    return (
        DocumentDerivative.objects.filter(
            version=version, kind=kind, status=DerivativeStatus.ACTIVE
        )
        .order_by("-built_at")
        .first()
    )


def build_preview(version: DocumentVersion) -> Preview:
    """Assemble the safe preview for one exact version.

    Assumes the caller has already established that this user may read the
    document. Authorization is the view's job and is done through the same
    `Document.objects.visible_to` queryset every other document route uses —
    doing it again here would be a second implementation of the rule, which is
    how the two drift apart.
    """
    state = version.extraction_state
    text_derivative = active_derivative(
        version, DerivativeKind.EXTRACTED_TEXT
    ) or active_derivative(version, DerivativeKind.OCR_TEXT)
    email_derivative = active_derivative(version, DerivativeKind.EMAIL_METADATA)

    fragments: list[PreviewFragment] = []
    truncated = False
    total = 0
    if text_derivative is not None:
        rows = list(text_derivative.fragments.order_by("ordinal")[: PREVIEW_FRAGMENT_LIMIT + 1])
        total = text_derivative.fragment_count
        budget = PREVIEW_CHARACTER_BUDGET
        for row in rows[:PREVIEW_FRAGMENT_LIMIT]:
            if budget <= 0:
                truncated = True
                break
            body = row.text[:budget]
            truncated = truncated or len(body) < len(row.text)
            budget -= len(body)
            fragments.append(
                PreviewFragment(
                    locator_label=row.locator_label,
                    text=body,
                    from_ocr=row.text_source == TextSource.OCR,
                )
            )
        truncated = truncated or len(rows) > PREVIEW_FRAGMENT_LIMIT

    email_fields: list[EmailField] = []
    if email_derivative is not None:
        metadata = email_derivative.metadata or {}
        for key, label in EMAIL_FIELD_ORDER:
            value = metadata.get(key)
            if not value:
                continue
            text = ", ".join(value) if isinstance(value, list) else str(value)
            email_fields.append(EmailField(label=label, value=text))

    thumbnail = active_derivative(version, DerivativeKind.THUMBNAIL)
    source = text_derivative or email_derivative
    return Preview(
        thumbnail_id=thumbnail.pk if thumbnail is not None and thumbnail.storage_key else None,
        state=state,
        state_label=STATE_LABELS.get(state, state),
        state_tone=STATE_TONES.get(state, "quiet"),
        note=version.extraction_note,
        is_image=version.mime_type in {"image/png", "image/jpeg"},
        fragments=tuple(fragments),
        truncated=truncated,
        total_fragments=total,
        email_fields=tuple(email_fields),
        ocr_used=any(fragment.from_ocr for fragment in fragments),
        generator=source.generator if source else "",
        generator_version=source.generator_version if source else "",
        built_at=source.built_at if source else None,
        metadata=dict(source.metadata or {}) if source else {},
    )
