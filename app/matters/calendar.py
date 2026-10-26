"""Kalender — Minu asjad's one calendar of dated work and recorded commencements.

A **presentation**, not a model (docs/adr/0158, amendment of 2026-10-26). It
reads two canonical sources and writes nothing:

* **Tähtajad ja tegevused** — the shared work model, `work_items.work_items`,
  narrowed to the person whose desk this is exactly as the bands above it are
  (an action by its responsible person, a milestone, an `Arvamuse tähtaeg` and
  a feedback wait by the Matter's owner). The same rows, the same precedence —
  an open step suppresses its file's response deadline — and so the same
  absence of duplicates: a row is placed once, from the one source that owns it.
* **Jõustumised** — active `MatterEffectiveDate` rows on Matters this person
  carries, open or closed (a commencement often lands after the file is closed,
  ADR 0158 §2), read through the *reader's* `visible_to`.

On a colleague's desk the colleague decides *whose* work is shown and the reader
remains the authorization principal: every queryset starts from
``visible_to(user)``, so a restricted Matter or a restricted child record on a
readable one contributes no dot, no count and no line.

Three rules carry over unchanged.

* **No day is invented.** A day — `EXACT` or `INFERRED` (ADR 0079 §8) — goes on
  its cell. A month, quarter, half-year or year is never drawn on a day: it is
  listed beside the grid under its period, once, however many displayed months
  it touches. In the year overview a month-precision record is counted in its
  own month (it is contained in it); a wider period is counted nowhere and
  listed once under the year.
* **A commencement is nobody's deadline** (ADR 0158 §6, ADR 0188 §7). It is never
  late, never in a work count and never confirmed: the line reads «Kirjas olev
  jõustumise kuupäev», and a past one «kirjas olev kuupäev on möödunud».
* **The meaning of a work date is the work model's.** «Üle tähtaja» only where
  `WorkItem.is_overdue` says so; a review date that has come round is ripe, not
  late; an informational milestone is for information.

Bounded by the shown window: the work model is read with ``earliest``/``latest``
and commencements by overlap, so a year overview never reads the archive.
"""

from __future__ import annotations

import calendar as stdlib_calendar
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from django.urls import reverse

from app.core import dates
from app.core.dates import format_estonian_date, short_day_month
from app.intelligence.enums import FactStatus
from app.intelligence.models import MatterEffectiveDate
from app.matters import work_items as wi
from app.matters.models import Matter
from app.workflow.dates import format_at_precision, is_approximate
from app.workflow.enums import ESTONIAN_MONTHS, ROMAN_QUARTERS, DatePrecision
from app.workflow.lateness import period_end_for

# ---------------------------------------------------------------------------
# Address
# ---------------------------------------------------------------------------

#: Which presentation. Not `vaade` (the portfolio's filter) and not `kuni` (the
#: timeline's horizon): those two keep their meaning on the same page.
VIEW_PARAM = "kalender"
#: Which period of it: ``2026-10-19`` (a Monday), ``2026-11``, ``2026-K4``, ``2026``.
PERIOD_PARAM = "kalendri_aeg"

VIEW_WEEKS = "4n"
VIEW_MONTH = "kuu"
VIEW_QUARTER = "kvartal"
VIEW_YEAR = "aasta"
VIEWS: tuple[tuple[str, str], ...] = (
    (VIEW_WEEKS, "4 nädalat"),
    (VIEW_MONTH, "Kuu"),
    (VIEW_QUARTER, "Kvartal"),
    (VIEW_YEAR, "Aasta"),
)
#: The two that need more room than the rail has (an overlay panel).
WIDE_VIEWS = (VIEW_QUARTER, VIEW_YEAR)

#: The HTMX target the calendar swaps itself into, and the id it carries.
ELEMENT_ID = "minu-kalender"

#: The four-week view starts this many weeks before the current one.
WEEKS_BEFORE = 1
WEEKS_SHOWN = 4
#: Dots drawn on one day before «+N».
DOTS_SHOWN = 3

