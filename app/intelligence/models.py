"""Structured Matter facts: important dates, commencements and work victories.

Three things the department currently keeps as department-wide OneNote lists,
maintained by hand and therefore always slightly wrong. Here each fact is
recorded **once, on the Matter it belongs to**, and every department-wide view
is generated from those records. Nobody maintains a second list, so nobody can
forget to update one (Stage-2G brief, section 0).

Why these are models and not tags
---------------------------------
``taxonomy.Tag`` is thematic classification: it says what a Matter is about. A
commencement date is not a theme, a work victory is a reviewed judgement with an
approver and a timestamp, and a watched milestone is a period with a precision.
Each needs dates, status transitions, provenance and its own audit trail. The UI
may render a badge; the record underneath is structured (Stage-2G brief 2).

Dates
-----
``date_value`` is the **first day** of the period the record stands for, and
``period_end`` its last. Both are derived by ``app.workflow.dates`` from what a
person chose, and ``date_precision`` says which of them may be shown: a QUARTER
record renders as *II kvartal 2026* and never as ``01.04.2026``. Storing the end
as well as the start is what lets "still ahead of us" be asked in SQL without
pretending II poolaasta 2027 has passed on 2 July (master specification 3.5).

Visibility
----------
All three inherit their Matter's visibility through ``VisibilityInheritingModel``
and the central ``child_visibility_q``. Nothing here stores a copy of the
parent's visibility, for the reason ADR 0005 records. The per-record override
exists because the base class provides it and the authorization chokepoint
already understands it; no UI exposes it, because no need for a fact more
restricted than its Matter has been established.
"""

from __future__ import annotations

from datetime import date

from django.conf import settings
from django.db import models
from django.utils import timezone

from app.core.authorization import apply as apply_scope
from app.core.authorization import child_visibility_q, scope_for_user
from app.core.enums import Visibility
from app.core.models import VisibilityInheritingModel
from app.intelligence.enums import EffectiveDateKind, FactStatus, WorkVictoryStatus
from app.workflow.dates import format_at_precision, is_approximate, period_bounds
from app.workflow.enums import DatePrecision

#: Reused by every constraint that guards a visibility override column.
VISIBILITY_OVERRIDE_VALUES = ["", Visibility.NORMAL, Visibility.RESTRICTED]


class MatterFactQuerySet(models.QuerySet):
    """Shared reads. Every one of the three tables is a Matter child."""

    def visible_to(self, user: object | None) -> MatterFactQuerySet:
        """The only supported entry point for reading these records.

        Authorization is applied here, before any grouping, counting or
        merging, so a restricted Matter cannot leak through a heading, a year
        option or a total (Stage-2G brief 31).
        """
        return apply_scope(self, child_visibility_q(scope_for_user(user)))

    def active(self) -> MatterFactQuerySet:
        return self.filter(status=FactStatus.ACTIVE)


class MatterFact(VisibilityInheritingModel):
    """What the three structured facts have in common.

    Deliberately not a single "everything event" table. These are different
    business facts with different vocabularies, different validation and
    different review rules; folding them together to save two tables would cost
    exactly the meaning the department needs (Stage-2G brief 4).

    ``matter`` and ``status`` are declared on each subclass, because their
    related names and their vocabularies differ.
    """

    note = models.TextField(blank=True, verbose_name="märkus")

    # -- provenance, for the migration that has not happened yet -----------
    #
    # The department's real lists live in OneNote today. A later reviewed
    # migration will create these records from the archive, and it must be able
    # to say which page a row came from and what the page actually said. The
    # seam is two nullable columns rather than a provenance framework, because
    # `LegacySourcePage` is already the archive's own model (Stage-2G brief 42).
    legacy_source_page = models.ForeignKey(
        "legacy_import.LegacySourcePage",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="ajalooline lähteleht",
    )
    source_text = models.TextField(
        blank=True,
        verbose_name="algne sõnastus",
        help_text="Imporditud kirje puhul säilib siin allika täpne tekst.",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="lisas",
    )

    class Meta:
        abstract = True


