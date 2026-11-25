"""One description per Statistika panel: what the page draws and what a download holds.

UX-008/UX-005 (overnight development 25.11.2026, docs/adr/0214 §7). Every
panel of `/statistika/` gets one :class:`ChartSpec`, built here from the
results `app.reporting.opinion_stats` has already worked out. Nothing is
counted, rounded or averaged again: a spec only *names* the numbers a result
already holds — the title, the period and filters it was taken under, its
categories, its series with their values, the units, the legend and what the
figure cannot say (coverage notes, the unfinished year, «andmed puuduvad»).

The page reads its titles, legends and notes from the spec, and the
downloads (`chart_export`) draw and tabulate the same spec, so the HTML, the
SVG/PNG and the workbook cannot disagree about a number. An unknown value is
``None`` here and stays unknown everywhere it goes — «—» on the page, a blank
cell in Excel, an empty column in the image — never a zero.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.reporting import opinion_stats as stats

Value = int | float | None

#: What each kind of panel is drawn as. A table is downloaded as Excel only.
STACKED = "stacked"  # one column per month, stacked by lawyer
PAIRED = "paired"  # two columns per month side by side
YEARS = "years"  # one column per year
SHARE = "share"  # one bar split into parts
TABLE = "table"  # rows and columns, no picture

UNKNOWN = "andmed puuduvad"


@dataclass(frozen=True)
class Series:
    """One series: a name for the legend, a value per category, and its unit."""

    name: str
    values: tuple[Value, ...]
    unit: str = ""
    #: The page's colour slot (`statseries--<slot>` / `statband--<n>`).
    slot: str = "0"
    #: The text the page prints for each value; «—» where it is unknown.
    texts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChartSpec:
    key: str
    title: str
    kind: str
    #: The page's element id for the panel; a link to the figure ends in it.
    anchor: str
    #: The span the figures were taken over, in words.
    period: str
    categories: tuple[str, ...]
    series: tuple[Series, ...]
    #: Longer names of the categories («mai 2026» for «mai 26»).
    category_titles: tuple[str, ...] = ()
    #: Per category: the year is still running.
    partial: tuple[bool, ...] = ()
    #: A stacked chart's column totals (the result's own `total`).
    totals: tuple[Value, ...] = ()
    #: The column heading for the categories in a table or a workbook.
    category_heading: str = ""
    #: The filters the figures were taken under: (label, value).
    filters: tuple[tuple[str, str], ...] = ()
    #: What the figure does not say or counts in a particular way.
    notes: tuple[str, ...] = ()
    #: The page shows «andmed puuduvad» instead of a picture.
    empty: bool = False

    @property
    def is_table(self) -> bool:
        return self.kind == TABLE

    @property
    def titles(self) -> tuple[str, ...]:
        return self.category_titles or self.categories

    def rows(self) -> list[tuple[str, list[Value]]]:
        """One row per category: its long name and every series' value."""
        return [
            (self.titles[index], [series.values[index] for series in self.series])
            for index in range(len(self.categories))
        ]

    def filter_line(self) -> str:
        return " · ".join(f"{label}: {value}" for label, value in self.filters)


def _period_text(period: stats.Period) -> str:
    return period.span if period.label == period.span else f"{period.label} ({period.span})"


def period_filters(period: stats.Period, jurist: str) -> tuple[tuple[str, str], ...]:
    return (("Periood", _period_text(period)), ("Jurist", jurist or "Kõik juristid"))


def _text(value: Value, suffix: str = "") -> str:
    return "—" if value is None else f"{value}{suffix}"


def _clipped(period: stats.Period, bars: Sequence[Any]) -> tuple[str, ...]:
    """A note when the first or the last month holds only part of the month."""
    if not bars:
        return ()
    last = bars[-1].end
    if bars[0].start.day != 1 or last.day != calendar.monthrange(last.year, last.month)[1]:
        return (f"Esimene ja viimane kuu on arvestatud perioodi piires ({period.span}).",)
    return ()