CATEGORY_WORK = "too"
CATEGORY_COMMENCEMENT = "joustumine"
CATEGORY_LABELS = {
    CATEGORY_WORK: "Tähtajad ja tegevused",
    CATEGORY_COMMENCEMENT: "Jõustumised",
}
COMMENCEMENT_MEANING = "Kirjas olev jõustumise kuupäev"
COMMENCEMENT_PASSED = "Kirjas olev kuupäev on möödunud"

_YEAR_MIN, _YEAR_MAX = 1990, 2100


def requested_view(params: Any) -> str:
    value = (params.get(VIEW_PARAM) or "").strip()
    return value if value in dict(VIEWS) else VIEW_WEEKS


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _quarter_of(day: date) -> int:
    return (day.month - 1) // 3 + 1


def default_anchor(view: str, today: date) -> date:
    """The first day of the period a view opens on when the address names none."""
    if view == VIEW_MONTH:
        return today.replace(day=1)
    if view == VIEW_QUARTER:
        return date(today.year, 3 * (_quarter_of(today) - 1) + 1, 1)
    if view == VIEW_YEAR:
        return date(today.year, 1, 1)
    return _week_start(today) - timedelta(weeks=WEEKS_BEFORE)


def anchor_from(view: str, raw: str | None, today: date) -> date:
    """The period's first day, read from ``kalendri_aeg`` — or the default.

    Unreadable or out-of-range input falls back to the current period rather
    than erroring: the parameter steers the presentation and selects nothing
    (the same reading `commencements.month_from` gives ``?kuu=``).
    """
    text = (raw or "").strip()
    try:
        if view == VIEW_WEEKS and len(text) == 10:
            day = date.fromisoformat(text)
            if _YEAR_MIN <= day.year <= _YEAR_MAX:
                return _week_start(day)
        elif view == VIEW_MONTH and len(text) == 7 and text[4] == "-":
            year, month = int(text[:4]), int(text[5:])
            if _YEAR_MIN <= year <= _YEAR_MAX and 1 <= month <= 12:
                return date(year, month, 1)
        elif view == VIEW_QUARTER and len(text) == 7 and text[4:6] == "-K":
            year, quarter = int(text[:4]), int(text[6])
            if _YEAR_MIN <= year <= _YEAR_MAX and 1 <= quarter <= 4:
                return date(year, 3 * (quarter - 1) + 1, 1)
        elif view == VIEW_YEAR and len(text) == 4:
            year = int(text)
            if _YEAR_MIN <= year <= _YEAR_MAX:
                return date(year, 1, 1)
    except ValueError:
        pass
    return default_anchor(view, today)


def period_key(view: str, anchor: date) -> str:
    if view == VIEW_MONTH:
        return f"{anchor.year:04d}-{anchor.month:02d}"
    if view == VIEW_QUARTER:
        return f"{anchor.year:04d}-K{_quarter_of(anchor)}"
    if view == VIEW_YEAR:
        return f"{anchor.year:04d}"
    return anchor.isoformat()


def period_range(view: str, anchor: date) -> tuple[date, date]:
    """First and last day the view shows."""
    if view == VIEW_MONTH:
        return anchor, dates.end_of_month(anchor)
    if view == VIEW_QUARTER:
        return anchor, dates.end_of_month(dates.add_months(anchor, 2))
    if view == VIEW_YEAR:
        return anchor, date(anchor.year, 12, 31)
    return anchor, anchor + timedelta(weeks=WEEKS_SHOWN, days=-1)


def shift_anchor(view: str, anchor: date, step: int) -> date:
    if view == VIEW_MONTH:
        return dates.add_months(anchor, step)
    if view == VIEW_QUARTER:
        return dates.add_months(anchor, 3 * step)
    if view == VIEW_YEAR:
        return date(anchor.year + step, 1, 1)
    return anchor + timedelta(weeks=WEEKS_SHOWN * step)


def _in_bounds(anchor: date) -> bool:
    return _YEAR_MIN <= anchor.year <= _YEAR_MAX


