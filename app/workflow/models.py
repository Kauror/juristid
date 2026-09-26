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
from app.workflow.lateness import days_past_period, is_past_period, overdue_date_q


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

        The date half is :func:`~app.workflow.lateness.overdue_date_q`, so an
        approximate plan goes late only once its whole period has ended — and
        this queryset returns exactly the rows ``is_overdue`` below says are
        late, rather than a wider set the page then has to disagree with.
        """
        return self.open().filter(
            overdue_date_q(today or timezone.localdate()),
            kind=OVERDUE_KIND,
            date_semantics=OVERDUE_SEMANTICS,
        )

    def due_for_review(self, today: date | None = None) -> NextActionQuerySet:
        return self.open().filter(
            kind__in=REVIEW_KINDS,
            target_date__isnull=False,
            target_date__lte=today or timezone.localdate(),
        )


#: What a step with no recorded day prints where its date would go.
#:
#: A `NextAction` may carry no `target_date` at all since docs/adr/0106 — the
#: lawyer knows what happens next before knowing when — and every surface that
#: shows one step has to be able to say so in words. The same construction as
#: `Etapp määramata`, `Vastutaja määramata` and `Hetkeseis määramata`.
#:
#: **Not «Tähtaeg määramata».** This product already has two things called a
#: tähtaeg — `Arvamuse tähtaeg` and `Oluline tähtaeg`, both owed to somebody
#: outside — and a day a lawyer picks for their own step is neither, which is
#: the distinction `date_label` keeps by printing «Plaanis» (docs/adr/0054).
NO_DATE_LABEL = "Kuupäev määramata"


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
            # **There is deliberately no «a DEADLINE needs a date» constraint.**
            #
            # `workflow_deadline_requires_a_date` stood here until docs/adr/0106.
            # It read the absence of a date as an incomplete record; what it
            # actually refused was a complete one. «Vaatan ministeeriumi vastuse
            # üle» is a whole instruction, and the day it happens is a second
            # fact the lawyer frequently does not have yet — so the rule made the
            # form either lose the sentence or invent a day, and an invented day
            # is a false statement the work queue then reports on.
            #
            # `text` is still required, because an action with no text is not a
            # record of anything. A NULL `target_date` means «no deadline has
            # been recorded yet» and never today, approximate, waiting or
            # overdue: every read of it guards the None, and `overdue_date_q`
            # excludes it in SQL.
            models.CheckConstraint(
                condition=models.Q(
                    visibility_override__in=["", Visibility.NORMAL, Visibility.RESTRICTED]
                ),
                name="workflow_next_action_visibility_vocabulary",
            ),
            # **The four vocabularies, and the one rule relating two columns**
            # (ENG-043). Every sibling precision column has had its vocabulary
            # `CHECK` since Stage 2G (`matters_engagement_occurred_precision_
            # vocabulary` and the rest); this table predates the pattern and was
            # never retrofitted, so `'BOGUS'` was storable and printed as a day.
            # The services refuse all of these first, with a sentence
            # (`set_next_action`, `acknowledge_review`); these are the backstop
            # for a writer that goes around them.
            models.CheckConstraint(
                condition=models.Q(kind__in=ActionKind.values),
                name="workflow_next_action_kind_vocabulary",
            ),
            models.CheckConstraint(
                condition=models.Q(date_semantics__in=DateSemantics.values),
                name="workflow_next_action_date_semantics_vocabulary",
            ),
            models.CheckConstraint(
                condition=models.Q(date_precision__in=DatePrecision.values),
                name="workflow_next_action_precision_vocabulary",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=ActionStatus.values),
                name="workflow_next_action_status_vocabulary",
            ),
            # **An undated step is `EXACT`**, and only that. docs/adr/0106's
            # `target_date IS NULL` stays legal — a step whose day nobody knows
            # yet — and a period with nothing to qualify does not.
            models.CheckConstraint(
                condition=models.Q(target_date__isnull=False)
                | models.Q(date_precision=DatePrecision.EXACT),
                name="workflow_next_action_undated_is_exact",
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
        """Whether this step's own period has ended without it being done.

        An approximate date is a period, and a period is missed only once all
        of it is behind us: *september 2026* anchors on 1 September so that it
        sorts, and reading that anchor as the commitment called a lawyer late on
        the second day of the month they were given (ADR 0079).
        """
        if not self.is_open or self.target_date is None:
            return False
        if self.kind != OVERDUE_KIND or self.date_semantics != OVERDUE_SEMANTICS:
            return False
        return is_past_period(self.target_date, self.date_precision, today or timezone.localdate())

    def is_due_for_review(self, today: date | None = None) -> bool:
        if not self.is_open or self.target_date is None:
            return False
        if self.kind not in REVIEW_KINDS:
            return False
        return self.target_date <= (today or timezone.localdate())

    @property
    def is_review_kind(self) -> bool:
        """Whether this step's date is a review date rather than a commitment.

        Read by the Järgmiseks row, which calls the same control «Vaatasin üle»
        here and «Lükka edasi» on a DO: moving a review date is doing the work
        of checking, and moving a deadline is changing a promise
        (master specification 18.8).
        """
        return self.kind in REVIEW_KINDS

    @property
    def days_late(self) -> int:
        """How many days past a missed deadline this is. Never negative.

        Only for something genuinely overdue: a review date that has come round
        is not late, and a number of days beside it would read as a tally of
        failure for waiting on a ministry (master specification 18.8).

        Counted from the **last** day of the stored period, for the same reason
        `is_overdue` reads it: a September plan is one day late on 1 October,
        and «30 p üle» would be a number nobody could account for.
        """
        today = timezone.localdate()
        if not self.is_overdue(today) or self.target_date is None:
            return 0
        return days_past_period(self.target_date, self.date_precision, today)

    @property
    def date_display(self) -> str:
        """The date, or the words that say there is not one yet.

        `display_date` answers `""` on a `None`, which is right for a caller
        deciding whether to draw a date cell at all and wrong for the two
        surfaces that have to draw *something*: `PRAEGUNE TEGEVUS` and the
        portfolio row each show one open step, and a blank where the day goes
        reads as a rendering fault rather than as a fact about the record
        (docs/adr/0106).

        :data:`NO_DATE_LABEL` rather than a dash or `None`: «Kuupäev määramata»
        is the same construction `Etapp määramata`, `Vastutaja määramata` and
        `Hetkeseis määramata` already use, so a reader meets one pattern for
        «this is not recorded» across the product.
        """
        return self.display_date or NO_DATE_LABEL

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

        The same 14 March is a plan, a reminder or a guess depending on the
        semantics, and the UI must never present all three identically.

        **A `DEADLINE` reads as «Plaanis», not «Tähtaeg».** The stored value is
        untouched and still means what it meant — the day the step is due, and
        the only combination that can go overdue — but the word a lawyer reads
        is a product decision, and this product already has two things called a
        tähtaeg: `Arvamuse tähtaeg`, which Koda owes an outside body, and
        `Oluline tähtaeg`, an externally meaningful milestone somebody is
        watching. A date a lawyer chose for their own next step is neither.
        Printing all three with one word made the two that carry an outside
        obligation indistinguishable from the one that does not
        (docs/adr/0054 §Amendment).
        """
        if self.target_date is None:
            return ""
        labels: dict[str, str] = {
            DateSemantics.DEADLINE.value: "Plaanis",
            DateSemantics.REVIEW_ON.value: "Vaatan üle",
            DateSemantics.EXPECTED_AROUND.value: "Oodatav",
        }
        return labels.get(self.date_semantics, "")