def monthly(
    bars: Sequence[stats.MonthBar],
    owners: Sequence[str],
    period: stats.Period,
    jurist: str,
    *,
    sent: int,
) -> ChartSpec:
    """«Välja saadetud arvamused kuude lõikes»: `opinion_stats.monthly`, stacked by lawyer."""
    series = []
    for index, owner in enumerate(owners):
        counts = []
        for bar in bars:
            counts.append(next((s.count for s in bar.segments if s.owner == owner), 0))
        series.append(
            Series(owner, tuple(counts), "arvamust", str(index % 8), tuple(map(str, counts)))
        )
    return ChartSpec(
        key="kuud",
        title="Välja saadetud arvamused kuude lõikes",
        kind=STACKED,
        anchor="stat-kuud",
        period=_period_text(period),
        categories=tuple(bar.label for bar in bars),
        category_titles=tuple(bar.title for bar in bars),
        series=tuple(series),
        totals=tuple(bar.total for bar in bars),
        category_heading="Kuu",
        filters=period_filters(period, jurist),
        notes=(
            "Arvamus on loetud välja saadetuks päeval, mil see välja läks; jurist on teema"
            " vastutaja.",
            *_clipped(period, bars),
        ),
        empty=not sent,
    )


def flow(bars: Sequence[stats.FlowBar], period: stats.Period, jurist: str) -> ChartSpec:
    """«Sisse tulnud ja välja saadetud kuude lõikes»: `opinion_stats.flow`."""
    received = tuple(bar.received for bar in bars)
    sent = tuple(bar.sent for bar in bars)
    return ChartSpec(
        key="voog",
        title="Sisse tulnud ja välja saadetud kuude lõikes",
        kind=PAIRED,
        anchor="stat-voog",
        period=_period_text(period),
        categories=tuple(bar.label for bar in bars),
        category_titles=tuple(bar.title for bar in bars),
        series=(
            Series("Sisse tulnud teemad", received, "teemat", "muted", tuple(map(str, received))),
            Series("Välja saadetud arvamused", sent, "arvamust", "0", tuple(map(str, sent))),
        ),
        category_heading="Kuu",
        filters=period_filters(period, jurist),
        notes=("Sisse tulnud teema on loetud saabumise kuupäeva järgi.",),
    )


#: The three groupings of the period's opinions (ADR 0215).
GROUPS = {
    "liigid": ("Õigusakti liigi järgi", "Liik", "stat-liigid"),
    "valdkonnad": ("Valdkonna järgi", "Valdkond", "stat-valdkonnad"),
    "saatjad": ("Saatja järgi", "Saatja", "stat-saatjad"),
}


def group(
    key: str, rows: Sequence[stats.InstrumentRow], period: stats.Period, jurist: str
) -> ChartSpec:
    """One of the three tables: `opinion_stats.by_instrument` / `by_area` / `by_sender`."""
    title, heading, anchor = GROUPS[key]
    sent = tuple(row.sent for row in rows)
    share = tuple(row.share for row in rows)
    on_time = tuple(row.on_time_share for row in rows)
    days = tuple(row.days_given for row in rows)
    received = tuple(row.received for row in rows)
    notes = ["«Tähtajaks» ja «Aega vastamiseks» puuduvad, kui rühmas pole tähtajaga arvamust."]
    if key != "liigid":
        notes.insert(
            0,
            "Teema võib kuuluda mitmesse rühma, nii et osakaalud võivad kokku anda üle 100%."
            " Arvesse on võetud ainult kinnitatud väärtused.",
        )
    return ChartSpec(
        key=key,
        title=title,
        kind=TABLE,
        anchor=anchor,
        period=_period_text(period),
        categories=tuple(row.label for row in rows),
        series=(
            Series("Välja saadetud", sent, "arvamust", texts=tuple(map(str, sent))),
            Series("Osakaal", share, "%", texts=tuple(_text(v, "%") for v in share)),
            Series("Tähtajaks", on_time, "%", texts=tuple(_text(v, "%") for v in on_time)),
            Series("Aega vastamiseks", days, "päeva", texts=tuple(_text(v, " p") for v in days)),
            Series("Sisse tulnud", received, "teemat", texts=tuple(map(str, received))),
        ),
        category_heading=heading,
        filters=period_filters(period, jurist),
        notes=tuple(notes),
        empty=not rows,
    )


