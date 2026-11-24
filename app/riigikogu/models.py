"""What Juristid knows about the Riigikogu, as the Riigikogu published it (ADR 0222–0226).

Every row is keyed by the Riigikogu's own identifier (a UUID), so a document met
through three endpoints — the document register, a bill's opinions, a committee
sitting — is one row, and a nightly cycle run twice writes nothing the second
time. A change the source makes is kept as a change (`DocumentChange`, a
superseded `ExternalDeadline`, a moved `CommitteeAgendaItem`), never by
overwriting what was known before.

None of this is a fact about a Teema, and none of it is restricted data: it is the
public record. **The association of a public record with a Teema can be
restricted**, so every view that names a Teema next to one of these rows reads the
Teema through `Matter.objects.visible_to` first (ADR 0222 §6).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from app.core.models import BaseModel
from app.riigikogu.enums import (
    AgendaState,
    AnalysisStatus,
    AnalysisType,
    DeadlineKind,
    DocumentKind,
    ExtractionStatus,
    FactState,
    InvitationReview,
    KodaRole,
    MentionCategory,
    ResponseCheck,
    RunStatus,
    Stage,
    TextStatus,
)


def _vocabulary(field: str, values: list[str], name: str) -> models.CheckConstraint:
    return models.CheckConstraint(condition=models.Q(**{f"{field}__in": values}), name=name)


class Bill(BaseModel):
    """A Riigikogu bill (`/api/volumes/drafts/{uuid}`): the durable external identity.

    The UUID is the identity. Number, type and membership are how a bill is found
    and shown («1082 SE», XV koosseis); a number restarts with every membership,
    so it is never an identity on its own. The title can change between readings
    and changes nothing here but the title.
    """

    uuid = models.UUIDField(unique=True, verbose_name="Riigikogu tunnus")
    mark = models.PositiveIntegerField(null=True, blank=True, verbose_name="number")
    type_code = models.CharField(max_length=8, blank=True, verbose_name="liik")
    membership = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="koosseis")
    title = models.CharField(max_length=1000, blank=True, verbose_name="pealkiri")
    url = models.URLField(max_length=500, blank=True, verbose_name="link")
    stage = models.CharField(max_length=64, blank=True, verbose_name="menetlusetapp")
    status = models.CharField(max_length=64, blank=True, verbose_name="menetlusseis")
    leading_committee = models.CharField(max_length=200, blank=True, verbose_name="juhtivkomisjon")
    initiated_on = models.DateField(null=True, blank=True, verbose_name="algatatud")
    #: The Riigikogu's own amendment deadline as last read; history in `ExternalDeadline`.
    amendments_deadline = models.DateTimeField(
        null=True, blank=True, verbose_name="muudatusettepanekute tähtaeg"
    )
    #: A digest of the fields above as last read: an unchanged bill writes nothing.
    metadata_hash = models.CharField(max_length=64, blank=True)
    fetched_at = models.DateTimeField(null=True, blank=True, verbose_name="viimati loetud")

    class Meta:
        verbose_name = "Riigikogu eelnõu"
        verbose_name_plural = "Riigikogu eelnõud"
        indexes = [models.Index(fields=["mark", "type_code"], name="rk_bill_mark")]

    def __str__(self) -> str:
        return self.label or self.title

    @property
    def label(self) -> str:
        return f"{self.mark} {self.type_code}" if self.mark and self.type_code else ""


class ParliamentDocument(BaseModel):
    """One Riigikogu document (`/api/documents/{uuid}`), whichever endpoint showed it first."""

    uuid = models.UUIDField(unique=True, verbose_name="Riigikogu tunnus")
    kind = models.CharField(
        max_length=16, choices=DocumentKind.choices, default=DocumentKind.UNCERTAIN, db_index=True
    )
    koda_role = models.CharField(max_length=12, choices=KodaRole.choices, default=KodaRole.NONE)
    title = models.CharField(max_length=1000, verbose_name="pealkiri")
    #: The API's own document type (``letterDocument``, ``muudatusettepanek``, …).
    document_type = models.CharField(max_length=64, blank=True)
    #: ``VALJA`` (sent by the Riigikogu) or ``SISSE`` (received), as the API says.
    direction = models.CharField(max_length=8, blank=True)
    #: The API's ``author`` field as written: for an outgoing letter, its addressees.
    parties = models.TextField(blank=True, verbose_name="saatja või adressaadid")
    bill = models.ForeignKey(
        Bill, on_delete=models.SET_NULL, null=True, blank=True, related_name="documents"
    )
    #: The register reference (``1-1/15-1082``) and the volume's own UUID.
    reference = models.CharField(max_length=120, blank=True)
    volume_uuid = models.UUIDField(null=True, blank=True)
    #: The day the letter is dated (``authorDate``) and the day it was registered
    #: (``created``): a letter can be registered, and so be readable, days later.
    letter_date = models.DateField(null=True, blank=True, verbose_name="kirja kuupäev")
    registered_on = models.DateField(null=True, blank=True, verbose_name="registreeritud")
    url = models.URLField(max_length=500, blank=True)
    #: The file the text was read from: Riigikogu file UUID and name.
    file_uuid = models.UUIDField(null=True, blank=True)
    file_name = models.CharField(max_length=300, blank=True)
    text_status = models.CharField(
        max_length=12, choices=TextStatus.choices, default=TextStatus.NOT_FETCHED
    )
    #: Digests: an unchanged rediscovery writes nothing; a changed one is a `DocumentChange`.
    metadata_hash = models.CharField(max_length=64, blank=True)
    content_hash = models.CharField(max_length=64, blank=True)
    first_seen_at = models.DateTimeField(verbose_name="esmakordselt nähtud")
    last_seen_at = models.DateTimeField(verbose_name="viimati nähtud")

    class Meta:
        verbose_name = "Riigikogu dokument"
        verbose_name_plural = "Riigikogu dokumendid"
        constraints = [
            _vocabulary("kind", DocumentKind.values, "rk_document_kind"),
            _vocabulary("koda_role", KodaRole.values, "rk_document_koda_role"),
            _vocabulary("text_status", TextStatus.values, "rk_document_text_status"),
        ]

    def __str__(self) -> str:
        return self.title


class DocumentChange(BaseModel):
    """What changed in a document the source had already shown (ADR 0222 §4)."""

    document = models.ForeignKey(
        ParliamentDocument, on_delete=models.CASCADE, related_name="changes"
    )
    #: ``metadata`` or ``content``.
    what = models.CharField(max_length=16)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Riigikogu dokumendi muutus"
        verbose_name_plural = "Riigikogu dokumentide muutused"
        ordering = ["created_at"]


class Invitation(BaseModel):
    """A committee's letter that names koda as an addressee (ADR 0223).

    Always kept, whether or not a deadline could be read from it. The deadline
    itself is an `ExternalDeadline` (the letter may set several, and a later
    letter may move one); the review state is what a person did with it.
    """

    document = models.OneToOneField(
        ParliamentDocument, on_delete=models.CASCADE, related_name="invitation"
    )
    committee = models.CharField(max_length=200, blank=True, verbose_name="komisjon")
    #: A hearing the letter names, if it names one with a date.
    hearing_on = models.DateField(null=True, blank=True, verbose_name="arutelu")
    review = models.CharField(
        max_length=12,
        choices=InvitationReview.choices,
        default=InvitationReview.NEW,
        db_index=True,
        verbose_name="otsus",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    #: The `Oluline tähtaeg` a person added from it, or linked it to.
    important_date = models.ForeignKey(
        "intelligence.MatterImportantDate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    #: The «Uued eelnõud» candidate it enriched, when its bill was no Teema yet.
    candidate = models.ForeignKey(
        "register_watch.TopicCandidate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invitations",
    )
    response = models.CharField(
        max_length=12,
        choices=ResponseCheck.choices,
        default=ResponseCheck.NOT_DUE,
        verbose_name="vastamine",
    )
    response_note = models.CharField(max_length=500, blank=True)
    #: The koda opinion the response was matched or linked to.
    response_document = models.ForeignKey(
        ParliamentDocument,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="answers",
    )
    response_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    response_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Riigikogu kutse kojale"
        verbose_name_plural = "Riigikogu kutsed kojale"
        constraints = [
            _vocabulary("review", InvitationReview.values, "rk_invitation_review"),
            _vocabulary("response", ResponseCheck.values, "rk_invitation_response"),
        ]


class ExternalDeadline(BaseModel):
    """An external parliamentary date, with its history (ADR 0223 §3–§4).

    Never koda's obligation and never `Arvamuse tähtaeg`: that is Lovable's
    (ADR 0213). A change is a new row that supersedes the old one; the old one
    stays, so «was 24.11, now 28.11» can always be said.
    """

    kind = models.CharField(max_length=16, choices=DeadlineKind.choices)
    bill = models.ForeignKey(
        Bill, on_delete=models.CASCADE, null=True, blank=True, related_name="deadlines"
    )
    document = models.ForeignKey(
        ParliamentDocument,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="deadlines",
    )
    due_on = models.DateField(null=True, blank=True, verbose_name="kuupäev")
    #: Only when the source states a time (Europe/Tallinn); date-only stays date-only.
    due_time = models.TimeField(null=True, blank=True, verbose_name="kellaaeg")
    extraction = models.CharField(max_length=12, choices=ExtractionStatus.choices)
    #: The sentence the date was read from, word for word; empty for a structured field.
    evidence = models.TextField(blank=True, verbose_name="lause allikast")
    #: Why it needs checking, in a person's words.
    doubt = models.CharField(max_length=300, blank=True)
    state = models.CharField(
        max_length=12, choices=FactState.choices, default=FactState.ACTIVE, db_index=True
    )
    supersedes = models.OneToOneField(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="superseded_by"
    )
    source_url = models.URLField(max_length=500, blank=True)

    class Meta:
        verbose_name = "Riigikogu tähtaeg"
        verbose_name_plural = "Riigikogu tähtajad"
        ordering = ["due_on", "due_time", "created_at"]
        constraints = [
            _vocabulary("kind", DeadlineKind.values, "rk_deadline_kind"),
            _vocabulary("extraction", ExtractionStatus.values, "rk_deadline_extraction"),
            _vocabulary("state", FactState.values, "rk_deadline_state"),
            # One current amendment deadline per bill, one current date per kind per letter.
            models.UniqueConstraint(
                fields=["bill", "kind"],
                condition=models.Q(state="ACTIVE", document__isnull=True),
                name="rk_deadline_one_active_per_bill",
            ),
            models.UniqueConstraint(
                fields=["document", "kind"],
                condition=models.Q(state="ACTIVE", document__isnull=False),
                name="rk_deadline_one_active_per_letter",
            ),
        ]

    @property
    def verified(self) -> bool:
        return self.extraction in (ExtractionStatus.FOUND, ExtractionStatus.STRUCTURED)


class CommitteeAgendaItem(BaseModel):
    """One item of a committee sitting's agenda (`/api/events` → committeeSittings)."""

    uuid = models.UUIDField(unique=True, verbose_name="Riigikogu tunnus")
    sitting_uuid = models.UUIDField(null=True, blank=True)
    committee = models.CharField(max_length=200, verbose_name="komisjon")
    sitting_at = models.DateTimeField(verbose_name="istung")
    title = models.CharField(max_length=1000, verbose_name="päevakorrapunkt")
    bill = models.ForeignKey(
        Bill, on_delete=models.SET_NULL, null=True, blank=True, related_name="agenda_items"
    )
    #: «1095 SE» when the title names a bill; how a bill is found when no UUID is given.
    bill_mark = models.CharField(max_length=16, blank=True)
    #: An early legislative signal: no bill yet («eelnõu algatamine», «kavandi tutvustamine»).
    signal = models.BooleanField(default=False)
    #: Generic housekeeping («Info ja muud küsimused»): kept, never shown by default.
    routine = models.BooleanField(default=False)
    state = models.CharField(
        max_length=12, choices=AgendaState.choices, default=AgendaState.SCHEDULED
    )
    previous_sitting_at = models.DateTimeField(null=True, blank=True)
    #: Complete readings of a window covering it in which it was absent.
    missing_readings = models.PositiveSmallIntegerField(default=0)
    protocol_uuids = models.JSONField(default=list, blank=True)
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()

    class Meta:
        verbose_name = "komisjoni päevakorrapunkt"
        verbose_name_plural = "komisjonide päevakorrapunktid"
        ordering = ["sitting_at", "committee"]
        constraints = [_vocabulary("state", AgendaState.values, "rk_agenda_state")]


