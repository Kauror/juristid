"""The canonical Matter (`Teema`).

One model carries both current operational work and the historical register.
`record_mode` distinguishes them; provenance and data-quality metadata say how
much of an imported row has been verified. Modern fields are nullable precisely
so that archive rows never have to invent a stage, an owner or a date
(master specification 11.2, 19.4).
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models

from app.core.authorization import apply as apply_scope
from app.core.authorization import child_visibility_q, matter_visibility_q, scope_for_user
from app.core.dates import format_estonian_date
from app.core.enums import Visibility
from app.core.models import AppendOnlyModel, BaseModel, VisibilityInheritingModel
from app.matters.entry_enums import EntryKind
from app.matters.enums import (
    DataQualityTier,
    EngagementKind,
    MatterDataClass,
    MatterOrigin,
    RecordMode,
    TagAssignmentSource,
)
from app.workflow.enums import Disposition, Track


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
    title = models.CharField(max_length=500, verbose_name="pealkiri")
    #: Optional, and that is the point. An e-mail campaign frequently has no
    #: durable address a colleague could open later; requiring one would make
    #: the commonest kind of engagement unrecordable (brief 12).
    url = models.URLField(max_length=1000, blank=True, verbose_name="link")
    note = models.TextField(blank=True, verbose_name="märkus")
    #: Neutral on purpose. One model carries a published call, a mailing and a
    #: questionnaire, so `sent_at`, `published_at` and `survey_opened_at` would
    #: each be wrong for two thirds of the rows. It means "the date this
    #: engagement is about", and it is optional because somebody recording an
    #: old consultation may genuinely not know it (brief 8).
    occurred_on = models.DateField(null=True, blank=True, db_index=True, verbose_name="kuupäev")
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
    def link_label(self) -> str:
        """A link's host, for a control that must not print a tracking URL.

        Campaign and survey links routinely run to hundreds of characters of
        query string. The host is what tells a reader where the link goes; the
        rest is machinery (brief 35).
        """
        if not self.url:
            return ""
        from urllib.parse import urlsplit

        return urlsplit(self.url).netloc or self.url[:60]

    @property
    def link_search_terms(self) -> list[str]:
        """The host, and each of its labels, for the search projection.

        The host alone is not enough. PostgreSQL tokenises
        ``survey.alchemer.example`` as one ``host`` token, so somebody typing
        the vendor's name finds nothing — which is precisely the search the
        column exists to answer. The labels are indexed beside the whole host
        so both work, and ``www`` and the public suffix are dropped because
        they match everything (Agent-F brief 47).
        """
        host = self.link_label
        if not host:
            return []
        labels = [part for part in host.split(".") if part and part != "www"]
        return [host, *labels[:-1]] if len(labels) > 1 else [host]


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