def pressure(found: stats.Pressure, period: stats.Period, jurist: str) -> ChartSpec:
    """«Kui palju aega vastamiseks anti»: `opinion_stats.pressure`."""
    counts = tuple(band.count for band in found.bands)
    shares = tuple(band.share for band in found.bands)
    notes = [f"{found.total} perioodil saabunud tähtajaga teemat."]
    if found.without:
        notes.append(
            f"{found.without} perioodil saabunud teemal puudub tähtaeg; need ei ole arvestatud."
        )
    return ChartSpec(
        key="aeg",
        title="Kui palju aega vastamiseks anti",
        kind=SHARE,
        anchor="stat-aeg",
        period=_period_text(period),
        categories=tuple(band.label for band in found.bands),
        series=(
            Series("Teemad", counts, "teemat", texts=tuple(map(str, counts))),
            Series("Osakaal", shares, "%", texts=tuple(f"{v}%" for v in shares)),
        ),
        category_heading="Aega vastamiseks",
        filters=period_filters(period, jurist),
        notes=tuple(notes),
        empty=not found.total,
    )


def decisions(found: stats.Decisions, period: stats.Period, jurist: str) -> ChartSpec:
    """«Saadetud ja saatmata jäetud»: `opinion_stats.decisions`."""
    counts = (found.sent, found.declined)
    shares: tuple[Value, Value] = (found.sent_share, None)
    return ChartSpec(
        key="otsus",
        title="Saadetud ja saatmata jäetud",
        kind=SHARE,
        anchor="stat-otsus",
        period=_period_text(period),
        categories=("Arvamus saadetud", "Otsustati mitte saata"),
        series=(
            Series("Teemad", counts, "teemat", texts=tuple(map(str, counts))),
            Series("Osakaal", shares, "%", texts=(_text(found.sent_share, "%"), "")),
        ),
        category_heading="Otsus",
        filters=period_filters(period, jurist),
        empty=not found.total,
    )


#: The unit of each long-term trend.
TREND_UNITS = {"sent": "arvamust", "days": "päeva", "ontime": "%", "eu": "%", "koda": "teemat"}

TREND_NOTES = {
    "sent": "Arvamus on arvestatud aasta järgi, millal see välja läks.",
    "days": "Mediaan: arvamuse tähtaeg miinus teema saabumise kuupäev, teema aasta järgi.",
    "ontime": "Tähtajaga välja saadetud arvamused, teema aasta järgi.",
    "eu": "Teemad, mille õigusakt on ELi õigusakt, teema aasta järgi.",
    "koda": "Koja algatatud teemad, teema aasta järgi.",
}


def trend(found: stats.Trend, span: str, jurist: str) -> ChartSpec:
    """One long-term trend: `opinion_stats.trends`, one column per year."""
    unknown = [str(bar.year) for bar in found.bars if bar.value is None]
    notes = [TREND_NOTES.get(found.key, "")]
    if any(bar.partial for bar in found.bars):
        notes.append("Jooksev aasta on pooleli.")
    if unknown:
        notes.append(f"Andmed puuduvad: {', '.join(unknown)}.")
    return ChartSpec(
        key=f"trend-{found.key}",
        title=found.title,
        kind=YEARS,
        anchor=f"trend-{found.key}",
        period=span,
        categories=tuple(str(bar.year) for bar in found.bars),
        category_titles=tuple(str(bar.year) for bar in found.bars),
        series=(
            Series(
                found.title,
                tuple(bar.value for bar in found.bars),
                TREND_UNITS.get(found.key, ""),
                texts=tuple(bar.text for bar in found.bars),
            ),
        ),
        partial=tuple(bar.partial for bar in found.bars),
        category_heading="Aasta",
        filters=(("Aastad", span), ("Jurist", jurist or "Kõik juristid")),
        notes=tuple(note for note in notes if note),
        empty=not found.has_data,
    )