def period_title(view: str, anchor: date) -> str:
    start, end = period_range(view, anchor)
    if view == VIEW_MONTH:
        return f"{ESTONIAN_MONTHS[anchor.month - 1].capitalize()} {anchor.year}"
    if view == VIEW_QUARTER:
        return f"{ROMAN_QUARTERS[_quarter_of(anchor) - 1]} kvartal {anchor.year}"
    if view == VIEW_YEAR:
        return str(anchor.year)
    if start.year != end.year:
        return f"{format_estonian_date(start)}–{format_estonian_date(end)}"
    return f"{short_day_month(start)}–{short_day_month(end)}.{end.year}"


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalendarEvent:
    """One real record, as the calendar shows it. Never a merge of several."""

    category: str
    #: ``"<source>:<pk>"`` — unique across the calendar, so a test (and a reader)
    #: can tell that one record is shown once.
    key: str
    matter: Matter
    #: What the record says: the step's sentence, the milestone's title, what
    #: commences. Empty for an `Arvamuse tähtaeg`, whose meaning is the whole line.
    text: str
    #: The date's meaning in the product's own words — PLAANIS, VAATAN ÜLE,
    #: ARVAMUSE TÄHTAEG, «Kirjas olev jõustumise kuupäev».
    meaning: str
    when: date
    period_end: date
    precision: str
    display_date: str
    #: «üle tähtaja», «ülevaatamise aeg on käes», «teadmiseks», «kirjas olev
    #: kuupäev on möödunud» — or nothing.
    status: str
    url: str
    #: The work row behind it, for a work event; ``None`` for a commencement.
    item: wi.WorkItem | None = None

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS[self.category]

    @property
    def is_work(self) -> bool:
        return self.category == CATEGORY_WORK

    @property
    def is_approximate(self) -> bool:
        return is_approximate(self.precision)

    @property
    def precision_label(self) -> str:
        """«kvartali täpsusega», «tuletatud tekstist» — nothing for an exact day."""
        if self.precision == DatePrecision.EXACT:
            return ""
        return str(DatePrecision(self.precision).label).lower()

    @property
    def is_overdue(self) -> bool:
        return self.item is not None and self.item.is_overdue

    @property
    def is_restricted(self) -> bool:
        return bool(self.matter.is_restricted)

    @property
    def summary(self) -> str:
        """One line for a dot's ``title`` and the screen reader's name."""
        parts = [self.category_label, self.matter.title, self.meaning]
        if self.text:
            parts.append(self.text)
        parts.append(self.display_date)
        if self.precision_label:
            parts.append(self.precision_label)
        if self.status:
            parts.append(self.status)
        return " · ".join(str(part) for part in parts if part)


def _work_status(item: wi.WorkItem) -> str:
    if item.is_overdue:
        return "üle tähtaja"
    if item.is_review_ripe:
        return "ülevaatamise aeg on käes"
    if item.is_informational:
        return "teadmiseks, ei ole tähtaeg"
    if item.is_follow_up:
        return "arvamuse järelkontroll"
    if item.is_planned:
        return "planeeritud tegevus"
    return ""


def _work_event(item: wi.WorkItem) -> CalendarEvent | None:
    if item.when is None:
        return None
    # `period_end_for`, not `period_bounds`: a period anchored outside the
    # supported years (a 1989 row read by the year-wide pre-filter) must not
    # turn the page into a 500 — the guard `action_item` already relies on.
    end = item.period_end or period_end_for(item.when, item.date_precision)
    return CalendarEvent(
        category=CATEGORY_WORK,
        key=f"{item.source_type}:{item.object_id}",
        matter=item.matter,
        text=item.text,
        meaning=item.meaning,
        when=item.when,
        period_end=end,
        precision=item.date_precision,
        display_date=item.display_date or format_at_precision(item.when, item.date_precision),
        status=_work_status(item),
        url=item.record_url,
        item=item,
    )


def _commencement_event(
    record: MatterEffectiveDate, today: date, when: date, last: date
) -> CalendarEvent:
    """``when``/``last`` are the record's own dates, passed already known to be set."""
    matter_url = reverse("matters:matter_detail", kwargs={"pk": record.matter_id})
    return CalendarEvent(
        category=CATEGORY_COMMENCEMENT,
        key=f"EFFECTIVE_DATE:{record.pk}",
        matter=record.matter,
        text=record.description,
        meaning=COMMENCEMENT_MEANING,
        when=when,
        period_end=last,
        precision=record.date_precision,
        display_date=record.display_date,
        status=COMMENCEMENT_PASSED if last < today else "",
        url=f"{matter_url}#joustumine",
    )


