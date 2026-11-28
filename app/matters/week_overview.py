"""«Selle nädala ülevaade» — what happened and what is still owed, this calendar week (r97).

A read side only. Nothing here writes, fetches or asks an AI anything, and every
figure is taken from a definition that already exists:

* **Sent opinions, new Teemad, closed Teemad, incoming monitoring** are Minu
  statistika's own cards (`app.matters.my_statistics`) for the week's period, so
  the overview and the detailed page cannot disagree, and a figure whose
  history is not fully recorded keeps its coverage («≥ 3», «—», a real 0).
  That builder is self-only, so a manager reading a colleague's desk gets none
  of these blocks — no cross-person statistics are exposed (r97 §33).
* **Outstanding opinions** are `dashboard.drafting_matters` for the subject's
  Teemad: the population Osakond and the register already count. They are
  counted as Teemad and never divided into the sent figure: sent opinions and
  open requests have no honest common denominator, so there is no percentage.
* **Muud tegevused** are counted from the `NextAction` rows themselves, by what
  actually ended them. A moved day is *edasi lükatud*, never *tehtud* — Lovable's
  Command Center counts a postponement as work; Juristid does not (ADR 0236).

Every action count names one action row once (its primary key is the identity),
so a step completed and its successor opened are two different facts, and an
automatic ending (no ``ended_by``) is nobody's work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlencode

from django.db.models import QuerySet
from django.urls import reverse
from django.utils import timezone

from app.core.dates import format_estonian_date, local_day_range
from app.matters import stat_periods
from app.matters import work_items as wi
from app.matters.models import Matter
from app.matters.my_dates import EffectiveRow, deadline_rows, effective_rows
from app.matters.my_statistics import Cell, Coverage
from app.matters.register_filters import register_url
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics
from app.workflow.models import NextAction

#: Outcomes of an action, in the order the overview prints them. The key is the
#: `?naitaja=` of the records page that lists exactly these rows.
DONE, REVIEWED, POSTPONED, CANCELLED, SUPERSEDED, OUTSTANDING = (
    "tehtud",
    "ule-vaadatud",
    "edasi-lukatud",
    "tuhistatud",
    "asendatud",
    "tegemata",
)

ACTION_LABELS: dict[str, str] = {
    DONE: "Tehtud",
    REVIEWED: "Üle vaadatud",
    POSTPONED: "Edasi lükatud",
    CANCELLED: "Tühistatud",
    SUPERSEDED: "Asendatud",
    OUTSTANDING: "Tegemata",
}

#: How many upcoming commencements / recent opinions / new Teemad the preview shows.
PREVIEW = 3

#: The seven-day window: today and the six days after it.
HORIZON_DAYS = 7


@dataclass(frozen=True)
class WeekFigure:
    """One number, what it counts, and the exact list behind it."""

    key: str
    label: str
    cell: Cell
    url: str = ""
    tone: str = ""
    #: What the number counts, said once for a screen reader: «teemat», «tegevust».
    unit: str = ""

    @property
    def text(self) -> str:
        return self.cell.text

    @property
    def accessible(self) -> str:
        return f"{self.label}: {self.cell.spoken} {self.unit}".strip()


def _exact(value: int) -> Cell:
    return Cell(Coverage.VERIFIED, value)


@dataclass(frozen=True)
class SevenDayRow:
    when: date
    relative: str
    matter: Matter
    what: str
    category: str
    url: str
    #: «Üle tähtaja» for a late DO deadline, «Ülevaatus» for a WAIT/MONITOR review
    #: date — which is a review, never an overdue obligation — «» otherwise.
    status: str
    is_review: bool
    is_late: bool


@dataclass(frozen=True)
class PreviewRow:
    matter: Matter
    day: date | None
    note: str = ""
    url: str = ""


@dataclass
class WeekOverview:
    start: date
    end: date
    previous_start: date
    previous_end: date
    #: Statistika-backed blocks exist only on one's own desk.
    has_statistics: bool
    opinions: list[WeekFigure] = field(default_factory=list)
    actions: list[WeekFigure] = field(default_factory=list)
    commencements: list[WeekFigure] = field(default_factory=list)
    previous: list[WeekFigure] = field(default_factory=list)
    monitoring: list[WeekFigure] = field(default_factory=list)
    seven_days: list[SevenDayRow] = field(default_factory=list)
    seven_days_url: str = ""
    recent_opinions: list[PreviewRow] = field(default_factory=list)
    recent_opinions_url: str = ""
    new_matters: list[PreviewRow] = field(default_factory=list)
    new_matters_url: str = ""
    upcoming_commencements: list[EffectiveRow] = field(default_factory=list)
    commencements_url: str = ""
    statistics_url: str = ""


# ---------------------------------------------------------------------------
# Actions — counted from the rows, by what ended them
# ---------------------------------------------------------------------------


def _ended_in(week: stat_periods.Span | Any, *, user: Any, subject: Any) -> QuerySet[NextAction]:
    """Actions *subject* ended during ``week``, on Teemad ``user`` may read.

    ``ended_by`` is the person who did it. A row ended by the system (a follow-up
    collapsing, the synchronisation) has none and is therefore nobody's work.
    """
    return (
        NextAction.objects.visible_to(user)
        .filter(local_day_range("ended_at", week.start, week.end), ended_by=subject)
        .select_related("matter", "replaced_by")
    )


def classify_ended(row: NextAction) -> str:
    """What ending ``row`` was. The single place that tells a postponement from a replacement.

    A row replaced by one with the **same text, kind and meaning whose day is
    later** is the same step moved on (`date_shift`, `Muuda`); a replacement
    that changes the sentence is a different step taking its place. Only a
    COMPLETED row is work done.
    """
    if row.status == ActionStatus.COMPLETED:
        return DONE
    if row.status == ActionStatus.CANCELLED:
        return CANCELLED
    if row.status != ActionStatus.SUPERSEDED:
        return ""
    successor = row.replaced_by
    if (
        successor is not None
        and successor.text == row.text
        and successor.kind == row.kind
        and row.target_date is not None
        and successor.target_date is not None
        and successor.target_date > row.target_date
    ):
        return POSTPONED
    return SUPERSEDED


def action_records(
    user: Any, subject: Any, kind: str, start: date, end: date
) -> list[tuple[NextAction, str, date]]:
    """The rows behind one *Muud tegevused* figure: (action, outcome, day it happened)."""
    span = stat_periods.Span(start, end)
    out: list[tuple[NextAction, str, date]] = []
    if kind == REVIEWED:
        from app.audit.enums import ChangeEventType
        from app.audit.models import ChangeEvent

        seen: set[Any] = set()
        events = (
            ChangeEvent.objects.filter(
                local_day_range("occurred_at", start, end),
                event_type=ChangeEventType.NEXT_ACTION_REVIEWED,
                actor=subject,
            )
            .exclude(payload__has_any_keys=["provenance_source", "provenance", "operation"])
            .order_by("-occurred_at")
        )
        by_id = {
            action.pk: action
            for action in NextAction.objects.visible_to(user)
            .filter(pk__in=[event.object_id for event in events if event.object_id])
            .select_related("matter")
        }
        for event in events:
            action = by_id.get(event.object_id)
            if action is None or action.pk in seen:
                continue
            seen.add(action.pk)
            out.append((action, REVIEWED, timezone.localtime(event.occurred_at).date()))
        return out
    if kind == OUTSTANDING:
        return []
    for row in _ended_in(span, user=user, subject=subject).order_by("-ended_at"):
        outcome = classify_ended(row)
        if outcome == kind and row.ended_at is not None:
            out.append((row, outcome, timezone.localtime(row.ended_at).date()))
    return out


def _outstanding(items: list[wi.WorkItem], today: date, week_end: date) -> list[wi.WorkItem]:
    """DO obligations that are late or fall due by Sunday. A review date is not one."""
    out = []
    for item in items:
        if not item.is_action or item.when is None:
            continue
        if item.action_kind != ActionKind.DO or item.date_semantics != DateSemantics.DEADLINE:
            continue
        if item.is_overdue or item.when <= week_end:
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# The overview
# ---------------------------------------------------------------------------


def records_page(kind: str, *, today: date, week: date | None = None) -> str:
    """The records page (the temporary preset) for sent opinions / new Teemad: this year to date."""
    params = {"naitaja": kind, "alates": date(today.year, 1, 1)}
    if week is not None:
        params = {"naitaja": kind, "nadal": week}
    return reverse("matters:my_week_records") + "?" + urlencode(params)


def records_url(kind: str, week: date) -> str:
    """The page that lists exactly the action rows of one outcome in the week of ``week``."""
    return reverse("matters:my_week_records") + "?" + urlencode({"naitaja": kind, "nadal": week})


def build_week_overview(
    user: Any,
    subject: Any,
    today: date,
    *,
    items: list[wi.WorkItem],
    is_self: bool,
    due_this_week: int,
    due_url: str,
    overdue_before_week: int,
) -> WeekOverview:
    """Assemble the overview. ``items`` is the work model the page already read."""
    from app.matters.my_statistics import my_statistics

    week = stat_periods.current(today)
    last = week.previous
    overview = WeekOverview(
        start=week.start,
        end=week.end,
        previous_start=last.start,
        previous_end=last.end,
        has_statistics=is_self,
    )
    stats_url = week.url(reverse("matters:my_statistics"))
    overview.statistics_url = stats_url

    stats_now = my_statistics(user, today, week) if is_self else None

    # --- Arvamused ---------------------------------------------------------
    if stats_now is not None:
        cards = {card.key: card for group in stats_now.groups for card in group.cards}
        sent = cards["saadetud"]
        overview.opinions.append(
            WeekFigure("sent", "Saadetud", sent.cell, sent.url, unit="arvamust")
        )
    overview.opinions.append(
        WeekFigure(
            "owed",
            "Täitmata sel nädalal",
            _exact(due_this_week),
            due_url,
            unit="teemat",
        )
    )
    overview.opinions.append(
        WeekFigure(
            "late",
            "Varasemast üle tähtaja",
            _exact(overdue_before_week),
            register_url(**_opinion_params(subject, week, before_week=True)),
            "danger" if overdue_before_week else "",
            unit="teemat",
        )
    )

    # --- Muud tegevused ----------------------------------------------------
    span = stat_periods.Span(week.start, week.end)
    counts = {DONE: 0, POSTPONED: 0, CANCELLED: 0, SUPERSEDED: 0}
    for row in _ended_in(span, user=user, subject=subject):
        outcome = classify_ended(row)
        if outcome in counts:
            counts[outcome] += 1
    reviews = len(action_records(user, subject, REVIEWED, week.start, week.end))
    open_do = _outstanding(items, today, week.end)
    figures = {
        DONE: counts[DONE],
        REVIEWED: reviews,
        POSTPONED: counts[POSTPONED],
        CANCELLED: counts[CANCELLED],
        SUPERSEDED: counts[SUPERSEDED],
    }
    for key, value in figures.items():
        overview.actions.append(
            WeekFigure(
                key,
                ACTION_LABELS[key],
                _exact(value),
                records_url(key, week.start),
                unit="tegevust",
            )
        )
    overview.actions.append(
        WeekFigure(
            OUTSTANDING,
            ACTION_LABELS[OUTSTANDING],
            _exact(len(open_do)),
            reverse("matters:my_deadlines"),
            "danger" if any(i.is_overdue for i in open_do) else "",
            unit="tegevust",
        )
    )

    # --- Jõustumised -------------------------------------------------------
    rows = effective_rows(user, today) if is_self else []
    dated = [(r, r.start, r.end) for r in rows if r.start is not None and r.end is not None]
    in_week = [(r, a, b) for r, a, b in dated if a <= week.end and b >= week.start]
    arrived = [r for r, _, b in in_week if r.certainty == "taps" and b <= today]
    upcoming = [r for r, _, b in in_week if r.certainty == "taps" and b > today]
    planned = [r for r, _, _ in in_week if r.certainty == "ligikaudne"]
    eff_url = reverse("matters:my_effective_dates")
    for key, label, found in (
        ("arrived", "Jõustunud", arrived),
        ("upcoming", "Jõustuvad", upcoming),
        ("planned", "Eeldatavad", planned),
    ):
        overview.commencements.append(
            WeekFigure(key, label, _exact(len(found)), eff_url, unit="jõustumist")
        )
    overview.upcoming_commencements = [
        r for r in rows if r.start is not None and r.end is not None and r.end >= today
    ][:PREVIEW]
    overview.commencements_url = eff_url

    # --- Eelmine nädal -----------------------------------------------------
    if is_self:
        before = my_statistics(user, today, last)
        for group in before.groups:
            for card in group.cards:
                if card.key in ("saadetud", "uued", "lopetatud"):
                    overview.previous.append(
                        WeekFigure(card.key, card.figure.label, card.cell, card.url)
                    )
        for key in (DONE,):
            overview.previous.append(
                WeekFigure(
                    key,
                    "Tehtud tegevused",
                    _exact(
                        sum(
                            1
                            for r in _ended_in(last, user=user, subject=subject)
                            if classify_ended(r) == DONE
                        )
                    ),
                    records_url(key, last.start),
                    unit="tegevust",
                )
            )

    # --- Sissetulev seire (never personal work) ---------------------------
    if stats_now is not None:
        for group in stats_now.groups:
            if group.key == "seire":
                overview.monitoring = [
                    WeekFigure(c.key, c.figure.label, c.cell, c.url)
                    for c in group.cards
                    if c.cell.value
                ]

    # --- 7 päeva -----------------------------------------------------------
    horizon = today + timedelta(days=HORIZON_DAYS - 1)
    if is_self:
        for drow in deadline_rows(user, today):
            if drow.due > horizon:
                continue
            item = drow.item
            review = item.action_kind in (ActionKind.WAIT, ActionKind.MONITOR) or (
                item.is_action and item.date_semantics == DateSemantics.REVIEW_ON
            )
            late = bool(item.is_overdue) and not review
            if drow.due < today and not late:
                continue  # a passed review date is a review, not a past obligation
            overview.seven_days.append(
                SevenDayRow(
                    when=drow.due,
                    relative=drow.relative,
                    matter=drow.matter,
                    what=drow.what or drow.category_label,
                    category=drow.category_label,
                    url=drow.matter_url,
                    status="Üle tähtaja" if late else ("Ülevaatus" if review else ""),
                    is_review=review,
                    is_late=late,
                )
            )
        overview.seven_days.sort(key=lambda r: (not r.is_late, r.when, str(r.matter.title)))
    overview.seven_days_url = reverse("matters:my_deadlines")

    # --- Previews ------------------------------------------------------------
    if is_self and stats_now is not None:
        overview.recent_opinions, overview.recent_opinions_url = _recent_opinions(user, subject)
        overview.new_matters, overview.new_matters_url = _new_matters(user, subject)
    return overview


def _opinion_params(subject: Any, week: Any, *, before_week: bool = False) -> dict[str, str]:
    from app.matters.register_filters import OPEN_FULL, OPINION_DRAFTING

    params: dict[str, str] = {
        **OPEN_FULL,
        "vastutaja": str(getattr(subject, "pk", "")),
        "arvamus": OPINION_DRAFTING,
    }
    if before_week:
        params["tahtaeg_kuni"] = format_estonian_date(week.start - timedelta(days=1))
    else:
        params["tahtaeg_kuni"] = format_estonian_date(week.end)
    return params


def _recent_opinions(user: Any, subject: Any) -> tuple[list[PreviewRow], str]:
    from app.reporting import opinions

    mine = [f for f in opinions.opinion_facts(user) if _credited(f, subject)]
    latest = sorted(mine, key=lambda f: f.sent_on, reverse=True)[:PREVIEW]
    matters = {
        m.pk: m
        for m in Matter.objects.visible_to(user).filter(pk__in=[f.matter.matter_id for f in latest])
    }
    rows = [
        PreviewRow(
            matters[f.matter.matter_id],
            f.sent_on,
            note=("Lovable'i teatatud" if f.source == opinions.SOURCE_IMPORTED else ""),
            url=reverse("matters:matter_detail", kwargs={"pk": f.matter.matter_id}),
        )
        for f in latest
        if f.matter.matter_id in matters
    ]
    return rows, records_page("saadetud-arvamused", today=timezone.localdate())


def _credited(fact: Any, subject: Any) -> bool:
    """Recorded sender, else the Teema's owner — Minu statistika's credit rule."""
    if fact.actor_id is not None:
        return str(fact.actor_id) == str(subject.pk)
    return fact.matter.owner_id == subject.pk


def _new_matters(user: Any, subject: Any) -> tuple[list[PreviewRow], str]:
    """Latest Teemad this person holds, by the day they were *received*.

    An imported historical Teema has its original receipt date, which is the
    business meaning; one with no receipt date is not «new» and is left out
    rather than given a date it never had.
    """
    found = list(
        Matter.objects.visible_to(user)
        .real_data()
        .filter(owner=subject, is_open=True, received_date__isnull=False)
        .order_by("-received_date", "-created_at")[:PREVIEW]
    )
    rows = [
        PreviewRow(m, m.received_date, url=reverse("matters:matter_detail", kwargs={"pk": m.pk}))
        for m in found
    ]
    return rows, records_page("uued-teemad", today=timezone.localdate())


def week_start_of(raw: str | None, today: date) -> date:
    """A ``?nadal=`` Monday, or this week's. Anything unparseable or future is this week."""
    try:
        value = date.fromisoformat(raw or "")
    except ValueError:
        return stat_periods.current(today).start
    if value > today or value < stat_periods.FIRST_DAY:
        return stat_periods.current(today).start
    return value - timedelta(days=value.weekday())


__all__ = [
    "ACTION_LABELS",
    "WeekOverview",
    "action_records",
    "build_week_overview",
    "classify_ended",
    "records_url",
    "week_start_of",
]
