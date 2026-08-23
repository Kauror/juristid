"""The canonical Matter (`Teema`).

One model carries both current operational work and the historical register.
`record_mode` distinguishes them; provenance and data-quality metadata say how
much of an imported row has been verified. Modern fields are nullable precisely
so that archive rows never have to invent a stage, an owner or a date
(master specification 11.2, 19.4).
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models

from app.core.authorization import apply as apply_scope
from app.core.authorization import child_visibility_q, matter_visibility_q, scope_for_user
from app.core.enums import Visibility
from app.core.models import AppendOnlyModel, BaseModel, VisibilityInheritingModel
from app.matters.entry_enums import EntryKind
from app.matters.enums import (
    DataQualityTier,
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
    source_organisation = models.ForeignKey(
        "organisations.Organisation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="matters_as_source",
        verbose_name="algataja või saatja",
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

    # -- dates -------------------------------------------------------------
    received_date = models.DateField(null=True, blank=True, verbose_name="saabus")
    response_deadline = models.DateField(
        null=True, blank=True, db_index=True, verbose_name="arvamuse tähtaeg"
    )

    # -- substance ---------------------------------------------------------
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
    def is_test_data(self) -> bool:
        """Whether this record was made while developing the system.

        Child records — entries, submissions, documents, dates, victories —
        deliberately have no flag of their own. A child is test data when its
        Matter is, which is the only arrangement in which a REAL Matter cannot
        end up holding a TEST submission (Agent-C brief 20).
        """
        return self.data_class == MatterDataClass.TEST


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
        return f"{self.get_kind_display()} {self.occurred_at:%d.%m.%Y}"

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