def work_events(
    user: Any,
    subject: Any,
    start: date,
    end: date,
    today: date,
    items: list[wi.WorkItem] | None = None,
) -> list[CalendarEvent]:
    """This person's dated work overlapping ``start``…``end``, from the work model.

    ``items`` is the work model the page has already read for this desk
    (`MyWork.items` — same reader, subject and day); given, nothing is read
    again. The calendar's own swaps read it bounded to the window.
    """
    if items is None:
        items = wi.work_items(user, today=today, responsible=subject, earliest=start, latest=end)
    events = [event for event in map(_work_event, items) if event is not None]
    return [event for event in events if event.when <= end and event.period_end >= start]


def commencement_events(
    user: Any, subject: Any, start: date, end: date, today: date
) -> list[CalendarEvent]:
    """Active, dated commencements on Matters this person carries, overlapping the window.

    The Matter population is ``Matter.objects.visible_to(user)`` narrowed to the
    subject's ownership — open and closed, never a tombstone (the default
    manager's scope) — and the records themselves are read through their own
    ``visible_to``, so a restricted commencement on a readable Matter is absent.
    `GENERAL_ORDER` and `UNKNOWN` carry no date by constraint and are never on a
    calendar.
    """
    matters = Matter.objects.visible_to(user).filter(owner=subject).order_by().values("pk")
    records = (
        MatterEffectiveDate.objects.visible_to(user)
        .filter(
            status=FactStatus.ACTIVE,
            matter__in=matters,
            date_value__isnull=False,
            date_value__lte=end,
            period_end__gte=start,
        )
        .select_related("matter")
    )
    return [
        _commencement_event(record, today, record.date_value, record.period_end)
        for record in records
        if record.date_value is not None and record.period_end is not None
    ]


def _ordered(events: list[CalendarEvent]) -> list[CalendarEvent]:
    return sorted(
        events,
        key=lambda e: (
            e.when,
            0 if e.is_work else 1,
            str(e.matter.title).casefold(),
            e.key,
        ),
    )


# ---------------------------------------------------------------------------
# Grids
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CategoryGroup:
    key: str
    label: str
    events: list[CalendarEvent]


def _grouped(events: list[CalendarEvent]) -> list[CategoryGroup]:
    groups = []
    for key, label in CATEGORY_LABELS.items():
        rows = [event for event in events if event.category == key]
        if rows:
            groups.append(CategoryGroup(key=key, label=label, events=rows))
    return groups