class StenogramMention(BaseModel):
    """A place in a plenary stenogram or a committee protocol that names koda (ADR 0226 §2).

    The quote is the evidence. A protocol naming koda among those present is evidence
    that koda *took part*; an invitation alone never is (ADR 0226 §3).
    """

    #: Sitting date + agenda item + position: one mention, however often it is read.
    key = models.CharField(max_length=200, unique=True)
    #: ``STENOGRAM`` (the plenary) or ``PROTOCOL`` (a committee sitting's minutes).
    source = models.CharField(max_length=12, default="STENOGRAM", db_default="STENOGRAM")
    #: In a protocol: koda is named among the participants («Kutsutud:», «Osalesid:»).
    participation = models.BooleanField(default=False)
    sitting_on = models.DateField(db_index=True)
    sitting_title = models.CharField(max_length=300, blank=True)
    agenda_title = models.CharField(max_length=1000, blank=True)
    agenda_uuid = models.UUIDField(null=True, blank=True)
    #: As the stenogram's own speaker heading writes it; never inferred by a model.
    speaker = models.CharField(max_length=200, blank=True)
    quote = models.TextField(verbose_name="tsitaat")
    context = models.TextField(blank=True, verbose_name="kontekst")
    url = models.URLField(max_length=500, blank=True)
    category = models.CharField(max_length=12, choices=MentionCategory.choices)
    bill = models.ForeignKey(
        Bill, on_delete=models.SET_NULL, null=True, blank=True, related_name="mentions"
    )

    class Meta:
        verbose_name = "koja mainimine stenogrammis"
        verbose_name_plural = "koja mainimised stenogrammides"
        ordering = ["-sitting_on"]
        constraints = [_vocabulary("category", MentionCategory.values, "rk_mention_category")]


