"""The period bar of Minu statistika: which days a figure counts, and the ones before it.

docs/adr/0223 §11. Pure: a query string and today in, a :class:`Period` out. The
state lives in the URL (``?periood=nadal&algus=2026-11-23``), so a link, Back
and a refresh keep it; anything the bar does not understand falls back to the
current week.

* **Nädal** — Monday to Sunday; **Kuu**, **Kvartal**, **Aasta** — calendar ones;
  **Muu periood** — two dates (``algus``, ``kuni``), both inclusive.
* ``‹`` and ``›`` step one period; ``›`` is offered only while the next period
  has begun. A period never reaches past today: its counts run to ``until``.
* The comparison is the **previous equal period, to the same point**: this week
  so far (Monday to Wednesday) against last week's Monday to Wednesday; a whole
  past period against the whole one before it.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlencode

WEEK, MONTH, QUARTER, YEAR, CUSTOM = "nadal", "kuu", "kvartal", "aasta", "muu"
KINDS: tuple[tuple[str, str], ...] = (
    (WEEK, "Nädal"),
    (MONTH, "Kuu"),
    (QUARTER, "Kvartal"),
    (YEAR, "Aasta"),
    (CUSTOM, "Muu periood"),
)
#: The earliest day the bar accepts; Juristid's register starts in 2011.
FIRST_DAY = date(2011, 1, 1)
#: A custom period is shown by day up to this many days, by week up to `WEEKS_UP_TO`.
DAYS_UP_TO = 31
WEEKS_UP_TO = 92

MONTHS_LONG = (
    "jaanuar",
    "veebruar",
    "märts",
    "aprill",
    "mai",
    "juuni",
    "juuli",
    "august",
    "september",
    "oktoober",
    "november",
    "detsember",
)
MONTHS_SHORT = (
    "jaan",
    "veebr",
    "märts",
    "apr",
    "mai",
    "juuni",
    "juuli",
    "aug",
    "sept",
    "okt",
    "nov",
    "dets",
)
WEEKDAY_LETTERS = ("E", "T", "K", "N", "R", "L", "P")
ROMAN = ("I", "II", "III", "IV")


@dataclass(frozen=True)
class Span:
    """Days from ``start`` to ``end``, both inclusive."""

    start: date
    end: date

    def __contains__(self, day: object) -> bool:
        return isinstance(day, date) and self.start <= day <= self.end

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def text(self) -> str:
        return f"{self.start:%d.%m.%Y}–{self.end:%d.%m.%Y}"


@dataclass(frozen=True)
class Bucket:
    """One column of the chart: a day, a week or a month of the period."""

    span: Span
    #: Above the date: a weekday letter, «48. n» or the month's short name.
    label: str
    #: Under it: «23.11», or the year for a month.
    sublabel: str
    #: The column lies wholly after today: drawn empty, never as a 0.
    future: bool


@dataclass(frozen=True)
class Period:
    kind: str
    #: The whole calendar period (a week runs to Sunday even on a Wednesday).
    start: date
    end: date
    today: date

    @property
    def until(self) -> date:
        """The last day counted: the period's end, or today while it runs."""
        return min(self.end, self.today)

    @property
    def counted(self) -> Span:
        return Span(self.start, self.until)

    @property
    def is_current(self) -> bool:
        return self.start <= self.today <= self.end

    @property
    def running(self) -> bool:
        return self.until < self.end

    # -- labels ------------------------------------------------------------------

    @property
    def label(self) -> str:
        if self.kind == WEEK:
            this_week = _monday(self.today)
            if self.start == this_week:
                return "See nädal"
            if self.start == this_week - timedelta(days=7):
                return "Eelmine nädal"
            return f"{self.start.isocalendar()[1]}. nädal {self.start.isocalendar()[0]}"
        if self.kind == MONTH:
            return f"{MONTHS_LONG[self.start.month - 1].capitalize()} {self.start.year}"
        if self.kind == QUARTER:
            return f"{ROMAN[(self.start.month - 1) // 3]} kvartal {self.start.year}"
        if self.kind == YEAR:
            return str(self.start.year)
        return "Valitud periood"

    @property
    def range_text(self) -> str:
        return Span(self.start, self.end).text

    @property
    def comparison_word(self) -> str:
        """The tail of «↑3 võrreldes eelmise nädalaga»."""
        return {
            WEEK: "eelmise nädalaga",
            MONTH: "eelmise kuuga",
            QUARTER: "eelmise kvartaliga",
            YEAR: "eelmise aastaga",
        }.get(self.kind, "eelmise perioodiga")

    # -- neighbours --------------------------------------------------------------

    def shifted(self, steps: int) -> Period:
        """The period ``steps`` before (negative) or after (positive) this one."""
        if self.kind == WEEK:
            start = self.start + timedelta(days=7 * steps)
            return Period(WEEK, start, start + timedelta(days=6), self.today)
        if self.kind in (MONTH, QUARTER, YEAR):
            months = {MONTH: 1, QUARTER: 3, YEAR: 12}[self.kind] * steps
            start = _add_months(self.start, months)
            return _calendar(self.kind, start, self.today)
        length = self.end - self.start + timedelta(days=1)
        return Period(CUSTOM, self.start + length * steps, self.end + length * steps, self.today)

    @property
    def previous(self) -> Period:
        return self.shifted(-1)

    @property
    def comparison(self) -> Span:
        """The previous equal period, to the same point as this one has run."""
        before = self.previous
        elapsed = self.until - self.start
        return Span(before.start, min(before.end, before.start + elapsed))

    @property
    def has_next(self) -> bool:
        return self.shifted(1).start <= self.today

    @property
    def has_previous(self) -> bool:
        return self.shifted(-1).end >= FIRST_DAY

    def query(self, **extra: str) -> dict[str, str]:
        params = {"periood": self.kind, "algus": self.start.isoformat()}
        if self.kind == CUSTOM:
            params["kuni"] = self.end.isoformat()
        params.update({key: value for key, value in extra.items() if value})
        return params

    def url(self, path: str, **extra: str) -> str:
        return f"{path}?{urlencode(self.query(**extra))}"

    # -- the chart ---------------------------------------------------------------

    @property
    def grain(self) -> str:
        """``day``, ``week`` or ``month``: what one column of the chart is."""
        if self.kind in (WEEK, MONTH):
            return "day"
        if self.kind == QUARTER:
            return "week"
        if self.kind == YEAR:
            return "month"
        days = (self.end - self.start).days + 1
        if days <= DAYS_UP_TO:
            return "day"
        return "week" if days <= WEEKS_UP_TO else "month"

    def buckets(self) -> list[Bucket]:
        out: list[Bucket] = []
        cursor = self.start
        while cursor <= self.end:
            if self.grain == "day":
                last = cursor
                label = WEEKDAY_LETTERS[cursor.weekday()]
                sub = f"{cursor:%d.%m}"
            elif self.grain == "week":
                last = min(_monday(cursor) + timedelta(days=6), self.end)
                label = f"{cursor.isocalendar()[1]}. n"
                sub = f"{cursor:%d.%m}"
            else:
                last = min(_month_end(cursor), self.end)
                label = MONTHS_SHORT[cursor.month - 1]
                sub = str(cursor.year) if cursor.month == 1 or cursor == self.start else ""
            out.append(Bucket(Span(cursor, last), label, sub, cursor > self.today))
            cursor = last + timedelta(days=1)
        return out


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _calendar(kind: str, day: date, today: date) -> Period:
    """The calendar ``kind`` period holding ``day``."""
    if kind == WEEK:
        start = _monday(day)
        return Period(WEEK, start, start + timedelta(days=6), today)
    if kind == MONTH:
        start = day.replace(day=1)
        return Period(MONTH, start, _month_end(start), today)
    if kind == QUARTER:
        start = date(day.year, (day.month - 1) // 3 * 3 + 1, 1)
        return Period(QUARTER, start, _month_end(_add_months(start, 2)), today)
    return Period(YEAR, date(day.year, 1, 1), date(day.year, 12, 31), today)


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def current(today: date, kind: str = WEEK) -> Period:
    return _calendar(kind if kind in (WEEK, MONTH, QUARTER, YEAR) else WEEK, today, today)


def parse(params: Any, today: date) -> Period:
    """The period a query string asks for; the current week when it asks for nothing usable.

    A start in the future, or before `FIRST_DAY`, is the current period of that
    kind. A custom period is ordered, cut at today and at `FIRST_DAY`.
    """
    kind = str(params.get("periood") or WEEK)
    start = _date(params.get("algus"))
    if kind == CUSTOM:
        end = _date(params.get("kuni")) or today
        start = start or end
        if start > end:
            start, end = end, start
        start, end = max(start, FIRST_DAY), min(end, today)
        if start > end:
            return current(today)
        return Period(CUSTOM, start, end, today)
    if kind not in (WEEK, MONTH, QUARTER, YEAR):
        return current(today)
    if start is None or start > today or start < FIRST_DAY:
        return current(today, kind)
    return _calendar(kind, start, today)
