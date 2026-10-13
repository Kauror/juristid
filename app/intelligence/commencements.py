"""Jõustumised — the Teemad register's commencement presentation (docs/adr/0158).

**Not a destination.** ADR 0071 retired the department-wide «Jõustuvad aktid»
page and made `Jõustumine` a dimension of the register; it stays retired
(`/jalgimine/joustumised/` still redirects, nothing is added to the bar). What
this module answers is the one question the register's Matter table cannot:
*which* commencements, on *which* dates, inside the population the reader has
already narrowed. It is an alternative rendering of the same filtered register,
offered only while a Jõustumine filter is active and kept in the URL
(``?vaade=joustumised`` or ``?vaade=kalender``), so a link is still the whole
state and every chip and `Tühjenda kõik` still work.

Four rules, each inherited rather than restated.

* **The population is the register's.** The caller hands over the Matter
  queryset the register has already searched, authorized and filtered; nothing
  here re-decides which Matters are in it.
* **Authorization before anything is counted.** Commencements are read through
  ``MatterEffectiveDate.objects.visible_to(user)`` before they are bucketed,
  placed on a day or counted, so a restricted record on a readable Matter does
  not exist for a reader who may not see it (Stage-2G brief 31).
* **Active only.** `CANCELLED` and `SUPERSEDED` rows are history on the Teema
  page; they are not commencements anybody should plan around, and
  ``?joustumine=on`` already means *active* (`register_filters.filter_by_commencement`).
* **Containment, never a manufactured day.** The window narrows each row by
  :func:`app.matters.register_filters.commencement_window_q` — the same
  condition that selected its Matter. A quarter is written as a quarter
  (`format_at_precision`), is never drawn on a day cell, and belongs to a
  30-day bucket only when the whole period is inside it. `GENERAL_ORDER` and
  `UNKNOWN` carry no date by database constraint and have a bucket of their
  own.

A commencement is a fact about the legal instrument, not anybody's deadline:
nothing here writes a `NextAction`, reaches Minu asjad or is ever "overdue"
(docs/adr/0071 §What was considered and refused).
"""

from __future__ import annotations

import calendar
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from django.db.models import QuerySet
from django.utils import timezone

from app.intelligence.enums import FactStatus
from app.intelligence.models import MatterEffectiveDate
from app.matters.register_filters import (
    COMMENCEMENT_END_PARAM,
    COMMENCEMENT_PARAM,
    COMMENCEMENT_START_PARAM,
    FACT_ABSENT,
    commencement_window_q,
)
from app.workflow.dates import is_approximate
from app.workflow.enums import ESTONIAN_MONTHS, DatePrecision

#: The register's presentation switch. `vaade` because that is the word every
#: other page's view switch already uses (Osakond, Minu asjad, Ülevaade).
VIEW_PARAM = "vaade"
VIEW_LIST = "joustumised"
VIEW_CALENDAR = "kalender"

#: The three ways the same filtered register can be read, in the switch's order.
#: The empty key is the ordinary Matter table, which is what an absent
#: parameter means.
VIEW_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "Teemad"),
    (VIEW_LIST, "Jõustumiste loend"),
    (VIEW_CALENDAR, "Jõustumiste kalender"),
)

#: The calendar's month, as ``YYYY-MM``.
MONTH_PARAM = "kuu"

#: The list's own ordering. Not `?jarjestus=`, which orders *Matters* and whose
#: values mean something else; a commencement list sorted by `Viimane tegevus`
#: would be answering a question about the file, not about the date.
SORT_PARAM = "joustumiste_jarjestus"
SORT_DATE = "kuupaev"
SORT_DATE_DESC = "kuupaev_desc"
SORT_MATTER = "teema"
SORT_CHOICES = (SORT_DATE, SORT_DATE_DESC, SORT_MATTER)

