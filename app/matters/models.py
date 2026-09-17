"""The canonical Matter (`Teema`).

One model carries both current operational work and the historical register.
`record_mode` distinguishes them; provenance and data-quality metadata say how
much of an imported row has been verified. Modern fields are nullable precisely
so that archive rows never have to invent a stage, an owner or a date
(master specification 11.2, 19.4).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone

from app.core.authorization import apply as apply_scope
from app.core.authorization import child_visibility_q, matter_visibility_q, scope_for_user
from app.core.dates import format_estonian_date
from app.core.enums import Visibility
from app.core.models import AppendOnlyModel, BaseModel, VisibilityInheritingModel
from app.matters.entry_enums import EntryKind
from app.matters.enums import (
    DataQualityTier,
    EngagementKind,
    ExternalPositionProvenance,
    MatterDataClass,
    MatterOrigin,
    RecordMode,
    TagAssignmentSource,
    WebsiteOverviewStatus,
)
from app.workflow.dates import format_at_precision, is_approximate
from app.workflow.enums import DatePrecision, Disposition, Track


class MatterQuerySet(models.QuerySet):
    def visible_to(self, user: object | None) -> MatterQuerySet:
        """The only supported entry point for reading Matters."""
        return apply_scope(self, matter_visibility_q(scope_for_user(user)))

    def active(self) -> MatterQuerySet:
        return self.filter(is_open=True)

    def full_records(self) -> MatterQuerySet:
        return self.filter(record_mode=RecordMode.FULL)

    def archive_records(self) -> MatterQuerySet:
        return self.filter(record_mode=RecordMode.ARCHIVE)

    def real_data(self) -> MatterQuerySet:
        """Business data. **Every statistic starts here** (Agent-C brief 28, 63).

        A production or business figure that counts development records is
        wrong in the way that is hardest to notice: nothing on the screen looks
        broken, the number is simply too big. So a reporting population says
        `.real_data()` explicitly rather than relying on a development database
        happening to be clean.

        Deliberately *not* folded into `visible_to`. Visibility answers "may
        this reader see it" and data class answers "is it about anything";
        collapsing them would mean a developer could not open the TEST Matter
        they had just created, and would make authorization depend on a field
        that has nothing to do with authorization (brief 13, 14).
        """
        return self.filter(data_class=MatterDataClass.REAL)

    def test_data(self) -> MatterQuerySet:
        """Records made while developing or testing the system."""
        return self.filter(data_class=MatterDataClass.TEST)


class MatterReferenceSequence(models.Model):
    """Per-year counter behind the familiar ``YYYY_N`` human reference.

    Allocation goes through ``app.matters.services.allocate_matter_reference``,
    which takes a row lock, so the numbering rule can change in one place if the
    department head decides differently (open decision, specification 28).
    """

    year = models.PositiveSmallIntegerField(primary_key=True, verbose_name="aasta")
    last_number = models.PositiveIntegerField(default=0, verbose_name="viimane number")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "viitenumbrite jada"
        verbose_name_plural = "viitenumbrite jadad"
        ordering = ["-year"]

    def __str__(self) -> str:
        return f"{self.year}: {self.last_number}"


class Matter(BaseModel):
    # -- human identity ----------------------------------------------------
    reference_year = models.PositiveSmallIntegerField(
        null=True, blank=True, db_index=True, verbose_name="viite aasta"
    )
    reference_number = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="viite number"
    )

    title = models.TextField(verbose_name="pealkiri")
    alternate_titles = ArrayField(
        models.TextField(),
        default=list,
        blank=True,
        verbose_name="muud pealkirjad",
        help_text="Varasemad või allikapõhised pealkirjad, mis peavad jääma otsitavaks.",
    )

    # -- record character --------------------------------------------------
    record_mode = models.CharField(
        max_length=16,
        choices=RecordMode.choices,
        default=RecordMode.FULL,
        db_index=True,
        verbose_name="kirje liik",
    )
    origin = models.CharField(
        max_length=32,
        choices=MatterOrigin.choices,
        default=MatterOrigin.NATIVE,
        db_index=True,
        verbose_name="päritolu",
    )
    data_quality_tier = models.CharField(
        max_length=16,
        choices=DataQualityTier.choices,
        blank=True,
        default="",
        verbose_name="andmekvaliteedi tase",
    )
    #: Real business data, or something made while developing the system.
    #:
    #: Indexed because every reporting population filters on it, and because
    #: the maintenance planner's first query is "which Matters are TEST".
    data_class = models.CharField(
        max_length=16,
        choices=MatterDataClass.choices,
        default=MatterDataClass.REAL,
        db_index=True,
        verbose_name="andmeklass",
        help_text="Testandmed on arenduseks loodud kirjed; need ei kuulu päris aruandlusse.",
    )
    policy_area_other = models.CharField(
        max_length=400,
        blank=True,
        verbose_name="muu valdkond",
        help_text=(
            "Vabatekst, kui ükski loetletud valdkond ei sobi. Ei ole taksonoomia: "
            "siit ei teki uut valdkonda ega silti, ja statistika ei loe seda "
            "kanoonilise valdkonna hulka."
        ),
    )
    source_era = models.CharField(
        max_length=32,
        blank=True,
        verbose_name="allika periood",
        help_text="Töövihiku ajastu, mille reeglite järgi see kirje imporditi.",
    )
    reporting_year = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="aruandlusaasta",
        help_text="Püsiv aruandlusidentiteet, mis ei muutu, kui teema aastaid kestab.",
    )

    # -- institutions ------------------------------------------------------
    # KELLELT and KELLELE are different facts and are never unified.
    #
    # The sender side is 0..N; the addressee side stays 0..1. That asymmetry is
    # the domain's, not an oversight: a draft law reaches Koda from a ministry
    # *and* an association at once often enough that the single column was being
    # worked around, while an answer Koda sends goes to one body.
    #
    # There is deliberately no singular `source_organisation` accessor and no
    # notion of a primary sender. A compatibility property returning `.first()`
    # would let every one-sender assumption in the codebase survive unnoticed
    # and read as correct (Agent-E brief 8).
    source_organisations = models.ManyToManyField(
        "organisations.Organisation",
        through="matters.MatterSourceOrganisation",
        blank=True,
        related_name="matters_as_sources",
        verbose_name="algatajad või saatjad",
    )
    addressee_organisation = models.ForeignKey(
        "organisations.Organisation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="matters_as_addressee",
        verbose_name="adressaat",
    )

    # -- people ------------------------------------------------------------
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="owned_matters",
        verbose_name="vastutaja",
    )
    collaborators = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="collaborating_matters",
        verbose_name="kaastöötajad",
    )

    # -- classification ----------------------------------------------------
    track = models.CharField(
        max_length=32,
        choices=Track.choices,
        blank=True,
        default="",
        verbose_name="menetlusliik",
    )
    policy_areas = models.ManyToManyField(
        "taxonomy.PolicyArea",
        blank=True,
        related_name="matters",
        verbose_name="valdkonnad",
    )
    #: `Õigusakt` — what kind of legal or source instrument this Matter concerns.
    #:
    #: **Not `track`.** `Menetlusliik` says what kind of *procedure* a file
    #: belongs to; this says what kind of *instrument* it is about, and the two
    #: are answered independently: `ELi õiguse ülevõtmine` about a `Seadus` and
    #: `ELi algatus` about an `EL määrus` are both ordinary combinations. Neither
    #: value can be derived from the other, which is why one is not a filter over
    #: the other (docs/adr/0070).
    #:
    #: **Many, because the register really says several.** `S, M`,
    #: `direktiiv ja määrus` and `direktiiv/määrus` are all in the historical
    #: column, and a single-valued field would have to either concatenate them
    #: into a pseudo-value or throw one away. Blank stays valid: only the title
    #: is ever required.
    legal_instruments = models.ManyToManyField(
        "taxonomy.LegalInstrumentType",
        blank=True,
        related_name="matters",
        verbose_name="õigusakt",
    )
    #: The free text `Muu` reveals. One Matter's own words, never taxonomy:
    #: nothing here creates a `LegalInstrumentType`, and no statistic counts it
    #: as one — exactly the rule `policy_area_other` follows above.
    #:
    #: Distinct from `legal_instrument_raw` on `CurrentRegisterState`, which is
    #: what the *spreadsheet* said and is never written from the application.
    #: This is what a person typed. Both can exist for one record and neither
    #: replaces the other (task §21).
    legal_instrument_other = models.CharField(
        max_length=400,
        blank=True,
        verbose_name="õigusakti liik",
        help_text=(
            "Vabatekst, kui ükski loetletud õigusakt ei sobi. Ei ole taksonoomia: "
            "siit ei teki uut õigusakti liiki."
        ),
    )
    tags = models.ManyToManyField(
        "taxonomy.Tag",
        through="matters.TagAssignment",
        blank=True,
        related_name="matters",
        verbose_name="sildid",
    )
    stage = models.ForeignKey(
        "workflow.StageVocabulary",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="matters",
        verbose_name="hetkeseis",
    )

    # -- lifecycle ---------------------------------------------------------
    is_open = models.BooleanField(default=True, db_index=True, verbose_name="avatud")
    disposition = models.CharField(
        max_length=32,
        choices=Disposition.choices,
        blank=True,
        default="",
        verbose_name="lõpetamise põhjus",
    )
    disposition_reason = models.TextField(blank=True, verbose_name="lõpetamise selgitus")
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name="suletud")
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="closed_matters",
        verbose_name="sulges",
    )
    #: `Järglane` — the Matter this one's work continues under.
    #:
    #: ``Disposition.SUPERSEDED`` has always been able to say *that* a file
    #: continued elsewhere; nothing could say *where*. So the answer lived in a
    #: closure comment, which no query can follow: opening a 2019 Matter and
    #: asking what became of it meant reading a paragraph and then searching the
    #: register by hand for a title somebody half-remembered.
    #:
    #: One nullable self-reference, written only by :func:`close_matter` when
    #: the person closing names a successor, and read by the Matter page's
    #: `Seotud` block in both directions (`supersedes` is the predecessor side).
    #:
    #: ``PROTECT``, so a successor cannot be deleted out from under the record
    #: pointing at it. Never inferred: the register's own
    #: ``continues_under_reference`` is imported free text about a reference
    #: somebody typed, and resolving it to a row would manufacture a
    #: relationship the source never asserted (Teema redesign §16).
    superseded_by = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="supersedes",
        verbose_name="jätkub teemana",
    )

    # -- dates -------------------------------------------------------------
    received_date = models.DateField(null=True, blank=True, verbose_name="saabus")
    response_deadline = models.DateField(
        null=True, blank=True, db_index=True, verbose_name="arvamuse tähtaeg"
    )

    # -- substance ---------------------------------------------------------
    #: What this Matter is actually about, in the words a member would use.
    #:
    #: A formal legislative title is frequently a bad description of the
    #: business issue: "Käibemaksuseaduse muutmise seaduse eelnõu" says nothing
    #: about the four hundred companies that would gain a quarterly reporting
    #: duty. This is the two or three sentences that do, and it is the largest
    #: body text on the Matter page for that reason.
    #:
    #: Deliberately not `position_summary`, not `rationale_summary` and not the
    #: first `Entry`. Those answer *what Koda thinks*, *why*, and *what happened
    #: on a given day*; this answers *what is this*, which none of them can be
    #: made to mean without corrupting it. Optional, never backfilled, and blank
    #: on every historical row until somebody writes one (Teema redesign §6).
    brief_summary = models.TextField(
        blank=True,
        verbose_name="lühikokkuvõte",
        help_text="Mida see teema ettevõtjatele tähendab. Kaks kuni kolm lauset tavakeeles.",
    )
    position_summary = models.TextField(blank=True, verbose_name="Koja seisukoht")
    rationale_summary = models.TextField(blank=True, verbose_name="põhjendus")

    # -- authorization -----------------------------------------------------
    visibility = models.CharField(
        max_length=16,
        choices=Visibility.choices,
        default=Visibility.NORMAL,
        db_index=True,
        verbose_name="nähtavus",
    )

    objects = MatterQuerySet.as_manager()

    class Meta:
        verbose_name = "teema"
        verbose_name_plural = "teemad"
        ordering = ["-reference_year", "-reference_number", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["reference_year", "reference_number"],
                condition=models.Q(reference_year__isnull=False, reference_number__isnull=False),
                name="matters_unique_human_reference",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(reference_year__isnull=True, reference_number__isnull=True)
                    | models.Q(reference_year__isnull=False, reference_number__isnull=False)
                ),
                name="matters_reference_year_and_number_together",
            ),
            models.CheckConstraint(
                condition=~models.Q(title=""),
                name="matters_title_required",
            ),
            # Closure is consistent, but an archive row is never forced to
            # invent a closure reason it does not have.
            models.CheckConstraint(
                condition=(
                    models.Q(is_open=True, disposition="", closed_at__isnull=True)
                    | models.Q(is_open=False, record_mode=RecordMode.ARCHIVE)
                    | (
                        models.Q(
                            is_open=False,
                            record_mode=RecordMode.FULL,
                            closed_at__isnull=False,
                        )
                        & ~models.Q(disposition="")
                    )
                ),
                name="matters_closure_fields_consistent",
            ),
            # Visibility drives authorization, so the database refuses a value
            # the authorization code does not know how to interpret. Without
            # this, a typo in a migration or an integration could introduce a
            # value that reads as neither NORMAL nor RESTRICTED.
            models.CheckConstraint(
                condition=models.Q(visibility__in=[Visibility.NORMAL, Visibility.RESTRICTED]),
                name="matters_visibility_vocabulary",
            ),
            # A Matter cannot continue under itself. The form refuses it first,
            # but a chain that closes on one row would make "what became of
            # this" a question with no answer and an infinite one at once.
            models.CheckConstraint(
                condition=~models.Q(superseded_by=models.F("id")),
                name="matters_not_superseded_by_itself",
            ),
            # The same reasoning as the visibility constraint above. A value
            # outside the vocabulary reads as neither REAL nor TEST: it
            # would be missing from `real_data()` — so absent from every
            # statistic — and missing from `test_data()` too, so invisible to
            # the maintenance planner that is supposed to find it. Django
            # choices do not stop a bulk `update()`, a data migration or a shell
            # session; this does (Agent-C brief 10).
            models.CheckConstraint(
                condition=models.Q(data_class__in=[MatterDataClass.REAL, MatterDataClass.TEST]),
                name="matters_data_class_vocabulary",
            ),
            # Only a natively created Matter may be development data.
            #
            # A historical register row is somebody's real work from 2017. It
            # arrived through an importer, it carries provenance nothing can
            # reconstruct, and the one thing that must never happen to it is
            # being marked disposable because a control was next to the wrong
            # row. The service refuses it and this refuses it again, because the
            # service is not the only thing that can write this column
            # (Agent-C brief 12, 38).
            models.CheckConstraint(
                condition=(
                    ~models.Q(data_class=MatterDataClass.TEST)
                    | models.Q(origin=MatterOrigin.NATIVE)
                ),
                name="matters_test_data_is_native",
            ),
        ]
        indexes = [
            models.Index(fields=["owner", "is_open"], name="matters_owner_open"),
            models.Index(fields=["record_mode", "is_open"], name="matters_mode_open"),
            models.Index(
                fields=["response_deadline"],
                condition=models.Q(is_open=True),
                name="matters_open_deadline",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.display_reference or '—'} {self.title}"[:120]

    @property
    def display_reference(self) -> str:
        """The familiar ``YYYY_N`` label, derived rather than stored."""
        if self.reference_year is None or self.reference_number is None:
            return ""
        return f"{self.reference_year}_{self.reference_number}"

    @staticmethod
    def parse_reference(value: str) -> tuple[int, int] | None:
        parts = value.strip().split("_")
        if len(parts) != 2:
            return None
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            return None

    @property
    def is_restricted(self) -> bool:
        return self.visibility == Visibility.RESTRICTED

    @property
    def sender_names(self) -> str:
        """Every sender, comma-joined, in a stable order.

        A string rather than a template loop because the header needs the same
        value inside a `title` attribute, where an included partial would carry
        its own newlines into the tooltip. One renderer, so the summary line and
        the tooltip cannot drift.

        Ordered by `Organisation.Meta`, which sorts by name.
        """
        return ", ".join(organisation.name for organisation in self.source_organisations.all())

    @property
    def source_organisation_ids(self) -> set[Any]:
        """Sender primary keys, for a template deciding which boxes are ticked.

        A set rather than a queryset: `{% if pk in ... %}` inside a loop over
        every organisation would otherwise be one query per row, and the
        prefetch every caller already asks for is used instead.

        Named for the ids it returns rather than for the organisations, so
        nothing reads it as the singular field this replaced.
        """
        return {organisation.pk for organisation in self.source_organisations.all()}

    @property
    def is_test_data(self) -> bool:
        """Whether this record was made while developing the system.

        Child records — entries, submissions, documents, dates, victories —
        deliberately have no flag of their own. A child is test data when its
        Matter is, which is the only arrangement in which a REAL Matter cannot
        end up holding a TEST submission (Agent-C brief 20).
        """
        return self.data_class == MatterDataClass.TEST


class MatterSourceOrganisation(BaseModel):
    """One sender of one Matter — `KELLELT`, plural.

    An explicit through model for a relation that carries no extra facts, and
    the reason is integrity rather than modelling. The singular field this
    replaces was ``on_delete=PROTECT``: an Organisation that had sent Koda
    something could not be deleted out from under the record. Django's
    auto-created through table cascades instead, so the ordinary
    ``ManyToManyField`` would have quietly traded a guarantee for a shorter
    model definition — and nothing would have failed until the day somebody
    tidied up the organisation list and took a decade of provenance with it
    (Agent-E brief 73).

    So the table exists to hold ``PROTECT`` on the organisation side and
    ``CASCADE`` on the Matter side, and holds nothing else. No primary flag, no
    ordering, no role, no per-relation provenance: none of those is a current
    requirement, raw source provenance already lives in
    ``MatterSourceReference``, and a column added "in case" is a column
    something starts depending on (brief 74, 75).
    """

    matter = models.ForeignKey(
        Matter, on_delete=models.CASCADE, related_name="source_links", verbose_name="teema"
    )
    organisation = models.ForeignKey(
        "organisations.Organisation",
        on_delete=models.PROTECT,
        related_name="matter_source_links",
        verbose_name="organisatsioon",
    )

    class Meta:
        verbose_name = "teema saatja"
        verbose_name_plural = "teema saatjad"
        # No `ordering`. Presentation order comes from `Organisation.Meta`,
        # which sorts by name; an ordering here would join the GROUP BY of
        # every aggregate over this relation and turn each count into 1.
        constraints = [
            models.UniqueConstraint(
                fields=["matter", "organisation"],
                name="matters_unique_source_organisation_per_matter",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.matter_id} ← {self.organisation_id}"


class TagAssignment(BaseModel):
    """A confirmed association between a Matter and a Tag.

    A machine suggestion is not an assignment. Nothing writes here until a
    person has accepted it (master specification 11.2, 21.2).
    """

    matter = models.ForeignKey(
        Matter, on_delete=models.CASCADE, related_name="tag_assignments", verbose_name="teema"
    )
    tag = models.ForeignKey(
        "taxonomy.Tag",
        on_delete=models.PROTECT,
        related_name="assignments",
        verbose_name="silt",
    )
    source = models.CharField(
        max_length=32,
        choices=TagAssignmentSource.choices,
        default=TagAssignmentSource.MANUAL,
        verbose_name="allikas",
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="confirmed_tag_assignments",
        verbose_name="kinnitas",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True, verbose_name="kinnitatud")

    class Meta:
        verbose_name = "sildi seos"
        verbose_name_plural = "sildi seosed"
        ordering = ["tag__name_et"]
        constraints = [
            models.UniqueConstraint(fields=["matter", "tag"], name="matters_unique_tag_per_matter"),
        ]

    def __str__(self) -> str:
        return f"{self.matter_id} · {self.tag_id}"


class EntryQuerySet(models.QuerySet):
    def visible_to(self, user: object | None) -> EntryQuerySet:
        return apply_scope(self, child_visibility_q(scope_for_user(user)))

    def chronological(self) -> EntryQuerySet:
        """Newest first, with a deterministic tie break.

        Two entries can share an `occurred_at` — a lawyer writing up three
        meetings from the same morning — so the ordering falls back to creation
        time and then to the time-sortable primary key. Without that, pagination
        can silently repeat or drop a row.
        """
        return self.order_by("-occurred_at", "-created_at", "-id")


class Entry(VisibilityInheritingModel):
    """`Sissekanne` — the authored professional chronology.

    This is what replaces the OneNote page: a fast, dated, attributable note
    about what actually happened. It is narrative work, never the canonical
    record of a formal written opinion (master specification 11.2).
    """

    matter = models.ForeignKey(
        Matter, on_delete=models.CASCADE, related_name="entries", verbose_name="teema"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="authored_entries",
        verbose_name="autor",
    )
    kind = models.CharField(
        max_length=32,
        choices=EntryKind.choices,
        default=EntryKind.NOTE,
        db_index=True,
        verbose_name="liik",
    )
    # When the work happened, which is not when it was typed up. Friday's
    # meeting written up on Monday belongs on Friday in the timeline.
    occurred_at = models.DateTimeField(db_index=True, verbose_name="toimus")
    body = models.TextField(
        verbose_name="sisu",
        help_text="Sanitiseeritud HTML; kirjutamine käib ainult teenusekihi kaudu.",
    )
    organisation = models.ForeignKey(
        "organisations.Organisation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="entries",
        verbose_name="asutus",
    )

    edited_at = models.DateTimeField(null=True, blank=True, verbose_name="muudetud")
    edit_count = models.PositiveIntegerField(default=0, verbose_name="muudatuste arv")

    objects = EntryQuerySet.as_manager()

    class Meta:
        verbose_name = "sissekanne"
        verbose_name_plural = "sissekanded"
        ordering = ["-occurred_at", "-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(body=""),
                name="matters_entry_body_required",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    visibility_override__in=["", Visibility.NORMAL, Visibility.RESTRICTED]
                ),
                name="matters_entry_visibility_vocabulary",
            ),
        ]
        indexes = [
            models.Index(fields=["matter", "-occurred_at"], name="matters_entry_timeline"),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {format_estonian_date(self.occurred_at.date())}"

    def parent_visibility(self) -> str:
        return self.matter.visibility

    @property
    def was_edited(self) -> bool:
        return self.edit_count > 0


class EntryRevision(AppendOnlyModel):
    """The superseded text of an edited Entry.

    An entry is editable — a lawyer fixing a typo should not have to add a
    correction note — but the earlier wording is kept so an edit can never
    silently rewrite what the record said at the time. This is edit history for
    one authored record, not a second timeline (master specification 16.5).
    """

    entry = models.ForeignKey(
        Entry, on_delete=models.CASCADE, related_name="revisions", verbose_name="sissekanne"
    )
    revision_number = models.PositiveIntegerField(verbose_name="versioon")
    body = models.TextField(verbose_name="varasem sisu")
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="entry_revisions",
    )

    class Meta:
        verbose_name = "sissekande varasem versioon"
        verbose_name_plural = "sissekande varasemad versioonid"
        ordering = ["entry", "revision_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["entry", "revision_number"],
                name="matters_unique_entry_revision",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.entry_id} v{self.revision_number}"


#: How long a stored engagement link may be, in characters.
#:
#: One number, named once, because three columns and one validator have to
#: agree about it. `URLField(max_length=1000)` is the column; a value past it
#: reaches PostgreSQL as `StringDataRightTruncation`, which surfaces as a
#: `django.db.utils.DataError` with no row reference and an aborted
#: transaction — the failure shape every refusal in
#: `app.matters.services` exists to avoid. So the bound is enforced in
#: `normalize_engagement_url`, which every writer of these three columns
#: already passes through, and the column stays as the defence behind it
#: rather than the first validator (red-team finding F-1, 2026-09-12).
ENGAGEMENT_URL_MAX_LENGTH = 1000

#: The same bound, for the same reason, on an `Ülevaade / uudis` address.
#: Stated separately rather than shared with the engagement columns because the
#: two are different product decisions that happen to agree today: an engagement
#: link is a campaign address full of tracking parameters, and this one is a
#: published page. `normalize_overview_news_url` enforces it, refusing rather
#: than truncating, and the column stays the defence behind it.
WEBSITE_OVERVIEW_URL_MAX_LENGTH = 1000

#: The same bound again, on the public address a `Väline seisukoht` points at.
#:
#: Stated separately for the reason the two above it are: they are three
#: different product decisions that happen to agree on a number today. This one
#: is somebody else's page — a ministry's press release, an association's
#: position paper — and `normalize_external_position_url` enforces it, refusing
#: rather than truncating, with the column behind it as the defence.
EXTERNAL_POSITION_URL_MAX_LENGTH = 1000

#: How long the `Seisukoht` a `Väline seisukoht` carries may be.
#:
#: A concise written position or comment received from the other organisation
#: — enough to hold «toetab eelnõu, kuid soovib pikemat üleminekuaega» or the
#: two sentences a member association sent back by e-mail, and not enough to
#: invite somebody to paste a position paper in here instead of attaching it.
#: The document is still the document and the link is still the link
#: (docs/adr/0084 §2, amended 2026-09-16).
EXTERNAL_POSITION_SUMMARY_MAX_LENGTH = 1000

#: How long the lawyer's own `Juristi märkus` on a `Väline seisukoht` may be.
#:
#: The same bound as the position it sits beside, and stated separately because
#: it is a different product decision that happens to agree on a number: this is
#: **this office's** reading of somebody else's words, not the words. The two are
#: never concatenated, never indexed as one string and never rendered as one
#: sentence — the whole reason the column exists is that «MKM toetab varianti B»
#: and «nende põhjendus ei arvesta liikmete kulumõjuga» must not become one
#: statement attributed to the ministry (docs/adr/0090 §4).
EXTERNAL_POSITION_LAWYER_NOTE_MAX_LENGTH = 1000

#: How long the `Allikas` label on aggregate received feedback may be.
#:
#: «Tööstusettevõtete küsitlus», «Liikmete kirjavastused» — the name of a
#: collection of answers that has no single author. Short on purpose: it names a
#: source, it is not a summary of what the source said, and a record that needs
#: more than this needs an organisation or a file (docs/adr/0090 §3.3).
EXTERNAL_POSITION_SOURCE_LABEL_MAX_LENGTH = 200

#: How long a `Menetluse areng`'s own `Sündmus` line may be.
#:
#: The same bound `MatterEngagement.title` keeps, and for the same reason: this
#: is the line a reader scans a year of chronology by, and the line Package D
#: will project into one substantive history. Detail belongs in the note and the
#: paper belongs in the attachments — a box that invited paragraphs would make
#: this record a worse copy of the document beside it (docs/adr/0090 §5).
DEVELOPMENT_TITLE_MAX_LENGTH = 500


class MatterEngagementQuerySet(models.QuerySet):
    def visible_to(self, user: object | None) -> MatterEngagementQuerySet:
        """The only supported entry point for reading engagements."""
        return apply_scope(self, child_visibility_q(scope_for_user(user)))


class MatterEngagement(VisibilityInheritingModel):
    """`Kaasamine` — how Koda asked members and stakeholders for input.

    A consultation request published on koda.ee, a mailing sent through whatever
    campaign tool is current, a questionnaire, a link to something else. Today
    those live in people's memory and in mail folders, so the question "did we
    ask anybody about this, and where did we ask" has no answer on the file.
    This records the pointer.

    **It is a pointer, not a system.** There is no recipient list, no response
    store, no click tracking and no integration with any provider. Those are
    the vendors' job and they do it better; what the file needs is a dated,
    attributable statement that the outreach happened and where to look
    (Agent-F brief 5).

    What it is not
    --------------
    Not a `Document`: supporting evidence keeps going to the immutable evidence
    store, and a second place to attach bytes is a second place to lose them.
    Not an `Entry`: narrative belongs in the chronology, and adding an
    engagement deliberately writes no entry — one action must not become two
    records that can disagree. Not a `Submission`: asking members what they
    think is not Koda's formal outbound opinion, and folding it into that
    vocabulary would corrupt every submission statistic (brief 44, 45, 46).

    No deletion
    -----------
    v1 has create and edit and nothing else. A mistaken row is corrected, not
    removed, and a soft-delete state machine for a five-field record would be
    more machinery than the fact deserves (brief 16).
    """

    matter = models.ForeignKey(
        Matter, on_delete=models.CASCADE, related_name="engagements", verbose_name="teema"
    )
    kind = models.CharField(
        max_length=32,
        choices=EngagementKind.choices,
        default=EngagementKind.OTHER,
        db_index=True,
        verbose_name="liik",
    )
    #: **Who was engaged, or what the outreach was about.** One human-readable
    #: line naming the engagement, and since the approved Teema target it is
    #: asked as `Keda kaasati` — «liikmed», «kaubandusvaldkonna töögrupp».
    #:
    #: The question the composer prints changed; the column did not. A title
    #: recorded through the old five-field form said what the outreach was
    #: («Küsitlus liikmetele pakendiseaduse kohta») and an answer recorded
    #: through `+ Kaasamine` says who it reached — both are the one line that
    #: identifies this engagement to a reader, both render unchanged in the
    #: chronology, and no stored row means anything different today than it did
    #: yesterday. A second `audience` column beside this one would have left
    #: every historical row with an empty new field and every new row with an
    #: empty old one, and the section heading would then have to choose which of
    #: the two to print (docs/adr/0074 §4).
    title = models.CharField(max_length=500, verbose_name="pealkiri")
    #: Optional, and that is the point. An e-mail campaign frequently has no
    #: durable address a colleague could open later; requiring one would make
    #: the commonest kind of engagement unrecordable (brief 12).
    url = models.URLField(max_length=ENGAGEMENT_URL_MAX_LENGTH, blank=True, verbose_name="link")
    #: The two provider pointers, beside the generic one rather than instead of
    #: it.
    #:
    #: ADR 0027 kept vendors out of the schema — «`SendSmaily`, `Alchemer` and
    #: `koda.ee` are this year's tools», and a column naming one of them is
    #: wrong the day a contract changes. That reasoning was about the *channel*,
    #: which is still `kind` and still names no vendor. What it did not
    #: anticipate is that one consultation round routinely has **two** working
    #: links at once — the mailing that asked and the questionnaire that
    #: collected — and a single `url` forces somebody to drop one of them or to
    #: keep it in a note nobody can click. Two optional columns is the smaller
    #: wrong than a lost address, and neither is required, derived from, or
    #: counted (docs/adr/0027, amended 2026-09-12).
    #:
    #: Pointers only. Nothing here is fetched, no campaign is created, no
    #: response is read back, and the day Koda changes supplier these stay as
    #: the historical record of where that round's material lived.
    smaily_url = models.URLField(
        max_length=ENGAGEMENT_URL_MAX_LENGTH, blank=True, verbose_name="Smaily link"
    )
    alchemer_url = models.URLField(
        max_length=ENGAGEMENT_URL_MAX_LENGTH, blank=True, verbose_name="Alchemer link"
    )
    #: `Vastuseid` — how many responses this engagement actually drew.
    #:
    #: Null, not zero, for every row that predates the question and for every
    #: row somebody leaves blank: «nobody answered» and «nobody counted» are
    #: different facts about a consultation, and a column that cannot tell them
    #: apart reports the second as the first (docs/adr/0074 §5).
    #:
    #: **Nothing is derived from it.** There is no response *rate* here and no
    #: contacted count to divide by — this model is a pointer to outreach that
    #: happened elsewhere, and a percentage computed from one of its two halves
    #: would be a statistic about a denominator nobody stored (brief 5).
    response_count = models.PositiveIntegerField(null=True, blank=True, verbose_name="vastuseid")
    note = models.TextField(blank=True, verbose_name="märkus")
    #: Neutral on purpose. One model carries a published call, a mailing and a
    #: questionnaire, so `sent_at`, `published_at` and `survey_opened_at` would
    #: each be wrong for two thirds of the rows. It means "the date this
    #: engagement is about", and it is optional because somebody recording an
    #: old consultation may genuinely not know it (brief 8).
    occurred_on = models.DateField(null=True, blank=True, db_index=True, verbose_name="kuupäev")
    #: How exactly :attr:`occurred_on` is known — `Täpne päev`, `Kuu`,
    #: `Kvartal` or `Aasta`.
    #:
    #: `Kaasamise kuupäev` came off docs/adr/0079 §11's exact-only list in
    #: docs/adr/0082, and for the reason that list exists at all: everything
    #: else on it is a day somebody *recorded or owes*, and this is a statement
    #: about when something happened out in the world. A consultation round run
    #: «kevadel 2019», typed up years later from a mail folder, had two answers
    #: before this column — an invented day or an empty field — and both are
    #: worse than the one the person actually has.
    #:
    #: **Named for its date, not `date_precision`.** The three Stage-2G facts
    #: each carry one date and call this `date_precision`; this model carries
    #: two, and the other one — `feedback_deadline` — is exact-day-only and
    #: stays that way. A bare `date_precision` beside them would read as
    #: qualifying both.
    #:
    #: The stored value is the **anchor**: the first day of the period, which
    #: exists so a month has a place in a sort and is never a day anybody named
    #: (docs/adr/0079 §2). :attr:`display_date` is the only supported way to
    #: write it down.
    #:
    #: **No `period_end` beside it**, unlike `MatterImportantDate` and
    #: `MatterEffectiveDate`. Those store one because their question is *has
    #: this passed yet*, which is about a period's last day and is asked in
    #: SQL. Nothing asks that here: an engagement is something that already
    #: happened, it is never late, and the two places that read `occurred_on`
    #: as a number — the activity maximum and the register sort — order on the
    #: anchor, which is what the anchor is for. `NextAction` reached the same
    #: conclusion and carries precision alone (docs/adr/0079, *Alternatives*).
    #:
    #: `EXACT` by default, so every row written before this column existed
    #: reads exactly as it did — which is true of them: they were all entered
    #: through a box that asked for a day. **Nothing is backfilled and no
    #: historical precision is inferred.**
    occurred_on_precision = models.CharField(
        max_length=16,
        choices=DatePrecision.choices,
        default=DatePrecision.EXACT,
        verbose_name="kuupäeva täpsus",
    )
    #: `Tagasisidet ootame kuni` — the day the lawyer asked people to answer by.
    #:
    #: A consultation that starts today almost always names a reply-by date in
    #: the same breath — «ootan vastuseid kuni 22.09» — and until now there was
    #: nowhere on the file to put it, so it lived in the mailing and in
    #: somebody's memory.
    #:
    #: **Set, it opens a wait, and the wait is work** (docs/adr/0086 §3).
    #: docs/adr/0078 §3 made this column inert - no work item, no badge, no
    #: reading of lateness - on a rule that is right about the *fact* and wrong
    #: about the *state*: what was asked of a ministry is indeed not an
    #: obligation this office owes anybody, but a lawyer who asked for answers
    #: by the 22nd has a thing to do on the 22nd, which is to read what came
    #: back and write it down. So the deadline still creates no `NextAction`,
    #: is still not `Matter.response_deadline`, is still not a
    #: `MatterImportantDate` and still contributes to no response-deadline
    #: statistic, no work-victory metric, no search row and no archive
    #: projection - and it now draws one `WorkItem` of its own, which
    #: :attr:`feedback_closed_at` ends (`app/matters/work_items.py`).
    #:
    #: The historical-row objection docs/adr/0078 §3 raised is answered by
    #: *where* the wait is read rather than by keeping the column inert: only
    #: an **open FULL** Matter reaches a work surface, and a decade of imported
    #: consultations are `ARCHIVE` rows no work source has ever looked at.
    #:
    #: Null for every row that predates the question and for every row somebody
    #: leaves blank. Nothing is inferred from `occurred_on`, `created_at`, the
    #: note or the provider links — a date guessed from a neighbouring column is
    #: a date nobody chose.
    #:
    #: Not indexed. The work source reads it through the Matter it hangs off,
    #: which is the index this table already carries.
    feedback_deadline = models.DateField(
        null=True, blank=True, verbose_name="tagasisidet ootame kuni"
    )
    #: `Saadud tagasiside / arvamused` — what came back, in writing.
    #:
    #: The half of a consultation the file could never hold. A round produced a
    #: pointer to where it was asked and a count of how many answered, and the
    #: answers themselves lived in a mail folder: «liikmed toetasid, v.a
    #: kaubandus» had to go into a `Sissekanne` that then said nothing about
    #: which round it belonged to (docs/adr/0086 §5).
    #:
    #: Separate from :attr:`note`, deliberately. `Märkus` is what the person
    #: recording the round wanted to say *about the round* - where the list came
    #: from, why it was sent late. This is what the people who were asked said
    #: back. One column carrying both would be a column whose meaning depends on
    #: who wrote the sentence.
    #:
    #: **Not required to close a wait**, and blank is a real answer: «keegi ei
    #: vastanud» is a result, and a completion that demanded prose would make
    #: the commonest disappointing outcome unrecordable (docs/adr/0086 §6).
    #:
    #: **Not indexed.** The search projection reads `title`, `note` and the link
    #: hosts, and this round does not widen it - what a member wrote to Koda in
    #: confidence is not a thing to make findable from the header search box
    #: without the visibility question being asked first
    #: (`app/search/child_indexing.py`, docs/adr/0086 §9).
    feedback_received = models.TextField(blank=True, verbose_name="saadud tagasiside")
    #: When the wait was closed - the moment a person said «this round is
    #: finished», or the moment the Matter closed underneath it.
    #:
    #: `NULL` while the wait is open and for every row that never had a
    #: deadline. Paired with :attr:`feedback_deadline` by
    #: `matters_engagement_feedback_closure_needs_deadline`: a wait that does
    #: not exist cannot be completed, so clearing the deadline clears the
    #: closure with it (`app.matters.services.update_engagement`).
    #:
    #: A timestamp rather than a date, because the act is a save somebody made
    #: at a moment and the audit row beside it says so to the microsecond. What
    #: it is emphatically *not* is the day the feedback arrived: nobody is asked
    #: that, and inventing it from the save would be the same manufactured fact
    #: docs/adr/0078 §2 removed from `occurred_on`.
    feedback_closed_at = models.DateTimeField(
        null=True, blank=True, verbose_name="tagasiside ootamine lõpetatud"
    )
    #: Who closed the wait. Null for a closure written by no person - the
    #: Matter closure path passes whatever actor closed the file, and a shell
    #: or a fixture passes nothing.
    feedback_closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="closed_engagement_feedback",
        verbose_name="tagasiside ootamise lõpetas",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="recorded_engagements",
        verbose_name="lisas",
    )

    objects = MatterEngagementQuerySet.as_manager()

    class Meta:
        verbose_name = "kaasamine"
        verbose_name_plural = "kaasamised"
        # Newest relevant date first, and a row with no date sorts *last*
        # rather than first: `NULLS LAST` is what stops an undated record
        # reading as though it happened today (brief 18).
        ordering = [models.F("occurred_on").desc(nulls_last=True), "-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(title=""),
                name="matters_engagement_title_required",
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=EngagementKind.values),
                name="matters_engagement_kind_vocabulary",
            ),
            models.CheckConstraint(
                condition=models.Q(occurred_on_precision__in=DatePrecision.values),
                name="matters_engagement_occurred_precision_vocabulary",
            ),
            # A wait that does not exist cannot be completed. `feedback_deadline`
            # is what opens the wait, so a closure timestamp without one would be
            # a row claiming to have finished waiting for something nobody asked
            # for - and `has_open_feedback_wait` would read it as neither open
            # nor closed (docs/adr/0086 §6).
            models.CheckConstraint(
                condition=models.Q(feedback_closed_at__isnull=True)
                | models.Q(feedback_deadline__isnull=False),
                name="matters_engagement_feedback_closure_needs_deadline",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    visibility_override__in=["", Visibility.NORMAL, Visibility.RESTRICTED]
                ),
                name="matters_engagement_visibility_vocabulary",
            ),
        ]
        indexes = [
            models.Index(fields=["matter", "-occurred_on"], name="matters_engagement_matter_date"),
        ]

    def __str__(self) -> str:
        return self.title[:120]

    def parent_visibility(self) -> str:
        return self.matter.visibility

    @property
    def display_date(self) -> str:
        """`Kaasamise kuupäev`, written the way it was actually known.

        The only supported way to put `occurred_on` on a screen. A row stored
        as `2026-10-01` + `MONTH` reads *oktoober 2026* here and must read that
        on every surface that shows it — the chronology, the correction form's
        own headline, *Viimane tegevus*, *Viimati muudetud*. Rendering the
        stored value instead would print `01.10.2026`, which is an anchor and
        not a day anybody named (docs/adr/0079 §2, §3, docs/adr/0082).

        Empty string for a row with no date. What the *chronology* prints in
        that case is «Kuupäev teadmata», which is a sentence about the file and
        belongs to the surface saying it, not to the model
        (`app.matters.timeline.ENGAGEMENT_DATE_UNKNOWN`).
        """
        return format_at_precision(self.occurred_on, self.occurred_on_precision)

    @property
    def has_approximate_date(self) -> bool:
        """Whether this engagement is dated to a period rather than to a day.

        `False` for a row with no date at all: an unknown date is not an
        approximate one, and the two say different things to a reader.
        """
        return self.occurred_on is not None and is_approximate(self.occurred_on_precision)

    @property
    def has_feedback_wait(self) -> bool:
        """Whether a reply-by date was ever named on this round.

        The question `has_open_feedback_wait` and `feedback_wait_is_closed`
        partition. A round with no deadline is in neither state, which is what
        every row written before docs/adr/0078 §3 is and what most rows will
        always be.
        """
        return self.feedback_deadline is not None

    @property
    def has_open_feedback_wait(self) -> bool:
        """Whether this round is still waiting for the answers it asked for.

        The one predicate the work surfaces, the Teema page and the completion
        service all read, so «is this still open» cannot be answered two ways.
        A deadline that has gone by is still *open* — the day passing is not a
        result, and nothing closes a wait except somebody saying so or the
        Matter shutting underneath it (docs/adr/0086 §4, §6).
        """
        return self.feedback_deadline is not None and self.feedback_closed_at is None

    @property
    def feedback_wait_is_closed(self) -> bool:
        """Whether a wait that existed has been completed."""
        return self.feedback_deadline is not None and self.feedback_closed_at is not None

    def feedback_wait_is_due(self, today: date | None = None) -> bool:
        """Whether an open wait has reached the day it asked to be answered by.

        Inclusive of the day itself: «vastake 22. septembriks» is a thing to
        look at *on* the 22nd, not on the 23rd. Before that day the round is
        waiting and says so; from it, it is the lawyer's to finish
        (docs/adr/0086 §4).

        `False` for a closed wait and for a round that never had a deadline,
        so a caller can ask this without asking `has_open_feedback_wait` first
        and get the honest answer either way.
        """
        if not self.has_open_feedback_wait or self.feedback_deadline is None:
            return False
        return self.feedback_deadline <= (today or timezone.localdate())

    @staticmethod
    def _hostname(url: str) -> str:
        """The host an address points at, and nothing else about the address.

        `urlsplit(url).netloc` is the URL's **authority**, which is
        ``userinfo@host:port`` — so a link pasted out of a provider dashboard
        that carries basic-auth credentials yields a "host" with a username and
        a password in it, and this model copies its hosts into a rendered label
        and into the search projection's `alias_text`. `parsed.hostname` is the
        host: it drops the userinfo and the port, and lowercases what is left
        (red-team finding F-2, 2026-09-12).

        The port goes with the userinfo deliberately. "Host" in this model
        means the name that tells a reader which provider a link runs to, and
        `:8443` is transport machinery of exactly the kind the label exists to
        leave out; no product contract here asks for host and port.

        The fallback is for the historical row that is not a parseable address
        at all, and it drops anything before an ``@`` before bounding what is
        left: ``https://user:pw@/path`` has a non-empty authority and *no*
        hostname, so a fallback that printed the stored string would put the
        credentials back exactly where this method exists to keep them out of.
        `normalize_engagement_url` now refuses that shape on the way in as well.
        """
        if not url:
            return ""
        from urllib.parse import urlsplit

        try:
            host = urlsplit(url).hostname or ""
        except ValueError:
            # A malformed authority — an unbracketed IPv6 literal, a port that
            # is not a number. Unparseable is not a crash on a read path.
            host = ""
        if host:
            return host
        remainder = url.split("://", 1)[-1]
        if "@" in remainder:
            remainder = remainder.rsplit("@", 1)[-1]
        return remainder[:60]

    @property
    def link_label(self) -> str:
        """A link's host, for a control that must not print a tracking URL.

        Campaign and survey links routinely run to hundreds of characters of
        query string. The host is what tells a reader where the link goes; the
        rest is machinery (brief 35) — including any credentials the address
        carries, which is why this is the parsed hostname and not the authority.
        """
        return self._hostname(self.url)

    @classmethod
    def _host_terms(cls, url: str) -> list[str]:
        """A single address, reduced to the words somebody would search for."""
        host = cls._hostname(url)
        if not host:
            return []
        labels = [part for part in host.split(".") if part and part != "www"]
        return [host, *labels[:-1]] if len(labels) > 1 else [host]

    @property
    def link_search_terms(self) -> list[str]:
        """Every stored address's host, and each of its labels.

        The host alone is not enough. PostgreSQL tokenises
        ``survey.alchemer.example`` as one ``host`` token, so somebody typing
        the vendor's name finds nothing — which is precisely the search the
        column exists to answer. The labels are indexed beside the whole host
        so both work, and ``www`` and the public suffix are dropped because
        they match everything (Agent-F brief 47).

        **The host, and never the query string.** A campaign address carries
        recipient ids and one-time tokens after the ``?``; indexing those would
        put somebody's unsubscribe key into a search field and answer queries
        nobody meant to ask. The three columns are read the same way for the
        same reason, so a provider link is findable by its provider's name and
        by nothing else (docs/adr/0027, amended 2026-09-12).

        **Nor the userinfo, nor the port.** Both live in the URL's authority
        beside the host, and `_hostname` is what separates them; before it, this
        list was built from `netloc` and a link carrying basic-auth credentials
        indexed the password as a matchable token (red-team finding F-2).
        """
        terms: list[str] = []
        for url in (self.url, self.smaily_url, self.alchemer_url):
            terms.extend(self._host_terms(url))
        return list(dict.fromkeys(terms))


class MatterWebsiteOverviewQuerySet(models.QuerySet):
    def visible_to(self, user: object | None) -> MatterWebsiteOverviewQuerySet:
        """The only supported entry point for reading website overviews."""
        return apply_scope(self, child_visibility_q(scope_for_user(user)))

    def planned(self) -> MatterWebsiteOverviewQuerySet:
        return self.filter(status=WebsiteOverviewStatus.PLANNED)

    def published(self) -> MatterWebsiteOverviewQuerySet:
        return self.filter(status=WebsiteOverviewStatus.PUBLISHED)


class MatterWebsiteOverview(VisibilityInheritingModel):
    """`Ülevaade / uudis` — this Matter, written up somewhere the public can read it.

    A lawyer finishing a round of work frequently decides that it should be
    written up: an overview for the membership on the Chamber's own site, or a
    news item somewhere else. Until now the file had nowhere to hold that: the
    intention lived in somebody's head until the page appeared, and the
    published address lived in a browser history. The question «did we ever
    write this up, and where is it» had no answer on the Teema.

    So it is a record with three states and two columns. `Plaanis` says the
    write-up is owed and carries neither an address nor a date, because neither
    exists yet. `Avaldatud` says it happened, and carries both. `Tühistatud` says
    the plan was dropped, which is part of the file rather than something to
    delete (docs/adr/0081).

    One activity, and no kind column
    --------------------------------
    The record was `Kodulehe ülevaade` and could point only at koda.ee. It is
    now neutral, and **there is deliberately no type selector**: an overview and
    a news item are the same act — this file, published where somebody can read
    it — and the only thing that distinguishes them is the address, which the
    row already carries. A `kind` here would be a question asked at planning
    time, when the answer is not yet known, and every consumer would then have
    to branch on a value nobody could correct (docs/adr/0085 §1).

    What it is not
    --------------
    Not a `Märge`: a note is narrative, and «ülevaade on plaanis» written as
    prose is a sentence nothing can ask a question of. Not a `Document`: nothing
    is uploaded here and a published web page is not the evidence store.
    Not `Tulemuse tõend` and not a `Submission`: a summary written for the
    membership is not the Chamber's formal outbound opinion, and folding it into
    that vocabulary would corrupt every submission statistic. Not a `Töövõit`:
    publishing a page is not a claim that anything was won. Not a `NextAction`
    and not a deadline: an overview that is owed is not a dated instruction, and
    a column that generated a task would make every Matter with a plan on it
    look late (docs/adr/0081 §4).

    Zero, one or many
    -----------------
    A Matter may carry none, one, or several. A long proceeding is written up
    more than once, and there is deliberately no uniqueness on ``matter``. The
    one uniqueness there *is* guards against the same address being filed twice
    on one Matter, which is a duplicate record rather than a second overview.

    No deletion
    -----------
    Create, publish, cancel and correct. A mistaken plan is cancelled, not
    removed; a wrong address is corrected, not replaced by a new row. That is
    the same rule `MatterEngagement` keeps, for the same reason.
    """

    matter = models.ForeignKey(
        Matter,
        on_delete=models.CASCADE,
        related_name="website_overviews",
        verbose_name="teema",
    )
    status = models.CharField(
        max_length=16,
        choices=WebsiteOverviewStatus.choices,
        default=WebsiteOverviewStatus.PLANNED,
        db_index=True,
        verbose_name="olek",
    )
    #: Where the published overview actually is. Empty until it is published,
    #: and empty forever on a plan that was dropped — the constraints below say
    #: so in the database rather than only in the service, because a URL on a
    #: record that claims nothing was published is a link a reader would follow.
    url = models.URLField(
        max_length=WEBSITE_OVERVIEW_URL_MAX_LENGTH, blank=True, verbose_name="link"
    )
    #: The day the overview or news item went up, as a person states it.
    #:
    #: **Never stamped by the server.** The panel that records a publication
    #: offers today because today is the usual answer, and what the person left
    #: in the box is what is stored. Nothing anywhere derives this from
    #: ``created_at``, from ``published_at`` or from the clock: a date the
    #: application invented is a date nobody can correct, because nobody knows
    #: it is wrong (docs/adr/0078 §2, and docs/adr/0081 §3).
    published_on = models.DateField(null=True, blank=True, verbose_name="avaldamise kuupäev")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="planned_website_overviews",
        verbose_name="lisas",
    )
    #: Who recorded the publication, and when they recorded it — which is a
    #: different fact from ``published_on``, the day the page appeared. A
    #: correction months later changes the second and never the first.
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="published_website_overviews",
        verbose_name="avaldamise kirjutas",
    )
    published_at = models.DateTimeField(null=True, blank=True, verbose_name="avaldatuks märgitud")
    cancelled_at = models.DateTimeField(null=True, blank=True, verbose_name="tühistatud")
    #: When this record last moved between states, whichever state that was.
    #: The same column `MatterWorkVictory` keeps for the same purpose.
    status_changed_at = models.DateTimeField(default=timezone.now, verbose_name="oleku muutus")

    objects = MatterWebsiteOverviewQuerySet.as_manager()

    class Meta:
        verbose_name = "ülevaade / uudis"
        verbose_name_plural = "ülevaated / uudised"
        # Newest publication first, and a row that has not been published sorts
        # *last* rather than first: `NULLS LAST` is what stops a plan reading as
        # though it went up today. The same ordering, for the same reason, as
        # `MatterEngagement` (brief 18).
        ordering = [models.F("published_on").desc(nulls_last=True), "-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=WebsiteOverviewStatus.values),
                name="matters_website_overview_status_vocabulary",
            ),
            # **The two halves of «published means published».** An overview in
            # that state has an address and a day; one in any other state has
            # neither. Written as a single implication each way so that no row
            # can exist claiming a publication with nothing to show, and none
            # can carry a link while saying it was never published.
            models.CheckConstraint(
                condition=(
                    ~models.Q(status=WebsiteOverviewStatus.PUBLISHED)
                    | (~models.Q(url="") & models.Q(published_on__isnull=False))
                ),
                name="matters_website_overview_published_has_link_and_date",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=WebsiteOverviewStatus.PUBLISHED)
                    | models.Q(url="", published_on__isnull=True)
                ),
                name="matters_website_overview_unpublished_has_neither",
            ),
            # A published row records when the publication was written down, and
            # a cancelled one when the plan was dropped. `published_by` and the
            # actor behind a cancellation are deliberately *not* required by the
            # database: a later reviewed import may carry a decision whose author
            # is not a user of this system, exactly as
            # `intelligence_work_victory_confirmed_has_timestamp` allows.
            models.CheckConstraint(
                condition=(
                    ~models.Q(status=WebsiteOverviewStatus.PUBLISHED)
                    | models.Q(published_at__isnull=False)
                ),
                name="matters_website_overview_published_has_timestamp",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status=WebsiteOverviewStatus.CANCELLED)
                    | models.Q(cancelled_at__isnull=False)
                ),
                name="matters_website_overview_cancelled_has_timestamp",
            ),
            # The only uniqueness this record needs, and it is not on `matter`:
            # a Matter may be written up several times. What must not happen is
            # one address filed twice against one Matter, which is not a second
            # overview but the same one recorded twice — and it is scoped to
            # PUBLISHED rows because every other row has an empty `url`, and
            # three plans on one Matter are three legitimate rows.
            models.UniqueConstraint(
                fields=["matter", "url"],
                condition=models.Q(status=WebsiteOverviewStatus.PUBLISHED),
                name="matters_website_overview_one_row_per_published_link",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    visibility_override__in=["", Visibility.NORMAL, Visibility.RESTRICTED]
                ),
                name="matters_website_overview_visibility_vocabulary",
            ),
        ]
        indexes = [
            # The planned strip's own question — «what does this Matter still
            # owe the website» — and the one closure asks when it cancels them.
            models.Index(fields=["matter", "status"], name="matters_weboverview_mat_stat"),
            # And the chronology's, which reads one Matter's overviews in the
            # model's own order. The pair `MatterEngagement` keeps on
            # `(matter, -occurred_on)`, for the same read.
            models.Index(fields=["matter", "-published_on"], name="matters_weboverview_mat_pub"),
        ]

    def __str__(self) -> str:
        return f"{self.get_status_display()}: {self.matter_id}"[:120]

    def parent_visibility(self) -> str:
        return self.matter.visibility

    @property
    def revision_token(self) -> str:
        """Which version of this record a rendered form was filled from.

        ``updated_at``, for the reasons `personal_note_revision` gives: `auto_now`
        sets it on every write, PostgreSQL stores it to the microsecond so two
        saves cannot share one, and having it costs no migration.

        A property on the record rather than only a function in the service,
        because both a template and the service need the token and a second
        spelling of it — a `date:"c"` in the template, say, which renders in the
        *reader's* timezone — would produce a different string for the same row
        and refuse every save. `app.matters.services.website_overview_revision`
        is the service-side name and returns exactly this.
        """
        return self.updated_at.isoformat()

    @property
    def is_planned(self) -> bool:
        return self.status == WebsiteOverviewStatus.PLANNED

    @property
    def is_published(self) -> bool:
        return self.status == WebsiteOverviewStatus.PUBLISHED

    @property
    def is_cancelled(self) -> bool:
        return self.status == WebsiteOverviewStatus.CANCELLED


class MatterExternalPositionQuerySet(models.QuerySet):
    def visible_to(self, user: object | None) -> MatterExternalPositionQuerySet:
        """The only supported entry point for reading external positions."""
        return apply_scope(self, child_visibility_q(scope_for_user(user)))


class MatterExternalPosition(VisibilityInheritingModel):
    """`Väline seisukoht` — what somebody else said about this Matter, on file.

    A ministry publishes a press release, an association sends its position
    paper, a member organisation answers a consultation in writing. The file has
    had nowhere to keep any of it: the address lived in a browser history and
    the PDF lived in a mail folder, so the question «kes on selle kohta midagi
    öelnud, ja kus see on» had no answer on the Teema (docs/adr/0084).

    So it is factual reference material and nothing more: **who** said it, a
    **source** a colleague can open, and — when it is known — **when** they said
    it.

    What it is not
    --------------
    Not Koda's own opinion and not a `Submission`: recording what a ministry
    published is the opposite claim from the Chamber having sent something, and
    folding the two together would corrupt every submission statistic. Not a
    `Töövõit`: somebody else's position is not a claim that anything was won,
    and a position *against* Koda is exactly as ordinary a record as one for it.
    Not a `NextAction` and not a deadline: an external position is something
    that already happened, so it creates no work, moves no
    `Matter.response_deadline` and makes no Matter read as late. Not a
    `Kaasamine`: an engagement records that Koda **asked**, and this records
    that somebody else **said** — the round and the answer are two facts, which
    is why :attr:`engagement` relates them rather than merging them. And not a
    `Märge`: «Rahandusministeerium avaldas oma seisukoha» written as prose is a
    sentence nothing can ask a question of.

    The source minimum
    ------------------
    A position with no source is hearsay on a file, so **one of three** is
    required: the written :attr:`summary` — what the organisation actually
    said, in their words or a faithful paraphrase of them — a public ``url``,
    or an attached `Document` through the ordinary `DocumentLink`
    architecture. Any one of them alone is enough and any combination is
    ordinary.

    The commonest feedback a department receives has neither a file nor a
    public address: a member association answers a consultation in two
    sentences by e-mail, or a ministry official says something on the telephone
    that is worth recording against the file. Refusing those was refusing to
    record ordinary feedback, and what it actually bought was a fabricated
    source — a made-up description or a URL pointing at something else — which
    is worse than the record it was protecting (docs/adr/0084 §3, amended
    2026-09-16).

    Two of the three are columns on this row and the third is a row in another
    table, so the rule still cannot be a `CHECK`: a constraint sees one row and
    cannot count `documents_documentlink`. It lives in
    `app.matters.services._external_position_source`, called by
    `record_external_position` before the insert and by
    `correct_external_position` under the row lock, which are the two doors a
    person's save comes through.

    Zero, one or many
    -----------------
    A Matter may carry none, one, or several — including several from the same
    `Organisation`, which is the ordinary case when a ministry states a position
    twice in a long proceeding. There is deliberately no uniqueness on
    ``(matter, organisation)`` and none on ``(matter, url)``: two positions may
    genuinely be published at one address (a page that carries both), and a
    constraint refusing that would make the second one unrecordable.

    No deletion
    -----------
    Create and correct, like `MatterEngagement`. A mistaken row is corrected,
    not removed: what the file recorded and who recorded it is part of the file.
    """

    matter = models.ForeignKey(
        Matter,
        on_delete=models.CASCADE,
        related_name="external_positions",
        verbose_name="teema",
    )
    #: **Whose position this is**, from the one shared catalogue that already
    #: answers `Saatja` and `Adressaat` (docs/adr/0063, docs/adr/0073).
    #:
    #: **Required for a discovered position and optional for received
    #: feedback**, which is the one asymmetry :attr:`provenance` introduces and
    #: the whole of docs/adr/0090 §3.3. A ministry's published opinion with no
    #: author is an anonymous claim on a professional file and stays refused. A
    #: survey of 234 industrial companies that produced 58 answers has no single
    #: author, and the two things this column could have been given for it were
    #: an invented organisation called «234 ettevõtet» or one arbitrary
    #: respondent standing for the rest — both of which put a fact on the file
    #: that nobody stated. So the column is nullable and
    #: `matters_external_position_author_or_label` is what keeps the rule: a
    #: position must name an organisation, and received feedback may name a
    #: :attr:`source_label` instead.
    #:
    #: Nothing about a *named* position is weakened. The ordinary record still
    #: carries the catalogue's own row, `PROTECT` still refuses to lose an
    #: institution a Matter cites, and the two correction doors ask the same
    #: question again against what the save would result in.
    organisation = models.ForeignKey(
        "organisations.Organisation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="matter_external_positions",
        verbose_name="organisatsioon",
    )
    #: How this position reached the file — `Meile saadetud tagasiside`,
    #: `Teiste arvamus`, or `Täpsustamata` for the rows that predate the
    #: question.
    #:
    #: The distinction the first lawyer test asked for, as **structured data and
    #: never an inference**: not the presence of a `Kaasamine` link, not whether
    #: an organisation was named, not whether a URL was given and not whether a
    #: file was attached. Every one of those combinations occurs under both
    #: values — a ministry does answer consultations, and a member company's
    #: position paper does get found on its website — so every inference from
    #: them is wrong for some real record (lawyer feedback 12, docs/adr/0090 §3).
    #:
    #: `LEGACY` by default, which is what a caller that does not say means and
    #: what every row written before this column existed says. **Nothing is
    #: backfilled**: the two readings a migration could have made are exactly the
    #: inferences above, and an unknown provenance is better than a manufactured
    #: one (docs/adr/0090 §3.4).
    #:
    #: Indexed, because the chronology and the two panels read one Matter's
    #: positions by provenance and the department will ask «what came back to us
    #: on this file» as a list.
    provenance = models.CharField(
        max_length=16,
        choices=ExternalPositionProvenance.choices,
        default=ExternalPositionProvenance.LEGACY,
        db_index=True,
        verbose_name="päritolu",
    )
    #: `Allikas` — what to call a collection of answers that has no one author.
    #:
    #: «Tööstusettevõtete küsitlus», «Liikmete kirjavastused». Optional, and
    #: **only meaningful on received feedback**: a discovered position has an
    #: author by definition, and a label beside a named ministry would be a
    #: second name for a body the catalogue already names
    #: (`matters_external_position_label_is_received`).
    #:
    #: It is not a title and not a summary. It names where the answers came
    #: from, so that a reader scanning the chronology sees «Meile saadetud
    #: tagasiside: Tööstusettevõtete küsitlus» rather than a row whose author
    #: cell is empty (docs/adr/0090 §3.3).
    source_label = models.CharField(
        max_length=EXTERNAL_POSITION_SOURCE_LABEL_MAX_LENGTH,
        blank=True,
        verbose_name="allikas",
    )
    #: Where the position was published, when it was published anywhere.
    #:
    #: Optional on its own, and one of the three answers to the source rule in
    #: the class docstring — a position whose `Seisukoht` says what the
    #: organisation wrote needs no address at all. `http` and `https` only,
    #: refused rather than
    #: truncated past :data:`EXTERNAL_POSITION_URL_MAX_LENGTH`, and checked by a
    #: parsed host so that an address whose «host» is nothing but credentials
    #: cannot be stored (`normalize_external_position_url`).
    url = models.URLField(
        max_length=EXTERNAL_POSITION_URL_MAX_LENGTH, blank=True, verbose_name="link"
    )
    #: `Seisukoha kuupäev` — the day the other organisation stated it.
    #:
    #: Optional, because a position found months later frequently carries no
    #: date a reader could defend, and «kuupäev teadmata» is a fact this column
    #: has to be able to hold. **Nothing derives it**: not `created_at`, not the
    #: engagement it answers, not the day somebody typed it in. A date the
    #: application invented is a date nobody can correct, because nobody knows
    #: it is wrong (docs/adr/0078 §2).
    stated_on = models.DateField(null=True, blank=True, db_index=True, verbose_name="kuupäev")
    #: How exactly :attr:`stated_on` is known — `Täpne päev`, `Kuu`, `Kvartal`
    #: or `Aasta`.
    #:
    #: The same reasoning that took `Kaasamise kuupäev` off docs/adr/0079 §11's
    #: exact-only list in docs/adr/0082, and the stronger case: this is a
    #: statement about *somebody else's* timetable, which is exactly what §11's
    #: list excludes. A position remembered as «kevadel 2019» had two answers
    #: before this column — an invented day or an empty field — and both are
    #: worse than the one the person actually has.
    #:
    #: The stored value is the **anchor**: the first day of the period, which
    #: exists so a month has a place in a sort and is never a day anybody named.
    #: :attr:`display_date` is the only supported way to write it down
    #: (docs/adr/0079 §2, §3).
    stated_on_precision = models.CharField(
        max_length=16,
        choices=DatePrecision.choices,
        default=DatePrecision.EXACT,
        verbose_name="kuupäeva täpsus",
    )
    #: `Seisukoht` — what the other organisation actually said, in writing.
    #:
    #: Optional on its own and one of the three answers to the source rule
    #: above: a position recorded here and nowhere else is a complete record,
    #: because a two-sentence reply by e-mail is the commonest feedback a
    #: department gets and it has neither a file nor a published address.
    #:
    #: It is a faithful record of somebody else's words and never this office's
    #: reading of them: nothing extracts it, nothing generates it, nothing
    #: indexes it, nothing summarises the linked document into it, and no
    #: stance vocabulary is derived from it (docs/adr/0084 §2, §6).
    summary = models.TextField(blank=True, verbose_name="seisukoht")
    #: `Juristi märkus` — this office's own reading of what the other
    #: organisation said.
    #:
    #: «Nende põhjendus ei arvesta liikmete kulumõjuga» is a lawyer's
    #: professional comment, and before this column it had two homes: appended to
    #: :attr:`summary`, where it became part of what the ministry was recorded as
    #: having said, or a separate `Märge` that then said nothing about which
    #: position it was about. The first is the serious one — a file that
    #: attributes this office's criticism to the body it is criticising is a file
    #: that lies about a professional record (lawyer feedback 12,
    #: docs/adr/0090 §4).
    #:
    #: **Never a source.** `_external_position_source` does not count it, and it
    #: may not: a record whose only content is Koda's opinion of a position
    #: nobody can read is a record of nothing. A position needs the `Seisukoht`,
    #: an address or a file; this is what somebody adds *beside* one of the three.
    #:
    #: **Never merged into the source on any surface.** The chronology prints it
    #: on its own line under its own label, the audit payload records only that it
    #: exists, and the search projection does not read it — so no rendered
    #: result can show this office's words as the other organisation's
    #: (docs/adr/0090 §4, §9).
    lawyer_note = models.TextField(blank=True, verbose_name="juristi märkus")
    #: `Seotud kaasamine` — the round this position answered, where it answered
    #: one.
    #:
    #: Optional and must stay optional: the commonest external position is
    #: **unsolicited**, published because the ministry chose to publish it, and
    #: a required relation would make that unrecordable. Where it is set it says
    #: something no other column can — that this is a reply to a consultation
    #: Koda ran — and the engagement must belong to the same Matter, which is
    #: refused in the service because a `CHECK` cannot follow a foreign key
    #: (the rule `link_document_to_record` states for the same reason).
    #:
    #: `SET_NULL`: the position is a fact in its own right and survives a round
    #: it happened to answer. Nothing in this product deletes an engagement, so
    #: this is the honest answer to a case that does not arise rather than a
    #: cascade that would silently take factual records with it.
    engagement = models.ForeignKey(
        MatterEngagement,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="external_positions",
        verbose_name="seotud kaasamine",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="recorded_external_positions",
        verbose_name="lisas",
    )

    objects = MatterExternalPositionQuerySet.as_manager()

    class Meta:
        verbose_name = "väline seisukoht"
        verbose_name_plural = "välised seisukohad"
        # Newest first, and a row with no date sorts *last* rather than first:
        # `NULLS LAST` is what stops an undated position reading as though it
        # was stated today. The ordering `MatterEngagement` keeps, for the same
        # reason (docs/adr/0084 §5).
        ordering = [models.F("stated_on").desc(nulls_last=True), "-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(stated_on_precision__in=DatePrecision.values),
                name="matters_external_position_precision_vocabulary",
            ),
            # An unknown date has no precision. `NULL` + `MONTH` would be a
            # period with nothing to qualify, and every surface reading it would
            # have to guess whether to print a period or «kuupäev teadmata» —
            # the rule `_engagement_precision` keeps in Python, stated here in
            # the database because this column is new and can afford it.
            models.CheckConstraint(
                condition=(
                    models.Q(stated_on__isnull=False)
                    | models.Q(stated_on_precision=DatePrecision.EXACT)
                ),
                name="matters_external_position_undated_is_exact",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    visibility_override__in=["", Visibility.NORMAL, Visibility.RESTRICTED]
                ),
                name="matters_external_position_visibility_vocabulary",
            ),
            models.CheckConstraint(
                condition=models.Q(provenance__in=ExternalPositionProvenance.values),
                name="matters_external_position_provenance_vocabulary",
            ),
            # **A position says whose it is.** An organisation, or — for
            # received feedback only — a `source_label` naming the collection
            # of answers it came from. Both is ordinary; neither is not.
            #
            # This *can* be a `CHECK`, unlike the source rule two constraints
            # above it, and the difference is worth stating: all three columns
            # this reads are on this row, while the source rule has to count
            # `documents_documentlink`. So the authorship rule is stated in the
            # database and the source rule is stated in the service, and neither
            # is stated in both (docs/adr/0090 §3.3).
            models.CheckConstraint(
                condition=(
                    models.Q(organisation__isnull=False)
                    | (
                        models.Q(provenance=ExternalPositionProvenance.RECEIVED)
                        & ~models.Q(source_label="")
                    )
                ),
                name="matters_external_position_author_or_label",
            ),
            # `Allikas` is a received-feedback column. A label beside a named
            # ministry would be a second name for a body the catalogue already
            # names, and a label on a `LEGACY` row would be a fact invented by a
            # writer that never had the field.
            models.CheckConstraint(
                condition=(
                    models.Q(source_label="")
                    | models.Q(provenance=ExternalPositionProvenance.RECEIVED)
                ),
                name="matters_external_position_label_is_received",
            ),
        ]
        indexes = [
            # The chronology's own read: one Matter's positions, newest first.
            models.Index(fields=["matter", "-stated_on"], name="matters_extpos_matter_date"),
            # And «what came back to us on this file» — one Matter's positions
            # split by where they came from, which is what the two panels and the
            # grouped chronology read (docs/adr/0090 §3).
            models.Index(fields=["matter", "provenance"], name="matters_extpos_matter_prov"),
            # And «what has this body said on this file», which is the question
            # a reader asks when a Matter carries several.
            models.Index(fields=["matter", "organisation"], name="matters_extpos_matter_org"),
        ]

    def __str__(self) -> str:
        return f"{self.kind_label}: {self.organisation_id or self.source_label}"[:120]

    def parent_visibility(self) -> str:
        return self.matter.visibility

    @property
    def is_received(self) -> bool:
        """Whether somebody gave this to Koda, rather than Koda finding it.

        Read from :attr:`provenance` and from nothing else. `LEGACY` is `False`
        here — a row recorded before the question was asked is not evidence that
        the answer was «discovered», and the two surfaces that care print
        :attr:`kind_label` rather than branching on this (docs/adr/0090 §3.4).
        """
        return self.provenance == ExternalPositionProvenance.RECEIVED

    @property
    def kind_label(self) -> str:
        """What the chronology calls this row — the provenance, in Estonian.

        `Meile saadetud tagasiside`, `Teiste arvamus`, or `Väline seisukoht` for
        a `LEGACY` row. The last one is deliberately the *old* headline rather
        than the enum's own «Täpsustamata»: those rows read exactly as they did
        before this column existed, because nothing about them changed and a file
        that started calling them «täpsustamata» would be announcing a gap
        somebody has to go and fill (docs/adr/0090 §3.4).
        """
        if self.provenance == ExternalPositionProvenance.LEGACY:
            return EXTERNAL_POSITION_LEGACY_HEADLINE
        return str(ExternalPositionProvenance(self.provenance).label)

    @property
    def author_label(self) -> str:
        """Whose position this is, as a name to read.

        The organisation where there is one, the `Allikas` where the answers
        have no single author, and — for neither, which the database refuses on
        a new row and which a `LEGACY` row cannot be in — the empty string. The
        chronology never prints an empty author: `external_position_milestone`
        composes the headline from :attr:`kind_label` and this, and drops the
        separator when this is blank (docs/adr/0090 §3.3).
        """
        organisation = self.organisation
        if organisation is not None:
            return organisation.name
        return self.source_label

    @property
    def revision_token(self) -> str:
        """Which version of this record a rendered correction form was filled from.

        ``updated_at``, for the reasons `entry_revision_token` and
        `MatterWebsiteOverview.revision_token` both give: `auto_now` sets it on
        every write, PostgreSQL stores it to the microsecond so two saves cannot
        share one, and having it costs no migration.
        """
        return self.updated_at.isoformat()

    @property
    def display_date(self) -> str:
        """`Seisukoha kuupäev`, written the way it was actually known.

        The only supported way to put :attr:`stated_on` on a screen. A row
        stored as `2026-10-01` + `MONTH` reads *oktoober 2026* here and must
        read that on every surface that shows it; rendering the stored value
        instead would print `01.10.2026`, which is an anchor and not a day
        anybody named (docs/adr/0079 §2, §3).

        Empty string for a row with no date. What the chronology prints in that
        case is «Kuupäev teadmata», which is a sentence about the file and
        belongs to the surface saying it, not to the model
        (`app.matters.timeline.EXTERNAL_POSITION_DATE_UNKNOWN`).
        """
        return format_at_precision(self.stated_on, self.stated_on_precision)

    @property
    def has_approximate_date(self) -> bool:
        """Whether this position is dated to a period rather than to a day.

        `False` for a row with no date at all: an unknown date is not an
        approximate one, and the two say different things to a reader.
        """
        return self.stated_on is not None and is_approximate(self.stated_on_precision)

    @property
    def link_label(self) -> str:
        """What the chronology calls the link, and never the address itself.

        The parsed host — `rahandusministeerium.ee` — because that is what tells
        a reader where a link goes, and because a raw URL as a row's own text is
        a line a reader has to parse instead of read and the one shape in which
        a look-alike address would be believed. An address this method cannot
        find a host in falls back to `Ava seisukoht`, which says what the
        control is for without claiming anything about where it points
        (docs/adr/0081 §4, docs/adr/0084 §3).

        `MatterEngagement._hostname` is the implementation, shared rather than
        copied: it is what drops the userinfo and the port, so a link carrying
        basic-auth credentials cannot put a password into a rendered label
        (red-team finding F-2).
        """
        if not self.url:
            return ""
        return MatterEngagement._hostname(self.url) or EXTERNAL_POSITION_LINK_FALLBACK


#: What the chronology calls a position recorded before `provenance` existed.
#:
#: The heading those rows have always had. Named here because the model, the
#: chronology and a test all have to agree about it, and because the alternative
#: — printing the enum's own «Täpsustamata» — would be the file announcing a
#: gap in itself that nobody can honestly close (docs/adr/0090 §3.4).
EXTERNAL_POSITION_LEGACY_HEADLINE = "Väline seisukoht"

#: What a `Väline seisukoht`'s link control says when the address has no host to
#: name. Named here because the model, the chronology and a test all have to
#: agree about it, and a sentence spelled twice is a sentence that drifts.
EXTERNAL_POSITION_LINK_FALLBACK = "Ava seisukoht"


class MatterProceduralDevelopmentQuerySet(models.QuerySet):
    def visible_to(self, user: object | None) -> MatterProceduralDevelopmentQuerySet:
        """The only supported entry point for reading procedural developments."""
        return apply_scope(self, child_visibility_q(scope_for_user(user)))


class MatterProceduralDevelopment(VisibilityInheritingModel):
    """`Menetluse areng` — one step the external procedure took.

    «Ministeerium saatis eelnõu uue versiooni», «Eelnõu läks
    Justiitsministeeriumisse», «Valitsus kiitis eelnõu heaks», «Eelnõu jõudis
    Riigikokku», «Seadus võeti vastu», «Määrus jõustus».

    It is the file's answer to *what happened to this proceeding, and when*, and
    it is the fact that made a Matter read as finished the moment Koda's opinion
    went out: the procedure does not stop when the Chamber answers, and until
    this record existed there was nowhere to say so. Lawyers were opening new
    Matters for the same proceeding (lawyer feedback 14, docs/adr/0090 §5).

    Why it is a record and not an `Entry`
    -------------------------------------
    This was an `Entry` of a new `EntryKind` for one round, on the reasoning that
    a procedural development is a dated, attributable sentence about something
    that happened — which the authored chronology already is. That reasoning is
    sound about the *shape* and wrong about three things the fact actually needs,
    and the Package D discovery is what proved it: an incoming development cannot
    be **projected truthfully** from an `Entry`.

    * **The date has to be allowed to be unknown.** `Entry.occurred_at` is
      `NOT NULL` and has been since the foundational schema — every chronology
      reader, the `-occurred_at` ordering and the timeline's pagination depend on
      it. A development learned about from a third party months later frequently
      has no day anybody could defend, and the two answers an `Entry` left were an
      invented day or no record at all. Here the date is nullable and carries its
      precision, like every other period on this product (docs/adr/0079).
    * **The lawyer's own note is a second field.** An `Entry` has one `body`, so
      «Ministeerium saatis uue versiooni» and «see tähendab, et meie tähtaeg
      nihkub» had to become one sentence — the same conflation docs/adr/0090 §4
      refuses for a `Väline seisukoht`, arriving on the other record.
    * **A projection needs a title it did not have to parse.** Package D reads
      these into one substantive history, and an `Entry` offers prose: deriving
      «what happened» from the first sentence of a `body` is exactly the guessing
      this repository refuses everywhere else.

    So: beside `MatterEngagement`, `MatterWebsiteOverview` and
    `MatterExternalPosition` in `app.matters`, with its own service functions and
    its own audit events. **No new framework.** It is a Matter child record
    written through named use cases exactly like every other structured fact on
    this page; the launcher, the lock discipline, the visibility inheritance, the
    evidence pipeline, the audit model and the chronology projection are all the
    existing ones.

    What it deliberately is not
    ---------------------------
    Not a `MatterImportantDate`: that is a milestone somebody **announced**, for a
    date still ahead, and this is a step that has already been taken. Not a
    `Submission`, which is what Koda *sent*. Not a `NextAction`: a development is
    something that happened, and a record that generated work would make every
    Matter carrying one read as owing something — the rule docs/adr/0078 §3 and
    docs/adr/0084 §1 both keep. It *may* be saved together with a next action and
    a stage change, and that is one atomic operation over three canonical
    services, not three columns on this row (`add_procedural_development`).

    **The stage it moved the file to is not copied here.** `Matter.stage` is where
    the file stands and `MATTER_STAGE_CHANGED` is the history of it moving; a copy
    on this row would be a second place for the same fact and therefore a second
    thing that can disagree. What ties the two together is the operation
    identifier both writes share (`app.audit.operations`).

    **Nothing is inferred from a `Menetluse link`.** Package B's procedural links
    are references — where a proceeding lives — and a reference is not an event.
    The association between the two is a pointer somebody sets, never a derivation
    (docs/adr/0090 §5.6).

    No deletion
    -----------
    Create and correct, like `MatterEngagement` and `MatterExternalPosition`. A
    mistaken row is corrected, because what the file recorded and who recorded it
    is part of the file.
    """

    matter = models.ForeignKey(
        Matter,
        on_delete=models.CASCADE,
        related_name="procedural_developments",
        verbose_name="teema",
    )
    #: `Mis juhtus` — one line naming the step the procedure took.
    #:
    #: Required, and the only required field: a development that does not say
    #: what happened is not a record of anything. Bounded rather than free prose,
    #: because this is the line a reader scans a year of chronology by and the
    #: line Package D will project into one substantive history — the detail goes
    #: in :attr:`note` and the paper goes in the attachments.
    title = models.CharField(max_length=500, verbose_name="sündmus")
    #: When it happened, as far as anybody knows.
    #:
    #: **Optional, and that is the whole reason this is not an `Entry`.** A
    #: development learned about from a third party months later frequently has no
    #: day anybody could defend, and «kuupäev teadmata» is a fact the file has to
    #: be able to hold. Nothing derives it: not `created_at`, not the day somebody
    #: typed it in, not the stage change saved beside it (docs/adr/0078 §2).
    occurred_on = models.DateField(null=True, blank=True, db_index=True, verbose_name="kuupäev")
    #: How exactly :attr:`occurred_on` is known — `Täpne päev`, `Kuu`, `Kvartal`
    #: or `Aasta`.
    #:
    #: The same four precisions, through the same composer, as every other period
    #: on this product. A government sitting remembered as «oktoobris» had two
    #: answers before this column — an invented day or an empty field — and both
    #: are worse than the one the person has.
    #:
    #: The stored value is the **anchor**: the first day of the period, which
    #: exists so a month has a place in a sort and is never a day anybody named.
    #: :attr:`display_date` is the only supported way to write it down
    #: (docs/adr/0079 §2, §3).
    occurred_on_precision = models.CharField(
        max_length=16,
        choices=DatePrecision.choices,
        default=DatePrecision.EXACT,
        verbose_name="kuupäeva täpsus",
    )
    #: `Juristi märkus` — what this office makes of the step.
    #:
    #: «Uus versioon ei arvesta meie ettepanekut» is a professional judgement and
    #: «Ministeerium saatis uue versiooni» is a fact about the world. Separate
    #: columns for the same reason `MatterExternalPosition.lawyer_note` is a
    #: separate column: one field carrying both is a field whose meaning depends
    #: on who wrote the sentence, and a history that reads this office's
    #: assessment as part of what the ministry did is a history that misleads
    #: (docs/adr/0090 §4, §5).
    note = models.TextField(blank=True, verbose_name="juristi märkus")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="recorded_procedural_developments",
        verbose_name="lisas",
    )

    objects = MatterProceduralDevelopmentQuerySet.as_manager()

    class Meta:
        verbose_name = "menetluse areng"
        verbose_name_plural = "menetluse arengud"
        # Newest first, and a row with no date sorts *last* rather than first:
        # `NULLS LAST` is what stops an undated development reading as though it
        # happened today. The ordering `MatterEngagement` and
        # `MatterExternalPosition` both keep, for the same reason.
        ordering = [models.F("occurred_on").desc(nulls_last=True), "-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(title=""),
                name="matters_development_title_required",
            ),
            models.CheckConstraint(
                condition=models.Q(occurred_on_precision__in=DatePrecision.values),
                name="matters_development_precision_vocabulary",
            ),
            # An unknown date has no precision. `NULL` + `MONTH` would be a period
            # with nothing to qualify, and every surface reading it would have to
            # guess whether to print a period or «kuupäev teadmata».
            models.CheckConstraint(
                condition=(
                    models.Q(occurred_on__isnull=False)
                    | models.Q(occurred_on_precision=DatePrecision.EXACT)
                ),
                name="matters_development_undated_is_exact",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    visibility_override__in=["", Visibility.NORMAL, Visibility.RESTRICTED]
                ),
                name="matters_development_visibility_vocabulary",
            ),
        ]
        indexes = [
            # The chronology's own read: one Matter's developments, newest first.
            models.Index(fields=["matter", "-occurred_on"], name="matters_devel_matter_date"),
        ]

    def __str__(self) -> str:
        return f"Menetluse areng: {self.title}"[:120]

    def parent_visibility(self) -> str:
        return self.matter.visibility

    @property
    def revision_token(self) -> str:
        """Which version of this record a rendered correction form was filled from.

        ``updated_at``, for the reasons `entry_revision_token` and
        `MatterExternalPosition.revision_token` both give: `auto_now` sets it on
        every write, PostgreSQL stores it to the microsecond so two saves cannot
        share one, and having it costs no migration.
        """
        return self.updated_at.isoformat()

    @property
    def display_date(self) -> str:
        """`Kuupäev`, written the way it was actually known.

        The only supported way to put :attr:`occurred_on` on a screen. A row
        stored as `2026-10-01` + `MONTH` reads *oktoober 2026* here and must read
        that on every surface; rendering the stored value would print
        `01.10.2026`, which is an anchor and not a day anybody named.

        Empty string for a row with no date. What the chronology prints in that
        case is «Kuupäev teadmata», which is a sentence about the file and belongs
        to the surface saying it (docs/adr/0079 §2, §3).
        """
        return format_at_precision(self.occurred_on, self.occurred_on_precision)

    @property
    def has_approximate_date(self) -> bool:
        """Whether this step is dated to a period rather than to a day.

        `False` for a row with no date at all: an unknown date is not an
        approximate one, and the two say different things to a reader.
        """
        return self.occurred_on is not None and is_approximate(self.occurred_on_precision)


class MatterPersonalNote(BaseModel):
    """`Märkmed` — one person's private scratch pad about one Matter.

    A phone number, a name to check, a reminder to read something before the
    committee sits. Today that lives on paper and in a personal OneNote page,
    and the reason it never reached the file is that everything the file offers
    is *published*: an `Entry` is dated, attributed and permanent, and nobody
    writes "ask Liina whether this is a directive requirement" into a
    professional chronology.

    So this is deliberately the opposite of every other record here.

    **Private, and privately queried.** It is scoped by user, not by
    `visible_to`: no colleague, no department head and no administrator reads it
    through the ordinary product, because there is no ordinary product surface
    that lists somebody else's notes. It is not a
    `VisibilityInheritingModel` — inheriting the Matter's visibility would make
    it *readable by whoever may read the Matter*, which is exactly wrong.

    **Not business history.** It writes no `ChangeEvent`, appears on no
    timeline, is not indexed for search, is not evidence, and is not exported.
    Autosaving a draft is not a business change and recording each keystroke's
    worth of it as one would drown the audit trail it belongs beside.

    **Not a second Matter field.** One row per person per Matter, so two lawyers
    working the same file never overwrite each other, and so the Matter's own
    columns keep meaning what they say (Teema redesign §22.4).
    """

    matter = models.ForeignKey(
        Matter,
        on_delete=models.CASCADE,
        related_name="personal_notes",
        verbose_name="teema",
    )
    #: CASCADE, unlike almost every other person reference in the schema. Those
    #: are PROTECT because they are attribution — who assigned, who sent, who
    #: closed — and business history must not lose its author. This is not
    #: attribution: it is the person's own scratch paper, and it has no meaning
    #: once they are gone.
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="matter_personal_notes",
        verbose_name="kasutaja",
    )
    body = models.TextField(blank=True, verbose_name="märkmed")

    class Meta:
        verbose_name = "isiklik märkmik"
        verbose_name_plural = "isiklikud märkmikud"
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["matter", "author"],
                name="matters_one_personal_note_per_person",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.author_id} @ {self.matter_id}"


class PersonalScratchpad(BaseModel):
    """`Märkmed` on Minu asjad — one person's own notepad, about nothing in particular.

    The sibling of :class:`MatterPersonalNote` and deliberately not the same
    thing. That one is *per Matter*: it belongs beside a file and is read while
    that file is open. This one is *per person*: «helista esmaspäeval MKM-i»,
    «küsi, kas teeme ühispöördumise», the things a lawyer writes on the corner
    of the desk pad and that belong to no file at all.

    **Privacy here is absolute, and it is enforced three times.** There is one
    row per person and it is keyed on the person, so the schema itself cannot
    express somebody else's notes. The endpoint reads and writes
    ``request.user`` and takes no subject parameter, so no URL can ask for
    another person's row. And the manager's view of a colleague's desk does not
    render the block at all — not hidden with CSS, absent from the HTML — so
    there is nothing in the response to find with a view-source
    (01-EHITUSJUHIS §3.5, §8; 03-BACKEND §2).

    Like the per-Matter note, this writes no `ChangeEvent`, appears on no
    timeline, is not indexed for search, is not evidence and is not exported.
    """

    #: OneToOne, because "my notes" is one thing. CASCADE for the same reason
    #: the per-Matter note cascades: this is not attribution and it has no
    #: meaning once the person is gone.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="scratchpad",
        verbose_name="kasutaja",
    )
    body = models.TextField(blank=True, default="", verbose_name="märkmed")

    class Meta:
        verbose_name = "isiklik märkmik"
        verbose_name_plural = "isiklikud märkmikud"
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"scratchpad @ {self.user_id}"


class MatterAssignmentNotice(BaseModel):
    """«Uus asi» — one receipt saying a colleague put this Matter on my desk.

    Deliberately not a notification system. There is no channel, no preference,
    no bell and no counter anywhere else in the product; there is one question,
    asked on Minu asjad and nowhere else: *has somebody just put a new Matter on
    my desk?* Everything this table holds exists to answer that and to stop
    answering it once the person has looked (docs/adr/0051).

    **It stores no copy of anything.** Not the title, not the owner's name, not
    a URL. Those are canonical on the Matter and on the User, and a notice that
    carried its own copy would go on saying «Sandra määras» about a Matter that
    has since been renamed and reassigned.

    **It is personal, in the same absolute sense the scratchpad is.** The
    selector reads ``recipient=user`` and the manager's view of a colleague's
    desk does not query it at all, so the block is absent from that response
    rather than hidden in it (:class:`PersonalScratchpad`,
    ``app/matters/person_work.py``).

    **Two nullable stamps rather than a deletion.** ``viewed_at`` is the read
    receipt, set only when the recipient opens the Matter *from this block*.
    ``superseded_at`` is what happens when the assignment stops being true — the
    file is handed on to somebody else, or taken off everybody's desk — because
    a notice that stayed unread through a reassignment would keep offering a
    Matter its recipient no longer owns. Neither stamp removes the row: what
    landed on somebody's desk in March is a fact about March.
    """

    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="assignment_notices",
        verbose_name="teema",
    )
    #: Who the Matter was handed to. CASCADE for the same reason the scratchpad
    #: cascades: this is one person's workflow state and it has no meaning once
    #: the person is gone. It is not attribution.
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="assignment_notices",
        verbose_name="saaja",
    )
    #: Who did the handing. May equal ``recipient``: assigning a Matter to
    #: yourself is an ordinary act here and produces an ordinary notice.
    #: SET_NULL rather than CASCADE — the receipt survives the departure of the
    #: colleague who wrote it, exactly as the audit trail does.
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assignment_notices_given",
        verbose_name="määraja",
    )
    viewed_at = models.DateTimeField(null=True, blank=True, verbose_name="vaadatud")
    superseded_at = models.DateTimeField(null=True, blank=True, verbose_name="asendatud")

    class Meta:
        verbose_name = "uue asja teavitus"
        verbose_name_plural = "uue asja teavitused"
        ordering = ["-created_at"]
        constraints = [
            # One active notice per Matter per person, and *partial* so that a
            # genuinely new assignment later — Marko, then Ireen, then Marko
            # again — is still allowed. A permanent unique constraint would make
            # legitimate reassignment impossible; this one only forbids the
            # duplicate that a repeated request could otherwise create
            # (docs/adr/0051).
            models.UniqueConstraint(
                fields=["matter", "recipient"],
                condition=models.Q(viewed_at__isnull=True, superseded_at__isnull=True),
                name="matters_one_active_assignment_notice",
            )
        ]
        indexes = [
            # The Minu asjad read, exactly: this person's active notices, newest
            # first. Partial, because every row this index will ever be asked
            # about is an active one and the table's whole history is not.
            models.Index(
                fields=["recipient", "-created_at"],
                condition=models.Q(viewed_at__isnull=True, superseded_at__isnull=True),
                name="matters_active_notice_read",
            )
        ]

    def __str__(self) -> str:
        return f"assignment notice {self.matter_id} → {self.recipient_id}"


# The pre-creation intake tables live in their own module because they obey the
# opposite rule to everything above: nothing in them is a record of anything,
# and all of it is deleted on a timer. Imported here so Django's app registry
# finds the models — splitting the file is a readability decision, not a second
# app (docs/adr/0064, the same reasoning as `app/documents/derivatives.py`).
from app.matters.staging import MatterIntakeFile, MatterIntakeSession  # noqa: E402, F401
