"""Derived content: rebuildable, disposable, and never evidence.

Three models live here and they share one property. Everything in them can be
deleted in full and regenerated from the ``DocumentVersion`` bytes, and the
product is expected to survive that with no loss beyond the time it takes. That
is what makes it safe to let a parser be wrong, to upgrade a parser, or to throw
the whole derived world away and rebuild it (Stage-2B brief 5, 47).

The relationship runs one way and must keep running one way::

    DocumentVersion (evidence, immutable)
        -> DocumentDerivative (what a parser made of it)
            -> DocumentTextFragment (that text, with somewhere to point)

``EmailAttachmentLink`` is the exception that proves the rule. It is *not*
derived data even though a parser creates it: it records that one exact binary
arrived inside another exact binary, which is a fact about the evidence rather
than an opinion about its contents. Deleting it would destroy provenance that
cannot be recomputed once the parser has moved on, so it sits under PROTECT and
survives a derivative rebuild.
"""

from __future__ import annotations

from django.db import models

from app.core.models import BaseModel
from app.documents.enums import (
    DerivativeKind,
    DerivativeStatus,
    LocatorKind,
    TextSource,
)


class DocumentDerivative(BaseModel):
    """One parser's output for one exact ``DocumentVersion``."""

    version = models.ForeignKey(
        "documents.DocumentVersion",
        on_delete=models.CASCADE,
        related_name="derivatives",
        verbose_name="tõendiversioon",
    )
    kind = models.CharField(
        max_length=32,
        choices=DerivativeKind.choices,
        db_index=True,
        verbose_name="tuletise liik",
    )

    # Which code produced this, and which version of it. Recorded because
    # "rebuild everything the old parser touched" is otherwise unanswerable, and
    # because a fragment's trustworthiness is a property of the parser that made
    # it.
    generator = models.CharField(max_length=64, verbose_name="tuletaja")
    generator_version = models.CharField(max_length=64, verbose_name="tuletaja versioon")

    status = models.CharField(
        max_length=16,
        choices=DerivativeStatus.choices,
        default=DerivativeStatus.BUILDING,
        db_index=True,
        verbose_name="olek",
    )

    # Of the derived payload, not of the evidence. Lets a rebuild prove it
    # reproduced the same thing without re-reading every fragment.
    content_sha256 = models.CharField(max_length=64, blank=True, verbose_name="sisu SHA-256")
    # Only for derivatives that are files rather than rows — previews and
    # thumbnails. Deliberately a separate storage class from evidence, so a
    # backup that omits it is still a complete backup (Stage-2B brief 9).
    storage_key = models.CharField(max_length=500, blank=True, verbose_name="hoidla võti")

    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="metaandmed",
        help_text="Parseri struktuuritulemus: e-kirja päised, lehekülgede arv, OCR-i osakaal.",
    )

    error_code = models.CharField(max_length=64, blank=True, verbose_name="vea kood")
    error_detail = models.TextField(
        blank=True,
        verbose_name="vea kirjeldus",
        help_text="Operaatorile loetav põhjus. Ei sisalda dokumendi sisu.",
    )

    character_count = models.PositiveIntegerField(default=0, verbose_name="märkide arv")
    fragment_count = models.PositiveIntegerField(default=0, verbose_name="fragmentide arv")
    built_at = models.DateTimeField(null=True, blank=True, verbose_name="valmis")

    class Meta:
        verbose_name = "dokumendi tuletis"
        verbose_name_plural = "dokumendi tuletised"
        ordering = ["version", "kind", "-created_at"]
        constraints = [
            # At most one live derivative of each kind per version. This is the
            # idempotency rule: reprocessing the same bytes with the same parser
            # cannot leave two active representations behind, and an upgrade
            # publishes by demoting the old row and promoting the new one in the
            # same transaction (Stage-2B brief 8).
            models.UniqueConstraint(
                fields=["version", "kind"],
                condition=models.Q(status=DerivativeStatus.ACTIVE),
                name="documents_one_active_derivative_per_kind",
            ),
        ]
        indexes = [
            models.Index(fields=["version", "kind", "status"], name="documents_derivative_lookup"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.generator} {self.generator_version}"


class DocumentTextFragment(BaseModel):
    """A bounded piece of extracted text that knows where it came from.

    Storing a 300-page draft as one opaque string makes every match say only
    "somewhere in this file", which for a legal document is barely better than
    not finding it. A fragment carries a locator so a result can say ``lk 17``
    and open there.
    """

    derivative = models.ForeignKey(
        DocumentDerivative,
        on_delete=models.CASCADE,
        related_name="fragments",
        verbose_name="tuletis",
    )
    ordinal = models.PositiveIntegerField(verbose_name="järjekord")
    text = models.TextField(verbose_name="tekst")

    text_source = models.CharField(
        max_length=16,
        choices=TextSource.choices,
        default=TextSource.NATIVE,
        verbose_name="teksti päritolu",
    )
    locator_kind = models.CharField(
        max_length=16,
        choices=LocatorKind.choices,
        default=LocatorKind.NONE,
        verbose_name="asukoha liik",
    )
    locator = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="asukoht",
        help_text="Struktuurne asukoht, näiteks lehekülje number või töölehe nimi.",
    )
    locator_label = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="asukoha nimetus",
        help_text="Eestikeelne kuju, mida otsingutulemus näitab.",
    )
    character_count = models.PositiveIntegerField(default=0, verbose_name="märkide arv")

    class Meta:
        verbose_name = "dokumendi tekstiosa"
        verbose_name_plural = "dokumendi tekstiosad"
        ordering = ["derivative", "ordinal"]
        constraints = [
            models.UniqueConstraint(
                fields=["derivative", "ordinal"],
                name="documents_unique_fragment_ordinal",
            ),
        ]
        indexes = [
            models.Index(fields=["derivative", "ordinal"], name="documents_fragment_order"),
        ]

    def __str__(self) -> str:
        return self.locator_label or f"#{self.ordinal}"


