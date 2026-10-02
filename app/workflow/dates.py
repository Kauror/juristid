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

from app.core.dates import format_estonian_date
from app.workflow.enums import ESTONIAN_MONTHS, ROMAN_QUARTERS, DatePrecision

#: The month, quarter and half-year a period control offers, in Estonian. One
#: copy for both precision-date forms (`app.matters.forms`,
#: `app.intelligence.forms`), built from the same vocabulary `format_at_precision`
#: prints, so a period is chosen in the words it is later shown in.
MONTH_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (str(number), name.capitalize()) for number, name in enumerate(ESTONIAN_MONTHS, start=1)
)
QUARTER_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (str(number), f"{numeral} kvartal") for number, numeral in enumerate(ROMAN_QUARTERS, start=1)
)
HALF_CHOICES: tuple[tuple[str, str], ...] = (("1", "I poolaasta"), ("2", "II poolaasta"))

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


def year_from(raw: str | None) -> int | None:
    """A supported year read from untrusted text, or ``None``.

    The range this module already owns, applied to the other direction: forms
    reach it through ``_check_year`` and refuse with a sentence, while a value
    arriving in a query string has no form to refuse it and every surface that
    reads one was checking only that it looked like a number.

    That gap was reachable and it was a crash rather than a wrong answer.
    ``?aasta=99999`` and ``?periood=0`` reached ``date(year, 1, 1)`` and raised
    ``ValueError``; ``?periood=9999`` reached ``date(end_year + 1, 1, 1)`` and
    raised on the *year after* a year that is itself in range; a twenty-digit
    value raised ``OverflowError``. Six parameters across seven surfaces, each
    one a 500 on a page somebody had hand-edited the URL of (CORR-02).

    **The caller decides what absence means**, which is why this returns
    ``None`` rather than raising or substituting. The surfaces disagree on that
    deliberately — Statistika falls back to its default period, Jälgimine drops
    the filter and shows everything, the register empties the list, and the
    Arvamused workspace refuses with a sentence — and those are four settled
    answers to "what should a nonsense filter do here", not an inconsistency to
    flatten under one rule.

    ``isascii()`` as well as ``isdigit()``, because ``"²".isdigit()`` is ``True``
    and ``int("²")`` raises: the check that looks like it covers this is exactly
    the one that does not.
    """
    value = (raw or "").strip()
    if not (value.isascii() and value.isdigit()):
        return None
    year = int(value)
    return year if MIN_YEAR <= year <= MAX_YEAR else None


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


def period_starts_after(value: date | None, precision: str, *, day: date) -> bool:
    """Does the **whole** period ``value`` anchors lie after ``day``?

    The question a surface asks when it has to know that something definitely has
    not happened yet. It is the mirror of the lateness rule in docs/adr/0079 §4 —
    that one asks whether a period has wholly *ended*, this one whether it has
    wholly *begun* — and it is stated here, once, for the same reason: four
    copies of an anchor comparison is how the same period came to sort into two
    places.

    **A broad period is not evidence of the future.** *september 2026* read on 18
    September covers today, and *2026* covers most of the year behind it; neither
    tells anybody that the thing it describes is still to come. Only a period
    whose first day is later than ``day`` does, so that is the comparison — and
    it is made through :func:`period_bounds` rather than against the stored
    number, so a row whose anchor was not normalised is still read as the period
    it stands for.

    An unknown date is not in the future either. It is unknown, which is a fact
    the product keeps rather than resolves (docs/adr/0079 §2), so ``None``
    answers ``False``.
    """
    if value is None:
        return False
    start, _end = period_bounds(value, precision)
    return start > day


def period_in_window(
    value: date | None, precision: str, *, start: date, end: date | None, today: date
) -> bool:
    """Does a date recorded at ``precision`` belong in the window ``start``–``end``?

    **The one rule every deadline window reads** (docs/adr/0122 §2) — Minu
    asjad's bands, the register's `?too=tahtaeg-*` populations, Osakond's
    *Eesolev*, the strip figures that count them. Before it there were two: Minu
    asjad banded a period as the period it is (docs/adr/0121 §7) and every other
    window compared the stored anchor, so «oktoober 2026» was *Hiljem* on one page
    and *Homme* on another the day before October began.

    **A day** — ``EXACT`` or ``INFERRED`` — is in the window when it falls inside
    it, both ends inclusive; ``end=None`` means "and everything after".

    **A period is not a day, so a window with a last day never holds one.** A
    window bounded in days — *Täna*, *Sel nädalal*, *Järgmine nädal*, *30 päeva
    jooksul* — is a claim about which days, and neither end of «oktoober 2026»
    is a day anybody named. Containment was considered and refused: it is
    exactly the representative-day reading in another place, because whether a
    month fits inside a window depends on where the window happens to start,
    and «november 2026» would be *30 päeva jooksul* on the Ülevaade strip on
    1 November while the same row is *Hiljem* in Minu asjad.

    **A window with no last day is the later category**, the one every set of
    windows ends in and the one that never implied a day. It holds every period
    that has not ended — so a period is in exactly one of a set of consecutive
    windows that begins today, as a day is, instead of in none while it runs —
    and, when the window opens in the past, every period that reaches it.

    **A period is behind us only once its last day is**: one that has ended is
    in no window beginning today, and is overdue or ripe by the lateness rule
    instead (`app.workflow.lateness`, docs/adr/0079 §4).

    ``None`` is unknown, which is in no window.
    """
    if value is None:
        return False
    if not is_approximate(precision):
        return start <= value and (end is None or value <= end)
    if end is not None:
        return False
    _first, last = period_bounds(value, precision)
    return last >= min(start, today)


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
    return format_estonian_date(value)


def is_approximate(precision: str) -> bool:
    return precision not in (DatePrecision.EXACT, DatePrecision.INFERRED)
