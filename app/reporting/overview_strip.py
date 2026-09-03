"""The Statistika overview's strip and rail: counts, and the lists behind them.

The v2 design replaced the overview's charts with tables and put two columns of
plain counts beside them (02-EKRAANID §E). Nothing here computes a new
*statistic*: every figure is either an existing catalogued metric read for its
value and its own drill-through, or a register population expressed in the
register's own query parameters — the same mechanism Ülevaade and Osakonna töö
already count with.

Two rules this module exists to keep.

**Every number opens the list it counted.** Where this product cannot express a
population as a list, the number is not printed at all. That is why the strip
here is shorter than the prototype's (01-EHITUSJUHIS §3.3,
docs/design-v2-compatibility.md DS-19).

**Authorization runs before arithmetic.** Every count is taken through
``visible_to(viewer)`` — the register populations through
``register_population``, the catalogued metrics through their own selectors, the
tracking counts through ``MatterFact.objects.visible_to`` — so a reader without
an entitlement sees a smaller number and never a placeholder.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.urls import reverse

from app.intelligence import selectors as intelligence
from app.intelligence.enums import WorkVictoryStatus
from app.matters import register_filters, selectors, work_items
from app.matters.department_dashboard import register_url
from app.matters.enums import RecordMode
from app.matters.models import Matter

#: How far «tähtaeg 30 p jooksul» reaches.
DEADLINE_WINDOW_DAYS = 30


@dataclass(frozen=True)
class Figure:
    """One number, its caption, and the list it opens."""

    value: int
    caption: str
    url: str
    tone: str = ""


@dataclass(frozen=True)
class RailBlock:
    """One column block: a heading and its rows, each row a count and a link."""

    label: str
    rows: list[Figure]


def _open_full() -> dict[str, Any]:
    return {"olek": "avatud", "liik": RecordMode.FULL.value}


def _count(viewer: Any, params: dict[str, Any], today: date, population: Any) -> int:
    return register_filters.register_population(
        viewer, params, today=today, population=population
    ).count()


def _confirmed_victories(viewer: Any, year: int) -> int:
    return (
        intelligence.work_victories(user=viewer, status=WorkVictoryStatus.CONFIRMED)
        .filter(period_date__year=year)
        .count()
    )


def _victories_url(year: int) -> str:
    # `?aasta=` only: the Töövõidud page has no state filter, so a `?staatus=`
    # here would name a parameter nothing reads.
    return f"{reverse('intelligence:work_victories')}?aasta={year}"


def _in_force(viewer: Any, today: date) -> int:
    return (
        intelligence.effective_dates(user=viewer, today=today, direction=intelligence.PAST)
        .filter(date_value__year=today.year)
        .count()
    )


def strip(results: dict[str, Any], viewer: Any, today: date, period_label: str) -> list[Figure]:
    """The five-figure strip, as far as this product can honestly fill it.

    The first three are catalogued metrics read for their value and their own
    drill-through, so the number here and the list it opens are the definition's
    own answer rather than a second one taken beside it. The fourth is the
    confirmed work victories of the current year, which live in
    ``app.intelligence`` and have a list of their own.
    """
    figures: list[Figure] = []
    for key, caption in (
        ("ACTIVE_FULL_MATTERS", "avatud teemat"),
        ("NEW_NATIVE_FULL_MATTERS", f"teemat {period_label}"),
        ("SUBMISSIONS_SENT", f"arvamust välja {period_label}"),
    ):
        result = results.get(key)
        if result is None:
            continue
        figures.append(Figure(result.value, caption, result.drillthrough_url))
    figures.append(
        Figure(
            _confirmed_victories(viewer, today.year),
            f"töövõitu {today.year}",
            _victories_url(today.year),
        )
    )
    return figures


def rail(viewer: Any, today: date, results: dict[str, Any]) -> list[RailBlock]:
    """Praegu, and this year's reporting totals.

    *Praegu* is five register populations — the same five Ülevaade counts, in
    the same order and through the same parameters, so a reader moving between
    the two pages does not find one number saying two things.
    """
    population = Matter.objects.visible_to(viewer)
    window_end = today + timedelta(days=DEADLINE_WINDOW_DAYS)

    now_rows: list[Figure] = []
    for params, caption, tone in (
        (_open_full(), "avatud teemat", ""),
        ({**_open_full(), "too": work_items.WORK_OVERDUE}, "üle tähtaja", "danger"),
        (
            {
                **_open_full(),
                "too": work_items.WORK_DEADLINE_WINDOW,
                "too_alates": today.isoformat(),
                "too_kuni": window_end.isoformat(),
            },
            f"tähtaeg {DEADLINE_WINDOW_DAYS} p jooksul",
            "",
        ),
        ({**_open_full(), "vastutaja": selectors.MISSING}, "vastutajata", "warning"),
        ({**_open_full(), "tegevus": selectors.MISSING}, "järgmise tegevuseta", "warning"),
    ):
        now_rows.append(
            Figure(_count(viewer, params, today, population), caption, register_url(**params), tone)
        )

    reporting_rows: list[Figure] = []
    sent = results.get("SUBMISSIONS_SENT")
    if sent is not None:
        reporting_rows.append(Figure(sent.value, "arvamusi välja", sent.drillthrough_url))
    reporting_rows.append(
        Figure(
            _confirmed_victories(viewer, today.year),
            "töövõite",
            _victories_url(today.year),
        )
    )
    reporting_rows.append(
        Figure(
            _in_force(viewer, today),
            "jõustunud akte",
            f"{reverse('intelligence:effective_dates')}"
            f"?suund={intelligence.PAST}&aasta={today.year}",
        )
    )

    return [
        RailBlock("Praegu", now_rows),
        RailBlock(f"Aruandlus {today.year}", reporting_rows),
    ]
