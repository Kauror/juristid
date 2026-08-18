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
from app.core.authorization import matter_visibility_q, scope_for_user
from app.core.enums import Visibility
from app.core.models import BaseModel
from app.matters.enums import DataQualityTier, MatterOrigin, RecordMode, TagAssignmentSource
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
