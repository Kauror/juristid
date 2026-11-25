"""Minu tähtajad and Minu jõustumised — two personal date pages (read models).

A **presentation**, not a model, in the sense the Minu asjad calendar already is
(`app/matters/calendar.py`, docs/adr/0158). Nothing here writes, and nothing
defines a second meaning of «minu»:

* **Minu tähtajad** reads the shared work model, `work_items.work_items`, with
  ``responsible=user`` — exactly the call `my_work.build_my_work` makes for the
  signed-in person's own desk. So a step the person answers for, an active
  `Oluline tähtaeg` (not cancelled, not superseded, not informational), an
  outstanding `Arvamuse tähtaeg` and a dated feedback wait on that person's open
  FULL Teemad are here, and nothing else is. A row on Minu asjad and a row here
  are the same row.
* **Minu jõustumised** reads active `MatterEffectiveDate` rows on Teemad the
  person owns, open or closed — the population the Minu asjad calendar draws
  under *Jõustumised* (`calendar.commencement_events`, ADR 0158 §2) — through
  the reader's own ``visible_to`` on both the Teema and the record. Undated
  rows (`Jõustub üldises korras`, `Kuupäev täpsustamisel`) are listed too: an
  unknown date is an honest state, not a missing row.

Both pages are self only: there is no person in the address, so a department
head opening them reads their own dates and nobody else's.

**No day is invented.** A day — `EXACT` or `INFERRED` — goes on its calendar
cell; a month, quarter, half-year or year is listed under the calendar as a
period, never placed on a day (the rule `calendar.py` states). A period's
relative wording therefore never counts days to a day nobody named.
"""

from __future__ import annotations

import calendar as stdlib_calendar
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from django.http import QueryDict
from django.urls import reverse

from app.core.dates import format_estonian_date
from app.intelligence.enums import EffectiveDateKind, FactStatus
from app.intelligence.models import MatterEffectiveDate
from app.matters import work_items as wi
from app.matters.models import Matter
from app.workflow.dates import format_at_precision, is_approximate
from app.workflow.enums import ESTONIAN_MONTHS, ActionStatus, DatePrecision
from app.workflow.lateness import period_end_for
from app.workflow.models import NextAction

# ---------------------------------------------------------------------------
# Address
# ---------------------------------------------------------------------------

MONTH_PARAM = "kuu"
DAY_PARAM = "paev"
SEARCH_PARAM = "q"
BUCKET_PARAM = "aeg"
CATEGORY_PARAM = "liik"
SORT_PARAM = "jarjestus"
PERIOD_PARAM = "periood"
STATUS_PARAM = "olek"

SORT_LATEST_FIRST = "hilisemad"

#: Monday first, as the department's calendars are (`calendar.py`).
WEEKDAYS: tuple[tuple[str, str], ...] = (
    ("E", "esmaspäev"),
    ("T", "teisipäev"),
    ("K", "kolmapäev"),
    ("N", "neljapäev"),
    ("R", "reede"),
    ("L", "laupäev"),
    ("P", "pühapäev"),
)

_YEAR_MIN, _YEAR_MAX = 1990, 2100


def month_from(raw: str | None, today: date) -> date:
    """``?kuu=2026-11`` to the month's first day, or the current month.

    Unreadable input falls back rather than erroring: the parameter steers the
    presentation and selects nothing.
    """
    text = (raw or "").strip()
    if len(text) == 7 and text[4] == "-":
        try:
            year, month = int(text[:4]), int(text[5:])
        except ValueError:
            return today.replace(day=1)
        if _YEAR_MIN <= year <= _YEAR_MAX and 1 <= month <= 12:
            return date(year, month, 1)
    return today.replace(day=1)


def day_from(raw: str | None) -> date | None:
    """``?paev=2026-11-25`` to a date, or nothing."""
    text = (raw or "").strip()
    if len(text) != 10:
        return None
    try:
        day = date.fromisoformat(text)
    except ValueError:
        return None
    return day if _YEAR_MIN <= day.year <= _YEAR_MAX else None


