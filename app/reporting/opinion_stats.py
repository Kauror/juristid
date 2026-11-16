"""What Statistika's first page says about Koda's opinions (owner, 2026-11-16).

Pure arithmetic over :mod:`app.reporting.opinions` — the one reader of sent
opinions — and :class:`~app.reporting.opinions.MatterFacts`. No query is made
here, so every figure on the page comes from the same two lists and cannot
disagree with another.

The definitions are the ones the department already reads in its register's
statistics, restated on Juristid's own records:

* **A sent opinion** is counted on the day it went out (`sent_on`), whatever
  year its Teema carries.
* **The period** is rolling months ending today, a calendar year, or two
  dates; all inclusive. Its comparison is the span just before it (3 and 6
  months, two dates) or the same dates a year earlier (12 months, a year).
* **The long-term trends** group by the Teema's own year (its reference):
  days given to answer (deadline − arrival, every Teema that has both, sent or
  not), the share sent by the deadline (sent opinions with a deadline), the
  share of Teemad on an EU instrument, and Koda's own initiatives.

Nobody is ranked: a lawyer's row is a count of what went out under their name,
in the order the page gives every breakdown (largest first).
"""

from __future__ import annotations

import calendar
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import mean, median
from typing import Any

from app.reporting.opinions import MatterFacts, OpinionFact

#: The first year of the long-term trends: the register's first year.
FIRST_YEAR = 2011

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

ROLLING = {"3": 3, "6": 6, "12": 12}
DEFAULT_PERIOD = "12"
ROLLING_LABELS = {"3": "Viimased 3 kuud", "6": "Viimased 6 kuud", "12": "Viimased 12 kuud"}


def _shift_months(day: date, months: int) -> date:
    """``day`` moved by whole months, the day clamped to the month's length."""
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def _shift_year(day: date, years: int) -> date:
    return _shift_months(day, 12 * years)


def fmt(day: date) -> str:
    return f"{day.day:02d}.{day.month:02d}.{day.year}"


@dataclass(frozen=True)
class Period:
    key: str
    start: date
    end: date
    label: str
    previous_start: date
    previous_end: date
    comparison_label: str
    year: int | None = None

    @property
    def span(self) -> str:
        return f"{fmt(self.start)}–{fmt(self.end)}"

    @property
    def previous_span(self) -> str:
        return f"{fmt(self.previous_start)}–{fmt(self.previous_end)}"

    def holds(self, day: date) -> bool:
        return self.start <= day <= self.end

    def held_before(self, day: date) -> bool:
        return self.previous_start <= day <= self.previous_end


def rolling_period(key: str, today: date) -> Period:
    months = ROLLING[key]
    start = _shift_months(today, -months) + timedelta(days=1)
    if months == 12:
        previous = (_shift_year(start, -1), _shift_year(today, -1))
        comparison = "Võrdlus sama perioodiga aasta varem"
    else:
        previous_end = start - timedelta(days=1)
        previous = (_shift_months(previous_end, -months) + timedelta(days=1), previous_end)
        comparison = f"Võrdlus vahetult eelnenud {months} kuuga"
    return Period(key, start, today, ROLLING_LABELS[key], previous[0], previous[1], comparison)


def year_period(year: int, today: date) -> Period:
    """A calendar year; the current year ends today, compared with the same days a year earlier."""
    end = today if year >= today.year else date(year, 12, 31)
    start = date(year, 1, 1)
    return Period(
        "aasta",
        start,
        end,
        str(year),
        _shift_year(start, -1),
        _shift_year(end, -1),
        "Võrdlus sama perioodiga aasta varem",
        year=year,
    )


def custom_period(start: date, end: date) -> Period:
    days = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    return Period(
        "vali",
        start,
        end,
        f"{fmt(start)}–{fmt(end)}",
        previous_end - timedelta(days=days - 1),
        previous_end,
        "Võrdlus vahetult eelnenud sama pika perioodiga",
    )


def parse_period(params: Any, today: date) -> Period:
    """The period a request names; anything malformed is the default (12 months)."""
    key = str(params.get("periood", "") or "")
    if key in ROLLING:
        return rolling_period(key, today)
    if key == "aasta":
        try:
            year = int(str(params.get("aasta", "")))
        except ValueError:
            year = 0
        if FIRST_YEAR <= year <= today.year:
            return year_period(year, today)
    if key == "vali":
        try:
            start = date.fromisoformat(str(params.get("alates", "")))
            end = date.fromisoformat(str(params.get("kuni", "")))
        except ValueError:
            start = end = None  # type: ignore[assignment]
        if start and end and date(FIRST_YEAR, 1, 1) <= start <= end <= today:
            return custom_period(start, end)
    return rolling_period(DEFAULT_PERIOD, today)