def _counted(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _counts_label(work: int, commencements: int) -> str:
    parts = []
    if work:
        parts.append(_counted(work, "tähtaeg või tegevus", "tähtaega või tegevust"))
    if commencements:
        parts.append(_counted(commencements, "jõustumine", "jõustumist"))
    return ", ".join(parts) if parts else "sündmusi ei ole"


@dataclass(frozen=True)
class Day:
    day: date
    #: Inside the period being shown (a month grid shows its neighbours' days
    #: greyed and empty).
    in_period: bool
    is_today: bool
    events: list[CalendarEvent]

    @property
    def dots(self) -> list[CalendarEvent]:
        return self.events[:DOTS_SHOWN]

    @property
    def more(self) -> int:
        return max(len(self.events) - DOTS_SHOWN, 0)

    @property
    def groups(self) -> list[CategoryGroup]:
        return _grouped(self.events)

    @property
    def work_count(self) -> int:
        return sum(1 for event in self.events if event.is_work)

    @property
    def commencement_count(self) -> int:
        return len(self.events) - self.work_count

    @property
    def title(self) -> str:
        """«N 12.11.2026», the popover's heading."""
        return f"{dates.weekday_name(self.day).capitalize()} {format_estonian_date(self.day)}"

    @property
    def label(self) -> str:
        """The day's accessible name: its date and what it holds."""
        today = ", täna" if self.is_today else ""
        return (
            f"{format_estonian_date(self.day)}{today}: "
            f"{_counts_label(self.work_count, self.commencement_count)}"
        )


@dataclass(frozen=True)
class Week:
    number: int
    days: list[Day]

    @property
    def busy_days(self) -> list[Day]:
        return [day for day in self.days if day.events]

    @property
    def total(self) -> int:
        return sum(len(day.events) for day in self.days)

    @property
    def label(self) -> str:
        first, last = self.days[0].day, self.days[-1].day
        work = sum(day.work_count for day in self.days)
        commencements = sum(day.commencement_count for day in self.days)
        return (
            f"{self.number}. nädal, {short_day_month(first)}–{short_day_month(last)}: "
            f"{_counts_label(work, commencements)}"
        )


@dataclass(frozen=True)
class MonthGrid:
    month: date
    title: str
    weeks: list[Week]
    #: Recorded to this month with no day — on no cell, listed under the grid.
    month_events: list[CalendarEvent]
    #: The address of this month in the detailed month view.
    query: str = ""


@dataclass(frozen=True)
class YearMonth:
    month: date
    title: str
    work_count: int
    commencement_count: int
    query: str

    @property
    def total(self) -> int:
        return self.work_count + self.commencement_count

    @property
    def density(self) -> int:
        """0–3, the shade of the month's tile. A count, never a judgement."""
        if self.total == 0:
            return 0
        if self.total <= 3:
            return 1
        if self.total <= 10:
            return 2
        return 3

    @property
    def label(self) -> str:
        return f"{self.title}: {_counts_label(self.work_count, self.commencement_count)}"


def _week(
    first: date,
    by_day: dict[date, list[CalendarEvent]],
    *,
    today: date,
    in_period: Callable[[date], bool],
) -> Week:
    days = []
    for offset in range(7):
        cell = first + timedelta(days=offset)
        inside = in_period(cell)
        days.append(
            Day(
                day=cell,
                in_period=inside,
                is_today=cell == today,
                events=by_day.get(cell, []) if inside else [],
            )
        )
    return Week(number=first.isocalendar()[1], days=days)


def _month_grid(
    month: date,
    by_day: dict[date, list[CalendarEvent]],
    month_events: list[CalendarEvent],
    *,
    today: date,
    query: str = "",
) -> MonthGrid:
    weeks = [
        _week(week[0], by_day, today=today, in_period=lambda cell: cell.month == month.month)
        for week in stdlib_calendar.Calendar(firstweekday=0).monthdatescalendar(
            month.year, month.month
        )
    ]
    return MonthGrid(
        month=month,
        title=f"{ESTONIAN_MONTHS[month.month - 1].capitalize()} {month.year}",
        weeks=weeks,
        month_events=month_events,
        query=query,
    )


# ---------------------------------------------------------------------------
# The calendar
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ViewLink:
    key: str
    label: str
    query: str
    is_current: bool


@dataclass
class Calendar:
    view: str
    anchor: date
    start: date
    end: date
    today: date
    title: str
    views: list[ViewLink]
    previous_query: str
    next_query: str
    today_query: str
    #: Whether «Täna» would go anywhere (the current period is not already shown).
    shows_today: bool
    #: ``4n`` — four weeks.
    weeks: list[Week] = field(default_factory=list)
    #: ``kuu`` (one) and ``kvartal`` (three).
    months: list[MonthGrid] = field(default_factory=list)
    #: ``aasta`` — twelve tiles with counts, no dots.
    year_months: list[YearMonth] = field(default_factory=list)
    #: Approximate records overlapping the shown period and not placed in a
    #: month list: a quarter, half-year or year — and in the four-week view
    #: every approximate record. Each is listed once.
    periods: list[CalendarEvent] = field(default_factory=list)
    #: Every event read for the window, once — what the counts are taken from.
    events: list[CalendarEvent] = field(default_factory=list)
    close_query: str = ""

    @property
    def is_wide(self) -> bool:
        return self.view in WIDE_VIEWS

    @property
    def work_count(self) -> int:
        return sum(1 for event in self.events if event.is_work)

    @property
    def commencement_count(self) -> int:
        return len(self.events) - self.work_count

    @property
    def counts_label(self) -> str:
        return _counts_label(self.work_count, self.commencement_count)

    @property
    def element_id(self) -> str:
        return ELEMENT_ID

    @property
    def weekdays(self) -> tuple[str, ...]:
        return ("E", "T", "K", "N", "R", "L", "P")


def build_calendar(
    user: Any,
    subject: Any,
    *,
    params: Any,
    today: date,
    query_for: Callable[[dict[str, str | None]], str],
    items: list[wi.WorkItem] | None = None,
) -> Calendar:
    """The calendar for ``subject``'s desk, as ``user`` may see it.

    ``params`` is the request's query; ``query_for(changes)`` returns the page's
    address with those calendar parameters set (``None`` drops one), so every
    link keeps ``kuni`` and ``vaade`` exactly as they were. ``items`` — the
    page's own work model, when it has read one (see `work_events`).
    """
    view = requested_view(params)
    anchor = anchor_from(view, params.get(PERIOD_PARAM), today)
    start, end = period_range(view, anchor)

    def address(target_view: str, target_anchor: date) -> str:
        default = target_view == VIEW_WEEKS and target_anchor == default_anchor(VIEW_WEEKS, today)
        return query_for(
            {
                VIEW_PARAM: None if target_view == VIEW_WEEKS else target_view,
                PERIOD_PARAM: None if default else period_key(target_view, target_anchor),
            }
        )

    events = _ordered(
        work_events(user, subject, start, end, today, items)
        + commencement_events(user, subject, start, end, today)
    )

    by_day: dict[date, list[CalendarEvent]] = {}
    approximate: list[CalendarEvent] = []
    for event in events:
        if event.is_approximate:
            approximate.append(event)
        else:
            by_day.setdefault(event.when, []).append(event)

    def contained_month(event: CalendarEvent) -> date | None:
        """The month a month-precision record belongs to, when the view lists months."""
        if view not in (VIEW_MONTH, VIEW_QUARTER, VIEW_YEAR):
            return None
        if event.precision != DatePrecision.MONTH:
            return None
        return event.when.replace(day=1)

    month_lists: dict[date, list[CalendarEvent]] = {}
    periods: list[CalendarEvent] = []
    for event in approximate:
        month = contained_month(event)
        if month is not None:
            month_lists.setdefault(month, []).append(event)
        else:
            periods.append(event)

    previous_anchor = shift_anchor(view, anchor, -1)
    next_anchor = shift_anchor(view, anchor, 1)
    current = default_anchor(view, today)
    result = Calendar(
        view=view,
        anchor=anchor,
        start=start,
        end=end,
        today=today,
        title=period_title(view, anchor),
        views=[
            ViewLink(key, label, address(key, default_anchor(key, today)), key == view)
            for key, label in VIEWS
        ],
        previous_query=address(view, previous_anchor) if _in_bounds(previous_anchor) else "",
        next_query=address(view, next_anchor) if _in_bounds(next_anchor) else "",
        today_query=address(view, current),
        shows_today=anchor != current,
        periods=periods,
        events=events,
        # Closing a wide view lands on the month it was about: today's, when
        # today is inside the period, else the period's first.
        close_query=address(VIEW_MONTH, today.replace(day=1) if start <= today <= end else anchor)
        if view in WIDE_VIEWS
        else "",
    )

    if view == VIEW_WEEKS:
        result.weeks = [
            _week(
                start + timedelta(weeks=n),
                by_day,
                today=today,
                in_period=lambda cell: start <= cell <= end,
            )
            for n in range(WEEKS_SHOWN)
        ]
    elif view in (VIEW_MONTH, VIEW_QUARTER):
        months = [anchor] if view == VIEW_MONTH else [dates.add_months(anchor, n) for n in range(3)]
        result.months = [
            _month_grid(
                month,
                by_day,
                month_lists.get(month, []),
                today=today,
                query=address(VIEW_MONTH, month),
            )
            for month in months
        ]
    else:
        tiles = []
        for number in range(1, 13):
            month = date(anchor.year, number, 1)
            last = dates.end_of_month(month)
            day_events = [e for day, rows in by_day.items() if month <= day <= last for e in rows]
            listed = month_lists.get(month, [])
            work = sum(1 for e in day_events + listed if e.is_work)
            commencements = len(day_events) + len(listed) - work
            tiles.append(
                YearMonth(
                    month=month,
                    title=ESTONIAN_MONTHS[number - 1].capitalize(),
                    work_count=work,
                    commencement_count=commencements,
                    query=address(VIEW_MONTH, month),
                )
            )
        result.year_months = tiles
    return result