#: Every parameter that steers the presentation rather than the population.
#: Dropped by `Tühjenda kõik`, and dropped by a chip's `×` when that chip was
#: the last Jõustumine filter — a view switch with no commencement filter
#: behind it would be a word in the address that nothing reads.
PRESENTATION_PARAMS = (VIEW_PARAM, MONTH_PARAM, SORT_PARAM)

#: How far ahead the first bucket reaches, both ends inclusive.
SOON_DAYS = 30

BUCKET_SOON = "jargmised"
BUCKET_LATER = "hiljem"
BUCKET_UNDATED = "teadmata"
BUCKET_PAST = "moodunud"

#: Each bucket's heading and the one sentence that says what put a row in it.
BUCKETS: tuple[tuple[str, str, str], ...] = (
    (
        BUCKET_SOON,
        f"Järgmise {SOON_DAYS} päeva jooksul",
        "Kuupäev või kogu periood jääb täielikult sellesse vahemikku.",
    ),
    (
        BUCKET_LATER,
        "Hiljem",
        "Siin on ka kuu, kvartali, poolaasta või aasta täpsusega jõustumised, mille "
        "periood ei mahu järgmise 30 päeva sisse — päeva ei ole teada.",
    ),
    (
        BUCKET_UNDATED,
        "Kuupäev teadmata",
        "Jõustub üldises korras või kuupäev on täpsustamisel.",
    ),
    (
        BUCKET_PAST,
        "Möödunud",
        "Kogu periood on läbi.",
    ),
)

WEEKDAY_LABELS = ("E", "T", "K", "N", "R", "L", "P")


def today() -> date:
    """The department's day, in Europe/Tallinn (settings.TIME_ZONE)."""
    return timezone.localdate()


def presentation_available(params: Any) -> bool:
    """Whether a Jõustumine filter is narrowing the register, so there is
    something to present.

    ``?joustumine=on`` or a window on its own (the window needs no `on` beside
    it, docs/adr/0071). ``?joustumine=puudub`` selects files with *no*
    commencement, so a commencement view of it would be empty by definition,
    and with a window beside it the two conditions contradict each other.
    """
    value = (params.get(COMMENCEMENT_PARAM) or "").strip()
    if value == FACT_ABSENT:
        return False
    windowed = any(
        (params.get(name) or "").strip()
        for name in (COMMENCEMENT_START_PARAM, COMMENCEMENT_END_PARAM)
    )
    return value == "on" or windowed


def requested_view(params: Any) -> str:
    """``VIEW_LIST``, ``VIEW_CALENDAR`` or ``""`` for the ordinary table.

    An unknown value is the ordinary table, and so is any value while no
    Jõustumine filter is active: the switch is a presentation of a
    commencement filter, never a page of its own.
    """
    if not presentation_available(params):
        return ""
    value = (params.get(VIEW_PARAM) or "").strip()
    return value if value in (VIEW_LIST, VIEW_CALENDAR) else ""


def requested_sort(params: Any) -> str:
    value = (params.get(SORT_PARAM) or "").strip()
    return value if value in SORT_CHOICES else SORT_DATE


def active_commencements(
    user: Any,
    matters: QuerySet[Any],
    window: tuple[date | None, date | None] | None,
) -> list[MatterEffectiveDate]:
    """Every active commencement this reader may see on these Matters.

    ``matters`` is the register's own filtered queryset. It is used as a
    subquery of primary keys, so its row annotations and ordering do not travel
    and a Matter is never duplicated by a join.

    ``window`` is the parsed `?joustub_alates=`/`?joustub_kuni=` pair from
    `register_filters.commencement_window`. A Matter is in the register because
    *one* of its commencements is inside the window; only the ones that are
    inside it are listed, or the list would contradict the filter above it.

    Bounded by the department's commencement records rather than the register:
    a few hundred rows at most, read once and bucketed in Python.
    """
    records = (
        MatterEffectiveDate.objects.visible_to(user)
        .filter(
            status=FactStatus.ACTIVE,
            matter__in=matters.order_by().values("pk"),
        )
        .select_related("matter", "matter__owner")
    )
    if window is not None:
        start, end = window
        if start is None and end is None:
            # A window that could not be read empties the register, and so it
            # empties this as well.
            return []
        records = records.filter(commencement_window_q(start, end))
    return list(records)


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    """One commencement, as one row. Several per Matter are several rows."""

    record: MatterEffectiveDate
    #: An approximate period that has begun and not ended: it may commence any
    #: day now, and the row says so rather than pretending to a date.
    running: bool

    @property
    def matter(self) -> Any:
        return self.record.matter

    @property
    def precision_label(self) -> str:
        """«kvartali täpsusega» under a period; nothing under a day."""
        if not self.record.is_approximate:
            return ""
        return str(DatePrecision(self.record.date_precision).label).lower()


