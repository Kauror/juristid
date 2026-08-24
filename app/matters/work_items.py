"""One read model for dated work, shared by Minu töö and Ülevaade.

Why this module exists
----------------------

Two questions were being answered by two different pieces of arithmetic. Minu
töö asked "what do I have to do", Ülevaade asked "where is the department
losing time", and each wrote its own idea of *overdue*, of *this week* and of
who a piece of work belongs to. Two similar definitions in two files is how two
screens start disagreeing about the same Matter — and the person who notices
first is the department head, who is looking at both.

So there is one definition here, and both pages read it.

What a work item is
-------------------

A :class:`WorkItem` is a **rendered answer**, not a stored row. Nothing here
creates a table, and the two sources keep their separate domain objects:

* an open :class:`~app.workflow.models.NextAction` — what Koda does next;
* an active :class:`~app.intelligence.models.MatterImportantDate` — a milestone
  the department watches.

They are unified only in the read layer, and only far enough to be sorted into
one chronological list. Everything that distinguishes them survives the trip:
the mode chip, the meaning of the date, and what may be done to it.

Three rules run through the whole module.

**Only a DO with a DEADLINE can be late.** A WAIT whose review date has passed
is ripe for a look, never missed. Describing an ordinary dependency on a
ministry as a failure is what makes a work queue stop being believed
(master specification 18.8). An ``Oluline tähtaeg`` is independently a real
deadline and may therefore be genuinely overdue.

**The date says where, the mode says what.** A ministry's answer expected on
Thursday and an opinion due on Thursday are both Thursday's problem, so they
share one timeline. What they are not is the same obligation, which is why every
row states its meaning in words beside the date.

**Authorization before arithmetic.** Every queryset starts from
``visible_to(user)``. A restricted Matter the reader may not see contributes
nothing to a count, a band or a row — so nothing downstream has to remember to
hide it, and no template re-implements a security check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.db.models import Q, QuerySet
from django.urls import reverse
from django.utils import timezone

from app.intelligence.enums import FactStatus
from app.intelligence.models import MatterImportantDate
from app.matters.enums import RecordMode
from app.matters.models import Matter
from app.workflow.dates import format_at_precision, period_bounds
from app.workflow.enums import (
    REVIEW_KINDS,
    ActionKind,
    ActionStatus,
    DatePrecision,
    DateSemantics,
)
from app.workflow.models import NextAction

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

SOURCE_NEXT_ACTION = "NEXT_ACTION"
SOURCE_IMPORTANT_DEADLINE = "IMPORTANT_DEADLINE"

#: What the date on a row means, in the words the department agreed.
#:
#: ``OODATAV AEG`` rather than the stored enum's *Oodatav umbes*: the label a
#: lawyer reads is a product decision and the column value is a storage one, and
#: this is the seam between them. Nothing here renames anything stored.
MEANING_DEADLINE = "TÄHTAEG"
MEANING_EXPECTED = "OODATAV AEG"
MEANING_REVIEW = "VAATAN ÜLE"
MEANING_IMPORTANT = "OLULINE TÄHTAEG"

_SEMANTICS_MEANING: dict[str, str] = {
    DateSemantics.DEADLINE.value: MEANING_DEADLINE,
    DateSemantics.EXPECTED_AROUND.value: MEANING_EXPECTED,
    DateSemantics.REVIEW_ON.value: MEANING_REVIEW,
}

#: The bands of the timeline, in reading order.
BAND_OVERDUE = "ule_tahtaja"
BAND_RIPE = "ulevaatamiseks_kups"
BAND_TODAY = "tana"
BAND_WEEK = "sel_nadalal"
BAND_LATER = "hiljem"

BAND_LABELS: dict[str, str] = {
    BAND_OVERDUE: "Üle tähtaja",
    BAND_RIPE: "Ülevaatamiseks küps",
    BAND_TODAY: "Täna",
    BAND_WEEK: "Sel nädalal",
    BAND_LATER: "Hiljem",
}

BAND_ORDER: tuple[str, ...] = (BAND_OVERDUE, BAND_RIPE, BAND_TODAY, BAND_WEEK, BAND_LATER)

#: How many rows a page may render before it stops being read. The count above
#: each list is the honest total either way.
BAND_LIMIT = 60


# ---------------------------------------------------------------------------
# The item
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkItem:
    """One dated obligation, ready to render.

    ``when`` is the anchor the list sorts on; ``display_date`` is how it reads
    at the precision it was actually recorded to. Those differ on purpose: a
    MONTH-precision expectation anchored on 1 September sorts with the first of
    the month and prints as *september 2026*, because printing ``01.09.2026``
    would manufacture a day nobody named (master specification 3.5).
    """

    source_type: str
    object_id: Any
    matter: Matter
    responsible: Any | None
    #: ``DO`` / ``WAIT`` / ``MONITOR``, or "" for an important deadline, which
    #: is not a NextAction and must never be dressed as one.
    action_kind: str
    action_kind_label: str
    date_semantics: str
    when: date | None
    period_end: date | None
    display_date: str
    meaning: str
    text: str
    is_overdue: bool
    is_review_ripe: bool
    #: The day this item was read against. Carried on the item rather than
    #: passed to each accessor, because a Django template cannot hand an
    #: argument to a property — and a row that had to be told what day it is
    #: would end up being told twice, differently.
    today: date

    @property
    def is_action(self) -> bool:
        return self.source_type == SOURCE_NEXT_ACTION

    @property
    def matter_url(self) -> str:
        return reverse("matters:matter_detail", kwargs={"pk": self.matter_id})

    @property
    def matter_id(self) -> Any:
        return self.matter.pk

    @property
    def reference(self) -> str:
        return self.matter.display_reference

    @property
    def stage_label(self) -> str:
        stage = self.matter.stage
        return stage.label_et if stage is not None else ""

    @property
    def is_restricted(self) -> bool:
        return self.matter.is_restricted

    @property
    def responsible_name(self) -> str:
        return self.responsible.get_short_name() if self.responsible is not None else "vastutajata"

    @property
    def days_late(self) -> int:
        """How many days past its last day this is. Never negative."""
        end = self.period_end or self.when
        if end is None or end >= self.today:
            return 0
        return (self.today - end).days

    @property
    def short_date(self) -> str:
        """The value the date cell prints — the honest one, not always a day.

        ``10 p üle`` for something late, ``täna`` for today, ``26.08`` for an
        exact date this year, and the stored period verbatim for anything
        recorded to a month or a quarter.
        """
        if self.when is None:
            return "—"
        late = self.days_late
        if late:
            return f"{late} p üle"
        if self.when == self.today:
            return "täna"
        if self.display_date and self._is_approximate:
            return self.display_date
        return f"{self.when.day:02d}.{self.when.month:02d}"

    @property
    def _is_approximate(self) -> bool:
        return "." not in self.display_date

    @property
    def meaning_line(self) -> str:
        """The meaning, carrying the original date when the value replaced it.

        ``TÄHTAEG 14.08`` rather than a bare ``TÄHTAEG``, because the cell above
        it is showing *10 p üle* and the reader still needs the day it was.
        """
        if self.when is not None and self.days_late:
            return f"{self.meaning} {self.display_date}"
        return self.meaning


@dataclass(frozen=True)
class WorkBand:
    """One band of the timeline. Rendered only when it holds something."""

    key: str
    label: str
    items: list[WorkItem]

    @property
    def count(self) -> int:
        return len(self.items)


# ---------------------------------------------------------------------------
# Building items
# ---------------------------------------------------------------------------


def open_matters(user: Any) -> QuerySet[Matter]:
    """Open FULL Matters the reader may see.

    ARCHIVE rows never reach a work surface: a decade of imported register rows
    is historical evidence, not a queue anybody can act on.
    """
    return Matter.objects.visible_to(user).filter(is_open=True, record_mode=RecordMode.FULL)


def action_item(action: NextAction, today: date) -> WorkItem:
    anchor = action.target_date
    end = anchor
    if anchor is not None and action.date_precision != DatePrecision.EXACT:
        # A month or a quarter is behind us only once its *last* day is, so the
        # stored precision decides where the item stops being current.
        try:
            _, end = period_bounds(anchor, action.date_precision)
        except Exception:  # pragma: no cover - a stored precision the parser refuses
            end = anchor
    overdue = action.is_overdue(today)
    ripe = (
        action.kind in REVIEW_KINDS
        and action.target_date is not None
        and end is not None
        and end < today
    )
    return WorkItem(
        source_type=SOURCE_NEXT_ACTION,
        object_id=action.pk,
        matter=action.matter,
        responsible=action.responsible,
        action_kind=action.kind,
        action_kind_label=action.get_kind_display(),
        date_semantics=action.date_semantics,
        when=action.target_date,
        period_end=end,
        display_date=action.display_date,
        meaning=_SEMANTICS_MEANING.get(action.date_semantics, MEANING_DEADLINE),
        text=action.text,
        is_overdue=overdue,
        is_review_ripe=ripe,
        today=today,
    )


def _deadline_item(record: MatterImportantDate, today: date) -> WorkItem:
    """An ``Oluline tähtaeg``, whose responsible person is the Matter's owner.

    ``ImportantDeadline`` carries no responsible column of its own, and this
    round does not add one. Reading the Matter's current owner is what makes the
    read model follow a reassignment without anybody editing the deadline: move
    the Matter and the milestone moves with it, which is the behaviour a
    department actually has (§4.2).
    """
    return WorkItem(
        source_type=SOURCE_IMPORTANT_DEADLINE,
        object_id=record.pk,
        matter=record.matter,
        responsible=record.matter.owner,
        action_kind="",
        action_kind_label="",
        date_semantics=DateSemantics.DEADLINE.value,
        when=record.date_value,
        period_end=record.period_end,
        display_date=record.display_date
        or format_at_precision(record.date_value, record.date_precision),
        meaning=MEANING_IMPORTANT,
        text=record.title,
        # A milestone is a real commitment, so its last day passing is genuine
        # lateness — unlike a review date, which is only a reminder.
        is_overdue=record.period_end < today,
        is_review_ripe=False,
        today=today,
    )


def dated_actions(user: Any, *, responsible: Any = None) -> QuerySet[NextAction]:
    """Open actions with a date, scoped to the reader and optionally to a person."""
    queryset = (
        NextAction.objects.visible_to(user)
        .filter(
            status=ActionStatus.OPEN,
            target_date__isnull=False,
            matter__is_open=True,
            matter__record_mode=RecordMode.FULL,
        )
        .select_related("matter", "matter__stage", "matter__owner", "responsible")
    )
    if responsible is not None:
        queryset = queryset.filter(responsible=responsible)
    return queryset


def undated_actions(user: Any, *, responsible: Any = None) -> QuerySet[NextAction]:
    queryset = (
        NextAction.objects.visible_to(user)
        .filter(
            status=ActionStatus.OPEN,
            target_date__isnull=True,
            matter__is_open=True,
            matter__record_mode=RecordMode.FULL,
        )
        .select_related("matter", "matter__stage", "matter__owner", "responsible")
    )
    if responsible is not None:
        queryset = queryset.filter(responsible=responsible)
    return queryset


def important_deadlines(user: Any, *, owner: Any = None) -> QuerySet[MatterImportantDate]:
    """Active milestones on open Matters, scoped to the reader.

    ``owner`` filters by ``Matter.owner`` because that is who the milestone
    belongs to for work purposes. An ownerless Matter's deadline therefore
    reaches nobody's Minu töö — it appears as *vastutajata* on Ülevaade, which
    is the honest place for work nobody has been given (§4.2).
    """
    queryset = (
        MatterImportantDate.objects.visible_to(user)
        .filter(
            status=FactStatus.ACTIVE,
            matter__is_open=True,
            matter__record_mode=RecordMode.FULL,
        )
        .select_related("matter", "matter__stage", "matter__owner")
    )
    if owner is not None:
        queryset = queryset.filter(matter__owner=owner)
    return queryset


def work_items(
    user: Any,
    *,
    today: date | None = None,
    responsible: Any = None,
    latest: date | None = None,
) -> list[WorkItem]:
    """Every dated work item this reader may see, chronologically.

    Two queries, not one per row. ``latest`` bounds the future so a page that
    only shows five weeks does not drag a decade of milestones through Python.
    Nothing bounds the past: work that is late is exactly what these pages
    exist to surface.
    """
    today = today or timezone.localdate()

    actions = dated_actions(user, responsible=responsible)
    deadlines = important_deadlines(user, owner=responsible)
    if latest is not None:
        actions = actions.filter(target_date__lte=latest)
        deadlines = deadlines.filter(date_value__lte=latest)

    items = [action_item(action, today) for action in actions]
    items += [_deadline_item(record, today) for record in deadlines]
    return sort_items(items)


def sort_items(items: list[WorkItem]) -> list[WorkItem]:
    """Oldest first, then by reference so the order is stable between loads."""
    return sorted(items, key=lambda item: (item.when or date.max, item.reference, item.text))


# ---------------------------------------------------------------------------
# Banding
# ---------------------------------------------------------------------------


def end_of_iso_week(today: date) -> date:
    """Sunday of the week ``today`` falls in. ISO weeks run Monday–Sunday."""
    return today + timedelta(days=6 - today.weekday())


def band_of(item: WorkItem, today: date, week_end: date, horizon: date | None) -> str | None:
    """Which band this item belongs to, or ``None`` if it is beyond the window.

    The last day of a period is what decides whether it is behind us. An
    expectation recorded as *III kvartal 2026* has not passed on 2 July, and
    banding it on its anchor would call a quarter that has barely started late.

    A past item that is not genuinely overdue lands in *Ülevaatamiseks küps*.
    That covers the WAIT and MONITOR the band is named for, and it also catches
    the case the old banding lost entirely: a DO whose source named a vague
    month is stored as an expectation rather than a deadline, so it can never be
    overdue and used to fall out of every band and off the page
    (app/legacy_import/register_next_actions.py).
    """
    when = item.when
    if when is None:
        return None
    end = item.period_end or when
    if end < today:
        return BAND_OVERDUE if item.is_overdue else BAND_RIPE
    if when <= today:
        # Either today exactly, or a period already running. Both are now.
        return BAND_TODAY
    if when <= week_end:
        return BAND_WEEK
    if horizon is None or when <= horizon:
        return BAND_LATER
    return None


def band_items(
    items: list[WorkItem],
    today: date,
    *,
    week_end: date | None = None,
    horizon: date | None = None,
) -> list[WorkBand]:
    """The bands that actually hold something, in reading order.

    An empty band is omitted rather than rendered empty: five headings above
    five "ei ole ühtegi" lines is a page that looks like a data-quality problem
    rather than a quiet morning.
    """
    week_end = week_end or end_of_iso_week(today)
    grouped: dict[str, list[WorkItem]] = {key: [] for key in BAND_ORDER}
    for item in items:
        key = band_of(item, today, week_end, horizon)
        if key is not None:
            grouped[key].append(item)

    # Most overdue first inside the red band; everything else earliest first,
    # which `sort_items` has already arranged.
    grouped[BAND_OVERDUE].sort(key=lambda item: item.period_end or item.when or date.max)
    grouped[BAND_RIPE].sort(key=lambda item: item.period_end or item.when or date.max)

    return [
        WorkBand(key=key, label=BAND_LABELS[key], items=grouped[key][:BAND_LIMIT])
        for key in BAND_ORDER
        if grouped[key]
    ]


# ---------------------------------------------------------------------------
# Population predicates the three surfaces share
# ---------------------------------------------------------------------------


def overdue_items(items: list[WorkItem]) -> list[WorkItem]:
    """Genuinely late work. Never includes a passed review date."""
    return [item for item in items if item.is_overdue]


def review_ripe_items(items: list[WorkItem]) -> list[WorkItem]:
    return [item for item in items if item.is_review_ripe]


def week_items(items: list[WorkItem], today: date, week_end: date | None = None) -> list[WorkItem]:
    """Dated work falling inside the current ISO week, today included."""
    week_end = week_end or end_of_iso_week(today)
    return [item for item in items if item.when is not None and today <= item.when <= week_end]


def no_next_action_q() -> Q:
    """Matters carrying no open instruction, as a condition rather than a list."""
    return ~Q(
        pk__in=NextAction.objects.filter(status=ActionStatus.OPEN).values("matter_id"),
    )


def matters_without_action(user: Any, *, owner: Any = None) -> QuerySet[Matter]:
    """Open Matters with no active NextAction — the one attention state no date can produce.

    Without it a Matter simply stops appearing anywhere and goes quiet, which is
    the failure the whole right rail exists to prevent (design handoff,
    recommendation 1).
    """
    queryset = open_matters(user).filter(no_next_action_q())
    if owner is not None:
        queryset = queryset.filter(owner=owner)
    return queryset


def ownerless_matters(user: Any) -> QuerySet[Matter]:
    return open_matters(user).filter(owner__isnull=True)


__all__ = [
    "BAND_LABELS",
    "BAND_LATER",
    "BAND_ORDER",
    "BAND_OVERDUE",
    "BAND_RIPE",
    "BAND_TODAY",
    "BAND_WEEK",
    "MEANING_DEADLINE",
    "MEANING_EXPECTED",
    "MEANING_IMPORTANT",
    "MEANING_REVIEW",
    "SOURCE_IMPORTANT_DEADLINE",
    "SOURCE_NEXT_ACTION",
    "ActionKind",
    "WorkBand",
    "WorkItem",
    "action_item",
    "band_items",
    "band_of",
    "dated_actions",
    "end_of_iso_week",
    "important_deadlines",
    "matters_without_action",
    "open_matters",
    "overdue_items",
    "ownerless_matters",
    "review_ripe_items",
    "sort_items",
    "undated_actions",
    "week_items",
    "work_items",
]