class AttachmentDisposition(models.TextChoices):
    """Whether a message part is a file somebody attached, or part of how the
    message draws itself.

    A signature logo and a tracking pixel are inline resources. Turning them
    into Documents would bury the two attachments that matter under nine that do
    not, every time somebody forwards a threaded conversation
    (Stage-2B brief 27).
    """

    ATTACHMENT = "ATTACHMENT", "Manus"
    INLINE = "INLINE", "Sisene ressurss"


class EmailAttachmentLink(BaseModel):
    """This exact binary arrived inside that exact message.

    Deliberately a row and not a sentence in ``provenance_note``. "Which
    original email did this PDF come from" is a question the database has to be
    able to answer years later, when the parser that knew has been replaced
    twice and the free text says *saabus e-kirjaga* (Stage-2B brief 25).

    SHA-256, MIME type and size are **not** copied here. They are on the
    attachment's own ``DocumentVersion``, which this row points at, and a second
    copy is a second thing that can disagree with the evidence.
    """

    parent_version = models.ForeignKey(
        "documents.DocumentVersion",
        on_delete=models.PROTECT,
        related_name="email_attachments",
        verbose_name="e-kirja versioon",
    )
    attachment_version = models.OneToOneField(
        "documents.DocumentVersion",
        on_delete=models.PROTECT,
        related_name="email_origin",
        verbose_name="manuse versioon",
    )

    ordinal = models.PositiveIntegerField(verbose_name="järjekord")
    # What the message called it, which is not necessarily what got stored: a
    # message may name two attachments identically, and the stored filename is
    # sanitised. Both are worth keeping.
    declared_filename = models.CharField(max_length=400, verbose_name="manuse nimi kirjas")
    content_id = models.CharField(max_length=200, blank=True, verbose_name="content-id")
    disposition = models.CharField(
        max_length=16,
        choices=AttachmentDisposition.choices,
        default=AttachmentDisposition.ATTACHMENT,
        verbose_name="liik",
    )

    class Meta:
        verbose_name = "e-kirja manuse seos"
        verbose_name_plural = "e-kirja manuste seosed"
        ordering = ["parent_version", "ordinal"]
        constraints = [
            models.UniqueConstraint(
                fields=["parent_version", "ordinal"],
                name="documents_unique_attachment_ordinal",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.declared_filename} (#{self.ordinal})"
