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

And the corollary of the first rule, for the catalogued half: **a metric the
catalogue has declined is not printed here either.** ``SUBMISSIONS_SENT`` is
``minimum_population=1`` precisely so that nobody reads "0 arvamust välja" as
the claim that Koda sent none; a strip that printed ``result.value`` regardless
of ``result.status`` made exactly that claim on a page whose Tegevus tab was
saying "Ebapiisavad andmed" about the same metric, over the same period, one
click away.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.matters import register_filters, selectors, work_items
from app.matters.department_dashboard import register_url
from app.matters.enums import RecordMode
from app.matters.models import Matter
from app.reporting.context import ReportingContext
from app.reporting.selectors.base import REGISTER_DATA_SCOPE, chip_params, merge_chip_params

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


def _scoped(params: dict[str, Any]) -> dict[str, Any]:
    """A register narrowing over real data only, counted and linked as one.

    Every other population on Statistika is real data (`reporting_population`),
    but these were counted and linked in the register's own default, which
    includes TEST Matters — so «avatud teemat» here and the same caption in the
    strip disagreed whenever a TEST Matter was open. The scope goes into the
    *params* rather than only into the URL: `_count` reads the same dict the
    link is built from, so the figure and its list cannot be scoped apart
    (`REGISTER_DATA_SCOPE`, app/reporting/selectors/base.py).
    """
    return {**params, "andmed": REGISTER_DATA_SCOPE}


def _chipped(context: ReportingContext | None, params: dict[str, Any]) -> dict[str, Any] | None:
    """``params`` narrowed by the page's filter chips, or ``None`` if they cannot meet.

    The strip and the rail count register populations of their own, and used to
    count them department-wide whatever the filter bar said: under «Vastutaja:
    Sandra» the strip printed «avatud teemat 3» and the rail «avatud teemat 6»
    (QA-STATS-02). Owner decision D2: a visible filter affects every figure it
    applies to. Every Matter chip applies to these populations; the *period*
    does not, because each row states its own window («Praegu», «Aruandlus
    <aasta>»), so it is deliberately not part of `chip_params`.

    The chips go into the params rather than only into the URL, for the reason
    `_scoped` gives: `_count` reads the dict the link is built from. ``None``
    means a chip contradicts the row's own narrowing — «Kirje liik: Arhiiv»
    against «avatud teemat», which is FULL only — and the honest figure is 0
    with no list behind it (QA-STATS-08).
    """
    if context is None:
        return params
    if context.owner_unreadable:
        return None
    return merge_chip_params(chip_params(context), params)


def _figure(
    context: ReportingContext | None,
    params: dict[str, Any],
    caption: str,
    count: Any,
    tone: str = "",
) -> Figure:
    """One register-population figure: counted and linked from the same params."""
    chipped = _chipped(context, params)
    if chipped is None:
        return Figure(0, caption, "", tone)
    return Figure(count(chipped), caption, register_url(**chipped), tone)


def _open_full() -> dict[str, Any]:
    return _scoped({"olek": "avatud", "liik": RecordMode.FULL.value})


def _count(
    viewer: Any,
    params: dict[str, Any],
    today: date,
    population: Any,
    shared_items: list[Any] | None = None,
) -> int:
    return register_filters.register_population(
        viewer, params, today=today, population=population, shared_items=shared_items
    ).count()


def _victory_params(year: int) -> dict[str, Any]:
    """The register narrowing that *is* this figure.

    `?toovoit=<aasta>` selects the Matters carrying a work victory recorded for
    that business period — the same population, read through the same
    `VISIBLE_VICTORY_STATUS`, that the retired Töövõidud page listed. `?olek=`
    is `koik` because a work victory does not stop being one when the file
    closes, and the register's default would otherwise hide most of them.
    """
    return _scoped({register_filters.VICTORY_PARAM: str(year), "olek": "koik"})


def _in_force_params(today: date) -> dict[str, Any]:
    """This year's commencements that have already taken effect, as a window.

    `?joustub_alates=` the first of January and `?joustub_kuni=` yesterday. The
    upper bound is yesterday rather than today because the window is a
    containment test on the whole period, so `period_end <= today - 1 day` is
    exactly `period_end < today` — which is the definition of *möödunud* this
    figure has always used (app/matters/register_filters.py).
    """
    return _scoped(
        {
            register_filters.COMMENCEMENT_PARAM: register_filters.FACT_PRESENT,
            register_filters.COMMENCEMENT_START_PARAM: date(today.year, 1, 1).isoformat(),
            register_filters.COMMENCEMENT_END_PARAM: (today - timedelta(days=1)).isoformat(),
            "olek": "koik",
        }
    )


