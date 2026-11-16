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

from datetime import date
from typing import Any
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from app.reporting import opinion_stats as stats
from app.reporting import opinions

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


@login_required
@require_GET
def dashboard(request: HttpRequest) -> HttpResponse:
    from app.reporting.views import TABS

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

    carried = {**_period_query(period), "jurist": jurist}
    trend_carried = {"trend": "" if trend_key == DEFAULT_TREND else trend_key}
    bars, bar_owners = stats.monthly(facts, period)
    found = stats.trends(facts, matters, deadlines, first=first_year, last=last_year, today=today)
    summary = stats.summary(facts, matters, deadlines, period)

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
                for bar in bars
            ],
            "bar_owners": [{"owner": o, "slot": i % 8} for i, o in enumerate(bar_owners)],
            "trends": found,
            "records": stats.records(facts, found, today),
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
                for year in range(last_year, stats.FIRST_YEAR - 1, -1)
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
                for owner in ["", *owners]
            ],
            "jurist": jurist,
            "trend_links": [
                {
                    "label": label,
                    "active": trend_key == key,
                    "url": _url(
                        "reporting:dashboard",
                        **carried,
                        trend="" if key == DEFAULT_TREND else key,
                    )
                    + "#trendid",
                }
                for key, label in TREND_SPANS.items()
            ],
            "trend": trend_key if trend_key != DEFAULT_TREND else "",
            "trend_span": f"{first_year}–{last_year}",
            "today": today,
            "first_day": date(stats.FIRST_YEAR, 1, 1),
        },
    )


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
    rows = sorted(
        (fact for fact in facts if period.holds(fact.sent_on)),
        key=lambda fact: (fact.sent_on, fact.matter.reference),
        reverse=True,
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
            "rows": rows,
            "back_url": _url("reporting:dashboard", **_period_query(period), jurist=jurist),
        },
    )