def month_key(month: date) -> str:
    return f"{month.year:04d}-{month.month:02d}"


def shift_month(month: date, delta: int) -> date:
    index = month.year * 12 + (month.month - 1) + delta
    return date(index // 12, index % 12 + 1, 1)


def month_label(month: date) -> str:
    return f"{ESTONIAN_MONTHS[month.month - 1].capitalize()} {month.year}"


def day_label(day: date | None) -> str:
    """«25. november 2026» — the side panel's heading."""
    if day is None:
        return ""
    return f"{day.day}. {ESTONIAN_MONTHS[day.month - 1]} {day.year}"


def month_last_day(month: date) -> date:
    return month.replace(day=stdlib_calendar.monthrange(month.year, month.month)[1])


QueryFor = Callable[[dict[str, str | None]], str]


def query_builder(path: str, params: QueryDict) -> QueryFor:
    """A link on this page's own address that changes only the named parameters.

    ``None`` removes a parameter. Every other parameter — the table's filters
    while the calendar moves, the calendar's month while a filter changes — is
    kept exactly as it is.
    """

    def query_for(changes: dict[str, str | None]) -> str:
        copy = params.copy()
        for key, value in changes.items():
            if value is None or value == "":
                copy.pop(key, None)
            else:
                copy[key] = value
        encoded = copy.urlencode()
        return f"{path}?{encoded}" if encoded else path

    return query_for


# ---------------------------------------------------------------------------
# Relative wording
# ---------------------------------------------------------------------------


def relative_day(day: date, today: date) -> str:
    """«täna», «homme», «3 päeva pärast», «eile», «2 päeva möödas»."""
    days = (day - today).days
    if days == 0:
        return "täna"
    if days == 1:
        return "homme"
    if days == -1:
        return "eile"
    if days > 1:
        return f"{days} päeva pärast"
    return f"{-days} päeva möödas"


def relative_period(start: date, end: date, precision: str, today: date) -> str:
    """The relative words for a dated record at its own precision.

    A day reads in days. A period never counts days to a day nobody named: it
    is «käimas» while today is inside it, «möödunud» once its last day is
    behind us, and «algab N päeva pärast» before it begins.
    """
    if not is_approximate(precision):
        return relative_day(start, today)
    if end < today:
        return "periood möödunud"
    if start <= today:
        return "periood käimas"
    days = (start - today).days
    return "periood algab homme" if days == 1 else f"periood algab {days} päeva pärast"


# ---------------------------------------------------------------------------
# The month calendar, shared by both pages
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalendarCell:
    day: date
    in_month: bool
    is_today: bool
    is_selected: bool
    count: int
    #: Every row on this day is behind us — the mark is drawn red.
    is_past: bool
    url: str

    @property
    def label(self) -> str:
        """The cell's accessible name: the day, and how many rows are on it."""
        text = format_estonian_date(self.day)
        if self.count == 1:
            text += ", 1 kirje"
        elif self.count:
            text += f", {self.count} kirjet"
        if self.is_today:
            text += " (täna)"
        return text


@dataclass(frozen=True)
class MonthCalendar:
    month: date
    label: str
    weeks: list[list[CalendarCell]]
    previous_url: str
    next_url: str
    today_url: str
    #: Rows recorded to a period overlapping the month — never on a day.
    period_rows: list[Any]
    weekdays: tuple[tuple[str, str], ...] = WEEKDAYS

    @property
    def total(self) -> int:
        return sum(cell.count for week in self.weeks for cell in week if cell.in_month)


def build_month_calendar(
    rows: Iterable[Any],
    *,
    month: date,
    today: date,
    selected: date | None,
    query_for: QueryFor,
) -> MonthCalendar:
    """One month, Monday first. ``rows`` carry ``start``, ``end`` and ``precision``.

    A day row is placed on its cell; a period row overlapping the month is
    listed beside the grid and placed on no cell.
    """
    first, last = month, month_last_day(month)
    by_day: dict[date, list[Any]] = {}
    periods: list[Any] = []
    for row in rows:
        if row.start is None or row.end is None:
            continue
        if is_approximate(row.precision):
            if row.start <= last and row.end >= first:
                periods.append(row)
            continue
        by_day.setdefault(row.start, []).append(row)

    weeks = []
    for week in stdlib_calendar.Calendar(firstweekday=0).monthdatescalendar(
        month.year, month.month
    ):
        cells = []
        for day in week:
            on_day = by_day.get(day, [])
            cells.append(
                CalendarCell(
                    day=day,
                    in_month=day.month == month.month,
                    is_today=day == today,
                    is_selected=day == selected,
                    count=len(on_day),
                    is_past=bool(on_day) and day < today,
                    url=query_for({DAY_PARAM: day.isoformat(), MONTH_PARAM: month_key(day)}),
                )
            )
        weeks.append(cells)
    return MonthCalendar(
        month=month,
        label=month_label(month),
        weeks=weeks,
        previous_url=query_for({MONTH_PARAM: month_key(shift_month(month, -1)), DAY_PARAM: None}),
        next_url=query_for({MONTH_PARAM: month_key(shift_month(month, 1)), DAY_PARAM: None}),
        today_url=query_for({MONTH_PARAM: None, DAY_PARAM: None}),
        period_rows=sorted(periods, key=lambda row: (row.start, row.end)),
    )


def default_selected_day(
    raw: str | None, month: date, today: date, rows: Iterable[Any]
) -> date | None:
    """The day the side panel shows.

    The one the address names; otherwise today when the month shown is the
    current one; otherwise the month's first day that carries a row — so moving
    to another month opens on something rather than on an empty panel.
    """
    named = day_from(raw)
    if named is not None:
        return named
    if month.year == today.year and month.month == today.month:
        return today
    last = month_last_day(month)
    days = sorted(
        row.start
        for row in rows
        if row.start is not None
        and not is_approximate(row.precision)
        and month <= row.start <= last
    )
    return days[0] if days else None


# ---------------------------------------------------------------------------
# Minu tähtajad
# ---------------------------------------------------------------------------

BUCKET_PAST = "moodunud"
BUCKET_TODAY = "tana"
BUCKET_WEEK = "nadal"
BUCKET_NEXT_30 = "30-paeva"
BUCKET_LATER = "hiljem"

BUCKETS: tuple[tuple[str, str], ...] = (
    (BUCKET_PAST, "Möödunud"),
    (BUCKET_TODAY, "Täna"),
    (BUCKET_WEEK, "Selle nädala jooksul"),
    (BUCKET_NEXT_30, "Järgmise 30 päeva jooksul"),
    (BUCKET_LATER, "Hiljem"),
)
BUCKET_LABELS = dict(BUCKETS)

CATEGORY_ACTION = "tegevus"
CATEGORY_IMPORTANT = "oluline"
CATEGORY_RESPONSE = "arvamus"
CATEGORY_FEEDBACK = "tagasiside"

#: The four sources of the work model, in the words a filter offers them.
CATEGORIES: tuple[tuple[str, str], ...] = (
    (CATEGORY_ACTION, "Tegevus"),
    (CATEGORY_IMPORTANT, "Oluline tähtaeg"),
    (CATEGORY_RESPONSE, "Arvamuse tähtaeg"),
    (CATEGORY_FEEDBACK, "Ootame tagasisidet"),
)
CATEGORY_LABELS = dict(CATEGORIES)
_SOURCE_CATEGORY = {
    wi.SOURCE_NEXT_ACTION: CATEGORY_ACTION,
    wi.SOURCE_IMPORTANT_DEADLINE: CATEGORY_IMPORTANT,
    wi.SOURCE_RESPONSE_DEADLINE: CATEGORY_RESPONSE,
    wi.SOURCE_FEEDBACK_WAIT: CATEGORY_FEEDBACK,
}


def due_day(item: wi.WorkItem) -> date | None:
    """The day a row falls due: its own day, or the last day of its period."""
    if item.when is None:
        return None
    return item.period_end or period_end_for(item.when, item.date_precision)


def bucket_of(due: date, today: date) -> str:
    """Five disjoint buckets, so a count card and the rows it filters agree."""
    if due < today:
        return BUCKET_PAST
    if due == today:
        return BUCKET_TODAY
    if due <= wi.end_of_iso_week(today):
        return BUCKET_WEEK
    if due <= today + timedelta(days=30):
        return BUCKET_NEXT_30
    return BUCKET_LATER


@dataclass(frozen=True)
class DeadlineRow:
    item: wi.WorkItem
    start: date
    end: date
    precision: str
    due: date
    bucket: str
    category: str
    relative: str
    #: The Teema's current open step, read once for the page.
    current_step: str
    edit_url: str
    edit_label: str

    @property
    def matter(self) -> Matter:
        return self.item.matter

    @property
    def matter_url(self) -> str:
        return self.item.matter_url

    @property
    def is_past(self) -> bool:
        return self.bucket == BUCKET_PAST

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS[self.category]

    @property
    def display_date(self) -> str:
        return self.item.display_date or format_at_precision(self.start, self.precision)

    @property
    def what(self) -> str:
        """What the date is: the step's sentence, the milestone's title, the round's name.

        Empty for an `Arvamuse tähtaeg`, whose meaning is the whole line — the
        category already says it.
        """
        return self.item.text


def _edit_link(item: wi.WorkItem) -> tuple[str, str]:
    """The existing form that edits this row on its Teema. No new write path."""
    if item.source_type == wi.SOURCE_IMPORTANT_DEADLINE:
        return (
            reverse(
                "intelligence:edit_important_date",
                kwargs={"matter_id": item.matter_id, "pk": item.object_id},
            ),
            "Muuda",
        )
    if item.source_type == wi.SOURCE_NEXT_ACTION and not item.is_follow_up:
        return item.full_edit_url, "Muuda"
    return item.record_url, "Ava"


def _current_steps(user: Any, matter_ids: set[Any]) -> dict[Any, str]:
    """Each Teema's open step, through the reader's own visibility. One query."""
    if not matter_ids:
        return {}
    steps: dict[Any, str] = {}
    actions = (
        NextAction.objects.visible_to(user)
        .filter(matter_id__in=matter_ids, status=ActionStatus.OPEN)
        .order_by("matter_id", "-created_at")
        .values_list("matter_id", "text")
    )
    for matter_id, text in actions:
        steps.setdefault(matter_id, text)
    return steps


def deadline_rows(user: Any, today: date) -> list[DeadlineRow]:
    """Every dated row of the signed-in person's own work, chronologically.

    The work model with ``responsible=user`` — the Minu asjad call, unchanged.
    """
    items = wi.work_items(user, today=today, responsible=user)
    steps = _current_steps(user, {item.matter_id for item in items if item.when is not None})
    rows = []
    for item in items:
        if item.when is None:
            continue
        due = due_day(item) or item.when
        end = item.period_end or due
        edit_url, edit_label = _edit_link(item)
        rows.append(
            DeadlineRow(
                item=item,
                start=item.when,
                end=end,
                precision=item.date_precision,
                due=due,
                bucket=bucket_of(due, today),
                category=_SOURCE_CATEGORY.get(item.source_type, CATEGORY_ACTION),
                relative=relative_period(item.when, end, item.date_precision, today),
                current_step=steps.get(item.matter_id, ""),
                edit_url=edit_url,
                edit_label=edit_label,
            )
        )
    return rows


@dataclass(frozen=True)
class CountCard:
    key: str
    label: str
    count: int
    url: str
    is_active: bool
    is_alert: bool


@dataclass(frozen=True)
class Choice:
    value: str
    label: str
    selected: bool


@dataclass
class DeadlinesPage:
    today: date
    cards: list[CountCard]
    calendar: MonthCalendar
    selected_day: date | None
    day_rows: list[DeadlineRow]
    rows: list[DeadlineRow]
    total: int
    search: str
    bucket: str
    category: str
    latest_first: bool
    categories: list[Choice]
    sort_url: str
    clear_url: str
    is_filtered: bool
    has_any: bool = field(default=False)

    @property
    def selected_label(self) -> str:
        return day_label(self.selected_day)

    @property
    def bucket_label(self) -> str:
        return BUCKET_LABELS.get(self.bucket, "")


def _matches(text: str, needle: str) -> bool:
    return needle in text.casefold()


def build_my_deadlines(user: Any, params: QueryDict, path: str, today: date) -> DeadlinesPage:
    query_for = query_builder(path, params)
    rows = deadline_rows(user, today)

    bucket = params.get(BUCKET_PARAM, "")
    bucket = bucket if bucket in BUCKET_LABELS else ""
    category = params.get(CATEGORY_PARAM, "")
    category = category if category in CATEGORY_LABELS else ""
    search = (params.get(SEARCH_PARAM) or "").strip()
    latest_first = params.get(SORT_PARAM) == SORT_LATEST_FIRST

    counts = {key: 0 for key, _ in BUCKETS}
    for row in rows:
        counts[row.bucket] += 1
    cards = [
        CountCard(
            key=key,
            label=label,
            count=counts[key],
            # A pressed card toggles: pressing the active one again clears it.
            url=query_for({BUCKET_PARAM: None if bucket == key else key}) + "#tabel",
            is_active=bucket == key,
            is_alert=key == BUCKET_PAST and counts[key] > 0,
        )
        for key, label in BUCKETS
    ]

    shown = rows
    if bucket:
        shown = [row for row in shown if row.bucket == bucket]
    if category:
        shown = [row for row in shown if row.category == category]
    if search:
        needle = search.casefold()
        shown = [
            row
            for row in shown
            if _matches(str(row.matter.title), needle)
            or _matches(row.item.text, needle)
            or _matches(row.matter.display_reference, needle)
            or _matches(row.current_step, needle)
        ]
    shown = sorted(shown, key=lambda row: (row.due, row.start), reverse=latest_first)

    month = month_from(params.get(MONTH_PARAM), today)
    selected = default_selected_day(params.get(DAY_PARAM), month, today, rows)
    calendar = build_month_calendar(
        rows, month=month, today=today, selected=selected, query_for=query_for
    )
    day_rows = (
        [row for row in rows if row.start == selected and not is_approximate(row.precision)]
        if selected
        else []
    )
    return DeadlinesPage(
        today=today,
        cards=cards,
        calendar=calendar,
        selected_day=selected,
        day_rows=day_rows,
        rows=shown,
        total=len(rows),
        search=search,
        bucket=bucket,
        category=category,
        latest_first=latest_first,
        categories=[Choice(key, label, key == category) for key, label in CATEGORIES],
        sort_url=query_for({SORT_PARAM: None if latest_first else SORT_LATEST_FIRST}) + "#tabel",
        clear_url=query_for(
            {BUCKET_PARAM: None, CATEGORY_PARAM: None, SEARCH_PARAM: None, SORT_PARAM: None}
        )
        + "#tabel",
        is_filtered=bool(bucket or category or search),
        has_any=bool(rows),
    )


# ---------------------------------------------------------------------------
# Minu jõustumised
# ---------------------------------------------------------------------------

PERIOD_NEXT_30 = "30-paeva"
PERIOD_FUTURE = "tulevased"
PERIOD_PAST = "moodunud"
PERIOD_ALL = "koik"

PERIODS: tuple[tuple[str, str], ...] = (
    (PERIOD_NEXT_30, "Järgmised 30 päeva"),
    (PERIOD_FUTURE, "Tulevased"),
    (PERIOD_PAST, "Möödunud"),
    (PERIOD_ALL, "Kõik"),
)
PERIOD_LABELS = dict(PERIODS)
DEFAULT_PERIOD = PERIOD_FUTURE

CERTAINTY_EXACT = "taps"
CERTAINTY_APPROXIMATE = "ligikaudne"
CERTAINTY_GENERAL = "uldine"
CERTAINTY_UNKNOWN = "tapsustamisel"

#: The certainty states, in the model's own words: `EffectiveDateKind` for the
#: three states of knowledge, and «ligikaudne» for a known date recorded to a
#: period (its `DatePrecision` label is shown on the row itself).
CERTAINTIES: tuple[tuple[str, str], ...] = (
    (CERTAINTY_EXACT, str(EffectiveDateKind.KNOWN_DATE.label)),
    (CERTAINTY_APPROXIMATE, "Ligikaudne kuupäev"),
    (CERTAINTY_GENERAL, str(EffectiveDateKind.GENERAL_ORDER.label)),
    (CERTAINTY_UNKNOWN, str(EffectiveDateKind.UNKNOWN.label)),
)
CERTAINTY_LABELS = dict(CERTAINTIES)

#: What a past date says — never «jõustunud»: the record says a date, nothing
#: on it confirms the act came into force (calendar.COMMENCEMENT_PASSED).
COMMENCEMENT_PASSED = "Kirjas olev kuupäev on möödunud"

NEXT_SHOWN = 3


def certainty_of(record: MatterEffectiveDate) -> str:
    if record.kind == EffectiveDateKind.GENERAL_ORDER:
        return CERTAINTY_GENERAL
    if record.kind == EffectiveDateKind.UNKNOWN:
        return CERTAINTY_UNKNOWN
    if is_approximate(record.date_precision):
        return CERTAINTY_APPROXIMATE
    return CERTAINTY_EXACT


@dataclass(frozen=True)
class EffectiveRow:
    record: MatterEffectiveDate
    start: date | None
    end: date | None
    precision: str
    certainty: str
    relative: str
    is_past: bool
    edit_url: str
    matter_url: str

    @property
    def matter(self) -> Matter:
        return self.record.matter

    @property
    def is_dated(self) -> bool:
        return self.start is not None

    @property
    def certainty_label(self) -> str:
        """The badge: the kind for an undated row, the precision for a period."""
        if self.certainty == CERTAINTY_APPROXIMATE:
            return str(DatePrecision(self.precision).label)
        return CERTAINTY_LABELS[self.certainty]

    @property
    def display_date(self) -> str:
        """The date at its precision, or the words for an undated state."""
        return self.record.display_when

    @property
    def what(self) -> str:
        return self.record.description or "Jõustumine"


def my_effective_records(user: Any) -> Any:
    """Active commencements on Teemad the person owns, open or closed.

    The Minu asjad calendar's population (`calendar._carried_matters` with the
    person as subject, `commencement_events`), without its date window: the
    Teema through ``Matter.objects.visible_to(user)`` narrowed to
    ``owner=user``, the record through its own ``visible_to``. Cancelled and
    superseded records are not commencements anybody expects any more.
    """
    matters = Matter.objects.visible_to(user).filter(owner=user).order_by().values("pk")
    return (
        MatterEffectiveDate.objects.visible_to(user)
        .filter(status=FactStatus.ACTIVE, matter__in=matters)
        .select_related("matter")
    )


def effective_rows(user: Any, today: date) -> list[EffectiveRow]:
    rows = []
    for record in my_effective_records(user):
        start, end = record.date_value, record.period_end
        rows.append(
            EffectiveRow(
                record=record,
                start=start,
                end=end,
                precision=record.date_precision,
                certainty=certainty_of(record),
                relative=(
                    relative_period(start, end, record.date_precision, today)
                    if start is not None and end is not None
                    else ""
                ),
                is_past=end is not None and end < today,
                edit_url=reverse(
                    "intelligence:edit_effective_date",
                    kwargs={"matter_id": record.matter_id, "pk": record.pk},
                ),
                matter_url=reverse("matters:matter_detail", kwargs={"pk": record.matter_id})
                + "#joustumine",
            )
        )
    # Dated first, by date; then the undated, by Teema.
    return sorted(
        rows,
        key=lambda row: (
            row.start is None,
            row.start or date.min,
            row.end or date.min,
            str(row.matter.title),
        ),
    )


def in_next_30(row: EffectiveRow, today: date) -> bool:
    if row.start is None or row.end is None:
        return False
    return row.start <= today + timedelta(days=30) and row.end >= today


def in_period(row: EffectiveRow, period: str, today: date) -> bool:
    if period == PERIOD_ALL:
        return True
    if period == PERIOD_NEXT_30:
        return in_next_30(row, today)
    if period == PERIOD_PAST:
        return row.is_past
    # Tulevased: not yet behind us — and the undated, whose date is still to come.
    return not row.is_past


@dataclass
class EffectivePage:
    today: date
    next_30_count: int
    next_30_url: str
    upcoming: list[EffectiveRow]
    calendar: MonthCalendar
    selected_day: date | None
    day_rows: list[EffectiveRow]
    rows: list[EffectiveRow]
    total: int
    search: str
    period: str
    certainty: str
    periods: list[Choice]
    certainties: list[Choice]
    clear_url: str
    is_filtered: bool
    has_upcoming: bool

    @property
    def selected_label(self) -> str:
        return day_label(self.selected_day)

    @property
    def next_30_label(self) -> str:
        return "jõustumine" if self.next_30_count == 1 else "jõustumist"


def build_my_effective_dates(user: Any, params: QueryDict, path: str, today: date) -> EffectivePage:
    query_for = query_builder(path, params)
    rows = effective_rows(user, today)

    period = params.get(PERIOD_PARAM, "")
    period = period if period in PERIOD_LABELS else DEFAULT_PERIOD
    certainty = params.get(STATUS_PARAM, "")
    certainty = certainty if certainty in CERTAINTY_LABELS else ""
    search = (params.get(SEARCH_PARAM) or "").strip()

    shown = [row for row in rows if in_period(row, period, today)]
    if certainty:
        shown = [row for row in shown if row.certainty == certainty]
    if search:
        needle = search.casefold()
        shown = [
            row
            for row in shown
            if _matches(str(row.matter.title), needle)
            or _matches(row.record.description, needle)
            or _matches(row.matter.display_reference, needle)
        ]
    if period == PERIOD_PAST:
        # The most recent first: a reader looking back starts from now.
        shown = list(reversed(shown))

    upcoming = [row for row in rows if row.start is not None and not row.is_past]
    month = month_from(params.get(MONTH_PARAM), today)
    selected = default_selected_day(params.get(DAY_PARAM), month, today, rows)
    calendar = build_month_calendar(
        rows, month=month, today=today, selected=selected, query_for=query_for
    )
    day_rows = (
        [
            row
            for row in rows
            if row.start == selected and row.start is not None and not is_approximate(row.precision)
        ]
        if selected
        else []
    )
    return EffectivePage(
        today=today,
        next_30_count=sum(1 for row in rows if in_next_30(row, today)),
        next_30_url=query_for(
            {PERIOD_PARAM: PERIOD_NEXT_30, STATUS_PARAM: None, SEARCH_PARAM: None}
        )
        + "#tabel",
        upcoming=upcoming[:NEXT_SHOWN],
        calendar=calendar,
        selected_day=selected,
        day_rows=day_rows,
        rows=shown,
        total=len(rows),
        search=search,
        period=period,
        certainty=certainty,
        periods=[Choice(key, label, key == period) for key, label in PERIODS],
        certainties=[Choice(key, label, key == certainty) for key, label in CERTAINTIES],
        clear_url=query_for({PERIOD_PARAM: None, STATUS_PARAM: None, SEARCH_PARAM: None})
        + "#tabel",
        is_filtered=bool(period != DEFAULT_PERIOD or certainty or search),
        has_upcoming=bool(upcoming),
    )
