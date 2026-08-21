"""Period arithmetic and rendering for the shared ``DatePrecision`` vocabulary.

``DatePrecision`` says how exact a date is; this module says what that *means*
— how the value is written for a reader, and which span of days it stands for.
Both answers were already needed twice: ``NextAction`` renders an expected date
at the precision it was known to, and Stage 2G's structured Matter facts have to
sort and filter periods that are not days.

Two ideas, kept apart on purpose.

**A stored date is the first day of the period it represents.** That is an
anchor for ordering and querying, never a fact. ``01.04.2026`` stored against
``QUARTER`` means *II kvartal 2026*, and :func:`format_at_precision` is the only
supported way to write it down. Nothing may present the anchor as though
somebody had committed to that day (master specification 3.5).

**A period also has a last day.** "Is this still ahead of us?" is a question
about the *end*: II poolaasta 2027 has not passed on 2 July 2027, and an anchor
comparison would say it had. :func:`period_bounds` returns both ends, and the
Stage 2G models store both so the question stays answerable in SQL.

``INFERRED`` is a day: it records that the value was derived from free text, not
that the day is approximate. ``EXACT`` and ``INFERRED`` therefore share this
module's behaviour, exactly as ``NextAction.display_date`` already treated them.
"""

from __future__ import annotations

import calendar
from datetime import date

from app.workflow.enums import ESTONIAN_MONTHS, ROMAN_QUARTERS, DatePrecision

#: Roman numerals for the two halves of a year, indexed from zero.
ROMAN_HALVES: tuple[str, ...] = ("I", "II")

#: The range a year may be entered in. Wide enough for a 2011 register row and
#: for a transposition deadline somebody has heard about, narrow enough that a
#: typed ``20226`` is refused rather than stored (Stage-2G brief 50).
MIN_YEAR = 1990
MAX_YEAR = 2100


class InvalidPeriod(ValueError):
    """A period that cannot exist: quarter V, half-year III, year 20226."""


def _check_year(year: int) -> int:
    if not MIN_YEAR <= year <= MAX_YEAR:
        raise InvalidPeriod(f"Aasta peab olema vahemikus {MIN_YEAR}–{MAX_YEAR}.")
    return year


def exact_bounds(value: date) -> tuple[date, date]:
    return value, value


def month_bounds(year: int, month: int) -> tuple[date, date]:
    _check_year(year)
    if not 1 <= month <= 12:
        raise InvalidPeriod("Kuu peab olema vahemikus 1–12.")
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def quarter_bounds(year: int, quarter: int) -> tuple[date, date]:
    _check_year(year)
    if not 1 <= quarter <= 4:
        raise InvalidPeriod("Kvartal peab olema I, II, III või IV.")
    first_month = (quarter - 1) * 3 + 1
    start = date(year, first_month, 1)
    end_month = first_month + 2
    return start, date(year, end_month, calendar.monthrange(year, end_month)[1])


def half_year_bounds(year: int, half: int) -> tuple[date, date]:
    _check_year(year)
    if half not in (1, 2):
        raise InvalidPeriod("Poolaasta peab olema I või II.")
    if half == 1:
        return date(year, 1, 1), date(year, 6, 30)
    return date(year, 7, 1), date(year, 12, 31)


def year_bounds(year: int) -> tuple[date, date]:
    _check_year(year)
    return date(year, 1, 1), date(year, 12, 31)


def period_bounds(value: date, precision: str) -> tuple[date, date]:
    """The first and last day of the period ``value`` anchors.

    The inverse of what the forms do: given a stored anchor and its precision,
    say which days the record actually covers. Used to recompute a stored
    ``period_end`` and to assert the two never disagree.
    """
    if precision == DatePrecision.YEAR:
        return year_bounds(value.year)
    if precision == DatePrecision.HALF_YEAR:
        return half_year_bounds(value.year, 1 if value.month <= 6 else 2)
    if precision == DatePrecision.QUARTER:
        return quarter_bounds(value.year, (value.month - 1) // 3 + 1)
    if precision == DatePrecision.MONTH:
        return month_bounds(value.year, value.month)
    return exact_bounds(value)


def bounds_for(
    precision: str,
    *,
    exact_date: date | None = None,
    year: int | None = None,
    month: int | None = None,
    quarter: int | None = None,
    half: int | None = None,
) -> tuple[date, date]:
    """Turn what a person chose into the anchor and end the database stores.

    One function, so a quarter entered in the Matter page and a quarter entered
    by a future importer cannot normalise to two different anchors.
    """
    if precision in (DatePrecision.EXACT, DatePrecision.INFERRED):
        if exact_date is None:
            raise InvalidPeriod("Täpne kuupäev on puudu.")
        _check_year(exact_date.year)
        return exact_bounds(exact_date)
    if year is None:
        raise InvalidPeriod("Aasta on puudu.")
    if precision == DatePrecision.MONTH:
        if month is None:
            raise InvalidPeriod("Kuu on puudu.")
        return month_bounds(year, month)
    if precision == DatePrecision.QUARTER:
        if quarter is None:
            raise InvalidPeriod("Kvartal on puudu.")
        return quarter_bounds(year, quarter)
    if precision == DatePrecision.HALF_YEAR:
        if half is None:
            raise InvalidPeriod("Poolaasta on puudu.")
        return half_year_bounds(year, half)
    if precision == DatePrecision.YEAR:
        return year_bounds(year)
    raise InvalidPeriod(f"Tundmatu täpsus {precision!r}.")


def format_at_precision(value: date | None, precision: str) -> str:
    """Write a date the way it was actually known.

    ``01.04.2026`` stored at ``QUARTER`` precision renders as *II kvartal 2026*.
    Rendering the anchor instead would manufacture a day nobody named.
    """
    if value is None:
        return ""
    if precision == DatePrecision.YEAR:
        return str(value.year)
    if precision == DatePrecision.HALF_YEAR:
        return f"{ROMAN_HALVES[0 if value.month <= 6 else 1]} poolaasta {value.year}"
    if precision == DatePrecision.QUARTER:
        return f"{ROMAN_QUARTERS[(value.month - 1) // 3]} kvartal {value.year}"
    if precision == DatePrecision.MONTH:
        return f"{ESTONIAN_MONTHS[value.month - 1]} {value.year}"
    return value.strftime("%d.%m.%Y")


def is_approximate(precision: str) -> bool:
    return precision not in (DatePrecision.EXACT, DatePrecision.INFERRED)
