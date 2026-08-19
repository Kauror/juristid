"""Import provenance.

Nothing here interprets history. It records, verbatim and immutably, what the
source said and how a Matter came to be matched to it, so that a future
maintainer can tell an imported guess from a verified fact — and so that
nobody can quietly "clean" a legitimate historical anomaly
(master specification 11.2, 19.3, 19.9).
"""

from __future__ import annotations

from typing import Any

from django.db import models

from app.core.errors import ImmutableRecordError
from app.core.models import BaseModel


class ReconciliationStatus(models.TextChoices):
    RUNNING = "RUNNING", "Töötab"
    COMPLETED = "COMPLETED", "Lõpetatud"
    COMPLETED_WITH_GAPS = "COMPLETED_WITH_GAPS", "Lõpetatud lünkadega"
    FAILED = "FAILED", "Ebaõnnestus"


class MatchMethod(models.TextChoices):
    """How a source row was tied to a Matter (specification 19.7)."""

    DETERMINISTIC_IDENTIFIER = "DETERMINISTIC_IDENTIFIER", "Determineeritud identifikaator"
    REFERENCE_TOKEN = "REFERENCE_TOKEN", "Viitenumber või ametlik ID"
    EXACT_URL = "EXACT_URL", "Täpne URL"
    FUZZY_CANDIDATE = "FUZZY_CANDIDATE", "Mitmesignaaliline sarnasus"
    HUMAN_REVIEW = "HUMAN_REVIEW", "Inimese otsus"
    UNMATCHED = "UNMATCHED", "Sidumata"


class ConflictState(models.TextChoices):
    NONE = "NONE", "Konflikti pole"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE", "Vastuoluline tõendus"
    RESOLVED_BY_REVIEW = "RESOLVED_BY_REVIEW", "Lahendatud ülevaatusega"
    UNRESOLVED = "UNRESOLVED", "Lahendamata"


class ImportBatch(BaseModel):
    """One reproducible import run."""

    source_system = models.CharField(max_length=100, verbose_name="lähtesüsteem")
    source_file_name = models.CharField(max_length=400, blank=True, verbose_name="lähtefail")
    source_snapshot_sha256 = models.CharField(
        max_length=64,
        blank=True,
        verbose_name="hetktõmmise SHA-256",
        help_text="Baiditäpse lähteallika kontrollsumma.",
    )
    importer_version = models.CharField(max_length=50, verbose_name="importija versioon")
    contract_version = models.CharField(
        max_length=50,
        verbose_name="lepingu versioon",
        help_text="docs/data-contracts all oleva ajastulepingu versioon.",
    )
    started_at = models.DateTimeField(verbose_name="algas")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="lõppes")
    source_row_count = models.PositiveIntegerField(default=0, verbose_name="ridu allikas")
    created_matter_count = models.PositiveIntegerField(default=0, verbose_name="loodud teemasid")
    matched_count = models.PositiveIntegerField(default=0, verbose_name="seotud ridu")
    unmatched_count = models.PositiveIntegerField(default=0, verbose_name="sidumata ridu")
    reconciliation_status = models.CharField(
        max_length=32,
        choices=ReconciliationStatus.choices,
        default=ReconciliationStatus.RUNNING,
        verbose_name="võrdlusseis",
    )
    notes = models.TextField(blank=True, verbose_name="märkused")

    class Meta:
        verbose_name = "impordipartii"
        verbose_name_plural = "impordipartiid"
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"{self.source_system} {self.started_at:%Y-%m-%d %H:%M}"


class MatterSourceReference(BaseModel):
    """Immutable evidence of where one Matter came from.

    The raw columns are write-once. A better interpretation is recorded by
    adding a new reference or by changing the *interpreted* fields on Matter,
    never by editing what the source said.
    """

    RAW_FIELDS = (
        "source_system",
        "source_file_name",
        "source_sheet",
        "source_row_number",
        "source_row_raw",
        "source_title",
        "source_date_raw",
        "onenote_page_id",
        "onenote_url",
    )

    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.PROTECT,
        related_name="source_references",
        verbose_name="teema",
    )
    import_batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="source_references",
        verbose_name="impordipartii",
    )

    source_system = models.CharField(max_length=100, verbose_name="lähtesüsteem")
    source_file_name = models.CharField(max_length=400, blank=True)
    source_sheet = models.CharField(max_length=200, blank=True)
    source_row_number = models.PositiveIntegerField(null=True, blank=True)
    source_row_raw = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="algne rida",
        help_text="Lähterea väärtused täpselt nii, nagu need allikas olid.",
    )
    source_title = models.TextField(blank=True)
    source_date_raw = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="algne kuupäev",
        help_text="Töötlemata kujul, kaasa arvatud järjenumbrid ja vigased väärtused.",
    )
    onenote_page_id = models.CharField(max_length=200, blank=True)
    onenote_url = models.TextField(blank=True)

    match_method = models.CharField(
        max_length=40,
        choices=MatchMethod.choices,
        default=MatchMethod.UNMATCHED,
        verbose_name="sidumise meetod",
    )
    match_confidence = models.DecimalField(
        max_digits=4,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name="kindlus",
    )
    conflict_state = models.CharField(
        max_length=32,
        choices=ConflictState.choices,
        default=ConflictState.NONE,
        verbose_name="konflikt",
    )
    reviewed_by = models.CharField(max_length=200, blank=True, verbose_name="üle vaadanud")
    review_note = models.TextField(blank=True, verbose_name="ülevaatuse märkus")

    class Meta:
        verbose_name = "teema allikaviide"
        verbose_name_plural = "teema allikaviited"
        ordering = ["source_system", "source_sheet", "source_row_number"]
        indexes = [
            models.Index(fields=["matter"], name="legacy_source_matter"),
            models.Index(fields=["onenote_page_id"], name="legacy_source_onenote"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(match_confidence__isnull=True)
                | models.Q(match_confidence__gte=0, match_confidence__lte=1),
                name="legacy_import_confidence_between_zero_and_one",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_system}:{self.source_sheet}:{self.source_row_number}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            stored = type(self).objects.filter(pk=self.pk).values(*self.RAW_FIELDS).first()
            if stored is not None:
                changed = [f for f in self.RAW_FIELDS if stored[f] != getattr(self, f)]
                if changed:
                    raise ImmutableRecordError(
                        "Imported source values are immutable; "
                        f"attempted to change {', '.join(changed)}."
                    )
        super().save(*args, **kwargs)