# ---------------------------------------------------------------------------
# The period's figures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OwnerRow:
    owner: str
    count: int
    previous: int = 0

    @property
    def diff(self) -> int:
        return self.count - self.previous


def _by_owner(counts: Counter[str], previous: Counter[str] | None = None) -> list[OwnerRow]:
    rows = [
        OwnerRow(owner, count, (previous or Counter())[owner])
        for owner, count in counts.items()
        if count > 0
    ]
    return sorted(rows, key=lambda row: (-row.count, row.owner.casefold()))


@dataclass(frozen=True)
class Summary:
    """The four headline figures, each beside the comparison period's."""

    sent: int
    sent_before: int
    owners: list[OwnerRow]
    received: int
    received_before: int
    on_time: int
    with_deadline: int
    on_time_before: int
    with_deadline_before: int
    days_given: float | None
    days_given_before: float | None

    @property
    def sent_diff(self) -> int:
        return self.sent - self.sent_before

    @property
    def received_diff(self) -> int:
        return self.received - self.received_before

    @property
    def on_time_share(self) -> int | None:
        return round(100 * self.on_time / self.with_deadline) if self.with_deadline else None

    @property
    def on_time_share_before(self) -> int | None:
        if not self.with_deadline_before:
            return None
        return round(100 * self.on_time_before / self.with_deadline_before)

    @property
    def on_time_diff(self) -> int | None:
        now, before = self.on_time_share, self.on_time_share_before
        return None if now is None or before is None else now - before


def _days_given(matters: list[MatterFacts], deadlines: dict[Any, date]) -> list[int]:
    days = []
    for matter in matters:
        deadline = deadlines.get(matter.matter_id)
        if deadline is None or matter.received is None:
            continue
        given = (deadline - matter.received).days
        if given >= 0:
            days.append(given)
    return days


def summary(
    facts: list[OpinionFact],
    matters: list[MatterFacts],
    deadlines: dict[Any, date],
    period: Period,
) -> Summary:
    now = [fact for fact in facts if period.holds(fact.sent_on)]
    before = [fact for fact in facts if period.held_before(fact.sent_on)]
    arrived = [m for m in matters if m.received and period.holds(m.received)]
    arrived_before = [m for m in matters if m.received and period.held_before(m.received)]
    given = _days_given(arrived, deadlines)
    given_before = _days_given(arrived_before, deadlines)
    return Summary(
        sent=len(now),
        sent_before=len(before),
        owners=_by_owner(
            Counter(fact.matter.owner for fact in now),
            Counter(fact.matter.owner for fact in before),
        ),
        received=len(arrived),
        received_before=len(arrived_before),
        on_time=sum(1 for fact in now if fact.on_time),
        with_deadline=sum(1 for fact in now if fact.on_time is not None),
        on_time_before=sum(1 for fact in before if fact.on_time),
        with_deadline_before=sum(1 for fact in before if fact.on_time is not None),
        days_given=median(given) if given else None,
        days_given_before=median(given_before) if given_before else None,
    )


@dataclass(frozen=True)
class Segment:
    owner: str
    count: int
    height: float
    slot: int


@dataclass(frozen=True)
class MonthBar:
    label: str
    title: str
    start: date
    end: date
    total: int
    height: float
    segments: list[Segment] = field(default_factory=list)


def monthly(facts: list[OpinionFact], period: Period) -> tuple[list[MonthBar], list[str]]:
    """One bar per calendar month of the period, stacked by lawyer; and the lawyers, in order."""
    counts: dict[tuple[int, int], Counter[str]] = {}
    for fact in facts:
        if period.holds(fact.sent_on):
            counts.setdefault((fact.sent_on.year, fact.sent_on.month), Counter())[
                fact.matter.owner
            ] += 1
    owners = sorted({owner for month in counts.values() for owner in month}, key=str.casefold)
    slots = {owner: index % 8 for index, owner in enumerate(owners)}
    top = max((sum(month.values()) for month in counts.values()), default=0)
    bars = []
    cursor = date(period.start.year, period.start.month, 1)
    while cursor <= period.end:
        last = date(cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1])
        month = counts.get((cursor.year, cursor.month), Counter())
        total = sum(month.values())
        bars.append(
            MonthBar(
                label=f"{MONTHS_SHORT[cursor.month - 1]} {cursor.year % 100:02d}",
                title=f"{MONTHS_LONG[cursor.month - 1]} {cursor.year}",
                start=max(cursor, period.start),
                end=min(last, period.end),
                total=total,
                height=round(100 * total / top, 2) if top else 0.0,
                segments=[
                    Segment(owner, month[owner], round(100 * month[owner] / total, 2), slots[owner])
                    for owner in owners
                    if month[owner]
                ],
            )
        )
        cursor = last + timedelta(days=1)
    return bars, owners


