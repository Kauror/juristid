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
Easter — the rahvuspüha of §1 (24 February) and the eleven entries of §2's
riigipühad (with §2 p 8¹, jõululaupäev). The act calls every one of them a
puhkepäev, and §4(2) applies to the rahvuspüha whatever other law says of
riigipühad, so a date box and a calendar treat the twelve alike. It is
applied to every year a date box accepts (1990–2100, `app.workflow.dates`).
The days that were holidays under earlier versions of the law, or that were not
yet, are not modelled — the hint is about the day a person is choosing now, and
almost every such day is in the present or the near future. Shortened working
days (lühendatud tööpäevad: the day before iseseisvuspäev, võidupüha,
jõululaupäev and uusaasta) are working days and say nothing.

**Names are the statute's, lower case as running text writes them.** 24
February is «iseseisvuspäev, Eesti Vabariigi aastapäev» in the law; the hint
says «iseseisvuspäev», the first half of the same name, so the sentence stays
one line under a narrow box.

**Verified against Riigi Teataja on 2026-11-06** (UI-11). The redaction in
force is RT I, 20.12.2022, 5, in force since 30.12.2022, with no later
redaction: https://www.riigiteataja.ee/akt/120122022005 (§1 rahvuspüha,
§2 riigipühad). Its last amendment (RT I, 20.12.2022, 1) added a tähtpäev, not a
holiday. The bills of 2024–2025 that would have made ülestõusmispühade 2. püha a
riigipüha were not enacted. 24 December has been a riigipüha and puhkepäev since
RT I 2005, 24, 185 (in force 20.05.2005). 31 December is in no list: the day
before uusaasta is a shortened working day. Nelipühade 1. püha is always a
Sunday and is listed anyway, because the act lists it. No flag day (§3 riiklikud
tähtpäevad) is a holiday here. No substitute day is invented for a holiday that
falls on a weekend, because the act has none.

**The calendars read the same rules** (`rest_day`, UI-11). Kalender on Minu
asjad, on a colleague's desk, on Osakond and in the large panel colours a
Saturday, a Sunday and a holiday as a rest day and names the holiday. That is
presentation only. Nothing here moves a deadline, disables a date or feeds a
working-day count. As of this date no business calculation in this product
imports this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

from app.core.dates import WEEKDAY_NAMES

#: What the act calls 24 February (§1), and what it calls every other day here (§2).
NATIONAL_DAY = "rahvuspüha"
PUBLIC_HOLIDAY = "riigipüha"

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


def holiday_kind(day: date) -> str | None:
    """«rahvuspüha» for 24 February (§1), «riigipüha» for the rest, else ``None``."""
    if holiday_name(day) is None:
        return None
    return NATIONAL_DAY if (day.month, day.day) == (2, 24) else PUBLIC_HOLIDAY


@dataclass(frozen=True)
class RestDay:
    """What a calendar cell says about its day off: the one provider (UI-11).

    Every calendar grid asks this for each day it draws, so the four weeks, the
    month, the quarter and the panel agree on every page. It is deterministic
    and offline: the rules above, and no request per day.
    """

    day: date
    #: The statute's name of the holiday, or ``None``.
    holiday: str | None
    #: `NATIONAL_DAY`, `PUBLIC_HOLIDAY` or ``None``.
    kind: str | None

    @property
    def is_weekend(self) -> bool:
        return self.day.weekday() >= 5

    @property
    def is_holiday(self) -> bool:
        return self.holiday is not None

    @property
    def is_rest_day(self) -> bool:
        """A Saturday, a Sunday or one of the twelve: drawn in `--calendar-rest-day`."""
        return self.is_weekend or self.is_holiday

    @property
    def label(self) -> str:
        """«jaanipäev (riigipüha)», «iseseisvuspäev (rahvuspüha)», or empty on any other day.

        A weekend says nothing here. Its column heading (L, P) already says it,
        and colour is never the only carrier of a holiday's name.
        """
        if self.holiday is None:
            return ""
        return f"{self.holiday} ({self.kind})"


def rest_day(day: date) -> RestDay:
    """The day-off facts for ``day``: weekend, holiday, the holiday's name."""
    return RestDay(day=day, holiday=holiday_name(day), kind=holiday_kind(day))


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
