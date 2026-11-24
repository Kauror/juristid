"""Statistika's first page: Koda's opinions, from Juristid's own records (owner, 2026-11-16).

`/statistika/` — the period's four figures beside the comparison period's,
what went out month by month, the long-term trends from 2011 and the records;
`/statistika/arvamused-valja/` — the opinions any of those figures counted.

Both read `app.reporting.opinions` (the one reader of sent opinions) and
`app.reporting.opinion_stats` (the arithmetic), for the Teemad the signed-in
reader may see and nobody else's. Every control is a link or a GET form: the
period, the lawyer and the trend range live in the URL.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from app.audit.access import record_export
from app.core.http import content_disposition
from app.core.xlsx_safety import workbook_response
from app.reporting import chart_spec, chart_svg, chart_workbook, classification, opinions
from app.reporting import opinion_stats as stats

TREND_SPANS = {"5": "Viimased 5 aastat", "10": "Viimased 10 aastat", "koik": "Alates 2011"}
DEFAULT_TREND = "koik"


def _period_query(period: stats.Period) -> dict[str, str]:
    if period.key == "aasta":
        return {"periood": "aasta", "aasta": str(period.year)}
    if period.key == "vali":
        return {
            "periood": "vali",
            "alates": period.start.isoformat(),
            "kuni": period.end.isoformat(),
        }
    return {} if period.key == stats.DEFAULT_PERIOD else {"periood": period.key}


def _url(name: str, **params: str) -> str:
    query = urlencode({key: value for key, value in params.items() if value})
    return reverse(name) + (f"?{query}" if query else "")


def _load(request: HttpRequest) -> tuple[list[Any], dict[Any, Any], dict[Any, date], list[str]]:
    """The reader's facts, narrowed to one lawyer when the URL names one."""
    known = opinions.matter_facts(request.user)
    owners = sorted({matter.owner for matter in known.values()}, key=str.casefold)
    chosen = str(request.GET.get("jurist", "") or "")
    if chosen in owners:
        known = {key: matter for key, matter in known.items() if matter.owner == chosen}
    facts = opinions.opinion_facts(request.user, known)
    return facts, known, opinions.matter_deadlines(request.user, known), owners


@dataclass(frozen=True)
class Figures:
    """Everything the page draws, worked out once for one request's URL state.

    The page and every download of one of its panels start here, so they read
    the same results for the same URL (UX-005/UX-008, docs/adr/0214 §7).
    """

    today: date
    period: stats.Period
    jurist: str
    owners: list[str]
    trend_key: str
    first_year: int
    last_year: int
    summary: stats.Summary
    bars: list[stats.MonthBar]
    bar_owners: list[str]
    flow: list[stats.FlowBar]
    instruments: list[stats.InstrumentRow]
    areas: list[stats.InstrumentRow]
    senders: list[stats.InstrumentRow]
    pressure: stats.Pressure
    decisions: stats.Decisions
    trends: list[stats.Trend]
    records: list[stats.Record]

    @property
    def trend_span(self) -> str:
        return f"{self.first_year}–{self.last_year}"

    @property
    def carried(self) -> dict[str, str]:
        """The period and the lawyer, as the URL names them."""
        return {**_period_query(self.period), "jurist": self.jurist}

    @property
    def trend_carried(self) -> dict[str, str]:
        return {"trend": "" if self.trend_key == DEFAULT_TREND else self.trend_key}


def figures(request: HttpRequest) -> Figures:
    """The page's results for this request: the reader's Teemad, the URL's period and lawyer."""
    today = timezone.localdate()
    period = stats.parse_period(request.GET, today)
    facts, known, deadlines, owners = _load(request)
    matters = list(known.values())
    jurist = str(request.GET.get("jurist", "") or "")
    jurist = jurist if jurist in owners else ""
    trend_key = str(request.GET.get("trend", "") or "")
    trend_key = trend_key if trend_key in TREND_SPANS else DEFAULT_TREND
    last_year = today.year
    first_year = {
        "5": max(stats.FIRST_YEAR, last_year - 4),
        "10": max(stats.FIRST_YEAR, last_year - 9),
        "koik": stats.FIRST_YEAR,
    }[trend_key]
    bars, bar_owners = stats.monthly(facts, period)
    found = stats.trends(facts, matters, deadlines, first=first_year, last=last_year, today=today)
    declined = opinions.declined_facts(request.user, known)
    return Figures(
        today=today,
        period=period,
        jurist=jurist,
        owners=owners,
        trend_key=trend_key,
        first_year=first_year,
        last_year=last_year,
        summary=stats.summary(facts, matters, deadlines, period),
        bars=bars,
        bar_owners=bar_owners,
        flow=stats.flow(facts, matters, period),
        instruments=stats.by_instrument(facts, matters, deadlines, period),
        areas=stats.by_area(facts, matters, deadlines, period),
        senders=stats.by_sender(facts, matters, deadlines, period),
        pressure=stats.pressure(matters, deadlines, period),
        decisions=stats.decisions(facts, declined, period),
        trends=found,
        records=stats.records(facts, found, today),
    )