class StageRun(BaseModel):
    """One stage of one nightly cycle (ADR 0222 §5): its own status, counts and errors.

    A stage's checkpoint is the ``window_end`` of its last SUCCEEDED run. A
    PARTIAL or FAILED run never moves it, so what was not read is read again.
    """

    cycle = models.UUIDField(db_index=True)
    stage = models.CharField(max_length=12, choices=Stage.choices)
    status = models.CharField(max_length=12, choices=RunStatus.choices, default=RunStatus.RUNNING)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    window_start = models.DateField(null=True, blank=True)
    window_end = models.DateField(null=True, blank=True)
    counts = models.JSONField(default=dict, blank=True)
    errors = models.JSONField(default=list, blank=True)
    requests = models.PositiveIntegerField(default=0)
    rate_limited = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Riigikogu kontrolli etapp"
        verbose_name_plural = "Riigikogu kontrolli etapid"
        ordering = ["-started_at"]
        constraints = [
            _vocabulary("stage", Stage.values, "rk_stage_run_stage"),
            _vocabulary("status", RunStatus.values, "rk_stage_run_status"),
        ]


class ApiCall(models.Model):
    """The shared ledger of calls to api.riigikogu.ee (ADR 0222 §3).

    1 request a second per address and 12 a minute per path, as the Riigikogu
    publishes. Every process that reads the API asks the ledger first, under one
    advisory lock, so the register watch, the sweep and this cycle cannot add up
    to a burst. Rows older than an hour are pruned.
    """

    id = models.BigAutoField(primary_key=True)
    path = models.CharField(max_length=200, db_index=True)
    at = models.DateTimeField(db_index=True)

    class Meta:
        verbose_name = "Riigikogu päring"
        verbose_name_plural = "Riigikogu päringud"

    def __str__(self) -> str:
        return f"{self.at:%Y-%m-%d %H:%M:%S} {self.path}"