def strip(
    results: dict[str, Any],
    viewer: Any,
    today: date,
    period_label: str,
    *,
    context: ReportingContext | None = None,
) -> list[Figure]:
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
        if result is None or not result.has_value:
            continue
        figures.append(Figure(result.value, caption, result.drillthrough_url))
    # Counted through the register, in the register's own parameters, because
    # that is now where the list lives. It counts **Matters** carrying a work
    # victory this year rather than the victories themselves — the register
    # pages Matters, so a figure counting rows would have been a number with no
    # list behind it the first time one file won twice, and this module's whole
    # first rule is that a number opens the list it counted (docs/adr/0071).
    #
    # Narrowed by the page's chips like the three catalogued figures beside it
    # (`_chipped`, QA-STATS-02); its window is the year its caption names.
    population = Matter.objects.visible_to(viewer)
    figures.append(
        _figure(
            context,
            _victory_params(today.year),
            f"teemat töövõiduga {today.year}",
            lambda params: _count(viewer, params, today, population),
        )
    )
    return figures


def rail(
    viewer: Any,
    today: date,
    results: dict[str, Any],
    *,
    context: ReportingContext | None = None,
) -> list[RailBlock]:
    """Praegu, and this year's reporting totals.

    ``results["SUBMISSIONS_SENT"]`` must be computed over **this calendar
    year**, whatever period the page shows: it sits under the heading
    «Aruandlus <this year>» beside two rows that count this year, and printing
    the page period's figure there made «kõik aastad» read as this year's sends
    (QA-STATS-01). ``context`` is the page's filter state; its chips narrow
    every row (`_chipped`, QA-STATS-02).

    *Praegu* is five register populations — the same five Ülevaade counts, in
    the same order and through the same parameters, so a reader moving between
    the two pages does not find one number saying two things. The one
    difference is ``andmed=paris`` (`_scoped`): Ülevaade is an operational page
    and shows TEST Matters, Statistika publishes real data only.
    """
    population = Matter.objects.visible_to(viewer)
    window_end = today + timedelta(days=DEADLINE_WINDOW_DAYS)
    # **One read of the work model for the strip** (AUD-003). The two `?too=`
    # rows each read it again through `filter_by_work_state` — two full reads
    # of every open Matter's dated work per Statistika load, for one list. The
    # department page has handed its read down since PERF-02; this is the same
    # list for the same reader on the same day, so no number changes.
    #
    # **Bounded at the window's end** (2026-10-17 audit, JPERF-005). The two rows
    # that read it are «üle tähtaja» and «tähtaeg 30 p jooksul»: an overdue item
    # is dated on or before today, and the window holds days up to
    # `window_end`, so nothing dated after `window_end` can enter either count.
    # Reading the decade of later milestones only to discard them was most of
    # this page's time at 10,000 open Matters.
    items = work_items.work_items(viewer, today=today, latest=window_end)

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
            _figure(
                context,
                params,
                caption,
                lambda chipped: _count(viewer, chipped, today, population, shared_items=items),
                tone,
            )
        )

    reporting_rows: list[Figure] = []
    sent = results.get("SUBMISSIONS_SENT")
    if sent is not None and sent.has_value:
        reporting_rows.append(Figure(sent.value, "arvamusi välja", sent.drillthrough_url))
    # Official «no comments» answers, counted beside the opinions rather than in
    # them (docs/adr/0174), and drawn only when there is one: a zero would be a
    # row about a kind most years never use.
    if context is not None:
        answered = _no_objection_answers(context, today)
        if answered is not None:
            reporting_rows.append(answered)
    # Both counted and both linked through the register, for the reason
    # `strip` above states: the lists these two used to open are retired, so the
    # only list either number can open is a list of Matters — and a caption
    # saying "töövõite" over a count of Matters would be the disagreement this
    # module exists to prevent (docs/adr/0071).
    for params, caption in (
        (_victory_params(today.year), "teemat töövõiduga"),
        (_in_force_params(today), "teemat jõustunud aktiga"),
    ):
        reporting_rows.append(
            _figure(
                context,
                params,
                caption,
                lambda chipped: _count(viewer, chipped, today, population),
            )
        )

    return [
        RailBlock("Praegu", now_rows),
        RailBlock(f"Aruandlus {today.year}", reporting_rows),
    ]


def _no_objection_answers(context: ReportingContext, today: date) -> Figure | None:
    """This year's official «no comments» answers, or ``None`` when there were none.

    The same population and list as «arvamusi välja» — `sent_submissions` over
    the heading's year, every chip kept — narrowed to the kind by name, so the
    figure and the list it opens count the same letters.
    """
    from app.reporting.context import PARAM_PERIOD
    from app.reporting.selectors.submissions import _submission_url, sent_submissions
    from app.submissions.enums import SubmissionKind

    kind = SubmissionKind.NO_OBJECTION_RESPONSE.value
    value = sent_submissions(context, year=today.year, kind=kind).count()
    if not value:
        return None
    url = _submission_url(context, arvamus=kind, **{PARAM_PERIOD: str(today.year)})
    return Figure(value, "vastust märkusteta", url)