class MatterImportantDate(MatterFact):
    """`Oluline tähtaeg` — a milestone the department is watching.

    Not ``Matter.response_deadline`` and not ``NextAction.target_date``.
    ``Järgmiseks`` answers *what does Koda do next*; this answers *what is
    expected to happen, by whom else, and roughly when*: a consultation round in
    the first quarter, a transposition deadline in a half-year, publication of a
    draft nobody has seen yet. The two may share a date and remain different
    facts (Stage-2G brief 6).
    """

    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="important_dates",
        verbose_name="teema",
    )
    title = models.TextField(verbose_name="tähtaeg")
    date_value = models.DateField(db_index=True, verbose_name="kuupäev või periood")
    period_end = models.DateField(verbose_name="perioodi lõpp")
    date_precision = models.CharField(
        max_length=16,
        choices=DatePrecision.choices,
        default=DatePrecision.EXACT,
        verbose_name="täpsus",
    )
    status = models.CharField(
        max_length=16,
        choices=FactStatus.choices,
        default=FactStatus.ACTIVE,
        db_index=True,
        verbose_name="olek",
    )
    replaced_by = models.OneToOneField(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replaces",
        verbose_name="asendatud kirjega",
    )

    objects = MatterFactQuerySet.as_manager()

    class Meta:
        verbose_name = "oluline tähtaeg"
        verbose_name_plural = "olulised tähtajad"
        # The department reads this chronologically. `period_end` breaks the tie
        # between periods starting on the same day: 01.07.2026 comes before
        # III kvartal 2026, which comes before II poolaasta 2026. A wider period
        # that starts earlier still sorts earlier — the year 2026 leads, because
        # it begins in January (Stage-2G brief 10).
        ordering = ["date_value", "period_end", "id"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(title=""),
                name="intelligence_important_date_title_required",
            ),
            models.CheckConstraint(
                condition=models.Q(period_end__gte=models.F("date_value")),
                name="intelligence_important_date_period_ordered",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=FactStatus.values),
                name="intelligence_important_date_status_vocabulary",
            ),
            models.CheckConstraint(
                condition=models.Q(date_precision__in=DatePrecision.values),
                name="intelligence_important_date_precision_vocabulary",
            ),
            models.CheckConstraint(
                condition=models.Q(visibility_override__in=VISIBILITY_OVERRIDE_VALUES),
                name="intelligence_important_date_visibility_vocabulary",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "date_value"], name="intel_important_status_date"),
            models.Index(fields=["matter", "status"], name="intel_important_matter_status"),
            models.Index(fields=["status", "period_end"], name="intel_important_status_end"),
        ]

    def __str__(self) -> str:
        return f"{self.display_date} {self.title}"[:120]

    def parent_visibility(self) -> str:
        return self.matter.visibility

    @property
    def display_date(self) -> str:
        return format_at_precision(self.date_value, self.date_precision)

    @property
    def is_approximate(self) -> bool:
        return is_approximate(self.date_precision)

    @property
    def is_cancelled(self) -> bool:
        return self.status == FactStatus.CANCELLED

    def has_passed(self, today: date | None = None) -> bool:
        """A period is past only once its **last** day is behind us."""
        return self.period_end < (today or timezone.localdate())

    def recomputed_bounds(self) -> tuple[date, date]:
        """What ``date_value``/``period_end`` should be for this precision."""
        return period_bounds(self.date_value, self.date_precision)


