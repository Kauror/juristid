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


#: Import outcomes that count as a reading of the source.
#:
#: ``COMPLETED_WITH_GAPS`` belongs here: the gap is source rows that did not
#: become Matters, not doubt about the rows that did
#: (:mod:`app.legacy_import.apply`). ``RUNNING`` and ``FAILED`` do not — a
#: half-written snapshot is not a reading of the register.
FINISHED_IMPORTS: tuple[str, ...] = (
    ReconciliationStatus.COMPLETED,
    ReconciliationStatus.COMPLETED_WITH_GAPS,
)


def latest_finished_snapshot(source_system: str) -> str:
    """The snapshot digest of the most recent finished import, or ``""``.

    **The only answer to "which source file is current".** Two callers ask it
    for different reasons — a reconciliation has to read one register rather
    than the union of every register ever imported
    (:func:`app.legacy_import.opinion_plan.select_register_snapshot`), and the
    register text on a work list has to name the workbook it is a photograph of
    (:func:`app.legacy_import.register_display.snapshot_label`) — and two
    answers to that question is how one surface names the file another surface
    is reading.

    The chronology is ``ImportBatch``'s because it is the only record of *when*
    a snapshot was read and whether the reading finished. A file's timestamp, a
    SHA's lexical order and a row's number all say nothing about which register
    is current, and the tie-break is the primary key so that two batches started
    in the same millisecond still resolve to one digest rather than to
    whichever the database happened to return.

    Returning ``""`` means *nothing finished*, never *nothing exists*. What a
    caller does about that is its own decision: the reconciliation refuses,
    because reconciling against the wrong register corrupts a match; the label
    says nothing, because a page must render.
    """
    return (
        ImportBatch.objects.filter(
            source_system=source_system,
            reconciliation_status__in=FINISHED_IMPORTS,
        )
        .exclude(source_snapshot_sha256="")
        .order_by("-started_at", "-pk")
        .values_list("source_snapshot_sha256", flat=True)
        .first()
        or ""
    )


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
from app.legacy_import.opinion_binary import (  # noqa: E402
    OpinionArchiveBinary,
    OpinionArchiveMatterLink,
    OpinionArchiveText,
)
from app.legacy_import.opinion_search_models import (  # noqa: E402
    OpinionArchiveSearchDocument,
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


class OutreachChannel(models.TextChoices):
    """Which reviewed source an imported engagement pointer came from."""

    EMAIL_CAMPAIGN = "EMAIL_CAMPAIGN", "Sendsmaily kampaania"
    PUBLIC_PAGE = "PUBLIC_PAGE", "koda.ee avalik konsultatsioon"


class RegisterEngagementImport(BaseModel):
    """This reviewed outreach pointer is why that ``MatterEngagement`` exists.

    The idempotency key, and the reason it is a row here rather than a column
    there. ``MatterEngagement`` is a five-field pointer a person fills in by
    hand; it has no import identity, and the only thing an importer could
    otherwise match on is the title string. Titles are edited — that is the
    whole of what ``Kaasamine`` supports, since there is no delete — so a second
    run after somebody corrected a campaign's wording would find no match and
    record the same mailing twice, with the first copy still on the page.

    So identity lives beside the import, exactly as ``OpinionSubmissionImport``
    holds it for the opinion archive rather than putting it on ``Submission``.
    Three things follow, and each of them is why this shape was chosen:

    **The engagement model is untouched.** No migration on ``matters``, no
    import-only column on a record the department fills in by hand, and nothing
    for the composer form to ignore.

    **Hand-made and imported rows stay distinguishable.** A row with no
    ``RegisterEngagementImport`` is somebody's own work, and a future refresh
    that wanted to correct what it wrote can find precisely what it wrote —
    the same distinction ``native_activity`` already draws for submissions.

    **The key is the source's own identity, not a rendering of it.** For a
    campaign that is the Sendsmaily template URL, which is stable across the
    title and note edits a person may make afterwards. For a public
    consultation it is the page URL. Neither is derived from anything this
    application controls, so re-running the same approved mapping finds the same
    row whatever happened to the engagement in between (brief 27).

    Deliberately not a provider integration. Nothing here fetches, polls or
    authenticates; a reviewed mapping file is prepared by a person and this
    records what it said.
    """

    engagement = models.ForeignKey(
        "matters.MatterEngagement",
        on_delete=models.CASCADE,
        related_name="register_imports",
        verbose_name="kaasamine",
    )
    #: Denormalised from the engagement, and it earns its place: the uniqueness
    #: rule below is per Matter, and expressing it through the FK would make
    #: every check a join. ``CASCADE`` on both means a Matter's removal takes
    #: the pointer and its identity together.
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="register_engagement_imports",
        verbose_name="teema",
    )
    channel = models.CharField(
        max_length=32, choices=OutreachChannel.choices, db_index=True, verbose_name="kanal"
    )
    #: The source's own stable identifier — a template URL, a public page URL.
    #: Never a title and never a hash of one.
    source_key = models.CharField(max_length=1000, verbose_name="allika tunnus")
    #: SHA-256 of the reviewed mapping file this row was written from, so an
    #: operator can tell which approval produced which pointer.
    mapping_sha256 = models.CharField(
        max_length=64, blank=True, db_index=True, verbose_name="ülevaadatud vaste SHA-256"
    )
    #: True when this run created the engagement; false when it attached
    #: provenance to one that was already there. The difference between
    #: recording outreach and double-counting it.
    created_engagement = models.BooleanField(default=False, verbose_name="loodi siin")

    class Meta:
        verbose_name = "registri kaasamise import"
        verbose_name_plural = "registri kaasamise impordid"
        ordering = ["matter", "channel", "source_key"]
        constraints = [
            # One approved source, one pointer, per Matter. This is the whole
            # idempotency guarantee, and it is enforced by the database rather
            # than by the importer remembering to check: a re-run that raced
            # itself would otherwise leave two engagements on the page with no
            # way to tell which was which.
            models.UniqueConstraint(
                fields=["matter", "channel", "source_key"],
                name="legacy_register_one_engagement_per_source",
            ),
            models.CheckConstraint(
                condition=~models.Q(source_key=""),
                name="legacy_register_engagement_source_key_required",
            ),
            models.CheckConstraint(
                condition=models.Q(channel__in=OutreachChannel.values),
                name="legacy_register_engagement_channel_vocabulary",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.channel} {self.source_key[:60]}"


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
    "OpinionArchiveBinary",
    "OpinionArchiveItem",
    "OpinionArchiveMatterLink",
    "OpinionArchiveMetadata",
    "OpinionArchiveSearchDocument",
    "OpinionArchiveText",
    "OpinionMatchCandidate",
    "OpinionSubmissionImport",
    "OutreachChannel",
    "ReconciliationStatus",
    "RegisterCurrency",
    "RegisterEngagementImport",
    "ResourceImportState",
    "ResourceKind",
    "SourceMatchClass",
    "SourceMatchMethod",
    "SourcePageRole",
    "SourceRelationshipKind",
    "SourceSystem",
]
