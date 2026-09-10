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


def _victory_params(year: int) -> dict[str, Any]:
    """The register narrowing that *is* this figure.

    `?toovoit=<aasta>` selects the Matters carrying a work victory recorded for
    that business period — the same population, read through the same
    `VISIBLE_VICTORY_STATUS`, that the retired Töövõidud page listed. `?olek=`
    is `koik` because a work victory does not stop being one when the file
    closes, and the register's default would otherwise hide most of them.
    """
    return {register_filters.VICTORY_PARAM: str(year), "olek": "koik"}


def _in_force_params(today: date) -> dict[str, Any]:
    """This year's commencements that have already taken effect, as a window.

    `?joustub_alates=` the first of January and `?joustub_kuni=` yesterday. The
    upper bound is yesterday rather than today because the window is a
    containment test on the whole period, so `period_end <= today - 1 day` is
    exactly `period_end < today` — which is the definition of *möödunud* this
    figure has always used (app/matters/register_filters.py).
    """
    return {
        register_filters.COMMENCEMENT_PARAM: register_filters.FACT_PRESENT,
        register_filters.COMMENCEMENT_START_PARAM: date(today.year, 1, 1).isoformat(),
        register_filters.COMMENCEMENT_END_PARAM: (today - timedelta(days=1)).isoformat(),
        "olek": "koik",
    }


def strip(results: dict[str, Any], viewer: Any, today: date, period_label: str) -> list[Figure]:
    """The five-figure strip, as far as this product can honestly fill it.

    The first three are catalogued metrics read for their value and their own
    drill-through, so the number here and the list it opens are the definition's
    own answer rather than a second one taken beside it. The fourth is the
    Matters carrying a confirmed work victory this year — a register population
    like the rail's, since the register is where a Teema is found
    (docs/adr/0071).
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
    # Counted through the register, in the register's own parameters, because
    # that is now where the list lives. It counts **Matters** carrying a work
    # victory this year rather than the victories themselves — the register
    # pages Matters, so a figure counting rows would have been a number with no
    # list behind it the first time one file won twice, and this module's whole
    # first rule is that a number opens the list it counted (docs/adr/0071).
    figures.append(
        Figure(
            _count(viewer, _victory_params(today.year), today, Matter.objects.visible_to(viewer)),
            f"teemat töövõiduga {today.year}",
            register_url(**_victory_params(today.year)),
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
    # Both counted and both linked through the register, for the reason
    # `strip` above states: the lists these two used to open are retired, so the
    # only list either number can open is a list of Matters — and a caption
    # saying "töövõite" over a count of Matters would be the disagreement this
    # module exists to prevent (docs/adr/0071).
    reporting_rows.append(
        Figure(
            _count(viewer, _victory_params(today.year), today, population),
            "teemat töövõiduga",
            register_url(**_victory_params(today.year)),
        )
    )
    reporting_rows.append(
        Figure(
            _count(viewer, _in_force_params(today), today, population),
            "teemat jõustunud aktiga",
            register_url(**_in_force_params(today)),
        )
    )

    return [
        RailBlock("Praegu", now_rows),
        RailBlock(f"Aruandlus {today.year}", reporting_rows),
    ]