@dataclass(frozen=True)
class Bucket:
    key: str
    label: str
    note: str
    entries: list[Entry] = field(default_factory=list)


def bucket_of(record: MatterEffectiveDate, day: date) -> str:
    """Which bucket a commencement belongs to on ``day``.

    *Möödunud* by the period's **end** — II poolaasta 2027 has not passed on
    2 July 2027 (`MatterEffectiveDate.has_passed`, the rule ADR 0071 calls
    `_direction_q`). *Järgmised* by containment — the whole period inside
    ``day`` … ``day + SOON_DAYS``, so a quarter is never there and a day is
    when it falls inside. Everything else that is dated and not over is
    *Hiljem*, which is the honest place for a period that has begun.
    """
    if record.date_value is None or record.period_end is None:
        return BUCKET_UNDATED
    if record.period_end < day:
        return BUCKET_PAST
    if record.date_value >= day and record.period_end <= day + timedelta(days=SOON_DAYS):
        return BUCKET_SOON
    return BUCKET_LATER


def _date_key(record: MatterEffectiveDate) -> tuple[date, date]:
    """The model's own ordering: period start, then narrowest first."""
    return (record.date_value or date.max, record.period_end or date.max)


def _matter_key(record: MatterEffectiveDate) -> str:
    return str(record.matter.title).casefold()


def _sorted(records: Iterable[MatterEffectiveDate], sort: str) -> list[MatterEffectiveDate]:
    rows = list(records)
    if sort == SORT_MATTER:
        return sorted(rows, key=lambda r: (_matter_key(r), _date_key(r), str(r.pk)))
    ordered = sorted(rows, key=lambda r: (_date_key(r), _matter_key(r), str(r.pk)))
    return list(reversed(ordered)) if sort == SORT_DATE_DESC else ordered


def bucketed(
    records: Iterable[MatterEffectiveDate], *, day: date, sort: str = SORT_DATE
) -> list[Bucket]:
    """The commencements in their buckets, empty buckets left out."""
    grouped: dict[str, list[MatterEffectiveDate]] = {key: [] for key, _l, _n in BUCKETS}
    for record in records:
        grouped[bucket_of(record, day)].append(record)
    buckets: list[Bucket] = []
    for key, label, note in BUCKETS:
        rows = grouped[key]
        if not rows:
            continue
        # A row with no date has nothing to order by but its file.
        order = SORT_MATTER if key == BUCKET_UNDATED else sort
        buckets.append(
            Bucket(
                key=key,
                label=label,
                note=note,
                entries=[
                    Entry(record=record, running=_is_running(record, day))
                    for record in _sorted(rows, order)
                ],
            )
        )
    return buckets


def _is_running(record: MatterEffectiveDate, day: date) -> bool:
    if record.date_value is None or record.period_end is None:
        return False
    return is_approximate(record.date_precision) and record.date_value <= day <= record.period_end


# ---------------------------------------------------------------------------
# The month calendar
# ---------------------------------------------------------------------------