class MatterEffectiveDate(MatterFact):
    """`Jõustumine` — when this Matter's act, or part of it, takes effect.

    Several per Matter, and that is the point rather than an allowance. One law
    routinely commences in stages: the main body on one date, particular
    provisions eighteen months later, one register abolished on a third. The
    department's list holds exactly that, and representing it as several Matters
    would fracture one file into three (Stage-2G brief 11).
    """

    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="effective_dates",
        verbose_name="teema",
    )
    kind = models.CharField(
        max_length=16,
        choices=EffectiveDateKind.choices,
        default=EffectiveDateKind.KNOWN_DATE,
        db_index=True,
        verbose_name="jõustumise liik",
    )
    date_value = models.DateField(null=True, blank=True, db_index=True, verbose_name="jõustub")
    period_end = models.DateField(null=True, blank=True, verbose_name="perioodi lõpp")
    date_precision = models.CharField(
        max_length=16,
        choices=DatePrecision.choices,
        default=DatePrecision.EXACT,
        verbose_name="täpsus",
    )
    description = models.TextField(
        blank=True,
        verbose_name="mis jõustub",
        help_text="Näiteks põhiosa, osad sätted või konkreetne muudatus.",
    )
    source_url = models.URLField(
        max_length=1000,
        blank=True,
        verbose_name="ametlik allikas",
        help_text="Näiteks Riigi Teataja viide. Käsitsi sisestatud; süsteem seda ei kontrolli.",
    )
    status = models.CharField(
        max_length=16,
        choices=FactStatus.choices,
        default=FactStatus.ACTIVE,
        db_index=True,
        verbose_name="olek",
    )

    objects = MatterFactQuerySet.as_manager()

    class Meta:
        verbose_name = "jõustumine"
        verbose_name_plural = "jõustumised"
        ordering = ["date_value", "period_end", "id"]
        constraints = [
            # The rule the whole model exists for: only a known date carries a
            # date. "Jõustub üldises korras" and "kuupäev täpsustamisel" are
            # statements about what is known, and a placeholder day stored
            # against either would be indistinguishable from a real one on
            # every page that reads this table (Stage-2G brief 12, 14).
            models.CheckConstraint(
                condition=(
                    models.Q(kind=EffectiveDateKind.KNOWN_DATE, date_value__isnull=False)
                    | (
                        ~models.Q(kind=EffectiveDateKind.KNOWN_DATE)
                        & models.Q(date_value__isnull=True)
                    )
                ),
                name="intelligence_effective_date_matches_kind",
            ),
            # Start and end are set together or not at all.
            models.CheckConstraint(
                condition=(
                    models.Q(date_value__isnull=True, period_end__isnull=True)
                    | models.Q(date_value__isnull=False, period_end__isnull=False)
                ),
                name="intelligence_effective_date_bounds_together",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(period_end__isnull=True)
                    | models.Q(period_end__gte=models.F("date_value"))
                ),
                name="intelligence_effective_date_period_ordered",
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=EffectiveDateKind.values),
                name="intelligence_effective_date_kind_vocabulary",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=FactStatus.values),
                name="intelligence_effective_date_status_vocabulary",
            ),
            models.CheckConstraint(
                condition=models.Q(date_precision__in=DatePrecision.values),
                name="intelligence_effective_date_precision_vocabulary",
            ),
            models.CheckConstraint(
                condition=models.Q(visibility_override__in=VISIBILITY_OVERRIDE_VALUES),
                name="intelligence_effective_date_visibility_vocabulary",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "kind", "date_value"], name="intel_effective_kind_date"),
            models.Index(fields=["matter", "status"], name="intel_effective_matter_status"),
            models.Index(fields=["status", "period_end"], name="intel_effective_status_end"),
        ]

    def __str__(self) -> str:
        return f"{self.display_when} {self.description}"[:120]

    def parent_visibility(self) -> str:
        return self.matter.visibility

    @property
    def has_known_date(self) -> bool:
        return self.kind == EffectiveDateKind.KNOWN_DATE

    @property
    def display_date(self) -> str:
        return format_at_precision(self.date_value, self.date_precision)

    @property
    def display_when(self) -> str:
        """How the commencement reads when there is no date to print.

        An unknown date is an honest state, not a missing field, so it gets
        wording of its own rather than an em dash (Stage-2G brief 15).
        """
        if self.has_known_date:
            return self.display_date
        return self.get_kind_display()

    @property
    def is_approximate(self) -> bool:
        return self.date_value is not None and is_approximate(self.date_precision)

    @property
    def is_cancelled(self) -> bool:
        return self.status == FactStatus.CANCELLED

    def has_passed(self, today: date | None = None) -> bool:
        if self.period_end is None:
            return False
        return self.period_end < (today or timezone.localdate())


