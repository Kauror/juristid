"""Import provenance.

Nothing here interprets history. It records, verbatim and immutably, what the
source said and how a Matter came to be matched to it, so that a future
maintainer can tell an imported guess from a verified fact — and so that
nobody can quietly "clean" a legitimate historical anomaly
(master specification 11.2, 19.3, 19.9).
"""

from __future__ import annotations

from typing import Any

from django.contrib.postgres.fields import ArrayField
from django.db import models

from app.core.errors import ImmutableRecordError
from app.core.models import AppendOnlyModel, BaseModel
from app.legacy_import.enums import OneNoteContentStatus, ProposedRecordMode, RowOutcome


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

    #: Write-once columns. Guarded in :meth:`save` *and* by a database trigger,
    #: because ``QuerySet.update()``, ``bulk_update()``, a data migration and a
    #: psql session all bypass ``save()`` entirely. A model-layer check on
    #: immutable provenance is a convention; the trigger is the guarantee
    #: (Stage-2A brief 13).
    RAW_FIELDS = (
        "source_system",
        "source_file_name",
        "source_snapshot_sha256",
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
    source_snapshot_sha256 = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        verbose_name="hetktõmmise SHA-256",
        help_text=(
            "Baiditäpne allika identiteet. Kordusimport sama tõmmise pealt tunneb "
            "rea selle järgi ära; uus tõmmis on uus tõendus, mitte vana muutmine."
        ),
    )
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

    # -- interpretation, not source ---------------------------------------
    # These say how the row was read. They are not raw, so they may be
    # corrected, and correcting them never touches what the source said.
    source_era = models.CharField(max_length=32, blank=True, verbose_name="allika periood")
    source_contract_version = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="ajastulepingu versioon",
        help_text="Millise aasta lepingu reeglite järgi see rida loeti.",
    )
    source_parser_version = models.CharField(
        max_length=50, blank=True, verbose_name="parseri versioon"
    )

    # -- mutable operational metadata -------------------------------------
    onenote_content_status = models.CharField(
        max_length=32,
        choices=OneNoteContentStatus.choices,
        default=OneNoteContentStatus.NOT_APPLICABLE,
        verbose_name="OneNote'i sisu seis",
        help_text=(
            "Kas lingi taga olev leht on imporditud. Link ise on muutumatu tõendus; "
            "see väli on tööseis ja seda tohib muuta."
        ),
    )

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
            # Idempotency, enforced rather than hoped for. One snapshot's row is
            # recorded once; a *different* snapshot of the same row is a new
            # observation and is allowed, because the second workbook is new
            # evidence rather than a correction of the first.
            models.UniqueConstraint(
                fields=[
                    "source_system",
                    "source_snapshot_sha256",
                    "source_sheet",
                    "source_row_number",
                ],
                condition=~models.Q(source_snapshot_sha256=""),
                name="legacy_import_one_reference_per_source_row",
            ),
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


class ImportRowLedger(AppendOnlyModel):
    """What one import run did with one source row.

    The completeness ledger the specification requires (19.9), kept deliberately
    narrow. It is *not* a second provenance system: the raw source values live
    on ``MatterSourceReference`` and nothing here duplicates them. This records
    only the decision — outcome, anomalies, and which Matter it landed on — so
    that "every row was accounted for" is a query rather than an assertion.

    Append-only, and enforced by a trigger. A ledger that can be edited after
    the fact answers a different, less useful question than the one it was
    written to answer.
    """

    import_batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.CASCADE,
        related_name="row_ledger",
        verbose_name="impordipartii",
    )
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="import_ledger_entries",
        verbose_name="teema",
    )

    source_sheet = models.CharField(max_length=200, verbose_name="leht")
    source_row_number = models.PositiveIntegerField(verbose_name="rida")
    source_reference = models.CharField(max_length=64, blank=True, verbose_name="viide allikas")

    outcome = models.CharField(
        max_length=32,
        choices=RowOutcome.choices,
        db_index=True,
        verbose_name="tulem",
    )
    anomalies = ArrayField(
        models.CharField(max_length=64),
        default=list,
        blank=True,
        verbose_name="kõrvalekalded",
    )
    proposed_record_mode = models.CharField(
        max_length=32,
        choices=ProposedRecordMode.choices,
        blank=True,
        default="",
        verbose_name="pakutud kirje liik",
    )
    proposed_record_mode_reason = models.TextField(
        blank=True, verbose_name="pakutud kirje liigi põhjus"
    )
    note = models.TextField(blank=True, verbose_name="märkus")

    class Meta:
        verbose_name = "impordirea kanne"
        verbose_name_plural = "impordiridade kanded"
        ordering = ["import_batch", "source_sheet", "source_row_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["import_batch", "source_sheet", "source_row_number"],
                name="legacy_import_one_ledger_row_per_source_row",
            ),
        ]
        indexes = [
            models.Index(fields=["import_batch", "outcome"], name="legacy_ledger_batch_outcome"),
        ]

    def __str__(self) -> str:
        return f"{self.source_sheet}:{self.source_row_number} {self.outcome}"


# The historical corpus models live in their own module because they obey a
# different rule to everything above: these are product data a lawyer reads,
# not importer bookkeeping. Imported here so Django's app registry finds them
# (docs/adr/0015).
from app.legacy_import.current_state import (  # noqa: E402
    CurrentRegisterState,
    RegisterCurrency,
)
from app.legacy_import.opinion_archive import (  # noqa: E402
    OpinionArchiveBatch,
    OpinionArchiveItem,
    OpinionArchiveMetadata,
    OpinionMatchCandidate,
    OpinionSubmissionImport,
)
from app.legacy_import.source_pages import (  # noqa: E402
    CandidateClass,
    CandidateState,
    HistoricalMatchCandidate,
    LegacySourcePage,
    LegacySourceResource,
    LegacySourceResourceImport,
    MatterSourcePage,
    ResourceImportState,
    ResourceKind,
    SourceMatchClass,
    SourceMatchMethod,
    SourcePageRole,
    SourceRelationshipKind,
    SourceSystem,
)

__all__ = [
    "CandidateClass",
    "CandidateState",
    "ConflictState",
    "CurrentRegisterState",
    "HistoricalMatchCandidate",
    "ImportBatch",
    "ImportRowLedger",
    "LegacySourcePage",
    "LegacySourceResource",
    "LegacySourceResourceImport",
    "MatchMethod",
    "MatterSourcePage",
    "MatterSourceReference",
    "OpinionArchiveBatch",
    "OpinionArchiveItem",
    "OpinionArchiveMetadata",
    "OpinionMatchCandidate",
    "OpinionSubmissionImport",
    "ReconciliationStatus",
    "RegisterCurrency",
    "ResourceImportState",
    "ResourceKind",
    "SourceMatchClass",
    "SourceMatchMethod",
    "SourcePageRole",
    "SourceRelationshipKind",
    "SourceSystem",
]