class Analysis(BaseModel):
    """One model analysis of public parliamentary text (ADR 0225). Disabled in production.

    Keyed by what was analysed (`source_key`: document UUIDs and content hashes)
    and how (`analysis_type`, `schema_version`, `prompt_version`), so an unchanged
    document is never analysed twice and a changed one supersedes only its own.
    """

    analysis_type = models.CharField(max_length=20, choices=AnalysisType.choices)
    bill = models.ForeignKey(
        Bill, on_delete=models.CASCADE, null=True, blank=True, related_name="analyses"
    )
    source_key = models.CharField(max_length=200)
    #: ``[{"document": uuid, "hash": sha256}]``: what the model read.
    sources = models.JSONField(default=list, blank=True)
    schema_version = models.CharField(max_length=16)
    prompt_version = models.CharField(max_length=16)
    model = models.CharField(max_length=80, blank=True)
    status = models.CharField(
        max_length=12, choices=AnalysisStatus.choices, default=AnalysisStatus.PENDING
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    #: Validated output only; every quoted passage in it was found in its source.
    result = models.JSONField(default=dict, blank=True)
    rejected = models.JSONField(default=list, blank=True)
    error = models.CharField(max_length=300, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    #: A demonstration from a mocked model: always labelled so on every surface.
    mocked = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Riigikogu dokumendi analüüs"
        verbose_name_plural = "Riigikogu dokumentide analüüsid"
        constraints = [
            _vocabulary("analysis_type", AnalysisType.values, "rk_analysis_type"),
            _vocabulary("status", AnalysisStatus.values, "rk_analysis_status"),
            models.UniqueConstraint(
                fields=["analysis_type", "source_key", "schema_version", "prompt_version"],
                name="rk_analysis_once",
            ),
        ]
