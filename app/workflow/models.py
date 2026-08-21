"""Code-managed procedural reference data.

There is no configurable workflow engine and no arbitrary admin create/delete
flow: stages are reference data reviewed with the lawyers and seeded through a
management command (master specification 11.2, 10).
"""

from __future__ import annotations

from datetime import date

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone

from app.core.authorization import apply as apply_scope
from app.core.authorization import child_visibility_q, scope_for_user
from app.core.enums import Visibility
from app.core.models import BaseModel, VisibilityInheritingModel
from app.workflow.dates import format_at_precision, is_approximate
from app.workflow.enums import (
    OVERDUE_KIND,
    OVERDUE_SEMANTICS,
    REVIEW_KINDS,
    ActionKind,
    ActionStatus,
    DatePrecision,
    DateSemantics,
    Disposition,
    Track,
)


class StageVocabulary(BaseModel):
    """One `Hetkeseis` value: where the external process stands."""

    key = models.SlugField(max_length=64, unique=True, verbose_name="võti")
    label_et = models.CharField(max_length=200, verbose_name="nimetus")
    help_text = models.TextField(blank=True, verbose_name="selgitus")
    is_active = models.BooleanField(default=True, verbose_name="aktiivne")
    sort_order = models.PositiveSmallIntegerField(default=100, verbose_name="järjekord")
    applicable_tracks = ArrayField(
        models.CharField(max_length=32, choices=Track.choices),
        default=list,
        blank=True,
        verbose_name="kehtib menetlusliikidele",
        help_text="Tühi loend tähendab, et etapp kehtib kõikidele menetlusliikidele.",
    )
    is_provisional = models.BooleanField(
        default=False,
        verbose_name="esialgne",
        help_text=("Märgitud seni, kuni osakonnajuht ja juristid on etapisõnastiku üle vaadanud."),
    )

    class Meta:
        verbose_name = "menetlusetapp"
        verbose_name_plural = "menetlusetapid"
        ordering = ["sort_order", "label_et"]

    def __str__(self) -> str:
        return self.label_et

    def applies_to(self, track: str) -> bool:
        return not self.applicable_tracks or track in self.applicable_tracks