class MatterWorkVictory(MatterFact):
    """`Töövõit` — a claimed advocacy win, and where it stands in review.

    A pragmatic intermediate model for what the department does today: somebody
    notices that a Koda proposal was taken up, records it as a candidate, and a
    person later decides whether it is a work victory. It is deliberately *not*
    the specification's eventual Proposal → Outcome → Attribution architecture,
    and it does not claim causation: a confirmed row means a human judged this a
    Chamber win, not that the system measured one (specification 3.5, 6.6;
    Stage-2G brief 19, 41).

    When the richer model arrives it can reference or migrate these rows — the
    Matter, the period, the wording, the approver and the timestamp are all
    here — rather than throwing them away.
    """

    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="work_victories",
        verbose_name="teema",
    )
    status = models.CharField(
        max_length=16,
        choices=WorkVictoryStatus.choices,
        default=WorkVictoryStatus.CANDIDATE,
        db_index=True,
        verbose_name="olek",
    )
    title = models.TextField(verbose_name="töövõit")
    detail = models.TextField(blank=True, verbose_name="selgitus")

    # The business period, entered explicitly. Never `created_at`, never the
    # Matter's reporting year and never a commencement date: those are three
    # other facts, and quietly reusing one of them would put a 2019 win in the
    # year somebody happened to type it up (Stage-2G brief 22).
    period_date = models.DateField(null=True, blank=True, db_index=True, verbose_name="periood")
    period_end = models.DateField(null=True, blank=True, verbose_name="perioodi lõpp")
    date_precision = models.CharField(
        max_length=16,
        choices=DatePrecision.choices,
        default=DatePrecision.YEAR,
        verbose_name="täpsus",
    )
    source_url = models.URLField(max_length=1000, blank=True, verbose_name="viide")

    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="confirmed_work_victories",
        verbose_name="kinnitas",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True, verbose_name="kinnitatud")
    status_changed_at = models.DateTimeField(default=timezone.now, verbose_name="oleku muutus")

    objects = MatterFactQuerySet.as_manager()

    class Meta:
        verbose_name = "töövõit"
        verbose_name_plural = "töövõidud"
        # Newest period first, and the ones with no period last rather than
        # first — an unknown period is not "the beginning of time".
        ordering = [models.F("period_date").desc(nulls_last=True), "-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(title=""),
                name="intelligence_work_victory_title_required",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(period_date__isnull=True, period_end__isnull=True)
                    | models.Q(period_date__isnull=False, period_end__isnull=False)
                ),
                name="intelligence_work_victory_bounds_together",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(period_end__isnull=True)
                    | models.Q(period_end__gte=models.F("period_date"))
                ),
                name="intelligence_work_victory_period_ordered",
            ),
            # A confirmed victory records when it was confirmed. `confirmed_by`
            # is not required at the database level so that a future reviewed
            # import can carry a decision whose author is not a user of this
            # system; the service always sets it for a person.
            models.CheckConstraint(
                condition=(
                    ~models.Q(status=WorkVictoryStatus.CONFIRMED)
                    | models.Q(confirmed_at__isnull=False)
                ),
                name="intelligence_work_victory_confirmed_has_timestamp",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=WorkVictoryStatus.values),
                name="intelligence_work_victory_status_vocabulary",
            ),
            models.CheckConstraint(
                condition=models.Q(date_precision__in=DatePrecision.values),
                name="intelligence_work_victory_precision_vocabulary",
            ),
            models.CheckConstraint(
                condition=models.Q(visibility_override__in=VISIBILITY_OVERRIDE_VALUES),
                name="intelligence_work_victory_visibility_vocabulary",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "period_date"], name="intel_victory_status_period"),
            models.Index(fields=["matter", "status"], name="intel_victory_matter_status"),
        ]

    def __str__(self) -> str:
        return f"{self.get_status_display()}: {self.title}"[:120]

    def parent_visibility(self) -> str:
        return self.matter.visibility

    @property
    def display_period(self) -> str:
        return format_at_precision(self.period_date, self.date_precision)

    @property
    def has_period(self) -> bool:
        return self.period_date is not None

    @property
    def is_candidate(self) -> bool:
        return self.status == WorkVictoryStatus.CANDIDATE

    @property
    def is_confirmed(self) -> bool:
        return self.status == WorkVictoryStatus.CONFIRMED