def panels(found: Figures) -> dict[str, chart_spec.ChartSpec]:
    """Every panel's chart spec, by key, in the page's order (docs/adr/0214 §7)."""
    period, jurist = found.period, found.jurist
    specs = [
        chart_spec.monthly(found.bars, found.bar_owners, period, jurist, sent=found.summary.sent),
        chart_spec.flow(found.flow, period, jurist),
        chart_spec.group("liigid", found.instruments, period, jurist),
        chart_spec.group("valdkonnad", found.areas, period, jurist),
        chart_spec.group("saatjad", found.senders, period, jurist),
        chart_spec.pressure(found.pressure, period, jurist),
        chart_spec.decisions(found.decisions, period, jurist),
        *(chart_spec.trend(trend, found.trend_span, jurist) for trend in found.trends),
    ]
    return {spec.key: spec for spec in specs}


def record_links(found: Figures) -> list[dict[str, Any]]:
    """Each record with a link to every period that holds it (UX-008).

    The record year of sent opinions opens that calendar year on this page (its
    months, and the same count in the tile); the record month opens the
    opinions it counted, as that month's column does; a record read from a
    trend opens the trend with that year's column marked. The lawyer and the
    trend range travel with every link. A tie links each of its periods: none
    is chosen for the reader.
    """
    out = []
    for record in found.records:
        links = []
        for held in record.periods:
            if record.source == "month" and held.month:
                last = calendar.monthrange(held.year, held.month)[1]
                url = _url(
                    "reporting:opinion_rows",
                    periood="vali",
                    alates=date(held.year, held.month, 1).isoformat(),
                    kuni=date(held.year, held.month, last).isoformat(),
                    jurist=found.jurist,
                )
            elif record.source == "sent":
                url = (
                    _url(
                        "reporting:dashboard",
                        periood="aasta",
                        aasta=str(held.year),
                        jurist=found.jurist,
                        **found.trend_carried,
                    )
                    + "#stat-kuud"
                )
            else:
                url = (
                    _url("reporting:dashboard", **found.carried, **found.trend_carried)
                    + f"#trend-{record.source}-{held.year}"
                )
            links.append({"label": held.label, "url": url})
        out.append({"record": record, "links": links, "shown": links[:3], "rest": links[3:]})
    return out