# ---------------------------------------------------------------------------
# The long-term trends, by year
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class YearBar:
    year: int
    value: float | None
    text: str
    title: str
    height: float
    partial: bool = False


@dataclass(frozen=True)
class Trend:
    key: str
    title: str
    bars: list[YearBar]
    note: str = ""

    @property
    def has_data(self) -> bool:
        return any(bar.value is not None for bar in self.bars)


def _bars(
    key: str,
    title: str,
    years: list[int],
    values: dict[int, float | None],
    texts: dict[int, str],
    titles: dict[int, str],
    today: date,
    *,
    ceiling: float | None = None,
    note: str = "",
) -> Trend:
    top = ceiling or max((v for v in values.values() if v is not None), default=0) or 1
    return Trend(
        key,
        title,
        [
            YearBar(
                year,
                values.get(year),
                texts.get(year, "—"),
                titles.get(year, f"{year}: andmed puuduvad"),
                round(100 * (values.get(year) or 0) / top, 2),
                partial=year == today.year,
            )
            for year in years
        ],
        note,
    )


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def trends(
    facts: list[OpinionFact],
    matters: list[MatterFacts],
    deadlines: dict[Any, date],
    *,
    first: int,
    last: int,
    today: date,
) -> list[Trend]:
    years = list(range(first, last + 1))
    by_year: dict[int, list[MatterFacts]] = {year: [] for year in years}
    for matter in matters:
        if matter.year in by_year:
            by_year[matter.year].append(matter)
    sent_by_year = Counter(fact.sent_on.year for fact in facts)
    facts_by_topic_year: dict[int, list[OpinionFact]] = {year: [] for year in years}
    for fact in facts:
        if fact.matter.year in facts_by_topic_year:
            facts_by_topic_year[fact.matter.year].append(fact)

    sent: dict[int, float | None] = {year: sent_by_year.get(year, 0) for year in years}
    sent_titles = {
        year: f"{year}: {_plural(int(sent[year] or 0), 'arvamus', 'arvamust')}" for year in years
    }

    given_values: dict[int, float | None] = {}
    given_texts: dict[int, str] = {}
    given_titles: dict[int, str] = {}
    on_time_values: dict[int, float | None] = {}
    on_time_texts: dict[int, str] = {}
    on_time_titles: dict[int, str] = {}
    eu_values: dict[int, float | None] = {}
    eu_texts: dict[int, str] = {}
    eu_titles: dict[int, str] = {}
    koda_values: dict[int, float | None] = {}
    koda_titles: dict[int, str] = {}
    for year in years:
        topics = by_year[year]
        days = _days_given(topics, deadlines)
        if days:
            given_values[year] = median(days)
            given_texts[year] = str(round(median(days)))
            given_titles[year] = (
                f"{year}: mediaan {round(median(days))} päeva, keskmine {round(mean(days))} päeva"
                f" · {_plural(len(days), 'teema', 'teemat')}"
            )
        else:
            given_values[year] = None
        dated = [fact for fact in facts_by_topic_year[year] if fact.on_time is not None]
        if dated:
            on_time = sum(1 for fact in dated if fact.on_time)
            on_time_values[year] = 100 * on_time / len(dated)
            on_time_texts[year] = f"{round(on_time_values[year] or 0)}%"
            on_time_titles[year] = (
                f"{year}: tähtaegselt {round(on_time_values[year] or 0)}%"
                f" · {on_time}/{len(dated)} arvamusest"
            )
        else:
            on_time_values[year] = None
        if topics:
            eu = sum(1 for matter in topics if matter.is_eu)
            eu_values[year] = 100 * eu / len(topics)
            eu_texts[year] = f"{round(eu_values[year] or 0)}%"
            eu_titles[year] = (
                f"{year}: ELiga seotud {round(eu_values[year] or 0)}%"
                f" · {eu} / {len(topics)} teemast"
            )
            koda = sum(1 for matter in topics if matter.koda_initiative)
            koda_values[year] = koda
            koda_titles[year] = (
                f"{year}: Koja omaalgatused {koda}"
                f" · {round(100 * koda / len(topics))}% kõigist teemadest ({koda} / {len(topics)})"
            )
        else:
            eu_values[year] = None
            koda_values[year] = None

    return [
        _bars(
            "sent",
            "Välja saadetud arvamused aastate lõikes",
            years,
            sent,
            {year: str(int(sent[year] or 0)) for year in years},
            sent_titles,
            today,
        ),
        _bars(
            "days",
            "Arvamuse esitamiseks antud aeg päevades",
            years,
            given_values,
            given_texts,
            given_titles,
            today,
            note="mediaan",
        ),
        _bars(
            "ontime",
            "Tähtaegselt esitatud arvamuste osakaal",
            years,
            on_time_values,
            on_time_texts,
            on_time_titles,
            today,
            ceiling=100,
        ),
        _bars(
            "eu",
            "Otseselt ELiga seotud teemade osakaal",
            years,
            eu_values,
            eu_texts,
            eu_titles,
            today,
        ),
        _bars(
            "koda",
            "Koja omaalgatused aastate lõikes",
            years,
            koda_values,
            {year: str(int(v)) for year, v in koda_values.items() if v is not None},
            koda_titles,
            today,
        ),
    ]