class LegacyStatusMapping(BaseModel):
    """How one verbatim historical `Hetkeseis` label is interpreted, per era.

    Some legacy labels are not procedural stages at all: the workbook value
    `rohkem pole tegevusi plaanis` describes closure, not where the external
    process stands. Keeping the raw label and its interpretation in separate
    columns means the import never rewrites the source (specification 11.2).

    The same label does not necessarily mean the same thing in every year — the
    register's structure and vocabulary changed materially between 2011 and 2026
    — so a label is unique **per era**, not globally. An empty ``source_era`` is
    the generic fallback, and an exact era match takes precedence over it
    (see ``resolve_legacy_status``).
    """

    raw_label = models.CharField(
        max_length=200,
        verbose_name="algne väärtus",
        help_text="Täpselt nii, nagu see töövihikus esineb.",
    )
    stage = models.ForeignKey(
        StageVocabulary,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="legacy_labels",
        verbose_name="vastav etapp",
    )
    disposition = models.CharField(
        max_length=32,
        choices=Disposition.choices,
        blank=True,
        default="",
        verbose_name="vastav lõpetamise põhjus",
    )
    source_era = models.CharField(
        max_length=32,
        blank=True,
        default="",
        db_index=True,
        verbose_name="allika periood",
        help_text=(
            "Näiteks 2023-2024 või 2025, kui tähendus on aastati erinev. "
            "Tühi väärtus on üldine vaste, mida kasutatakse siis, kui täpsemat ei leidu."
        ),
    )
    reviewed_by = models.CharField(max_length=200, blank=True, verbose_name="üle vaadanud")
    notes = models.TextField(blank=True, verbose_name="märkused")

    class Meta:
        verbose_name = "ajaloolise seisundi vaste"
        verbose_name_plural = "ajalooliste seisundite vasted"
        ordering = ["raw_label", "source_era"]
        constraints = [
            # A label maps to a stage or to a closure reason, never to both.
            models.CheckConstraint(
                condition=~models.Q(stage__isnull=False) | models.Q(disposition=""),
                name="workflow_legacy_status_single_interpretation",
            ),
            # One interpretation per label per era; the empty era is the
            # generic fallback and is itself unique.
            models.UniqueConstraint(
                fields=["raw_label", "source_era"],
                name="workflow_legacy_status_unique_per_era",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.raw_label} ({self.source_era or 'üldine'})"

    @property
    def is_generic(self) -> bool:
        return self.source_era == ""


def resolve_legacy_status(raw_label: str, source_era: str = "") -> LegacyStatusMapping | None:
    """Interpret one historical label, preferring the era-specific meaning.

    An exact era match wins. Only if none exists does the generic mapping
    apply, so adding a 2025 meaning never silently changes how a 2014 row was
    already read.
    """
    candidates = LegacyStatusMapping.objects.filter(
        raw_label=raw_label, source_era__in={source_era, ""}
    )
    exact = None
    generic = None
    for candidate in candidates:
        if candidate.source_era == source_era and source_era != "":
            exact = candidate
        elif candidate.source_era == "":
            generic = candidate
    return exact or generic


class NextActionQuerySet(models.QuerySet):
    def visible_to(self, user: object | None) -> NextActionQuerySet:
        return apply_scope(self, child_visibility_q(scope_for_user(user)))

    def open(self) -> NextActionQuerySet:
        return self.filter(status=ActionStatus.OPEN)

    def overdue(self, today: date | None = None) -> NextActionQuerySet:
        """Actions that are genuinely late.

        Only a DO with a DEADLINE qualifies. A WAIT whose review date has passed
        is due for a look, not missed, and calling it overdue would make the
        whole list untrustworthy.
        """
        return self.open().filter(
            kind=OVERDUE_KIND,
            date_semantics=OVERDUE_SEMANTICS,
            target_date__lt=today or timezone.localdate(),
        )

    def due_for_review(self, today: date | None = None) -> NextActionQuerySet:
        return self.open().filter(
            kind__in=REVIEW_KINDS,
            target_date__isnull=False,
            target_date__lte=today or timezone.localdate(),
        )


class NextAction(VisibilityInheritingModel):
    """`Järgmiseks` — the one prominent instruction for a Matter.

    A Matter has at most one open action; replacing it supersedes the previous
    one rather than deleting it, so the record of what Koda intended and when
    survives (master specification 11.2).

    This is not a task manager. There is no assignment queue, no sub-task, no
    recurrence and no notification engine, because the department's real need is
    a single unambiguous answer to "what happens next with this file".
    """

    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="next_actions",
        verbose_name="teema",
    )
    text = models.TextField(verbose_name="järgmiseks")
    kind = models.CharField(
        max_length=16,
        choices=ActionKind.choices,
        default=ActionKind.DO,
        db_index=True,
        verbose_name="tegevuse liik",
    )
    date_semantics = models.CharField(
        max_length=32,
        choices=DateSemantics.choices,
        default=DateSemantics.DEADLINE,
        verbose_name="kuupäeva tähendus",
    )
    target_date = models.DateField(null=True, blank=True, db_index=True, verbose_name="kuupäev")
    date_precision = models.CharField(
        max_length=16,
        choices=DatePrecision.choices,
        default=DatePrecision.EXACT,
        verbose_name="kuupäeva täpsus",
    )
    source_text = models.TextField(
        blank=True,
        verbose_name="algne tekst",
        help_text="Kui kuupäev on tuletatud vabast tekstist, säilib siin algne sõnastus.",
    )

    responsible = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="next_actions",
        verbose_name="vastutaja",
    )
    status = models.CharField(
        max_length=16,
        choices=ActionStatus.choices,
        default=ActionStatus.OPEN,
        db_index=True,
        verbose_name="olek",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="created_next_actions",
    )
    ended_at = models.DateTimeField(null=True, blank=True, verbose_name="lõpetatud")
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ended_next_actions",
    )
    replaced_by = models.OneToOneField(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replaces",
        verbose_name="asendatud tegevusega",
    )

    objects = NextActionQuerySet.as_manager()

    class Meta:
        verbose_name = "järgmine tegevus"
        verbose_name_plural = "järgmised tegevused"
        ordering = ["-created_at"]
        constraints = [
            # The invariant the whole Minu töö page depends on.
            models.UniqueConstraint(
                fields=["matter"],
                condition=models.Q(status=ActionStatus.OPEN),
                name="workflow_one_open_action_per_matter",
            ),
            models.CheckConstraint(
                condition=~models.Q(text=""),
                name="workflow_next_action_text_required",
            ),
            # The rule the work queue rests on: a deadline with no date cannot
            # be met, missed or planned against. WAIT and MONITOR may be
            # dateless, because "no idea when" is an honest state.
            models.CheckConstraint(
                condition=(
                    ~models.Q(kind=ActionKind.DO, date_semantics=DateSemantics.DEADLINE)
                    | models.Q(target_date__isnull=False)
                ),
                name="workflow_deadline_requires_a_date",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    visibility_override__in=["", Visibility.NORMAL, Visibility.RESTRICTED]
                ),
                name="workflow_next_action_visibility_vocabulary",
            ),
        ]
        indexes = [
            models.Index(
                fields=["responsible", "status", "target_date"],
                name="workflow_action_queue",
            ),
            models.Index(
                fields=["status", "kind", "target_date"],
                name="workflow_action_kind_date",
            ),
        ]

    def __str__(self) -> str:
        return self.text[:80]

    def parent_visibility(self) -> str:
        return self.matter.visibility

    @property
    def is_open(self) -> bool:
        return self.status == ActionStatus.OPEN

    def is_overdue(self, today: date | None = None) -> bool:
        if not self.is_open or self.target_date is None:
            return False
        if self.kind != OVERDUE_KIND or self.date_semantics != OVERDUE_SEMANTICS:
            return False
        return self.target_date < (today or timezone.localdate())

    def is_due_for_review(self, today: date | None = None) -> bool:
        if not self.is_open or self.target_date is None:
            return False
        if self.kind not in REVIEW_KINDS:
            return False
        return self.target_date <= (today or timezone.localdate())

    @property
    def display_date(self) -> str:
        """The date rendered at the precision it was actually known to.

        An EXPECTED_AROUND date is frequently a guess about someone else's
        timetable — "some time in the autumn", "next quarter". Rendering that as
        an exact day would manufacture a certainty the source never had, so the
        stored precision decides the wording (master specification 3.5).
        """
        return format_at_precision(self.target_date, self.date_precision)

    @property
    def is_approximate(self) -> bool:
        return is_approximate(self.date_precision)

    @property
    def date_label(self) -> str:
        """How this date should be described to a reader.

        The same 14 March is a deadline, a reminder or a guess depending on the
        semantics, and the UI must never present all three identically.
        """
        if self.target_date is None:
            return ""
        labels: dict[str, str] = {
            DateSemantics.DEADLINE.value: "Tähtaeg",
            DateSemantics.REVIEW_ON.value: "Vaatan üle",
            DateSemantics.EXPECTED_AROUND.value: "Oodatav",
        }
        return labels.get(self.date_semantics, "")