def month_from(raw: str | None, window: tuple[date | None, date | None] | None, day: date) -> date:
    """The first day of the month the calendar opens on.

    ``?kuu=YYYY-MM`` when it reads; otherwise the window's first month, so a
    calendar opened on «jõustub alates 1.3.2027» opens in March 2027; otherwise
    the current month. An unreadable ``kuu`` falls back rather than emptying
    anything: it steers the presentation, never the population.
    """
    text = (raw or "").strip()
    if len(text) == 7 and text[4] == "-":
        try:
            year, month = int(text[:4]), int(text[5:])
            if 1990 <= year <= 2100 and 1 <= month <= 12:
                return date(year, month, 1)
        except ValueError:
            pass
    if window is not None:
        start, end = window
        anchor = start or end
        if anchor is not None:
            return anchor.replace(day=1)
    return day.replace(day=1)


def month_key(month: date) -> str:
    return f"{month.year:04d}-{month.month:02d}"


def shift_month(month: date, delta: int) -> date:
    index = month.year * 12 + (month.month - 1) + delta
    return date(index // 12, index % 12 + 1, 1)


def month_label(month: date) -> str:
    return f"{ESTONIAN_MONTHS[month.month - 1].capitalize()} {month.year}"


def month_end(month: date) -> date:
    return month.replace(day=calendar.monthrange(month.year, month.month)[1])


@dataclass(frozen=True)
class CalendarDay:
    day: date
    in_month: bool
    is_today: bool
    entries: list[Entry]
    #: The register narrowed to this one day, as the list (`?joustub_alates=`
    #: and `?joustub_kuni=` both set to it).
    query: str


@dataclass(frozen=True)
class MonthCalendar:
    month: date
    label: str
    weeks: list[list[CalendarDay]]
    #: Recorded to this month, day unknown. Contained in the month, so it
    #: belongs to it — but on no day.
    month_entries: list[Entry]
    #: A quarter, half-year or year that overlaps this month. Not *in* the
    #: month by the containment rule, and said so: it may commence here.
    wider_entries: list[Entry]
    #: How many commencements in the population carry no date at all.
    undated_count: int
    weekdays: tuple[str, ...] = WEEKDAY_LABELS

    @property
    def has_any(self) -> bool:
        return bool(
            self.month_entries
            or self.wider_entries
            or any(cell.entries for week in self.weeks for cell in week)
        )


def month_calendar(
    records: Iterable[MatterEffectiveDate],
    *,
    month: date,
    day: date,
    day_query: Callable[[date], str],
) -> MonthCalendar:
    """One month, Monday first, with every commencement placed honestly.

    A day — `EXACT` or `INFERRED` — goes on its cell. A `MONTH` row recorded
    for this month goes above the grid as *this month, day unknown*. A wider
    period that overlaps the month is listed beside it as a period that may
    commence here, never on a cell and never counted as the month's. Undated
    rows are counted and left to the list, where they have a bucket.
    """
    first, last = month, month_end(month)
    by_day: dict[date, list[MatterEffectiveDate]] = {}
    in_month: list[MatterEffectiveDate] = []
    wider: list[MatterEffectiveDate] = []
    undated = 0
    for record in records:
        if record.date_value is None or record.period_end is None:
            undated += 1
            continue
        if not is_approximate(record.date_precision):
            if first <= record.date_value <= last:
                by_day.setdefault(record.date_value, []).append(record)
            continue
        if record.date_value >= first and record.period_end <= last:
            in_month.append(record)
        elif record.date_value <= last and record.period_end >= first:
            wider.append(record)

    def entries(rows: list[MatterEffectiveDate]) -> list[Entry]:
        return [Entry(record=r, running=_is_running(r, day)) for r in _sorted(rows, SORT_DATE)]

    weeks = [
        [
            CalendarDay(
                day=cell,
                in_month=cell.month == month.month,
                is_today=cell == day,
                entries=entries(by_day.get(cell, [])),
                query=day_query(cell),
            )
            for cell in week
        ]
        for week in calendar.Calendar(firstweekday=0).monthdatescalendar(month.year, month.month)
    ]
    return MonthCalendar(
        month=month,
        label=month_label(month),
        weeks=weeks,
        month_entries=entries(in_month),
        wider_entries=entries(wider),
        undated_count=undated,
    )
