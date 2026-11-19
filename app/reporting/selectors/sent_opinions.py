"""«Arvamusi välja» — every count of sent opinions, from the one reader.

Since the owner's decision of 2026-11-19 (quality audit Q1, docs/adr/0214 §6)
every figure that says how many opinions Koda sent reads
:func:`app.reporting.opinions.opinion_facts`: Juristid's own sent `Koja arvamus`,
a request a person ended as answered without a document, and the register's
imported «välja saadetud». Before that, Statistika's Ülevaade counted all three
and Seis, Tegevus, Osakond and Minu asjad counted the first only, so one question
had two answers under one label (2026: 227 and 96).

What stays on `Submission` is what only a `Submission` has: its recipients, its
kind, its file and the `/statistika/arvamused/` register with its CSV. Those
lists say «Juristidis registreeritud arvamused» and are not this count.

Each count links to `reporting:opinion_rows`, the list of exactly the facts it
counted. That list narrows by period and owner only, so a figure computed
under any other narrowing of the filter bar carries no link rather than one to
a list that would not add up to it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any
from urllib.parse import urlencode

from django.urls import reverse
from django.utils import timezone

from app.reporting import opinions
from app.reporting.context import ReportingContext
from app.reporting.opinions import OpinionFact
from app.reporting.selectors.base import visible_matters

#: Where a count's list lives.
ROWS_URL_NAME = "reporting:opinion_rows"


def facts(context: ReportingContext) -> list[OpinionFact]:
    """Every sent opinion on the Teemad this page's filters keep, oldest first. Read once."""

    def compute() -> list[OpinionFact]:
        kept = set(visible_matters(context).values_list("pk", flat=True))
        known = {
            key: matter
            for key, matter in opinions.matter_facts(context.viewer).items()
            if key in kept
        }
        return opinions.opinion_facts(context.viewer, known)

    return context.shared("sent_opinions.facts", compute)


def in_window(
    rows: list[OpinionFact],
    *,
    year: int | None = None,
    month: int | None = None,
    through: date | None = None,
    since: date | None = None,
    until: date | None = None,
) -> list[OpinionFact]:
    """The facts sent in a calendar year (and month, up to a day), or between two days."""
    out = rows
    if year is not None:
        out = [f for f in out if f.sent_on.year == year]
        if month is not None:
            out = [f for f in out if f.sent_on.month == month]
        if through is not None:
            out = [f for f in out if f.sent_on <= through]
    if since is not None:
        out = [f for f in out if f.sent_on >= since]
    if until is not None:
        out = [f for f in out if f.sent_on <= until]
    return out


def in_period(context: ReportingContext) -> list[OpinionFact]:
    """The facts inside the page's period (whole years, or all of them)."""
    rows = facts(context)
    if context.period.is_all:
        return rows
    return [f for f in rows if context.period.contains_year(f.sent_on.year)]


def first_day(context: ReportingContext) -> date | None:
    rows = facts(context)
    return rows[0].sent_on if rows else None


def _linkable(context: ReportingContext) -> bool:
    """The list narrows by period and owner only: no link under any other narrowing."""
    return not replace(context, owner_id=None).has_matter_narrowing


def rows_url(
    context: ReportingContext,
    *,
    year: int | None = None,
    since: date | None = None,
    until: date | None = None,
) -> str:
    """The opinion list behind a count: one year, two days, or the page's period."""
    if not _linkable(context):
        return ""
    # The list accepts days up to the real today (`opinion_stats.parse_period`).
    today = max(context.today, timezone.localdate())
    if since is not None or until is not None:
        start = since or date(opinions_first_year(), 1, 1)
        end = min(until or today, today)
        params: dict[str, Any] = {
            "periood": "vali",
            "alates": start.isoformat(),
            "kuni": end.isoformat(),
        }
    elif year is not None:
        params = {"periood": "aasta", "aasta": str(year)}
    elif context.period.is_all:
        params = {
            "periood": "vali",
            "alates": date(opinions_first_year(), 1, 1).isoformat(),
            "kuni": today.isoformat(),
        }
    elif context.period.start_year == context.period.end_year:
        params = {"periood": "aasta", "aasta": str(context.period.start_year)}
    else:
        start_year = context.period.start_year or opinions_first_year()
        end_year = context.period.end_year or today.year
        params = {
            "periood": "vali",
            "alates": date(start_year, 1, 1).isoformat(),
            "kuni": min(date(end_year, 12, 31), today).isoformat(),
        }
    if context.owner_id is not None:
        params["vastutaja"] = str(context.owner_id)
    return f"{reverse(ROWS_URL_NAME)}?{urlencode(params)}"


def opinions_first_year() -> int:
    """The earliest year the opinion list accepts in a period."""
    from app.reporting.opinion_stats import FIRST_YEAR

    return FIRST_YEAR
