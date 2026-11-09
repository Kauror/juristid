"""What a later OneNote capture adds, and how far the OneNote coverage has got.

Three tables, each append-only in use (live-sync round 2026-11, agent D):

``OneNoteResourceRevision`` — a later sealed capture's reading of a file
occurrence a Teema already holds (ADR 0205 amendment 2026-11-05, P1/P2). The
first capture's ``LegacySourceResource`` and ``LegacySourceResourceImport`` stay
exactly as they were: one row per (Teema link, occurrence) is still the
idempotency rule. A later capture that reads *other bytes* under the same block
identity becomes the next ``DocumentVersion`` of the same Document (state
``VERSIONED``); one that cannot (the earlier file was held, the new bytes are
empty or too large) is ``HELD`` with its reason; a changed creation time or a
changed structural comment is ``RECORDED`` beside the earlier reading. Never an
overwrite, never a silent skip.

``OneNotePageRevision`` — a later capture's reading of a page whose text, title
or place changed since the page row (or its latest revision) was written (P3).
The ``LegacySourcePage`` row stays the first record.

``OneNoteCoverageMark`` — one progress mark per page (or section): what was
inspected, captured, verified, imported or held, when, by which capture and
whom. The current state of a page is its latest mark, or, with none, what the
records already prove (``onenote_coverage``).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from app.core.models import AppendOnlyModel, BaseModel


class RevisionKind(models.TextChoices):
    CHANGED_BYTES = "CHANGED_BYTES", "Faili baidid muutunud"
    CHANGED_TIME = "CHANGED_TIME", "Ploki loomise aeg muutunud"
    CHANGED_COMMENT = "CHANGED_COMMENT", "Faili märkus muutunud"


class RevisionState(models.TextChoices):
    VERSIONED = "VERSIONED", "Dokumendi uus versioon"
    HELD = "HELD", "Ootab ülevaatust"
    RECORDED = "RECORDED", "Salvestatud varasema kõrvale"


class OneNoteResourceRevision(BaseModel):
    """A later capture's reading of a file occurrence a Teema link already holds."""

    matter_source_page = models.ForeignKey(
        "legacy_import.MatterSourcePage",
        on_delete=models.CASCADE,
        related_name="onenote_revisions",
        verbose_name="teema lähteleht",
    )
    resource = models.ForeignKey(
        "legacy_import.LegacySourceResource",
        on_delete=models.PROTECT,
        related_name="revisions",
        verbose_name="lähtefail",
    )
    import_batch = models.ForeignKey(
        "legacy_import.ImportBatch",
        on_delete=models.PROTECT,
        related_name="onenote_resource_revisions",
        verbose_name="impordipartii",
    )
    capture_id = models.CharField(max_length=100, verbose_name="hõive ID")
    manifest_sha256 = models.CharField(max_length=64, verbose_name="hõive manifesti SHA-256")
    kind = models.CharField(max_length=24, choices=RevisionKind.choices, verbose_name="muutus")
    state = models.CharField(max_length=16, choices=RevisionState.choices, verbose_name="olek")
    reason = models.CharField(max_length=64, blank=True, verbose_name="põhjuse kood")
    detail = models.TextField(blank=True, verbose_name="selgitus")
    # This capture's reading, as sealed. The earlier reading stays on the resource.
    sha256 = models.CharField(max_length=64, verbose_name="SHA-256")
    size_bytes = models.BigIntegerField(default=0, verbose_name="suurus baitides")
    #: The block's creation time in this capture (``ONENOTE_BLOCK_CREATED``) —
    #: never an event date of its own; blank for a floating file (D8).
    source_added_at = models.DateTimeField(null=True, blank=True, verbose_name="lisatud allikasse")
    #: The block's last modification in this capture: when the bytes were last
    #: replaced, at the latest (D2). Source metadata, never an addition date.
    block_modified_at = models.DateTimeField(
        null=True, blank=True, verbose_name="ploki viimane muutmine"
    )
    archive_relative_path = models.TextField(blank=True, verbose_name="tee arhiivis")
    #: This capture's structural context records for the file, verbatim (P2).
    context = models.JSONField(default=list, blank=True, verbose_name="kontekst")
    document = models.ForeignKey(
        "documents.Document",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="onenote_revisions",
        verbose_name="dokument",
    )
    document_version = models.ForeignKey(
        "documents.DocumentVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="onenote_revisions",
        verbose_name="tõendiversioon",
    )

    class Meta:
        verbose_name = "OneNote'i faili hilisem lugemine"
        verbose_name_plural = "OneNote'i failide hilisemad lugemised"
        ordering = ["matter_source_page", "resource", "created_at"]
        constraints = [
            # One reading per (Teema link, occurrence, sealed capture): a capture
            # applied twice records nothing the second time.
            models.UniqueConstraint(
                fields=["matter_source_page", "resource", "manifest_sha256"],
                name="onenote_one_revision_per_link_resource_capture",
            ),
            # A version is made exactly when the state says so.
            models.CheckConstraint(
                condition=(
                    Q(state="VERSIONED", document_version__isnull=False, document__isnull=False)
                    | (
                        ~Q(state="VERSIONED")
                        & Q(document_version__isnull=True)
                        & Q(document__isnull=True)
                    )
                ),
                name="onenote_revision_version_iff_versioned",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.resource} · {self.capture_id} · {self.state}"


class OneNotePageRevision(BaseModel):
    """A later capture's reading of a page whose text, title or place changed (P3)."""

    page = models.ForeignKey(
        "legacy_import.LegacySourcePage",
        on_delete=models.PROTECT,
        related_name="capture_revisions",
        verbose_name="lähteleht",
    )
    import_batch = models.ForeignKey(
        "legacy_import.ImportBatch",
        on_delete=models.PROTECT,
        related_name="onenote_page_revisions",
        verbose_name="impordipartii",
    )
    capture_id = models.CharField(max_length=100, verbose_name="hõive ID")
    manifest_sha256 = models.CharField(max_length=64, verbose_name="hõive manifesti SHA-256")
    source_xml_storage_key = models.CharField(max_length=500, verbose_name="lähte-XML hoidla võti")
    source_xml_sha256 = models.CharField(max_length=64, verbose_name="lähte-XML SHA-256")
    #: Digest of the page's reading (block kinds, texts, creation times; title and
    #: place) — what decides that the page changed. Never block modification
    #: times: navigation touches those (Phase 1 §9c).
    text_signature = models.CharField(max_length=64, verbose_name="sisu sõrmejälg")
    text_changed = models.BooleanField(default=False, verbose_name="tekst muutunud")
    location_changed = models.BooleanField(
        default=False, verbose_name="pealkiri või asukoht muutunud"
    )
    title = models.TextField(blank=True, verbose_name="pealkiri")
    source_section = models.CharField(max_length=300, blank=True, verbose_name="sektsioon")
    source_section_group = models.CharField(
        max_length=400, blank=True, verbose_name="sektsioonirühm"
    )
    source_modified_at = models.DateTimeField(
        null=True, blank=True, verbose_name="muudetud allikas"
    )
    derived_text = models.TextField(blank=True, verbose_name="tuletatud tekst")
    blocks = models.JSONField(default=list, blank=True, verbose_name="plokid")

    class Meta:
        verbose_name = "OneNote'i lehe hilisem lugemine"
        verbose_name_plural = "OneNote'i lehtede hilisemad lugemised"
        ordering = ["page", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["page", "manifest_sha256"],
                name="onenote_one_page_revision_per_capture",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.page} · {self.capture_id}"


class PageState(models.TextChoices):
    """How far one OneNote page (or section) has got (owner brief §8 D5)."""

    NOT_INSPECTED = "NOT_INSPECTED", "Kontrollimata"
    NO_CHANGES = "NO_CHANGES", "Kontrollitud, muudatusi pole"
    NEW_MATERIAL = "NEW_MATERIAL", "Leitud uut materjali"
    CAPTURE_PREPARED = "CAPTURE_PREPARED", "Hõive koostatud"
    VERIFIED = "VERIFIED", "Hõive kontrollitud"
    IMPORTED = "IMPORTED", "Imporditud"
    AWAITING_ASSOCIATION = "AWAITING_ASSOCIATION", "Ootab seostamise otsust"
    FAILED = "FAILED", "Ebaõnnestus või pole ligipääsetav"


class CoverageScope(models.TextChoices):
    PAGE = "PAGE", "Leht"
    SECTION = "SECTION", "Sektsioon"


class OneNoteCoverageMark(AppendOnlyModel):
    """One progress mark. The latest mark of a page (or section) is its state."""

    scope = models.CharField(
        max_length=8, choices=CoverageScope.choices, default=CoverageScope.PAGE
    )
    #: The desktop page GUID, upper-case, without braces. Blank for a section mark.
    page_guid = models.CharField(max_length=36, blank=True, db_index=True)
    #: The section's name as OneNote shows it. Blank for a page mark.
    section = models.CharField(max_length=300, blank=True)
    state = models.CharField(max_length=24, choices=PageState.choices)
    capture_id = models.CharField(max_length=100, blank=True)
    manifest_sha256 = models.CharField(max_length=64, blank=True)
    source_page = models.ForeignKey(
        "legacy_import.LegacySourcePage",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="coverage_marks",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="onenote_coverage_marks",
    )
    reason = models.CharField(max_length=300, blank=True)
    recorded_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "OneNote'i katvuse märge"
        verbose_name_plural = "OneNote'i katvuse märked"
        ordering = ["-recorded_at", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(scope="PAGE", page_guid__regex=r"^[0-9A-F-]{36}$", section="")
                    | (Q(scope="SECTION", page_guid="") & ~Q(section=""))
                ),
                name="onenote_coverage_mark_names_its_scope",
            ),
        ]
        indexes = [
            models.Index(fields=["scope", "page_guid", "recorded_at"], name="onenote_mark_page"),
        ]

    def __str__(self) -> str:
        return f"{self.page_guid or self.section} · {self.state}"
