"""Estonian public holidays, and the one sentence a date box says about them.

A date somebody types or picks that falls on a Saturday, a Sunday or a
riigipüha is pointed out under the box — **pointed out, never refused**
(docs/adr/0197). A lawyer may well record a deadline that lands on jaanipäev;
what the hint catches is the slip of a finger one row down in the calendar.

**This module is the only place the rules live.** The browser does not compute
a holiday: it asks `core:public_holidays` for a year and gets this module's
answer as JSON (`static/js/app.js`, «A weekend or a public holiday is pointed
out»). Weekends it does work out itself, because a weekday is not a rule.

**Current law, for every supported year.** The list is the one in the
Pühade ja tähtpäevade seadus as it stands: twelve days, three of them tied to
Easter. It is applied to every year a date box accepts (1990–2100,
`app.workflow.dates`). The days that were holidays under earlier versions of
the law, or that were not yet, are not modelled — the hint is about the day a
person is choosing now, and almost every such day is in the present or the
near future. Shortened working days (lühendatud tööpäevad: the day before
iseseisvuspäev, võidupüha, jõululaupäev and uusaasta) are working days and say
nothing.

**Names are the statute's, lower case as running text writes them.** 24
February is «Eesti Vabariigi aastapäev, iseseisvuspäev» in the law; the hint
says «iseseisvuspäev», the shorter half of the same name, so the sentence stays
one line under a narrow box.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

from app.core.dates import WEEKDAY_NAMES

#: The fixed-date holidays, as (month, day, name).
FIXED_HOLIDAYS: tuple[tuple[int, int, str], ...] = (
    (1, 1, "uusaasta"),
    (2, 24, "iseseisvuspäev"),
    (5, 1, "kevadpüha"),
    (6, 23, "võidupüha"),
    (6, 24, "jaanipäev"),
    (8, 20, "taasiseseisvumispäev"),
    (12, 24, "jõululaupäev"),
    (12, 25, "esimene jõulupüha"),
    (12, 26, "teine jõulupüha"),
)

#: The movable holidays, as (days from Easter Sunday, name).
EASTER_HOLIDAYS: tuple[tuple[int, str], ...] = (
    (-2, "suur reede"),
    (0, "ülestõusmispühade 1. püha"),
    (49, "nelipühade 1. püha"),
)


def easter_sunday(year: int) -> date:
    """Western (Gregorian) Easter Sunday — the anonymous Gregorian algorithm
    (Meeus/Jones/Butcher), exact for every Gregorian year."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month, day = divmod(h + el - 7 * m + 114, 31)
    return date(year, month, day + 1)


@lru_cache(maxsize=256)
def _holidays(year: int) -> tuple[tuple[date, str], ...]:
    days = [(date(year, month, day), name) for month, day, name in FIXED_HOLIDAYS]
    easter = easter_sunday(year)
    days.extend((easter + timedelta(days=offset), name) for offset, name in EASTER_HOLIDAYS)
    return tuple(sorted(days))


def estonian_public_holidays(year: int) -> dict[date, str]:
    """Every riigipüha of ``year`` under current law, in date order."""
    return dict(_holidays(year))


def holiday_name(day: date) -> str | None:
    """The riigipüha ``day`` is, or ``None`` for any other day."""
    return estonian_public_holidays(day.year).get(day)


def date_notice(day: date | None) -> str | None:
    """The one informational sentence for ``day``, or ``None``.

    A holiday wins over the weekend it may fall on: «jaanipäev» says more than
    «laupäev». The date is written zero-padded, the way the sentence is read —
    as a statement about one day — rather than the way the box holds it.
    """
    if day is None:
        return None
    written = f"{day.day:02d}.{day.month:02d}.{day.year}"
    name = holiday_name(day)
    if name is not None:
        return f"{written} valitud kuupäev on riigipüha ({name})."
    if day.weekday() >= 5:
        return f"{written} valitud kuupäev on {WEEKDAY_NAMES[day.weekday()]}."
    return None