@login_required
@require_GET
def dashboard(request: HttpRequest) -> HttpResponse:
    from app.reporting.views import TABS

    found = figures(request)
    period, jurist, today = found.period, found.jurist, found.today
    carried, trend_carried = found.carried, found.trend_carried
    summary = found.summary
    specs = panels(found)

    def rows_url(**extra: str) -> str:
        return _url("reporting:opinion_rows", **{**carried, **extra})

    return render(
        request,
        "reporting/dashboard.html",
        {
            "tabs": TABS,
            "active_tab": "arvamused",
            "nav_active": "statistika",
            "period": period,
            "summary": summary,
            # Each panel's title, legend and notes, shared with its downloads.
            "charts": specs,
            # Every download link carries the page's own state (UX-005).
            "chart_query": chart_query(found),
            "chart_stems": {key: _file_name(spec, found, "")[:-1] for key, spec in specs.items()},
            "rows_url": rows_url(),
            "owner_rows": [
                {"row": row, "url": rows_url(jurist=row.owner)} for row in summary.owners
            ],
            "bars": [
                {
                    "bar": bar,
                    "url": _url(
                        "reporting:opinion_rows",
                        periood="vali",
                        alates=bar.start.isoformat(),
                        kuni=bar.end.isoformat(),
                        jurist=jurist,
                    ),
                }
                for bar in found.bars
            ],
            # A closer look at the period (phase 2).
            "flow": found.flow,
            "instruments": [
                {"row": row, "url": rows_url(liik=row.key) if row.key and row.sent else ""}
                for row in found.instruments
            ],
            # Who sends, and about what (phase 3, ADR 0215): confirmed fields only.
            "areas": [
                {"row": row, "url": rows_url(valdkond=row.key) if row.key and row.sent else ""}
                for row in found.areas
            ],
            "senders": [
                {"row": row, "url": rows_url(saatja=row.key) if row.key and row.sent else ""}
                for row in found.senders
            ],
            "pending_classification": classification.pending_for(request.user).count(),
            "pressure": found.pressure,
            "decisions": found.decisions,
            "trends": found.trends,
            "trend_items": [
                {
                    "trend": trend,
                    "chart": specs[f"trend-{trend.key}"],
                    "stem": _file_name(specs[f"trend-{trend.key}"], found, "")[:-1],
                }
                for trend in found.trends
            ],
            "records": found.records,
            "record_items": record_links(found),
            "period_links": [
                {
                    "label": label,
                    "active": period.key == key,
                    "url": _url(
                        "reporting:dashboard",
                        periood="" if key == stats.DEFAULT_PERIOD else key,
                        jurist=jurist,
                        **trend_carried,
                    ),
                }
                for key, label in stats.ROLLING_LABELS.items()
            ],
            "year_links": [
                {
                    "label": str(year),
                    "active": period.key == "aasta" and period.year == year,
                    "url": _url(
                        "reporting:dashboard",
                        periood="aasta",
                        aasta=str(year),
                        jurist=jurist,
                        **trend_carried,
                    ),
                }
                for year in range(found.last_year, stats.FIRST_YEAR - 1, -1)
            ],
            "jurist_links": [
                {
                    "label": owner or "Kõik juristid",
                    "active": jurist == owner,
                    "url": _url(
                        "reporting:dashboard",
                        **_period_query(period),
                        jurist=owner,
                        **trend_carried,
                    ),
                }
                for owner in ["", *found.owners]
            ],
            "jurist": jurist,
            "trend_links": [
                {
                    "label": label,
                    "active": found.trend_key == key,
                    "url": _url(
                        "reporting:dashboard",
                        **carried,
                        trend="" if key == DEFAULT_TREND else key,
                    )
                    + "#trendid",
                }
                for key, label in TREND_SPANS.items()
            ],
            "trend": found.trend_key if found.trend_key != DEFAULT_TREND else "",
            "trend_span": found.trend_span,
            "today": today,
            "first_day": date(stats.FIRST_YEAR, 1, 1),
        },
    )


#: The URL parameters a panel download reads: the page's own state, the
#: background of the image and whether the browser is turning it into a PNG.
CHART_PARAMS = ("periood", "aasta", "alates", "kuni", "jurist", "trend", "taust", "kuju")


def chart_query(found: Figures) -> str:
    """The page's URL state, as every download link of its panels carries it."""
    return urlencode(
        {key: value for key, value in {**found.carried, **found.trend_carried}.items() if value}
    )


def _file_name(spec: chart_spec.ChartSpec, found: Figures, extension: str) -> str:
    period = found.period
    if spec.kind == chart_spec.YEARS:
        span = found.trend_span.replace("–", "-")
    elif period.key == "aasta":
        span = str(period.year)
    else:
        span = f"{period.start:%Y%m%d}-{period.end:%Y%m%d}"
    return f"statistika-{spec.key}-{span}.{extension}"


@login_required
@require_GET
def chart_download(request: HttpRequest, key: str, fmt: str) -> HttpResponse:
    """One panel of the page as SVG (PNG is drawn from it in the browser) or Excel (UX-005).

    The same results as the page for the same URL — `figures` over the reader's
    own Teemad — so a file never holds more than the page shows. Every file
    handed over is on the export trail (`record_export`); a table panel has no
    picture and its SVG is a 404.
    """
    found = figures(request)
    spec = panels(found).get(key)
    if spec is None or (fmt == "svg" and spec.is_table):
        raise Http404
    now = timezone.localtime()
    if fmt == "xlsx":
        response = workbook_response(
            chart_workbook.workbook(spec, now), _file_name(spec, found, "xlsx")
        )
        file_format = "xlsx"
    else:
        png = request.GET.get("kuju") == "png"
        svg = chart_svg.render(
            spec, transparent=request.GET.get("taust") == "puudub", generated=now.date()
        )
        response = HttpResponse(svg, content_type="image/svg+xml; charset=utf-8")
        response["Content-Disposition"] = content_disposition(
            "attachment", _file_name(spec, found, "svg")
        )
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        # Opened on its own, the file runs nothing and loads nothing.
        response["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
        file_format = "png" if png else "svg"
    record_export(
        request,
        export=f"reporting.chart.{spec.key}",
        file_format=file_format,
        rows=len(spec.categories),
        params=request.GET,
        honoured=CHART_PARAMS,
    )
    return response


#: The opinions list's columns, in order, and how each one sorts.
SORT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("saadetud", "Saadetud"),
    ("teema", "Teema"),
    ("liik", "Liik"),
    ("tahtaeg", "Tähtaeg"),
    ("jurist", "Jurist"),
)
SORT_KEYS: dict[str, Any] = {
    "saadetud": lambda fact: (fact.sent_on, fact.matter.reference),
    "teema": lambda fact: fact.matter.title.casefold(),
    "liik": lambda fact: (fact.matter.instrument.casefold(), fact.sent_on),
    # A row with no deadline sorts before every dated one, either way round.
    "tahtaeg": lambda fact: (fact.deadline is not None, fact.deadline or date.min),
    "jurist": lambda fact: (fact.matter.owner.casefold(), fact.sent_on),
}


