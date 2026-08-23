"""The opinions archive, its provenance, and what was decided about each file.

Four models and the reason each is separate.

``OpinionArchiveBatch`` pins the *snapshots* one reconciliation reasoned about:
the archive's hash, the Excel's hash, the KodaDash artefact's hash and the
OneNote capture id. A plan approved against one set of sources may not be
applied against another, and this is the row that makes that checkable rather
than trusted (brief 48).

``OpinionArchiveItem`` is one **occurrence** — one path inside one archive. Two
paths holding identical bytes are two occurrences and one binary, and the
catalogue keeps both facts because collapsing them would erase the only record
that the same letter was filed twice (brief 29).

``OpinionArchiveMetadata`` is somebody else's reading of that occurrence. Today
that is KodaDash: public summaries, Chamber-position sentences, topic labels and
a normalised recipient bucket. It is stored beside the evidence and never on top
of it — ``Matter.position_summary`` is a lawyer's field and a public-app summary
is not a lawyer (brief 33, 34).

``OpinionMatchCandidate`` is the proposal, with its signals and conflicts listed
individually. ``OpinionSubmissionImport`` is the *outcome*: this occurrence is
the evidence behind that Submission, and here is the specific reason we believe
its date and its recipient (brief 46).
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models

from app.core.models import BaseModel
from app.legacy_import.opinion_enums import (
    OpinionCandidateState,
    OpinionConflict,
    OpinionMatchClass,
    OpinionMetadataSystem,
    OpinionSignal,
    OpinionSourceKind,
    RecipientBasis,
    SentDateBasis,
)


class OpinionArchiveBatch(BaseModel):
    """One reconciliation run and the exact sources it reasoned about."""

    source_kind = models.CharField(
        max_length=32,
        choices=OpinionSourceKind.choices,
        default=OpinionSourceKind.OPINIONS_ARCHIVE,
        verbose_name="allika liik",
    )
    archive_file_name = models.CharField(max_length=400, blank=True, verbose_name="arhiivi fail")
    archive_sha256 = models.CharField(max_length=64, verbose_name="arhiivi SHA-256")
    archive_occurrence_count = models.PositiveIntegerField(default=0, verbose_name="esinemisi")
    archive_distinct_sha_count = models.PositiveIntegerField(
        default=0, verbose_name="erinevaid baidijadasid"
    )

    excel_sha256 = models.CharField(max_length=64, blank=True, verbose_name="Exceli SHA-256")
    kodadash_artifact_name = models.CharField(
        max_length=400, blank=True, verbose_name="KodaDashi artefakt"
    )
    kodadash_artifact_sha256 = models.CharField(
        max_length=64, blank=True, verbose_name="KodaDashi SHA-256"
    )
    onenote_capture_id = models.CharField(
        max_length=100, blank=True, verbose_name="OneNote'i hõive"
    )

    importer_version = models.CharField(max_length=50, verbose_name="importija versioon")
    started_at = models.DateTimeField(verbose_name="algas")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="lõppes")
    notes = models.TextField(blank=True, verbose_name="märkused")

    class Meta:
        verbose_name = "arvamuste arhiivi partii"
        verbose_name_plural = "arvamuste arhiivi partiid"
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"{self.archive_file_name or self.source_kind} {self.started_at:%Y-%m-%d %H:%M}"

    @property
    def source_fingerprint(self) -> tuple[str, str, str, str]:
        """The four hashes a plan and its apply must agree on."""
        return (
            self.archive_sha256,
            self.excel_sha256,
            self.kodadash_artifact_sha256,
            self.onenote_capture_id,
        )


class OpinionArchiveItem(BaseModel):
    """One file as it sits at one path inside the opinions archive.

    Identity is (archive SHA-256, path, content SHA-256). A *later* snapshot of
    the archive is new evidence rather than a correction of the old one, so
    re-running against a new zip adds occurrences and never rewrites the ones
    already recorded (brief 65).
    """

    batch = models.ForeignKey(
        OpinionArchiveBatch,
        on_delete=models.PROTECT,
        related_name="items",
        verbose_name="partii",
    )
    archive_sha256 = models.CharField(max_length=64, verbose_name="arhiivi SHA-256")
    archive_relative_path = models.TextField(verbose_name="tee arhiivis")
    original_filename = models.CharField(max_length=500, verbose_name="algne failinimi")
    #: How the archive's own bytes had to be decoded to read the name at all.
    #: 91 of 767 entries carry UTF-8 without the ZIP's UTF-8 flag, and some
    #: names were already mojibake before they were zipped. Recording the
    #: decoding keeps a damaged name from looking like a deliberate one.
    filename_encoding = models.CharField(
        max_length=40, blank=True, verbose_name="failinime kodeering"
    )

    sha256 = models.CharField(max_length=64, db_index=True, verbose_name="SHA-256")
    size_bytes = models.BigIntegerField(default=0, verbose_name="suurus baitides")
    detected_type = models.CharField(max_length=100, blank=True, verbose_name="tuvastatud tüüp")

    #: The stored bytes, once somebody has materialised them (Stage 2H.2).
    #:
    #: Nullable because cataloguing and holding are different acts: an
    #: occurrence is recorded the moment the archive is read, and the bytes are
    #: copied into evidence storage later, by an explicit operator command. A
    #: row with no binary is an honest statement that the catalogue knows about
    #: a file the application cannot yet open.
    #:
    #: PROTECT, because deleting bytes that occurrences point at is not a
    #: cascade — it is the loss of the evidence itself.
    binary = models.ForeignKey(
        "legacy_import.OpinionArchiveBinary",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="occurrences",
        verbose_name="bait",
    )

    # -- what the archive's own naming convention says --------------------
    #
    # Metadata, never truth. The corpus names every file
    # `YYYY-MM-DD - Saaja - Pealkiri.pdf`, and that date is the letter's own
    # date: the register's VÄLJA is the same day in 326 cases and the next day
    # in 227. It is a matching signal and never a sent date (brief 12, 19).
    filename_date = models.DateField(null=True, blank=True, verbose_name="failinime kuupäev")
    filename_recipient = models.CharField(
        max_length=300, blank=True, verbose_name="failinime saaja"
    )
    filename_title = models.TextField(blank=True, verbose_name="failinime pealkiri")

    class Meta:
        verbose_name = "arvamuste arhiivi kirje"
        verbose_name_plural = "arvamuste arhiivi kirjed"
        ordering = ["filename_date", "original_filename"]
        constraints = [
            models.UniqueConstraint(
                fields=["archive_sha256", "archive_relative_path", "sha256"],
                name="opinion_one_item_per_archive_path",
            ),
            models.CheckConstraint(
                condition=models.Q(sha256__regex=r"^[0-9a-f]{64}$"),
                name="opinion_item_sha256_is_lowercase_hex",
            ),
        ]
        indexes = [
            models.Index(fields=["sha256"], name="opinion_item_sha"),
            models.Index(fields=["filename_date"], name="opinion_item_file_date"),
        ]

    def __str__(self) -> str:
        return self.original_filename

    @property
    def source_year(self) -> int | None:
        return self.filename_date.year if self.filename_date else None


class OpinionArchiveMetadata(BaseModel):
    """Somebody else's derived reading of one archive occurrence.

    Every field is kept under its producer's name and beside its producer's
    hash. A public summary is a summary somebody generated for a membership app;
    it is searchable context and it is not what the letter said (brief 34, 57).
    """

    item = models.ForeignKey(
        OpinionArchiveItem,
        on_delete=models.CASCADE,
        related_name="metadata_rows",
        verbose_name="arhiivikirje",
    )
    source_system = models.CharField(
        max_length=32,
        choices=OpinionMetadataSystem.choices,
        default=OpinionMetadataSystem.KODADASH,
        verbose_name="lähtesüsteem",
    )
    source_artifact_name = models.CharField(max_length=400, verbose_name="artefakt")
    source_artifact_sha256 = models.CharField(max_length=64, verbose_name="artefakti SHA-256")
    external_id = models.CharField(max_length=100, verbose_name="väline ID")
    captured_at = models.DateTimeField(verbose_name="hõivatud")

    # -- the recipient, kept in three separate facts ----------------------
    #
    # ``recipient_raw`` is what the historical source wrote.
    # ``recipient_normalized`` is KodaDash's canonical current name, which
    # rewrites Keskkonnaministeerium to Kliimaministeerium in 52 rows and folds
    # a comma-separated pair down to its first name. That is a legitimate
    # cross-era analytics bucket and an illegitimate historical identity, so the
    # two never share a column (brief 10, 21, 22).
    recipient_raw = models.CharField(max_length=400, blank=True, verbose_name="saaja allikas")
    recipient_normalized = models.CharField(
        max_length=400, blank=True, verbose_name="saaja normaliseeritud"
    )
    recipient_filter_group = models.CharField(
        max_length=200, blank=True, verbose_name="saaja filtrirühm"
    )
    recipient_type = models.CharField(max_length=40, blank=True, verbose_name="saaja liik")
    recipient_secondary = models.CharField(max_length=400, blank=True, verbose_name="teine saaja")
    recipient_review_required = models.BooleanField(
        default=False, verbose_name="saaja vajab ülevaatust"
    )

    document_date = models.DateField(null=True, blank=True, verbose_name="dokumendi kuupäev")
    title = models.TextField(blank=True, verbose_name="pealkiri")

    related_koda_news_url = models.TextField(blank=True, verbose_name="seotud uudise link")
    related_koda_news_id = models.CharField(max_length=100, blank=True, verbose_name="uudise ID")
    policy_thread_id = models.CharField(max_length=100, blank=True, verbose_name="teemalõnga ID")

    #: The producer's public gate, kept as provenance and never consulted as an
    #: archival gate. A row KodaDash hid from members is still a letter Koda
    #: sent (brief 35).
    public_import_eligible = models.BooleanField(
        null=True, blank=True, verbose_name="avalik import lubatud"
    )
    excluded_from_public = models.BooleanField(default=False, verbose_name="avalikust välja jäetud")
    exclusion_reason = models.TextField(blank=True, verbose_name="väljajätmise põhjus")

    #: Everything else the producer wrote about this row, verbatim. A JSON
    #: column because the producer's schema drifts between revisions and a
    #: migration per renamed column would be a migration per revision.
    payload = models.JSONField(default=dict, blank=True, verbose_name="tuletatud väljad")

    class Meta:
        verbose_name = "arvamuse tuletatud metaandmed"
        verbose_name_plural = "arvamuste tuletatud metaandmed"
        ordering = ["item", "source_system"]
        constraints = [
            models.UniqueConstraint(
                fields=["item", "source_system", "source_artifact_sha256", "external_id"],
                name="opinion_one_metadata_row_per_artifact",
            ),
        ]
        indexes = [
            models.Index(fields=["source_system", "external_id"], name="opinion_meta_external"),
        ]

    def __str__(self) -> str:
        return f"{self.source_system}:{self.external_id}"


class OpinionMatchCandidate(BaseModel):
    """One proposal that this archive occurrence belongs to that Matter."""

    item = models.ForeignKey(
        OpinionArchiveItem,
        on_delete=models.CASCADE,
        related_name="candidates",
        verbose_name="arhiivikirje",
    )
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="opinion_candidates",
        verbose_name="teema",
    )
    batch = models.ForeignKey(
        OpinionArchiveBatch,
        on_delete=models.PROTECT,
        related_name="candidates",
        verbose_name="partii",
    )

    match_class = models.CharField(
        max_length=32,
        choices=OpinionMatchClass.choices,
        db_index=True,
        verbose_name="sidumise klass",
    )
    signals = ArrayField(
        models.CharField(max_length=40, choices=OpinionSignal.choices),
        default=list,
        blank=True,
        verbose_name="signaalid",
    )
    conflicts = ArrayField(
        models.CharField(max_length=40, choices=OpinionConflict.choices),
        default=list,
        blank=True,
        verbose_name="vastuolud",
    )

    #: The reference the evidence points at, kept as text as well as a foreign
    #: key so the row stays readable when the Matter is not found.
    excel_reference = models.CharField(max_length=40, blank=True, verbose_name="registri viide")
    excel_sent_date = models.DateField(null=True, blank=True, verbose_name="registri VÄLJA")
    excel_addressee_raw = models.CharField(
        max_length=400, blank=True, verbose_name="registri KELLELE"
    )
    onenote_page_key = models.CharField(max_length=100, blank=True, verbose_name="OneNote'i leht")
    onenote_page_title = models.TextField(blank=True, verbose_name="OneNote'i lehe pealkiri")
    onenote_section = models.CharField(
        max_length=300, blank=True, verbose_name="OneNote'i sektsioon"
    )
    onenote_block_ordinal = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="ploki järjekord"
    )

    #: How many other Matters satisfied the same evidence. Anything above one is
    #: why this row is in a queue instead of in the database.
    competing_matter_count = models.PositiveIntegerField(default=0, verbose_name="konkureerivaid")
    explanation = models.TextField(blank=True, verbose_name="selgitus")

    state = models.CharField(
        max_length=20,
        choices=OpinionCandidateState.choices,
        default=OpinionCandidateState.PENDING,
        db_index=True,
        verbose_name="olek",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="opinion_decisions",
        verbose_name="otsustaja",
    )
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name="otsustatud")
    decision_note = models.TextField(blank=True, verbose_name="otsuse märkus")

    #: A reviewer confirming the Matter is not the same as a reviewer asserting
    #: the letter was sent. The queue offers both, and only this flag makes the
    #: next import create a canonical SENT record (brief 26, 63).
    review_approves_submission = models.BooleanField(
        default=False, verbose_name="ülevaataja kinnitas saatmise"
    )
    #: A date the reviewer established from something the reconciliation could
    #: not read. Kept apart from ``excel_sent_date`` so a human decision never
    #: passes for a register value.
    reviewed_sent_date = models.DateField(
        null=True, blank=True, verbose_name="ülevaatusel kinnitatud kuupäev"
    )

    # -- supersession -----------------------------------------------------
    #
    # A candidate's identity includes its match class, so newer evidence that
    # reclassifies the same occurrence produces a *different* row rather than
    # updating this one. Without somewhere to say so, the old proposal sits in
    # the queue forever: not APPLIED, because it produced nothing; not
    # REJECTED, because nobody rejected it; and not deletable, because it is
    # the record of what the reconciliation believed (Stage-2H.1 finding).
    superseded_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supersedes",
        verbose_name="asendatud kandidaadiga",
    )
    superseded_at = models.DateTimeField(null=True, blank=True, verbose_name="asendatud")
    supersession_reason = models.TextField(blank=True, verbose_name="asendamise põhjus")

    class Meta:
        verbose_name = "arvamuse sidumiskandidaat"
        verbose_name_plural = "arvamuste sidumiskandidaadid"
        ordering = ["match_class", "item__filename_date", "item__original_filename"]
        constraints = [
            # One proposal per (occurrence, Matter, class). A re-run finds the
            # row it wrote last time instead of stacking a second copy of the
            # same suggestion under the reviewer (brief 64).
            models.UniqueConstraint(
                fields=["item", "matter", "match_class"],
                condition=models.Q(matter__isnull=False),
                name="opinion_one_candidate_per_item_matter_class",
            ),
            models.UniqueConstraint(
                fields=["item", "match_class"],
                condition=models.Q(matter__isnull=True),
                name="opinion_one_matterless_candidate_per_item_class",
            ),
            # A row cannot replace itself. Cheap to state, and the alternative
            # is a "what replaced this?" link on the detail page that loops.
            models.CheckConstraint(
                condition=~models.Q(superseded_by=models.F("id")),
                name="opinion_candidate_does_not_supersede_itself",
            ),
        ]
        indexes = [
            models.Index(fields=["state", "match_class"], name="opinion_candidate_queue"),
        ]

    def __str__(self) -> str:
        return f"{self.match_class} {self.item_id}"

    @property
    def is_automatic(self) -> bool:
        from app.legacy_import.opinion_enums import AUTOMATIC_MATCH_CLASSES

        return self.match_class in AUTOMATIC_MATCH_CLASSES and not self.conflicts


class OpinionSubmissionImport(BaseModel):
    """This archive occurrence is the evidence behind that Submission.

    It exists so the question "why does Juristid say this went out on this date
    to this ministry" has a structured answer rather than a sentence in a notes
    field. It is also the idempotency key: a second run finds this row and
    enriches it instead of counting the same letter twice (brief 46, 64, 67).
    """

    item = models.ForeignKey(
        OpinionArchiveItem,
        on_delete=models.PROTECT,
        related_name="submission_imports",
        verbose_name="arhiivikirje",
    )
    submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.CASCADE,
        related_name="archive_imports",
        verbose_name="arvamus",
    )
    batch = models.ForeignKey(
        OpinionArchiveBatch,
        on_delete=models.PROTECT,
        related_name="submission_imports",
        verbose_name="partii",
    )
    candidate = models.ForeignKey(
        OpinionMatchCandidate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submission_imports",
        verbose_name="kandidaat",
    )

    #: True when this run created the Submission; false when it attached the
    #: archive's provenance to one that already existed. The difference is the
    #: difference between reconstructing history and double-counting it.
    created_submission = models.BooleanField(default=False, verbose_name="loodi siin")

    match_class = models.CharField(
        max_length=32, choices=OpinionMatchClass.choices, verbose_name="sidumise klass"
    )
    sent_date_basis = models.CharField(
        max_length=32,
        choices=SentDateBasis.choices,
        blank=True,
        verbose_name="kuupäeva alus",
    )
    recipient_basis = models.CharField(
        max_length=32,
        choices=RecipientBasis.choices,
        default=RecipientBasis.UNRESOLVED,
        verbose_name="saaja alus",
    )
    #: The recipient exactly as the historical source wrote it, resolved or not.
    #:
    #: It exists because the unresolved case is the common one and used to leave
    #: nothing behind: `_attach_recipient` returned early, the Submission got no
    #: SubmissionRecipient, and the only surviving trace of *who Koda actually
    #: wrote to* was a sentence in a notes field. That is the one fact the
    #: archive is certain about, and it was the one being discarded.
    #:
    #: Keeping it structurally is also what makes resolution retryable. When the
    #: reference data later learns that a former ministry is today's
    #: Organisation, `resolve_archive_recipients` reads this column and attaches
    #: the recipient to the Submission that already exists — no new Submission,
    #: no rewritten history, and this value never changes.
    recipient_raw = models.CharField(
        max_length=400,
        blank=True,
        verbose_name="saaja allikas",
    )
    matter_match_signals = ArrayField(
        models.CharField(max_length=40),
        default=list,
        blank=True,
        verbose_name="teema signaalid",
    )
    #: The exact binary this occurrence contributed. Null when the Submission
    #: already carried the same bytes from the OneNote materialisation and this
    #: run only recorded that the archive holds a second copy (brief 30, 68).
    document_version = models.ForeignKey(
        "documents.DocumentVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="opinion_archive_imports",
        verbose_name="tõendiversioon",
    )
    note = models.TextField(blank=True, verbose_name="märkus")

    class Meta:
        verbose_name = "arvamuse arhiiviimport"
        verbose_name_plural = "arvamuste arhiiviimport"
        ordering = ["submission", "item"]
        constraints = [
            models.UniqueConstraint(
                fields=["item", "submission"],
                name="opinion_one_import_per_item_and_submission",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.item_id} → {self.submission_id}"