# ---------------------------------------------------------------------------
# Rekordid
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Record:
    title: str
    value: str
    when: str


def _best(
    values: dict[Any, float | None], *, lowest: bool = False
) -> tuple[float, list[Any]] | None:
    present = {key: value for key, value in values.items() if value is not None}
    if not present:
        return None
    best = min(present.values()) if lowest else max(present.values())
    return best, [key for key, value in present.items() if value == best]


def _when(keys: list[Any]) -> str:
    shown = ", ".join(str(key) for key in keys[:3])
    return shown if len(keys) <= 3 else f"{shown} +{len(keys) - 3}"


def records(facts: list[OpinionFact], found: list[Trend], today: date) -> list[Record]:
    """The six records, over the same years the trends show; a tie names every year."""
    by_key = {trend.key: trend for trend in found}
    out: list[Record] = []

    def values(key: str, *, complete: bool) -> dict[int, float | None]:
        return {
            bar.year: bar.value
            for bar in by_key[key].bars
            if bar.value and (not complete or bar.year < today.year)
        }

    best = _best(values("sent", complete=False))
    if best:
        out.append(
            Record(
                "Välja saadetud arvamuste rekordaasta",
                _plural(int(best[0]), "arvamus", "arvamust"),
                _when(best[1]),
            )
        )
    years = {bar.year for bar in by_key["sent"].bars}
    months = Counter(
        (fact.sent_on.year, fact.sent_on.month)
        for fact in facts
        if fact.sent_on.year in years
        and (fact.sent_on.year, fact.sent_on.month) < (today.year, today.month)
    )
    best_month = _best({key: float(count) for key, count in months.items()})
    if best_month:
        labels = [f"{MONTHS_LONG[month - 1]} {year}" for year, month in sorted(best_month[1])]
        out.append(
            Record(
                "Välja saadetud arvamuste rekordkuu",
                _plural(int(best_month[0]), "arvamus", "arvamust"),
                _when(labels),
            )
        )
    best = _best(values("ontime", complete=True))
    if best:
        out.append(
            Record(
                "Suurima tähtaegselt esitatud arvamuste osakaaluga aasta",
                f"{best[0]:.1f}%".replace(".", ","),
                _when(best[1]),
            )
        )
    best = _best(values("days", complete=True), lowest=True)
    if best:
        out.append(
            Record(
                "Lühima arvamuse esitamiseks antud tähtajaga aasta",
                f"{round(best[0])} päeva",
                _when(best[1]),
            )
        )
    best = _best(values("koda", complete=False))
    if best:
        out.append(Record("Koja omaalgatuste rekordaasta", str(int(best[0])), _when(best[1])))
    best = _best(values("eu", complete=False))
    if best:
        out.append(
            Record(
                "ELiga otseselt seotud teemade rekordaasta", f"{round(best[0])}%", _when(best[1])
            )
        )
    return out