@login_required
@require_GET
def opinion_rows(request: HttpRequest) -> HttpResponse:
    """The opinions a figure counted: sent in the period, by the lawyer the URL names."""
    from app.reporting.views import TABS

    today = timezone.localdate()
    period = stats.parse_period(request.GET, today)
    facts, _known, _deadlines, owners = _load(request)
    jurist = str(request.GET.get("jurist", "") or "")
    jurist = jurist if jurist in owners else ""
    kinds = {m.instrument_key: m.instrument for m in _known.values() if m.instrument_key}
    liik = str(request.GET.get("liik", "") or "")
    liik = liik if liik in kinds else ""
    areas = {key: name for m in _known.values() for key, name in m.areas}
    valdkond = str(request.GET.get("valdkond", "") or "")
    valdkond = valdkond if valdkond in areas else ""
    senders = {key: name for m in _known.values() for key, name in m.senders}
    saatja = str(request.GET.get("saatja", "") or "")
    saatja = saatja if saatja in senders else ""
    # The Teema's current owner by id (Seis's owner chip, docs/adr/0214 §6):
    # exact where «jurist» is a name label.
    vastutaja = str(request.GET.get("vastutaja", "") or "")
    vastutaja = vastutaja if vastutaja in {str(m.owner_id) for m in _known.values()} else ""
    rows = sorted(
        (
            fact
            for fact in facts
            if period.holds(fact.sent_on)
            and (not liik or fact.matter.instrument_key == liik)
            and (not valdkond or valdkond in {key for key, _ in fact.matter.areas})
            and (not saatja or saatja in {key for key, _ in fact.matter.senders})
            and (not vastutaja or str(fact.matter.owner_id) == vastutaja)
        ),
        key=lambda fact: (fact.sent_on, fact.matter.reference),
        reverse=True,
    )
    # Sortable by any column (owner, 2026-11-17): «jarjesta=<column>», a leading
    # «-» for descending. Newest sent first is the default and the tie-break.
    order = str(request.GET.get("jarjesta", "") or "")
    column = order.lstrip("-")
    if column in SORT_KEYS:
        rows = sorted(rows, key=SORT_KEYS[column], reverse=order.startswith("-"))
    else:
        order = ""
    here = {
        **_period_query(period),
        "jurist": jurist,
        "liik": liik,
        "valdkond": valdkond,
        "saatja": saatja,
        "vastutaja": vastutaja,
    }
    headers = []
    for key, label in SORT_COLUMNS:
        current = order.lstrip("-") == key
        descending = order.startswith("-") if current else False
        # A first click sorts dates newest first and text A–Z; a second reverses.
        first = f"-{key}" if key in ("saadetud", "tahtaeg") else key
        target = (key if descending else f"-{key}") if current else first
        headers.append(
            {
                "label": label,
                "url": _url("reporting:opinion_rows", **here, jarjesta=target),
                "sort": ("descending" if descending else "ascending") if current else "",
            }
        )
    return render(
        request,
        "reporting/opinion_rows.html",
        {
            "tabs": TABS,
            "active_tab": "arvamused",
            "nav_active": "statistika",
            "period": period,
            "jurist": jurist,
            "liik": kinds.get(liik, "") or areas.get(valdkond, "") or senders.get(saatja, ""),
            "rows": rows,
            # How many the list holds: what every count that links here counted.
            "total": len(rows),
            "headers": headers,
            "back_url": _url("reporting:dashboard", **_period_query(period), jurist=jurist),
        },
    )
